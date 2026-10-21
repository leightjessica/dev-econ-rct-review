#!/usr/bin/env python3
"""Stage 21f - prepare in-session site extraction for the multi-country RCTs.

Stages 21b-21d covered the 350 single-country RCTs; the 25 rows of
data/site_extracted.csv with extract_status == "not_single_country" were set
aside by the original scoping rule (PROJECT_MEMO Section 12.3). The project lead
now wants them included with one site-row per country arm. This script builds
one batch item per (paper, study country), in the same form as the 21c RECHECK
batches (full text cut at References, the country's admissible units from the
gazetteer plus the 21b SUPPLEMENTARY units), so that subagents extract the site
separately for each country arm.

Study countries come from data/country_classified.csv (country_iso3_list). For
the 13 papers whose list is empty, the countries were read from the paper text
on 2026-10-21 and are fixed in COUNTRY_OVERRIDE below (one paper names no
country and is carried as country_unresolved); study countries missing from a
non-empty list, found by the extraction agents, are added through COUNTRY_ADD.
Countries with no World Bank
subnational coverage (matching_level NONE, or absent from the gazetteer) get a
work-list row with status country_not_covered and no batch item.

Writes, under data/_site_session/multicountry/:
  text/<key>.txt          full text (pdftotext), cut at the References heading
  batches/mc_NN.json      batch items, grouped so a paper's countries share a batch
  worklist.json           every (paper, country) row with its status, read by 21g
  out/<key>__<ISO3>.json  written by the subagents, one per (paper, country)

Env flags:
  MC_BATCH=6   papers per batch
"""

import csv
import importlib.util
import json
import os
import sys
from collections import defaultdict
from pathlib import Path

csv.field_size_limit(min(sys.maxsize, 2147483647))

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
FULLTEXT = ROOT / "fulltext"
SITE_CSV = DATA / "site_extracted.csv"
CC_CSV = DATA / "country_classified.csv"
GAZ_CSV = DATA / "wb_subnat_gazetteer.csv"
COV_CSV = DATA / "wb_subnat_coverage_profile.csv"
WORK = DATA / "_site_session" / "multicountry"

MC_BATCH = int(os.environ.get("MC_BATCH", "6"))

# Reuse the 21c helpers (doi_key, LEVEL_DESC) and through them the 21b helpers.
_spec = importlib.util.spec_from_file_location(
    "site_prep", ROOT / "scripts" / "21c_site_session_prep.py")
site_prep = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(site_prep)
site_local = site_prep.site_local

# Study countries read from the paper text (2026-10-21) where country_classified
# carries no list. Several are in fact single-country papers that the country
# classifier left without an ISO3 code; they are carried here so that every row
# set aside as not_single_country is placed.
COUNTRY_OVERRIDE = {
    "10.1257/aer.20190586": (["ETH"], "Ethiopia (country_justification)"),
    "10.1257/aer.20220471": (["ETH"], "Addis Ababa, Ethiopia"),
    "10.1257/pol.20200778": (["LBN"], "American University of Beirut, Lebanon"),
    "10.1086/717891": ([], "unnamed lower-middle-income country in Southeast Asia "
                           "(firm anonymized); country cannot be resolved"),
    "10.1086/727288": (["CHL"], "Chile's national health system"),
    "10.1093/jeea/jvae005": (["NIC"], "CCT in rural Nicaragua (Red de Proteccion Social)"),
    "10.1016/j.jdeveco.2021.102807": (["ETH"], "Ethiopia (country_justification)"),
    "10.1016/j.jdeveco.2023.103166": (["CHN"], "rural northwest China"),
    "10.1016/j.jdeveco.2024.103445": (["ETH"], "southern Ethiopia"),
    "10.1016/j.jdeveco.2024.103405": (["CHL"], "Chile (country_justification)"),
    "10.1016/j.jdeveco.2020.102613": (["UGA"], "Uganda (paper text)"),
    "10.1016/j.jdeveco.2020.102615": (["BGD"], "Bangladesh (paper text)"),
    "10.1016/j.jdeveco.2024.103315": (["CHL"], "Chile 2016 municipal elections"),
    "10.1016/j.jdeveco.2025.103687": (["ARG", "BRA", "SLV", "GTM", "MEX", "PRY", "PER"],
                                      "seven countries named in the abstract"),
}
# Study countries missing from a non-empty country_classified list, found by the
# extraction agents (2026-10-21). The listed countries are kept, so that a
# miscoded one surfaces as not_study_country rather than silently vanishing.
COUNTRY_ADD = {
    "10.1093/ej/ueae012": (["MOZ"], "Manica and Sofala sites named in the paper; "
                                    "missing from country_classified (KEN;TZA)"),
}


