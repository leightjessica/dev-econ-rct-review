#!/usr/bin/env python3
"""Stage 13 (LOCAL/RULE-BASED variant) - deterministic IRB-location extraction.

This is a no-API-key alternative to 13_irb_classify.py. It produces the SAME
output file (data/irb_classified.csv) with the SAME columns and reuses the SAME
deterministic study-vs-IRB comparison (classify_location). The ONLY difference
is the extraction step: instead of asking the Anthropic API to read each snippet
and name the approving body + its country, this script matches the snippet text
against a curated dictionary of institution-name fragments -> ISO 3166-1 alpha-3
country codes.

Trade-off (be honest in the writeup): recall is lower than an LLM pass. Any
approving body NOT in INSTITUTIONS below falls through to `undetermined`
(institution mentioned, country not identifiable). The dictionary was seeded
empirically from the actual snippets in irb_mentions.csv, so the common bodies
are covered, but a long tail will be missed. Rows are stamped
irb_llm_model="local-rules-v1" so a local run is never confused with a Sonnet run.

Snippets are normalised to lowercase with all non-alphanumerics removed before
matching, because many Elsevier snippets are "despaced" (e.g.
"institutionalreviewboardofmcgilluniversity"). Dictionary keys are therefore
also written in despaced form.

Run (from project root, no API key needed):
  py scripts\\13_irb_classify_local.py
  SMOKE_N=10 py scripts\\13_irb_classify_local.py     # quick look (env flag)
"""

import csv
import os
import re
import sys
from datetime import datetime, timezone

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_DIR = os.path.abspath(os.path.join(SCRIPT_DIR, ".."))
IRB_CSV = os.path.join(PROJECT_DIR, "data", "irb_mentions.csv")
COUNTRY_CSV = os.path.join(PROJECT_DIR, "data", "country_classified.csv")
OUT_CSV = os.path.join(PROJECT_DIR, "data", "irb_classified.csv")
LOG_TXT = os.path.join(PROJECT_DIR, "data", "13_irb_classify_local.log")
MISS_TXT = os.path.join(PROJECT_DIR, "data", "13_irb_classify_local_misses.log")

SMOKE_N = int(os.environ.get("SMOKE_N", "0"))
MODEL_STAMP = "local-rules-v1"
PROMPT_STAMP = "local-dict-v1"

FIELDS = [
    "doi", "journal_short", "publication_year", "title",
    "study_setting", "study_iso3_list",
    "irb_status", "irb_terms_matched",
    "irb_institutions", "irb_iso3_list",
    "irb_country_identified", "irb_in_study_country",
    "irb_location_class", "irb_n_institutions",
    "irb_approval_status", "irb_justification",
    "irb_llm_model", "irb_llm_prompt_version", "snapshot_utc",
]

