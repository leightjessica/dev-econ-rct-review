"""
Macro extension, Stage 1c: backfill abstracts still missing after m01b from the
Semantic Scholar batch endpoint. Mirrors scripts/01c_semantic_scholar_backfill.py;
only the target path differs (data/macro/).

Reads/writes (in place): data/macro/raw_with_abstracts_macro_2021_2025.csv
"""

import csv
import json
import os
import time
import urllib.request
from datetime import datetime, timezone

EMAIL = "J.Leight@cgiar.org"

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_DIR = os.path.abspath(os.path.join(SCRIPT_DIR, ".."))
TARGET_CSV = os.path.join(PROJECT_DIR, "data", "macro", "raw_with_abstracts_macro_2021_2025.csv")
LOG_TXT = os.path.join(PROJECT_DIR, "data", "macro", "m01c_semantic_scholar_backfill.log")

S2_BATCH_URL = "https://api.semanticscholar.org/graph/v1/paper/batch?fields=abstract"
BATCH_SIZE = 400


def log(msg):
    line = f"[{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%SZ')}] {msg}"
    print(line, flush=True)
    with open(LOG_TXT, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def s2_batch(dois):
    body = json.dumps({"ids": [f"DOI:{d}" for d in dois]}).encode("utf-8")
    req = urllib.request.Request(
        S2_BATCH_URL, data=body,
        headers={"Content-Type": "application/json",
                 "User-Agent": f"dev-rct-review-script (mailto:{EMAIL})"},
        method="POST",
    )
    attempts = 0
    while True:
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                data = json.load(resp)
            break
        except urllib.error.HTTPError as e:
            attempts += 1
            if e.code == 429 and attempts <= 5:
                wait = 2 ** attempts
                log(f"  429 rate-limit; sleep {wait}s")
                time.sleep(wait)
                continue
            raise
        except Exception as e:
            attempts += 1
            if attempts > 4:
                raise
            wait = 2 ** attempts
            log(f"  request error ({e}); retry in {wait}s")
            time.sleep(wait)
    out = {}
    for doi, item in zip(dois, data):
        out[doi] = None if item is None else ((item.get("abstract") or "").strip() or None)
    return out


def main():
    open(LOG_TXT, "w").close()
    log(f"Macro Stage 1c start. Target: {TARGET_CSV}")

    with open(TARGET_CSV, encoding="utf-8") as f:
        reader = csv.DictReader(f)
        rows = list(reader)
        fieldnames = list(reader.fieldnames)

    n_total = len(rows)
    pre_have = sum(1 for r in rows if r.get("abstract"))
    needs = [r for r in rows if not r.get("abstract") and r.get("doi", "").strip()]
    no_doi_no_abs = sum(1 for r in rows if not r.get("abstract") and not r.get("doi", "").strip())
    log(f"Total rows: {n_total}; have abstract: {pre_have}; need lookup: {len(needs)}; no DOI: {no_doi_no_abs}")

    backfilled = s2_no_abstract = s2_unknown = n_batches = 0
    for i in range(0, len(needs), BATCH_SIZE):
        chunk = needs[i:i+BATCH_SIZE]
        dois = [r["doi"].strip() for r in chunk]
        log(f"  batch {n_batches+1}: {len(dois)} DOIs")
        result = s2_batch(dois)
        for r in chunk:
            d = r["doi"].strip()
            abs_text = result.get(d)
            if abs_text is None and d not in result:
                r["abstract_source"] = "none_s2_missing"
                s2_unknown += 1
            elif abs_text is None:
                r["abstract_source"] = "none_s2_no_abstract"
                s2_no_abstract += 1
            else:
                r["abstract"] = abs_text
                r["abstract_source"] = "semantic_scholar"
                backfilled += 1
        n_batches += 1
        time.sleep(1.1)

    with open(TARGET_CSV, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    post_have = sum(1 for r in rows if r.get("abstract"))
    log("---- Backfill summary ----")
    log(f"  Backfilled from Semantic Scholar: {backfilled}")
    log(f"  S2 had no abstract / unknown:     {s2_no_abstract + s2_unknown}")
    log(f"  Coverage before this stage:       {pre_have}/{n_total} ({100*pre_have/n_total:.1f}%)")
    log(f"  Coverage after this stage:        {post_have}/{n_total} ({100*post_have/n_total:.1f}%)")
    log("Macro Stage 1c complete.")


if __name__ == "__main__":
    main()
