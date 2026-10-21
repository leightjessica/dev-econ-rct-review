#!/usr/bin/env python3
"""Stage 21e - fieldwork years for each RCT, for the Stage-22 survey-year match.

Stage 22 attaches, for each study site, the World Bank SPID survey year nearest
to the trial's fieldwork period (PROJECT_MEMO Section 12.3). The in-session site
extraction (21c/21d) deliberately did not ask for years (project lead,
2026-10-20); they are taken here from the AEA RCT Registry instead, which records
structured trial and intervention dates for registered trials.

Sources, in order of preference, per RCT:
  1. aea_registry   the trial's AEA registry entry, found via the AEARCTR ids that
                    Stage 24 detected in the paper (data/prereg_classified.csv).
                    fieldwork_year_start = year of "Intervention start date"
                    (else "Start date"); fieldwork_year_end = year of
                    "Intervention end date" (else "End date"). When a paper cites
                    several AEA ids (8 papers), the span covers all of them and
                    n_aea_ids records the count, since some of those cite a
                    companion trial rather than their own.
                    Registry exports are read in REGISTRY_EXPORTS order; the first
                    export containing an id wins, so list the most recent first.
  1b. aea_title_match  for papers that print no AEA id (many state "registered
                    in the AEA RCT Registry" without the number, and some do not
                    mention registration at all), the paper is matched to a
                    registry entry on title-word overlap plus author overlap with
                    the registered PIs (match_registry below). Accepted when an
                    author is a registered PI and the title Jaccard is >=
                    TITLE_MIN_WITH_AUTHOR, or the Jaccard alone is >=
                    TITLE_MIN_ALONE; every accepted and near-miss candidate is
                    written to data/aea_title_matches.csv for review, and ids in
                    TITLE_MATCH_REJECT (hand-checked false matches) are excluded.
  1c. paper_text    for placed papers the registry could not date, fieldwork
                    years read from the full text by in-session subagents (21c
                    YEARS=1; data/_site_session/years_out/<key>.json).
  2. text_hint      the deterministic Stage-21b hint (calendar years near
                    "baseline" / "endline" / "data collection" in the paper text;
                    data/site_local_matched.csv). Noisier; flagged as such.
  3. none           left blank; Stage 22 falls back on the publication year.
Sanity flag: year_flag = "start_after_publication" when the start year exceeds
the publication year (a registry typo or a mismatched id).

Inputs:
  data/prereg_classified.csv, data/site_local_matched.csv
  data/trials.csv   AEA RCT Registry full export (REGISTRY_EXPORTS)
Output:
  data/site_fieldwork_years.csv   one row per RCT; 21d fills the fieldwork-year
                                  columns of data/site_extracted.csv from it, so
                                  run 21e before 21d.

Env flags:
  AEA_EXPORTS="path1;path2"   override REGISTRY_EXPORTS (most recent first)
"""

import csv
import json
import os
import re
import sys
import unicodedata
from collections import Counter
from pathlib import Path

csv.field_size_limit(min(sys.maxsize, 2147483647))

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
PREREG_CSV = DATA / "prereg_classified.csv"
LOCAL_CSV = DATA / "site_local_matched.csv"
OUT_CSV = DATA / "site_fieldwork_years.csv"

# AEA RCT Registry export, downloaded into the project (2026-10-21; 12,932 trials).
# Inputs must live inside the project folder (PROJECT_MEMO Section 3).
REGISTRY_EXPORTS = [DATA / "trials.csv"]
if os.environ.get("AEA_EXPORTS"):
    REGISTRY_EXPORTS = [Path(p) for p in os.environ["AEA_EXPORTS"].split(";") if p.strip()]

FINAL_CSV = DATA / "final_dataset.csv"
YEARS_OUT = DATA / "_site_session" / "years_out"
MATCH_CSV = DATA / "aea_title_matches.csv"

