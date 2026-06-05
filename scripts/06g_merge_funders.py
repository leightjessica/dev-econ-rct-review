#!/usr/bin/env python3
"""Stage 6g - merge metadata + full-text funders, harmonize names, chart top funders.

The 407 RCTs split cleanly into two disjoint groups:
  - 144 papers with >=1 funder from metadata  (data/funders_6a.csv)
  - 263 papers with no metadata funder, run through full-text extraction
    (data/funders_6f_fulltext.csv); of these, ~231 yielded funders.

This script unifies both into one per-paper funder table, applying a single
harmonization layer so that, e.g., "IGC", "International Growth Center", and
"International Growth Centre" all collapse to one canonical funder. It then
writes a funder-frequency summary and a horizontal bar chart of the most
common funders.

Harmonization is intentionally transparent (curated alias + acronym maps, not
fuzzy matching). Every raw->canonical mapping is written to
funder_alias_map_full.csv for inspection and override.

Reads:  data/funders_6a.csv, data/funders_6f_fulltext.csv
Writes: data/funders_all.csv               per-RCT canonical funders + source
        data/funders_all_summary.csv        canonical funder -> paper count
        data/funder_alias_map_full.csv       raw cleaned -> canonical (audit)
        data/figures/fig20_top_funders.{png,pdf}
"""

import csv
import html
import os
import re
from collections import Counter, defaultdict
from datetime import datetime, timezone

try:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
except ImportError:
    raise SystemExit("matplotlib is required. Run: pip install matplotlib")

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_DIR = os.path.abspath(os.path.join(SCRIPT_DIR, ".."))
DATA = os.path.join(PROJECT_DIR, "data")
FIG_DIR = os.path.join(DATA, "figures")
IN_META = os.path.join(DATA, "funders_6a.csv")
IN_FULL = os.path.join(DATA, "funders_6f_fulltext.csv")
OUT_ALL = os.path.join(DATA, "funders_all.csv")
OUT_SUMMARY = os.path.join(DATA, "funders_all_summary.csv")
OUT_ALIAS = os.path.join(DATA, "funder_alias_map_full.csv")
LOG_TXT = os.path.join(DATA, "06g_merge_funders.log")

TOP_N = 22  # bars in the figure

# Strings the extractor occasionally emits that are NOT funders.
DROP = {
    "irb", "institutional review board", "ethics committee",
    "aea rct registry", "aearctr", "n/a", "none", "not applicable",
}

# Acronyms (and short forms) -> canonical display name. Matched against the whole
# cleaned string AND against a trailing "(ACRONYM)" parenthetical.
ACRONYM = {
    "igc": "International Growth Centre",
    "nsf": "National Science Foundation",
    "nih": "National Institutes of Health",
    "esrc": "Economic and Social Research Council",
    "nber": "National Bureau of Economic Research",
    "cepr": "Centre for Economic Policy Research",
    "dfid": "FCDO",
    "dfg": "Deutsche Forschungsgemeinschaft (DFG)",
    "idrc": "International Development Research Centre",
    "fcdo": "FCDO",
    "sshrc": "Social Sciences and Humanities Research Council of Canada",
    "ipa": "Innovations for Poverty Action",
    "iadb": "Inter-American Development Bank",
    "idb": "Inter-American Development Bank",
    "pedl": "Private Enterprise Development in Low-Income Countries (PEDL)",
    "3ie": "International Initiative for Impact Evaluation (3ie)",
    "usaid": "United States Agency for International Development (USAID)",
    "nichd": "Eunice Kennedy Shriver National Institute of Child Health and Human Development",
    "sida": "Swedish International Development Cooperation Agency",
    "ausaid": "Australian Agency for International Development",
    "jsps": "Japan Society for the Promotion of Science",
    "erc": "European Research Council",
    "unicef": "UNICEF",
    "cgiar": "CGIAR",
}

