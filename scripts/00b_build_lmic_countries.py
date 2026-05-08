"""
Stage 0b (bootstrap): Build the LIC/LMIC/UMIC country-name lookup from the
World Bank's official income-classification API and write
`data/lmic_countries.csv` for Stage 2 to consume.

Why this exists
---------------
The development-paper inclusion rule for Stage 2 includes a country-mention
check: a paper is "development" if its title or abstract mentions any country
the World Bank classifies as low-income (LIC), lower-middle-income (LMIC),
or upper-middle-income (UMIC). This script fetches the authoritative list
once, normalizes it, adds common name variants, and writes a CSV that Stage 2
loads at runtime.

By bootstrapping rather than hardcoding, the country list:
  - is reproducibly sourced (the script itself documents the data source)
  - is committed alongside the code so future runs are deterministic
  - can be regenerated when the WB updates its classification

API source
----------
https://api.worldbank.org/v2/country?format=json — returns one record per
country with an `incomeLevel` object that has the WB code (LIC, LMC, UMC, HIC)
and a display name. We keep LIC, LMC, UMC.

Output schema (data/lmic_countries.csv)
---------------------------------------
  iso3            ISO-3166 alpha-3 country code
  name_canonical  WB canonical name (e.g., "Russian Federation")
  income_group    "LIC", "LMC", or "UMC" (WB codes)
  alt_names       semicolon-separated alternate names used for matching
                  (e.g., "Russia;Russian Federation")
  match_terms     semicolon-separated terms to use as regex matchers,
                  including the canonical name and all alternates,
                  with apostrophes normalized.

Replicability: standard library only.
"""

import csv
import json
import os
import re
import urllib.request
from datetime import datetime, timezone

EMAIL = "J.Leight@cgiar.org"
WB_URL = "https://api.worldbank.org/v2/country?format=json&per_page=400"

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_DIR = os.path.abspath(os.path.join(SCRIPT_DIR, ".."))
OUT_CSV = os.path.join(PROJECT_DIR, "data", "lmic_countries.csv")
LOG_TXT = os.path.join(PROJECT_DIR, "data", "00b_build_lmic_countries.log")

# Income groups to keep. The WB codes are LIC (low), LMC (lower-middle),
# UMC (upper-middle). HIC (high) is excluded.
KEEP_INCOME = {"LIC", "LMC", "UMC"}

# Manually curated alternate names. Keyed by ISO3.
# WB's canonical names sometimes differ from journal-paper usage (e.g., the
# WB lists "Russian Federation" while papers say "Russia"). We add these
# alternates so the country-match rule catches both.
ALTERNATES = {
    "RUS": ["Russia"],
    "TUR": ["Turkey"],
    "LAO": ["Laos"],
    "VNM": ["Viet Nam"],
    "PRK": ["North Korea", "DPRK"],
    "COD": ["Democratic Republic of the Congo", "DR Congo", "DRC"],
    "COG": ["Republic of the Congo", "Congo-Brazzaville"],
    "CIV": ["Cote d'Ivoire", "Ivory Coast"],
    "EGY": ["Egypt"],
    "IRN": ["Iran"],
    "VEN": ["Venezuela"],
    "BOL": ["Bolivia"],
    "TZA": ["Tanzania"],
    "MKD": ["North Macedonia", "Macedonia"],
    "FSM": ["Federated States of Micronesia"],
    "STP": ["Sao Tome and Principe", "Sao Tome"],
    "SVK": ["Slovak Republic"],
    "SYR": ["Syria"],
    "YEM": ["Yemen"],
    "BIH": ["Bosnia"],
    "CPV": ["Cape Verde"],
    "GMB": ["Gambia", "the Gambia"],
    "BHS": ["Bahamas"],
    "KGZ": ["Kyrgyz Republic", "Kyrgyzstan"],
    "MDA": ["Moldova"],
    "MMR": ["Burma"],
    "PSE": ["West Bank", "Gaza", "Palestine", "Palestinian Territories"],
    "SLB": ["Solomon Islands"],
    "MHL": ["Marshall Islands"],
    "VCT": ["St. Vincent", "Saint Vincent"],
    "LCA": ["St. Lucia", "Saint Lucia"],
    "TLS": ["East Timor"],
}

