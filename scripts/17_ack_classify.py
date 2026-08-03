#!/usr/bin/env python3
"""Stage 17 - parse each acknowledgments block into structured counts (LLM).

Downstream of Stage 16. For every RCT whose acknowledgments block was located
(data/ack_blocks.csv, extraction_status == ok), this script asks Claude to
separate the thanked entities into the categories that the analysis needs and
return counts. Regex cannot reliably tell "David Atkin" (a person) from "seminar
participants at Brown" (a venue) from "Innovations for Poverty Action" (an
institution); the LLM does that cleanly.

Per the project lead's specification (2026-06-22):
  * HEADLINE count = named individuals thanked for COMMENTS/discussion/advice
    only. Research assistants, anonymous referees, and the editor are captured in
    SEPARATE fields and are deliberately excluded from the headline.
  * SEMINAR count = distinct named venues at which participants/audiences are
    thanked, combining seminars + conferences + workshops + meetings into one
    count. A vague "various/numerous seminars" with no named venue is flagged
    (seminars_unspecified) rather than guessed at.

Like Stage 6f, this uses the local Claude Code subscription via headless
`claude -p` (no API key, no metered billing). Each block is one
`claude -p --output-format json` subprocess; the default system prompt is
replaced by our extractor prompt. Resumable, model + prompt version recorded
per row.

Rows that Stage 16 marked no_block_found are written with all-zero counts
(classify_status = no_block: the paper has an acknowledgment with no thanked
individuals, or none at all -> 0 individuals / 0 seminars, a valid data point).
Rows marked no_pdf / pdf_error are passed through with blank counts (excluded
from the analysis denominator).

Inputs:
  data/ack_blocks.csv
Output:
  data/ack_classified.csv
  data/17_ack_classify.log

Env flags:
  SMOKE_N=10        only classify the first N blocks (validation run)
  CLAUDE_MODEL      model for claude -p (default claude-sonnet-4-5)
"""

import csv
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

csv.field_size_limit(min(sys.maxsize, 2147483647))

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
IN_CSV = DATA / "ack_blocks.csv"
OUT_CSV = DATA / "ack_classified.csv"
LOG_TXT = DATA / "17_ack_classify.log"

MODEL = os.environ.get("CLAUDE_MODEL", "claude-sonnet-4-5")
PROMPT_VERSION = "ack-count-v1"
SMOKE_N = int(os.environ.get("SMOKE_N", "0"))
SAVE_EVERY = 10
CALL_TIMEOUT = 180
CALL_RETRIES = 3       # transient CLI crashes (exit 1 / timeout) self-recover
RETRY_BACKOFF = 5      # seconds; multiplied by attempt number

SYSTEM_PROMPT = r"""You analyze the ACKNOWLEDGMENTS text of an economics research paper and return structured counts of who and what is thanked.

You will receive the raw acknowledgments block (usually a first-page footnote). It may contain people thanked for comments, research assistants, seminar/conference audiences, funders, implementing partners, and the corresponding-author line. Read carefully and sort the thanked entities into the categories below.

DEFINITIONS (apply exactly):

1. individuals_comments = DISTINCT NAMED NATURAL PERSONS thanked for intellectual input — comments, discussions, suggestions, feedback, advice, conversations, or for reading the paper. Count a person here once even if they are also listed among seminar participants.
   INCLUDE only people identified by a personal name (first name, initials, or surname).
   EXCLUDE from this category:
     - research assistants / field / survey / data-collection staff (category 2)
     - anonymous referees and the editor (categories 3 and 4)
     - the paper's own authors (people who wrote the paper, e.g. "Smith and Lee gratefully acknowledge ...")
     - any institution, organization, foundation, agency, university, NGO, company, or government body
     - groups thanked without individual names ("seminar participants", "two discussants", "our field team")

2. research_assistants = DISTINCT NAMED NATURAL PERSONS thanked specifically for research assistance, field work, survey work, data collection, or programming. If the text thanks "excellent research assistance" from named people, list those names here, not in category 1.

3. n_anonymous_referees = the number of anonymous referees thanked. If a number is stated ("two anonymous referees") use it; if referees are mentioned in the plural with no number ("anonymous referees") use 2; if singular ("an anonymous referee", "a referee") use 1; if none are mentioned use 0.

4. editor_thanked = true if an editor is thanked (whether named, e.g. "Andrew Foster (the editor)", or just "the editor"); otherwise false. A named editor goes here ONLY — do not also put them in category 1.

5. seminar_venues = the DISTINCT NAMED venues at which the authors thank seminar/conference/workshop/meeting participants or audiences. Combine seminars, conferences, workshops, symposia, and named annual meetings into this one list. Each distinct named venue counts once.
   - "seminar participants at Brown University, Columbia University, and NYU" -> ["Brown University", "Columbia University", "NYU"]
   - "the 2017 NEUDC conference at MIT" -> ["NEUDC conference at MIT"] (one venue; the year is not a separate venue)
   - "workshops at Deakin, Monash and Singapore Management University" -> three venues
   EXCLUDE: a vague reference with no named venue ("various seminars", "numerous conferences", "several workshops"). If such a vague reference appears, set seminars_unspecified = true. Do NOT invent a count for it.

6. funders = organizations/entities named as providing FINANCIAL support (foundations, agencies, research councils, grant programs, universities named as a grant source). Captured for completeness; list their names. Exclude individuals.

IGNORE any reference-list / bibliography text that may trail the acknowledgments (names in "Surname, F., ... (YEAR)" citation form, or with "et al."). Those are citations, not thanked people. Likewise ignore the running journal header and the abstract/body if any leaked into the block.

COUNTING RULES:
 - n_individuals_comments = the number of names in individuals_comments.
 - n_research_assistants = the number of names in research_assistants.
 - n_seminars = the number of venues in seminar_venues.
 - Deduplicate: the same person or venue named twice counts once.
 - If the block contains no acknowledgment content (only a corresponding-author line or funding-only text), return zeros and empty lists.

Respond with ONLY a JSON object, no prose, no code fence, in exactly this shape:
{"individuals_comments": ["<name>", ...], "research_assistants": ["<name>", ...], "n_anonymous_referees": <int>, "editor_thanked": true/false, "seminar_venues": ["<venue>", ...], "seminars_unspecified": true/false, "funders": ["<org>", ...], "notes": "<at most one short sentence, or empty>"}

Use names/venues exactly as written in the text. Do not add entities that are not in the text."""

