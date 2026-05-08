"""
Stage 0 (bootstrap): Build the JEL descriptor -> code lookup table from the
American Economic Association's authoritative classification at
https://www.aeaweb.org/econlit/jelCodes.php?view=jel.

Why this is needed
------------------
EBSCO's EconLit export populates the `subjects` column with human-readable JEL
descriptor strings (e.g., "Microeconomic Analyses of Economic Development"),
not the JEL codes themselves (e.g., "O12"). Our development-paper filter is
defined as "any JEL code beginning with O", which requires mapping descriptors
back to codes. This script fetches the AEA's canonical JEL list and produces a
descriptor-to-code lookup that Stage 2 can use.

Why a separate bootstrap stage
------------------------------
The AEA JEL list is stable on the order of years, but the script depends on
HTML structure of one external page. By running it once and committing
`data/jel_lookup.csv` to the project, we (a) decouple Stage 2 runtime from the
AEA website's availability, and (b) make the JEL classification used in any
given run inspectable and version-controllable.

EconLit descriptor formatting
-----------------------------
For each JEL leaf code, EconLit may render the descriptor in either of two
forms:

- BARE: the AEA's leaf descriptor verbatim, with bullets normalized to
  semicolons (e.g., "Microeconomic Analyses of Economic Development").
- PREFIXED: the parent-subcategory name, a colon-space, and then the bare
  descriptor (e.g., "Economic Development: Urban, Rural, Regional, and
  Transportation Analysis; Housing; Infrastructure").

EconLit's choice between the two is per-record and not entirely consistent.
We therefore generate BOTH forms for every leaf code, and the Stage 2 matcher
checks against both.

Output
------
`data/jel_lookup.csv` with columns:
    code                JEL leaf code (e.g., "O12")
    parent_code         Subcategory code (e.g., "O1")
    parent_name         Subcategory name (e.g., "Economic Development")
    top_letter          Top-level letter (e.g., "O")
    top_name            Top-level name (e.g., "Economic Development, ...")
    descriptor_bare     Leaf descriptor with bullets -> semicolons
    descriptor_prefixed parent_name + ": " + descriptor_bare

Replicability: standard library only.
"""

import csv
import html as html_mod
import os
import re
import urllib.request
from datetime import datetime, timezone

EMAIL = "J.Leight@cgiar.org"
SOURCE_URL = "https://www.aeaweb.org/econlit/jelCodes.php?view=jel"

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_DIR = os.path.abspath(os.path.join(SCRIPT_DIR, ".."))
OUT_CSV = os.path.join(PROJECT_DIR, "data", "jel_lookup.csv")
LOG_TXT = os.path.join(PROJECT_DIR, "data", "00_build_jel_lookup.log")


def log(msg):
    line = f"[{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%SZ')}] {msg}"
    print(line, flush=True)
    with open(LOG_TXT, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def normalize_descriptor(text):
    """AEA uses ' &bull; ' as separator; EconLit uses '; '. Normalize."""
    text = html_mod.unescape(text)               # &bull; -> u2022, &amp; -> &
    text = text.replace("•", ";")           # bullets to semicolons
    text = re.sub(r"\s+", " ", text).strip()
    # AEA puts " ; " around bullets sometimes; EconLit drops the leading space
    text = text.replace(" ;", ";")
    return text


def fetch_html(url):
    req = urllib.request.Request(
        url,
        headers={"User-Agent": f"dev-rct-review-bootstrap (mailto:{EMAIL})"},
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        return resp.read().decode("utf-8")


# Top-level categories: <a name='X'> ... <center><b>X. Name</b></center>
TOP_RE = re.compile(
    r"<a name='([A-Z])'>.*?<center><b>\s*([A-Z])\.\s*([^<]+?)\s*</b></center>",
    re.DOTALL,
)

# Subcategories (2-char like O1): row with leading <td> containing the code
# and a second <td colspan='2'> with the name.
SUB_RE = re.compile(
    r"<td[^>]*>\s*([A-Z]\d)\s*</td>\s*<td[^>]*colspan=['\"]2['\"][^>]*>\s*([^<]+?)\s*</td>",
    re.DOTALL,
)

# Leaf codes (3-char like O11): <td></td><td>code</td><td>descriptor</td>
LEAF_RE = re.compile(
    r"<td>\s*</td>\s*<td[^>]*>\s*([A-Z]\d{2})\s*</td>\s*<td[^>]*>\s*([^<]+?)\s*</td>",
    re.DOTALL,
)


def main():
    open(LOG_TXT, "w").close()
    log(f"Fetching {SOURCE_URL}")
    html = fetch_html(SOURCE_URL)
    log(f"  fetched {len(html):,} bytes")

    # Parse top-level letters
    tops = {}
    for m in TOP_RE.finditer(html):
        letter, _letter2, name = m.group(1), m.group(2), m.group(3)
        tops[letter] = normalize_descriptor(name)
    log(f"  top-level categories: {len(tops)} -> {sorted(tops.keys())}")

    # Parse subcategories (2-char codes)
    subs = {}
    for m in SUB_RE.finditer(html):
        code, name = m.group(1), m.group(2)
        subs[code] = normalize_descriptor(name)
    log(f"  subcategory codes: {len(subs)}")

    # Parse leaf codes (3-char)
    leaves = []
    for m in LEAF_RE.finditer(html):
        code, desc = m.group(1), m.group(2)
        leaves.append((code, normalize_descriptor(desc)))
    log(f"  leaf codes: {len(leaves)}")

    if not leaves:
        raise SystemExit("ERROR: no leaf codes parsed; AEA HTML structure may have changed")

    # Assemble rows
    rows = []
    seen = set()
    for code, bare in leaves:
        if code in seen:
            continue
        seen.add(code)
        parent_code = code[:2]
        parent_name = subs.get(parent_code, "")
        top_letter = code[:1]
        top_name = tops.get(top_letter, "")
        prefixed = f"{parent_name}: {bare}" if parent_name else bare
        rows.append({
            "code": code,
            "parent_code": parent_code,
            "parent_name": parent_name,
            "top_letter": top_letter,
            "top_name": top_name,
            "descriptor_bare": bare,
            "descriptor_prefixed": prefixed,
        })

    rows.sort(key=lambda r: r["code"])

    fieldnames = ["code", "parent_code", "parent_name", "top_letter",
                  "top_name", "descriptor_bare", "descriptor_prefixed"]
    os.makedirs(os.path.dirname(OUT_CSV), exist_ok=True)
    with open(OUT_CSV, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    # Spot-check by top-letter
    from collections import Counter
    cnt = Counter(r["top_letter"] for r in rows)
    log(f"Wrote {len(rows)} rows -> {OUT_CSV}")
    log("Counts by top-level letter:")
    for letter in sorted(cnt):
        log(f"  {letter} ({tops.get(letter,'')[:50]}): {cnt[letter]}")
    log("Sample O-family rows (descriptor_bare | descriptor_prefixed):")
    for r in [x for x in rows if x["top_letter"] == "O"][:5]:
        log(f"  {r['code']}  bare='{r['descriptor_bare']}'  prefixed='{r['descriptor_prefixed']}'")
    log("Bootstrap complete.")


if __name__ == "__main__":
    main()
