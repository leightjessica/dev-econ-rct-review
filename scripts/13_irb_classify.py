#!/usr/bin/env python3
"""Stage 13 - LLM extraction of the IRB/ethics-approval LOCATION for each RCT.

This is the structured-interpretation pass that sits downstream of the Stage-12
keyword scanner. Stage 12 (12_irb_extract.py) locates and excerpts every
IRB / ethics / human-subjects sentence in a paper; it does NOT interpret them.
This stage reads those excerpts and answers the question the project lead posed:

    For each RCT that mentions ethics oversight, WHICH approving body granted it,
    in WHICH country is that body located, and is that country the same as the
    country where the data were collected (the "study country")?

The motivating fact: in development economics the approving IRB is frequently the
researchers' HOME (often high-income) institution rather than a body in the
country of study. This pass makes that distinction measurable.

Division of labour
------------------
The LLM does EXTRACTION only -- it reads the snippets and returns the named
approving institutions plus the ISO 3166-1 alpha-3 country code of each. The
study-vs-IRB COMPARISON is done deterministically in Python (set membership of
ISO3 codes), so the headline classification is reproducible and not at the mercy
of the model's arithmetic. The study country comes from Stage 6a
(country_classified.csv, column country_iso3_list).

Per-paper output classification (study country = data-collection country):
  local_only     every identified IRB is located in the study country
  foreign_only   every identified IRB is OUTSIDE the study country
                 (e.g., a US/European home institution)
  both           at least one IRB in the study country AND at least one outside
  undetermined   IRB mentioned but no institution/country identifiable, OR the
                 study country itself is not a single identifiable country
                 (cross_country / no_country / unlisted), so no comparison is
                 possible. The raw irb_iso3_list and study_iso3_list are kept so
                 these can be re-derived by hand if desired.
  no_mention     Stage 12 found no IRB/ethics text in the paper
  no_pdf         no full-text PDF was matched to the RCT

Inputs:
  data/irb_mentions.csv        (Stage 12 output; snippets per RCT)
  data/country_classified.csv  (Stage 6a output; study country per DOI)
Output:
  data/irb_classified.csv      (one row per RCT)
  data/13_irb_classify.log
  data/13_irb_classify_failures.log   (raw bodies of unparseable responses)

Resumable: rerunning skips RCTs that already have a non-empty irb_location_class.

Env flags:
  ANTHROPIC_MODEL   override the model (default claude-sonnet-4-5, matching 6a)
  SMOKE_N=10        only classify the first N IRB-mention RCTs (quick validation)
"""

import csv
import json
import os
import re
import sys
import time
from datetime import datetime, timezone

try:
    from anthropic import Anthropic
except ImportError:
    sys.exit("Missing anthropic package. From the project root:\n  pip install -r requirements.txt")

# ---- Configuration ----------------------------------------------------------

MODEL = os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-4-5")
PROMPT_VERSION = "irb-location-v1"
MAX_TOKENS = 1024
SAVE_EVERY = 25
SMOKE_N = int(os.environ.get("SMOKE_N", "0"))

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_DIR = os.path.abspath(os.path.join(SCRIPT_DIR, ".."))
IRB_CSV = os.path.join(PROJECT_DIR, "data", "irb_mentions.csv")
COUNTRY_CSV = os.path.join(PROJECT_DIR, "data", "country_classified.csv")
OUT_CSV = os.path.join(PROJECT_DIR, "data", "irb_classified.csv")
LOG_TXT = os.path.join(PROJECT_DIR, "data", "13_irb_classify.log")
DEBUG_TXT = os.path.join(PROJECT_DIR, "data", "13_irb_classify_failures.log")

ISO3_RE = re.compile(r"^[A-Z]{3}$")

FIELDS = [
    "doi", "journal_short", "publication_year", "title",
    "study_setting", "study_iso3_list",
    "irb_status",               # carried from Stage 12: ok / no_mention / no_pdf
    "irb_terms_matched",        # carried from Stage 12
    "irb_institutions",         # ; -joined approving-body names (LLM)
    "irb_iso3_list",            # ; -joined unique ISO3 of those bodies (LLM)
    "irb_country_identified",   # TRUE if >=1 institution had an identifiable country
    "irb_in_study_country",     # TRUE if any identified IRB is in the study country
    "irb_location_class",       # local_only|foreign_only|both|undetermined|no_mention|no_pdf
    "irb_n_institutions",
    "irb_approval_status",      # approved|exempt|mentioned_only|unclear (LLM)
    "irb_justification",        # one-sentence LLM rationale
    "irb_llm_model", "irb_llm_prompt_version", "snapshot_utc",
]


