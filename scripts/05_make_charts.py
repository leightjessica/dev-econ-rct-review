"""
Stage 5: Generate publication-quality figures from the final dataset.

Reads:  data/final_dataset.csv (Stage 4 output)
Writes: data/figures/*.png and data/figures/*.pdf

Five figures:
  fig1_rct_share_by_journal       Horizontal bar, RCT share per journal
  fig2_rct_share_by_year          Line, overall RCT share over 2021-2025
  fig3_rct_share_by_year_journal  Small multiples, RCT share by year per journal
  fig4_rct_subtype_distribution   Bar, distribution of RCT subtypes
  fig5_dev_papers_by_year_journal Stacked bar, dev papers per year split RCT/non-RCT

Dependencies: matplotlib only (added to requirements.txt). All other logic
uses the standard library.

Usage:
  pip install matplotlib
  python 05_make_charts.py
"""

import csv
import os
from collections import defaultdict, OrderedDict
from datetime import datetime, timezone

try:
    import matplotlib
    matplotlib.use("Agg")  # non-interactive backend
    import matplotlib.pyplot as plt
except ImportError:
    raise SystemExit("matplotlib is required. Run:\n  pip install matplotlib")

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_DIR = os.path.abspath(os.path.join(SCRIPT_DIR, ".."))
IN_CSV = os.path.join(PROJECT_DIR, "data", "final_dataset.csv")
FIG_DIR = os.path.join(PROJECT_DIR, "data", "figures")
LOG_TXT = os.path.join(PROJECT_DIR, "data", "05_make_charts.log")

# Display order (for journals) — top-tier first, JDE last so it does not
# dominate visually
JOURNAL_ORDER = ["AER", "AERI", "AEJ_Applied", "AEJ_EP", "ECMA", "QJE",
                 "JPE", "RES", "RESTAT", "EJ", "JEEA", "JDE"]
JOURNAL_LABELS = {
    "AER": "AER", "AERI": "AER:I", "AEJ_Applied": "AEJ:Applied",
    "AEJ_EP": "AEJ:EP", "ECMA": "ECMA", "QJE": "QJE", "JPE": "JPE",
    "RES": "ReStud", "RESTAT": "ReStat", "EJ": "EJ", "JEEA": "JEEA", "JDE": "JDE",
}

YEARS = ["2021", "2022", "2023", "2024", "2025"]


