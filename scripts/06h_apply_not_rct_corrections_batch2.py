#!/usr/bin/env python3
"""Stage 6h - apply a second batch of manual Not-RCT corrections (2026-06-09).

Fifteen papers classified as RCTs by the Stage 3b LLM were flagged on author
inspection as not randomized controlled trials. They are lab experiments,
survey/measurement experiments, or methods papers (see CORRECTIONS below for
the verbatim per-DOI reason). They remain development-economics papers, so they
are NOT removed from final_dataset.csv; instead rct_classification is flipped
yes -> no, rct_subtype is cleared, rct_confidence is set to manual_override, and
an audit note is appended to rct_justification.

This mirrors scripts/06e_apply_not_rct_corrections.py (the 2026-06-04 batch of
10). Differences from that batch:

  * All 15 papers ARE present in funders_6a.csv (the per-RCT funder file), so
    they are removed from it here, which materially changes the funder figures
    (fig20-fig23). (The 06e batch all had n_funders == 0.)

  * The derived classification snapshots country_classified.csv and
    topic_classified.csv each carry their OWN copy of rct_classification /
    rct_subtype (taken when they were built in May). The RCT-filtered figures
    fig13 (top RCT countries) and fig16 (topic x RCT) read the flag from those
    snapshots, not from final_dataset.csv. We therefore re-sync the
    rct_classification and rct_subtype columns in both snapshots from the
    corrected final_dataset.csv, keyed by DOI. This also propagates the
    2026-06-04 batch of 10 (06e), which was never pushed into the snapshots,
    so the regenerated country/topic figures become fully consistent with
    final_dataset.csv.

Writes timestamped .bak copies of every file before modifying it.

After this script, re-run the deterministic RCT figure pipeline:
    06a_normalize_funders.py  ->  06g_merge_funders.py  ->  11_funders_by_journal_tier.py
    06c_country_analysis.py   ->  05_make_charts.py      ->  07_topic_bar_chart.py
"""

import csv
import os
import shutil
import sys
from collections import Counter
from datetime import datetime, timezone

csv.field_size_limit(min(sys.maxsize, 2147483647))

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.abspath(os.path.join(SCRIPT_DIR, "..", "data"))
FINAL = os.path.join(DATA, "final_dataset.csv")
FUNDERS = os.path.join(DATA, "funders_6a.csv")
COUNTRY = os.path.join(DATA, "country_classified.csv")
TOPIC = os.path.join(DATA, "topic_classified.csv")

# DOI -> author's reason for reclassification (verbatim from manual inspection).
CORRECTIONS = {
    "10.3982/ecta19303": "methods paper",
    "10.1093/ej/ueae097": "lab experiment",
    "10.1093/ej/ueae116": "lab experiment",
    "10.1016/j.jdeveco.2022.102978": "measurement",
    "10.1016/j.jdeveco.2023.103199": "survey experiment",
    "10.1016/j.jdeveco.2023.103136": "measurement",
    "10.1016/j.jdeveco.2023.103198": "measurement",
    "10.1016/j.jdeveco.2023.103148": "measurement",
    "10.1016/j.jdeveco.2024.103392": "survey experiment",
    "10.1016/j.jdeveco.2024.103309": "lab experiment",
    "10.1016/j.jdeveco.2024.103435": "lab experiment",
    "10.1016/j.jdeveco.2025.103612": "survey experiment",
    "10.1016/j.jdeveco.2024.103449": "survey experiment",
    "10.1016/j.jdeveco.2025.103532": "measurement",
    "10.1093/jeea/jvac068": "survey experiment",
}
CORR_L = {d.lower() for d in CORRECTIONS}
STAMP = datetime.now(timezone.utc).strftime("%Y-%m-%d")


def backup(path):
    bak = f"{path}.{STAMP}.bak"
    shutil.copy2(path, bak)
    print(f"  backup -> {os.path.basename(bak)}")


def reason_for(doi):
    return CORRECTIONS[next(d for d in CORRECTIONS if d.lower() == doi.lower())]


def read_rows(path):
    with open(path, encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    return rows, list(rows[0].keys())


def write_rows(path, rows, fields):
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)


def fix_final():
    rows, fields = read_rows(FINAL)
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
    assert changed == len(CORRECTIONS), f"expected {len(CORRECTIONS)} flips, made {changed}"
    backup(FINAL)
    write_rows(FINAL, rows, fields)
    print(f"final_dataset.csv: reclassified {changed} rows yes->no")
    return rows


def fix_funders():
    rows, fields = read_rows(FUNDERS)
    keep = [r for r in rows if (r["doi"] or "").lower() not in CORR_L]
    backup(FUNDERS)
    write_rows(FUNDERS, keep, fields)
    print(f"funders_6a.csv: {len(rows)} -> {len(keep)} rows "
          f"(removed {len(rows) - len(keep)})")


def sync_snapshot(path, label, final_by_doi):
    """Re-sync rct_classification + rct_subtype in a derived snapshot CSV from the
    corrected final_dataset.csv, keyed by DOI. Reports rows changed."""
    rows, fields = read_rows(path)
    for col in ("rct_classification", "rct_subtype"):
        assert col in fields, f"{label}: missing column {col}"
    changed = 0
    changed_dois = []
    for r in rows:
        fr = final_by_doi.get((r["doi"] or "").lower())
        if not fr:
            continue
        if (r["rct_classification"] != fr["rct_classification"]
                or r["rct_subtype"] != fr["rct_subtype"]):
            r["rct_classification"] = fr["rct_classification"]
            r["rct_subtype"] = fr["rct_subtype"]
            changed += 1
            changed_dois.append((r["doi"] or "").lower())
    backup(path)
    write_rows(path, rows, fields)
    in_batch = sum(1 for d in changed_dois if d in CORR_L)
    print(f"{label}: synced rct flag from final_dataset; {changed} rows changed "
          f"({in_batch} from this batch of 15, {changed - in_batch} from prior overrides)")


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
    print(f"Applying {len(CORRECTIONS)} Not-RCT corrections (batch 2, {STAMP})...\n")
    rows = fix_final()
    final_by_doi = {(r["doi"] or "").lower(): r for r in rows}
    fix_funders()
    sync_snapshot(COUNTRY, "country_classified.csv", final_by_doi)
    sync_snapshot(TOPIC, "topic_classified.csv", final_by_doi)
    report(rows)
    print("\nNext: re-run 06a_normalize_funders -> 06g_merge_funders -> "
          "11_funders_by_journal_tier -> 06c_country_analysis -> "
          "05_make_charts -> 07_topic_bar_chart")


if __name__ == "__main__":
    main()
