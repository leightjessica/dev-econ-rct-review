#!/usr/bin/env python3
"""Stage 22 - attach World Bank subnational poverty indicators to each RCT site.

Reads the site extraction (data/site_extracted.csv, one row per single-country
RCT; plus data/site_extracted_multicountry.csv, one row per paper x country, when
the multi-country pass has produced it) and the World Bank AM25 release
(`World Bank subnational data/`: SPID poverty/inequality and the subnational MPM),
and writes, for every site row, the indicators of its matched unit(s) in the
survey nearest to the trial's fieldwork, together with the site's position within
that survey's regional distribution.

Unit resolution. Units are matched on (iso3, SPID `sample` name, level tag): the
gazetteer units at the country's Stage-20 matching level, and the 21b
SUPPLEMENTARY units at their own level (e.g. Pakistan's "Punjab & Islamabad",
GAULx). Matching on name and tag rather than on geo_code is deliberate: the same
region carries different geo_codes across survey vintages.

Survey and year selection (PROJECT_MEMO Section 12.3). The reference year is the
fieldwork START year (Stage 21e), since long-run follow-ups carry spans of a decade
or more and the start year marks when the intervention reached these places; if
no year is known, publication year minus SIX (the median registry lag, Section
12.8), flagged year_rule = "pub_minus_lag". Among SPID surveys (code, year,
survname) containing at least one of the site's units, we take the one covering
the most of them, then the nearest year to the reference year, then the earlier
year (conditions at or before the intervention). All units of a multi-region site
are therefore read from one survey.

Relative position. SPID carries no national rows and no population weights, but
each survey's geography-only rows tile the country (mixing levels where the WB
does, e.g. Pakistan's two GAUL1 provinces plus two GAULx groupings), so each site
is compared with the other regions of the same survey: an UNWEIGHTED percentile
rank of the site's $3.00 headcount (0 = least poor region, 1 = poorest) and its gap
from the unweighted regional mean. Multi-region sites take the unweighted mean of
their units. Population weighting is not possible with this release; this is a
limitation to report.

MPM. The subnational MPM indicators are attached for the same units from the MPM
survey nearest the reference year (ties to the earlier year), separately, since
MPM survey years need not coincide with SPID's.

Inputs:  data/site_extracted.csv, data/site_extracted_multicountry.csv (optional),
         data/wb_subnat_gazetteer.csv, World Bank subnational data/*.xlsx
Outputs: data/site_wb_merged.csv        one row per site row (paper x country)
         data/site_wb_merged_units.csv  one row per matched unit (audit)
"""

import csv
import importlib.util
import statistics
import sys
from collections import Counter, defaultdict
from pathlib import Path

import openpyxl

csv.field_size_limit(min(sys.maxsize, 2147483647))

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
WB = ROOT / "World Bank subnational data"
SPID_XLSX = WB / "AM25_-_SPID_data.xlsx"
MPM_XLSX = WB / "AM25_-_Subnational_MPM.xlsx"
SITES = [DATA / "site_extracted.csv", DATA / "site_extracted_multicountry.csv"]
GAZ_CSV = DATA / "wb_subnat_gazetteer.csv"
OUT_CSV = DATA / "site_wb_merged.csv"
UNITS_CSV = DATA / "site_wb_merged_units.csv"

PUB_LAG = 6

# Boundary changes. Telangana was carved out of Andhra Pradesh in June 2014; the
# SPID carries Telangana only from 2022, so a pre-2014 Telangana site reads the
# undivided state. After 2014 the residual Andhra Pradesh appears under GAULx.
# (iso3, unit) -> (cutoff ref year, unit used before the cutoff)
BEFORE_SPLIT = {("IND", "Telangana"): (2014, "Andhra Pradesh")}
# (iso3, unit) -> additional SPID level tags carrying the same unit
ALT_TAGS = {("IND", "Andhra Pradesh"): ["GAULx"]}
PLACED = {"single_region", "multi_region"}
SPID_VARS = ["poor300", "poor420", "poor830", "mean2021", "prosgap2021", "gini", "theil"]
MPM_VARS = ["dep_poor1", "dep_educ_com", "dep_educ_enr", "dep_infra_elec",
            "dep_infra_imps", "dep_infra_impw", "mdpoor_i1"]

_spec = importlib.util.spec_from_file_location(
    "site_local", ROOT / "scripts" / "21b_site_match_local.py")
site_local = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(site_local)