FIELDS = ["doi", "journal_short", "publication_year", "title", "matched_file",
          "n_individuals_comments", "individuals_comments",
          "n_research_assistants", "research_assistants",
          "n_anonymous_referees", "editor_thanked",
          "n_seminars", "seminar_venues", "seminars_unspecified",
          "funders", "notes", "ack_source",
          "llm_model", "llm_prompt_version", "classify_status", "snapshot_utc"]


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


def _call_once(claude_bin, sys_file, block):
    proc = subprocess.run(
        [claude_bin, "-p", "--output-format", "json",
         "--system-prompt-file", sys_file, "--model", MODEL,
         "--exclude-dynamic-system-prompt-sections"],
        input=block, capture_output=True, text=True,
        encoding="utf-8", timeout=CALL_TIMEOUT,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"claude exit {proc.returncode}: {proc.stderr[:200]}")
    env = json.loads(proc.stdout)
    if env.get("is_error"):
        raise RuntimeError(f"claude reported error: {env.get('result','')[:200]}")
    return parse_inner_json(env.get("result", "")), env.get("total_cost_usd", 0.0)


def call_claude(claude_bin, sys_file, block):
    # The Windows `claude` CLI intermittently dies with exit 1 / empty stderr
    # (0xC0000409) or times out, especially after a laptop-sleep degrades the
    # machine. These failures are transient, so retry a few times with backoff
    # before giving up and recording the row as call_error.
    last = None
    for attempt in range(1, CALL_RETRIES + 1):
        try:
            return _call_once(claude_bin, sys_file, block)
        except (RuntimeError, subprocess.TimeoutExpired) as e:
            last = e
            if attempt < CALL_RETRIES:
                time.sleep(RETRY_BACKOFF * attempt)
    raise last


def _names(lst):
    return [s.strip() for s in (lst or []) if isinstance(s, str) and s.strip()]


