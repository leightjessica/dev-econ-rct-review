"""
Macro extension, Stage 3a: LLM resolution of BORDERLINE rows to development /
not-development, via the local `claude -p` subscription (no API key).

This mirrors scripts/03a_dev_borderline_classify.py (identical system prompt,
"dev-classify-v1") but uses the `claude -p --output-format json` subprocess
pattern from scripts/17_ack_classify.py instead of the Anthropic SDK, because
there is no API key on this machine.

Reads:  data/macro/dev_filtered_macro.csv      (m02 output)
Writes: data/macro/dev_classified_macro.csv    (BORDERLINE rows resolved)

Scope: every row with is_development == 'BORDERLINE' AND type == 'article' AND
not paratext. Unlike the main Stage 3a, title-only rows (no abstract) ARE
classified here, because the goal is to resolve all borderline articles; those
rows are flagged abstract_available='no' so the weaker basis is auditable.

Resumable: rerun skips rows that already carry dev_llm_classification.
"""

import csv
import json
import os
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone

MODEL = os.environ.get("CLAUDE_MODEL", "claude-sonnet-4-5")
PROMPT_VERSION = "dev-classify-v1"
SAVE_EVERY = 10
CALL_TIMEOUT = 180     # seconds per claude -p call
CALL_RETRIES = 3
RETRY_BACKOFF = 4

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_DIR = os.path.abspath(os.path.join(SCRIPT_DIR, ".."))
IN_CSV = os.path.join(PROJECT_DIR, "data", "macro", "dev_filtered_macro.csv")
OUT_CSV = os.path.join(PROJECT_DIR, "data", "macro", "dev_classified_macro.csv")
LOG_TXT = os.path.join(PROJECT_DIR, "data", "macro", "m03a_dev_classify.log")

# Identical to scripts/03a_dev_borderline_classify.py SYSTEM_PROMPT.
SYSTEM_PROMPT = """You are an expert economist helping classify academic papers as development economics or not.

A paper qualifies as DEVELOPMENT ECONOMICS if it primarily:
- Studies economic outcomes, institutions, or policies in low-income or middle-income countries (the World Bank's LIC, LMIC, or UMIC categories), OR
- Addresses theoretical or empirical questions explicitly about economic development, growth, poverty, structural transformation, or development policy, OR
- Examines microeconomic phenomena (households, firms, markets) primarily in developing-country settings.

Borderline guidance:
- Papers using developing-country data but addressing general questions (e.g., a labor economics paper that happens to use Indian administrative data): mark "yes" if the paper engages substantively with development questions; "no" if the country is incidental.
- Macro or finance papers with international scope: "no" unless development is the central focus.
- Migration, foreign aid, or trade papers: "yes" if developing-country effects are central.
- Pure theory papers: "yes" only if the model is explicitly about development.
- A paper conducted in a high-income country (US, UK, Western Europe, Japan, etc.) is almost never development.
- If genuinely unclear from the abstract, mark "uncertain".

Respond with ONLY a single JSON object in this exact format and nothing else:
{"is_development": "yes" | "no" | "uncertain", "justification": "<one short sentence>"}"""


