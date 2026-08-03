"""
Stage 19: acknowledgments + seminars broken out by journal tier and by
individual journal, each metric on its own figure.

Reads:  data/ack_classified.csv (Stage 17 output)
Writes: data/figures/fig29_ack_individuals_by_tier.{png,pdf}
        data/figures/fig30_ack_seminars_by_tier.{png,pdf}
        data/figures/fig31_ack_individuals_by_journal.{png,pdf}
        data/figures/fig32_ack_seminars_by_journal.{png,pdf}
        data/ack_by_journal.csv   (per-journal n, mean/median for both metrics)

Companion to 18_ack_charts.py, which plots the two metrics together (fig28).
This script instead gives each metric its own panel so the level differences
are read off a single axis, and adds a per-journal breakdown (12 journals).

Two metrics, per the project lead's specification (2026-06-22):
  individuals = named people thanked for COMMENTS only (research assistants,
                anonymous referees, editor excluded); seminars = distinct named
                venues (seminars + conferences + workshops + meetings combined).

Analysis sample = RCTs with classify_status in {ok, no_block}; a no_block paper
contributes 0 / 0 (a valid zero). no_pdf / pdf_error rows are excluded.

Usage:
  python 19_ack_by_journal.py
"""

import csv
import os
import statistics
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
OUT_CSV = os.path.join(PROJECT_DIR, "data", "ack_by_journal.csv")
LOG_TXT = os.path.join(PROJECT_DIR, "data", "19_ack_by_journal.log")

BLUE = "#3a7ca5"
DARK = "#16425b"
GOLD = "#b5862b"

# Journal tiers (journal_short codes), matching 11_funders_by_journal_tier.py
# and 18_ack_charts.py.
TIER_TOP5 = {"AER", "QJE", "JPE", "ECMA", "RES"}
TIER_OTHER_GI = {"AERI", "AEJ_Applied", "AEJ_EP", "RESTAT", "EJ", "JEEA"}
TIER_JDE = {"JDE"}
TIERS = [("top5", "Top 5"), ("other_gi", "Other general-interest"), ("jde", "JDE")]
TIER_COLOR = {"top5": DARK, "other_gi": BLUE, "jde": GOLD}

# Display labels for the journal_short codes (kept short for the axis).
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


def bar_by_tier(vals_by_tier, title, ylabel, color, name):
    """Vertical bars: mean of one metric per paper, by tier."""
    labels = [lab for key, lab in TIERS]
    means = [statistics.mean(vals_by_tier[key]) if vals_by_tier[key] else 0
             for key, _ in TIERS]
    meds = [statistics.median(vals_by_tier[key]) if vals_by_tier[key] else 0
            for key, _ in TIERS]
    ns = [len(vals_by_tier[key]) for key, _ in TIERS]

    x = range(len(labels))
    fig, ax = plt.subplots(figsize=(7.5, 4.5))
    bars = ax.bar(list(x), means, 0.6, color=color, edgecolor="black",
                  linewidth=0.5)
    # median markers, so the right-skew (esp. seminars) is visible
    ax.scatter(list(x), meds, marker="D", color=DARK, zorder=3, s=42,
               label="median")
    ax.set_xticks(list(x))
    ax.set_xticklabels([f"{lab}\n(n={n})" for lab, n in zip(labels, ns)])
    ax.set_ylabel(ylabel)
    ax.set_title(title)
    ax.grid(axis="y", alpha=0.3)
    ax.set_axisbelow(True)
    top = max(means or [1])
    ax.set_ylim(0, top * 1.18)  # headroom so value labels clear the title
    for b, m in zip(bars, means):
        ax.text(b.get_x() + b.get_width() / 2, b.get_height() + 0.02 * top,
                f"{m:.1f}", ha="center", va="bottom", fontsize=10)
    ax.legend(loc="upper right")
    save(fig, name)


