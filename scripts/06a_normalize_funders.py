"""
Stage 6a (post-step): normalize the funder list and emit the full-text worklist.

Reads:  data/funders_6a.csv      (per-RCT funders from Stage 6a)
        data/final_dataset.csv   (bibliographic detail for the worklist)
Writes: data/funders_6a_normalized.csv          per paper, canonical funders
        data/funders_6a_summary_normalized.csv  canonical funder frequency
        data/funder_alias_map.csv               raw -> canonical, for audit
        data/rcts_need_fulltext.csv             RCTs with NO structured funder

Normalization is two layers, both intentionally transparent rather than fuzzy:

  1. Generic cleaning applied to every funder string:
       - HTML-unescape (fixes '&amp;' -> '&', '&apos;' -> "'")
       - collapse internal whitespace, strip
       - drop a leading "The "
       - strip trailing punctuation
  2. A curated alias map that unifies well-known abbreviation/full-name pairs
     and spelling variants OBSERVED IN THIS SAMPLE (e.g. USAID, NSF, ESRC,
     CEPR, NBER, MIT, UCL, World Bank, Gates Foundation). The map is applied by
     the cleaned, lower-cased key. Sub-units that are arguably distinct funders
     (e.g. "UCLA Anderson", individual NIH institutes, named J-PAL initiatives)
     are deliberately NOT merged. The full raw->canonical mapping is written to
     funder_alias_map.csv so every merge can be inspected and overridden.

Anything not in the alias map keeps its cleaned form as its own canonical name;
the long tail of singletons is therefore cleaned but not forcibly merged.
"""

import csv
import html
import os
import re
from datetime import datetime, timezone

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_DIR = os.path.abspath(os.path.join(SCRIPT_DIR, ".."))
DATA = os.path.join(PROJECT_DIR, "data")
IN_FUNDERS = os.path.join(DATA, "funders_6a.csv")
IN_FINAL = os.path.join(DATA, "final_dataset.csv")
OUT_NORM = os.path.join(DATA, "funders_6a_normalized.csv")
OUT_SUMMARY = os.path.join(DATA, "funders_6a_summary_normalized.csv")
OUT_ALIAS = os.path.join(DATA, "funder_alias_map.csv")
OUT_WORKLIST = os.path.join(DATA, "rcts_need_fulltext.csv")

