#!/usr/bin/env python3
"""Stage 24 - scan full-text PDFs for trial pre-registration and pre-analysis plans.

A new line of analysis (2026-08-03), parallel in spirit to the IRB/ethics
attribution (Stages 12-15): for every RCT with a collected full-text PDF, detect
(a) whether the trial is registered in a public trial registry, and if so which
one and under what identifier, and (b) whether the paper reports a pre-analysis
plan (PAP). The motivation is descriptive -- to document the diffusion of the
open-science / pre-specification norms (the AEA RCT Registry opened in 2013, and
pre-analysis plans have become an increasingly common expectation at the leading
outlets) across the population of published development RCTs, and how the practice
varies by journal tier and over time.

WHY THIS IS FULLY DETERMINISTIC (no LLM). Unlike the IRB location task (Stage 13),
the two quantities of interest here are self-identifying in the text:
  * A trial registration leaves a high-precision fingerprint -- a registry
    identifier of a fixed form (AEARCTR-0003555, NCT01234567, ISRCTN75964374,
    PACTR..., CTRI/YYYY/NN/NNNNNN, ACTRN..., DRKS...) or the named registry
    ("AEA RCT Registry", "socialscienceregistry.org"). These are matched by regex
    with essentially no false positives.
  * A pre-analysis plan is reported in a small, stable vocabulary ("pre-analysis
    plan", "pre-registered analysis plan", "pre-specified analysis plan").
Accordingly the headline classification is computed here in Python, and no metered
API call is required (this also sidesteps the Stage-21 API-credit blocker; see
PROJECT_MEMO Section 12.5). A keyed-LLM refinement sibling could be added later to
adjudicate the residual (as 13_irb_classify.py complements 13_irb_classify_local.py),
but is not needed for the headline.

This is a KEYWORD/REGEX SCANNER that also folds in the deterministic classification.
It reads the same full-text corpus and the DOI->PDF map already assembled for the
IRB work (Stage 12), so no new PDF matching is required.

Text extraction uses poppler `pdftotext -enc UTF-8` (as in Stage 16, chosen for its
clean UTF-8 output and speed -- a whole paper in well under a second, so the full
392-RCT pass is a few minutes rather than the ~40 the pdfplumber IRB scan took).
Page structure is recovered by splitting on the form-feed (\f) that pdftotext emits
between pages, so each match carries a page number for audit. Two scans are run per
page: a whitespace-collapsed scan (joins phrases split across line breaks) and a
"despaced" scan (all whitespace stripped, lowercased) that recovers the Elsevier/JDE
lost-inter-word-space case ("aearctr-0003555", "preanalysisplan").

Inputs:
  data/irb_mentions.csv     (DOI -> matched_file map; all 392 RCTs carry one)
  data/final_dataset.csv    (fallback RCT list if irb_mentions.csv is absent)
  fulltext/*.pdf

Output:
  data/prereg_classified.csv   one row per RCT, with the deterministic fields plus
                               audit snippets. Columns documented in FIELDS below.
  data/24_prereg_extract.log

Resumable: rerunning skips RCTs already written with status ok/pdf_error; only
rows without a prior result (or newly added PDFs) are re-read. Because resume reuses
stored snippets, a run that changes SNIPPET_PAD/MAX_SNIPPETS should first move the
existing data/prereg_classified.csv aside so every row is rebuilt at the new width.

Env flags:
  SMOKE_N=15   only process the first N matched RCTs (quick validation run)
"""

import csv
import os
import re
import shutil
import subprocess
import sys
import unicodedata
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

csv.field_size_limit(min(sys.maxsize, 2147483647))

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
FULLTEXT = ROOT / "fulltext"
IRB_MENTIONS = DATA / "irb_mentions.csv"     # DOI -> matched_file map to reuse
FINAL = DATA / "final_dataset.csv"           # fallback RCT list
OUT_CSV = DATA / "prereg_classified.csv"
LOG_TXT = DATA / "24_prereg_extract.log"

SMOKE_N = int(os.environ.get("SMOKE_N", "0"))
SNIPPET_PAD = 90         # chars of context on each side of a match
MAX_SNIPPETS = 12        # cap snippets stored per paper
PDFTOTEXT_TIMEOUT = 120  # seconds

