"""
Stage 3c: LLM classification of development papers by substantive topic.

Reads:  data/final_dataset.csv  (Stage 4 output; 1,601 development articles)
Writes: data/topic_classified.csv

For each development article, sends title+abstract to a headless `claude -p`
subprocess and asks for one primary topic code (required) and one secondary
topic code (optional) from a fixed 16-code taxonomy. Codes:

    agriculture, health, education, labor, firms, finance, social_protection,
    gender, political_economy, conflict_crime, environment, trade_macro,
    migration, infrastructure, behavioral_info, other

Execution
---------
This script does NOT use the Anthropic Python SDK. Instead it shells out to
`claude -p` (headless Claude Code), which authenticates via the user's
local subscription OAuth in the system keychain. No ANTHROPIC_API_KEY is
required. The model is pinned to claude-haiku-4-5-20251001 by default;
override with the CLAUDE_MODEL env var.

Resumable: rerunning skips rows already classified in data/topic_classified.csv.
Parallel: a thread pool (default 4 workers) issues claude calls concurrently.

Smoke-test usage
----------------
Run on a random 20-paper subsample, write to a separate file:
    python scripts/03c_topic_classify.py --smoke 20

Full run:
    python scripts/03c_topic_classify.py
"""

import argparse
import csv
import json
import os
import random
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from threading import Lock

# ---- Configuration ----------------------------------------------------------

MODEL = os.environ.get("CLAUDE_MODEL", "claude-haiku-4-5-20251001")
PROMPT_VERSION = "topic-classify-v2"
SAVE_EVERY = 50
CLAUDE_TIMEOUT_SEC = 90

# On Windows, PATHEXT resolves `claude` to a stale `claude.exe` shim that imports
# a missing `claude_shim_launcher` module. Force the working `.cmd` wrapper.
CLAUDE_BIN = os.environ.get("CLAUDE_BIN") or (
    "claude.cmd" if sys.platform == "win32" else "claude"
)

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_DIR = os.path.abspath(os.path.join(SCRIPT_DIR, ".."))
IN_CSV = os.path.join(PROJECT_DIR, "data", "final_dataset.csv")
OUT_CSV_FULL = os.path.join(PROJECT_DIR, "data", "topic_classified.csv")
OUT_CSV_SMOKE = os.path.join(PROJECT_DIR, "data", "topic_classified_smoke.csv")
LOG_TXT = os.path.join(PROJECT_DIR, "data", "03c_topic_classify.log")

TOPIC_CODES = [
    "agriculture", "health", "education", "labor", "firms", "finance",
    "social_protection", "gender", "political_economy", "conflict_crime",
    "environment", "trade_macro", "migration", "infrastructure",
    "behavioral_info", "other",
]

JSON_SCHEMA = {
    "type": "object",
    "required": ["primary_topic", "secondary_topic", "confidence", "justification"],
    "additionalProperties": False,
    "properties": {
        "primary_topic": {"type": "string", "enum": TOPIC_CODES},
        "secondary_topic": {"type": "string", "enum": TOPIC_CODES + [""]},
        "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
        "justification": {"type": "string"},
    },
}

SYSTEM_PROMPT = "You are an automated topic classifier for development-economics papers. Follow the user's instructions exactly. Respond with only a single-line JSON object matching the requested schema. No prose, no markdown, no code fences."