# ---- Institution-fragment -> (display name, ISO3) dictionary -----------------
# Keys are DESPACED lowercase substrings searched within the normalised snippet.
# Order matters only for the display name; ISO3 set membership is what counts.
# Keep fragments specific enough to avoid false positives (e.g. do NOT add a bare
# "academyofsciences" because PNAS = "proceedings of the national academy of
# sciences" appears in citation text and is not an IRB).
INSTITUTIONS = [
    # --- United States universities / orgs ---
    ("columbiauniversity", "Columbia University", "USA"),
    ("yaleuniversity", "Yale University", "USA"),
    ("harvarduniversity", "Harvard University", "USA"),
    ("harvardirb", "Harvard University", "USA"),
    ("stanforduniversity", "Stanford University", "USA"),
    ("princetonuniversity", "Princeton University", "USA"),
    ("dukeuniversity", "Duke University", "USA"),
    ("theduke", "Duke University", "USA"),
    ("northeasternuniversity", "Northeastern University", "USA"),
    ("brownuniversity", "Brown University", "USA"),
    ("universityofchicago", "University of Chicago", "USA"),
    ("universityofwashington", "University of Washington", "USA"),
    ("universityofvirginia", "University of Virginia", "USA"),
    ("universityofmichigan", "University of Michigan", "USA"),
    ("universityofrochester", "University of Rochester", "USA"),
    ("universityofcalifornia", "University of California", "USA"),
    ("universityofpennsylvania", "University of Pennsylvania", "USA"),
    ("newyorkuniversity", "New York University", "USA"),
    ("cornelluniversity", "Cornell University", "USA"),
    ("dartmouth", "Dartmouth College", "USA"),
    ("georgetownuniversity", "Georgetown University", "USA"),
    ("massachusettsinstituteoftechnology", "MIT", "USA"),
    ("bloombergschoolofpublic", "Johns Hopkins (Bloomberg School)", "USA"),
    ("johnshopkins", "Johns Hopkins University", "USA"),
    ("nationalbureauofeconomic", "NBER", "USA"),
    ("randshumansubjects", "RAND Corporation", "USA"),
    ("randcorporation", "RAND Corporation", "USA"),
    ("innovationsforpovertyaction", "Innovations for Poverty Action", "USA"),
    ("irbatipa", "Innovations for Poverty Action", "USA"),
    ("ipahumansub", "Innovations for Poverty Action", "USA"),
    ("worldbank", "World Bank", "USA"),
    # --- J-PAL: generic = MIT/USA; South Asia office = IFMR/India ---
    ("jpalsouthasi", "J-PAL South Asia (IFMR)", "IND"),
    ("ifmrhumansub", "IFMR", "IND"),
    ("ifmr", "IFMR", "IND"),
    # --- Canada ---
    ("mcgilluniversity", "McGill University", "CAN"),
    ("universityoftoronto", "University of Toronto", "CAN"),
    ("universityofbritishcolumbia", "University of British Columbia", "CAN"),
    # --- United Kingdom ---
    ("universitycollegelondon", "University College London", "GBR"),
    ("londonschoolofeconomics", "London School of Economics", "GBR"),
    ("londonschoolofhygiene", "LSHTM", "GBR"),
    ("universityofoxford", "University of Oxford", "GBR"),
    ("universityofcambridge", "University of Cambridge", "GBR"),
    ("universityofessex", "University of Essex", "GBR"),
    ("universityofeastang", "University of East Anglia", "GBR"),
    ("universityofwarwick", "University of Warwick", "GBR"),
    ("universityofmanchester", "University of Manchester", "GBR"),
    ("universityofnottingham", "University of Nottingham", "GBR"),
    ("universityofsussex", "University of Sussex", "GBR"),
    ("edinburghschoolofsocial", "University of Edinburgh", "GBR"),
    ("universityofedinburgh", "University of Edinburgh", "GBR"),
    # --- Continental Europe ---
    ("bocconiuniversity", "Bocconi University", "ITA"),
    ("ofboccon", "Bocconi University", "ITA"),
    ("universityofzurich", "University of Zurich", "CHE"),
    ("nhhinstitutional", "NHH Norwegian School of Economics", "NOR"),
    ("norwegianschoolofeconomics", "NHH Norwegian School of Economics", "NOR"),
    ("universitypompeufabra", "Universitat Pompeu Fabra", "ESP"),
    ("pompeufabra", "Universitat Pompeu Fabra", "ESP"),
    ("parisschoolofeconomics", "Paris School of Economics", "FRA"),
    ("wageningenuniversity", "Wageningen University", "NLD"),
    ("tilburguniversity", "Tilburg University", "NLD"),
    ("universityofgothenburg", "University of Gothenburg", "SWE"),
    ("stockholmschoolofeconomics", "Stockholm School of Economics", "SWE"),
    ("universityofnamur", "University of Namur", "BEL"),
    ("kuleuven", "KU Leuven", "BEL"),
    # --- Ireland ---
    ("trinitycollegedublin", "Trinity College Dublin", "IRL"),
    ("universitycollegedublin", "University College Dublin", "IRL"),
    # --- Australia ---
    ("australiannationaluniversity", "Australian National University", "AUS"),
    ("universityofmelbourne", "University of Melbourne", "AUS"),
    ("monashuniversity", "Monash University", "AUS"),
    # --- India ---
    ("indianstatisticalinstitute", "Indian Statistical Institute", "IND"),
    ("instituteforfinancialmanagement", "IFMR", "IND"),
    ("kreauniversity", "KREA University", "IND"),
    ("indianinstituteofmanagement", "Indian Institute of Management", "IND"),
    ("goodbusinesslabsethicscommitteeinindia", "Good Business Lab (India)", "IND"),
    ("itamsinstitutional", "ITAM", "MEX"),  # bare "itam" omitted: matches "vitamin"
    # --- Kenya ---
    ("masenouniversity", "Maseno University", "KEN"),
    ("universityofnairobi", "University of Nairobi", "KEN"),
    ("kenyamedicalresearch", "KEMRI", "KEN"),
    ("kemri", "KEMRI", "KEN"),
    # --- Uganda ---
    ("makerereuniversity", "Makerere University", "UGA"),
    ("ugandanationalcouncilforscien", "Uganda National Council for Science & Technology", "UGA"),
    # --- Malawi ---
    ("universityofmalawi", "University of Malawi (College of Medicine)", "MWI"),
    ("malawinationalcommittee", "Malawi National Committee on Research", "MWI"),
    ("malawinationalhealthsciences", "Malawi National Health Sciences Research Committee", "MWI"),
    # --- Mozambique ---
    ("mozambiqueministryofhealth", "Mozambique Ministry of Health", "MOZ"),
    # --- Senegal ---
    ("senegalsirb", "Senegal national IRB", "SEN"),
    # --- South Africa ---
    ("universityofcapetown", "University of Cape Town", "ZAF"),
    ("capetowncommerce", "University of Cape Town", "ZAF"),
    ("universityofthewitwatersrand", "University of the Witwatersrand", "ZAF"),
    ("universityofpretoria", "University of Pretoria", "ZAF"),

    # === Expansion round 2 (seeded from 13_irb_classify_local_misses.log) =====
    # USA: "<school>institutionalreviewboard" surface forms + more institutions
    ("harvardinstitut", "Harvard University", "USA"),
    ("stanfordinstitut", "Stanford University", "USA"),
    ("mitinstitutionalreviewboard", "MIT", "USA"),
    ("ucberkeley", "UC Berkeley", "USA"),
    ("berkeleycommitteefor", "UC Berkeley", "USA"),
    ("michiganstateuniversity", "Michigan State University", "USA"),
    ("universityofflorida", "University of Florida", "USA"),
    ("tuftsuniversity", "Tufts University", "USA"),
    ("northwesternuniversity", "Northwestern University", "USA"),
    ("universityoftexas", "University of Texas", "USA"),
    ("texasataustin", "University of Texas at Austin", "USA"),
    ("heartlandinstitutionalreviewboard", "Heartland IRB", "USA"),
    ("chesapeakeinstitutionalreviewboard", "Chesapeake IRB", "USA"),
    ("ipasirb", "Innovations for Poverty Action", "USA"),
    ("ipairb", "Innovations for Poverty Action", "USA"),
    # GBR
    ("oxforduniversity", "University of Oxford", "GBR"),
    ("lseethicscommittee", "London School of Economics", "GBR"),
    ("lseref", "London School of Economics", "GBR"),
    ("oflse", "London School of Economics", "GBR"),
    ("internationalgrowthcentre", "International Growth Centre (LSE/Oxford)", "GBR"),
    # Continental Europe
    ("europeanuniversityinstitute", "European University Institute", "ITA"),
    ("universityofgroningen", "University of Groningen", "NLD"),
    ("novaschoolofbus", "Nova School of Business (Lisbon)", "PRT"),
    ("universidadenova", "Universidade Nova de Lisboa", "PRT"),
    # Pakistan
    ("lahoreschoolofeconomics", "Lahore School of Economics", "PAK"),
    ("lahoreuniversityofmanagement", "Lahore University of Management Sciences", "PAK"),
    # Kenya
    ("strathmoreuniversity", "Strathmore University", "KEN"),
    # China
    ("renminuniversity", "Renmin University of China", "CHN"),
    # Rwanda
    ("rwandanationalethics", "Rwanda National Ethics Committee", "RWA"),
    # Nigeria
    ("committeeofnigeria", "National Health Research Ethics Committee of Nigeria", "NGA"),
    # Tanzania
    ("ifakarahealthinstitute", "Ifakara Health Institute", "TZA"),
    # Senegal
    ("cners", "CNERS (Senegal national ethics committee)", "SEN"),

    # === Expansion round 3 (remaining identifiable misses) ===================
    ("pekinguniversity", "Peking University", "CHN"),
    ("beijingnormaluniversity", "Beijing Normal University", "CHN"),
    ("universityincairo", "American University in Cairo", "EGY"),
    ("universityatcairo", "American University in Cairo", "EGY"),
    ("universityofzambia", "University of Zambia", "ZMB"),
    ("chapelhill", "UNC Chapel Hill", "USA"),
    ("ucsd", "UC San Diego", "USA"),
    ("itaminstitutional", "ITAM", "MEX"),
    ("fgvsresearchethics", "Fundacao Getulio Vargas", "BRA"),
    ("fundacaogetulio", "Fundacao Getulio Vargas", "BRA"),
    ("universityofbristol", "University of Bristol", "GBR"),
    ("copenhagenbusinessschool", "Copenhagen Business School", "DNK"),
    ("kadirhasuniversity", "Kadir Has University", "TUR"),

    # === Expansion round 4 (surface-form + missing-institution misses) =======
    # These are bodies an LLM pass would also catch; most are dictionary gaps
    # (e.g. MIT written as "mitirb"/"COUHES" rather than "MIT University").
    # USA surface forms / additions
    ("mitirb", "MIT", "USA"),
    ("mitcouhes", "MIT", "USA"),
    ("couhes", "MIT", "USA"),                 # MIT's IRB (Cmte on Use of Humans)
    ("mitprotocol", "MIT", "USA"),
    ("frommit", "MIT", "USA"),
    ("stanfordirb", "Stanford University", "USA"),
    ("stanfordprotocol", "Stanford University", "USA"),
    ("fromipa", "Innovations for Poverty Action", "USA"),
    ("andipa", "Innovations for Poverty Action", "USA"),
    ("ipaprotocol", "Innovations for Poverty Action", "USA"),
    ("berkeleyoffice", "UC Berkeley", "USA"),
    ("georgiastate", "Georgia State University", "USA"),
    ("universityofdelaware", "University of Delaware", "USA"),
    ("universityofmaryland", "University of Maryland", "USA"),
    ("uiuc", "University of Illinois Urbana-Champaign", "USA"),
    ("universityofillinois", "University of Illinois", "USA"),
    ("ucdavis", "UC Davis", "USA"),
    # Germany
    ("universityofmannheim", "University of Mannheim", "DEU"),
    ("economicslmu", "LMU Munich", "DEU"),
    # El Salvador (text names the country explicitly)
    ("universidadfranciscogavidia", "Universidad Francisco Gavidia", "SLV"),
    # Chile
    ("pontificiauniversidadcatolicadechile", "PUC Chile", "CHL"),
    ("pucchile", "PUC Chile", "CHL"),
    # Peru
    ("sanagustin", "Univ. Nacional de San Agustin (Peru)", "PER"),
    # Uganda
    ("murec", "Mildmay Uganda Research Ethics Committee", "UGA"),
    # Mali
    ("bamako", "Medical faculty, Bamako (Mali)", "MLI"),
    # Ethiopia
    ("amhara", "Amhara regional health bureau (Ethiopia)", "ETH"),
    # Sierra Leone
    ("governmentofsierraleone", "Government of Sierra Leone", "SLE"),
    # Singapore
    ("fromnus", "National University of Singapore", "SGP"),
    ("nationaluniversityofsingapore", "National University of Singapore", "SGP"),
    # Pakistan
    ("pakistanlums", "LUMS (Pakistan)", "PAK"),
    ("economicspakistanethicalreviewboard", "School of Economics Pakistan ethical review board", "PAK"),
    # Malawi
    ("malawincrsh", "Malawi NCRSH", "MWI"),
]

