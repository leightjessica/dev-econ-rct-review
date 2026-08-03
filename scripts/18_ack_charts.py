"""
Stage 18: acknowledgments figures + summary + validation sample.

Reads:  data/ack_classified.csv (Stage 17 output)
Writes: data/figures/fig26_ack_individuals_hist.{png,pdf}
        data/figures/fig27_ack_seminars_hist.{png,pdf}
        data/figures/fig28_ack_by_journal_tier.{png,pdf}
        data/ack_summary.csv                (overall + by-tier mean/median)
        data/_ack_validation_sample.csv     (28 papers for a manual check)

Analysis sample = RCTs with classify_status in {ok, no_block}. A no_block paper
contributes 0 individuals / 0 seminars (its acknowledgment names nobody, or it has
none) -- a valid data point. no_pdf / pdf_error rows are excluded (the count is
unknown, not zero).

Headline counts, per the project lead's specification (2026-06-22):
  individuals = named people thanked for COMMENTS only (research assistants,
                anonymous referees, and the editor are tracked but NOT in this
                count); seminars = distinct named venues (seminars + conferences
                + workshops + meetings combined).

Mirrors 14_irb_charts.py / 05_make_charts.py style.

Usage:
  python 18_ack_charts.py
"""

import csv
import os
import statistics
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
IN_CSV = os.path.join(PROJECT_DIR, "data", "ack_classified.csv")
BLOCKS_CSV = os.path.join(PROJECT_DIR, "data", "ack_blocks.csv")
FIG_DIR = os.path.join(PROJECT_DIR, "data", "figures")
SUMMARY_CSV = os.path.join(PROJECT_DIR, "data", "ack_summary.csv")
VALID_CSV = os.path.join(PROJECT_DIR, "data", "_ack_validation_sample.csv")
LOG_TXT = os.path.join(PROJECT_DIR, "data", "18_ack_charts.log")

BLUE = "#3a7ca5"
DARK = "#16425b"
GOLD = "#b5862b"

# Journal tiers (journal_short codes), matching 11_funders_by_journal_tier.py.
TIER_TOP5 = {"AER", "QJE", "JPE", "ECMA", "RES"}
TIER_OTHER_GI = {"AERI", "AEJ_Applied", "AEJ_EP", "RESTAT", "EJ", "JEEA"}
TIER_JDE = {"JDE"}
TIERS = [("top5", "Top 5"), ("other_gi", "Other general-interest"), ("jde", "JDE")]

VALID_N = 28
VALID_SEED = 20260622   # fixed seed -> reproducible draw (Date.now is unavailable)


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


def describe(vals):
    if not vals:
        return dict(n=0, mean=0, median=0, p90=0, mx=0, zero=0)
    s = sorted(vals)
    p90 = s[min(len(s) - 1, int(0.9 * len(s)))]
    return dict(n=len(vals), mean=statistics.mean(vals),
                median=statistics.median(vals), p90=p90, mx=max(vals),
                zero=sum(1 for v in vals if v == 0))


def hist(vals, title, xlabel, color, name, max_bin=None):
    mx = max(vals) if vals else 0
    if max_bin is None:
        max_bin = mx + 1
    bins = range(0, max_bin + 2)
    fig, ax = plt.subplots(figsize=(8, 4.5))
    ax.hist(vals, bins=bins, color=color, edgecolor="black", linewidth=0.5,
            align="left", rwidth=0.9)
    m = statistics.mean(vals)
    md = statistics.median(vals)
    ax.axvline(m, color=DARK, linestyle="--", linewidth=1.2,
               label=f"mean {m:.1f}")
    ax.axvline(md, color=GOLD, linestyle=":", linewidth=1.4,
               label=f"median {md:.0f}")
    ax.set_xlabel(xlabel)
    ax.set_ylabel(f"Number of RCTs (N = {len(vals)})")
    ax.set_title(title)
    ax.grid(axis="y", alpha=0.3)
    ax.set_axisbelow(True)
    ax.legend()
    save(fig, name)


def fig28_by_tier(rows):
    """Grouped bar: mean individuals and mean seminars per paper, by tier."""
    by_tier_ind = defaultdict(list)
    by_tier_sem = defaultdict(list)
    for r in rows:
        t = tier_of(r["journal_short"])
        by_tier_ind[t].append(r["_ind"])
        by_tier_sem[t].append(r["_sem"])
    labels = [lab for key, lab in TIERS]
    ind_means = [statistics.mean(by_tier_ind[key]) if by_tier_ind[key] else 0
                 for key, _ in TIERS]
    sem_means = [statistics.mean(by_tier_sem[key]) if by_tier_sem[key] else 0
                 for key, _ in TIERS]
    ns = [len(by_tier_ind[key]) for key, _ in TIERS]

    x = range(len(labels))
    w = 0.38
    fig, ax = plt.subplots(figsize=(8, 4.5))
    b1 = ax.bar([i - w / 2 for i in x], ind_means, w, label="Individuals thanked (comments)",
                color=BLUE, edgecolor="black", linewidth=0.5)
    b2 = ax.bar([i + w / 2 for i in x], sem_means, w, label="Seminars/venues",
                color=GOLD, edgecolor="black", linewidth=0.5)
    ax.set_xticks(list(x))
    ax.set_xticklabels([f"{lab}\n(n={n})" for lab, n in zip(labels, ns)])
    ax.set_ylabel("Mean per paper")
    ax.set_title("Acknowledgments by journal tier, 2021-2025")
    ax.grid(axis="y", alpha=0.3)
    ax.set_axisbelow(True)
    ax.legend()
    for bars in (b1, b2):
        for b in bars:
            ax.text(b.get_x() + b.get_width() / 2, b.get_height() + 0.2,
                    f"{b.get_height():.1f}", ha="center", va="bottom", fontsize=8)
    save(fig, "fig28_ack_by_journal_tier")


