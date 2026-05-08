"""
Stage 2: Merge EconLit JEL codes onto the OpenAlex pull, backfill abstracts
from EconLit where missing, and apply the development-paper filter.

Inputs
------
  data/raw_with_abstracts_2021_2025.csv   (Stage 1 output, post-1c)
  data/EconLit/*.csv                      (manual EBSCO exports)
  data/jel_lookup.csv                     (Stage 0 output)

Output
------
  data/dev_filtered.csv

Logic
-----
1. Load JEL descriptor -> code lookup (both bare and prefixed forms).
2. Concatenate all EconLit CSVs in data/EconLit/.
3. Filter EconLit rows to: (a) one of the 12 in-scope journals (matched by
   ISSN), and (b) publication year in 2021-2025.
4. Parse each filtered row's `subjects` field by splitting on " ; " and
   resolving each token via the JEL lookup. Collect the set of JEL codes.
5. Index the filtered EconLit rows by normalized DOI; first occurrence wins.
6. Walk each OpenAlex row and:
   - Match on DOI to bring in JEL codes and EconLit abstract.
   - Backfill the abstract from EconLit if the OpenAlex/Crossref/S2 chain
     left it empty (this is the deferred Stage 1d backfill).
   - Apply the development filter:
       JDE                                                  -> TRUE
       any JEL code starts with 'O'                         -> TRUE
       JEL codes present, none start with 'O'               -> FALSE
       no JEL codes (no EconLit match or empty subjects)    -> BORDERLINE
   - Record `dev_filter_source` so the filter outcome is fully diagnosable.
7. Write the enriched CSV. Borderline rows are routed to LLM classification
   in a later step.
"""

import csv
import os
import re
import sys
from collections import defaultdict
from datetime import datetime, timezone

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_DIR = os.path.abspath(os.path.join(SCRIPT_DIR, ".."))

OA_CSV = os.path.join(PROJECT_DIR, "data", "raw_with_abstracts_2021_2025.csv")
ECONLIT_DIR = os.path.join(PROJECT_DIR, "data", "EconLit")
JEL_LOOKUP_CSV = os.path.join(PROJECT_DIR, "data", "jel_lookup.csv")
LMIC_CSV = os.path.join(PROJECT_DIR, "data", "lmic_countries.csv")
OUT_CSV = os.path.join(PROJECT_DIR, "data", "dev_filtered.csv")
LOG_TXT = os.path.join(PROJECT_DIR, "data", "02_dev_filter.log")

# Development filter parameters
DEV_O_PREFIXES = ("O1", "O2")  # JEL O-codes that count as development.
                                # Tightened from "any O" because O3 (innovation),
                                # O4 (growth), and parts of O5 (country studies of
                                # high-income countries) frequently appear on
                                # papers that are not development by content.

# Journal scope: ISSN -> short code. Both print and electronic ISSNs included.
JOURNAL_ISSNS = {
    "0002-8282": "AER",
    "2640-205X": "AERI", "2640-2068": "AERI",
    "1945-7782": "AEJ_Applied", "1945-7790": "AEJ_Applied",
    "1945-7731": "AEJ_EP",      "1945-774X": "AEJ_EP",
    "0012-9682": "ECMA",        "1468-0262": "ECMA",
    "0033-5533": "QJE",         "1531-4650": "QJE",
    "0022-3808": "JPE",         "1537-534X": "JPE",
    "0034-6527": "RES",         "1467-937X": "RES",
    "0034-6535": "RESTAT",      "1530-9142": "RESTAT",
    "0013-0133": "EJ",          "1468-0297": "EJ",
    "1542-4766": "JEEA",        "1542-4774": "JEEA",
    "0304-3878": "JDE",
}


