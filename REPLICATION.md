# Replication package: Development RCTs in Top Economics Journals, 2021-2025

This document follows the structure recommended by the AEA Data Editor's openly-published "Template README" for empirical economics replication packages, adapted for a bibliometric/dataset project.

## Overview

The code in this replication package constructs `data/final_dataset.csv`, a dataset of all randomized controlled trials in development economics published in twelve leading economics journals over the 2021-2025 period, by combining six external data sources and applying a multi-stage filtering and classification pipeline. It also produces a journal-year summary table (`data/summary_journal_year.csv`) and five figures (`data/figures/`).

## Data availability and provenance statements

### Statement about rights

The author of this replication package certifies that they have legitimate access to and permission to use all data employed in its construction. Data sources, licenses, and access procedures are documented per source in `docs/data_attribution.md`. In summary:

- **OpenAlex, Crossref, Semantic Scholar, AEA JEL Classification, World Bank Country Classification:** open access, freely redistributable in derived form.
- **EconLit (via EBSCOhost):** licensed; raw exports cannot be redistributed in this repository. Replicators must obtain their own exports through institutional library access following the procedure documented in `docs/econlit_export_instructions.md`.
- **Anthropic Claude API:** paid service; replicators must supply their own API key.

### License for data

Derived data products in this repository (under `data/`, excluding the licensed EconLit raw exports, which are not present) are released under **CC-BY-4.0**. Code is released under **MIT**. See `LICENSE` for the full text.

### Summary of availability

- All open-data inputs are accessible at no cost via public APIs.
- One input (EconLit) is licensed and requires institutional library access.
- One pipeline stage (LLM classification) requires a paid Anthropic API key (~$7 in API charges to reproduce the full Stage 3 pipeline).
- All other costs are zero.

### Details on each data source

See `docs/data_attribution.md` for full per-source documentation including license, access mechanism, retrieval date, and citation language.

## Dataset list

| File | Description | Provenance |
|------|-------------|------------|
| `data/final_dataset.csv` | Final dataset, one row per development article (1,601 rows × 28 columns) | Stage 4 output; column dictionary in `docs/codebook.md` |
| `data/summary_journal_year.csv` | Counts by journal × year (60 rows) | Stage 4 output |
| `data/figures/fig*.png` and `.pdf` | Five publication figures | Stage 5 output |
| `data/jel_lookup.csv` | JEL descriptor → code mapping (856 leaf codes) | Bootstrapped from AEA JEL classification (Stage 0) |
| `data/lmic_countries.csv` | Low/lower-middle/upper-middle-income country list with name variants | Bootstrapped from World Bank API (Stage 0b) |

Intermediate artifacts (`data/raw_openalex_2021_2025.csv`, `data/raw_with_abstracts_2021_2025.csv`, `data/dev_filtered.csv`, `data/dev_classified.csv`, `data/rct_classified.csv`) are not included in this distribution but are reproducibly regenerated from the scripts.

## Computational requirements

### Software

| Software / package | Version used | Source |
|---|---|---|
| Python | 3.14.x (3.10+ acceptable) | https://python.org |
| `anthropic` | 0.40.0+ | https://pypi.org/project/anthropic/ |
| `matplotlib` | 3.7.0+ | https://pypi.org/project/matplotlib/ |

All other code uses the Python standard library. See `requirements.txt` for the pinned dependency list.

### Memory and runtime

| Stage | Wall time | Notes |
|---|---:|---|
| 0 (JEL bootstrap) | <1 min | Single web fetch + parse |
| 0b (LMIC bootstrap) | <1 min | Single API fetch + parse |
| 1a (OpenAlex pull) | 2-5 min | ~5,635 records via paginated REST |
| 1b (Crossref backfill) | 5-10 min | ~1,100 DOI lookups |
| 1c (Semantic Scholar backfill) | <1 min | Batch endpoint, 3 batches |
| 2 (Dev filter) | <1 min | Local merge + regex |
| 3a (Dev LLM classification) | ~25 min | 672 Anthropic API calls |
| 3b (RCT LLM classification) | ~75 min | 1,498 Anthropic API calls |
| 4 (Final assembly) | <1 min | Local CSV write |
| 5 (Figures) | <1 min | matplotlib rendering |
| **Total wall time** | **~110 min** | dominated by Stage 3 |

Memory: <1 GB at peak. Disk: ~50 MB total (data + figures + logs).

### Compute and API cost

The full pipeline costs approximately **$7** in Anthropic API charges (Stage 3a + 3b combined, using `claude-sonnet-4-5` with 2026-vintage pricing). All other inputs are free.

### External services

- **OpenAlex polite pool:** mailto header recommended; the script sets one. No registration required.
- **Crossref polite pool:** same.
- **Semantic Scholar:** no API key required at our query volume.
- **Anthropic API:** required for Stage 3 only. Replicators provide their own key.

## Description of programs / code

### Pipeline scripts (in `scripts/`)

