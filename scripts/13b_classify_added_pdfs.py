#!/usr/bin/env python3
"""Stage 13b - incrementally scan + classify the few RCTs that were `no_pdf`,
now that their full-text PDFs have been added to fulltext/.

Motivation: the main Stage-12 scan (12_irb_extract.py) re-reads ALL ~403 PDFs and
takes ~40 minutes. When only a handful of previously-missing PDFs have been added,
that is wasteful. This script does the minimal incremental update WITHOUT re-running
the whole pipeline:

  1. Read data/irb_mentions.csv and collect the DOIs still marked `no_pdf` (the
     targets) and the set of PDF filenames already matched to some RCT.
  2. The newly-added PDFs are exactly the files in fulltext/ NOT already matched.
     Sniff each one's DOI from its first pages (same logic as Stage 12) and keep
     only those whose DOI is one of the `no_pdf` targets.
  3. Scan each matched target PDF with Stage 12's own scan_pages() (imported, not
     re-implemented) so the snippet format is byte-identical to a full run.
  4. Run the LOCAL rule-based extraction (imported from 13_irb_classify_local.py)
     and the deterministic study-vs-IRB comparison on each, exactly as the full
     local pass would.
  5. Patch the affected rows IN PLACE in BOTH data/irb_mentions.csv and
     data/irb_classified.csv. Every other row is rewritten unchanged.

It imports 12_irb_extract.py and 13_irb_classify_local.py by file path (their
names start with a digit, so a normal `import` is impossible). Importing runs only
their top-level definitions; neither has import-time side effects, and main() is
guarded by `if __name__ == "__main__"`.

Idempotent: re-running simply re-resolves the (now fewer) no_pdf targets. A target
whose PDF still cannot be found stays `no_pdf`.

Run (no API key needed):
  py scripts\\13b_classify_added_pdfs.py
"""

import csv
import importlib.util
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
FULLTEXT = ROOT / "fulltext"
MENTIONS_CSV = DATA / "irb_mentions.csv"
CLASSIFIED_CSV = DATA / "irb_classified.csv"


