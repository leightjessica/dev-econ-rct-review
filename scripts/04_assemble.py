"""
Stage 4: Assemble the final dataset and summary tables.

Reads (in priority order, taking the first that exists):
  data/rct_classified.csv     (full pipeline output)
  data/dev_classified.csv     (if Stage 3b was skipped)
  data/dev_filtered.csv       (if both Stage 3a and 3b were skipped)

Writes:
  data/final_dataset.csv          (one row per development paper, with RCT flag)
  data/summary_journal_year.csv   (counts by journal × year)
  data/04_assemble.log

The final dataset:
- Includes only rows where is_development == 'TRUE' AND type == 'article'
- Restricts to a clean column set suitable for sharing
- Records a SHA-256 of the file content in the run log for reproducibility

Replicability: standard library only.
"""

import csv
import hashlib
import os
import sys
from collections import defaultdict
from datetime import datetime, timezone

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_DIR = os.path.abspath(os.path.join(SCRIPT_DIR, ".."))

SRC_CANDIDATES = [
    os.path.join(PROJECT_DIR, "data", "rct_classified.csv"),
    os.path.join(PROJECT_DIR, "data", "dev_classified.csv"),
    os.path.join(PROJECT_DIR, "data", "dev_filtered.csv"),
]
OUT_FINAL = os.path.join(PROJECT_DIR, "data", "final_dataset.csv")
OUT_SUMMARY = os.path.join(PROJECT_DIR, "data", "summary_journal_year.csv")
LOG_TXT = os.path.join(PROJECT_DIR, "data", "04_assemble.log")

FINAL_COLS = [
    "journal_short", "journal_full", "publication_year", "publication_date",
    "volume", "issue", "first_page", "last_page",
    "doi", "title", "authors", "abstract", "abstract_source",
    "jel_codes", "jel_codes_o_only",
    "is_development", "dev_filter_source",
    "dev_llm_classification", "dev_llm_justification",
    "rct_classification", "rct_subtype", "rct_confidence", "rct_justification",
    "openalex_id", "snapshot_utc",
    "dev_llm_model", "dev_llm_prompt_version",
    "rct_llm_model", "rct_llm_prompt_version",
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


def main():
    open(LOG_TXT, "w").close()

    src = next((c for c in SRC_CANDIDATES if os.path.exists(c)), None)
    if not src:
        sys.exit(f"No source CSV found among:\n  " + "\n  ".join(SRC_CANDIDATES))
    log(f"Source: {src}")

    with open(src, encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    log(f"  loaded {len(rows):,} rows")

    # Filter to development articles
    keep = [r for r in rows
            if r.get("is_development") == "TRUE"
            and r.get("type") == "article"]
    log(f"  development articles: {len(keep):,}")

    # Write final CSV with selected columns
    for r in keep:
        for c in FINAL_COLS:
            r.setdefault(c, "")

    log(f"Writing {OUT_FINAL}")
    with open(OUT_FINAL, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=FINAL_COLS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(keep)
    log(f"  SHA-256: {file_sha256(OUT_FINAL)}")

    # Summary by journal × year
    by_jy = defaultdict(lambda: {"dev_total": 0, "rct_yes": 0, "rct_no": 0, "rct_uncertain": 0})
    for r in keep:
        j = r.get("journal_short", "")
        y = (r.get("publication_year") or "")[:4] or "?"
        cell = by_jy[(j, y)]
        cell["dev_total"] += 1
        rct = (r.get("rct_classification") or "").lower()
        if rct == "yes":
            cell["rct_yes"] += 1
        elif rct == "no":
            cell["rct_no"] += 1
        elif rct == "uncertain":
            cell["rct_uncertain"] += 1

    log(f"Writing {OUT_SUMMARY}")
    with open(OUT_SUMMARY, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["journal", "year", "dev_total", "rct_yes", "rct_no",
                    "rct_uncertain", "rct_yes_pct"])
        for (j, y) in sorted(by_jy):
            d = by_jy[(j, y)]
            pct = 100 * d["rct_yes"] / max(d["dev_total"], 1)
            w.writerow([j, y, d["dev_total"], d["rct_yes"], d["rct_no"],
                        d["rct_uncertain"], f"{pct:.1f}%"])
    log(f"  SHA-256: {file_sha256(OUT_SUMMARY)}")

    # Per-journal aggregate to log
    by_j = defaultdict(lambda: {"dev": 0, "yes": 0, "no": 0, "unc": 0})
    for r in keep:
        d = by_j[r.get("journal_short", "")]
        d["dev"] += 1
        rct = (r.get("rct_classification") or "").lower()
        if rct == "yes": d["yes"] += 1
        elif rct == "no": d["no"] += 1
        elif rct == "uncertain": d["unc"] += 1

    log("Per-journal aggregate:")
    log(f"  {'journal':14s} {'dev':>5s} {'RCT_yes':>8s} {'RCT_no':>7s} {'RCT_unc':>8s} {'%RCT':>6s}")
    for j in ["AER", "AERI", "AEJ_Applied", "AEJ_EP", "ECMA", "QJE",
              "JPE", "RES", "RESTAT", "EJ", "JEEA", "JDE"]:
        d = by_j.get(j, {"dev": 0, "yes": 0, "no": 0, "unc": 0})
        pct = 100 * d["yes"] / max(d["dev"], 1)
        log(f"  {j:14s} {d['dev']:>5d} {d['yes']:>8d} {d['no']:>7d} {d['unc']:>8d} {pct:>5.1f}%")
    log("Stage 4 complete.")


if __name__ == "__main__":
    main()
