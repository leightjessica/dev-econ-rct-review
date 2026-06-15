"""
Stage 14: IRB / ethics-approval figures from the IRB classification.

Reads:  data/irb_classified.csv (Stage 13 output, with 2026-06-15 full-text
        re-extraction of the previously-undetermined cases)
Writes: data/figures/fig24_irb_reporting.{png,pdf}

fig24_irb_reporting   Horizontal bar: share of studies reporting any IRB, and
                      the in-country / foreign composition (foreign only, local
                      only, both local and foreign), as a share of all studies.

Dependencies: matplotlib + standard library. Mirrors 05_make_charts.py style.

Usage:
  python 14_irb_charts.py
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
IN_CSV = os.path.join(PROJECT_DIR, "data", "irb_classified.csv")
FIG_DIR = os.path.join(PROJECT_DIR, "data", "figures")
LOG_TXT = os.path.join(PROJECT_DIR, "data", "14_irb_charts.log")

BLUE = "#3a7ca5"      # house accent (matches fig1)
DARK = "#16425b"      # darker shade for the aggregate "Any IRB" bar


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


def fig24_irb_reporting(rows):
    n = len(rows)
    loc = Counter(r["irb_location_class"] for r in rows)
    any_irb = sum(1 for r in rows if r["irb_status"] == "ok")

    # Top bar is the aggregate; the three below decompose the in-country/foreign mix.
    cats = [
        ("Any IRB reported",        any_irb,             DARK),
        ("Foreign IRB only",        loc["foreign_only"], BLUE),
        ("Local IRB only",          loc["local_only"],   BLUE),
        ("Both local and foreign",  loc["both"],         BLUE),
    ]
    labels = [c[0] for c in cats]
    counts = [c[1] for c in cats]
    colors = [c[2] for c in cats]
    shares = [100 * c / n for c in counts]

    fig, ax = plt.subplots(figsize=(8, 4.5))
    y = list(range(len(labels)))
    bars = ax.barh(y, shares, color=colors, edgecolor="black", linewidth=0.5)
    ax.set_yticks(y)
    ax.set_yticklabels(labels)
    ax.invert_yaxis()
    ax.set_xlabel(f"Share of RCTs (%), N = {n}")
    ax.set_title("IRB / ethics approval: reporting and location, 2021-2025")
    ax.set_xlim(0, max(shares) * 1.25)
    ax.grid(axis="x", alpha=0.3)
    ax.set_axisbelow(True)
    # visually separate the aggregate from its components
    ax.axhline(0.5, color="grey", linewidth=0.6, linestyle="--")

    for b, c, s in zip(bars, counts, shares):
        ax.text(b.get_width() + max(shares) * 0.01,
                b.get_y() + b.get_height() / 2,
                f"{c} ({s:.1f}%)", va="center", ha="left", fontsize=9)

    save(fig, "fig24_irb_reporting")

    # footnote-worthy reconciliation for the log
    undet = loc.get("undetermined", 0)
    nomen = loc.get("no_mention", 0)
    log(f"  N={n}; any IRB={any_irb} ({100*any_irb/n:.1f}%); "
        f"foreign_only={loc['foreign_only']}, local_only={loc['local_only']}, "
        f"both={loc['both']}, undetermined={undet}, no_mention={nomen}")
    log("  note: foreign_only + local_only + both + undetermined = any IRB "
        "(undetermined = IRB reported but location not identifiable).")


def main():
    os.makedirs(FIG_DIR, exist_ok=True)
    with open(IN_CSV, encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    log(f"Read {len(rows)} rows from {os.path.basename(IN_CSV)}")
    fig24_irb_reporting(rows)
    log("Done.")


if __name__ == "__main__":
    main()
