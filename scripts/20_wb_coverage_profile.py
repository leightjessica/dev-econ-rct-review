#!/usr/bin/env python3
"""Stage 20 - profile the World Bank AM25 subnational data and build a per-country
gazetteer, restricted to the countries that host our RCT sample.

This is the deterministic prerequisite to Stage 21 (LLM site extraction). It
answers the question we could not answer a priori -- at what geographic level does
the WB AM25 release actually resolve each country, and does that vary across our
RCT study countries -- and it emits the controlled vocabulary of admissible unit
names that Stage 21 hands to the model so extracted sites map straight onto
WB-canonical names.

Key facts about the AM25 SPID file (World Bank Poverty Global Practice, Fall 2025
vintage) that drive the logic here:

  * Every geographic row carries a `geo_code` of the form
        {ISO3}_{vintage}_{LEVELTAG}_{id}       e.g. AGO_2015_GAUL1_399
    where field 3 (LEVELTAG) names the shapefile admin level:
        GAUL1 / GADM1 / NUTS2 / NSO1 ...  -> first subnational level (admin-1)
        GAUL2 / GADM2 / NUTS3            -> second subnational level (admin-2)
        GAULx / GADMx / NUTSx / UNx      -> a SURVEY GROUPING of several admin-1
                                            units, i.e. COARSER than admin-1
    and a human-readable `sample` name (e.g. "Benguela").
  * The SPID `Data` sheet is disaggregated both geographically AND by the
    `data`/`data_group` demographic dimension (agegroup3, Gender, ALL). Only the
    rows with data == 'ALL' are the geography-only rows we match to, so we filter
    to those.
  * There are multiple survey years (`rep_year`, 1989-2024) per country; we record
    the full set here and let Stage 21 pick the year nearest each study's
    fieldwork.

"Finest usable level" (matching_level) is defined as the level that actually
TILES the country -- operationally, the LEVELTAG with the most distinct units --
NOT the numerically-deepest tag. This matters because several countries carry a
single stray admin-2 unit (e.g. a separately-identified capital district) atop a
full admin-1 partition; picking the deepest tag would wrongly flip such a country
to admin-2. Taking the max-unit level yields admin-1 for those, and correctly
yields admin-2 for countries the WB genuinely tiles at the district level
(Malawi, Rwanda, Sierra Leone, Sri Lanka, Philippines), and the coarse grouping
for countries offered only at GAULx (Uganda -> 4 macro-regions, Uruguay,
Argentina, Vietnam, ...).

Inputs:
  data/country_classified.csv                    (RCT study countries)
  "World Bank subnational data/AM25_-_SPID_data.xlsx"

Outputs:
  data/wb_subnat_coverage_profile.csv   one row per RCT country: matching_level,
                                        unit count, all levels present, year span
  data/wb_subnat_gazetteer.csv          one row per (country, unit) at the
                                        matching level: iso3, level, geo_code, name

Dependency: openpyxl (already pinned for the workbook-reading stages).
"""

import csv
import re
from collections import Counter, defaultdict
from pathlib import Path

import openpyxl

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
CC_CSV = DATA / "country_classified.csv"
SPID_XLSX = ROOT / "World Bank subnational data" / "AM25_-_SPID_data.xlsx"
PROFILE_CSV = DATA / "wb_subnat_coverage_profile.csv"
GAZ_CSV = DATA / "wb_subnat_gazetteer.csv"

# LEVELTAGs that denote a survey grouping coarser than a single admin-1 unit.
GROUPED_TAGS = {"GAULx", "GADMx", "NUTSx", "UNx"}


def leveltag(geo_code):
    """Field 3 of the geo_code, e.g. AGO_2015_GAUL1_399 -> 'GAUL1'."""
    parts = (geo_code or "").split("_")
    return parts[2] if len(parts) > 2 else "NA"


def level_num(tag):
    """Numeric rank of a LEVELTAG for finest-selection tie handling.
    Grouped ('x') sits between national (0) and admin-1 (1)."""
    m = re.match(r"[A-Z]+([0-9x])", tag or "")
    if not m:
        return -1
    d = m.group(1)
    return int(d) if d.isdigit() else 0  # 'x' -> 0 (coarser than admin-1)


def load_rct_countries():
    """iso3 -> count of RCTs whose study setting names that country."""
    rct = Counter()
    with open(CC_CSV, encoding="utf-8") as f:
        for row in csv.DictReader(f):
            if row["rct_classification"] != "yes":
                continue
            for iso in (row["country_iso3_list"] or "").split(";"):
                iso = iso.strip()
                if iso:
                    rct[iso] += 1
    return rct


def main():
    rct = load_rct_countries()
    print(f"RCT study countries: {len(rct)}  (RCT-country mentions: {sum(rct.values())})")

    wb = openpyxl.load_workbook(SPID_XLSX, read_only=True, data_only=True)
    ws = wb["Data"]
    rows = ws.iter_rows(values_only=True)
    hdr = list(next(rows))
    I = {h: i for i, h in enumerate(hdr)}

    # per country: {leveltag: {geo_code: sample_name}}, and the set of years.
    prof = defaultdict(lambda: {"lvl_units": defaultdict(dict), "years": set()})
    for r in rows:
        if r[I["data"]] != "ALL":
            continue
        c = r[I["code"]]
        if c not in rct:
            continue
        tag = leveltag(r[I["geo_code"]])
        p = prof[c]
        p["lvl_units"][tag][r[I["geo_code"]]] = r[I["sample"]]
        p["years"].add(int(r[I["rep_year"]]))
    wb.close()

    prof_rows = []
    gaz_rows = []
    for iso, n in rct.most_common():
        if iso not in prof:
            prof_rows.append([iso, n, "NONE", "", 0, "", ""])
            continue
        lu = prof[iso]["lvl_units"]
        # matching level = the level that tiles the country = most distinct units
        match_lvl = max(lu, key=lambda t: len(lu[t]))
        n_units = len(lu[match_lvl])
        note = "grouped(coarser than admin1)" if match_lvl in GROUPED_TAGS else ""
        levels_present = ";".join(
            f"{t}:{len(lu[t])}" for t in sorted(lu, key=lambda t: -len(lu[t]))
        )
        yrs = sorted(prof[iso]["years"])
        prof_rows.append([iso, n, match_lvl, note, n_units, levels_present,
                          f"{yrs[0]}-{yrs[-1]} ({len(yrs)}y)"])
        for gc, name in sorted(lu[match_lvl].items(), key=lambda x: (x[1] or "")):
            gaz_rows.append([iso, match_lvl, gc, name])

    with open(PROFILE_CSV, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["iso3", "n_rcts", "matching_level", "note", "n_units",
                    "levels_present", "years"])
        w.writerows(prof_rows)
    with open(GAZ_CSV, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["iso3", "matching_level", "geo_code", "unit_name"])
        w.writerows(gaz_rows)

    covered = sum(1 for r in prof_rows if r[2] != "NONE")
    grouped = sum(1 for r in prof_rows if r[3])
    admin2 = sum(1 for r in prof_rows if level_num(r[2]) >= 2)
    print(f"Wrote {PROFILE_CSV.name}: {len(prof_rows)} countries "
          f"({covered} covered, {grouped} grouped-only, {admin2} admin-2, "
          f"{len(prof_rows)-covered} not covered)")
    print(f"Wrote {GAZ_CSV.name}: {len(gaz_rows)} unit rows")


if __name__ == "__main__":
    main()
