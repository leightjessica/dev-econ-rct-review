# LLM prompts used in Stages 3a, 3b, and 3c

This document records the exact prompt text and API parameters used by the project's three LLM classification stages, for transparency and replicability. The prompts are also embedded in the source files (`scripts/03a_dev_borderline_classify.py`, `scripts/03b_rct_classify.py`, and `scripts/03c_topic_classify.py`); this document is the canonical human-readable reference and is what should be cited or reproduced in supplementary appendices.

Each row classified by any stage records the model identifier and a project-internal prompt-version string (`dev_llm_prompt_version`, `rct_llm_prompt_version`, `topic_prompt_version`) so that downstream users can verify which prompt produced any given classification.

Stages 3a and 3b call the Anthropic Python SDK and require an `ANTHROPIC_API_KEY`. Stage 3c does NOT use the API: it shells out to headless `claude -p`, which authenticates via the local Claude Code subscription, so no API key is required on the running machine.

## Common API parameters

Both stages call `client.messages.create(...)` with the parameters below.

| Parameter | Value | Rationale |
|---|---|---|
| `model` | `claude-sonnet-4-5` (default; overridable via `ANTHROPIC_MODEL` env var) | Mid-tier Claude, sufficient for binary classification with structured JSON output |
| `temperature` | `0` | Determinism — same input yields the same classification |
| `max_tokens` | 256 (Stage 3a), 384 (Stage 3b) | Tight cap on output; the JSON response is short |
| `system` | Single text block with `cache_control: {"type": "ephemeral"}` | Caching declared, though the system prompts in both stages are below Sonnet 4.5's 1,024-token caching threshold and the cache flag is therefore silently ignored |
| `messages` | One user message containing `Title: <title>\n\nAbstract: <abstract>` | The article-specific input |

The user message follows a fixed template:

```
Title: <article title>

Abstract: <article abstract>
```

## Stage 3a — Development-paper classification

**Purpose.** For each row in `dev_filtered.csv` where `is_development == 'BORDERLINE'`, `type == 'article'`, and `abstract` is non-empty, decide whether the paper is a development-economics paper.

**Prompt version:** `dev-classify-v1`

**System prompt (verbatim):**

```
You are an expert economist helping classify academic papers as development economics or not.

A paper qualifies as DEVELOPMENT ECONOMICS if it primarily:
- Studies economic outcomes, institutions, or policies in low-income or middle-income countries (the World Bank's LIC, LMIC, or UMIC categories), OR
- Addresses theoretical or empirical questions explicitly about economic development, growth, poverty, structural transformation, or development policy, OR
- Examines microeconomic phenomena (households, firms, markets) primarily in developing-country settings.

Borderline guidance:
- Papers using developing-country data but addressing general questions (e.g., a labor economics paper that happens to use Indian administrative data): mark "yes" if the paper engages substantively with development questions; "no" if the country is incidental.
- Macro or finance papers with international scope: "no" unless development is the central focus.
- Migration, foreign aid, or trade papers: "yes" if developing-country effects are central.
- Pure theory papers: "yes" only if the model is explicitly about development.
- A paper conducted in a high-income country (US, UK, Western Europe, Japan, etc.) is almost never development.
- If genuinely unclear from the abstract, mark "uncertain".

Respond with ONLY a single JSON object in this exact format and nothing else:
{"is_development": "yes" | "no" | "uncertain", "justification": "<one short sentence>"}
```

**Output schema.** A single JSON object with two fields:

| Field | Type | Allowed values |
|---|---|---|
| `is_development` | string | `"yes"`, `"no"`, `"uncertain"` |
| `justification` | string | Free text, expected to be one short sentence |

**Mapping to dataset columns.**

| LLM output | Stored in | Effect on `is_development` |
|---|---|---|
| `is_development = "yes"` | `dev_llm_classification` | `is_development` updated to `TRUE`; `dev_filter_source` set to `llm_yes` |
| `is_development = "no"` | `dev_llm_classification` | `is_development` updated to `FALSE`; `dev_filter_source` set to `llm_no` |
| `is_development = "uncertain"` | `dev_llm_classification` | `is_development` remains `BORDERLINE`; `dev_filter_source` set to `llm_uncertain` |
| `justification` | `dev_llm_justification` | (no further effect) |

## Stage 3b — RCT classification

**Purpose.** For each row in `dev_classified.csv` where `is_development == 'TRUE'`, `type == 'article'`, and `abstract` is non-empty, decide whether the paper reports the results of a randomized controlled trial in the project's defined sense, and if so, classify the RCT subtype.

**Prompt version:** `rct-classify-v1`

**System prompt (verbatim):**

```
You are an expert in empirical research methods classifying development economics papers as RCTs (randomized controlled trials) or not.

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
}
```

**Output schema.** A single JSON object with four fields:

| Field | Type | Allowed values |
|---|---|---|
| `is_rct` | string | `"yes"`, `"no"`, `"uncertain"` |
| `rct_subtype` | string | `"individual"`, `"cluster"`, `"encouragement"`, `"sub_component"`, `"follow_up"`, `"field_experiment"`, `"n/a"` |
| `confidence` | string | `"high"`, `"medium"`, `"low"` |
| `justification` | string | Free text, expected to be one short sentence |