def barh_by_journal(stats, metric_mean, metric_med, title, xlabel, name):
    """Horizontal bars: mean of one metric per paper, one bar per journal,
    sorted descending, colored by tier, with median markers and n labels."""
    js = sorted(stats, key=lambda j: stats[j][metric_mean])  # asc -> top is largest
    labels = [f"{JOURNAL_LABEL.get(j, j)}  (n={stats[j]['n']})" for j in js]
    means = [stats[j][metric_mean] for j in js]
    meds = [stats[j][metric_med] for j in js]
    colors = [TIER_COLOR[tier_of(j)] for j in js]

    y = range(len(js))
    fig, ax = plt.subplots(figsize=(8, 6))
    ax.barh(list(y), means, color=colors, edgecolor="black", linewidth=0.5)
    ax.scatter(meds, list(y), marker="D", color="black", zorder=3, s=34)
    ax.set_yticks(list(y))
    ax.set_yticklabels(labels)
    ax.set_xlabel(xlabel)
    ax.set_title(title)
    ax.grid(axis="x", alpha=0.3)
    ax.set_axisbelow(True)
    span = max(means) if means else 1
    for yi, m in zip(y, means):
        ax.text(m + 0.01 * span, yi, f"{m:.1f}", va="center", ha="left", fontsize=9)
    handles = [Patch(facecolor=TIER_COLOR[k], edgecolor="black", label=lab)
               for k, lab in TIERS]
    handles.append(plt.Line2D([0], [0], marker="D", color="w",
                              markerfacecolor="black", markersize=7, label="median"))
    ax.legend(handles=handles, loc="lower right", fontsize=9)
    ax.set_xlim(0, span * 1.12)
    save(fig, name)


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
            r["_ind"] = int(r["n_individuals_comments"])
            r["_sem"] = int(r["n_seminars"])
        except (ValueError, TypeError):
            continue
        rows.append(r)
    log(f"Analysis sample: {len(rows)} RCTs (classify_status ok/no_block)")

    # By-tier vectors.
    ind_tier, sem_tier = defaultdict(list), defaultdict(list)
    # By-journal vectors.
    ind_j, sem_j = defaultdict(list), defaultdict(list)
    for r in rows:
        t = tier_of(r["journal_short"])
        ind_tier[t].append(r["_ind"]); sem_tier[t].append(r["_sem"])
        ind_j[r["journal_short"]].append(r["_ind"])
        sem_j[r["journal_short"]].append(r["_sem"])

    # ---- by tier, each metric its own figure ----
    bar_by_tier(ind_tier,
                "Individuals thanked for comments, by journal tier (2021-2025)",
                "Mean individuals thanked per paper", BLUE,
                "fig29_ack_individuals_by_tier")
    bar_by_tier(sem_tier,
                "Seminars/venues acknowledged, by journal tier (2021-2025)",
                "Mean seminars/venues per paper", GOLD,
                "fig30_ack_seminars_by_tier")

    # ---- by individual journal, each metric its own figure ----
    stats = {}
    for j in ind_j:
        stats[j] = {
            "n": len(ind_j[j]),
            "ind_mean": statistics.mean(ind_j[j]),
            "ind_med": statistics.median(ind_j[j]),
            "sem_mean": statistics.mean(sem_j[j]),
            "sem_med": statistics.median(sem_j[j]),
        }
    barh_by_journal(stats, "ind_mean", "ind_med",
                    "Individuals thanked for comments, by journal (2021-2025)",
                    "Mean individuals thanked per paper",
                    "fig31_ack_individuals_by_journal")
    barh_by_journal(stats, "sem_mean", "sem_med",
                    "Seminars/venues acknowledged, by journal (2021-2025)",
                    "Mean seminars/venues per paper",
                    "fig32_ack_seminars_by_journal")

    # ---- per-journal CSV ----
    fields = ["journal_short", "journal_label", "tier", "n",
              "ind_mean", "ind_median", "sem_mean", "sem_median"]
    with open(OUT_CSV, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for j in sorted(stats, key=lambda j: -stats[j]["ind_mean"]):
            s = stats[j]
            w.writerow({
                "journal_short": j, "journal_label": JOURNAL_LABEL.get(j, j),
                "tier": dict(TIERS)[tier_of(j)], "n": s["n"],
                "ind_mean": round(s["ind_mean"], 2), "ind_median": s["ind_med"],
                "sem_mean": round(s["sem_mean"], 2), "sem_median": s["sem_med"],
            })
    log(f"  wrote {os.path.basename(OUT_CSV)} ({len(stats)} journals)")
    log("Done.")


if __name__ == "__main__":
    main()
