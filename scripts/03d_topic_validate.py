"""
Stage 3d: blind-coding validation of Stage 3c topic classifications.

Two modes:

(1) BUILD mode (default): read data/topic_classified.csv, draw a stratified
    random sample of N papers (default 50) by LLM primary_topic, and write
    a blind coding sheet to data/topic_validation_blind.csv. The blind sheet
    contains DOI, journal, year, title, abstract, and blank columns for
    primary_topic_human, secondary_topic_human, and notes. The LLM's
    classifications are NOT present in the blind sheet, to avoid anchoring.
    A companion data/topic_validation_codebook.md is written as a one-page
    cheat sheet of the 16 codes.

(2) REPORT mode (--report): after the user has filled in
    topic_validation_blind.csv, this mode merges the human codes against
    the LLM classifications and writes data/topic_validation_report.md
    with: overall primary-topic agreement, per-code precision and recall,
    a confusion matrix, and a side-by-side list of disagreements.

Usage
-----
    python scripts/03d_topic_validate.py                  # build (n=50)
    python scripts/03d_topic_validate.py --n 100          # build, larger n
    python scripts/03d_topic_validate.py --report         # compute report
"""

import argparse
import csv
import math
import os
import random
import sys
from collections import Counter, defaultdict

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_DIR = os.path.abspath(os.path.join(SCRIPT_DIR, ".."))
IN_CSV = os.path.join(PROJECT_DIR, "data", "topic_classified.csv")
BLIND_CSV = os.path.join(PROJECT_DIR, "data", "topic_validation_blind.csv")
CODEBOOK_MD = os.path.join(PROJECT_DIR, "data", "topic_validation_codebook.md")
REPORT_MD = os.path.join(PROJECT_DIR, "data", "topic_validation_report.md")

TOPIC_CODES = [
    "agriculture", "health", "education", "labor", "firms", "finance",
    "social_protection", "gender", "political_economy", "conflict_crime",
    "environment", "trade_macro", "migration", "infrastructure",
    "behavioral_info", "other",
]

BLIND_FIELDS = [
    "doi", "journal_short", "publication_year", "title", "abstract",
    "primary_topic_human", "secondary_topic_human", "notes",
]

CODEBOOK = """# Topic-classification codebook (for blind validation)

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
"""


def stratified_sample(rows, n, seed=42):
    """Stratified sample by primary_topic. Each code that has ≥2 LLM-classified
    rows gets at least 2; the remainder is filled proportional to size.
    Returns the sampled rows."""
    by_code = defaultdict(list)
    for r in rows:
        code = (r.get("primary_topic") or "").strip()
        if code and code != "INVALID":
            by_code[code].append(r)

    rng = random.Random(seed)
    sample = []
    # First pass: 2 per code with ≥2 (or all if fewer)
    for code, papers in by_code.items():
        k = min(len(papers), 2)
        sample.extend(rng.sample(papers, k))
    # Second pass: fill the remaining budget proportional to size
    used = {r["doi"] for r in sample}
    remaining_budget = n - len(sample)
    if remaining_budget > 0:
        # Build a pool weighted by code size (excluding already-sampled rows)
        pool = []
        for code, papers in by_code.items():
            unsampled = [r for r in papers if r["doi"] not in used]
            pool.extend(unsampled)
        rng.shuffle(pool)
        sample.extend(pool[:remaining_budget])
    return sample[:n]


