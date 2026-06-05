#!/usr/bin/env python3
"""Stage 6f - extract funding sources from full-text PDFs (LLM via Claude Code).

For the RCTs that Stage 6a could not find a funder for in metadata and for which
a PDF was collected (data/fulltext_match_report.csv, status == FOUND), this
script builds a "funding context" from each PDF and asks Claude to extract the
funding ORGANIZATIONS only. Output mirrors funders_6a.csv so the two can be
merged into a unified funder dataset.

This variant uses the local Claude Code subscription via headless `claude -p`
(no separate API key, no metered API billing) rather than the Anthropic SDK.
Each paper is one `claude -p --output-format json` subprocess call; the default
Claude Code system prompt is replaced by our extractor prompt so the model acts
as a pure extractor.

Funding context per paper = the page-1 title footnote (AEA/Oxford/JPE) PLUS
keyword-anchored windows from anywhere in the document (Elsevier/JDE put an
Acknowledgments / Funding / competing-interest section near the references).
Elsevier extractions often lose inter-word spaces; the model is robust to that.

The model returns FUNDERS only, excluding individuals thanked, research
assistants, seminar audiences, editors/referees, IRB/ethics committees, trial
registries (AEA RCT Registry), and implementing/data partners that did not also
provide money.

Conventions match Stages 3a/3b: temperature is fixed by the CLI; model + prompt
version recorded per row; JSON output; resumable (rerunning skips DOIs already
in the output).

Env flags:
  SMOKE_N=5         only process the first N papers (validation run)
  CLAUDE_MODEL      model for claude -p (default claude-sonnet-4-5)
"""

import csv
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone

try:
    import pdfplumber
except ImportError:
    sys.exit("Missing pdfplumber. From the project root:\n  pip install -r requirements.txt")

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_DIR = os.path.abspath(os.path.join(SCRIPT_DIR, ".."))
DATA = os.path.join(PROJECT_DIR, "data")
FULLTEXT = os.path.join(PROJECT_DIR, "fulltext")
MATCH_REPORT = os.path.join(DATA, "fulltext_match_report.csv")
OUT_CSV = os.path.join(DATA, "funders_6f_fulltext.csv")
LOG_TXT = os.path.join(DATA, "06f_funders_fulltext.log")

MODEL = os.environ.get("CLAUDE_MODEL", "claude-sonnet-4-5")
PROMPT_VERSION = "funders-fulltext-v1"
SAVE_EVERY = 10
SMOKE_N = int(os.environ.get("SMOKE_N", "0"))
CALL_TIMEOUT = 180  # seconds per claude -p call

PAGES_FULL = 60
CONTEXT_CHARS = 9000
WINDOW = 1100

KW = re.compile(
    r"(acknowledg|we (?:thank|are grateful|gratefully)|financial support|"
    r"for funding|funded by|funding (?:from|for|was|is|statement)|grant"
    r"|supported by|we acknowledge|declaration of competing|conflict of interest)",
    re.I,
)

SYSTEM_PROMPT = """You extract FUNDING SOURCES from the acknowledgments text of an economics research paper.

You will receive raw text excerpts from a paper (a first-page title footnote and/or an acknowledgments or funding section). The text may have lost spaces between words; read through that.

Return the organizations or entities that provided FINANCIAL SUPPORT for the research. Funders include: foundations; government agencies and ministries; national research councils; multilateral and development organizations (e.g. World Bank, IADB); named research initiatives, funds, programs, or labs that disburse money (e.g. J-PAL initiatives, IGC, PEDL); and universities ONLY when named as the source of a grant, fellowship, or research fund.

Do NOT include any of the following:
- Individuals thanked (for comments, advice, etc.)
- Research assistants or field/survey teams
- Seminar, conference, or workshop audiences
- Editors and referees
- IRB / ethics review boards
- Trial registries (e.g. the AEA RCT Registry, AEARCTR IDs)
- Implementing partners, NGOs, or government bodies named only as collaborators or data providers (UNLESS the text says they also provided funding)
- The authors' own universities named only as affiliations

If a grant or award number is given for a funder, capture it.

Respond with ONLY a JSON object, no prose, no code fence, in exactly this shape:
{"has_funding_statement": true/false, "funders": [{"name": "<organization>", "grant_number": "<number or empty>"}], "notes": "<at most one short sentence, or empty>"}

Set has_funding_statement to false and funders to [] if the text contains acknowledgments but no financial-support statement. Use the funder's full name as written; do not invent funders that are not in the text."""


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


def build_context(pdf_path):
    try:
        with pdfplumber.open(pdf_path) as pdf:
            pages = [(p.extract_text() or "") for p in pdf.pages[:PAGES_FULL]]
    except Exception as e:  # noqa: BLE001
        log(f"    PDF read error: {type(e).__name__}: {e}")
        return ""
    full = "\n".join(pages)
    parts = [pages[0] if pages else ""]
    used = [(0, len(parts[0]))]
    for m in KW.finditer(full):
        s, e = max(0, m.start() - 120), min(len(full), m.start() + WINDOW)
        if any(s < ue and e > us for us, ue in used[1:]):
            continue
        parts.append(full[s:e])
        used.append((s, e))
        if sum(len(p) for p in parts) > CONTEXT_CHARS:
            break
    return ("\n---\n".join(parts))[:CONTEXT_CHARS]


