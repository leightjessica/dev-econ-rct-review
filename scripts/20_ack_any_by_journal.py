"""
Stage 20: share of RCTs that report ANY acknowledgments and ANY seminars,
by journal tier and by individual journal.

Reads:  data/ack_classified.csv (Stage 17 output)
Writes: data/figures/fig33_ack_any_by_tier.{png,pdf}
        data/figures/fig34_ack_any_by_journal.{png,pdf}
        data/ack_any_shares.csv   (per-tier and per-journal shares, both metrics)

Companion to 19_ack_by_journal.py, which plots the *levels* (mean individuals,
mean seminars). This script instead plots *incidence* -- the percentage of
papers that report at least one of each -- which separates the extensive margin
(does the paper acknowledge anyone / present anywhere at all?) from the
intensive margin (how many?).

Two extensive-margin metrics, per the project lead's specification (2026-06-24):
  any acknowledgments = thanks >=1 named individual for COMMENTS
                        (n_individuals_comments > 0); research assistants,
                        anonymous referees, editor do not count toward this.
  any seminars        = reports presenting at >=1 venue, named or vague
                        (n_seminars > 0 OR seminars_unspecified == True).

Analysis sample = RCTs with classify_status in {ok, no_block}; a no_block paper
contributes False / False (a valid zero). no_pdf / pdf_error rows are excluded.

Usage:
  python 20_ack_any_by_journal.py
"""

import csv
import os
import sys
from collections import defaultdict
from datetime import datetime, timezone

csv.field_size_limit(min(sys.maxsize, 2147483647))

try:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.patches import Patch
except ImportError:
    raise SystemExit("matplotlib is required. Run:\n  pip install matplotlib")

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_DIR = os.path.abspath(os.path.join(SCRIPT_DIR, ".."))
IN_CSV = os.path.join(PROJECT_DIR, "data", "ack_classified.csv")
FIG_DIR = os.path.join(PROJECT_DIR, "data", "figures")
OUT_CSV = os.path.join(PROJECT_DIR, "data", "ack_any_shares.csv")
LOG_TXT = os.path.join(PROJECT_DIR, "data", "20_ack_any_by_journal.log")

BLUE = "#3a7ca5"  # any acknowledgments (individuals)
DARK = "#16425b"
GOLD = "#b5862b"  # any seminars

# Journal tiers (journal_short codes), matching 18_ack_charts.py and
# 19_ack_by_journal.py.
TIER_TOP5 = {"AER", "QJE", "JPE", "ECMA", "RES"}
TIER_OTHER_GI = {"AERI", "AEJ_Applied", "AEJ_EP", "RESTAT", "EJ", "JEEA"}
TIER_JDE = {"JDE"}
TIERS = [("top5", "Top 5"), ("other_gi", "Other general-interest"), ("jde", "JDE")]

# Display labels for the journal_short codes (kept short for the axis), matching
# 19_ack_by_journal.py.
JOURNAL_LABEL = {
    "AER": "AER", "QJE": "QJE", "JPE": "JPE", "ECMA": "Econometrica",
    "RES": "REStud", "AERI": "AER: Insights", "AEJ_Applied": "AEJ: Applied",
    "AEJ_EP": "AEJ: Econ Policy", "RESTAT": "REStat", "EJ": "Economic Journal",
    "JEEA": "JEEA", "JDE": "JDE",
}