def log(msg):
    line = f"[{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%SZ')}] {msg}"
    print(line, flush=True)
    with open(LOG_TXT, "a", encoding="utf-8") as f:
        f.write(line + "\n")


# --- tolerant JSON extraction (same approach as 06a_country_extract.py) -------

def _find_balanced_json_objects(text):
    """Return all top-level balanced `{...}` substrings, ignoring braces inside
    JSON string literals. Lets us recover the model's final answer when it emits
    an initial JSON, then prose, then a corrected JSON."""
    out = []
    depth = 0
    start = None
    in_string = False
    escape = False
    for i, ch in enumerate(text):
        if escape:
            escape = False
            continue
        if in_string:
            if ch == "\\":
                escape = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
        elif ch == "{":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "}":
            if depth > 0:
                depth -= 1
                if depth == 0 and start is not None:
                    out.append(text[start:i + 1])
                    start = None
    return out


def parse_json_response(text):
    """Locate all balanced top-level `{...}` blocks and json.loads() the LAST one
    (the model's corrected final answer if it self-revised). Fall back to the raw
    text so the caller sees a real JSONDecodeError on total failure."""
    t = text.strip()
    for block in reversed(_find_balanced_json_objects(t)):
        try:
            return json.loads(block)
        except json.JSONDecodeError:
            continue
    return json.loads(t)


# --- prompt ------------------------------------------------------------------

def build_system_prompt():
    return """You extract research-ethics oversight information from an economics paper.

You are given short text excerpts (snippets) that a keyword scanner pulled from a single paper because they matched terms like "IRB", "Institutional Review Board", "ethics/ethical", "ethical approval", or "human subjects". Some snippets come from Elsevier PDFs that lost all inter-word spaces, so you will sometimes see run-together text like "institutionalreviewboardofmcgilluniversity" -- read it as normal words.

You are also told the study country/countries (where the data were collected), for context only.

Your task: identify every distinct body that APPROVED, reviewed, or granted an ethics determination for THIS study, and the country in which that body is located.

Rules:
- An approving body is an Institutional Review Board (IRB), ethics committee/board, research ethics committee, ministry/national ethics body, or a university/organization named as having granted ethics approval or an exemption.
- For each body, give its country as an ISO 3166-1 alpha-3 code (e.g., USA, GBR, CAN, AUS, DEU, KEN, IND, UGA). Infer the country from the institution name when it is unambiguous: "Columbia University" -> USA, "McGill University" -> CAN, "Australian National University" -> AUS, "London School of Economics" / "Oxford" -> GBR, "University of Nairobi" -> KEN, "Innovations for Poverty Action" (IPA's IRB) -> USA, "Indian Institute of Management" -> IND, "<Country> Ministry of Health ethics committee" -> that country.
- If a body is named but its country genuinely cannot be inferred, include it with "iso3": "" (empty). If approval is referenced but NO body is named at all (e.g., "the study received IRB approval"), return an empty institutions list.
- Do NOT treat trial REGISTRIES as IRBs. "AEA RCT Registry / AEARCTR-...", "ClinicalTrials.gov / NCT...", "ISRCTN", "RIDIE", and "pre-analysis plan" are registrations, not ethics approvals -- ignore them.
- Do NOT invent an IRB that is not in the text. It is correct to return an empty list.
- A single study often lists BOTH a home-institution IRB (frequently in a high-income country) AND a local ethics body in the study country -- include all of them as separate entries.
- approval_status: "approved" if the text says ethics approval/clearance/permission was granted; "exempt" if the study was deemed exempt or not human-subjects research; "mentioned_only" if ethics/IRB is referenced without a clear approval or exemption; "unclear" otherwise.

Respond with ONLY a single JSON object in this exact format and nothing else:
{
  "institutions": [
    {"name": "<approving body as written>", "iso3": "<ISO3 or empty>", "country_name": "<country or empty>"}
  ],
  "approval_status": "approved" | "exempt" | "mentioned_only" | "unclear",
  "justification": "<one short sentence naming the body/bodies and their location>"
}"""


def extract_one(client, system_prompt, study_ctx, snippets):
    user = (f"Study country (data collected in): {study_ctx}\n\n"
            f"IRB/ethics snippets from the paper:\n{snippets}")
    msg = client.messages.create(
        model=MODEL,
        max_tokens=MAX_TOKENS,
        temperature=0,
        system=[{"type": "text", "text": system_prompt,
                 "cache_control": {"type": "ephemeral"}}],
        messages=[{"role": "user", "content": user}],
    )
    text = msg.content[0].text
    try:
        return parse_json_response(text), msg.usage, text
    except json.JSONDecodeError:
        return None, msg.usage, text