# --- Trial-registry patterns -------------------------------------------------
# Each entry: canonical registry name -> compiled regex over the flat (ws-collapsed)
# text. The match's normalized form (see norm_reg_id) is stored as an identifier.
REGISTRY_ID_PATTERNS = {
    "AEA RCT Registry": re.compile(r"AEARCTR[-\u2010-\u2015\s]?\d{6,8}", re.I),
    "ClinicalTrials.gov": re.compile(r"\bNCT\s?\d{8}\b"),
    "ISRCTN": re.compile(r"\bISRCTN\s?\d{6,8}\b", re.I),
    "PACTR": re.compile(r"\bPACTR\s?\d{10,16}\b", re.I),
    "CTRI (India)": re.compile(r"\bCTRI/\d{4}/\d{2,3}/\d{5,7}\b", re.I),
    "ANZCTR": re.compile(r"\bACTRN\s?\d{12,14}\b", re.I),
    "DRKS (Germany)": re.compile(r"\bDRKS\s?\d{8}\b", re.I),
    "RIDIE": re.compile(r"\bRIDIE(?:[-\s]?STUDY[-\s]?ID[-\s]?\d{4}[-\s]?\d{2}[-\s]?\d+)?", re.I),
}
# Registries identifiable by name even when no structured ID is printed. AsPredicted
# is a pre-registration platform whose name is unambiguous (it is not also a funder
# or conference), so a bare name match is safe.
REGISTRY_NAME_PATTERNS = {
    "AEA RCT Registry": re.compile(r"AEA\s+RCT\s+registry|social\s*science\s*registry(?:\.org)?", re.I),
    "ClinicalTrials.gov": re.compile(r"clinicaltrials\.gov", re.I),
    "ISRCTN": re.compile(r"\bISRCTN\s+registry\b", re.I),
    "RIDIE": re.compile(r"ridie(?:\.3ieimpact\.org)?|registry\s+for\s+international\s+development\s+impact\s+evaluations", re.I),
    "AsPredicted": re.compile(r"aspredicted\.org|\bAsPredicted\b", re.I),
}
# Despaced (Elsevier lost-space) literals -> registry name. Lowercased, no spaces.
REGISTRY_DESPACED = {
    "aearctr": "AEA RCT Registry",
    "aearctregistry": "AEA RCT Registry",
    "socialscienceregistry": "AEA RCT Registry",
    "clinicaltrials.gov": "ClinicalTrials.gov",
    "isrctn": "ISRCTN",
}

# Overloaded platforms: their bare name is NOT a reliable registration signal, so
# count them only when a registration / pre-analysis-plan cue sits within CUE_WINDOW
# chars. OSF: osf.io links in econ papers are frequently preprint / supplementary-
# material DOIs, not registrations. EGAP (Evidence in Governance and Politics) is a
# registry but ALSO a funder (the "EGAP Metaketa initiative"), a conference series
# ("participants at ... EGAP"), and an author affiliation, so a bare "EGAP" hit is
# most often not a registration.
CUE_REGISTRY_PATTERNS = {
    "OSF Registries": re.compile(r"osf\.io/[A-Za-z0-9]+", re.I),
    "EGAP": re.compile(r"\bEGAP\b|evidence\s+in\s+governance\s+and\s+politics", re.I),
}
CUE_WINDOW = 110
CUE_RE = re.compile(r"regist|pre[-\s]?analysis\s+plan|pre[-\s]?specif|\banalysis\s+plan\b", re.I)

# --- Pre-analysis-plan patterns ---------------------------------------------
# Headline PAP signal: "pre-analysis plan" or an analysis plan qualified as
# pre-registered / pre-specified / filed / registered.
PAP_RE = re.compile(
    r"pre[-\s]?analysis\s+plan"
    r"|(?:pre[-\s]?regist\w*|pre[-\s]?specif\w*|filed|registered|deposited)\s+"
    r"(?:\w+\s+){0,2}analysis\s+plan",
    re.I,
)
PAP_DESPACED = ("preanalysisplan", "pre-analysisplan")
# Supporting signals (reported separately; NOT folded into the headline has_pap).
ANALYSIS_PLAN_RE = re.compile(r"\banalysis\s+plan\b", re.I)      # bare "analysis plan"
PREREG_WORDING_RE = re.compile(r"pre[-\s]?regist\w*|pre[-\s]?specif\w*", re.I)

DOI_RE = re.compile(r"10\.\d{4,9}/[-._;()/:a-z0-9]+", re.I)


