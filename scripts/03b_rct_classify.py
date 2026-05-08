"""
Stage 3b: LLM classification of development papers as RCTs (or not).

Reads:  data/dev_classified.csv  (Stage 3a output; or dev_filtered.csv if 3a was skipped)
Writes: data/rct_classified.csv

Workflow
--------
For each row where is_development == 'TRUE' AND type == 'article' AND abstract
is non-empty, send title+abstract to the Anthropic API and classify whether
the paper reports the results of a randomized controlled trial in the sense
defined for this project (real-world manipulation; cluster, encouragement,
sub-component, and long-run-follow-up RCTs all qualify; lab-in-the-field is
excluded unless real-world manipulation is involved).

Resumable: rerunning skips rows already classified.

Cost (rough)
------------
~1,500 development articles with abstracts. Sonnet 4.5 with prompt caching:
estimated $3-5 total.

Optional Pass 2 (intro fetch)
-----------------------------
For rows classified as 'uncertain' from the abstract alone, a follow-up step
can fetch the article's introduction and re-classify. That step is intentionally
deferred to a separate invocation of this script with the env flag
PASS2_INTRO=1. Pass 2 is not yet implemented in this revision; uncertain
rows are flagged in the output for manual review.
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
PROMPT_VERSION = "rct-classify-v1"
MAX_TOKENS = 384
SAVE_EVERY = 50

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_DIR = os.path.abspath(os.path.join(SCRIPT_DIR, ".."))
IN_CSV_PRIMARY = os.path.join(PROJECT_DIR, "data", "dev_classified.csv")
IN_CSV_FALLBACK = os.path.join(PROJECT_DIR, "data", "dev_filtered.csv")
OUT_CSV = os.path.join(PROJECT_DIR, "data", "rct_classified.csv")
LOG_TXT = os.path.join(PROJECT_DIR, "data", "03b_rct_classify.log")

SYSTEM_PROMPT = """You are an expert in empirical research methods classifying development economics papers as RCTs (randomized controlled trials) or not.

A paper IS an RCT for our purposes if it reports the results of:
- An individual-randomized controlled trial of a real-world intervention
- A cluster-randomized controlled trial (randomized at school, village, community, firm, market, or other group level)
- An encouragement design (random assignment of encouragement to take up a treatment)
- A randomized evaluation of one component within a larger program (sub-component RCT)
- A long-run follow-up of a previously-conducted RCT (use rct_subtype = "follow_up")
- A field experiment with random assignment to a treatment that participants experience in a real-world setting

A paper is NOT an RCT if:
- It uses observational data with quasi-experimental methods (DID, RD, IV, synthetic control, propensity matching) WITHOUT actual randomization by researchers or implementers
- It is a structural model, pure theory paper, simulation, or systematic review / meta-analysis
- It is a lab-in-the-field experiment WITHOUT real-world manipulation (e.g., pure preference elicitation in a lab session, with no real intervention)
- It is a long-run study of an intervention using only observational data (no original randomization)
- The randomization is purely incidental to the question (e.g., randomized question order in a survey instrument)

Important nuances:
- Lab-in-the-field experiments WITH real-world manipulation (where participants actually receive the treatment outside the lab session) DO qualify; mark rct_subtype = "field_experiment".
- "Natural experiments" or "as-if random" assignments are NOT RCTs.
- If the design is described in the abstract as randomized but it is unclear whether the randomization is at the level of a real-world intervention, mark "uncertain".
- Confidence: "high" when the abstract explicitly describes randomization to a real-world treatment; "medium" when randomization is mentioned but design details are sparse; "low" if you are largely guessing.