def main():
    open(LOG_TXT, "w").close()
    log(f"Stage 17 (ack classify) start. Model={MODEL} Prompt={PROMPT_VERSION} SMOKE_N={SMOKE_N}")
    claude_bin = find_claude()
    log(f"claude CLI: {claude_bin}")

    with open(IN_CSV, encoding="utf-8") as f:
        blocks = list(csv.DictReader(f))
    log(f"ack_blocks rows: {len(blocks)}  "
        f"(ok={sum(1 for r in blocks if r['extraction_status']=='ok')})")

    done = {}
    if OUT_CSV.exists():
        with open(OUT_CSV, encoding="utf-8") as f:
            for r in csv.DictReader(f):
                if r.get("classify_status") in ("ok", "no_block"):
                    done[r["doi"]] = r
        log(f"Resuming: {len(done)} already classified")

    sys_fd, sys_file = tempfile.mkstemp(suffix=".txt", prefix="ack_sys_")
    with os.fdopen(sys_fd, "w", encoding="utf-8") as f:
        f.write(SYSTEM_PROMPT)

    out_rows = list(done.values())
    n_ok = n_err = n_noblock = n_other = 0
    tot_cost = 0.0
    t0 = time.time()
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    # Determine the LLM work-list (ok blocks not yet done).
    todo = [r for r in blocks
            if r["extraction_status"] == "ok" and r["doi"] not in done]
    if SMOKE_N:
        todo = todo[:SMOKE_N]
    log(f"To classify via LLM this run: {len(todo)}")
    todo_dois = {r["doi"] for r in todo}

    def base_of(r):
        return {
            "doi": r["doi"], "journal_short": r["journal_short"],
            "publication_year": r["publication_year"], "title": r["title"],
            "matched_file": r["matched_file"], "ack_source": r.get("ack_source", ""),
            "llm_model": MODEL, "llm_prompt_version": PROMPT_VERSION,
            "snapshot_utc": stamp,
        }

    def save():
        with open(OUT_CSV, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=FIELDS)
            w.writeheader()
            w.writerows(out_rows)

    try:
        for k, r in enumerate(todo, 1):
            b = base_of(r)
            try:
                result, cost = call_claude(claude_bin, sys_file, r["ack_text"])
            except Exception as e:  # noqa: BLE001
                log(f"  [{k}/{len(todo)}] call error {r['doi']}: {type(e).__name__}: {e}")
                b.update(n_individuals_comments="", individuals_comments="",
                         n_research_assistants="", research_assistants="",
                         n_anonymous_referees="", editor_thanked="",
                         n_seminars="", seminar_venues="", seminars_unspecified="",
                         funders="", notes="", classify_status="call_error")
                out_rows.append(b); n_err += 1; continue
            tot_cost += cost or 0.0
            ind = _names(result.get("individuals_comments"))
            ras = _names(result.get("research_assistants"))
            ven = _names(result.get("seminar_venues"))
            fun = _names(result.get("funders"))
            try:
                n_ref = int(result.get("n_anonymous_referees") or 0)
            except (TypeError, ValueError):
                n_ref = 0
            b.update(
                n_individuals_comments=len(ind),
                individuals_comments=" | ".join(ind),
                n_research_assistants=len(ras),
                research_assistants=" | ".join(ras),
                n_anonymous_referees=n_ref,
                editor_thanked=str(bool(result.get("editor_thanked"))),
                n_seminars=len(ven),
                seminar_venues=" | ".join(ven),
                seminars_unspecified=str(bool(result.get("seminars_unspecified"))),
                funders=" | ".join(fun),
                notes=(result.get("notes") or "").strip(),
                classify_status="ok",
            )
            out_rows.append(b); n_ok += 1
            if (k % SAVE_EVERY) == 0:
                save()
                rate = k / max(time.time() - t0, 1)
                log(f"  progress {k}/{len(todo)}  ok={n_ok} err={n_err}  "
                    f"sub-cost≈${tot_cost:.2f}  {rate:.2f} req/s")
    finally:
        save()
        try:
            os.remove(sys_file)
        except OSError:
            pass

    # Pass-through rows for non-LLM statuses (skip ones already written, and
    # skip ok rows when SMOKE_N capped the LLM work-list to keep them pending).
    handled = {r["doi"] for r in out_rows}
    for r in blocks:
        if r["doi"] in handled:
            continue
        if SMOKE_N and r["extraction_status"] == "ok" and r["doi"] not in todo_dois:
            continue  # leave remaining ok rows for the full run
        b = base_of(r)
        st = r["extraction_status"]
        if st == "no_block_found":
            b.update(n_individuals_comments=0, individuals_comments="",
                     n_research_assistants=0, research_assistants="",
                     n_anonymous_referees=0, editor_thanked="False",
                     n_seminars=0, seminar_venues="", seminars_unspecified="False",
                     funders="", notes="no acknowledgments block located",
                     classify_status="no_block")
            out_rows.append(b); n_noblock += 1
        else:  # no_pdf / pdf_error
            b.update(n_individuals_comments="", individuals_comments="",
                     n_research_assistants="", research_assistants="",
                     n_anonymous_referees="", editor_thanked="",
                     n_seminars="", seminar_venues="", seminars_unspecified="",
                     funders="", notes="", classify_status=st)
            out_rows.append(b); n_other += 1
    save()

    log(f"\nDone. LLM ok={n_ok} err={n_err}; no_block={n_noblock}; other={n_other}. "
        f"Subscription-equivalent cost≈${tot_cost:.2f}. Output: {OUT_CSV.name}")
    # Quick headline summary over classified rows.
    vals = [int(r["n_individuals_comments"]) for r in out_rows
            if r["classify_status"] in ("ok", "no_block") and str(r["n_individuals_comments"]).isdigit()]
    sem = [int(r["n_seminars"]) for r in out_rows
           if r["classify_status"] in ("ok", "no_block") and str(r["n_seminars"]).isdigit()]
    if vals:
        import statistics
        log(f"Individuals thanked (n={len(vals)}): mean {statistics.mean(vals):.1f}, "
            f"median {statistics.median(vals)}, max {max(vals)}")
    if sem:
        import statistics
        log(f"Seminars per paper (n={len(sem)}): mean {statistics.mean(sem):.1f}, "
            f"median {statistics.median(sem)}, max {max(sem)}")
    if SMOKE_N:
        log("SMOKE RUN — review ack_classified.csv before the full run.")


if __name__ == "__main__":
    main()