# The user-message body that follows the title+abstract. Empirically, placing the
# schema-enforcement instruction AFTER the abstract (rather than only in the
# system prompt) is what produces consistently-formatted output from Claude Code's
# headless `claude -p`. The default Claude Code system prompt cannot be fully
# replaced in OAuth mode and otherwise causes the model to write essays.
USER_INSTRUCTION = """REQUIRED OUTPUT FORMAT
Begin your response with the character { and end with }. Emit exactly one JSON object on a single line with these four keys and no others: primary_topic, secondary_topic, confidence, justification.

primary_topic MUST be exactly one of (no other values, no capitalization changes):
agriculture, health, education, labor, firms, finance, social_protection, gender, political_economy, conflict_crime, environment, trade_macro, migration, infrastructure, behavioral_info, other.

secondary_topic MUST be one of the same 16 values, OR the empty string "" if no clear second topic.
confidence MUST be one of: high, medium, low.
justification: one short sentence naming the intervention or outcome that drove the choice.

CODE GUIDE
- agriculture: farming, livestock, agricultural extension, input subsidies, food security, crop markets
- health: clinical/preventive health, nutrition, mental health, WASH measured as a health outcome
- education: schooling, learning outcomes, teacher policies, ed-tech, formal skills training
- labor: job search, employment, vocational training (workers), labor regulation, child labor
- firms: SMEs, entrepreneurship, business training, capital grants, firm productivity, management
- finance: MICRO-level financial topics — microfinance, savings, credit, insurance, mobile money, household financial inclusion, fintech adoption at the household or microenterprise level
- social_protection: cash transfers (CCT/UCT), in-kind transfers, public works, safety nets, pensions. Use ONLY for actual welfare-program interventions — scholarships or financial aid bundled into a regular admissions process do NOT count
- gender: women's empowerment, dowry, marriage markets, intimate partner violence (IPV), female genital mutilation (FGM), women's decision-making, intra-household allocation when the gender lens is central, GBV. Use as primary whenever any of these is the substantive focus, not merely a heterogeneity cut. For IPV specifically, secondary=conflict_crime.
- political_economy: the behavior of elected or appointed officials within formal governing institutions — corruption among public officials, elections, state capacity, bureaucracy, formal governance reforms. Use as primary only when the focus is on formal political actors and institutions; informal community-monitoring or social-accountability interventions belong under their substantive outcome code, with political_economy as a secondary code at most.
- conflict_crime: armed conflict, policing, crime, violence prevention, terrorism
- environment: climate, pollution, energy use, deforestation, natural resources
- trade_macro: international trade, exchange rates, sovereign and emerging-market corporate FX debt, carry trades, capital flows, macro policy, growth, industrial policy. Use for international/macro finance topics (NOT the micro-level finance code)
- migration: internal/international migration, remittances, refugees
- infrastructure: roads, electricity, water/sanitation as infrastructure, digital connectivity
- behavioral_info: information provision, social norms change, beliefs, behavioral nudges, intra-household bargaining experiments, and other studies of economic decision-making mechanisms. Use only when this is the paper's substantive focus (not merely the delivery channel)
- other: residual when none of the 15 substantive codes fits

DISAMBIGUATION
- Cash transfers => social_protection (NOT finance). Microcredit/savings/insurance/mobile money => finance.
- CCT for school attendance: primary=education, secondary=social_protection. CCT for clinic visits: primary=health, secondary=social_protection.
- Scholarships, financial aid, tuition-fee waivers bundled into a regular admissions or matching process => education only. Do NOT add social_protection unless the aid functions as a stand-alone cash-transfer welfare program.
- Business training/capital to firms => firms. Worker training => labor.
- WASH measured as a health outcome => health. WASH measured as infrastructure access => infrastructure.
- Carry trades, FX risk, foreign-currency debt, sovereign debt, exchange rates, capital flows => trade_macro (NOT finance — finance is reserved for micro/household-level financial topics).
- Intra-household bargaining experiments and lab-style tests of household decision-making => primary=behavioral_info, secondary=gender (only if the gender lens is central).
- Lab-in-the-field experiments (artefactual field experiments, preference elicitation in field settings, behavioral games with real-world participants) => behavioral_info as either primary or secondary. If the headline outcome is itself a behavioral construct (preferences, beliefs, trust, risk attitudes), use primary=behavioral_info. If the lab-in-the-field design is a measurement tool for a substantive policy outcome (e.g., risk-aversion measurement in an agricultural-insurance RCT), use that substantive code as primary and behavioral_info as secondary.
- Papers whose headline contribution is improving the measurement of a phenomenon (rather than estimating a treatment effect or a substantive relationship) => primary=other, regardless of the substantive domain of the measured object.
- Regulatory policy analyses concerning financial markets, macroeconomic policy, or trade policy => use the substantive code (finance, trade_macro) as primary; political_economy is appropriate only when the paper's lens is explicitly on the decision-making of the political actors who set the regulation, not on the regulation's economic effects.
- Tie-breaker: when two codes both fit, pick the one closer to the paper's headline outcome variable. When unsure about a secondary, leave it empty rather than guess.

REMINDER: Output ONLY the JSON object. No code fences. No prose. No "subtopics" or "methods" or any other keys."""


_log_lock = Lock()
_save_lock = Lock()