TITLE_MIN_WITH_AUTHOR = 0.25
TITLE_MIN_ALONE = 0.60
REVIEW_MIN = 0.15
# Hand review of data/aea_title_matches.csv (2026-10-21) against registry country,
# PIs, and dates. REJECT: automatic matches that are a different trial, or a
# follow-up registered years after the paper's fieldwork.
TITLE_MATCH_REJECT = {
    ("10.1086/724320", "AEARCTR-0005548"),          # Tanzania soft skills vs India paper
    ("10.1162/rest_a_01041", "AEARCTR-0011919"),    # US 2023 migration trial vs Comoros
    ("10.1016/j.jdeveco.2023.103215", "AEARCTR-0003995"),  # KLPS 2018-21 follow-up
    ("10.1162/rest.a.275", "AEARCTR-0001402"),      # different team-production trial
    ("10.1257/aer.20220720", "AEARCTR-0012455"),    # 2023-25 follow-up registration
    ("10.1162/rest_a_01487", "AEARCTR-0019911"),    # unrelated (mental health care)
}
# ACCEPT: below-threshold candidates confirmed as the same trial (an author is a
# registered PI; same country and intervention; title reworded).
TITLE_MATCH_ACCEPT = {
    ("10.1016/j.jdeveco.2023.103056", "AEARCTR-0006855"),
    ("10.1257/app.20210248", "AEARCTR-0000285"),
    ("10.1257/app.20230672", "AEARCTR-0003232"),
    ("10.1093/jeea/jvab002", "AEARCTR-0001156"),
    ("10.1016/j.jdeveco.2022.102911", "AEARCTR-0002701"),
    ("10.1016/j.jdeveco.2023.103051", "AEARCTR-0004504"),
    ("10.1086/734133", "AEARCTR-0003812"),
    ("10.1016/j.jdeveco.2021.102718", "AEARCTR-0004708"),
    ("10.1016/j.jdeveco.2021.102780", "AEARCTR-0000388"),
    ("10.1016/j.jdeveco.2021.102807", "AEARCTR-0001642"),
    ("10.3982/ecta19408", "AEARCTR-0001937"),
    ("10.1016/j.jdeveco.2023.103227", "AEARCTR-0010307"),
    ("10.1257/aer.20210687", "AEARCTR-0012696"),
    ("10.3982/ecta17497", "AEARCTR-0000415"),
    ("10.1093/jeea/jvab007", "AEARCTR-0001175"),
    ("10.1016/j.jdeveco.2022.102893", "AEARCTR-0001170"),
    ("10.1257/aeri.20220696", "AEARCTR-0001948"),
    ("10.1016/j.jdeveco.2022.102984", "AEARCTR-0008999"),
    ("10.1016/j.jdeveco.2024.103407", "AEARCTR-0005642"),
    ("10.1093/jeea/jvab052", "AEARCTR-0000991"),
}

STOP = set("""a an the of in on and or for to from with by at as is are be its their
this that these those evidence experimental experiment field randomized randomised
trial controlled rct study impact impacts effect effects case new how do does can
when why what""".split())

AEA_RE = re.compile(r"AEARCTR-\d+")
YEAR_RE = re.compile(r"\b(19\d\d|20\d\d)\b")

FIELDS = [
    "doi", "publication_year", "aea_ids", "n_aea_ids", "aea_ids_found",
    "registry_export", "reg_start_date", "reg_end_date",
    "reg_intervention_start", "reg_intervention_end",
    "fieldwork_year_start", "fieldwork_year_end", "fieldwork_year_source",
    "year_flag",
]


def norm_id(raw):
    """AEA ids are AEARCTR- plus 7 digits; papers occasionally print an extra
    leading zero (e.g. AEARCTR-00011095)."""
    return f"AEARCTR-{int(raw.split('-')[1]):07d}"


def year_of(s):
    m = YEAR_RE.search(s or "")
    return int(m.group(1)) if m else None


