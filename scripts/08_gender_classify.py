"""
Stage 8: Infer author gender from given (first) names.

Reads:
  data/final_dataset.csv     (one row per development paper; semicolon-delimited
                              "authors" field of full names in "First [Middle] Last" order)
  data/author_countries.csv  (OPTIONAL; from Stage 8a) per-author affiliation
                              country, used as a disambiguation prior

Writes:
  data/author_gender.csv          (one row per (paper, author): inferred gender)
  data/paper_gender_summary.csv   (one row per paper: gender composition measures)
  data/08_gender_classify.log

Method
------
Each author's given name is taken as the first whitespace-delimited token of the
full name (skipping bare initials such as "J." or "J"). That token is matched
against the gender_guesser dictionary (Joerg Michael's ~48k-name international
name list, the same data underlying the GNU `gender` utility). The detector
returns one of six raw labels:

    female, mostly_female, male, mostly_male, andy (androgynous), unknown

These are collapsed into three coded categories:

    female        <- female, mostly_female
    male          <- male, mostly_male
    undetermined  <- andy, unknown  (and any author with no usable given name)

Country prior (step a). If data/author_countries.csv is present, the author's
affiliation country (ISO-3166 alpha-2) is mapped to gender_guesser's country
vocabulary and passed to the detector. This resolves a subset of names that are
gender-ambiguous only in the aggregate but not within a country (for example,
"Andrea" -> female in Germany, male in Italy). The prior is applied only when
the country maps to a supported value AND the stored author name aligns with the
name at that position (a light first+last token check); otherwise the base,
country-free lookup is used. The prior does little for romanized Chinese given
names, which carry no gender signal once transliterated and remain undetermined
(those are the target of the optional Stage 8b Namsor pass).

The "undetermined" bucket is, by design, where difficult cases land: names not
in the dictionary, names equally common across genders, romanized Chinese and
some other names that carry little given-name gender signal, and authors listed
by initials only. "undetermined" is NOT imputed to either gender.

Limitations (recorded here for transparency, not as conclusions):
  - Name-based gender inference is a noisy proxy and is more reliable for names
    of European origin than for many other naming traditions. Coverage gaps are
    non-random across regions, so the undetermined share will itself vary by the
    composition of authors.
  - The "authors" field is assumed to be in given-name-first order. Entries that
    are not (or that are mononyms / initials only) resolve to undetermined.

Replicability: requires the `gender-guesser` package (see requirements.txt);
otherwise standard library only. The classification is fully deterministic and
offline (no network, no API).
"""

import csv
import hashlib
import os
import sys
from collections import Counter
from datetime import datetime, timezone

import gender_guesser.detector as gender_detector

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_DIR = os.path.abspath(os.path.join(SCRIPT_DIR, ".."))

SRC = os.path.join(PROJECT_DIR, "data", "final_dataset.csv")
COUNTRIES = os.path.join(PROJECT_DIR, "data", "author_countries.csv")
OUT_AUTHORS = os.path.join(PROJECT_DIR, "data", "author_gender.csv")
OUT_PAPERS = os.path.join(PROJECT_DIR, "data", "paper_gender_summary.csv")
LOG_TXT = os.path.join(PROJECT_DIR, "data", "08_gender_classify.log")

# Map the detector's six raw labels onto three coded categories.
RAW_TO_CODED = {
    "female": "female",
    "mostly_female": "female",
    "male": "male",
    "mostly_male": "male",
    "andy": "undetermined",
    "unknown": "undetermined",
}

# ISO-3166 alpha-2 -> gender_guesser country vocabulary. Only countries the
# detector supports are mapped; everything else falls back to the country-free
# lookup. Arabic-script and Central-Asian groupings follow the detector's own
# coarse buckets ("arabia", "the_stans").
ISO2_TO_GG = {
    "GB": "great_britain", "IE": "ireland", "US": "usa", "IT": "italy",
    "MT": "malta", "PT": "portugal", "ES": "spain", "FR": "france",
    "BE": "belgium", "LU": "luxembourg", "NL": "the_netherlands",
    "DE": "germany", "AT": "austria", "CH": "swiss", "IS": "iceland",
    "DK": "denmark", "NO": "norway", "SE": "sweden", "FI": "finland",
    "EE": "estonia", "LV": "latvia", "LT": "lithuania", "PL": "poland",
    "CZ": "czech_republic", "SK": "slovakia", "HU": "hungary",
    "RO": "romania", "BG": "bulgaria", "BA": "bosniaand", "HR": "croatia",
    "XK": "kosovo", "MK": "macedonia", "ME": "montenegro", "RS": "serbia",
    "SI": "slovenia", "AL": "albania", "GR": "greece", "RU": "russia",
    "BY": "belarus", "MD": "moldova", "UA": "ukraine", "AM": "armenia",
    "AZ": "azerbaijan", "GE": "georgia", "TR": "turkey", "IL": "israel",
    "CN": "china", "IN": "india", "JP": "japan", "KR": "korea", "VN": "vietnam",
    # Arabic-speaking countries -> "arabia"
    "SA": "arabia", "AE": "arabia", "EG": "arabia", "JO": "arabia",
    "LB": "arabia", "IQ": "arabia", "KW": "arabia", "QA": "arabia",
    "BH": "arabia", "OM": "arabia", "YE": "arabia", "SY": "arabia",
    "MA": "arabia", "DZ": "arabia", "TN": "arabia", "LY": "arabia",
    "SD": "arabia", "PS": "arabia",
    # Central-Asian "stans"
    "KZ": "the_stans", "UZ": "the_stans", "TM": "the_stans",
    "TJ": "the_stans", "KG": "the_stans", "AF": "the_stans",
}