def log(msg):
    line = f"[{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%SZ')}] {msg}"
    with _log_lock:
        print(line, flush=True)
        with open(LOG_TXT, "a", encoding="utf-8") as f:
            f.write(line + "\n")


def parse_json_response(text):
    """Strip markdown code fences and parse JSON. Mirrors 03b."""
    t = text.strip()
    if t.startswith("```"):
        t = t.split("\n", 1)[1] if "\n" in t else t[3:]
        if t.endswith("```"):
            t = t[:-3]
        t = t.strip()
        if t.startswith("json"):
            t = t[4:].strip()
    return json.loads(t)


def classify_one(title, abstract):
    """Call `claude -p` headlessly with Haiku and return parsed JSON or None.

    Uses --system-prompt (REPLACES the default helper-assistant prompt) plus
    --json-schema to force structured output. The user message begins with
    an explicit "Classify this paper." directive because without it Haiku
    tends to respond conversationally even with a directive system prompt.
    The parsed result is read from the envelope's `structured_output` field.
    """
    user_msg = f"Title: {title}\n\nAbstract: {abstract}\n\n{USER_INSTRUCTION}"
    cmd = [
        CLAUDE_BIN, "-p",
        "--model", MODEL,
        "--output-format", "json",
        "--system-prompt", SYSTEM_PROMPT,
        "--json-schema", json.dumps(JSON_SCHEMA),
        "--tools", "",
        "--no-session-persistence",
        "--disable-slash-commands",
    ]
    try:
        proc = subprocess.run(
            cmd,
            input=user_msg,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=CLAUDE_TIMEOUT_SEC,
        )
    except subprocess.TimeoutExpired:
        return None, "timeout", None
    if proc.returncode != 0:
        return None, f"claude exit {proc.returncode}: {proc.stderr.strip()[:200]}", None

    try:
        envelope = json.loads(proc.stdout)
    except json.JSONDecodeError:
        return None, f"envelope not JSON: {proc.stdout[:200]!r}", None

    usage = envelope.get("usage", {})
    # Prefer the validated structured_output if present.
    if isinstance(envelope.get("structured_output"), dict):
        return envelope["structured_output"], None, usage

    # Fall back to parsing the textual result.
    inner_text = envelope.get("result", "")
    try:
        parsed = parse_json_response(inner_text)
        return parsed, None, usage
    except json.JSONDecodeError:
        return None, f"inner not JSON: {inner_text[:200]!r}", usage


def validate_topic(code, allow_empty=False):
    if allow_empty and (code is None or code == ""):
        return ""
    if code in TOPIC_CODES:
        return code
    return None


