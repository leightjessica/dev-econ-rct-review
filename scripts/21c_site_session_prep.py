#!/usr/bin/env python3
"""Stage 21c - prepare in-session (subagent) site extraction batches.

The Stage-21 API path (21_site_extract.py) is blocked on credits, so the
interpretive pass is run instead inside an authenticated Claude Code session
(PROJECT_MEMO Section 12.5, "Route B"), layered on the deterministic Stage-21b
pass (Route C). This script writes the inputs the subagents read:

  data/_site_session/text/<key>.txt     title + abstract + leading TEXT_HEAD_CHARS of
                                        the paper (pdftotext), the same window
                                        Stage 21 would have sent to the API
  data/_site_session/batches/<task>_NN.json
        task "verify":  the 21b resolved_single / dominant_single rows, with the
                        proposed unit, the competing units, and the 21b snippets,
                        for confirmation or correction
        task "extract": every other ok row (multi_candidate, single_weak,
                        weak_only, no_hit), for full extraction
  Each batch item carries the country, target level, and admissible unit names
  (gazetteer plus the 21b SUPPLEMENTARY units). Subagents write one JSON per
  paper to data/_site_session/out/<key>.json; 21d merges them.

Fieldwork years are not requested in the site batches (project lead,
2026-10-20); they come from the AEA RCT Registry (Stage 21e). YEARS=1 builds a
separate batch set asking for years only, for placed papers the registry could
not date (see years()).

Env flags:
  VERIFY_BATCH=20   papers per verify batch
  EXTRACT_BATCH=12  papers per extract batch
"""

import csv
import importlib.util
import json
import os
import re
import sys
from collections import defaultdict
from pathlib import Path

csv.field_size_limit(min(sys.maxsize, 2147483647))

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
FULLTEXT = ROOT / "fulltext"
LOCAL_CSV = DATA / "site_local_matched.csv"
CC_CSV = DATA / "country_classified.csv"
GAZ_CSV = DATA / "wb_subnat_gazetteer.csv"
WORK = DATA / "_site_session"

TEXT_HEAD_CHARS = 32000
VERIFY_BATCH = int(os.environ.get("VERIFY_BATCH", "20"))
EXTRACT_BATCH = int(os.environ.get("EXTRACT_BATCH", "12"))
VERIFY_STATUSES = {"resolved_single", "dominant_single"}

# Reuse the 21b helpers (pdftotext lookup, level descriptions, SUPPLEMENTARY units).
_spec = importlib.util.spec_from_file_location(
    "site_local", ROOT / "scripts" / "21b_site_match_local.py")
site_local = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(site_local)

LEVEL_DESC = {
    "admin1": "a first-level administrative unit (state / province / region)",
    "admin2": "a second-level administrative unit (district / department / municipality)",
    "grouped": ("a World Bank survey region -- a GROUP of several districts/provinces "
                "(coarser than a single admin-1 unit); each admissible name lists the "
                "group's constituent units"),
}


def doi_key(doi):
    return re.sub(r"[^A-Za-z0-9]+", "_", doi).strip("_")[:120]


