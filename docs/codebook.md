# Codebook: `final_dataset.csv`

This document defines every column in `data/final_dataset.csv`, the project's primary output. One row corresponds to one published article that satisfies the development-economics inclusion rule (any JEL O-code, or all of JDE).

## Bibliographic identifiers

| Column | Description |
|---|---|
| `journal_short` | Project-internal short code: AER, AERI, AEJ_Applied, AEJ_EP, ECMA, QJE, JPE, RES, RESTAT, EJ, JEEA, JDE. |
| `journal_full` | Full canonical journal title. |
| `publication_year` | Year of journal-issue publication, from OpenAlex. |
| `publication_date` | YYYY-MM-DD where available, else first day of issue month. |
| `volume` | Volume number. |
| `issue` | Issue number (empty for JDE, which uses continuous numbering). |
| `first_page` | First page of the article. |
| `last_page` | Last page of the article. |
| `doi` | Normalized DOI (lowercase, no `https://doi.org/` prefix). |
| `openalex_id` | OpenAlex Work ID (URL form). |
| `snapshot_utc` | UTC timestamp of when OpenAlex was queried (Stage 1a). |

## Article content

| Column | Description |
|---|---|
| `title` | Article title from OpenAlex. |
| `authors` | Semicolon-separated author display names. |
| `abstract` | Final abstract text (after Stage 1a-1d backfill chain). |
| `abstract_source` | Provenance of the abstract: `openalex`, `crossref`, `semantic_scholar`, `econlit`, or one of `none_*` indicating why no abstract was obtained. |

## JEL classification (from EconLit / EBSCOhost)

| Column | Description |
|---|---|
| `jel_codes` | Semicolon-separated list of all JEL codes assigned to the article in EconLit, mapped from descriptor strings via `data/jel_lookup.csv`. May be empty if no EconLit match was found. |
| `jel_codes_o_only` | Subset of `jel_codes` restricted to codes beginning with `O`. Empty if none. |

## Development-paper classification

| Column | Description |
|---|---|
| `is_development` | `TRUE` if the paper qualifies as development economics under the project's rule, `FALSE` otherwise, `BORDERLINE` only if the LLM classifier returned "uncertain". |
| `dev_filter_source` | Reason for the `is_development` value: `jde_inclusion_rule` (JDE auto-include), `jel_o_code` (at least one O code present), `jel_no_o_code` (JEL codes present, none in O), `econlit_no_jel` (EconLit had no JEL codes, fell to LLM), `no_econlit_match` (no EconLit row found), `llm_yes`, `llm_no`, `llm_uncertain` (Stage 3a output). |
| `dev_llm_classification` | Raw LLM output for borderline rows: `yes`, `no`, or `uncertain`. Empty if not LLM-classified. |
| `dev_llm_justification` | One-sentence LLM rationale. Empty if not LLM-classified. |
| `dev_llm_model` | Anthropic model identifier used for classification. |
| `dev_llm_prompt_version` | Project-internal prompt version string (e.g., `dev-classify-v1`). |

## RCT classification

| Column | Description |
|---|---|
| `rct_classification` | LLM classification: `yes`, `no`, or `uncertain`. |
| `rct_subtype` | One of: `individual`, `cluster`, `encouragement`, `sub_component`, `follow_up`, `field_experiment`, `n/a`. Distinguishes RCT designs; `field_experiment` is used for lab-in-the-field studies that involve real-world manipulation. |
| `rct_confidence` | LLM-self-reported confidence: `high`, `medium`, `low`. |
| `rct_justification` | One-sentence LLM rationale. |
| `rct_llm_model` | Anthropic model identifier used. |
| `rct_llm_prompt_version` | Project-internal prompt version (e.g., `rct-classify-v1`). |

## Definitions

**Development economics paper.** Any paper meeting at least one of:
- The journal is *Journal of Development Economics* (auto-include)
- The article carries at least one JEL O-code in EconLit
- An LLM classifier judged the paper to be primarily about low-/middle-income country economic outcomes, institutions, or policy

**Randomized controlled trial (RCT).** A paper reporting the results of one of:
- An individual-randomized real-world intervention
- A cluster-randomized intervention (school, village, firm, etc.)
- An encouragement design (random assignment of encouragement to take up a treatment)
- A sub-component RCT (random variation in one component of a larger program)
- A long-run follow-up of a previously-conducted RCT (subtype: `follow_up`)
- A field experiment with random assignment to a real-world treatment
- A lab-in-the-field experiment that includes real-world manipulation (subtype: `field_experiment`)

Excluded from RCT: pure quasi-experiments (DID, RD, IV, synthetic control); structural and theoretical models; reviews and meta-analyses; lab-in-the-field experiments without real-world manipulation; long-run studies with only observational data.

## Notes on interpretation

- A small share of rows (typically <5%) have `rct_classification = "uncertain"`. These are flagged for manual review and should not be treated as RCTs in summary tables without inspection.
- `is_development = "BORDERLINE"` rows in the final output are residual cases the LLM could not resolve. These are usually papers without abstracts or with very short abstracts. Manual review recommended.
- `jel_codes_o_only` empty does not always mean the paper is not development — JDE papers, by inclusion rule, are TRUE regardless of JEL coding.
- The `abstract_source` field documents the provenance of every abstract; for downstream replication of LLM classification, abstracts marked `none_*` were not classified.
