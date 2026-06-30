"""
Macro extension, Stage 2: Merge EconLit JEL codes onto the macro OpenAlex pull,
backfill abstracts from EconLit, and apply the development-paper filter.

Standalone screen for two macroeconomics journals, separate from the main
12-journal dataset. Reads and writes only under data/macro/ (plus the shared,
journal-agnostic data/jel_lookup.csv and data/lmic_countries.csv).

The development-filter logic is reused verbatim from scripts/02_dev_filter.py
(imported as a module) so the operationalization of "development" is identical
to the main project. Only the journal scope and file paths differ.

Inputs
------
  data/macro/raw_openalex_macro_2021_2025.csv   (m01 output)
  data/macro/EconLit/*.csv                       (manual EBSCO exports)
  data/jel_lookup.csv                            (shared Stage 0 output)
  data/lmic_countries.csv                        (shared Stage 0b output)

Output
------
  data/macro/dev_filtered_macro.csv
"""

import csv
import importlib.util
import os
import sys
from collections import defaultdict
from datetime import datetime, timezone

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_DIR = os.path.abspath(os.path.join(SCRIPT_DIR, ".."))

# ---- Import the main Stage 2 module for its helper functions ----------------
# The filename begins with a digit, so import via importlib rather than `import`.
_spec = importlib.util.spec_from_file_location(
    "dev_filter_main", os.path.join(SCRIPT_DIR, "02_dev_filter.py")
)
S2 = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(S2)

# ---- Override journal scope -------------------------------------------------
# journal_for_issns() reads the module global JOURNAL_ISSNS, so patch it.
S2.JOURNAL_ISSNS = {
    "1945-7707": "AEJ_Macro", "1945-7715": "AEJ_Macro",
    "0164-0704": "JMacro",    "1873-152X": "JMacro",
}
MACRO_JOURNALS = ["AEJ_Macro", "JMacro"]

# ---- Paths (all under data/macro/, except the two shared lookups) -----------
# Prefer the abstract-backfilled pull (m01b/m01c) if present; else the raw pull.
_BACKFILLED = os.path.join(PROJECT_DIR, "data", "macro", "raw_with_abstracts_macro_2021_2025.csv")
_RAW = os.path.join(PROJECT_DIR, "data", "macro", "raw_openalex_macro_2021_2025.csv")
OA_CSV = _BACKFILLED if os.path.exists(_BACKFILLED) else _RAW
ECONLIT_DIR = os.path.join(PROJECT_DIR, "data", "macro", "EconLit")
JEL_LOOKUP_CSV = os.path.join(PROJECT_DIR, "data", "jel_lookup.csv")
LMIC_CSV = os.path.join(PROJECT_DIR, "data", "lmic_countries.csv")
OUT_CSV = os.path.join(PROJECT_DIR, "data", "macro", "dev_filtered_macro.csv")
LOG_TXT = os.path.join(PROJECT_DIR, "data", "macro", "m02_dev_filter.log")

DEV_O_PREFIXES = S2.DEV_O_PREFIXES  # ("O1", "O2")