def recheck():
    """RECHECK=1: second pass over (a) every row the first pass left
    'unspecified' and (b) every row of RECHECK_BATCHES (batches whose agent
    grepped rather than read). Several first-pass agents reported that the
    leading TEXT_HEAD_CHARS window ended before the paper's setting section, so
    these batches point at the FULL text (cut at the References heading)."""
    (WORK / "text_full").mkdir(parents=True, exist_ok=True)
    pdftotext_bin = site_local.find_pdftotext()
    gaz = defaultdict(list)
    with open(GAZ_CSV, encoding="utf-8") as f:
        for r in csv.DictReader(f):
            gaz[r["iso3"]].append(r["unit_name"])
    extracted = {}
    with open(DATA / "site_extracted.csv", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            extracted[r["doi"]] = r
    redo = {d for d, r in extracted.items() if r["site_scope"] == "unspecified"}
    for b in os.environ.get("RECHECK_BATCHES", "extract_08").split(","):
        redo |= {it["doi"] for it in json.loads(
            (WORK / "batches" / f"{b.strip()}.json").read_text(encoding="utf-8"))}
    items = []
    first_pass = [p for p in sorted((WORK / "batches").glob("*_[0-9][0-9].json"))
                  if not p.name.startswith("recheck_")]
    for name in first_pass:
        for it in json.loads(name.read_text(encoding="utf-8")):
            if it["doi"] not in redo:
                continue
            r = extracted[it["doi"]]
            tpath = WORK / "text_full" / f"{it['key']}.txt"
            if not tpath.exists():
                text = site_local.pdf_to_text(
                    pdftotext_bin, FULLTEXT / r["matched_file"]) or ""
                tpath.write_text(site_local.strip_references(text), encoding="utf-8")
            units = gaz[it["iso3"]] + [
                n for n, _, _ in site_local.SUPPLEMENTARY.get(it["iso3"], [])]
            items.append({
                **{k: it[k] for k in ("key", "doi", "title", "iso3", "target_level")},
                "admissible_units": units,
                "text_file": str(tpath.relative_to(ROOT)).replace("\\", "/"),
                "first_pass": {k: r[k] for k in ("site_scope", "matched_units",
                                                 "raw_site_text", "agent_note")},
            })
    for old in (WORK / "batches").glob("recheck_*.json"):
        old.unlink()
    size = int(os.environ.get("RECHECK_BATCH", "8"))
    for i in range(0, len(items), size):
        (WORK / "batches" / f"recheck_{i // size + 1:02d}.json").write_text(
            json.dumps(items[i:i + size], ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"recheck: {len(items)} papers in {-(-len(items) // size)} batches")


def years():
    """YEARS=1: batches asking subagents for the fieldwork years of placed papers
    (single_ or multi_region, in site_extracted.csv or the multi-country file)
    whose Stage-21e year is only the 21b text hint or missing, and that have no
    answer in years_out yet. Points at the full text; answers go to data/_site_session/years_out/
    and are read by 21e as source 'paper_text'."""
    (WORK / "text_full").mkdir(parents=True, exist_ok=True)
    (WORK / "years_out").mkdir(parents=True, exist_ok=True)
    pdftotext_bin = site_local.find_pdftotext()
    rows = []
    for name in ("site_extracted.csv", "site_extracted_multicountry.csv"):
        if not (DATA / name).exists():
            continue
        with open(DATA / name, encoding="utf-8") as f:
            rows += [r for r in csv.DictReader(f)
                     if r["extract_status"] == "ok"
                     and r["site_scope"] in ("single_region", "multi_region")
                     and r["fieldwork_year_source"] in ("text_hint", "none", "")]
    items, seen = [], set()
    for r in rows:
        key = doi_key(r["doi"])
        if key in seen or (WORK / "years_out" / f"{key}.json").exists():
            continue        # one item per paper; skip papers already answered
        seen.add(key)
        tpath = WORK / "text_full" / f"{key}.txt"
        if not tpath.exists():
            text = site_local.pdf_to_text(pdftotext_bin, FULLTEXT / r["matched_file"]) or ""
            tpath.write_text(site_local.strip_references(text), encoding="utf-8")
        items.append({"key": key, "doi": r["doi"], "title": r["title"], "iso3": r["iso3"],
                      "publication_year": r["publication_year"],
                      "text_file": str(tpath.relative_to(ROOT)).replace("\\", "/")})
    for old in (WORK / "batches").glob("years_*.json"):
        old.unlink()
    size = int(os.environ.get("YEARS_BATCH", "10"))
    for i in range(0, len(items), size):
        (WORK / "batches" / f"years_{i // size + 1:02d}.json").write_text(
            json.dumps(items[i:i + size], ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"years: {len(items)} papers in {-(-len(items) // size)} batches")


def main():
    if os.environ.get("RECHECK"):
        return recheck()
    if os.environ.get("YEARS"):
        return years()
    for sub in ("text", "batches", "out"):
        (WORK / sub).mkdir(parents=True, exist_ok=True)
    pdftotext_bin = site_local.find_pdftotext()

    abstracts = {}
    with open(CC_CSV, encoding="utf-8") as f:
        for r in csv.DictReader(f):
            abstracts[r["doi"]] = r.get("abstract", "")
    gaz = defaultdict(list)
    with open(GAZ_CSV, encoding="utf-8") as f:
        for r in csv.DictReader(f):
            gaz[r["iso3"]].append(r["unit_name"])
    with open(LOCAL_CSV, encoding="utf-8") as f:
        rows = [r for r in csv.DictReader(f) if r["extract_status"] == "ok"]

    verify, extract = [], []
    for r in rows:
        key = doi_key(r["doi"])
        tpath = WORK / "text" / f"{key}.txt"
        if not tpath.exists():
            text = site_local.pdf_to_text(pdftotext_bin, FULLTEXT / r["matched_file"]) or ""
            tpath.write_text(
                f"TITLE: {r['title']}\n\nABSTRACT:\n{abstracts.get(r['doi'], '') or '(none)'}"
                f"\n\nPAPER TEXT (leading pages):\n{text[:TEXT_HEAD_CHARS]}\n",
                encoding="utf-8")
        units = list(gaz[r["iso3"]])
        units += [n for n, _, _ in site_local.SUPPLEMENTARY.get(r["iso3"], [])]
        item = {
            "key": key, "doi": r["doi"], "title": r["title"], "iso3": r["iso3"],
            "target_level": LEVEL_DESC[r["level_kind"]],
            "admissible_units": units,
            "text_file": str(tpath.relative_to(ROOT)).replace("\\", "/"),
        }
        if r["local_status"] in VERIFY_STATUSES:
            item.update(proposed_unit=r["top_unit"], local_status=r["local_status"],
                        units_found=r["all_units"], snippets=r["snippets"])
            verify.append(item)
        else:
            item.update(local_status=r["local_status"], units_found=r["all_units"])
            extract.append(item)

    for task, items, size in (("verify", verify, VERIFY_BATCH),
                              ("extract", extract, EXTRACT_BATCH)):
        for old in (WORK / "batches").glob(f"{task}_*.json"):
            old.unlink()
        for i in range(0, len(items), size):
            path = WORK / "batches" / f"{task}_{i // size + 1:02d}.json"
            path.write_text(json.dumps(items[i:i + size], ensure_ascii=False, indent=1),
                            encoding="utf-8")
        print(f"{task}: {len(items)} papers in {-(-len(items) // size)} batches")


if __name__ == "__main__":
    main()