**Mapping to dataset columns.**

| LLM output | Stored in |
|---|---|
| `is_rct` | `rct_classification` |
| `rct_subtype` | `rct_subtype` |
| `confidence` | `rct_confidence` |
| `justification` | `rct_justification` |

A paper is treated as an RCT in summary tables and figures only if `rct_classification == "yes"`. The `"uncertain"` and missing categories are reported separately and are flagged for manual review.

## Stage 3c — Substantive-topic classification

**Purpose.** For each row in `final_dataset.csv` (i.e., every published article that passed the development filter), assign one primary substantive-topic code (required) and one secondary code (optional) from a fixed 16-code taxonomy. The taxonomy was chosen to cover the substantive concentrations observed in development-economics RCT publishing while keeping the total number of buckets small enough to support per-code tabulation in a sample of ~1,600 papers.

**Prompt version:** `topic-classify-v2`

**Topic codes (16):** `agriculture`, `health`, `education`, `labor`, `firms`, `finance`, `social_protection`, `gender`, `political_economy`, `conflict_crime`, `environment`, `trade_macro`, `migration`, `infrastructure`, `behavioral_info`, `other`.

**Execution method.** Unlike Stages 3a and 3b, Stage 3c does not call the Anthropic Python SDK. Instead it invokes the local `claude -p` CLI (headless Claude Code) as a subprocess for each paper, with `--model claude-haiku-4-5-20251001`, `--output-format json`, `--system-prompt <prompt>`, and `--tools ""`. Authentication is via the user's Claude Code subscription OAuth token in the system keychain; no `ANTHROPIC_API_KEY` is required. This decision reflects the project sponsor's preference to keep all Stage-3c LLM cost inside the existing subscription rather than incur incremental API billing on a per-call basis.

**System prompt (verbatim):**

```
You are an automated topic classifier for development-economics papers. Follow the user's instructions exactly. Respond with only a single-line JSON object matching the requested schema. No prose, no markdown, no code fences.
```

**User-message template (verbatim):** The script sends the title and abstract followed by the schema-enforcement and code-guide instructions in the user turn. Empirical testing showed that headless `claude -p` ignores or under-weights instructions in `--system-prompt` (Claude Code's default helper system prompt cannot be fully replaced in OAuth mode), but follows instructions placed at the end of the user message reliably. The user-message body following the title and abstract is:

```
REQUIRED OUTPUT FORMAT
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

REMINDER: Output ONLY the JSON object. No code fences. No prose. No "subtopics" or "methods" or any other keys.
```

**Output schema.** A single JSON object with four fields:

| Field | Type | Allowed values |
|---|---|---|
| `primary_topic` | string | One of the 16 topic codes |
| `secondary_topic` | string | One of the 16 topic codes, or `""` (empty) |
| `confidence` | string | `"high"`, `"medium"`, `"low"` |
| `justification` | string | Free text, expected to be one short sentence |

**Mapping to dataset columns.** Each LLM output field is recorded into a column of the same name on `data/topic_classified.csv`, plus `topic_llm_model` and `topic_prompt_version` for replicability.

**Validation.** A stratified random sample of 50 LLM-classified rows (drawn proportional-to-frequency across primary codes, with a minimum of 2-3 per code) is blind-coded by the project lead and compared against the LLM output. The resulting agreement rate and confusion matrix are reported in `data/topic_validation_report.md`.

## Robustness and known limitations

**Determinism.** Both stages set `temperature = 0`. Anthropic guarantees output stability within named model versions, so re-running on the same model version returns the same JSON. Anthropic occasionally versions models silently; if the underlying `claude-sonnet-4-5` is updated by Anthropic in the future, classification differences for borderline cases may occur. We mitigate this by recording the model identifier on every classified row.

**Prompt-cache behavior.** Both system prompts are below the 1,024-token minimum for ephemeral caching on Sonnet 4.5. Anthropic silently disables caching when this minimum is not met. The total cost penalty is small (a few dollars at our query volume) and we have not padded the prompts to force caching. A future revision could add ~700 tokens of additional examples to enable caching and reduce per-call cost; this would also constitute a new prompt version (`-v2`) and require a re-run for consistency.

**Pass-2 introduction fetching.** The original design contemplates a Pass-2 step where rows classified `"uncertain"` from the abstract alone are re-classified after fetching the article's introduction. This is scaffolded in `03b_rct_classify.py` but is not yet implemented in v1. Uncertain rows are flagged for manual review.

**Prompt sensitivity.** No formal prompt-engineering ablation has been performed. The prompts above were drafted to encode the project's RCT definition (Section 3 of `PROJECT_MEMO.md`) and tested informally on spot-check samples after the first 50, 100, 300, and 550 classifications. All sampled `yes` and `no` decisions matched the author's expert judgment. A formal validation against a hand-coded benchmark sample would be a useful addition for a v2 release.

## Citing the prompts

For papers that use this dataset, the recommended supplementary-material practice is to either include the full text of these system prompts verbatim in an online appendix, or to cite this document by its version-controlled commit hash and the prompt version strings (`dev-classify-v1`, `rct-classify-v1`).
