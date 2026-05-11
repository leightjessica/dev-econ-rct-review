"""
Stage 6a: LLM extraction of study-country attribution for development papers.

Reads:  data/final_dataset.csv  (Stage 4 output)
        data/lmic_countries.csv  (Stage 0b output; constrains ISO3 vocabulary)
Writes: data/country_classified.csv

Workflow
--------
For each row where is_development == 'TRUE' AND abstract is non-empty, send
title + abstract to the Anthropic API and classify the country attribution
of the study.

Output schema (new columns appended to the source row):
  country_study_setting       'single_country' | 'multi_country' | 'cross_country' | 'no_country'
  country_iso3_list           Semicolon-separated ISO3 codes from the LMIC lookup
                              (empty for 'cross_country' and 'no_country')
  country_n_named             Integer count of distinct countries the LLM identified,
                              before LMIC filtering (so a 7-country trial is recorded
                              as 7 even though we drop it)
  country_is_cross_country    'TRUE' if study_setting == 'cross_country' (5+ countries
                              OR explicit cross-country / global framing)
  country_non_lmic_only       'TRUE' if the study's named countries are entirely
                              high-income (paper still counted as development by JDE
                              auto-include or JEL O-code but not relevant to the
                              LMIC poverty analysis)
  country_justification       One-sentence LLM rationale
  country_llm_model           Anthropic model identifier
  country_llm_prompt_version  Project-internal prompt version

Cross-country threshold: 5+ named countries OR an explicit cross-country,
global, or region-wide design (e.g., "across 7 Sub-Saharan African countries")
maps to study_setting = 'cross_country'.

Resumable: rerunning skips rows already classified.
"""

import csv
import json
import os
import sys
import time
from datetime import datetime, timezone

try:
    from anthropic import Anthropic
except ImportError:
    sys.exit("Missing anthropic package. From the project root:\n  pip install -r requirements.txt")

# ---- Configuration ----------------------------------------------------------

MODEL = os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-4-5")
PROMPT_VERSION = "country-extract-v1"
MAX_TOKENS = 512
SAVE_EVERY = 50
CROSS_COUNTRY_THRESHOLD = 5

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_DIR = os.path.abspath(os.path.join(SCRIPT_DIR, ".."))
IN_CSV = os.path.join(PROJECT_DIR, "data", "final_dataset.csv")
LMIC_CSV = os.path.join(PROJECT_DIR, "data", "lmic_countries.csv")
OUT_CSV = os.path.join(PROJECT_DIR, "data", "country_classified.csv")
LOG_TXT = os.path.join(PROJECT_DIR, "data", "06a_country_extract.log")

NEW_FIELDS = [
    "country_study_setting",
    "country_iso3_list",
    "country_n_named",
    "country_is_cross_country",
    "country_non_lmic_only",
    "country_justification",
    "country_llm_model",
    "country_llm_prompt_version",
]


