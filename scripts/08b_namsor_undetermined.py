"""
Stage 8b (optional): resolve still-undetermined author genders with NamSor.

Stage 8 leaves names that the offline gender_guesser dictionary cannot classify
(romanized Chinese names, names absent from the dictionary, etc.) coded as
"undetermined". This script sends ONLY those names to the NamSor API
(https://namsor.app), which models gender from the full name plus name origin
and so does better on many non-European naming traditions. Names already coded
female/male in Stage 8 are NOT re-queried.

Reads:
  data/author_gender.csv   (Stage 8 output; rows with gender_coded == undetermined)

Writes:
  data/namsor_cache.csv              (one row per unique name queried; the
                                      authoritative cache, committed for replication)
  data/author_gender_namsor.csv      (author_gender.csv + NamSor result + final coding)
  data/paper_gender_summary_namsor.csv  (paper summary recomputed on the final coding)
  data/08b_namsor_undetermined.log

API key
-------
The key is read, in order, from:
  1. environment variable NAMSOR_API_KEY
  2. a file path in environment variable NAMSOR_API_KEY_FILE
  3. <project>/.namsor_key   /   <project>/namsor_key   /   <project>/namsor_key.txt
  4. <project>/secrets/namsor_api_key.txt
(all of the above file locations are gitignored)
The key is never written to any output or log (only its length is logged).

Method
------
- Undetermined authors are de-duplicated to unique (first, last, country) keys to
  minimize API units. The given name is `first_name_used` from Stage 8; the
  surname is the last whitespace token of the full name.
- Names with a country code use the geo-aware endpoint (genderGeoBatch); names
  without use genderBatch. Requests are batched (100 names/request).
- Results are cached to namsor_cache.csv; rerunning queries only uncached names,
  so the run is resumable and a replicator with the cache needs no API access.
- The final coding (`gender_coded_final`) accepts a NamSor prediction only when
  its calibrated probability is at least NAMSOR_MIN_PROB; otherwise the author
  stays "undetermined". The raw NamSor fields are retained so the threshold can
  be revisited without re-querying.

Usage:
  python 08b_namsor_undetermined.py --test   # query 2 names, print raw JSON, stop
  python 08b_namsor_undetermined.py          # full run (resumable)

Replicability: requires only the standard library (urllib) plus a NamSor key for
the initial population of the cache. Deterministic given the cache.
"""

import argparse
import csv
import hashlib
import json
import os
import sys
import time
import urllib.error
import urllib.request
from collections import Counter
from datetime import datetime, timezone

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_DIR = os.path.abspath(os.path.join(SCRIPT_DIR, ".."))

SRC = os.path.join(PROJECT_DIR, "data", "author_gender.csv")
CACHE = os.path.join(PROJECT_DIR, "data", "namsor_cache.csv")
OUT_AUTHORS = os.path.join(PROJECT_DIR, "data", "author_gender_namsor.csv")
OUT_PAPERS = os.path.join(PROJECT_DIR, "data", "paper_gender_summary_namsor.csv")
LOG_TXT = os.path.join(PROJECT_DIR, "data", "08b_namsor_undetermined.log")

API_BASE = "https://v2.namsor.com/NamSorAPIv2/api2/json"
BATCH = 100
NAMSOR_MIN_PROB = 0.85   # minimum calibrated probability to accept into final coding

CACHE_COLS = [
    "first_name", "last_name", "country_iso2",
    "likely_gender", "prob_calibrated", "gender_scale", "score", "endpoint",
]


