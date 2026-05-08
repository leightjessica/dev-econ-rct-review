"""
Stage 1: Pull article-level metadata for 12 economics journals (2021-2025) from
OpenAlex (https://api.openalex.org).

Outputs: data/raw_openalex_2021_2025.csv

Replicability notes
-------------------
- Uses Python standard library only (no pip install required).
- Sends a "polite pool" mailto header so OpenAlex rate-limits cooperatively.
- Filters by ISSN (OpenAlex matches against any ISSN registered for the
  source, so ISSN-L vs print vs electronic does not matter).
- Each row records the UTC snapshot timestamp; OpenAlex updates continuously,
  so re-running on a different day may yield small differences.
- All filtering by document type (article vs editorial vs correction) is
  deferred to a later cleaning step. This script captures everything.
"""

import csv
import json
import os
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone

# ---- Configuration ----------------------------------------------------------

EMAIL = "J.Leight@cgiar.org"  # OpenAlex polite pool

YEAR_FROM = 2021
YEAR_TO = 2025

# Journal list: (short_code, full_name, issn). The ISSN need not be ISSN-L;
# OpenAlex matches across all registered ISSNs for the source.
JOURNALS = [
    ("AER",         "American Economic Review",                       "0002-8282"),
    ("AERI",        "American Economic Review: Insights",             "2640-205X"),
    ("AEJ_Applied", "American Economic Journal: Applied Economics",   "1945-7782"),
    ("AEJ_EP",      "American Economic Journal: Economic Policy",     "1945-7731"),
    ("ECMA",        "Econometrica",                                   "0012-9682"),
    ("QJE",         "Quarterly Journal of Economics",                 "0033-5533"),
    ("JPE",         "Journal of Political Economy",                   "0022-3808"),
    ("RES",         "Review of Economic Studies",                     "0034-6527"),
    ("RESTAT",      "Review of Economics and Statistics",             "0034-6535"),
    ("EJ",          "Economic Journal",                               "0013-0133"),
    ("JEEA",        "Journal of the European Economic Association",   "1542-4766"),
    ("JDE",         "Journal of Development Economics",               "0304-3878"),
]

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_DIR = os.path.abspath(os.path.join(SCRIPT_DIR, ".."))
OUT_CSV = os.path.join(PROJECT_DIR, "data", "raw_openalex_2021_2025.csv")
LOG_TXT = os.path.join(PROJECT_DIR, "data", "01_openalex_pull.log")

# ---- Helpers ----------------------------------------------------------------

def log(msg):
    line = f"[{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%SZ')}] {msg}"
    print(line, flush=True)
    with open(LOG_TXT, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def reconstruct_abstract(inv):
    """OpenAlex returns abstracts as an inverted index {word: [positions]}."""
    if not inv:
        return ""
    positions = {}
    for word, idx_list in inv.items():
        for idx in idx_list:
            positions[idx] = word
    if not positions:
        return ""
    return " ".join(positions[i] for i in sorted(positions))


def fetch_works(issn):
    base = "https://api.openalex.org/works"
    cursor = "*"
    page = 0
    while cursor:
        params = {
            "filter": f"primary_location.source.issn:{issn},publication_year:{YEAR_FROM}-{YEAR_TO}",
            "per-page": "200",
            "cursor": cursor,
            "mailto": EMAIL,
        }
        url = f"{base}?{urllib.parse.urlencode(params)}"
        req = urllib.request.Request(
            url,
            headers={"User-Agent": f"dev-rct-review-script (mailto:{EMAIL})"},
        )
        attempts = 0
        while True:
            try:
                with urllib.request.urlopen(req, timeout=60) as resp:
                    data = json.load(resp)
                break
            except Exception as e:
                attempts += 1
                if attempts > 4:
                    raise
                wait = 2 ** attempts
                log(f"    request failed ({e}); retry in {wait}s")
                time.sleep(wait)

        page += 1
        for w in data.get("results", []):
            yield w
        cursor = data.get("meta", {}).get("next_cursor")
        time.sleep(0.15)  # politeness delay between pages


def extract_row(w, short, full, issn, snapshot):
    authorships = w.get("authorships") or []
    authors = "; ".join(
        (a.get("author") or {}).get("display_name", "") for a in authorships
    )
    primary = w.get("primary_location") or {}
    source = primary.get("source") or {}
    biblio = w.get("biblio") or {}
    return {
        "journal_short": short,
        "journal_full": full,
        "issn_query": issn,
        "openalex_id": w.get("id", "") or "",
        "doi": (w.get("doi") or "").replace("https://doi.org/", ""),
        "title": (w.get("title") or "").strip(),
        "abstract": reconstruct_abstract(w.get("abstract_inverted_index")),
        "authors": authors,
        "publication_year": w.get("publication_year", "") or "",
        "publication_date": w.get("publication_date", "") or "",
        "volume": biblio.get("volume", "") or "",
        "issue": biblio.get("issue", "") or "",
        "first_page": biblio.get("first_page", "") or "",
        "last_page": biblio.get("last_page", "") or "",
        "type": w.get("type", "") or "",
        "type_crossref": w.get("type_crossref", "") or "",
        "is_paratext": w.get("is_paratext", False),
        "openalex_source_id": source.get("id", "") or "",
        "openalex_source_name": source.get("display_name", "") or "",
        "snapshot_utc": snapshot,
    }


# ---- Main -------------------------------------------------------------------

def main():
    os.makedirs(os.path.dirname(OUT_CSV), exist_ok=True)
    # Truncate log
    open(LOG_TXT, "w").close()
    snapshot = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    log(f"Stage 1 start. Snapshot UTC = {snapshot}")
    log(f"Years: {YEAR_FROM}-{YEAR_TO}. Journals: {len(JOURNALS)}.")

    rows = []
    counts = {}
    for short, full, issn in JOURNALS:
        log(f"--> {short} | {full} | ISSN {issn}")
        n = 0
        for w in fetch_works(issn):
            rows.append(extract_row(w, short, full, issn, snapshot))
            n += 1
        counts[short] = n
        log(f"    {n} records")

    fieldnames = [
        "journal_short", "journal_full", "issn_query",
        "openalex_id", "doi", "title", "abstract", "authors",
        "publication_year", "publication_date",
        "volume", "issue", "first_page", "last_page",
        "type", "type_crossref", "is_paratext",
        "openalex_source_id", "openalex_source_name",
        "snapshot_utc",
    ]
    with open(OUT_CSV, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    log(f"Wrote {len(rows)} rows -> {OUT_CSV}")
    log("Per-journal counts:")
    for short, n in counts.items():
        log(f"  {short:14s} {n:5d}")
    log("Stage 1 complete.")


if __name__ == "__main__":
    main()
