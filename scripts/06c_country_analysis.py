"""
Stage 6c: Country-representation analysis.

Reads:  data/country_classified.csv  (Stage 6a output)
        data/poverty_2021.csv        (Stage 6b output)
        data/lmic_countries.csv      (Stage 0b output; for canonical names)
Writes: data/country_summary.csv
        data/figures/fig6_top_countries_bar.{png,pdf}
        data/figures/fig7_papers_vs_poverty_scatter.{png,pdf}
        data/figures/fig8_papers_vs_population_scatter.{png,pdf}

Methodology
-----------
1. Drop papers with country_is_cross_country == TRUE or country_study_setting
   in {cross_country, no_country, ''} from the country tally. Also drop papers
   with country_non_lmic_only == TRUE (study setting entirely outside LMICs).

2. For each remaining paper, distribute weight 1/n across its n named LMIC
   countries (single_country -> weight 1 on that country; two-country trial ->
   weight 0.5 each; up to 4 countries per the multi_country definition).

3. Merge against poverty_2021.csv. For each LMIC country, compute:
   - papers_raw         number of papers where this country appears
   - papers_fractional  sum of fractional weights
   - share_of_papers    papers_fractional / total fractional papers
   - share_of_poor      poor_population_2021 / total poor_population (LMICs with data)
   - share_of_pop       total_population_2021 / total LMIC population (LMICs with data)
   - rep_ratio_poor     share_of_papers / share_of_poor (1 = proportional)
   - rep_ratio_pop      share_of_papers / share_of_pop  (1 = proportional)

4. Figures:
   - fig6: horizontal bar of top 20 by papers_fractional
   - fig7: scatter of papers_fractional (y) vs poor_population_2021_mn (x, log)
   - fig8: scatter of papers_fractional (y) vs total_population_2021 (x, log)
   In both scatters, a dashed reference line marks the slope corresponding
   to proportional representation (total_papers / total_X). Selected
   over- and under-represented countries are labeled.

Dependencies: matplotlib only.
"""

import csv
import math
import os
from collections import defaultdict
from datetime import datetime, timezone

try:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
except ImportError:
    raise SystemExit("matplotlib is required. Run:\n  pip install matplotlib")

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_DIR = os.path.abspath(os.path.join(SCRIPT_DIR, ".."))
IN_PAPERS = os.path.join(PROJECT_DIR, "data", "country_classified.csv")
IN_POVERTY = os.path.join(PROJECT_DIR, "data", "poverty_2021.csv")
IN_LMIC = os.path.join(PROJECT_DIR, "data", "lmic_countries.csv")
OUT_SUMMARY = os.path.join(PROJECT_DIR, "data", "country_summary.csv")
FIG_DIR = os.path.join(PROJECT_DIR, "data", "figures")
LOG_TXT = os.path.join(PROJECT_DIR, "data", "06c_country_analysis.log")

TOP_N_BAR = 20
LABEL_N_SCATTER = 15  # number of points to label in scatter plots


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


def load_papers():
    """Return papers as a list of (paper_index, [iso3,...]).

    Only retains rows that qualify for the country tally:
      - is_development == TRUE
      - country_study_setting in {single_country, multi_country}
      - country_is_cross_country != TRUE
      - country_non_lmic_only != TRUE
      - country_iso3_list non-empty
    """
    kept = []
    n_total = n_dev = 0
    n_skip_no_class = n_skip_cross = n_skip_no_country = 0
    n_skip_non_lmic = n_skip_empty = 0
    setting_counts = defaultdict(int)
    with open(IN_PAPERS, encoding="utf-8") as f:
        for i, r in enumerate(csv.DictReader(f)):
            n_total += 1
            if r.get("is_development") != "TRUE":
                continue
            n_dev += 1
            setting = (r.get("country_study_setting") or "").strip().lower()
            setting_counts[setting or "(unclassified)"] += 1
            if not setting:
                n_skip_no_class += 1
                continue
            if setting in ("cross_country",):
                n_skip_cross += 1
                continue
            if setting == "no_country":
                n_skip_no_country += 1
                continue
            if (r.get("country_non_lmic_only") or "").upper() == "TRUE":
                n_skip_non_lmic += 1
                continue
            iso_list = [c for c in (r.get("country_iso3_list") or "").split(";") if c]
            if not iso_list:
                n_skip_empty += 1
                continue
            kept.append((i, iso_list))
    log(f"Loaded {n_total} rows; {n_dev} development papers")
    log("Study-setting distribution among dev papers:")
    for k in sorted(setting_counts, key=lambda x: -setting_counts[x]):
        log(f"    {k:25s} {setting_counts[k]}")
    log(f"Skipped: no_classification={n_skip_no_class}  cross_country={n_skip_cross}  "
        f"no_country={n_skip_no_country}  non_lmic_only={n_skip_non_lmic}  empty_iso3={n_skip_empty}")
    log(f"Retained for country tally: {len(kept)} papers")
    return kept