# Approval-status keyword detection (despaced).
EXEMPT_PAT = re.compile(r"(deemedexempt|wasexempt|exemptfrom|nothumansubjects|nothumansubjectsresearch|exemptdetermination)")
APPROVED_PAT = re.compile(r"(approval|approved|clearance|cleared|wasgranted|wasgrantedby|grantedby|permission|reviewedandapproved|ethicalapproval|ethicsapproval)")
MENTION_PAT = re.compile(r"(institutionalreviewboard|ethicscommittee|ethicsboard|irb|humansubjects|ethicalreview|ethicsreview|researchethics)")

ISO3_RE = re.compile(r"^[A-Z]{3}$")


def log(msg):
    line = f"[{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%SZ')}] {msg}"
    print(line, flush=True)
    with open(LOG_TXT, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def normalise(text):
    return re.sub(r"[^a-z0-9]", "", (text or "").lower())


def load_study_countries():
    """doi -> (study_setting, set(study_iso3))."""
    out = {}
    with open(COUNTRY_CSV, encoding="utf-8") as f:
        for r in csv.DictReader(f):
            doi = (r.get("doi") or "").strip().lower()
            if not doi:
                continue
            iso = {c.strip() for c in (r.get("country_iso3_list") or "").split(";") if c.strip()}
            out[doi] = ((r.get("country_study_setting") or "").strip(), iso)
    return out


def classify_location(irb_iso3s, study_iso3s):
    """Deterministic comparison (identical to 13_irb_classify.py)."""
    country_identified = len(irb_iso3s) > 0
    if not country_identified:
        return "undetermined", False, False
    if not study_iso3s:
        return "undetermined", False, True
    local = bool(irb_iso3s & study_iso3s)
    foreign = bool(irb_iso3s - study_iso3s)
    if local and foreign:
        cls = "both"
    elif local:
        cls = "local_only"
    else:
        cls = "foreign_only"
    return cls, local, True


def extract_local(snippets):
    """Rule-based extraction. Returns (names, iso3_set, approval_status)."""
    norm = normalise(snippets)
    names, iso3s = [], set()
    seen_frag = set()
    for frag, name, iso3 in INSTITUTIONS:
        if not iso3:
            continue
        if frag in norm and frag not in seen_frag:
            seen_frag.add(frag)
            if name and name not in names:
                names.append(name)
            if ISO3_RE.match(iso3):
                iso3s.add(iso3)
    if EXEMPT_PAT.search(norm):
        approval = "exempt"
    elif APPROVED_PAT.search(norm):
        approval = "approved"
    elif MENTION_PAT.search(norm):
        approval = "mentioned_only"
    else:
        approval = "unclear"
    return names, iso3s, approval


def main():
    open(LOG_TXT, "w").close()
    log(f"Stage 13 LOCAL (rule-based) start. dict={sum(1 for _,_,i in INSTITUTIONS if i)} mapped fragments  SMOKE_N={SMOKE_N}")

    study = load_study_countries()
    log(f"Loaded study-country attribution for {len(study)} papers")

    with open(IRB_CSV, encoding="utf-8") as f:
        irb_rows = list(csv.DictReader(f))
    log(f"Stage-12 IRB rows: {len(irb_rows)}")

    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    out_rows = []
    n_llm_like = 0  # rows that went through extraction (status ok)
    cls_counter = {}
    miss_lines = []

    todo_count = 0
    for r in irb_rows:
        doi = (r.get("doi") or "").strip().lower()
        setting, study_iso = study.get(doi, ("", set()))
        status = r.get("extraction_status") or ""
        base = {
            "doi": doi, "journal_short": r.get("journal_short", ""),
            "publication_year": r.get("publication_year", ""), "title": r.get("title", ""),
            "study_setting": setting, "study_iso3_list": ";".join(sorted(study_iso)),
            "irb_status": status, "irb_terms_matched": r.get("terms_matched", ""),
            "irb_institutions": "", "irb_iso3_list": "",
            "irb_country_identified": "", "irb_in_study_country": "",
            "irb_location_class": "", "irb_n_institutions": "",
            "irb_approval_status": "", "irb_justification": "",
            "irb_llm_model": "", "irb_llm_prompt_version": "", "snapshot_utc": stamp,
        }
        if status == "no_mention":
            base["irb_location_class"] = "no_mention"
            out_rows.append(base)
            continue
        if status != "ok":
            base["irb_location_class"] = "no_pdf" if status == "no_pdf" else (status or "no_pdf")
            out_rows.append(base)
            continue

        # status == ok -> rule-based extraction
        todo_count += 1
        if SMOKE_N and todo_count > SMOKE_N:
            base["irb_location_class"] = ""  # leave blank when smoke-limited
            out_rows.append(base)
            continue

        snippets = r.get("snippets", "") or ""
        names, iso_set, approval = extract_local(snippets)
        cls, in_study, identified = classify_location(iso_set, study_iso)
        base["irb_institutions"] = " ; ".join(names)
        base["irb_iso3_list"] = ";".join(sorted(iso_set))
        base["irb_country_identified"] = "TRUE" if identified else "FALSE"
        base["irb_in_study_country"] = "TRUE" if in_study else "FALSE"
        base["irb_location_class"] = cls
        base["irb_n_institutions"] = str(len(names))
        base["irb_approval_status"] = approval
        base["irb_justification"] = (
            f"Rule-based match: {', '.join(names)}" if names
            else "Rule-based: IRB/ethics text present but no dictionary institution matched"
        )
        base["irb_llm_model"] = MODEL_STAMP
        base["irb_llm_prompt_version"] = PROMPT_STAMP
        out_rows.append(base)
        n_llm_like += 1
        cls_counter[cls] = cls_counter.get(cls, 0) + 1
        if not names:  # log misses for dictionary expansion
            miss_lines.append(f"=== {doi}  ({setting}) ===\n{normalise(snippets)[:700]}\n")

    with open(OUT_CSV, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        w.writerows(out_rows)

    with open(MISS_TXT, "w", encoding="utf-8") as f:
        f.write("\n".join(miss_lines))

    # Summary over the full output.
    final_counts = {}
    for r in out_rows:
        c = r.get("irb_location_class") or "(blank)"
        final_counts[c] = final_counts.get(c, 0) + 1
    log("\n=== IRB location summary (all RCTs) ===")
    for c in sorted(final_counts):
        log(f"  {c:14s} {final_counts[c]}")
    comparable = sum(final_counts.get(c, 0) for c in ("local_only", "foreign_only", "both"))
    if comparable:
        loc = final_counts.get("local_only", 0)
        both = final_counts.get("both", 0)
        log(f"Of {comparable} RCTs with an identifiable IRB country AND a single study country: "
            f"{loc} local_only, {final_counts.get('foreign_only', 0)} foreign_only, {both} both "
            f"({100*(loc+both)/comparable:.0f}% have >=1 IRB in the study country)")
    log(f"ok-status rows processed: {n_llm_like}  "
        f"(of which {len(miss_lines)} matched NO dictionary institution -> undetermined; "
        f"see {os.path.basename(MISS_TXT)} to expand the dictionary)")
    log(f"\nOutput: {os.path.basename(OUT_CSV)}")


if __name__ == "__main__":
    main()
