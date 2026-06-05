#!/usr/bin/env python3
"""Stage 6d - reconcile downloaded full-text PDFs against the no-funder RCT list.

The user downloaded full-text PDFs for the RCTs that Stage 6a could not find a
funder for in metadata (funders_6a.csv, n_funders == 0; 273 papers). PDFs were
saved under their publisher-default filenames, which do NOT encode the DOI, so
the only reliable way to know which papers are present is to read each PDF's
first pages and pull out the DOI (and, for working-paper versions that carry no
published DOI, the title).

This script:
  1. builds the "needed" list = funders_6a rows with n_funders == 0, minus the
     manually flagged Not-RCT corrections;
  2. extracts text from the first few pages of every PDF in fulltext/;
  3. matches each needed paper to a PDF by DOI, falling back to a normalized
     full-title substring match (catches WP versions);
  4. writes data/fulltext_match_report.csv and prints a summary of missing
     papers and unmatched PDFs.

Read-only with respect to the dataset; it only writes the report CSV.
"""

import csv
import html
import re
import sys
from pathlib import Path

import pdfplumber

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
FULLTEXT = ROOT / "fulltext"
PAGES_TO_READ = 3  # acknowledgments footnote + DOI are on the first page(s)

# DOIs the user manually reclassified as Not RCT -> drop from the needed list.
NOT_RCT = {
    "10.1257/app.20220258",
    "10.1257/app.20220601",
    "10.3982/ecta17527",
    "10.1162/rest_a_01552",
    "10.1016/j.jdeveco.2022.103026",
    "10.1016/j.jdeveco.2022.103004",
    "10.1016/j.jdeveco.2023.103097",
    "10.1016/j.jdeveco.2023.103069",
    "10.1016/j.jdeveco.2024.103265",
    "10.1016/j.jdeveco.2025.103462",
}

# Confirmed by manual first-page inspection: papers whose DOI text was mangled
# by the PDF extractor, or whose downloaded copy is a working-paper version with
# a different title/DOI than the published metadata. (doi -> filename, version)
MANUAL_MATCH = {
    "10.3982/ecta18916": ("EBSCO-FullText-06_04_2026 (28).pdf", "published"),
    "10.3982/ecta19959": ("EBSCO-FullText-06_04_2026 (29).pdf", "published"),
    "10.1093/jeea/jvaf052": ("Political Information and Network Effects.pdf", "wp"),
    "10.1093/ej/ueae117": ("ifpridp02271.pdf", "wp"),
    "10.1093/ej/ueaf046": ("w27887.pdf", "wp"),
}

DOI_RE = re.compile(r"10\.\d{4,9}/[-._;()/:a-z0-9]+", re.I)


def norm_doi(doi: str) -> str:
    doi = (doi or "").strip().lower()
    doi = re.sub(r"^https?://(dx\.)?doi\.org/", "", doi)
    return doi.rstrip(".,;)")


