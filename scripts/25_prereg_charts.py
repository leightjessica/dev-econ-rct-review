#!/usr/bin/env python3
"""Stage 25 - pre-registration / pre-analysis-plan figures and summary.

Reads:  data/prereg_classified.csv (Stage 24 output)
Writes: data/figures/fig35_prereg_overview.{png,pdf}
        data/figures/fig36_prereg_by_year.{png,pdf}
        data/figures/fig37_prereg_by_tier.{png,pdf}
        data/figures/fig38_registry_composition.{png,pdf}
        data/prereg_summary.csv                 (overall / by-tier / by-year)
        data/_prereg_validation_sample.csv       (fixed-seed human-audit draw)

fig35  Horizontal bars: share of RCTs that are registered, report a pre-analysis
       plan, both, or neither (as a share of all analyzed RCTs).
fig36  Line chart: share registered and share reporting a PAP, by publication year.
fig37  Grouped bars: share registered and share with a PAP, by journal tier
       (Top 5 / other general-interest / JDE), matching the funders/ack tiers.
fig38  Horizontal bar: registry composition (papers by registry; registries can
       co-occur, so the bars sum to more than the registered count).

Analysis sample: rows with extraction_status == "ok" (all 392 RCTs in the current
run; no_pdf / pdf_error rows, if any, are excluded because their status is unknown
rather than a true zero). Deterministic; matplotlib + standard library only.

Usage:
  python 25_prereg_charts.py
"""

import csv
import os
import random
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
IN_CSV = os.path.join(PROJECT_DIR, "data", "prereg_classified.csv")
FIG_DIR = os.path.join(PROJECT_DIR, "data", "figures")
SUMMARY_CSV = os.path.join(PROJECT_DIR, "data", "prereg_summary.csv")
VALID_CSV = os.path.join(PROJECT_DIR, "data", "_prereg_validation_sample.csv")
LOG_TXT = os.path.join(PROJECT_DIR, "data", "25_prereg_charts.log")

BLUE = "#3a7ca5"
DARK = "#16425b"
GOLD = "#b5862b"
GREY = "#9aa5ab"

# Journal tiers, matching 11/18/19.
TIER_TOP5 = {"AER", "QJE", "JPE", "ECMA", "RES"}
TIER_OTHER_GI = {"AERI", "AEJ_Applied", "AEJ_EP", "RESTAT", "EJ", "JEEA"}
TIER_JDE = {"JDE"}
TIERS = [("top5", "Top 5"), ("other_gi", "Other general-interest"), ("jde", "JDE")]

VALID_SEED = 42
VALID_PER_CLASS = 8   # up to this many per prereg_class in the audit draw


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


def is_true(v):
    return str(v).strip().lower() == "true"


def fig35_overview(rows):
    n = len(rows)
    reg = sum(1 for r in rows if is_true(r["registered"]))
    pap = sum(1 for r in rows if is_true(r["has_pap"]))
    both = sum(1 for r in rows if is_true(r["registered"]) and is_true(r["has_pap"]))
    neither = sum(1 for r in rows if not is_true(r["registered"]) and not is_true(r["has_pap"]))

    cats = [
        ("Registered in a trial registry", reg,     DARK),
        ("Reports a pre-analysis plan",    pap,     BLUE),
        ("Both registered and PAP",        both,    BLUE),
        ("Neither",                        neither, GREY),
    ]
    labels = [c[0] for c in cats]
    counts = [c[1] for c in cats]
    colors = [c[2] for c in cats]
    shares = [100 * c / n for c in counts]

    fig, ax = plt.subplots(figsize=(8, 4.2))
    y = list(range(len(labels)))
    bars = ax.barh(y, shares, color=colors, edgecolor="black", linewidth=0.5)
    ax.set_yticks(y)
    ax.set_yticklabels(labels)
    ax.invert_yaxis()
    ax.set_xlabel(f"Share of RCTs (%), N = {n}")
    ax.set_title("Trial registration and pre-analysis plans, development RCTs 2021-2025")
    ax.set_xlim(0, max(shares) * 1.25)
    ax.grid(axis="x", alpha=0.3)
    ax.set_axisbelow(True)
    for b, c, s in zip(bars, counts, shares):
        ax.text(b.get_width() + max(shares) * 0.01, b.get_y() + b.get_height() / 2,
                f"{c} ({s:.1f}%)", va="center", ha="left", fontsize=9)
    save(fig, "fig35_prereg_overview")
    log(f"  N={n}; registered={reg} ({100*reg/n:.1f}%), pap={pap} ({100*pap/n:.1f}%), "
        f"both={both}, neither={neither}")