# Curated alias map. Keys are the *cleaned, lower-cased* form (post HTML-unescape,
# post leading-"The" strip). Values are the canonical display name. Built from the
# distinct strings actually present in funders_6a_summary.csv.
ALIASES = {
    # World Bank
    "world bank group": "World Bank Group",
    "world bank": "World Bank Group",
    # Gates ('&amp;' is unescaped to '&' before lookup)
    "bill and melinda gates foundation": "Bill & Melinda Gates Foundation",
    "bill & melinda gates foundation": "Bill & Melinda Gates Foundation",
    "gates foundation": "Bill & Melinda Gates Foundation",
    # USAID
    "usaid": "United States Agency for International Development",
    "united states agency for international development": "United States Agency for International Development",
    # J-PAL (top-level only; named initiatives kept separate)
    "abdul latif jameel poverty action lab": "Abdul Latif Jameel Poverty Action Lab (J-PAL)",
    # NSF
    "nsf": "National Science Foundation",
    "national science foundation": "National Science Foundation",
    # NBER
    "nber": "National Bureau of Economic Research",
    "national bureau of economic research": "National Bureau of Economic Research",
    # ESRC (incl. UKRI form)
    "esrc": "Economic and Social Research Council",
    "economic and social research council": "Economic and Social Research Council",
    "uk research and innovation economic and social research council": "Economic and Social Research Council",
    # CEPR (named programmes such as PEDL kept separate)
    "cepr": "Centre for Economic Policy Research",
    "centre for economic policy research": "Centre for Economic Policy Research",
    # DfID
    "department for international development": "Department for International Development",
    "department for international development, uk government": "Department for International Development",
    # SSHRC
    "sshrc": "Social Sciences and Humanities Research Council of Canada",
    "social sciences and humanities research council of canada": "Social Sciences and Humanities Research Council of Canada",
    # IDRC
    "idrc": "International Development Research Centre",
    "international development research centre": "International Development Research Centre",
    # DFG
    "deutsche forschungsgemeinschaft": "Deutsche Forschungsgemeinschaft (DFG)",
    "german research foundation": "Deutsche Forschungsgemeinschaft (DFG)",
    # DAAD
    "deutscher akademischer austauschdienst": "Deutscher Akademischer Austauschdienst (DAAD)",
    "german academic exchange service": "Deutscher Akademischer Austauschdienst (DAAD)",
    # ILO (spelling)
    "international labour organisation": "International Labour Organization",
    "international labour organization": "International Labour Organization",
    # AusAID
    "ausaid": "Australian Agency for International Development",
    "australian agency for international development": "Australian Agency for International Development",
    # AEA
    "aea": "American Economic Association",
    "american economic association": "American Economic Association",
    # UNU-WIDER
    "unu-wider": "UNU-WIDER",
    "united nations university world institute for development economics research": "UNU-WIDER",
    # Horizon 2020
    "horizon 2020": "Horizon 2020",
    "horizon 2020 framework programme": "Horizon 2020",
    # la Caixa ('&apos;' is unescaped to "'" before lookup)
    "fundación la caixa": "la Caixa Foundation",
    "'la caixa' foundation": "la Caixa Foundation",
    # Universities (campus-level; sub-units kept separate)
    "uc berkeley": "University of California, Berkeley",
    "university of california berkeley": "University of California, Berkeley",
    "ucsd": "University of California, San Diego",
    "uc san diego": "University of California, San Diego",
    "university of california, san diego": "University of California, San Diego",
    "ucla": "University of California, Los Angeles",
    "university of california, los angeles": "University of California, Los Angeles",
    "ucl": "University College London",
    "university college london": "University College London",
    "mit": "Massachusetts Institute of Technology",
    "massachusetts institute of technology": "Massachusetts Institute of Technology",
    "nyu": "New York University",
    "new york university": "New York University",
    "university of the south": "University of the South",
    "sewanee: the university of the south": "University of the South",
}


def log(msg):
    print(msg, flush=True)


def clean_name(raw):
    """Generic-cleaning layer (see module docstring)."""
    n = html.unescape(raw or "")
    n = re.sub(r"\s+", " ", n).strip()
    n = re.sub(r"^[Tt]he\s+", "", n).strip()
    n = n.rstrip(".,;").strip()
    return n


def canonical(raw):
    """Cleaned + alias-mapped canonical name; ('', '') for empties."""
    cleaned = clean_name(raw)
    if not cleaned:
        return "", ""
    canon = ALIASES.get(cleaned.lower(), cleaned)
    return canon, cleaned


