#!/usr/bin/env python3
"""Stage 23 - descriptive figures, summary, and validation sample for the
subnational site / World Bank poverty match (PROJECT_MEMO Sections 12.8-12.10).

Reads:  data/site_wb_merged.csv (Stage 22), data/site_extracted.csv and
        data/site_extracted_multicountry.csv (for the validation sample)
Writes: data/figures/fig39_site_coverage.{png,pdf}
        data/figures/fig40_site_poverty_rank.{png,pdf}
        data/figures/fig41_site_rank_by_group.{png,pdf}
        data/figures/fig42_site_vs_regional_mean.{png,pdf}
        data/site_wb_summary.csv               (overall / by-tier / by-level rows)
        data/_site_validation_sample.csv       (fixed-seed human-audit draw)

fig39  Horizontal bars: disposition of all site rows (one per single-country RCT,
       one per country arm of a multi-country RCT) - placed in one unit, placed in
       several, nationwide, unspecified, outside WB coverage - the denominator.
fig40  Histogram: each placed site's percentile rank in its own survey's regional
       distribution of the $3.00 (2021 PPP) headcount (0 = least poor region,
       1 = poorest), with the flat line expected if sites were drawn at random
       across regions.
fig41  Point estimates with 95% intervals: mean percentile rank by journal tier
       and by World Bank level of geography (admin-1 / admin-2 / grouped).
fig42  Scatter: site $3.00 headcount against the unweighted mean of its survey's
       regions, with the 45-degree line.

The rank is unweighted (the SPID release carries no population weights), so the
0.5 benchmark is "a region drawn at random", not "a person drawn at random".
Deterministic; matplotlib + standard library only.
"""

import csv
import math
import os
import random
import statistics
from collections import Counter
from datetime import datetime, timezone

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_DIR = os.path.abspath(os.path.join(SCRIPT_DIR, ".."))
DATA = os.path.join(PROJECT_DIR, "data")
MERGED_CSV = os.path.join(DATA, "site_wb_merged.csv")
SITE_CSVS = [os.path.join(DATA, "site_extracted.csv"),
             os.path.join(DATA, "site_extracted_multicountry.csv")]
FIG_DIR = os.path.join(DATA, "figures")
SUMMARY_CSV = os.path.join(DATA, "site_wb_summary.csv")
VALID_CSV = os.path.join(DATA, "_site_validation_sample.csv")
LOG_TXT = os.path.join(DATA, "23_site_charts.log")

BLUE = "#3a7ca5"
DARK = "#16425b"
GOLD = "#b5862b"
GREY = "#9aa5ab"

# Journal tiers, matching 11/18/19/25.
TIER_TOP5 = {"AER", "QJE", "JPE", "ECMA", "RES"}
TIER_OTHER_GI = {"AERI", "AEJ_Applied", "AEJ_EP", "RESTAT", "EJ", "JEEA"}
TIERS = [("top5", "Top 5"), ("other_gi", "Other general-interest"), ("jde", "JDE")]
LEVELS = [("admin1", "Admin-1"), ("admin2", "Admin-2"), ("grouped", "Grouped regions")]

VALID_SEED = 42
VALID_PER_ROUTE = 10


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
    return "jde"


def mean_ci(xs):
    n = len(xs)
    if n == 0:
        return None, None, 0
    m = statistics.mean(xs)
    half = 1.96 * statistics.stdev(xs) / math.sqrt(n) if n > 1 else 0.0
    return m, half, n


def disposition(r):
    st = r["merge_status"]
    if st in ("ok", "partial"):
        return "multi" if r["site_scope"] == "multi_region" else "single"
    if st.startswith("not_placed:"):
        return st.split(":", 1)[1] or "unspecified"
    return "outside"