def norm_text(s: str) -> str:
    """Lowercase, strip HTML, keep only alphanumerics for robust substring match."""
    s = html.unescape(s or "")
    s = re.sub(r"<[^>]+>", " ", s)  # drop <sup>, <i>, etc.
    s = s.lower()
    s = re.sub(r"[^a-z0-9]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def load_needed():
    rows = list(csv.DictReader(open(DATA / "funders_6a.csv", encoding="utf-8")))
    needed = []
    for r in rows:
        if (r["n_funders"] or "0").strip() not in ("0", ""):
            continue
        doi = norm_doi(r["doi"])
        if doi in {norm_doi(d) for d in NOT_RCT}:
            continue
        needed.append(
            {
                "doi": doi,
                "journal_short": r["journal_short"],
                "publication_year": r["publication_year"],
                "title": r["title"],
                "title_norm": norm_text(r["title"]),
            }
        )
    return needed


def extract_pdf(path: Path):
    """Return (raw_text, dois_found) from the first PAGES_TO_READ pages."""
    text_parts = []
    try:
        with pdfplumber.open(path) as pdf:
            for page in pdf.pages[:PAGES_TO_READ]:
                text_parts.append(page.extract_text() or "")
    except Exception as e:  # noqa: BLE001 - report and continue
        return "", set(), f"ERROR: {e}"
    raw = "\n".join(text_parts)
    dois = {norm_doi(m.group(0)) for m in DOI_RE.finditer(raw)}
    return raw, dois, ""


def main():
    needed = load_needed()
    print(f"Needed (no-funder RCTs, minus Not-RCT corrections): {len(needed)}")

    pdfs = sorted(FULLTEXT.glob("*.pdf"))
    print(f"PDFs on disk: {len(pdfs)}\n")

    # Pre-extract every PDF once.
    pdf_info = {}
    for i, p in enumerate(pdfs, 1):
        raw, dois, err = extract_pdf(p)
        pdf_info[p.name] = {
            "raw_norm": norm_text(raw),
            "dois": dois,
            "err": err,
        }
        if err:
            print(f"  [{i}/{len(pdfs)}] {p.name}: {err}")
        if i % 25 == 0:
            print(f"  ...extracted {i}/{len(pdfs)}")

    needed_by_doi = {n["doi"]: n for n in needed}
    matched_pdf = {}  # pdf_name -> matched doi
    for n in needed:
        n["matched_file"] = ""
        n["match_method"] = ""

    # Pass 1: DOI match.
    for name, info in pdf_info.items():
        for d in info["dois"]:
            if d in needed_by_doi and not needed_by_doi[d]["matched_file"]:
                needed_by_doi[d]["matched_file"] = name
                needed_by_doi[d]["match_method"] = "doi"
                matched_pdf[name] = d
                break

    # Pass 1b: confirmed manual matches (mangled DOI text / WP versions).
    for doi, (name, version) in MANUAL_MATCH.items():
        d = norm_doi(doi)
        if d in needed_by_doi and not needed_by_doi[d]["matched_file"]:
            needed_by_doi[d]["matched_file"] = name
            needed_by_doi[d]["match_method"] = f"manual-{version}"
            matched_pdf[name] = d

    # Pass 2: title substring match for still-missing papers (WP versions).
    for n in needed:
        if n["matched_file"] or len(n["title_norm"]) < 15:
            continue
        for name, info in pdf_info.items():
            if name in matched_pdf:
                continue
            if n["title_norm"] in info["raw_norm"]:
                n["matched_file"] = name
                n["match_method"] = "title"
                matched_pdf[name] = n["doi"]
                break

    found = [n for n in needed if n["matched_file"]]
    missing = [n for n in needed if not n["matched_file"]]
    unmatched_pdfs = [p.name for p in pdfs if p.name not in matched_pdf]

    # Write report.
    out = DATA / "fulltext_match_report.csv"
    with open(out, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["doi", "journal_short", "publication_year", "title",
                    "status", "matched_file", "match_method"])
        for n in sorted(needed, key=lambda x: (x["matched_file"] == "", x["journal_short"])):
            w.writerow([n["doi"], n["journal_short"], n["publication_year"],
                        n["title"], "FOUND" if n["matched_file"] else "MISSING",
                        n["matched_file"], n["match_method"]])

    print("\n" + "=" * 70)
    print(f"FOUND   : {len(found)} / {len(needed)}")
    print(f"  by DOI  : {sum(1 for n in found if n['match_method']=='doi')}")
    print(f"  by title: {sum(1 for n in found if n['match_method']=='title')}")
    print(f"MISSING : {len(missing)}")
    print(f"Unmatched PDFs on disk (extras/dupes/out-of-scope): {len(unmatched_pdfs)}")
    print("=" * 70)

    if missing:
        print("\n--- MISSING (no PDF found) ---")
        from collections import Counter
        for j, c in sorted(Counter(n["journal_short"] for n in missing).items(),
                           key=lambda x: -x[1]):
            print(f"  {c:3d}  {j}")
        print()
        for n in sorted(missing, key=lambda x: x["journal_short"]):
            print(f"  {n['journal_short']:12s} {n['doi']:30s} {n['title'][:60]}")

    if unmatched_pdfs:
        print("\n--- UNMATCHED PDFs (not tied to any needed paper) ---")
        for name in sorted(unmatched_pdfs):
            dois = pdf_info[name]["dois"]
            tag = (";".join(sorted(dois))[:50] if dois else "no-doi-found")
            print(f"  {name[:55]:55s} [{tag}]")

    print(f"\nReport written: {out}")


if __name__ == "__main__":
    sys.exit(main())