def write_summary(rows):
    fields = ["group", "n", "ind_mean", "ind_median", "ind_p90", "ind_max",
              "ind_zero", "sem_mean", "sem_median", "sem_p90", "sem_max",
              "sem_zero", "any_seminar_unspec", "mean_referees", "share_editor"]
    out = []

    def row_for(name, rs):
        di = describe([r["_ind"] for r in rs])
        ds = describe([r["_sem"] for r in rs])
        unspec = sum(1 for r in rs if r.get("seminars_unspecified") == "True")
        refs = [r["_ref"] for r in rs]
        eds = sum(1 for r in rs if r.get("editor_thanked") == "True")
        return {
            "group": name, "n": len(rs),
            "ind_mean": round(di["mean"], 2), "ind_median": di["median"],
            "ind_p90": di["p90"], "ind_max": di["mx"], "ind_zero": di["zero"],
            "sem_mean": round(ds["mean"], 2), "sem_median": ds["median"],
            "sem_p90": ds["p90"], "sem_max": ds["mx"], "sem_zero": ds["zero"],
            "any_seminar_unspec": unspec,
            "mean_referees": round(statistics.mean(refs), 2) if refs else 0,
            "share_editor": round(100 * eds / len(rs), 1) if rs else 0,
        }

    out.append(row_for("ALL", rows))
    for key, lab in TIERS:
        rs = [r for r in rows if tier_of(r["journal_short"]) == key]
        if rs:
            out.append(row_for(lab, rs))
    for yr in sorted({r["publication_year"] for r in rows}):
        rs = [r for r in rows if r["publication_year"] == yr]
        out.append(row_for(f"year {yr}", rs))

    with open(SUMMARY_CSV, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(out)
    log(f"  wrote {os.path.basename(SUMMARY_CSV)} ({len(out)} groups)")


def write_validation_sample(rows):
    """Stratified-ish reproducible draw for a manual recount. Joins the ack_text
    back in from ack_blocks.csv so a human can recount against the source."""
    import random
    blocks = {}
    with open(BLOCKS_CSV, encoding="utf-8") as f:
        for b in csv.DictReader(f):
            blocks[b["doi"]] = b.get("ack_text", "")
    pool = [r for r in rows if r["classify_status"] == "ok"]
    rng = random.Random(VALID_SEED)
    sample = rng.sample(pool, min(VALID_N, len(pool)))
    sample.sort(key=lambda r: (r["journal_short"], r["publication_year"]))
    fields = ["doi", "journal_short", "publication_year", "title",
              "llm_n_individuals", "llm_individuals", "llm_n_seminars",
              "llm_seminars", "manual_n_individuals", "manual_n_seminars",
              "notes", "ack_text"]
    with open(VALID_CSV, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for r in sample:
            w.writerow({
                "doi": r["doi"], "journal_short": r["journal_short"],
                "publication_year": r["publication_year"], "title": r["title"],
                "llm_n_individuals": r["_ind"],
                "llm_individuals": r.get("individuals_comments", ""),
                "llm_n_seminars": r["_sem"],
                "llm_seminars": r.get("seminar_venues", ""),
                "manual_n_individuals": "", "manual_n_seminars": "",
                "notes": "", "ack_text": blocks.get(r["doi"], ""),
            })
    log(f"  wrote {os.path.basename(VALID_CSV)} ({len(sample)} papers, "
        f"seed {VALID_SEED}) -- fill manual_* columns to validate")


def main():
    os.makedirs(FIG_DIR, exist_ok=True)
    with open(IN_CSV, encoding="utf-8") as f:
        allrows = list(csv.DictReader(f))
    log(f"Read {len(allrows)} rows from {os.path.basename(IN_CSV)}")

    # Analysis sample: ok + no_block, with integer counts coerced.
    rows = []
    for r in allrows:
        if r["classify_status"] not in ("ok", "no_block"):
            continue
        try:
            r["_ind"] = int(r["n_individuals_comments"])
            r["_sem"] = int(r["n_seminars"])
            r["_ref"] = int(r["n_anonymous_referees"] or 0)
        except (ValueError, TypeError):
            continue
        rows.append(r)
    by_status = Counter(r["classify_status"] for r in allrows)
    log(f"Analysis sample: {len(rows)}  "
        f"(ok={by_status.get('ok',0)}, no_block={by_status.get('no_block',0)}; "
        f"excluded no_pdf/pdf_error/call_error="
        f"{sum(by_status.get(s,0) for s in ('no_pdf','pdf_error','call_error'))})")

    ind = [r["_ind"] for r in rows]
    sem = [r["_sem"] for r in rows]
    di, ds = describe(ind), describe(sem)
    log(f"Individuals thanked: mean {di['mean']:.1f}, median {di['median']}, "
        f"p90 {di['p90']}, max {di['mx']}, zero {di['zero']}")
    log(f"Seminars per paper:  mean {ds['mean']:.1f}, median {ds['median']}, "
        f"p90 {ds['p90']}, max {ds['mx']}, zero {ds['zero']}")

    hist(ind, "Individuals thanked for comments, per RCT (2021-2025)",
         "Number of individuals thanked (comments)", BLUE,
         "fig26_ack_individuals_hist")
    hist(sem, "Seminars/conferences acknowledged, per RCT (2021-2025)",
         "Number of distinct seminars/venues", GOLD,
         "fig27_ack_seminars_hist")
    fig28_by_tier(rows)
    write_summary(rows)
    write_validation_sample(rows)
    log("Done.")


if __name__ == "__main__":
    main()
