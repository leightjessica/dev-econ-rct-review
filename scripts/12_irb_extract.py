#!/usr/bin/env python3
"""Stage 12 (DRAFT) - scan full-text PDFs for IRB / ethics / human-subjects mentions.

A new exercise (2026-06-09): for every RCT with a collected full-text PDF, find
any reference to research-ethics oversight anywhere in the paper -- NOT just the
acknowledgments. Economics papers place these statements unpredictably: a page-1
title footnote, a "Declarations"/"Ethics" section near the references, a data or
online appendix, or a sentence buried in the experimental-design section.

This is a KEYWORD/REGEX SCANNER, not an interpreter. It locates and excerpts the
relevant sentences so they can be reviewed (or fed to a downstream LLM pass that
extracts structured fields: approving body, approval/protocol number, and whether
approval was granted vs. the study deemed exempt). The terms searched, per the
project lead's specification:

    IRB                         \bIRBs?\b
    Institutional Review Board  institutional review board(s)
    ethical / ethics            \bethic(al|s)\b   (catches "ethics committee",
                                                    "ethical review", etc.)
    ethical / ethics approval   \bethic(al|s) approval\b
    human subjects [review]     \bhuman subjects?\b

Robustness notes:
  * Whitespace (including newlines that split a phrase across two lines) is
    collapsed to single spaces before matching, so "Institutional Review\nBoard"
    still matches.
  * Elsevier (JDE) extractions frequently lose ALL inter-word spaces. A secondary
    "despaced" scan (whitespace stripped, lowercased) catches the multi-word
    phrases in that form ("institutionalreviewboard", "humansubjects",
    "ethicalapproval"). Single-token "IRB" is deliberately excluded from the
    despaced scan to avoid spurious substring hits.
  * Page numbers are recorded per match so a reviewer can jump straight to them.

Inputs:
  data/final_dataset.csv        (RCTs = rct_classification == "yes")
  data/fulltext_match_report.csv (filename -> doi map for the 263 already matched)
  fulltext/*.pdf

Each RCT is matched to a PDF by: (1) the existing match report, then (2) a DOI
read from the PDF's first pages, then (3) a normalized-title substring fallback
(same logic as 06d, which catches working-paper versions).

Output:
  data/irb_mentions.csv   one row per RCT (status: ok / no_mention / no_pdf / pdf_error)
  data/12_irb_extract.log

Resumable: rerunning skips DOIs already written with status ok/no_mention.

Env flags:
  SMOKE_N=10   only process the first N matched PDFs (quick validation run)
"""

import csv
import html
import os
import re
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

try:
    import pdfplumber
except ImportError:
    sys.exit("Missing pdfplumber. From the project root:\n  pip install -r requirements.txt")

csv.field_size_limit(min(sys.maxsize, 2147483647))

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
FULLTEXT = ROOT / "fulltext"
FINAL = DATA / "final_dataset.csv"
MATCH_REPORT = DATA / "fulltext_match_report.csv"
OUT_CSV = DATA / "irb_mentions.csv"
LOG_TXT = DATA / "12_irb_extract.log"

SMOKE_N = int(os.environ.get("SMOKE_N", "0"))
SNIPPET_PAD = 140        # chars of context on each side of a match
MAX_SNIPPETS = 8         # cap snippets stored per paper
DOI_PAGES = 3            # pages to read when sniffing a DOI for matching

# --- term patterns (label -> compiled regex over whitespace-collapsed text) ---
TERM_PATTERNS = {
    "IRB": re.compile(r"\bIRBs?\b"),
    "Institutional Review Board": re.compile(r"institutional\s+review\s+boards?", re.I),
    "ethical/ethics": re.compile(r"\bethic(?:al|s)\b", re.I),
    "ethical/ethics approval": re.compile(r"\bethic(?:al|s)\s+approval\b", re.I),
    "human subjects": re.compile(r"\bhuman\s+subjects?\b", re.I),
}
# Multi-word phrases in their no-space (Elsevier) form. label -> tuple of literals.
DESPACED_PATTERNS = {
    "Institutional Review Board": ("institutionalreviewboards", "institutionalreviewboard"),
    "ethical/ethics approval": ("ethicalapproval", "ethicsapproval"),
    "human subjects": ("humansubjects", "humansubject"),
}

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


