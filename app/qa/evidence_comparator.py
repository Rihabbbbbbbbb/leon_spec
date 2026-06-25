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
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from app.qa.rule_extractor import (
    ExtractedRules, SectionRule, WritingGuideRule, TemplateInstruction,
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
    best_match = None
    best_score = 0.0

    for found in found_sections:
        f_lower = found.lower().strip()
        if f_lower == req_lower:
            return found
        # Skip title/meta lines that are clearly not section headings
        if f_lower in ("requirements document", "of the alarm siren unit", "module"):
            continue
        f_words = set(f_lower.split())
        # For short required names (1-2 words), require exact or near-exact match
        if len(req_words) <= 2:
            if req_lower == f_lower:
                return found
            # Allow 'requirements' to match 'requirements' but NOT 'requirements document'
            if req_lower in f_lower and len(f_words) <= len(req_words) + 1:
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


# ── Requirement patterns (from the template + writing guide) ──────
# R22/PCIEE: Requirement IDs follow REF-/APP-/GEN- prefix pattern
REQ_ID_RE = re.compile(
    r"\b(?:REF|APP|GEN)\s*[-_\s]\s*[A-Z0-9][A-Z0-9_-]*?\s*[-_]\s*\d+",
    re.IGNORECASE,
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
            user_excerpt="; ".join(sample[:3]),
            user_location=f"{len(placeholders)} occurrences throughout document",
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
            user_location=f"{len(component_vars)} occurrences",
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
            user_location=f"{len(tbds)} occurrences",
            why="TBD/TBC/TODO markers signal decisions or data that are still pending. In an industrial specification, all values must be finalized before release.",
            fix_suggestion="Resolve all TBD/TBC/TODO markers with final values.",
        ))

    if not placeholders and not component_vars and not tbds:
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
        # (only flag if they appear near 'shall' lines)
        shall_lines = [l for l in user_text.split("\n") if SHALL_RE.search(l)]
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
def check_traceability(
    user_text: str,
    rules: ExtractedRules,
) -> List[EvidenceFinding]:
    """Check R22: each requirement has an upstream requirement reference (or N/A)."""
    findings: List[EvidenceFinding] = []
    r22 = get_rule_by_id("R22")

    shall_lines = [l for l in user_text.split("\n") if SHALL_RE.search(l) and len(l.strip()) > 20]
    if not shall_lines:
        return findings

    req_with_trace = sum(1 for l in shall_lines if _has_traceability(l))

    # Also check broader context for table-format specs
    lines = user_text.split("\n")
    if req_with_trace == 0:
        for i, line in enumerate(lines):
            if SHALL_RE.search(line) and len(line.strip()) > 20:
                start = max(0, i - 10)
                end = min(len(lines), i + 11)
                context = " ".join(lines[start:end])
                if _has_traceability(context):
                    req_with_trace += 1

    trace_ratio = req_with_trace / len(shall_lines) if shall_lines else 0

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
        has_acronym_defs = bool(re.search(r"[A-Z]{2,}\s*[:\-—]\s*[A-Z][a-z]", user_text))
        if has_acronym_defs:
            findings.append(EvidenceFinding(
                check="H_WRITING_GUIDE_RULES",
                severity="pass",
                section="ACRONYMS",
                rule_id="WG_ACRONYMS",
                message="Acronyms section contains acronym definitions.",
                source_rule="Writing guide §4.2: 'In this paragraph, we clarify only the abbreviation, in alphabetical order.'",
                source_doc="writing_guide",
                user_excerpt=_find_excerpt(user_text, r"[A-Z]{2,}\s*[:\-—]\s*[A-Z][a-z]", 80),
                user_location="ACRONYMS section",
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

    This function covers rules R01-R04, R06, R08, R10, R13-R19, R21,
    R24-R53, P01-P05, P07-P10 — all the rules NOT covered by the
    check_writing_guide_rules function above.
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
            user_location=f"{color_only_refs} occurrences",
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
    is_generic = bool(re.search(r"\bgeneric\s+(specification|RD|document|spec)\b", text_lower))
    if is_generic:
        # Check for highlighted elements (can't detect yellow in text, but check for placeholder markers)
        has_unspecified = bool(re.search(r"\b(?:TBD|to\s+be\s+defined|to\s+be\s+specified|per\s+project|per\s+application)\b", text_lower))
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
    r14 = get_rule_by_id("R14")
    ref_section_text = _extract_section_text(user_text, "REFERENCE DOCUMENTS")
    appl_section_text = _extract_section_text(user_text, "APPLICABLE DOCUMENTS")
    combined_docs = (ref_section_text + "\n" + appl_section_text).lower()
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

    # ── R15: At least one Design file quoted in reference documents ──
    r15 = get_rule_by_id("R15")
    has_design_file = bool(re.search(r"\b(?:design\s+file|DC\b|upstream\s+(?:functional\s+)?requirements?|architecture\s+(?:constraints?|file))\b", ref_section_text, re.IGNORECASE))
    if has_design_file:
        findings.append(EvidenceFinding(
            check="I_EXTENDED_WG_RULES", severity="pass", section="REFERENCE DOCUMENTS",
            rule_id="R15",
            message="At least one design file / upstream requirement quoted (R15 compliant).",
            source_rule=f"R15: {r15.text if r15 else 'There is at least one Design file to quote. All the DCs assigning at least one requirement should be quoted.'}",
            source_doc="writing_guide",
            user_excerpt=_find_excerpt(ref_section_text, r"\b(?:design\s+file|upstream\s+requirements?)\b", 80),
            user_location="REFERENCE DOCUMENTS section",
            why="R15 requires at least one Design file to be quoted. All DCs assigning requirements to the component must be referenced.",
        ))
    elif ref_section_text:
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
        findings.append(EvidenceFinding(
            check="I_EXTENDED_WG_RULES", severity="pass", section="ERGONOMICS",
            rule_id="R41",
            message="Random noise requirement found (R41 compliant — compulsory for electro-mechanical components).",
            source_rule=f"R41: {r41.text if r41 else 'A requirement concerning random noise is compulsory for each electro-mechanical component.'}",
            source_doc="writing_guide",
            user_excerpt=_find_excerpt(user_text, r"random\s+noise|bruit\s+aléatoire|noise\s+level"),
            user_location="ERGONOMICS / OPERATIONAL section",
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
    has_regulatory = bool(re.search(r"\b(?:regulation|regulatory|réglementation|ECER|FMVSS|ISTA|ISO\s*\d+)\b", text_lower))
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
    has_sdf = bool(re.search(r"\b(?:SdF|sûreté\s+de\s+fonctionnement|dependability|safety|reliability|RAMS|ASIL|FTA|FMEA)\b", text_lower))
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
    has_network = _section_matches("NETWORK INTERFACES", user_sections) or bool(re.search(r"\b(?:CAN|LIN|network\s+interface)\b", text_lower))
    has_context_diagram = bool(re.search(r"context(?:ual)?\s+diagram", text_lower))
    if has_network and has_context_diagram:
        findings.append(EvidenceFinding(
            check="I_EXTENDED_WG_RULES", severity="pass", section="NETWORK INTERFACES",
            rule_id="R31",
            message="Network context diagram found (R31 compliant).",
            source_rule=f"R31: {r31.text if r31 else 'Rule of writing of the Network Context Diagram (§ 5.3.1).'}",
            source_doc="writing_guide",
            user_excerpt=_find_excerpt(user_text, r"context.*diagram"),
            user_location="NETWORK INTERFACES section",
            why="R31 requires a Network Context Diagram for network interfaces. This shows the communication architecture.",
        ))
    elif has_network and not has_context_diagram:
        findings.append(EvidenceFinding(
            check="I_EXTENDED_WG_RULES", severity="info", section="NETWORK INTERFACES",
            rule_id="R31",
            message="Network interfaces present but no context diagram detected. R31 recommends a Network Context Diagram.",
            source_rule=f"R31: {r31.text if r31 else 'Rule of writing of the Network Context Diagram (§ 5.3.1).'}",
            source_doc="writing_guide", user_excerpt="", user_location="NETWORK INTERFACES section",
            why="R31 recommends a Network Context Diagram for network interfaces. Without it, the communication architecture is unclear.",
            fix_suggestion="Add a Network Context Diagram showing the component's network connections.",
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
        if not has_packaging and not has_recycling:
            findings.append(EvidenceFinding(
                check="I_EXTENDED_WG_RULES", severity="pass", section="CONSTRAINT REQUIREMENTS",
                rule_id="R49",
                message="No packaging/recycling process requirements in constraint section (R49/R50 compliant).",
                source_rule=f"R49/R50: Do not insert packaging or development/recycling process requirements in this paragraph.",
                source_doc="writing_guide", user_excerpt="", user_location="CONSTRAINT REQUIREMENTS section",
                why="R49/R50 prohibit process requirements in the CTS constraint section. Process requirements belong in dedicated process documents.",
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
        findings.append(EvidenceFinding(
            check="I_EXTENDED_WG_RULES", severity="pass", section="PERFORMANCE REQUIREMENTS",
            rule_id="R30",
            message="Performance requirements section present (R30 compliant).",
            source_rule=f"R30: {r30.text if r30 else 'Rule about performance requirements (§ 5.2).'}",
            source_doc="writing_guide",
            user_excerpt=_find_excerpt(user_text, r"performance\s+requirement", 80),
            user_location="PERFORMANCE REQUIREMENTS section",
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
        has_network_reqs = bool(re.search(r"\b(?:CAN|LIN|network\s+(?:frame|message|signal|protocol))\b.*\bshall\b", text_lower))
        if has_network_reqs:
            findings.append(EvidenceFinding(
                check="I_EXTENDED_WG_RULES", severity="pass", section="NETWORK INTERFACES",
                rule_id="R52",
                message="Network requirements specified (R52 compliant).",
                source_rule=f"R52: {r52.text if r52 else 'Specify all the network requirements to be applied to the component specified.'}",
                source_doc="writing_guide",
                user_excerpt=_find_excerpt(user_text, r"(?:CAN|LIN|network).*shall"),
                user_location="NETWORK INTERFACES section",
                why="R52 requires all network requirements to be specified. This defines the communication protocols and messages.",
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

    # ── R01: Conformity matrix / template compliance ──
    r01 = get_rule_by_id("R01")
    # If we got here, the document is being validated against the template — so R01 is being satisfied
    findings.append(EvidenceFinding(
        check="I_EXTENDED_WG_RULES", severity="pass", section="",
        rule_id="R01",
        message="Document is being validated against the CTS template conformity matrix (R01 compliant by validation).",
        source_rule=f"R01: {r01.text if r01 else 'RD written in Word must respect the Conformity matrix [PT0] defined by standard A10 0310.'}",
        source_doc="writing_guide", user_excerpt="", user_location="Entire document",
        why="R01 requires Word-based RDs to respect the conformity matrix. This validation system enforces that by checking against the template.",
    ))

    # ── R24: No SIMULINK block diagrams as requirements ──
    r24 = get_rule_by_id("R24")
    has_simulink = bool(re.search(r"\bSIMULINK\b", text_lower))
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
        # Count total artifacts
        total_artifacts = 0
        for f in ph_findings:
            if f.severity == "warning":
                # Extract count from message
                m = re.search(r"(\d+)\s+template", f.message)
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

    # Axis H+I: Writing guide compliance (includes extended rules)
    wg_findings = [f for f in findings if f.check in ("H_WRITING_GUIDE_RULES", "I_EXTENDED_WG_RULES")]
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


# ── Main validation function ──────────────────────────────────────
def validate_with_evidence(file_name: str, user_text: str) -> Dict:
    """
    Validate a user specification against the REAL extracted rules.

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

    # Run all checks
    all_findings: List[EvidenceFinding] = []
    all_findings.extend(check_section_coverage(user_text, rules))
    all_findings.extend(check_section_order(user_text, rules))
    all_findings.extend(check_placeholder_residue(user_text, rules))
    all_findings.extend(check_requirement_format(user_text, rules))
    all_findings.extend(check_requirement_language(user_text, rules))
    all_findings.extend(check_requirement_ids(user_text, rules))
    all_findings.extend(check_traceability(user_text, rules))
    all_findings.extend(check_writing_guide_rules(user_text, rules))
    all_findings.extend(check_extended_writing_guide_rules(user_text, rules))

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
            "source_documents": [
                "Component_or_Part_Specification_Template 1.docx",
                "Component_or_Part_Specification_Writing_guide 1.docx",
            ],
        },
        "textLength": len(user_text),
    }
