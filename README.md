# Development RCTs in Top Economics Journals, 2021-2025

This project assembles a replicable dataset of all randomized controlled trials in development economics published in twelve leading economics journals over 2021-2025. See `PROJECT_MEMO.md` for the full motivation, scope, and methodological decisions.

## Quick start

### Prerequisites

- Python 3.10 or later (3.14 used in development)
- Anthropic API key (for Stages 2-3 only); not required for Stage 1
- EBSCOhost / EconLit access through an institutional library (for Stage 2 only)

### Pipeline overview

| Stage | Script | Inputs | Output | Runtime | Dependencies |
|-------|--------|--------|--------|---------|--------------|
| 0     | `00_build_jel_lookup.py` | (AEA website) | `data/jel_lookup.csv` | <1 min | stdlib |
| 0b    | `00b_build_lmic_countries.py` | (World Bank API) | `data/lmic_countries.csv` | <1 min | stdlib |
| 1a    | `01_openalex_pull.py` | (OpenAlex API) | `data/raw_openalex_2021_2025.csv` | 3-5 min | stdlib |
| 1b    | `01b_crossref_abstract_backfill.py` | Stage 1a output | updates `data/raw_with_abstracts_2021_2025.csv` | 5-10 min | stdlib |
| 1c    | `01c_semantic_scholar_backfill.py` | Stage 1b output | updates same file | <1 min | stdlib |
| 2     | `02_dev_filter.py` | Stage 1c output + `data/EconLit/*.csv` (manual export) + `data/jel_lookup.csv` + `data/lmic_countries.csv` | `data/dev_filtered.csv` | <1 min | stdlib |
| 3a    | `03a_dev_borderline_classify.py` | Stage 2 output | `data/dev_classified.csv` | ~25 min | `anthropic` |
| 3b    | `03b_rct_classify.py` | Stage 3a output | `data/rct_classified.csv` | ~75 min | `anthropic` |
| 4     | `04_assemble.py` | Stage 3b output | `data/final_dataset.csv`, `data/summary_journal_year.csv` | <1 min | stdlib |
| 5     | `05_make_charts.py` | Stage 4 output | `data/figures/*.{png,pdf}` | <1 min | `matplotlib` |

### Setup

```bash
# Stages 3a-3b require the Anthropic SDK
pip install -r requirements.txt

# And an API key in your environment
export ANTHROPIC_API_KEY="sk-ant-..."     # bash/zsh
$env:ANTHROPIC_API_KEY = "sk-ant-..."     # PowerShell
```

Before running Stage 1, optionally edit the `EMAIL` constant at the top of each script in `scripts/` to your own contact email; this places API queries in the OpenAlex / Crossref / Semantic Scholar "polite pools."

### Running the pipeline

```bash
cd scripts

# Stage 0 (one-time bootstrap; commit the output to share with collaborators)
python 00_build_jel_lookup.py

# Stage 1: metadata + abstracts
python 01_openalex_pull.py
python 01b_crossref_abstract_backfill.py
python 01c_semantic_scholar_backfill.py

# Stage 2 requires the EconLit export to be in place under data/EconLit/.
# See docs/econlit_export_instructions.md for the export procedure.
python 02_dev_filter.py

# Stage 3 (uses Anthropic API; resumable on rerun)
python 03a_dev_borderline_classify.py
python 03b_rct_classify.py

# Stage 4: final dataset assembly
python 04_assemble.py

# Stage 5: figures
python 05_make_charts.py
```

The scripts in Stages 1, 2, and 4 are **idempotent** — rerunning produces the same output. Stages 3a and 3b are **resumable** — rerunning skips already-classified rows.

### Optional: model override

The Anthropic model can be overridden via environment variable. Default is `claude-sonnet-4-5`.

```bash
export ANTHROPIC_MODEL="claude-opus-4-7"
```

## Repository layout

```
dev-econ-rct-review/
├── README.md                  Quick-start guide (this file)
├── REPLICATION.md             AEA-format replication-package documentation
├── PROJECT_MEMO.md            Full project plan, methodology, post-run outturn
├── LICENSE                    MIT for code; CC-BY for derived data
├── CITATION.cff               Machine-readable citation metadata
├── requirements.txt           Pinned Python dependencies
├── .gitignore                 Excludes licensed EconLit raw exports
├── scripts/                   All pipeline scripts (Stages 0, 0b, 1a-c, 2, 3a-b, 4, 5)
├── data/                      Inputs and outputs (CSV); figures in data/figures/
└── docs/
    ├── codebook.md                       Column dictionary for final_dataset.csv
    ├── data_attribution.md               Per-source licensing and citation
    ├── econlit_export_instructions.md    Manual EBSCO export procedure
    └── llm_prompts.md                    (Optional companion: full prompt text)
```

## Replicability and sharing

Full replication-package documentation is in `REPLICATION.md`, which follows the AEA Data Editor template. Highlights:

- **Code license:** MIT (`LICENSE`).
- **Data license:** Derived data products under CC-BY-4.0; raw EconLit exports are licensed and excluded from version control. Per-source attribution in `docs/data_attribution.md`.
- **Pinned dependencies:** `requirements.txt` (only `anthropic` and `matplotlib` are non-stdlib).
- **Snapshot timestamps:** every Stage 1 row records the UTC time at which OpenAlex was queried.
- **LLM determinism:** `temperature = 0`; model identifier and prompt version recorded per-row.
- **Resumability:** Stage 3a and 3b scripts checkpoint every 50 calls and resume from the last saved row on rerun.
- **Reproducibility checksum:** Stage 4 logs SHA-256 of the final outputs.

### Recommended sharing path

For public release alongside a publication:

1. **Push to GitHub.** Initialize a repository, push the working tree (the `.gitignore` excludes the licensed EconLit raw exports automatically). Use a private repository while a paper is under review; flip to public on publication.
2. **Mint a DOI through Zenodo.** Enable the GitHub-Zenodo integration (one-click in Zenodo's GitHub settings); each tagged release on GitHub then automatically receives a permanent DOI suitable for citing in the paper. The `CITATION.cff` file is automatically rendered as a "Cite this repository" button on the GitHub landing page.
3. **(Optional) AEA Data and Code Repository.** If the paper is submitted to an AEA journal, the AEA Data Editor will request deposit at `openicpsr.org`. The `REPLICATION.md` is structured to satisfy their template. Adapt minor details (e.g., final DOI) at submission time.

### Cost to a replicator

Approximately **$7 in Anthropic API charges** to reproduce Stage 3 end-to-end. All other inputs are free.

## Contact

Jessica Leight — J.Leight@cgiar.org