AUTHOR_COLS = [
    "openalex_id", "doi", "publication_year", "journal_short",
    "author_position", "n_authors", "author_name",
    "first_name_used", "country_iso2", "gg_country",
    "gender_raw", "gender_coded",
]

PAPER_COLS = [
    "openalex_id", "doi", "publication_year", "journal_short", "title",
    "n_authors", "n_female", "n_male", "n_undetermined", "n_determined",
    "share_female_of_determined", "any_female",
    "first_author_gender", "last_author_gender", "all_undetermined",
]


def log(msg):
    line = f"[{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%SZ')}] {msg}"
    print(line, flush=True)
    with open(LOG_TXT, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def file_sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def short_id(openalex_id):
    return (openalex_id or "").rstrip("/").rsplit("/", 1)[-1]


def given_name(full_name):
    """Return the token used as the given name, or '' if none is usable.

    Takes the first whitespace-delimited token, skipping bare initials such as
    "J." or "J". Trailing periods/commas are stripped; the token is returned in
    its original case (the detector is configured case-insensitive).
    """
    for tok in full_name.split():
        cleaned = tok.strip(".,").strip()
        # A bare initial (single letter, with or without a period) carries no
        # given-name signal; move on to the next token.
        if len(cleaned) >= 2:
            return cleaned
    return ""


def name_key(full_name):
    """Normalized (first, last) token pair for sanity-checking the country join."""
    toks = [t.strip(".,").lower() for t in full_name.split() if t.strip(".,")]
    if not toks:
        return ("", "")
    return (toks[0], toks[-1])


def split_authors(field):
    """Split the semicolon-delimited authors field into a clean list of names."""
    if not field:
        return []
    return [a.strip() for a in field.split(";") if a.strip()]


def load_country_prior():
    """(openalex_short_id, position) -> (iso2, name_key) from Stage 8a, if present."""
    if not os.path.exists(COUNTRIES):
        return {}
    prior = {}
    with open(COUNTRIES, encoding="utf-8") as f:
        for r in csv.DictReader(f):
            try:
                pos = int(r["author_position"])
            except (KeyError, ValueError):
                continue
            key = (short_id(r.get("openalex_id", "")), pos)
            prior[key] = (
                (r.get("country_iso2") or "").strip().upper(),
                name_key(r.get("author_name", "")),
            )
    return prior


def main():
    open(LOG_TXT, "w").close()

    if not os.path.exists(SRC):
        sys.exit(f"Source not found: {SRC}")
    log(f"Source: {SRC}")

    detector = gender_detector.Detector(case_sensitive=False)

    prior = load_country_prior()
    if prior:
        log(f"Country prior: {len(prior):,} author-country records loaded from "
            f"{os.path.basename(COUNTRIES)}")
    else:
        log("Country prior: none (data/author_countries.csv absent); "
            "using country-free lookup")

    with open(SRC, encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    log(f"  loaded {len(rows):,} papers")

    author_records = []
    paper_records = []

    raw_counter = Counter()
    coded_counter = Counter()
    papers_no_authors = 0

    # Diagnostics for the country prior's effect.
    prior_applied = 0          # authors where a supported country was used
    prior_resolved = 0         # base-undetermined -> determined via the prior
    prior_flipped = 0          # male<->female change due to the prior (audit flag)
    join_name_mismatch = 0     # position join had a non-matching name; prior skipped

    for r in rows:
        oa_id = r.get("openalex_id", "")
        oa_short = short_id(oa_id)
        doi = r.get("doi", "")
        year = r.get("publication_year", "")
        journal = r.get("journal_short", "")
        title = r.get("title", "")

        authors = split_authors(r.get("authors", ""))
        n = len(authors)
        if n == 0:
            papers_no_authors += 1

        coded_seq = []
        for i, name in enumerate(authors, start=1):
            gn = given_name(name)

            # Base (country-free) classification.
            raw_base = detector.get_gender(gn) if gn else "unknown"
            coded_base = RAW_TO_CODED.get(raw_base, "undetermined")

            # Apply the country prior when available, supported, and the
            # position-join name aligns. gender_guesser's get_gender(name,
            # country) consults ONLY that country's column, so it returns andy /
            # unknown whenever the column is sparse even for a name that is
            # confident in the aggregate. We therefore accept the country result
            # only when it is itself determinate, and otherwise keep the base
            # (country-free) lookup. This lets the prior RESOLVE undetermined
            # names and CORRECT region-flipping ones (e.g. "Andrea" -> male in
            # Italy) without ever downgrading a confident base answer.
            iso2, gg_country = "", ""
            raw, coded = raw_base, coded_base
            rec = prior.get((oa_short, i))
            if rec and gn:
                iso2, stored_key = rec
                if iso2 and stored_key == name_key(name):
                    gg = ISO2_TO_GG.get(iso2)
                    if gg:
                        raw_c = detector.get_gender(gn, gg)
                        coded_c = RAW_TO_CODED.get(raw_c, "undetermined")
                        if coded_c != "undetermined":
                            gg_country = gg
                            raw, coded = raw_c, coded_c
                            prior_applied += 1
                            if coded_base == "undetermined":
                                prior_resolved += 1
                            elif {coded_base, coded} == {"male", "female"}:
                                prior_flipped += 1
                elif iso2 == "" or stored_key != name_key(name):
                    if stored_key != name_key(name):
                        join_name_mismatch += 1

            raw_counter[raw] += 1
            coded_counter[coded] += 1
            coded_seq.append(coded)

            author_records.append({
                "openalex_id": oa_id,
                "doi": doi,
                "publication_year": year,
                "journal_short": journal,
                "author_position": i,
                "n_authors": n,
                "author_name": name,
                "first_name_used": gn,
                "country_iso2": iso2,
                "gg_country": gg_country,
                "gender_raw": raw,
                "gender_coded": coded,
            })

        n_female = coded_seq.count("female")
        n_male = coded_seq.count("male")
        n_undet = coded_seq.count("undetermined")
        n_det = n_female + n_male

        paper_records.append({
            "openalex_id": oa_id,
            "doi": doi,
            "publication_year": year,
            "journal_short": journal,
            "title": title,
            "n_authors": n,
            "n_female": n_female,
            "n_male": n_male,
            "n_undetermined": n_undet,
            "n_determined": n_det,
            "share_female_of_determined": (
                f"{n_female / n_det:.4f}" if n_det > 0 else ""
            ),
            "any_female": "TRUE" if n_female > 0 else "FALSE",
            "first_author_gender": coded_seq[0] if coded_seq else "",
            "last_author_gender": coded_seq[-1] if coded_seq else "",
            "all_undetermined": "TRUE" if n > 0 and n_det == 0 else "FALSE",
        })

    # ---- write author-level file -------------------------------------------
    log(f"Writing {OUT_AUTHORS}")
    with open(OUT_AUTHORS, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=AUTHOR_COLS, extrasaction="ignore")
        w.writeheader()
        w.writerows(author_records)
    log(f"  {len(author_records):,} author rows")
    log(f"  SHA-256: {file_sha256(OUT_AUTHORS)}")

    # ---- write paper-level summary -----------------------------------------
    log(f"Writing {OUT_PAPERS}")
    with open(OUT_PAPERS, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=PAPER_COLS, extrasaction="ignore")
        w.writeheader()
        w.writerows(paper_records)
    log(f"  {len(paper_records):,} paper rows")
    log(f"  SHA-256: {file_sha256(OUT_PAPERS)}")

    # ---- summary to log -----------------------------------------------------
    total_authors = len(author_records)
    log("")
    log(f"Author-level totals (n = {total_authors:,} author-appearances):")
    log("  Raw detector labels:")
    for lab in ["female", "mostly_female", "male", "mostly_male", "andy", "unknown"]:
        c = raw_counter.get(lab, 0)
        log(f"    {lab:14s} {c:>7,d}  ({100 * c / max(total_authors, 1):5.1f}%)")
    log("  Coded categories:")
    for lab in ["female", "male", "undetermined"]:
        c = coded_counter.get(lab, 0)
        log(f"    {lab:14s} {c:>7,d}  ({100 * c / max(total_authors, 1):5.1f}%)")

    det = coded_counter.get("female", 0) + coded_counter.get("male", 0)
    if det:
        log(f"  Female share among determined authors: "
            f"{100 * coded_counter.get('female', 0) / det:.1f}%")

    if prior:
        log("")
        log("Country-prior (step a) effect:")
        log(f"  prior applied to:                {prior_applied:,} authors")
        log(f"  undetermined -> determined:      {prior_resolved:,}")
        log(f"  male<->female changes (audit):   {prior_flipped:,}")
        if join_name_mismatch:
            log(f"  position-join name mismatches:   {join_name_mismatch:,} "
                f"(prior skipped for these)")

    n_any_female = sum(1 for p in paper_records if p["any_female"] == "TRUE")
    n_all_undet = sum(1 for p in paper_records if p["all_undetermined"] == "TRUE")
    log("")
    log(f"Paper-level: {len(paper_records):,} papers")
    log(f"  with >=1 inferred-female author: {n_any_female:,} "
        f"({100 * n_any_female / max(len(paper_records), 1):.1f}%)")
    log(f"  with all authors undetermined:   {n_all_undet:,} "
        f"({100 * n_all_undet / max(len(paper_records), 1):.1f}%)")
    if papers_no_authors:
        log(f"  papers with no authors listed:   {papers_no_authors:,}")
    log("Stage 8 complete.")


if __name__ == "__main__":
    main()
