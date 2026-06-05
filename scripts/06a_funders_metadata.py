"""
Stage 6a: Funder list for the RCT subsample, from free structured metadata.

Reads:  data/final_dataset.csv  (filters to rct_classification == 'yes')
Writes: data/funders_6a.csv          one row per RCT paper, funders joined
        data/funders_6a_long.csv     one row per (paper, funder, source)
        data/funders_6a_summary.csv  funder frequency tally across the sample
        data/06a_funders_metadata.log

For each RCT paper we query two open APIs by identifier and union the funder
NAMES they report (grant/award numbers are deliberately ignored — only the
list of funders is wanted):

  - Crossref Works API   GET https://api.crossref.org/works/{doi}
        -> message.funder[].name
  - OpenAlex Works API   GET https://api.openalex.org/works/{openalex_id}
        -> grants[].funder_display_name

Coverage is partial and uneven by publisher (funder deposition to Crossref is
strong for AEA journals, sparse for several others); OpenAlex's grants field is
partly mined from full text and fills some gaps. This stage is therefore a
free baseline, not a complete census — papers with no structured funder
metadata are written with an empty funder list and a status flag, so the
residual that needs full-text (manual-PDF) extraction is explicit.

Replicability notes
-------------------
- Python standard library only (no pip install required).
- Polite-pool mailto header on both APIs.
- Resumable: rerunning skips DOIs already fetched successfully; checkpoint
  every 50 papers.
- Each row records the UTC snapshot timestamp.
"""

import csv
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone

# ---- Configuration ----------------------------------------------------------

EMAIL = "J.Leight@cgiar.org"  # polite pool (Crossref + OpenAlex)
RCT_VALUE = "yes"             # final_dataset.csv rct_classification flag to keep
SAVE_EVERY = 50
REQUEST_TIMEOUT = 60
POLITE_DELAY = 0.15           # seconds between API calls

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_DIR = os.path.abspath(os.path.join(SCRIPT_DIR, ".."))
IN_CSV = os.path.join(PROJECT_DIR, "data", "final_dataset.csv")
OUT_CSV = os.path.join(PROJECT_DIR, "data", "funders_6a.csv")
OUT_LONG = os.path.join(PROJECT_DIR, "data", "funders_6a_long.csv")
OUT_SUMMARY = os.path.join(PROJECT_DIR, "data", "funders_6a_summary.csv")
LOG_TXT = os.path.join(PROJECT_DIR, "data", "06a_funders_metadata.log")

PAPER_FIELDS = [
    "doi", "journal_short", "publication_year", "title",
    "n_funders", "funders", "funder_sources",
    "crossref_n", "openalex_n", "fetch_status", "snapshot_utc",
]

# ---- Helpers ----------------------------------------------------------------

def log(msg):
    line = f"[{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%SZ')}] {msg}"
    try:
        print(line, flush=True)
    except UnicodeEncodeError:
        print(line.encode("ascii", "replace").decode("ascii"), flush=True)
    with open(LOG_TXT, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def get_json(url):
    """GET a URL and parse JSON, with polite retry/backoff. Returns (data, err).

    A 404 is returned as (None, 'http_404') without retrying, since it means the
    record is simply absent from that source rather than a transient failure.
    """
    req = urllib.request.Request(
        url, headers={"User-Agent": f"dev-rct-review-funders (mailto:{EMAIL})"}
    )
    attempts = 0
    while True:
        try:
            with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT) as resp:
                return json.load(resp), None
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return None, "http_404"
            attempts += 1
            if attempts > 4:
                return None, f"http_{e.code}"
            time.sleep(2 ** attempts)
        except Exception as e:
            attempts += 1
            if attempts > 4:
                return None, f"error_{type(e).__name__}"
            time.sleep(2 ** attempts)


def clean_name(name):
    """Trim and collapse internal whitespace; drop empties."""
    if not name:
        return ""
    return re.sub(r"\s+", " ", str(name)).strip()


def norm_key(name):
    """Loose key for deduping a funder within a paper and across the sample:
    lowercased, whitespace-collapsed, trailing punctuation stripped. This merges
    only trivial variants (case/spacing); it deliberately does NOT unify
    abbreviations vs full names (e.g. 'NSF' vs 'National Science Foundation'),
    which is left to a downstream normalization pass."""
    k = clean_name(name).lower().rstrip(".,;")
    return k


def crossref_funders(doi):
    url = f"https://api.crossref.org/works/{urllib.parse.quote(doi)}?mailto={EMAIL}"
    data, err = get_json(url)
    if err:
        return [], err
    msg = (data or {}).get("message", {})
    names = [clean_name(f.get("name")) for f in (msg.get("funder") or [])]
    return [n for n in names if n], None


def openalex_funders(openalex_id):
    # openalex_id is stored as a full URL, e.g. https://openalex.org/W2887450805.
    work_id = openalex_id.rstrip("/").split("/")[-1]
    if not work_id:
        return [], "no_id"
    url = f"https://api.openalex.org/works/{work_id}?mailto={EMAIL}"
    data, err = get_json(url)
    if err:
        return [], err
    names = [clean_name(g.get("funder_display_name")) for g in (data.get("grants") or [])]
    return [n for n in names if n], None


