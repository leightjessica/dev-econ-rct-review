#!/usr/bin/env python3
"""Stage 21b (LOCAL/RULE-BASED) - deterministic first pass at the subnational site.

This is the no-LLM sibling of 21_site_extract.py (PROJECT_MEMO Section 12.5,
"Route C"), in the same spirit as 13_irb_classify_local.py vis-a-vis
13_irb_classify.py. It runs over the SAME work-list as Stage 21 (single-country,
WB-covered RCTs with a matched PDF) and, for each paper, scans the full text for
the WB-admissible unit names of that country (data/wb_subnat_gazetteer.csv, at
the country's Stage-20 matching level), plus a curated alias table (spelling
variants, renamed units, English renderings, and a small set of major cities and
macro-regions that roll up unambiguously to one unit). Its purpose is to resolve
the easy single-region cases at zero cost and to shrink the LLM work-list to the
residual; it is NOT a substitute for the LLM on site scope or roll-up of
villages/towns.

Matching:
  * Text is accent-folded (NFKD, combining marks stripped). Gazetteer names are
    matched case-sensitively in Title or ALL-CAPS form (connector words such as
    "del", "de", "and" are case-free), so common-word collisions ("delta",
    "plateau", "central") are suppressed. Multi-word names tolerate hyphens and
    line breaks between tokens.
  * GENERIC names (directional or common-word units: "Central", "Northern",
    "Est", "Plateau", "Delta", "Niger", "Rivers", and units that share a name with
    their country, e.g. "Mexico", "Guatemala") count ONLY when an administrative
    noun is adjacent ("Northern Region", "Plateau State", "Region du Nord").
  * Grouped WB units (GAULx "A & B & C", Turkey NUTS2 "A, B") are matched on
    their constituent names; a constituent belonging to two groups credits both.
  * Longest names are matched first and their spans masked, so "Baja California
    Sur" is not also counted as "Baja California".
  * A hit is dropped as an affiliation if an institutional noun ("University",
    "Institute", ...) sits in the preceding 60 characters.
  * The body is scanned up to the References heading. Papers whose text has lost
    inter-word spaces (the Elsevier case, see Stage 24) are detected and scanned
    with relaxed word boundaries.

Scoring and status, per paper:
  ta   = hits in the title + abstract (from country_classified.csv)
  cue  = body hits with a site cue within CUE_WINDOW chars ("conducted in",
         "study area", "districts of", "baseline survey", ...)
  raw  = all body hits
  A unit is STRONG if ta > 0 or cue > 0.
  local_status:
    resolved_single  exactly one strong unit U, (raw(U) >= 2 or ta(U) > 0), and
                     every other unit has raw <= max(1, raw(U) // 4)
    multi_candidate  two or more strong units (multi-region site, or ambiguity)
    dominant_single  two or more strong units, but the top unit carries >= 75% of
                     the strong-unit score and raw >= 3 (the others are typically
                     comparison or passing mentions; needs a quick human check)
    single_weak      one strong unit that fails the dominance test
    weak_only        hits, but none in title/abstract or near a site cue
    no_hit           no admissible name found
  nationwide_signal is flagged separately ("nationally representative", "all
  districts", ...) and should be read alongside the status.
Fieldwork years are a HINT only: calendar years within FW_WINDOW chars of a
fieldwork cue ("baseline", "endline", "data collection", ...), capped at the
publication year.

Inputs:
  data/country_classified.csv, data/irb_mentions.csv,
  data/wb_subnat_coverage_profile.csv, data/wb_subnat_gazetteer.csv,
  fulltext/<matched_file>.pdf
Output:
  data/site_local_matched.csv   one row per RCT (pass-through rows carry the same
                                not_single_country / country_not_covered / no_pdf
                                statuses as Stage 21)
  data/21b_site_match_local.log

Env flags:
  SMOKE_N=10   only process the first N work-list papers
"""

import csv
import os
import re
import shutil
import subprocess
import sys
import unicodedata
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

csv.field_size_limit(min(sys.maxsize, 2147483647))

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
FULLTEXT = ROOT / "fulltext"
CC_CSV = DATA / "country_classified.csv"
MATCH_CSV = DATA / "irb_mentions.csv"
PROFILE_CSV = DATA / "wb_subnat_coverage_profile.csv"
GAZ_CSV = DATA / "wb_subnat_gazetteer.csv"
OUT_CSV = DATA / "site_local_matched.csv"
LOG_TXT = DATA / "21b_site_match_local.log"

RULES_VERSION = "site-local-v1"
SMOKE_N = int(os.environ.get("SMOKE_N", "0"))
PDFTOTEXT_TIMEOUT = 120
CUE_WINDOW = 150
FW_WINDOW = 80
AFFIL_WINDOW = 60
SNIPPET_PAD = 80
MAX_SNIPPETS = 6

