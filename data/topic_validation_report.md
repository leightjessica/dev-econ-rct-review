# Topic-classification validation report

- Blind-coded sample: **49** papers (of 50 drawn)
- Skipped (no human code): 1 rows

## Headline agreement

- **Primary-topic agreement:** 32/49 = 65.3%
- **Secondary-topic exact agreement:** 17/49 = 34.7%

## Per-code precision and recall (primary topic)

Precision = of papers the LLM assigned to code C, what share did the human also assign to C?
Recall = of papers the human assigned to code C, what share did the LLM also assign to C?

| Code | Human n | LLM n | TP | FP | FN | Precision | Recall |
|---|---:|---:|---:|---:|---:|---:|---:|
| `education` | 5 | 4 | 4 | 0 | 1 | 100% | 80% |
| `gender` | 5 | 2 | 2 | 0 | 3 | 100% | 40% |
| `labor` | 5 | 5 | 4 | 1 | 1 | 80% | 80% |
| `political_economy` | 5 | 6 | 4 | 2 | 1 | 67% | 80% |
| `finance` | 4 | 2 | 1 | 1 | 3 | 50% | 25% |
| `firms` | 4 | 3 | 2 | 1 | 2 | 67% | 50% |
| `agriculture` | 3 | 3 | 2 | 1 | 1 | 67% | 67% |
| `health` | 3 | 4 | 3 | 1 | 0 | 75% | 100% |
| `other` | 3 | 2 | 1 | 1 | 2 | 50% | 33% |
| `social_protection` | 3 | 2 | 2 | 0 | 1 | 100% | 67% |
| `conflict_crime` | 2 | 2 | 1 | 1 | 1 | 50% | 50% |
| `infrastructure` | 2 | 3 | 2 | 1 | 0 | 67% | 100% |
| `trade_macro` | 2 | 3 | 1 | 2 | 1 | 33% | 50% |
| `behavioral_info` | 1 | 4 | 1 | 3 | 0 | 25% | 100% |
| `environment` | 1 | 2 | 1 | 1 | 0 | 50% | 100% |
| `migration` | 1 | 2 | 1 | 1 | 0 | 50% | 100% |

## Confusion matrix (rows = human, columns = LLM)

| human \\ llm | `agriculture` | `behavioral_info` | `conflict_crime` | `education` | `environment` | `finance` | `firms` | `gender` | `health` | `infrastructure` | `labor` | `migration` | `other` | `political_economy` | `social_protection` | `trade_macro` |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| `agriculture` | 2 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 1 | 0 | 0 |
| `behavioral_info` | 0 | 1 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| `conflict_crime` | 0 | 0 | 1 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 1 | 0 | 0 |
| `education` | 0 | 0 | 0 | 4 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 1 | 0 | 0 | 0 |
| `environment` | 0 | 0 | 0 | 0 | 1 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| `finance` | 0 | 1 | 0 | 0 | 0 | 1 | 0 | 0 | 0 | 0 | 0 | 1 | 0 | 0 | 0 | 1 |
| `firms` | 0 | 0 | 0 | 0 | 1 | 0 | 2 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 1 |
| `gender` | 0 | 1 | 1 | 0 | 0 | 1 | 0 | 2 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| `health` | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 3 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| `infrastructure` | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 2 | 0 | 0 | 0 | 0 | 0 | 0 |
| `labor` | 0 | 1 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 4 | 0 | 0 | 0 | 0 | 0 |
| `migration` | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 1 | 0 | 0 | 0 | 0 |
| `other` | 1 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 1 | 0 | 1 | 0 | 0 | 0 |
| `political_economy` | 0 | 0 | 0 | 0 | 0 | 0 | 1 | 0 | 0 | 0 | 0 | 0 | 0 | 4 | 0 | 0 |
| `social_protection` | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 1 | 0 | 0 | 0 | 0 | 2 | 0 |
| `trade_macro` | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 1 | 0 | 0 | 0 | 0 | 0 | 0 | 1 |

## Disagreements (17)

### Regulating Conglomerates: Evidence from an Energy Conservation Program in China
- DOI: `10.1257/aer.20211455` (AER 2025)
- Human: `firms` / `environment`
- LLM:    `environment` / `firms`
- LLM justification: Energy regulation targeting carbon reduction and manufacturer energy efficiency; firms respond by shifting production within conglomerates rather than improving efficiency.

### Corruption and Firm Growth: Evidence from around the World
- DOI: `10.1093/ej/uead100` (EJ 2021)
- Human: `political_economy` / `firms`
- LLM:    `firms` / `political_economy`
- LLM justification: The paper examines how corruption (informal payments/bribes) affects firm growth outcomes using firm-level data from 141 economies; firm productivity is the headline outcome and corruption is the prim

### Long-Run Effects of Aid: Forecasts and Evidence from Sierra Leone
- DOI: `10.1093/ej/uead001` (EJ 2023)
- Human: `social_protection` / `(none)`
- LLM:    `infrastructure` / `political_economy`
- LLM justification: The paper's headline finding is large persistent gains in local public goods (infrastructure), with secondary modest improvements in institutions; CDD as a governance mechanism complicates the primary

