# Development RCTs in Top Economics Journals, 2021-2025

This project assembles a replicable dataset of all randomized controlled trials in development economics published in twelve leading economics journals over 2021-2025. See `PROJECT_MEMO.md` for the full motivation, scope, and methodological decisions.

## Quick start

### Prerequisites

- Python 3.10 or later (3.14 used in development)
- Anthropic API key (for Stages 2-3 only); not required for Stage 1
- EBSCOhost / EconLit access through an institutional library (for Stage 2 only)

#### Python interpreter on the development machine (Windows)

On the development machine the working Python is a per-user install, **not** the
`python` / `py` aliases on the `PATH` (those are the Microsoft Store
"App execution alias" stubs, which do not run and instead open the Store).
Use the full interpreter path:

```
C:\Users\JLEIGHT\AppData\Local\Python\pythoncore-3.14-64\python.exe
```

```powershell
# PowerShell: run a pipeline script with the correct interpreter
& "C:\Users\JLEIGHT\AppData\Local\Python\pythoncore-3.14-64\python.exe" scripts\08_gender_classify.py

# Optional: define a shorthand for the session
$py = "C:\Users\JLEIGHT\AppData\Local\Python\pythoncore-3.14-64\python.exe"
& $py --version    # -> Python 3.14.x
```

To confirm the interpreter location on any machine, query the registry:
`(Get-ItemProperty 'HKCU:\SOFTWARE\Python\PythonCore\3.14\InstallPath').'(default)'`.
The `python` shown in the generic `bash`/`zsh` commands below should be read as
this interpreter on the development machine.

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
| 5     | `05_make_charts.py` | Stage 4 output | `data/figures/fig{1..5}_*.{png,pdf}` (RCT share + subtype) | <1 min | `matplotlib` |
| 3c    | `03c_topic_classify.py` | Stage 4 output | `data/topic_classified.csv` | ~20 min | `claude -p` (local subscription) |
| 3d    | `03d_topic_validate.py` | Stage 3c output | `data/topic_validation_*` (blind sheet / report) | <1 min | stdlib |
| 7     | `07_topic_bar_chart.py` | Stage 3c output | `data/figures/fig{14,15,16}_topic_*.{png,pdf}` (fig16 splits on RCT) | <1 min | `matplotlib` |
| 10    | `10_topic_by_journal_tier.py` | Stage 3c output | `data/figures/fig19_topic_by_journal_tier.{png,pdf}` | <1 min | `matplotlib` |
| 6a    | `06a_country_extract.py` | Stage 4 output + `data/lmic_countries.csv` | `data/country_classified.csv` | ~25 min | `anthropic` |
| 6b    | `06b_poverty_pull.py` | (World Bank PIP + Indicators APIs) | `data/poverty_2021.csv` | 1-3 min | stdlib |
| 6c    | `06c_country_analysis.py` | Stages 6a + 6b outputs | `data/country_summary.csv`, `data/figures/fig{6,7,7b,7c,8..13}_*.{png,pdf}` (plus `fig6_top_countries_bar_jde.{png,pdf}` for the JDE-only top-countries bar; fig13 is the RCT subsample) | <1 min | `matplotlib` |
| 6a-f  | `06a_funders_metadata.py` | Stage 4 output (RCTs only) + Crossref/OpenAlex | `data/funders_6a.csv` (per-RCT metadata funders) | 2-5 min | stdlib |
| 6a-n  | `06a_normalize_funders.py` | `funders_6a.csv` + Stage 4 output | `data/funders_6a_normalized.csv`, `data/rcts_need_fulltext.csv` (no-metadata-funder worklist) | <1 min | stdlib |
| 6d    | `06d_match_fulltext_pdfs.py` | `funders_6a.csv` + `fulltext/*.pdf` | `data/fulltext_match_report.csv` | 2-4 min | `pdfplumber` |
| 6f    | `06f_funders_fulltext.py` | `fulltext_match_report.csv` + `fulltext/*.pdf` | `data/funders_6f_fulltext.csv` | ~15 min | `pdfplumber` + `claude -p` |
| 6g    | `06g_merge_funders.py` | `funders_6a.csv` + `funders_6f_fulltext.csv` | `data/funders_all.csv`, `data/figures/fig20_top_funders.{png,pdf}` | <1 min | `matplotlib` |
| 11    | `11_funders_by_journal_tier.py` | `funders_all.csv` | `data/figures/fig{21,22,23}_funders_by_journal_tier*.{png,pdf}` | <1 min | `matplotlib` |
| 8a    | `08a_author_countries.py` | Stage 4 output (OpenAlex re-pull by ID) | `data/author_countries.csv` | 1-2 min | stdlib |
| 8     | `08_gender_classify.py` | Stage 4 output (+ Stage 8a, optional) | `data/author_gender.csv`, `data/paper_gender_summary.csv` | <1 min | `gender-guesser` |
| 8b    | `08b_namsor_undetermined.py` | Stage 8 output | `data/namsor_cache.csv`, `data/author_gender_namsor.csv`, `data/paper_gender_summary_namsor.csv` | 1-2 min | stdlib + NamSor API key |
| 9     | `09_gender_charts.py` | Stage 8/8b output + `data/topic_classified.csv` | `data/figures/fig{17,18}_gender_*.{png,pdf}` | <1 min | `matplotlib` |

### Manual Not-RCT corrections (Stages 6e / 6h)

