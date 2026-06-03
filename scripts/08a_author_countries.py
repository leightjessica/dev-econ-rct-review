"""
Stage 8a: Pull per-author affiliation country from OpenAlex.

The Stage 1 pull (01_openalex_pull.py) kept only author display names and
discarded the affiliation data carried in each work's `authorships` array. This
script re-queries OpenAlex by work ID to recover, for every author of every
paper in the final dataset, the affiliation country (ISO-3166 alpha-2). That
country is used as a prior in Stage 8 (08_gender_classify.py): some given names
flip gender by region (for example, "Andrea" is female in Germany and male in
Italy), and the prior resolves a subset of otherwise-undetermined names.

Reads:
  data/final_dataset.csv   (for the set of openalex_id values)

Writes:
  data/author_countries.csv   (one row per (work, author_position): country)
  data/08a_author_countries.log

Method
------
- Queries the OpenAlex /works endpoint in batches of 50 IDs, requesting only
  the `id` and `authorships` fields (select=...), via the polite pool (mailto).
- Per author, the country is taken from the authorship-level `countries` list
  (OpenAlex's own roll-up); if absent, it falls back to the first institution's
  `country_code`. A blank is recorded when neither is present.
- Author order in the `authorships` array matches the order used to build the
  semicolon-delimited `authors` field in Stage 1, so Stage 8 joins on
  (openalex_id, author_position). The author display name is also stored so the
  join can be sanity-checked.

Resumable: on rerun, works already present in author_countries.csv are skipped,
so an interrupted pull can be continued. No API key required.

Replicability: standard library only. OpenAlex updates continuously, so author
lists pulled on different days may differ slightly; the pull timestamp is logged.
"""

import csv
import json
import os
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone

EMAIL = "J.Leight@cgiar.org"  # OpenAlex polite pool
BATCH = 50                    # OpenAlex allows up to 50 OR-values per filter

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_DIR = os.path.abspath(os.path.join(SCRIPT_DIR, ".."))

SRC = os.path.join(PROJECT_DIR, "data", "final_dataset.csv")
OUT_CSV = os.path.join(PROJECT_DIR, "data", "author_countries.csv")
LOG_TXT = os.path.join(PROJECT_DIR, "data", "08a_author_countries.log")

OUT_COLS = ["openalex_id", "author_position", "author_name", "country_iso2"]


def log(msg):
    line = f"[{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%SZ')}] {msg}"
    print(line, flush=True)
    with open(LOG_TXT, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def short_id(openalex_id):
    """Reduce a full OpenAlex work URL to its short 'W...' identifier."""
    return (openalex_id or "").rstrip("/").rsplit("/", 1)[-1]


def fetch_batch(short_ids):
    """Fetch id + authorships for up to BATCH work IDs in one request."""
    base = "https://api.openalex.org/works"
    params = {
        "filter": "openalex_id:" + "|".join(short_ids),
        "select": "id,authorships",
        "per-page": str(len(short_ids)),
        "mailto": EMAIL,
    }
    url = f"{base}?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(
        url, headers={"User-Agent": f"dev-rct-review-script (mailto:{EMAIL})"}
    )
    attempts = 0
    while True:
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                return json.load(resp).get("results", [])
        except Exception as e:
            attempts += 1
            if attempts > 4:
                raise
            wait = 2 ** attempts
            log(f"    request failed ({e}); retry in {wait}s")
            time.sleep(wait)


def author_country(authorship):
    """Best-available affiliation country (ISO2) for one authorship entry."""
    countries = authorship.get("countries") or []
    if countries:
        return countries[0] or ""
    for inst in authorship.get("institutions") or []:
        cc = inst.get("country_code")
        if cc:
            return cc
    return ""


def load_done():
    """Work IDs already present in the output (for resumability)."""
    if not os.path.exists(OUT_CSV):
        return set()
    done = set()
    with open(OUT_CSV, encoding="utf-8") as f:
        for r in csv.DictReader(f):
            done.add(short_id(r["openalex_id"]))
    return done


def main():
    if not os.path.exists(SRC):
        sys.exit(f"Source not found: {SRC}")

    # Truncate log; preserve any existing output for resumability.
    open(LOG_TXT, "w").close()
    log(f"Source: {SRC}")

    with open(SRC, encoding="utf-8") as f:
        ids_full = [r.get("openalex_id", "") for r in csv.DictReader(f)]
    ids_full = [i for i in ids_full if i]
    # Unique short IDs, preserving order.
    seen, work_ids = set(), []
    for i in ids_full:
        s = short_id(i)
        if s and s not in seen:
            seen.add(s)
            work_ids.append((s, i))  # (short, full)
    log(f"  {len(work_ids):,} unique works in final dataset")

    done = load_done()
    todo = [(s, full) for (s, full) in work_ids if s not in done]
    log(f"  {len(done):,} already pulled; {len(todo):,} to fetch")

    new_open = not os.path.exists(OUT_CSV)
    out_f = open(OUT_CSV, "a", newline="", encoding="utf-8")
    writer = csv.DictWriter(out_f, fieldnames=OUT_COLS)
    if new_open:
        writer.writeheader()

    full_by_short = {s: full for (s, full) in work_ids}
    n_works, n_authors, n_with_country = 0, 0, 0
    try:
        for start in range(0, len(todo), BATCH):
            chunk = todo[start:start + BATCH]
            results = fetch_batch([s for (s, _) in chunk])
            for w in results:
                w_short = short_id(w.get("id", ""))
                w_full = full_by_short.get(w_short, w.get("id", ""))
                authorships = w.get("authorships") or []
                for pos, a in enumerate(authorships, start=1):
                    name = (a.get("author") or {}).get("display_name", "")
                    cc = author_country(a)
                    writer.writerow({
                        "openalex_id": w_full,
                        "author_position": pos,
                        "author_name": name,
                        "country_iso2": cc,
                    })
                    n_authors += 1
                    if cc:
                        n_with_country += 1
                n_works += 1
            out_f.flush()
            log(f"  fetched {min(start + BATCH, len(todo)):,}/{len(todo):,} works")
            time.sleep(0.15)  # politeness delay
    finally:
        out_f.close()

    log("")
    log(f"Fetched {n_works:,} works this run, {n_authors:,} author rows.")
    if n_authors:
        log(f"  with a country code: {n_with_country:,} "
            f"({100 * n_with_country / n_authors:.1f}%)")
    log("Stage 8a complete.")


if __name__ == "__main__":
    main()