Respond with ONLY a single JSON object in this exact format and nothing else:
{
  "is_rct": "yes" | "no" | "uncertain",
  "rct_subtype": "individual" | "cluster" | "encouragement" | "sub_component" | "follow_up" | "field_experiment" | "n/a",
  "confidence": "high" | "medium" | "low",
  "justification": "<one short sentence>"
}"""


def log(msg):
    line = f"[{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%SZ')}] {msg}"
    print(line, flush=True)
    with open(LOG_TXT, "a", encoding="utf-8") as f:
        f.write(line + "\n")


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


def classify_one(client, title, abstract):
    msg = client.messages.create(
        model=MODEL,
        max_tokens=MAX_TOKENS,
        temperature=0,
        system=[{"type": "text", "text": SYSTEM_PROMPT,
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
    log(f"Stage 3b start. Model={MODEL}  Prompt={PROMPT_VERSION}")

    # Choose source: prefer Stage 3a output if present
    if os.path.exists(OUT_CSV):
        src = OUT_CSV
        log(f"Resuming from existing {OUT_CSV}")
    elif os.path.exists(IN_CSV_PRIMARY):
        src = IN_CSV_PRIMARY
        log(f"Reading Stage 3a output: {src}")
    else:
        src = IN_CSV_FALLBACK
        log(f"Stage 3a output not found; reading Stage 2 output: {src}")

    with open(src, encoding="utf-8") as f:
        reader = csv.DictReader(f)
        rows = list(reader)
        fields = list(reader.fieldnames)

    for nf in ("rct_classification", "rct_subtype", "rct_confidence",
               "rct_justification", "rct_llm_model", "rct_llm_prompt_version"):
        if nf not in fields:
            fields.append(nf)
    for r in rows:
        for nf in ("rct_classification", "rct_subtype", "rct_confidence",
                   "rct_justification", "rct_llm_model", "rct_llm_prompt_version"):
            r.setdefault(nf, "")

    todo_idx = []
    for i, r in enumerate(rows):
        if r.get("is_development") != "TRUE":
            continue
        if r.get("type") != "article":
            continue
        if not (r.get("abstract") or "").strip():
            continue
        if r.get("rct_classification"):
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
    n_done = n_yes = n_no = n_unc = n_err = 0
    by_subtype = {}
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
            result, usage, raw = classify_one(client, title, abstract)
        except Exception as e:
            log(f"  [{k}] API error on idx {i}: {type(e).__name__}: {e}; sleeping 5s")
            time.sleep(5)
            n_err += 1
            continue
        if result is None:
            log(f"  [{k}] could not parse JSON for idx {i}: {raw[:200]!r}")
            n_err += 1
            continue
        cls = (result.get("is_rct") or "").strip().lower()
        sub = (result.get("rct_subtype") or "").strip().lower()
        conf = (result.get("confidence") or "").strip().lower()
        just = (result.get("justification") or "").strip()
        r["rct_classification"] = cls
        r["rct_subtype"] = sub
        r["rct_confidence"] = conf
        r["rct_justification"] = just
        r["rct_llm_model"] = MODEL
        r["rct_llm_prompt_version"] = PROMPT_VERSION
        if cls == "yes":
            n_yes += 1
            by_subtype[sub] = by_subtype.get(sub, 0) + 1
        elif cls == "no":
            n_no += 1
        else:
            n_unc += 1
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
            log(f"  progress {n_done}/{len(todo_idx)}  yes={n_yes} no={n_no} unc={n_unc} err={n_err}  "
                f"tok in={tot_in} cached_read={tot_cache_read} out={tot_out}  {rate:.2f} req/s")

    save_checkpoint()
    log(f"Stage 3b complete. Classified {n_done}/{len(todo_idx)} "
        f"(yes={n_yes} no={n_no} unc={n_unc} err={n_err})")
    if by_subtype:
        log("RCT subtype distribution:")
        for s, n in sorted(by_subtype.items(), key=lambda x: -x[1]):
            log(f"  {s:25s} {n}")
    log(f"Tokens: input={tot_in} cache_read={tot_cache_read} cache_create={tot_cache_create} output={tot_out}")


if __name__ == "__main__":
    main()