def load_by_path(path, modname):
    spec = importlib.util.spec_from_file_location(modname, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


s12 = load_by_path(ROOT / "scripts" / "12_irb_extract.py", "s12_irb_extract")
s13 = load_by_path(ROOT / "scripts" / "13_irb_classify_local.py", "s13_irb_local")


def main():
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    # --- 1. read existing mentions; find no_pdf targets + already-used files ---
    with open(MENTIONS_CSV, encoding="utf-8") as f:
        mention_rows = list(csv.DictReader(f))
    targets = {r["doi"] for r in mention_rows if r.get("extraction_status") == "no_pdf"}
    used_files = {r["matched_file"] for r in mention_rows if (r.get("matched_file") or "").strip()}
    if not targets:
        print("No RCTs are currently marked no_pdf; nothing to do.")
        return
    print(f"no_pdf targets to resolve: {len(targets)}")
    for d in sorted(targets):
        print(f"  - {d}")

    # --- 2. candidate PDFs = files on disk not already matched to an RCT --------
    candidates = [p for p in sorted(FULLTEXT.glob("*.pdf")) if p.name not in used_files]
    print(f"Unused PDFs on disk to inspect: {len(candidates)}")

    # --- 3. sniff DOI of each candidate; match to a target; scan it ------------
    resolved = {}   # doi -> (filename, pages)
    for p in candidates:
        if set(resolved) >= targets:
            break
        pages = s12.read_pages(p)
        if not pages:
            continue
        head = s12.collapse_ws("\n".join(pages[: s12.DOI_PAGES]))
        for m in s12.DOI_RE.finditer(head):
            cand = s12.norm_doi(m.group(0))
            if cand in targets and cand not in resolved:
                resolved[cand] = (p.name, pages)
                print(f"  matched {cand}  <-  {p.name}")
                break
    missing = targets - set(resolved)
    if missing:
        print(f"WARNING: still no PDF found for {len(missing)}: {sorted(missing)}")

    # --- 4. study countries (for the deterministic comparison) -----------------
    study = s13.load_study_countries()

    # --- 5. build the updated mention + classified rows for resolved targets ---
    new_mentions = {}     # doi -> dict in s12.FIELDS
    new_classified = {}   # doi -> dict in s13.FIELDS
    for doi, (fname, pages) in resolved.items():
        terms, n_total, pages_hit, snippets = s12.scan_pages(pages)
        status = "ok" if terms else "no_mention"
        snippet_str = "  ||  ".join(snippets)
        new_mentions[doi] = {
            "matched_file": fname, "n_pages": len(pages),
            "has_irb_mention": str(bool(terms)),
            "terms_matched": " | ".join(sorted(terms)),
            "n_mentions": n_total,
            "pages_with_mentions": ";".join(str(x) for x in pages_hit),
            "snippets": snippet_str, "extraction_status": status,
            "snapshot_utc": stamp,
        }
        setting, study_iso = study.get(doi, ("", set()))
        cl = {"study_setting": setting, "study_iso3_list": ";".join(sorted(study_iso)),
              "irb_status": status, "irb_terms_matched": " | ".join(sorted(terms)),
              "snapshot_utc": stamp}
        if status == "ok":
            names, iso_set, approval = s13.extract_local(snippet_str)
            cls, in_study, identified = s13.classify_location(iso_set, study_iso)
            cl.update({
                "irb_institutions": " ; ".join(names),
                "irb_iso3_list": ";".join(sorted(iso_set)),
                "irb_country_identified": "TRUE" if identified else "FALSE",
                "irb_in_study_country": "TRUE" if in_study else "FALSE",
                "irb_location_class": cls, "irb_n_institutions": str(len(names)),
                "irb_approval_status": approval,
                "irb_justification": (f"Rule-based match: {', '.join(names)}" if names
                                      else "Rule-based: IRB/ethics text present but no dictionary institution matched"),
                "irb_llm_model": s13.MODEL_STAMP, "irb_llm_prompt_version": s13.PROMPT_STAMP,
            })
        else:  # no_mention
            cl.update({"irb_location_class": "no_mention", "irb_country_identified": "",
                       "irb_in_study_country": "", "irb_n_institutions": "",
                       "irb_institutions": "", "irb_iso3_list": "",
                       "irb_approval_status": "", "irb_justification": "",
                       "irb_llm_model": "", "irb_llm_prompt_version": ""})
        new_classified[doi] = cl
        print(f"  -> {doi}: status={status}  class={cl.get('irb_location_class')}  "
              f"irb={cl.get('irb_iso3_list','')}  ({cl.get('irb_institutions','')[:60]})")

    if not new_mentions:
        print("No targets resolved; files unchanged.")
        return

    # --- 6. patch irb_mentions.csv in place ------------------------------------
    for r in mention_rows:
        if r["doi"] in new_mentions:
            r.update(new_mentions[r["doi"]])
    with open(MENTIONS_CSV, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=s12.FIELDS)
        w.writeheader()
        w.writerows(mention_rows)
    print(f"Patched {len(new_mentions)} row(s) in {MENTIONS_CSV.name}")

    # --- 7. patch irb_classified.csv in place ----------------------------------
    with open(CLASSIFIED_CSV, encoding="utf-8") as f:
        cls_rows = list(csv.DictReader(f))
    for r in cls_rows:
        if r["doi"] in new_classified:
            r.update(new_classified[r["doi"]])
    with open(CLASSIFIED_CSV, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=s13.FIELDS)
        w.writeheader()
        w.writerows(cls_rows)
    print(f"Patched {len(new_classified)} row(s) in {CLASSIFIED_CSV.name}")


if __name__ == "__main__":
    main()