def log(msg):
    line = f"[{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%SZ')}] {msg}"
    print(line, flush=True)
    with open(LOG_TXT, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def find_claude():
    for cand in ("claude", "claude.cmd", "claude.exe"):
        p = shutil.which(cand)
        if p:
            return p
    sys.exit("Could not find the `claude` CLI on PATH.")


def parse_inner_json(text):
    t = text.strip()
    if t.startswith("```"):
        t = t.split("\n", 1)[1] if "\n" in t else t[3:]
        if t.endswith("```"):
            t = t[:-3]
        t = t.strip()
        if t.startswith("json"):
            t = t[4:].strip()
    try:
        return json.loads(t)
    except json.JSONDecodeError:
        a, b = t.find("{"), t.rfind("}")
        if a != -1 and b != -1 and b > a:
            return json.loads(t[a:b + 1])
        raise


def _call_once(claude_bin, sys_file, user_block):
    proc = subprocess.run(
        [claude_bin, "-p", "--output-format", "json",
         "--system-prompt-file", sys_file, "--model", MODEL,
         "--exclude-dynamic-system-prompt-sections"],
        input=user_block, capture_output=True, text=True,
        encoding="utf-8", timeout=CALL_TIMEOUT,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"claude exit {proc.returncode}: {proc.stderr[:200]}")
    env = json.loads(proc.stdout)
    if env.get("is_error"):
        raise RuntimeError(f"claude reported error: {env.get('result','')[:200]}")
    return parse_inner_json(env.get("result", "")), env.get("total_cost_usd", 0.0)


def call_claude(claude_bin, sys_file, user_block):
    last = None
    for attempt in range(1, CALL_RETRIES + 1):
        try:
            return _call_once(claude_bin, sys_file, user_block)
        except (RuntimeError, subprocess.TimeoutExpired, json.JSONDecodeError) as e:
            last = e
            if attempt < CALL_RETRIES:
                time.sleep(RETRY_BACKOFF * attempt)
    raise last


def main():
    open(LOG_TXT, "w").close()
    log(f"Macro Stage 3a start. Model={MODEL} Prompt={PROMPT_VERSION}")
    claude_bin = find_claude()
    log(f"claude CLI: {claude_bin}")

    src_path = OUT_CSV if os.path.exists(OUT_CSV) else IN_CSV
    log(f"Reading {src_path}")
    with open(src_path, encoding="utf-8") as f:
        reader = csv.DictReader(f)
        rows = list(reader)
        fields = list(reader.fieldnames)
    new_cols = ("dev_llm_classification", "dev_llm_justification",
                "dev_llm_model", "dev_llm_prompt_version", "abstract_available")
    for nf in new_cols:
        if nf not in fields:
            fields.append(nf)
    for r in rows:
        for nf in new_cols:
            r.setdefault(nf, "")

    todo_idx = []
    for i, r in enumerate(rows):
        if r.get("is_development") != "BORDERLINE":
            continue
        if r.get("type") != "article" or str(r.get("is_paratext")).lower() == "true":
            continue
        if r.get("dev_llm_classification"):
            continue
        todo_idx.append(i)
    n_titleonly = sum(1 for i in todo_idx if not (rows[i].get("abstract") or "").strip())
    log(f"Borderline articles to classify: {len(todo_idx)} (title-only: {n_titleonly})")

    def save_checkpoint():
        with open(OUT_CSV, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fields)
            writer.writeheader()
            writer.writerows(rows)

    if not todo_idx:
        log("Nothing to do; writing pass-through.")
        save_checkpoint()
        return

    n_done = n_yes = n_no = n_unc = n_err = 0
    tot_cost = 0.0
    for k, i in enumerate(todo_idx, start=1):
        r = rows[i]
        title = (r.get("title") or "").strip()
        abstract = (r.get("abstract") or "").strip()
        has_abs = "yes" if abstract else "no"
        user_block = (f"Title: {title}\n\nAbstract: {abstract}" if abstract
                      else f"Title: {title}\n\nAbstract: (no abstract available; classify from the title alone, and mark uncertain if the title is not informative enough)")
        try:
            result, cost = call_claude(claude_bin, SYS_FILE, user_block)
        except Exception as e:
            log(f"  [{k}/{len(todo_idx)}] error idx {i}: {type(e).__name__}: {str(e)[:160]}")
            r["dev_llm_classification"] = "call_error"
            n_err += 1
            continue
        tot_cost += cost or 0.0
        cls = (result.get("is_development") or "").strip().lower()
        just = (result.get("justification") or "").strip()
        r["dev_llm_classification"] = cls
        r["dev_llm_justification"] = just
        r["dev_llm_model"] = MODEL
        r["dev_llm_prompt_version"] = PROMPT_VERSION
        r["abstract_available"] = has_abs
        if cls == "yes":
            r["is_development"] = "TRUE"
            r["dev_filter_source"] = "llm_yes"
            n_yes += 1
        elif cls == "no":
            r["is_development"] = "FALSE"
            r["dev_filter_source"] = "llm_no"
            n_no += 1
        else:
            r["is_development"] = "BORDERLINE"
            r["dev_filter_source"] = "llm_uncertain"
            n_unc += 1
        n_done += 1
        if (n_done % SAVE_EVERY) == 0:
            save_checkpoint()
            log(f"  progress {n_done}/{len(todo_idx)}  yes={n_yes} no={n_no} unc={n_unc} err={n_err}  cost=${tot_cost:.4f}")

    save_checkpoint()
    log(f"Macro Stage 3a complete. Classified {n_done}/{len(todo_idx)} "
        f"(yes={n_yes} no={n_no} unc={n_unc} err={n_err}); cost=${tot_cost:.4f}")


# Write the system prompt to a temp file once, then run.
import tempfile
_sys_fd, SYS_FILE = tempfile.mkstemp(prefix="m03a_sys_", suffix=".txt")
with os.fdopen(_sys_fd, "w", encoding="utf-8") as _f:
    _f.write(SYSTEM_PROMPT)

if __name__ == "__main__":
    try:
        main()
    finally:
        try:
            os.remove(SYS_FILE)
        except OSError:
            pass
