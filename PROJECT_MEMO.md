# Project memo: Identifying development RCTs in top economics journals, 2021-2025

**Last updated:** 2026-05-06
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

- **Development scope.** Any JEL code beginning with **O** counts a paper as development. This is broader than O1 (Economic Development) alone and conventionally bounds the field. Borderline cases — for example, a paper with no JEL O codes but content suggestive of a developing-country setting — are escalated to LLM classification of the abstract for a yes/no judgment.
- **RCT scope.** We classify a paper as an RCT if it reports the results of a real-world randomized intervention. This includes individual-randomized RCTs, cluster RCTs, encouragement designs, RCTs of one component within a larger program, and long-run follow-ups of prior RCTs (the latter flagged as a distinct subtype). Lab-in-the-field experiments are excluded **unless** they involve a real-world manipulation of incentives, information, or services that participants experience outside the experimental setting.
- **Output format.** Single CSV (UTF-8). Optionally also a Stata `.dta` if requested.
- **Execution environment.** Local, on Jessica's Windows machine. Python 3.14.x. Stage 1 uses standard library only; later stages introduce a small set of pinned dependencies (`requirements.txt`).
- **Sharing.** The project folder is structured so that any collaborator with Python and an Anthropic API key (for Stage 3) can re-run end-to-end. Raw data exports from EBSCO/EconLit (Stage 2) are licensed and therefore checked into the project as derived JEL-code lookups rather than full record exports; this is documented in the README.

## 4. Workflow

The pipeline has four stages. Each stage produces a standalone CSV; each subsequent stage takes the previous stage's CSV as its only input. This makes any stage independently re-runnable and any intermediate state inspectable.

### Stage 1 — Metadata acquisition (multi-source)

Stage 1 is implemented as a chain of three scripts. Each successive script attempts to fill abstracts for rows where the prior script found none, recording the provenance of every abstract in an `abstract_source` column. This chain is necessary because the three largest publishers in the journal list (Elsevier for JDE, Wiley for Econometrica, University of Chicago Press for JPE) restrict abstract redistribution under their licensing agreements with both OpenAlex and Crossref. We discovered this empirically during Stage 1b: of 1,115 OpenAlex-missing abstracts, Crossref recovered only 21.