GROUPED_TAGS = {"GAULx", "GADMx", "NUTSx", "UNx"}

# Connector words matched case-insensitively inside multi-word names.
CONNECTORS = {"de", "del", "do", "da", "dos", "das", "y", "and", "of", "la", "le",
              "les", "des", "du", "i", "el", "los", "las"}

# Units whose bare name is a common word, a direction, or the country's own name:
# count only next to an administrative noun.
GENERIC = {
    "Central", "Centre", "Centrale", "Eastern", "Western", "Northern", "Southern",
    "North", "South", "East", "West", "Est", "Nord", "Ouest", "Sud", "Littoral",
    "Plateau", "Plateaux", "Delta", "Niger", "Rivers", "Mexico", "Guatemala",
    "Panama", "Sahel", "Cascades", "Savanes", "Kara", "Mono", "Zou", "Collines",
    "Atlantique", "Bolivar", "Cordoba", "Sucre", "Meta", "Cesar", "Magdalena",
    "La Paz", "Amazonas", "Cauca", "Para", "Acre", "Mon", "Shan",
    "Chin", "Gaza", "Manica", "Sofia", "Diana", "Sava", "Mara", "Bie", "Zaire",
    "Huila", "Somali", "Georgia", "Victoria", "Aurora", "Antique",
    "Rizal", "Quezon", "Isabela", "Abra", "Capiz", "Samar", "Leyte", "Guaira",
    "Misiones", "Concepcion", "Cordillera", "San Pedro", "Est", "Centre-est",
    "Nord", "Sud", "Ouest", "North West", "Free State", "Benue", "Delta",
    "Plateau Central", "Mount Lebanon", "Red Sea", "New Valley", "Valle",
    "Colon", "Cortes", "Herrera", "Los Santos", "Bolama/bijagos", "Hardap",
    "Cabanas", "La Libertad", "La Union",
    "San Miguel", "Santa Ana", "San Vicente", "Upper River", "Lower River",
    "North Bank", "West Coast",
}
ADMIN_NOUN = (r"(?:Region|Regions|Province|Provinces|State|States|Department|"
              r"Departamento|Departement|District|Districts|County|Counties|"
              r"Division|Governorate|Prefecture|Oblast|Estado|Provincia|"
              r"Municipality)")
ADMIN_PRE = (r"(?:Region|Province|State|Department|Departamento|Departement|"
             r"Estado|Provincia|District|Governorate)\s+(?:of|de|du|del|de\s+la|"
             r"des)?\s*")

