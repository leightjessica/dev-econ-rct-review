#!/usr/bin/env python3
"""Stage 21 - locate and extract the SUBNATIONAL STUDY SITE of each RCT (LLM).

A new line of analysis (2026-07-22): for every single-country RCT whose study
country appears in the World Bank AM25 subnational poverty/inequality release, we
extract the subnational site at which the trial was fielded, so that it can be
matched (Stage 22) to WB poverty, inequality, and multidimensional-deprivation
indicators for that place.

Design rule (project lead, 2026-07-22): we do NOT extract finer than the WB can
resolve. Each country carries a "matching level" -- the finest administrative
level at which the WB AM25 data actually tiles that country (built in
data/wb_subnat_coverage_profile.csv). For most countries that is admin-1
(state/province/region); for a real minority (Malawi, Rwanda, Sierra Leone, Sri
Lanka, Philippines) the WB resolves admin-2 (district); for a handful (Uganda,
Uruguay, Argentina, Vietnam, ...) the WB only offers a survey grouping COARSER
than admin-1. This stage passes each country's target level and its admissible
unit names (data/wb_subnat_gazetteer.csv) into the prompt as a controlled
vocabulary, so the model maps the study site directly onto WB-canonical names and
the Stage-22 merge is mostly exact.

Transport: the Anthropic API via the `anthropic` SDK (client.messages.create),
matching Stages 3a/3b -- NOT the headless `claude -p` path used by Stages 16/17,
whose subscription OAuth is unavailable behind enterprise controls on this
machine (2026-07-22). Requires ANTHROPIC_API_KEY in the environment. The static
system prompt is cached (ephemeral) for cost efficiency; temperature is 0. The
costly doi -> PDF match is reused verbatim from data/irb_mentions.csv. This stage
is a LOCATOR/EXTRACTOR; the study-vs-WB RESOLUTION and indicator merge are
deterministic in Stage 22.

Scope handling (project lead, 2026-07-22): each RCT is flagged single_region /
multi_region / nationwide / unspecified. Only single_region rows carry a clean
one-to-one match downstream; the rest are carried with their flag so the analysis
denominator stays transparent.

Inputs:
  data/country_classified.csv          (doi, rct_classification, country_* fields)
  data/irb_mentions.csv                (doi -> matched_file; the fuller 389-PDF map
                                        assembled for the IRB work, reused here)
  data/wb_subnat_coverage_profile.csv  (iso3 -> matching_level; Stage 20)
  data/wb_subnat_gazetteer.csv         (iso3, ..., unit_name; Stage 20)
  fulltext/<matched_file>.pdf

Output:
  data/site_extracted.csv              one row per RCT
  data/21_site_extract.log

Env flags:
  SMOKE_N=6            only extract the first N work-list papers
  SMOKE_ISO3=IND,MWI  restrict the work-list to these countries (<=2 each) - for
                      a diverse smoke test across admin-1 / admin-2 / grouped
  ANTHROPIC_API_KEY   (required) Anthropic API key
  ANTHROPIC_MODEL     model (default claude-sonnet-4-5)
"""

import csv
import json
import os
import subprocess
import sys
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

try:
    from anthropic import Anthropic
    import anthropic
except ImportError:
    sys.exit("Missing anthropic package. From the project root:\n"
             "  pip install -r requirements.txt")

csv.field_size_limit(min(sys.maxsize, 2147483647))

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
FULLTEXT = ROOT / "fulltext"
CC_CSV = DATA / "country_classified.csv"
MATCH_CSV = DATA / "irb_mentions.csv"
PROFILE_CSV = DATA / "wb_subnat_coverage_profile.csv"
GAZ_CSV = DATA / "wb_subnat_gazetteer.csv"
OUT_CSV = DATA / "site_extracted.csv"
LOG_TXT = DATA / "21_site_extract.log"

MODEL = os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-4-5")
PROMPT_VERSION = "site-extract-v1"
SMOKE_N = int(os.environ.get("SMOKE_N", "0"))
SMOKE_ISO3 = [s.strip().upper() for s in os.environ.get("SMOKE_ISO3", "").split(",") if s.strip()]

MAX_TOKENS = 700
CALL_RETRIES = 4
RETRY_BACKOFF = 5

# Approximate Sonnet 4.5 unit prices (USD / 1M tokens), for run-cost logging only.
PRICE_IN, PRICE_OUT, PRICE_CACHE_R, PRICE_CACHE_W = 3.0, 15.0, 0.30, 3.75

# How much paper text to send. The study site is nearly always named in the
# abstract, introduction, and context/experimental-design sections, which sit in
# the first pages; a generous head window captures them without shipping the whole
# paper (references, appendices).
TEXT_HEAD_CHARS = 32000

# GAUL family tags that denote a survey grouping COARSER than admin-1.
GROUPED_TAGS = {"GAULx", "GADMx", "NUTSx", "UNx"}