# Region/grouping terms to add as additional matchers (always TRUE for dev).
REGION_TERMS = [
    "Sub-Saharan Africa",
    "Sub Saharan Africa",
    "developing country",
    "developing countries",
    "developing economies",
    "developing economy",
    "low-income country",
    "low-income countries",
    "low- and middle-income countries",
    "low and middle income countries",
    "LMICs",
    "LMIC",
    "emerging markets",
    "emerging economies",
    "emerging market economies",
    "the Global South",
    "Global South",
]


def log(msg):
    line = f"[{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%SZ')}] {msg}"
    print(line, flush=True)
    with open(LOG_TXT, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def normalize_apostrophes(s):
    """Convert curly apostrophes to straight, for consistent regex matching."""
    return s.replace("’", "'").replace("‘", "'")


def fetch_wb():
    req = urllib.request.Request(
        WB_URL,
        headers={"User-Agent": f"dev-rct-review-bootstrap (mailto:{EMAIL})"},
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        data = json.load(resp)
    # Response: [meta_dict, list_of_records]
    if not isinstance(data, list) or len(data) < 2:
        raise SystemExit("Unexpected WB API response shape")
    return data[1]


def main():
    open(LOG_TXT, "w").close()
    log(f"Fetching {WB_URL}")
    records = fetch_wb()
    log(f"  {len(records)} country/region records returned")

    keep = []
    for r in records:
        income = (r.get("incomeLevel") or {}).get("id")
        if income not in KEEP_INCOME:
            continue
        # WB API returns aggregate "regions" (e.g., "World") as records too;
        # the region object has id "NA" for actual countries.
        region_id = (r.get("region") or {}).get("id")
        if region_id == "NA":
            continue  # this is an aggregate, not a country
        keep.append(r)
    log(f"  {len(keep)} countries in LIC/LMC/UMC after filtering aggregates")

    rows = []
    for r in keep:
        iso3 = r.get("id", "")
        name = normalize_apostrophes((r.get("name") or "").strip())
        income = r.get("incomeLevel", {}).get("id", "")
        alts = ALTERNATES.get(iso3, [])
        match_terms = [name] + alts
        # Dedupe while preserving order
        seen = set()
        match_terms = [t for t in match_terms if not (t.lower() in seen or seen.add(t.lower()))]
        rows.append({
            "iso3": iso3,
            "name_canonical": name,
            "income_group": income,
            "alt_names": ";".join(alts),
            "match_terms": ";".join(match_terms),
        })

    # Append region terms as pseudo-rows with iso3 = REGION
    for term in REGION_TERMS:
        rows.append({
            "iso3": "REGION",
            "name_canonical": term,
            "income_group": "REGION",
            "alt_names": "",
            "match_terms": term,
        })

    rows.sort(key=lambda r: (r["iso3"], r["name_canonical"]))

    os.makedirs(os.path.dirname(OUT_CSV), exist_ok=True)
    with open(OUT_CSV, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=["iso3", "name_canonical", "income_group", "alt_names", "match_terms"],
        )
        writer.writeheader()
        writer.writerows(rows)

    # Summary by income group
    from collections import Counter
    cnt = Counter(r["income_group"] for r in rows)
    log(f"Wrote {len(rows)} rows -> {OUT_CSV}")
    for k, n in sorted(cnt.items()):
        log(f"  {k}: {n}")
    log(f"Total distinct match terms: {sum(len(r['match_terms'].split(';')) for r in rows)}")
    log("Bootstrap complete.")


if __name__ == "__main__":
    main()