def parse_inner_json(text):
    """Parse the model's JSON answer, tolerating code fences / surrounding prose."""
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


def call_claude(claude_bin, sys_file, context):
    """Run one headless extraction; return (inner_dict, cost_usd, raw_result)."""
    proc = subprocess.run(
        [claude_bin, "-p", "--output-format", "json",
         "--system-prompt-file", sys_file, "--model", MODEL,
         "--exclude-dynamic-system-prompt-sections"],
        input=context, capture_output=True, text=True,
        encoding="utf-8", timeout=CALL_TIMEOUT,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"claude exit {proc.returncode}: {proc.stderr[:200]}")
    env = json.loads(proc.stdout)
    if env.get("is_error"):
        raise RuntimeError(f"claude reported error: {env.get('result','')[:200]}")
    result_text = env.get("result", "")
    return parse_inner_json(result_text), env.get("total_cost_usd", 0.0), result_text


FIELDS = ["doi", "journal_short", "publication_year", "title", "matched_file",
          "version", "has_funding_statement", "n_funders", "funders",
          "grant_numbers", "extraction_status", "notes",
          "llm_model", "llm_prompt_version", "snapshot_utc"]


def main():
    open(LOG_TXT, "w").close()
    log(f"Stage 6f start (claude -p). Model={MODEL}  Prompt={PROMPT_VERSION}  SMOKE_N={SMOKE_N}")
    claude_bin = find_claude()
    log(f"claude CLI: {claude_bin}")

    with open(MATCH_REPORT, encoding="utf-8") as f:
        report = [r for r in csv.DictReader(f) if r["status"] == "FOUND"]
    log(f"Papers with a collected PDF: {len(report)}")

    done = {}
    if os.path.exists(OUT_CSV):
        with open(OUT_CSV, encoding="utf-8") as f:
            for r in csv.DictReader(f):
                done[r["doi"]] = r
        log(f"Resuming: {len(done)} already extracted")

    todo = [r for r in report if r["doi"] not in done]
    if SMOKE_N:
        todo = todo[:SMOKE_N]
    log(f"To extract this run: {len(todo)}")

    # Write the system prompt to a temp file for --system-prompt-file.
    sys_fd, sys_file = tempfile.mkstemp(suffix=".txt", prefix="funders_sys_")
    with os.fdopen(sys_fd, "w", encoding="utf-8") as f:
        f.write(SYSTEM_PROMPT)

    out_rows = list(done.values())
    n_ok = n_none = n_err = 0
    tot_cost = 0.0
    t0 = time.time()

    def save():
        with open(OUT_CSV, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=FIELDS)
            w.writeheader()
            w.writerows(out_rows)

    try:
        for k, r in enumerate(todo, 1):
            pdf_path = os.path.join(FULLTEXT, r["matched_file"])
            version = "wp" if r["match_method"] == "manual-wp" else "published?"
            base = {
                "doi": r["doi"], "journal_short": r["journal_short"],
                "publication_year": r["publication_year"], "title": r["title"],
                "matched_file": r["matched_file"], "version": version,
                "llm_model": MODEL, "llm_prompt_version": PROMPT_VERSION,
                "snapshot_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            }
            ctx = build_context(pdf_path)
            if not ctx:
                base.update(has_funding_statement="", n_funders=0, funders="",
                            grant_numbers="", extraction_status="pdf_error", notes="")
                out_rows.append(base); n_err += 1; continue
            try:
                result, cost, raw = call_claude(claude_bin, sys_file, ctx)
            except Exception as e:  # noqa: BLE001
                log(f"  [{k}/{len(todo)}] call error {r['doi']}: {type(e).__name__}: {e}")
                base.update(has_funding_statement="", n_funders=0, funders="",
                            grant_numbers="", extraction_status="call_error", notes="")
                out_rows.append(base); n_err += 1; continue
            tot_cost += cost or 0.0
            funders = result.get("funders") or []
            names = [(fd.get("name") or "").strip() for fd in funders
                     if (fd.get("name") or "").strip()]
            grants = [f"{(fd.get('name') or '').strip()}: {(fd.get('grant_number') or '').strip()}"
                      for fd in funders if (fd.get("grant_number") or "").strip()]
            base.update(
                has_funding_statement=str(bool(result.get("has_funding_statement"))),
                n_funders=len(names),
                funders=" | ".join(names),
                grant_numbers=" | ".join(grants),
                extraction_status="ok",
                notes=(result.get("notes") or "").strip(),
            )
            out_rows.append(base); n_ok += 1
            if len(names) == 0:
                n_none += 1
            if (k % SAVE_EVERY) == 0:
                save()
                rate = k / max(time.time() - t0, 1)
                log(f"  progress {k}/{len(todo)}  ok={n_ok} (none={n_none}) err={n_err}  "
                    f"sub-cost≈${tot_cost:.2f}  {rate:.2f} req/s")
    finally:
        save()
        try:
            os.remove(sys_file)
        except OSError:
            pass

    log(f"Done. ok={n_ok} (no funder found={n_none}), errors={n_err}. "
        f"Subscription-equivalent cost≈${tot_cost:.2f}. Output: {os.path.basename(OUT_CSV)}")
    if SMOKE_N:
        log("SMOKE RUN — review funders_6f_fulltext.csv before the full run.")


if __name__ == "__main__":
    main()