FIELDS = [
    "doi", "journal_short", "publication_year", "title", "matched_file",
    "iso3", "country_setting", "matching_level", "level_kind",
    "site_scope", "matched_units", "raw_site_text", "n_units_named",
    "fieldwork_year_start", "fieldwork_year_end",
    "extract_confidence", "extract_justification", "extract_status",
    "llm_model", "llm_prompt_version", "snapshot_utc",
]

SYSTEM_PROMPT = r"""You extract the SUBNATIONAL STUDY SITE of a development-economics randomized controlled trial (RCT) from the paper's text, and return it as strict JSON.

You are given, for one paper: the study COUNTRY, a TARGET administrative LEVEL, a list of ADMISSIBLE UNIT NAMES at that level (the controlled vocabulary), and the paper's title, abstract, and leading text.

Your task: determine WHERE INSIDE THE COUNTRY the trial's data were collected (the fieldwork site), expressed at the target level, and map it onto the admissible unit names.

Rules:
1. Report the site of the EXPERIMENT / DATA COLLECTION, not the authors' institutions, not places mentioned only as prior literature or comparison.
2. TARGET LEVEL. Return the site at the given target level only. If the paper names a finer place (a village, town, district) than the target level, roll it UP to the target-level unit that contains it, and put the finer place text in raw_site_text. Do NOT invent detail finer than the target level.
3. CONTROLLED VOCABULARY. When you can identify the target-level unit(s), return them using the EXACT spelling from the admissible-unit list in "matched_units" (an array). If the site is identifiable but you cannot confidently map it to a name on the list, leave matched_units empty and put your best plain-language name(s) in raw_site_text.
4. SITE SCOPE ("site_scope"), choose exactly one:
   - "single_region": the trial is confined to ONE target-level unit.
   - "multi_region": the trial spans TWO OR MORE named target-level units.
   - "nationwide": the sample is national / nationally representative / spread across the whole country with no subnational concentration.
   - "unspecified": the paper does not identify the subnational site clearly enough to place it.
   For multi_region, list every named target-level unit in matched_units.
5. FIELDWORK YEARS. Give the calendar years of data collection (baseline year in fieldwork_year_start, latest/endline year in fieldwork_year_end). Use the year the survey/experiment was run in the field, NOT the publication year. If a single year, set both equal. If not stated, use null for both.
6. Never guess a specific unit to satisfy the format. Prefer "unspecified" over a fabricated match. Base everything only on the provided text.

Return ONLY this JSON object, no prose:
{
  "site_scope": "single_region|multi_region|nationwide|unspecified",
  "matched_units": ["<exact admissible name>", ...],
  "raw_site_text": "<verbatim place phrase(s) from the paper, or ''>",
  "fieldwork_year_start": <int or null>,
  "fieldwork_year_end": <int or null>,
  "confidence": "high|medium|low",
  "justification": "<one sentence citing the locating phrase>"
}
"""