def load_existing_classifications(out_csv):
    """Return dict[doi -> row_dict] of already-classified rows."""
    if not os.path.exists(out_csv):
        return {}
    existing = {}
    with open(out_csv, encoding="utf-8") as f:
        for r in csv.DictReader(f):
            if (r.get("primary_topic") or "").strip():
                doi = (r.get("doi") or "").strip()
                if doi:
                    existing[doi] = r
    return existing


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--smoke", type=int, default=0,
                    help="If >0, run on a random subsample of this size and write to topic_classified_smoke.csv")
    ap.add_argument("--workers", type=int, default=4,
                    help="Number of parallel claude -p calls (default 4)")
    ap.add_argument("--seed", type=int, default=42,
                    help="Random seed for smoke-test subsampling")
    args = ap.parse_args()

    out_csv = OUT_CSV_SMOKE if args.smoke else OUT_CSV_FULL

    open(LOG_TXT, "w").close()
    log(f"Stage 3c start. Model={MODEL}  Prompt={PROMPT_VERSION}  Workers={args.workers}"
        + (f"  SMOKE={args.smoke}" if args.smoke else ""))

    with open(IN_CSV, encoding="utf-8") as f:
        reader = csv.DictReader(f)
        rows = list(reader)
        in_fields = list(reader.fieldnames)

    new_fields = ["primary_topic", "secondary_topic", "topic_confidence",
                  "topic_justification", "topic_llm_model", "topic_prompt_version"]
    fields = in_fields + [c for c in new_fields if c not in in_fields]
    for r in rows:
        for nf in new_fields:
            r.setdefault(nf, "")

    # Merge in any existing classifications (resume). Skipped in smoke mode so
    # repeated smoke runs always classify the same N papers from scratch under
    # the chosen seed.
    if not args.smoke:
        existing = load_existing_classifications(out_csv)
        if existing:
            log(f"Found existing {out_csv} with {len(existing)} classified rows; will resume")
            for r in rows:
                doi = (r.get("doi") or "").strip()
                if doi in existing:
                    for nf in new_fields:
                        r[nf] = existing[doi].get(nf, "")

    # Decide which rows to process
    todo_idx = []
    for i, r in enumerate(rows):
        if not (r.get("abstract") or "").strip():
            continue
        if (r.get("primary_topic") or "").strip():
            continue
        todo_idx.append(i)

    if args.smoke:
        random.Random(args.seed).shuffle(todo_idx)
        todo_idx = todo_idx[:args.smoke]
        # For smoke mode, restrict the output rows we keep to just those we classify
        keep_idx = set(todo_idx)
        rows = [rows[i] for i in sorted(keep_idx)]
        # Rebuild todo_idx against the new row list (all of these are unclassified by construction)
        todo_idx = list(range(len(rows)))

    log(f"Papers to classify in this run: {len(todo_idx)}")

    if not todo_idx:
        log("Nothing to do; writing pass-through and exiting.")
        with open(out_csv, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fields)
            writer.writeheader()
            writer.writerows(rows)
        return

    n_done = n_err = n_invalid = 0
    by_primary = {}
    t0 = time.time()

    def save_checkpoint():
        with _save_lock:
            tmp = out_csv + ".tmp"
            with open(tmp, "w", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(f, fieldnames=fields)
                writer.writeheader()
                writer.writerows(rows)
            # Dropbox occasionally holds a sync lock on the target file; retry briefly.
            for attempt in range(6):
                try:
                    os.replace(tmp, out_csv)
                    return
                except PermissionError:
                    time.sleep(2 * (attempt + 1))
            log(f"WARNING: could not replace {out_csv} after retries; leaving {tmp} in place")

    def process(i):
        r = rows[i]
        title = (r.get("title") or "").strip()
        abstract = (r.get("abstract") or "").strip()
        result, err, _usage = classify_one(title, abstract)
        return i, result, err

    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        futures = {ex.submit(process, i): i for i in todo_idx}
        for fut in as_completed(futures):
            i, result, err = fut.result()
            if err is not None:
                log(f"  [idx {i}] {err}")
                n_err += 1
                continue
            if not isinstance(result, dict):
                n_err += 1
                continue
            prim = validate_topic((result.get("primary_topic") or "").strip().lower())
            sec = validate_topic((result.get("secondary_topic") or "").strip().lower(), allow_empty=True)
            conf = (result.get("confidence") or "").strip().lower()
            just = (result.get("justification") or "").strip()
            if prim is None or sec is None:
                log(f"  [idx {i}] invalid topic codes: {result}")
                n_invalid += 1
                # Still record the raw response so the row isn't silently dropped
                rows[i]["primary_topic"] = "INVALID"
                rows[i]["secondary_topic"] = ""
                rows[i]["topic_confidence"] = conf or "low"
                rows[i]["topic_justification"] = f"INVALID_RAW: {result}"
                rows[i]["topic_llm_model"] = MODEL
                rows[i]["topic_prompt_version"] = PROMPT_VERSION
                continue
            rows[i]["primary_topic"] = prim
            rows[i]["secondary_topic"] = sec
            rows[i]["topic_confidence"] = conf
            rows[i]["topic_justification"] = just
            rows[i]["topic_llm_model"] = MODEL
            rows[i]["topic_prompt_version"] = PROMPT_VERSION
            by_primary[prim] = by_primary.get(prim, 0) + 1
            n_done += 1
            if (n_done % SAVE_EVERY) == 0:
                save_checkpoint()
                elapsed = time.time() - t0
                rate = n_done / max(elapsed, 1)
                log(f"  progress {n_done}/{len(todo_idx)}  err={n_err} invalid={n_invalid}  "
                    f"{rate:.2f} req/s")

    save_checkpoint()
    log(f"Stage 3c complete. Classified {n_done}/{len(todo_idx)} (err={n_err} invalid={n_invalid})")
    if by_primary:
        log("Primary-topic distribution (this run):")
        for c, n in sorted(by_primary.items(), key=lambda x: -x[1]):
            log(f"  {c:20s} {n}")


if __name__ == "__main__":
    main()
