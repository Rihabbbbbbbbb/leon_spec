"""
Spec → Conformity Matrix generator.

Extracts every requirement (ID + description) from a specification document
— excluding bare "Input requirement" DOORS/PLM traceability references,
which point to an upstream requirement rather than describing one of this
spec's own requirements — and fills them into the official CTS conformity
matrix template
(data/refs/Conformity_Matrix_Template.xlsx — the "new version" sheet of
Conformity_matrix_history_management_V1_5, macros removed), so suppliers
receive a pre-filled matrix instead of building it by hand.

Template layout (sheet "new version"):
  - rows 1-9: header block (COUNTIF stats, column titles in row 9)
  - column A: requirement description ("Libellé de la dernière version…")
  - column C: requirement identifier ("Numéro de l'exigence")
  - columns D-I: to be filled by the supplier (applicability, commitment,
    comments, PSA status) — left empty on purpose
  - data starts at row 10
"""
from __future__ import annotations

import io
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from app.config import REFS_DIR

TEMPLATE_NAME = "Conformity_Matrix_Template.xlsx"
TEMPLATE_PATH = REFS_DIR / TEMPLATE_NAME
# Shipped beside this module so Azure still finds it when data/refs is not
# copied into /home/site/wwwroot (the deployed package includes app/).
BUNDLED_TEMPLATE_PATH = Path(__file__).resolve().parent / "templates" / TEMPLATE_NAME


def resolve_template_path(explicit: Optional[Path] = None) -> Path:
    """Locate the conformity matrix workbook.

    The deployed Function App looks at ``/home/site/wwwroot/data/refs`` because
    that is ``REFS_DIR``, but that folder is not always in the package. The
    copy under ``app/qa/templates`` is.
    """
    if explicit:
        path = Path(explicit)
        if path.is_file():
            return path
        raise FileNotFoundError(
            f"Conformity matrix template not found: {path}. "
            "Expected data/refs/Conformity_Matrix_Template.xlsx."
        )

    candidates: List[Path] = []
    try:
        from app import config as cfg
        candidates.append(Path(cfg.REFS_DIR) / TEMPLATE_NAME)
    except Exception:
        pass
    candidates.append(Path(TEMPLATE_PATH))
    candidates.append(BUNDLED_TEMPLATE_PATH)
    script_root = os.getenv("AzureWebJobsScriptRoot")
    if script_root:
        candidates.append(Path(script_root) / "data" / "refs" / TEMPLATE_NAME)
        candidates.append(Path(script_root) / "app" / "qa" / "templates" / TEMPLATE_NAME)

    seen = set()
    for path in candidates:
        key = str(path)
        if key in seen:
            continue
        seen.add(key)
        if path.is_file():
            return path
    looked = ", ".join(str(p) for p in candidates) or str(TEMPLATE_PATH)
    raise FileNotFoundError(
        f"Conformity matrix template not found: {TEMPLATE_PATH}. "
        "Expected data/refs/Conformity_Matrix_Template.xlsx. "
        f"Also looked in: {looked}"
    )

DATA_START_ROW = 10
COL_DESCRIPTION = 1   # A
COL_REQ_ID = 3        # C

# Requirement identifier schemes seen in CTS specs and conformity matrices:
#   REF-PSP-COMP-001 / APP-xxx / GEN-xxx (spec schemes)
#   REQ-0945126 (matrix scheme)
#
# Real specs are hand-typed, so the separators between segments are
# inconsistent — all of these occur verbatim in the real ASU spec and are
# genuine requirement IDs with real description text attached:
#     REF-ASU-CD--CONN-0002     (doubled dash — already handled: "-" is a
#                                 body character, so no fix needed here)
#     REF-SIR-CD ESSAI-0002     (a SPACE where a dash belongs, no dash at
#                                 all between segments)
#     REF-ASU-CD- -CONN-0002    (dash, then a stray space, then a dash)
#     GEN-XXX-CDC-54411.001     (dotted numeric suffix — dots stay in the
#                                 body class, unlike a segment separator)
# The body class tolerates ONE embedded space at a time, but ONLY when the
# very next character is itself a valid ID character (upper-case letter,
# digit, underscore, dot or dash) — never a lower-case letter — so it
# bridges real typos without ever drifting into ordinary prose ("REF-ASU
# this is just text" still stops at "REF-ASU", exactly like before).
_ID_BODY = r"(?:[A-Za-z0-9_.-]|\s(?=[A-Z0-9_.-]))"
_REQ_ID_RE = re.compile(
    r"\b("
    r"(?:REF|APP|GEN)-[A-Za-z0-9]" + _ID_BODY + r"{1,40}"
    r"|REQ-\d{4,10}"
    r")\b"
)

# CTS specs use both "shall" and "must" for binding requirements
_SHALL_RE = re.compile(r"\b(?:shall|must)\b", re.IGNORECASE)

# Lines that look like headings / boilerplate, not requirements
_NOISE_RE = re.compile(
    r"^(table of|figure \d|page \d|see |cf\.|nota\b|note\s*:)", re.IGNORECASE
)

_MAX_DESC_CHARS = 600
_MIN_DESC_CHARS = 15


