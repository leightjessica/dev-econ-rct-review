#!/usr/bin/env python3
"""Stage 6e - apply manual Not-RCT corrections from author inspection (2026-06-04).

Ten papers classified as RCTs by the Stage 3b LLM were flagged on manual review
as not randomized controlled trials (lab/survey experiments, methods/measurement
papers, a government-lottery analysis). They remain development-economics papers,
so they are NOT removed from final_dataset.csv; instead rct_classification is
flipped yes -> no, the subtype is cleared, and an audit note is appended to
rct_justification. They are dropped from funders_6a.csv (the per-RCT funder file).

All 10 have n_funders == 0, so no funder summary / long-format output changes;
only the RCT count (417 -> 407) and the no-funder worklist change. After this
script, re-run 06a_normalize_funders.py to regenerate the derived funder files
and rcts_need_fulltext.csv from the corrected funders_6a.csv.

Writes timestamped .bak copies before modifying anything.
"""

import csv
import os
import shutil
from collections import Counter
from datetime import datetime, timezone

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.abspath(os.path.join(SCRIPT_DIR, "..", "data"))
FINAL = os.path.join(DATA, "final_dataset.csv")
FUNDERS = os.path.join(DATA, "funders_6a.csv")

# DOI -> author's reason for reclassification (verbatim from manual inspection).
CORRECTIONS = {
    "10.1257/app.20220258": "analyzing a government lottery",
    "10.1257/app.20220601": "lab experiment",
    "10.3982/ecta17527": "methods paper",
    "10.1162/rest_a_01552": "methods paper",
    "10.1016/j.jdeveco.2022.103026": "survey experiment",
    "10.1016/j.jdeveco.2022.103004": "methods / measurement",
    "10.1016/j.jdeveco.2023.103097": "methods / survey experiment",
    "10.1016/j.jdeveco.2023.103069": "methods / measurement",
    "10.1016/j.jdeveco.2024.103265": "survey experiment",
    "10.1016/j.jdeveco.2025.103462": "non-experimental evaluation",
}
CORR_L = {d.lower() for d in CORRECTIONS}
STAMP = datetime.now(timezone.utc).strftime("%Y-%m-%d")


def backup(path):
    bak = f"{path}.{STAMP}.bak"
    shutil.copy2(path, bak)
    print(f"  backup -> {os.path.basename(bak)}")


def reason_for(doi):
    return CORRECTIONS[next(d for d in CORRECTIONS if d.lower() == doi.lower())]


def fix_final():
    with open(FINAL, encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
        fields = rows[0].keys()
    changed = 0
    for r in rows:
        if (r["doi"] or "").lower() in CORR_L:
            assert r["rct_classification"] == "yes", f"{r['doi']} not currently yes"
            note = (f"[MANUAL OVERRIDE {STAMP}: rct yes->no on author review - "
                    f"not an RCT ({reason_for(r['doi'])})]")
            r["rct_classification"] = "no"
            r["rct_subtype"] = ""
            r["rct_confidence"] = "manual_override"
            r["rct_justification"] = (r["rct_justification"] + " " + note).strip()
            changed += 1
    backup(FINAL)
    with open(FINAL, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(fields))
        w.writeheader()
        w.writerows(rows)
    print(f"final_dataset.csv: reclassified {changed} rows yes->no")
    return rows


def fix_funders():
    with open(FUNDERS, encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
        fields = rows[0].keys()
    keep = [r for r in rows if (r["doi"] or "").lower() not in CORR_L]
    backup(FUNDERS)
    with open(FUNDERS, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(fields))
        w.writeheader()
        w.writerows(keep)
    print(f"funders_6a.csv: {len(rows)} -> {len(keep)} rows "
          f"(removed {len(rows) - len(keep)})")


def report(rows):
    rcts = [r for r in rows if r["rct_classification"] == "yes"]
    dev = [r for r in rows if r["is_development"].upper() == "TRUE"]
    print("\n=== Recomputed counts (corrected final_dataset.csv) ===")
    print(f"Development papers: {len(dev)}   RCTs: {len(rcts)}")
    print("\nRCTs by journal:")
    jc = Counter(r["journal_short"] for r in rcts)
    for j, n in sorted(jc.items()):
        print(f"  {j:14s} {n}")
    print("\nRCT subtype distribution:")
    sc = Counter((r["rct_subtype"] or "(blank)") for r in rcts)
    for s, n in sorted(sc.items(), key=lambda x: -x[1]):
        print(f"  {s:18s} {n}")


def main():
    print("Applying 10 Not-RCT corrections...\n")
    fix_funders()
    rows = fix_final()
    report(rows)
    print("\nNext: re-run scripts/06a_normalize_funders.py to refresh derived files.")


if __name__ == "__main__":
    main()
