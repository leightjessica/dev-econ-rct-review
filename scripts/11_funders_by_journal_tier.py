"""
Generate 100%-stacked bar charts of the funder mix across three journal tiers:
  - Top 5            : AER, QJE, JPE, ECMA, RES (Review of Economic Studies)
  - Other general-interest : AERI, AEJ_Applied, AEJ_EP, RESTAT, EJ, JEEA
  - JDE              : Journal of Development Economics

This is the funder analog of fig19 (topic_by_journal_tier). Because a single RCT
can acknowledge several funders, each bar sums to 100% of FUNDER ACKNOWLEDGMENTS
within the tier (not papers): a segment's share = its mentions / the relevant
mention total in the tier.

Two variants are produced:
  fig21 (top 14 + "Other")  : denominator = ALL funder mentions in the tier; the
                              long tail (600+ distinct funders) collapses into a
                              single "Other" segment.
  fig22 (top 22, no "Other"): denominator = top-22 mentions only, so the named
                              segments are renormalized to sum to 100% within
                              each tier (the tail is dropped, not shown).

Reads:  data/funders_all.csv (Stage 6g output; per-RCT canonical funders)
Writes: data/figures/fig21_funders_by_journal_tier.png + .pdf
        data/figures/fig22_funders_by_journal_tier_top22.png + .pdf
        data/funders_by_journal_tier.csv         (fig21 shares)
        data/funders_by_journal_tier_top22.csv   (fig22 shares)

Usage:
  py scripts/11_funders_by_journal_tier.py
"""

import csv
import os
from collections import Counter
from datetime import datetime, timezone

try:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
except ImportError:
    raise SystemExit("matplotlib is required. Run:\n  pip install matplotlib")

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_DIR = os.path.abspath(os.path.join(SCRIPT_DIR, ".."))
IN_CSV = os.path.join(PROJECT_DIR, "data", "funders_all.csv")
FIG_DIR = os.path.join(PROJECT_DIR, "data", "figures")
LOG_TXT = os.path.join(PROJECT_DIR, "data", "11_funders_by_journal_tier.log")

# Short legend labels for long canonical funder names (keyed by canonical).
SHORT_LABELS = {
    "Abdul Latif Jameel Poverty Action Lab (J-PAL)": "J-PAL",
    "World Bank Group": "World Bank Group",
    "FCDO": "FCDO (UK)",
    "United States Agency for International Development (USAID)": "USAID",
    "International Growth Centre": "IGC",
    "Bill & Melinda Gates Foundation": "Gates Foundation",
    "National Science Foundation": "NSF",
    "International Initiative for Impact Evaluation (3ie)": "3ie",
    "Weiss Family Fund": "Weiss Family Fund",
    "Inter-American Development Bank": "IDB",
    "CGIAR": "CGIAR",
    "Economic and Social Research Council": "ESRC",
    "European Research Council": "ERC",
    "Private Enterprise Development in Low-Income Countries (PEDL)": "PEDL",
    "Deutsche Forschungsgemeinschaft (DFG)": "DFG",
    "Agricultural Technology Adoption Initiative": "ATAI",
    "National Institutes of Health": "NIH",
    "Social Sciences and Humanities Research Council of Canada": "SSHRC",
    "University of Michigan": "Univ. of Michigan",
    "Fundação para a Ciência e a Tecnologia": "FCT (Portugal)",
    "National Natural Science Foundation of China": "NSFC (China)",
    "Spanish Ministry of Economy and Competitiveness": "Spanish Min. of Economy",
}
OTHER = "Other"

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
    fig.savefig(os.path.join(FIG_DIR, f"{name}.png"), dpi=300, bbox_inches="tight")
    fig.savefig(os.path.join(FIG_DIR, f"{name}.pdf"), bbox_inches="tight")
    plt.close(fig)
    log(f"  wrote {name}.png + {name}.pdf")