def log(msg):
    line = f"[{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%SZ')}] {msg}"
    print(line, flush=True)
    with open(LOG_TXT, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def save(fig, name):
    fig.tight_layout()
    fig.savefig(os.path.join(FIG_DIR, f"{name}.png"), dpi=300, bbox_inches="tight")
    fig.savefig(os.path.join(FIG_DIR, f"{name}.pdf"), bbox_inches="tight")
    plt.close(fig)
    log(f"  wrote {name}.png + {name}.pdf")


def tier_of(js):
    if js in TIER_TOP5:
        return "top5"
    if js in TIER_OTHER_GI:
        return "other_gi"
    if js in TIER_JDE:
        return "jde"
    return "other"


def pct(flags):
    """Share (percent) of True values in a list of booleans; 0 if empty."""
    return 100.0 * sum(flags) / len(flags) if flags else 0.0


def grouped_bar_by_tier(ack_by_tier, sem_by_tier):
    """Vertical grouped bars: % any-ack and % any-seminar, by tier."""
    labels = [lab for key, lab in TIERS]
    ns = [len(ack_by_tier[key]) for key, _ in TIERS]
    ack = [pct(ack_by_tier[key]) for key, _ in TIERS]
    sem = [pct(sem_by_tier[key]) for key, _ in TIERS]

    x = range(len(labels))
    w = 0.38
    fig, ax = plt.subplots(figsize=(7.5, 4.5))
    b1 = ax.bar([i - w / 2 for i in x], ack, w, color=BLUE, edgecolor="black",
                linewidth=0.5, label="Any acknowledgments (>=1 individual)")
    b2 = ax.bar([i + w / 2 for i in x], sem, w, color=GOLD, edgecolor="black",
                linewidth=0.5, label="Any seminars (named or vague)")
    ax.set_xticks(list(x))
    ax.set_xticklabels([f"{lab}\n(n={n})" for lab, n in zip(labels, ns)])
    ax.set_ylabel("Share of RCTs (%)")
    ax.set_title("Share reporting any acknowledgments / any seminars, "
                 "by journal tier (2021-2025)")
    ax.set_ylim(0, 105)
    ax.grid(axis="y", alpha=0.3)
    ax.set_axisbelow(True)
    for bars in (b1, b2):
        for b in bars:
            ax.text(b.get_x() + b.get_width() / 2, b.get_height() + 1.0,
                    f"{b.get_height():.0f}", ha="center", va="bottom", fontsize=9)
    ax.legend(loc="lower center", fontsize=9)
    save(fig, "fig33_ack_any_by_tier")


def grouped_barh_by_journal(stats):
    """Horizontal grouped bars: % any-ack and % any-seminar, one pair per
    journal, sorted descending by % any-ack."""
    js = sorted(stats, key=lambda j: stats[j]["pct_ack"])  # asc -> top is largest
    labels = [f"{JOURNAL_LABEL.get(j, j)}  (n={stats[j]['n']})" for j in js]
    ack = [stats[j]["pct_ack"] for j in js]
    sem = [stats[j]["pct_sem"] for j in js]

    y = range(len(js))
    h = 0.38
    fig, ax = plt.subplots(figsize=(8, 6.5))
    ax.barh([i + h / 2 for i in y], ack, h, color=BLUE, edgecolor="black",
            linewidth=0.5, label="Any acknowledgments (>=1 individual)")
    ax.barh([i - h / 2 for i in y], sem, h, color=GOLD, edgecolor="black",
            linewidth=0.5, label="Any seminars (named or vague)")
    ax.set_yticks(list(y))
    ax.set_yticklabels(labels)
    ax.set_xlabel("Share of RCTs (%)")
    ax.set_title("Share reporting any acknowledgments / any seminars, "
                 "by journal (2021-2025)")
    ax.set_xlim(0, 108)
    ax.grid(axis="x", alpha=0.3)
    ax.set_axisbelow(True)
    for yi, a, s in zip(y, ack, sem):
        ax.text(a + 1.0, yi + h / 2, f"{a:.0f}", va="center", ha="left", fontsize=8)
        ax.text(s + 1.0, yi - h / 2, f"{s:.0f}", va="center", ha="left", fontsize=8)
    handles = [Patch(facecolor=BLUE, edgecolor="black",
                     label="Any acknowledgments (>=1 individual)"),
               Patch(facecolor=GOLD, edgecolor="black",
                     label="Any seminars (named or vague)")]
    ax.legend(handles=handles, loc="lower right", fontsize=9)
    save(fig, "fig34_ack_any_by_journal")


def main():
    os.makedirs(FIG_DIR, exist_ok=True)
    open(LOG_TXT, "w").close()
    with open(IN_CSV, encoding="utf-8") as f:
        allrows = list(csv.DictReader(f))
    rows = []
    for r in allrows:
        if r["classify_status"] not in ("ok", "no_block"):
            continue
        try:
            ind = int(r["n_individuals_comments"])
            sem = int(r["n_seminars"])
        except (ValueError, TypeError):
            continue
        unspec = str(r.get("seminars_unspecified", "")).strip().lower() == "true"
        r["_any_ack"] = ind > 0
        r["_any_sem"] = (sem > 0) or unspec
        rows.append(r)
    log(f"Analysis sample: {len(rows)} RCTs (classify_status ok/no_block)")
    log(f"Overall: any acknowledgments {pct([r['_any_ack'] for r in rows]):.1f}%, "
        f"any seminars {pct([r['_any_sem'] for r in rows]):.1f}%")

    # By-tier and by-journal boolean vectors.
    ack_tier, sem_tier = defaultdict(list), defaultdict(list)
    ack_j, sem_j = defaultdict(list), defaultdict(list)
    for r in rows:
        t = tier_of(r["journal_short"])
        ack_tier[t].append(r["_any_ack"]); sem_tier[t].append(r["_any_sem"])
        ack_j[r["journal_short"]].append(r["_any_ack"])
        sem_j[r["journal_short"]].append(r["_any_sem"])

    grouped_bar_by_tier(ack_tier, sem_tier)

    stats = {}
    for j in ack_j:
        stats[j] = {
            "n": len(ack_j[j]),
            "pct_ack": pct(ack_j[j]),
            "pct_sem": pct(sem_j[j]),
        }
    grouped_barh_by_journal(stats)

    # ---- shares CSV: tiers first, then journals, then overall ----
    fields = ["group_type", "group", "tier", "n", "pct_any_ack", "pct_any_seminar"]
    with open(OUT_CSV, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerow({
            "group_type": "all", "group": "All", "tier": "",
            "n": len(rows),
            "pct_any_ack": round(pct([r["_any_ack"] for r in rows]), 1),
            "pct_any_seminar": round(pct([r["_any_sem"] for r in rows]), 1),
        })
        for key, lab in TIERS:
            w.writerow({
                "group_type": "tier", "group": lab, "tier": lab,
                "n": len(ack_tier[key]),
                "pct_any_ack": round(pct(ack_tier[key]), 1),
                "pct_any_seminar": round(pct(sem_tier[key]), 1),
            })
        for j in sorted(stats, key=lambda j: -stats[j]["pct_ack"]):
            s = stats[j]
            w.writerow({
                "group_type": "journal", "group": JOURNAL_LABEL.get(j, j),
                "tier": dict(TIERS)[tier_of(j)], "n": s["n"],
                "pct_any_ack": round(s["pct_ack"], 1),
                "pct_any_seminar": round(s["pct_sem"], 1),
            })
    log(f"  wrote {os.path.basename(OUT_CSV)} "
        f"(1 overall + {len(TIERS)} tiers + {len(stats)} journals)")
    log("Done.")


if __name__ == "__main__":
    main()