def log(msg):
    line = f"[{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%SZ')}] {msg}"
    print(line, flush=True)
    with open(LOG_TXT, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def file_sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def read_api_key():
    key = os.environ.get("NAMSOR_API_KEY", "").strip()
    if key:
        return key, "env:NAMSOR_API_KEY"
    keyfile = os.environ.get("NAMSOR_API_KEY_FILE", "").strip()
    candidates = [keyfile] if keyfile else []
    candidates += [
        os.path.join(PROJECT_DIR, ".namsor_key"),
        os.path.join(PROJECT_DIR, "namsor_key"),
        os.path.join(PROJECT_DIR, "namsor_key.txt"),
        os.path.join(PROJECT_DIR, "secrets", "namsor_api_key.txt"),
    ]
    for path in candidates:
        if path and os.path.exists(path):
            with open(path, encoding="utf-8") as f:
                k = f.read().strip()
            if k:
                return k, f"file:{os.path.relpath(path, PROJECT_DIR)}"
    return "", ""


def surname(full_name):
    toks = [t.strip(".,") for t in full_name.split() if t.strip(".,")]
    return toks[-1] if toks else ""


def post_batch(endpoint, payload, key):
    """POST one batch to NamSor; return parsed JSON (raises on HTTP error)."""
    url = f"{API_BASE}/{endpoint}"
    data = json.dumps({"personalNames": payload}).encode("utf-8")
    req = urllib.request.Request(
        url, data=data, method="POST",
        headers={
            "X-API-KEY": key,
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
    )
    attempts = 0
    while True:
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                return json.load(resp)
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", "replace")[:300]
            # 4xx are not worth retrying (bad key / bad request / quota).
            if 400 <= e.code < 500:
                raise RuntimeError(f"NamSor HTTP {e.code}: {body}") from e
            attempts += 1
            if attempts > 4:
                raise RuntimeError(f"NamSor HTTP {e.code}: {body}") from e
            wait = 2 ** attempts
            log(f"    HTTP {e.code}; retry in {wait}s")
            time.sleep(wait)
        except Exception as e:
            attempts += 1
            if attempts > 4:
                raise
            wait = 2 ** attempts
            log(f"    request error ({e}); retry in {wait}s")
            time.sleep(wait)


def parse_result(item):
    """Extract (likely_gender, prob_calibrated, gender_scale, score) from a result."""
    g = (item.get("likelyGender") or "").lower()
    prob = item.get("probabilityCalibrated")
    if prob is None or prob < 0:
        # Older responses may omit calibration; fall back to |genderScale|.
        gs = item.get("genderScale")
        prob = abs(gs) if isinstance(gs, (int, float)) else ""
    return (
        g,
        prob if prob != "" else "",
        item.get("genderScale", ""),
        item.get("score", ""),
    )


def load_cache():
    cache = {}
    if not os.path.exists(CACHE):
        return cache
    with open(CACHE, encoding="utf-8") as f:
        for r in csv.DictReader(f):
            key = (
                r.get("first_name", ""),
                r.get("last_name", ""),
                r.get("country_iso2", ""),
            )
            cache[key] = r
    return cache


def collect_undetermined():
    """Unique (first, last, iso2) keys needing classification, plus their rows."""
    if not os.path.exists(SRC):
        sys.exit(f"Source not found: {SRC} (run 08_gender_classify.py first)")
    with open(SRC, encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    keys = {}
    for r in rows:
        if r.get("gender_coded") != "undetermined":
            continue
        first = (r.get("first_name_used") or "").strip()
        if not first:
            continue  # no usable given name; NamSor cannot help
        last = surname(r.get("author_name", ""))
        iso2 = (r.get("country_iso2") or "").strip().upper()
        keys.setdefault((first, last, iso2))
    return rows, list(keys.keys())


def run_test(key):
    _, keys = collect_undetermined()
    sample = keys[:2]
    log(f"TEST mode: querying {len(sample)} name(s): {sample}")
    geo = [
        {"id": str(i), "firstName": f, "lastName": l, "countryIso2": (c or "US")}
        for i, (f, l, c) in enumerate(sample)
    ]
    resp = post_batch("genderGeoBatch", geo, key)
    log("Raw genderGeoBatch response:")
    log(json.dumps(resp, indent=2)[:2000])
    log("TEST complete. Inspect the field names above before the full run.")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--test", action="store_true",
                    help="query 2 names, print raw JSON, and stop")
    args = ap.parse_args()

    open(LOG_TXT, "w").close()

    key, key_src = read_api_key()
    if not key:
        sys.exit(
            "No NamSor API key found. Provide it via one of:\n"
            "  - environment variable NAMSOR_API_KEY\n"
            f"  - a single-line file at {os.path.join(PROJECT_DIR, '.namsor_key')}\n"
            "  - a single-line file at "
            f"{os.path.join(PROJECT_DIR, 'secrets', 'namsor_api_key.txt')}"
        )
    log(f"API key loaded from {key_src} (length {len(key)})")

    if args.test:
        run_test(key)
        return

    rows, keys = collect_undetermined()
    log(f"Source: {SRC}  ({len(rows):,} author rows)")
    log(f"Unique undetermined names to resolve: {len(keys):,}")

    cache = load_cache()
    todo = [k for k in keys if k not in cache]
    log(f"  already cached: {len(keys) - len(todo):,};  to query: {len(todo):,}")

    # ---- query NamSor in batches, split by presence of a country ------------
    new_open = not os.path.exists(CACHE)
    cf = open(CACHE, "a", newline="", encoding="utf-8")
    cw = csv.DictWriter(cf, fieldnames=CACHE_COLS)
    if new_open:
        cw.writeheader()

    n_queried = 0
    try:
        for start in range(0, len(todo), BATCH):
            chunk = todo[start:start + BATCH]
            geo_items, plain_items = [], []
            geo_keys, plain_keys = [], []
            for j, (f, l, c) in enumerate(chunk):
                item = {"id": str(j), "firstName": f, "lastName": l}
                if c:
                    geo_items.append({**item, "countryIso2": c})
                    geo_keys.append((f, l, c))
                else:
                    plain_items.append(item)
                    plain_keys.append((f, l, c))

            for endpoint, items, kchunk in (
                ("genderGeoBatch", geo_items, geo_keys),
                ("genderBatch", plain_items, plain_keys),
            ):
                if not items:
                    continue
                resp = post_batch(endpoint, items, key)
                results = {r.get("id"): r for r in resp.get("personalNames", [])}
                for idx, (f, l, c) in enumerate(kchunk):
                    item = results.get(str(idx), {})
                    g, prob, gscale, score = parse_result(item)
                    row = {
                        "first_name": f, "last_name": l, "country_iso2": c,
                        "likely_gender": g, "prob_calibrated": prob,
                        "gender_scale": gscale, "score": score,
                        "endpoint": endpoint,
                    }
                    cw.writerow(row)
                    cache[(f, l, c)] = row
                    n_queried += 1
            cf.flush()
            log(f"  queried {min(start + BATCH, len(todo)):,}/{len(todo):,}")
            time.sleep(0.1)
    finally:
        cf.close()
    log(f"Queried {n_queried:,} new names; cache now {len(cache):,} entries.")
    log(f"  cache SHA-256: {file_sha256(CACHE)}")

    # ---- build final author-level file -------------------------------------
    def accept(row):
        """Map a cache row to a coded gender if confident enough, else ''."""
        g = (row.get("likely_gender") or "").lower()
        if g not in ("male", "female"):
            return ""
        try:
            p = float(row.get("prob_calibrated") or 0)
        except ValueError:
            p = 0.0
        return g if p >= NAMSOR_MIN_PROB else ""

    out_cols = list(rows[0].keys()) + [
        "namsor_gender", "namsor_prob", "namsor_endpoint", "gender_coded_final"
    ]
    resolved = 0
    prob_buckets = Counter()  # for threshold sensitivity among undetermined
    paper_final = {}          # openalex_id -> list of final codes (in order)

    with open(OUT_AUTHORS, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=out_cols, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            ns_g, ns_p, ns_ep, final = "", "", "", r.get("gender_coded", "")
            if r.get("gender_coded") == "undetermined":
                first = (r.get("first_name_used") or "").strip()
                if first:
                    key3 = (first, surname(r.get("author_name", "")),
                            (r.get("country_iso2") or "").strip().upper())
                    crow = cache.get(key3)
                    if crow:
                        ns_g = (crow.get("likely_gender") or "").lower()
                        ns_p = crow.get("prob_calibrated", "")
                        ns_ep = crow.get("endpoint", "")
                        try:
                            pf = float(ns_p or 0)
                        except ValueError:
                            pf = 0.0
                        if ns_g in ("male", "female"):
                            for t in (0.70, 0.80, 0.85, 0.90, 0.95):
                                if pf >= t:
                                    prob_buckets[t] += 1
                        acc = accept(crow)
                        if acc:
                            final = acc
                            resolved += 1
            out = dict(r)
            out["namsor_gender"] = ns_g
            out["namsor_prob"] = ns_p
            out["namsor_endpoint"] = ns_ep
            out["gender_coded_final"] = final
            w.writerow(out)
            paper_final.setdefault(r.get("openalex_id", ""), []).append(
                (int(r.get("author_position") or 0), final)
            )
    log(f"Writing {OUT_AUTHORS}")
    log(f"  resolved {resolved:,} previously-undetermined authors "
        f"(prob >= {NAMSOR_MIN_PROB})")
    log(f"  SHA-256: {file_sha256(OUT_AUTHORS)}")

    # ---- recompute paper-level summary on the final coding ------------------
    paper_cols = [
        "openalex_id", "n_authors", "n_female", "n_male", "n_undetermined",
        "n_determined", "share_female_of_determined", "any_female",
        "first_author_gender", "last_author_gender", "all_undetermined",
    ]
    with open(OUT_PAPERS, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=paper_cols)
        w.writeheader()
        for oa, seq in paper_final.items():
            seq.sort()
            codes = [c for _, c in seq]
            nf = codes.count("female")
            nm = codes.count("male")
            nu = codes.count("undetermined")
            nd = nf + nm
            w.writerow({
                "openalex_id": oa,
                "n_authors": len(codes),
                "n_female": nf, "n_male": nm, "n_undetermined": nu,
                "n_determined": nd,
                "share_female_of_determined": f"{nf / nd:.4f}" if nd else "",
                "any_female": "TRUE" if nf > 0 else "FALSE",
                "first_author_gender": codes[0] if codes else "",
                "last_author_gender": codes[-1] if codes else "",
                "all_undetermined": "TRUE" if codes and nd == 0 else "FALSE",
            })
    log(f"Writing {OUT_PAPERS}")
    log(f"  SHA-256: {file_sha256(OUT_PAPERS)}")

    # ---- threshold sensitivity ----------------------------------------------
    log("")
    log("NamSor resolution among undetermined names, by probability threshold:")
    for t in (0.70, 0.80, 0.85, 0.90, 0.95):
        log(f"  prob >= {t:.2f}:  {prob_buckets.get(t, 0):,} authors resolvable")
    log(f"(final coding uses prob >= {NAMSOR_MIN_PROB})")
    log("Stage 8b complete.")


if __name__ == "__main__":
    main()