# Curated aliases: iso3 -> [(regex, unit_name or [unit_names])]. Regexes are
# case-insensitive and applied to the accent-folded text. Unit names must equal a
# gazetteer unit_name (checked at start-up). A list target credits each unit
# (e.g. Kigali spans three Rwandan districts).
ALIASES = {
    "IND": [
        (r"\bOdisha\b", "Orissa"), (r"\bUttaranchal\b", "Uttarakhand"),
        (r"\bChattisgarh\b", "Chhattisgarh"), (r"\bPondicherry\b", "Puducherry"),
        (r"\bNew\s+Delhi\b", "Delhi"),
        (r"\b(?:Mumbai|Bombay|Pune)\b", "Maharashtra"),
        (r"\b(?:Kolkata|Calcutta)\b", "West Bengal"),
        (r"\b(?:Chennai|Madras)\b", "Tamil Nadu"),
        (r"\b(?:Bangalore|Bengaluru)\b", "Karnataka"),
        (r"\b(?:Lucknow|Varanasi)\b", "Uttar Pradesh"),
        (r"\bPatna\b", "Bihar"), (r"\b(?:Jaipur|Udaipur)\b", "Rajasthan"),
        (r"\bAhmedabad\b", "Gujarat"), (r"\bBhubaneswar\b", "Orissa"),
        (r"\bBhopal\b", "Madhya Pradesh"), (r"\bRanchi\b", "Jharkhand"),
    ],
    # Districts commonly used as RCT sites -> division (Rangpur split from Rajshahi
    # in 2010 and Mymensingh from Dhaka in 2015; the WB keeps Rangpur, not Mymensingh).
    "BGD": [(r"\bChattogram\b|\bCox.?s\s+Bazar\b|\bComilla\b|\bCumilla\b|\bNoakhali\b|"
             r"\bFeni\b|\bChandpur\b|\bBrahmanbaria\b", "Chittagong"),
            (r"\bBarishal\b|\bPatuakhali\b|\bBhola\b|\bBarguna\b", "Barisal"),
            (r"\bMymensingh\b|\bJamalpur\b|\bNetrokona\b|\bSherpur\b|\bTangail\b|"
             r"\bGazipur\b|\bNarayanganj\b|\bKishoreganj\b|\bManikganj\b|\bFaridpur\b", "Dhaka"),
            (r"\bKurigram\b|\bGaibandha\b|\bLalmonirhat\b|\bNilphamari\b|\bDinajpur\b|"
             r"\bThakurgaon\b|\bPanchagarh\b", "Rangpur"),
            (r"\bBogra\b|\bBogura\b|\bSirajganj\b|\bPabna\b|\bNatore\b|\bNaogaon\b", "Rajshahi"),
            (r"\bSatkhira\b|\bJessore\b|\bJashore\b|\bBagerhat\b|\bKushtia\b", "Khulna"),
            (r"\bSunamganj\b|\bHabiganj\b|\bMoulvibazar\b", "Sylhet")],
    "PAK": [(r"\bBaluchistan\b", "Balochistan"), (r"\bKarachi\b|\bHyderabad\b", "Sindh"),
            (r"\bQuetta\b", "Balochistan")],
    "COL": [(r"\bMedell[ií]n\b", "Antioquia"), (r"\bCali\b", "Valle Del Cauca"),
            (r"\bBarranquilla\b", "Atlantico"), (r"\bCartagena\b", "Bolivar"),
            (r"\bBucaramanga\b", "Santander"), (r"\bC[uú]cuta\b", "Norte De Santander"),
            (r"\bLa\s+Guajira\b", "Guajira"), (r"\bNari[nñ]o\b", "Narino")],
    "GHA": [(r"\bBrong[-\s]+Ahafo\b", "Brong Ahafo"), (r"\bAccra\b", "Greater Accra"),
            (r"\bKumasi\b", "Ashanti"), (r"\bTamale\b", "Northern"),
            (r"\bnorthern\s+Ghana\b", "Northern")],
    "NGA": [(r"\b(?:FCT|Federal\s+Capital\s+Territory)\b", "Abuja"),
            (r"\bNasarawa\b", "Nassarawa"), (r"\bIbadan\b", "Oyo")],
    "TZA": [(r"\bDar\s+es[-\s]+Salaam\b", "Dar es Salaam"), (r"\bZanzibar\b", None)],
    "KEN": [(r"\bTharaka[-\s]+Nithi\b", "Tharaka-Nithi"),
            (r"\bElgeyo[-\s]+Marakwet\b", "Elgeyo-Marakwet"),
            (r"\bMurang.?a\b", "Murang'a"), (r"\bTaita[-\s]+Taveta\b", "Taita Taveta")],
    "ETH": [(r"\bSNNP\b|Southern\s+Nations", "SNNPR"),
            (r"\bBenishangul\b", "Beneshangul Gumu"), (r"\bHarari?\b", "Hareri"),
            (r"\bTigrai\b", "Tigray"), (r"\bAmhara\b", "Amhara")],
    "MEX": [(r"\bMexico\s+City\b|\bCiudad\s+de\s+Mexico\b", "Distrito Federal"),
            (r"\b(?:State\s+of\s+Mexico|Estado\s+de\s+Mexico|Mexico\s+State)\b", "Mexico")],
    "IDN": [(r"\bWest\s+Java\b", "Jawa Barat"), (r"\bCentral\s+Java\b", "Jawa Tengah"),
            (r"\bEast\s+Java\b", "Jawa Timur"),
            (r"\b(?:Yogyakarta|Jogjakarta)\b", "Daerah Istimewa Yogyakarta"),
            (r"\bJakarta\b", "Dki Jakarta"), (r"\bAceh\b", "Nangroe Aceh Darussalam"),
            (r"\bRiau\s+Islands\b|Kepulauan\s+Riau", "Kepulauan-riau"),
            (r"\bWest\s+Nusa\s+Tenggara\b|Nusa\s+Tenggara\s+Barat", "Nusatenggara Barat"),
            (r"\bEast\s+Nusa\s+Tenggara\b|Nusa\s+Tenggara\s+Timur", "Nusatenggara Timur"),
            (r"\bNorth\s+Sumatra\b", "Sumatera Utara"), (r"\bWest\s+Sumatra\b", "Sumatera Barat"),
            (r"\bSouth\s+Sumatra\b", "Sumatera Selatan"),
            (r"\bSouth\s+Sulawesi\b", "Sulawesi Selatan"), (r"\bNorth\s+Sulawesi\b", "Sulawesi Utara"),
            (r"\bCentral\s+Sulawesi\b", "Sulawesi Tengah"),
            (r"\bSoutheast\s+Sulawesi\b", "Sulawesi Tenggara"), (r"\bWest\s+Sulawesi\b", "Sulawesi Barat"),
            (r"\bWest\s+Kalimantan\b", "Kalimantan Barat"), (r"\bEast\s+Kalimantan\b", "Kalimantan Timur"),
            (r"\bSouth\s+Kalimantan\b", "Kalimantan Selatan"),
            (r"\bCentral\s+Kalimantan\b", "Kalimantan Tengah"),
            (r"\bNorth\s+Maluku\b", "Maluku Utara"), (r"\bWest\s+Papua\b", "Papua Barat")],
    "PHL": [(r"\bMetro(?:politan)?\s+Manila\b|\bManila\b|\bQuezon\s+City\b",
             "Metropolitan Manila"),
            (r"\bDavao\s+City\b", "Davao Del Sur"), (r"\bCebu\s+City\b", "Cebu")],
    "RWA": [(r"\bKigali\b", ["Gasabo", "Kicukiro", "Nyarugenge"])],
    "BRA": [(r"\bSao\s+Paulo\b", "Sao Paulo")],
    "CHL": [(r"\bAy?sen\b|\bAisen\b","Aisen del Gral. Carlos Ibañez del Campo"),
            (r"\bO.?Higgins\b", "Libertador Gral. Bernardo O'Higgins"),
            (r"\bMagallanes\b", "Magallanes y Antartica chilena"),
            (r"\bArica\b", "Arica y Painacota"),
            (r"\bSantiago\b|Region\s+Metropolitana", "Metropolitana"),
            (r"\bBio[-\s]?bio\b", "Biobio")],
    "SEN": [(r"\bSaint[-\s]+Louis\b|\bSt\.?\s+Louis\b", "Saint louis")],
    "SLE": [(r"\bBo\s+District\b|\bBo\s+town\b", "Bo")],
    "KAZ": [(r"\bAlmaty\b", "Almaty City area")],
    "MKD": [(r"\bSkopje\b", "Skopski")],
    "GNB": [(r"\bBissau\b", "Sector Autonomo De Bissau")],
    "TJK": [(r"\bGBAO\b", "Gorno-Badakhshan"), (r"\bSogd\b|\bSughd\b", "Sughd")],
    "ZAF": [(r"\b(?:Johannesburg|Soweto|Pretoria|Tshwane)\b", "Gauteng"),
            (r"\bCape\s+Town\b", "Western Cape"), (r"\bDurban\b", "KwaZulu-Natal")],
    "PER": [(r"\bLima\b", "Lima"), (r"\bCuzco\b", "Cusco")],
    "UGA": [(r"\bnorthern\s+Uganda\b", "@N"), (r"\beastern\s+Uganda\b", "@E"),
            (r"\bwestern\s+Uganda\b", "@W"), (r"\bcentral\s+Uganda\b", "@C")],
}
# Supplementary WB units that exist in the SPID but at a level OTHER than the
# country's Stage-20 matching level, and so are missing from the gazetteer even
# though they fill a gap in its tiling (e.g. Pakistan's GAUL1 layer carries only
# Sindh and Balochistan; Punjab and KP sit in GAULx groupings). Matched by alias
# regex only. These should eventually move into Stage 20; until then Stage 22
# must resolve them against the SPID at the level given here.
SUPPLEMENTARY = {
    "PAK": [("Punjab & Islamabad", "GAULx",
             r"\bPunjab\b|\bIslamabad\b|\bLahore\b|\bFaisalabad\b|\bRawalpindi\b|"
             r"\bMultan\b|\bGujranwala\b|\bSialkot\b|\bSargodha\b|\bBahawalpur\b"),
            ("North-West Frontier & Federally Administered Tribal Areas", "GAULx",
             r"\bKhyber[-\s]+Pakhtunkhwa\b|\bKPK?\b|North[-\s]+West\s+Frontier|"
             r"\bNWFP\b|\bPeshawar\b|\bFATA\b")],
    "COL": [("Santafe De Bogota D.c.", "GAUL2", r"\bBogot[aá]\b"),
            ("Cundinamarca", "GAULx", r"\bCundinamarca\b")],
    "IND": [("Telangana", "GAULx", r"\bTelangana\b|\bHyderabad\b"),
            ("Jammu and Kashmir", "GAULx", r"\bJammu\b|\bKashmir\b")],
    # Capital areas carried outside the matching level (found by the 2026-10-20
    # gap scan of the SPID and by the in-session extraction).
    "MOZ": [("Maputo", "GAULx", r"\bMaputo\s+Province\b|\bMatola\b"),
            ("Cidade de Maputo", "GAUL2", r"\bMaputo\b(?!\s+Province)")],
    "TGO": [("Maritime", "GAULx", r"\bMaritime\b|\bLom[eé]\b")],
    "LBR": [("Greater Monrovia", "GAUL2", r"\bMonrovia\b"),
            ("Montserrado", "GAULx", r"\bMontserrado\b")],
    "SLE": [("Western Area", "GAUL1", r"\bWestern\s+Area\b|\bFreetown\b")],
    "PRY": [("Asuncion", "GAUL2", r"\bAsunci[oó]n\b"),
            ("Central", "GAULx", r"\bCentral\s+Department\b")],
    "NIC": [("Managua", "GAUL1", r"\bManagua\b")],
    "TUN": [("Tunis", "GAUL1", r"\bTunis\b")],
    "JOR": [("Amman", "GAUL1", r"\bAmman\b")],
}