def fig36_by_year(rows):
    years = sorted({r["publication_year"] for r in rows if r["publication_year"]})
    reg_share, pap_share, ns = [], [], []
    for yr in years:
        yrows = [r for r in rows if r["publication_year"] == yr]
        ns.append(len(yrows))
        reg_share.append(100 * sum(is_true(r["registered"]) for r in yrows) / len(yrows))
        pap_share.append(100 * sum(is_true(r["has_pap"]) for r in yrows) / len(yrows))

    fig, ax = plt.subplots(figsize=(8, 4.5))
    ax.plot(years, reg_share, marker="o", color=DARK, linewidth=2, label="Registered")
    ax.plot(years, pap_share, marker="s", color=BLUE, linewidth=2, label="Pre-analysis plan")
    for x, y in zip(years, reg_share):
        ax.text(x, y + 2, f"{y:.0f}%", ha="center", va="bottom", fontsize=8, color=DARK)
    for x, y in zip(years, pap_share):
        ax.text(x, y - 3, f"{y:.0f}%", ha="center", va="top", fontsize=8, color=BLUE)
    ax.set_ylabel("Share of RCTs (%)")
    ax.set_xlabel("Publication year")
    ax.set_title("Registration and pre-analysis plans over time")
    ax.set_ylim(0, 100)
    ax.set_xticks(years)
    ax.grid(axis="y", alpha=0.3)
    ax.set_axisbelow(True)
    ax.legend(loc="lower right", frameon=False)
    # annotate small annual cell sizes
    ax.text(0.01, 0.02, "Annual N: " + ", ".join(f"{y}={n}" for y, n in zip(years, ns)),
            transform=ax.transAxes, fontsize=7, color="grey")
    save(fig, "fig36_prereg_by_year")
    log("  by-year registered %: " + ", ".join(f"{y}:{s:.0f}" for y, s in zip(years, reg_share)))
    log("  by-year pap %:        " + ", ".join(f"{y}:{s:.0f}" for y, s in zip(years, pap_share)))


def fig37_by_tier(rows):
    reg_by, pap_by, n_by = {}, {}, {}
    for key, _ in TIERS:
        trows = [r for r in rows if tier_of(r["journal_short"]) == key]
        n_by[key] = len(trows)
        reg_by[key] = 100 * sum(is_true(r["registered"]) for r in trows) / len(trows) if trows else 0
        pap_by[key] = 100 * sum(is_true(r["has_pap"]) for r in trows) / len(trows) if trows else 0

    labels = [lab for _, lab in TIERS]
    reg_vals = [reg_by[k] for k, _ in TIERS]
    pap_vals = [pap_by[k] for k, _ in TIERS]
    ns = [n_by[k] for k, _ in TIERS]

    x = range(len(labels))
    w = 0.38
    fig, ax = plt.subplots(figsize=(8, 4.7))
    b1 = ax.bar([i - w / 2 for i in x], reg_vals, w, color=DARK, edgecolor="black",
                linewidth=0.5, label="Registered")
    b2 = ax.bar([i + w / 2 for i in x], pap_vals, w, color=BLUE, edgecolor="black",
                linewidth=0.5, label="Pre-analysis plan")
    for bars in (b1, b2):
        for b in bars:
            ax.text(b.get_x() + b.get_width() / 2, b.get_height() + 1,
                    f"{b.get_height():.0f}%", ha="center", va="bottom", fontsize=8)
    ax.set_xticks(list(x))
    ax.set_xticklabels([f"{lab}\n(n={n})" for lab, n in zip(labels, ns)])
    ax.set_ylabel("Share of RCTs (%)")
    ax.set_title("Registration and pre-analysis plans by journal tier")
    ax.set_ylim(0, 100)
    ax.grid(axis="y", alpha=0.3)
    ax.set_axisbelow(True)
    ax.legend(loc="upper right", frameon=False)
    save(fig, "fig37_prereg_by_tier")
    log("  by-tier registered %: " + ", ".join(f"{lab}:{v:.0f}" for lab, v in zip(labels, reg_vals)))
    log("  by-tier pap %:        " + ", ".join(f"{lab}:{v:.0f}" for lab, v in zip(labels, pap_vals)))