@dataclass
class Requirement:
    """One requirement extracted from the spec."""
    req_id: str        # "" when the spec gives no identifier
    text: str          # requirement description / statement
    line_no: int = 0   # source line (traceability)


@dataclass
class MatrixCoverage:
    """Result of verifying a generated matrix against the source spec.

    The matrix is generated FROM the spec, so in the healthy case every
    extracted requirement appears in the matrix and there are no ghost rows.
    This check is the QA round-trip that proves it: it reads the generated
    workbook back and confirms (a) no requirement was lost during generation
    and (b) no unexpected row (template residue, corruption, a generation
    bug) slipped in.
    """
    total_requirements: int = 0
    matched_requirements: int = 0
    missing_requirements: List[Dict] = field(default_factory=list)
    ghost_rows: List[Dict] = field(default_factory=list)
    coverage_rate: float = 0.0

    @property
    def complete(self) -> bool:
        """True when every spec requirement is in the matrix AND there are
        no ghost rows."""
        return not self.missing_requirements and not self.ghost_rows

    def to_dict(self) -> dict:
        return {
            "totalRequirements": self.total_requirements,
            "matchedRequirements": self.matched_requirements,
            "missingRequirements": self.missing_requirements,
            "ghostRows": self.ghost_rows,
            "coverageRate": round(self.coverage_rate, 4),
            "complete": self.complete,
        }


def _normalize_id(text: str) -> str:
    """Canonical form of a requirement ID: uppercase, no whitespace, no
    trailing version suffix like '(0)'."""
    if not text:
        return ""
    t = re.sub(r"\s+", "", str(text)).upper()
    t = re.sub(r"\(\d{1,3}\)$", "", t)
    return t


def _normalize_text(text: str) -> str:
    """Canonical form of a description for matching: collapse whitespace
    (incl. line breaks) to single spaces and trim."""
    return re.sub(r"\s+", " ", (text or "")).strip()


def verify_matrix_coverage(
    requirements: List[Requirement],
    xlsx_bytes: bytes,
) -> MatrixCoverage:
    """Read a generated matrix back and verify it against the source spec.

    Matching is deterministic:
      - a requirement WITH an ID is matched to the matrix row whose
        'Numéro de l'exigence' cell normalizes to the same ID;
      - a requirement WITHOUT an ID is matched to the row whose description
        normalizes to the same text (the generator writes req.text verbatim,
        so an exact normalized match is the correct contract).

    Returns a MatrixCoverage with:
      - missing_requirements: spec requirements absent from the matrix
        (generation lost them — a bug);
      - ghost_rows: matrix data rows that match no spec requirement
        (template residue, corruption, or a generation bug).
    """
    from openpyxl import load_workbook

    wb = load_workbook(io.BytesIO(xlsx_bytes))
    ws = wb["new version"]

    # Read every non-empty data row of the generated matrix.
    matrix_rows: List[Dict] = []
    for r in range(DATA_START_ROW, ws.max_row + 1):
        rid = ws.cell(row=r, column=COL_REQ_ID).value
        desc = ws.cell(row=r, column=COL_DESCRIPTION).value
        if not (rid or desc):
            continue
        matrix_rows.append({
            "row": r,
            "req_id": str(rid).strip() if rid else "",
            "text": str(desc).strip() if desc else "",
        })

    # Index matrix rows by normalized ID and by normalized text so each
    # spec requirement can be located in O(1).
    by_id: Dict[str, List[int]] = {}
    by_text: Dict[str, List[int]] = {}
    for mi, mrow in enumerate(matrix_rows):
        rid = _normalize_id(mrow["req_id"])
        if rid:
            by_id.setdefault(rid, []).append(mi)
        key = _normalize_text(mrow["text"])
        if key:
            by_text.setdefault(key, []).append(mi)

    used: set = set()          # matrix row indices already claimed
    missing: List[Dict] = []

    for req in requirements:
        rid = _normalize_id(req.req_id)
        candidates: List[int] = []
        if rid:
            candidates = [mi for mi in by_id.get(rid, []) if mi not in used]
        if not candidates:
            key = _normalize_text(req.text)
            if key:
                candidates = [mi for mi in by_text.get(key, []) if mi not in used]
        if candidates:
            used.add(candidates[0])
        else:
            missing.append({
                "reqId": req.req_id,
                "text": req.text,
                "lineNo": req.line_no,
            })

    # Ghost rows: matrix rows never claimed by any spec requirement.
    ghost: List[Dict] = []
    for mi, mrow in enumerate(matrix_rows):
        if mi not in used:
            ghost.append({
                "row": mrow["row"],
                "reqId": mrow["req_id"],
                "text": mrow["text"],
            })

    total = len(requirements)
    matched = total - len(missing)
    return MatrixCoverage(
        total_requirements=total,
        matched_requirements=matched,
        missing_requirements=missing,
        ghost_rows=ghost,
        coverage_rate=(matched / total) if total else 1.0,
    )


def _clean_segment(seg: str) -> str:
    """Normalize a text segment extracted from a (possibly table) line."""
    seg = re.sub(r"\s+", " ", seg).strip(" |·-–—\t")
    return seg.strip()


