"""
Stage 6c: Country-representation analysis.

Reads:  data/country_classified.csv  (Stage 6a output)
        data/poverty_2021.csv        (Stage 6b output)
        data/lmic_countries.csv      (Stage 0b output; for canonical names)
Writes: data/country_summary.csv
        data/figures/fig6_top_countries_bar.{png,pdf}
        data/figures/fig7_papers_vs_poverty_scatter.{png,pdf}
        data/figures/fig7b_papers_vs_poverty_scatter_linear.{png,pdf}
        data/figures/fig7c_papers_vs_poverty_reduction.{png,pdf}
        data/figures/fig7d_papers_vs_poverty_reduction_log.{png,pdf}
        data/figures/fig7e_papers_vs_poverty_headcount_share.{png,pdf}
        data/figures/fig8_papers_vs_population_scatter.{png,pdf}
        data/figures/fig9_top_papers_per_capita.{png,pdf}
        data/figures/fig10_top_papers_per_poor.{png,pdf}
        data/figures/fig11_bottom_papers_per_capita.{png,pdf}
        data/figures/fig12_bottom_papers_per_poor.{png,pdf}
        data/figures/fig13_top_rct_countries_bar.{png,pdf}
        data/figures/fig6_top_countries_bar_jde.{png,pdf}
            (top-countries bar restricted to journal_short == 'JDE';
            the other figures are emitted only for the pooled sample)

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
   - fig6:  horizontal bar of top 20 by papers_fractional
   - fig7:  scatter of papers_fractional (y) vs poor_population_2021_mn (x, log)
   - fig7b: companion to fig7 with a LINEAR x-axis (no log); includes
            countries whose 2021 $2.15/day headcount rounds to 0 (e.g. China)
   - fig7c: scatter of papers_fractional (y) vs poverty reduction
            2002 -> 2021 (x, linear; sign-flipped so positive = reduction).
            Countries missing 2002 PIP data are dropped from this figure.
   - fig7d: log-x companion to fig7c. Countries with zero or negative
            2002->2021 reduction are dropped (log-x requires positive
            values); the omitted count is annotated in a footnote.
   - fig7e: scatter of papers_fractional (y) vs headcount_pct_2021 (x,
            linear scale, 0-100%; the country-level poverty rate rather
            than the absolute count of poor people). Reference line is
            an OLS fit through the plotted points, not the
            proportional-representation slope used by fig7/fig7b
            (papers vs a country-level rate has no clean
            proportional-rep anchor).
   - fig8:  scatter of papers_fractional (y) vs total_population_2021 (x, log)
   - fig9:  horizontal bar of top 10 by papers per million people (per-capita
            intensity of research; restricted to countries with at least
            MIN_PAPERS_FOR_RATE fractional papers)
   - fig10: horizontal bar of top 10 by papers per million poor people
            (same restriction)
   - fig11: horizontal bar of the 10 MOST underrepresented LMICs by papers
            per million people, restricted to countries with at least
            MIN_POP_FOR_UNDERREP_MN million inhabitants (to avoid ranking
            being dominated by small island states with zero research)
   - fig12: same idea for papers per million poor, restricted to countries
            with at least MIN_POOR_FOR_UNDERREP_MN million people in poverty
   - fig13: parallel to fig6 but restricted to the RCT subsample
            (rct_classification == 'yes')
   - fig6_jde: top-countries bar restricted to articles in the Journal
            of Development Economics (journal_short == 'JDE'). The other
            figures (fig7-12) are emitted only for the pooled sample.
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
TOP_N_RATE_BAR = 10  # countries to show in the per-capita / per-poor bar charts
LABEL_N_SCATTER = 15  # number of points to label in scatter plots
MIN_PAPERS_FOR_RATE = 1.0  # minimum fractional papers a country must have
                           # before it is eligible for the TOP rate-based
                           # rankings; avoids ranking countries dominated by
                           # population scale rather than research interest
MIN_POP_FOR_UNDERREP_MN = 10.0    # minimum population (in millions) to enter
                                  # the BOTTOM-rate-per-capita ranking
MIN_POOR_FOR_UNDERREP_MN = 1.0    # minimum poor population (in millions) to
                                  # enter the BOTTOM-rate-per-poor ranking


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


def load_papers(rct_only=False, journal_short=None):
    """Return papers as a list of (paper_index, [iso3,...]).

    Only retains rows that qualify for the country tally:
      - is_development == TRUE
      - country_study_setting in {single_country, multi_country}
      - country_is_cross_country != TRUE
      - country_non_lmic_only != TRUE
      - country_iso3_list non-empty
      - (if rct_only) rct_classification == 'yes'
      - (if journal_short is set) journal_short == this value (case-insensitive)
    """
    kept = []
    n_total = n_dev = 0
    n_skip_no_class = n_skip_cross = n_skip_no_country = 0
    n_skip_non_lmic = n_skip_empty = n_skip_not_rct = n_skip_wrong_journal = 0
    setting_counts = defaultdict(int)
    journal_filter = journal_short.upper() if journal_short else None
    with open(IN_PAPERS, encoding="utf-8") as f:
        for i, r in enumerate(csv.DictReader(f)):
            n_total += 1
            if r.get("is_development") != "TRUE":
                continue
            if journal_filter and (r.get("journal_short") or "").upper() != journal_filter:
                n_skip_wrong_journal += 1
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
            if rct_only and (r.get("rct_classification") or "").strip().lower() != "yes":
                n_skip_not_rct += 1
                continue
            kept.append((i, iso_list))
    tags = []
    if rct_only:
        tags.append("RCT subsample")
    if journal_filter:
        tags.append(f"journal_short={journal_filter}")
    tag = f" ({'; '.join(tags)})" if tags else ""
    log(f"Loaded {n_total} rows; {n_dev} development papers{tag}")
    if not rct_only and not journal_filter:
        log("Study-setting distribution among dev papers:")
        for k in sorted(setting_counts, key=lambda x: -setting_counts[x]):
            log(f"    {k:25s} {setting_counts[k]}")
    extras = []
    if rct_only:
        extras.append(f"not_rct={n_skip_not_rct}")
    if journal_filter:
        extras.append(f"wrong_journal={n_skip_wrong_journal}")
    extra = ("  " + "  ".join(extras)) if extras else ""
    log(f"Skipped: no_classification={n_skip_no_class}  cross_country={n_skip_cross}  "
        f"no_country={n_skip_no_country}  non_lmic_only={n_skip_non_lmic}  empty_iso3={n_skip_empty}{extra}")
    log(f"Retained for country tally{tag}: {len(kept)} papers")
    return kept


def load_poverty():
    out = {}
    with open(IN_POVERTY, encoding="utf-8") as f:
        for r in csv.DictReader(f):
            # Defensive .get() on 2002 columns so this script still runs against
            # an older poverty_2021.csv that pre-dates the 2002 backfill.
            def _flt(k):
                v = r.get(k) or ""
                return float(v) if v else None
            out[r["iso3"]] = {
                "name": r["name_canonical"],
                "income_group": r["income_group"],
                "headcount_pct_2021": _flt("headcount_pct_2021"),
                "headcount_year_used": r["headcount_year_used"] or "",
                "headcount_is_stale": r["headcount_is_stale"] == "TRUE",
                "headcount_is_interpolated": r["headcount_is_interpolated"] == "TRUE",
                "total_population_2021": _flt("total_population_2021"),
                "poor_population_2021_mn": _flt("poor_population_2021_mn"),
                "poverty_data_missing": r["poverty_data_missing"] == "TRUE",
                "headcount_pct_2002": _flt("headcount_pct_2002"),
                "total_population_2002": _flt("total_population_2002"),
                "poor_population_2002_mn": _flt("poor_population_2002_mn"),
                "poverty_reduction_2002_to_2021_mn": _flt("poverty_reduction_2002_to_2021_mn"),
                "poverty_change_data_missing": (r.get("poverty_change_data_missing") or "") == "TRUE",
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
        poor_2002 = p.get("poor_population_2002_mn")
        reduction = p.get("poverty_reduction_2002_to_2021_mn")
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
            "poor_population_2002_mn": (f"{poor_2002:.4f}" if poor_2002 is not None else ""),
            "poverty_reduction_2002_to_2021_mn":
                (f"{reduction:.4f}" if reduction is not None else ""),
            "share_of_papers": f"{share_papers:.6f}",
            "share_of_poor": (f"{share_poor:.6f}" if share_poor is not None else ""),
            "share_of_pop": (f"{share_pop:.6f}" if share_pop is not None else ""),
            "rep_ratio_poor": (f"{rep_poor:.3f}" if rep_poor is not None else ""),
            "rep_ratio_pop": (f"{rep_pop:.3f}" if rep_pop is not None else ""),
        })
    return rows, total_papers, total_poor, total_pop


def write_summary(rows, out_path=None):
    if out_path is None:
        out_path = OUT_SUMMARY
    fields = list(rows[0].keys())
    # Sort by papers_fractional desc for readability
    rows = sorted(rows, key=lambda r: -float(r["papers_fractional"]))
    with open(out_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)
    log(f"Wrote {len(rows)} rows -> {out_path}")


def _top_countries_bar(entries, name_lookup, total_papers, name, title, color="#3a7ca5"):
    """Shared helper for top-N fractional-paper bar charts.

    entries: iterable of (iso3, papers_raw, papers_fractional) tuples.
    name_lookup: iso3 -> canonical country name (falls back to iso3).
    total_papers: total fractional papers in the parent universe (used to
                  annotate share-of-total alongside the raw count).
    """
    top = sorted(entries, key=lambda e: -e[2])[:TOP_N_BAR]
    labels = [name_lookup.get(iso) or iso for iso, _, _ in top]
    frac = [f for _, _, f in top]
    raw = [n for _, n, _ in top]

    fig, ax = plt.subplots(figsize=(9, 8))
    y_pos = list(range(len(labels)))
    bars = ax.barh(y_pos, frac, color=color, edgecolor="black", linewidth=0.5)
    ax.set_yticks(y_pos)
    ax.set_yticklabels(labels, fontsize=10)
    ax.invert_yaxis()
    ax.set_xlabel("Fractional paper count (1/n weighting for multi-country studies)")
    ax.set_title(title)
    ax.set_xlim(0, max(frac) * 1.25)
    ax.grid(axis="x", alpha=0.3)
    ax.set_axisbelow(True)
    for b, f, n in zip(bars, frac, raw):
        share = (f / total_papers * 100) if total_papers else 0
        ax.text(b.get_width() + max(frac) * 0.01, b.get_y() + b.get_height() / 2,
                f"{f:.1f}  (raw n={n}, {share:.1f}%)",
                va="center", ha="left", fontsize=8)
    save(fig, name)


def fig6_top_countries_bar(rows, total_papers, name_suffix="",
                            subset_phrase="development articles"):
    entries = [(r["iso3"], int(r["papers_raw"]), float(r["papers_fractional"]))
               for r in rows]
    name_lookup = {r["iso3"]: r["name_canonical"] for r in rows}
    _top_countries_bar(
        entries, name_lookup, total_papers,
        name=f"fig6_top_countries_bar{name_suffix}",
        title=f"Top {TOP_N_BAR} LMICs by representation in {subset_phrase}, 2021-2025",
    )


def fig13_top_rct_countries_bar(rct_tally, rows, total_rct_papers):
    """Parallel to fig6, restricted to papers classified as RCT == 'yes'."""
    entries = [(iso, t["papers_raw"], t["papers_fractional"])
               for iso, t in rct_tally.items()]
    name_lookup = {r["iso3"]: r["name_canonical"] for r in rows}
    _top_countries_bar(
        entries, name_lookup, total_rct_papers,
        name="fig13_top_rct_countries_bar",
        title=f"Top {TOP_N_BAR} LMICs by representation in RCT articles, 2021-2025",
    )


def _scatter(rows, x_key, x_label, x_unit_note, name, total_papers, total_x,
             min_x_orig=0.0, x_display_scale=1.0, x_format="default",
             vertical_grid=True, slope_unit_label="papers per unit",
             footnote=None, title_subject="Development articles",
             log_x=True, explicit_label_iso3=None, overlay_points=None,
             reference_mode="proportional"):
    """Scatter of papers_fractional vs an x-axis denominator.

    Parameters that vary across figures:
      min_x_orig         drop points with x_key < this value (in the input
                         units of the column, before any display scaling)
      x_display_scale    multiplier applied to x values before plotting
                         (e.g. 1e6 to convert "millions" -> actual count)
      x_format           "default" or "absolute_commas" (thousands-separator
                         tick labels instead of scientific notation)
      vertical_grid      if False, only horizontal gridlines are drawn
      slope_unit_label   text shown alongside the slope in the legend; the
                         slope value itself is always reported in the
                         original (un-scaled) units of x_key
      title_subject      leading phrase used in the plot title
                         (e.g. "Development articles" or "JDE articles")
      explicit_label_iso3  if not None, an iterable of ISO3 codes that
                           overrides the auto-label heuristic (top-N by
                           paper count + top-5 over/under-representation).
                           Use when the auto-set produces an unreadable
                           cluster and you want to pick representatives
                           by hand.
      overlay_points     if not None, an iterable of dicts each with keys
                         {x, y, label, marker, color, edgecolor}. Plotted
                         on top of the main scatter with a distinctive
                         marker; intended for imputed or otherwise
                         non-canonical observations that should be
                         visually distinguished and excluded from the
                         proportional-representation slope.
      reference_mode     "proportional" (default) draws the
                         total_papers / total_x slope anchored at the
                         origin; "ols" fits and draws a simple linear
                         regression line through the plotted points
                         (use when x is a country-level rate and the
                         proportional-rep anchor has no clean meaning);
                         "none" suppresses the reference line entirely.
    """
    pts = []
    for r in rows:
        x_raw = r.get(x_key) or ""
        if not x_raw:
            continue
        x = float(x_raw)
        # In log-x mode, x must be strictly positive (log undefined at 0).
        # In linear-x mode, x = 0 is plottable, so allow it.
        if log_x and x <= 0:
            continue
        if x < min_x_orig:
            continue
        y = float(r["papers_fractional"])
        pts.append((r["iso3"], r["name_canonical"] or r["iso3"], x, y))

    if not pts:
        log(f"  no points to plot for {name}")
        return

    xs = [p[2] * x_display_scale for p in pts]
    ys = [p[3] for p in pts]

    # Reference line. Two supported modes:
    #   proportional - y = (total_papers / total_x) * x_orig, anchored at
    #                  the origin. Slope reported in the original (un-scaled)
    #                  units of x_key so the legend remains interpretable
    #                  when x is rescaled for display.
    #   ols          - simple linear regression y = a + b*x fit to the
    #                  plotted points (in display-x units). Used for fig7e
    #                  where x is a country-level rate and the
    #                  proportional-rep anchor is not meaningful.
    #   none         - no reference line drawn.
    # Sample many points across the x range: a straight slope in linear-x
    # space curves in log-x linear-y space, so two endpoints would render a
    # misleading "straight diagonal".
    n_ref = 200
    if log_x:
        log_min = math.log10(min(xs))
        log_max = math.log10(max(xs))
        x_ref = [10 ** (log_min + (log_max - log_min) * i / (n_ref - 1))
                 for i in range(n_ref)]
    else:
        # For proportional mode, anchor at 0 so the line passes through the
        # origin. For ols mode, span only the observed x range so the fitted
        # line is not visually extrapolated beyond the data.
        if reference_mode == "proportional":
            x_min_lin = min(min(xs), 0.0)
        else:
            x_min_lin = min(xs)
        x_max_lin = max(xs)
        x_ref = [x_min_lin + (x_max_lin - x_min_lin) * i / (n_ref - 1)
                 for i in range(n_ref)]

    slope_orig = total_papers / total_x if total_x else 0
    slope_display = slope_orig / x_display_scale if x_display_scale else 0
    ols_a = ols_b = ols_r = 0.0
    if reference_mode == "proportional":
        y_ref = [slope_display * x for x in x_ref]
        ref_label = (f"proportional representation\n"
                     f"(slope = {slope_orig:.3g} {slope_unit_label})")
    elif reference_mode == "ols":
        n_pts = len(xs)
        mean_x = sum(xs) / n_pts
        mean_y = sum(ys) / n_pts
        sxx = sum((x - mean_x) ** 2 for x in xs)
        sxy = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys))
        syy = sum((y - mean_y) ** 2 for y in ys)
        ols_b = sxy / sxx if sxx > 0 else 0.0
        ols_a = mean_y - ols_b * mean_x
        ols_r = sxy / math.sqrt(sxx * syy) if (sxx > 0 and syy > 0) else 0.0
        y_ref = [ols_a + ols_b * x for x in x_ref]
        ref_label = (f"OLS fit: y = {ols_a:.2f} + {ols_b:.3g}·x\n"
                     f"(r = {ols_r:.2f}, n = {n_pts})")
    else:
        y_ref = None
        ref_label = None

    fig, ax = plt.subplots(figsize=(10, 7))
    ax.scatter(xs, ys, s=22, color="#3a7ca5", alpha=0.75, edgecolor="black", linewidth=0.3)
    if y_ref is not None:
        ax.plot(x_ref, y_ref, linestyle="--", color="#888888", linewidth=1.2,
                label=ref_label)

    if explicit_label_iso3 is not None:
        label_set = set(explicit_label_iso3)
    else:
        # Label the top-N by paper count and the top-N over- and under-representation.
        # Residuals are computed against whichever reference line is in use
        # so the auto-labeled points are the ones that visually stand out.
        by_papers = sorted(pts, key=lambda p: -p[3])[:LABEL_N_SCATTER]
        if reference_mode == "ols":
            def _resid(p):
                return p[3] - (ols_a + ols_b * p[2] * x_display_scale)
        else:
            def _resid(p):
                return p[3] - slope_orig * p[2]
        resids = [(p, _resid(p)) for p in pts if p[3] > 0]
        over = sorted(resids, key=lambda x: -x[1])[:5]
        under = sorted(resids, key=lambda x: x[1])[:5]
        label_set = {p[0] for p in by_papers}
        label_set.update(p[0][0] for p in over)
        label_set.update(p[0][0] for p in under)
    for iso, name_c, x, y in pts:
        if iso in label_set:
            ax.annotate(name_c, xy=(x * x_display_scale, y), xytext=(4, 3),
                        textcoords="offset points", fontsize=8, color="#222222")

    if log_x:
        ax.set_xscale("log")
    ax.set_xlabel(f"{x_label} ({x_unit_note})")
    ax.set_ylabel("Fractional paper count, 2021-2025")
    ax.set_title(f"{title_subject} vs {x_label.lower()} across LMICs")
    if x_format == "absolute_commas":
        from matplotlib.ticker import FuncFormatter, NullFormatter
        ax.xaxis.set_major_formatter(FuncFormatter(lambda v, pos: f"{int(v):,}"))
        ax.xaxis.set_minor_formatter(NullFormatter())
    elif x_format == "percent":
        from matplotlib.ticker import FuncFormatter
        ax.xaxis.set_major_formatter(FuncFormatter(lambda v, pos: f"{v:g}%"))
    if vertical_grid:
        ax.grid(alpha=0.3, which="both")
    else:
        ax.grid(axis="y", alpha=0.3, which="major")
    ax.set_axisbelow(True)
    ax.legend(loc="upper left", fontsize=9, frameon=True)
    if overlay_points:
        for op in overlay_points:
            x_o = op["x"] * x_display_scale
            y_o = op["y"]
            ax.scatter([x_o], [y_o],
                       marker=op.get("marker", "D"),
                       s=op.get("size", 80),
                       color=op.get("color", "#c1666b"),
                       edgecolor=op.get("edgecolor", "black"),
                       linewidth=0.9,
                       zorder=10,
                       label=op.get("legend_label"))
            # Place label above the marker with an arrow, so it is
            # unambiguous even when nearby auto-labeled points (e.g.,
            # China on fig7d) compete for the same screen region.
            ann_offset = op.get("annotation_offset", (0, 22))
            ann_ha = op.get("annotation_ha", "center")
            ax.annotate(
                op.get("label", ""),
                xy=(x_o, y_o),
                xytext=ann_offset,
                textcoords="offset points",
                fontsize=10,
                color=op.get("color", "#222222"),
                fontweight="bold",
                ha=ann_ha,
                arrowprops=dict(arrowstyle="-",
                                color=op.get("color", "#888888"),
                                lw=0.8,
                                shrinkA=0, shrinkB=2),
                zorder=11,
            )
        # If any overlay supplied a legend_label, refresh the legend.
        if any(op.get("legend_label") for op in overlay_points):
            ax.legend(loc="upper left", fontsize=9, frameon=True)
    if footnote:
        fig.text(0.02, 0.005, footnote, fontsize=8, color="#555555",
                 ha="left", va="bottom", style="italic")
    save(fig, name)


def fig7_papers_vs_poverty(rows, total_papers, total_poor, name_suffix="",
                            title_subject="Development articles"):
    # Restrict to countries with at least 100,000 poor people (0.1 mn) to
    # avoid stretching the log axis with effectively-zero-poverty LMICs,
    # display x in absolute people (not "millions"), and suppress vertical
    # gridlines per editorial preference.
    #
    # China (the top country by paper count) is absent from this scatter
    # because its World Bank PIP headcount at the $2.15/day 2017 PPP line
    # rounds to 0.0% in 2021, giving it zero poor population and an
    # undefined log-x position. Reported on the figure via the footnote
    # below; appears normally in fig8 (vs total population).
    chn = next((r for r in rows if r["iso3"] == "CHN"), None)
    chn_frac = float(chn["papers_fractional"]) if chn else 0.0
    log(f"  Note: China is excluded from fig7{name_suffix} because its $2.15/day 2017 PPP "
        f"headcount rounds to zero in the 2021 WB PIP record. "
        f"China's papers_fractional = {chn_frac:.1f}.")
    note = (f"Note: China ({chn_frac:.1f} fractional papers) is omitted because its "
            f"$2.15/day 2017 PPP headcount rounds to 0.0% in 2021.")
    _scatter(rows, "poor_population_2021_mn",
             "Poor population in 2021 ($2.15/day, 2017 PPP)",
             "log scale",
             f"fig7_papers_vs_poverty_scatter{name_suffix}",
             total_papers, total_poor,
             min_x_orig=0.1,
             x_display_scale=1e6,
             x_format="absolute_commas",
             vertical_grid=False,
             slope_unit_label="papers per million poor people",
             footnote=note,
             title_subject=title_subject)


def fig7b_papers_vs_poverty_linear(rows, total_papers, total_poor, name_suffix="",
                                    title_subject="Development articles"):
    # Linear-x companion to fig7. China (and any other LMIC whose 2021
    # $2.15/day headcount rounds to 0) is included at x=0, where the log
    # version had to drop it. No min_x_orig filter; tick labels in absolute
    # people so the x range stays human-readable.
    _scatter(rows, "poor_population_2021_mn",
             "Poor population in 2021 ($2.15/day, 2017 PPP)",
             "linear scale",
             f"fig7b_papers_vs_poverty_scatter_linear{name_suffix}",
             total_papers, total_poor,
             min_x_orig=0.0,
             x_display_scale=1e6,
             x_format="absolute_commas",
             vertical_grid=False,
             slope_unit_label="papers per million poor people",
             title_subject=title_subject,
             log_x=False)


def _india_imputed_overlay(rows):
    """Return an overlay-point dict for India, or None if India has no row.

    India is absent from PIP's 2002 lineup at the $2.15/day 2017 PPP line,
    so build_summary() leaves poverty_reduction_2002_to_2021_mn empty and
    the country drops out of fig7c/d. However, PIP per-country history for
    India contains national surveys at 1993, 2004, 2009, 2011, and 2022.
    We linearly interpolate the headcount ratio between the surveys that
    bracket 2002 (1993, 2004) and 2021 (2011, 2022) and use total
    population from poverty_2021.csv to recover an imputed poor count for
    each year.

    Methodology (deliberately simple, documented on the chart):
      hc_2002 = interp(1993 -> 17.66%, 2004 -> 16.59%)  ~ 16.78%
      hc_2021 = interp(2011 ->  6.61%, 2022 ->  0.75%)  ~  1.28%
      poor_y = hc_y * pop_y / 1e6
      reduction = poor_2002 - poor_2021

    The slope/total_x used by the proportional-representation reference
    line is unchanged because India's row in `rows` still has an empty
    poverty_reduction_2002_to_2021_mn (i.e., it is excluded from the
    denominator). The point is purely an annotation.

    PIP survey values are hardcoded here rather than re-fetched at runtime
    so the figure remains reproducible offline. If PIP revises India's
    historical series, refresh these constants and rerun.
    """
    ind_row = next((r for r in rows if r["iso3"] == "IND"), None)
    if ind_row is None:
        return None
    pop_2002_raw = ind_row.get("total_population_2021")  # filled separately
    # We need 2002 population, which is in the source poverty_2021.csv but
    # not threaded into the summary rows. Reload it inline.
    ind_pop_2002 = ind_pop_2021 = None
    with open(IN_POVERTY, encoding="utf-8") as f:
        for r in csv.DictReader(f):
            if r["iso3"] == "IND":
                if r.get("total_population_2002"):
                    ind_pop_2002 = float(r["total_population_2002"])
                if r.get("total_population_2021"):
                    ind_pop_2021 = float(r["total_population_2021"])
                break
    if ind_pop_2002 is None or ind_pop_2021 is None:
        log("  India imputation skipped: missing 2002 or 2021 population")
        return None

    hc_2002 = 0.1766 + (0.1659 - 0.1766) * (2002 - 1993) / (2004 - 1993)
    hc_2021 = 0.0661 + (0.0075 - 0.0661) * (2021 - 2011) / (2022 - 2011)
    poor_2002_mn = hc_2002 * ind_pop_2002 / 1e6
    poor_2021_mn = hc_2021 * ind_pop_2021 / 1e6
    reduction_mn = poor_2002_mn - poor_2021_mn
    papers_y = float(ind_row["papers_fractional"]) if ind_row.get("papers_fractional") else 0.0
    log(f"  India (imputed): hc_2002={hc_2002*100:.2f}% -> poor_2002={poor_2002_mn:.1f}M; "
        f"hc_2021={hc_2021*100:.2f}% -> poor_2021={poor_2021_mn:.1f}M; "
        f"reduction={reduction_mn:.1f}M; papers={papers_y:.1f}")
    return {
        "x": reduction_mn,
        "y": papers_y,
        "label": "India (imputed)",
        "marker": "D",
        "color": "#d97706",
        "edgecolor": "black",
        "size": 90,
        "legend_label": "India: linearly interpolated between PIP surveys\n(1993/2004 for 2002; 2011/2022 for 2021)",
    }


def fig7c_papers_vs_poverty_reduction(rows, total_papers, name_suffix="",
                                       title_subject="Development articles"):
    # Sign convention follows the user's request: poverty_reduction is
    # 2002 - 2021, so positive values are reductions (poor people lifted
    # above the $2.15/day 2017 PPP line) and negative values are increases.
    # Plotted on a linear x-axis so the sign is legible; China sits far to
    # the right as the largest reduction. The proportional-representation
    # reference line is fitted only over countries with positive reduction
    # (negative-reduction countries are off-line by definition since the
    # slope concept is "papers per million lifted").
    total_reduction = 0.0
    n_with_data = 0
    for r in rows:
        v = r.get("poverty_reduction_2002_to_2021_mn") or ""
        if not v:
            continue
        rv = float(v)
        n_with_data += 1
        if rv > 0:
            total_reduction += rv
    log(f"  fig7c{name_suffix}: {n_with_data} LMICs with 2002->2021 change data; "
        f"total reduction over positive-reduction countries = {total_reduction:.1f}M")
    india_overlay = _india_imputed_overlay(rows)
    overlays = [india_overlay] if india_overlay else None
    fn_india = ("India is shown as an imputed point (linear interpolation "
                "between PIP surveys 1993/2004 for 2002 and 2011/2022 for 2021); "
                "excluded from the proportional-representation slope.")
    _scatter(rows, "poverty_reduction_2002_to_2021_mn",
             "Poverty reduction, 2002 -> 2021 ($2.15/day, 2017 PPP)",
             "millions of people lifted; negative = increase",
             f"fig7c_papers_vs_poverty_reduction{name_suffix}",
             total_papers, total_reduction,
             min_x_orig=float("-inf"),  # allow negative x values
             x_display_scale=1.0,        # already in millions
             x_format="default",
             vertical_grid=True,
             slope_unit_label="papers per million poor lifted",
             title_subject=title_subject,
             log_x=False,
             overlay_points=overlays,
             footnote=(fn_india if overlays else None))


def fig7d_papers_vs_poverty_reduction_log(rows, total_papers, name_suffix="",
                                            title_subject="Development articles"):
    # Log-x companion to fig7c. Log scale cannot show non-positive values,
    # so countries with zero or negative 2002->2021 reduction (i.e.,
    # poverty increased or held flat) are dropped. We require at least
    # 0.1 million people lifted to enter the chart; this also avoids the
    # log axis being stretched by near-zero observations.
    total_reduction = 0.0
    n_with_data = 0
    n_positive = 0
    n_dropped_nonpos = 0
    for r in rows:
        v = r.get("poverty_reduction_2002_to_2021_mn") or ""
        if not v:
            continue
        rv = float(v)
        n_with_data += 1
        if rv > 0:
            total_reduction += rv
            n_positive += 1
        else:
            n_dropped_nonpos += 1
    india_overlay = _india_imputed_overlay(rows)
    overlays = [india_overlay] if india_overlay and india_overlay["x"] > 0 else None
    india_note = (" India is shown as an imputed point (linear interpolation "
                  "between PIP surveys 1993/2004 and 2011/2022); excluded from slope."
                  if overlays else "")
    note = (f"Note: {n_dropped_nonpos} countries with zero or negative "
            f"reduction (poverty held flat or rose) are omitted because "
            f"log-x requires positive values.{india_note}")
    log(f"  fig7d{name_suffix}: {n_positive} LMICs with positive reduction shown; "
        f"{n_dropped_nonpos} non-positive dropped")
    # The auto-label heuristic (top-N by papers + top-5 over/under-residual)
    # crams El Salvador, Turkiye, Bolivia, Argentina, and Russia into a single
    # unreadable column at x ~= 1M. Override with a hand-picked set: the
    # largest positive-reduction countries (well separated on the log axis)
    # plus three SSA exemplars (CIV/LSO/GNB are the SSA countries with 2002
    # PIP coverage and positive reduction; Guinea is omitted because it
    # overlaps Cote d'Ivoire at x ~= 2.2M).
    labels_fig7d = {
        "CHN", "IDN", "VNM", "BRA", "MEX", "COL", "PER",
        "CIV", "LSO", "GNB",
    }
    _scatter(rows, "poverty_reduction_2002_to_2021_mn",
             "Poverty reduction, 2002 -> 2021 ($2.15/day, 2017 PPP)",
             "millions of people lifted; log scale",
             f"fig7d_papers_vs_poverty_reduction_log{name_suffix}",
             total_papers, total_reduction,
             min_x_orig=0.1,
             x_display_scale=1.0,
             x_format="default",
             vertical_grid=True,
             slope_unit_label="papers per million poor lifted",
             title_subject=title_subject,
             footnote=note,
             log_x=True,
             explicit_label_iso3=labels_fig7d,
             overlay_points=overlays)


def fig7e_papers_vs_poverty_headcount_share(rows, total_papers, name_suffix="",
                                              title_subject="Development articles"):
    # Scatter of papers_fractional (y) vs the country-level poverty
    # headcount rate in 2021 (x, %). Unlike fig7/fig7b, which use the
    # absolute count of poor people, this figure plots the *share* of
    # each country's own population below the $2.15/day 2017 PPP line.
    # The proportional-representation reference line used elsewhere has
    # no clean meaning here (papers are a count; headcount is a rate),
    # so we draw an OLS fit through the plotted points instead.
    # Countries with missing 2021 headcount data are dropped silently
    # by the x-key filter inside _scatter.
    _scatter(rows, "headcount_pct_2021",
             "Poverty headcount rate in 2021 ($2.15/day, 2017 PPP)",
             "share of population, linear scale",
             f"fig7e_papers_vs_poverty_headcount_share{name_suffix}",
             total_papers, total_x=0,
             min_x_orig=0.0,
             x_display_scale=1.0,
             x_format="percent",
             vertical_grid=True,
             slope_unit_label="",
             title_subject=title_subject,
             log_x=False,
             reference_mode="ols")


def fig8_papers_vs_population(rows, total_papers, total_pop, name_suffix="",
                               title_subject="Development articles"):
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
             f"fig8_papers_vs_population_scatter{name_suffix}",
             total_papers, total_pop_mn,
             title_subject=title_subject)


def _rate_bar(rows, denom_key, denom_to_millions, x_label, title, name):
    """Generic horizontal-bar helper for the rate-based rankings.

    rows: list of country dicts from build_summary().
    denom_key: column name in `rows` that holds the denominator (e.g.
        'total_population_2021' or 'poor_population_2021_mn').
    denom_to_millions: divisor that converts the denominator to millions
        (1e6 for raw population, 1.0 for the already-in-millions poor count).
    """
    candidates = []
    for r in rows:
        denom = r.get(denom_key) or ""
        papers = float(r["papers_fractional"])
        if not denom or papers < MIN_PAPERS_FOR_RATE:
            continue
        denom_mn = float(denom) / denom_to_millions
        if denom_mn <= 0:
            continue
        rate = papers / denom_mn   # papers per million (of pop or poor)
        candidates.append((r["iso3"], r["name_canonical"] or r["iso3"],
                           rate, papers, denom_mn))

    if not candidates:
        log(f"  no eligible countries for {name}; skipping")
        return

    top = sorted(candidates, key=lambda c: -c[2])[:TOP_N_RATE_BAR]
    labels = [c[1] for c in top]
    rates = [c[2] for c in top]
    papers = [c[3] for c in top]
    denoms = [c[4] for c in top]

    fig, ax = plt.subplots(figsize=(9, 6))
    y_pos = list(range(len(labels)))
    bars = ax.barh(y_pos, rates, color="#3a7ca5", edgecolor="black", linewidth=0.5)
    ax.set_yticks(y_pos)
    ax.set_yticklabels(labels, fontsize=10)
    ax.invert_yaxis()
    ax.set_xlabel(x_label)
    ax.set_title(title)
    ax.set_xlim(0, max(rates) * 1.30)
    ax.grid(axis="x", alpha=0.3)
    ax.set_axisbelow(True)
    for b, rate, p, d in zip(bars, rates, papers, denoms):
        ax.text(b.get_width() + max(rates) * 0.01,
                b.get_y() + b.get_height() / 2,
                f"{rate:.3g}  ({p:.1f} papers / {d:.2f}M)",
                va="center", ha="left", fontsize=8)
    save(fig, name)


def fig9_top_papers_per_capita(rows, name_suffix="", journal_qualifier=""):
    qual = f", {journal_qualifier}" if journal_qualifier else ""
    _rate_bar(
        rows,
        denom_key="total_population_2021",
        denom_to_millions=1e6,
        x_label="Papers per million people",
        title=(f"Top {TOP_N_RATE_BAR} LMICs by papers per capita{qual} "
               f"(min {MIN_PAPERS_FOR_RATE:g} fractional papers)"),
        name=f"fig9_top_papers_per_capita{name_suffix}",
    )


def fig10_top_papers_per_poor(rows, name_suffix="", journal_qualifier=""):
    qual = f", {journal_qualifier}" if journal_qualifier else ""
    _rate_bar(
        rows,
        denom_key="poor_population_2021_mn",
        denom_to_millions=1.0,
        x_label="Papers per million poor people ($2.15/day, 2017 PPP)",
        title=(f"Top {TOP_N_RATE_BAR} LMICs by papers per poor person{qual} "
               f"(min {MIN_PAPERS_FOR_RATE:g} fractional papers)"),
        name=f"fig10_top_papers_per_poor{name_suffix}",
    )


def _underrep_bar(rows, denom_key, denom_to_millions, min_denom_millions,
                  x_label, title, name):
    """Bottom-of-distribution horizontal-bar helper.

    Restricts the universe to countries whose denominator (population in
    millions, or poor population in millions) is at least
    `min_denom_millions`, then ranks ascending by the papers-per-million
    rate. Ties at rate = 0 are broken by descending denominator, so the
    most populous (or most poor-burdened) country with zero research
    surfaces first.
    """
    candidates = []
    for r in rows:
        denom = r.get(denom_key) or ""
        if not denom:
            continue
        denom_mn = float(denom) / denom_to_millions
        if denom_mn < min_denom_millions:
            continue
        papers = float(r["papers_fractional"])
        rate = papers / denom_mn
        candidates.append((r["iso3"], r["name_canonical"] or r["iso3"],
                           rate, papers, denom_mn))

    if not candidates:
        log(f"  no eligible countries for {name}; skipping")
        return

    # Ascending rate; secondary key = descending denominator so the
    # largest country at rate=0 ranks first.
    bottom = sorted(candidates, key=lambda c: (c[2], -c[4]))[:TOP_N_RATE_BAR]
    labels = [c[1] for c in bottom]
    rates = [c[2] for c in bottom]
    papers = [c[3] for c in bottom]
    denoms = [c[4] for c in bottom]

    fig, ax = plt.subplots(figsize=(9, 6))
    y_pos = list(range(len(labels)))
    # Use a red palette to visually distinguish underrepresentation from
    # the top-N (blue) charts.
    bars = ax.barh(y_pos, rates, color="#c1666b", edgecolor="black", linewidth=0.5)
    ax.set_yticks(y_pos)
    ax.set_yticklabels(labels, fontsize=10)
    ax.invert_yaxis()  # most underrepresented at the top of the plot
    ax.set_xlabel(x_label)
    ax.set_title(title)
    max_rate = max(rates) if rates and max(rates) > 0 else 0.01
    ax.set_xlim(0, max_rate * 1.35)
    ax.grid(axis="x", alpha=0.3)
    ax.set_axisbelow(True)
    for b, rate, p, d in zip(bars, rates, papers, denoms):
        ax.text(b.get_width() + max_rate * 0.01,
                b.get_y() + b.get_height() / 2,
                f"{rate:.3g}  ({p:.1f} papers / {d:.1f}M)",
                va="center", ha="left", fontsize=8)
    save(fig, name)


def fig11_bottom_papers_per_capita(rows, name_suffix="", journal_qualifier=""):
    qual = f", {journal_qualifier}" if journal_qualifier else ""
    _underrep_bar(
        rows,
        denom_key="total_population_2021",
        denom_to_millions=1e6,
        min_denom_millions=MIN_POP_FOR_UNDERREP_MN,
        x_label="Papers per million people",
        title=(f"Most underrepresented LMICs by papers per capita{qual}\n"
               f"(restricted to countries with at least "
               f"{MIN_POP_FOR_UNDERREP_MN:g}M inhabitants)"),
        name=f"fig11_bottom_papers_per_capita{name_suffix}",
    )


def fig12_bottom_papers_per_poor(rows, name_suffix="", journal_qualifier=""):
    qual = f", {journal_qualifier}" if journal_qualifier else ""
    _underrep_bar(
        rows,
        denom_key="poor_population_2021_mn",
        denom_to_millions=1.0,
        min_denom_millions=MIN_POOR_FOR_UNDERREP_MN,
        x_label="Papers per million poor people ($2.15/day, 2017 PPP)",
        title=(f"Most underrepresented LMICs by papers per poor person{qual}\n"
               f"(restricted to countries with at least "
               f"{MIN_POOR_FOR_UNDERREP_MN:g}M people in poverty)"),
        name=f"fig12_bottom_papers_per_poor{name_suffix}",
    )


def run_breakdown(poverty, journal_short=None, name_suffix="",
                   subset_phrase="development articles",
                   title_subject="Development articles",
                   journal_qualifier="",
                   summary_path=None):
    """Build the country tally + summary + figs6-12 for a paper subset.

    The poverty/population denominators are passed in from the caller so the
    LMIC universe is identical across calls; only the numerator (papers)
    varies with the journal/RCT filter.
    """
    papers = load_papers(journal_short=journal_short)
    tally = compute_country_tallies(papers)
    rows, total_papers, total_poor, total_pop = build_summary(tally, poverty)
    label = f" ({journal_short})" if journal_short else ""
    log(f"Total fractional papers tallied{label}: {total_papers:.2f}")
    write_summary(rows, summary_path)

    fig6_top_countries_bar(rows, total_papers,
                           name_suffix=name_suffix, subset_phrase=subset_phrase)
    fig7_papers_vs_poverty(rows, total_papers, total_poor,
                           name_suffix=name_suffix, title_subject=title_subject)
    fig7b_papers_vs_poverty_linear(rows, total_papers, total_poor,
                                    name_suffix=name_suffix, title_subject=title_subject)
    fig7c_papers_vs_poverty_reduction(rows, total_papers,
                                       name_suffix=name_suffix, title_subject=title_subject)
    fig7d_papers_vs_poverty_reduction_log(rows, total_papers,
                                           name_suffix=name_suffix, title_subject=title_subject)
    fig7e_papers_vs_poverty_headcount_share(rows, total_papers,
                                              name_suffix=name_suffix, title_subject=title_subject)
    fig8_papers_vs_population(rows, total_papers, total_pop,
                              name_suffix=name_suffix, title_subject=title_subject)
    fig9_top_papers_per_capita(rows, name_suffix=name_suffix,
                                journal_qualifier=journal_qualifier)
    fig10_top_papers_per_poor(rows, name_suffix=name_suffix,
                               journal_qualifier=journal_qualifier)
    fig11_bottom_papers_per_capita(rows, name_suffix=name_suffix,
                                    journal_qualifier=journal_qualifier)
    fig12_bottom_papers_per_poor(rows, name_suffix=name_suffix,
                                  journal_qualifier=journal_qualifier)
    return rows


def main():
    open(LOG_TXT, "w").close()
    log("Stage 6c start")
    if not os.path.exists(IN_PAPERS):
        raise SystemExit(f"Missing input: {IN_PAPERS}. Run Stage 6a first.")
    if not os.path.exists(IN_POVERTY):
        raise SystemExit(f"Missing input: {IN_POVERTY}. Run Stage 6b first.")
    os.makedirs(FIG_DIR, exist_ok=True)

    poverty = load_poverty()
    total_poor = sum(p["poor_population_2021_mn"] for p in poverty.values()
                     if p["poor_population_2021_mn"] is not None)
    total_pop = sum(p["total_population_2021"] for p in poverty.values()
                    if p["total_population_2021"] is not None)
    log(f"Total LMIC poor population, 2021 (mn): {total_poor:.1f}")
    log(f"Total LMIC population, 2021: {total_pop:,.0f}")

    log("--- All development articles ---")
    rows = run_breakdown(poverty, summary_path=OUT_SUMMARY)

    log("--- Journal of Development Economics subset (top-countries bar only) ---")
    jde_papers = load_papers(journal_short="JDE")
    jde_tally = compute_country_tallies(jde_papers)
    jde_rows, jde_total_papers, _, _ = build_summary(jde_tally, poverty)
    log(f"Total fractional papers tallied (JDE): {jde_total_papers:.2f}")
    fig6_top_countries_bar(
        jde_rows, jde_total_papers,
        name_suffix="_jde",
        subset_phrase="Journal of Development Economics articles",
    )

    log("--- RCT subsample (fig13) ---")
    rct_papers = load_papers(rct_only=True)
    rct_tally = compute_country_tallies(rct_papers)
    total_rct_papers = sum(t["papers_fractional"] for t in rct_tally.values())
    log(f"Total fractional papers tallied (RCT subsample): {total_rct_papers:.2f}")
    fig13_top_rct_countries_bar(rct_tally, rows, total_rct_papers)

    log("Stage 6c complete.")


if __name__ == "__main__":
    main()