def load_registry():
    """id -> (row, export name); earlier exports take precedence."""
    reg = {}
    for path in REGISTRY_EXPORTS:
        if not path.exists():
            print(f"  (registry export not found, skipped: {path})")
            continue
        n = 0
        with open(path, encoding="utf-8", errors="replace") as f:
            for r in csv.DictReader(f):
                rid = (r.get("RCT_ID") or "").strip()
                if AEA_RE.fullmatch(rid):
                    rid = norm_id(rid)
                if rid and rid not in reg:
                    reg[rid] = (r, path.name)
                    n += 1
        print(f"  {path.name}: {n} new trial ids")
    return reg


def words(s):
    s = unicodedata.normalize("NFKD", s or "")
    s = "".join(c for c in s if not unicodedata.combining(c)).lower()
    return {w for w in re.findall(r"[a-z]+", s) if len(w) > 2 and w not in STOP}


def surnames(s):
    """Surnames from 'A B; C D' (paper) or 'Name email; Name () Affil' (registry)."""
    out = set()
    for part in re.split(r";", s or ""):
        part = re.sub(r"\S+@\S+|\(.*?\).*$", "", part).strip()
        toks = re.findall(r"[^\W\d_]+", unicodedata.normalize("NFKD", part))
        if toks:
            out.add("".join(c for c in toks[-1] if not unicodedata.combining(c)).lower())
    return out


def match_registry(reg, papers):
    """Best registry candidate per paper by title Jaccard, with an author check.
    Returns doi -> (aea_id, jaccard, author_hit, status)."""
    index = []
    for rid, (r, _) in reg.items():
        pis = surnames(r.get("Primary Investigator", "")) | surnames(
            r.get("Other Primary Investigators", ""))
        index.append((rid, words(r.get("Title", "")), pis, r.get("Title", "")))
    out, log_rows = {}, []
    for doi, title, authors in papers:
        tw, au = words(title), surnames(authors)
        best = None
        for rid, rw, pis, rtitle in index:
            if not rw:
                continue
            j = len(tw & rw) / len(tw | rw)
            hit = bool(au & pis)
            score = j + (0.3 if hit else 0)
            if j >= REVIEW_MIN and (best is None or score > best[0]):
                best = (score, rid, j, hit, rtitle)
        if not best:
            continue
        _, rid, j, hit, rtitle = best
        ok = ((hit and j >= TITLE_MIN_WITH_AUTHOR) or j >= TITLE_MIN_ALONE) \
            or (doi, rid) in TITLE_MATCH_ACCEPT
        if (doi, rid) in TITLE_MATCH_REJECT:
            status = "rejected"
        else:
            status = "accepted" if ok else "review"
        out[doi] = (rid, j, hit, status)
        log_rows.append({"doi": doi, "paper_title": title, "aea_id": rid,
                         "registry_title": rtitle, "title_jaccard": f"{j:.2f}",
                         "author_is_pi": int(hit), "status": status})
    with open(MATCH_CSV, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["doi", "paper_title", "aea_id", "registry_title",
                                          "title_jaccard", "author_is_pi", "status"])
        w.writeheader()
        w.writerows(sorted(log_rows, key=lambda r: (r["status"], r["title_jaccard"]),
                           reverse=True))
    return out


def years_from(recs):
    starts = [year_of(r["Intervention start date"]) or year_of(r["Start date"]) for r in recs]
    ends = [year_of(r["Intervention end date"]) or year_of(r["End date"]) for r in recs]
    starts = [y for y in starts if y]
    ends = [y for y in ends if y]
    return (min(starts) if starts else "",
            max(ends) if ends else (min(starts) if starts else ""))