def load_worklist():
    """Return the work list: one dict per (paper, country), plus the paper rows."""
    with open(SITE_CSV, encoding="utf-8") as f:
        papers = [r for r in csv.DictReader(f)
                  if r["extract_status"] == "not_single_country"]
    with open(CC_CSV, encoding="utf-8") as f:
        cc = {r["doi"]: r for r in csv.DictReader(f)}
    with open(COV_CSV, encoding="utf-8") as f:
        cov = {r["iso3"]: r["matching_level"] for r in csv.DictReader(f)}
    gaz = defaultdict(list)
    with open(GAZ_CSV, encoding="utf-8") as f:
        for r in csv.DictReader(f):
            gaz[r["iso3"]].append(r["unit_name"])

    work = []
    for p in papers:
        c = cc.get(p["doi"], {})
        isos = [x.strip() for x in (c.get("country_iso3_list") or "").split(";") if x.strip()]
        source = "country_classified"
        if not isos and p["doi"] in COUNTRY_OVERRIDE:
            isos, why = COUNTRY_OVERRIDE[p["doi"]]
            source = f"paper_text: {why}"
        base = {"doi": p["doi"], "key": site_prep.doi_key(p["doi"]),
                "setting": c.get("country_study_setting", ""), "country_source": source}
        if not isos:
            work.append({**base, "iso3": "", "matching_level": "",
                         "status": "country_unresolved", "units": []})
            continue
        added, why_add = COUNTRY_ADD.get(p["doi"], ([], ""))
        for iso in isos + added:
            if iso in added:
                base = {**base, "country_source": f"paper_text: {why_add}"}
            lvl = cov.get(iso, "NONE")
            units = gaz[iso] + [n for n, _, _ in site_local.SUPPLEMENTARY.get(iso, [])]
            status = "ok" if lvl != "NONE" and gaz[iso] else "country_not_covered"
            work.append({**base, "iso3": iso, "matching_level": lvl if lvl != "NONE" else "",
                         "status": status, "units": units if status == "ok" else []})
    return papers, work


def main():
    for sub in ("text", "batches", "out"):
        (WORK / sub).mkdir(parents=True, exist_ok=True)
    pdftotext_bin = site_local.find_pdftotext()
    papers, work = load_worklist()
    meta = {p["doi"]: p for p in papers}

    by_paper = defaultdict(list)
    for w in work:
        if w["status"] == "ok":
            by_paper[w["doi"]].append(w)
    items = []
    for doi, ws in by_paper.items():
        p = meta[doi]
        key = ws[0]["key"]
        tpath = WORK / "text" / f"{key}.txt"
        if not tpath.exists():
            text = site_local.pdf_to_text(pdftotext_bin, FULLTEXT / p["matched_file"]) or ""
            tpath.write_text(f"TITLE: {p['title']}\n\n{site_local.strip_references(text)}",
                             encoding="utf-8")
        countries = [w["iso3"] for w in ws]
        for w in ws:
            items.append({
                "key": key, "doi": doi, "title": p["title"], "iso3": w["iso3"],
                "out_file": f"data/_site_session/multicountry/out/{key}__{w['iso3']}.json",
                "paper_countries": countries,
                "target_level": site_prep.LEVEL_DESC[site_local.level_kind(w["matching_level"])],
                "admissible_units": w["units"],
                "text_file": str(tpath.relative_to(ROOT)).replace("\\", "/"),
            })

    for old in (WORK / "batches").glob("mc_*.json"):
        old.unlink()
    dois = list(by_paper)
    n_batches = 0
    for i in range(0, len(dois), MC_BATCH):
        chunk = set(dois[i:i + MC_BATCH])
        n_batches += 1
        (WORK / "batches" / f"mc_{n_batches:02d}.json").write_text(
            json.dumps([it for it in items if it["doi"] in chunk],
                       ensure_ascii=False, indent=1), encoding="utf-8")
    (WORK / "worklist.json").write_text(
        json.dumps([{k: v for k, v in w.items() if k != "units"} for w in work],
                   ensure_ascii=False, indent=1), encoding="utf-8")

    st = defaultdict(int)
    for w in work:
        st[w["status"]] += 1
    print(f"papers: {len(papers)}; (paper, country) rows: {len(work)} {dict(st)}")
    print(f"batch items: {len(items)} for {len(dois)} papers in {n_batches} batches")
    for w in work:
        if w["status"] != "ok":
            print(f"   {w['status']}: {w['doi']} {w['iso3']}")


if __name__ == "__main__":
    main()