def fig39_coverage(rows):
    c = Counter(disposition(r) for r in rows)
    cats = [
        ("Placed in one World Bank unit", c["single"], DARK),
        ("Placed in several units", c["multi"], BLUE),
        ("Nationwide sample", c["nationwide"], GREY),
        ("Site not identifiable from the paper", c["unspecified"], GREY),
        ("Outside WB coverage or country unresolved", c["outside"], GREY),
    ]
    n = len(rows)
    fig, ax = plt.subplots(figsize=(8, 3.8))
    y = list(range(len(cats)))
    bars = ax.barh(y, [k[1] for k in cats], color=[k[2] for k in cats],
                   edgecolor="black", linewidth=0.5)
    ax.set_yticks(y)
    ax.set_yticklabels([k[0] for k in cats])
    ax.invert_yaxis()
    ax.set_xlabel(f"Site rows (one per single-country RCT or per country arm), N = {n}")
    ax.set_title("Subnational placement of RCT study sites")
    ax.set_xlim(0, max(k[1] for k in cats) * 1.2)
    ax.grid(axis="x", alpha=0.3)
    ax.set_axisbelow(True)
    for b, (_, cnt, _) in zip(bars, cats):
        ax.text(b.get_width() + n * 0.005, b.get_y() + b.get_height() / 2,
                f"{cnt} ({100 * cnt / n:.0f}%)", va="center", fontsize=9)
    save(fig, "fig39_site_coverage")
    return c


def fig40_rank(placed):
    ranks = [float(r["site_rank_pct_poor300"]) for r in placed if r["site_rank_pct_poor300"]]
    fig, ax = plt.subplots(figsize=(8, 4.2))
    bins = [i / 10 for i in range(11)]
    ax.hist(ranks, bins=bins, color=BLUE, edgecolor="white", linewidth=1.5)
    ax.axhline(len(ranks) / 10, color=DARK, linestyle="--", linewidth=1.2)
    ax.text(0.99, len(ranks) / 10, "  expected if sites were\n  random across regions",
            va="bottom", ha="right", fontsize=8, color=DARK)
    m = statistics.mean(ranks)
    ax.axvline(m, color=GOLD, linewidth=2)
    ax.text(m, ax.get_ylim()[1] * 0.97, f" mean {m:.2f}", color=GOLD, va="top", fontsize=9)
    ax.set_xlim(0, 1)
    ax.set_xlabel("Site's percentile rank among its country's survey regions, "
                  "$3.00/day headcount\n(0 = least poor region, 1 = poorest; unweighted)")
    ax.set_ylabel("Number of sites")
    ax.set_title(f"Where RCT sites sit in their country's regional poverty distribution (N = {len(ranks)})")
    ax.grid(axis="y", alpha=0.3)
    ax.set_axisbelow(True)
    save(fig, "fig40_site_poverty_rank")
    return ranks


def fig41_groups(placed):
    groups = []
    for key, label in TIERS:
        xs = [float(r["site_rank_pct_poor300"]) for r in placed
              if r["site_rank_pct_poor300"] and tier_of(r["journal_short"]) == key]
        groups.append(("Journal tier", label, xs))
    for key, label in LEVELS:
        xs = [float(r["site_rank_pct_poor300"]) for r in placed
              if r["site_rank_pct_poor300"] and r["level_kind"] == key]
        groups.append(("WB level of geography", label, xs))
    fig, ax = plt.subplots(figsize=(8, 4.4))
    y = list(range(len(groups)))
    for i, (panel, label, xs) in enumerate(groups):
        m, half, n = mean_ci(xs)
        if m is None:
            continue
        ax.errorbar(m, i, xerr=half, fmt="o", color=DARK if panel == "Journal tier" else BLUE,
                    markersize=7, capsize=3, linewidth=1.5)
        ax.text(1.0, i, f"n = {n}", va="center", ha="right", fontsize=8, color="#444444")
    ax.set_yticks(y)
    ax.set_yticklabels([f"{g[1]}" for g in groups])
    ax.axhline(2.5, color=GREY, linewidth=0.8)
    ax.text(0.02, 0.2, "Journal tier", fontsize=8, color=DARK, va="center")
    ax.text(0.02, 3.2, "WB level of geography", fontsize=8, color=BLUE, va="center")
    ax.axvline(0.5, color=GREY, linestyle="--", linewidth=1)
    ax.invert_yaxis()
    ax.set_xlim(0, 1)
    ax.set_xlabel("Mean percentile rank of site, $3.00/day headcount (95% interval)\n"
                  "dashed line: 0.5 = a region drawn at random")
    ax.set_title("Site poverty rank by journal tier and level of geography")
    ax.grid(axis="x", alpha=0.3)
    ax.set_axisbelow(True)
    save(fig, "fig41_site_rank_by_group")
    return groups