def main():
    reg = load_registry()
    meta = {}
    with open(FINAL_CSV, encoding="utf-8") as f:
        for r in csv.DictReader(f):
            meta[r["doi"]] = (r["title"], r["authors"])
    hints = {}
    with open(LOCAL_CSV, encoding="utf-8") as f:
        for r in csv.DictReader(f):
            hints[r["doi"]] = (r.get("fieldwork_year_start", ""), r.get("fieldwork_year_end", ""))

    rows = []
    paper_years = {}
    for path in YEARS_OUT.glob("*.json") if YEARS_OUT.exists() else []:
        try:
            d = json.loads(path.read_text(encoding="utf-8-sig"))
        except json.JSONDecodeError:
            print(f"  (unparseable years file skipped: {path.name})")
            continue
        paper_years[d.get("doi", "")] = (d.get("fieldwork_year_start") or "",
                                         d.get("fieldwork_year_end") or "")
    with open(PREREG_CSV, encoding="utf-8") as f:
        prereg = list(csv.DictReader(f))
    ids_of = {p["doi"]: sorted({norm_id(i) for i in AEA_RE.findall(p.get("registry_ids", ""))})
              for p in prereg}
    unlinked = [(d, *meta.get(d, ("", ""))) for d, ids in ids_of.items()
                if not any(i in reg for i in ids)]
    title_matches = match_registry(reg, unlinked)

    for p in prereg:
            ids = ids_of[p["doi"]]
            found = [i for i in ids if i in reg]
            o = {k: "" for k in FIELDS}
            o.update(doi=p["doi"], publication_year=p["publication_year"],
                     aea_ids=";".join(ids), n_aea_ids=len(ids),
                     aea_ids_found=";".join(found))
            source = "aea_registry"
            tm = title_matches.get(p["doi"])
            if not found and tm and tm[3] == "accepted":
                found, source = [tm[0]], "aea_title_match"
                o["aea_ids_found"] = tm[0]
            if found:
                recs = [reg[i][0] for i in found]
                y0, y1 = years_from(recs)
                o.update(
                    registry_export=";".join(sorted({reg[i][1] for i in found})),
                    reg_start_date=";".join(r["Start date"] for r in recs),
                    reg_end_date=";".join(r["End date"] for r in recs),
                    reg_intervention_start=";".join(r["Intervention start date"] for r in recs),
                    reg_intervention_end=";".join(r["Intervention end date"] for r in recs),
                    fieldwork_year_start=y0, fieldwork_year_end=y1,
                    fieldwork_year_source=source if y0 else "",
                )
            pt = paper_years.get(p["doi"])
            if not o["fieldwork_year_source"] and pt and pt[0]:
                o.update(fieldwork_year_start=pt[0], fieldwork_year_end=pt[1] or pt[0],
                         fieldwork_year_source="paper_text")
            if not o["fieldwork_year_source"]:
                hs, he = hints.get(p["doi"], ("", ""))
                if hs:
                    o.update(fieldwork_year_start=hs, fieldwork_year_end=he or hs,
                             fieldwork_year_source="text_hint")
                else:
                    o["fieldwork_year_source"] = "none"
            try:
                if o["fieldwork_year_start"] and int(o["fieldwork_year_start"]) > int(p["publication_year"]):
                    o["year_flag"] = "start_after_publication"
            except ValueError:
                pass
            rows.append(o)

    with open(OUT_CSV, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        w.writerows(rows)

    print(f"RCTs: {len(rows)}  with AEA ids: {sum(1 for r in rows if r['aea_ids'])}  "
          f"ids found in exports: {sum(1 for r in rows if r['aea_ids_found'])}")
    missing = sorted({i for r in rows for i in r["aea_ids"].split(";") if i}
                     - {i for r in rows for i in r["aea_ids_found"].split(";") if i})
    print(f"AEA ids not in any export ({len(missing)}): {' '.join(missing)}")
    print(f"fieldwork_year_source: {dict(Counter(r['fieldwork_year_source'] for r in rows))}")
    print(f"flags: {dict(Counter(r['year_flag'] for r in rows if r['year_flag']))}")
    lag = [int(r["publication_year"]) - int(r["fieldwork_year_start"]) for r in rows
           if r["fieldwork_year_source"] == "aea_registry" and r["fieldwork_year_start"]]
    if lag:
        lag.sort()
        print(f"publication lag (pub year - registry start year), n={len(lag)}: "
              f"median {lag[len(lag)//2]}, p10 {lag[len(lag)//10]}, p90 {lag[9*len(lag)//10]}")
    print(f"Wrote {OUT_CSV} ({len(rows)} rows)")


if __name__ == "__main__":
    main()