# Uganda's four GAULx groupings are keyed by a constituent they uniquely contain.
UGA_MACRO = {"@N": "Gulu", "@E": "Mbale", "@W": "Mbarara", "@C": "Kampala"}

# A name followed by a citation year or "et al." is an author surname, not a site.
CITE_RE = re.compile(r"^\s*(?:et\s+al|and\s+[A-Z]|\(?\s*(?:19|20)\d\d[a-z]?\b)")
AFFIL_RE = re.compile(
    r"(?:Universit|Universidad|Universidade|Institut|Instituto|School\s+of|College|"
    r"\bIIT\b|\bIIM\b|Cent(?:er|re)\s+for|Ministry|Bank\s+of|Office|Faculty|"
    r"Polytechnic|Academy)", re.I)
# "Lahore University of ...", "Delhi School of Economics": institution follows.
AFFIL_AFTER_RE = re.compile(
    r"^\s*(?:\w+\s+){0,2}(?:Universit|Institut|School\s+of|College|Polytechnic)", re.I)
CUE_RE = re.compile(
    r"conducted\s+in|took\s+place\s+in|implemented\s+in|carried\s+out\s+in|located\s+in|"
    r"fielded\s+in|study\s+(?:area|site|region|district|setting|location)|"
    r"(?:districts?|counties|county|provinces?|states?|regions?|villages?|"
    r"municipalit\w+|sub-?counties|upazilas?|blocks?|communities|schools?)\s+(?:of|in)\b|"
    r"in\s+the\s+(?:state|province|region|district|county|department)\s+of|"
    r"sample|randomi[sz]|experiment|intervention|baseline|endline|field\s*work|"
    r"we\s+(?:selected|worked|partnered)|setting|context",
    re.I)
