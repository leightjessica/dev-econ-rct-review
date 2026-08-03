#!/usr/bin/env python3
"""Stage 16 - locate and extract the ACKNOWLEDGMENTS block from each RCT's PDF.

A new exercise (2026-06-22): for every RCT with a collected full-text PDF, isolate
the acknowledgments text so a downstream pass (Stage 17, an `claude -p` LLM
extractor) can count (a) the number of individuals personally thanked and (b) the
number of seminars / conferences / workshops at whose audiences the authors thank
participants.

This stage is a LOCATOR/EXTRACTOR, not an interpreter. In economics papers the
acknowledgments nearly always sit in the page-1 title/author footnote (marked with
*, dagger, double-dagger, or a star), opening with a phrase such as "We thank ...",
"We are grateful to ...", or "For helpful comments ...". A minority of journals
(some Elsevier/JDE papers) instead place an unnumbered "Acknowledgments" section
just before the references. We capture from the opening trigger to a natural
terminator ("All errors are our own", a competing-interests declaration, the JEL
codes, the corresponding-author block, or the start of Section 1).

Why pdftotext (not pdfplumber here): `pdftotext -enc UTF-8` returns clean Unicode
(accented names such as "Bjorkegren"/"Angelica" survive intact, which matters for
name counting) and reads a whole paper in well under a second, so all 392 RCTs
process in a few minutes. Run from the MAIN session shell -- prior sessions found
that background sub-agents lack the PDF binaries.

Reuse: the costly doi -> PDF match is already complete. Every RCT carries a
`matched_file` in data/irb_mentions.csv (from Stage 12); we read that map verbatim
rather than re-matching.

Inputs:
  data/irb_mentions.csv         (doi, journal_short, publication_year, title,
                                 matched_file -- all 392 RCTs)
  fulltext/<matched_file>.pdf

Output:
  data/ack_blocks.csv           one row per RCT
  data/16_ack_extract.log

The cheap regex pre-counts (`pre_name_candidates`, `pre_seminar_kw`) are a
rule-based BASELINE only -- the headline counts come from Stage 17. They are
stored so the two can be compared and large disagreements flagged for the
validation sample.

Resumable: rerunning skips DOIs already written with status ok / no_block_found.
Re-extract everything by moving data/ack_blocks.csv aside first.

Env flags:
  SMOKE_N=10   only process the first N RCTs (quick validation run)
"""

import csv
import os
import re
import shutil
import subprocess
import sys
import unicodedata
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

csv.field_size_limit(min(sys.maxsize, 2147483647))

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
FULLTEXT = ROOT / "fulltext"
IRB_MENTIONS = DATA / "irb_mentions.csv"
OUT_CSV = DATA / "ack_blocks.csv"
LOG_TXT = DATA / "16_ack_extract.log"

SMOKE_N = int(os.environ.get("SMOKE_N", "0"))
MAX_ACK_CHARS = 2200     # hard cap on a stored block. Real acknowledgments almost
                         # never exceed this; a tighter cap bounds the damage when
                         # no terminator is found (overrun into body/references).
PDFTOTEXT_TIMEOUT = 60   # seconds per PDF

