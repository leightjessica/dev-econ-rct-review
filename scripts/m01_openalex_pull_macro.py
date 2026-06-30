"""
Macro extension, Stage 1: Pull article-level metadata for two macroeconomics
journals (2021-2025) from OpenAlex (https://api.openalex.org).

This is a standalone screen, separate from the main 12-journal development-RCT
dataset. It writes to data/macro/ and does not touch any main-project file.

Journals
--------
  AEJ: Macroeconomics        ISSN-L 1945-7707 (also 1945-7715)
  Journal of Macroeconomics  ISSN-L 0164-0704 (also 1873-152X)

Output: data/macro/raw_openalex_macro_2021_2025.csv

Mirrors scripts/01_openalex_pull.py in logic (stdlib only, polite-pool mailto,
ISSN filter, deferred document-type cleaning, per-row UTC snapshot).
"""

import csv
import json
import os
import time
import urllib.parse
import urllib.request
from collections import defaultdict
from datetime import datetime, timezone

# ---- Configuration ----------------------------------------------------------

EMAIL = "J.Leight@cgiar.org"  # OpenAlex polite pool

YEAR_FROM = 2021
YEAR_TO = 2025

# Journal list: (short_code, full_name, issn). OpenAlex matches across all
# registered ISSNs for the source, so the ISSN-L suffices.
JOURNALS = [
    ("AEJ_Macro", "American Economic Journal: Macroeconomics", "1945-7707"),
    ("JMacro",    "Journal of Macroeconomics",                 "0164-0704"),
]

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_DIR = os.path.abspath(os.path.join(SCRIPT_DIR, ".."))
OUT_CSV = os.path.join(PROJECT_DIR, "data", "macro", "raw_openalex_macro_2021_2025.csv")
LOG_TXT = os.path.join(PROJECT_DIR, "data", "macro", "m01_openalex_pull.log")

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
    open(LOG_TXT, "w").close()  # truncate log
    snapshot = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    log(f"Macro Stage 1 start. Snapshot UTC = {snapshot}")
    log(f"Years: {YEAR_FROM}-{YEAR_TO}. Journals: {len(JOURNALS)}.")

    rows = []
    counts = {}
    by_year = defaultdict(lambda: defaultdict(int))
    for short, full, issn in JOURNALS:
        log(f"--> {short} | {full} | ISSN {issn}")
        n = 0
        for w in fetch_works(issn):
            row = extract_row(w, short, full, issn, snapshot)
            rows.append(row)
            by_year[short][row["publication_year"]] += 1
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
        log(f"  {short:12s} {n:5d}")
    log("Per-journal-year counts (all document types, pre-cleaning):")
    for short in counts:
        yrs = by_year[short]
        detail = "  ".join(f"{y}:{yrs[y]}" for y in sorted(yrs))
        log(f"  {short:12s} {detail}")
    log("Macro Stage 1 complete.")


if __name__ == "__main__":
    main()
