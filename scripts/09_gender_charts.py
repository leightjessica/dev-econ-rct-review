"""
Stage 9: Author-gender composition charts.

Reads:
  data/author_gender_namsor.csv  (preferred; Stage 8b, uses gender_coded_final)
  data/author_gender.csv         (fallback; Stage 8, uses gender_coded)
  data/topic_classified.csv      (Stage 3c; primary_topic, joined on openalex_id)

Writes:
  data/figures/fig17_gender_share_overall.png + .pdf
  data/figures/fig18_gender_share_by_topic.png + .pdf
  data/09_gender_charts.log

Unit of analysis: author-appearances (each authorship counts once; a prolific
author is counted once per paper). The "by topic" panel groups author-
appearances by the primary topic of their paper; papers with no assigned topic
are excluded from that panel only. Shares are female / male / undetermined,
using the final coding (NamSor-augmented when Stage 8b has been run; otherwise
the gender_guesser + country-prior coding from Stage 8).

Usage:
  python scripts/09_gender_charts.py
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

SRC_NAMSOR = os.path.join(PROJECT_DIR, "data", "author_gender_namsor.csv")
SRC_BASE = os.path.join(PROJECT_DIR, "data", "author_gender.csv")
TOPICS = os.path.join(PROJECT_DIR, "data", "topic_classified.csv")
FIG_DIR = os.path.join(PROJECT_DIR, "data", "figures")
LOG_TXT = os.path.join(PROJECT_DIR, "data", "09_gender_charts.log")

# Three-category palette: distinct from, but harmonized with, the house blue.
COLORS = {
    "female": "#e07a5f",        # terracotta
    "male": "#3a7ca5",          # house blue
    "undetermined": "#bcc6cc",  # house grey
}
CATS = ["female", "male", "undetermined"]
CAT_LABELS = {"female": "Female", "male": "Male", "undetermined": "Undetermined"}

# Shared with Stage 7 for consistent topic display names.
TOPIC_LABELS = {
    "agriculture": "Agriculture", "health": "Health", "education": "Education",
    "labor": "Labor", "firms": "Firms", "finance": "Finance",
    "social_protection": "Social protection", "gender": "Gender",
    "political_economy": "Political economy", "conflict_crime": "Conflict & crime",
    "environment": "Environment", "trade_macro": "Trade & macro",
    "migration": "Migration", "infrastructure": "Infrastructure",
    "behavioral_info": "Behavioral & information", "other": "Other",
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


def load_authors():
    """Return (rows, gender_field, source_note). Prefer the NamSor coding."""
    if os.path.exists(SRC_NAMSOR):
        with open(SRC_NAMSOR, encoding="utf-8") as f:
            rows = list(csv.DictReader(f))
        return rows, "gender_coded_final", "gender_guesser + country prior + NamSor (p>=0.85)"
    if os.path.exists(SRC_BASE):
        with open(SRC_BASE, encoding="utf-8") as f:
            rows = list(csv.DictReader(f))
        return rows, "gender_coded", "gender_guesser + country prior"
    raise SystemExit("No author-gender file found. Run Stage 8 (and optionally 8b) first.")


def coded(row, field):
    c = (row.get(field) or "").strip().lower()
    return c if c in CATS else "undetermined"


def add_segment_labels(ax, y, counts, total, min_pct=6.0):
    """Draw centered percentage labels inside each segment of one 100% bar."""
    left = 0.0
    for cat in CATS:
        pct = 100 * counts.get(cat, 0) / total if total else 0
        if pct >= min_pct:
            txtcol = "white" if cat in ("male",) else "black"
            ax.text(left + pct / 2, y, f"{pct:.0f}%", va="center", ha="center",
                    fontsize=8, color=txtcol)
        left += pct


def main():
    open(LOG_TXT, "w").close()
    log("Gender charts start")
    os.makedirs(FIG_DIR, exist_ok=True)

    rows, gfield, source_note = load_authors()
    log(f"Author rows: {len(rows):,}  (coding: {source_note})")

    # ---- overall ------------------------------------------------------------
    overall = Counter(coded(r, gfield) for r in rows)
    total = sum(overall.values())
    log("Overall author-appearances:")
    for c in CATS:
        log(f"  {CAT_LABELS[c]:13s} {overall[c]:>6,d}  ({100*overall[c]/total:5.1f}%)")

    fig, ax = plt.subplots(figsize=(9, 2.6))
    left = 0.0
    for cat in CATS:
        pct = 100 * overall[cat] / total if total else 0
        ax.barh(0, pct, left=left, color=COLORS[cat], edgecolor="black",
                linewidth=0.5, label=f"{CAT_LABELS[cat]} ({overall[cat]:,}; {pct:.1f}%)")
        left += pct
    add_segment_labels(ax, 0, overall, total)
    ax.set_xlim(0, 100)
    ax.set_ylim(-0.6, 0.6)
    ax.set_yticks([])
    ax.set_xlabel("Share of author-appearances (%)")
    ax.set_title(f"Author gender composition, development articles 2021–2025 (n={total:,})")
    ax.legend(loc="upper center", bbox_to_anchor=(0.5, -0.35), ncol=3, frameon=False)
    fig.text(0.5, -0.02, f"Coding: {source_note}. Unit: author-appearances.",
             ha="center", fontsize=7, color="#555555")
    save(fig, "fig17_gender_share_overall")

    # ---- by topic -----------------------------------------------------------
    topic_of = {}
    with open(TOPICS, encoding="utf-8") as f:
        for r in csv.DictReader(f):
            t = (r.get("primary_topic") or "").strip().lower()
            if t and t != "invalid":
                topic_of[r.get("openalex_id", "")] = t

    by_topic = defaultdict(Counter)
    n_no_topic = 0
    for r in rows:
        t = topic_of.get(r.get("openalex_id", ""))
        if not t:
            n_no_topic += 1
            continue
        by_topic[t][coded(r, gfield)] += 1
    log(f"By-topic: {len(by_topic)} topics; "
        f"{n_no_topic:,} author-appearances dropped (paper had no assigned topic)")

    # Order topics by female share (descending -> highest female share on top).
    def fshare(t):
        c = by_topic[t]
        tot = sum(c.values())
        return c["female"] / tot if tot else 0
    ordered = sorted(by_topic.keys(), key=fshare)  # ascending; barh puts last on top

    labels, y = [], list(range(len(ordered)))
    fig, ax = plt.subplots(figsize=(10, 8))
    for i, t in enumerate(ordered):
        c = by_topic[t]
        tot = sum(c.values())
        labels.append(f"{TOPIC_LABELS.get(t, t)} (n={tot:,})")
        left = 0.0
        for cat in CATS:
            pct = 100 * c[cat] / tot if tot else 0
            ax.barh(i, pct, left=left, color=COLORS[cat], edgecolor="black",
                    linewidth=0.5)
            left += pct
        add_segment_labels(ax, i, c, tot, min_pct=7.0)

    ax.set_yticks(y)
    ax.set_yticklabels(labels)
    ax.set_xlim(0, 100)
    ax.set_xlabel("Share of author-appearances (%)")
    ax.set_title("Author gender composition by topic, development articles 2021–2025")
    ax.grid(axis="x", alpha=0.3)
    ax.set_axisbelow(True)
    handles = [plt.Rectangle((0, 0), 1, 1, color=COLORS[c], ec="black", lw=0.5)
               for c in CATS]
    ax.legend(handles, [CAT_LABELS[c] for c in CATS],
              loc="upper center", bbox_to_anchor=(0.5, -0.07), ncol=3, frameon=False)
    fig.text(0.5, -0.04,
             f"Coding: {source_note}. Unit: author-appearances. "
             f"Topics ordered by female share. {n_no_topic:,} appearances without a topic excluded.",
             ha="center", fontsize=7, color="#555555")
    save(fig, "fig18_gender_share_by_topic")

    log("Gender charts complete.")


if __name__ == "__main__":
    main()