def main():
    with open(IN_FUNDERS, encoding="utf-8") as f:
        funder_rows = list(csv.DictReader(f))

    # --- Per-paper canonical funder lists -----------------------------------
    alias_seen = {}   # cleaned -> canonical (audit)
    norm_rows = []
    tally = {}        # canonical -> {"n": int, "variants": set}
    for r in funder_rows:
        raw_list = r["funders"].split(" | ") if r["funders"] else []
        seen_keys, canon_list = set(), []
        for raw in raw_list:
            canon, cleaned = canonical(raw)
            if not canon:
                continue
            alias_seen[cleaned] = canon
            k = canon.lower()
            if k in seen_keys:
                continue
            seen_keys.add(k)
            canon_list.append(canon)
        norm_rows.append({
            "doi": r["doi"],
            "journal_short": r["journal_short"],
            "publication_year": r["publication_year"],
            "title": r["title"],
            "n_funders": len(canon_list),
            "funders": " | ".join(canon_list),
            "fetch_status": r["fetch_status"],
        })
        for fn in canon_list:
            t = tally.setdefault(fn, {"n": 0, "variants": set()})
            t["n"] += 1
        # record which raw variants merged into each canonical (for summary)
        for raw in raw_list:
            canon, cleaned = canonical(raw)
            if canon and cleaned.lower() != canon.lower():
                tally.setdefault(canon, {"n": 0, "variants": set()})["variants"].add(cleaned)

    with open(OUT_NORM, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["doi", "journal_short", "publication_year",
                                          "title", "n_funders", "funders", "fetch_status"])
        w.writeheader()
        w.writerows(norm_rows)

    # --- Canonical summary --------------------------------------------------
    summary = sorted(tally.items(), key=lambda kv: (-kv[1]["n"], kv[0].lower()))
    with open(OUT_SUMMARY, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["funder", "n_papers", "variants_merged"])
        w.writeheader()
        for name, info in summary:
            w.writerow({"funder": name, "n_papers": info["n"],
                        "variants_merged": "; ".join(sorted(info["variants"]))})

    # --- Alias map audit ----------------------------------------------------
    with open(OUT_ALIAS, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["raw_cleaned", "canonical", "merged"])
        w.writeheader()
        for cleaned in sorted(alias_seen, key=str.lower):
            canon = alias_seen[cleaned]
            w.writerow({"raw_cleaned": cleaned, "canonical": canon,
                        "merged": "yes" if cleaned.lower() != canon.lower() else ""})

    # --- Full-text worklist: RCTs with NO structured funder -----------------
    need_dois = {r["doi"] for r in norm_rows if not r["funders"]}
    with open(IN_FINAL, encoding="utf-8") as f:
        final_rows = list(csv.DictReader(f))
    final_by_doi = {(r.get("doi") or "").strip(): r for r in final_rows}

    worklist = []
    for r in norm_rows:
        if r["doi"] not in need_dois:
            continue
        fr = final_by_doi.get(r["doi"], {})
        worklist.append({
            "doi": r["doi"],
            "doi_url": f"https://doi.org/{r['doi']}" if r["doi"] else "",
            "journal_short": r["journal_short"],
            "publication_year": r["publication_year"],
            "volume": fr.get("volume", ""),
            "issue": fr.get("issue", ""),
            "first_page": fr.get("first_page", ""),
            "last_page": fr.get("last_page", ""),
            "title": r["title"],
            "authors": fr.get("authors", ""),
            "pdf_filename": f"{r['doi'].replace('/', '_')}.pdf" if r["doi"] else "",
            "pdf_collected": "",   # to be filled "yes" as PDFs are gathered
        })
    worklist.sort(key=lambda x: (x["journal_short"], x["publication_year"], x["title"].lower()))
    with open(OUT_WORKLIST, "w", newline="", encoding="utf-8") as f:
        fields = ["doi", "doi_url", "journal_short", "publication_year", "volume",
                  "issue", "first_page", "last_page", "title", "authors",
                  "pdf_filename", "pdf_collected"]
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(worklist)

    # --- Report -------------------------------------------------------------
    raw_distinct = len({clean_name(raw).lower()
                        for r in funder_rows
                        for raw in (r["funders"].split(" | ") if r["funders"] else [])
                        if clean_name(raw)})
    n_with = sum(1 for r in norm_rows if r["funders"])
    log(f"Normalized {len(norm_rows)} RCT rows.")
    log(f"Distinct funders: {raw_distinct} (cleaned) -> {len(summary)} (after alias map).")
    log(f"Papers with >=1 funder: {n_with}; need full text: {len(worklist)}.")
    log("Worklist by journal:")
    by_j = {}
    for w_ in worklist:
        by_j[w_["journal_short"]] = by_j.get(w_["journal_short"], 0) + 1
    for j, n in sorted(by_j.items(), key=lambda x: -x[1]):
        log(f"  {j:12s} {n}")
    log("Top canonical funders:")
    for name, info in summary[:15]:
        log(f"  {info['n']:4d}  {name}")
    log(f"Outputs: {os.path.basename(OUT_NORM)}, {os.path.basename(OUT_SUMMARY)}, "
        f"{os.path.basename(OUT_ALIAS)}, {os.path.basename(OUT_WORKLIST)}")


if __name__ == "__main__":
    main()