### Saving for dowry: Evidence from rural India
- DOI: `10.1016/j.jdeveco.2021.102750` (JDE 2021)
- Human: `gender` / `(none)`
- LLM:    `finance` / `gender`
- LLM justification: Paper examines household saving behavior in response to anticipated dowry obligations using variation in firstborn gender and marriage-market dowry amounts.

### Measuring social unrest using media reports
- DOI: `10.1016/j.jdeveco.2022.102924` (JDE 2022)
- Human: `conflict_crime` / `(none)`
- LLM:    `political_economy` / `trade_macro`
- LLM justification: The paper develops an index of social unrest to measure state instability and examines spillovers to neighboring countries alongside macroeconomic correlates of unrest events.

### Social media as a recruitment and data collection tool: Experimental evidence on the relative effectiveness of web surve
- DOI: `10.1016/j.jdeveco.2023.103069` (JDE 2023)
- Human: `education` / `(none)`
- LLM:    `other` / `(none)`
- LLM justification: The paper's substantive focus is on survey methodology and data collection tools (chatbot vs. web-form vs. CATI effectiveness; social media recruitment); it does not address a substantive development 

### Economic shocks and infant health: Evidence from a trade reform in Brazil
- DOI: `10.1016/j.jdeveco.2023.103193` (JDE 2023)
- Human: `trade_macro` / `health`
- LLM:    `health` / `labor`
- LLM justification: Infant mortality is the headline outcome; female employment is the mechanism through which trade shocks affect health behaviors.

### The limits of hegemony: U.S. banks and Chilean firms in the Cold War
- DOI: `10.1016/j.jdeveco.2023.103212` (JDE 2023)
- Human: `firms` / `(none)`
- LLM:    `trade_macro` / `firms`
- LLM justification: Economic sanctions disrupt international capital flows between U.S. banks and Chilean firms; firms adapt through domestic bank substitution.

### Persistent effects of colonial land tenure institutions: Village-level evidence from India
- DOI: `10.1016/j.jdeveco.2023.103247` (JDE 2023)
- Human: `agriculture` / `political_economy`
- LLM:    `political_economy` / `agriculture`
- LLM justification: Colonial land tenure institutions (property rights system assignment) are the core treatment affecting long-run development outcomes; Green Revolution technology access is a key mechanism linking inst

### Community monitoring and social accountability in development projects: Experimental evidence from Uganda
- DOI: `10.1016/j.jdeveco.2025.103537` (JDE 2025)
- Human: `other` / `(none)`
- LLM:    `agriculture` / `political_economy`
- LLM justification: The headline outcome is household livestock from a livestock distribution program; the intervention is community monitoring and social accountability training.

### How Much Does Your Boss Make? The Effects of Salary Comparisons
- DOI: `10.1086/717891` (JPE 2021)
- Human: `labor` / `firms`
- LLM:    `behavioral_info` / `labor`
- LLM justification: Natural field experiment testing how information about salary comparisons causally affects employee behavior, focusing on economic decision-making mechanisms driven by beliefs and information provisio

### Investor Memory and Biased Beliefs: Evidence from the Field
- DOI: `10.1093/qje/qjaf035` (QJE 2025)
- Human: `finance` / `(none)`
- LLM:    `behavioral_info` / `finance`
- LLM justification: Paper studies memory-based selective recall as mechanism of belief formation in investor return expectations and trading decisions, with substantive focus on cognitive bias in economic decision-making

### China’s Model of Managing the Financial System
- DOI: `10.1093/restud/rdab098` (RES 2021)
- Human: `finance` / `(none)`
- LLM:    `trade_macro` / `political_economy`
- LLM justification: Macroeconomic policy framework analyzing government intervention in financial markets and comparative financial system management approaches.

### Culture and the Historical Fertility Transition
- DOI: `10.1093/restud/rdac059` (RES 2022)
- Human: `gender` / `(none)`
- LLM:    `behavioral_info` / `gender`
- LLM justification: Cultural transmission of contraceptive information and norms (via the Bradlaugh–Besant trial) shifts beliefs and economic decision-making about fertility; gender is secondary because fertility control

### Measuring Commuting and Economic Activity inside Cities with Cell Phone Records
- DOI: `10.1162/rest_a_01085` (RESTAT 2021)
- Human: `other` / `(none)`
- LLM:    `labor` / `infrastructure`
- LLM justification: Paper uses commuting flows from cell phone records to infer spatial income distribution within cities; validity is demonstrated by showing transportation strikes (hartals) reduce commuting differentia

### Is Mobile Money Changing Rural Africa? Evidence from a Field Experiment
- DOI: `10.1162/rest_a_01333` (RESTAT 2023)
- Human: `finance` / `(none)`
- LLM:    `migration` / `finance`
- LLM justification: Headline outcome is increased out-migration and remittances due to mobile money; remittances explicitly fall under the migration code.

### Dynamic Impacts of Lockdown on Domestic Violence: Evidence from Multiple Policy Shifts in Chile
- DOI: `10.1162/rest_a_01412` (RESTAT 2024)
- Human: `gender` / `(none)`
- LLM:    `conflict_crime` / `social_protection`
- LLM justification: Domestic violence is the headline outcome; cash transfers are identified as a mitigation for lockdown-induced DV increases.
