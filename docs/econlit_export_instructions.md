# EconLit export from EBSCOhost — instructions

Stage 2 of this project requires JEL codes for every article in the OpenAlex pull. EconLit (the AEA's bibliographic database) is the authoritative source for JEL codes, and most institutional libraries provide access to EconLit through EBSCOhost. This document describes how to perform the export manually; programmatic API access to EconLit via EBSCOhost is not available to individual researchers.

The export must be repeated each time the pipeline is re-run on a refreshed time window. EBSCO output is licensed and is not redistributed in this project.

## Procedure

### Step 1 — Open EconLit on EBSCOhost

Navigate to your institutional library's EBSCOhost portal and select the **EconLit** database. CGIAR users can typically access this through the IFPRI library proxy.

### Step 2 — Build the search

Use the **Advanced Search** interface and construct a query for one journal at a time. **Search by ISSN, not by title.** Title-based searches (`SO "Journal Title"`) use fuzzy substring matching, which causes two recurrent problems: (i) journals with overlapping titles pull each other in (e.g., a search for `SO "Economic Journal"` returns articles from *American Economic Journal: Economic Policy* alongside *Economic Journal*), and (ii) journals indexed under slight title variants (e.g., *The Review of Economics and Statistics* with the leading article) are missed entirely. ISSN-based search is exact and bypasses both pitfalls.

The recommended query template, applied per journal:

```
(IS 0034-6535 OR IS 1530-9142) AND DT 20210101-20251231
```

Where `IS` is the ISSN field, the OR pattern catches both print and electronic ISSNs (EconLit populates them inconsistently), and `DT` is publication date.

ISSN reference for the twelve journals:

| Code        | Journal                                             | Print ISSN | Electronic ISSN   |
|-------------|-----------------------------------------------------|-----------:|------------------:|
| AER         | American Economic Review                            | 0002-8282  | (none active)     |
| AERI        | American Economic Review: Insights                  | 2640-205X  | 2640-2068         |
| AEJ:Applied | American Economic Journal: Applied Economics        | 1945-7782  | 1945-7790         |
| AEJ:EP      | American Economic Journal: Economic Policy          | 1945-7731  | 1945-774X         |
| ECMA        | Econometrica                                        | 0012-9682  | 1468-0262         |
| QJE         | Quarterly Journal of Economics                      | 0033-5533  | 1531-4650         |
| JPE         | Journal of Political Economy                        | 0022-3808  | 1537-534X         |
| RES         | Review of Economic Studies                          | 0034-6527  | 1467-937X         |
| RESTAT      | Review of Economics and Statistics                  | 0034-6535  | 1530-9142         |
| EJ          | Economic Journal                                    | 0013-0133  | 1468-0297         |
| JEEA        | Journal of the European Economic Association        | 1542-4766  | 1542-4774         |
| JDE         | Journal of Development Economics                    | 0304-3878  | (Elsevier; none)  |

### Step 3 — Export

For each search, use **Share → Export** and select either:

- **CSV** with all fields including the `Descriptors` and `Subject Terms` columns (these contain JEL codes); or
- **RIS** if the CSV option is unavailable, then convert to CSV in a downstream step.

Export in batches of 100 or 500 (EBSCO's default cap is typically 25,000 per session, but per-export caps are smaller). Concatenate the per-journal exports into a single file.

### Step 4 — Save to project

Save the combined export as:

```
data/econlit_jel_codes_raw.csv
```

A small cleanup script (`scripts/02a_clean_econlit.py`, to be written) will then normalize the JEL-code column into a long-format lookup keyed by DOI, written to `data/econlit_jel_codes.csv`.

## Field requirements

The EconLit export must include at minimum the following columns. Field labels vary slightly across EBSCO interface versions; common labels are listed in parentheses.

- DOI (`DOI`, sometimes embedded within `URL` or `Notes`)
- Title (`Title` / `TI`)
- Authors (`Authors` / `AU`)
- Journal (`Source` / `SO`)
- Year (`Publication Year` / `PY` / `DT`)
- JEL codes — appears in **Descriptors** or **Subject Terms** as comma-separated values; preserved by exporting "all available fields"

## Coverage caveats

- **EconLit lag.** EconLit indexes articles after journal publication, with typical lag of two to six months. The most recent issues (late 2025) may not yet be fully indexed at the time of export. Articles missing from EconLit will be flagged `BORDERLINE` in Stage 2 and resolved via LLM classification of the abstract.
- **DOI matching.** EconLit's DOI field is occasionally missing or formatted inconsistently. The cleaning script normalizes DOIs to lowercase and strips the `https://doi.org/` prefix before merging.
- **Discussion notes and corrections.** EconLit indexes some non-research items (book reviews, editorial notes, errata). These are filtered out at Stage 2 using the `type` field from OpenAlex.