def log(msg):
    line = f"[{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%SZ')}] {msg}"
    print(line, flush=True)
    with open(LOG_TXT, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def save(fig, name):
    fig.tight_layout()
    png = os.path.join(FIG_DIR, f"{name}.png")
    pdf = os.path.join(FIG_DIR, f"{name}.pdf")
    fig.savefig(png, dpi=300, bbox_inches="tight")
    fig.savefig(pdf, bbox_inches="tight")
    plt.close(fig)
    log(f"  wrote {name}.png + {name}.pdf")


def is_rct(r):
    return (r.get("rct_classification") or "").lower() == "yes"


def is_classified(r):
    """We only count rows where the LLM actually classified the paper. Rows
    with empty rct_classification (i.e., no abstract available) are excluded
    from RCT-rate denominators to avoid biasing rates downward."""
    return bool((r.get("rct_classification") or "").strip())


def fig1_rct_share_by_journal(rows):
    """Horizontal bar chart: RCT share per journal, with counts annotated."""
    by_j = defaultdict(lambda: {"dev": 0, "rct": 0})
    for r in rows:
        if not is_classified(r):
            continue
        j = r["journal_short"]
        by_j[j]["dev"] += 1
        if is_rct(r):
            by_j[j]["rct"] += 1

    labels = [JOURNAL_LABELS[j] for j in JOURNAL_ORDER]
    shares = [100 * by_j[j]["rct"] / by_j[j]["dev"] if by_j[j]["dev"] else 0 for j in JOURNAL_ORDER]
    rcts = [by_j[j]["rct"] for j in JOURNAL_ORDER]
    devs = [by_j[j]["dev"] for j in JOURNAL_ORDER]

    fig, ax = plt.subplots(figsize=(8, 6))
    y_pos = list(range(len(labels)))
    bars = ax.barh(y_pos, shares, color="#3a7ca5", edgecolor="black", linewidth=0.5)
    ax.set_yticks(y_pos)
    ax.set_yticklabels(labels)
    ax.invert_yaxis()
    ax.set_xlabel("RCT share of development articles (%)")
    ax.set_title("RCT share by journal, 2021-2025")
    ax.set_xlim(0, max(shares) * 1.25)
    ax.grid(axis="x", alpha=0.3)
    ax.set_axisbelow(True)

    for i, (b, rct, dev, share) in enumerate(zip(bars, rcts, devs, shares)):
        ax.text(b.get_width() + 0.5, b.get_y() + b.get_height() / 2,
                f"{rct}/{dev} ({share:.1f}%)",
                va="center", ha="left", fontsize=9)

    save(fig, "fig1_rct_share_by_journal")


def fig2_rct_share_by_year(rows):
    """Line chart: overall RCT share by year, with counts annotated."""
    by_y = defaultdict(lambda: {"dev": 0, "rct": 0})
    for r in rows:
        if not is_classified(r):
            continue
        y = (r.get("publication_year") or "")[:4]
        if y not in YEARS:
            continue
        by_y[y]["dev"] += 1
        if is_rct(r):
            by_y[y]["rct"] += 1

    shares = [100 * by_y[y]["rct"] / by_y[y]["dev"] if by_y[y]["dev"] else 0 for y in YEARS]
    rcts = [by_y[y]["rct"] for y in YEARS]
    devs = [by_y[y]["dev"] for y in YEARS]

    fig, ax = plt.subplots(figsize=(8, 5))
    ax.plot(YEARS, shares, marker="o", color="#3a7ca5", linewidth=2, markersize=10)
    for x, s, rct, dev in zip(YEARS, shares, rcts, devs):
        ax.annotate(f"{rct}/{dev}\n({s:.1f}%)", xy=(x, s),
                    xytext=(0, 12), textcoords="offset points",
                    ha="center", fontsize=9)
    ax.set_xlabel("Publication year")
    ax.set_ylabel("RCT share of development articles (%)")
    ax.set_title("RCT share over time (all 12 journals pooled)")
    ax.set_ylim(0, max(shares) * 1.4)
    ax.grid(alpha=0.3)
    ax.set_axisbelow(True)

    save(fig, "fig2_rct_share_by_year")


def fig3_rct_share_by_year_journal(rows):
    """Small multiples: RCT share by year, one panel per journal."""
    by_jy = defaultdict(lambda: {"dev": 0, "rct": 0})
    for r in rows:
        if not is_classified(r):
            continue
        j = r["journal_short"]
        y = (r.get("publication_year") or "")[:4]
        if y not in YEARS:
            continue
        by_jy[(j, y)]["dev"] += 1
        if is_rct(r):
            by_jy[(j, y)]["rct"] += 1

    n = len(JOURNAL_ORDER)
    ncol = 4
    nrow = (n + ncol - 1) // ncol
    fig, axes = plt.subplots(nrow, ncol, figsize=(13, 8), sharex=True, sharey=True)
    axes = axes.flatten()

    for ax, j in zip(axes, JOURNAL_ORDER):
        shares = []
        for y in YEARS:
            d = by_jy.get((j, y), {"dev": 0, "rct": 0})
            shares.append(100 * d["rct"] / d["dev"] if d["dev"] else 0)
        ax.plot(YEARS, shares, marker="o", color="#3a7ca5", linewidth=1.5, markersize=5)
        ax.set_title(JOURNAL_LABELS[j], fontsize=11)
        ax.set_ylim(0, 100)
        ax.grid(alpha=0.3)
        ax.set_axisbelow(True)

    # Hide extra axes
    for ax in axes[n:]:
        ax.axis("off")

    fig.supxlabel("Publication year")
    fig.supylabel("RCT share (%)")
    fig.suptitle("RCT share by journal × year, 2021-2025", y=1.0)

    save(fig, "fig3_rct_share_by_year_journal")


def fig4_rct_subtype_distribution(rows):
    """Bar chart: distribution of RCT subtypes among the YES-classified rows."""
    counts = defaultdict(int)
    for r in rows:
        if is_rct(r):
            sub = (r.get("rct_subtype") or "").strip().lower()
            if not sub or sub == "n/a":
                sub = "(unspecified)"
            counts[sub] += 1
    # Order subtypes: most common first; place 'follow_up' next-to-last and
    # '(unspecified)' last for readability.
    order = sorted(counts.keys(), key=lambda k: (-counts[k], k))

    labels = order
    values = [counts[k] for k in order]
    total = sum(values)

    fig, ax = plt.subplots(figsize=(8, 5))
    bars = ax.bar(labels, values, color="#3a7ca5", edgecolor="black", linewidth=0.5)
    for b, v in zip(bars, values):
        share = 100 * v / total if total else 0
        ax.text(b.get_x() + b.get_width() / 2, b.get_height() + 1,
                f"{v}\n({share:.0f}%)",
                ha="center", va="bottom", fontsize=9)
    ax.set_ylabel("Count")
    ax.set_title(f"Distribution of RCT subtypes (n={total})")
    ax.set_ylim(0, max(values) * 1.25)
    ax.grid(axis="y", alpha=0.3)
    ax.set_axisbelow(True)
    plt.setp(ax.get_xticklabels(), rotation=20, ha="right")

    save(fig, "fig4_rct_subtype_distribution")


def fig5_dev_by_year_stacked(rows):
    """Stacked bar: dev papers per year, split RCT yes / no / unclassified-no-abstract."""
    cats = OrderedDict([("RCT", 0), ("not RCT", 1), ("no abstract", 2)])
    by_year = {y: [0, 0, 0] for y in YEARS}
    for r in rows:
        y = (r.get("publication_year") or "")[:4]
        if y not in YEARS:
            continue
        if not is_classified(r):
            by_year[y][cats["no abstract"]] += 1
        elif is_rct(r):
            by_year[y][cats["RCT"]] += 1
        else:
            by_year[y][cats["not RCT"]] += 1

    rct = [by_year[y][cats["RCT"]] for y in YEARS]
    notrct = [by_year[y][cats["not RCT"]] for y in YEARS]
    noabs = [by_year[y][cats["no abstract"]] for y in YEARS]

    fig, ax = plt.subplots(figsize=(8, 5))
    ax.bar(YEARS, rct, color="#3a7ca5", label="RCT", edgecolor="black", linewidth=0.5)
    ax.bar(YEARS, notrct, bottom=rct, color="#bcc6cc", label="not RCT", edgecolor="black", linewidth=0.5)
    bottom2 = [a + b for a, b in zip(rct, notrct)]
    ax.bar(YEARS, noabs, bottom=bottom2, color="#e07a5f", label="no abstract (unclassified)", edgecolor="black", linewidth=0.5)
    ax.set_xlabel("Publication year")
    ax.set_ylabel("Number of development articles")
    ax.set_title("Development articles per year, split by RCT status")
    ax.legend(loc="upper left")
    ax.grid(axis="y", alpha=0.3)
    ax.set_axisbelow(True)

    # Annotate totals on top of each bar
    for x, t in zip(YEARS, [a + b + c for a, b, c in zip(rct, notrct, noabs)]):
        ax.text(x, t + 4, f"{t}", ha="center", fontsize=9)

    save(fig, "fig5_dev_papers_by_year_stacked")


def main():
    open(LOG_TXT, "w").close()
    log("Stage 5 start")
    if not os.path.exists(IN_CSV):
        raise SystemExit(f"Missing input: {IN_CSV}. Run Stage 4 first.")
    os.makedirs(FIG_DIR, exist_ok=True)
    with open(IN_CSV, encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    log(f"Loaded {len(rows):,} rows from {IN_CSV}")

    fig1_rct_share_by_journal(rows)
    fig2_rct_share_by_year(rows)
    fig3_rct_share_by_year_journal(rows)
    fig4_rct_subtype_distribution(rows)
    fig5_dev_by_year_stacked(rows)

    log(f"All figures written to {FIG_DIR}")
    log("Stage 5 complete.")


if __name__ == "__main__":
    main()
