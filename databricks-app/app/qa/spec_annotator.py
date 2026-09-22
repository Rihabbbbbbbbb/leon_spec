"""
Spec annotator — generates a copy of the ORIGINAL uploaded specification
with every passage that needs to change highlighted in violet
(see HIGHLIGHT_COLOR_NAME below for why that colour).

Complements the validation report: the report tells the engineer WHAT is
wrong and WHY; this gives them a marked-up version of their own document
showing exactly WHERE, so they can jump straight to each spot and fix it
in place.

Currently supports .docx only (python-docx can rewrite runs in place via
run.font.highlight_color). PDF/TXT originals have no
reliable in-place highlighting path with the libraries in this project
(PyPDF2 is read-only; adding a PDF-writing dependency was out of scope
for this feature) — generate_annotated_spec() returns None for those,
and callers should surface that as "not available" rather than failing.

Matching strategy: every finding's user_excerpt (and, for findings that
carry an itemized breakdown — e.g. every untraced requirement — each
item's excerpt) becomes a "target" string. The ORIGINAL document is
walked using the SAME body-paragraph + flattened-table-row grouping as
extract_text_from_file() (retrieval.py), so a target derived from a
table row like "REF-A-002 | The system shall stop within 1 second."
matches back against the reconstructed "cell | cell | cell" text of that
row — then every run in every cell of that row is highlighted, since the
excerpt spans multiple cells and python-docx runs cannot be highlighted
"by substring" across cell boundaries.
"""
from __future__ import annotations

import io
import re
from typing import Dict, List, Optional, Tuple

_MIN_TARGET_LEN = 15
_MAX_TARGET_LEN = 200

# Highlight colour used to mark every passage LEON wants changed.
#
# Deliberately NOT yellow: real CTS specs already use yellow themselves
# (R04 requires generic RD elements to be highlighted in yellow), and the
# ASU spec additionally contains pre-existing pink, turquoise, bright-green
# and red highlights. VIOLET is the only vivid colour in the Word palette
# that the source document never uses, so in the annotated copy every
# violet mark is unambiguously a LEON finding and nothing else.
HIGHLIGHT_COLOR_NAME = "VIOLET"
HIGHLIGHT_COLOR_LABEL = "violet"


