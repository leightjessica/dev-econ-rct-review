import pdfplumber, re, sys

files = [
    r"C:\Users\JLEIGHT\IFPRI Dropbox\Jessica Leight\dev-econ-rct-review\fulltext\1-s2.0-S0304387825000884-main.pdf",
    r"C:\Users\JLEIGHT\IFPRI Dropbox\Jessica Leight\dev-econ-rct-review\fulltext\1-s2.0-S0304387825000963-main.pdf",
    r"C:\Users\JLEIGHT\IFPRI Dropbox\Jessica Leight\dev-econ-rct-review\fulltext\1-s2.0-S0304387825000975-main.pdf",
]

terms = re.compile(r"(irb|institutional review|review board|ethic|human subjects?|approval|protocol|consent|clearance|exempt|helsinki|declaration|registr)", re.I)

for f in files:
    print("\n\n########## FILE:", f.split("\\")[-1])
    with pdfplumber.open(f) as pdf:
        n = len(pdf.pages)
        print("PAGES:", n)
        for i, pg in enumerate(pdf.pages):
            txt = pg.extract_text() or ""
            collapsed = re.sub(r"\s+", " ", txt)
            for m in terms.finditer(collapsed):
                s = max(0, m.start()-180)
                e = min(len(collapsed), m.end()+180)
                print(f"--- p{i+1} [{m.group(0)}]: ...{collapsed[s:e]}...")