def apply_aggregation(mentions, overall, aggregate):
    """Collapse groups of canonical funders into one merged label.

    aggregate maps a merged display name -> set of source canonical names.
    Returns fresh (mentions, overall) copies so the caller's shared counters
    are left untouched.
    """
    src_to_merged = {}
    for merged, srcs in (aggregate or {}).items():
        for s in srcs:
            src_to_merged[s] = merged
    new_overall = Counter()
    for f, n in overall.items():
        new_overall[src_to_merged.get(f, f)] += n
    new_mentions = {t: Counter() for t in mentions}
    for t, counter in mentions.items():
        for f, n in counter.items():
            new_mentions[t][src_to_merged.get(f, f)] += n
    return new_mentions, new_overall


def build_chart(mentions, tier_mentions, overall, top_n, restrict, fig_name,
                out_csv, title, aggregate=None):
    """Render one tier chart.

    restrict=False : show top_n named funders + an "Other" tail; shares are over
                     ALL mentions in the tier (each bar = 100% of acknowledgments).
    restrict=True  : show only the top_n funders, with shares renormalized over
                     the top_n mentions in the tier (each bar = 100% of the
                     top_n; the tail is dropped).
    aggregate      : optional {merged label -> set of canonical funders} applied
                     before ranking, so the merged entity competes for a slot.
    """
    if aggregate:
        mentions, overall = apply_aggregation(mentions, overall, aggregate)
    ranked = sorted(overall.items(), key=lambda kv: (-kv[1], kv[0].lower()))
    top_funders = [f for f, _ in ranked[:top_n]]
    top_set = set(top_funders)

    if restrict:
        segment_order = top_funders
        # Per-tier denominator = sum of top_n mentions in that tier.
        denom = {t: sum(mentions[t][f] for f in top_funders) for t, _ in TIERS}
    else:
        segment_order = top_funders + [OTHER]
        denom = dict(tier_mentions)
        for t, _ in TIERS:
            mentions[t][OTHER] = sum(n for f, n in mentions[t].items()
                                     if f not in top_set and f != OTHER)

    # Color per segment: tab20 for named funders, neutral grey for Other.
    cmap = plt.get_cmap("tab20")
    colors = {f: cmap(i % 20) for i, f in enumerate(top_funders)}
    colors[OTHER] = "#bdbdbd"

    fig, ax = plt.subplots(figsize=(11, 5))
    y_pos = list(range(len(TIERS)))[::-1]  # first tier on top
    bar_h = 0.62

    for ti, (t, _lbl) in enumerate(TIERS):
        total = denom[t]
        left = 0.0
        for seg in segment_order:
            n = mentions[t][seg]
            if n == 0 or total == 0:
                continue
            share = 100.0 * n / total
            ax.barh(y_pos[ti], share, left=left, height=bar_h,
                    color=colors[seg], edgecolor="white", linewidth=0.5)
            if share >= 5.0:
                ax.text(left + share / 2, y_pos[ti], f"{share:.0f}%",
                        va="center", ha="center", fontsize=8, color="black")
            left += share

    ax.set_yticks(y_pos)
    ax.set_yticklabels([f"{lbl}\n({denom[t]:,} mentions)" for t, lbl in TIERS])
    ax.set_xlim(0, 100)
    ax.set_xlabel("Share of funder acknowledgments within journal tier (%)")
    ax.set_title(title)
    ax.set_axisbelow(True)
    ax.grid(axis="x", alpha=0.3)

    handles = [plt.Rectangle((0, 0), 1, 1, color=colors[s]) for s in segment_order]
    labels = [SHORT_LABELS.get(s, s) for s in segment_order]
    leg_title = f"Funder (top {top_n})"
    ax.legend(handles, labels, loc="center left", bbox_to_anchor=(1.01, 0.5),
              fontsize=8, frameon=False, title=leg_title)

    save(fig, fig_name)

    # Companion CSV of the underlying shares.
    with open(out_csv, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["funder", "label"] +
                   [f"{lbl}_n" for _, lbl in TIERS] +
                   [f"{lbl}_pct" for _, lbl in TIERS])
        for seg in segment_order:
            ns = [mentions[t][seg] for t, _ in TIERS]
            pcts = [100.0 * mentions[t][seg] / denom[t] if denom[t] else 0
                    for t, _ in TIERS]
            w.writerow([seg, SHORT_LABELS.get(seg, seg)] + ns +
                       [f"{p:.1f}" for p in pcts])
        w.writerow(["TOTAL", "Total (denominator)"] +
                   [denom[t] for t, _ in TIERS] + ["100.0" for _ in TIERS])
    log(f"  wrote {os.path.basename(out_csv)}")