# --- helpers -----------------------------------------------------------------

def load_study_countries():
    """doi -> (study_setting, set(study_iso3))."""
    out = {}
    with open(COUNTRY_CSV, encoding="utf-8") as f:
        for r in csv.DictReader(f):
            doi = (r.get("doi") or "").strip().lower()
            if not doi:
                continue
            iso = {c.strip() for c in (r.get("country_iso3_list") or "").split(";") if c.strip()}
            out[doi] = ((r.get("country_study_setting") or "").strip(), iso)
    return out


def classify_location(irb_iso3s, study_iso3s):
    """Deterministic comparison. irb_iso3s/study_iso3s are sets of ISO3 strings.
    Returns (location_class, in_study_country_bool, country_identified_bool)."""
    country_identified = len(irb_iso3s) > 0
    if not country_identified:
        return "undetermined", False, False
    if not study_iso3s:
        # IRB country known but study country not a single identifiable country
        # (cross_country / no_country / unlisted HIC). No comparison possible.
        return "undetermined", False, True
    local = bool(irb_iso3s & study_iso3s)
    foreign = bool(irb_iso3s - study_iso3s)
    if local and foreign:
        cls = "both"
    elif local:
        cls = "local_only"
    else:
        cls = "foreign_only"
    return cls, local, True