def load_poverty():
    out = {}
    with open(IN_POVERTY, encoding="utf-8") as f:
        for r in csv.DictReader(f):
            out[r["iso3"]] = {
                "name": r["name_canonical"],
                "income_group": r["income_group"],
                "headcount_pct_2021": float(r["headcount_pct_2021"]) if r["headcount_pct_2021"] else None,
                "headcount_year_used": r["headcount_year_used"] or "",
                "headcount_is_stale": r["headcount_is_stale"] == "TRUE",
                "headcount_is_interpolated": r["headcount_is_interpolated"] == "TRUE",
                "total_population_2021": float(r["total_population_2021"]) if r["total_population_2021"] else None,
                "poor_population_2021_mn": float(r["poor_population_2021_mn"]) if r["poor_population_2021_mn"] else None,
                "poverty_data_missing": r["poverty_data_missing"] == "TRUE",
            }
    return out


def compute_country_tallies(papers):
    """Return iso3 -> {papers_raw, papers_fractional}."""
    tally = defaultdict(lambda: {"papers_raw": 0, "papers_fractional": 0.0})
    for _, iso_list in papers:
        n = len(iso_list)
        w = 1.0 / n
        for iso in iso_list:
            tally[iso]["papers_raw"] += 1
            tally[iso]["papers_fractional"] += w
    return tally


def build_summary(tally, poverty):
    """One row per LMIC ISO3; LMICs with no papers still included for the
    denominator/representation analysis."""
    # Universe: union of poverty keys and tally keys
    all_iso = sorted(set(poverty) | set(tally))

    # Denominators: papers, poor, pop (over LMICs that have non-missing data)
    total_papers = sum(t["papers_fractional"] for t in tally.values())
    total_poor = sum(poverty[i]["poor_population_2021_mn"] for i in poverty
                     if poverty[i]["poor_population_2021_mn"] is not None)
    total_pop = sum(poverty[i]["total_population_2021"] for i in poverty
                    if poverty[i]["total_population_2021"] is not None)

    rows = []
    for iso in all_iso:
        t = tally.get(iso, {"papers_raw": 0, "papers_fractional": 0.0})
        p = poverty.get(iso, {})
        poor = p.get("poor_population_2021_mn")
        pop = p.get("total_population_2021")
        share_papers = (t["papers_fractional"] / total_papers) if total_papers else 0.0
        share_poor = (poor / total_poor) if (poor and total_poor) else None
        share_pop = (pop / total_pop) if (pop and total_pop) else None
        rep_poor = (share_papers / share_poor) if share_poor else None
        rep_pop = (share_papers / share_pop) if share_pop else None
        rows.append({
            "iso3": iso,
            "name_canonical": p.get("name", ""),
            "income_group": p.get("income_group", ""),
            "papers_raw": t["papers_raw"],
            "papers_fractional": round(t["papers_fractional"], 4),
            "headcount_pct_2021": (f"{p['headcount_pct_2021']:.4f}"
                                   if p.get("headcount_pct_2021") is not None else ""),
            "headcount_year_used": p.get("headcount_year_used", ""),
            "headcount_is_stale": "TRUE" if p.get("headcount_is_stale") else "FALSE",
            "total_population_2021": (f"{int(pop)}" if pop is not None else ""),
            "poor_population_2021_mn": (f"{poor:.4f}" if poor is not None else ""),
            "share_of_papers": f"{share_papers:.6f}",
            "share_of_poor": (f"{share_poor:.6f}" if share_poor is not None else ""),
            "share_of_pop": (f"{share_pop:.6f}" if share_pop is not None else ""),
            "rep_ratio_poor": (f"{rep_poor:.3f}" if rep_poor is not None else ""),
            "rep_ratio_pop": (f"{rep_pop:.3f}" if rep_pop is not None else ""),
        })
    return rows, total_papers, total_poor, total_pop


def write_summary(rows):
    fields = list(rows[0].keys())
    # Sort by papers_fractional desc for readability
    rows = sorted(rows, key=lambda r: -float(r["papers_fractional"]))
    with open(OUT_SUMMARY, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)
    log(f"Wrote {len(rows)} rows -> {OUT_SUMMARY}")


def fig6_top_countries_bar(rows):
    top = sorted(rows, key=lambda r: -float(r["papers_fractional"]))[:TOP_N_BAR]
    labels = [r["name_canonical"] or r["iso3"] for r in top]
    frac = [float(r["papers_fractional"]) for r in top]
    raw = [int(r["papers_raw"]) for r in top]

    fig, ax = plt.subplots(figsize=(9, 8))
    y_pos = list(range(len(labels)))
    bars = ax.barh(y_pos, frac, color="#3a7ca5", edgecolor="black", linewidth=0.5)
    ax.set_yticks(y_pos)
    ax.set_yticklabels(labels, fontsize=10)
    ax.invert_yaxis()
    ax.set_xlabel("Fractional paper count (1/n weighting for multi-country studies)")
    ax.set_title(f"Top {TOP_N_BAR} LMICs by representation in development articles, 2021-2025")
    ax.set_xlim(0, max(frac) * 1.2)
    ax.grid(axis="x", alpha=0.3)
    ax.set_axisbelow(True)
    for b, f, n in zip(bars, frac, raw):
        ax.text(b.get_width() + max(frac) * 0.01, b.get_y() + b.get_height() / 2,
                f"{f:.1f}  (raw n={n})", va="center", ha="left", fontsize=8)
    save(fig, "fig6_top_countries_bar")