def main():
    open(LOG_TXT, "w").close()
    log("Funder-by-journal-tier charts start")

    if not os.path.exists(IN_CSV):
        raise SystemExit(f"Missing input: {IN_CSV}. Run Stage 6g first.")
    os.makedirs(FIG_DIR, exist_ok=True)

    with open(IN_CSV, encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    log(f"Loaded {len(rows):,} rows from {IN_CSV}")

    mentions = {t: Counter() for t, _ in TIERS}
    tier_mentions = Counter()
    tier_papers = Counter()      # papers with >=1 funder (context only)
    overall = Counter()
    skipped_no_tier = 0

    for r in rows:
        tier = tier_of((r.get("journal_short") or "").strip())
        if tier is None:
            skipped_no_tier += 1
            continue
        funders = [x for x in (r.get("funders") or "").split(" | ") if x]
        if funders:
            tier_papers[tier] += 1
        for fnd in funders:
            mentions[tier][fnd] += 1
            tier_mentions[tier] += 1
            overall[fnd] += 1

    log(f"Funder mentions used: {sum(tier_mentions.values()):,} "
        f"(skipped {skipped_no_tier} rows w/o tier; {len(overall)} distinct funders)")
    for t, lbl in TIERS:
        log(f"  {lbl}: {tier_papers[t]:,} papers w/ funder, "
            f"{tier_mentions[t]:,} mentions")

    # Variant 1: top 14 + Other, shares over ALL mentions.
    log("Building fig21 (top 14 + Other; shares over all mentions)")
    build_chart(
        mentions, tier_mentions, overall, top_n=14, restrict=False,
        fig_name="fig21_funders_by_journal_tier",
        out_csv=os.path.join(PROJECT_DIR, "data", "funders_by_journal_tier.csv"),
        title="Funder mix of development RCTs by journal tier, 2021–2025",
    )

    # Variant 2: top 22 only, shares renormalized over the top 22 (no Other).
    log("Building fig22 (top 22 only; shares renormalized to the top 22)")
    build_chart(
        mentions, tier_mentions, overall, top_n=22, restrict=True,
        fig_name="fig22_funders_by_journal_tier_top22",
        out_csv=os.path.join(PROJECT_DIR, "data", "funders_by_journal_tier_top22.csv"),
        title="Funder mix of development RCTs by journal tier, 2021–2025\n"
              "(restricted to the 22 most common funders)",
    )

    # Variant 3: top 22, with FCDO + IGC + PEDL collapsed into one UK-aid group.
    # IGC is an FCDO programme and PEDL is the CEPR-FCDO initiative, so this
    # counts the UK's footprint as a single funder.
    fcdo_group = {
        "FCDO/IGC/PEDL": {
            "FCDO",
            "International Growth Centre",
            "Private Enterprise Development in Low-Income Countries (PEDL)",
        }
    }
    log("Building fig23 (top 22, FCDO/IGC/PEDL aggregated; renormalized)")
    build_chart(
        mentions, tier_mentions, overall, top_n=22, restrict=True,
        fig_name="fig23_funders_by_journal_tier_top22_fcdo_agg",
        out_csv=os.path.join(PROJECT_DIR, "data",
                             "funders_by_journal_tier_top22_fcdo_agg.csv"),
        title="Funder mix of development RCTs by journal tier, 2021–2025\n"
              "(top 22; FCDO, IGC and PEDL counted as one funder)",
        aggregate=fcdo_group,
    )

    log("Done.")


if __name__ == "__main__":
    main()
