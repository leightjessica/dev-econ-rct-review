import csv, os, random
DATA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data")
rows = list(csv.DictReader(open(os.path.join(DATA, "irb_mentions.csv"), encoding="utf-8")))
none = [r for r in rows if r["extraction_status"] == "no_mention"]
random.seed(42)
sample = random.sample(none, 10)
sample.sort(key=lambda r: (r["journal_short"], r["doi"]))
out = os.path.join(DATA, "irb_no_mention_sample10.csv")
with open(out, "w", newline="", encoding="utf-8") as f:
    w = csv.writer(f)
    w.writerow(["doi", "journal_short", "publication_year", "title", "matched_file", "n_pages"])
    for r in sample:
        w.writerow([r["doi"], r["journal_short"], r["publication_year"], r["title"],
                    r["matched_file"], r["n_pages"]])
print(f"Random 10 of {len(none)} no-mention RCTs (seed=42):\n")
for i, r in enumerate(sample, 1):
    print(f"{i:2d}. {r['journal_short']:11s} {r['doi']}")
    print(f"    {r['title'][:88]}")
    print(f"    PDF: {r['matched_file']}  ({r['n_pages']} pp)")
print(f"\nWritten: {os.path.basename(out)}")
