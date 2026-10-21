#!/usr/bin/env python3
"""Stage 21g - merge the multi-country site results into data/site_extracted_multicountry.csv.

Reads the Stage-21f work list (data/_site_session/multicountry/worklist.json; one
entry per (paper, study country)) and the subagent outputs written against the
21f batches (data/_site_session/multicountry/out/<key>__<ISO3>.json), validates
every matched unit against that country's admissible list, and writes one row
per (paper, country) with exactly the column layout of data/site_extracted.csv
(the 21d FIELDS), so that Stage 22 can stack the two files.

Row conventions:
  iso3 / matching_level / level_kind   the country arm, not the paper
  extract_route    "multicountry"
  extract_status   ok / country_not_covered (no WB subnational data, e.g. CHN) /
                   country_unresolved (paper names no country; iso3 blank) /
                   not_study_country (listed upstream, but the agent found no
                   experiment there) / not_run / parse_error / bad_scope
  agent_note       prefixed with the source of the country list where it was
                   read from the paper text (21f COUNTRY_OVERRIDE)
Fieldwork years are paper-level and are copied from data/site_fieldwork_years.csv
by DOI (Stage 21e), as in 21d; blank where 21e has none.
"""

import csv
import importlib.util
import json
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

csv.field_size_limit(min(sys.maxsize, 2147483647))

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
WORK = DATA / "_site_session" / "multicountry"
OUT_CSV = DATA / "site_extracted_multicountry.csv"

MODEL = "claude-code-session"
PROMPT_VERSION = "site-session-multicountry-v1"

# Reuse the 21d layout (and through it 21c / 21b) and the 21f work list.
_spec = importlib.util.spec_from_file_location(
    "site_merge", ROOT / "scripts" / "21d_site_session_merge.py")
site_merge = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(site_merge)
_spec = importlib.util.spec_from_file_location(
    "mc_prep", ROOT / "scripts" / "21f_multicountry_prep.py")
mc_prep = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mc_prep)
FIELDS = site_merge.FIELDS
SCOPES = site_merge.SCOPES
SUPPLEMENTARY = site_merge.SUPPLEMENTARY
level_kind = site_merge.site_prep.site_local.level_kind


def main():
    gaz = defaultdict(set)
    with open(site_merge.GAZ_CSV, encoding="utf-8") as f:
        for r in csv.DictReader(f):
            gaz[r["iso3"]].add(r["unit_name"])
    with open(mc_prep.SITE_CSV, encoding="utf-8") as f:
        papers = {r["doi"]: r for r in csv.DictReader(f)
                  if r["extract_status"] == "not_single_country"}
    work = json.loads((WORK / "worklist.json").read_text(encoding="utf-8"))
    years = {}
    if site_merge.YEARS_CSV.exists():
        with open(site_merge.YEARS_CSV, encoding="utf-8") as f:
            years = {r["doi"]: r for r in csv.DictReader(f)}

    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    rows, missing = [], []
    for w in work:
        p = papers[w["doi"]]
        lvl = w["matching_level"]
        o = {k: "" for k in FIELDS}
        o.update({k: p[k] for k in ("doi", "journal_short", "publication_year",
                                    "title", "matched_file")})
        y = years.get(w["doi"], {})
        src_note = "" if w["country_source"] == "country_classified" else \
            f"[country from {w['country_source']}] "
        o.update(iso3=w["iso3"], country_setting=w["setting"], matching_level=lvl,
                 level_kind=level_kind(lvl) if lvl else "",
                 llm_model=MODEL, llm_prompt_version=PROMPT_VERSION, snapshot_utc=stamp,
                 extract_route="multicountry", agent_note=src_note.strip(),
                 fieldwork_year_start=y.get("fieldwork_year_start", ""),
                 fieldwork_year_end=y.get("fieldwork_year_end", ""),
                 fieldwork_year_source=y.get("fieldwork_year_source", ""))
        if w["status"] != "ok":
            o["extract_status"] = w["status"]
            rows.append(o)
            continue
        path = WORK / "out" / f"{w['key']}__{w['iso3']}.json"
        if not path.exists():
            o["extract_status"] = "not_run"
            missing.append(f"{w['doi']} {w['iso3']}")
            rows.append(o)
            continue
        try:
            res = json.loads(path.read_text(encoding="utf-8-sig"))
        except json.JSONDecodeError:
            o["extract_status"] = "parse_error"
            rows.append(o)
            continue
        supp = {n: lv for n, lv, _ in SUPPLEMENTARY.get(w["iso3"], [])}
        admissible = gaz[w["iso3"]] | set(supp)
        units = [str(u).strip() for u in res.get("matched_units") or [] if str(u).strip()]
        valid = [u for u in units if u in admissible]
        invalid = [u for u in units if u not in admissible]
        scope = res.get("site_scope", "")
        status = "ok" if scope in SCOPES else "bad_scope"
        if res.get("country_is_study_site") is False:
            status = "not_study_country"
        o.update(
            site_scope=scope if scope in SCOPES else "",
            matched_units=";".join(valid), n_units_named=len(valid),
            unit_levels=";".join(supp.get(u, lvl) for u in valid),
            invalid_units=";".join(invalid),
            raw_site_text=(res.get("raw_site_text") or "")[:500],
            extract_confidence=res.get("confidence", ""),
            extract_justification=(res.get("justification") or "")[:500],
            extract_status=status,
            agent_note=(src_note + (res.get("note") or ""))[:500].strip(),
        )
        rows.append(o)

    with open(OUT_CSV, "w", newline="", encoding="utf-8") as f:
        wr = csv.DictWriter(f, fieldnames=FIELDS)
        wr.writeheader()
        wr.writerows(rows)

    ok = [r for r in rows if r["extract_status"] == "ok"]
    print(f"papers: {len({r['doi'] for r in rows})}; (paper, country) rows: {len(rows)}")
    print(f"extract_status: {dict(Counter(r['extract_status'] for r in rows))}")
    print(f"site_scope (ok, n={len(ok)}): {dict(Counter(r['site_scope'] for r in ok))}")
    print(f"confidence (ok): {dict(Counter(r['extract_confidence'] for r in ok))}")
    placed = {r["doi"] for r in ok if r["site_scope"] in ("single_region", "multi_region")}
    print(f"papers with >=1 placed country arm: {len(placed)}")
    bad = [r for r in ok if r["invalid_units"]]
    print(f"rows with invalid unit names dropped: {len(bad)}")
    for r in bad:
        print(f"   {r['doi']} {r['iso3']} {r['invalid_units']!r}")
    for r in rows:
        if r["extract_status"] not in ("ok",):
            print(f"   {r['extract_status']}: {r['doi']} {r['iso3']}")
    if missing:
        print(f"not yet run: {len(missing)}")
    print(f"Wrote {OUT_CSV} ({len(rows)} rows)")


if __name__ == "__main__":
    main()