# Bracket reference tags ("[M11]", "[SSD_AUE]") and short version markers
# ("(0)") are "Input requirement" column content, never requirement prose.
# A table cell that's ONLY this — no real sentence — is an orphaned upstream
# reference that ended up on its own line during table flattening (e.g.
# "[M11] REF-CONN-CDC-DOC.0012 (0) [M11]"), not a description.
_REFERENCE_NOISE_RE = re.compile(r"\[[^\[\]]{1,20}\]|\(\d{1,3}\)")


def _is_reference_noise(text: str) -> bool:
    """True when `text` has no real requirement prose — only reference
    tags/version markers — so it must never be accepted as a description.
    A 2-letter fragment ("SI", "IF", "ASU") still counts as real content:
    these are genuine opening tokens of a multi-line cell (the real
    description follows on a later line), not reference-tag noise."""
    stripped = _REFERENCE_NOISE_RE.sub(" ", text)
    return not re.search(r"[A-Za-z]{2,}", stripped)


# Column-header labels from nested sub-tables (DTC parameter tables, fault-
# list tables) — e.g. "Flow | Label | Detection criteria | Disappearing
# criteria | Life sequence | Component" repeated inside a description cell
# before the actual data row. These are never real requirement content, but
# unlike _TABLE_HEADER_RE (the top-level "Requirement Number (v) | ..."
# header) they're short enough to otherwise pass _MIN_DESC_CHARS and get
# mistaken for the description itself — confirmed on the real ASU spec to
# make 6 distinct DTC requirements (MAINT-0001..0006) collapse to the
# identical, uninformative "...below: Flow", and 3 fault-list requirements
# (MAINT-0017..0019) fabricate "Appearance/Disappearance criteria" as if it
# were their description when the row is genuinely blank otherwise.
_GENERIC_TABLE_LABEL_RE = re.compile(
    r"^(?:requirement\s+description|appearance\s*/\s*disappearance\s+criteria"
    r"|life\s+phase|flow|label|detection\s+criteria|disappearing\s+criteria"
    r"|life\s+sequence|component)$",
    re.IGNORECASE,
)


def _best_description(segments: List[str]) -> str:
    """Pick the requirement description among table-cell segments:
    prefer the segment containing 'shall', else the longest one."""
    candidates = [
        s for s in segments
        if len(s) >= _MIN_DESC_CHARS and not _GENERIC_TABLE_LABEL_RE.match(s)
    ]
    if not candidates:
        return ""
    shall_segs = [s for s in candidates if _SHALL_RE.search(s)]
    pool = shall_segs or candidates
    return max(pool, key=len)


# A line that IS a requirement anchor: an ID alone (possibly followed by a
# short applicability code like " C" or "(v)"), e.g. "REQ-0937326  C".
_ANCHOR_RE = re.compile(
    r"^\s*("
    r"(?:REF|APP|GEN)-[A-Za-z0-9]" + _ID_BODY + r"{1,40}"
    r"|REQ-\d{4,10}"
    r")\b[\s.()A-Za-z0-9]{0,12}$"
)

# Table header row repeated before each requirement in CTS specs
_TABLE_HEADER_RE = re.compile(
    r"requirement\s+number.*description\s+of\s+the\s+requirement", re.IGNORECASE
)

# Metadata segments inside requirement tables (safety attributes, PSA refs)
_META_SEGMENT_RE = re.compile(
    r"att_sdf@|psa_comments@|\{\{|\}\}", re.IGNORECASE
)

# Template-instruction examples accidentally left in specs — not real reqs
_TEMPLATE_EXAMPLE_RE = re.compile(
    r"free to modify the example|it is mandatory to write a requirement"
    r"|<do something>|<expected functional performance|shall\s*…\s*$"
    r"|the requirement engineering template shall|<\s*be made of",
    re.IGNORECASE,
)

# A change-history "version bump" mention — "REF-X-0020(0) changed to
# REF-X-0020(1)" — resolves to the SAME rid on both sides (the "(0)"/"(1)"
# version suffix isn't part of the id), leaving just "(0) changed to (1)"
# once the ids are stripped. This is never a requirement description, but
# unlike a bare change-history heading it can't be caught by
# _is_reference_noise (real English words, "changed"/"to", not tag noise) —
# it must be filtered explicitly so it can never block the id's real,
# current definition from being recorded (changelogs sit near the top of
# the document, so left unfiltered this always wins the "first text wins"
# race against the real definition that follows later).
_VERSION_TRANSITION_RE = re.compile(
    r"^\(\d{1,3}\)\s*changed\s+to\s*\(\d{1,3}\)$", re.IGNORECASE
)

# Change-history headings ("New requirements:", "Removed requirements: …")
_HISTORY_HEADING_RE = re.compile(
    r"\b(new|removed|modified)\s+requirements?\s*:?\s*$", re.IGNORECASE
)

_BLOCK_MAX_LINES = 30

# A line that is a self-contained INLINE requirement row
# ("APP-ASU-CD-PERF-0001(0) | The ASU must … | [M8]") — such a line always
# ENDS the current anchor block: it belongs to the next requirement.
_INLINE_ROW_RE = re.compile(
    r"^\s*(?:(?:REF|APP|GEN)-[A-Za-z0-9]" + _ID_BODY + r"{1,40}|REQ-\d{4,10})\b[^|]{0,20}\|"
)


