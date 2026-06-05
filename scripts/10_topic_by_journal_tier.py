"""
Generate a 100%-stacked bar chart of the topical mix across three journal tiers:
  - Top 5            : AER, QJE, JPE, ECMA, RES (Review of Economic Studies)
  - Other general-interest : AERI, AEJ_Applied, AEJ_EP, RESTAT, EJ, JEEA
  - JDE              : Journal of Development Economics

Each bar sums to 100%; segments show each topic's within-tier share of papers
(primary topic only). Sample is ALL topic-classified development papers.

Reads:  data/topic_classified.csv (Stage 3c output, topic-classify-v2)
Writes: data/figures/fig19_topic_by_journal_tier.png + .pdf

Usage:
  python scripts/10_topic_by_journal_tier.py
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
LOG_TXT = os.path.join(PROJECT_DIR, "data", "10_topic_by_journal_tier.log")

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

# Journal-tier definitions (journal_short codes).
TIER_TOP5 = {"AER", "QJE", "JPE", "ECMA", "RES"}
TIER_OTHER_GI = {"AERI", "AEJ_Applied", "AEJ_EP", "RESTAT", "EJ", "JEEA"}
TIER_JDE = {"JDE"}

# Display order of the three bars (top to bottom in the figure).
TIERS = [
    ("top5",  "Top 5"),
    ("other", "Other general-interest"),
    ("jde",   "JDE"),
]


def tier_of(journal_short):
    if journal_short in TIER_TOP5:
        return "top5"
    if journal_short in TIER_OTHER_GI:
        return "other"
    if journal_short in TIER_JDE:
        return "jde"
    return None


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
    log("Topic-by-journal-tier chart start")

    if not os.path.exists(IN_CSV):
        raise SystemExit(f"Missing input: {IN_CSV}. Run Stage 3c first.")
    os.makedirs(FIG_DIR, exist_ok=True)

    with open(IN_CSV, encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    log(f"Loaded {len(rows):,} rows from {IN_CSV}")

    # counts[tier][topic] and per-tier totals
    counts = {t: Counter() for t, _ in TIERS}
    tier_totals = Counter()
    overall = Counter()
    skipped_no_topic = 0
    skipped_no_tier = 0

    for r in rows:
        topic = (r.get("primary_topic") or "").strip().lower()
        if not topic or topic == "invalid":
            skipped_no_topic += 1
            continue
        tier = tier_of((r.get("journal_short") or "").strip())
        if tier is None:
            skipped_no_tier += 1
            continue
        counts[tier][topic] += 1
        tier_totals[tier] += 1
        overall[topic] += 1

    log(f"Classified papers used: {sum(tier_totals.values()):,} "
        f"(skipped {skipped_no_topic} w/o topic, {skipped_no_tier} w/o tier)")
    for t, lbl in TIERS:
        log(f"  {lbl}: n={tier_totals[t]:,}")

    # Topic stacking order: most common overall first.
    topic_order = [t for t, _ in overall.most_common()]
    log(f"Topics ({len(topic_order)}): " + ", ".join(topic_order))

    # Color per topic (16 distinct colors from tab20).
    cmap = plt.get_cmap("tab20")
    colors = {t: cmap(i % 20) for i, t in enumerate(topic_order)}

    # --- Build the 100%-stacked horizontal bars -------------------------------
    fig, ax = plt.subplots(figsize=(11, 5))
    y_pos = list(range(len(TIERS)))[::-1]  # first tier on top
    bar_h = 0.62

    for ti, (t, _lbl) in enumerate(TIERS):
        total = tier_totals[t]
        left = 0.0
        for topic in topic_order:
            n = counts[t][topic]
            if n == 0:
                continue
            share = 100.0 * n / total
            ax.barh(y_pos[ti], share, left=left, height=bar_h,
                    color=colors[topic], edgecolor="white", linewidth=0.5)
            # Label segments wide enough to hold text.
            if share >= 5.0:
                ax.text(left + share / 2, y_pos[ti], f"{share:.0f}%",
                        va="center", ha="center", fontsize=8,
                        color="black")
            left += share

    ax.set_yticks(y_pos)
    ax.set_yticklabels([f"{lbl}\n(n={tier_totals[t]:,})" for t, lbl in TIERS])
    ax.set_xlim(0, 100)
    ax.set_xlabel("Share of development articles within journal tier (%)")
    ax.set_title("Topical mix of development articles by journal tier, 2021–2025")
    ax.set_axisbelow(True)
    ax.grid(axis="x", alpha=0.3)

    # Legend in overall-frequency order, placed to the right.
    handles = [plt.Rectangle((0, 0), 1, 1, color=colors[t]) for t in topic_order]
    labels = [TOPIC_LABELS.get(t, t) for t in topic_order]
    ax.legend(handles, labels, loc="center left", bbox_to_anchor=(1.01, 0.5),
              fontsize=8, frameon=False, title="Primary topic")

    save(fig, "fig19_topic_by_journal_tier")

    # --- Companion CSV of the underlying shares -------------------------------
    out_csv = os.path.join(PROJECT_DIR, "data", "topic_by_journal_tier.csv")
    with open(out_csv, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["topic", "label"] +
                   [f"{lbl}_n" for _, lbl in TIERS] +
                   [f"{lbl}_pct" for _, lbl in TIERS])
        for topic in topic_order:
            row = [topic, TOPIC_LABELS.get(topic, topic)]
            ns = [counts[t][topic] for t, _ in TIERS]
            pcts = [100.0 * counts[t][topic] / tier_totals[t] if tier_totals[t] else 0
                    for t, _ in TIERS]
            w.writerow(row + ns + [f"{p:.1f}" for p in pcts])
        w.writerow(["TOTAL", "Total"] +
                   [tier_totals[t] for t, _ in TIERS] +
                   ["100.0" for _ in TIERS])
    log(f"  wrote {os.path.basename(out_csv)}")

    log("Done.")


if __name__ == "__main__":
    main()