NATIONWIDE_RE = re.compile(
    r"nationally[-\s]+representative|nation[-\s]?wide\s+(?:sample|survey|experiment|"
    r"randomi[sz]ed|trial|program)|all\s+(?:\d+\s+)?(?:districts|provinces|states|"
    r"regions|counties)\s+(?:of|in)\s+the\s+country|across\s+all\s+(?:\d+\s+)?(?:districts|"
    r"provinces|states|regions|counties)",
    re.I)
FW_CUE_RE = re.compile(
    r"baseline|endline|midline|follow[-\s]?up\s+survey|data\s+collection|field\s*work|"
    r"survey\s+(?:was|were)\s+(?:conducted|carried\s+out|fielded|administered)|"
    r"(?:was|were)\s+(?:implemented|rolled\s+out|launched)|randomi[sz]ation\s+took",
    re.I)
YEAR_RE = re.compile(r"\b(19[89]\d|20[0-2]\d)\b")
# A fieldwork year is introduced as a date ("in 2014", "March 2015", "2014-15");
# a year after a surname or "al.," is a citation and is skipped.
DATE_LEAD_RE = re.compile(
    r"(?:\b(?:in|of|from|between|to|and|until|since|during|by|early|late|mid|"
    r"Jan\w*|Feb\w*|Mar\w*|Apr\w*|May|Jun\w*|Jul\w*|Aug\w*|Sep\w*|Oct\w*|"
    r"Nov\w*|Dec\w*|spring|summer|fall|autumn|winter)|[-–/])\s*$", re.I)
REF_HEAD_RE = re.compile(r"\n\s*(?:References|REFERENCES|Bibliography|BIBLIOGRAPHY|"
                         r"Works\s+Cited|Literature\s+Cited)\s*\n")

FIELDS = [
    "doi", "journal_short", "publication_year", "title", "matched_file",
    "iso3", "country_setting", "matching_level", "level_kind",
    "local_status", "top_unit", "top_unit_level", "n_strong_units", "strong_units", "all_units",
    "nationwide_signal", "despaced_text",
    "fieldwork_year_start", "fieldwork_year_end", "fieldwork_years_seen",
    "snippets", "extract_status", "rules_version", "snapshot_utc",
]