# Curated long-form aliases (cleaned, lower-cased key -> canonical display).
ALIASES = {
    # World Bank
    "world bank group": "World Bank Group",
    "world bank": "World Bank Group",
    # Gates
    "bill and melinda gates foundation": "Bill & Melinda Gates Foundation",
    "bill & melinda gates foundation": "Bill & Melinda Gates Foundation",
    "gates foundation": "Bill & Melinda Gates Foundation",
    # USAID
    "united states agency for international development": "United States Agency for International Development (USAID)",
    "united states agency for international development (usaid)": "United States Agency for International Development (USAID)",
    "u.s. agency for international development": "United States Agency for International Development (USAID)",
    # NSF
    "national science foundation": "National Science Foundation",
    # J-PAL top level
    "abdul latif jameel poverty action lab": "Abdul Latif Jameel Poverty Action Lab (J-PAL)",
    "abdul latif jameel poverty action lab (j-pal)": "Abdul Latif Jameel Poverty Action Lab (J-PAL)",
    "jameel poverty action lab": "Abdul Latif Jameel Poverty Action Lab (J-PAL)",
    "j-pal": "Abdul Latif Jameel Poverty Action Lab (J-PAL)",
    "jpal": "Abdul Latif Jameel Poverty Action Lab (J-PAL)",
    # J-PAL Governance Initiative (one initiative, several spellings)
    "j-pal governance initiative": "J-PAL Governance Initiative",
    "jpal governance initiative": "J-PAL Governance Initiative",
    "governance initiative at j-pal": "J-PAL Governance Initiative",
    "governance initiative at the abdul latif jameel poverty action lab": "J-PAL Governance Initiative",
    # J-PAL Post-Primary Education Initiative
    "j-pal post-primary education initiative": "J-PAL Post-Primary Education Initiative",
    "post-primary education initiative of the jameel poverty action lab (j-pal)": "J-PAL Post-Primary Education Initiative",
    # IGC spellings
    "international growth centre": "International Growth Centre",
    "international growth center": "International Growth Centre",
    "international growth center (igc)": "International Growth Centre",
    "international growth centre (igc)": "International Growth Centre",
    # 3ie
    "international initiative for impact evaluation": "International Initiative for Impact Evaluation (3ie)",
    "international initiative for impact evaluation (3ie)": "International Initiative for Impact Evaluation (3ie)",
    # PEDL
    "private enterprise development in low-income countries": "Private Enterprise Development in Low-Income Countries (PEDL)",
    "private enterprise development in low-income countries (pedl)": "Private Enterprise Development in Low-Income Countries (PEDL)",
    "private enterprise development in low-income countries (pedl) initiative": "Private Enterprise Development in Low-Income Countries (PEDL)",
    # ESRC
    "economic and social research council": "Economic and Social Research Council",
    "uk research and innovation economic and social research council": "Economic and Social Research Council",
    # DFID
    "department for international development": "Department for International Development",
    "department for international development, uk government": "Department for International Development",
    "department for international development (dfid) of the united kingdom": "Department for International Development",
    "uk department for international development": "Department for International Development",
    "u.k. department for international development": "Department for International Development",
    # DFG
    "deutsche forschungsgemeinschaft": "Deutsche Forschungsgemeinschaft (DFG)",
    "german research foundation": "Deutsche Forschungsgemeinschaft (DFG)",
    # NBER / CEPR / IDRC / SSHRC
    "national bureau of economic research": "National Bureau of Economic Research",
    "centre for economic policy research": "Centre for Economic Policy Research",
    "international development research centre": "International Development Research Centre",
    "social sciences and humanities research council of canada": "Social Sciences and Humanities Research Council of Canada",
    # NICHD
    "national institute of child health and human development": "Eunice Kennedy Shriver National Institute of Child Health and Human Development",
    "eunice kennedy shriver national institute of child health and human development": "Eunice Kennedy Shriver National Institute of Child Health and Human Development",
    # NIH
    "national institutes of health": "National Institutes of Health",
    # IADB
    "inter-american development bank": "Inter-American Development Bank",
    # IPA
    "innovations for poverty action": "Innovations for Poverty Action",
    # Weiss Family
    "weiss family fund": "Weiss Family Fund",
    "weiss family program fund": "Weiss Family Fund",
    # FCT (Portugal) - accent + with/without second 'a'
    "fundação para a ciência e a tecnologia": "Fundação para a Ciência e a Tecnologia",
    "fundação para a ciência e tecnologia": "Fundação para a Ciência e a Tecnologia",
    # Spanish ministry
    "ministerio de economía y competitividad": "Spanish Ministry of Economy and Competitiveness",
    "spanish ministry of economy and competitiveness": "Spanish Ministry of Economy and Competitiveness",
    # AEA
    "aea": "American Economic Association",
    "american economic association": "American Economic Association",
    # Hewlett
    "hewlett foundation": "William and Flora Hewlett Foundation",
    "william and flora hewlett foundation": "William and Flora Hewlett Foundation",
    # Universities (campus-level)
    "mit": "Massachusetts Institute of Technology",
    "massachusetts institute of technology": "Massachusetts Institute of Technology",
    "nyu": "New York University",
    "new york university": "New York University",
    "uc berkeley": "University of California, Berkeley",
    "university of california berkeley": "University of California, Berkeley",
    "university of california, berkeley": "University of California, Berkeley",
}