def log(msg):
    line = f"[{datetime.now().strftime('%H:%M:%S')}] {msg}"
    print(line, flush=True)
    with open(LOG_TXT, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def pdf_to_text(pdf_path):
    proc = subprocess.run(
        ["pdftotext", "-enc", "UTF-8", "-q", str(pdf_path), "-"],
        capture_output=True, text=True, encoding="utf-8", timeout=120,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"pdftotext exit {proc.returncode}")
    return proc.stdout or ""


def parse_inner_json(text):
    t = (text or "").strip()
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


def call_api(client, user_block):
    """One extraction call. Retries transient API errors (rate limit, overload,
    connection/timeout) with linear backoff; JSON parsing happens after a
    successful response and is not retried as an API failure."""
    last = None
    msg = None
    for attempt in range(1, CALL_RETRIES + 1):
        try:
            msg = client.messages.create(
                model=MODEL,
                max_tokens=MAX_TOKENS,
                temperature=0,
                system=[{"type": "text", "text": SYSTEM_PROMPT,
                         "cache_control": {"type": "ephemeral"}}],
                messages=[{"role": "user", "content": user_block}],
            )
            break
        except (anthropic.APIStatusError, anthropic.APIConnectionError) as e:
            last = e
            if attempt < CALL_RETRIES:
                time.sleep(RETRY_BACKOFF * attempt)
                continue
            raise
    if msg is None:  # pragma: no cover - defensive
        raise last
    return parse_inner_json(msg.content[0].text), msg.usage


def level_kind(tag):
    """Human-facing description + coarse class of the WB matching level."""
    t = tag or ""
    if t in GROUPED_TAGS:
        return ("grouped", "a World Bank survey region -- a GROUP of several "
                "districts/provinces (coarser than a single admin-1 unit); the "
                "trial's site may be finer, but only these groupings are available")
    # admin-2 families
    if t in ("GAUL2", "GADM2", "NUTS3"):
        return ("admin2", "a second-level administrative unit (district / "
                "department / municipality)")
    # everything else keying to the first subnational level
    return ("admin1", "a first-level administrative unit (state / province / "
            "region)")


def load_meta():
    """doi -> {journal, year, title, iso3, setting} for RCTs only."""
    meta = {}
    with open(CC_CSV, encoding="utf-8") as f:
        for r in csv.DictReader(f):
            if r["rct_classification"] != "yes":
                continue
            isos = [x.strip() for x in (r["country_iso3_list"] or "").split(";") if x.strip()]
            meta[r["doi"]] = {
                "journal_short": r["journal_short"],
                "publication_year": r["publication_year"],
                "title": r["title"],
                "setting": r["country_study_setting"],
                "iso_list": isos,
            }
    return meta


def load_matches():
    """doi -> matched_file, from the IRB-stage map, keeping only rows whose PDF
    actually exists on disk (a few rows may carry a filename but no file)."""
    m = {}
    with open(MATCH_CSV, encoding="utf-8") as f:
        for r in csv.DictReader(f):
            mf = (r.get("matched_file") or "").strip()
            if mf and (FULLTEXT / mf).exists():
                m[r["doi"]] = mf
    return m


def load_profile():
    prof = {}
    with open(PROFILE_CSV, encoding="utf-8") as f:
        for r in csv.DictReader(f):
            prof[r["iso3"]] = r
    return prof


def load_gazetteer():
    gaz = defaultdict(list)  # iso3 -> [unit_name,...] at matching level
    with open(GAZ_CSV, encoding="utf-8") as f:
        for r in csv.DictReader(f):
            gaz[r["iso3"]].append(r["unit_name"])
    return gaz


def build_user_block(m, iso3, level_desc, unit_names, title, abstract, body):
    names = "\n".join(f"  - {n}" for n in unit_names) if unit_names else "  (none available)"
    return (
        f"STUDY COUNTRY (ISO3): {iso3}\n"
        f"TARGET LEVEL: {level_desc}\n\n"
        f"ADMISSIBLE UNIT NAMES at the target level (use EXACT spellings):\n{names}\n\n"
        f"PAPER TITLE: {title}\n\n"
        f"ABSTRACT:\n{abstract or '(none)'}\n\n"
        f"PAPER TEXT (leading pages):\n{body}\n"
    )


def main():
    open(LOG_TXT, "w").close()
    log(f"Stage 21 (site extract) start. Model={MODEL} Prompt={PROMPT_VERSION} "
        f"SMOKE_N={SMOKE_N} SMOKE_ISO3={SMOKE_ISO3 or '(all)'}")
    if not os.environ.get("ANTHROPIC_API_KEY"):
        sys.exit("ANTHROPIC_API_KEY is not set in the environment. Set it, then re-run.")
    client = Anthropic()

    meta = load_meta()
    matches = load_matches()
    prof = load_profile()
    gaz = load_gazetteer()
    log(f"RCTs: {len(meta)}  matched PDFs: {len(matches)}  "
        f"profiled countries: {len(prof)}")

    # Abstracts live in country_classified.csv too; pull them for the prompt.
    abstracts = {}
    with open(CC_CSV, encoding="utf-8") as f:
        for r in csv.DictReader(f):
            abstracts[r["doi"]] = r.get("abstract", "")

    # Resume
    done = {}
    if OUT_CSV.exists():
        with open(OUT_CSV, encoding="utf-8") as f:
            for r in csv.DictReader(f):
                if r.get("extract_status") == "ok":
                    done[r["doi"]] = r
        log(f"Resuming: {len(done)} already extracted ok")

    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    def base_of(doi, m):
        iso = m["iso_list"][0] if m["iso_list"] else ""
        return {
            "doi": doi, "journal_short": m["journal_short"],
            "publication_year": m["publication_year"], "title": m["title"],
            "matched_file": matches.get(doi, ""), "iso3": iso,
            "country_setting": m["setting"], "matching_level": "",
            "level_kind": "", "site_scope": "", "matched_units": "",
            "raw_site_text": "", "n_units_named": "",
            "fieldwork_year_start": "", "fieldwork_year_end": "",
            "extract_confidence": "", "extract_justification": "",
            "extract_status": "", "llm_model": MODEL,
            "llm_prompt_version": PROMPT_VERSION, "snapshot_utc": stamp,
        }

    # Classify every RCT into a status; build the LLM work-list.
    passthrough = []   # rows we can decide without the LLM
    worklist = []      # (doi, meta, iso3, matching_level, kind, level_desc, unit_names)
    per_iso_smoke = defaultdict(int)

    for doi, m in meta.items():
        b = base_of(doi, m)
        setting = m["setting"]
        iso = m["iso_list"][0] if m["iso_list"] else ""
        # Non-single-country: cannot resolve to one country's subnational grid.
        if setting != "single_country" or len(m["iso_list"]) != 1:
            b["extract_status"] = "not_single_country"
            passthrough.append(b)
            continue
        p = prof.get(iso)
        if not p or p["matching_level"] in ("", "NONE"):
            b["extract_status"] = "country_not_covered"
            passthrough.append(b)
            continue
        if doi not in matches:
            b["extract_status"] = "no_pdf"
            b["matching_level"] = p["matching_level"]
            passthrough.append(b)
            continue
        kind, desc = level_kind(p["matching_level"])
        worklist.append((doi, m, iso, p["matching_level"], kind, desc, gaz.get(iso, [])))

    # Smoke restriction
    if SMOKE_ISO3:
        filt = []
        for item in worklist:
            iso = item[2]
            if iso in SMOKE_ISO3 and per_iso_smoke[iso] < 2:
                per_iso_smoke[iso] += 1
                filt.append(item)
        worklist = filt
    worklist = [w for w in worklist if w[0] not in done]
    if SMOKE_N:
        worklist = worklist[:SMOKE_N]

    log(f"Pass-through rows: {len(passthrough)}  "
        f"(not_single_country / country_not_covered / no_pdf)")
    log(f"LLM work-list this run: {len(worklist)}")

    out_rows = list(done.values())
    n_ok = n_err = 0
    tok_in = tok_out = tok_cr = tok_cw = 0
    t0 = time.time()

    def est_cost():
        return (tok_in * PRICE_IN + tok_out * PRICE_OUT
                + tok_cr * PRICE_CACHE_R + tok_cw * PRICE_CACHE_W) / 1e6

    def save():
        with open(OUT_CSV, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=FIELDS)
            w.writeheader()
            w.writerows(out_rows + passthrough)

    try:
        for k, (doi, m, iso, mlvl, kind, desc, unit_names) in enumerate(worklist, 1):
            b = base_of(doi, m)
            b["matching_level"] = mlvl
            b["level_kind"] = kind
            pdf = FULLTEXT / matches[doi]
            try:
                text = pdf_to_text(pdf)
            except Exception as e:  # noqa: BLE001
                log(f"  [{k}/{len(worklist)}] pdf error {doi}: {e}")
                b["extract_status"] = "pdf_error"
                out_rows.append(b)
                continue
            body = text[:TEXT_HEAD_CHARS]
            block = build_user_block(m, iso, desc, unit_names, m["title"],
                                     abstracts.get(doi, ""), body)
            try:
                res, usage = call_api(client, block)
            except Exception as e:  # noqa: BLE001
                log(f"  [{k}/{len(worklist)}] call error {doi}: {type(e).__name__}: {e}")
                b["extract_status"] = "call_error"
                out_rows.append(b)
                n_err += 1
                continue
            tok_in += getattr(usage, "input_tokens", 0) or 0
            tok_out += getattr(usage, "output_tokens", 0) or 0
            tok_cr += getattr(usage, "cache_read_input_tokens", 0) or 0
            tok_cw += getattr(usage, "cache_creation_input_tokens", 0) or 0
            units = [str(x).strip() for x in (res.get("matched_units") or []) if str(x).strip()]
            b.update(
                site_scope=res.get("site_scope", ""),
                matched_units=";".join(units),
                raw_site_text=(res.get("raw_site_text") or "")[:500],
                n_units_named=len(units),
                fieldwork_year_start=res.get("fieldwork_year_start") if res.get("fieldwork_year_start") is not None else "",
                fieldwork_year_end=res.get("fieldwork_year_end") if res.get("fieldwork_year_end") is not None else "",
                extract_confidence=res.get("confidence", ""),
                extract_justification=(res.get("justification") or "")[:500],
                extract_status="ok",
            )
            out_rows.append(b)
            n_ok += 1
            log(f"  [{k}/{len(worklist)}] {doi} {iso}/{mlvl} -> "
                f"{b['site_scope']} {b['matched_units'] or b['raw_site_text'][:40]!r}")
            if k % 10 == 0:
                save()
                log(f"  checkpoint: {n_ok} ok, {n_err} err, ~${est_cost():.2f}, "
                    f"{(time.time()-t0)/k:.1f}s/paper")
    finally:
        save()

    log(f"Done. ok={n_ok} err={n_err} passthrough={len(passthrough)} "
        f"tokens(in/out/cacheR/cacheW)={tok_in}/{tok_out}/{tok_cr}/{tok_cw} "
        f"est_cost=~${est_cost():.2f} elapsed={time.time()-t0:.0f}s")
    log(f"Wrote {OUT_CSV} ({len(out_rows)+len(passthrough)} rows)")


if __name__ == "__main__":
    main()