def _fix_id_spacing(text: str) -> str:
    """Repair IDs broken by stray spaces anywhere inside them:
    'REF- ASU…', 'REF-ASU-CD- EXINTER -0006(0)' → 'REF-ASU-CD-EXINTER-0006(0)'.
    Requires at least two dash-segments so prose like 'REF - see below'
    is never touched."""
    def _join(m):
        return re.sub(r"\s+", "", m.group(0))
    return re.sub(
        r"\b(?:REF|APP|GEN)(?:\s*-\s*[A-Z0-9_.]{1,20}){2,}",
        _join,
        text,
    )


def _parse_block_description(block_lines: List[str]) -> Tuple[str, str]:
    """
    Extract (internal_ref, description) from a requirement block.

    The block is the flattened table content following an ID anchor:
    header row, internal requirement number (REF-…), safety/PSA metadata,
    then "…}} | <description> | [upstream]" — where the description may
    span several lines between the pipe separators.
    """
    kept = [l for l in block_lines if not _TABLE_HEADER_RE.search(l)]
    joined = "\n".join(kept)

    m = _REQ_ID_RE.search(joined)
    internal_ref = m.group(1) if m else ""

    segments = []
    for seg in joined.split("|"):
        seg_clean = re.sub(r"\s+", " ", seg).strip(" ·-–—\t")
        if not seg_clean or len(seg_clean) < _MIN_DESC_CHARS:
            continue
        if _META_SEGMENT_RE.search(seg_clean):
            continue
        # Upstream-reference cells like "[SSD_AUE]"
        if re.fullmatch(r"\[?[A-Z0-9_ ,;/-]{1,40}\]?", seg_clean):
            continue
        segments.append(seg_clean)

    desc = _best_description(segments)
    return internal_ref, desc