def union_funders(cr_names, oa_names):
    """Union by loose key, preserving first-seen display form, and tag the
    source of each funder as crossref / openalex / both."""
    order = []
    by_key = {}
    src = {}
    for name in cr_names:
        k = norm_key(name)
        if not k:
            continue
        if k not in by_key:
            by_key[k] = name
            order.append(k)
            src[k] = {"crossref"}
        else:
            src[k].add("crossref")
    for name in oa_names:
        k = norm_key(name)
        if not k:
            continue
        if k not in by_key:
            by_key[k] = name
            order.append(k)
            src[k] = {"openalex"}
        else:
            src[k].add("openalex")
    funders = [by_key[k] for k in order]
    sources = ["both" if src[k] == {"crossref", "openalex"}
               else next(iter(src[k])) for k in order]
    return funders, sources


def load_existing():
    """Return dict[doi -> paper_row] for DOIs already fetched successfully."""
    if not os.path.exists(OUT_CSV):
        return {}
    out = {}
    with open(OUT_CSV, encoding="utf-8") as f:
        for r in csv.DictReader(f):
            doi = (r.get("doi") or "").strip()
            status = (r.get("fetch_status") or "").strip()
            # Re-fetch rows whose previous run errored on BOTH sources.
            if doi and status and not status.startswith("error_both"):
                out[doi] = r
    return out


# ---- Main -------------------------------------------------------------------

def main():
    open(LOG_TXT, "w").close()
    snapshot = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    with open(IN_CSV, encoding="utf-8") as f:
        all_rows = list(csv.DictReader(f))
    rcts = [r for r in all_rows if (r.get("rct_classification") or "").strip() == RCT_VALUE]
    log(f"Stage 6a start. {len(rcts)} RCT papers (of {len(all_rows)} in final_dataset).")

    existing = load_existing()
    if existing:
        log(f"Resuming: {len(existing)} papers already fetched in {os.path.basename(OUT_CSV)}.")

    paper_rows = []
    n_done = n_skip = 0
    n_with_funder = 0

    def checkpoint():
        with open(OUT_CSV, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=PAPER_FIELDS)
            w.writeheader()
            w.writerows(paper_rows)

    for r in rcts:
        doi = (r.get("doi") or "").strip()
        if doi in existing:
            paper_rows.append({k: existing[doi].get(k, "") for k in PAPER_FIELDS})
            n_skip += 1
            if existing[doi].get("funders"):
                n_with_funder += 1
            continue

        cr_names, cr_err = crossref_funders(doi)
        time.sleep(POLITE_DELAY)
        oa_names, oa_err = openalex_funders((r.get("openalex_id") or "").strip())
        time.sleep(POLITE_DELAY)

        funders, sources = union_funders(cr_names, oa_names)
        if cr_err and oa_err:
            status = f"error_both:cr={cr_err};oa={oa_err}"
        elif not funders:
            status = "ok_no_funders"
        else:
            status = "ok"

        paper_rows.append({
            "doi": doi,
            "journal_short": r.get("journal_short", ""),
            "publication_year": r.get("publication_year", ""),
            "title": (r.get("title") or "").strip(),
            "n_funders": len(funders),
            "funders": " | ".join(funders),
            "funder_sources": " | ".join(sources),
            "crossref_n": len(cr_names),
            "openalex_n": len(oa_names),
            "fetch_status": status,
            "snapshot_utc": snapshot,
        })
        n_done += 1
        if funders:
            n_with_funder += 1
        if (n_done % SAVE_EVERY) == 0:
            checkpoint()
            log(f"  progress: fetched {n_done}, skipped {n_skip}, "
                f"{n_with_funder} papers with >=1 funder so far")

    checkpoint()

    # --- Long format: one row per (paper, funder, source) -------------------
    long_rows = []
    for p in paper_rows:
        funders = [x for x in (p["funders"].split(" | ") if p["funders"] else [])]
        srcs = [x for x in (p["funder_sources"].split(" | ") if p["funder_sources"] else [])]
        for i, fn in enumerate(funders):
            long_rows.append({
                "doi": p["doi"],
                "journal_short": p["journal_short"],
                "publication_year": p["publication_year"],
                "funder": fn,
                "source": srcs[i] if i < len(srcs) else "",
            })
    with open(OUT_LONG, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["doi", "journal_short", "publication_year", "funder", "source"])
        w.writeheader()
        w.writerows(long_rows)

    # --- Summary: funder frequency across the sample ------------------------
    tally = {}          # norm_key -> [display_name, paper_count]
    for p in paper_rows:
        seen_here = set()
        for fn in (p["funders"].split(" | ") if p["funders"] else []):
            k = norm_key(fn)
            if not k or k in seen_here:
                continue
            seen_here.add(k)
            if k not in tally:
                tally[k] = [fn, 0]
            tally[k][1] += 1
    summary = sorted(tally.values(), key=lambda x: (-x[1], x[0].lower()))
    with open(OUT_SUMMARY, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["funder", "n_papers"])
        w.writeheader()
        for name, cnt in summary:
            w.writerow({"funder": name, "n_papers": cnt})

    n_total = len(paper_rows)
    n_none = sum(1 for p in paper_rows if not p["funders"])
    log(f"Stage 6a complete. {n_total} papers; {n_with_funder} with >=1 funder, "
        f"{n_none} with none (need full-text). Distinct funders: {len(summary)}.")
    log(f"Outputs: {os.path.basename(OUT_CSV)}, {os.path.basename(OUT_LONG)}, "
        f"{os.path.basename(OUT_SUMMARY)}")
    if summary[:15]:
        log("Top funders by paper count:")
        for name, cnt in summary[:15]:
            log(f"  {cnt:4d}  {name}")


if __name__ == "__main__":
    main()
