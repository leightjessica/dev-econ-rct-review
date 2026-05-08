# LLM prompts used in Stages 3a and 3b

This document records the exact prompt text and API parameters used by the project's two LLM classification stages, for transparency and replicability. The prompts are also embedded in the source files (`scripts/03a_dev_borderline_classify.py` and `scripts/03b_rct_classify.py`); this document is the canonical human-readable reference and is what should be cited or reproduced in supplementary appendices.

Each row classified by either stage records the model identifier and a project-internal prompt-version string (`dev_llm_prompt_version`, `rct_llm_prompt_version`) so that downstream users can verify which prompt produced any given classification.

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

## Robustness and known limitations

**Determinism.** Both stages set `temperature = 0`. Anthropic guarantees output stability within named model versions, so re-running on the same model version returns the same JSON. Anthropic occasionally versions models silently; if the underlying `claude-sonnet-4-5` is updated by Anthropic in the future, classification differences for borderline cases may occur. We mitigate this by recording the model identifier on every classified row.

**Prompt-cache behavior.** Both system prompts are below the 1,024-token minimum for ephemeral caching on Sonnet 4.5. Anthropic silently disables caching when this minimum is not met. The total cost penalty is small (a few dollars at our query volume) and we have not padded the prompts to force caching. A future revision could add ~700 tokens of additional examples to enable caching and reduce per-call cost; this would also constitute a new prompt version (`-v2`) and require a re-run for consistency.

**Pass-2 introduction fetching.** The original design contemplates a Pass-2 step where rows classified `"uncertain"` from the abstract alone are re-classified after fetching the article's introduction. This is scaffolded in `03b_rct_classify.py` but is not yet implemented in v1. Uncertain rows are flagged for manual review.

**Prompt sensitivity.** No formal prompt-engineering ablation has been performed. The prompts above were drafted to encode the project's RCT definition (Section 3 of `PROJECT_MEMO.md`) and tested informally on spot-check samples after the first 50, 100, 300, and 550 classifications. All sampled `yes` and `no` decisions matched the author's expert judgment. A formal validation against a hand-coded benchmark sample would be a useful addition for a v2 release.

## Citing the prompts

For papers that use this dataset, the recommended supplementary-material practice is to either include the full text of these system prompts verbatim in an online appendix, or to cite this document by its version-controlled commit hash and the prompt version strings (`dev-classify-v1`, `rct-classify-v1`).