**Stage 1a — OpenAlex pull.** `scripts/01_openalex_pull.py` queries the OpenAlex Works API by ISSN and publication year for each of the twelve journals, paginates through results with cursor-based pagination, and writes one row per article to `data/raw_openalex_2021_2025.csv`. Captured fields include DOI, title, abstract (reconstructed from OpenAlex's inverted index), authors, journal name, ISSN, publication year/date, volume, issue, page range, and OpenAlex document type. A UTC snapshot timestamp is recorded on every row. The "polite pool" mailto header is set; OpenAlex rate-limits cooperatively in this mode (~10 req/sec, 100,000 req/day). Standard-library only.

**Stage 1b — Crossref backfill.** `scripts/01b_crossref_abstract_backfill.py` queries the Crossref Works API by DOI for every row missing an OpenAlex abstract. Crossref returns abstracts as JATS-tagged HTML; tags are stripped. Output: `data/raw_with_abstracts_2021_2025.csv` (also tracked thereafter as the "running" file). Standard-library only.

**Stage 1c — Semantic Scholar backfill.** `scripts/01c_semantic_scholar_backfill.py` queries the Semantic Scholar Graph API batch endpoint (up to 500 DOIs per request) for any rows still without an abstract. Updates `data/raw_with_abstracts_2021_2025.csv` in place. Standard-library only. No API key required at our query volume; we run at ~1 batch/sec.

**Stage 1d — EconLit backfill (deferred to Stage 2).** Any rows still missing abstracts after 1a-1c — primarily JDE articles given Elsevier's licensing posture — are backfilled from EconLit when the EBSCO export is performed for JEL codes (Stage 2). EconLit records include both abstracts and JEL codes, so a single export serves both purposes.

**Stage 1e — Manual residual.** Any rows still missing abstracts after 1d are flagged for manual retrieval from the publisher's website, restricted to development-tagged rows that survive Stage 2 filtering. This minimizes manual work to the actually-relevant subset.

The `abstract_source` column takes values `openalex`, `crossref`, `semantic_scholar`, `econlit`, `manual`, or one of the `none_*` flags indicating why no abstract is available.

### Stage 0 — Bootstrap: JEL descriptor → code lookup

`scripts/00_build_jel_lookup.py` fetches the AEA's authoritative JEL classification (https://www.aeaweb.org/econlit/jelCodes.php?view=jel) and writes `data/jel_lookup.csv` with one row per leaf code (856 in total) and both bare and prefixed descriptor forms. Stage 2 uses this lookup to map EconLit's descriptor strings to JEL codes.

### Stage 2 — Development-topic filtering

`scripts/02_dev_filter.py` reads all CSVs from `data/EconLit/`, restricts to the twelve in-scope journals (matched by ISSN, not by title — see EconLit export doc for why this matters), parses each row's `subjects` field by splitting on " ; ", and resolves descriptors to JEL codes via the lookup. Three indexes are built — by DOI, by `(journal, volume, issue, first_page)`, and by `(journal, normalized_title)` — to handle the fact that EconLit DOI population is uneven across publishers (100% for AEA journals; 1-30% for UChicago, Wiley, MIT). The merge tries DOI first, then bibliographic tuple, then title.

The development filter is applied as: JDE → TRUE; any JEL O code → TRUE; JEL codes present, none in O → FALSE; no JEL codes → BORDERLINE. The `dev_filter_source` column records the rule that fired. Stage 2 also backfills any abstracts still missing after the Stage 1 chain from EconLit's abstract field (Stage 1d, deferred from Stage 1).

Output: `data/dev_filtered.csv`.

### Stage 3a — LLM classification of borderline rows

`scripts/03a_dev_borderline_classify.py` reads `dev_filtered.csv`, identifies rows with `is_development == 'BORDERLINE'` of type `article` with non-empty abstracts, and asks the Anthropic API (default: `claude-sonnet-4-5`) to classify each as development / not / uncertain. The system prompt is cached (5-minute TTL) for cost efficiency; `temperature = 0` for determinism. Each classified row records the model identifier, prompt version, and the LLM's one-sentence justification. Output: `data/dev_classified.csv`.

The script is **resumable** — rerunning skips rows already classified — and writes a checkpoint every 50 calls.

### Stage 3b — LLM classification of RCT status

`scripts/03b_rct_classify.py` reads `dev_classified.csv` (or `dev_filtered.csv` if 3a was skipped), filters to development papers of type `article` with non-empty abstracts, and asks the LLM to classify each as RCT / not / uncertain with subtype (`individual`, `cluster`, `encouragement`, `sub_component`, `follow_up`, `field_experiment`, `n/a`) and self-reported confidence. The prompt encodes the project's RCT definition: cluster, encouragement, sub-component, and long-run follow-up RCTs all qualify; lab-in-the-field is excluded unless real-world manipulation is involved. Output: `data/rct_classified.csv`.

A second pass that fetches article introductions for "uncertain" rows is scaffolded but deferred to a later iteration; uncertain rows are flagged in the output for manual review.

### Stage 4 — Output assembly and summary

`scripts/04_assemble.py` reads the latest available output (rct_classified > dev_classified > dev_filtered, in priority order), filters to `is_development == 'TRUE'` AND `type == 'article'`, and writes `data/final_dataset.csv` with the columns documented in `docs/codebook.md`. A second output, `data/summary_journal_year.csv`, is a wide table of counts per journal-year (development total, RCT yes / no / uncertain). A SHA-256 of each output is logged for reproducibility verification.

## 5. File structure

```
dev-econ-rct-review/
├── PROJECT_MEMO.md               (this file)
├── README.md                     (quick how-to-run for collaborators)
├── requirements.txt              (pinned dependencies for Stages 2-4)
├── scripts/
│   ├── 01_openalex_pull.py
│   ├── 02_dev_filter.py          (forthcoming)
│   ├── 03_rct_classify.py        (forthcoming)
│   └── 04_assemble.py            (forthcoming)
├── data/
│   ├── raw_openalex_2021_2025.csv    (Stage 1 output)
│   ├── econlit_jel_codes.csv         (Stage 2 input, from EBSCO export)
│   ├── dev_filtered.csv              (Stage 2 output)
│   ├── rct_classified.csv            (Stage 3 output)
│   ├── final_dataset.csv             (Stage 4 output)
│   └── *.log                         (per-stage run logs)
└── docs/
    ├── codebook.md
    ├── econlit_export_instructions.md
    └── llm_prompts.md                (full prompt text for Stages 2-3, for transparency)
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

## 7a. Final pipeline outturn (2026-05-07 run)

The full pipeline produced **1,601 development-economics articles** across the twelve in-scope journals over 2021-2025, of which **417 (26.0%)** are classified as randomized controlled trials. Per-journal counts:

| Journal      | Dev articles | RCTs | RCT rate |
|--------------|-------------:|-----:|---------:|
| AER          | 118 | 46  | 39.0% |
| AERI         | 32  | 14  | 43.8% |
| AEJ:Applied  | 78  | 32  | 41.0% |
| AEJ:EP       | 57  | 10  | 17.5% |
| ECMA         | 48  | 11  | 22.9% |
| QJE          | 42  | 14  | 33.3% |
| JPE          | 51  | 12  | 23.5% |
| RES          | 68  | 13  | 19.1% |
| RESTAT       | 138 | 33  | 23.9% |
| EJ           | 146 | 41  | 28.1% |
| JEEA         | 59  | 18  | 30.5% |
| JDE          | 764 | 173 | 22.6% |
| **Total**    | **1,601** | **417** | **26.0%** |

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
- `data/final_dataset.csv` — 1,601 rows, single-row-per-article
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

## 8. Known limitations

- OpenAlex abstract coverage is incomplete for some recent journal issues; expect a small share of rows to require Crossref or manual backfill.
- EconLit JEL code coverage lags publication by several months; the most recent issues (late 2025) may not yet have JEL codes assigned. Borderline LLM classification handles these cases.
- LLM classification of RCT status is highly accurate from abstracts when the design is clearly described, but some abstracts use ambiguous language (e.g., "we randomize the order of survey questions" — not an RCT in our sense). The two-pass design and explicit subtype taxonomy are intended to catch these.
- The "any O code" development filter is broad; it will include some macro-development and growth-theory papers that are not what most readers would call empirical development. A stricter filter (e.g., O1 only, or restriction to a subset of country-classification fields) can be applied after Stage 4 by post-hoc subsetting.