| Script | Stage | Inputs | Outputs |
|--------|------:|--------|---------|
| `00_build_jel_lookup.py` | 0 | (web) | `data/jel_lookup.csv` |
| `00b_build_lmic_countries.py` | 0b | (web) | `data/lmic_countries.csv` |
| `01_openalex_pull.py` | 1a | (web) | `data/raw_openalex_2021_2025.csv` |
| `01b_crossref_abstract_backfill.py` | 1b | Stage 1a output | `data/raw_with_abstracts_2021_2025.csv` |
| `01c_semantic_scholar_backfill.py` | 1c | Stage 1b output | updates Stage 1b output in place |
| `02_dev_filter.py` | 2 | Stage 1c output + `data/EconLit/` + `data/jel_lookup.csv` + `data/lmic_countries.csv` | `data/dev_filtered.csv` |
| `03a_dev_borderline_classify.py` | 3a | Stage 2 output | `data/dev_classified.csv` |
| `03b_rct_classify.py` | 3b | Stage 3a output | `data/rct_classified.csv` |
| `04_assemble.py` | 4 | Stage 3b output | `data/final_dataset.csv`, `data/summary_journal_year.csv` |
| `05_make_charts.py` | 5 | Stage 4 output | `data/figures/*.png`, `.pdf` |

### Documentation

| File | Purpose |
|------|---------|
| `README.md` | Quick-start guide |
| `PROJECT_MEMO.md` | Full project plan, methodological decisions, and post-run outturn |
| `docs/codebook.md` | Column dictionary for `final_dataset.csv` |
| `docs/data_attribution.md` | Per-source licensing and citation |
| `docs/econlit_export_instructions.md` | Manual EBSCO/EconLit export procedure |
| `LICENSE` | MIT for code; data licensing summary |
| `CITATION.cff` | Citation metadata |
| `REPLICATION.md` | This file |

## Instructions to replicators

### Step 1 — Clone the repository

```bash
git clone https://github.com/<user>/dev-econ-rct-review.git
cd dev-econ-rct-review
```

### Step 2 — Set up the Python environment

```bash
python -m venv .venv
source .venv/bin/activate       # macOS/Linux
.venv\Scripts\Activate.ps1      # Windows PowerShell
pip install -r requirements.txt
```

### Step 3 — Set up your Anthropic API key

```bash
export ANTHROPIC_API_KEY="sk-ant-..."        # macOS/Linux
$env:ANTHROPIC_API_KEY = "sk-ant-..."        # Windows PowerShell
```

### Step 4 — Obtain the EconLit raw exports

Following the procedure in `docs/econlit_export_instructions.md`, perform per-journal EBSCOhost exports for the twelve in-scope journals over 2021-2025 using the ISSN-based query template documented there. Save the exported CSVs to `data/EconLit/`. Approximately 11 files, ~5,300 rows total.

This is the one manual step in the pipeline. EBSCO does not offer programmatic API access for individual researchers.

### Step 5 — Run the pipeline

The pipeline is sequential. Each stage reads the prior stage's output. Run from the `scripts/` directory:

```bash
python 00_build_jel_lookup.py
python 00b_build_lmic_countries.py
python 01_openalex_pull.py
python 01b_crossref_abstract_backfill.py
python 01c_semantic_scholar_backfill.py
python 02_dev_filter.py
python 03a_dev_borderline_classify.py
python 03b_rct_classify.py
python 04_assemble.py
python 05_make_charts.py
```

Stages 1, 2, and 4 are fully deterministic given the same inputs. Stages 3a and 3b are LLM-based and use `temperature = 0`, but minor classification differences may occur if Anthropic later versions the underlying model. Stages 3a and 3b are **resumable** — a Ctrl+C interruption is safe; rerunning continues from the last checkpoint.

### Step 6 — Verify

The final outputs should match these row counts (within model-version drift for Stage 3):

- `data/final_dataset.csv`: ~1,600 rows of development articles
- `data/summary_journal_year.csv`: 60 rows (12 journals × 5 years)
- `data/figures/`: 5 figures, each in PNG and PDF

The 2026-05-07 run produced `final_dataset.csv` with SHA-256 = `770306cf84b0922e55c45eae12b5c9ddad27613ffba0f67895fa76a41fc353db`.

## List of tables and programs

| Output | Source script | Notes |
|--------|---------------|-------|
| `data/final_dataset.csv` | `04_assemble.py` | Primary dataset |
| `data/summary_journal_year.csv` | `04_assemble.py` | Journal × year counts |
| `data/figures/fig1_rct_share_by_journal.{png,pdf}` | `05_make_charts.py` | Bar chart |
| `data/figures/fig2_rct_share_by_year.{png,pdf}` | `05_make_charts.py` | Line chart |
| `data/figures/fig3_rct_share_by_year_journal.{png,pdf}` | `05_make_charts.py` | Small multiples |
| `data/figures/fig4_rct_subtype_distribution.{png,pdf}` | `05_make_charts.py` | Bar chart |
| `data/figures/fig5_dev_papers_by_year_stacked.{png,pdf}` | `05_make_charts.py` | Stacked bars |

## References (cited in this replication package)

See `docs/data_attribution.md` for full citations of each underlying data source and `CITATION.cff` for the citation of this replication package.

## Acknowledgements

Pipeline development assistance: Anthropic Claude (Sonnet 4.5) via Claude Code, used as a coding pair-programmer during script development and as the LLM classifier in Stages 3a and 3b. All design decisions were the author's; the role of the LLM in pipeline construction was to draft and adjust code under direct supervision.