FIELDS = (
    ["doi", "journal_short", "publication_year", "title", "iso3", "matching_level",
     "level_kind", "site_scope", "matched_units", "n_units", "extract_route",
     "extract_confidence", "fieldwork_year_start", "fieldwork_year_end",
     "fieldwork_year_source", "ref_year", "year_rule", "merge_status",
     "units_unmatched", "spid_year", "spid_survname", "spid_year_gap",
     "spid_units_found"]
    + SPID_VARS
    + ["survey_n_regions", "survey_mean_poor300", "site_minus_survey_mean_poor300",
       "site_rank_pct_poor300", "site_rank_pct_mean2021",
       "mpm_year", "mpm_survname", "mpm_units_found"] + MPM_VARS
)
UNIT_FIELDS = (["doi", "iso3", "unit", "level", "spid_year", "spid_survname",
                "geo_code"] + SPID_VARS + ["rank_pct_poor300", "mpm_year"] + MPM_VARS)


def tag_of(geo_code):
    parts = (geo_code or "").split("_")
    return parts[2] if len(parts) > 2 else ""


def num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def load_sheet(path):
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    rows = wb["Data"].iter_rows(values_only=True)
    hdr = list(next(rows))
    out = [dict(zip(hdr, r)) for r in rows]
    wb.close()
    return out


def pct_rank(value, values):
    """Unweighted percentile rank in [0, 1]: share of OTHER regions with a lower
    value, ties counted half. None if the survey has a single region."""
    others = [v for v in values if v is not None]
    if value is None or len(others) < 2:
        return None
    below = sum(1 for v in others if v < value)
    ties = sum(1 for v in others if v == value) - 1
    return (below + 0.5 * ties) / (len(others) - 1)


def fmt(v, nd=4):
    return "" if v is None else (f"{v:.{nd}f}" if isinstance(v, float) else v)


