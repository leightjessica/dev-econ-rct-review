#!/usr/bin/env python3
"""One-off: dump text of selected pages from 3 PDFs for manual IRB re-check."""
import sys
from pathlib import Path

try:
    import pdfplumber
except ImportError:
    sys.exit("need pdfplumber")

ROOT = Path(__file__).resolve().parent.parent
FT = ROOT / "fulltext"

targets = ["720466", "724320", "726237"]

for t in targets:
    p = FT / f"{t}.pdf"
    print("\n\n############################################")
    print(f"### FILE {t}.pdf")
    print("############################################")
    with pdfplumber.open(p) as pdf:
        n = len(pdf.pages)
        print(f"### TOTAL PAGES: {n}")
        # pages to dump: first 4, last 7
        first = list(range(0, min(4, n)))
        last = list(range(max(0, n - 7), n))
        idxs = sorted(set(first + last))
        for i in idxs:
            txt = pdf.pages[i].extract_text() or ""
            print(f"\n----- PAGE {i+1} -----")
            print(txt)
