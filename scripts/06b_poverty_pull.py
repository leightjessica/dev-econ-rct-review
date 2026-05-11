"""
Stage 6b: Pull 2021 poverty headcount and total population per LMIC.

Reads:  data/lmic_countries.csv  (Stage 0b output)
Writes: data/poverty_2021.csv

Sources
-------
1. World Bank Poverty and Inequality Platform (PIP) API
   https://api.worldbank.org/pip/v1/pip
   Provides headcount ratios at $2.15/day (2017 PPP) for 2021 (lineup
   estimates where no survey was conducted that year). Some LMICs are not
   in the 2021 release; for those we fall back to the most recent available
   year (preferring national reporting level over urban/rural).

2. World Bank Indicators API for total population (SP.POP.TOTL) in 2021
   https://api.worldbank.org/v2/country/all/indicator/SP.POP.TOTL

Output schema (data/poverty_2021.csv)
-------------------------------------
  iso3                       ISO-3166 alpha-3 country code
  name_canonical             From lmic_countries.csv
  income_group               LIC / LMC / UMC
  headcount_pct_2021         Headcount ratio (in percent) at $2.15/day 2017 PPP
  headcount_year_used        Reporting year of the headcount used (2021 if lineup, else fallback)
  headcount_reporting_level  national / urban / rural
  headcount_is_interpolated  TRUE if PIP marked the value as interpolated/extrapolated
  headcount_is_stale         TRUE if headcount_year_used < 2021 - 5 (i.e., before 2016)
  total_population_2021      From WB Indicators API
  poor_population_2021_mn    headcount * pop / 1e6, in millions
  poverty_data_missing       TRUE if no PIP data of any year is available

Replicability: standard library only.
"""

import csv
import json
import os
import sys
import time
import urllib.request
import urllib.error
from datetime import datetime, timezone

EMAIL = "J.Leight@cgiar.org"

PIP_2021_URL = "https://api.worldbank.org/pip/v1/pip?country=all&year=2021&povline=2.15&format=json"
PIP_PER_COUNTRY_URL = "https://api.worldbank.org/pip/v1/pip?country={iso3}&year=all&povline=2.15&format=json"
WB_POP_URL = "https://api.worldbank.org/v2/country/all/indicator/SP.POP.TOTL?date=2021&format=json&per_page=400"

REF_YEAR = 2021
STALE_CUTOFF = 2016  # headcount surveys older than this flagged stale

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_DIR = os.path.abspath(os.path.join(SCRIPT_DIR, ".."))
LMIC_CSV = os.path.join(PROJECT_DIR, "data", "lmic_countries.csv")
OUT_CSV = os.path.join(PROJECT_DIR, "data", "poverty_2021.csv")
LOG_TXT = os.path.join(PROJECT_DIR, "data", "06b_poverty_pull.log")