# --- locating the block ------------------------------------------------------
# Opening triggers, ordered so the EARLIEST match in the document anchors the
# block. These cover the standard econ footnote openers plus a few variants.
TRIGGER_RE = re.compile(
    r"(?:"
    r"we\s+(?:would\s+like\s+to\s+|wish\s+to\s+|sincerely\s+|especially\s+)?thank\b"
    r"|we\s+are\s+(?:especially\s+|deeply\s+|extremely\s+|very\s+)?(?:grateful|indebted|thankful)\b"
    r"|we\s+gratefully\s+acknowledge\b"
    # bare "we acknowledge" fires on discussion/limitations prose ("we acknowledge
    # that our evaluation ...") -- require an acknowledgment-type object.
    r"|we\s+(?:also\s+)?acknowledge\s+(?:financial|funding|generous|partial|support|helpful|valuable|excellent|comments|grant|funds|the\s+(?:financial|generous|support|help|comments|funding))"
    # third-person / single-author openers ("The authors are grateful ...",
    # "The author thanks ...", common in single-authored and some JDE papers)
    r"|(?:the\s+)?authors?\s+(?:would\s+like\s+to\s+|wish\s+to\s+)?thanks?\b"
    r"|(?:the\s+)?authors?\s+(?:are\s+|is\s+)?(?:especially\s+|deeply\s+|very\s+)?(?:grateful|indebted|thankful)\b"
    r"|(?:the\s+)?authors?\s+gratefully\s+acknowledge\b"
    r"|i\s+(?:would\s+like\s+to\s+|wish\s+to\s+)?thank\b"
    r"|i\s+am\s+(?:especially\s+|deeply\s+|very\s+)?(?:grateful|indebted|thankful)\b"
    r"|i\s+gratefully\s+acknowledge\b"
    r"|we\s+wish\s+to\s+acknowledge\b"
    # "appreciate"/"benefited from" likewise need an acknowledgment-type object,
    # else they fire on body text ("we benefited from the exogenous variation").
    r"|we\s+(?:also\s+)?(?:greatly\s+)?appreciate\s+(?:helpful|valuable|insightful|detailed|useful|excellent|comments|the\s+(?:helpful|comments))"
    r"|we\s+(?:have\s+)?benefit(?:ed|ted)?\s+(?:greatly\s+)?from\s+(?:helpful|valuable|comments|discussions?|conversations?|suggestions|the\s+(?:comments|suggestions|helpful|discussions?))"
    # qualified "thanks to" openers (NOT bare "thanks to", which fires on causal
    # body text such as "...less time in court thanks to the lawyer"): require a
    # qualifier before "thanks" or an adverb/auxiliary after it.
    r"|(?:(?:many|special|our|with)\s+thanks|thanks\s+(?:also|go|are\s+(?:also\s+)?(?:due|owed)))\s+to\b"
    # bare "For helpful comments ..." opener -- restricted to "comments" so it
    # does not fire on body text such as "For detailed discussion of <X>".
    r"|for\s+(?:their\s+|his\s+|her\s+|very\s+)?(?:helpful|valuable|detailed|useful|insightful|excellent)\s+comments\b"
    r"|this\s+(?:paper|project|research|work|study|article)\s+(?:has\s+)?(?:benefit|benefited|benefitted)\b"
    r")",
    re.I,
)

# Name-led acknowledgments ("<names> provided helpful comments", "<name> provided
# excellent research assistance") that carry no "we thank" opener. When this is
# the earliest anchor, the block is backed up to the start of its sentence so the
# leading names are captured.
PROVIDED_RE = re.compile(
    r"provided\s+(?:(?:[a-z]+\s+){0,2})?"
    r"(?:helpful|valuable|useful|insightful|detailed|excellent|outstanding|"
    r"superb|invaluable|expert|able|capable|dedicated|tremendous|sterling|"
    r"diligent|exceptional|terrific|stellar|tireless)\s+"
    r"(?:comments|suggestions|feedback|research\s+assistance|assistance)\b",
    re.I,
)

# A standalone "Acknowledgments" header (Elsevier end-of-paper section).
ACK_HEADER_RE = re.compile(r"\bAcknowledge?ments?\b", re.I)

# Terminators searched AFTER the trigger; the earliest one ends the block. The
# end-section headers (References / Data availability / etc.) are critical for the
# many Elsevier/JDE papers that place the acknowledgments just before the
# references -- without them the block sweeps a bibliography full of cited author
# names, which would be miscounted as thanked individuals.
TERMINATOR_RE = re.compile(
    r"(?:"
    r"all\s+(?:remaining\s+)?errors?\s+(?:and\s+omissions\s+)?are\s+(?:our|my|the\s+authors[’'])\s+own"
    r"|any\s+(?:and\s+all\s+)?(?:remaining\s+)?errors?\s+are\s+(?:our|my)\s+own"
    r"|the\s+(?:usual\s+)?(?:disclaimers?\s+appl|caveats?\s+appl)"
    r"|declarations?\s+of\s+(?:competing|interest)"
    r"|conflicts?\s+of\s+interest"
    r"|competing\s+interests?"
    r"|disclosure\s+statement"
    r"|data\s+availability"
    r"|supplementary\s+(?:data|material|information)"
    r"|\bReferences\b"
    r"|\bBibliography\b"
    r"|\bJEL\s+(?:codes?|classification|nos?\b|:)"
    r"|\bKeywords?\s*:"
    r"|corresponding\s+author"
    r"|†\s*corresponding"          # dagger + corresponding
    r"|\n\s*1\.?\s+Introduction\b"
    r")",
    re.I,
)

# --- pre-counts (rule-based baseline) ---------------------------------------
SEMINAR_KW_RE = re.compile(
    r"\b(?:seminars?|conferences?|workshops?|symposi|colloqui"
    r"|participants?\s+(?:at|in|of)|audiences?\s+at|presentations?\s+at"
    r"|annual\s+meetings?|summer\s+institute|meetings?\s+of)\b",
    re.I,
)
# A rough "First Last" capitalized-bigram name candidate (BASELINE ONLY -- it
# overcounts city/institution names and undercounts non-Western names; Stage 17
# does the real counting).
NAME_CAND_RE = re.compile(
    r"\b[A-Z][a-z]+(?:\s+[A-Z]\.?)?\s+[A-Z][a-z]+(?:-[A-Z][a-z]+)?\b"
)

