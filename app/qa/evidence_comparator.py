"""
Evidence-based specification comparator.

Compares a user's specification document against the REAL rules extracted
from the Stellantis template and writing guide (via rule_extractor.py).

Every finding produced by this module carries DOUBLE EVIDENCE:
  1. The exact rule/instruction from the source document (template or guide)
  2. The exact excerpt from the user's document (or "NOT FOUND" if absent)

This guarantees 100% traceability and zero hallucination: every finding
can be verified by a human by checking both the source rule and the user
document excerpt.

Check categories (all deterministic, no LLM):
  A. SECTION COVERAGE     — mandatory sections from the template present?
  B. SECTION ORDER        — sections appear in the template's standard-plan order?
  C. PLACEHOLDER RESIDUE  — template <<...>> / <...> markers left unfilled?
  D. REQUIREMENT FORMAT   — R22: 3-column table (ID, description, upstream req)
  E. REQUIREMENT LANGUAGE — R23: "shall" mandatory, subjective words prohibited
  F. REQUIREMENT IDs      — R20/PCIEE: each requirement has a unique ID
  G. TRACEABILITY         — R22: upstream requirement column (or N/A)
  H. WRITING GUIDE RULES  — R01-R53, P01-P10 checks that can be verified deterministically
  I. DOCUMENT IDENTIFICATION — R05/R09: title, revision history, writer/approver
  J. STANDARDS CONSISTENCY — R17: standards/norms declared in Applicable
     Documents/Standards vs. actually referenced elsewhere in the document
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from app.qa.rule_extractor import (
    ExtractedRules,
    extract_all_rules, get_rule_by_id,
)


# ── Finding data structure (extends the existing Finding with evidence) ──
@dataclass
class EvidenceFinding:
    """A validation finding with full double-evidence traceability."""
    check: str              # check category (A-I)
    severity: str           # "error" | "warning" | "info" | "pass"
    section: str            # user-document section (or "")
    rule_id: str            # source rule ID (e.g. "R22", "TEMPLATE", "STRUCTURE")
    message: str            # human-readable description
    # ── Double evidence ──
    source_rule: str        # the exact rule text from template/guide
    source_doc: str         # "template" | "writing_guide" | "standard_plan"
    user_excerpt: str       # the exact excerpt from the user's document (or "")
    user_location: str      # where in the user doc (section/line context)
    why: str                # WHY this matters (rationale for the engineer)
    fix_suggestion: str = ""  # actionable fix
    # Optional structured breakdown for findings that aggregate many items
    # (e.g. every untraced requirement) — each dict has "id"/"location"/"excerpt".
    items: List[Dict] = field(default_factory=list)


# ── Structural requirement-table extraction (DOCX only) ───────────
#
# The CTS requirement tables have an explicit 3-column shape:
#   "Requirement Number (v)" | "Description of the requirement" | "Input requirement (v)"
# The third column IS the traceability answer — filled means traced, empty
# means untraced, "N/A" means "deliberately no upstream" (which R22 states
# is the correct way to declare that, so it counts as traced).
#
# Reading that cell directly is exact. The flattened-text fallback used for
# PDF/TXT can only guess, because extract_text_from_file() drops empty
# cells — so "REF-X | description" is indistinguishable from a row whose
# upstream column was blank, and a neighbouring requirement's reference
# can bleed into the guess. Whenever the original .docx is available we
# therefore use the structural reading and never the guess.

@dataclass
class RequirementRow:
    """One requirement row read structurally from a CTS requirement table."""
    req_id: str
    description: str
    upstream: str
    section: str
    traced: bool          # the "Input requirement" cell is filled (R22-compliant)
    table_index: int
    row_index: int
    explicit_na: bool = False   # filled, but with "N/A" rather than a reference
    # The CTS template's own unedited requirement-engineering EXAMPLE row
    # (id "REF-PSP-...", description literally "The system shall…", upstream
    # literally "Nothing in this field") — confirmed present, unedited, in a
    # real ASU spec during a 2026 audit. It must never count as a real,
    # "compliant" requirement (it isn't one), but IS worth its own explicit
    # finding since leaving template boilerplate in a submitted document is
    # itself a real authoring defect.
    is_template_example: bool = False


# The template's literal placeholder text for "this requirement has no
# upstream reference" — distinct from a real "N/A" declaration (see
# _NA_UPSTREAM_RE), and distinct enough from any genuine engineering content
# that matching it verbatim is safe.
_TEMPLATE_EXAMPLE_UPSTREAM_RE = re.compile(r"^\s*nothing\s+in\s+this\s+field\s*$", re.IGNORECASE)
# The template's literal unfilled requirement-statement stub — a real
# requirement would never read as JUST "The system shall" with nothing
# after it but an ellipsis.
_TEMPLATE_EXAMPLE_DESCRIPTION_RE = re.compile(r"^\s*the\s+system\s+shall\s*[.…]{0,3}\s*$", re.IGNORECASE)


_UPSTREAM_HEADER_RE = re.compile(r"input\s+requirement|exigence\s+amont", re.IGNORECASE)
# R22: "When there is no input requirement, the field is filled with N/A."
# So an explicit N/A is a COMPLIANT declaration, not a gap.
_NA_UPSTREAM_RE = re.compile(
    r"^(?:n\s*/?\s*a|none|null|sans\s+objet|n[ée]ant|-{1,3}|_{1,3})$",
    re.IGNORECASE,
)
_STRUCT_HEADING_RE = re.compile(r"^[A-Z][A-Z0-9 /()\-&,:;.–—]{3,}$")


def _docx_table_sections(doc) -> List[str]:
    """
    Return the nearest heading above each top-level table, positionally:
    result[i] is the section for doc.tables[i].

    Indexed by position rather than keyed on element identity on purpose —
    lxml creates throwaway proxy objects for elements, so id() values are
    recycled by the garbage collector and an id-keyed dict silently returns
    ANOTHER table's heading. Body order and doc.tables order agree for
    top-level tables, so the running counter is exact.
    """
    from docx.oxml.ns import qn
    from docx.text.paragraph import Paragraph

    sections: List[str] = []
    current = ""
    for child in doc.element.body.iterchildren():
        if child.tag == qn("w:p"):
            para = Paragraph(child, doc)
            text = para.text.strip()
            if text and len(text) < 120:
                style = (para.style.name or "") if para.style is not None else ""
                if style.startswith("Heading") or _STRUCT_HEADING_RE.match(text):
                    current = text
        elif child.tag == qn("w:tbl"):
            sections.append(current)
    return sections


def extract_requirement_rows(source_path) -> List[RequirementRow]:
    """
    Read every CTS requirement row directly from the .docx tables.

    Only tables that actually declare an "Input requirement" / "Exigence
    amont" column are considered, and only rows whose first cell holds a
    real requirement ID — so descriptive/aggregate tables never pollute
    the traceability statistics.

    Returns [] for non-DOCX inputs or if the file cannot be parsed, which
    makes the caller fall back to the text heuristic.
    """
    try:
        from docx import Document
        from app.qa.retrieval import extract_cell_text
    except Exception:
        return []

    path_str = str(source_path)
    if not path_str.lower().endswith(".docx"):
        return []

    try:
        doc = Document(path_str)
    except Exception:
        return []

    table_sections = _docx_table_sections(doc)
    rows: List[RequirementRow] = []

    for ti, table in enumerate(doc.tables):
        if not table.rows:
            continue
        header = [_normalize_ws_lower(c.text) for c in table.rows[0].cells]
        upstream_idx = None
        for ci, head in enumerate(header):
            if _UPSTREAM_HEADER_RE.search(head):
                upstream_idx = ci
        if upstream_idx is None:
            continue

        section = table_sections[ti] if ti < len(table_sections) else ""

        for ri, row in enumerate(table.rows):
            if ri == 0:
                continue
            # extract_cell_text (not the bare cell.text python-docx exposes)
            # so a cell whose real content lives in a NESTED table — e.g. a
            # "Description" cell containing a whole failure-mode sub-table —
            # isn't silently read as empty.
            cells = [extract_cell_text(c).strip() for c in row.cells]
            if upstream_idx >= len(cells) or not cells:
                continue
            match = REQ_ID_RE.search(cells[0])
            if not match:
                continue  # not a requirement row (spacer, note, continuation)

            upstream = cells[upstream_idx].strip()
            # R22 is satisfied as soon as the field is FILLED — either with a
            # real upstream reference, or with "N/A" which the rule explicitly
            # designates as the way to declare "this requirement has no
            # upstream". Only a genuinely EMPTY cell is a traceability gap.
            traced = bool(upstream)
            explicit_na = bool(upstream) and bool(_NA_UPSTREAM_RE.match(upstream))
            # cells[1] is already extract_cell_text's full recursive read
            # (own paragraph(s) + any nested table, header row included).
            # An earlier version tried to skip a nested table's supposed
            # "header row" for a cleaner excerpt — but real requirements
            # (e.g. a voltage-vs-time profile with no column labels at
            # all) have genuine DATA as their very first nested row, so
            # that heuristic silently discarded real content. Keeping the
            # full text is lossless, and the annotator (spec_annotator.py)
            # builds its highlight units from the SAME extract_cell_text
            # call, so this excerpt is always found as a match.
            description = cells[1] if len(cells) > 1 else ""
            is_template_example = bool(
                _TEMPLATE_EXAMPLE_UPSTREAM_RE.match(upstream)
                or _TEMPLATE_EXAMPLE_DESCRIPTION_RE.match(description)
            )

            rows.append(RequirementRow(
                req_id=match.group(0).strip(),
                description=description,
                upstream=upstream,
                section=section,
                traced=traced,
                table_index=ti,
                row_index=ri,
                explicit_na=explicit_na,
                is_template_example=is_template_example,
            ))

    return rows


def _normalize_ws_lower(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip().lower()


def check_dreaded_event_associations(source_path) -> List["EvidenceFinding"]:
    """
    R43: "For Menace-Aggression ER identified, there must be at least one
    requirement of constraint associated." — every row of a "dreaded
    event" table that HAS an explicit "Associated requirements" column
    must have that cell filled in; a dreaded event with no associated
    requirement is processed nowhere in the specification.

    Structural (docx table) check, mirroring extract_requirement_rows:
    only tables whose header names BOTH a dreaded-event column and an
    "associated requirement(s)" column qualify. The real CTS spec has
    several differently-shaped "dreaded event" tables — a plain
    "Reference | Definition" list, a customer-impact matrix, an FMEA
    quantitative table — none of which have this column at all, and must
    never be checked against a column that doesn't exist for them.

    Returns [] for non-DOCX inputs or if the file cannot be parsed —
    same fallback contract as extract_requirement_rows.
    """
    findings: List[EvidenceFinding] = []
    try:
        from docx import Document
        from app.qa.retrieval import extract_cell_text
    except Exception:
        return findings

    path_str = str(source_path)
    if not path_str.lower().endswith(".docx"):
        return findings
    try:
        doc = Document(path_str)
    except Exception:
        return findings

    r43 = get_rule_by_id("R43")
    r43_text = r43.text if r43 else (
        "For Menace-Aggression ER identified, there must be at least one "
        "requirement of constraint associated."
    )
    found_table = False
    violations = 0

    for ti, table in enumerate(doc.tables):
        if not table.rows:
            continue
        header = [_normalize_ws_lower(c.text) for c in table.rows[0].cells]
        event_idx = assoc_idx = None
        for ci, h in enumerate(header):
            if "dreaded event" in h:
                event_idx = ci
            if "associated requirement" in h:
                assoc_idx = ci
        if event_idx is None or assoc_idx is None:
            continue
        found_table = True

        for ri, row in enumerate(table.rows):
            if ri == 0:
                continue
            cells = [extract_cell_text(c).strip() for c in row.cells]
            if max(event_idx, assoc_idx) >= len(cells):
                continue
            event = cells[event_idx]
            if not event:
                continue
            if not cells[assoc_idx]:
                violations += 1
                findings.append(EvidenceFinding(
                    check="I_EXTENDED_WG_RULES", severity="warning",
                    section="DEMONSTRATION OF COMPLIANCE WITH REQUIREMENTS",
                    rule_id="R43",
                    message=f"Dreaded event '{event[:80]}' has no associated requirement (R43 violation).",
                    source_rule=f"R43: {r43_text}",
                    source_doc="writing_guide",
                    user_excerpt=event[:200],
                    user_location=f"Dreaded events table (table {ti + 1}), row {ri}",
                    why="R43 requires every identified dreaded event to be covered by at least one constraint requirement — an event with none is processed nowhere in the specification.",
                    fix_suggestion=f"Add at least one associated requirement reference for the dreaded event '{event[:80]}'.",
                ))

    if not found_table:
        findings.append(EvidenceFinding(
            check="I_EXTENDED_WG_RULES", severity="info",
            section="DEMONSTRATION OF COMPLIANCE WITH REQUIREMENTS",
            rule_id="R43",
            message="No dreaded-events table with an 'Associated requirements' column found — R43 not applicable.",
            source_rule=f"R43: {r43_text}",
            source_doc="writing_guide", user_excerpt="", user_location="Entire document",
            why="R43 only applies when the document has a dreaded-events table listing an associated requirement per event.",
        ))
    elif violations == 0:
        findings.append(EvidenceFinding(
            check="I_EXTENDED_WG_RULES", severity="pass",
            section="DEMONSTRATION OF COMPLIANCE WITH REQUIREMENTS",
            rule_id="R43",
            message="All identified dreaded events have at least one associated requirement (R43 compliant).",
            source_rule=f"R43: {r43_text}",
            source_doc="writing_guide", user_excerpt="", user_location="Dreaded events table",
            why="R43 requires every dreaded event to be covered by at least one constraint requirement.",
        ))
    return findings


# ── User document analysis helpers ────────────────────────────────

def _detect_user_sections(text: str) -> List[Tuple[str, int]]:
    """
    Detect section headings in the user's document text.
    Returns list of (section_name, line_number) in order of appearance.
    """
    lines = text.split("\n")
    sections: List[Tuple[str, int]] = []
    seen = set()

    allcaps_re = re.compile(r"^([A-Z][A-Z0-9\s/()\-&,:;.\u2013\u2014]{2,})$")
    titlecase_re = re.compile(r"^([A-Z][A-Za-z]+(?:\s+(?:[A-Z][A-Za-z]+|[a-z]+)){0,5})$")
    numbered_re = re.compile(r"^\d+(?:\.\d+)*\.?\s+([A-Z][A-Za-z\s/()\-&,:;.]{2,})$")

    cts_keywords = {
        "purpose", "scope", "system", "development", "context", "general",
        "description", "roles", "physical", "architecture", "diversity",
        "quoted", "documents", "reference", "applicable", "terminology",
        "glossary", "acronyms", "requirements", "functional", "performance",
        "external", "interfaces", "operational", "mission", "profile",
        "lifetime", "ergonomics", "human", "factors", "rams", "safety",
        "maintainability", "product", "quality", "constraint", "design",
        "manufacturing", "environment", "conditions", "integration",
        "validation", "demonstration", "compliance", "traceability",
        "configuration", "network", "electrical", "mechanical", "machine",
        "weight", "physical", "withdrawal", "flexibility", "extension",
        "transportability", "storage", "packaging", "protection", "hostility",
        "resources", "reserve", "capacity", "document",
    }

    for i, line in enumerate(lines):
        stripped = line.strip()
        if not stripped or len(stripped) > 120:
            continue
        # Skip numbered list items
        if re.match(r"^\d+\.\s+[A-Z][a-z]", stripped):
            continue

        # ALLCAPS
        m = allcaps_re.match(stripped)
        if m:
            name = m.group(1).strip().rstrip(":")
            letters = [c for c in name if c.isalpha()]
            if letters and len(name) >= 3 and name not in seen:
                upper_ratio = sum(1 for c in letters if c.isupper()) / len(letters)
                if upper_ratio >= 0.8:
                    sections.append((name, i))
                    seen.add(name)
                    continue

        # Title Case (only if CTS keyword present)
        m = titlecase_re.match(stripped)
        if m:
            name = m.group(1).strip().rstrip(":")
            if len(name) >= 3 and name not in seen:
                words = name.lower().split()
                if any(w in cts_keywords for w in words):
                    sections.append((name, i))
                    seen.add(name)
                    continue

        # Numbered headings
        m = numbered_re.match(stripped)
        if m:
            name = m.group(1).strip()
            if len(name) >= 3 and name not in seen:
                sections.append((name, i))
                seen.add(name)
                continue

    return sections


def _section_matches(required: str, found_sections: List[str]) -> Optional[str]:
    """Check if a required section matches any found section. Returns the matched section or None.

    Matching priority:
      1. Exact (case-insensitive) match
      2. Found section contains the required section as a distinct phrase
         (e.g. 'EXTERNAL INTERFACES REQUIREMENTS' matches 'EXTERNAL INTERFACES REQUIREMENTS')
      3. Word overlap >= 60% (but NOT for short names like 'SCOPE' to avoid false matches)
    """
    req_lower = required.lower().strip()
    req_words = set(req_lower.split())

    # Pass 1: exact match, checked across ALL headings BEFORE any partial/
    # substring fallback below. A short required name (e.g. "REQUIREMENTS")
    # can legitimately appear as a whole word inside an earlier, unrelated
    # heading (e.g. "UPSTREAM REQUIREMENTS", a subsection of REFERENCE
    # DOCUMENTS) — returning on that first partial hit, before ever reaching
    # the section's own real, exact heading later in the document, was
    # confirmed during a 2026 audit to report the wrong section as evidence.
    for found in found_sections:
        if found.lower().strip() == req_lower:
            return found

    best_match = None
    best_score = 0.0

    for found in found_sections:
        f_lower = found.lower().strip()
        # Skip title/meta lines that are clearly not section headings
        if f_lower in ("requirements document", "of the alarm siren unit", "module"):
            continue
        f_words = set(f_lower.split())
        # For short required names (1-2 words), match as a whole word/phrase
        # anywhere in the heading — e.g. required 'ERGONOMICS' must match a
        # real heading like '6.4.3 ERGONOMICS AND HUMAN FACTORS'. A plain
        # substring+length-cap check used to reject this because the extra
        # words ('AND HUMAN FACTORS') pushed the heading past the cap, even
        # though `found_sections` only ever contains genuine detected
        # headings (never arbitrary prose), so a word-boundary match here
        # can't produce a stray mid-sentence false positive.
        if len(req_words) <= 2:
            # Exact match already handled by Pass 1 above.
            if re.search(r"\b" + re.escape(req_lower) + r"\b", f_lower):
                return found
            continue
        # For longer names, use word overlap
        overlap = req_words & f_words
        if len(req_words) > 0:
            score = len(overlap) / len(req_words)
            if score >= 0.6 and score > best_score:
                best_score = score
                best_match = found
    return best_match


def _find_excerpt(text: str, pattern: str, context_chars: int = 100) -> str:
    """Find a pattern in text and return a surrounding excerpt."""
    m = re.search(pattern, text, re.IGNORECASE)
    if not m:
        return ""
    start = max(0, m.start() - context_chars)
    end = min(len(text), m.end() + context_chars)
    excerpt = text[start:end].replace("\n", " ").strip()
    if start > 0:
        excerpt = "..." + excerpt
    if end < len(text):
        excerpt = excerpt + "..."
    return excerpt


def _find_line_excerpt(text: str, pattern: str, max_len: int = 200) -> str:
    """
    Find `pattern` and return the WHOLE LINE it appears on — not a
    fixed-radius character window around it (see _find_excerpt).

    A declaration table ("[Mark] | Reference | Version | Title") is
    flattened one row per line, and each row is typically far shorter
    than a 100-character radius — so _find_excerpt's window almost always
    pulls in the NEXT row too. The excerpt then describes two physically
    separate table rows glued together, for which the real document has
    no single matching run of text, so it can never be found and
    highlighted (confirmed: 14 of 16 R17 standards findings on the real
    ASU spec silently failed to highlight anything for exactly this
    reason). Returning just the one containing line keeps the excerpt
    confined to a single real row/paragraph.
    """
    try:
        regex = re.compile(pattern, re.IGNORECASE)
    except re.error:
        return ""
    for line in text.split("\n"):
        if regex.search(line):
            line = line.strip()
            return line[:max_len] + ("..." if len(line) > max_len else "")
    return ""


def _locate_pattern(user_text: str, pattern: str, limit: int = 3) -> str:
    """
    Build a PRECISE, human-readable location for every match of `pattern`:
    the enclosing section heading plus the line number, e.g.
    "PROTECTION AGAINST HOSTILITY (line 1523)".

    Findings used to report a bare count ("1 occurrences") as their
    location, which tells the engineer nothing about where to look. Any
    check that detects a textual pattern should use this instead.
    """
    lines = user_text.split("\n")
    sections = _detect_user_sections(user_text)
    try:
        regex = re.compile(pattern, re.IGNORECASE)
    except re.error:
        return ""

    hits: List[str] = []
    for i, line in enumerate(lines):
        if not regex.search(line):
            continue
        section_name = ""
        for name, sec_line in sections:
            if sec_line <= i:
                section_name = name
            else:
                break
        hits.append(
            f"{section_name} (line {i + 1})" if section_name else f"line {i + 1}"
        )

    if not hits:
        return ""
    if len(hits) <= limit:
        return " ; ".join(hits)
    return " ; ".join(hits[:limit]) + f" … (+{len(hits) - limit} more)"


# ── Requirement patterns (from the template + writing guide) ──────
# R22/PCIEE: Requirement IDs follow a REF-/APP-/GEN- prefix followed by
# dash-separated segments and ending in a number.
#
# Real specs are typed by hand, so the separators are inconsistent. All of
# these occur verbatim in the ASU spec and denote valid requirements:
#     REF-ASU-CD-EXIFUNC-001        (clean)
#     REF- ASU-CD-EXIFUNC-023       (space after the prefix dash)
#     REF-ASU-CD- EXINTER-0001      (space on ONE side only)
#     REF-ASU-CD- EXINTER -0005     (spaces on BOTH sides)
#     REF-ASU-CD- -CONN-0002        (doubled dash)
#     APP-ASU-CD-SdF-0001           (mixed-case segment)
#     REF-SIR-CD ESSAI-0002         (separator is a SPACE, no dash at all)
# Rather than special-casing each typo, the separator itself tolerates
# surrounding whitespace and repeated dashes. A whitespace-only separator
# is allowed too, but only when the following segment is 2+ upper-case
# alphanumerics — otherwise "REF-ASU this is ... -5" in ordinary prose
# would be swallowed as an ID. Requiring at least one intermediate segment
# AND a trailing number keeps it from drifting to an unrelated figure.
# Verified against the full ASU spec: 300 unique matches, zero false
# positives (the only mixed-case hits are the real REF-ASU-CD-Safety-000N).
_REQ_ID_SEP = r"(?:\s*[-_](?:\s*[-_])*\s*|\s+(?=[A-Z0-9]{2,}))"
REQ_ID_RE = re.compile(
    r"\b(?:REF|APP|GEN)"
    r"(?:" + _REQ_ID_SEP + r"[A-Za-z0-9]+)+"
    + _REQ_ID_SEP + r"\d+"
)
SHALL_RE = re.compile(r"\bshall\b", re.IGNORECASE)
# R23: prohibited subjective words
SUBJECTIVE_WORDS_RE = re.compile(
    r"\b(certain|various|some|several|little|much|often|almost|sometimes|etc\.?)\b",
    re.IGNORECASE,
)
# Template placeholders
PLACEHOLDER_RE = re.compile(r"<<[^>]*>>")
TBD_RE = re.compile(r"\b(TBD|TBC|TODO|XXX)\b", re.IGNORECASE)
COMPONENT_VAR_RE = re.compile(
    r"<(component name|part name|Part name|name of the Model|reference|PSP|stakeholder|project name)>"
)
# Traceability indicators
TRACE_PATTERNS = [
    re.compile(r"\binput\s+requirement\b", re.IGNORECASE),
    re.compile(r"\bupstream\s+requirement\b", re.IGNORECASE),
    re.compile(r"\bderived\s+from\b", re.IGNORECASE),
    re.compile(r"\btraced?\s+(?:to|from)\b", re.IGNORECASE),
    re.compile(r"\bN/A\b"),
    re.compile(r"\bVF_\d{2,6}\b"),
    re.compile(r"\bCS\.\d{4,6}\b"),
    re.compile(r"\bISO\s*\d{4,6}", re.IGNORECASE),
    re.compile(r"\[([A-Z]{2,}[_\s][A-Z]{2,}[_\d]*)\]"),
]


def _has_traceability(line: str) -> bool:
    for pat in TRACE_PATTERNS:
        if pat.search(line):
            return True
    return False


# ── Check A: Section coverage ─────────────────────────────────────
def check_section_coverage(
    user_text: str,
    rules: ExtractedRules,
) -> List[EvidenceFinding]:
    """Check that all mandatory sections from the template are present in the user doc."""
    findings: List[EvidenceFinding] = []
    user_sections = [s[0] for s in _detect_user_sections(user_text)]

    for sec_rule in rules.mandatory_sections:
        if sec_rule.level != 1:
            continue
        matched = _section_matches(sec_rule.name, user_sections)
        if matched:
            findings.append(EvidenceFinding(
                check="A_SECTION_COVERAGE",
                severity="pass",
                section=matched,
                rule_id="TEMPLATE",
                message=f"Mandatory section '{sec_rule.name}' is present (matched as '{matched}').",
                source_rule=f"Template standard plan requires section: {sec_rule.name}",
                source_doc="template",
                user_excerpt=matched,
                user_location=f"Section heading: '{matched}'",
                why=f"This section is part of the Stellantis CTS standard plan (template position #{sec_rule.order + 1}). Its presence ensures the specification covers this required aspect.",
            ))
        else:
            findings.append(EvidenceFinding(
                check="A_SECTION_COVERAGE",
                severity="error",
                section=sec_rule.name,
                rule_id="TEMPLATE",
                message=f"Mandatory section '{sec_rule.name}' is MISSING from the document.",
                source_rule=f"Template standard plan requires section: {sec_rule.name} (position #{sec_rule.order + 1})",
                source_doc="template",
                user_excerpt="",
                user_location="NOT FOUND",
                why=f"The Stellantis CTS template mandates this section. Its absence means the specification is incomplete and will not pass governance review. Without '{sec_rule.name}', critical information may be undocumented.",
                fix_suggestion=f"Add a '{sec_rule.name}' section to your document following the template structure.",
            ))

    # Recommended sections
    for rec_sec in rules.recommended_sections:
        matched = _section_matches(rec_sec, user_sections)
        if not matched:
            findings.append(EvidenceFinding(
                check="A_SECTION_COVERAGE",
                severity="warning",
                section=rec_sec,
                rule_id="WRITING_GUIDE",
                message=f"Recommended section '{rec_sec}' is not found in the document.",
                source_rule=f"Writing guide recommends section: {rec_sec}",
                source_doc="writing_guide",
                user_excerpt="",
                user_location="NOT FOUND",
                why="This section is recommended by the Stellantis writing guide. Its absence is not a compliance failure but may reduce the specification's completeness.",
                fix_suggestion=f"Consider adding a '{rec_sec}' section for a more complete specification.",
            ))

    return findings


# ── Check B: Section order ────────────────────────────────────────
def check_section_order(
    user_text: str,
    rules: ExtractedRules,
) -> List[EvidenceFinding]:
    """Check that sections appear in the template's standard-plan order.

    Only tracks sections that match the template's standard plan. Skips
    title/meta lines and sections not in the template to avoid false positives.
    """
    findings: List[EvidenceFinding] = []
    if len(rules.section_order) < 2:
        return findings

    user_sections_with_pos = _detect_user_sections(user_text)

    # Build a mapping: for each user section, find the BEST matching template
    # section (highest word-overlap score) and its position. Only track sections
    # that have a clear template match (score >= 0.6).
    matched_sequence: List[Tuple[str, int, int]] = []  # (user_name, template_order, line)
    for uname, line in user_sections_with_pos:
        uname_lower = uname.lower()
        # Skip title/meta lines
        if uname_lower in ("requirements document", "of the alarm siren unit", "module"):
            continue
        best_j = -1
        best_score = 0.0
        uname_words = set(uname_lower.split())
        for j, tname in enumerate(rules.section_order):
            tname_lower = tname.lower()
            tname_words = set(tname_lower.split())
            if tname_lower == uname_lower:
                best_j = j
                best_score = 1.0
                break
            overlap = uname_words & tname_words
            if uname_words and tname_words:
                # Jaccard-like score: overlap / union
                score = len(overlap) / len(uname_words | tname_words)
                if score > best_score:
                    best_score = score
                    best_j = j
        if best_j >= 0 and best_score >= 0.6:
            matched_sequence.append((uname, best_j, line))

    # Check for out-of-order sections (only among matched template sections).
    # We only flag violations where the gap is significant (>= 3 positions)
    # to avoid noise from minor reorderings and ambiguous matches.
    order_violations = []
    last_expected_order = -1
    for uname, tmpl_order, line in matched_sequence:
        if tmpl_order < last_expected_order and (last_expected_order - tmpl_order) >= 3:
            order_violations.append((uname, rules.section_order[tmpl_order], tmpl_order, last_expected_order, line))
        else:
            last_expected_order = max(last_expected_order, tmpl_order)

    if order_violations:
        for uname, tname, expected, after, line in order_violations[:5]:
            findings.append(EvidenceFinding(
                check="B_SECTION_ORDER",
                severity="warning",
                section=uname,
                rule_id="P06",
                message=f"Section '{uname}' appears out of order (template position #{expected + 1} but appears after position #{after + 1}).",
                source_rule="P06: the standard design applied is standard A10 0310. It is prohibited to delete any paragraph of this standard plan or to add a paragraph following a mandatory paragraph.",
                source_doc="writing_guide",
                user_excerpt=uname,
                user_location=f"Line ~{line + 1}",
                why="The Stellantis standard plan defines a fixed section order. Out-of-order sections make the document harder to review and may cause governance tools to misparse the structure.",
                fix_suggestion=f"Move section '{uname}' to its correct position (#{expected + 1}) in the standard plan.",
            ))
    else:
        findings.append(EvidenceFinding(
            check="B_SECTION_ORDER",
            severity="pass",
            section="",
            rule_id="P06",
            message="All detected sections appear in the correct standard-plan order.",
            source_rule="P06: the standard design applied is standard A10 0310. It is prohibited to delete any paragraph of this standard plan.",
            source_doc="writing_guide",
            user_excerpt="",
            user_location="All sections",
            why="Section ordering compliance ensures the document follows the Stellantis standard plan.",
        ))

    return findings


# ── Check C: Placeholder residue ──────────────────────────────────
def check_placeholder_residue(
    user_text: str,
    rules: ExtractedRules,
) -> List[EvidenceFinding]:
    """Check for unfilled template placeholders (<<...>>, <component name>, TBD)."""
    findings: List[EvidenceFinding] = []

    # <<...>> placeholders
    placeholders = PLACEHOLDER_RE.findall(user_text)
    if placeholders:
        sample = placeholders[:5]
        findings.append(EvidenceFinding(
            check="C_PLACEHOLDER_RESIDUE",
            severity="warning",
            section="",
            rule_id="TEMPLATE",
            message=f"{len(placeholders)} template placeholders (<<...>>) remaining unfilled. Examples: {', '.join(sample[:3])}",
            source_rule="Template: 'Writing instructions are PRINTED IN RED, delete them before submitting the document for revision'",
            source_doc="template",
            # ONE literal occurrence, not several joined with "; " — a
            # joined multi-sample string spans several physically separate
            # locations, which no single highlightable unit in the real
            # document ever contains, so it silently fails to highlight
            # anything (confirmed: this exact join was one of only 2
            # error/warning findings, out of 22, that matched no unit at
            # all in a full sweep of the real ASU spec).
            user_excerpt=sample[0] if sample else "",
            user_location=_locate_pattern(user_text, r"<<[^>]*>>")
                          or f"{len(placeholders)} occurrences throughout document",
            why="Template placeholders like <<...>> are unfilled fields from the CTS template. They must be replaced with real values before submission. The template explicitly instructs to delete writing instructions before revision.",
            fix_suggestion="Replace all <<...>> placeholders with actual content or remove the instruction text.",
        ))

    # <component name> etc.
    component_vars = COMPONENT_VAR_RE.findall(user_text)
    if component_vars:
        findings.append(EvidenceFinding(
            check="C_PLACEHOLDER_RESIDUE",
            severity="warning",
            section="",
            rule_id="TEMPLATE",
            message=f"{len(component_vars)} unfilled template variables (<component name>, <part name>, etc.) remaining.",
            source_rule="Template uses <component name>, <part name>, <reference> as placeholders to be replaced with actual values.",
            source_doc="template",
            user_excerpt=", ".join(f"<{v}>" for v in component_vars[:5]),
            user_location=_locate_pattern(user_text, COMPONENT_VAR_RE.pattern)
                          or f"{len(component_vars)} occurrences",
            why="Template variables like '<component name>' must be replaced with the actual part/component name. Leaving them unfilled makes the specification ambiguous.",
            fix_suggestion="Replace all <...> template variables with the actual component/part names and references.",
        ))

    # TBD/TBC/TODO/XXX
    tbds = TBD_RE.findall(user_text)
    if tbds:
        findings.append(EvidenceFinding(
            check="C_PLACEHOLDER_RESIDUE",
            severity="warning",
            section="",
            rule_id="TEMPLATE",
            message=f"{len(tbds)} TBD/TBC/TODO/XXX markers found — these should be resolved before submission.",
            source_rule="Template: all values must be finalized before release. TBD markers signal pending decisions.",
            source_doc="template",
            user_excerpt=", ".join(set(tbds[:5])),
            user_location=_locate_pattern(user_text, r"\b(?:TBD|TBC|TODO|XXX)\b")
                          or f"{len(tbds)} occurrences",
            why="TBD/TBC/TODO markers signal decisions or data that are still pending. In an industrial specification, all values must be finalized before release.",
            fix_suggestion="Resolve all TBD/TBC/TODO markers with final values.",
        ))

    # Template instruction sentences left in the document with the <<>>
    # markers removed but the red instruction text not deleted.
    # Uses rules.template_instructions extracted from the template source doc.
    leftover_instructions: List[str] = []
    if rules.template_instructions:
        # Remove regions already counted as <<...>> placeholders so we only
        # catch instructions whose angle-bracket markers were deleted.
        stripped_text = PLACEHOLDER_RE.sub(" ", user_text)
        norm_text = re.sub(r"\s+", " ", stripped_text).lower()
        seen_instructions = set()
        for ti in rules.template_instructions:
            inner = ti.placeholder.strip()
            if inner.startswith("<<") and inner.endswith(">>"):
                inner = inner[2:-2]
            inner = re.sub(r"\s+", " ", inner).strip().lower()
            # Only long, template-specific sentences (short fragments would
            # match legitimate content by coincidence).
            if len(inner) < 40 or inner in seen_instructions:
                continue
            seen_instructions.add(inner)
            if inner in norm_text:
                leftover_instructions.append(inner)

    if leftover_instructions:
        sample_instr = [i[:80] + ("…" if len(i) > 80 else "") for i in leftover_instructions[:3]]
        findings.append(EvidenceFinding(
            check="C_PLACEHOLDER_RESIDUE",
            severity="warning",
            section="",
            rule_id="TEMPLATE",
            message=(
                f"{len(leftover_instructions)} template instruction sentence(s) left in the "
                f"document (the <<>> markers were removed but the instruction text was not deleted)."
            ),
            source_rule="Template: 'Writing instructions are PRINTED IN RED, delete them before submitting the document for revision'",
            source_doc="template",
            user_excerpt="; ".join(sample_instr),
            user_location=f"{len(leftover_instructions)} instruction(s) throughout document",
            why="Instruction text copied from the CTS template must be deleted, not just stripped of its <<>> markers — it is guidance for the author, not specification content.",
            fix_suggestion="Delete the remaining template instruction sentences from the document.",
        ))

    if not placeholders and not component_vars and not tbds and not leftover_instructions:
        findings.append(EvidenceFinding(
            check="C_PLACEHOLDER_RESIDUE",
            severity="pass",
            section="",
            rule_id="TEMPLATE",
            message="No template placeholders or TBD markers found — document is clean of template artifacts.",
            source_rule="Template: 'Writing instructions are PRINTED IN RED, delete them before submitting'",
            source_doc="template",
            user_excerpt="",
            user_location="Entire document",
            why="A clean document with no residual placeholders is ready for review.",
        ))

    return findings


# ── Check D: Requirement format (R22 — 3-column table) ────────────
def check_requirement_format(
    user_text: str,
    rules: ExtractedRules,
) -> List[EvidenceFinding]:
    """Check R22: requirements presented as 3-column tables (ID, description, upstream req)."""
    findings: List[EvidenceFinding] = []
    r22 = get_rule_by_id("R22")

    # Detect table-like structures (pipe-separated rows from DOCX table extraction)
    table_rows = [l for l in user_text.split("\n") if "|" in l and l.count("|") >= 2]
    shall_lines = [l for l in user_text.split("\n") if SHALL_RE.search(l) and len(l.strip()) > 20]

    if shall_lines:
        # Check if requirements are in table format (heuristic: pipe-separated rows near shall)
        has_table_format = len(table_rows) > 10  # substantial table content
        if has_table_format:
            findings.append(EvidenceFinding(
                check="D_REQUIREMENT_FORMAT",
                severity="pass",
                section="REQUIREMENTS",
                rule_id="R22",
                message=f"Requirements appear to be presented in table format ({len(table_rows)} table rows detected).",
                source_rule=f"R22: {r22.text if r22 else 'Requirements are presented in the form of a 3 columns table, containing: Requirement number, The title of the requirement, Number(s) of the upstream requirement(s).'}",
                source_doc="writing_guide",
                user_excerpt=table_rows[0][:200] if table_rows else "",
                user_location=f"{len(table_rows)} table rows",
                why="R22 requires requirements to be in a 3-column table (ID, description, upstream requirement). Table format ensures structured, traceable requirements.",
            ))
        else:
            findings.append(EvidenceFinding(
                check="D_REQUIREMENT_FORMAT",
                severity="warning",
                section="REQUIREMENTS",
                rule_id="R22",
                message=f"Requirements using 'shall' found ({len(shall_lines)}) but limited table structure detected ({len(table_rows)} table rows). R22 requires 3-column table format.",
                source_rule=f"R22: {r22.text if r22 else 'Requirements are presented in the form of a 3 columns table, containing: Requirement number, The title of the requirement, Number(s) of the upstream requirement(s).'}",
                source_doc="writing_guide",
                user_excerpt=shall_lines[0][:200] if shall_lines else "",
                user_location=f"{len(shall_lines)} shall-statements",
                why="R22 requires requirements to be presented in a 3-column table (ID, description, upstream requirement). Without table format, traceability and structure are harder to maintain.",
                fix_suggestion="Present requirements in a 3-column table: Requirement ID | Description | Upstream Requirement.",
            ))

    return findings


# ── Check E: Requirement language (R23 — shall, no subjective words) ─
def check_requirement_language(
    user_text: str,
    rules: ExtractedRules,
) -> List[EvidenceFinding]:
    """Check R23: 'shall' for mandatory requirements, no subjective words."""
    findings: List[EvidenceFinding] = []
    r23 = get_rule_by_id("R23")

    shall_count = len(SHALL_RE.findall(user_text))
    should_count = len(re.findall(r"\bshould\b", user_text, re.IGNORECASE))
    may_count = len(re.findall(r"\bmay\b", user_text, re.IGNORECASE))

    if shall_count > 0:
        findings.append(EvidenceFinding(
            check="E_REQUIREMENT_LANGUAGE",
            severity="pass",
            section="REQUIREMENTS",
            rule_id="R23",
            message=f"Document uses 'shall' language ({shall_count} occurrences) for mandatory requirements.",
            source_rule=f"R23: {r23.text if r23 else 'The verbs are always at the present tense. The verb have to is prohibited.'}",
            source_doc="writing_guide",
            user_excerpt=_find_excerpt(user_text, r"\bshall\b"),
            user_location=f"{shall_count} occurrences",
            why="'Shall' is the standard mandatory requirement language in engineering specifications (ISO/IEC Directives Part 2). It indicates binding requirements.",
        ))
    else:
        findings.append(EvidenceFinding(
            check="E_REQUIREMENT_LANGUAGE",
            severity="error",
            section="REQUIREMENTS",
            rule_id="R23",
            message="No 'shall' language found — requirements must use 'shall' for mandatory statements.",
            source_rule=f"R23: {r23.text if r23 else 'Requirements must use mandatory language (shall).'}",
            source_doc="writing_guide",
            user_excerpt="",
            user_location="NOT FOUND",
            why="'Shall' indicates a mandatory requirement. Without it, suppliers may treat critical requirements as optional. The Stellantis writing guide and ISO/IEC Directives require 'shall' for all binding requirements.",
            fix_suggestion="Use 'shall' for all mandatory requirements instead of 'should' or 'may'.",
        ))

    # Check for subjective words (R23 prohibits them)
    subjective_matches = SUBJECTIVE_WORDS_RE.findall(user_text)
    if subjective_matches and shall_count > 0:
        # Filter out subjective words that appear in non-requirement context
        # (only flag if they appear near 'shall' lines). Also excludes the
        # CTS template's own unedited example row ("...| The system shall… |
        # Nothing in this field") — confirmed present in a real ASU spec —
        # whose "PSA_Comments@{{if you want to add some information}}"
        # instruction text otherwise gets misread as a subjective-word
        # violation in a real requirement, when it isn't a requirement at all.
        shall_lines = [
            l for l in user_text.split("\n")
            if SHALL_RE.search(l) and "nothing in this field" not in l.lower()
        ]
        subjective_in_reqs = []
        for line in shall_lines:
            sm = SUBJECTIVE_WORDS_RE.findall(line)
            if sm:
                subjective_in_reqs.append((line[:150], sm))

        if subjective_in_reqs:
            findings.append(EvidenceFinding(
                check="E_REQUIREMENT_LANGUAGE",
                severity="warning",
                section="REQUIREMENTS",
                rule_id="R23",
                message=f"Subjective words found in requirement statements: {subjective_in_reqs[0][1]}. R23 prohibits subjective adjectives/adverbs.",
                source_rule="R23: The following adjectives and adverbs are prohibited, because subjective: certain, various, some, several, little, much, often, almost, sometimes, etc.",
                source_doc="writing_guide",
                user_excerpt=subjective_in_reqs[0][0],
                user_location="In requirement statements",
                why="Subjective words like 'various', 'several', 'often' make requirements ambiguous and unverifiable. Each requirement must have a single, clear interpretation.",
                fix_suggestion="Replace subjective words with specific, quantified values (e.g. '3 interfaces' instead of 'several interfaces').",
            ))

    # Check should/may usage (informational)
    if shall_count == 0 and (should_count > 5 or may_count > 5):
        findings.append(EvidenceFinding(
            check="E_REQUIREMENT_LANGUAGE",
            severity="warning",
            section="REQUIREMENTS",
            rule_id="R23",
            message=f"Document uses 'should' ({should_count}x) and 'may' ({may_count}x) but no 'shall'. Non-mandatory language creates ambiguity.",
            source_rule="R23: 'shall' is mandatory; 'should' is a recommendation; 'may' is optional. Using non-mandatory language creates ambiguity.",
            source_doc="writing_guide",
            user_excerpt="",
            user_location=f"should: {should_count}x, may: {may_count}x",
            why="In engineering specifications, 'should' is a recommendation and 'may' is optional. Suppliers may treat non-mandatory requirements as optional, creating risk.",
            fix_suggestion="Replace 'should' with 'shall' for all binding requirements.",
        ))

    return findings


# ── Check F: Requirement IDs (R20/PCIEE) ─────────────────────────
def check_requirement_ids(
    user_text: str,
    rules: ExtractedRules,
) -> List[EvidenceFinding]:
    """Check R20/PCIEE: each requirement has a unique ID (REF-/APP-/GEN- prefix)."""
    findings: List[EvidenceFinding] = []
    r20 = get_rule_by_id("R20")

    all_ids = set(REQ_ID_RE.findall(user_text))
    shall_lines = [l for l in user_text.split("\n") if SHALL_RE.search(l) and len(l.strip()) > 20]

    if not shall_lines:
        return findings  # no requirements → handled by check E

    unique_id_count = len(all_ids)

    # Count shall-lines that have an ID nearby (inline or ±10 lines)
    lines = user_text.split("\n")
    req_with_id = 0
    for i, line in enumerate(lines):
        if SHALL_RE.search(line) and len(line.strip()) > 20:
            if REQ_ID_RE.search(line):
                req_with_id += 1
            else:
                start = max(0, i - 10)
                end = min(len(lines), i + 11)
                context = " ".join(lines[start:end])
                if REQ_ID_RE.search(context):
                    req_with_id += 1

    if req_with_id == 0 and unique_id_count == 0:
        findings.append(EvidenceFinding(
            check="F_REQUIREMENT_IDS",
            severity="warning",
            section="REQUIREMENTS",
            rule_id="R20",
            message=f"Requirements found ({len(shall_lines)} 'shall' statements) but NONE have formal requirement IDs.",
            source_rule=f"R20: {r20.text if r20 else 'The identification of the requirements of a Word document will comply with the PCIEE rule.'} Template: 'It is mandatory to write a Requirement no like: REF-PSP-COMP-001'",
            source_doc="writing_guide",
            user_excerpt=shall_lines[0][:200] if shall_lines else "",
            user_location=f"{len(shall_lines)} requirements without IDs",
            why="Formal requirement IDs (REF-/APP-/GEN- prefix) enable unambiguous traceability from specification through design, testing, and verification. Without IDs, engineers cannot uniquely reference requirements in test plans or compliance audits.",
            fix_suggestion="Assign unique IDs to each requirement using the format: REF-PSP-COMP-001 (or APP-/GEN- prefix as appropriate).",
        ))
    elif unique_id_count > 0:
        findings.append(EvidenceFinding(
            check="F_REQUIREMENT_IDS",
            severity="pass",
            section="REQUIREMENTS",
            rule_id="R20",
            message=f"Requirements have formal IDs ({unique_id_count} unique IDs found, ~{req_with_id} requirements with IDs near 'shall' statements).",
            source_rule=f"R20: {r20.text if r20 else 'Requirements must comply with the PCIEE identification rule.'} Template: 'REF-PSP-COMP-001'",
            source_doc="writing_guide",
            user_excerpt=list(all_ids)[0] if all_ids else "",
            user_location=f"{unique_id_count} unique IDs",
            why="Formal requirement IDs enable traceability from specification through design, testing, and verification.",
        ))

    return findings


# ── Check G: Traceability (R22 — upstream requirement column) ─────
_MAX_ID_LOOKBACK = 20


def _find_owning_req_id(lines: List[str], i: int) -> Optional[str]:
    """
    Find the requirement ID that "owns" line i.

    A requirement's ID sits on the FIRST line of its table row/cell (e.g.
    "REF-ASU-CD-EXINTER-0007(0) | In the standby operating situation..."),
    while its individual "shall" bullets are continuation lines below it
    with no ID of their own (flattened one paragraph per line). So: if
    this line has no ID, walk backward to the nearest preceding line that
    does — that is this requirement's real name, not an arbitrary line
    number.
    """
    m = REQ_ID_RE.search(lines[i])
    if m:
        return m.group(0).strip()
    for j in range(i - 1, max(-1, i - _MAX_ID_LOOKBACK), -1):
        m = REQ_ID_RE.search(lines[j])
        if m:
            return m.group(0).strip()
    return None


def _locate_untraced_requirements(
    lines: List[str],
    shall_entries: List[Tuple[int, str]],
    traced_flags: List[bool],
    user_sections: List[Tuple[str, int]],
    limit: int = 200,
) -> List[Dict]:
    """
    Build the list of requirements that lack an upstream reference, each
    with an identifier (its REF-/APP-/GEN- ID — found on this line, or on
    the nearest preceding line if this is a continuation bullet within
    the same requirement row — else its line number as a last resort) and
    its placement (the enclosing section heading, or the line number if
    no section was detected above it).
    """
    items: List[Dict] = []
    for (i, line), traced in zip(shall_entries, traced_flags):
        if traced:
            continue
        req_id = _find_owning_req_id(lines, i) or f"Line {i + 1}"

        section_name = ""
        for name, sec_line in user_sections:
            if sec_line <= i:
                section_name = name
            else:
                break
        location = f"{section_name} (line {i + 1})" if section_name else f"Line {i + 1}"

        items.append({
            "id": req_id,
            "location": location,
            "excerpt": line.strip()[:180],
        })
        if len(items) >= limit:
            break
    return items


def _structural_untraced_items(
    req_rows: List["RequirementRow"], limit: int = 400
) -> List[Dict]:
    """Itemize every requirement row whose Input requirement cell is empty."""
    items: List[Dict] = []
    for row in req_rows:
        if row.traced:
            continue
        location = row.section or f"Table {row.table_index + 1}"
        items.append({
            "id": row.req_id,
            "location": f"{location} (table {row.table_index + 1})",
            "excerpt": re.sub(r"\s+", " ", row.description).strip()[:180],
        })
        if len(items) >= limit:
            break
    return items


def check_traceability(
    user_text: str,
    rules: ExtractedRules,
    req_rows: Optional[List["RequirementRow"]] = None,
) -> List[EvidenceFinding]:
    """
    Check R22: each requirement has an upstream requirement reference (or N/A).

    When `req_rows` is supplied (structurally read from the .docx requirement
    tables) the "Input requirement" column is read directly, which is exact.
    Otherwise falls back to a text heuristic for formats where the column
    structure is unavailable (PDF/TXT) — see extract_requirement_rows().
    """
    findings: List[EvidenceFinding] = []
    r22 = get_rule_by_id("R22")

    if req_rows:
        template_example_rows = [r for r in req_rows if r.is_template_example]
        real_rows = [r for r in req_rows if not r.is_template_example]

        total = len(real_rows)
        traced = sum(1 for r in real_rows if r.traced)
        ratio = traced / total if total else 0
        untraced_items = _structural_untraced_items(real_rows)
        n_untraced = total - traced

        example = next((r for r in real_rows if r.traced), None)
        excerpt = (
            f"{example.req_id} → {example.upstream}"[:200] if example else ""
        )

        n_na = sum(1 for r in real_rows if r.explicit_na)
        na_note = f" ({n_na} declared explicitly as 'N/A')" if n_na else ""

        if total == 0:
            severity, message = "info", "No real requirement rows found to check traceability on."
        elif n_untraced == 0:
            severity, message = "pass", (
                f"Traceability complete: all {total} requirements declare an "
                f"upstream requirement (or N/A) in the 'Input requirement' "
                f"column{na_note}."
            )
        elif ratio >= 0.5:
            severity, message = "warning", (
                f"Traceability incomplete: {n_untraced} of {total} requirements "
                f"have an EMPTY 'Input requirement' column "
                f"({round(ratio * 100)}% declared)."
            )
        else:
            # Escalated above "warning": a document where LESS THAN HALF of
            # its requirements are traced is a materially worse risk than
            # one that's "mostly done" — the two used to share one severity,
            # making them visually indistinguishable in the report.
            severity, message = "error", (
                f"Traceability largely missing: {n_untraced} of {total} "
                f"requirements have an EMPTY 'Input requirement' column "
                f"(only {round(ratio * 100)}% declared)."
            )

        if template_example_rows:
            ids = ", ".join(r.req_id for r in template_example_rows[:3])
            findings.append(EvidenceFinding(
                check="G_TRACEABILITY", severity="warning", section="REQUIREMENTS",
                rule_id="R22",
                message=(
                    f"{len(template_example_rows)} requirement row(s) are the CTS "
                    f"template's own unedited example ({ids}) — left in the "
                    f"document instead of being replaced with a real requirement."
                ),
                source_rule="Template: 'Requirement no. (v) | Description of the requirement | "
                            "Input requirement (v)' example row, to be replaced before submission.",
                source_doc="template",
                user_excerpt=f"{template_example_rows[0].req_id} → {template_example_rows[0].description}",
                user_location=f"Table {template_example_rows[0].table_index + 1}, row {template_example_rows[0].row_index + 1}",
                why="The template's example row ('The system shall…' / 'Nothing in this field') is "
                    "writing guidance, not a real requirement — it must be replaced or deleted, not "
                    "counted as a compliant, traced requirement.",
                fix_suggestion="Replace the example row with a real requirement, or delete it if unused.",
            ))

        findings.append(EvidenceFinding(
            check="G_TRACEABILITY",
            severity=severity,
            section="REQUIREMENTS",
            rule_id="R22",
            message=message,
            source_rule="R22: 'Number(s) of the upstream requirement(s) with version, to which the requirement refers. When there is no input requirement, the field is filled with N/A.'",
            source_doc="writing_guide",
            user_excerpt=excerpt,
            user_location=f"{traced}/{total} requirements with a filled 'Input requirement' cell",
            why="Traceability links each requirement to its source (customer spec, regulation, standard). This is critical for change impact analysis and compliance audits. R22 requires the field to be filled with 'N/A' when a requirement genuinely has no upstream source — leaving it empty is not equivalent.",
            fix_suggestion=(
                "Fill the 'Input requirement' column for every requirement listed "
                "below — either with the upstream requirement reference, or with "
                "'N/A' if the requirement deliberately has no upstream source."
            ) if n_untraced else "",
            items=untraced_items,
        ))
        return findings

    lines = user_text.split("\n")
    shall_entries = [
        (i, line) for i, line in enumerate(lines)
        if SHALL_RE.search(line) and len(line.strip()) > 20
    ]
    if not shall_entries:
        return findings

    # A requirement's upstream reference frequently lands on a DIFFERENT
    # physical line than its "shall" statement: a multi-paragraph table
    # cell (e.g. a description with several bullet sub-clauses — "Idle
    # State: ... shall wait...", "Arming State: ...") gets flattened one
    # paragraph per line, while the Input Requirement value is appended
    # to the LAST paragraph's line — several lines below an EARLIER
    # "shall" bullet in that very same row. So every line is checked
    # directly first, then (if that fails) within a ±10-line context
    # window — unconditionally, not only as a last-resort fallback when
    # the whole document found zero direct matches, otherwise genuinely
    # traced sub-bullets get wrongly reported as independent untraced
    # requirements (confirmed on the real ASU spec: REF-ASU-CD-EXIFUNC-002
    # has both a VF_xxx reference and [SSD_AUE], yet its "Idle State"
    # sub-bullet was flagged untraced because those markers sit a few
    # lines below it, outside the single line being checked).
    traced_flags = []
    for i, line in shall_entries:
        if _has_traceability(line):
            traced_flags.append(True)
            continue
        start, end = max(0, i - 10), min(len(lines), i + 11)
        context = " ".join(lines[start:end])
        traced_flags.append(_has_traceability(context))
    req_with_trace = sum(traced_flags)

    shall_lines = [line for _, line in shall_entries]
    trace_ratio = req_with_trace / len(shall_entries) if shall_entries else 0
    user_sections = _detect_user_sections(user_text)
    untraced_items = _locate_untraced_requirements(
        lines, shall_entries, traced_flags, user_sections
    )

    if trace_ratio >= 0.5:
        findings.append(EvidenceFinding(
            check="G_TRACEABILITY",
            severity="pass",
            section="REQUIREMENTS",
            rule_id="R22",
            message=f"Traceability present: {req_with_trace}/{len(shall_lines)} requirements reference upstream requirements ({round(trace_ratio * 100)}%).",
            source_rule=f"R22: 'Number(s) of the upstream requirement(s) with version, to which the requirement refers. When there is no input requirement, the field is filled with N/A.'",
            source_doc="writing_guide",
            user_excerpt=_find_excerpt(user_text, r"\b(input requirement|upstream|N/A|derived from)\b"),
            user_location=f"{req_with_trace}/{len(shall_lines)} requirements",
            why="Traceability links each requirement to its source (customer spec, regulation, standard). This is critical for change impact analysis and compliance audits.",
            # Even when the OVERALL ratio passes, the remaining untraced
            # requirements are still real gaps and must stay visible in the
            # report — previously these `items` were silently dropped
            # whenever the document passed the 50% threshold overall,
            # hiding every individual "no input requirement" requirement
            # in an otherwise mostly-compliant document.
            items=untraced_items,
        ))
    elif trace_ratio > 0:
        findings.append(EvidenceFinding(
            check="G_TRACEABILITY",
            severity="warning",
            section="REQUIREMENTS",
            rule_id="R22",
            message=f"Partial traceability: only {req_with_trace}/{len(shall_lines)} requirements reference upstream requirements ({round(trace_ratio * 100)}%).",
            source_rule="R22: 'Number(s) of the upstream requirement(s) with version, to which the requirement refers. When there is no input requirement, the field is filled with N/A.'",
            source_doc="writing_guide",
            user_excerpt=_find_excerpt(user_text, r"\b(input requirement|upstream|N/A)\b"),
            user_location=f"{req_with_trace}/{len(shall_lines)} requirements",
            why="Each requirement should reference its upstream source. Partial traceability means some requirements cannot be traced to their origin, creating gaps in compliance audits.",
            fix_suggestion="Add upstream requirement references to all requirements. Use 'N/A' for requirements with no upstream source.",
            items=untraced_items,
        ))
    else:
        findings.append(EvidenceFinding(
            check="G_TRACEABILITY",
            severity="warning",
            section="REQUIREMENTS",
            rule_id="R22",
            message=f"No input requirement traceability found. {len(shall_lines)} requirements should reference upstream requirements or mark N/A.",
            source_rule="R22: 'Number(s) of the upstream requirement(s) with version, to which the requirement refers. When there is no input requirement, the field is filled with N/A.'",
            source_doc="writing_guide",
            user_excerpt="",
            user_location="NOT FOUND",
            why="Traceability links each requirement to its source. Without it, change impact analysis and compliance audits cannot be performed. Mark genuinely new requirements as 'N/A'.",
            fix_suggestion="Add an 'Input Requirement' column to each requirement table, referencing the upstream requirement ID or 'N/A'.",
            items=untraced_items,
        ))

    return findings


# ── Check H: Writing guide rules (deterministic subset) ───────────
def check_writing_guide_rules(
    user_text: str,
    rules: ExtractedRules,
) -> List[EvidenceFinding]:
    """Check deterministic writing-guide rules (R05, R09, R11, R12, etc.)."""
    findings: List[EvidenceFinding] = []

    # R09: Revision history / table of updates
    r09 = get_rule_by_id("R09")
    has_revision = bool(re.search(r"table\s+of\s+updates|revision\s+history|update\s+history|version\s+history", user_text, re.IGNORECASE))
    if has_revision:
        findings.append(EvidenceFinding(
            check="H_WRITING_GUIDE_RULES",
            severity="pass",
            section="HISTORY",
            rule_id="R09",
            message="Revision history / table of updates found.",
            source_rule=f"R09: {r09.text if r09 else 'rule for the tracking of the modifications of the RD'}",
            source_doc="writing_guide",
            user_excerpt=_find_excerpt(user_text, r"table\s+of\s+updates|revision\s+history"),
            user_location="Document header area",
            why="A revision history tracks who changed what and when. It is essential for audit trails and configuration management.",
        ))
    else:
        findings.append(EvidenceFinding(
            check="H_WRITING_GUIDE_RULES",
            severity="warning",
            section="HISTORY",
            rule_id="R09",
            message="No table of updates / revision history found.",
            source_rule=f"R09: {r09.text if r09 else 'The tracking of the modifications is conducted through a table. Each line is a valid version.'}",
            source_doc="writing_guide",
            user_excerpt="",
            user_location="NOT FOUND",
            why="A revision history tracks who changed what and when. Without it, there is no formal record of spec evolution, which is essential for audit trails.",
            fix_suggestion="Add a 'Table of updates' section at the beginning of the document listing each version with date, author, and nature of modifications.",
        ))

    # R05: Title identification
    r05 = get_rule_by_id("R05")
    has_title_id = bool(re.search(r"RSP-\d+|REQUIREMENTS DOCUMENT|TECHNICAL SPECIFICATION|CTS|Specification\s+of\s+the", user_text, re.IGNORECASE))
    if has_title_id:
        findings.append(EvidenceFinding(
            check="H_WRITING_GUIDE_RULES",
            severity="pass",
            section="TITLE",
            rule_id="R05",
            message="Document title/identification found.",
            source_rule=f"R05: {r05.text if r05 else 'The title is the full name of the Product, possibly with the associated acronym preceded by RD.'}",
            source_doc="writing_guide",
            user_excerpt=_find_excerpt(user_text, r"REQUIREMENTS DOCUMENT|TECHNICAL SPECIFICATION|Specification", 80),
            user_location="Document title area",
            why="The title identifies the product and specification type. It is required by standard A10 0310.",
        ))
    else:
        findings.append(EvidenceFinding(
            check="H_WRITING_GUIDE_RULES",
            severity="warning",
            section="TITLE",
            rule_id="R05",
            message="No clear document title/identification found.",
            source_rule=f"R05: {r05.text if r05 else 'The title is the full name of the Product.'}",
            source_doc="writing_guide",
            user_excerpt="",
            user_location="NOT FOUND",
            why="The document title must identify the product by its full name and acronym. This is required by standard A10 0310.",
            fix_suggestion="Add a clear title following the format: 'Requirements Document of the [Product Name] ([Acronym]) Module'.",
        ))

    # R07/R08: Writer/approver identification
    has_writer = bool(re.search(r"written\s+by|writter|author|checked\s+by|approved\s+by|writer", user_text, re.IGNORECASE))
    if has_writer:
        findings.append(EvidenceFinding(
            check="H_WRITING_GUIDE_RULES",
            severity="pass",
            section="APPROVAL",
            rule_id="R07",
            message="Writer/approver identification found.",
            source_rule="R07: the writer specified here is necessarily part of the STELLANTIS employees. R08: The auditor is necessarily separate from the writers.",
            source_doc="writing_guide",
            user_excerpt=_find_excerpt(user_text, r"written\s+by|author|checked\s+by|approved\s+by", 80),
            user_location="Document header area",
            why="Identifying the writer, checker, and approver ensures accountability. R07 requires the writer to be a STELLANTIS employee; R08 requires the auditor to be separate from the writers.",
        ))
    else:
        findings.append(EvidenceFinding(
            check="H_WRITING_GUIDE_RULES",
            severity="warning",
            section="APPROVAL",
            rule_id="R07",
            message="No writer/checker/approver identification found.",
            source_rule="R07: the writer specified here is necessarily part of the STELLANTIS employees. R08: The auditor is necessarily separate from the writers.",
            source_doc="writing_guide",
            user_excerpt="",
            user_location="NOT FOUND",
            why="The specification must identify its writer(s), verifier(s), and approver(s). This enables accountability and is required by standard A10 0310.",
            fix_suggestion="Add a 'Written by / Checked by / Approved by' table in the document header.",
        ))

    # R11: UML formalism for diagrams
    r11 = get_rule_by_id("R11")
    has_diagrams = bool(re.search(r"figure\s+<?\d+|diagram|use\s+case|state\s+chart|sequence\s+diagram", user_text, re.IGNORECASE))
    if has_diagrams:
        findings.append(EvidenceFinding(
            check="H_WRITING_GUIDE_RULES",
            severity="info",
            section="DIAGRAMS",
            rule_id="R11",
            message="Diagrams detected in document. Verify they respect UML formalism (R11).",
            source_rule=f"R11: {r11.text if r11 else 'charts types class diagram, use case diagram, state chart diagram, sequence diagram shall respect the UML formalism.'}",
            source_doc="writing_guide",
            user_excerpt=_find_excerpt(user_text, r"figure\s+<?\d+|diagram", 80),
            user_location="Throughout document",
            why="R11 requires all diagrams (class, use case, state chart, sequence) to respect UML formalism. Non-UML diagrams may be misinterpreted by reviewers and tools.",
            fix_suggestion="Verify all diagrams use standard UML notation.",
        ))

    # R12: No requirements in SCOPE section
    r12 = get_rule_by_id("R12")
    user_sections = _detect_user_sections(user_text)
    scope_section_content = ""
    in_scope = False
    for name, line in user_sections:
        if _section_matches("SCOPE", [name]):
            in_scope = True
            continue
        if in_scope and not _section_matches("SCOPE", [name]):
            # Next section after scope
            break
    if in_scope:
        # Extract scope section text
        lines = user_text.split("\n")
        scope_start = None
        scope_end = None
        for name, line in user_sections:
            if _section_matches("SCOPE", [name]) and scope_start is None:
                scope_start = line
            elif scope_start is not None and not _section_matches("SCOPE", [name]):
                scope_end = line
                break
        if scope_start is not None:
            scope_end = scope_end or len(lines)
            scope_section_content = "\n".join(lines[scope_start:scope_end])
            scope_shall_count = len(SHALL_RE.findall(scope_section_content))
            if scope_shall_count > 0:
                findings.append(EvidenceFinding(
                    check="H_WRITING_GUIDE_RULES",
                    severity="warning",
                    section="SCOPE",
                    rule_id="R12",
                    message=f"R12 violation: {scope_shall_count} 'shall' requirement(s) found in SCOPE section. Requirements must NOT be in SCOPE.",
                    source_rule=f"R12: {r12.text if r12 else 'Never insert requirements in this paragraph, but only information to help the reader understand.'}",
                    source_doc="writing_guide",
                    user_excerpt=_find_excerpt(scope_section_content, r"\bshall\b"),
                    user_location="SCOPE section",
                    why="R12 explicitly prohibits inserting requirements in the SCOPE section. SCOPE should only contain information to help the reader understand the system context. Requirements belong in section 5 (REQUIREMENTS).",
                    fix_suggestion="Move any 'shall' requirements from SCOPE to the REQUIREMENTS section (§5).",
                ))
            else:
                findings.append(EvidenceFinding(
                    check="H_WRITING_GUIDE_RULES",
                    severity="pass",
                    section="SCOPE",
                    rule_id="R12",
                    message="SCOPE section contains no requirements (compliant with R12).",
                    source_rule=f"R12: {r12.text if r12 else 'Never insert requirements in this paragraph.'}",
                    source_doc="writing_guide",
                    user_excerpt="",
                    user_location="SCOPE section",
                    why="R12 prohibits requirements in SCOPE. The SCOPE section should only help the reader understand the system context.",
                ))

    # Acronyms check (if ACRONYMS section present)
    if _section_matches("ACRONYMS", [s[0] for s in user_sections]):
        # Real CTS specs write the acronyms list as a table ("ASU | Alarm
        # ASU unit"), not the colon/dash-separated inline style the pattern
        # originally only covered ("ASU: Alarm ASU unit") — confirmed on a
        # real ASU spec whose ACRONYMS section is entirely pipe-delimited,
        # which the colon/dash-only pattern couldn't match at all, so a
        # section full of real definitions was reported as having none.
        acronym_pattern = r"[A-Z]{2,}\s*[:\-—|]\s*[A-Z][a-z]"
        # Scoped to the ACRONYMS section's own text — matching against the
        # WHOLE document let an unrelated match elsewhere (e.g. the cover
        # page's author-initials table) count as "the section has real
        # definitions" even when the section itself was empty, confirmed
        # during a 2026 audit against a real ASU spec.
        acronyms_section_text = _extract_full_section_text(user_text, "ACRONYMS", rules)
        has_acronym_defs = bool(re.search(acronym_pattern, acronyms_section_text or user_text))
        if has_acronym_defs:
            excerpt, location = _find_excerpt_scoped(user_text, "ACRONYMS", acronym_pattern, rules, 80)
            findings.append(EvidenceFinding(
                check="H_WRITING_GUIDE_RULES",
                severity="pass",
                section="ACRONYMS",
                rule_id="WG_ACRONYMS",
                message="Acronyms section contains acronym definitions.",
                source_rule="Writing guide §4.2: 'In this paragraph, we clarify only the abbreviation, in alphabetical order.'",
                source_doc="writing_guide",
                user_excerpt=excerpt,
                user_location=location,
                why="Every acronym used in the specification must be defined once in the ACRONYMS section. Undefined acronyms cause confusion.",
            ))
        else:
            findings.append(EvidenceFinding(
                check="H_WRITING_GUIDE_RULES",
                severity="warning",
                section="ACRONYMS",
                rule_id="WG_ACRONYMS",
                message="ACRONYMS section present but no acronym definitions found.",
                source_rule="Writing guide §4.2: 'In this paragraph, we clarify only the abbreviation, in alphabetical order.'",
                source_doc="writing_guide",
                user_excerpt="",
                user_location="ACRONYMS section",
                why="The ACRONYMS section exists but contains no actual definitions. Every acronym used must be defined here.",
                fix_suggestion="Add acronym definitions in the format: ACRONYM — Full Name (alphabetical order).",
            ))

    # Figure/table numbering
    has_numbered_figures = bool(re.search(r"figure\s+<?\d+|picture\s+\d+|table\s+\d+", user_text, re.IGNORECASE))
    if has_numbered_figures:
        findings.append(EvidenceFinding(
            check="H_WRITING_GUIDE_RULES",
            severity="pass",
            section="FIGURES",
            rule_id="WG_FIGURES",
            message="Numbered figures/tables found — enables cross-referencing.",
            source_rule="Writing guide: numbered figures and tables enable unambiguous cross-referencing ('see Figure 3').",
            source_doc="writing_guide",
            user_excerpt=_find_excerpt(user_text, r"figure\s+<?\d+|table\s+\d+", 80),
            user_location="Throughout document",
            why="Numbered figures and tables enable efficient navigation between text and visuals.",
        ))
    else:
        findings.append(EvidenceFinding(
            check="H_WRITING_GUIDE_RULES",
            severity="info",
            section="FIGURES",
            rule_id="WG_FIGURES",
            message="No numbered figures or tables found.",
            source_rule="Writing guide: figures and tables should be numbered for cross-referencing.",
            source_doc="writing_guide",
            user_excerpt="",
            user_location="NOT FOUND",
            why="Numbered figures and tables enable unambiguous cross-referencing. Without numbering, reviewers cannot efficiently navigate between text and visuals.",
            fix_suggestion="Number all figures and tables (e.g. 'Figure 1', 'Table 1') and reference them in the text.",
        ))

    return findings


# ── Check I: Extended writing-guide rules (deterministic subset) ───
# These cover the remaining R##/P## rules that can be verified from the
# document text alone. Each check cites the exact source rule.

def check_extended_writing_guide_rules(
    user_text: str,
    rules: ExtractedRules,
) -> List[EvidenceFinding]:
    """Check the remaining writing-guide rules that can be verified deterministically.

    Implemented rules: R01-R04, R06, R08, R10, R13-R15, R19, R21, R24, R25,
    R27, R30, R31, R33, R36, R37, R40, R41, R45, R46, R49-R53, and
    P01, P02, P04, P08-P10. (R17 is implemented separately —
    see check_standards_consistency below.)

    NOT implemented (require human/semantic judgment — reported as
    unchecked_rule_ids in rulesUsed): R16, R18, R26, R28, R29, R32, R34,
    R35, R38, R39, R42-R44, R47, R48, P03, P05, P07.
    """
    findings: List[EvidenceFinding] = []
    user_sections = [s[0] for s in _detect_user_sections(user_text)]
    user_section_names_lower = [s.lower() for s in user_sections]
    text_lower = user_text.lower()
    lines = user_text.split("\n")

    # ── R02: Document should be readable in black and white printing ──
    r02 = get_rule_by_id("R02")
    # Heuristic: check for color-only references that would be lost in B&W
    color_only_refs = len(re.findall(r"\b(?:in\s+red|in\s+blue|in\s+green|red\s+text|blue\s+text|colored\s+in)\b", text_lower))
    if color_only_refs == 0:
        findings.append(EvidenceFinding(
            check="I_EXTENDED_WG_RULES", severity="pass", section="",
            rule_id="R02",
            message="No color-dependent references found (R02: document should be readable in B&W printing).",
            source_rule=f"R02: {r02.text if r02 else 'The document should be readable in black and white printing.'}",
            source_doc="writing_guide", user_excerpt="", user_location="Entire document",
            why="R02 requires the document to be readable in black and white printing. Color-only references would be lost when printed in B&W.",
        ))
    else:
        findings.append(EvidenceFinding(
            check="I_EXTENDED_WG_RULES", severity="warning", section="",
            rule_id="R02",
            message=f"{color_only_refs} color-dependent references found. R02 requires B&W readability.",
            source_rule=f"R02: {r02.text if r02 else 'The document should be readable in black and white printing.'}",
            source_doc="writing_guide",
            user_excerpt=_find_excerpt(user_text, r"\b(?:in\s+red|in\s+blue|colored)\b"),
            user_location=_locate_pattern(
                user_text,
                r"\b(?:in\s+red|in\s+blue|in\s+green|red\s+text|blue\s+text|colored\s+in)\b",
            ) or f"{color_only_refs} occurrences",
            why="Color-only references are lost in B&W printing. R02 requires the document to be readable without color.",
            fix_suggestion="Replace color-dependent references with text labels or patterns (e.g. bold, underline) that survive B&W printing.",
        ))

    # ── R03: Reference language (default English) ──
    r03 = get_rule_by_id("R03")
    # Check if document is primarily English or French
    en_indicators = len(re.findall(r"\b(the|shall|must|requirement|system|component|document)\b", text_lower))
    fr_indicators = len(re.findall(r"\b(le|la|les|doit|exigence|système|composant|document)\b", text_lower))
    if en_indicators > fr_indicators:
        findings.append(EvidenceFinding(
            check="I_EXTENDED_WG_RULES", severity="pass", section="",
            rule_id="R03",
            message="Document reference language is English (R03 compliant — default reference language is English).",
            source_rule=f"R03: {r03.text if r03 else 'The RD has only one reference language: by default, it is English.'}",
            source_doc="writing_guide", user_excerpt="", user_location="Entire document",
            why="R03 specifies that the default reference language is English. Bilingual documents must clearly identify the reference language.",
        ))
    elif fr_indicators > en_indicators * 2:
        findings.append(EvidenceFinding(
            check="I_EXTENDED_WG_RULES", severity="info", section="",
            rule_id="R03",
            message="Document appears to be primarily in French. R03 default is English — verify this is intentional.",
            source_rule=f"R03: {r03.text if r03 else 'The RD has only one reference language: by default, it is English.'}",
            source_doc="writing_guide", user_excerpt="", user_location="Entire document",
            why="R03 specifies English as the default reference language. French documents are allowed but the reference language must be clearly identified.",
            fix_suggestion="If French is the reference language, state it explicitly. Otherwise, translate to English.",
        ))

    # ── R04: Elements not defined in generic RD identified by yellow highlight ──
    r04 = get_rule_by_id("R04")
    # This is about generic RDs — check if document appears to be generic
    is_generic = bool(re.search(r"\bgeneric\s+(specification|RD|document|spec)\b", text_lower, re.IGNORECASE))
    if is_generic:
        # Check for highlighted elements (can't detect yellow in text, but check for placeholder markers)
        has_unspecified = bool(re.search(r"\b(?:TBD|to\s+be\s+defined|to\s+be\s+specified|per\s+project|per\s+application)\b", text_lower, re.IGNORECASE))
        if has_unspecified:
            findings.append(EvidenceFinding(
                check="I_EXTENDED_WG_RULES", severity="info", section="",
                rule_id="R04",
                message="Generic RD with unspecified elements detected. R04 requires these to be highlighted in yellow.",
                source_rule=f"R04: {r04.text if r04 else 'Elements not defined in a generic RD will be identified by characters highlighted in yellow.'}",
                source_doc="writing_guide",
                user_excerpt=_find_excerpt(user_text, r"\b(?:TBD|to\s+be\s+defined|per\s+project)\b"),
                user_location="Throughout document",
                why="R04 requires elements not defined in a generic RD (performance requirements, special characteristics, configuration tables) to be highlighted in yellow so they can be identified for each applicative RD.",
                fix_suggestion="Highlight all project-specific elements in yellow in the generic RD.",
            ))

    # ── R06: Page footer — "Generic" not "All projects" for generic CdC ──
    r06 = get_rule_by_id("R06")
    has_all_projects = bool(re.search(r"\ball\s+projects\b", text_lower, re.IGNORECASE))
    has_generic = bool(re.search(r"\bgeneric\b", text_lower, re.IGNORECASE))
    if has_all_projects and has_generic:
        findings.append(EvidenceFinding(
            check="I_EXTENDED_WG_RULES", severity="warning", section="PAGE FOOTERS",
            rule_id="R06",
            message="R06 violation: 'All projects' found in a generic CdC — should use 'Generic' instead.",
            source_rule=f"R06: {r06.text if r06 else 'In the Project box, insert Generic and not All projects if the CdC is generic.'}",
            source_doc="writing_guide",
            user_excerpt=_find_excerpt(user_text, r"\ball\s+projects\b"),
            user_location="Page footer area",
            why="R06 requires generic CdCs to use 'Generic' in the Project box, not 'All projects'. This prevents confusion about the document's applicability scope.",
            fix_suggestion="Replace 'All projects' with 'Generic' in the page footer Project box.",
        ))
    else:
        findings.append(EvidenceFinding(
            check="I_EXTENDED_WG_RULES", severity="info", section="PAGE FOOTERS",
            rule_id="R06",
            message="No 'All projects'/'Generic' conflict detected in the document text (R06 not triggered).",
            source_rule=f"R06: {r06.text if r06 else 'In the Project box, insert Generic and not All projects if the CdC is generic.'}",
            source_doc="writing_guide", user_excerpt="", user_location="Entire document",
            why="R06 only applies to generic CdCs that mislabel their scope as 'All projects'. This document doesn't contain that specific wording, so the rule has nothing to flag.",
        ))

    # ── R08: Auditor separate from writers ──
    r08 = get_rule_by_id("R08")
    # Check if writer and checker names appear to be different (heuristic)
    has_written_by = bool(re.search(r"written\s+by|writter", text_lower))
    has_checked_by = bool(re.search(r"checked\s+by|auditor|verifier", text_lower))
    if has_written_by and has_checked_by:
        findings.append(EvidenceFinding(
            check="I_EXTENDED_WG_RULES", severity="pass", section="APPROVAL",
            rule_id="R08",
            message="Both writer and checker/auditor identified (R08: auditor must be separate from writers).",
            source_rule=f"R08: {r08.text if r08 else 'The auditor is necessarily separate from the writers.'}",
            source_doc="writing_guide",
            user_excerpt=_find_excerpt(user_text, r"written\s+by|checked\s+by", 80),
            user_location="Document header",
            why="R08 requires the auditor to be a different person from the writers. This ensures independent verification.",
        ))
    else:
        findings.append(EvidenceFinding(
            check="I_EXTENDED_WG_RULES", severity="info", section="APPROVAL",
            rule_id="R08",
            message="Document does not clearly label both a writer and a checker/auditor — R08 cannot be verified from the text.",
            source_rule=f"R08: {r08.text if r08 else 'The auditor is necessarily separate from the writers.'}",
            source_doc="writing_guide", user_excerpt="", user_location="Document header",
            why="R08 requires an identifiable, separate auditor. Without both a 'written by' and a 'checked by' label, this cannot be confirmed automatically — verify manually that the auditor is not one of the writers.",
            fix_suggestion="Add 'Written by' and 'Checked by' labels with distinct names in the document header.",
        ))

    # ── R13: Diversity characteristics in tables ──
    r13 = get_rule_by_id("R13")
    has_diversity = _section_matches("SYSTEM DIVERSITY", user_sections) or _section_matches("DIVERSITY", user_sections)
    if has_diversity:
        # Check if diversity section has table-like content
        diversity_text = _extract_section_text(user_text, "DIVERSITY")
        has_tables = "|" in diversity_text or re.search(r"\bvariant\b|\bcharacteristic\b", diversity_text, re.IGNORECASE)
        if has_tables:
            findings.append(EvidenceFinding(
                check="I_EXTENDED_WG_RULES", severity="pass", section="SYSTEM DIVERSITY",
                rule_id="R13",
                message="Diversity section contains variant/characteristic data (R13 compliant).",
                source_rule=f"R13: {r13.text if r13 else 'The characteristics of diversity must be presented in tables, and for each characteristic, the values that this characteristic can take.'}",
                source_doc="writing_guide",
                user_excerpt=_find_excerpt(diversity_text, r"\bvariant\b|\bcharacteristic\b", 80) if diversity_text else "",
                user_location="SYSTEM DIVERSITY section",
                why="R13 requires diversity characteristics to be presented in tables with values per characteristic. This defines the product variants.",
            ))
        else:
            findings.append(EvidenceFinding(
                check="I_EXTENDED_WG_RULES", severity="warning", section="SYSTEM DIVERSITY",
                rule_id="R13",
                message="Diversity section found but no variant/characteristic tables detected (R13 violation).",
                source_rule=f"R13: {r13.text if r13 else 'The characteristics of diversity must be presented in tables.'}",
                source_doc="writing_guide", user_excerpt="", user_location="SYSTEM DIVERSITY section",
                why="R13 requires diversity characteristics in tables. Without tables, variants are not clearly defined.",
                fix_suggestion="Add tables for functional and architecture diversity characteristics with their possible values.",
            ))

    # ── R14: Documents cited with revision index ──
    # R14/R15 both look for the same real thing: content in the document's
    # reference-document ecosystem (Quoted/Reference/Applicable Documents,
    # Upstream Requirements, Standards — headings that nest each other in
    # this template, e.g. REFERENCE DOCUMENTS > UPSTREAM REQUIREMENTS holds
    # the actual "Mark | Reference | Version | Title" table). Heading-
    # boundary text extraction stops at the first subsection heading, so it
    # misses that nested table entirely. Reuse the structural declaration-
    # table detection built for R17 (finds every "[TAG] | Reference | ..."
    # row anywhere in the document, regardless of which heading it sits
    # under) instead of the narrower heading-scoped text.
    r14 = get_rule_by_id("R14")
    declaration_text, _ = _split_declaration_and_body(user_text)
    ref_section_text = _extract_section_text(user_text, "REFERENCE DOCUMENTS")
    appl_section_text = _extract_section_text(user_text, "APPLICABLE DOCUMENTS")
    combined_docs = (declaration_text + "\n" + ref_section_text + "\n" + appl_section_text).lower()
    has_revision_indices = bool(re.search(r"\b(?:rev\.?|revision|version|v\d+|index)\b", combined_docs))
    has_doc_references = bool(re.search(r"\b\d{4,}_\d{2}_\d{4,}\b|\b[A-Z]{2,}\d{3,}\b|\bSTA\d+\b", combined_docs))
    if has_doc_references and has_revision_indices:
        findings.append(EvidenceFinding(
            check="I_EXTENDED_WG_RULES", severity="pass", section="QUOTED DOCUMENTS",
            rule_id="R14",
            message="Reference/applicable documents include revision indices (R14 compliant).",
            source_rule=f"R14: {r14.text if r14 else 'These documents are cited with the index of revision.'}",
            source_doc="writing_guide",
            user_excerpt=_find_excerpt(combined_docs, r"\b(?:rev\.?|version|index)\b", 80),
            user_location="REFERENCE/APPLICABLE DOCUMENTS sections",
            why="R14 requires all quoted documents to be cited with their revision index. This ensures the correct version is referenced.",
        ))
    elif has_doc_references:
        findings.append(EvidenceFinding(
            check="I_EXTENDED_WG_RULES", severity="warning", section="QUOTED DOCUMENTS",
            rule_id="R14",
            message="Documents referenced but revision indices not clearly detected (R14 requires revision index).",
            source_rule=f"R14: {r14.text if r14 else 'These documents are cited with the index of revision.'}",
            source_doc="writing_guide", user_excerpt="", user_location="REFERENCE/APPLICABLE DOCUMENTS sections",
            why="R14 requires all quoted documents to include their revision index. Without it, the wrong version may be referenced.",
            fix_suggestion="Add revision/version indices to all referenced and applicable documents.",
        ))
    else:
        findings.append(EvidenceFinding(
            check="I_EXTENDED_WG_RULES", severity="info", section="QUOTED DOCUMENTS",
            rule_id="R14",
            message="No reference/applicable document citations detected — R14 (revision index) not applicable.",
            source_rule=f"R14: {r14.text if r14 else 'These documents are cited with the index of revision.'}",
            source_doc="writing_guide", user_excerpt="", user_location="REFERENCE/APPLICABLE DOCUMENTS sections",
            why="R14 only applies when external documents are quoted. None were detected in this document.",
        ))

    # ── R15: At least one Design file quoted in reference documents ──
    r15 = get_rule_by_id("R15")
    has_design_file = bool(re.search(r"\b(?:design\s+file|DC\b|upstream\s+(?:functional\s+)?requirements?|architecture\s+(?:constraints?|file))\b", combined_docs, re.IGNORECASE))
    if has_design_file:
        findings.append(EvidenceFinding(
            check="I_EXTENDED_WG_RULES", severity="pass", section="REFERENCE DOCUMENTS",
            rule_id="R15",
            message="At least one design file / upstream requirement quoted (R15 compliant).",
            source_rule=f"R15: {r15.text if r15 else 'There is at least one Design file to quote. All the DCs assigning at least one requirement should be quoted.'}",
            source_doc="writing_guide",
            user_excerpt=_find_excerpt(
                combined_docs,
                r"\b(?:design\s+file|DC\b|upstream\s+(?:functional\s+)?requirements?|architecture\s+(?:constraints?|file))\b",
                80,
            ),
            user_location="REFERENCE DOCUMENTS / UPSTREAM REQUIREMENTS section",
            why="R15 requires at least one Design file to be quoted. All DCs assigning requirements to the component must be referenced.",
        ))
    elif declaration_text.strip() or ref_section_text:
        findings.append(EvidenceFinding(
            check="I_EXTENDED_WG_RULES", severity="warning", section="REFERENCE DOCUMENTS",
            rule_id="R15",
            message="No design file / upstream requirements found in reference documents (R15 violation).",
            source_rule=f"R15: {r15.text if r15 else 'There is at least one Design file to quote.'}",
            source_doc="writing_guide", user_excerpt="", user_location="REFERENCE DOCUMENTS section",
            why="R15 requires at least one Design file to be quoted. Without it, the traceability to upstream design is broken.",
            fix_suggestion="Add at least one Design file reference in the REFERENCE DOCUMENTS section.",
        ))

    # ── R21: Compliance with requirements drafting rules ──
    r21 = get_rule_by_id("R21")
    shall_count = len(SHALL_RE.findall(user_text))
    if shall_count > 0:
        findings.append(EvidenceFinding(
            check="I_EXTENDED_WG_RULES", severity="pass", section="REQUIREMENTS",
            rule_id="R21",
            message="Requirements use formal drafting language (R21 compliant — 'shall' statements present).",
            source_rule=f"R21: {r21.text if r21 else 'Requirements of the RD shall be in accordance with the rules for drafting requirements.'}",
            source_doc="writing_guide",
            user_excerpt=_find_excerpt(user_text, r"\bshall\b"),
            user_location=f"{shall_count} shall statements",
            why="R21 requires requirements to follow the formal drafting rules from [GA2]. The presence of 'shall' statements indicates formal requirement drafting.",
        ))

    # ── R25: Requirements containing tables/diagrams must reference them ──
    r25 = get_rule_by_id("R25")
    has_figure_refs = bool(re.search(r"(?:see|refer\s+to|per|according\s+to)\s+(?:figure|table|diagram)\s+\d+", text_lower))
    has_figures = bool(re.search(r"figure\s+<?\d+|table\s+\d+", text_lower))
    if has_figures and has_figure_refs:
        findings.append(EvidenceFinding(
            check="I_EXTENDED_WG_RULES", severity="pass", section="REQUIREMENTS",
            rule_id="R25",
            message="Requirements reference figures/tables by number (R25 compliant).",
            source_rule=f"R25: {r25.text if r25 else 'If table/diagram/curve not integrated in requirement text, insert a textual phrase referencing it.'}",
            source_doc="writing_guide",
            user_excerpt=_find_excerpt(user_text, r"(?:see|refer\s+to)\s+(?:figure|table)\s+\d+"),
            user_location="Throughout document",
            why="R25 requires requirements containing tables/diagrams/curves to include a textual reference to them. This ensures the reader can find the referenced visual.",
        ))
    elif has_figures and not has_figure_refs:
        findings.append(EvidenceFinding(
            check="I_EXTENDED_WG_RULES", severity="info", section="REQUIREMENTS",
            rule_id="R25",
            message="Figures/tables found but no explicit cross-references ('see Figure N') detected. R25 recommends referencing them in requirement text.",
            source_rule=f"R25: {r25.text if r25 else 'Insert a textual phrase that references the table/diagram/curve.'}",
            source_doc="writing_guide", user_excerpt="", user_location="Throughout document",
            why="R25 requires requirements to explicitly reference their associated tables/diagrams by number. This helps the reader navigate between text and visuals.",
            fix_suggestion="Add explicit references like 'see Figure 3' or 'per Table 2' in requirements that use visual elements.",
        ))

    # ── R27: State-transitions diagram for functional behavior ──
    r27 = get_rule_by_id("R27")
    has_state_machine = bool(re.search(r"\b(?:state\s+(?:machine|chart|transition|diagram)|state\s+chart|statemachine|mode\s+diagram)\b", text_lower))
    has_functional_reqs = _section_matches("FUNCTIONAL REQUIREMENTS", user_sections)
    if has_functional_reqs and has_state_machine:
        findings.append(EvidenceFinding(
            check="I_EXTENDED_WG_RULES", severity="pass", section="FUNCTIONAL REQUIREMENTS",
            rule_id="R27",
            message="State-transition diagram detected for functional behavior (R27 compliant).",
            source_rule=f"R27: {r27.text if r27 else 'Definition of the state-transitions diagram of the Product, from the point of view of the Service.'}",
            source_doc="writing_guide",
            user_excerpt=_find_excerpt(user_text, r"state\s+(?:machine|chart|transition|diagram)"),
            user_location="FUNCTIONAL REQUIREMENTS section",
            why="R27 requires a state-transitions diagram for complex functional behaviors. This formalizes the system's operational states.",
        ))
    elif has_functional_reqs and not has_state_machine:
        findings.append(EvidenceFinding(
            check="I_EXTENDED_WG_RULES", severity="info", section="FUNCTIONAL REQUIREMENTS",
            rule_id="R27",
            message="Functional requirements present but no state-transition diagram detected. R27 recommends one for complex behaviors.",
            source_rule=f"R27: {r27.text if r27 else 'Definition of the state-transitions diagram of the Product.'}",
            source_doc="writing_guide", user_excerpt="", user_location="FUNCTIONAL REQUIREMENTS section",
            why="R27 recommends state-transition diagrams for complex functional behaviors. Without them, the system's state logic may be ambiguous.",
            fix_suggestion="Add a state-transition diagram for complex functional behaviors (UML state chart).",
        ))

    # ── R33: Binary/hexa values prohibited in requirements ──
    r33 = get_rule_by_id("R33")
    binary_hex_in_reqs = []
    shall_lines = [l for l in lines if SHALL_RE.search(l) and len(l.strip()) > 20]
    for line in shall_lines:
        if re.search(r"\b0[bB][01]+\b|\b0[xX][0-9A-Fa-f]+\b", line):
            binary_hex_in_reqs.append(line[:150])
    if not binary_hex_in_reqs:
        findings.append(EvidenceFinding(
            check="I_EXTENDED_WG_RULES", severity="pass", section="REQUIREMENTS",
            rule_id="R33",
            message="No binary/hexadecimal values found in requirements (R33 compliant).",
            source_rule=f"R33: {r33.text if r33 else 'The binary or hexa value (ex: 0b01) of the data is prohibited in the RD / ST.'}",
            source_doc="writing_guide", user_excerpt="", user_location="REQUIREMENTS section",
            why="R33 prohibits binary/hexadecimal values in requirements. They are implementation details that don't belong in a black-box specification.",
        ))
    else:
        findings.append(EvidenceFinding(
            check="I_EXTENDED_WG_RULES", severity="warning", section="REQUIREMENTS",
            rule_id="R33",
            message=f"R33 violation: binary/hexadecimal values found in {len(binary_hex_in_reqs)} requirement(s).",
            source_rule=f"R33: {r33.text if r33 else 'The binary or hexa value (ex: 0b01) of the data is prohibited in the RD / ST.'}",
            source_doc="writing_guide",
            user_excerpt=binary_hex_in_reqs[0],
            user_location="In requirement statements",
            why="R33 prohibits binary/hex values in requirements. They are implementation-level details. Use logical/semantic descriptions instead.",
            fix_suggestion="Replace binary/hex values with logical descriptions (e.g. 'active' instead of '0b01').",
        ))

    # ── R40: Mission profile present ──
    r40 = get_rule_by_id("R40")
    has_mission = _section_matches("MISSION PROFILE", user_sections) or bool(re.search(r"\bmission\s+profile\b", text_lower))
    if has_mission:
        findings.append(EvidenceFinding(
            check="I_EXTENDED_WG_RULES", severity="pass", section="MISSION PROFILE",
            rule_id="R40",
            message="Mission profile section/content found (R40 compliant).",
            source_rule=f"R40: {r40.text if r40 else 'Rule on Mission Profiles (§ 5.4.1).'}",
            source_doc="writing_guide",
            user_excerpt=_find_excerpt(user_text, r"mission\s+profile", 80),
            user_location="OPERATIONAL REQUIREMENTS section",
            why="R40 requires a mission profile defining the operational usage conditions. This is critical for validation test design.",
        ))
    elif _section_matches("OPERATIONAL REQUIREMENTS", user_sections):
        findings.append(EvidenceFinding(
            check="I_EXTENDED_WG_RULES", severity="warning", section="OPERATIONAL REQUIREMENTS",
            rule_id="R40",
            message="No mission profile found in operational requirements (R40 violation).",
            source_rule=f"R40: {r40.text if r40 else 'Rule on Mission Profiles (§ 5.4.1).'}",
            source_doc="writing_guide", user_excerpt="", user_location="OPERATIONAL REQUIREMENTS section",
            why="R40 requires a mission profile. Without it, validation tests cannot be designed against real usage conditions.",
            fix_suggestion="Add a MISSION PROFILE section defining the operational usage conditions (duration, cycles, environment).",
        ))

    # ── R41: Random noise requirement compulsory for electro-mechanical components ──
    r41 = get_rule_by_id("R41")
    has_noise_req = bool(re.search(r"\b(?:random\s+noise|bruit\s+(?:aléatoire|parasite)|noise\s+(?:level|requirement|target))\b", text_lower))
    if has_noise_req:
        # Scoped to the ERGONOMICS section first — an unscoped whole-document
        # search was confirmed during a 2026 audit to pick up an unrelated
        # match from the Applicable-Documents reference table (a standard
        # named "...random noises...") instead of the real requirement, while
        # still claiming "ERGONOMICS / OPERATIONAL section" as the location.
        excerpt, location = _find_excerpt_scoped(
            user_text, "ERGONOMICS", r"random\s+noise|bruit\s+aléatoire|noise\s+level", rules)
        findings.append(EvidenceFinding(
            check="I_EXTENDED_WG_RULES", severity="pass", section="ERGONOMICS",
            rule_id="R41",
            message="Random noise requirement found (R41 compliant — compulsory for electro-mechanical components).",
            source_rule=f"R41: {r41.text if r41 else 'A requirement concerning random noise is compulsory for each electro-mechanical component.'}",
            source_doc="writing_guide",
            user_excerpt=excerpt,
            user_location=location,
            why="R41 requires a random noise requirement for every electro-mechanical component. This is a mandatory ergonomic constraint.",
        ))
    else:
        findings.append(EvidenceFinding(
            check="I_EXTENDED_WG_RULES", severity="warning", section="ERGONOMICS",
            rule_id="R41",
            message="No random noise requirement found. R41 requires one for each electro-mechanical component.",
            source_rule=f"R41: {r41.text if r41 else 'A requirement concerning random noise is compulsory for each electro-mechanical component.'}",
            source_doc="writing_guide", user_excerpt="", user_location="NOT FOUND",
            why="R41 mandates a random noise requirement for all electro-mechanical components. Without it, the component's acoustic impact is uncontrolled.",
            fix_suggestion="Add a random noise requirement specifying the maximum noise level (in dB) under operational conditions.",
        ))

    # ── R45: Each regulatory requirement refers to upstream regulatory requirement ──
    r45 = get_rule_by_id("R45")
    has_regulatory = bool(re.search(r"\b(?:regulation|regulatory|réglementation|ECER|FMVSS|ISTA|ISO\s*\d+)\b", text_lower, re.IGNORECASE))
    has_reg_refs = bool(re.search(r"\b(?:regulation\s+(?:requirement|standard)|réglementation|regulatory\s+requirement)\b", text_lower))
    if has_regulatory:
        findings.append(EvidenceFinding(
            check="I_EXTENDED_WG_RULES", severity="info", section="CONSTRAINT REQUIREMENTS",
            rule_id="R45",
            message="Regulatory references found. R45 requires each regulatory requirement to refer to an upstream regulatory requirement or standard.",
            source_rule=f"R45: {r45.text if r45 else 'Each regulatory requirement refers to at least a regulatory requirement from a DC, or a Design Guide, or standard R99 1010.'}",
            source_doc="writing_guide",
            user_excerpt=_find_excerpt(user_text, r"regulation|regulatory|ECER|FMVSS|ISO"),
            user_location="CONSTRAINT REQUIREMENTS section",
            why="R45 requires each regulatory requirement to trace to an upstream regulatory source. This ensures compliance can be audited.",
            fix_suggestion="Ensure each regulatory requirement references its source regulation or standard.",
        ))

    # ── P01: Separation of Product/Project/Other-systems requirements ──
    p01 = get_rule_by_id("P01")
    # Heuristic: check that requirements don't mix product and process concerns
    has_process_reqs = bool(re.search(r"\b(?:assembly\s+process|packaging\s+process|manufacturing\s+process|development\s+process)\b.*\bshall\b", text_lower))
    if not has_process_reqs:
        findings.append(EvidenceFinding(
            check="I_EXTENDED_WG_RULES", severity="pass", section="REQUIREMENTS",
            rule_id="P01",
            message="No process/assembly requirements mixed into product requirements (P01 compliant).",
            source_rule=f"P01: {p01.text if p01 else 'Principle of separation Product requirements / Project requirements / requirements for other systems.'}",
            source_doc="writing_guide", user_excerpt="", user_location="REQUIREMENTS section",
            why="P01 requires separation of product requirements from project/process/other-system requirements. Mixing them creates confusion about what the supplier must deliver.",
        ))
    else:
        findings.append(EvidenceFinding(
            check="I_EXTENDED_WG_RULES", severity="warning", section="REQUIREMENTS",
            rule_id="P01",
            message="P01 violation: process/assembly requirements found mixed with product requirements.",
            source_rule=f"P01: {p01.text if p01 else 'Principle of separation Product requirements / Project requirements / requirements for other systems.'}",
            source_doc="writing_guide",
            user_excerpt=_find_excerpt(user_text, r"(?:assembly|packaging|manufacturing)\s+process.*shall"),
            user_location="In requirement statements",
            why="P01 requires product requirements to be separated from process/assembly/packaging requirements. Process requirements belong in other documents (Packaging ST, FR, GEED).",
            fix_suggestion="Move process/assembly/packaging requirements to the appropriate process specification document.",
        ))

    # ── P02: Black-box description (no internal functional analysis) ──
    p02 = get_rule_by_id("P02")
    has_internal_analysis = bool(re.search(r"\binternal\s+functional\s+analysis\b", text_lower))
    if not has_internal_analysis:
        findings.append(EvidenceFinding(
            check="I_EXTENDED_WG_RULES", severity="pass", section="FUNCTIONAL REQUIREMENTS",
            rule_id="P02",
            message="No internal functional analysis detected (P02 compliant — black-box description).",
            source_rule=f"P02: {p02.text if p02 else 'The description of the behavior in § 5.1 is a black box description. The use of Internal Functional Analysis is prohibited.'}",
            source_doc="writing_guide", user_excerpt="", user_location="FUNCTIONAL REQUIREMENTS section",
            why="P02 requires black-box description. Internal Functional Analysis generates internal data loops that make requirements unverifiable.",
        ))

    # ── P04: Requirements structured in 4 points (preconditions, process, effects, post-conditions) ──
    p04 = get_rule_by_id("P04")
    # Check for preconditions/conditions in requirements
    has_preconditions = bool(re.search(r"\b(?:if|when|while|during|in\s+case\s+of|upon|precondition|mode)\b.*\bshall\b", text_lower))
    if has_preconditions:
        findings.append(EvidenceFinding(
            check="I_EXTENDED_WG_RULES", severity="pass", section="REQUIREMENTS",
            rule_id="P04",
            message="Requirements contain preconditions/conditions (P04 compliant — structured with preconditions).",
            source_rule=f"P04: {p04.text if p04 else 'Principle of Structuring the requirements in 4 points: preconditions, generating process, observable effects, post-conditions.'}",
            source_doc="writing_guide",
            user_excerpt=_find_excerpt(user_text, r"\b(?:if|when|while|in\s+case\s+of)\b.*\bshall\b"),
            user_location="In requirement statements",
            why="P04 recommends structuring requirements with preconditions, generating process, observable effects, and post-conditions. This makes requirements verifiable.",
        ))
    else:
        findings.append(EvidenceFinding(
            check="I_EXTENDED_WG_RULES", severity="info", section="REQUIREMENTS",
            rule_id="P04",
            message="No explicit preconditions (if/when/while) found in requirements. P04 recommends structuring with preconditions.",
            source_rule=f"P04: {p04.text if p04 else 'Principle of Structuring the requirements in 4 points: preconditions, generating process, observable effects, post-conditions.'}",
            source_doc="writing_guide", user_excerpt="", user_location="REQUIREMENTS section",
            why="P04 recommends structuring requirements with preconditions. Without them, the context in which a requirement applies may be ambiguous.",
            fix_suggestion="Structure requirements with 'If/When <precondition>, the system shall <process>, producing <observable effect>.'",
        ))

    # ── P08: Distinction reference vs applicable documents ──
    p08 = get_rule_by_id("P08")
    has_ref_docs = _section_matches("REFERENCE DOCUMENTS", user_sections)
    has_appl_docs = _section_matches("APPLICABLE DOCUMENTS", user_sections)
    if has_ref_docs and has_appl_docs:
        findings.append(EvidenceFinding(
            check="I_EXTENDED_WG_RULES", severity="pass", section="QUOTED DOCUMENTS",
            rule_id="P08",
            message="Both REFERENCE DOCUMENTS and APPLICABLE DOCUMENTS sections present (P08 compliant).",
            source_rule=f"P08: {p08.text if p08 else 'Principle of Distinction reference documents relative to applicable documents.'}",
            source_doc="writing_guide",
            user_excerpt="REFERENCE DOCUMENTS + APPLICABLE DOCUMENTS",
            user_location="QUOTED DOCUMENTS section",
            why="P08 requires distinguishing reference documents (input specs, not sent to supplier) from applicable documents (completing the RD definition, delivered to supplier).",
        ))
    elif has_ref_docs or has_appl_docs:
        findings.append(EvidenceFinding(
            check="I_EXTENDED_WG_RULES", severity="warning", section="QUOTED DOCUMENTS",
            rule_id="P08",
            message="Only one of REFERENCE/APPLICABLE DOCUMENTS found. P08 requires both to be distinguished.",
            source_rule=f"P08: {p08.text if p08 else 'Principle of Distinction reference documents relative to applicable documents.'}",
            source_doc="writing_guide", user_excerpt="", user_location="QUOTED DOCUMENTS section",
            why="P08 requires both reference and applicable documents sections. They serve different purposes: reference docs are inputs, applicable docs are delivered to the supplier.",
            fix_suggestion="Ensure both REFERENCE DOCUMENTS and APPLICABLE DOCUMENTS sections are present and clearly separated.",
        ))

    # ── P09: Diversity characteristics defined ──
    p09 = get_rule_by_id("P09")
    if has_diversity:
        findings.append(EvidenceFinding(
            check="I_EXTENDED_WG_RULES", severity="pass", section="SYSTEM DIVERSITY",
            rule_id="P09",
            message="System diversity section present (P09 compliant — diversity characteristics defined).",
            source_rule=f"P09: {p09.text if p09 else 'The diversity characteristics of the Product and related variants are defined.'}",
            source_doc="writing_guide",
            user_excerpt=_find_excerpt(user_text, r"diversity|variant", 80),
            user_location="SYSTEM DIVERSITY section",
            why="P09 requires diversity characteristics to be defined. These determine which requirements apply to which product variants.",
        ))

    # ── P10: SdF (Dependability) study requirements incorporated ──
    p10 = get_rule_by_id("P10")
    has_sdf = bool(re.search(r"\b(?:SdF|sûreté\s+de\s+fonctionnement|dependability|safety|reliability|RAMS|ASIL|FTA|FMEA)\b", text_lower, re.IGNORECASE))
    if has_sdf:
        findings.append(EvidenceFinding(
            check="I_EXTENDED_WG_RULES", severity="pass", section="RAMS REQUIREMENTS",
            rule_id="P10",
            message="Dependability/SdF requirements found (P10 compliant — SdF study requirements incorporated).",
            source_rule=f"P10: {p10.text if p10 else 'The RD incorporates the requirements justified by a product SdF study.'}",
            source_doc="writing_guide",
            user_excerpt=_find_excerpt(user_text, r"SdF|dependability|safety|reliability|ASIL"),
            user_location="RAMS REQUIREMENTS section",
            why="P10 requires the RD to incorporate requirements from a product SdF (Dependability) study. This ensures safety and reliability are addressed.",
        ))
    elif _section_matches("RAMS REQUIREMENTS", user_sections):
        findings.append(EvidenceFinding(
            check="I_EXTENDED_WG_RULES", severity="warning", section="RAMS REQUIREMENTS",
            rule_id="P10",
            message="RAMS section present but no SdF/dependability/safety content detected (P10 violation).",
            source_rule=f"P10: {p10.text if p10 else 'The RD incorporates the requirements justified by a product SdF study.'}",
            source_doc="writing_guide", user_excerpt="", user_location="RAMS REQUIREMENTS section",
            why="P10 requires the RD to incorporate SdF study requirements. Without them, safety and reliability are not addressed.",
            fix_suggestion="Add dependability/safety requirements from the product SdF study (ASIL levels, FTA, FMEA results).",
        ))

    # ── R31: Network context diagram ──
    r31 = get_rule_by_id("R31")
    # This ASU spec has NO section actually titled "NETWORK INTERFACES" —
    # has_network becomes True purely from CAN/LIN keyword mentions
    # elsewhere in the document. Hardcoding "NETWORK INTERFACES section" as
    # the location regardless was confirmed to mislead a reviewer looking
    # for a section that doesn't exist; report honestly which one it was.
    network_section_match = _section_matches("NETWORK INTERFACES", user_sections)
    has_network = bool(network_section_match) or bool(re.search(r"\b(?:CAN|LIN|network\s+interface)\b", text_lower, re.IGNORECASE))
    network_location = (
        f"{network_section_match} section" if network_section_match
        else "Entire document (no section literally titled \"NETWORK INTERFACES\" — detected via CAN/LIN keyword mentions)"
    )
    has_context_diagram = bool(re.search(r"context(?:ual)?\s+diagram", text_lower))
    if has_network and has_context_diagram:
        findings.append(EvidenceFinding(
            check="I_EXTENDED_WG_RULES", severity="pass", section="NETWORK INTERFACES",
            rule_id="R31",
            message="Network context diagram found (R31 compliant).",
            source_rule=f"R31: {r31.text if r31 else 'Rule of writing of the Network Context Diagram (§ 5.3.1).'}",
            source_doc="writing_guide",
            user_excerpt=_find_excerpt(user_text, r"context.*diagram"),
            user_location=network_location,
            why="R31 requires a Network Context Diagram for network interfaces. This shows the communication architecture.",
        ))
    elif has_network and not has_context_diagram:
        findings.append(EvidenceFinding(
            check="I_EXTENDED_WG_RULES", severity="info", section="NETWORK INTERFACES",
            rule_id="R31",
            message="Network interfaces present but no context diagram detected. R31 recommends a Network Context Diagram.",
            source_rule=f"R31: {r31.text if r31 else 'Rule of writing of the Network Context Diagram (§ 5.3.1).'}",
            source_doc="writing_guide", user_excerpt="", user_location=network_location,
            why="R31 recommends a Network Context Diagram for network interfaces. Without it, the communication architecture is unclear.",
            fix_suggestion="Add a Network Context Diagram showing the component's network connections.",
        ))
    else:
        findings.append(EvidenceFinding(
            check="I_EXTENDED_WG_RULES", severity="info", section="NETWORK INTERFACES",
            rule_id="R31",
            message="No network interfaces detected — R31 (network context diagram) not applicable.",
            source_rule=f"R31: {r31.text if r31 else 'Rule of writing of the Network Context Diagram (§ 5.3.1).'}",
            source_doc="writing_guide", user_excerpt="", user_location="Entire document",
            why="R31 only applies to components with network interfaces (CAN/LIN/etc.). None were detected in this document.",
        ))

    # ── R36: Electric interface diagram ──
    r36 = get_rule_by_id("R36")
    has_electrical = _section_matches("ELECTRICAL INTERFACES", user_sections) or bool(re.search(r"\b(?:electric(?:al)?|power\s+supply|voltage|current)\b", text_lower))
    has_elec_diagram = bool(re.search(r"(?:electric|wiring|power)\s+(?:interface\s+)?diagram|schematic", text_lower))
    if has_electrical and has_elec_diagram:
        findings.append(EvidenceFinding(
            check="I_EXTENDED_WG_RULES", severity="pass", section="ELECTRICAL INTERFACES",
            rule_id="R36",
            message="Electric interface diagram found (R36 compliant).",
            source_rule=f"R36: {r36.text if r36 else 'Rule for the electric interface diagram.'}",
            source_doc="writing_guide",
            user_excerpt=_find_excerpt(user_text, r"(?:electric|wiring|power)\s+.*diagram|schematic"),
            user_location="ELECTRICAL INTERFACES section",
            why="R36 requires an electric interface diagram. This shows the power and signal connections.",
        ))

    # ── R37: Power consumption requirements ──
    r37 = get_rule_by_id("R37")
    has_power_req = bool(re.search(r"\b(?:power\s+consumption|current\s+consumption|supply\s+(?:current|voltage)|power\s+dissipation)\b.*\b(?:shall|must|≤|<=|max)\b", text_lower))
    if has_power_req:
        findings.append(EvidenceFinding(
            check="I_EXTENDED_WG_RULES", severity="pass", section="ELECTRICAL INTERFACES",
            rule_id="R37",
            message="Power consumption requirement found (R37 compliant).",
            source_rule=f"R37: {r37.text if r37 else 'Rule for drafting power consumption requirements (§ 5.3.2).'}",
            source_doc="writing_guide",
            user_excerpt=_find_excerpt(user_text, r"power\s+consumption|current\s+consumption"),
            user_location="ELECTRICAL INTERFACES section",
            why="R37 requires power consumption requirements. These define the electrical load the component places on the vehicle.",
        ))
    elif has_electrical:
        findings.append(EvidenceFinding(
            check="I_EXTENDED_WG_RULES", severity="info", section="ELECTRICAL INTERFACES",
            rule_id="R37",
            message="Electrical interfaces present but no explicit power consumption requirement detected. R37 recommends one.",
            source_rule=f"R37: {r37.text if r37 else 'Rule for drafting power consumption requirements (§ 5.3.2).'}",
            source_doc="writing_guide", user_excerpt="", user_location="ELECTRICAL INTERFACES section",
            why="R37 recommends power consumption requirements for electrical interfaces. Without them, the component's electrical load is undefined.",
            fix_suggestion="Add power consumption requirements specifying max current/voltage/dissipation.",
        ))

    # ── R29: Reset of the computer (§ 5.1) ──
    # A deterministic PRESENCE check, the same shape as R37/R41 above: does
    # the document define what happens when the component/ECU is reset?
    # Verifying the reset behaviour is CORRECT would need understanding
    # the requirement's meaning — out of reach here — but verifying a
    # reset requirement EXISTS at all is a plain keyword-presence check,
    # exactly like the power-consumption (R37) and random-noise (R41)
    # checks already deterministic in this file.
    r29 = get_rule_by_id("R29")
    has_reset_req = bool(re.search(
        r"\breset\b.*\b(?:shall|must)\b|\b(?:shall|must)\b.*\breset\b",
        text_lower,
    ))
    if has_reset_req:
        findings.append(EvidenceFinding(
            check="I_EXTENDED_WG_RULES", severity="pass", section="FUNCTIONAL REQUIREMENTS",
            rule_id="R29",
            message="Reset requirement found (R29 compliant).",
            source_rule=f"R29: {r29.text if r29 else 'Reset of the computer (§ 5.1).'}",
            source_doc="writing_guide",
            user_excerpt=_find_line_excerpt(user_text, r"\breset\b"),
            user_location="FUNCTIONAL REQUIREMENTS section",
            why="R29 requires the component's reset behaviour to be specified — what triggers a reset and what state the component returns to.",
        ))
    else:
        findings.append(EvidenceFinding(
            check="I_EXTENDED_WG_RULES", severity="info", section="FUNCTIONAL REQUIREMENTS",
            rule_id="R29",
            message="No explicit reset requirement detected. R29 recommends specifying the computer's reset behaviour.",
            source_rule=f"R29: {r29.text if r29 else 'Reset of the computer (§ 5.1).'}",
            source_doc="writing_guide", user_excerpt="", user_location="FUNCTIONAL REQUIREMENTS section",
            why="R29 expects a reset requirement — what causes the component to reset, and what state it returns to afterward. Without one, this behaviour is undefined.",
            fix_suggestion="Add a requirement specifying what triggers a reset (power cycle, watchdog, command…) and the component's state immediately after.",
        ))

    # ── R51: Environment constraints without referring to test implementation ──
    r51 = get_rule_by_id("R51")
    has_env_section = _section_matches("ENVIRONMENT CONDITIONS", user_sections)
    if has_env_section:
        env_text = _extract_section_text(user_text, "ENVIRONMENT")
        # Check that environment section focuses on constraints, not test procedures
        has_test_refs = bool(re.search(r"\b(?:test\s+procedure|test\s+method|how\s+to\s+test|test\s+setup)\b", env_text.lower()))
        if not has_test_refs:
            findings.append(EvidenceFinding(
                check="I_EXTENDED_WG_RULES", severity="pass", section="ENVIRONMENT CONDITIONS",
                rule_id="R51",
                message="Environment conditions section focuses on constraints, not test procedures (R51 compliant).",
                source_rule=f"R51: {r51.text if r51 else 'This paragraph expresses the environment constraints to respect which can be stated without referring to the implementation of the tests.'}",
                source_doc="writing_guide", user_excerpt="", user_location="ENVIRONMENT CONDITIONS section",
                why="R51 requires environment constraints to be stated without referring to test implementation. Test procedures belong in the validation plan.",
            ))
        else:
            findings.append(EvidenceFinding(
                check="I_EXTENDED_WG_RULES", severity="warning", section="ENVIRONMENT CONDITIONS",
                rule_id="R51",
                message="R51 violation: environment section contains test procedure references — should focus on constraints only.",
                source_rule=f"R51: {r51.text if r51 else 'This paragraph expresses the environment constraints without referring to the implementation of the tests.'}",
                source_doc="writing_guide",
                user_excerpt=_find_excerpt(env_text, r"test\s+procedure|test\s+method"),
                user_location="ENVIRONMENT CONDITIONS section",
                why="R51 requires environment constraints without test implementation references. Test procedures belong in the validation plan (§ 5.6).",
                fix_suggestion="Move test procedure descriptions to the INTEGRATION AND VALIDATION section. Keep only constraints in ENVIRONMENT CONDITIONS.",
            ))

    # ── R49/R50: No packaging/development/recycling process requirements in constraint section ──
    r49 = get_rule_by_id("R49")
    r50 = get_rule_by_id("R50")
    constraint_text = _extract_section_text(user_text, "CONSTRAINT REQUIREMENTS")
    if constraint_text:
        has_packaging = bool(re.search(r"\bpackaging\s+process\b", constraint_text.lower()))
        has_recycling = bool(re.search(r"\b(?:recycling|development\s+process)\b", constraint_text.lower()))
        if has_packaging:
            findings.append(EvidenceFinding(
                check="I_EXTENDED_WG_RULES", severity="warning", section="CONSTRAINT REQUIREMENTS",
                rule_id="R49",
                message="R49 violation: packaging process requirements found in CONSTRAINT REQUIREMENTS — they belong in Packaging and Assembly ST.",
                source_rule=f"R49: {r49.text if r49 else 'Pay attention not to insert requirements concerning the packaging process, which is subject to the Packaging and Assembly ST.'}",
                source_doc="writing_guide",
                user_excerpt=_find_excerpt(constraint_text, r"packaging\s+process"),
                user_location="CONSTRAINT REQUIREMENTS section",
                why="R49 prohibits packaging process requirements in the CTS. They belong in the Packaging and Assembly ST.",
                fix_suggestion="Move packaging process requirements to the Packaging and Assembly ST document.",
            ))
        if has_recycling:
            findings.append(EvidenceFinding(
                check="I_EXTENDED_WG_RULES", severity="warning", section="CONSTRAINT REQUIREMENTS",
                rule_id="R50",
                message="R50 violation: development/recycling process requirements found in CONSTRAINT REQUIREMENTS.",
                source_rule=f"R50: {r50.text if r50 else 'Please do not insert requirements concerning the development and recycling process.'}",
                source_doc="writing_guide",
                user_excerpt=_find_excerpt(constraint_text, r"recycling|development\s+process"),
                user_location="CONSTRAINT REQUIREMENTS section",
                why="R50 prohibits development and recycling process requirements in the CTS. They belong in process documents.",
                fix_suggestion="Move development/recycling process requirements to the appropriate process document.",
            ))
        if not has_packaging:
            findings.append(EvidenceFinding(
                check="I_EXTENDED_WG_RULES", severity="pass", section="CONSTRAINT REQUIREMENTS",
                rule_id="R49",
                message="No packaging process requirements in constraint section (R49 compliant).",
                source_rule=f"R49: {r49.text if r49 else 'Pay attention not to insert requirements concerning the packaging process, which is subject to the Packaging and Assembly ST.'}",
                source_doc="writing_guide", user_excerpt="", user_location="CONSTRAINT REQUIREMENTS section",
                why="R49 prohibits packaging process requirements in the CTS constraint section. They belong in the Packaging and Assembly ST.",
            ))
        if not has_recycling:
            findings.append(EvidenceFinding(
                check="I_EXTENDED_WG_RULES", severity="pass", section="CONSTRAINT REQUIREMENTS",
                rule_id="R50",
                message="No development/recycling process requirements in constraint section (R50 compliant).",
                source_rule=f"R50: {r50.text if r50 else 'Please do not insert requirements concerning the development and recycling process.'}",
                source_doc="writing_guide", user_excerpt="", user_location="CONSTRAINT REQUIREMENTS section",
                why="R50 prohibits development/recycling process requirements in the CTS constraint section. They belong in dedicated process documents.",
            ))
    else:
        findings.append(EvidenceFinding(
            check="I_EXTENDED_WG_RULES", severity="info", section="CONSTRAINT REQUIREMENTS",
            rule_id="R49",
            message="No CONSTRAINT REQUIREMENTS section content found — R49 not applicable.",
            source_rule=f"R49: {r49.text if r49 else 'Pay attention not to insert requirements concerning the packaging process, which is subject to the Packaging and Assembly ST.'}",
            source_doc="writing_guide", user_excerpt="", user_location="NOT FOUND",
            why="R49 only applies within the CONSTRAINT REQUIREMENTS section. It was not found in this document (see section coverage).",
        ))
        findings.append(EvidenceFinding(
            check="I_EXTENDED_WG_RULES", severity="info", section="CONSTRAINT REQUIREMENTS",
            rule_id="R50",
            message="No CONSTRAINT REQUIREMENTS section content found — R50 not applicable.",
            source_rule=f"R50: {r50.text if r50 else 'Please do not insert requirements concerning the development and recycling process.'}",
            source_doc="writing_guide", user_excerpt="", user_location="NOT FOUND",
            why="R50 only applies within the CONSTRAINT REQUIREMENTS section. It was not found in this document (see section coverage).",
        ))

    # ── R46: Design constraints only internal, interface constraints in §5.3 ──
    r46 = get_rule_by_id("R46")
    design_text = _extract_section_text(user_text, "DESIGN AND MANUFACTURING")
    if design_text:
        has_interface_in_design = bool(re.search(r"\b(?:interface\s+(?:requirement|constraint)|network\s+interface|electrical\s+interface|mechanical\s+interface)\b", design_text.lower()))
        if not has_interface_in_design:
            findings.append(EvidenceFinding(
                check="I_EXTENDED_WG_RULES", severity="pass", section="DESIGN AND MANUFACTURING",
                rule_id="R46",
                message="Design section contains only internal design constraints (R46 compliant).",
                source_rule=f"R46: {r46.text if r46 else 'This paragraph should only address design constraints internal to the components. Interface constraints are given in § 5.3.'}",
                source_doc="writing_guide", user_excerpt="", user_location="DESIGN AND MANUFACTURING section",
                why="R46 requires design constraints to be internal only. Interface constraints belong in § 5.3 (External Interfaces).",
            ))
        else:
            findings.append(EvidenceFinding(
                check="I_EXTENDED_WG_RULES", severity="warning", section="DESIGN AND MANUFACTURING",
                rule_id="R46",
                message="R46 violation: interface constraints found in DESIGN section — they belong in § 5.3 External Interfaces.",
                source_rule=f"R46: {r46.text if r46 else 'This paragraph should only address design constraints internal to the components. Interface constraints are given in § 5.3.'}",
                source_doc="writing_guide",
                user_excerpt=_find_excerpt(design_text, r"interface\s+(?:requirement|constraint)"),
                user_location="DESIGN AND MANUFACTURING section",
                why="R46 requires design constraints to be internal only. Interface constraints in the design section create duplication and inconsistency.",
                fix_suggestion="Move interface constraints to the EXTERNAL INTERFACES section (§ 5.3).",
            ))

    # ── R19: Distribution of transfer/protocol/application between §5.1 and §5.3 ──
    r19 = get_rule_by_id("R19")
    has_func_reqs = _section_matches("FUNCTIONAL REQUIREMENTS", user_sections)
    has_ext_interfaces = _section_matches("EXTERNAL INTERFACES", user_sections) or _section_matches("NETWORK INTERFACES", user_sections)
    if has_func_reqs and has_ext_interfaces:
        findings.append(EvidenceFinding(
            check="I_EXTENDED_WG_RULES", severity="pass", section="REQUIREMENTS",
            rule_id="R19",
            message="Both functional requirements (§5.1) and external interfaces (§5.3) present (R19 compliant — distribution possible).",
            source_rule=f"R19: {r19.text if r19 else 'The distribution of transfer functions, protocols and application level between § 5.1 and 5.3.'}",
            source_doc="writing_guide", user_excerpt="", user_location="REQUIREMENTS section",
            why="R19 requires proper distribution of transfer/protocol/application levels between functional requirements (§5.1) and external interfaces (§5.3).",
        ))

    # ── R30: Performance requirements present ──
    r30 = get_rule_by_id("R30")
    has_perf = _section_matches("PERFORMANCE REQUIREMENTS", user_sections)
    if has_perf:
        # Scoped to the PERFORMANCE REQUIREMENTS section first — an unscoped
        # whole-document search was confirmed during a 2026 audit to pick up
        # an unrelated match from the Applicable-Documents standards table
        # (a standard literally named "...PERFORMANCE REQUIREMENTS") instead
        # of the real section content.
        excerpt, location = _find_excerpt_scoped(
            user_text, "PERFORMANCE REQUIREMENTS", r"performance\s+requirement", rules, 80)
        findings.append(EvidenceFinding(
            check="I_EXTENDED_WG_RULES", severity="pass", section="PERFORMANCE REQUIREMENTS",
            rule_id="R30",
            message="Performance requirements section present (R30 compliant).",
            source_rule=f"R30: {r30.text if r30 else 'Rule about performance requirements (§ 5.2).'}",
            source_doc="writing_guide",
            user_excerpt=excerpt,
            user_location=location,
            why="R30 requires performance requirements. These define the efficiency criteria for functional requirements (response time, accuracy, etc.).",
        ))
    elif _section_matches("REQUIREMENTS", user_sections):
        findings.append(EvidenceFinding(
            check="I_EXTENDED_WG_RULES", severity="warning", section="REQUIREMENTS",
            rule_id="R30",
            message="No PERFORMANCE REQUIREMENTS section found (R30 violation).",
            source_rule=f"R30: {r30.text if r30 else 'Rule about performance requirements (§ 5.2).'}",
            source_doc="writing_guide", user_excerpt="", user_location="NOT FOUND",
            why="R30 requires performance requirements. Without them, the efficiency criteria for functional requirements are undefined.",
            fix_suggestion="Add a PERFORMANCE REQUIREMENTS section defining response times, accuracy, throughput, etc.",
        ))

    # ── R52: Network requirements specified ──
    r52 = get_rule_by_id("R52")
    if has_network:
        has_network_reqs = bool(re.search(r"\b(?:CAN|LIN|network\s+(?:frame|message|signal|protocol))\b.*\bshall\b", text_lower, re.IGNORECASE))
        if has_network_reqs:
            findings.append(EvidenceFinding(
                check="I_EXTENDED_WG_RULES", severity="pass", section="NETWORK INTERFACES",
                rule_id="R52",
                message="Network requirements specified (R52 compliant).",
                source_rule=f"R52: {r52.text if r52 else 'Specify all the network requirements to be applied to the component specified.'}",
                source_doc="writing_guide",
                user_excerpt=_find_excerpt(user_text, r"(?:CAN|LIN|network).*shall"),
                user_location=network_location,
                why="R52 requires all network requirements to be specified. This defines the communication protocols and messages.",
            ))
        else:
            findings.append(EvidenceFinding(
                check="I_EXTENDED_WG_RULES", severity="warning", section="NETWORK INTERFACES",
                rule_id="R52",
                message="Network interfaces present but no explicit 'shall' network requirement statements detected (R52 violation).",
                source_rule=f"R52: {r52.text if r52 else 'Specify all the network requirements to be applied to the component specified.'}",
                source_doc="writing_guide", user_excerpt="", user_location=network_location,
                why="R52 requires all network requirements to be formally specified with 'shall'. Without them, the communication protocol behavior is undefined.",
                fix_suggestion="Add formal 'shall' requirements for each CAN/LIN frame, message, or signal used by the component.",
            ))
    else:
        findings.append(EvidenceFinding(
            check="I_EXTENDED_WG_RULES", severity="info", section="NETWORK INTERFACES",
            rule_id="R52",
            message="No network interfaces detected — R52 not applicable.",
            source_rule=f"R52: {r52.text if r52 else 'Specify all the network requirements to be applied to the component specified.'}",
            source_doc="writing_guide", user_excerpt="", user_location="Entire document",
            why="R52 only applies to components with network interfaces. None were detected in this document.",
        ))

    # ── R53: Distinction between semantic I/O and physical I/O ──
    r53 = get_rule_by_id("R53")
    has_io_list = bool(re.search(r"\b(?:list\s+of\s+I/O|input\s+/output|I/O\s+list|semantic\s+I/O|physical\s+I/O)\b", text_lower))
    if has_io_list:
        findings.append(EvidenceFinding(
            check="I_EXTENDED_WG_RULES", severity="pass", section="FUNCTIONAL REQUIREMENTS",
            rule_id="R53",
            message="I/O list detected (R53 compliant — semantic/physical I/O distinction possible).",
            source_rule=f"R53: {r53.text if r53 else 'Rule for distinguishing between the semantic I/O (functional) and the interface physical I/O.'}",
            source_doc="writing_guide",
            user_excerpt=_find_excerpt(user_text, r"list\s+of\s+I/O|input\s+/output|I/O\s+list"),
            user_location="FUNCTIONAL REQUIREMENTS section",
            why="R53 requires distinguishing semantic I/O (functional, §5.1) from physical I/O (interface, §5.3). This prevents ambiguity in data definitions.",
        ))
    else:
        findings.append(EvidenceFinding(
            check="I_EXTENDED_WG_RULES", severity="info", section="FUNCTIONAL REQUIREMENTS",
            rule_id="R53",
            message="No explicit I/O list found — R53 (semantic vs. physical I/O) not verifiable from this document.",
            source_rule=f"R53: {r53.text if r53 else 'Rule for distinguishing between the semantic I/O (functional) and the interface physical I/O.'}",
            source_doc="writing_guide", user_excerpt="", user_location="Entire document",
            why="R53 requires an explicit I/O list to check the semantic/physical distinction against. None was detected — verify manually if the component has data inputs/outputs.",
        ))

    # ── R10: Secondary writers/participants ──
    r10 = get_rule_by_id("R10")
    has_participants = bool(re.search(r"\b(?:participants|secondary\s+(?:writer|editor)|co-?writ(?:er|ten|ed))\b", text_lower))
    if has_participants:
        findings.append(EvidenceFinding(
            check="I_EXTENDED_WG_RULES", severity="pass", section="PARTICIPANTS",
            rule_id="R10",
            message="Participants/secondary writers identified (R10 compliant).",
            source_rule=f"R10: {r10.text if r10 else 'Rules on secondary editors of the RD (§ 0.7).'}",
            source_doc="writing_guide",
            user_excerpt=_find_excerpt(user_text, r"participants|secondary\s+writer|co-?writ"),
            user_location="Document header",
            why="R10 requires secondary writers to be identified. This ensures all contributors are credited and accountable.",
        ))
    else:
        findings.append(EvidenceFinding(
            check="I_EXTENDED_WG_RULES", severity="info", section="PARTICIPANTS",
            rule_id="R10",
            message="No secondary writers/participants mentioned — R10 only applies if others contributed besides the main writer.",
            source_rule=f"R10: {r10.text if r10 else 'Rules on secondary editors of the RD (§ 0.7).'}",
            source_doc="writing_guide", user_excerpt="", user_location="Document header",
            why="R10 governs how secondary/co-writers are credited. It has nothing to check when the document names no secondary contributors.",
        ))

    # ── R01: Conformity matrix / template compliance ──
    # Actually checks for the standard Conformity-matrix table structure
    # (pipe-delimited table rows + real requirement IDs) instead of always
    # emitting "pass" unconditionally — the previous version's own comment
    # ("if we got here... R01 is being satisfied") never inspected the
    # document at all, silently inflating the pass count regardless of
    # whether the document actually follows the template's table format.
    r01 = get_rule_by_id("R01")
    r01_table_rows = [l for l in user_text.split("\n") if "|" in l and l.count("|") >= 2]
    r01_req_ids = set(REQ_ID_RE.findall(user_text))
    if len(r01_table_rows) > 10 and r01_req_ids:
        findings.append(EvidenceFinding(
            check="I_EXTENDED_WG_RULES", severity="pass", section="",
            rule_id="R01",
            message=(
                f"Document uses the standard Conformity-matrix table format "
                f"({len(r01_table_rows)} table rows, {len(r01_req_ids)} requirement "
                f"IDs found) — R01 compliant."
            ),
            source_rule=f"R01: {r01.text if r01 else 'RD written in Word must respect the Conformity matrix [PT0] defined by standard A10 0310.'}",
            source_doc="writing_guide",
            user_excerpt=r01_table_rows[0][:200] if r01_table_rows else "",
            user_location=f"{len(r01_table_rows)} table rows detected",
            why="R01 requires Word-based RDs to follow the Conformity matrix's standard table structure (requirement ID, description, upstream reference). Genuine table-formatted requirement rows were found.",
        ))
    else:
        findings.append(EvidenceFinding(
            check="I_EXTENDED_WG_RULES", severity="warning", section="",
            rule_id="R01",
            message=(
                f"Document does not appear to follow the Conformity-matrix standard "
                f"table format ({len(r01_table_rows)} table rows, {len(r01_req_ids)} "
                f"requirement IDs found) — R01 may be violated."
            ),
            source_rule=f"R01: {r01.text if r01 else 'RD written in Word must respect the Conformity matrix [PT0] defined by standard A10 0310.'}",
            source_doc="writing_guide", user_excerpt="", user_location="Entire document",
            why="R01 requires Word-based RDs to respect the Conformity matrix's standard table structure. Little or no table-formatted requirement content was detected.",
            fix_suggestion="Present requirements in the standard Conformity-matrix 3-column table format (Requirement ID | Description | Upstream requirement).",
        ))

    # ── R24: No SIMULINK block diagrams as requirements ──
    r24 = get_rule_by_id("R24")
    has_simulink = bool(re.search(r"\bSIMULINK\b", text_lower, re.IGNORECASE))
    if not has_simulink:
        findings.append(EvidenceFinding(
            check="I_EXTENDED_WG_RULES", severity="pass", section="REQUIREMENTS",
            rule_id="R24",
            message="No SIMULINK block diagrams used as requirements (R24 compliant).",
            source_rule=f"R24: {r24.text if r24 else 'A SIMULINK block diagram can be used to describe a regulation, but cannot constitute a requirement.'}",
            source_doc="writing_guide", user_excerpt="", user_location="REQUIREMENTS section",
            why="R24 prohibits SIMULINK block diagrams as requirements. They lack precision and temporal specifications. Use mathematical formulas instead.",
        ))
    else:
        findings.append(EvidenceFinding(
            check="I_EXTENDED_WG_RULES", severity="info", section="REQUIREMENTS",
            rule_id="R24",
            message="SIMULINK reference found. R24: SIMULINK can describe regulations but cannot constitute a requirement — verify it's not used as one.",
            source_rule=f"R24: {r24.text if r24 else 'A SIMULINK block diagram can describe a regulation, but cannot constitute a requirement. A regulation should be described by its mathematical formula.'}",
            source_doc="writing_guide",
            user_excerpt=_find_excerpt(user_text, r"SIMULINK"),
            user_location="REQUIREMENTS section",
            why="R24 prohibits SIMULINK block diagrams as requirements. They lack precision and temporal specifications.",
            fix_suggestion="Replace SIMULINK block diagrams with mathematical formulas in requirement statements.",
        ))

    return findings


# ── Helper: extract a section's text ──────────────────────────────
def _extract_section_text(text: str, section_keyword: str) -> str:
    """Extract the text content of a section matching the keyword."""
    lines = text.split("\n")
    user_sections = _detect_user_sections(text)
    start_line = None
    end_line = None
    for name, line in user_sections:
        if section_keyword.lower() in name.lower():
            start_line = line
        elif start_line is not None and section_keyword.lower() not in name.lower():
            end_line = line
            break
    if start_line is None:
        return ""
    end_line = end_line or len(lines)
    return "\n".join(lines[start_line:end_line])


def _find_excerpt_scoped(user_text: str, section_name: str, pattern: str,
                          rules: "ExtractedRules", context_chars: int = 100):
    """
    Search for `pattern` WITHIN `section_name`'s own text first; only fall
    back to a whole-document search if the section itself doesn't contain a
    match. Returns (excerpt, user_location) — the location is honest about
    where the match actually came from, instead of a hardcoded section name
    disconnected from _find_excerpt's real (whole-document) search result.
    A 2026 audit confirmed this mismatch: e.g. an "ACRONYMS section"-labeled
    finding whose excerpt was actually the cover-page author table, because
    _find_excerpt just returns the FIRST match anywhere in the document.
    """
    section_text = _extract_full_section_text(user_text, section_name, rules)
    if section_text:
        excerpt = _find_excerpt(section_text, pattern, context_chars)
        if excerpt:
            return excerpt, f"{section_name} section"
    excerpt = _find_excerpt(user_text, pattern, context_chars)
    if excerpt:
        return excerpt, f"elsewhere in the document (not within the {section_name} section)"
    return "", "NOT FOUND"


def _extract_full_section_text(user_text: str, section_name: str, rules: ExtractedRules) -> str:
    """
    Extract ALL text belonging to a section — INCLUDING its own
    subsections — bounded by the NEXT genuine TOP-LEVEL section from the
    template's own standard plan (rules.section_order), not by just any
    detected heading.

    _extract_section_text stops at the very next heading regardless of
    whether that heading is a real sibling section or one of THIS
    section's own subsections — confirmed during the R14/R15 fix
    (REFERENCE DOCUMENTS' real content lived under a nested "UPSTREAM
    REQUIREMENTS" subsection heading, cut off before it was reached) and
    again here (EXTERNAL INTERFACES REQUIREMENTS, ELECTRICAL INTERFACES:
    each returned under 35 characters — their real content lives under
    their OWN subsections, e.g. "Power supply requirements").

    Scans forward from the section's start line through every detected
    heading, in order, and stops at the first one that is ALSO a genuine
    top-level entry in rules.section_order — this correctly handles a
    subsection that isn't itself in section_order (e.g. "Maintainability"
    is §5.4.4, a subsection of "RAMS REQUIREMENTS", not its own top-level
    entry): it still gets bounded by whatever real top-level section
    comes after it, instead of running to the end of the whole document.
    """
    lines = user_text.split("\n")
    user_sections = _detect_user_sections(user_text)
    user_names = [s[0] for s in user_sections]

    matched = _section_matches(section_name, user_names)
    if not matched:
        return ""
    start_line = next(line for name, line in user_sections if name == matched)

    end_line = len(lines)
    top_level_names = {n.lower() for n in rules.section_order}
    for name, line in user_sections:
        if line <= start_line:
            continue
        if name.lower() in top_level_names:
            end_line = line
            break
    return "\n".join(lines[start_line:end_line])


# ── Check J: Standards/norms consistency (R17) ─────────────────────
# Stellantis internal norm/standard references use bracketed MARK tags —
# [STA20] (Stellantis STAndard) and [N41] (Norme) — confirmed by the
# BeStandard integration (app/config.py BESTANDARD_*), which resolves
# exactly this convention. This is the ONLY identifier real documents
# actually cite by: a requirement says "per [N9]" or "refer to [STA19]",
# never the spelled-out name from the Reference/Title column ("NF EN
# 60352", "IATF 16949"...). Matching those descriptive names as if they
# were independently citable standards produces noise no requirement
# could ever satisfy — so only the Mark tag is tracked, on both the
# declared side and the used side.
STANDARD_REF_RE = re.compile(r"\[(STA\d{1,4}|N\d{1,3})\]", re.IGNORECASE)

# A "declaration row": Stellantis reference/applicable-document tables are
# flattened by DOCX extraction into "[TAG] | Reference | Title" lines
# (Mark | Reference | Title columns). This structural shape is what marks
# a line as a DECLARATION, regardless of which section heading it happens
# to sit under — real documents were found to have this table's real
# content far from where APPLICABLE DOCUMENTS/STANDARDS headings sit
# (those headings can be empty stubs while the actual table lives
# hundreds of lines later, undetected by heading-based section slicing).
_DECLARATION_ROW_RE = re.compile(r"^\s*\[[A-Z0-9_]{1,15}\]\s*\|[^|\n]*\|")


def _flexible_ref_pattern(std: str) -> str:
    """Build a search pattern for a normalized Mark id (e.g. 'STA20') that
    tolerates a stray space between the letter prefix and its number
    ('STA 20') when locating an excerpt in the original text."""
    m = re.match(r"([A-Z]+)(\d[\d_]*)$", std)
    if not m:
        return re.escape(std)
    prefix, digits = m.group(1), m.group(2)
    return re.escape(prefix) + r"\s?" + re.escape(digits)


def _extract_standard_refs(text: str) -> set:
    """Extract normalized Mark identifiers from text (e.g. {'STA20', 'N41'})."""
    refs = set()
    for m in STANDARD_REF_RE.finditer(text):
        token = (m.group(1) or "").strip()
        if token:
            refs.add(re.sub(r"\s+", "", token).upper())
    return refs


def _split_declaration_and_body(user_text: str) -> Tuple[str, str]:
    """
    Split the document into (declaration_text, body_text):
    - declaration_text: every line inside a genuine "Mark | Reference |
      Title" reference-document table, PLUS the text under any heading
      literally named APPLICABLE DOCUMENTS/STANDARDS (covers documents
      that declare standards as plain narrative text instead of a table).
    - body_text: everything else — the actual requirements/prose where
      a standard would be genuinely "used".

    A table is located by scanning for contiguous runs of pipe-containing
    lines that include at least 2 proper "[TAG] | Reference | Title" rows
    — then the WHOLE run is treated as declaration content, including any
    row in the middle that is missing its own [TAG] (observed in real
    documents: a stray row can lose its Mark/tag column while clearly
    still belonging to the same reference table as its neighbors).
    """
    lines = user_text.split("\n")
    n = len(lines)
    is_decl_row = [bool(_DECLARATION_ROW_RE.match(l)) for l in lines]
    has_pipe = ["|" in l for l in lines]

    declared_idx: set = set()
    i = 0
    while i < n:
        if not has_pipe[i]:
            i += 1
            continue
        j = i
        while j < n and has_pipe[j]:
            j += 1
        if sum(is_decl_row[i:j]) >= 2:
            declared_idx.update(range(i, j))
        i = j

    declaration_lines = [lines[k] for k in sorted(declared_idx)]
    body_lines = [l for k, l in enumerate(lines) if k not in declared_idx]

    applicable_text = _extract_section_text(user_text, "APPLICABLE DOCUMENTS") or ""
    standards_text = _extract_section_text(user_text, "STANDARDS") or ""
    declaration_text = "\n".join(declaration_lines) + "\n" + applicable_text + "\n" + standards_text
    body_text = "\n".join(body_lines)
    if applicable_text:
        body_text = body_text.replace(applicable_text, "")
    if standards_text:
        body_text = body_text.replace(standards_text, "")
    return declaration_text, body_text


def check_standards_reference_completeness(
    user_text: str,
    rules: ExtractedRules,
) -> List[EvidenceFinding]:
    """
    Every standard/norm declared in the Applicable Documents table must
    have a REAL, resolvable reference (document/drawing number) — not an
    empty cell, and not an unfilled template placeholder (<<...>> or a
    trailing TBD-style revision index). A declared standard whose
    reference is blank or still a placeholder cannot actually be found or
    verified by anyone reading the specification: the declaration exists
    but points nowhere.

    Distinct from R17 (check_standards_consistency): R17 checks whether a
    declared standard is ever actually USED — this checks whether a
    declared standard can even be LOCATED. Confirmed on the real ASU
    spec: [STA2]'s entire reference is an unfilled placeholder
    ("<<96 xxx xxx 99 xx>>"), and [STA7] appears three times with a real
    document number but an unresolved trailing revision index
    ("<<(1)>>") each time.
    """
    findings: List[EvidenceFinding] = []
    declaration_text, _ = _split_declaration_and_body(user_text)

    for line in declaration_text.split("\n"):
        if not _DECLARATION_ROW_RE.match(line):
            continue
        parts = [p.strip() for p in line.strip().split("|")]
        if len(parts) < 2:
            continue
        mark = parts[0].strip("[] ")
        reference = parts[1].strip()
        if not reference:
            problem = "its reference field is empty"
        elif PLACEHOLDER_RE.search(reference) or TBD_RE.search(reference):
            problem = f"its reference still contains an unfilled placeholder: '{reference}'"
        else:
            continue
        findings.append(EvidenceFinding(
            check="J_STANDARDS_CONSISTENCY",
            severity="warning",
            section="APPLICABLE DOCUMENTS",
            rule_id="TEMPLATE",
            message=f"Standard/norm '{mark}' is declared in Applicable Documents but {problem}.",
            source_rule="Template: every declared applicable document/standard must have a real, resolvable reference — an empty or placeholder reference cannot be located or verified.",
            source_doc="template",
            user_excerpt=line.strip()[:200],
            user_location=f"APPLICABLE DOCUMENTS declaration row for '{mark}'",
            why="A declared standard with no real, resolvable reference cannot be looked up or verified by anyone reading the specification — the declaration exists but points nowhere.",
            fix_suggestion=f"Fill in the real reference/drawing number (and revision index, if applicable) for '{mark}'.",
        ))
    return findings


def check_standards_consistency(
    user_text: str,
    rules: ExtractedRules,
) -> List[EvidenceFinding]:
    """
    Check R17: consistency between the standards/norms DECLARED (in a
    reference-document table row, or under an Applicable Documents/
    Standards heading) and those actually REFERENCED elsewhere in the
    document (requirements, constraints, etc.).

    Emits ONE finding PER problematic standard (not one aggregated
    message), so the report lists every individual standard that has a
    problem:
    - Declared but never cited anywhere else (stale/unused declaration).
    - Referenced in the document (e.g. a requirement citing [STA20])
      but never declared (an undeclared dependency — the compliance
      perimeter is incomplete).

    Emits a single "not applicable" info finding if no standard reference
    exists anywhere in the document.
    """
    findings: List[EvidenceFinding] = []
    r17 = get_rule_by_id("R17")
    r17_text = r17.text if r17 else "Rule on the applicable documents and the requirements (§ 3.2)."

    declaration_text, body_text = _split_declaration_and_body(user_text)
    declared = _extract_standard_refs(declaration_text)
    used_elsewhere = _extract_standard_refs(body_text)

    if not declared and not used_elsewhere:
        findings.append(EvidenceFinding(
            check="J_STANDARDS_CONSISTENCY", severity="info",
            section="APPLICABLE DOCUMENTS",
            rule_id="R17",
            message="No standards/norms detected in this document — R17 not applicable.",
            source_rule=f"R17: {r17_text}",
            source_doc="writing_guide", user_excerpt="", user_location="Entire document",
            why="R17 only applies when the document depends on external standards/norms. None were detected.",
        ))
        return findings

    declared_but_unused = sorted(declared - used_elsewhere)
    used_but_undeclared = sorted(used_elsewhere - declared)

    for std in declared_but_unused:
        findings.append(EvidenceFinding(
            check="J_STANDARDS_CONSISTENCY", severity="warning",
            section="APPLICABLE DOCUMENTS",
            rule_id="R17",
            message=f"Standard/norm '{std}' is declared in Applicable Documents/Standards but never cited by any requirement.",
            source_rule=f"R17: {r17_text}",
            source_doc="writing_guide",
            user_excerpt=_find_line_excerpt(declaration_text, _flexible_ref_pattern(std)),
            user_location="APPLICABLE DOCUMENTS / STANDARDS (declaration table)",
            why="A standard listed as applicable but never cited by any requirement suggests either a stale declaration or a missing requirement that should apply it.",
            fix_suggestion=f"Remove '{std}' from Applicable Documents if it truly doesn't apply, or add the requirement(s) that apply it.",
        ))

    for std in used_but_undeclared:
        findings.append(EvidenceFinding(
            check="J_STANDARDS_CONSISTENCY", severity="warning",
            section="REQUIREMENTS",
            rule_id="R17",
            message=f"Standard/norm '{std}' is cited in the document but NOT declared in Applicable Documents/Standards.",
            source_rule=f"R17: {r17_text}",
            source_doc="writing_guide",
            user_excerpt=_find_line_excerpt(body_text, _flexible_ref_pattern(std)),
            user_location="Referenced in document body",
            why="Every standard/norm a requirement depends on must be declared in Applicable Documents/Standards so the full compliance perimeter is visible and traceable.",
            fix_suggestion=f"Add '{std}' to the Applicable Documents or Standards section.",
        ))

    if not declared_but_unused and not used_but_undeclared:
        findings.append(EvidenceFinding(
            check="J_STANDARDS_CONSISTENCY", severity="pass",
            section="APPLICABLE DOCUMENTS",
            rule_id="R17",
            message=f"All {len(declared)} declared standard(s)/norm(s) are consistently referenced in the document (R17 compliant).",
            source_rule=f"R17: {r17_text}",
            source_doc="writing_guide", user_excerpt="", user_location="APPLICABLE DOCUMENTS / STANDARDS section",
            why="R17 requires consistency between declared applicable documents/standards and their actual use in the document.",
        ))

    return findings


# ── Scoring ───────────────────────────────────────────────────────
def _compute_scores(findings: List[EvidenceFinding], rules: ExtractedRules) -> Dict[str, float]:
    """Compute per-axis scores from the findings."""
    # Axis A: Structure (section coverage)
    struct_findings = [f for f in findings if f.check == "A_SECTION_COVERAGE"]
    struct_errors = sum(1 for f in struct_findings if f.severity == "error")
    total_mandatory = sum(1 for s in rules.mandatory_sections if s.level == 1)
    struct_present = total_mandatory - struct_errors
    struct_ratio = struct_present / total_mandatory if total_mandatory > 0 else 0
    if struct_ratio >= 0.95:
        structure = 0.9 + (struct_ratio - 0.95) * 2.0
    elif struct_ratio >= 0.80:
        structure = 0.6 + (struct_ratio - 0.80) * 2.0
    elif struct_ratio >= 0.60:
        structure = 0.3 + (struct_ratio - 0.60) * 1.5
    else:
        structure = struct_ratio * 0.5

    # Axis C: Template cleanliness (placeholders)
    ph_findings = [f for f in findings if f.check == "C_PLACEHOLDER_RESIDUE"]
    has_placeholders = any(f.severity == "warning" for f in ph_findings)
    if not has_placeholders:
        cleanliness = 1.0
    else:
        # Count total artifacts. Every C-check warning message starts with
        # its count ("N template placeholders…", "N unfilled template
        # variables…", "N TBD/TBC…") — parse the leading integer so ALL
        # artifact types are counted, not just <<...>> placeholders.
        total_artifacts = 0
        for f in ph_findings:
            if f.severity == "warning":
                m = re.match(r"(\d+)", f.message)
                if m:
                    total_artifacts += int(m.group(1))
        if total_artifacts <= 2:
            cleanliness = 0.85
        elif total_artifacts <= 5:
            cleanliness = 0.70
        elif total_artifacts <= 15:
            cleanliness = 0.50
        elif total_artifacts <= 40:
            cleanliness = 0.35
        else:
            cleanliness = 0.20

    # Axis D-G: Requirements quality (format, language, IDs, traceability)
    req_findings = [f for f in findings if f.check.startswith(("D_", "E_", "F_", "G_"))]
    req_pass = sum(1 for f in req_findings if f.severity == "pass")
    req_error = sum(1 for f in req_findings if f.severity == "error")
    req_warn = sum(1 for f in req_findings if f.severity == "warning")
    req_total = req_pass + req_error + req_warn
    if req_total == 0:
        requirements_quality = 0.1
    else:
        requirements_quality = (req_pass * 1.0 + req_warn * 0.5) / req_total
        if req_error > 0:
            requirements_quality *= max(0.3, 1.0 - req_error * 0.2)

    # Axis H+I: Writing guide compliance (includes extended rules).
    # Recommended-section warnings (check A, rule_id WRITING_GUIDE) come
    # from the writing guide too — count them here so they influence the
    # score instead of being reported but scoreless.
    wg_findings = [
        f for f in findings
        if f.check in ("H_WRITING_GUIDE_RULES", "I_EXTENDED_WG_RULES", "J_STANDARDS_CONSISTENCY")
        or (f.check == "A_SECTION_COVERAGE" and f.rule_id == "WRITING_GUIDE")
    ]
    wg_pass = sum(1 for f in wg_findings if f.severity == "pass")
    wg_warn = sum(1 for f in wg_findings if f.severity == "warning")
    wg_info = sum(1 for f in wg_findings if f.severity == "info")
    wg_total = wg_pass + wg_warn + wg_info
    if wg_total == 0:
        writing_guide = 0.5
    else:
        writing_guide = (wg_pass * 1.0 + wg_info * 0.8 + wg_warn * 0.4) / wg_total

    # Axis B: Section order
    order_findings = [f for f in findings if f.check == "B_SECTION_ORDER"]
    order_violations = sum(1 for f in order_findings if f.severity == "warning")
    section_order = max(0.3, 1.0 - order_violations * 0.1) if order_findings else 0.8

    return {
        "structure": round(min(structure, 1.0), 2),
        "section_order": round(section_order, 2),
        "template_cleanliness": round(cleanliness, 2),
        "requirements_quality": round(requirements_quality, 2),
        "writing_guide_compliance": round(writing_guide, 2),
    }


# ── Check K: Semantic writing-guide rules (require judgment, not pattern-matching) ──
#
# Every other check in this file is deterministic: a regex, a keyword, a
# table structure. That's what makes them trustworthy — the same input
# always gives the same, explainable answer. A specific subset of the
# writing-guide rules genuinely cannot be judged that way: they ask
# whether TWO pieces of text are *consistent in meaning* (R42: does the
# dreaded event's wording match its defect mode's wording?), whether a
# requirement is written at the wrong *level of abstraction* (P05: is a
# "what" requirement secretly describing "how"?), or whether content is
# in the *conceptually right place* (R28/R44: is this diagnostic content
# actually about the right kind of diagnostic?). These need an LLM.
#
# To keep that boundary explicit rather than quietly blurring it into the
# deterministic findings: these live under their own check id
# (K_SEMANTIC_ANALYSIS), every finding says outright that it is an
# AI-generated judgment to be verified by a human, and the whole
# function degrades to a single informational finding — never an
# exception — if the LLM is unavailable, misconfigured, or returns
# something unparseable. Disabled by default (see validate_with_evidence's
# include_semantic_analysis parameter) so every existing caller and test
# keeps its current fully offline, deterministic behaviour unless it
# explicitly opts in.
_SEMANTIC_RULE_SECTIONS: List[Tuple[str, str, List[str]]] = [
    ("P03", "Each service must be described autonomously in §5.1 — purely in terms of the service's OWN inputs and "
     "outputs, without requiring knowledge of another system's INTERNAL architecture/implementation to understand "
     "the behavior. Do NOT evaluate this rule against §2.2/general-context narrative text — that introductory text "
     "is expected and allowed to name external actors/systems for scene-setting, and is out of scope for this "
     "check; only the excerpt below (the §5.1 functional requirement) is what to judge. Naming an external system as "
     "merely the SOURCE of a trigger or input signal (e.g. \"the service reacts when input signal X is absent/"
     "present\", even if X's origin is another ECU) is NOT a violation — this is normal black-box input/output "
     "description. A REAL violation looks like: the requirement text itself explains what the OTHER system does "
     "internally, or how the other system's internal state machine/logic works, in order to explain THIS service's "
     "behavior. If the excerpt only names an input signal (whatever its origin) and describes THIS service's own "
     "reaction to it, that is compliant, not a violation.",
     ["FUNCTIONAL REQUIREMENTS"]),
    ("P05", "Requirements must stay at the right level of abstraction (Application = pure functional behavior, with no protocol/transfer detail) — §5.",
     ["FUNCTIONAL REQUIREMENTS"]),
    ("R26", "Every value in the applicative I/O tables must be used in the semantic-level requirements — §5.1. "
     "IMPORTANT: check ALL provided sections, including Maintainability — a value used only in a DTC/fault-recording "
     "requirement there still counts as being used in a semantic-level requirement.",
     ["EXTERNAL INTERFACES REQUIREMENTS", "FUNCTIONAL REQUIREMENTS", "MAINTAINABILITY"]),
    ("R28", "The \"phase of life\" diagnostic must be handled under Maintainability (§5.4.4.5); autodiagnostic must remain in §5.1 with the other use cases. "
     "IMPORTANT: DTC/fault recording, protocol conformance, assembly-phase self-test, download, remote coding, and "
     "traceability requirements ALL correctly belong under Maintainability — do not flag these as \"autodiagnostic\" "
     "violations. Only flag a violation if you find requirement text describing a RUNTIME self-check performed during "
     "normal operation (e.g. an arming-state diagnosis check) actually placed inside the Maintainability excerpt.",
     ["MAINTAINABILITY", "FUNCTIONAL REQUIREMENTS"]),
    ("R34", "The network frame reception protocol must describe the behavior for invalid/unused values, frame loss, and default values in fault mode — §5.3.1.",
     ["EXTERNAL INTERFACES REQUIREMENTS"]),
    ("R35", "The controller must check the consistency of received input data — §5.3.1.",
     ["EXTERNAL INTERFACES REQUIREMENTS"]),
    ("R38", "Wired interfaces must be described via the expected reference catalogs — the wired-interface selection "
     "guide and the [DA8]/[DA9]-style wired-interface catalogs — §5.3.2. IMPORTANT: connector hardware catalogs "
     "(e.g. references named CON1, CON2, or a \"connector specification\"/\"connector technical specification\" "
     "document) are a DIFFERENT document category and do NOT satisfy this rule — a connector-catalog citation is "
     "NOT evidence of compliance. Only a citation of the wired-interface selection guide or a DA8/DA9-style wired-"
     "interface catalog counts.",
     ["ELECTRICAL INTERFACES"]),
    ("R39", "HMI content (color, message readability, icon style, force) must be grouped in the section dedicated to Human-Machine Interfaces — §5.3.4.",
     ["HUMAN-MACHINE INTERFACES"]),
    ("R42", "The statement of a dreaded event must be consistent with the statement of its associated failure mode — §5.4.4.",
     ["DEMONSTRATION OF COMPLIANCE WITH REQUIREMENTS", "RAMS REQUIREMENTS"]),
    ("R44", "Diagnostic, download, remote coding and programming must be grouped in the same paragraph; customer-facing autodiagnostic remains in §5.1 — §5.4.4. "
     "IMPORTANT: this rule is about requirements being SCATTERED — only flag a violation if you can point to a "
     "SPECIFIC diagnostic/download/remote-coding/programming requirement located OUTSIDE the Maintainability excerpt "
     "(e.g. one you also see in the other candidate section provided). If everything relevant is contained within "
     "the Maintainability excerpt, that is compliant grouping, not scattering.",
     ["MAINTAINABILITY"]),
]

# Per-CANDIDATE cap (not per combined, multi-candidate result — a rule with
# 2 candidates gets up to 2x this budget). A 2026 audit against the real ASU
# spec found the OLD behaviour — truncating the JOINED string after
# concatenating every candidate — let whichever candidate came first consume
# the entire budget, silently dropping the second candidate's content 100%
# of the time (confirmed for R26/R28/R42/R44, all 2-candidate rules: their
# second section never reached the LLM at all, regardless of its own size).
_SEMANTIC_MAX_SECTION_CHARS = 4000
_SEMANTIC_TRUNCATION_MARKER = "\n[...remainder truncated, not shown to the reviewer...]"
_SEMANTIC_VERDICT_TO_SEVERITY = {
    "compliant": "pass",
    "violation": "warning",
    "not_applicable": "info",
    "cannot_verify": "info",
}


def check_semantic_writing_guide_rules(user_text: str, rules: ExtractedRules) -> List[EvidenceFinding]:
    """
    LLM-assisted evaluation of the writing-guide rules that require
    judging MEANING rather than matching a pattern (see module comment
    above for which rules and why). Every returned finding is severity
    "pass"/"warning"/"info" mapped from the LLM's own verdict, and every
    one explicitly states in its `why` field that it is an AI judgment
    requiring human verification — never presented as an equally-certain
    peer of the deterministic checks.

    Returns a single informational finding (never raises) if the LLM
    cannot be reached, isn't configured, or returns something that
    doesn't parse as the expected structured response.
    """
    sections_used: Dict[str, str] = {}
    prompt_blocks: List[str] = []
    for rule_id, rule_desc, candidates in _SEMANTIC_RULE_SECTIONS:
        # Truncate EACH candidate independently, THEN join — truncating the
        # joined string instead let whichever candidate came first consume
        # the whole budget, silently excluding every other candidate 100%
        # (confirmed on the real ASU spec for every 2-candidate rule).
        parts = []
        for cand in candidates:
            text = _extract_full_section_text(user_text, cand, rules)
            if not text:
                continue
            if len(text) > _SEMANTIC_MAX_SECTION_CHARS:
                text = text[:_SEMANTIC_MAX_SECTION_CHARS] + _SEMANTIC_TRUNCATION_MARKER
            parts.append(text)
        combined = "\n".join(parts)
        sections_used[rule_id] = combined
        excerpt_note = combined if combined else "[Section missing or not found in the document]"
        prompt_blocks.append(
            f"=== {rule_id} ===\nRule: {rule_desc}\nDocument excerpt:\n{excerpt_note}\n"
        )

    system_prompt = (
        "You are an expert reviewer of Stellantis mechatronics technical "
        "specifications (CTS). You are given a list of writing-guide rules that "
        "require a JUDGMENT ON MEANING — not a simple pattern match (already "
        "covered elsewhere). For EACH rule, you receive the document excerpt "
        "judged relevant (or an explicit mention that the section is missing). "
        "Judge ONLY from the text provided — never invent content. If the "
        "excerpt is missing, empty, or simply says \"NA\", answer "
        "not_applicable. Before judging, check that the excerpt actually names "
        "the SPECIFIC things this rule is about (read the rule text carefully) "
        "— a generic or tangentially related passage that never actually "
        "addresses the rule's subject is not evidence either way: answer "
        "cannot_verify rather than guessing from unrelated text. An excerpt "
        "ending with '[...remainder truncated, not shown to the reviewer...]' "
        "was cut short by length limits — if what you can see does not "
        "resolve the question, answer cannot_verify rather than assuming the "
        "truncated part would have confirmed compliance. A 'violation' verdict "
        "must point to DIRECT textual evidence of the SPECIFIC problem the rule "
        "describes — never infer a violation merely because a section discusses "
        "a related-sounding topic, or because content 'could' be misplaced. If "
        "you cannot quote the exact words that demonstrate the problem, answer "
        "cannot_verify or compliant instead of violation. Likewise, a 'compliant' "
        "verdict must point to evidence that actually satisfies what the rule "
        "requires — a citation of a document from a different, unrelated "
        "reference-document category (read the rule text's own examples of "
        "what counts) does not satisfy it.\n\n"
        "Respond STRICTLY with a JSON array, one object per rule, in this exact "
        "order, with no text before or after:\n"
        '[{"rule_id": "P03", "verdict": "compliant|violation|not_applicable|'
        'cannot_verify", "explanation": "one clear sentence in English '
        'explaining the verdict", "excerpt": "the exact quote from the document '
        'that justifies the verdict, or an empty string"}, ...]'
    )
    user_message = "\n".join(prompt_blocks)

    try:
        from app.embeddings import call_llm
        raw_response = call_llm(system_prompt, user_message, temperature=0.1, max_tokens=2500)
        cleaned = raw_response.strip()
        if cleaned.startswith("```"):
            cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
            cleaned = re.sub(r"\s*```$", "", cleaned)
        parsed = json.loads(cleaned)
        if not isinstance(parsed, list):
            raise ValueError("LLM response is not a JSON array")
    except Exception as exc:
        return [EvidenceFinding(
            check="K_SEMANTIC_ANALYSIS", severity="info", section="",
            rule_id="K_SEMANTIC",
            message="Semantic analysis (AI-assisted) unavailable — could not reach or parse the LLM response.",
            source_rule="Rules requiring judgment on meaning (P03, P05, R26, R28, R34, R35, R38, R39, R42, R44).",
            source_doc="writing_guide", user_excerpt="", user_location="Entire document",
            why=f"The LLM call failed or returned an unusable response ({exc}); these rules still require manual review.",
        )]

    # Build the lookup defensively: a syntactically-valid JSON array can
    # still carry a "rule_id" that isn't a string (e.g. a nested object) —
    # using that directly as a dict key would raise an uncaught TypeError
    # OUTSIDE this function's own try/except, contradicting the "never
    # raises" guarantee this function documents.
    by_rule: Dict[str, dict] = {}
    for item in parsed:
        if not isinstance(item, dict):
            continue
        raw_rule_id = item.get("rule_id")
        if isinstance(raw_rule_id, str):
            by_rule[raw_rule_id] = item

    findings: List[EvidenceFinding] = []
    for rule_id, rule_desc, candidates in _SEMANTIC_RULE_SECTIONS:
        item = by_rule.get(rule_id)
        r = get_rule_by_id(rule_id)
        source_rule_text = f"{rule_id}: {r.text if r else rule_desc}"
        # Which document section(s) this rule's excerpt was actually drawn
        # from — a reviewer checking an AI-assisted excerpt needs to know
        # WHERE to go verify it, not just the excerpt text on its own.
        candidate_location = (
            " / ".join(candidates) + (" sections" if len(candidates) > 1 else " section")
        ) if candidates else "Entire document"
        if not item:
            findings.append(EvidenceFinding(
                check="K_SEMANTIC_ANALYSIS", severity="info", section="",
                rule_id=rule_id,
                message=f"{rule_id}: the AI analysis did not return a verdict for this rule.",
                source_rule=source_rule_text, source_doc="writing_guide",
                user_excerpt="", user_location=candidate_location,
                why="The LLM response omitted this rule — treat as not yet reviewed.",
            ))
            continue
        verdict = item.get("verdict", "cannot_verify")
        if not isinstance(verdict, str):
            verdict = "cannot_verify"
        severity = _SEMANTIC_VERDICT_TO_SEVERITY.get(verdict, "info")
        explanation = str(item.get("explanation", ""))[:400]
        excerpt = str(item.get("excerpt", ""))[:250]
        findings.append(EvidenceFinding(
            check="K_SEMANTIC_ANALYSIS", severity=severity, section="",
            rule_id=rule_id,
            message=f"[AI analysis — to verify] {rule_id}: {explanation or verdict}",
            source_rule=source_rule_text, source_doc="writing_guide",
            user_excerpt=excerpt, user_location=candidate_location,
            why=(
                "This finding comes from an AI-assisted analysis (not a "
                "deterministic rule) because this rule requires judging the "
                "MEANING of the text, not just whether it is present. It must "
                "be verified by a human reviewer before any action is taken."
            ),
            fix_suggestion="Manually verify this point before treating it as final." if severity == "warning" else "",
        ))
    return findings


# ── Main validation function ──────────────────────────────────────
def validate_with_evidence(
    file_name: str,
    user_text: str,
    source_path=None,
    include_semantic_analysis: bool = False,
) -> Dict:
    """
    Validate a user specification against the REAL extracted rules.

    Args:
        file_name: display name of the document.
        user_text: flattened text (from extract_text_from_file).
        source_path: optional path to the ORIGINAL .docx. When given, the
            requirement tables are read structurally so the "Input
            requirement" column can be checked exactly instead of guessed
            from the flattened text. Ignored for non-DOCX sources.
        include_semantic_analysis: when True, also runs
            check_semantic_writing_guide_rules — an LLM-assisted judgment
            of the writing-guide rules that need to evaluate MEANING
            (P03, P05, R26, R28, R34, R35, R38, R39, R42, R44), clearly
            tagged as AI-assisted and requiring human verification.
            Defaults to False so every existing caller keeps today's
            fully deterministic, offline, network-free behaviour unless
            it explicitly opts in — this is a real network call (cost,
            latency, and a dependency on Azure OpenAI being reachable
            and configured), never silently added to the default path.

    Returns a dict with:
      - fileName, overallScore, verdict, scores
      - findings (list of evidence-backed findings)
      - detailed (errors/warnings/passes/info separated)
      - summary, summaryCounts
      - rulesUsed (metadata about the extracted rules)
      - evidence (source rule + user excerpt for every finding)
    """
    rules = extract_all_rules()

    if not user_text or not user_text.strip():
        return {
            "fileName": file_name,
            "overallScore": 0.0,
            "verdict": "NON_COMPLIANT",
            "scores": {},
            "summary": "Empty document — no content to validate.",
            "summaryCounts": {"errors": 1, "warnings": 0, "info": 0, "pass": 0},
            "findings": [{
                "check": "content", "severity": "error", "section": "",
                "rule_id": "CONTENT", "message": "The document is empty or no text could be extracted.",
                "source_rule": "A valid specification must contain content.",
                "source_doc": "system", "user_excerpt": "", "user_location": "NOT FOUND",
                "why": "Without content, the specification cannot be validated.",
                "fix_suggestion": "Upload a valid .docx, .txt, or .pdf file with specification content.",
            }],
            "detailed": {"errors": [], "warnings": [], "info": [], "pass": []},
            "rulesUsed": {"extraction_ok": rules.extraction_ok, "errors": rules.errors},
            "sectionsFound": [], "sectionsMissing": [],
        }

    # Structural read of the requirement tables (exact traceability) when
    # the original .docx is available; [] otherwise → text heuristic.
    req_rows: List[RequirementRow] = []
    if source_path:
        try:
            req_rows = extract_requirement_rows(source_path)
        except Exception:
            req_rows = []

    # Run all checks
    all_findings: List[EvidenceFinding] = []
    all_findings.extend(check_section_coverage(user_text, rules))
    all_findings.extend(check_section_order(user_text, rules))
    all_findings.extend(check_placeholder_residue(user_text, rules))
    all_findings.extend(check_requirement_format(user_text, rules))
    all_findings.extend(check_requirement_language(user_text, rules))
    all_findings.extend(check_requirement_ids(user_text, rules))
    all_findings.extend(check_traceability(user_text, rules, req_rows=req_rows))
    all_findings.extend(check_writing_guide_rules(user_text, rules))
    all_findings.extend(check_extended_writing_guide_rules(user_text, rules))
    all_findings.extend(check_standards_consistency(user_text, rules))
    all_findings.extend(check_standards_reference_completeness(user_text, rules))
    if source_path:
        all_findings.extend(check_dreaded_event_associations(source_path))
    if include_semantic_analysis:
        all_findings.extend(check_semantic_writing_guide_rules(user_text, rules))

    # Compute scores
    scores = _compute_scores(all_findings, rules)

    # Weighted overall score
    weights = {
        "structure": 0.25,
        "section_order": 0.05,
        "template_cleanliness": 0.10,
        "requirements_quality": 0.35,
        "writing_guide_compliance": 0.25,
    }
    overall = sum(scores.get(k, 0) * w for k, w in weights.items())

    # Verdict
    errors = sum(1 for f in all_findings if f.severity == "error")
    warnings = sum(1 for f in all_findings if f.severity == "warning")

    if overall >= 0.80 and errors == 0:
        verdict = "GOOD"
    elif overall >= 0.60 and errors <= 2:
        verdict = "ACCEPTABLE_WITH_FIXES"
    elif overall >= 0.35:
        verdict = "NOT_RELIABLE"
    else:
        verdict = "NON_COMPLIANT"

    # Separate findings
    def _to_dict(f: EvidenceFinding) -> Dict:
        return {
            "check": f.check, "severity": f.severity, "section": f.section,
            "rule_id": f.rule_id, "message": f.message,
            "source_rule": f.source_rule, "source_doc": f.source_doc,
            "user_excerpt": f.user_excerpt, "user_location": f.user_location,
            "why": f.why, "fix_suggestion": f.fix_suggestion,
            "items": f.items,
        }

    findings_list = [_to_dict(f) for f in all_findings]
    errors_list = [_to_dict(f) for f in all_findings if f.severity == "error"]
    warnings_list = [_to_dict(f) for f in all_findings if f.severity == "warning"]
    info_list = [_to_dict(f) for f in all_findings if f.severity == "info"]
    pass_list = [_to_dict(f) for f in all_findings if f.severity == "pass"]

    # Section summary
    user_sections = [s[0] for s in _detect_user_sections(user_text)]
    sections_missing = [f.section for f in all_findings
                        if f.check == "A_SECTION_COVERAGE" and f.severity == "error"]

    # Summary
    total_mandatory = sum(1 for s in rules.mandatory_sections if s.level == 1)
    mandatory_present = total_mandatory - len(sections_missing)
    shall_count = len(SHALL_RE.findall(user_text))
    req_ids = len(set(REQ_ID_RE.findall(user_text)))

    # Count how many unique rule IDs were actually checked
    checked_rule_ids = set()
    for f in all_findings:
        if f.rule_id and f.rule_id not in ("TEMPLATE", "WRITING_GUIDE", "CONTENT", "WG_ACRONYMS", "WG_FIGURES"):
            checked_rule_ids.add(f.rule_id)

    # Rules extracted from the writing guide but NOT covered by any
    # implemented check — reported for transparency so the coverage
    # ratio is honest.
    extracted_rule_ids = {r.rule_id for r in rules.writing_guide_rules}
    unchecked_rule_ids = sorted(extracted_rule_ids - checked_rule_ids)

    summary = (
        f"Overall score: {overall:.0%} — Verdict: {verdict}. "
        f"Structure: {scores.get('structure', 0):.0%} "
        f"({mandatory_present}/{total_mandatory} mandatory sections from template). "
        f"Template cleanliness: {scores.get('template_cleanliness', 0):.0%}. "
        f"Requirements: {scores.get('requirements_quality', 0):.0%} "
        f"({shall_count} 'shall' statements, {req_ids} unique IDs). "
        f"Writing guide: {scores.get('writing_guide_compliance', 0):.0%}. "
        f"Findings: {errors} errors, {warnings} warnings, {len(pass_list)} passes, {len(info_list)} info. "
        f"Rules checked: {len(checked_rule_ids)}/{len(rules.writing_guide_rules)} writing-guide rules + "
        f"{total_mandatory} template sections (100% extracted from source documents)."
    )

    return {
        "fileName": file_name,
        "overallScore": round(overall, 2),
        "verdict": verdict,
        "scores": scores,
        "summary": summary,
        "summaryCounts": {
            "errors": len(errors_list),
            "warnings": len(warnings_list),
            "info": len(info_list),
            "pass": len(pass_list),
        },
        "findings": findings_list,
        "detailed": {
            "errors": errors_list,
            "warnings": warnings_list,
            "info": info_list,
            "pass": pass_list,
        },
        "sectionsFound": user_sections,
        "sectionsMissing": sections_missing,
        "rulesUsed": {
            "extraction_ok": rules.extraction_ok,
            "errors": rules.errors,
            "mandatory_sections_count": total_mandatory,
            "writing_guide_rules_count": len(rules.writing_guide_rules),
            "writing_guide_rules_checked": len(checked_rule_ids),
            "template_instructions_count": len(rules.template_instructions),
            "checked_rule_ids": sorted(checked_rule_ids),
            "unchecked_rule_ids": unchecked_rule_ids,
            "source_documents": [
                "Component_or_Part_Specification_Template 1.docx",
                "Component_or_Part_Specification_Writing_guide 1.docx",
            ],
        },
        "textLength": len(user_text),
    }
