"""
Specification structure validator — checks an uploaded spec file against the
Stellantis CTS template structure and writing guide rules.

Validation checks:
1. SECTION COVERAGE — mandatory CTS sections present?
2. PLACEHOLDER DETECTION — template artifacts remaining (<<...>>, TBD, XXX)?
3. REQUIREMENT QUALITY — requirement IDs, "shall" language, traceability?
4. WRITING GUIDE COMPLIANCE — requirement format, section ordering, terminology?
5. SCORING — per-axis scores + overall verdict

All checks are deterministic (no LLM) and auditable.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import List, Dict, Tuple, Optional


# ── Mandatory CTS sections (from the Stellantis template) ─────────
MANDATORY_SECTIONS = [
    "PURPOSE",
    "SCOPE",
    "SYSTEM DEVELOPMENT CONTEXT",
    "GENERAL DESCRIPTION",
    "SYSTEM ROLES",
    "PHYSICAL SYSTEM ARCHITECTURE",
    "QUOTED DOCUMENTS",
    "REFERENCE DOCUMENTS",
    "APPLICABLE DOCUMENTS",
    "TERMINOLOGY",
    "GLOSSARY",
    "ACRONYMS",
    "REQUIREMENTS",
    "FUNCTIONAL REQUIREMENTS",
    "PERFORMANCE REQUIREMENTS",
    "EXTERNAL INTERFACES",
    "OPERATIONAL REQUIREMENTS",
    "RAMS REQUIREMENTS",
    "CONSTRAINT REQUIREMENTS",
]

RECOMMENDED_SECTIONS = [
    "SYSTEM DIVERSITY",
    "MISSION PROFILE",
    "LIFETIME",
    "ERGONOMICS",
    "SAFETY REQUIREMENTS",
    "MAINTAINABILITY",
    "PRODUCT QUALITY",
    "DESIGN AND MANUFACTURING",
    "ENVIRONMENT CONDITIONS",
    "INTEGRATION AND VALIDATION",
    "DEMONSTRATION OF COMPLIANCE",
    "TRACEABILITY",
]

# ── Placeholder patterns ───────────────────────────────────────────
PLACEHOLDER_RE = re.compile(r"<<[^>]*>>")
TBD_RE = re.compile(r"\b(TBD|TBC|TODO|XXX)\b", re.IGNORECASE)
COMPONENT_NAME_RE = re.compile(r"<component name>|<part name>|<Part name>|<name of the Model>|<reference>|<PSP>")


# ── Requirement patterns ──────────────────────────────────────────
REQ_ID_RE = re.compile(
    # Matches requirement IDs like:
    #   REF-PSP-COMP-001
    #   REF- ASU-CD-EXIFUNC-014       (optional space after prefix)
    #   GEN-F1-CD-FUNC-09             (digit after prefix, multi-segment)
    #   Gen-VHL-FD-TFx-001-0-1-02     (mixed case prefix)
    #   GEN-5-E-03-CD-DURA-0001       (digit-first middle, many hyphens)
    # Uses lazy quantifier so trailing digits are matched separately.
    r"\b(?:REF|APP|GEN)\s*[-_\s]\s*"  # prefix + separator (hyphen/underscore)
    r"[A-Z0-9]"                        # first middle char (letter or digit)
    r"[A-Z0-9_-]*?"                    # rest of middle (lazy — gives up for trailing)
    r"\s*[-_]\s*"                      # separator before numeric suffix
    r"\d+",                            # trailing numeric ID
    re.IGNORECASE,
)
SHALL_RE = re.compile(r"\bshall\b", re.IGNORECASE)

# ── Traceability detection (broad, real-world patterns) ───────────
# Stellantis specs use diverse traceability formats. We detect ALL of them.
_TRACE_PATTERNS = [
    # 1. Explicit traceability phrases
    re.compile(r"\binput\s+requirement\b", re.IGNORECASE),
    re.compile(r"\bderived\s+from\b", re.IGNORECASE),
    re.compile(r"\bbased\s+on\b.{0,50}(?:requirement|spec|standard|regulation)", re.IGNORECASE),
    re.compile(r"\b(?:traced|traceable)\s+to\b", re.IGNORECASE),
    re.compile(r"\boriginating\s+(?:from|requirement)\b", re.IGNORECASE),
    re.compile(r"\bupstream\s+(?:requirement|source)\b", re.IGNORECASE),
    re.compile(r"\bsource\s+(?:requirement|document|spec)\b", re.IGNORECASE),
    # 2. Vehicle Function IDs (Stellantis: VF_nnnn links to upstream functions)
    re.compile(r"\bVF_\d{2,6}\b"),
    # 3. Stellantis Corporate Standards (CS.nnnnn)
    re.compile(r"\bCS\.\d{4,6}\b"),
    # 4. International/industry standards
    re.compile(r"\bISO\s*\d{4,6}(?:[:\-]\d+)?\b", re.IGNORECASE),
    re.compile(r"\bSAE[-\s]?[JZ]\d+\b", re.IGNORECASE),
    re.compile(r"\bECER\d{2,3}\b", re.IGNORECASE),
    re.compile(r"\bFMVSS\s*\d+\b", re.IGNORECASE),
    re.compile(r"\bISTA\s*\d[\.\d]*\b", re.IGNORECASE),
    # 5. Stellantis annotation markers (metadata with traceability info)
    re.compile(r"PSA_Comments@"),
    re.compile(r"Att_Sdf@"),
    # 6. Bracketed source references like [SSD_AUE], [Req_xxx]
    re.compile(r"\[([A-Z]{2,}[_\s][A-Z]{2,}[_\d]*)\]"),
    # 7. Traceability section markers
    re.compile(r"\b[tT]raceability\b"),
    re.compile(r"\btraced?\s+(?:to|from)\b", re.IGNORECASE),
    # 8. N/A marker (design choice, no upstream requirement)
    re.compile(r"\bN/A\b"),
]

ASIL_RE = re.compile(r"ASIL[_\s]?(?:A|B|C|D)\b", re.IGNORECASE)


def _has_traceability(line: str) -> bool:
    """Check if a requirement line contains any traceability indicator."""
    for pat in _TRACE_PATTERNS:
        if pat.search(line):
            return True
    return False


# ── Data structures ───────────────────────────────────────────────
@dataclass
class Finding:
    """A single validation finding with rationale."""
    check: str          # which check produced this
    severity: str       # "error" | "warning" | "info"
    section: str        # related section name (or "")
    message: str        # human-readable description
    evidence: str       # excerpt from the document (or "")
    why: str = ""       # WHY this matters — rationale for the engineer


@dataclass
class ValidationReport:
    """Complete validation report for a spec file."""
    file_name: str
    overall_score: float = 0.0          # 0.0 - 1.0
    verdict: str = "UNKNOWN"            # GOOD | ACCEPTABLE_WITH_FIXES | NOT_RELIABLE | NON_COMPLIANT
    scores: Dict[str, float] = field(default_factory=dict)  # per-axis scores
    findings: List[Finding] = field(default_factory=list)
    sections_found: List[str] = field(default_factory=list)
    sections_missing: List[str] = field(default_factory=list)
    sections_recommended_missing: List[str] = field(default_factory=list)
    placeholder_count: int = 0
    tbd_count: int = 0
    requirement_count: int = 0
    requirement_with_id: int = 0
    requirement_with_shall: int = 0
    requirement_with_traceability: int = 0
    text_length: int = 0
    summary: str = ""


# ── Section detection ─────────────────────────────────────────────
def _detect_sections(text: str) -> List[str]:
    """Detect section headings in the document text."""
    sections = []
    lines = text.split("\n")
    seen = set()

    # Pattern 1: ALLCAPS headings (e.g. "PURPOSE", "SCOPE", "RAMS REQUIREMENTS")
    allcaps_re = re.compile(r"^([A-Z][A-Z\s/()\-&,:;.\u2013\u2014]{2,})$")
    # Pattern 1b: Title Case headings (e.g. "Reference documents", "Functional requirements")
    # Also matches mixed-case with ALLCAPS words like "RAMS requirements", "HMI requirements"
    titlecase_re = re.compile(r"^([A-Z][A-Za-z]+(?:\s+(?:[A-Z][A-Za-z]+|[a-z]+)){0,5})$")
    # Pattern 2: Numbered headings (e.g. "2.1 Purpose", "6.2.4.4.1 Strength Test")
    numbered_re = re.compile(r"^\d+(?:\.\d+)*\.?\s+([A-Z][A-Za-z\s/()\-&,:;.]{2,})$")
    # Pattern 3: Tab-separated TOC headings (e.g. "2\tPURPOSE AND SCOPE\t9")
    toc_re = re.compile(r"^\d+\t([A-Z][A-Za-z\s/()\-&,:;.]{2,})\t\d+")

    # Known CTS section keywords for Title Case matching
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
        "configuration", "parameters", "appendix", "annex", "component",
        "output", "input", "analysis", "noise", "water", "tightness",
        "corrosion", "electronic", "mechanical", "testing",
    }

    for line in lines:
        stripped = line.strip()
        if not stripped or len(stripped) > 100:
            continue

        # Skip numbered list items (e.g. "1. Coffee", "2. Cola")
        if re.match(r"^\d+\.\s+[A-Z][a-z]", stripped):
            continue

        # Try ALLCAPS — require high uppercase ratio to filter out Title Case lines
        m = allcaps_re.match(stripped)
        if m:
            name = m.group(1).strip().rstrip(":")
            if len(name) >= 3 and name not in seen:
                letters = [c for c in name if c.isalpha()]
                if letters:
                    upper_ratio = sum(1 for c in letters if c.isupper()) / len(letters)
                    if upper_ratio >= 0.8:
                        sections.append(name)
                        seen.add(name)
                        continue

        # Try Title Case headings (e.g. "Reference documents", "Functional requirements")
        # Only accept if the words match known CTS section keywords
        m = titlecase_re.match(stripped)
        if m:
            name = m.group(1).strip().rstrip(":")
            if len(name) >= 3 and name not in seen:
                words = name.lower().split()
                # At least one word must be a CTS keyword
                if any(w in cts_keywords for w in words):
                    sections.append(name)
                    seen.add(name)
                    continue

        # Try numbered headings (e.g. "2.1 Purpose")
        m = numbered_re.match(stripped)
        if m:
            name = m.group(1).strip()
            if name not in seen and len(name) >= 3:
                sections.append(name)
                seen.add(name)
                continue

        # Try TOC format
        m = toc_re.match(stripped)
        if m:
            name = m.group(1).strip()
            if name not in seen and len(name) >= 3:
                sections.append(name)
                seen.add(name)
                continue

    return sections


def _section_matches(required: str, found_sections: List[str]) -> bool:
    """Check if a required section name matches any found section (case-insensitive)."""
    req_lower = required.lower()
    req_words = set(req_lower.split())
    for found in found_sections:
        f_lower = found.lower()
        # Direct substring match
        if req_lower in f_lower or f_lower in req_lower:
            return True
        # Word overlap: if most required words appear in the found section
        f_words = set(f_lower.split())
        overlap = req_words & f_words
        # Require at least 60% of required words to match
        if len(req_words) > 0 and len(overlap) / len(req_words) >= 0.6:
            return True
    return False


# ── Placeholder detection ─────────────────────────────────────────
def _count_placeholders(text: str) -> Tuple[int, int, int]:
    """Return (placeholder_count, tbd_count, component_name_placeholder_count)."""
    placeholders = len(PLACEHOLDER_RE.findall(text))
    tbds = len(TBD_RE.findall(text))
    component_names = len(COMPONENT_NAME_RE.findall(text))
    return placeholders, tbds, component_names


# ── Requirement analysis ──────────────────────────────────────────
def _analyze_requirements(text: str) -> Dict:
    """Analyze requirement quality in the document.
    
    Handles both inline IDs (ID on same line as 'shall') and table-format IDs
    (ID on a different line than 'shall', common in DOCX tables).
    """
    lines = text.split("\n")
    req_lines = []
    for line in lines:
        if SHALL_RE.search(line) and len(line.strip()) > 20:
            req_lines.append(line.strip())

    # Count unique requirement IDs globally (across entire document)
    all_ids = set(REQ_ID_RE.findall(text))
    unique_id_count = len(all_ids)

    # Count IDs that appear on the same line as "shall" (inline format)
    inline_id_count = sum(1 for l in req_lines if REQ_ID_RE.search(l))

    # For table-format specs: search broader context around each shall line
    nearby_id_count = 0
    if unique_id_count > 0 and inline_id_count == 0:
        for i, line in enumerate(lines):
            if SHALL_RE.search(line) and len(line.strip()) > 20:
                # Check ±10 lines for an ID (covers multi-line table rows)
                start = max(0, i - 10)
                end = min(len(lines), i + 11)
                context = " ".join(lines[start:end])
                if REQ_ID_RE.search(context):
                    nearby_id_count += 1

    # Best estimate: inline + nearby, or global unique count as floor.
    # In table-format specs, each unique ID represents a distinct requirement.
    req_with_id = max(inline_id_count, nearby_id_count, 0)
    if req_with_id == 0 and unique_id_count > 0:
        # Document has formal IDs — use unique count (capped at total)
        req_with_id = min(unique_id_count, len(req_lines)) if req_lines else unique_id_count

    req_with_traceability = sum(1 for l in req_lines if _has_traceability(l))

    return {
        "total": len(req_lines),
        "with_id": req_with_id,
        "with_shall": len(req_lines),  # all have "shall" by definition
        "with_traceability": req_with_traceability,
    }


# ── Scoring ───────────────────────────────────────────────────────
def _score_structure(sections_found: List[str]) -> Tuple[float, List[str], List[str], List[str]]:
    """Score structure based on mandatory section coverage."""
    missing = []
    for req in MANDATORY_SECTIONS:
        if not _section_matches(req, sections_found):
            missing.append(req)

    recommended_missing = []
    for rec in RECOMMENDED_SECTIONS:
        if not _section_matches(rec, sections_found):
            recommended_missing.append(rec)

    present = len(MANDATORY_SECTIONS) - len(missing)
    ratio = present / len(MANDATORY_SECTIONS)

    if ratio >= 0.95:
        score = 0.9 + (ratio - 0.95) * 2.0
    elif ratio >= 0.80:
        score = 0.6 + (ratio - 0.80) * 2.0
    elif ratio >= 0.60:
        score = 0.3 + (ratio - 0.60) * 1.5
    else:
        score = ratio * 0.5

    return min(score, 1.0), missing, recommended_missing, sections_found


def _score_template_cleanliness(placeholders: int, tbds: int, component_names: int, doc_len: int) -> Tuple[float, int]:
    """Score based on remaining template artifacts."""
    total = placeholders + tbds + component_names
    if total == 0:
        return 1.0, 0
    density = total / max(doc_len / 1000, 1)
    if total <= 2:
        return 0.85, total
    elif total <= 5:
        return 0.70, total
    elif total <= 15:
        return 0.50, total
    elif total <= 40:
        return 0.35, total
    else:
        return 0.20, total


def _score_requirements(req_analysis: Dict) -> Tuple[float, str]:
    """Score requirement quality."""
    total = req_analysis["total"]
    if total == 0:
        return 0.1, "No requirements using 'shall' language found"

    with_id = req_analysis["with_id"]
    with_trace = req_analysis["with_traceability"]

    id_ratio = with_id / total if total > 0 else 0
    trace_ratio = with_trace / total if total > 0 else 0

    # Weighted: 50% having requirements, 30% IDs, 20% traceability
    base = min(total / 20, 1.0) * 0.5
    id_score = id_ratio * 0.3
    trace_score = trace_ratio * 0.2

    score = base + id_score + trace_score
    detail = f"{total} requirements found, {with_id} with IDs ({round(id_ratio*100)}%), {with_trace} with traceability ({round(trace_ratio*100)}%)"
    return min(score, 1.0), detail


# ── Writing guide rationale map ────────────────────────────────────
_WG_WHY_MAP = {
    "No table of updates / revision history found":
        "A revision history tracks who changed what and when. It is essential for audit trails, "
        "configuration management, and understanding which version of the spec a supplier or "
        "test team is working from. Without it, there is no formal record of spec evolution.",
    "No author or document identification found":
        "Every specification must identify its author/owner and have a unique document ID "
        "(e.g. RSP-XXX). This enables accountability — if questions arise about a requirement, "
        "the author can be consulted. It also prevents confusion with other specifications.",
    "Requirements use 'should'/'may' instead of mandatory 'shall' language":
        "In engineering specifications, 'shall' indicates a mandatory requirement, while "
        "'should' is a recommendation and 'may' is optional. Using non-mandatory language "
        "creates ambiguity — suppliers may treat critical requirements as optional. "
        "The ISO/IEC Directives Part 2 and Stellantis writing guide require 'shall' for "
        "all binding requirements.",
    "No numbered figures or tables found":
        "Numbered figures and tables enable unambiguous cross-referencing in the text "
        "('see Figure 3'). Without numbering, reviewers and test engineers cannot efficiently "
        "navigate between textual descriptions and visual representations.",
    "Acronyms section present but no acronym definitions found":
        "The ACRONYMS section exists but contains no actual definitions. Every acronym used "
        "in the specification (e.g. ASU, ADML, NFC) must be defined once, usually in this "
        "section. Undefined acronyms cause confusion, especially for new team members or "
        "external suppliers.",
}


def _score_writing_guide(text: str, sections_found: List[str]) -> Tuple[float, List[str]]:
    """Score compliance with writing guide rules."""
    findings = []
    score = 1.0

    # Check 1: Document should have a table of updates / revision history
    if not any("update" in s.lower() or "revision" in s.lower() or "history" in s.lower() for s in sections_found):
        if not re.search(r"table\s+of\s+updates|revision\s+history|update\s+history", text, re.IGNORECASE):
            findings.append("No table of updates / revision history found")
            score -= 0.1

    # Check 2: Should have author / identification info
    if not re.search(r"author|identification|RSP-\d+", text, re.IGNORECASE):
        findings.append("No author or document identification found")
        score -= 0.1

    # Check 3: Requirements should use "shall" language (not "should" or "may")
    should_count = len(re.findall(r"\bshould\b", text, re.IGNORECASE))
    may_count = len(re.findall(r"\bmay\b", text, re.IGNORECASE))
    shall_count = len(re.findall(r"\bshall\b", text, re.IGNORECASE))
    if shall_count == 0 and (should_count > 5 or may_count > 5):
        findings.append("Requirements use 'should'/'may' instead of mandatory 'shall' language")
        score -= 0.15

    # Check 4: Check for figure/table numbering
    if not re.search(r"figure\s+<?\d+|picture\s+\d+|table\s+\d+", text, re.IGNORECASE):
        findings.append("No numbered figures or tables found")
        score -= 0.05

    # Check 5: Check for acronyms section content
    if _section_matches("ACRONYMS", sections_found):
        if not re.search(r"[A-Z]{2,}\s*[:\-—]\s*[A-Z]", text):
            findings.append("Acronyms section present but no acronym definitions found")
            score -= 0.05

    return max(score, 0.0), findings


# ── Main validation function ──────────────────────────────────────
def validate_specification(file_name: str, text: str) -> ValidationReport:
    """
    Validate a specification document against the Stellantis CTS template
    structure and writing guide rules.

    Args:
        file_name: name of the uploaded file
        text: extracted plain text from the file

    Returns:
        ValidationReport with scores, findings, and verdict
    """
    report = ValidationReport(file_name=file_name, text_length=len(text))

    if not text or not text.strip():
        report.verdict = "NON_COMPLIANT"
        report.summary = "Empty document — no content to validate."
        report.findings.append(Finding(
            check="content", severity="error", section="",
            message="The document is empty or no text could be extracted.",
            evidence="",
            why="Without content, the specification cannot be validated against the CTS template. Upload a valid .docx, .txt, or .pdf file with specification content.",
        ))
        return report

    # 1. Detect sections
    sections_found = _detect_sections(text)
    report.sections_found = sections_found

    # 2. Score structure
    struct_score, missing, rec_missing, _ = _score_structure(sections_found)
    report.sections_missing = missing
    report.sections_recommended_missing = rec_missing
    report.scores["structure"] = round(struct_score, 2)

    for sec in missing:
        report.findings.append(Finding(
            check="structure", severity="error", section=sec,
            message=f"Mandatory section '{sec}' is missing from the document.",
            evidence="",
            why=f"The Stellantis CTS template requires this section. Its absence means the specification is incomplete and may not pass governance review. Without '{sec}', critical design or validation information may be undocumented.",
        ))
    for sec in rec_missing:
        report.findings.append(Finding(
            check="structure", severity="warning", section=sec,
            message=f"Recommended section '{sec}' is not found in the document.",
            evidence="",
            why=f"This section is recommended by Stellantis best practices. Its absence is not a compliance failure but may reduce the specification's completeness and traceability.",
        ))

    # 3. Placeholder detection
    placeholders, tbds, component_names = _count_placeholders(text)
    report.placeholder_count = placeholders
    report.tbd_count = tbds
    clean_score, total_artifacts = _score_template_cleanliness(placeholders, tbds, component_names, len(text))
    report.scores["template_cleanliness"] = round(clean_score, 2)

    if placeholders > 0:
        sample = PLACEHOLDER_RE.findall(text)[:3]
        report.findings.append(Finding(
            check="placeholders", severity="warning", section="",
            message=f"{placeholders} template placeholders (<<...>>) remaining. Examples: {', '.join(sample[:3])}",
            evidence="; ".join(sample[:3]),
            why="Template placeholders like <<...>> are unfilled fields from the CTS template. They must be replaced with real values before submission. Unresolved placeholders indicate incomplete work and may cause misinterpretation by reviewers or suppliers.",
        ))
    if tbds > 0:
        report.findings.append(Finding(
            check="placeholders", severity="warning", section="",
            message=f"{tbds} TBD/TBC/TODO/XXX markers found — these should be resolved before submission.",
            evidence="",
            why="TBD/TBC/TODO markers signal decisions or data that are still pending. In an industrial specification, all values must be finalized before release. Unresolved markers create risk of downstream errors and supplier misinterpretation.",
        ))
    if component_names > 0:
        report.findings.append(Finding(
            check="placeholders", severity="warning", section="",
            message=f"{component_names} unfilled template variables (<component name>, <part name>, etc.) remaining.",
            evidence="",
            why="Template variables like '<component name>' are generic placeholders that must be replaced with the actual part/component name. Leaving them unfilled makes the specification ambiguous — readers cannot identify which component the spec applies to.",
        ))

    # 4. Requirement analysis
    req_analysis = _analyze_requirements(text)
    report.requirement_count = req_analysis["total"]
    report.requirement_with_id = req_analysis["with_id"]
    report.requirement_with_traceability = req_analysis["with_traceability"]
    req_score, req_detail = _score_requirements(req_analysis)
    report.scores["requirements_quality"] = round(req_score, 2)

    if req_analysis["total"] == 0:
        report.findings.append(Finding(
            check="requirements", severity="error", section="REQUIREMENTS",
            message="No requirements using 'shall' language found. The CTS must contain formal requirements.",
            evidence="",
            why="A Component Technical Specification without formal requirements is not a valid specification. Requirements using 'shall' language define mandatory design constraints, performance targets, and test criteria. Without them, the spec cannot drive design, validation, or supplier contracts.",
        ))
    else:
        if req_analysis["with_id"] == 0:
            report.findings.append(Finding(
                check="requirements", severity="warning", section="REQUIREMENTS",
                message="Requirements found but none have formal requirement IDs (e.g. REF-PSP-COMP-001).",
                evidence="",
                why="Formal requirement IDs enable unambiguous traceability from the specification through design, testing, and verification. Without IDs, engineers cannot uniquely reference a requirement in test plans, design reviews, or compliance audits. Use REF-*, APP-*, or GEN-* prefixes with numeric suffixes.",
            ))
        if req_analysis["with_traceability"] == 0:
            report.findings.append(Finding(
                check="requirements", severity="warning", section="REQUIREMENTS",
                message="No input requirement traceability found. Requirements should reference upstream requirements or mark N/A.",
                evidence="",
                why="Traceability links each requirement to its source (customer spec, regulation, standard, or design decision). This is critical for: (1) change impact analysis — if an upstream requirement changes, you know which downstream requirements are affected; (2) compliance audits — showing each requirement is justified. Mark genuinely new requirements as 'N/A' to signal intentional design decisions.",
            ))
        report.findings.append(Finding(
            check="requirements", severity="info", section="REQUIREMENTS",
            message=req_detail,
            evidence="",
            why="This is a statistical summary of your requirement quality. Tracking these numbers over revisions helps measure specification maturity and completeness.",
        ))

    # 5. Writing guide compliance
    wg_score, wg_findings = _score_writing_guide(text, sections_found)
    report.scores["writing_guide_compliance"] = round(wg_score, 2)
    for msg in wg_findings:
        report.findings.append(Finding(
            check="writing_guide", severity="warning", section="",
            message=msg,
            evidence="",
            why=_WG_WHY_MAP.get(msg, "The Stellantis writing guide recommends this practice for consistency and quality."),
        ))

    # 6. Overall score (weighted average)
    weights = {
        "structure": 0.35,
        "template_cleanliness": 0.15,
        "requirements_quality": 0.30,
        "writing_guide_compliance": 0.20,
    }
    overall = sum(report.scores.get(k, 0) * w for k, w in weights.items())
    report.overall_score = round(overall, 2)

    # 7. Verdict
    errors = sum(1 for f in report.findings if f.severity == "error")
    warnings = sum(1 for f in report.findings if f.severity == "warning")

    if report.overall_score >= 0.80 and errors == 0:
        report.verdict = "GOOD"
    elif report.overall_score >= 0.60 and errors <= 2:
        report.verdict = "ACCEPTABLE_WITH_FIXES"
    elif report.overall_score >= 0.35:
        report.verdict = "NOT_RELIABLE"
    else:
        report.verdict = "NON_COMPLIANT"

    # 8. Summary
    report.summary = (
        f"Overall score: {report.overall_score:.0%} — Verdict: {report.verdict}. "
        f"Structure: {report.scores.get('structure', 0):.0%} "
        f"({len(MANDATORY_SECTIONS) - len(missing)}/{len(MANDATORY_SECTIONS)} mandatory sections). "
        f"Template cleanliness: {report.scores.get('template_cleanliness', 0):.0%} "
        f"({total_artifacts} artifacts). "
        f"Requirements: {report.scores.get('requirements_quality', 0):.0%} "
        f"({req_analysis['total']} requirements, {req_analysis['with_id']} with IDs). "
        f"Writing guide: {report.scores.get('writing_guide_compliance', 0):.0%}. "
        f"Findings: {errors} errors, {warnings} warnings."
    )

    return report


def report_to_dict(report: ValidationReport) -> Dict:
    """Convert a ValidationReport to a JSON-serializable dict with summary + detailed views."""
    # Separate findings by severity
    errors = [f for f in report.findings if f.severity == "error"]
    warnings = [f for f in report.findings if f.severity == "warning"]
    infos = [f for f in report.findings if f.severity == "info"]

    def _finding_dict(f):
        return {
            "check": f.check,
            "severity": f.severity,
            "section": f.section,
            "message": f.message,
            "evidence": f.evidence,
            "why": f.why,
        }

    return {
        "fileName": report.file_name,
        "overallScore": report.overall_score,
        "verdict": report.verdict,
        "scores": report.scores,
        # ── Summary view (high-level) ──
        "summary": report.summary,
        "summaryCounts": {
            "errors": len(errors),
            "warnings": len(warnings),
            "info": len(infos),
        },
        # ── Detailed view (full findings with rationale) ──
        "detailed": {
            "errors": [_finding_dict(f) for f in errors],
            "warnings": [_finding_dict(f) for f in warnings],
            "info": [_finding_dict(f) for f in infos],
        },
        # Legacy flat list (backward compat)
        "findings": [_finding_dict(f) for f in report.findings],
        "sectionsFound": report.sections_found,
        "sectionsMissing": report.sections_missing,
        "sectionsRecommendedMissing": report.sections_recommended_missing,
        "placeholderCount": report.placeholder_count,
        "tbdCount": report.tbd_count,
        "requirementCount": report.requirement_count,
        "requirementWithId": report.requirement_with_id,
        "requirementWithTraceability": report.requirement_with_traceability,
        "textLength": report.text_length,
    }
