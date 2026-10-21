#!/usr/bin/env python3
"""Stage 21d - merge in-session (subagent) site results into data/site_extracted.csv.

Reads the Stage-21b deterministic pass (data/site_local_matched.csv) and the
per-paper subagent outputs written against the Stage-21c batches
(data/_site_session/out/<key>.json), validates every matched unit against the
paper's admissible list, and writes data/site_extracted.csv with the Stage-21
column layout, so that Stage 22 reads the same file whichever route produced it.
Rows are stamped llm_model="claude-code-session" and
llm_prompt_version="site-session-v1" so they are never confused with an API run.

Extra columns (appended after the Stage-21 layout):
  extract_route     verify (21b proposal checked by a subagent) / extract (full
                    subagent extraction) / recheck (second pass over the full text
                    for rows the first pass left unspecified, or whose agent only
                    grepped; 21c RECHECK=1) / passthrough
  verify_verdict    confirmed / corrected (verify route only)
  local_status      the Stage-21b status, for audit
  unit_levels       WB level tag of each matched unit (differs from
                    matching_level for the 21b SUPPLEMENTARY units)
  invalid_units     subagent unit names NOT in the admissible list (dropped)
  agent_note        subagent flag for issues outside the schema (e.g. the paper's
                    study country looks miscoded upstream)

Fieldwork-year columns are filled from data/site_fieldwork_years.csv (Stage
21e: AEA RCT Registry dates, else the 21b text hint) when that file exists, with
fieldwork_year_source recording which; run 21e before 21d.
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
LOCAL_CSV = DATA / "site_local_matched.csv"
GAZ_CSV = DATA / "wb_subnat_gazetteer.csv"
WORK = DATA / "_site_session"
OUT_CSV = DATA / "site_extracted.csv"

MODEL = "claude-code-session"
PROMPT_VERSION = "site-session-v1"

_spec = importlib.util.spec_from_file_location(
    "site_prep", ROOT / "scripts" / "21c_site_session_prep.py")
site_prep = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(site_prep)
SUPPLEMENTARY = site_prep.site_local.SUPPLEMENTARY

FIELDS = [
    "doi", "journal_short", "publication_year", "title", "matched_file",
    "iso3", "country_setting", "matching_level", "level_kind",
    "site_scope", "matched_units", "raw_site_text", "n_units_named",
    "fieldwork_year_start", "fieldwork_year_end",
    "extract_confidence", "extract_justification", "extract_status",
    "llm_model", "llm_prompt_version", "snapshot_utc",
    "extract_route", "verify_verdict", "local_status", "unit_levels", "invalid_units",
    "agent_note", "fieldwork_year_source",
]
YEARS_CSV = DATA / "site_fieldwork_years.csv"
SCOPES = {"single_region", "multi_region", "nationwide", "unspecified"}


def main():
    gaz = defaultdict(set)
    with open(GAZ_CSV, encoding="utf-8") as f:
        for r in csv.DictReader(f):
            gaz[r["iso3"]].add(r["unit_name"])
    with open(LOCAL_CSV, encoding="utf-8") as f:
        local = list(csv.DictReader(f))
    years = {}
    if YEARS_CSV.exists():
        with open(YEARS_CSV, encoding="utf-8") as f:
            years = {r["doi"]: r for r in csv.DictReader(f)}

    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    rows, missing = [], []
    for r in local:
        o = {k: r.get(k, "") for k in FIELDS if k in r}
        o.update(llm_model=MODEL, llm_prompt_version=PROMPT_VERSION, snapshot_utc=stamp,
                 local_status=r["local_status"])
        y = years.get(r["doi"], {})
        o.update(fieldwork_year_start=y.get("fieldwork_year_start", ""),
                 fieldwork_year_end=y.get("fieldwork_year_end", ""),
                 fieldwork_year_source=y.get("fieldwork_year_source", ""))
        if r["extract_status"] != "ok":
            o["extract_route"] = "passthrough"
            rows.append(o)
            continue
        path = WORK / "out" / f"{site_prep.doi_key(r['doi'])}.json"
        if not path.exists():
            o["extract_status"] = "not_run"
            missing.append(r["doi"])
            rows.append(o)
            continue
        try:
            res = json.loads(path.read_text(encoding="utf-8-sig"))
        except json.JSONDecodeError:
            o["extract_status"] = "parse_error"
            rows.append(o)
            continue
        supp = {n: lv for n, lv, _ in SUPPLEMENTARY.get(r["iso3"], [])}
        admissible = gaz[r["iso3"]] | set(supp)
        units = [str(u).strip() for u in res.get("matched_units") or [] if str(u).strip()]
        valid = [u for u in units if u in admissible]
        invalid = [u for u in units if u not in admissible]
        scope = res.get("site_scope", "")
        o.update(
            site_scope=scope if scope in SCOPES else "",
            matched_units=";".join(valid), n_units_named=len(valid),
            unit_levels=";".join(supp.get(u, r["matching_level"]) for u in valid),
            invalid_units=";".join(invalid),
            raw_site_text=(res.get("raw_site_text") or "")[:500],
            extract_confidence=res.get("confidence", ""),
            extract_justification=(res.get("justification") or "")[:500],
            extract_status="ok" if scope in SCOPES else "bad_scope",
            extract_route=res.get("task", ""),
            verify_verdict=res.get("verdict", ""),
            agent_note=(res.get("note") or "")[:500],
        )
        rows.append(o)

    with open(OUT_CSV, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        w.writerows(rows)

    ok = [r for r in rows if r["extract_status"] == "ok"]
    print(f"extract_status: {dict(Counter(r['extract_status'] for r in rows))}")
    print(f"site_scope (ok, n={len(ok)}): {dict(Counter(r['site_scope'] for r in ok))}")
    for route in ("verify", "extract", "recheck"):
        sub = [r for r in ok if r["extract_route"] == route]
        print(f"  {route:7s} n={len(sub):3d} {dict(Counter(r['site_scope'] for r in sub))}")
    ver = [r for r in ok if r["extract_route"] == "verify"]
    print(f"verify verdicts: {dict(Counter(r['verify_verdict'] for r in ver))}")
    bad = [r for r in ok if r["invalid_units"]]
    print(f"rows with invalid unit names dropped: {len(bad)}")
    for r in bad:
        print(f"   {r['iso3']} {r['invalid_units']!r}")
    if missing:
        print(f"not yet run: {len(missing)}")
    print(f"Wrote {OUT_CSV} ({len(rows)} rows)")


if __name__ == "__main__":
    main()