def log(msg):
    line = f"[{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%SZ')}] {msg}"
    print(line, flush=True)
    with open(LOG_TXT, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def load_lmic_reference():
    """Returns (iso3_set, reference_table_string).

    The reference table is the block we paste into the system prompt so the
    model knows which ISO3 codes are allowed. We pass canonical names; the
    model is asked to map any alternates back to these ISO3 codes.
    """
    iso3_set = set()
    rows = []
    with open(LMIC_CSV, encoding="utf-8") as f:
        for r in csv.DictReader(f):
            if r["iso3"] == "REGION":
                continue
            iso3_set.add(r["iso3"])
            rows.append((r["iso3"], r["name_canonical"]))
    rows.sort(key=lambda x: x[0])
    table = "\n".join(f"  {iso3}  {name}" for iso3, name in rows)
    return iso3_set, table


def build_system_prompt(reference_table):
    return f"""You classify the study-country attribution of a development economics paper based on its title and abstract.

Decide which of four study settings the paper falls into:

- "single_country": the paper's empirical analysis is conducted in exactly one country.
- "multi_country": the paper's empirical analysis is conducted in 2 to 4 named countries.
- "cross_country": the paper studies {CROSS_COUNTRY_THRESHOLD} or more countries, OR is framed as a cross-country, global, region-wide, or panel-of-countries analysis (e.g., "across all Sub-Saharan African countries", "panel of 142 developing countries", "global"). Treat unspecified regional studies the same way.
- "no_country": the paper is theoretical, methodological, a systematic review, or otherwise has no country-specific empirical setting.

For "single_country" or "multi_country" papers, list the ISO3 codes of the named countries. Use ONLY codes from the LMIC reference list below. If a named country is a high-income country (USA, UK, Germany, Japan, Korea, etc., not on the list), do NOT invent an ISO3 for it — instead set country_non_lmic_only = true ONLY if every named country in the paper is high-income (no LMIC named); set it to false if at least one LMIC is named alongside HIC countries (and list the LMIC ISO3 codes).

For "cross_country" and "no_country", leave countries_iso3 empty.

Count countries_named as the total number of distinct country names mentioned as study settings in the title and abstract (including any HIC countries that you did not list in countries_iso3). If the abstract says "across 12 African countries" without naming them, set countries_named = 12 and study_setting = "cross_country".

Important nuances:
- A paper that uses a sample of migrants in the US from a developing country has study setting = the US (HIC); set non_lmic_only = true.
- A paper that uses cross-country data on, say, conflict events does count as cross_country.
- A paper that fits a structural model to data from Indonesia does count as single_country (Indonesia).
- A paper that runs a randomized experiment in two countries (e.g., Kenya and Sierra Leone) is multi_country.
- "Sub-Saharan Africa", "developing countries", "low-income countries" without specific country names = cross_country with empty countries_iso3.

LMIC reference list (ISO3 code, canonical name):
{reference_table}

Respond with ONLY a single JSON object in this exact format and nothing else:
{{
  "study_setting": "single_country" | "multi_country" | "cross_country" | "no_country",
  "countries_iso3": ["XXX", ...],
  "countries_named": <integer>,
  "non_lmic_only": true | false,
  "justification": "<one short sentence>"
}}"""


def parse_json_response(text):
    t = text.strip()
    if t.startswith("```"):
        t = t.split("\n", 1)[1] if "\n" in t else t[3:]
        if t.endswith("```"):
            t = t[:-3]
        t = t.strip()
        if t.startswith("json"):
            t = t[4:].strip()
    return json.loads(t)


def classify_one(client, system_prompt, title, abstract):
    msg = client.messages.create(
        model=MODEL,
        max_tokens=MAX_TOKENS,
        temperature=0,
        system=[{"type": "text", "text": system_prompt,
                 "cache_control": {"type": "ephemeral"}}],
        messages=[{"role": "user", "content": f"Title: {title}\n\nAbstract: {abstract}"}],
    )
    text = msg.content[0].text
    try:
        return parse_json_response(text), msg.usage, text
    except json.JSONDecodeError:
        return None, msg.usage, text


def main():
    open(LOG_TXT, "w").close()
    log(f"Stage 6a start. Model={MODEL}  Prompt={PROMPT_VERSION}")

    iso3_set, reference_table = load_lmic_reference()
    log(f"Loaded {len(iso3_set)} LMIC ISO3 codes for vocabulary constraint")
    system_prompt = build_system_prompt(reference_table)

    # Resume from existing output if present; otherwise start from final_dataset
    if os.path.exists(OUT_CSV):
        src = OUT_CSV
        log(f"Resuming from existing {OUT_CSV}")
    else:
        src = IN_CSV
        log(f"Starting fresh from {IN_CSV}")

    with open(src, encoding="utf-8") as f:
        reader = csv.DictReader(f)
        rows = list(reader)
        fields = list(reader.fieldnames)

    for nf in NEW_FIELDS:
        if nf not in fields:
            fields.append(nf)
    for r in rows:
        for nf in NEW_FIELDS:
            r.setdefault(nf, "")

    todo_idx = []
    for i, r in enumerate(rows):
        if r.get("is_development") != "TRUE":
            continue
        if not (r.get("abstract") or "").strip():
            continue
        if r.get("country_study_setting"):
            continue
        todo_idx.append(i)
    log(f"Development articles to classify: {len(todo_idx)}")

    if not todo_idx:
        log("Nothing to do; writing pass-through and exiting.")
        with open(OUT_CSV, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fields)
            writer.writeheader()
            writer.writerows(rows)
        return

    client = Anthropic()
    n_done = n_single = n_multi = n_cross = n_no = n_err = 0
    n_non_lmic = 0
    tot_in = tot_out = tot_cache_read = tot_cache_create = 0
    t0 = time.time()

    def save_checkpoint():
        with open(OUT_CSV, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fields)
            writer.writeheader()
            writer.writerows(rows)

    for k, i in enumerate(todo_idx, start=1):
        r = rows[i]
        title = (r.get("title") or "").strip()
        abstract = (r.get("abstract") or "").strip()
        try:
            result, usage, raw = classify_one(client, system_prompt, title, abstract)
        except Exception as e:
            log(f"  [{k}] API error on idx {i}: {type(e).__name__}: {e}; sleeping 5s")
            time.sleep(5)
            n_err += 1
            continue
        if result is None:
            log(f"  [{k}] could not parse JSON for idx {i}: {raw[:200]!r}")
            n_err += 1
            continue

        setting = (result.get("study_setting") or "").strip().lower()
        iso_list = result.get("countries_iso3") or []
        if isinstance(iso_list, str):
            iso_list = [s.strip() for s in iso_list.split(";") if s.strip()]
        # Restrict to known LMIC ISO3 codes; silently drop anything else.
        iso_list_clean = [c for c in iso_list if c in iso3_set]
        n_named = int(result.get("countries_named") or 0)
        non_lmic = bool(result.get("non_lmic_only"))
        just = (result.get("justification") or "").strip()

        # Enforce the cross-country threshold post-hoc as well: if the LLM
        # called it multi_country but reported >= threshold names, upgrade
        # to cross_country.
        if setting == "multi_country" and n_named >= CROSS_COUNTRY_THRESHOLD:
            setting = "cross_country"
            iso_list_clean = []

        is_cross = (setting == "cross_country")

        r["country_study_setting"] = setting
        r["country_iso3_list"] = ";".join(iso_list_clean)
        r["country_n_named"] = str(n_named) if n_named else ""
        r["country_is_cross_country"] = "TRUE" if is_cross else "FALSE"
        r["country_non_lmic_only"] = "TRUE" if non_lmic else "FALSE"
        r["country_justification"] = just
        r["country_llm_model"] = MODEL
        r["country_llm_prompt_version"] = PROMPT_VERSION

        if setting == "single_country":
            n_single += 1
        elif setting == "multi_country":
            n_multi += 1
        elif setting == "cross_country":
            n_cross += 1
        elif setting == "no_country":
            n_no += 1
        if non_lmic:
            n_non_lmic += 1

        if usage:
            tot_in += getattr(usage, "input_tokens", 0) or 0
            tot_out += getattr(usage, "output_tokens", 0) or 0
            tot_cache_read += getattr(usage, "cache_read_input_tokens", 0) or 0
            tot_cache_create += getattr(usage, "cache_creation_input_tokens", 0) or 0
        n_done += 1
        if (n_done % SAVE_EVERY) == 0:
            save_checkpoint()
            elapsed = time.time() - t0
            rate = n_done / max(elapsed, 1)
            log(f"  progress {n_done}/{len(todo_idx)}  single={n_single} multi={n_multi} "
                f"cross={n_cross} none={n_no} non_lmic={n_non_lmic} err={n_err}  "
                f"tok in={tot_in} cached_read={tot_cache_read} out={tot_out}  {rate:.2f} req/s")

    save_checkpoint()
    log(f"Stage 6a complete. Classified {n_done}/{len(todo_idx)} "
        f"(single={n_single} multi={n_multi} cross={n_cross} none={n_no} non_lmic={n_non_lmic} err={n_err})")
    log(f"Tokens: input={tot_in} cache_read={tot_cache_read} cache_create={tot_cache_create} output={tot_out}")


if __name__ == "__main__":
    main()