LLM RCT classification (Stage 3b) is occasionally wrong on papers whose abstracts use experimental language but are not real-world randomized interventions (lab experiments, survey/measurement experiments, methods papers). Two batches of such papers were reclassified from RCT to non-RCT on author inspection by `scripts/06e_apply_not_rct_corrections.py` (10 papers, 2026-06-04) and `scripts/06h_apply_not_rct_corrections_batch2.py` (15 papers, 2026-06-09). Each script flips `rct_classification` yes→no, clears `rct_subtype`, sets `rct_confidence = manual_override`, appends an audit note to `rct_justification`, and writes timestamped `.bak` copies before modifying anything. The corrected papers remain in the development set; only the RCT flag changes. The batch-2 script also removes the corrected rows from `funders_6a.csv` and re-syncs the `rct_classification`/`rct_subtype` columns of the derived `country_classified.csv` and `topic_classified.csv` snapshots from the corrected `final_dataset.csv`, because the RCT-filtered figures fig13 and fig16 read the flag from those snapshots. After running a correction script, re-run the deterministic RCT figure pipeline (no LLM/network calls): `06a_normalize_funders` → `06g_merge_funders` → `11_funders_by_journal_tier` → `06c_country_analysis` → `05_make_charts` → `07_topic_bar_chart`.

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

# Topic classification + topic figures (optional add-on)
python 03c_topic_classify.py       # local Claude subscription via `claude -p`; resumable
python 07_topic_bar_chart.py       # fig14-16 (fig16 splits dev papers by RCT status)
python 10_topic_by_journal_tier.py # fig19

# Stage 6: country-representation analysis (optional add-on)
python 06a_country_extract.py   # uses Anthropic API; resumable
python 06b_poverty_pull.py      # World Bank PIP + Indicators APIs; stdlib only
python 06c_country_analysis.py  # summary CSV + figures (fig6-13; fig13 = RCT subsample)

# Stage 6 (funders): per-RCT funding sources + funder figures (optional add-on)
python 06a_funders_metadata.py     # Crossref/OpenAlex funder metadata for RCTs; resumable
python 06a_normalize_funders.py    # canonicalize funders; build no-funder full-text worklist
python 06d_match_fulltext_pdfs.py  # match collected fulltext/*.pdf to the worklist
python 06f_funders_fulltext.py     # extract funders from PDFs via `claude -p`; resumable
python 06g_merge_funders.py        # merge metadata + full-text funders; fig20
python 11_funders_by_journal_tier.py  # fig21-23

# Stage 8: name-based author-gender inference (optional add-on)
python 08a_author_countries.py  # OpenAlex re-pull of author affiliation country; resumable
python 08_gender_classify.py    # gender_guesser + country prior; offline, no API
python 08b_namsor_undetermined.py  # OPTIONAL: resolve the residual via NamSor (needs key)
python 09_gender_charts.py      # gender-composition figures (overall + by topic)
```

Stage 8 uses the offline `gender_guesser` dictionary; the country prior from
Stage 8a corrects a small number of region-ambiguous names (for example,
Italian "Andrea") but does not reduce the undetermined share. Stage 8b is an
optional add-on that sends only the still-undetermined names to the NamSor API,
which resolves most romanized East-Asian and other non-European names. It
requires a NamSor key (free tier suffices for this dataset, ~1,000 names),
supplied via the `NAMSOR_API_KEY` environment variable or a gitignored file in
the project root named `namsor_key.txt` (or `.namsor_key`). Results are cached
to `data/namsor_cache.csv`, so the cache — not the key — is what a replicator
needs. The acceptance threshold (`NAMSOR_MIN_PROB`, default 0.85 calibrated
probability) is set at the top of the script.

The scripts in Stages 1, 2, 4, 6b, and 8 are **idempotent** — rerunning produces the same output. Stages 3a, 3b, and 6a are **resumable** — rerunning skips already-classified rows.

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
├── scripts/                   All pipeline scripts (Stages 0, 0b, 1a-c, 2, 3a-d, 4, 5, 6a-h, 7, 8, 8a-b, 9, 10, 11)
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
- **Pinned dependencies:** `requirements.txt` (only `anthropic`, `matplotlib`, and `gender-guesser` are non-stdlib).
- **Snapshot timestamps:** every Stage 1 row records the UTC time at which OpenAlex was queried.
- **LLM determinism:** `temperature = 0`; model identifier and prompt version recorded per-row.
- **Resumability:** Stage 3a and 3b scripts checkpoint every 50 calls and resume from the last saved row on rerun.
- **Reproducibility checksum:** Stage 4 logs SHA-256 of the final outputs.

### Recommended sharing path

This project is shared via a public GitHub repository at https://github.com/leightjessica/dev-econ-rct-review. Anyone with the link can clone it and reproduce the dataset following the instructions above.

A few optional steps for stronger archival or formal-publication contexts:

- **Zenodo DOI.** If a permanent DOI is needed (e.g., for inclusion in a published paper's bibliography), enable the GitHub-Zenodo integration; each tagged release on GitHub then receives a permanent DOI. The `CITATION.cff` file in the repository auto-renders as a "Cite this repository" button on the GitHub landing page regardless of whether a DOI is minted.
- **AEA Data and Code Repository.** Submission to an AEA journal triggers a deposit requirement at `openicpsr.org`. The `REPLICATION.md` here is structured to satisfy that template.

### Cost to a replicator

Approximately **$7 in Anthropic API charges** to reproduce Stage 3 end-to-end. All other inputs are free.

## Contact

Jessica Leight — J.Leight@cgiar.org