def log(msg):
    line = f"[{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%SZ')}] {msg}"
    print(line, flush=True)
    with open(LOG_TXT, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def clean_name(raw):
    n = html.unescape(raw or "")
    n = re.sub(r"\s+", " ", n).strip()
    n = re.sub(r"^[Tt]he\s+", "", n).strip()
    n = n.rstrip(".,;").strip()
    return n


def canonical(raw):
    """Return (canonical_display, cleaned) or ('', cleaned) if dropped/empty."""
    cleaned = clean_name(raw)
    if not cleaned:
        return "", cleaned
    key = cleaned.lower()
    if key in DROP:
        return "", cleaned
    # --- Pattern rules from manual inspection (checked BEFORE the alias maps).
    #     Each collapses a whole family of surface forms onto one funder.
    if "cgiar" in key or "consortium of international agricultural research" in key:
        return "CGIAR", cleaned
    if (key.startswith("world bank") or re.search(r"\bdime\b", key)
            or "gender innovation lab" in key):
        return "World Bank Group", cleaned
    if "j-pal" in key or "jpal" in key or "jameel poverty action lab" in key:
        return "Abdul Latif Jameel Poverty Action Lab (J-PAL)", cleaned
    if (re.search(r"\b(dfid|fcdo|ukaid)\b", key)
            or "department for international development" in key
            or "foreign, commonwealth" in key or "foreign commonwealth" in key
            or "uk aid" in key or "u.k. aid" in key
            or "uk government" in key or "u.k. government" in key
            or "united kingdom government" in key
            or "government of the united kingdom" in key):
        return "FCDO", cleaned
    if "development innovation ventures" in key or re.search(r"\bdiv\b", key):
        return "United States Agency for International Development (USAID)", cleaned
    # --- Curated alias / acronym maps.
    if key in ALIASES:
        return ALIASES[key], cleaned
    if key in ACRONYM:
        return ACRONYM[key], cleaned
    # trailing "(ACRONYM)" -> map by acronym, else by the stripped long name
    m = re.search(r"\(([^)]+)\)\s*$", cleaned)
    if m:
        acr = m.group(1).strip().lower()
        if acr in ACRONYM:
            return ACRONYM[acr], cleaned
        stripped = cleaned[:m.start()].strip().lower()
        if stripped in ALIASES:
            return ALIASES[stripped], cleaned
    return cleaned, cleaned  # own canonical


def load_paper_funders(path, col="funders"):
    out = {}
    for r in csv.DictReader(open(path, encoding="utf-8")):
        raws = r[col].split(" | ") if r.get(col) else []
        out[r["doi"]] = (r, raws)
    return out


def main():
    open(LOG_TXT, "w").close()
    log("Stage 6g start: merge + harmonize funders")
    os.makedirs(FIG_DIR, exist_ok=True)

    meta = load_paper_funders(IN_META)        # 407 RCT rows; some have funders
    full = load_paper_funders(IN_FULL)        # 263 no-metadata-funder rows
    log(f"metadata rows={len(meta)}  fulltext rows={len(full)}")

    alias_audit = {}        # cleaned -> canonical
    tally = defaultdict(lambda: {"n": 0, "variants": set()})
    per_paper = []

    for doi, (r, _raws) in meta.items():
        meta_raws = r["funders"].split(" | ") if r["funders"] else []
        if meta_raws:
            source, raws = "metadata", meta_raws
        elif doi in full:
            source, raws = "fulltext", full[doi][1]
        else:
            source, raws = "none", []

        canon_list, seen = [], set()
        for raw in raws:
            canon, cleaned = canonical(raw)
            if not canon:
                continue
            alias_audit[cleaned] = canon
            if canon.lower() in seen:
                continue
            seen.add(canon.lower())
            canon_list.append(canon)
            if cleaned.lower() != canon.lower():
                tally[canon]["variants"].add(cleaned)
        for c in canon_list:
            tally[c]["n"] += 1
        per_paper.append({
            "doi": doi,
            "journal_short": r["journal_short"],
            "publication_year": r["publication_year"],
            "title": r["title"],
            "funder_source": source,
            "n_funders": len(canon_list),
            "funders": " | ".join(canon_list),
        })

    # --- per-paper output ----------------------------------------------------
    with open(OUT_ALL, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["doi", "journal_short", "publication_year",
                                          "title", "funder_source", "n_funders", "funders"])
        w.writeheader()
        w.writerows(per_paper)

    # --- summary -------------------------------------------------------------
    # CSV writes are wrapped: a locked file (e.g. open in Excel / Dropbox sync)
    # must not abort the run before the figure downstream is regenerated.
    summary = sorted(tally.items(), key=lambda kv: (-kv[1]["n"], kv[0].lower()))
    try:
        with open(OUT_SUMMARY, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["funder", "n_papers", "variants_merged"])
            for name, info in summary:
                w.writerow([name, info["n"], "; ".join(sorted(info["variants"]))])
    except PermissionError:
        log(f"  WARNING: could not write {os.path.basename(OUT_SUMMARY)} "
            f"(file locked?); continuing.")

    try:
        with open(OUT_ALIAS, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["raw_cleaned", "canonical", "merged"])
            for cleaned in sorted(alias_audit, key=str.lower):
                canon = alias_audit[cleaned]
                w.writerow([cleaned, canon, "yes" if cleaned.lower() != canon.lower() else ""])
    except PermissionError:
        log(f"  WARNING: could not write {os.path.basename(OUT_ALIAS)} "
            f"(file locked?); continuing.")

    # --- stats ---------------------------------------------------------------
    n_total = len(per_paper)
    by_source = Counter(p["funder_source"] for p in per_paper)
    n_with = sum(1 for p in per_paper if p["n_funders"] > 0)
    log(f"RCTs: {n_total}.  with >=1 funder: {n_with}  none: {n_total - n_with}")
    log(f"source mix: {dict(by_source)}")
    log(f"distinct canonical funders: {len(summary)}")
    log("Top funders:")
    for name, info in summary[:TOP_N]:
        log(f"  {info['n']:3d}  {name}")

    # --- bar chart -----------------------------------------------------------
    top = summary[:TOP_N][::-1]  # smallest at bottom for barh
    names = [n for n, _ in top]
    counts = [i["n"] for _, i in top]
    fig, ax = plt.subplots(figsize=(10, 9))
    bars = ax.barh(range(len(top)), counts, color="#2b6cb0", edgecolor="white")
    ax.set_yticks(range(len(top)))
    ax.set_yticklabels(names, fontsize=9)
    ax.set_xlabel("Number of RCTs acknowledging the funder")
    ax.set_title(f"Most common funders of development RCTs, 2021–2025\n"
                 f"(top {TOP_N} of {len(summary)} distinct funders; "
                 f"{n_with} of {n_total} RCTs report a funder)")
    for b, c in zip(bars, counts):
        ax.text(b.get_width() + 0.2, b.get_y() + b.get_height() / 2,
                str(c), va="center", fontsize=8)
    ax.set_axisbelow(True)
    ax.grid(axis="x", alpha=0.3)
    fig.tight_layout()
    for ext in ("png", "pdf"):
        fig.savefig(os.path.join(FIG_DIR, f"fig20_top_funders.{ext}"),
                    dpi=300, bbox_inches="tight")
    plt.close(fig)
    log("  wrote fig20_top_funders.png + .pdf")
    log("Done.")


if __name__ == "__main__":
    main()