def norm_title(s):
    s = html.unescape(s or "")
    s = re.sub(r"<[^>]+>", " ", s).lower()
    s = re.sub(r"[^a-z0-9]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def collapse_ws(s):
    """Collapse all runs of whitespace to a single space (joins phrases split
    across line breaks) without otherwise altering the text."""
    return re.sub(r"\s+", " ", s or "")


def clean_snippet(s):
    return re.sub(r"\s+", " ", s).strip()


def load_rcts():
    rcts = []
    with open(FINAL, encoding="utf-8") as f:
        for r in csv.DictReader(f):
            if r.get("rct_classification") == "yes":
                rcts.append({
                    "doi": norm_doi(r["doi"]),
                    "journal_short": r["journal_short"],
                    "publication_year": r["publication_year"],
                    "title": r["title"],
                    "title_norm": norm_title(r["title"]),
                })
    return rcts


def load_match_report():
    """filename -> doi, from the existing 06d output (matched no-funder RCTs)."""
    m = {}
    if MATCH_REPORT.exists():
        with open(MATCH_REPORT, encoding="utf-8") as f:
            for r in csv.DictReader(f):
                if r.get("status") == "FOUND" and r.get("matched_file"):
                    m[r["matched_file"]] = norm_doi(r["doi"])
    return m


def read_pages(path):
    """Return list of page texts (all pages). Empty list on error."""
    try:
        with pdfplumber.open(path) as pdf:
            return [(pg.extract_text() or "") for pg in pdf.pages]
    except Exception as e:  # noqa: BLE001
        log(f"    PDF read error {path.name}: {type(e).__name__}: {e}")
        return None


def scan_pages(pages):
    """Scan a paper's pages for every term. Return (terms_set, n_total, pages_hit, snippets)."""
    terms = set()
    n_total = 0
    pages_hit = set()
    snippets = []
    seen_windows = set()
    for pno, raw in enumerate(pages, 1):
        flat = collapse_ws(raw)
        despaced = re.sub(r"\s+", "", raw).lower()
        for label, rx in TERM_PATTERNS.items():
            for m in rx.finditer(flat):
                terms.add(label)
                n_total += 1
                pages_hit.add(pno)
                if len(snippets) < MAX_SNIPPETS:
                    a = max(0, m.start() - SNIPPET_PAD)
                    b = min(len(flat), m.end() + SNIPPET_PAD)
                    key = (pno, a // 50)  # coarse dedup of overlapping windows
                    if key not in seen_windows:
                        seen_windows.add(key)
                        snippets.append(f"[p{pno}|{label}] ...{clean_snippet(flat[a:b])}...")
        # despaced fallback for multi-word phrases (Elsevier lost-space case)
        for label, literals in DESPACED_PATTERNS.items():
            for lit in literals:
                idx = despaced.find(lit)
                if idx != -1:
                    if label not in terms:
                        terms.add(label)
                    n_total += 1
                    pages_hit.add(pno)
                    if len(snippets) < MAX_SNIPPETS:
                        a = max(0, idx - 60)
                        b = min(len(despaced), idx + len(lit) + 60)
                        key = (pno, "despaced", label)
                        if key not in seen_windows:
                            seen_windows.add(key)
                            snippets.append(f"[p{pno}|{label}|despaced] ...{despaced[a:b]}...")
                    break
    return terms, n_total, sorted(pages_hit), snippets


FIELDS = ["doi", "journal_short", "publication_year", "title", "matched_file",
          "n_pages", "has_irb_mention", "terms_matched", "n_mentions",
          "pages_with_mentions", "snippets", "extraction_status", "snapshot_utc"]


def main():
    open(LOG_TXT, "w").close()
    log(f"Stage 12 (IRB scan) start. SMOKE_N={SMOKE_N}")

    rcts = load_rcts()
    by_doi = {r["doi"]: r for r in rcts}
    log(f"RCTs (rct_classification == yes): {len(rcts)}")

    file2doi = load_match_report()
    log(f"Pre-matched filenames from 06d report: {len(file2doi)}")

    # Resume: load already-written results.
    done = {}
    if OUT_CSV.exists():
        with open(OUT_CSV, encoding="utf-8") as f:
            for r in csv.DictReader(f):
                if r.get("extraction_status") in ("ok", "no_mention"):
                    done[r["doi"]] = r
        log(f"Resuming: {len(done)} papers already scanned")

    pdfs = sorted(FULLTEXT.glob("*.pdf"))
    log(f"PDFs on disk: {len(pdfs)}")

    # First pass: read every PDF once, deciding which RCT (if any) it belongs to.
    # Match priority: (1) 06d report filename map, (2) DOI sniffed from page 1-3,
    # (3) normalized-title substring. Each RCT is claimed by at most one PDF.
    paper_pages = {}          # doi -> list[str] pages (for the matched PDF)
    paper_file = {}           # doi -> filename
    used_pdfs = set()
    pending_title = []        # (name, pages) for the title fallback pass

    todo_pdfs = pdfs[:SMOKE_N] if SMOKE_N else pdfs
    for i, p in enumerate(todo_pdfs, 1):
        pages = read_pages(p)
        if pages is None:
            continue
        doi = file2doi.get(p.name, "")
        if not doi:
            head = collapse_ws("\n".join(pages[:DOI_PAGES]))
            for m in DOI_RE.finditer(head):
                cand = norm_doi(m.group(0))
                if cand in by_doi and cand not in paper_file:
                    doi = cand
                    break
        if doi and doi in by_doi and doi not in paper_file:
            paper_file[doi] = p.name
            paper_pages[doi] = pages
            used_pdfs.add(p.name)
        else:
            pending_title.append((p.name, pages))
        if i % 50 == 0:
            log(f"  read {i}/{len(todo_pdfs)} PDFs; matched {len(paper_file)} papers")

    # Title-substring fallback for RCTs still unmatched (working-paper versions).
    unmatched = [r for r in rcts if r["doi"] not in paper_file and len(r["title_norm"]) >= 15]
    for name, pages in pending_title:
        flat_norm = norm_title(" ".join(pages[:DOI_PAGES]))
        for r in unmatched:
            if r["doi"] in paper_file:
                continue
            if r["title_norm"] in flat_norm:
                paper_file[r["doi"]] = name
                paper_pages[r["doi"]] = pages
                used_pdfs.add(name)
                break

    log(f"Matched PDFs to {len(paper_file)} / {len(rcts)} RCTs "
        f"({len(used_pdfs)} PDFs used; {len(todo_pdfs) - len(used_pdfs)} unused)")

    # Second pass: scan matched papers (skip already-done on resume).
    out_rows = list(done.values())
    n_ok = n_none = 0
    term_counter = Counter()
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    def save():
        with open(OUT_CSV, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=FIELDS)
            w.writeheader()
            w.writerows(out_rows)

    scanned = 0
    for doi, pages in paper_pages.items():
        if doi in done:
            continue
        r = by_doi[doi]
        terms, n_total, pages_hit, snippets = scan_pages(pages)
        status = "ok" if terms else "no_mention"
        if terms:
            n_ok += 1
            term_counter.update(terms)
        else:
            n_none += 1
        out_rows.append({
            "doi": doi, "journal_short": r["journal_short"],
            "publication_year": r["publication_year"], "title": r["title"],
            "matched_file": paper_file[doi], "n_pages": len(pages),
            "has_irb_mention": str(bool(terms)),
            "terms_matched": " | ".join(sorted(terms)),
            "n_mentions": n_total,
            "pages_with_mentions": ";".join(str(x) for x in pages_hit),
            "snippets": "  ||  ".join(snippets),
            "extraction_status": status, "snapshot_utc": stamp,
        })
        scanned += 1
        if scanned % 25 == 0:
            save()
            log(f"  scanned {scanned} papers; with-mention={n_ok} none={n_none}")

    # RCTs with no PDF matched -> record as no_pdf (don't overwrite resumed rows).
    for r in rcts:
        if r["doi"] in paper_file or r["doi"] in done:
            continue
        out_rows.append({
            "doi": r["doi"], "journal_short": r["journal_short"],
            "publication_year": r["publication_year"], "title": r["title"],
            "matched_file": "", "n_pages": "", "has_irb_mention": "",
            "terms_matched": "", "n_mentions": "", "pages_with_mentions": "",
            "snippets": "", "extraction_status": "no_pdf", "snapshot_utc": stamp,
        })
    save()

    # Summary.
    final = {r["doi"]: r for r in out_rows}
    by_status = Counter(r["extraction_status"] for r in final.values())
    log("\n=== IRB scan summary ===")
    log(f"RCTs total: {len(rcts)}")
    for st in ("ok", "no_mention", "no_pdf", "pdf_error"):
        if by_status.get(st):
            log(f"  {st:11s} {by_status[st]}")
    with_pdf = by_status.get("ok", 0) + by_status.get("no_mention", 0)
    if with_pdf:
        log(f"Of {with_pdf} RCTs with a PDF: "
            f"{by_status.get('ok', 0)} have an IRB/ethics mention "
            f"({100*by_status.get('ok',0)/with_pdf:.0f}%)")
    log("Mentions by term (papers):")
    for label, _ in TERM_PATTERNS.items():
        log(f"  {label:28s} {term_counter.get(label, 0)}")
    log(f"\nOutput: {OUT_CSV.name}  (review snippets, then optionally run an LLM "
        f"extraction pass for approving body / protocol number / exempt-vs-approved)")


if __name__ == "__main__":
    main()
