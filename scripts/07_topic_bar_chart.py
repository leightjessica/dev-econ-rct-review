"""
Generate horizontal bar charts of development-economics papers by topic.

Reads:  data/topic_classified.csv (Stage 3c output)
Writes: data/figures/fig14_topic_distribution.png + .pdf      (primary topic only)
        data/figures/fig15_topic_distribution_weighted.png + .pdf  (primary + secondary, weighted)
        data/figures/fig16_topic_by_rct_weighted.png + .pdf        (weighted, split RCT vs non-RCT)

Usage:
  python scripts/07_topic_bar_chart.py
"""

import csv
import os
from collections import Counter, defaultdict
from datetime import datetime, timezone

try:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
except ImportError:
    raise SystemExit("matplotlib is required. Run:\n  pip install matplotlib")

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_DIR = os.path.abspath(os.path.join(SCRIPT_DIR, ".."))
IN_CSV = os.path.join(PROJECT_DIR, "data", "topic_classified.csv")
FIG_DIR = os.path.join(PROJECT_DIR, "data", "figures")
LOG_TXT = os.path.join(PROJECT_DIR, "data", "07_topic_bar_chart.log")

TOPIC_LABELS = {
    "agriculture":       "Agriculture",
    "health":            "Health",
    "education":         "Education",
    "labor":             "Labor",
    "firms":             "Firms",
    "finance":           "Finance",
    "social_protection": "Social protection",
    "gender":            "Gender",
    "political_economy": "Political economy",
    "conflict_crime":    "Conflict & crime",
    "environment":       "Environment",
    "trade_macro":       "Trade & macro",
    "migration":         "Migration",
    "infrastructure":    "Infrastructure",
    "behavioral_info":   "Behavioral & information",
    "other":             "Other",
}


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