def log(msg):
    line = f"[{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%SZ')}] {msg}"
    print(line, flush=True)
    with open(LOG_TXT, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def normalize_doi(doi):
    if not doi:
        return ""
    d = doi.strip().lower()
    for prefix in ("https://doi.org/", "http://doi.org/", "doi:"):
        if d.startswith(prefix):
            d = d[len(prefix):]
    return d


def normalize_descriptor(s):
    """For lookup matching: lowercase, collapse whitespace."""
    if not s:
        return ""
    return re.sub(r"\s+", " ", s).strip().lower()


def normalize_title(s):
    """Aggressive title normalization for fuzzy matching across DBs.
    Lowercase, drop punctuation, collapse whitespace, strip."""
    if not s:
        return ""
    t = s.lower()
    t = re.sub(r"[‘’“”]", "'", t)  # curly quotes -> straight
    t = re.sub(r"[^\w\s]", " ", t)  # punctuation -> space
    t = re.sub(r"\s+", " ", t).strip()
    return t


def normalize_apostrophes(s):
    return s.replace("’", "'").replace("‘", "'") if s else s


def load_lmic_pattern(path):
    """Load LMIC country list and compile a single case-insensitive regex
    that matches any country name or region term as a whole word.

    Returns (pattern, term_to_iso3): the compiled regex and a lookup from
    matched term (lowercased) to its ISO3 code (or 'REGION').
    """
    terms = []
    term_to_iso3 = {}
    with open(path, encoding="utf-8") as f:
        for r in csv.DictReader(f):
            iso3 = r["iso3"]
            for t in r["match_terms"].split(";"):
                t = t.strip()
                if not t:
                    continue
                t = normalize_apostrophes(t)
                if t.lower() not in term_to_iso3:
                    terms.append(t)
                    term_to_iso3[t.lower()] = iso3
    # Sort longer first so the regex prefers "South Africa" over a shorter sub-match
    terms.sort(key=len, reverse=True)
    # Standard \b word boundaries: matches "China" in "China's", "in China,"
    # and "Côte d'Ivoire" alike. Apostrophes are not word characters in regex,
    # so \b correctly transitions at "China'" and at "d'I".
    pattern = re.compile(
        r"\b(" + "|".join(re.escape(t) for t in terms) + r")\b",
        re.IGNORECASE,
    )
    return pattern, term_to_iso3


def find_country_mention(text, pattern, term_to_iso3):
    """Return (matched_term_canonical, iso3) if any country/region term is
    found in `text`; else (None, None). Returns the FIRST match by position."""
    if not text:
        return (None, None)
    text_n = normalize_apostrophes(text)
    m = pattern.search(text_n)
    if not m:
        return (None, None)
    matched = m.group(1)
    return (matched, term_to_iso3.get(matched.lower(), ""))


def vip_key(journal_short, vol, issue, first_page):
    """Build a (journal, volume, issue, first_page) tuple, normalized to strings."""
    if not journal_short or not vol or not first_page:
        return None
    # ECMA, RES, etc. use issue numbers; some journals have empty issue (e.g., JDE)
    return (
        journal_short,
        str(vol).strip(),
        str(issue).strip(),
        str(first_page).strip(),
    )


def load_jel_lookup(path):
    """Build two indexes from the JEL lookup CSV:

    - `lookup`: {normalized_full_descriptor: code}, where full forms include:
        * bare (AEA's leaf descriptor as-is)
        * sub-prefixed   (parent_name + ": " + bare)        [from CSV]
        * top-prefixed   (top_name    + ": " + bare)        [synthesized]
        * short-top-prefixed (first word of top_name + ": " + bare)
        * first-component-prefixed (first ';'-component of parent_name + ": " + bare)

    - `bare_to_codes`: {normalized_bare: [code, code, ...]} for suffix
      fallback when a record's descriptor uses a prefix variant we don't have.

    First occurrence wins to keep deterministic behavior on collisions.
    """
    lookup = {}
    bare_to_codes = defaultdict(list)

    def add(key, code):
        n = normalize_descriptor(key)
        if n and n not in lookup:
            lookup[n] = code

    with open(path, encoding="utf-8") as f:
        for r in csv.DictReader(f):
            code = r["code"]
            bare = r["descriptor_bare"]
            parent_name = r["parent_name"]
            top_name = r["top_name"]
            sub_pref = r["descriptor_prefixed"]

            bare_n = normalize_descriptor(bare)
            if bare_n:
                bare_to_codes[bare_n].append(code)

            add(bare, code)
            add(sub_pref, code)
            if top_name and bare:
                add(f"{top_name}: {bare}", code)
            # First standalone word of top_name (e.g., "Macroeconomics" from
            # "Macroeconomics and Monetary Economics") -- EconLit's habitual
            # abbreviation pattern.
            tn_first = top_name.split(",")[0].split(" and ")[0].strip()
            if tn_first and tn_first != top_name and bare:
                add(f"{tn_first}: {bare}", code)
            # First ';'-component of parent_name (e.g., "Cultural Economics"
            # from "Cultural Economics; Economic Sociology; Economic Anthropology")
            pn_first = parent_name.split(";")[0].strip() if parent_name else ""
            if pn_first and pn_first != parent_name and bare:
                add(f"{pn_first}: {bare}", code)
    return lookup, bare_to_codes


def parse_subjects(subjects_str, lookup, bare_to_codes):
    """Split EconLit subjects on ' ; ' and resolve each token to a JEL code.

    Strategy per token:
        1. Exact match against the precomputed lookup (covers bare, sub-prefix,
           top-prefix, and the two abbreviation variants).
        2. If the token contains ': ', take the suffix and look it up in
           bare_to_codes. If exactly one code matches, accept it. If multiple,
           leave unresolved (would need prefix-based disambiguation).

    Returns (codes, unresolved_tokens) where codes preserves the order in
    which descriptors appear and dedupes.
    """
    if not subjects_str or not subjects_str.strip():
        return [], []
    parts = re.split(r"\s+;\s+", subjects_str.strip())
    codes = []
    unresolved = []
    for p in parts:
        norm = normalize_descriptor(p)
        if not norm:
            continue
        # Step 1: exact lookup
        code = lookup.get(norm)
        # Step 2: bare-suffix fallback
        if code is None and ": " in p:
            suffix = p.split(": ", 1)[1].strip()
            suffix_n = normalize_descriptor(suffix)
            cs = bare_to_codes.get(suffix_n, [])
            if len(cs) == 1:
                code = cs[0]
        if code:
            if code not in codes:
                codes.append(code)
        else:
            unresolved.append(p.strip())
    return codes, unresolved


def parse_issns_field(s):
    if not s:
        return []
    return [t.strip() for t in re.split(r"[,;]", s) if t.strip()]


def journal_for_issns(s):
    for issn in parse_issns_field(s):
        if issn in JOURNAL_ISSNS:
            return JOURNAL_ISSNS[issn]
    return None


def main():
    open(LOG_TXT, "w").close()
    log("Stage 2 start.")

    # ---- 1. JEL lookup --------------------------------------------------
    log(f"Loading JEL lookup: {JEL_LOOKUP_CSV}")
    desc_to_code, bare_to_codes = load_jel_lookup(JEL_LOOKUP_CSV)
    log(f"  {len(desc_to_code):,} full-descriptor->code entries")
    log(f"  {len(bare_to_codes):,} bare descriptors (some with multiple codes)")

    # ---- 1b. LMIC country pattern --------------------------------------
    log(f"Loading LMIC country pattern: {LMIC_CSV}")
    lmic_pattern, term_to_iso3 = load_lmic_pattern(LMIC_CSV)
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
    log("Filtering EconLit rows to 12 in-scope journals × 2021-2025")
    in_scope = []
    drop_no_issn_match = 0
    drop_year = 0
    for r in econlit_rows:
        jshort = journal_for_issns(r.get("issns", ""))
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

    # Per-journal kept counts
    by_j = defaultdict(int)
    for r in in_scope:
        by_j[r["_journal_short"]] += 1
    log("  Per-journal kept counts:")
    for j in ["AER","AERI","AEJ_Applied","AEJ_EP","ECMA","QJE","JPE","RES","RESTAT","EJ","JEEA","JDE"]:
        log(f"    {j:14s} {by_j.get(j,0):>5d}")

    # ---- 4. Parse JEL codes; build three indexes ------------------------
    # Strategy: EconLit DOI population is uneven across publishers (100% for
    # AEA journals; 1-30% for UChicago Press, Wiley, MIT Press journals). We
    # build three indexes and match in priority order at merge time:
    #   (a) DOI                                       -- most reliable
    #   (b) (journal, volume, issue, first_page)      -- bibliographic
    #   (c) (journal, normalized_title)               -- title fallback
    log("Parsing JEL codes and building DOI / vol-iss-pg / title indexes")
    econlit_by_doi = {}
    econlit_by_vip = {}
    econlit_by_title = {}
    n_with_jel = 0
    n_with_o = 0
    n_indexed_doi = 0
    n_indexed_vip = 0
    n_indexed_title = 0
    n_indexable_none = 0
    unresolved_counter = defaultdict(int)
    for r in in_scope:
        codes, unres = parse_subjects(r.get("subjects", ""), desc_to_code, bare_to_codes)
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

        indexed = False
        # (a) DOI
        doi = normalize_doi(r.get("doi", ""))
        if doi and doi not in econlit_by_doi:
            econlit_by_doi[doi] = rec
            n_indexed_doi += 1
            indexed = True
        # (b) volume/issue/first_page tuple
        vip = vip_key(r["_journal_short"], r.get("volume", ""), r.get("issue", ""), r.get("pageStart", ""))
        if vip and vip not in econlit_by_vip:
            econlit_by_vip[vip] = rec
            n_indexed_vip += 1
            indexed = True
        # (c) (journal, normalized title)
        title_norm = normalize_title(r.get("title", ""))
        if title_norm:
            tkey = (r["_journal_short"], title_norm)
            if tkey not in econlit_by_title:
                econlit_by_title[tkey] = rec
                n_indexed_title += 1
                indexed = True
        if not indexed:
            n_indexable_none += 1
    log(f"  records w/ at least one JEL code:        {n_with_jel:,}")
    log(f"  records w/ at least one O-family code:   {n_with_o:,}")
    log(f"  index sizes: DOI={n_indexed_doi:,}  VIP={n_indexed_vip:,}  TITLE={n_indexed_title:,}")
    log(f"  records with no usable index key:        {n_indexable_none:,}")
    if unresolved_counter:
        log(f"  unresolved descriptor tokens (top 10):")
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
                  "econlit_matched", "econlit_match_key"]
    for nf in new_fields:
        if nf not in oa_fields:
            oa_fields.append(nf)

    # ---- 6. Merge + dev filter ------------------------------------------
    log("Merging EconLit data; applying development filter")
    n_match = 0
    n_match_by = defaultdict(int)  # which key matched
    n_abs_backfilled = 0
    counts = defaultdict(lambda: defaultdict(int))
    for r in oa_rows:
        ec = None
        match_key = ""
        # (a) DOI
        doi = normalize_doi(r.get("doi", ""))
        if doi:
            ec = econlit_by_doi.get(doi)
            if ec:
                match_key = "doi"
        # (b) (journal, vol, iss, fp)
        if ec is None:
            vip = vip_key(r.get("journal_short", ""), r.get("volume", ""),
                          r.get("issue", ""), r.get("first_page", ""))
            if vip:
                ec = econlit_by_vip.get(vip)
                if ec:
                    match_key = "vol_iss_page"
        # (c) (journal, normalized title)
        if ec is None:
            tnorm = normalize_title(r.get("title", ""))
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

        # Country-mention scan: check title and abstract separately so we can
        # record where the match came from for diagnostics.
        title = r.get("title", "") or ""
        abstract = r.get("abstract", "") or ""
        match_t, iso_t = find_country_mention(title, lmic_pattern, term_to_iso3)
        match_a, iso_a = find_country_mention(abstract, lmic_pattern, term_to_iso3)
        if match_t:
            r["country_match"] = match_t
            r["country_match_iso3"] = iso_t
            r["country_match_in"] = "title"
        elif match_a:
            r["country_match"] = match_a
            r["country_match_iso3"] = iso_a
            r["country_match_in"] = "abstract"
        else:
            r["country_match"] = ""
            r["country_match_iso3"] = ""
            r["country_match_in"] = ""

        # Development filter (ordered rules; first match wins):
        #   1. JDE auto-include
        #   2. JEL O1/O2 code present
        #   3. LMIC country mention in title/abstract
        #   4. JEL codes present, none in O1/O2, no country -> FALSE
        #   5. No JEL codes, no country -> BORDERLINE
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
    log("--- Per-journal development-filter summary ---")
    log(f"  {'journal':14s} {'TRUE':>6s} {'FALSE':>6s} {'BORDER':>7s} {'total':>6s}")
    for j in ["AER","AERI","AEJ_Applied","AEJ_EP","ECMA","QJE","JPE","RES","RESTAT","EJ","JEEA","JDE"]:
        d = counts.get(j, {})
        t = sum(d.values())
        log(f"  {j:14s} {d.get('TRUE',0):>6d} {d.get('FALSE',0):>6d} {d.get('BORDERLINE',0):>7d} {t:>6d}")
    log("Stage 2 complete.")


if __name__ == "__main__":
    main()