def main():
    print("Reading SPID and MPM workbooks ...")
    spid = [r for r in load_sheet(SPID_XLSX) if r.get("data") == "ALL"]
    mpm = load_sheet(MPM_XLSX)
    print(f"  SPID geography-only rows: {len(spid)}  MPM rows: {len(mpm)}")

    # surveys: (code, year, survname) -> list of rows
    surveys = defaultdict(list)
    for r in spid:
        surveys[(r["code"], int(r["year"]), r["survname"])].append(r)
    # (iso3, sample, tag) -> {survey_key: row}
    spid_idx = defaultdict(dict)
    for key, rows in surveys.items():
        for r in rows:
            spid_idx[(r["code"], r["sample"], tag_of(r["geo_code"]))][key] = r
    mpm_idx = defaultdict(list)
    for r in mpm:
        if r.get("year") is not None:
            mpm_idx[(r["code"], r["sample"], tag_of(r["geo_code"]))].append(r)

    gaz_level = {}
    with open(GAZ_CSV, encoding="utf-8") as f:
        for r in csv.DictReader(f):
            gaz_level[(r["iso3"], r["unit_name"])] = r["matching_level"]
    for iso, lst in site_local.SUPPLEMENTARY.items():
        for name, lvl, _ in lst:
            gaz_level[(iso, name)] = lvl

    sites = []
    for path in SITES:
        if path.exists():
            with open(path, encoding="utf-8") as f:
                rows = list(csv.DictReader(f))
            print(f"  {path.name}: {len(rows)} rows")
            sites += rows

    out, unit_rows = [], []
    for s in sites:
        o = {k: s.get(k, "") for k in FIELDS}
        units = [u for u in (s.get("matched_units") or "").split(";") if u]
        o["n_units"] = len(units)
        if s.get("extract_status") != "ok":
            o["merge_status"] = f"not_extracted:{s.get('extract_status', '')}"
            out.append(o)
            continue
        if s.get("site_scope") not in PLACED or not units:
            o["merge_status"] = f"not_placed:{s.get('site_scope', '')}"
            out.append(o)
            continue

        iso = s["iso3"]
        try:
            ref = int(s.get("fieldwork_year_start") or 0) or None
        except ValueError:
            ref = None
        if ref:
            o["year_rule"] = "fieldwork_start"
        else:
            ref = int(s["publication_year"]) - PUB_LAG
            o["year_rule"] = "pub_minus_lag"
        o["ref_year"] = ref

        units = list(dict.fromkeys(
            BEFORE_SPLIT[(iso, u)][1] if (iso, u) in BEFORE_SPLIT
            and ref < BEFORE_SPLIT[(iso, u)][0] else u for u in units))
        keys = {}
        for u in units:
            tag = gaz_level.get((iso, u), s.get("matching_level", ""))
            keys[u] = [(iso, u, t) for t in [tag] + ALT_TAGS.get((iso, u), [])]
        by_unit = {u: {sk: r for k in ks for sk, r in spid_idx.get(k, {}).items()}
                   for u, ks in keys.items()}
        unmatched = [u for u in units if not by_unit[u]]
        o["units_unmatched"] = ";".join(unmatched)
        cover = Counter()
        for u in units:
            for sk in by_unit[u]:
                cover[sk] += 1
        if not cover:
            o["merge_status"] = "no_spid_match"
            out.append(o)
            continue
        best = min(cover, key=lambda sk: (-cover[sk], abs(sk[1] - ref), sk[1]))
        srows = surveys[best]
        o.update(spid_year=best[1], spid_survname=best[2], spid_year_gap=best[1] - ref,
                 spid_units_found=cover[best])
        all_p300 = [num(r["poor300"]) for r in srows]
        all_mean = [num(r["mean2021"]) for r in srows]
        survey_mean = statistics.mean([v for v in all_p300 if v is not None]) \
            if any(v is not None for v in all_p300) else None

        found, ranks, ranks_m = [], [], []
        for u, ks in keys.items():
            r = by_unit[u].get(best)
            k = ks[0] if r is None else (iso, u, tag_of(r["geo_code"]))
            m_rows = [m for kk in ks for m in mpm_idx.get(kk, [])]
            m = min(m_rows, key=lambda x: (abs(int(x["year"]) - ref), int(x["year"]))) \
                if m_rows else None
            if r is None:
                continue
            rk = pct_rank(num(r["poor300"]), all_p300)
            rkm = pct_rank(num(r["mean2021"]), all_mean)
            found.append((r, m))
            if rk is not None:
                ranks.append(rk)
            if rkm is not None:
                ranks_m.append(rkm)
            unit_rows.append({
                "doi": s["doi"], "iso3": iso, "unit": u, "level": k[2],
                "spid_year": best[1], "spid_survname": best[2], "geo_code": r["geo_code"],
                **{v: fmt(num(r[v])) for v in SPID_VARS},
                "rank_pct_poor300": fmt(rk, 3),
                "mpm_year": m["year"] if m else "",
                **{v: fmt(num(m[v])) if m else "" for v in MPM_VARS},
            })

        def avg(vals):
            vals = [v for v in vals if v is not None]
            return statistics.mean(vals) if vals else None

        for v in SPID_VARS:
            o[v] = fmt(avg([num(r[v]) for r, _ in found]))
        p300 = avg([num(r["poor300"]) for r, _ in found])
        o.update(
            survey_n_regions=len(srows),
            survey_mean_poor300=fmt(survey_mean),
            site_minus_survey_mean_poor300=fmt(p300 - survey_mean)
            if p300 is not None and survey_mean is not None else "",
            site_rank_pct_poor300=fmt(avg(ranks), 3),
            site_rank_pct_mean2021=fmt(avg(ranks_m), 3),
        )
        mfound = [m for _, m in found if m]
        if mfound:
            o.update(mpm_year=";".join(sorted({str(m["year"]) for m in mfound})),
                     mpm_survname=";".join(sorted({str(m["survname"]) for m in mfound})),
                     mpm_units_found=len(mfound))
            for v in MPM_VARS:
                o[v] = fmt(avg([num(m[v]) for m in mfound]))
        o["n_units"] = len(units)
        o["merge_status"] = "ok" if not unmatched and cover[best] == len(units) \
            else "partial"
        out.append(o)

    with open(OUT_CSV, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        w.writerows(out)
    with open(UNITS_CSV, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=UNIT_FIELDS)
        w.writeheader()
        w.writerows(unit_rows)

    st = Counter(r["merge_status"].split(":")[0] for r in out)
    print(f"merge_status: {dict(st)}")
    ok = [r for r in out if r["merge_status"] in ("ok", "partial")]
    print(f"year_rule: {dict(Counter(r['year_rule'] for r in ok))}")
    gaps = sorted(abs(int(r["spid_year_gap"])) for r in ok)
    if gaps:
        print(f"|survey year - ref year|: median {gaps[len(gaps)//2]}, "
              f"p90 {gaps[9*len(gaps)//10]}, max {gaps[-1]}")
    rk = [float(r["site_rank_pct_poor300"]) for r in ok if r["site_rank_pct_poor300"]]
    if rk:
        print(f"site percentile rank of $3.00 headcount within survey (0=least poor): "
              f"n={len(rk)}, mean {statistics.mean(rk):.3f}, median {statistics.median(rk):.3f}, "
              f"share above 0.5: {sum(1 for x in rk if x > 0.5)/len(rk):.2f}")
    print(f"MPM attached: {sum(1 for r in ok if r['mpm_year'])}")
    for r in out:
        if r["merge_status"] in ("no_spid_match", "partial"):
            print(f"  {r['merge_status']}: {r['iso3']} {r['doi']} unmatched={r['units_unmatched']!r} "
                  f"found={r['spid_units_found']}/{r['n_units']}")
    print(f"Wrote {OUT_CSV} ({len(out)} rows), {UNITS_CSV} ({len(unit_rows)} rows)")


if __name__ == "__main__":
    main()
