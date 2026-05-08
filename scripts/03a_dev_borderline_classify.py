"""
Stage 3a: LLM classification of BORDERLINE rows for development-paper status.

Reads:  data/dev_filtered.csv     (Stage 2 output)
Writes: data/dev_classified.csv   (BORDERLINE rows resolved to TRUE/FALSE/UNCERTAIN)

Workflow
--------
For each row where is_development == 'BORDERLINE' AND type == 'article' AND
abstract is non-empty, send title+abstract to the Anthropic API and ask
whether the paper is a development-economics paper. Update the row's
is_development field to TRUE / FALSE / BORDERLINE based on the response.

Requirements
------------
- pip install -r ../requirements.txt
- ANTHROPIC_API_KEY environment variable set

Replicability features
----------------------
- temperature = 0
- model identifier and prompt version stored on each classified row
- prompt caching applied to the system prompt (5-minute TTL); each request
  re-uses cached input tokens after the first
- script is RESUMABLE: rerunning skips rows already classified
- output written incrementally every 50 classifications

Cost (rough)
------------
~780 BORDERLINE articles with abstracts. Sonnet 4.5 at $3/M input + $15/M
output. With prompt caching the per-call cost is dominated by the abstract
(~200-300 input tokens uncached) plus ~50 output tokens. Total estimated:
$1.50-2.50.
"""

import csv
import json
import os
import sys
import time
from datetime import datetime, timezone

try:
    from anthropic import Anthropic
except ImportError:
    sys.exit("Missing anthropic package. From the project root, run:\n  pip install -r requirements.txt")

# ---- Configuration ----------------------------------------------------------

MODEL = os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-4-5")
PROMPT_VERSION = "dev-classify-v1"
MAX_TOKENS = 256
SAVE_EVERY = 50

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_DIR = os.path.abspath(os.path.join(SCRIPT_DIR, ".."))
IN_CSV = os.path.join(PROJECT_DIR, "data", "dev_filtered.csv")
OUT_CSV = os.path.join(PROJECT_DIR, "data", "dev_classified.csv")
LOG_TXT = os.path.join(PROJECT_DIR, "data", "03a_dev_classify.log")

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


def parse_json_response(text):
    """Strip markdown fences if present, then json.loads."""
    t = text.strip()
    if t.startswith("```"):
        # remove first fence
        t = t.split("\n", 1)[1] if "\n" in t else t[3:]
        # remove trailing fence
        if t.endswith("```"):
            t = t[:-3]
        # drop leading 'json' label
        t = t.strip()
        if t.startswith("json"):
            t = t[4:].strip()
    return json.loads(t)


def classify_one(client, title, abstract):
    """Single call. Returns (parsed_dict_or_None, usage)."""
    msg = client.messages.create(
        model=MODEL,
        max_tokens=MAX_TOKENS,
        temperature=0,
        system=[{"type": "text", "text": SYSTEM_PROMPT,
                 "cache_control": {"type": "ephemeral"}}],
        messages=[{"role": "user", "content": f"Title: {title}\n\nAbstract: {abstract}"}],
    )
    text = msg.content[0].text
    try:
        return parse_json_response(text), msg.usage, text
    except json.JSONDecodeError:
        return None, msg.usage, text


def main():
    open(LOG_TXT, "w").close()
    log(f"Stage 3a start. Model={MODEL}  Prompt={PROMPT_VERSION}")

    # Load (resume from OUT_CSV if it exists)
    src_path = OUT_CSV if os.path.exists(OUT_CSV) else IN_CSV
    log(f"Reading {src_path}")
    with open(src_path, encoding="utf-8") as f:
        reader = csv.DictReader(f)
        rows = list(reader)
        fields = list(reader.fieldnames)
    for nf in ("dev_llm_classification", "dev_llm_justification",
               "dev_llm_model", "dev_llm_prompt_version"):
        if nf not in fields:
            fields.append(nf)
    for r in rows:
        for nf in ("dev_llm_classification", "dev_llm_justification",
                   "dev_llm_model", "dev_llm_prompt_version"):
            r.setdefault(nf, "")

    # Identify rows to classify
    todo_idx = []
    for i, r in enumerate(rows):
        if r["is_development"] != "BORDERLINE":
            continue
        if r.get("type") != "article":
            continue
        if not (r.get("abstract") or "").strip():
            continue
        if r.get("dev_llm_classification"):
            continue
        todo_idx.append(i)
    log(f"Borderline articles to classify: {len(todo_idx)}")

    if not todo_idx:
        log("Nothing to do; writing pass-through and exiting.")
        with open(OUT_CSV, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fields)
            writer.writeheader()
            writer.writerows(rows)
        return

    client = Anthropic()
    n_done = n_yes = n_no = n_unc = n_err = 0
    tot_in = tot_out = tot_cache_read = tot_cache_create = 0
    t0 = time.time()

    def save_checkpoint():
        with open(OUT_CSV, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fields)
            writer.writeheader()
            writer.writerows(rows)

    for k, i in enumerate(todo_idx, start=1):
        r = rows[i]
        title = (r.get("title") or "").strip()
        abstract = (r.get("abstract") or "").strip()
        try:
            result, usage, raw = classify_one(client, title, abstract)
        except Exception as e:
            log(f"  [{k}] API error on idx {i}: {type(e).__name__}: {e}; sleeping 5s")
            time.sleep(5)
            n_err += 1
            continue
        if result is None:
            log(f"  [{k}] could not parse JSON for idx {i}: {raw[:200]!r}")
            n_err += 1
            continue
        cls = (result.get("is_development") or "").strip().lower()
        just = (result.get("justification") or "").strip()
        r["dev_llm_classification"] = cls
        r["dev_llm_justification"] = just
        r["dev_llm_model"] = MODEL
        r["dev_llm_prompt_version"] = PROMPT_VERSION
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
        if usage:
            tot_in += getattr(usage, "input_tokens", 0) or 0
            tot_out += getattr(usage, "output_tokens", 0) or 0
            tot_cache_read += getattr(usage, "cache_read_input_tokens", 0) or 0
            tot_cache_create += getattr(usage, "cache_creation_input_tokens", 0) or 0
        n_done += 1

        if (n_done % SAVE_EVERY) == 0:
            save_checkpoint()
            elapsed = time.time() - t0
            rate = n_done / max(elapsed, 1)
            log(f"  progress {n_done}/{len(todo_idx)}  yes={n_yes} no={n_no} unc={n_unc} err={n_err}  "
                f"tok in={tot_in} cached_read={tot_cache_read} out={tot_out}  {rate:.2f} req/s")

    save_checkpoint()
    log(f"Stage 3a complete. Classified {n_done}/{len(todo_idx)} (yes={n_yes} no={n_no} unc={n_unc} err={n_err})")
    log(f"Tokens: input={tot_in} cache_read={tot_cache_read} cache_create={tot_cache_create} output={tot_out}")


if __name__ == "__main__":
    main()