def log(msg):
    line = f"[{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%SZ')}] {msg}"
    print(line, flush=True)
    with open(LOG_TXT, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def main():
    open(LOG_TXT, "w").close()
    log("Macro Stage 2 start.")

    # ---- 1. JEL lookup --------------------------------------------------
    log(f"Loading JEL lookup: {JEL_LOOKUP_CSV}")
    desc_to_code, bare_to_codes = S2.load_jel_lookup(JEL_LOOKUP_CSV)
    log(f"  {len(desc_to_code):,} full-descriptor->code entries")
    log(f"  {len(bare_to_codes):,} bare descriptors")

    # ---- 1b. LMIC country pattern --------------------------------------
    log(f"Loading LMIC country pattern: {LMIC_CSV}")
    lmic_pattern, term_to_iso3 = S2.load_lmic_pattern(LMIC_CSV)
    log(f"  {len(term_to_iso3):,} country/region match terms")

    # ---- 2. EconLit files -----------------------------------------------
    if not os.path.isdir(ECONLIT_DIR):
        log(f"ERROR: EconLit directory not found at {ECONLIT_DIR}")
        sys.exit(1)
    files = sorted(f for f in os.listdir(ECONLIT_DIR) if f.endswith(".csv"))
    if not files:
        log(f"ERROR: no .csv files in {ECONLIT_DIR}")
        sys.exit(1)
    log(f"Loading EconLit exports from {ECONLIT_DIR} ({len(files)} files)")
    econlit_rows = []
    for fn in files:
        with open(os.path.join(ECONLIT_DIR, fn), encoding="utf-8-sig", newline="") as f:
            n = 0
            for r in csv.DictReader(f):
                econlit_rows.append(r)
                n += 1
        log(f"  {fn}: {n} rows")
    log(f"  total EconLit rows loaded: {len(econlit_rows):,}")

    # ---- 3. Filter to scope ---------------------------------------------
    log("Filtering EconLit rows to the 2 macro journals x 2021-2025")
    in_scope = []
    drop_no_issn_match = 0
    drop_year = 0
    for r in econlit_rows:
        jshort = S2.journal_for_issns(r.get("issns", ""))
        if not jshort:
            drop_no_issn_match += 1
            continue
        pubdate = (r.get("publicationDate") or "").strip()
        year = pubdate[:4] if len(pubdate) >= 4 and pubdate[:4].isdigit() else ""
        if not year or not (2021 <= int(year) <= 2025):
            drop_year += 1
            continue
        r["_journal_short"] = jshort
        r["_year"] = year
        in_scope.append(r)
    log(f"  kept: {len(in_scope):,}; dropped (out of scope): {drop_no_issn_match:,}; dropped (year): {drop_year:,}")
    by_j = defaultdict(int)
    for r in in_scope:
        by_j[r["_journal_short"]] += 1
    for j in MACRO_JOURNALS:
        log(f"    {j:12s} {by_j.get(j,0):>5d}")

    # ---- 4. Parse JEL codes; build DOI / vol-iss-pg / title indexes -----
    log("Parsing JEL codes and building match indexes")
    econlit_by_doi, econlit_by_vip, econlit_by_title = {}, {}, {}
    n_with_jel = n_with_o = 0
    unresolved_counter = defaultdict(int)
    for r in in_scope:
        codes, unres = S2.parse_subjects(r.get("subjects", ""), desc_to_code, bare_to_codes)
        for u in unres:
            unresolved_counter[u] += 1
        rec = {
            "journal_short": r["_journal_short"],
            "year": r["_year"],
            "subjects_raw": r.get("subjects") or "",
            "jel_codes": codes,
            "abstract_econlit": (r.get("abstract") or "").strip(),
        }
        if codes:
            n_with_jel += 1
        if any(c.startswith("O") for c in codes):
            n_with_o += 1
        doi = S2.normalize_doi(r.get("doi", ""))
        if doi and doi not in econlit_by_doi:
            econlit_by_doi[doi] = rec
        vip = S2.vip_key(r["_journal_short"], r.get("volume", ""), r.get("issue", ""), r.get("pageStart", ""))
        if vip and vip not in econlit_by_vip:
            econlit_by_vip[vip] = rec
        title_norm = S2.normalize_title(r.get("title", ""))
        if title_norm:
            tkey = (r["_journal_short"], title_norm)
            if tkey not in econlit_by_title:
                econlit_by_title[tkey] = rec
    log(f"  records w/ at least one JEL code:      {n_with_jel:,}")
    log(f"  records w/ at least one O-family code: {n_with_o:,}")
    log(f"  index sizes: DOI={len(econlit_by_doi):,}  VIP={len(econlit_by_vip):,}  TITLE={len(econlit_by_title):,}")
    if unresolved_counter:
        log("  unresolved descriptor tokens (top 10):")
        for tok, n in sorted(unresolved_counter.items(), key=lambda x: -x[1])[:10]:
            log(f"    [{n}x] {tok!r}")

    # ---- 5. Load OpenAlex pull ------------------------------------------
    log(f"Loading OpenAlex pull: {OA_CSV}")
    with open(OA_CSV, encoding="utf-8") as f:
        reader = csv.DictReader(f)
        oa_rows = list(reader)
        oa_fields = list(reader.fieldnames)
    log(f"  {len(oa_rows):,} rows")

    new_fields = ["jel_codes", "jel_codes_o_only", "jel_codes_o12_only",
                  "subjects_raw_econlit",
                  "country_match", "country_match_iso3", "country_match_in",
                  "is_development", "dev_filter_source",
                  "econlit_matched", "econlit_match_key", "abstract_source"]
    for nf in new_fields:
        if nf not in oa_fields:
            oa_fields.append(nf)

    # ---- 6. Merge + dev filter (logic identical to main Stage 2) --------
    log("Merging EconLit data; applying development filter")
    n_match = 0
    n_match_by = defaultdict(int)
    n_abs_backfilled = 0
    counts = defaultdict(lambda: defaultdict(int))
    src_counts = defaultdict(int)
    for r in oa_rows:
        ec = None
        match_key = ""
        doi = S2.normalize_doi(r.get("doi", ""))
        if doi:
            ec = econlit_by_doi.get(doi)
            if ec:
                match_key = "doi"
        if ec is None:
            vip = S2.vip_key(r.get("journal_short", ""), r.get("volume", ""),
                             r.get("issue", ""), r.get("first_page", ""))
            if vip:
                ec = econlit_by_vip.get(vip)
                if ec:
                    match_key = "vol_iss_page"
        if ec is None:
            tnorm = S2.normalize_title(r.get("title", ""))
            if tnorm:
                ec = econlit_by_title.get((r.get("journal_short", ""), tnorm))
                if ec:
                    match_key = "title"

        if ec:
            r["econlit_matched"] = "yes"
            r["econlit_match_key"] = match_key
            r["jel_codes"] = ";".join(ec["jel_codes"])
            r["jel_codes_o_only"] = ";".join(c for c in ec["jel_codes"] if c.startswith("O"))
            r["jel_codes_o12_only"] = ";".join(
                c for c in ec["jel_codes"]
                if any(c.startswith(p) for p in DEV_O_PREFIXES)
            )
            r["subjects_raw_econlit"] = ec["subjects_raw"]
            n_match += 1
            n_match_by[match_key] += 1
            if not (r.get("abstract") or "").strip() and ec["abstract_econlit"]:
                r["abstract"] = ec["abstract_econlit"]
                r["abstract_source"] = "econlit"
                n_abs_backfilled += 1
        else:
            r["econlit_matched"] = "no"
            r["econlit_match_key"] = ""
            r["jel_codes"] = ""
            r["jel_codes_o_only"] = ""
            r["jel_codes_o12_only"] = ""
            r["subjects_raw_econlit"] = ""

        title = r.get("title", "") or ""
        abstract = r.get("abstract", "") or ""
        match_t, iso_t = S2.find_country_mention(title, lmic_pattern, term_to_iso3)
        match_a, iso_a = S2.find_country_mention(abstract, lmic_pattern, term_to_iso3)
        if match_t:
            r["country_match"], r["country_match_iso3"], r["country_match_in"] = match_t, iso_t, "title"
        elif match_a:
            r["country_match"], r["country_match_iso3"], r["country_match_in"] = match_a, iso_a, "abstract"
        else:
            r["country_match"], r["country_match_iso3"], r["country_match_in"] = "", "", ""

        # Development filter (ordered rules; first match wins). The JDE rule is
        # inert here (neither macro journal is JDE) but kept for parity.
        jshort = r.get("journal_short", "")
        if jshort == "JDE":
            r["is_development"] = "TRUE"
            r["dev_filter_source"] = "jde_inclusion_rule"
        elif r["jel_codes_o12_only"]:
            r["is_development"] = "TRUE"
            r["dev_filter_source"] = "jel_o1_o2_code"
        elif r["country_match"]:
            r["is_development"] = "TRUE"
            r["dev_filter_source"] = "country_match"
        elif r["jel_codes"]:
            r["is_development"] = "FALSE"
            r["dev_filter_source"] = "jel_no_dev"
        elif ec is not None:
            r["is_development"] = "BORDERLINE"
            r["dev_filter_source"] = "econlit_no_jel"
        else:
            r["is_development"] = "BORDERLINE"
            r["dev_filter_source"] = "no_signal"

        counts[jshort][r["is_development"]] += 1
        src_counts[r["dev_filter_source"]] += 1

    log(f"  EconLit matches: {n_match:,} / {len(oa_rows):,} ({100*n_match/len(oa_rows):.1f}%)")
    for k in ("doi", "vol_iss_page", "title"):
        log(f"    via {k:13s}: {n_match_by[k]:,}")
    log(f"  Abstracts backfilled from EconLit: {n_abs_backfilled:,}")

    # ---- 7. Write -------------------------------------------------------
    log(f"Writing {OUT_CSV}")
    with open(OUT_CSV, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=oa_fields)
        writer.writeheader()
        writer.writerows(oa_rows)
    log(f"  wrote {len(oa_rows):,} rows")

    # ---- Summary --------------------------------------------------------
    log("--- Per-journal development-filter summary (all OpenAlex rows) ---")
    log(f"  {'journal':12s} {'TRUE':>6s} {'FALSE':>6s} {'BORDER':>7s} {'total':>6s}")
    for j in MACRO_JOURNALS:
        d = counts.get(j, {})
        t = sum(d.values())
        log(f"  {j:12s} {d.get('TRUE',0):>6d} {d.get('FALSE',0):>6d} {d.get('BORDERLINE',0):>7d} {t:>6d}")
    log("--- TRUE/BORDERLINE attribution by rule (dev_filter_source) ---")
    for src in ("jel_o1_o2_code", "country_match", "jel_no_dev", "econlit_no_jel", "no_signal"):
        log(f"  {src:18s} {src_counts.get(src,0):>5d}")
    log("Macro Stage 2 complete.")


if __name__ == "__main__":
    main()
