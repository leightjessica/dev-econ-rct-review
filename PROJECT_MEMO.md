# Project memo: Identifying development RCTs in top economics journals, 2021-2025

**Last updated:** 2026-05-22
**Owner:** Jessica Leight (J.Leight@cgiar.org)
**Project root:** `~/IFPRI Dropbox/Jessica Leight/dev-econ-rct-review/`

## 1. Objective

We aim to construct a replicable dataset of all randomized controlled trials in development economics published in a defined set of leading economics journals over 2021-2025. The end product is a single CSV in which every row is one published article, with flags for (a) whether the paper falls within development economics and (b) whether the paper reports an RCT. The dataset is intended to be shareable with other researchers, so every step is designed to run from a clean clone of the project folder using publicly accessible inputs wherever feasible.

## 2. Scope

### Journals

Eleven general-interest journals are included with a development-topic restriction, plus one field journal taken in full.

| Code         | Journal                                                     | Filter        |
|--------------|-------------------------------------------------------------|---------------|
| AER          | American Economic Review                                    | Development   |
| AERI         | American Economic Review: Insights                          | Development   |
| AEJ_Applied  | American Economic Journal: Applied Economics                | Development   |
| AEJ_EP       | American Economic Journal: Economic Policy                  | Development   |
| ECMA         | Econometrica                                                | Development   |
| QJE          | Quarterly Journal of Economics                              | Development   |
| JPE          | Journal of Political Economy                                | Development   |
| RES          | Review of Economic Studies                                  | Development   |
| RESTAT       | Review of Economics and Statistics                          | Development   |
| EJ           | Economic Journal                                            | Development   |
| JEEA         | Journal of the European Economic Association                | Development   |
| JDE          | Journal of Development Economics                            | All articles  |

### Time window

Publication years 2021 through 2025 inclusive (using the OpenAlex `publication_year` field, which corresponds to the journal-issue year of record).

## 3. Key decisions

The following decisions were made at project inception and are recorded here so future re-runs are interpretable.