def log(msg):
    line = f"[{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%SZ')}] {msg}"
    print(line, flush=True)
    with open(LOG_TXT, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def fetch_json(url, timeout=60, retries=2):
    """GET a JSON URL with simple retry on transient timeouts / 5xx errors.

    4xx (e.g., 404 for a country with no PIP coverage) is treated as terminal
    and re-raised so the caller can log and move on.
    """
    last_err = None
    for attempt in range(retries + 1):
        req = urllib.request.Request(
            url,
            headers={"User-Agent": f"dev-rct-review (mailto:{EMAIL})"},
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.load(resp)
        except urllib.error.HTTPError as e:
            # 4xx is terminal; 5xx is transient.
            if 500 <= e.code < 600 and attempt < retries:
                last_err = e
                time.sleep(2 ** attempt)
                continue
            raise
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            last_err = e
            if attempt < retries:
                time.sleep(2 ** attempt)
                continue
            raise
    raise last_err  # unreachable, but defensive


def load_lmic():
    rows = {}
    with open(LMIC_CSV, encoding="utf-8") as f:
        for r in csv.DictReader(f):
            if r["iso3"] == "REGION":
                continue
            rows[r["iso3"]] = {
                "name_canonical": r["name_canonical"],
                "income_group": r["income_group"],
            }
    return rows


def pick_best(records):
    """Among PIP records for one country-year, prefer national over urban/rural.

    Returns the chosen dict, or None.
    """
    if not records:
        return None
    by_level = {r.get("reporting_level"): r for r in records}
    for lvl in ("national", "urban", "rural"):
        if lvl in by_level:
            return by_level[lvl]
    return records[0]


def pick_best_history(records):
    """From a year=all PIP response, pick the most recent year <= REF_YEAR.

    Falls back to the most recent year overall if none <= REF_YEAR. Within a
    chosen year, prefers national over urban/rural reporting level.
    """
    if not records:
        return None
    by_year = {}
    for r in records:
        y = r.get("reporting_year")
        if y is None:
            continue
        by_year.setdefault(int(y), []).append(r)
    years_le = sorted([y for y in by_year if y <= REF_YEAR], reverse=True)
    if years_le:
        return pick_best(by_year[years_le[0]])
    # No year <= REF_YEAR; use the earliest year available (rare).
    years_all = sorted(by_year.keys())
    if not years_all:
        return None
    return pick_best(by_year[years_all[0]])


def main():
    open(LOG_TXT, "w").close()
    log("Stage 6b start")

    lmic = load_lmic()
    log(f"Loaded {len(lmic)} LMIC countries")

    # --- 1. Pull PIP 2021 lineup, country=all -----------------------------
    log(f"Fetching {PIP_2021_URL}")
    pip21 = fetch_json(PIP_2021_URL)
    log(f"  PIP 2021 returned {len(pip21)} rows")

    pip_by_iso = {}  # iso3 -> {chosen record, year used}
    grouped = {}
    for r in pip21:
        iso3 = r.get("country_code")
        if not iso3:
            continue
        grouped.setdefault(iso3, []).append(r)
    for iso3, recs in grouped.items():
        chosen = pick_best(recs)
        if chosen is None:
            continue
        pip_by_iso[iso3] = chosen

    log(f"  Distinct ISO3 with 2021 PIP estimate: {len(pip_by_iso)}")
    log(f"  LMICs covered by PIP 2021: {sum(1 for c in pip_by_iso if c in lmic)}")

    # --- 2. Per-country fallback for LMICs missing from 2021 lineup -------
    missing = [iso3 for iso3 in lmic if iso3 not in pip_by_iso]
    log(f"  LMICs missing from PIP 2021 lineup: {len(missing)}")
    fallback_results = {}
    for k, iso3 in enumerate(missing, 1):
        url = PIP_PER_COUNTRY_URL.format(iso3=iso3)
        try:
            data = fetch_json(url, timeout=60, retries=2)
        except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, OSError) as e:
            log(f"  [{k}/{len(missing)}] {iso3}: fetch error {type(e).__name__}: {e}")
            continue
        if not data:
            log(f"  [{k}/{len(missing)}] {iso3}: no PIP history at all")
            continue
        chosen = pick_best_history(data)
        if chosen is None:
            log(f"  [{k}/{len(missing)}] {iso3}: history present but no usable row")
            continue
        fallback_results[iso3] = chosen
        # Polite rate-limit; PIP returns quickly but we don't want to hammer.
        time.sleep(0.1)
    log(f"  Fallback succeeded for {len(fallback_results)} of {len(missing)} missing LMICs")

    # Combined poverty records, with a tag for whether it came from the
    # 2021 lineup or a fallback.
    poverty = {}
    for iso3, rec in pip_by_iso.items():
        poverty[iso3] = (rec, "lineup_2021")
    for iso3, rec in fallback_results.items():
        poverty[iso3] = (rec, "fallback")

    # --- 3. Pull WB SP.POP.TOTL for 2021 ----------------------------------
    log(f"Fetching {WB_POP_URL}")
    wb_pop_resp = fetch_json(WB_POP_URL)
    if not isinstance(wb_pop_resp, list) or len(wb_pop_resp) < 2:
        raise SystemExit("Unexpected WB Indicators API response shape")
    pop_records = wb_pop_resp[1] or []
    pop_by_iso = {}
    for r in pop_records:
        iso3 = r.get("countryiso3code")
        val = r.get("value")
        if iso3 and val is not None:
            pop_by_iso[iso3] = val
    log(f"  Population records loaded for {len(pop_by_iso)} ISO3 codes")
    missing_pop = [iso3 for iso3 in lmic if iso3 not in pop_by_iso]
    if missing_pop:
        log(f"  LMICs missing 2021 population data: {len(missing_pop)} -> {missing_pop}")

    # --- 4. Assemble output -----------------------------------------------
    out_rows = []
    for iso3 in sorted(lmic):
        info = lmic[iso3]
        row = {
            "iso3": iso3,
            "name_canonical": info["name_canonical"],
            "income_group": info["income_group"],
            "headcount_pct_2021": "",
            "headcount_year_used": "",
            "headcount_reporting_level": "",
            "headcount_is_interpolated": "",
            "headcount_is_stale": "",
            "total_population_2021": "",
            "poor_population_2021_mn": "",
            "poverty_data_missing": "",
        }
        pop = pop_by_iso.get(iso3)
        if pop is not None:
            row["total_population_2021"] = f"{int(pop)}"
        if iso3 in poverty:
            rec, source = poverty[iso3]
            headcount = rec.get("headcount")
            year_used = int(rec.get("reporting_year") or 0)
            level = rec.get("reporting_level") or ""
            interp = bool(rec.get("is_interpolated"))
            stale = year_used < STALE_CUTOFF
            row["headcount_pct_2021"] = f"{100.0 * headcount:.4f}" if headcount is not None else ""
            row["headcount_year_used"] = str(year_used)
            row["headcount_reporting_level"] = level
            row["headcount_is_interpolated"] = "TRUE" if interp else "FALSE"
            row["headcount_is_stale"] = "TRUE" if stale else "FALSE"
            if headcount is not None and pop is not None:
                row["poor_population_2021_mn"] = f"{headcount * pop / 1e6:.4f}"
            row["poverty_data_missing"] = "FALSE"
        else:
            row["poverty_data_missing"] = "TRUE"
        out_rows.append(row)

    fields = list(out_rows[0].keys())
    with open(OUT_CSV, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows(out_rows)
    log(f"Wrote {len(out_rows)} rows -> {OUT_CSV}")
    n_missing = sum(1 for r in out_rows if r["poverty_data_missing"] == "TRUE")
    n_stale = sum(1 for r in out_rows if r["headcount_is_stale"] == "TRUE")
    n_interp = sum(1 for r in out_rows if r["headcount_is_interpolated"] == "TRUE")
    log(f"Summary: {n_missing} missing poverty data; {n_stale} stale (<{STALE_CUTOFF}); {n_interp} interpolated 2021 lineup values")
    log("Stage 6b complete.")


if __name__ == "__main__":
    main()