def _scatter(rows, x_key, x_label, x_unit_note, name, total_papers, total_x):
    pts = []
    for r in rows:
        x_raw = r.get(x_key) or ""
        if not x_raw:
            continue
        x = float(x_raw)
        if x <= 0:
            continue
        y = float(r["papers_fractional"])
        pts.append((r["iso3"], r["name_canonical"] or r["iso3"], x, y))

    if not pts:
        log(f"  no points to plot for {name}")
        return

    xs = [p[2] for p in pts]
    ys = [p[3] for p in pts]

    # Reference line: y = (total_papers / total_x) * x  (proportional representation)
    slope = total_papers / total_x if total_x else 0
    x_ref = [min(xs), max(xs)]
    y_ref = [slope * x for x in x_ref]

    fig, ax = plt.subplots(figsize=(10, 7))
    ax.scatter(xs, ys, s=22, color="#3a7ca5", alpha=0.75, edgecolor="black", linewidth=0.3)
    ax.plot(x_ref, y_ref, linestyle="--", color="#888888", linewidth=1.2,
            label=f"proportional representation\n(slope = {slope:.3g} papers per unit)")

    # Label the top-N by paper count and the top-N over- and under-representation
    by_papers = sorted(pts, key=lambda p: -p[3])[:LABEL_N_SCATTER]
    # Identify over/under-represented based on residual from reference line
    resids = [(p, p[3] - slope * p[2]) for p in pts if p[3] > 0]
    over = sorted(resids, key=lambda x: -x[1])[:5]
    under = sorted(resids, key=lambda x: x[1])[:5]
    label_set = {p[0] for p in by_papers}
    label_set.update(p[0][0] for p in over)
    label_set.update(p[0][0] for p in under)
    for iso, name_c, x, y in pts:
        if iso in label_set:
            ax.annotate(name_c, xy=(x, y), xytext=(4, 3), textcoords="offset points",
                        fontsize=8, color="#222222")

    ax.set_xscale("log")
    ax.set_xlabel(f"{x_label} ({x_unit_note})")
    ax.set_ylabel("Fractional paper count, 2021-2025")
    ax.set_title(f"Development articles vs {x_label.lower()} across LMICs")
    ax.grid(alpha=0.3, which="both")
    ax.set_axisbelow(True)
    ax.legend(loc="upper left", fontsize=9, frameon=True)
    save(fig, name)


def fig7_papers_vs_poverty(rows, total_papers, total_poor):
    _scatter(rows, "poor_population_2021_mn",
             "Poor population in 2021 ($2.15/day, 2017 PPP)",
             "millions, log scale",
             "fig7_papers_vs_poverty_scatter",
             total_papers, total_poor)


def fig8_papers_vs_population(rows, total_papers, total_pop):
    # Convert pop to millions for axis readability
    rows_mn = []
    for r in rows:
        if not r.get("total_population_2021"):
            rows_mn.append(r)
            continue
        rcopy = dict(r)
        rcopy["total_population_mn"] = f"{float(r['total_population_2021']) / 1e6:.4f}"
        rows_mn.append(rcopy)
    total_pop_mn = total_pop / 1e6 if total_pop else 0
    _scatter(rows_mn, "total_population_mn",
             "Total population in 2021",
             "millions, log scale",
             "fig8_papers_vs_population_scatter",
             total_papers, total_pop_mn)


def main():
    open(LOG_TXT, "w").close()
    log("Stage 6c start")
    if not os.path.exists(IN_PAPERS):
        raise SystemExit(f"Missing input: {IN_PAPERS}. Run Stage 6a first.")
    if not os.path.exists(IN_POVERTY):
        raise SystemExit(f"Missing input: {IN_POVERTY}. Run Stage 6b first.")
    os.makedirs(FIG_DIR, exist_ok=True)

    papers = load_papers()
    poverty = load_poverty()
    tally = compute_country_tallies(papers)
    rows, total_papers, total_poor, total_pop = build_summary(tally, poverty)
    log(f"Total fractional papers tallied: {total_papers:.2f}")
    log(f"Total LMIC poor population, 2021 (mn): {total_poor:.1f}")
    log(f"Total LMIC population, 2021: {total_pop:,.0f}")
    write_summary(rows)

    fig6_top_countries_bar(rows)
    fig7_papers_vs_poverty(rows, total_papers, total_poor)
    fig8_papers_vs_population(rows, total_papers, total_pop)

    log("Stage 6c complete.")


if __name__ == "__main__":
    main()