def extract_requirements(text: str) -> List[Requirement]:
    """
    Extract requirements from the full spec text (paragraphs + flattened
    tables, as produced by extract_text_from_file).

    Three mechanisms, in document order:
    1. BLOCK: an anchor line holding just an ID ("REQ-0937326  C") opens a
       requirement block that runs until the next anchor; the description
       (and the internal REF-… number) are parsed from the block's table
       segments — multi-line descriptions between pipes are handled.
    2. INLINE: a line containing both an ID and its text ("REF-X | The
       system shall … | [SSD]") is parsed directly.
    3. SHALL-ONLY: 'shall' statements without any ID are kept with an
       empty identifier so the matrix stays complete.

    Deduplication prefers the occurrence WITH a description: change-history
    mentions ("New requirements: REQ-123") are superseded by the real
    definition found later in the document.
    """
    text = _fix_id_spacing(text)
    lines = text.split("\n")

    requirements: List[Requirement] = []
    by_id: dict = {}
    seen_texts: set = set()
    consumed = [False] * (len(lines) + 1)
    # line index → Requirement that owns that source line (for attaching
    # continuation lines of long multi-line descriptions in pass 2)
    line_owner: dict = {}

    def _add(rid: str, desc: str, line_no: int):
        desc = re.sub(r"\s+", " ", desc).strip()[:_MAX_DESC_CHARS]
        if desc and _TEMPLATE_EXAMPLE_RE.search(desc):
            return
        # A change-history heading is not a description
        if desc and _HISTORY_HEADING_RE.search(desc):
            desc = ""
        # A bare "(0) changed to (1)" version-bump mention is not a
        # description either — see _VERSION_TRANSITION_RE.
        if desc and _VERSION_TRANSITION_RE.match(desc):
            desc = ""
        # A "REQ-…" (DOORS export id) block that never got a real
        # requirement statement merged into it — because the next anchor
        # turned out to be a genuinely separate, unrelated requirement,
        # not this one's internal ref — can end up with its OWN "block"
        # being nothing but the next subsection's heading (e.g. "Timing
        # Performances"), since that heading is the only content sitting
        # between the two anchors. A heading is not a description: if a
        # REQ- entry's text has no shall/must statement, it isn't one.
        if rid.startswith("REQ-") and desc and not _SHALL_RE.search(desc):
            desc = ""
        if rid:
            if rid in by_id:
                # Prefer the occurrence that has a description — and, once
                # both have one, prefer whichever contains a real shall/must
                # statement over one that doesn't. Without this second rule,
                # a change-history sentence that happens to mention this same
                # id (e.g. "REF-X-0020(0) changed to REF-X-0020(1)" — the
                # "(0)"/"(1)" version suffix isn't part of the id, so both
                # sides resolve to the same rid) can win permanently just by
                # sitting earlier in the document than the id's real, current
                # definition, since changelogs are always near the top.
                existing = by_id[rid]
                existing_has_shall = bool(existing.text) and bool(_SHALL_RE.search(existing.text))
                new_has_shall = bool(desc) and bool(_SHALL_RE.search(desc))
                if desc and not existing.text:
                    existing.text = desc
                elif new_has_shall and not existing_has_shall:
                    existing.text = desc
                elif (desc and not existing_has_shall and not new_has_shall
                        and len(desc) > len(existing.text) * 2):
                    # Neither side is a real shall/must statement — prefer
                    # the substantially longer, more complete text. A real
                    # multi-version id (like a diagnostic mapping entry that
                    # legitimately has no "shall" wording at all) can have
                    # its OLD, superseded version's table produce a short
                    # column-label fragment ("Appearance/Disappearance
                    # criteria") that would otherwise permanently block the
                    # id's real, current, much more substantial definition
                    # appearing later in the document.
                    existing.text = desc
                return
            req = Requirement(req_id=rid, text=desc, line_no=line_no)
            by_id[rid] = req
            requirements.append(req)
        else:
            if not desc:
                return
            key = re.sub(r"\W+", "", desc.lower())[:120]
            if key in seen_texts:
                return
            seen_texts.add(key)
            requirements.append(Requirement(req_id="", text=desc, line_no=line_no))

    # ── Pass 1: anchor blocks ──────────────────────────────────────
    anchor_idx = [
        i for i, l in enumerate(lines) if _ANCHOR_RE.match(l.strip())
    ]

    def _history_heading_kind(i: int) -> str:
        """'new'/'modified'/'removed' if anchor `i` is listed directly under
        that change-history heading, else ''."""
        for k in (i - 1, i - 2):
            if k >= 0 and lines[k].strip():
                m = _HISTORY_HEADING_RE.search(lines[k].strip())
                return m.group(1).lower() if m else ""
        return ""

    # Coalesce DOORS + internal anchors: in CTS requirement tables the
    # "REQ-… C" (DOORS id) anchor is immediately followed by the internal
    # "REF-…" number of the SAME requirement — merge them into one block
    # keyed by the REQ id, with the REF id kept as internal reference.
    #
    # This must NOT fire on the real ASU spec's actual layout: there, every
    # "REQ-… C" is the TRAILING doors-id of a requirement whose own
    # substantial description was already given by an EARLIER "REF-…"
    # anchor — and the very next anchor after it is a completely
    # DIFFERENT, unrelated requirement's own "REF-…", sometimes only 0-3
    # lines away (a short subsection heading + the repeated table-header
    # row, or nothing at all between two tightly-packed rows). Naively
    # coalescing on proximity alone silently dropped that next
    # requirement from the matrix entirely — confirmed on the real spec:
    # 11+ real requirements lost this way.
    #
    # The reliable signal is whether the "REQ-…" anchor is TRAILING an
    # already-substantial block (real content — a shall/must statement —
    # between the PREVIOUS anchor and this one) versus genuinely OPENING
    # a fresh one (nothing of substance precedes it, e.g. it's the very
    # first anchor, or a bare history-mention placeholder). Only the
    # latter may still absorb the next anchor, and only when the gap to
    # that next anchor is itself trivial (blank, or just the repeated
    # table-header row — never a real subsection heading).
    def _has_shall_content(start: int, stop: int) -> bool:
        return any(_SHALL_RE.search(lines[k]) for k in range(start, stop))

    merged_into_prev = set()
    for pos in range(len(anchor_idx) - 1):
        i, nxt = anchor_idx[pos], anchor_idx[pos + 1]
        rid = _ANCHOR_RE.match(lines[i].strip()).group(1)
        nid = _ANCHOR_RE.match(lines[nxt].strip()).group(1)
        gap_is_trivial = all(
            not lines[k].strip() or _TABLE_HEADER_RE.search(lines[k])
            for k in range(i + 1, nxt)
        )
        prev_anchor = anchor_idx[pos - 1] if pos > 0 else -1
        trails_substantial_block = _has_shall_content(prev_anchor + 1, i)
        if (rid.startswith("REQ-") and not nid.startswith("REQ-")
                and nxt - i <= 4 and gap_is_trivial
                and not trails_substantial_block):
            merged_into_prev.add(pos + 1)

    for pos, i in enumerate(anchor_idx):
        if pos in merged_into_prev:
            continue
        rid = _ANCHOR_RE.match(lines[i].strip()).group(1)

        history_kind = _history_heading_kind(i)
        if history_kind == "removed":
            # A requirement explicitly listed as REMOVED in the change
            # history was deleted from the document body — unlike "new" or
            # "modified" mentions, there is no real definition anywhere
            # later in the spec to supersede this placeholder (that's what
            # "removed" means). Registering it anyway leaves a permanent
            # phantom entry with no content, so it must be dropped entirely.
            consumed[i] = True
            continue
        if history_kind:
            _add(rid, "", i + 1)
            consumed[i] = True
            continue

        # Block ends at the next non-merged anchor
        end = len(lines)
        nxt_pos = pos + 1
        while nxt_pos < len(anchor_idx) and nxt_pos in merged_into_prev:
            nxt_pos += 1
        if nxt_pos < len(anchor_idx):
            end = anchor_idx[nxt_pos]
        end = min(end, i + 1 + _BLOCK_MAX_LINES)

        # …but never swallow a self-contained inline requirement row —
        # those belong to other requirements (they are parsed in pass 2).
        skip_first = 2 if pos + 1 in merged_into_prev else 0
        for j in range(i + 1 + skip_first, end):
            if _INLINE_ROW_RE.match(lines[j]):
                end = j
                break

        block = lines[i + 1:end]
        if not block:
            # An anchor immediately followed (zero gap) by the next
            # anchor/row has NO content of its own at all — every real
            # requirement in this spec has at least some body text. This is
            # a stray "Input requirement" reference (e.g. a second/historical
            # DOORS reference stacked in the same table cell as another
            # row's own upstream column — "REF-TF-TFD-MVEL-0108(1)" sitting
            # alone right before the NEXT row's own complete "id | desc |
            # upstream" line) that happened to match the anchor pattern —
            # not a requirement of this spec, and must never become its own
            # row, empty or otherwise.
            consumed[i] = True
            continue
        internal_ref, desc = _parse_block_description(block)
        if internal_ref and internal_ref != rid and desc:
            desc = f"[{internal_ref}] {desc}"
        _add(rid, desc, i + 1)
        owner = by_id.get(rid)
        for j in range(i, end):
            consumed[j] = True
            if owner is not None:
                line_owner[j] = owner

    # ── Pass 2: inline ID lines + shall-only statements ───────────
    # Logic keywords used inside requirement bodies (IF/THEN blocks) —
    # ALLCAPS but NOT section headings.
    _LOGIC_KEYWORDS = {"IF", "THEN", "ELSE", "SI", "ALORS", "AND", "OR",
                       "ET", "OU", "NOT", "ENDIF", "ELSEIF"}

    def _is_boundary(s: str, noise_is_boundary: bool = True) -> bool:
        """A line that starts a new requirement/table/section — it can
        never be the continuation of the previous cell. In continuation
        contexts noise lines ('See picture…', 'Note: …') are cell CONTENT,
        not boundaries."""
        return bool(
            _ANCHOR_RE.match(s)
            or _INLINE_ROW_RE.match(s)
            or _TABLE_HEADER_RE.search(s)
            or (noise_is_boundary and _NOISE_RE.match(s))
            or _HISTORY_HEADING_RE.search(s)
            or (s == s.upper() and len(s) > 3
                and any(c.isalpha() for c in s)
                and s.strip() not in _LOGIC_KEYWORDS)
        )

    def _looks_like_heading_not_continuation(
        prev_text: str, s: str, upcoming: Optional[List[str]] = None
    ) -> bool:
        """
        A subsection heading that immediately follows a table (e.g. "LIN 2.1
        Physical Layers", "Time requirements", "Self-test procedure and
        operator control in assembly phase") is NOT all-caps, so the plain
        _is_boundary() check above misses it — confirmed during a 2026 audit
        to silently glue ~20 real subsection headings onto the END of the
        PRECEDING requirement's description, across mixed heading styles
        (Title Case, sentence case) too inconsistent for a pure
        capitalization-pattern match.

        Instead, use the CONTEXT: a genuine continuation either keeps
        writing the same unterminated sentence, or is itself real prose
        (ends with punctuation, or contains "shall"/"must"). A short,
        capitalized fragment with NO terminal punctuation and NO
        requirement verb is treated as a heading, not a continuation, when
        EITHER the accumulated description already reads as a complete,
        terminated sentence (ends in punctuation or a closing bracket), OR
        the very NEXT line is itself the requirement TABLE header — the
        latter catches a heading whose preceding sentence happens to end on
        a bare word with no closing marker at all (the document's own prose
        is sometimes this abrupt), since a heading directly adjacent to the
        table it introduces is unambiguous regardless of how the previous
        sentence happened to end. Deliberately narrow (only the immediate
        next line, not a wider lookahead window) — a wider window can see
        PAST an intervening real heading/table and wrongly treat unrelated
        earlier prose as if it were what introduces that later table.
        """
        s = s.strip()
        if not s or len(s) > 80 or not s[0].isupper():
            return False
        if s[-1] in ".!?:":
            return False
        if _SHALL_RE.search(s):
            return False
        prev = prev_text.strip()
        # A reference tag "[STA20]" or version marker "(0)" closing the
        # accumulated text also reads as "finished", not mid-sentence —
        # this document's own style routinely ends a requirement sentence
        # on a bare reference tag with no period at all.
        if bool(prev) and prev[-1] in ".!?])":
            return True
        if upcoming and any(_TABLE_HEADER_RE.search(u) for u in upcoming):
            return True
        return False

    n = len(lines)
    i = 0
    # Last captured requirement + the last source line attributed to it —
    # used to attach continuation lines of long multi-line descriptions.
    last_req: Optional[Requirement] = None
    last_req_end = -10
    orphans: List[Requirement] = []

    def _attachable_gap(start: int, stop: int) -> Optional[List[str]]:
        """Return the plain continuation lines between the last requirement
        and a candidate continuation line, or None if any boundary
        (new table row, header, heading, pipes) separates them."""
        if stop - start > 6:
            return None
        gap: List[str] = []
        for k in range(start, stop):
            s = lines[k].strip()
            if not s:
                continue
            if (consumed[k] or _is_boundary(s, noise_is_boundary=False)
                    or "|" in s or _REQ_ID_RE.search(s)):
                return None
            gap.append(s)
        return gap

    while i < n:
        if consumed[i]:
            owner = line_owner.get(i)
            if owner is not None:
                last_req, last_req_end = owner, i
            i += 1
            continue
        line = lines[i].strip()
        if not line or _NOISE_RE.match(line):
            i += 1
            continue

        ids = _REQ_ID_RE.findall(line)
        if ids:
            # Table rows are "requirement number | description | upstream
            # requirement": IDs sitting AFTER the description segment are
            # upstream references to other documents' requirements — they
            # are NOT requirements of this spec and must not become rows.
            raw_segments = line.split("|")
            seg_clean = [_clean_segment(_REQ_ID_RE.sub(" ", s)) for s in raw_segments]
            desc = _best_description(seg_clean)
            if desc and desc in seg_clean:
                desc_seg_idx = seg_clean.index(desc)
            else:
                desc_seg_idx = len(raw_segments)
            pre_ids, post_ids = [], []
            for si, seg in enumerate(raw_segments):
                for rid in _REQ_ID_RE.findall(seg):
                    (pre_ids if si <= desc_seg_idx else post_ids).append(rid)
            # "id | desc | upstream" rows: trailing ids are upstream refs of
            # OTHER requirements → excluded. "desc | id" rows have no leading
            # id: the trailing id IS the requirement's own identifier — but
            # ONLY for a clean 2-column shape (exactly one pipe). A row with
            # MORE columns and no leading id is raw multi-column table data
            # (e.g. an FMEA failure-mode row "Flow: X | failure mode | PPM
            # value | GEN-xxx(0)") whose trailing id is that row's own
            # "Input requirement" reference, not a fresh anchor of this spec.
            if pre_ids:
                req_ids = pre_ids
            elif len(raw_segments) <= 2:
                req_ids = post_ids
            else:
                req_ids = []
            # A line whose ENTIRE content (every segment, ids stripped) is
            # reference-tag/version-marker noise — e.g. an orphaned "Input
            # requirement" cell like "[M11] REF-CONN-CDC-DOC.0012 (0) [M11]"
            # that ended up on its own line during table flattening — carries
            # no real requirement content anywhere: its id is a
            # cross-document reference, not an anchor of THIS spec, and must
            # never become its own row. This must check the WHOLE line, not
            # just the selected `desc`: a short-but-real opening fragment
            # like "REF-ASU-CD-DOC-0004(0) | ASU" is a genuine anchor whose
            # real description is still to come via continuation merging —
            # "ASU" is real content, just too short to BE the description yet.
            combined_clean = " ".join(seg_clean).strip()
            line_is_pure_noise = not combined_clean or _is_reference_noise(combined_clean)
            if not line_is_pure_noise:
                for rid in req_ids:
                    _add(rid, desc, i + 1)

            # ── Continuation merging ──
            # When the row has no upstream cell after the description, the
            # description cell is still OPEN: the following lines (numbered
            # methods, second paragraph of the same cell, …) belong to THIS
            # requirement until the next boundary or a "… | [upstream]"
            # closing line. Prevents one requirement from being split into
            # several rows.
            cell_open = desc_seg_idx >= len(raw_segments) - 1
            req_obj = by_id.get(req_ids[0]) if req_ids else None
            if (cell_open and req_obj is not None
                    and req_obj.line_no == i + 1):
                j = i + 1
                appended = 0
                while j < n and appended < 12:
                    nxt = lines[j].strip()
                    if not nxt:
                        j += 1
                        continue  # blank line inside the same cell
                    if (consumed[j] or _is_boundary(nxt, noise_is_boundary=False)
                            or _looks_like_heading_not_continuation(
                                req_obj.text, nxt, lines[j + 1:j + 2])):
                        break
                    if "|" in nxt:
                        pre_part, post_part = nxt.split("|", 1)
                        pre = _clean_segment(pre_part)
                        # "Description of a NEW requirement | GEN-…-041(0)"
                        # (own id in the trailing cell, a fresh capitalized
                        # sentence with no id of its own before the pipe) is
                        # a genuinely separate row — leave it for normal
                        # processing, do not absorb it. But when `pre`
                        # continues the CURRENTLY open sentence (starts
                        # lowercase — a real spec requirement always opens
                        # with a capitalized "The/A/..."), the trailing id is
                        # simply this same cell's closing "Input requirement"
                        # reference, not a new anchor — it must be absorbed,
                        # never treated as a fresh row (that previously
                        # misattributed this requirement's own continuation
                        # text to an unrelated upstream-reference id).
                        starts_new_sentence = bool(pre) and pre[0].isupper()
                        if (_REQ_ID_RE.search(post_part)
                                and not post_part.lstrip().startswith("[")
                                and starts_new_sentence):
                            break
                        # A nested sub-table's own header row ("Flow | Label
                        # | Detection criteria | ...") is not real content —
                        # skip it entirely (don't append, don't stop) so the
                        # loop reaches the actual DATA row that follows, e.g.
                        # "Circuit short to battery or open | ..." — the one
                        # piece of text that actually distinguishes this DTC
                        # requirement from the next one. Confirmed on the
                        # real ASU spec: without this, 6 distinct MAINT-000X
                        # requirements all collapsed to the identical,
                        # uninformative "...below: Flow".
                        if pre and _GENERIC_TABLE_LABEL_RE.match(pre):
                            consumed[j] = True
                            j += 1
                            continue
                        # Closing line of the cell: "…rest of desc | [M20]"
                        if pre and not _REQ_ID_RE.search(pre):
                            req_obj.text = (req_obj.text + " " + pre).strip()[:_MAX_DESC_CHARS]
                            consumed[j] = True
                            j += 1
                        break
                    req_obj.text = (req_obj.text + " " + nxt).strip()[:_MAX_DESC_CHARS]
                    consumed[j] = True
                    appended += 1
                    j += 1
                last_req, last_req_end = req_obj, j - 1
                i = j
                continue
            if req_obj is not None and req_obj.line_no == i + 1:
                last_req, last_req_end = req_obj, i
            i += 1
            continue

        if _SHALL_RE.search(line):
            # Multi-column data rows (parameter tables: "T_Prearm_ASU | 6 |
            # [0;6] | s | description…") are table data, NOT requirements.
            if line.count("|") >= 2:
                i += 1
                continue
            segments = [_clean_segment(s) for s in line.split("|")]
            desc = _best_description(segments)
            if desc:
                # A shall/must statement shortly after the last requirement,
                # separated only by plain continuation lines (IF/THEN,
                # formulas…), is the CONTINUATION of that requirement's long
                # description — merge it instead of creating an id-less row.
                gap = (
                    _attachable_gap(last_req_end + 1, i)
                    if last_req is not None and "|" not in line
                    else None
                )
                if gap is not None:
                    addition = " ".join(gap + [desc])
                    last_req.text = (
                        (last_req.text + " " + addition).strip()[:_MAX_DESC_CHARS]
                    )
                    for k in range(last_req_end + 1, i + 1):
                        consumed[k] = True
                    last_req_end = i
                elif len(desc) >= 30:
                    # Unattachable id-less statement: kept aside — only used
                    # when the whole document defines NO requirement ids
                    # (otherwise it is table prose, not a requirement).
                    orphans.append(Requirement(req_id="", text=desc, line_no=i + 1))
        i += 1

    # Id-less statements become rows ONLY for documents without any
    # requirement identifiers (else the matrix keeps ids exclusively).
    if not by_id:
        for orph in orphans:
            _add("", orph.text, orph.line_no)

    # A bare "REQ-nnnnnnn" id (no REF-/APP-/GEN- prefix) is the DOORS/PLM
    # export id used by CTS tables as the "Input requirement (v)" column
    # value — an upstream traceability reference, not a requirement of
    # THIS spec. It only became a candidate row here because the anchor
    # scanner also treats it as a block anchor (some CTS tables place it
    # first, as its own requirement's DOORS id). When that anchor's block
    # never yields a real shall/must statement of its own — the normal
    # case, since real content already belongs to the requirement it
    # trails — it carries no description and must not appear in the
    # matrix at all: it is the input requirement, not a requirement.
    requirements = [
        r for r in requirements
        if not (re.fullmatch(r"REQ-\d{4,10}", r.req_id) and not r.text)
    ]

    return requirements