def log(msg):
    line = f"[{datetime.now().strftime('%H:%M:%S')}] {msg}"
    print(line, flush=True)
    with open(LOG_TXT, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def fold(s):
    s = (s or "").replace("ı", "i").replace("İ", "I")
    s = unicodedata.normalize("NFKD", s)
    return "".join(c for c in s if not unicodedata.combining(c))


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
    try:
        proc = subprocess.run([pdftotext_bin, "-enc", "UTF-8", str(pdf_path), "-"],
                              capture_output=True, timeout=PDFTOTEXT_TIMEOUT)
        if proc.returncode != 0:
            return None
        return unicodedata.normalize("NFKC", proc.stdout.decode("utf-8", "replace"))
    except Exception:  # noqa: BLE001
        return None


def level_kind(tag):
    if tag in GROUPED_TAGS:
        return "grouped"
    if tag in ("GAUL2", "GADM2", "NUTS3"):
        return "admin2"
    return "admin1"


def token_rx(tok):
    """Title-case regex for one name token; connectors are case-free. ALL-CAPS
    forms are deliberately not matched: in journal PDFs they are mostly running
    heads ("AYDIN: CONSUMPTION RESPONSE ...") rather than site mentions."""
    if tok.lower() in CONNECTORS:
        return f"(?i:{re.escape(tok)})"
    if tok.isupper() and len(tok) <= 5:          # acronyms, e.g. SNNPR
        return re.escape(tok)
    return re.escape(tok[0].upper() + tok[1:].lower())


def name_rx(name, relaxed):
    """Regex for a gazetteer name. relaxed=True for despaced text."""
    parts = [p for p in re.split(r"[\s\-]+", name) if p]
    sep = r"[\s\-]*" if relaxed else r"[\s\-]+"
    core = sep.join(token_rx(p) for p in parts)
    if relaxed:
        return f"(?<![A-Z]){core}"
    return f"(?<![A-Za-z]){core}(?![A-Za-z])"


def constituents(unit_name, iso3, mlevel):
    """Names to match for one WB unit: the unit itself, or its constituents."""
    if mlevel in GROUPED_TAGS or iso3 == "TUR":
        parts = re.split(r"\s*&\s*|\s*,\s*", unit_name)
        out = []
        for p in parts:
            if p == "BulambuliIganga":                 # WB source typo
                out += ["Bulambuli", "Iganga"]
            elif p:
                out.append(p)
        return out
    return [unit_name]


class Matcher:
    """Compiled patterns for one country: list of (regex, [unit_names], label)."""

    def __init__(self, iso3, mlevel, units):
        self.units = units
        self.mlevel = mlevel
        self.supp_level = {n: lv for n, lv, _ in SUPPLEMENTARY.get(iso3, [])}
        name_to_units = defaultdict(set)
        for u in units:
            for c in constituents(u, iso3, mlevel):
                name_to_units[fold(c).strip()].add(u)
        self.pats = {False: [], True: []}
        for relaxed in (False, True):
            pats = []
            for nm in sorted(name_to_units, key=len, reverse=True):
                core = name_rx(nm, relaxed)
                if nm in GENERIC or nm.title() in GENERIC:
                    sep = r"\s*" if relaxed else r"\s+"
                    rx = (f"(?:{core}{sep}{ADMIN_NOUN}(?![A-Za-z]))|"
                          f"(?:(?i:{ADMIN_PRE}){core})")
                else:
                    rx = core
                pats.append((re.compile(rx), sorted(name_to_units[nm]), nm))
            for arx, target in ALIASES.get(iso3, []):
                if target is None:
                    continue
                if isinstance(target, str) and target.startswith("@"):
                    key = UGA_MACRO[target]
                    tgt = sorted(u for u in units if key in u)
                elif isinstance(target, list):
                    tgt = target
                else:
                    tgt = [target]
                a = arx.replace(r"\s+", r"\s*") if relaxed else arx
                pats.append((re.compile(a, re.I), tgt, f"alias:{arx[:24]}"))
            for sname, _, srx in SUPPLEMENTARY.get(iso3, []):
                a = srx.replace(r"\s+", r"\s*") if relaxed else srx
                pats.append((re.compile(a), [sname], f"supp:{sname[:20]}"))
            self.pats[relaxed] = pats

    def find(self, text, relaxed):
        """Yield (start, end, units, label), masking spans already claimed."""
        claimed = []
        hits = []
        for rx, tgt, label in self.pats[relaxed]:
            for m in rx.finditer(text):
                a, b = m.start(), m.end()
                if any(a < cb and ca < b for ca, cb in claimed):
                    continue
                claimed.append((a, b))
                hits.append((a, b, tgt, label))
        hits.sort()
        return hits


def is_despaced(text):
    body = text[:40000]
    if not body:
        return False
    return body.count(" ") / max(1, len(body)) < 0.06


def fieldwork_years(flat, pub_year):
    seen = Counter()
    for m in FW_CUE_RE.finditer(flat):
        win = flat[max(0, m.start() - FW_WINDOW): m.end() + FW_WINDOW]
        for ym in YEAR_RE.finditer(win):
            if not DATE_LEAD_RE.search(win[max(0, ym.start() - 12): ym.start()]):
                continue
            y = int(ym.group(1))
            if pub_year and not (pub_year - 15 <= y <= pub_year):
                continue
            seen[y] += 1
    if not seen:
        return "", "", ""
    # Keep years seen at least twice when possible, to drop stray citations.
    keep = [y for y, c in seen.items() if c >= 2] or list(seen)
    return min(keep), max(keep), ";".join(f"{y}:{c}" for y, c in sorted(seen.items()))


def score_paper(matcher, title_abs, body, pub_year):
    despaced = is_despaced(body)
    flat_ta = re.sub(r"\s+", " ", fold(title_abs))
    flat = re.sub(r"\s+", " ", fold(body)) if not despaced else fold(body)

    ta = Counter()
    for a, b, tgt, _ in matcher.find(flat_ta, False):
        for u in tgt:
            ta[u] += 1

    raw, cue = Counter(), Counter()
    snippets = []
    for a, b, tgt, label in matcher.find(flat, despaced):
        left = flat[max(0, a - AFFIL_WINDOW): a]
        if (AFFIL_RE.search(left) or CITE_RE.match(flat[b: b + 20])
                or AFFIL_AFTER_RE.match(flat[b: b + 40])):
            continue
        win = flat[max(0, a - CUE_WINDOW): b + CUE_WINDOW]
        has_cue = bool(CUE_RE.search(win))
        for u in tgt:
            raw[u] += 1
            if has_cue:
                cue[u] += 1
        if has_cue and len(snippets) < MAX_SNIPPETS:
            s = flat[max(0, a - SNIPPET_PAD): b + SNIPPET_PAD]
            snippets.append(f"[{label}] ...{re.sub(r'\s+', ' ', s)}...")

    units = set(raw) | set(ta)
    strong = sorted((u for u in units if ta[u] or cue[u]),
                    key=lambda u: (-(3 * ta[u] + 2 * cue[u] + raw[u]), u))
    nationwide = bool(NATIONWIDE_RE.search(flat_ta) or NATIONWIDE_RE.search(flat))

    if not units:
        status, top = "no_hit", ""
    elif not strong:
        status, top = "weak_only", max(units, key=lambda u: raw[u])
    elif len(strong) == 1:
        top = strong[0]
        others = max([raw[u] for u in units if u != top] or [0])
        dominant = (raw[top] >= 2 or ta[top] > 0) and others <= max(1, raw[top] // 4)
        status = "resolved_single" if dominant else "single_weak"
    else:
        top = strong[0]
        sc = {u: 3 * ta[u] + 2 * cue[u] + raw[u] for u in strong}
        share = sc[top] / sum(sc.values())
        status = ("dominant_single" if share >= 0.75 and raw[top] >= 3
                  else "multi_candidate")

    def fmt(us):
        return ";".join(f"{u}[ta{ta[u]}/cue{cue[u]}/raw{raw[u]}]" for u in us)

    fy0, fy1, fyseen = fieldwork_years(flat, pub_year)
    return {
        "local_status": status, "top_unit": top,
        "top_unit_level": matcher.supp_level.get(top, matcher.mlevel) if top else "",
        "n_strong_units": len(strong),
        "strong_units": fmt(strong),
        "all_units": fmt(sorted(units, key=lambda u: -raw[u])),
        "nationwide_signal": int(nationwide), "despaced_text": int(despaced),
        "fieldwork_year_start": fy0, "fieldwork_year_end": fy1,
        "fieldwork_years_seen": fyseen, "snippets": " || ".join(snippets),
    }


def strip_references(text):
    """Cut the text at the first References heading past 40% of the document."""
    for m in REF_HEAD_RE.finditer(text):
        if m.start() > len(text) * 0.4:
            return text[:m.start()]
    return text


def main():
    open(LOG_TXT, "w").close()
    log(f"Stage 21b (local site match) start. Rules={RULES_VERSION} SMOKE_N={SMOKE_N}")
    pdftotext_bin = find_pdftotext()

    meta, abstracts = {}, {}
    with open(CC_CSV, encoding="utf-8") as f:
        for r in csv.DictReader(f):
            if r["rct_classification"] != "yes":
                continue
            isos = [x.strip() for x in (r["country_iso3_list"] or "").split(";") if x.strip()]
            meta[r["doi"]] = {"journal_short": r["journal_short"],
                              "publication_year": r["publication_year"],
                              "title": r["title"], "setting": r["country_study_setting"],
                              "iso_list": isos}
            abstracts[r["doi"]] = r.get("abstract", "")
    matches = {}
    with open(MATCH_CSV, encoding="utf-8") as f:
        for r in csv.DictReader(f):
            mf = (r.get("matched_file") or "").strip()
            if mf and (FULLTEXT / mf).exists():
                matches[r["doi"]] = mf
    with open(PROFILE_CSV, encoding="utf-8") as f:
        prof = {r["iso3"]: r for r in csv.DictReader(f)}
    gaz = defaultdict(list)
    with open(GAZ_CSV, encoding="utf-8") as f:
        for r in csv.DictReader(f):
            gaz[r["iso3"]].append(r["unit_name"])

    # Sanity-check that every alias target is a real gazetteer unit.
    for iso, lst in ALIASES.items():
        for _, tgt in lst:
            if tgt is None or (isinstance(tgt, str) and tgt.startswith("@")):
                continue
            for t in ([tgt] if isinstance(tgt, str) else tgt):
                if t not in gaz.get(iso, []):
                    sys.exit(f"Alias target {iso}:{t!r} is not in the gazetteer.")

    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    rows, worklist = [], []
    for doi, m in meta.items():
        iso = m["iso_list"][0] if m["iso_list"] else ""
        b = {k: "" for k in FIELDS}
        b.update(doi=doi, journal_short=m["journal_short"],
                 publication_year=m["publication_year"], title=m["title"],
                 matched_file=matches.get(doi, ""), iso3=iso,
                 country_setting=m["setting"], rules_version=RULES_VERSION,
                 snapshot_utc=stamp)
        p = prof.get(iso)
        if m["setting"] != "single_country" or len(m["iso_list"]) != 1:
            b["extract_status"] = "not_single_country"
        elif not p or p["matching_level"] in ("", "NONE"):
            b["extract_status"] = "country_not_covered"
        elif doi not in matches:
            b["extract_status"] = "no_pdf"
            b["matching_level"] = p["matching_level"]
        else:
            b["matching_level"] = p["matching_level"]
            b["level_kind"] = level_kind(p["matching_level"])
            worklist.append(b)
            continue
        rows.append(b)
    if SMOKE_N:
        worklist = worklist[:SMOKE_N]
    log(f"Pass-through rows: {len(rows)}  work-list: {len(worklist)}")

    matchers = {}
    for k, b in enumerate(worklist, 1):
        iso = b["iso3"]
        if iso not in matchers:
            matchers[iso] = Matcher(iso, b["matching_level"], gaz[iso])
        text = pdf_to_text(pdftotext_bin, FULLTEXT / b["matched_file"])
        if text is None:
            b["extract_status"] = "pdf_error"
            rows.append(b)
            continue
        body = strip_references(text)
        try:
            py = int(b["publication_year"])
        except ValueError:
            py = None
        res = score_paper(matchers[iso], f"{b['title']}\n{abstracts.get(b['doi'], '')}",
                          body, py)
        b.update(res)
        b["extract_status"] = "ok"
        rows.append(b)
        if k % 25 == 0:
            log(f"  [{k}/{len(worklist)}] processed")

    with open(OUT_CSV, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        w.writerows(rows)

    ok = [r for r in rows if r["extract_status"] == "ok"]
    log(f"Extract status: {dict(Counter(r['extract_status'] for r in rows))}")
    log(f"Local status (ok rows, n={len(ok)}): "
        f"{dict(Counter(r['local_status'] for r in ok).most_common())}")
    for kind in ("admin1", "admin2", "grouped"):
        sub = [r for r in ok if r["level_kind"] == kind]
        log(f"  {kind:8s} n={len(sub):3d} "
            f"{dict(Counter(r['local_status'] for r in sub).most_common())}")
    log(f"Nationwide signal among ok rows: {sum(int(r['nationwide_signal']) for r in ok)}; "
        f"despaced texts: {sum(int(r['despaced_text']) for r in ok)}; "
        f"fieldwork year found: {sum(1 for r in ok if r['fieldwork_year_start'] != '')}")
    log(f"Wrote {OUT_CSV} ({len(rows)} rows)")


if __name__ == "__main__":
    main()