def _normalize_ws(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip().lower()


def _clean_target(raw: str) -> Optional[str]:
    """
    Turn a stored excerpt into a safe substring-match target.

    Excerpts from _find_excerpt() are cut at a fixed character offset and
    prefixed/suffixed with "..." when truncated — meaning the very first
    or last word can be a partial fragment ("...uirements are refined").
    Matching on a partial word would either fail to match or, worse,
    match somewhere unintended. So: strip the ellipses, then drop the
    first/last whitespace-separated token as a precaution whenever there
    are enough tokens left to still be a meaningful, specific match.
    """
    text = (raw or "").strip()
    had_leading_ellipsis = text.startswith("...")
    had_trailing_ellipsis = text.endswith("...")
    text = text.strip(".").strip()
    if not text:
        return None

    tokens = text.split()
    if len(tokens) > 4:
        if had_leading_ellipsis:
            tokens = tokens[1:]
        if had_trailing_ellipsis and tokens:
            tokens = tokens[:-1]
    text = " ".join(tokens)[:_MAX_TARGET_LEN]

    if len(text) < _MIN_TARGET_LEN:
        return None
    return text


def collect_highlight_targets(report: Dict) -> List[str]:
    """
    Gather every distinct "this needs to change" text snippet from a
    validation report: each error/warning's user_excerpt, plus every
    item excerpt from findings that carry an itemized breakdown (e.g.
    G_TRACEABILITY lists every untraced requirement individually).
    """
    targets: List[str] = []
    seen = set()

    def _add(raw: Optional[str]):
        cleaned = _clean_target(raw or "")
        if not cleaned:
            return
        key = _normalize_ws(cleaned)
        if key in seen:
            return
        seen.add(key)
        targets.append(cleaned)

    for f in report.get("findings", []):
        # A finding's own excerpt is only worth highlighting when it
        # reports a problem (error/warning). Its itemized breakdown is
        # different: each item IS an individual actionable defect (e.g.
        # one specific untraced requirement) regardless of the parent
        # finding's overall severity — a mostly-compliant document can
        # "pass" overall while still listing a handful of gaps that each
        # deserve their own highlight.
        # G_TRACEABILITY's own top-level excerpt is illustrative context
        # (e.g. "here is one requirement that IS correctly traced"), built
        # by joining two DIFFERENT cells with an arrow ("ID → reference")
        # that doesn't exist anywhere in the real document — it was never
        # meant to be a literal substring, and every ACTUAL untraced
        # requirement is already itemized below. Highlighting it would
        # either match nothing (the common case) or, worse, mark a
        # correctly-traced requirement as if it were a problem.
        if f.get("severity") in ("error", "warning") and f.get("check") != "G_TRACEABILITY":
            excerpt = f.get("user_excerpt", "")
            if excerpt and excerpt != "NOT FOUND":
                _add(excerpt)
        for item in f.get("items") or []:
            _add(item.get("excerpt"))

    return targets


def _direct_nested_tables(cell):
    """Tables nested directly inside a cell (not recursively collected)."""
    from docx.oxml.ns import qn
    from docx.table import Table
    return [Table(tbl_el, cell) for tbl_el in cell._tc.findall(qn("w:tbl"))]


def _all_paragraphs_recursive(cell):
    """Every paragraph in a cell, INCLUDING ones inside table(s) nested
    inside it (recursively) — the full paragraph set that
    extract_cell_text(cell)'s text corresponds to."""
    paras = list(cell.paragraphs)
    for nested in _direct_nested_tables(cell):
        for row in nested.rows:
            for c in row.cells:
                paras.extend(_all_paragraphs_recursive(c))
    return paras


def _iter_table_row_units(table):
    """
    Yield (paragraphs, combined_text) for every row of `table`; for any
    cell that has a table NESTED inside it, ALSO yield:
      (a) every row of that nested table as its own unit (a target that
          happens to match just one nested row still gets highlighted
          precisely), and
      (b) ONE combined "whole cell" unit spanning the cell's own
          paragraph(s) PLUS its entire nested table, using
          extract_cell_text — the EXACT same function evidence_comparator
          uses to build a requirement's description/excerpt.

    (b) matters because a requirement's own text often precedes its
    nested data table (e.g. "Configurable data:\nTYPE_HEARTBEAT | ...",
    or a multi-paragraph "shall" statement followed by a voltage-vs-time
    table) — the excerpt spans BOTH, so nothing shorter than the whole
    cell can ever match it. An earlier attempt tried to build a shorter,
    "cleaner" excerpt by guessing which nested row was a header to drop —
    but real tables (e.g. a voltage profile with no column labels) have
    genuine data as their first row, so that guess silently discarded
    real content. Using the exact same whole-cell text on both sides
    removes the guessing entirely: whatever the excerpt contains, this
    unit contains too.
    """
    from app.qa.retrieval import extract_cell_text

    for row in table.rows:
        cell_paras = []
        cell_texts = []
        seen_cell_ids = set()
        for cell in row.cells:
            # Merged cells repeat the same underlying cell object in
            # row.cells — skip duplicates so we don't double-collect.
            cid = id(cell._tc)
            if cid in seen_cell_ids:
                continue
            seen_cell_ids.add(cid)
            t = extract_cell_text(cell).strip()
            if t:
                cell_texts.append(t)
                cell_paras.extend(cell.paragraphs)
            nested_tables = _direct_nested_tables(cell)
            for nested in nested_tables:
                yield from _iter_table_row_units(nested)
            if nested_tables and t:
                yield _all_paragraphs_recursive(cell), t
        if cell_texts:
            yield cell_paras, " | ".join(cell_texts)


def _iter_docx_units(doc):
    """
    Yield (paragraphs, combined_text) covering every body paragraph
    (individually) and every table row (its cells flattened with " | ",
    exactly matching retrieval.py's _extract_docx — including any nested
    tables, see _iter_table_row_units), so a target string built from the
    extracted text can be matched back to the right run(s) — a whole
    paragraph, or every cell paragraph in a table row.
    """
    for para in doc.paragraphs:
        text = para.text.strip()
        if text:
            yield [para], text

    for table in doc.tables:
        yield from _iter_table_row_units(table)


def _highlight_paragraph(paragraph) -> None:
    from docx.enum.text import WD_COLOR_INDEX
    colour = getattr(WD_COLOR_INDEX, HIGHLIGHT_COLOR_NAME)
    for run in paragraph.runs:
        run.font.highlight_color = colour
    # A paragraph with text but no runs (rare edge case) — nothing to
    # style; python-docx has no paragraph-level highlight attribute.


def highlight_docx(original_bytes: bytes, targets: List[str]) -> Tuple[bytes, int]:
    """
    Open a DOCX from bytes, highlight every paragraph/table-row whose
    text contains one of the target snippets, and return the modified
    document as bytes plus how many distinct units were highlighted.
    """
    from docx import Document

    doc = Document(io.BytesIO(original_bytes))
    normalized_targets = [_normalize_ws(t) for t in targets]

    highlighted = 0
    # Cap unit size so one accidental match inside a huge paragraph
    # doesn't paint an implausibly large block of text yellow.
    _MAX_UNIT_LEN = 4000

    for paragraphs, combined_text in _iter_docx_units(doc):
        if len(combined_text) > _MAX_UNIT_LEN:
            continue
        norm_unit = _normalize_ws(combined_text)
        if any(t in norm_unit for t in normalized_targets):
            for p in paragraphs:
                _highlight_paragraph(p)
            highlighted += 1

    buf = io.BytesIO()
    doc.save(buf)
    buf.seek(0)
    return buf.read(), highlighted


def generate_annotated_spec(
    original_bytes: bytes,
    file_ext: str,
    report: Dict,
) -> Optional[Tuple[bytes, int]]:
    """
    Top-level entry point: produce a yellow-highlighted copy of the
    original spec pinpointing every passage a finding refers to.

    Returns (annotated_bytes, highlighted_count), or None if the file
    format isn't supported (only .docx today) or there is nothing to
    highlight (a fully compliant document — nothing needs to change).
    """
    if file_ext.lower() != ".docx":
        return None

    targets = collect_highlight_targets(report)
    if not targets:
        return None

    return highlight_docx(original_bytes, targets)