def main():
    open(LOG_TXT, "w").close()
    log("Topic bar chart start")

    if not os.path.exists(IN_CSV):
        raise SystemExit(f"Missing input: {IN_CSV}. Run Stage 3c first.")
    os.makedirs(FIG_DIR, exist_ok=True)

    with open(IN_CSV, encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    log(f"Loaded {len(rows):,} rows from {IN_CSV}")

    counts = Counter()
    for r in rows:
        topic = (r.get("primary_topic") or "").strip().lower()
        if topic and topic != "INVALID":
            counts[topic] += 1

    total = sum(counts.values())
    log(f"Classified rows with valid topic: {total:,}")

    ordered = sorted(counts.keys(), key=lambda k: counts[k])
    labels = [TOPIC_LABELS.get(t, t) for t in ordered]
    values = [counts[t] for t in ordered]

    fig, ax = plt.subplots(figsize=(9, 7))
    bars = ax.barh(range(len(labels)), values, color="#3a7ca5",
                   edgecolor="black", linewidth=0.5)
    ax.set_yticks(range(len(labels)))
    ax.set_yticklabels(labels)
    ax.set_xlabel("Number of development articles")
    ax.set_title(f"Development articles by topic, 2021–2025 (n={total:,})")
    ax.set_xlim(0, max(values) * 1.2)
    ax.grid(axis="x", alpha=0.3)
    ax.set_axisbelow(True)

    for b, v in zip(bars, values):
        share = 100 * v / total if total else 0
        ax.text(b.get_width() + 1, b.get_y() + b.get_height() / 2,
                f"{v} ({share:.1f}%)",
                va="center", ha="left", fontsize=9)

    save(fig, "fig14_topic_distribution")

    # --- Fig 15: weighted primary + secondary ---------------------------------
    weighted = defaultdict(float)
    n_papers = 0
    for r in rows:
        prim = (r.get("primary_topic") or "").strip().lower()
        sec = (r.get("secondary_topic") or "").strip().lower()
        if not prim or prim == "INVALID":
            continue
        n_papers += 1
        if sec and sec != prim:
            weighted[prim] += 0.5
            weighted[sec] += 0.5
        else:
            weighted[prim] += 1.0

    log(f"Weighted chart: {n_papers:,} papers, {sum(weighted.values()):.0f} total weight")

    w_ordered = sorted(weighted.keys(), key=lambda k: weighted[k])
    w_labels = [TOPIC_LABELS.get(t, t) for t in w_ordered]
    w_values = [weighted[t] for t in w_ordered]
    w_total = sum(w_values)

    fig, ax = plt.subplots(figsize=(9, 7))
    bars = ax.barh(range(len(w_labels)), w_values, color="#3a7ca5",
                   edgecolor="black", linewidth=0.5)
    ax.set_yticks(range(len(w_labels)))
    ax.set_yticklabels(w_labels)
    ax.set_xlabel("Weighted article count (primary = 0.5, secondary = 0.5)")
    ax.set_title(f"Development articles by topic (weighted), 2021–2025 (n={n_papers:,})")
    ax.set_xlim(0, max(w_values) * 1.2)
    ax.grid(axis="x", alpha=0.3)
    ax.set_axisbelow(True)

    for b, v in zip(bars, w_values):
        share = 100 * v / w_total if w_total else 0
        ax.text(b.get_width() + 1, b.get_y() + b.get_height() / 2,
                f"{v:.1f} ({share:.1f}%)",
                va="center", ha="left", fontsize=9)

    save(fig, "fig15_topic_distribution_weighted")

    # --- Fig 16: weighted primary + secondary, split by RCT status ------------
    w_rct = defaultdict(float)
    w_nonrct = defaultdict(float)
    n_rct_papers = 0
    n_nonrct_papers = 0
    for r in rows:
        prim = (r.get("primary_topic") or "").strip().lower()
        sec = (r.get("secondary_topic") or "").strip().lower()
        if not prim or prim == "INVALID":
            continue
        is_rct = (r.get("rct_classification") or "").strip().lower() == "yes"
        bucket = w_rct if is_rct else w_nonrct
        if is_rct:
            n_rct_papers += 1
        else:
            n_nonrct_papers += 1
        if sec and sec != prim:
            bucket[prim] += 0.5
            bucket[sec] += 0.5
        else:
            bucket[prim] += 1.0

    all_topics = sorted(set(list(w_rct.keys()) + list(w_nonrct.keys())),
                        key=lambda k: w_rct[k] + w_nonrct[k])
    rct_labels = [TOPIC_LABELS.get(t, t) for t in all_topics]
    rct_vals = [w_rct[t] for t in all_topics]
    nonrct_vals = [w_nonrct[t] for t in all_topics]

    log(f"RCT cross-tab: {n_rct_papers:,} RCT papers, {n_nonrct_papers:,} non-RCT papers")

    fig, ax = plt.subplots(figsize=(10, 7))
    y_pos = range(len(rct_labels))
    ax.barh(y_pos, nonrct_vals, color="#bcc6cc", edgecolor="black",
            linewidth=0.5, label=f"Non-RCT (n={n_nonrct_papers:,})")
    ax.barh(y_pos, rct_vals, left=nonrct_vals, color="#3a7ca5",
            edgecolor="black", linewidth=0.5, label=f"RCT (n={n_rct_papers:,})")
    ax.set_yticks(list(y_pos))
    ax.set_yticklabels(rct_labels)
    ax.set_xlabel("Weighted article count (primary = 0.5, secondary = 0.5)")
    ax.set_title(f"Development articles by topic and RCT status (weighted), 2021–2025")
    ax.set_xlim(0, max(r + n for r, n in zip(rct_vals, nonrct_vals)) * 1.25)
    ax.grid(axis="x", alpha=0.3)
    ax.set_axisbelow(True)
    ax.legend(loc="lower right")

    for i, (rv, nv) in enumerate(zip(rct_vals, nonrct_vals)):
        total_v = rv + nv
        rct_share = 100 * rv / total_v if total_v else 0
        ax.text(total_v + 1, i,
                f"{total_v:.1f}  ({rv:.1f} RCT, {rct_share:.0f}%)",
                va="center", ha="left", fontsize=8)

    save(fig, "fig16_topic_by_rct_weighted")
    log("Topic bar charts complete.")


if __name__ == "__main__":
    main()