def generate_conformity_matrix(
    requirements: List[Requirement],
    spec_name: str = "",
    template_path: Optional[Path] = None,
) -> bytes:
    """
    Fill the conformity matrix template with the extracted requirements.

    Writes only column A (description) and column C (requirement ID) from
    row 10 down — the supplier columns (applicability, commitment, comments,
    PSA status) and the header block with its COUNTIF statistics are left
    exactly as in the template.

    Returns the filled workbook as XLSX bytes (macro-free).
    """
    from openpyxl import load_workbook
    from openpyxl.styles import Alignment

    path = resolve_template_path(template_path)

    wb = load_workbook(str(path))
    ws = wb["new version"]

    wrap = Alignment(wrap_text=True, vertical="top")
    row = DATA_START_ROW
    for req in requirements:
        desc_cell = ws.cell(row=row, column=COL_DESCRIPTION, value=req.text)
        desc_cell.alignment = wrap
        id_cell = ws.cell(row=row, column=COL_REQ_ID, value=req.req_id)
        id_cell.alignment = wrap
        row += 1

    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return buf.read()


def spec_to_matrix(spec_text: str, spec_name: str = "") -> dict:
    """
    Full pipeline: extract requirements from spec text and produce the
    pre-filled conformity matrix.

    Returns a dict with the XLSX bytes, extraction statistics AND the
    round-trip coverage check (every extracted requirement must appear in
    the generated matrix; any matrix row with no matching requirement is
    flagged as a ghost row):
      {
        "xlsxBytes": bytes,
        "requirementsCount": int,
        "withIdCount": int,
        "withoutIdCount": int,
        "sampleIds": [str, ...],
        "coverage": {
            "totalRequirements": int,
            "matchedRequirements": int,
            "missingRequirements": [ {reqId, text, lineNo}, ... ],
            "ghostRows": [ {row, reqId, text}, ... ],
            "coverageRate": float,
            "complete": bool,
        },
      }
    """
    requirements = extract_requirements(spec_text)
    xlsx_bytes = generate_conformity_matrix(requirements, spec_name)
    with_id = [r for r in requirements if r.req_id]
    coverage = verify_matrix_coverage(requirements, xlsx_bytes)
    return {
        "xlsxBytes": xlsx_bytes,
        "requirementsCount": len(requirements),
        "withIdCount": len(with_id),
        "withoutIdCount": len(requirements) - len(with_id),
        "sampleIds": [r.req_id for r in with_id[:10]],
        "coverage": coverage.to_dict(),
    }