- **Development scope.** A paper is classified as development if any of the following holds (rules evaluated in order; first match wins): (i) the journal is JDE; (ii) at least one JEL code is in **O1 (Economic Development) or O2 (Development Planning and Policy)**; (iii) the article's title or abstract mentions a low-, lower-middle-, or upper-middle-income country (per the World Bank classification) or a developing-region term such as "Sub-Saharan Africa" or "developing countries". Rule (ii) was tightened from an earlier draft that also accepted O3 (Innovation), O4 (Growth), and O5 (Country Studies) codes; spot-checking revealed that O3/O4 codes routinely appear on innovation and growth papers based in high-income countries (e.g., AER's "Market Power and Innovation in the Intangible Economy" using French and US data), and that O5 codes were always co-assigned with O1/O2 in our sample, so adding them would not have changed the result. Rule (iii) was added because the Stage 1 abstract pull plus the JEL-code merge missed papers that name a developing country directly in the abstract but were never indexed in EconLit (e.g., empirical papers in RESTAT and EJ where EconLit's DOI population is sparse). Borderline cases — papers that satisfy none of the three rules but might still be development — are escalated to LLM classification of the abstract (Stage 3a) for a yes/no/uncertain judgment.
- **RCT scope.** We classify a paper as an RCT if it reports the results of a real-world randomized intervention. This includes individual-randomized RCTs, cluster RCTs, encouragement designs, RCTs of one component within a larger program, and long-run follow-ups of prior RCTs (the latter flagged as a distinct subtype). Lab-in-the-field experiments are excluded **unless** they involve a real-world manipulation of incentives, information, or services that participants experience outside the experimental setting.
- **Output format.** Single CSV (UTF-8). Optionally also a Stata `.dta` if requested.
- **Execution environment.** Local, on Jessica's Windows machine. Python 3.14.x. Stage 1 uses standard library only; later stages introduce a small set of pinned dependencies (`requirements.txt`).
- **Sharing.** The project folder is structured so that any collaborator with Python and an Anthropic API key (for Stage 3) can re-run end-to-end. Raw data exports from EBSCO/EconLit (Stage 2) are licensed and therefore checked into the project as derived JEL-code lookups rather than full record exports; this is documented in the README.

## 4. Workflow

The pipeline has six stages (numbered 0, 0b, 1, 2, 3a, 3b, 4, 5). Each stage produces a standalone CSV; each subsequent stage takes the previous stage's CSV as its only input. This makes any stage independently re-runnable and any intermediate state inspectable.

### Stage 0 — Bootstrap: JEL descriptor → code lookup

`scripts/00_build_jel_lookup.py` fetches the AEA's authoritative JEL classification (https://www.aeaweb.org/econlit/jelCodes.php?view=jel) and writes `data/jel_lookup.csv` with one row per leaf code (856 in total) and both bare and prefixed descriptor forms. Stage 2 uses this lookup to map EconLit's descriptor strings (e.g., "Microeconomic Analyses of Economic Development") back to JEL codes (e.g., "O12").

### Stage 0b — Bootstrap: LMIC country list

`scripts/00b_build_lmic_countries.py` fetches the World Bank's country classification by income level via the WB API and writes `data/lmic_countries.csv` with one row per country in LIC, LMIC, or UMIC plus a curated set of region terms ("Sub-Saharan Africa", "developing countries", etc.) and common name variants ("Russia" alongside "Russian Federation"; "Cote d'Ivoire" alongside "Côte d'Ivoire"; etc.). Stage 2 uses this list to apply the country-mention rule of the development filter.

### Stage 1 — Metadata acquisition (multi-source)

Stage 1 is implemented as a chain of three scripts. Each successive script attempts to fill abstracts for rows where the prior script found none, recording the provenance of every abstract in an `abstract_source` column. This chain is necessary because the three largest publishers in the journal list (Elsevier for JDE, Wiley for Econometrica, University of Chicago Press for JPE) restrict abstract redistribution under their licensing agreements with both OpenAlex and Crossref. We discovered this empirically during Stage 1b: of 1,115 OpenAlex-missing abstracts, Crossref recovered only 21.

**Stage 1a — OpenAlex pull.** `scripts/01_openalex_pull.py` queries the OpenAlex Works API by ISSN and publication year for each of the twelve journals, paginates through results with cursor-based pagination, and writes one row per article to `data/raw_openalex_2021_2025.csv`. Captured fields include DOI, title, abstract (reconstructed from OpenAlex's inverted index), authors, journal name, ISSN, publication year/date, volume, issue, page range, and OpenAlex document type. A UTC snapshot timestamp is recorded on every row. The "polite pool" mailto header is set; OpenAlex rate-limits cooperatively in this mode (~10 req/sec, 100,000 req/day). Standard-library only.

**Stage 1b — Crossref backfill.** `scripts/01b_crossref_abstract_backfill.py` queries the Crossref Works API by DOI for every row missing an OpenAlex abstract. Crossref returns abstracts as JATS-tagged HTML; tags are stripped. Output: `data/raw_with_abstracts_2021_2025.csv` (also tracked thereafter as the "running" file). Standard-library only.

**Stage 1c — Semantic Scholar backfill.** `scripts/01c_semantic_scholar_backfill.py` queries the Semantic Scholar Graph API batch endpoint (up to 500 DOIs per request) for any rows still without an abstract. Updates `data/raw_with_abstracts_2021_2025.csv` in place. Standard-library only. No API key required at our query volume; we run at ~1 batch/sec.

**Stage 1d — EconLit backfill (deferred to Stage 2).** Any rows still missing abstracts after 1a-1c — primarily JDE articles given Elsevier's licensing posture — are backfilled from EconLit when the EBSCO export is performed for JEL codes (Stage 2). EconLit records include both abstracts and JEL codes, so a single export serves both purposes.

**Stage 1e — Manual residual.** Any rows still missing abstracts after 1d are flagged for manual retrieval from the publisher's website, restricted to development-tagged rows that survive Stage 2 filtering. This minimizes manual work to the actually-relevant subset.

The `abstract_source` column takes values `openalex`, `crossref`, `semantic_scholar`, `econlit`, `manual`, or one of the `none_*` flags indicating why no abstract is available.

### Stage 2 — Development-topic filtering

`scripts/02_dev_filter.py` reads all CSVs from `data/EconLit/`, restricts to the twelve in-scope journals (matched by ISSN, not by title — see EconLit export doc for why this matters), parses each row's `subjects` field by splitting on " ; ", and resolves descriptors to JEL codes via the Stage-0 lookup. Three indexes are built for the EconLit→OpenAlex merge — by DOI, by `(journal, volume, issue, first_page)`, and by `(journal, normalized_title)` — to handle the fact that EconLit DOI population is uneven across publishers (100% for AEA journals; 1-30% for UChicago, Wiley, MIT). The merge tries DOI first, then bibliographic tuple, then title; this raises end-to-end EconLit-to-OpenAlex match coverage from 43% (DOI alone) to 84% in our 2026-05-07 run.

In parallel with the EconLit merge, Stage 2 builds a single case-insensitive word-boundary regex over the LIC/LMIC/UMIC country names and region terms loaded from `data/lmic_countries.csv` (Stage 0b) and scans each row's title and abstract for any country mention. The matched term and its source field (`title` vs `abstract`) are recorded in `country_match` and `country_match_in` columns.

The development filter is then applied as a five-rule decision tree, evaluated in order, with the `dev_filter_source` column recording which rule fired:

1. The journal is **JDE** → `is_development = TRUE`, `dev_filter_source = jde_inclusion_rule`
2. The article carries at least one JEL code beginning with **O1 or O2** → TRUE, `jel_o1_o2_code`
3. The title or abstract mentions an LMIC country or developing-region term → TRUE, `country_match`
4. EconLit returned JEL codes, none in O1 or O2, and no country mention → FALSE, `jel_no_dev`
5. No EconLit JEL codes available and no country mention → BORDERLINE, `no_signal` (or `econlit_no_jel` if the row was matched to EconLit but its `subjects` field was empty)

The `jel_codes_o_only` diagnostic column preserves the broader O-family code set (O1-O5) for audit purposes; the binding filter uses `jel_codes_o12_only`. Stage 2 also backfills any abstracts still missing after the Stage 1 chain from EconLit's abstract field (this is Stage 1d, deferred to Stage 2 because the EBSCO export is the authoritative source for both JEL codes and abstracts).

Output: `data/dev_filtered.csv`.

### Stage 3a — LLM classification of borderline rows

`scripts/03a_dev_borderline_classify.py` reads `dev_filtered.csv`, identifies rows with `is_development == 'BORDERLINE'` of type `article` with non-empty abstracts, and asks the Anthropic API (default: `claude-sonnet-4-5`) to classify each as development / not / uncertain. The system prompt is cached (5-minute TTL) for cost efficiency; `temperature = 0` for determinism. Each classified row records the model identifier, prompt version, and the LLM's one-sentence justification. Output: `data/dev_classified.csv`.

The script is **resumable** — rerunning skips rows already classified — and writes a checkpoint every 50 calls.

### Stage 3b — LLM classification of RCT status

`scripts/03b_rct_classify.py` reads `dev_classified.csv` (or `dev_filtered.csv` if 3a was skipped), filters to development papers of type `article` with non-empty abstracts, and asks the LLM to classify each as RCT / not / uncertain with subtype (`individual`, `cluster`, `encouragement`, `sub_component`, `follow_up`, `field_experiment`, `n/a`) and self-reported confidence. The prompt encodes the project's RCT definition: cluster, encouragement, sub-component, and long-run follow-up RCTs all qualify; lab-in-the-field is excluded unless real-world manipulation is involved. Output: `data/rct_classified.csv`.

A second pass that fetches article introductions for "uncertain" rows is scaffolded but deferred to a later iteration; uncertain rows are flagged in the output for manual review.

### Stage 3c — LLM classification by substantive topic

`scripts/03c_topic_classify.py` reads `data/final_dataset.csv` and assigns each development paper a primary and (optional) secondary code from a 16-element topic taxonomy: `agriculture, health, education, labor, firms, finance, social_protection, gender, political_economy, conflict_crime, environment, trade_macro, migration, infrastructure, behavioral_info, other`. Unlike Stages 3a and 3b, which use the Anthropic Python SDK, Stage 3c shells out to headless `claude -p` (authenticating via the user's local subscription OAuth) and pins the model to `claude-haiku-4-5-20251001`; structured output is enforced via `--json-schema`. The script is resumable, parallelized at four workers, and checkpoints every 50 calls. Output: `data/topic_classified.csv` (one row per development paper, with `primary_topic`, `secondary_topic`, `topic_confidence`, `topic_justification`, `topic_llm_model`, `topic_prompt_version`).

### Stage 3d — Blind-coding validation of Stage 3c

`scripts/03d_topic_validate.py` runs in two modes. BUILD mode (default) draws a stratified random sample of N papers (default 50) from `topic_classified.csv` — stratified by LLM primary topic, two papers per code with the residual filled proportional to code size — and writes `data/topic_validation_blind.csv` with the paper's bibliographic metadata, title, abstract, and three blank human-coder columns (`primary_topic_human`, `secondary_topic_human`, `notes`); the LLM's classification is NOT present in the blind sheet, to avoid anchoring. A companion `data/topic_validation_codebook.md` is written as a one-page cheat sheet of the 16 codes. REPORT mode (`--report`) merges human and LLM codes after the sheet is filled in, computing primary and secondary agreement, per-code precision and recall, a confusion matrix, and a side-by-side disagreement listing. Output: `data/topic_validation_report.md`.

### Stage 4 — Output assembly and summary

`scripts/04_assemble.py` reads the latest available output (rct_classified > dev_classified > dev_filtered, in priority order), filters to `is_development == 'TRUE'` AND `type == 'article'`, and writes `data/final_dataset.csv` with the columns documented in `docs/codebook.md`. A second output, `data/summary_journal_year.csv`, is a wide table of counts per journal-year (development total, RCT yes / no / uncertain). A SHA-256 of each output is logged for reproducibility verification.

### Stage 5 — Figures

`scripts/05_make_charts.py` reads `data/final_dataset.csv` and writes five publication-quality figures to `data/figures/`, each as PNG (300 dpi raster) and PDF (vector for inclusion in LaTeX manuscripts):

1. `fig1_rct_share_by_journal` — horizontal bar of RCT share per journal, with absolute counts annotated
2. `fig2_rct_share_by_year` — line chart of overall RCT share by publication year
3. `fig3_rct_share_by_year_journal` — small multiples by journal × year
4. `fig4_rct_subtype_distribution` — bar chart of the six RCT subtypes (individual, cluster, encouragement, sub-component, follow-up, field experiment)
5. `fig5_dev_papers_by_year_stacked` — stacked bars of development articles per year, split by RCT yes / not RCT / unclassified-no-abstract

Stage 5 has one external dependency (`matplotlib`); all other stages are stdlib-only or use `anthropic`.

## 5. File structure

```
dev-econ-rct-review/
├── README.md                              quick-start
├── REPLICATION.md                         AEA-format replication-package documentation
├── PROJECT_MEMO.md                        this file
├── LICENSE                                MIT (code) + data-licensing notes
├── CITATION.cff                           machine-readable citation metadata
├── requirements.txt                       pinned dependencies (anthropic, matplotlib)
├── .gitignore                             excludes licensed EconLit raw exports
├── scripts/
│   ├── 00_build_jel_lookup.py             Stage 0 bootstrap
│   ├── 00b_build_lmic_countries.py        Stage 0b bootstrap
│   ├── 01_openalex_pull.py                Stage 1a
│   ├── 01b_crossref_abstract_backfill.py  Stage 1b
│   ├── 01c_semantic_scholar_backfill.py   Stage 1c
│   ├── 02_dev_filter.py                   Stage 2
│   ├── 03a_dev_borderline_classify.py     Stage 3a (LLM)
│   ├── 03b_rct_classify.py                Stage 3b (LLM)
│   ├── 04_assemble.py                     Stage 4
│   └── 05_make_charts.py                  Stage 5
├── data/
│   ├── jel_lookup.csv                     Stage 0 output (committed)
│   ├── lmic_countries.csv                 Stage 0b output (committed)
│   ├── EconLit/*.csv                      Stage 2 inputs (NOT committed; licensed)
│   ├── raw_openalex_2021_2025.csv         Stage 1a output (not committed; regeneratable)
│   ├── raw_with_abstracts_2021_2025.csv   Stages 1b+1c output (not committed; regeneratable)
│   ├── dev_filtered.csv                   Stage 2 output (not committed; regeneratable)
│   ├── dev_classified.csv                 Stage 3a output (not committed; regeneratable)
│   ├── rct_classified.csv                 Stage 3b output (not committed; regeneratable)
│   ├── final_dataset.csv                  Stage 4 output (committed)
│   ├── summary_journal_year.csv           Stage 4 output (committed)
│   ├── figures/fig*.{png,pdf}             Stage 5 output (committed)
│   └── *.log                              per-stage run logs (not committed)
└── docs/
    ├── codebook.md                        column dictionary for final_dataset.csv
    ├── data_attribution.md                per-source licensing + citation language
    ├── econlit_export_instructions.md     manual EBSCO export procedure
    └── llm_prompts.md                     verbatim Stage 3a/3b prompts
```

## 6. Replicability and sharing

We have followed several conventions to keep the project shareable.

- **Stdlib-only Stage 1.** No external dependencies are required for the metadata pull, so a collaborator can re-run Stage 1 with only a working Python install.
- **Pinned dependencies.** Stages 2-4 list dependencies in `requirements.txt` with explicit version pins.
- **Snapshot timestamps.** Every Stage 1 row records the UTC time at which OpenAlex was queried. Re-running on a different day may yield small differences as OpenAlex's coverage updates; the timestamp makes such drift explicit.
- **Deterministic LLM calls.** Stages 2-3 set `temperature = 0` and record the model identifier and prompt version on every row, so classifications are reproducible to within Anthropic's model-version stability guarantees.
- **Licensed inputs.** EconLit/EBSCO output is licensed and is not redistributed. Collaborators who wish to re-run Stage 2 must perform their own EBSCO export following the documented procedure.
- **No raw PDF redistribution.** Where Stage 3 fetches article introductions, only the small text excerpts strictly needed for classification are stored (in the run log) and never the full article content.
- **Versioning.** The project folder can be initialized as a git repository at any time; data files in `data/` are large and may be excluded via `.gitignore` if shared via GitHub. For Dropbox-based sharing, the entire folder is portable.

## 7a. Final pipeline outturn (2026-05-07 run, manual exclusion 2026-05-22)

The full pipeline produced **1,600 development-economics articles** across the twelve in-scope journals over 2021-2025, of which **417 (26.1%)** are classified as randomized controlled trials. One article (DOI `10.1093/restud/rdae020`, "Estimating Equilibrium in Health Insurance Exchanges: Price Competition and Subsidy Design under the ACA", RES 2024) was manually excluded on review as a US ACA paper carrying O15 and O16 JEL codes but no development content; see Section 8 on the JEL-misassignment limitation. Per-journal counts:

| Journal      | Dev articles | RCTs | RCT rate |
|--------------|-------------:|-----:|---------:|
| AER          | 118 | 46  | 39.0% |
| AERI         | 32  | 14  | 43.8% |
| AEJ:Applied  | 78  | 32  | 41.0% |
| AEJ:EP       | 57  | 10  | 17.5% |
| ECMA         | 48  | 11  | 22.9% |
| QJE          | 42  | 14  | 33.3% |
| JPE          | 51  | 12  | 23.5% |
| RES          | 67  | 13  | 19.4% |
| RESTAT       | 138 | 33  | 23.9% |
| EJ           | 146 | 41  | 28.1% |
| JEEA         | 59  | 18  | 30.5% |
| JDE          | 764 | 173 | 22.6% |
| **Total**    | **1,600** | **417** | **26.1%** |

RCT subtype distribution (across the 417 yeses):

| Subtype          | Count | Share |
|------------------|------:|------:|
| individual       | 212 | 51% |
| cluster          | 125 | 30% |
| field_experiment | 58  | 14% |
| follow_up        | 16  | 4% |
| encouragement    | 3   | 1% |
| sub_component    | 1   | <1% |

Stage 3 LLM cost was approximately **$7.00** total ($1.50 for Stage 3a, $5.50 for Stage 3b). The system prompt was below Sonnet 4.5's 1,024-token caching threshold, so prompt caching did not activate; switching to a longer prompt (or to Haiku for the simpler Stage 3a task) would reduce the cost further on a re-run.

Reproducibility checksums (recorded in `data/04_assemble.log`):
- `data/final_dataset.csv` — 1,600 rows, single-row-per-article
- `data/summary_journal_year.csv` — 60 rows (12 journals × 5 years)

## 7b. Stage 1 outturn (as of 2026-05-06 run)

The three-stage Stage 1 chain produced 5,635 records across the twelve journals over 2021-2025. Aggregate abstract coverage was 80.2% after OpenAlex alone, 80.6% after Crossref backfill, and 83.2% after Semantic Scholar backfill. Per-journal article-only missing counts after all three sources:

| Journal | Total | Article rows missing abstract |
|---------|------:|------------------------------:|
| JDE     | 824   | 394 |
| ECMA    | 519   | 147 |
| JPE     | 633   | 121 |
| RES     | 509   | 12  |
| AER     | 611   | 11  |
| EJ      | 563   | 10  |
| JEEA    | 322   | 5   |
| QJE     | 242   | 2   |
| Others  | —     | ≤ 1 |

The pattern is consistent with publisher licensing: the three large publishers (Elsevier/JDE, Wiley/ECMA, UChicago Press/JPE) restrict abstract distribution to indexing services. Stage 1d (EconLit backfill, performed during Stage 2 export) is expected to close most of the JDE gap; ECMA and JPE will likely require some manual residual.

## 7c. Topic-classification validation (2026-05-22)

We drew a 50-paper stratified random sample (seed 42; two papers per LLM primary code where available, with the residual filled proportional to code size) from `topic_classified.csv` and blind-coded 49 of the 50 against the 16-code taxonomy. The 50th, the ACA paper described in Section 7a, was retained for the manual exclusion rather than re-coded. After canonicalizing the human codes to match the LLM's underscored variants (`political economy → political_economy`, `social protection → social_protection`, `trade_ macro → trade_macro`, `behavioral → behavioral_info`, `credit → finance`, `educatiion → education`), headline agreement on primary topic was **65.3% (32/49)** and on exact-match secondary topic was **34.7% (17/49)**. The pre-normalization raw figure of 49.0% understated accuracy, with much of the apparent disagreement reflecting label drift in the human sheet rather than substantive mismatch.

Per-code patterns from the 49-paper sample (precision / recall, primary topic):

- High-precision, high-recall: `education` (100% / 80%), `health` (75% / 100%), `social_protection` (100% / 67%), `political_economy` (67% / 80%).
- High precision but low recall: `gender` (100% / 40%). The LLM correctly tags gender-framed papers it identifies, but misses dowry, IPV, marriage-market, and women's-decision-making papers, routing them into `finance`, `behavioral_info`, or `conflict_crime`.
- Low precision, high recall: `behavioral_info` (25% / 100%). The code is over-assigned, absorbing labor, gender, and finance papers in which information provision is the mechanism rather than the substantive outcome.
- Substantive disagreement patterns from the 17 remaining mismatches: regulatory analyses of financial and macroeconomic policy routed to `political_economy` rather than `finance` or `trade_macro`; informal community-monitoring interventions (e.g., a Sierra Leone CDD aid evaluation; a Uganda livestock-monitoring program) classified as `political_economy` when the substantive outcome was infrastructure, social protection, or agriculture; and methodology papers (cell-phone-record commuting measurement) coded to the substantive domain of the data rather than `other`.

We assess that the substantive disagreements are concentrated in a small number of taxonomic ambiguities and are amenable to a targeted prompt revision; see Section 9.

## 8. Known limitations

- OpenAlex abstract coverage is incomplete for some recent journal issues; the Stage 1b-1d backfill chain reduces the gap to roughly 6% of articles, with the residual concentrated in ECMA, JPE, and JDE (publishers that restrict abstracts to indexing services).
- EconLit JEL code coverage lags publication by several months; the most recent issues (late 2025) may not yet have JEL codes assigned. The country-mention rule (Stage 2 step iii) and the borderline LLM classifier (Stage 3a) together catch these cases.
- LLM classification of RCT status is highly accurate from abstracts when the design is clearly described, but some abstracts use ambiguous language (e.g., "we randomize the order of survey questions" — not an RCT in our sense). The subtype taxonomy and the explicit "field_experiment" vs "n/a" distinction are intended to surface these. Six rows in our 2026-05-07 run were classified `uncertain` and are flagged for manual review.
- The country-mention rule uses bare country names with `\b` word-boundary matching. It catches "China" in "China's growth" but NOT "Chinese" in "Chinese economy" (the adjective form). It also does not include the bare term "Africa" (which our regex specifically excludes to avoid false positives from "African Americans" — though `\b` would in fact preclude that match; this conservative choice could be revisited in a v2 release). A handful of papers (~6 in our sample) were caught only by Stage 3a's LLM classifier, not by the structural country rule, because they used adjective forms or referred to "Africa" without a country name.
- The development scope is conservative on the JEL side (O1 + O2 only). A v2 could explore including specific O5 country-study codes (O53 Asia, O54 Latin America, O55 Africa) as additional inclusion signals, though in our 2026-05-07 sample no paper carried an O5 code without also carrying an O1 or O2 code, so this would have made no difference.
- The O1 + O2 inclusion rule is also vulnerable to JEL false positives — papers carrying O1- or O2-family codes that are not development by substantive content. The 2026-05-07 run produced one such case (DOI `10.1093/restud/rdae020`, "Estimating Equilibrium in Health Insurance Exchanges: Price Competition and Subsidy Design under the ACA", a US ACA paper carrying O15 and O16 codes), which was manually excluded on 2026-05-22 (Section 7a). The O15 (Human Resources; Income Distribution) and O16 (Financial Markets; Capital Investment) codes appear most prone to this misassignment because they map to substantive topics that are also studied extensively in high-income contexts. We assess that future re-runs should re-screen O1/O2-only inclusions (i.e., those without an LMIC country signal) for false positives, either via a brief LLM pass or a manual review of the residual list.

## 9. Pending: prompt v2 refinements and re-run

Stage 3c is scheduled for a re-run under a v2 prompt that incorporates four refinements identified from the Stage 3d validation (Section 7c). The mechanical steps for the re-run are: (i) edit the `USER_INSTRUCTION` block in `scripts/03c_topic_classify.py` (lines ~93-132) as specified below; (ii) bump `PROMPT_VERSION` from `topic-classify-v1` to `topic-classify-v2`; (iii) delete or rename `data/topic_classified.csv` so the resume-skip logic does not preserve v1 classifications; (iv) re-run `python scripts/03c_topic_classify.py` on all 1,600 papers; (v) re-run Stage 4 (`scripts/04_assemble.py`) is NOT required because `final_dataset.csv` does not carry the topic columns. After re-classification, a fresh held-out sample (target ~25-30 papers, different seed, stratified by the v2 LLM assignments) should be drawn for clean re-validation of the refined prompt, to avoid the train-on-the-validation-set issue with the existing 49-paper sample.

The four refinements (with proposed edits to the `USER_INSTRUCTION` block):

1. **Measurement papers route to `other`.** When the paper's headline contribution is improving the measurement of a phenomenon rather than estimating a treatment effect or a substantive relationship, the primary code is `other` regardless of the domain measured. Example from the validation set: "Measuring Commuting and Economic Activity inside Cities with Cell Phone Records" (RESTAT 2021) infers spatial income from cell-phone data; the contribution is methodological, so primary should be `other`, not `labor` or `infrastructure`. *Proposed edit:* append to DISAMBIGUATION — "Papers whose headline contribution is improving the measurement of a phenomenon (rather than estimating a treatment effect or a substantive relationship) => primary=other, regardless of the substantive domain of the measured object."

2. **Expand `gender` triggers.** The `gender` code should fire whenever any of the following is the substantive focus (not merely a heterogeneity cut): dowry, marriage markets, intimate partner violence (IPV), women's empowerment, female genital mutilation (FGM), women's decision-making, or intra-household allocation when the gender lens is central. For IPV specifically, the secondary code should be `conflict_crime`. Validation-set examples currently misclassified: "Saving for dowry: Evidence from rural India" (JDE 2021, LLM=finance), "Culture and the Historical Fertility Transition" (RES 2022, LLM=behavioral_info), "Dynamic Impacts of Lockdown on Domestic Violence in Chile" (RESTAT 2024, LLM=conflict_crime). *Proposed edit:* replace the `gender` entry in CODE GUIDE with — "gender: women's empowerment, dowry, marriage markets, intimate partner violence (IPV), female genital mutilation (FGM), women's decision-making, intra-household allocation when the gender lens is central, GBV. Use as primary whenever any of these is the substantive focus, not merely a heterogeneity cut. For IPV specifically, secondary=conflict_crime."

3. **`political_economy` requires formal governing actors as primary.** As primary, the code should be reserved for papers whose substantive focus is the behavior of elected or appointed officials operating within formal governing institutions — corruption among public officials, elections, state capacity, bureaucracy, or formal governance reforms. Informal-institution interventions (community monitoring, social accountability committees, village-level mobilization, community-driven development aid) should use the substantive outcome code (`infrastructure`, `education`, `agriculture`, `social_protection`, `health`) as primary, with `political_economy` available as secondary if institutional change is part of the intervention. Validation-set examples: "Community monitoring and social accountability in development projects: Experimental evidence from Uganda" (JDE 2025; livestock outcome, not political_economy primary); "Long-Run Effects of Aid: Forecasts and Evidence from Sierra Leone" (EJ 2023; CDD aid with infrastructure outcomes). *Proposed edit:* replace the `political_economy` entry in CODE GUIDE with — "political_economy: the behavior of elected or appointed officials within formal governing institutions — corruption among public officials, elections, state capacity, bureaucracy, formal governance reforms. Use as primary only when the focus is on formal political actors and institutions; informal community-monitoring or social-accountability interventions belong under their substantive outcome code, with political_economy as a secondary code at most."

4. **Regulatory policy in finance/macro/trade stays in those buckets.** Analyses of regulatory policy concerning financial markets, macroeconomic policy, or trade policy should be classified as `finance`, `trade_macro`, or the appropriate substantive code as primary. `political_economy` is appropriate only when the paper's lens is explicitly on the decision-making of the political actors who set the regulation, not when the lens is on the regulation's economic effects. Validation-set example: "China's Model of Managing the Financial System" (RES 2021; finance/political_economy by the LLM, finance by the human coder). *Proposed edit:* append to DISAMBIGUATION — "Regulatory policy analyses concerning financial markets, macroeconomic policy, or trade policy => use the substantive code (finance, trade_macro) as primary; political_economy is appropriate only when the paper's lens is explicitly on the decision-making of the political actors who set the regulation, not on the regulation's economic effects."

The proposed edits are mechanical and can be applied by a focused edit pass on `scripts/03c_topic_classify.py`. Once applied and the re-run is complete, the v2 results should be compared to the v1 results on the 49-paper sample (a sanity check that no code's recall regresses) before drawing the fresh held-out sample.
