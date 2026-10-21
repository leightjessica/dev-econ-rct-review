import pdfplumber, re

PDF = r"C:\Users\JLEIGHT\IFPRI Dropbox\Jessica Leight\dev-econ-rct-review\fulltext\1-s2.0-S0304387825000884-main.pdf"

terms = ["irb","institutional review","review board","ethic","ethical","human subjects",
         "ethics committee","ethical approval","ethical clearance","consent","protocol number",
         "exempt","helsinki","declaration of helsinki","research ethics","reb","ipa irb",
         "ministry of health"]

with pdfplumber.open(PDF) as pdf:
    n = len(pdf.pages)
    print("TOTAL_PAGES:", n)
    pages_text = []
    for i, page in enumerate(pdf.pages):
        t = page.extract_text() or ""
        pages_text.append(t)

# search every page
print("\n===== TERM MATCHES =====")
for i, t in enumerate(pages_text):
    low = t.lower()
    for term in terms:
        for m in re.finditer(re.escape(term), low):
            s = max(0, m.start()-150); e = min(len(t), m.end()+150)
            ctx = t[s:e].replace("\n"," ")
            print(f"[p{i+1}] <{term}> ...{ctx}...")

print("\n===== FIRST 4 PAGES =====")
for i in range(min(4, len(pages_text))):
    print(f"\n----- PAGE {i+1} -----\n{pages_text[i]}")

print("\n===== LAST 6 PAGES =====")
for i in range(max(0, len(pages_text)-6), len(pages_text)):
    print(f"\n----- PAGE {i+1} -----\n{pages_text[i]}")
