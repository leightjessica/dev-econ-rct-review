"""
Stage 15: Top study-site countries among RCTs that report an in-country IRB.

Reads:  data/irb_classified.csv (Stage 13 output, 2026-06-15 full-text re-extraction)
        data/lmic_countries.csv (iso3 -> canonical name lookup)
Writes: data/figures/fig25_irb_incountry_top_sites.{png,pdf}

fig25_irb_incountry_top_sites
        Horizontal bar: the five most common study-site countries among the RCTs
        for which at least one IRB/ethics approval was obtained in the study
        country itself (irb_location_class in {local_only, both}).

Counting: each study contributes once per distinct study-site country, so a
cross-country study is counted in every site it names (only 1 of the 91
in-country-IRB studies names more than one site, so per-study and per-mention
counts coincide except for that case).

Dependencies: matplotlib + standard library. Mirrors 05_make_charts.py /
14_irb_charts.py style.

Usage:
  "C:\\Users\\JLEIGHT\\AppData\\Local\\Python\\pythoncore-3.14-64\\python.exe" 15_irb_country_sites.py
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
LMIC_CSV = os.path.join(PROJECT_DIR, "data", "lmic_countries.csv")
FIG_DIR = os.path.join(PROJECT_DIR, "data", "figures")
LOG_TXT = os.path.join(PROJECT_DIR, "data", "15_irb_country_sites.log")

BLUE = "#3a7ca5"      # house accent (matches fig1 / fig24)

TOP_N = 5
IN_COUNTRY = {"local_only", "both"}   # at least one IRB in the study country


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


def split_codes(s):
    """Split a study_iso3_list cell into ISO3 codes (handles ; | , delimiters)."""
    s = (s or "").strip()
    if not s:
        return []
    for d in (";", "|", ","):
        if d in s:
            return [c.strip() for c in s.split(d) if c.strip()]
    return [s]


def load_iso3_names():
    names = {}
    with open(LMIC_CSV, encoding="utf-8") as f:
        for r in csv.DictReader(f):
            names[r["iso3"].strip()] = r["name_canonical"].strip()
    return names


def fig25_incountry_top_sites(rows, iso3_names):
    incountry = [r for r in rows if r["irb_location_class"] in IN_COUNTRY]
    n_studies = len(incountry)

    site_studies = Counter()   # distinct studies per country site
    for r in incountry:
        for iso3 in set(split_codes(r["study_iso3_list"])):
            site_studies[iso3] += 1

    top = site_studies.most_common(TOP_N)
    labels = [iso3_names.get(iso3, iso3) for iso3, _ in top]
    counts = [c for _, c in top]

    fig, ax = plt.subplots(figsize=(8, 4.5))
    y = list(range(len(labels)))
    bars = ax.barh(y, counts, color=BLUE, edgecolor="black", linewidth=0.5)
    ax.set_yticks(y)
    ax.set_yticklabels(labels)
    ax.invert_yaxis()   # most common at the top
    ax.set_xlabel(f"Number of RCTs with an in-country IRB (N = {n_studies})")
    ax.set_title("Top study-site countries among RCTs with an in-country IRB,\n"
                 "development economics, 2021-2025")
    ax.set_xlim(0, max(counts) * 1.18)
    ax.grid(axis="x", alpha=0.3)
    ax.set_axisbelow(True)

    for b, c in zip(bars, counts):
        ax.text(b.get_width() + max(counts) * 0.012,
                b.get_y() + b.get_height() / 2,
                f"{c} ({100 * c / n_studies:.0f}%)",
                va="center", ha="left", fontsize=9)

    save(fig, "fig25_irb_incountry_top_sites")

    pretty = ", ".join(f"{iso3_names.get(k, k)}={v}" for k, v in top)
    log(f"  in-country-IRB RCTs (local_only+both) N={n_studies}; "
        f"top {TOP_N} sites: {pretty}")
    log(f"  full ranking (top 10): "
        + ", ".join(f"{iso3_names.get(k, k)}={v}"
                    for k, v in site_studies.most_common(10)))


def main():
    os.makedirs(FIG_DIR, exist_ok=True)
    with open(IN_CSV, encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    iso3_names = load_iso3_names()
    log(f"Read {len(rows)} rows from {os.path.basename(IN_CSV)}")
    fig25_incountry_top_sites(rows, iso3_names)
    log("Done.")


if __name__ == "__main__":
    main()
