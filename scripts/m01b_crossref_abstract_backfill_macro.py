"""
Macro extension, Stage 1b: backfill missing abstracts in the macro OpenAlex
pull from Crossref by DOI. Mirrors scripts/01b_crossref_abstract_backfill.py
exactly; only the input/output paths differ (data/macro/).

Reads:  data/macro/raw_openalex_macro_2021_2025.csv     (m01 output)
Writes: data/macro/raw_with_abstracts_macro_2021_2025.csv
"""

import csv
import json
import os
import re
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone

EMAIL = "J.Leight@cgiar.org"

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_DIR = os.path.abspath(os.path.join(SCRIPT_DIR, ".."))
IN_CSV = os.path.join(PROJECT_DIR, "data", "macro", "raw_openalex_macro_2021_2025.csv")
OUT_CSV = os.path.join(PROJECT_DIR, "data", "macro", "raw_with_abstracts_macro_2021_2025.csv")
LOG_TXT = os.path.join(PROJECT_DIR, "data", "macro", "m01b_crossref_backfill.log")

TAG_RE = re.compile(r"<[^>]+>")
JATS_TITLE_RE = re.compile(r"^\s*Abstract\s*", re.IGNORECASE)


def log(msg):
    line = f"[{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%SZ')}] {msg}"
    print(line, flush=True)
    with open(LOG_TXT, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def clean_abstract(raw):
    if not raw:
        return ""
    text = TAG_RE.sub(" ", raw)
    text = JATS_TITLE_RE.sub("", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def fetch_crossref_abstract(doi):
    if not doi:
        return None
    url = f"https://api.crossref.org/works/{urllib.parse.quote(doi, safe='/')}"
    req = urllib.request.Request(
        url, headers={"User-Agent": f"dev-rct-review-script (mailto:{EMAIL})"}
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.load(resp)
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None
        raise
    return clean_abstract(data.get("message", {}).get("abstract"))


def main():
    open(LOG_TXT, "w").close()
    log(f"Macro Stage 1b start. Input: {IN_CSV}")

    with open(IN_CSV, encoding="utf-8") as f:
        reader = csv.DictReader(f)
        rows = list(reader)
        fieldnames = list(reader.fieldnames)
    if "abstract_source" not in fieldnames:
        fieldnames.append("abstract_source")

    n_total = len(rows)
    n_have = sum(1 for r in rows if r.get("abstract"))
    log(f"Total rows: {n_total}; with OpenAlex abstract: {n_have}; missing: {n_total - n_have}")

    backfilled = no_doi = crossref_404 = crossref_no_abs = errors = 0
    for i, r in enumerate(rows):
        if r.get("abstract"):
            r["abstract_source"] = "openalex"
            continue
        doi = r.get("doi", "").strip()
        if not doi:
            r["abstract_source"] = "none_no_doi"
            no_doi += 1
            continue
        try:
            abs_text = fetch_crossref_abstract(doi)
        except Exception as e:
            log(f"  error on DOI {doi}: {e}")
            r["abstract_source"] = "error"
            errors += 1
            time.sleep(1)
            continue
        if abs_text is None:
            r["abstract_source"] = "none_crossref_404"
            crossref_404 += 1
        elif not abs_text:
            r["abstract_source"] = "none_crossref_no_abstract"
            crossref_no_abs += 1
        else:
            r["abstract"] = abs_text
            r["abstract_source"] = "crossref"
            backfilled += 1
        time.sleep(0.12)
        if (i + 1) % 100 == 0:
            log(f"  progress: {i+1}/{n_total}; backfilled so far: {backfilled}")

    with open(OUT_CSV, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    n_with_abs = sum(1 for r in rows if r.get("abstract"))
    log("---- Backfill summary ----")
    log(f"  Backfilled from Crossref:    {backfilled}")
    log(f"  No DOI available:            {no_doi}")
    log(f"  Crossref 404 (no record):    {crossref_404}")
    log(f"  Crossref had no abstract:    {crossref_no_abs}")
    log(f"  Errors:                      {errors}")
    log(f"  Final coverage: {n_with_abs}/{n_total} ({100*n_with_abs/n_total:.1f}%)")
    log(f"Wrote -> {OUT_CSV}")
    log("Macro Stage 1b complete.")


if __name__ == "__main__":
    main()