def fig42_scatter(placed):
    pts = [(float(r["survey_mean_poor300"]), float(r["poor300"])) for r in placed
           if r["survey_mean_poor300"] and r["poor300"]]
    fig, ax = plt.subplots(figsize=(5.6, 5.4))
    ax.plot([0, 1], [0, 1], color=GREY, linestyle="--", linewidth=1, zorder=1)
    ax.scatter([p[0] for p in pts], [p[1] for p in pts], s=22, color=BLUE, alpha=0.75,
               edgecolor="white", linewidth=0.6, zorder=2)
    above = sum(1 for x, y in pts if y > x)
    ax.text(0.03, 0.97, f"{above} of {len(pts)} sites above the line\n(poorer than the regional mean)",
            transform=ax.transAxes, va="top", fontsize=8.5, color="#333333")
    hi = max(max(p) for p in pts) * 1.05
    ax.set_xlim(0, hi)
    ax.set_ylim(0, hi)
    ax.set_xlabel("Unweighted mean of the survey's regions, $3.00/day headcount")
    ax.set_ylabel("Site's $3.00/day headcount")
    ax.set_title("Site poverty versus its country's regional mean")
    ax.grid(alpha=0.3)
    ax.set_axisbelow(True)
    save(fig, "fig42_site_vs_regional_mean")
    return pts


def write_summary(placed, groups):
    def row(scope, label, xs):
        m, half, n = mean_ci(xs)
        return {"scope": scope, "group": label, "n_sites": n,
                "mean_rank_pct_poor300": f"{m:.3f}" if m is not None else "",
                "ci95_half_width": f"{half:.3f}" if half is not None else "",
                "share_poorer_half": f"{sum(1 for x in xs if x > 0.5) / n:.3f}" if n else ""}
    allr = [float(r["site_rank_pct_poor300"]) for r in placed if r["site_rank_pct_poor300"]]
    out = [row("overall", "All placed sites", allr)]
    out += [row(scope, label, xs) for scope, label, xs in groups]
    with open(SUMMARY_CSV, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(out[0]))
        w.writeheader()
        w.writerows(out)
    log(f"  wrote {os.path.basename(SUMMARY_CSV)} ({len(out)} rows)")


def write_validation(placed):
    """Fixed-seed draw of placed sites, stratified by extraction route, with the
    paper's own locating text joined back in, for a human check."""
    site = {}
    for p in SITE_CSVS:
        if os.path.exists(p):
            with open(p, encoding="utf-8") as f:
                for r in csv.DictReader(f):
                    site[(r["doi"], r["iso3"])] = r
    rng = random.Random(VALID_SEED)
    by_route = {}
    for r in placed:
        by_route.setdefault(r["extract_route"] or "unknown", []).append(r)
    draw = []
    for route in sorted(by_route):
        rows = sorted(by_route[route], key=lambda r: (r["doi"], r["iso3"]))
        draw += rng.sample(rows, min(VALID_PER_ROUTE, len(rows)))
    fields = ["doi", "iso3", "title", "extract_route", "site_scope", "matched_units",
              "raw_site_text", "extract_justification", "extract_confidence",
              "fieldwork_year_start", "fieldwork_year_source", "spid_year",
              "site_rank_pct_poor300", "reviewer_site_correct", "reviewer_note"]
    with open(VALID_CSV, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for r in draw:
            s = site.get((r["doi"], r["iso3"]), {})
            w.writerow({k: r.get(k) or s.get(k, "") for k in fields
                        if k not in ("reviewer_site_correct", "reviewer_note")})
    log(f"  wrote {os.path.basename(VALID_CSV)} ({len(draw)} rows; seed {VALID_SEED})")


def main():
    open(LOG_TXT, "w").close()
    os.makedirs(FIG_DIR, exist_ok=True)
    with open(MERGED_CSV, encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    placed = [r for r in rows if r["merge_status"] in ("ok", "partial")]
    log(f"Stage 23 start: {len(rows)} site rows, {len(placed)} placed and merged")
    c = fig39_coverage(rows)
    log(f"  disposition: {dict(c)}")
    ranks = fig40_rank(placed)
    log(f"  rank: n={len(ranks)} mean={statistics.mean(ranks):.3f} "
        f"median={statistics.median(ranks):.3f}")
    groups = fig41_groups(placed)
    for scope, label, xs in groups:
        m, half, n = mean_ci(xs)
        if n:
            log(f"  {scope} / {label}: n={n} mean={m:.3f} +/- {half:.3f}")
    pts = fig42_scatter(placed)
    log(f"  scatter: {sum(1 for x, y in pts if y > x)}/{len(pts)} sites above regional mean")
    write_summary(placed, groups)
    write_validation(placed)


if __name__ == "__main__":
    main()