def build(args):
    if not os.path.exists(IN_CSV):
        sys.exit(f"Input not found: {IN_CSV}\nRun Stage 3c first.")
    with open(IN_CSV, encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    n_classified = sum(1 for r in rows if (r.get("primary_topic") or "").strip()
                       and r.get("primary_topic") != "INVALID")
    if n_classified == 0:
        sys.exit("No classified rows found in topic_classified.csv. Run Stage 3c first.")
    print(f"Input: {IN_CSV} ({len(rows)} rows; {n_classified} with primary_topic)")

    sample = stratified_sample(
        [r for r in rows if (r.get("primary_topic") or "").strip()
         and r.get("primary_topic") != "INVALID"],
        n=args.n, seed=args.seed,
    )
    # Preserve the journal-then-year ordering so the user reads through the
    # sheet in a sensible sequence, but the underlying sampling is random.
    sample.sort(key=lambda r: (r.get("journal_short", ""),
                               r.get("publication_year", ""),
                               r.get("doi", "")))

    with open(BLIND_CSV, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=BLIND_FIELDS)
        writer.writeheader()
        for r in sample:
            writer.writerow({
                "doi": r.get("doi", ""),
                "journal_short": r.get("journal_short", ""),
                "publication_year": r.get("publication_year", ""),
                "title": r.get("title", ""),
                "abstract": r.get("abstract", ""),
                "primary_topic_human": "",
                "secondary_topic_human": "",
                "notes": "",
            })

    with open(CODEBOOK_MD, "w", encoding="utf-8") as f:
        f.write(CODEBOOK)

    code_counts = Counter((r.get("primary_topic") or "").strip() for r in sample)
    print(f"Wrote blind sheet: {BLIND_CSV} ({len(sample)} rows)")
    print(f"Wrote codebook:    {CODEBOOK_MD}")
    print()
    print("Sample composition by LLM primary_topic:")
    for code, n in sorted(code_counts.items(), key=lambda x: -x[1]):
        print(f"  {code:20s} {n}")
    print()
    print(f"Next: open {os.path.basename(BLIND_CSV)} in Excel/Stata, fill in")
    print("primary_topic_human (required) and secondary_topic_human (optional).")
    print(f"When done, rerun with --report to compute agreement.")


def report(args):
    if not os.path.exists(BLIND_CSV):
        sys.exit(f"Blind sheet not found: {BLIND_CSV}\nRun without --report first.")
    if not os.path.exists(IN_CSV):
        sys.exit(f"LLM output not found: {IN_CSV}")

    with open(BLIND_CSV, encoding="utf-8") as f:
        blind = list(csv.DictReader(f))
    with open(IN_CSV, encoding="utf-8") as f:
        llm = {r["doi"]: r for r in csv.DictReader(f) if r.get("doi")}

    # Filter to coded rows
    coded = [r for r in blind if (r.get("primary_topic_human") or "").strip()]
    skipped = [r for r in blind if not (r.get("primary_topic_human") or "").strip()]
    if not coded:
        sys.exit("No human-coded rows found. Fill in primary_topic_human in the blind sheet first.")

    # Compute agreement
    n = len(coded)
    n_primary_agree = 0
    n_secondary_agree = 0
    confusion = defaultdict(lambda: defaultdict(int))  # confusion[human][llm]
    disagreements = []

    for r in coded:
        doi = r["doi"]
        h_prim = (r.get("primary_topic_human") or "").strip().lower()
        h_sec = (r.get("secondary_topic_human") or "").strip().lower()
        if doi not in llm:
            continue
        l_prim = (llm[doi].get("primary_topic") or "").strip().lower()
        l_sec = (llm[doi].get("secondary_topic") or "").strip().lower()
        confusion[h_prim][l_prim] += 1
        if h_prim == l_prim:
            n_primary_agree += 1
        else:
            disagreements.append({
                "doi": doi,
                "journal": llm[doi].get("journal_short", ""),
                "year": llm[doi].get("publication_year", ""),
                "title": llm[doi].get("title", "")[:120],
                "human_primary": h_prim,
                "llm_primary": l_prim,
                "human_secondary": h_sec,
                "llm_secondary": l_sec,
                "human_notes": (r.get("notes") or "").strip(),
                "llm_justification": llm[doi].get("topic_justification", "")[:200],
            })
        if h_sec == l_sec:
            n_secondary_agree += 1

    # Per-code precision/recall on primary
    per_code = {}
    all_codes = sorted(set([c for c in confusion.keys()] +
                           [c for row in confusion.values() for c in row.keys()]))
    for c in all_codes:
        tp = confusion.get(c, {}).get(c, 0)
        # tp: human=c AND llm=c
        # human_c_total = sum over llm of confusion[c][llm]  (rows where human=c)
        # llm_c_total = sum over human of confusion[human][c]
        human_c_total = sum(confusion[c].values())
        llm_c_total = sum(confusion[h].get(c, 0) for h in confusion)
        fp = llm_c_total - tp  # LLM said c but human said otherwise
        fn = human_c_total - tp  # Human said c but LLM said otherwise
        precision = tp / llm_c_total if llm_c_total else None
        recall = tp / human_c_total if human_c_total else None
        per_code[c] = {
            "human_n": human_c_total, "llm_n": llm_c_total,
            "tp": tp, "fp": fp, "fn": fn,
            "precision": precision, "recall": recall,
        }

    # Write report
    lines = []
    lines.append("# Topic-classification validation report")
    lines.append("")
    lines.append(f"- Blind-coded sample: **{n}** papers (of {len(blind)} drawn)")
    if skipped:
        lines.append(f"- Skipped (no human code): {len(skipped)} rows")
    lines.append("")
    lines.append("## Headline agreement")
    lines.append("")
    lines.append(f"- **Primary-topic agreement:** {n_primary_agree}/{n} = {100*n_primary_agree/n:.1f}%")
    lines.append(f"- **Secondary-topic exact agreement:** {n_secondary_agree}/{n} = {100*n_secondary_agree/n:.1f}%")
    lines.append("")
    lines.append("## Per-code precision and recall (primary topic)")
    lines.append("")
    lines.append("Precision = of papers the LLM assigned to code C, what share did the human also assign to C?")
    lines.append("Recall = of papers the human assigned to code C, what share did the LLM also assign to C?")
    lines.append("")
    lines.append("| Code | Human n | LLM n | TP | FP | FN | Precision | Recall |")
    lines.append("|---|---:|---:|---:|---:|---:|---:|---:|")
    for c in sorted(per_code.keys(), key=lambda k: -per_code[k]["human_n"]):
        d = per_code[c]
        p = f"{100*d['precision']:.0f}%" if d['precision'] is not None else "—"
        rc = f"{100*d['recall']:.0f}%" if d['recall'] is not None else "—"
        lines.append(f"| `{c}` | {d['human_n']} | {d['llm_n']} | {d['tp']} | {d['fp']} | {d['fn']} | {p} | {rc} |")
    lines.append("")
    lines.append("## Confusion matrix (rows = human, columns = LLM)")
    lines.append("")
    header = "| human \\\\ llm |" + "|".join(f" `{c}` " for c in all_codes) + "|"
    sep = "|---|" + "|".join("---:" for _ in all_codes) + "|"
    lines.append(header)
    lines.append(sep)
    for h in all_codes:
        row = [f" {confusion[h].get(l, 0)} " for l in all_codes]
        lines.append(f"| `{h}` |" + "|".join(row) + "|")
    lines.append("")
    lines.append(f"## Disagreements ({len(disagreements)})")
    lines.append("")
    for d in disagreements:
        lines.append(f"### {d['title']}")
        lines.append(f"- DOI: `{d['doi']}` ({d['journal']} {d['year']})")
        lines.append(f"- Human: `{d['human_primary']}` / `{d['human_secondary'] or '(none)'}`")
        lines.append(f"- LLM:    `{d['llm_primary']}` / `{d['llm_secondary'] or '(none)'}`")
        if d['human_notes']:
            lines.append(f"- Human notes: {d['human_notes']}")
        lines.append(f"- LLM justification: {d['llm_justification']}")
        lines.append("")

    with open(REPORT_MD, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))

    print(f"Wrote report: {REPORT_MD}")
    print()
    print(f"Headline primary agreement: {n_primary_agree}/{n} = {100*n_primary_agree/n:.1f}%")
    print(f"Headline secondary agreement: {n_secondary_agree}/{n} = {100*n_secondary_agree/n:.1f}%")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--report", action="store_true",
                    help="Compute report after the blind sheet has been filled in")
    ap.add_argument("--n", type=int, default=50, help="Sample size for blind sheet (default 50)")
    ap.add_argument("--seed", type=int, default=42, help="RNG seed for stratified sampling")
    args = ap.parse_args()
    if args.report:
        report(args)
    else:
        build(args)


if __name__ == "__main__":
    main()