def log(msg):
    line = f"[{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%SZ')}] {msg}"
    print(line, flush=True)
    with open(LOG_TXT, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def norm_doi(d):
    d = (d or "").strip().lower()
    d = re.sub(r"^https?://(dx\.)?doi\.org/", "", d)
    return d.rstrip(".,;)")


def collapse_ws(s):
    return re.sub(r"\s+", " ", s or "")


def clean_snippet(s):
    return re.sub(r"\s+", " ", s).strip()


def norm_reg_id(registry, raw):
    """Normalize a matched registry token to a canonical identifier string."""
    s = re.sub(r"\s+", "", raw).upper()
    s = re.sub(r"[\u2010-\u2015]", "-", s)  # unify dash variants
    if registry == "AEA RCT Registry":
        m = re.search(r"AEARCTR-?(\d{6,8})", s)
        if m:
            return f"AEARCTR-{m.group(1)}"
    return s


def find_pdftotext():
    for cand in ("pdftotext", "pdftotext.exe"):
        p = shutil.which(cand)
        if p:
            return p
    for hard in (r"C:\Program Files\Git\mingw64\bin\pdftotext.exe",
                 "/mingw64/bin/pdftotext"):
        if os.path.exists(hard):
            return hard
    sys.exit("Could not find `pdftotext`. Install poppler / Git-for-Windows.")


def pdf_to_text(pdftotext_bin, pdf_path):
    """Whole-document text, clean UTF-8, form-feeds preserved. None on error."""
    try:
        proc = subprocess.run(
            [pdftotext_bin, "-enc", "UTF-8", str(pdf_path), "-"],
            capture_output=True, timeout=PDFTOTEXT_TIMEOUT,
        )
        if proc.returncode != 0:
            log(f"    pdftotext exit {proc.returncode} on {pdf_path.name}: "
                f"{proc.stderr[:160].decode('utf-8', 'replace')}")
            return None
        return unicodedata.normalize("NFKC", proc.stdout.decode("utf-8", "replace"))
    except Exception as e:  # noqa: BLE001
        log(f"    pdftotext error on {pdf_path.name}: {type(e).__name__}: {e}")
        return None


def add_snippet(snippets, seen, pno, label, flat, a, b):
    if len(snippets) >= MAX_SNIPPETS:
        return
    key = (pno, label, a // 60)
    if key in seen:
        return
    seen.add(key)
    snippets.append(f"[p{pno}|{label}] ...{clean_snippet(flat[a:b])}...")


def scan_text(text):
    """Scan one paper. Return a dict of the deterministic detection fields."""
    registries = {}           # registry name -> set of normalized ids ("" if name-only)
    reg_id_hits = 0
    has_pap = False
    prereg_wording = False
    analysis_plan_any = False
    pages_hit = set()
    snippets = []
    seen = set()

    pages = text.split("\f") if text else []
    for pno, page in enumerate(pages, 1):
        flat = collapse_ws(page)
        despaced = re.sub(r"\s+", "", page).lower()

        # -- registry identifiers (structured) --
        for registry, rx in REGISTRY_ID_PATTERNS.items():
            for m in rx.finditer(flat):
                rid = norm_reg_id(registry, m.group(0))
                registries.setdefault(registry, set()).add(rid)
                reg_id_hits += 1
                pages_hit.add(pno)
                a = max(0, m.start() - SNIPPET_PAD)
                b = min(len(flat), m.end() + SNIPPET_PAD)
                add_snippet(snippets, seen, pno, f"reg:{registry}", flat, a, b)

        # -- registry names (no structured id required) --
        for registry, rx in REGISTRY_NAME_PATTERNS.items():
            m = rx.search(flat)
            if m:
                registries.setdefault(registry, set())
                pages_hit.add(pno)
                a = max(0, m.start() - SNIPPET_PAD)
                b = min(len(flat), m.end() + SNIPPET_PAD)
                add_snippet(snippets, seen, pno, f"regname:{registry}", flat, a, b)

        # -- overloaded platforms (OSF, EGAP): count only with a nearby cue --
        for registry, rx in CUE_REGISTRY_PATTERNS.items():
            for m in rx.finditer(flat):
                lo = max(0, m.start() - CUE_WINDOW)
                hi = min(len(flat), m.end() + CUE_WINDOW)
                if CUE_RE.search(flat[lo:hi]):
                    s = registries.setdefault(registry, set())
                    if registry == "OSF Registries":
                        s.add(re.sub(r"\s+", "", m.group(0)).lower())
                        reg_id_hits += 1
                    pages_hit.add(pno)
                    add_snippet(snippets, seen, pno, f"reg:{registry}", flat, lo, hi)
                    break  # one cue-confirmed hit per page is enough

        # -- despaced registry fallback (Elsevier lost-space) --
        for lit, registry in REGISTRY_DESPACED.items():
            idx = despaced.find(lit)
            if idx != -1:
                if registry == "AEA RCT Registry":
                    m = re.search(r"aearctr-?(\d{6,8})", despaced)
                    if m:
                        registries.setdefault(registry, set()).add(
                            f"AEARCTR-{m.group(1)}")
                    else:
                        registries.setdefault(registry, set())
                else:
                    registries.setdefault(registry, set())
                pages_hit.add(pno)
                a = max(0, idx - 60)
                b = min(len(despaced), idx + len(lit) + 60)
                add_snippet(snippets, seen, pno, f"reg-despaced:{registry}",
                            despaced, a, b)

        # -- pre-analysis plan (headline) --
        for m in PAP_RE.finditer(flat):
            has_pap = True
            pages_hit.add(pno)
            a = max(0, m.start() - SNIPPET_PAD)
            b = min(len(flat), m.end() + SNIPPET_PAD)
            add_snippet(snippets, seen, pno, "PAP", flat, a, b)
        if not has_pap:
            for lit in PAP_DESPACED:
                if lit in despaced:
                    has_pap = True
                    pages_hit.add(pno)
                    idx = despaced.find(lit)
                    a = max(0, idx - 60)
                    b = min(len(despaced), idx + len(lit) + 60)
                    add_snippet(snippets, seen, pno, "PAP-despaced", despaced, a, b)
                    break

        # -- supporting signals --
        if not analysis_plan_any and ANALYSIS_PLAN_RE.search(flat):
            analysis_plan_any = True
        if not prereg_wording and PREREG_WORDING_RE.search(flat):
            prereg_wording = True

    return {
        "registries": registries,
        "reg_id_hits": reg_id_hits,
        "has_pap": has_pap,
        "prereg_wording": prereg_wording,
        "analysis_plan_any": analysis_plan_any,
        "pages_hit": sorted(pages_hit),
        "snippets": snippets,
        "n_pages": len(pages),
    }


def classify(registered, has_pap, status):
    if status != "ok":
        return status
    if registered and has_pap:
        return "registered_with_pap"
    if registered and not has_pap:
        return "registered_no_pap"
    if has_pap and not registered:
        return "pap_only"
    return "neither"


FIELDS = ["doi", "journal_short", "publication_year", "title", "matched_file",
          "n_pages", "registered", "registries", "registry_ids",
          "has_pap", "prereg_wording", "analysis_plan_any", "prereg_class",
          "n_reg_id_hits", "pages_with_hits", "snippets",
          "extraction_status", "snapshot_utc"]


def load_rcts():
    """RCT list + DOI->matched_file map, preferring the reusable irb_mentions map."""
    rcts = {}
    src = None
    if IRB_MENTIONS.exists():
        src = IRB_MENTIONS.name
        with open(IRB_MENTIONS, encoding="utf-8") as f:
            for r in csv.DictReader(f):
                doi = norm_doi(r["doi"])
                rcts[doi] = {
                    "doi": doi,
                    "journal_short": r.get("journal_short", ""),
                    "publication_year": r.get("publication_year", ""),
                    "title": r.get("title", ""),
                    "matched_file": r.get("matched_file", ""),
                }
    else:
        src = FINAL.name
        with open(FINAL, encoding="utf-8") as f:
            for r in csv.DictReader(f):
                if r.get("rct_classification") == "yes":
                    doi = norm_doi(r["doi"])
                    rcts[doi] = {
                        "doi": doi,
                        "journal_short": r.get("journal_short", ""),
                        "publication_year": r.get("publication_year", ""),
                        "title": r.get("title", ""),
                        "matched_file": "",
                    }
    return rcts, src


def main():
    open(LOG_TXT, "w").close()
    log(f"Stage 24 (pre-registration / PAP scan) start. SMOKE_N={SMOKE_N}")
    pdftotext_bin = find_pdftotext()
    log(f"pdftotext: {pdftotext_bin}")

    rcts, src = load_rcts()
    log(f"RCTs loaded: {len(rcts)} (map source: {src})")

    # Resume: load already-written results.
    done = {}
    if OUT_CSV.exists():
        with open(OUT_CSV, encoding="utf-8") as f:
            for r in csv.DictReader(f):
                if r.get("extraction_status") in ("ok", "pdf_error"):
                    done[norm_doi(r["doi"])] = r
        log(f"Resuming: {len(done)} papers already scanned")

    work = [d for d in rcts if d not in done]
    if SMOKE_N:
        work = work[:SMOKE_N]
    log(f"To scan this run: {len(work)}")

    out_rows = list(done.values())
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    n_reg = n_pap = n_scanned = 0
    registry_counter = Counter()

    def save():
        with open(OUT_CSV, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=FIELDS)
            w.writeheader()
            w.writerows(out_rows)

    for i, doi in enumerate(work, 1):
        r = rcts[doi]
        fn = r["matched_file"]
        pdf_path = FULLTEXT / fn if fn else None
        if not fn or not pdf_path.exists():
            out_rows.append({**{k: r.get(k, "") for k in
                                ("doi", "journal_short", "publication_year",
                                 "title", "matched_file")},
                             "n_pages": "", "registered": "", "registries": "",
                             "registry_ids": "", "has_pap": "", "prereg_wording": "",
                             "analysis_plan_any": "", "prereg_class": "no_pdf",
                             "n_reg_id_hits": "", "pages_with_hits": "",
                             "snippets": "", "extraction_status": "no_pdf",
                             "snapshot_utc": stamp})
            continue

        text = pdf_to_text(pdftotext_bin, pdf_path)
        if text is None:
            out_rows.append({**{k: r.get(k, "") for k in
                                ("doi", "journal_short", "publication_year",
                                 "title", "matched_file")},
                             "n_pages": "", "registered": "", "registries": "",
                             "registry_ids": "", "has_pap": "", "prereg_wording": "",
                             "analysis_plan_any": "", "prereg_class": "pdf_error",
                             "n_reg_id_hits": "", "pages_with_hits": "",
                             "snippets": "", "extraction_status": "pdf_error",
                             "snapshot_utc": stamp})
            continue

        d = scan_text(text)
        registered = bool(d["registries"])
        status = "ok"
        cls = classify(registered, d["has_pap"], status)
        if registered:
            n_reg += 1
            registry_counter.update(d["registries"].keys())
        if d["has_pap"]:
            n_pap += 1
        n_scanned += 1

        # flatten registries/ids for CSV
        reg_names = sorted(d["registries"].keys())
        all_ids = sorted({rid for ids in d["registries"].values() for rid in ids if rid})
        out_rows.append({
            "doi": doi, "journal_short": r["journal_short"],
            "publication_year": r["publication_year"], "title": r["title"],
            "matched_file": fn, "n_pages": d["n_pages"],
            "registered": str(registered),
            "registries": " | ".join(reg_names),
            "registry_ids": " | ".join(all_ids),
            "has_pap": str(d["has_pap"]),
            "prereg_wording": str(d["prereg_wording"]),
            "analysis_plan_any": str(d["analysis_plan_any"]),
            "prereg_class": cls,
            "n_reg_id_hits": d["reg_id_hits"],
            "pages_with_hits": ";".join(str(x) for x in d["pages_hit"]),
            "snippets": "  ||  ".join(d["snippets"]),
            "extraction_status": status, "snapshot_utc": stamp,
        })
        if i % 25 == 0:
            save()
            log(f"  scanned {i}/{len(work)}; registered={n_reg} pap={n_pap}")

    save()

    # Summary.
    final = {norm_doi(r["doi"]): r for r in out_rows}
    by_status = Counter(r["extraction_status"] for r in final.values())
    by_class = Counter(r["prereg_class"] for r in final.values())
    n_pdf = sum(1 for r in final.values() if r["extraction_status"] == "ok")
    reg_ok = sum(1 for r in final.values()
                 if r["extraction_status"] == "ok" and r["registered"] == "True")
    pap_ok = sum(1 for r in final.values()
                 if r["extraction_status"] == "ok" and r["has_pap"] == "True")
    log("\n=== pre-registration / PAP summary ===")
    log(f"RCTs total: {len(rcts)}")
    for st in ("ok", "no_pdf", "pdf_error"):
        if by_status.get(st):
            log(f"  status {st:10s} {by_status[st]}")
    if n_pdf:
        log(f"Of {n_pdf} RCTs with readable PDF:")
        log(f"  registered (any registry): {reg_ok} ({100*reg_ok/n_pdf:.1f}%)")
        log(f"  pre-analysis plan:         {pap_ok} ({100*pap_ok/n_pdf:.1f}%)")
    log("Class distribution:")
    for c in ("registered_with_pap", "registered_no_pap", "pap_only", "neither",
              "no_pdf", "pdf_error"):
        if by_class.get(c):
            log(f"  {c:20s} {by_class[c]}")
    log("Registry composition (papers, registries can co-occur):")
    for reg, n in registry_counter.most_common():
        log(f"  {reg:22s} {n}")
    log(f"\nOutput: {OUT_CSV.name}  (review snippets; optionally add a keyed-LLM "
        f"refinement pass for the residual, as 13_irb_classify.py does for IRB)")


if __name__ == "__main__":
    main()