def main():
    open(LOG_TXT, "w").close()
    log(f"Stage 13 (IRB location) start. Model={MODEL}  Prompt={PROMPT_VERSION}  SMOKE_N={SMOKE_N}")

    study = load_study_countries()
    log(f"Loaded study-country attribution for {len(study)} papers from country_classified.csv")

    with open(IRB_CSV, encoding="utf-8") as f:
        irb_rows = list(csv.DictReader(f))
    log(f"Stage-12 IRB rows: {len(irb_rows)}  "
        f"(ok={sum(r['extraction_status']=='ok' for r in irb_rows)}, "
        f"no_mention={sum(r['extraction_status']=='no_mention' for r in irb_rows)}, "
        f"no_pdf={sum(r['extraction_status']=='no_pdf' for r in irb_rows)})")

    # Resume: load any already-classified rows, keyed by DOI.
    done = {}
    if os.path.exists(OUT_CSV):
        with open(OUT_CSV, encoding="utf-8") as f:
            for r in csv.DictReader(f):
                if (r.get("irb_location_class") or "").strip():
                    done[(r.get("doi") or "").strip().lower()] = r
        log(f"Resuming: {len(done)} RCTs already classified")

    system_prompt = build_system_prompt()
    client = Anthropic()
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    out_rows = []
    todo = []   # indices into irb_rows that need an LLM call
    for idx, r in enumerate(irb_rows):
        doi = (r.get("doi") or "").strip().lower()
        setting, study_iso = study.get(doi, ("", set()))
        status = r.get("extraction_status") or ""
        base = {
            "doi": doi, "journal_short": r.get("journal_short", ""),
            "publication_year": r.get("publication_year", ""), "title": r.get("title", ""),
            "study_setting": setting, "study_iso3_list": ";".join(sorted(study_iso)),
            "irb_status": status, "irb_terms_matched": r.get("terms_matched", ""),
            "irb_institutions": "", "irb_iso3_list": "",
            "irb_country_identified": "", "irb_in_study_country": "",
            "irb_location_class": "", "irb_n_institutions": "",
            "irb_approval_status": "", "irb_justification": "",
            "irb_llm_model": "", "irb_llm_prompt_version": "", "snapshot_utc": stamp,
        }
        if doi in done:
            out_rows.append(done[doi])
            continue
        if status == "no_mention":
            base["irb_location_class"] = "no_mention"
            out_rows.append(base)
            continue
        if status != "ok":   # no_pdf (or pdf_error)
            base["irb_location_class"] = "no_pdf" if status == "no_pdf" else (status or "no_pdf")
            out_rows.append(base)
            continue
        # status == ok -> needs LLM extraction
        out_rows.append(base)            # placeholder, filled in below
        todo.append((idx, len(out_rows) - 1))

    if SMOKE_N:
        todo = todo[:SMOKE_N]
    log(f"RCTs needing LLM extraction (status ok, not yet done): {len(todo)}")

    def save():
        with open(OUT_CSV, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=FIELDS)
            w.writeheader()
            w.writerows(out_rows)

    n_done = n_err = 0
    cls_counter = {}
    tot_in = tot_out = tot_cache_read = 0
    t0 = time.time()

    for k, (src_idx, out_idx) in enumerate(todo, start=1):
        r = irb_rows[src_idx]
        doi = (r.get("doi") or "").strip().lower()
        setting, study_iso = study.get(doi, ("", set()))
        study_ctx = (f"{setting or 'unknown'}; ISO3=[{';'.join(sorted(study_iso)) or 'none listed'}]")
        snippets = r.get("snippets", "") or "(no snippets stored)"
        try:
            result, usage, raw = extract_one(client, system_prompt, study_ctx, snippets)
        except Exception as e:   # noqa: BLE001
            log(f"  [{k}] API error on {doi}: {type(e).__name__}: {e}; sleeping 5s")
            time.sleep(5)
            n_err += 1
            continue
        if result is None:
            log(f"  [{k}] could not parse JSON for {doi}: {raw[:160]!r} "
                f"(full body -> {os.path.basename(DEBUG_TXT)})")
            with open(DEBUG_TXT, "a", encoding="utf-8") as df:
                df.write(f"=== doi={doi}  k={k}  ts={datetime.now(timezone.utc).isoformat()} ===\n{raw}\n\n")
            n_err += 1
            continue

        insts = result.get("institutions") or []
        if not isinstance(insts, list):
            insts = []
        names, iso3s = [], []
        for it in insts:
            if not isinstance(it, dict):
                continue
            nm = (it.get("name") or "").strip()
            code = (it.get("iso3") or "").strip().upper()
            if nm:
                names.append(nm)
            if ISO3_RE.match(code):
                iso3s.append(code)
        irb_iso_set = set(iso3s)
        cls, in_study, identified = classify_location(irb_iso_set, study_iso)
        approval = (result.get("approval_status") or "").strip().lower()
        if approval not in ("approved", "exempt", "mentioned_only", "unclear"):
            approval = "unclear"
        just = (result.get("justification") or "").strip()

        row = out_rows[out_idx]
        row["irb_institutions"] = " ; ".join(names)
        row["irb_iso3_list"] = ";".join(sorted(irb_iso_set))
        row["irb_country_identified"] = "TRUE" if identified else "FALSE"
        row["irb_in_study_country"] = "TRUE" if in_study else "FALSE"
        row["irb_location_class"] = cls
        row["irb_n_institutions"] = str(len(names))
        row["irb_approval_status"] = approval
        row["irb_justification"] = just
        row["irb_llm_model"] = MODEL
        row["irb_llm_prompt_version"] = PROMPT_VERSION

        cls_counter[cls] = cls_counter.get(cls, 0) + 1
        if usage:
            tot_in += getattr(usage, "input_tokens", 0) or 0
            tot_out += getattr(usage, "output_tokens", 0) or 0
            tot_cache_read += getattr(usage, "cache_read_input_tokens", 0) or 0
        n_done += 1
        if (n_done % SAVE_EVERY) == 0:
            save()
            rate = n_done / max(time.time() - t0, 1)
            log(f"  progress {n_done}/{len(todo)}  "
                f"{ {k2: cls_counter[k2] for k2 in sorted(cls_counter)} }  "
                f"err={n_err}  tok in={tot_in} cached_read={tot_cache_read} out={tot_out}  "
                f"{rate:.2f} req/s")

    save()

    # Summary over the full output (all RCTs, including no_mention / no_pdf).
    final_counts = {}
    for r in out_rows:
        c = r.get("irb_location_class") or "(blank)"
        final_counts[c] = final_counts.get(c, 0) + 1
    log("\n=== IRB location summary (all RCTs) ===")
    for c in sorted(final_counts):
        log(f"  {c:14s} {final_counts[c]}")
    # Among papers with an identified IRB country, how many are local vs foreign.
    comparable = sum(final_counts.get(c, 0) for c in ("local_only", "foreign_only", "both"))
    if comparable:
        loc = final_counts.get("local_only", 0)
        both = final_counts.get("both", 0)
        log(f"Of {comparable} RCTs with an identifiable IRB country AND a single study country: "
            f"{loc} local_only, {final_counts.get('foreign_only', 0)} foreign_only, {both} both "
            f"({100*(loc+both)/comparable:.0f}% have at least one IRB in the study country)")
    log(f"Classified this run: {n_done}  errors: {n_err}")
    log(f"Tokens: input={tot_in} cache_read={tot_cache_read} output={tot_out}")
    log(f"\nOutput: {os.path.basename(OUT_CSV)}")


if __name__ == "__main__":
    main()
