# Data sources and attribution

This project draws on six external data sources. This document records each source's license, access mechanism, retrieval date, and citation language. The licensing terms determine what is redistributed in this repository and what is not.

## 1. OpenAlex (article metadata)

- **URL:** https://api.openalex.org/works
- **License:** CC0 1.0 Universal (public domain dedication)
- **Access:** Free public REST API; mailto-style "polite pool" header recommended
- **Retrieval:** 2026-05-06 (UTC snapshot timestamp recorded on every row of `data/raw_openalex_2021_2025.csv`)
- **Used for:** Article metadata (DOI, title, authors, abstract, journal, volume/issue/page) for the twelve in-scope journals over 2021-2025
- **Citation:** Priem, J., Piwowar, H., & Orr, R. (2022). OpenAlex: A fully-open index of scholarly works, authors, venues, institutions, and concepts. *arXiv:2205.01833*. https://arxiv.org/abs/2205.01833

## 2. Crossref (abstract backfill)

- **URL:** https://api.crossref.org/works/{DOI}
- **License:** Open via Crossref's REST API; abstract availability subject to the depositing publisher's policy
- **Access:** Free public REST API; mailto in User-Agent recommended
- **Retrieval:** 2026-05-06
- **Used for:** Backfilling abstracts for OpenAlex rows where the abstract field is empty (Stage 1b)
- **Citation:** Crossref. (2024). Crossref REST API. https://api.crossref.org/

## 3. Semantic Scholar Graph API (abstract backfill)

- **URL:** https://api.semanticscholar.org/graph/v1/paper/batch
- **License:** Use is subject to Semantic Scholar's API terms; data may be used for research with attribution
- **Access:** Free public REST API; an API key is recommended for higher rate limits but not required at our query volume
- **Retrieval:** 2026-05-06
- **Used for:** Second-pass abstract backfill where OpenAlex and Crossref both lack the abstract (Stage 1c)
- **Citation:** Kinney, R., Anastasiades, C., Authur, R., et al. (2023). The Semantic Scholar Open Data Platform. *arXiv:2301.10140*. https://arxiv.org/abs/2301.10140

## 4. AEA JEL Classification

- **URL:** https://www.aeaweb.org/econlit/jelCodes.php?view=jel
- **License:** Maintained and made publicly available by the American Economic Association
- **Access:** Public webpage; bootstrap script `00_build_jel_lookup.py` parses it once and writes `data/jel_lookup.csv`
- **Retrieval:** 2026-05-06
- **Used for:** Mapping EconLit's human-readable JEL descriptors back to JEL codes (Stage 0, used by Stage 2)
- **Citation:** American Economic Association. (n.d.). JEL Classification System. https://www.aeaweb.org/econlit/jelCodes.php

## 5. World Bank Country Classification by Income Level

- **URL:** https://api.worldbank.org/v2/country?format=json
- **License:** CC-BY-4.0 — World Bank Open Data
- **Access:** Free public REST API
- **Retrieval:** 2026-05-06
- **Used for:** Low-, lower-middle-, and upper-middle-income country list, used in the country-mention rule of the Stage 2 development filter
- **Citation:** World Bank. (2026). World Bank Country and Lending Groups. https://datahelpdesk.worldbank.org/knowledgebase/articles/906519

## 6. EconLit (via EBSCOhost) — LICENSED, NOT REDISTRIBUTED

- **URL:** Institutional access via EBSCOhost (e.g., https://search.ebscohost.com/)
- **License:** EconLit is a proprietary database licensed by the American Economic Association and distributed by EBSCO; bulk redistribution is not permitted under standard institutional licenses
- **Access:** Manual export through EBSCOhost interface (procedure: `docs/econlit_export_instructions.md`)
- **Retrieval:** 2026-05-06 (manual export by Jessica Leight, IFPRI/CGIAR institutional access)
- **Used for:** JEL codes and abstracts joined to OpenAlex rows (Stage 2)
- **Important:** The contents of `data/EconLit/` are excluded from version control via `.gitignore`. Replicators must obtain their own EconLit exports through their institutional library; the export procedure is fully documented and produces the same record set given the same query parameters.
- **Citation:** EBSCO Industries, Inc. (2026). EconLit Database. https://www.ebsco.com/products/research-databases/econlit

## 7. Anthropic Claude (LLM classification)

- **URL:** https://api.anthropic.com/v1/messages
- **License:** Anthropic API; usage subject to Anthropic's commercial terms
- **Access:** Requires an Anthropic API key; replicators must supply their own
- **Model used:** `claude-sonnet-4-5` (2026-05-07 run)
- **Used for:** Stage 3a (development classification of borderline rows) and Stage 3b (RCT classification of development articles)
- **Reproducibility:** `temperature = 0` is set on all calls. Each classified row records the model identifier, prompt version, and the LLM's structured response. Anthropic guarantees model-version stability within named model versions; minor classification differences may occur if the model is later versioned.
- **Citation:** Anthropic. (2025). Claude Sonnet 4.5. https://www.anthropic.com/

## Summary table

| Source            | License            | In repo?   | Replicator action |
|-------------------|--------------------|-----------:|-------------------|
| OpenAlex          | CC0                | derivatives | rerun Stage 1     |
| Crossref          | Open               | derivatives | rerun Stage 1b    |
| Semantic Scholar  | Open + attribution | derivatives | rerun Stage 1c    |
| AEA JEL           | Public             | lookup CSV  | rerun Stage 0 to refresh |
| World Bank        | CC-BY-4.0          | lookup CSV  | rerun Stage 0b to refresh |
| EconLit/EBSCO     | Licensed           | **NO**      | manual export per docs |
| Anthropic Claude  | API (paid)         | derivatives | own API key       |
