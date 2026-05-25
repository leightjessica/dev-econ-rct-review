# Topic-classification codebook (for blind validation)

Code each paper independently of any LLM output. Read the title + abstract, then assign:
- `primary_topic_human` — the single best fit from the 16 codes below (required).
- `secondary_topic_human` — an optional second code, ONLY if a clear second topic is present. Otherwise leave empty.
- `notes` — free text (optional). Use this to record any disagreement with the taxonomy or borderline calls.

## The 16 codes

- **agriculture** — farming, livestock, agricultural extension, input subsidies, food security, crop markets
- **health** — clinical/preventive health, nutrition, mental health, WASH measured as a health outcome
- **education** — schooling, learning outcomes, teacher policies, ed-tech, formal skills training
- **labor** — job search, employment, vocational training (workers), labor regulation, child labor
- **firms** — SMEs, entrepreneurship, business training, capital grants, firm productivity, management
- **finance** — MICRO-level financial topics: microfinance, savings, credit, insurance, mobile money, household financial inclusion
- **social_protection** — cash transfers (CCT/UCT), in-kind transfers, public works, safety nets, pensions
- **gender** — women's empowerment, intra-household allocation when gender is central, GBV (only when gender is the central frame, not just a heterogeneity cut)
- **political_economy** — governance, corruption, accountability, elections, state capacity, bureaucracy
- **conflict_crime** — armed conflict, policing, crime, violence prevention, terrorism
- **environment** — climate, pollution, energy use, deforestation, natural resources
- **trade_macro** — international trade, exchange rates, EM corporate FX debt, carry trades, capital flows, macro policy, growth, industrial policy
- **migration** — internal/international migration, remittances, refugees
- **infrastructure** — roads, electricity, water/sanitation as infrastructure, digital connectivity
- **behavioral_info** — information provision, social norms, beliefs, behavioral nudges, intra-household bargaining experiments, lab-style tests of household decision-making
- **other** — residual when none of the 15 substantive codes fits

## Disambiguation reminders

- Cash transfers → `social_protection` (NOT `finance`). Microcredit/savings/insurance → `finance`.
- CCT for school attendance: primary=`education`, secondary=`social_protection`.
- Scholarships/financial aid bundled into a regular admissions process → `education` only.
- Business training/capital to firms → `firms`. Worker training → `labor`.
- WASH measured as a health outcome → `health`. WASH measured as access → `infrastructure`.
- Carry trades, FX debt, exchange rates → `trade_macro` (not `finance`).
- Tie-breaker: when two codes both fit, pick the one closer to the paper's headline outcome variable.