DOI_TO_FILE_STATUS = ("ok", "no_mention")  # statuses whose matched_file is real

FIELDS = ["doi", "journal_short", "publication_year", "title", "matched_file",
          "n_pdf_chars", "ack_found", "trigger", "ack_source", "ack_text",
          "pre_name_candidates", "pre_seminar_kw", "extraction_status",
          "snapshot_utc"]


def log(msg):
    line = f"[{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%SZ')}] {msg}"
    print(line, flush=True)
    with open(LOG_TXT, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def find_pdftotext():
    for cand in ("pdftotext", "pdftotext.exe"):
        p = shutil.which(cand)
        if p:
            return p
    for hard in (r"C:\Program Files\Git\mingw64\bin\pdftotext.exe",
                 "/mingw64/bin/pdftotext"):
        if os.path.exists(hard):
            return hard
    sys.exit("Could not find `pdftotext`. Install poppler / Git-for-Windows.")


def pdf_to_text(pdftotext_bin, pdf_path):
    """Whole-document text in reading order, clean UTF-8. None on error."""
    try:
        proc = subprocess.run(
            [pdftotext_bin, "-enc", "UTF-8", str(pdf_path), "-"],
            capture_output=True, timeout=PDFTOTEXT_TIMEOUT,
        )
        if proc.returncode != 0:
            log(f"    pdftotext exit {proc.returncode} on {pdf_path.name}: "
                f"{proc.stderr[:160].decode('utf-8', 'replace')}")
            return None
        return proc.stdout.decode("utf-8", "replace")
    except Exception as e:  # noqa: BLE001
        log(f"    pdftotext error on {pdf_path.name}: {type(e).__name__}: {e}")
        return None


def clean(s):
    """Normalize compatibility ligatures (NFKC folds 'Woodruﬀ' -> 'Woodruff',
    'scientiﬁc' -> 'scientific') and collapse whitespace (joins footnote
    lines split across the PDF)."""
    s = unicodedata.normalize("NFKC", s or "")
    return re.sub(r"\s+", " ", s).strip()


SENT_BOUNDARY_RE = re.compile(r"[.!?]\s")


def _sentence_start(full_text, pos, lookback=600):
    """Position of the start of the sentence containing `pos` (for name-led
    'X provided helpful comments' anchors, so the leading names are kept)."""
    win0 = max(0, pos - lookback)
    pre = full_text[win0:pos]
    last = None
    for mm in SENT_BOUNDARY_RE.finditer(pre):
        last = mm
    return win0 + last.end() if last else win0


def _block_from(full_text, start, end_from, label, source):
    tail = full_text[start:start + MAX_ACK_CHARS + 1500]
    term = TERMINATOR_RE.search(tail, end_from)
    end = (start + term.end()) if term else (start + MAX_ACK_CHARS)
    block = clean(full_text[start:end])[:MAX_ACK_CHARS]
    return block, label, source


def locate_block(full_text):
    """Return (ack_text, trigger_phrase, source) or (None, '', '')."""
    # Primary: earliest opening trigger anywhere in the document (the title
    # footnote renders near the front, so the first hit is the acknowledgments).
    m = TRIGGER_RE.search(full_text)
    if m:
        return _block_from(full_text, m.start(), m.end() - m.start(),
                           clean(m.group(0)), "trigger")
    # Fallback A: a purely name-led ack with no standard opener ("<names> provided
    # helpful comments"). Used ONLY when no standard trigger exists anywhere, so a
    # body-text "provided ... assistance" cannot pre-empt a real "We thank ..."
    # ack elsewhere in the same paper. Back up to the sentence start for the names.
    mp = PROVIDED_RE.search(full_text)
    if mp:
        start = _sentence_start(full_text, mp.start())
        return _block_from(full_text, start, mp.end() - start,
                           "provided (name-led)", "provided")
    # Fallback: a standalone "Acknowledgments" header (Elsevier end section).
    for hm in ACK_HEADER_RE.finditer(full_text):
        after = full_text[hm.end():hm.end() + 1800]
        # require some thanking language nearby to avoid a bare TOC entry
        if re.search(r"thank|grateful|acknowledge|comments|assistance|support",
                     after, re.I):
            term = TERMINATOR_RE.search(after)
            end = term.end() if term else min(len(after), 1500)
            block = clean(after[:end])[:MAX_ACK_CHARS]
            if len(block) > 40:
                return block, "Acknowledgments (header)", "header"
    return None, "", ""


def load_rcts():
    rows = []
    with open(IRB_MENTIONS, encoding="utf-8") as f:
        for r in csv.DictReader(f):
            rows.append({
                "doi": r["doi"],
                "journal_short": r["journal_short"],
                "publication_year": r["publication_year"],
                "title": r["title"],
                "matched_file": r.get("matched_file", ""),
                "irb_status": r.get("extraction_status", ""),
            })
    return rows


def main():
    open(LOG_TXT, "w").close()
    log(f"Stage 16 (acknowledgments extract) start. SMOKE_N={SMOKE_N}")
    pdftotext_bin = find_pdftotext()
    log(f"pdftotext: {pdftotext_bin}")

    rcts = load_rcts()
    log(f"RCTs from irb_mentions.csv: {len(rcts)}  "
        f"(with matched_file: {sum(1 for r in rcts if r['matched_file'])})")

    done = {}
    if OUT_CSV.exists():
        with open(OUT_CSV, encoding="utf-8") as f:
            for r in csv.DictReader(f):
                if r.get("extraction_status") in ("ok", "no_block_found"):
                    done[r["doi"]] = r
        log(f"Resuming: {len(done)} RCTs already extracted")

    todo = [r for r in rcts if r["doi"] not in done]
    if SMOKE_N:
        todo = todo[:SMOKE_N]
    log(f"To extract this run: {len(todo)}")

    out_rows = list(done.values())
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    n_ok = n_noblock = n_nopdf = n_err = 0

    def save():
        with open(OUT_CSV, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=FIELDS)
            w.writeheader()
            w.writerows(out_rows)

    for k, r in enumerate(todo, 1):
        base = {
            "doi": r["doi"], "journal_short": r["journal_short"],
            "publication_year": r["publication_year"], "title": r["title"],
            "matched_file": r["matched_file"], "snapshot_utc": stamp,
        }
        fname = r["matched_file"]
        pdf_path = FULLTEXT / fname if fname else None
        if not fname or not pdf_path.exists():
            base.update(n_pdf_chars="", ack_found="", trigger="", ack_source="",
                        ack_text="", pre_name_candidates="", pre_seminar_kw="",
                        extraction_status="no_pdf")
            out_rows.append(base); n_nopdf += 1; continue

        text = pdf_to_text(pdftotext_bin, pdf_path)
        if text is None:
            base.update(n_pdf_chars="", ack_found="", trigger="", ack_source="",
                        ack_text="", pre_name_candidates="", pre_seminar_kw="",
                        extraction_status="pdf_error")
            out_rows.append(base); n_err += 1; continue

        block, trigger, source = locate_block(text)
        if block:
            pre_names = len(set(NAME_CAND_RE.findall(block)))
            pre_sem = len(SEMINAR_KW_RE.findall(block))
            base.update(
                n_pdf_chars=len(text), ack_found="True", trigger=trigger,
                ack_source=source, ack_text=block,
                pre_name_candidates=pre_names, pre_seminar_kw=pre_sem,
                extraction_status="ok",
            )
            out_rows.append(base); n_ok += 1
        else:
            base.update(n_pdf_chars=len(text), ack_found="False", trigger="",
                        ack_source="", ack_text="", pre_name_candidates=0,
                        pre_seminar_kw=0, extraction_status="no_block_found")
            out_rows.append(base); n_noblock += 1

        if k % 25 == 0:
            save()
            log(f"  processed {k}/{len(todo)}  ok={n_ok} no_block={n_noblock} "
                f"no_pdf={n_nopdf} err={n_err}")

    save()

    final = {r["doi"]: r for r in out_rows}
    by_status = Counter(r["extraction_status"] for r in final.values())
    log("\n=== acknowledgments extraction summary ===")
    log(f"RCTs total: {len(final)}")
    for st in ("ok", "no_block_found", "no_pdf", "pdf_error"):
        if by_status.get(st):
            log(f"  {st:15s} {by_status[st]}")
    oks = [r for r in final.values() if r["extraction_status"] == "ok"]
    if oks:
        import statistics
        lens = [len(r["ack_text"]) for r in oks]
        log(f"Blocks found: {len(oks)}  "
            f"(median {int(statistics.median(lens))} chars, "
            f"max {max(lens)})")
        bysrc = Counter(r["ack_source"] for r in oks)
        log(f"  by source: {dict(bysrc)}")
    log(f"\nOutput: {OUT_CSV.name}  -> next: scripts/17_ack_classify.py")


if __name__ == "__main__":
    main()