def fig38_registry_composition(rows):
    counter = Counter()
    for r in rows:
        for reg in [x.strip() for x in r["registries"].split("|") if x.strip()]:
            counter[reg] += 1
    if not counter:
        log("  fig38 skipped (no registries)")
        return
    items = counter.most_common()
    names = [k for k, _ in items]
    vals = [v for _, v in items]

    fig, ax = plt.subplots(figsize=(8, 0.55 * len(names) + 1.6))
    y = list(range(len(names)))
    bars = ax.barh(y, vals, color=BLUE, edgecolor="black", linewidth=0.5)
    ax.set_yticks(y)
    ax.set_yticklabels(names)
    ax.invert_yaxis()
    ax.set_xlabel("Number of RCTs (registries can co-occur)")
    ax.set_title("Which registries do development RCTs use?")
    ax.set_xlim(0, max(vals) * 1.15)
    ax.grid(axis="x", alpha=0.3)
    ax.set_axisbelow(True)
    for b, v in zip(bars, vals):
        ax.text(b.get_width() + max(vals) * 0.01, b.get_y() + b.get_height() / 2,
                str(v), va="center", ha="left", fontsize=9)
    save(fig, "fig38_registry_composition")
    log("  registry composition: " + ", ".join(f"{k}={v}" for k, v in items))


def write_summary(rows):
    def block(label, sub):
        n = len(sub)
        if not n:
            return None
        reg = sum(is_true(r["registered"]) for r in sub)
        pap = sum(is_true(r["has_pap"]) for r in sub)
        both = sum(is_true(r["registered"]) and is_true(r["has_pap"]) for r in sub)
        neither = sum(not is_true(r["registered"]) and not is_true(r["has_pap"]) for r in sub)
        return {
            "group": label, "n": n,
            "n_registered": reg, "pct_registered": round(100 * reg / n, 1),
            "n_pap": pap, "pct_pap": round(100 * pap / n, 1),
            "n_both": both, "pct_both": round(100 * both / n, 1),
            "n_neither": neither, "pct_neither": round(100 * neither / n, 1),
        }

    out = [block("overall", rows)]
    for key, lab in TIERS:
        out.append(block(f"tier:{lab}", [r for r in rows if tier_of(r["journal_short"]) == key]))
    for yr in sorted({r["publication_year"] for r in rows if r["publication_year"]}):
        out.append(block(f"year:{yr}", [r for r in rows if r["publication_year"] == yr]))
    out = [o for o in out if o]
    with open(SUMMARY_CSV, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(out[0].keys()))
        w.writeheader()
        w.writerows(out)
    log(f"  wrote {os.path.basename(SUMMARY_CSV)} ({len(out)} rows)")


def write_validation_sample(rows):
    """Fixed-seed stratified draw with snippets joined, for a human recount."""
    by_class = defaultdict(list)
    for r in rows:
        by_class[r["prereg_class"]].append(r)
    rng = random.Random(VALID_SEED)
    picked = []
    for cls in sorted(by_class):
        pool = sorted(by_class[cls], key=lambda r: r["doi"])
        rng.shuffle(pool)
        picked.extend(pool[:VALID_PER_CLASS])
    cols = ["doi", "journal_short", "publication_year", "title", "prereg_class",
            "registered", "registries", "registry_ids", "has_pap",
            "prereg_wording", "analysis_plan_any", "snippets"]
    with open(VALID_CSV, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        w.writerows(picked)
    log(f"  wrote {os.path.basename(VALID_CSV)} ({len(picked)} papers, "
        f"seed={VALID_SEED}, up to {VALID_PER_CLASS} per class)")


def main():
    os.makedirs(FIG_DIR, exist_ok=True)
    open(LOG_TXT, "w").close()
    with open(IN_CSV, encoding="utf-8") as f:
        allrows = list(csv.DictReader(f))
    rows = [r for r in allrows if r["extraction_status"] == "ok"]
    log(f"Read {len(allrows)} rows; analysis sample (status ok) = {len(rows)}")
    excluded = len(allrows) - len(rows)
    if excluded:
        log(f"  excluded {excluded} rows (no_pdf / pdf_error)")

    fig35_overview(rows)
    fig36_by_year(rows)
    fig37_by_tier(rows)
    fig38_registry_composition(rows)
    write_summary(rows)
    write_validation_sample(rows)
    log("Done.")


if __name__ == "__main__":
    main()
