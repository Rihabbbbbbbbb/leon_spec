"""
Deterministic pre-validation checks for LEON Spec Validator.

These checks run BEFORE the LLM call and produce findings based on
explicit, auditable rules. They supplement LLM-based validation with
guaranteed detection of common issues like placeholders, missing sections,
and weak traceability patterns.
"""
import re
from typing import List, Dict, Any, Optional, Tuple


# ── Scoring rubric constants ─────────────────────────────────────

class ScoreRubric:
    """Deterministic scoring rubrics for each validation axis."""
    
    @staticmethod
    def structure(user_text: str, sections_found: List[str]) -> Tuple[float, str]:
        """
        Score based on presence of mandatory CTS sections.
        Expected sections per the standard CTS plan.
        """
        mandatory_sections = [
            "PURPOSE", "SCOPE", "SYSTEM DEVELOPMENT CONTEXT",
            "GENERAL DESCRIPTION", "SYSTEM ROLES", "PHYSICAL SYSTEM ARCHITECTURE",
            "SYSTEM DIVERSITY", "QUOTED DOCUMENTS", "REFERENCE DOCUMENTS",
            "APPLICABLE DOCUMENTS", "TERMINOLOGY", "GLOSSARY",
            "ACRONYMS", "REQUIREMENTS", "FUNCTIONAL REQUIREMENTS",
            "PERFORMANCE REQUIREMENTS", "EXTERNAL INTERFACES",
            "OPERATIONAL REQUIREMENTS", "MISSION PROFILE",
            "RAMS REQUIREMENTS", "CONSTRAINT REQUIREMENTS",
            "INTEGRATION AND VALIDATION", "DEMONSTRATION OF COMPLIANCE",
        ]
        present = sum(1 for s in mandatory_sections if any(
            s.lower() in found.lower() for found in sections_found
        ))
        ratio = present / len(mandatory_sections)
        
        if ratio >= 0.95:
            return (0.9 + (ratio - 0.95) * 2.0, f"All {present}/{len(mandatory_sections)} mandatory sections present")
        elif ratio >= 0.80:
            return (0.6 + (ratio - 0.80) * 2.0, f"{present}/{len(mandatory_sections)} mandatory sections present")
        elif ratio >= 0.60:
            return (0.3 + (ratio - 0.60) * 1.5, f"Only {present}/{len(mandatory_sections)} sections found")
        else:
            return (0.0 + ratio * 0.5, f"Missing many mandatory sections ({present}/{len(mandatory_sections)})")

    @staticmethod
    def template_cleanliness(user_text: str, placeholder_count: int, 
                             tbd_count: int, xxx_count: int) -> Tuple[float, str]:
        """Score based on presence of template artifacts.
        
        Template artifacts are informational — they indicate sections that need
        finalization but do NOT block the overall assessment. The score reflects
        how much template content remains, but it's weighted lower than other axes.
        """
        total_issues = placeholder_count + tbd_count + xxx_count
        doc_len = max(len(user_text), 1)
        issue_density = total_issues / (doc_len / 1000)  # issues per 1000 chars
        
        if total_issues == 0:
            return (1.0, "No template artifacts detected — document is clean")
        elif total_issues <= 2:
            return (0.85, f"Negligible template artifacts ({total_issues} total)")
        elif total_issues <= 5:
            return (0.70, f"Few template artifacts ({total_issues} total) — minor cleanup needed")
        elif total_issues <= 15:
            return (0.50, f"Some template artifacts ({total_issues} total) — should be replaced before submission")
        elif total_issues <= 40:
            return (0.35, f"Many template artifacts ({total_issues} total) — sections need finalization")
        else:
            return (0.20, f"Extensive template artifacts ({total_issues} total) — significant content still templated")

    @staticmethod
    def traceability(req_rows: List[Dict], total_requirements: int) -> Tuple[float, str]:
        """Score based on traceability coverage in requirement rows."""
        if total_requirements == 0:
            return (0.5, "No requirement rows extracted; cannot assess traceability")
        
        with_input = sum(1 for r in req_rows if r.get("input_requirement") and 
                        r["input_requirement"].strip() not in ("N/A", "n/a", "", "N / A"))
        ratio = with_input / total_requirements
        
        if ratio >= 0.9:
            return (0.9, f"Strong traceability: {with_input}/{total_requirements} requirements have upstream references")
        elif ratio >= 0.7:
            return (0.7, f"Good traceability: {with_input}/{total_requirements} with upstream references")
        elif ratio >= 0.5:
            return (0.5, f"Partial traceability: {with_input}/{total_requirements} with upstream references")
        elif ratio >= 0.25:
            return (0.3, f"Weak traceability: only {with_input}/{total_requirements} with upstream references")
        else:
            return (0.1, f"Very weak traceability: {with_input}/{total_requirements} with upstream references")

    # [COMMENTED OUT — Validation readiness scoring disabled]
    # @staticmethod
    # def validation_readiness(req_rows: List[Dict], total_requirements: int,
    #                          user_text: str) -> Tuple[float, str]:
    #     """Score based on validation content coverage."""
    #     # Check for validation section presence
    #     has_validation_section = bool(re.search(
    #         r'(INTEGRATION AND VALIDATION|DEMONSTRATION OF COMPLIANCE|VALIDATION PLAN|validation requirements)',
    #         user_text, re.IGNORECASE
    #     ))
    #     
    #     if total_requirements == 0:
    #         if has_validation_section:
    #             return (0.5, "Validation section present but no structured rows detected")
    #         return (0.2, "No validation content detected")
    #     
    #     with_validation = sum(1 for r in req_rows if r.get("validation") and r["validation"].strip())
    #     ratio = with_validation / total_requirements
    #     
    #     base = 0.3 if has_validation_section else 0.0
    #     if ratio >= 0.5:
    #         return (min(base + 0.6, 1.0), f"Good validation coverage: {with_validation}/{total_requirements} rows have validation methods")
    #     elif ratio >= 0.25:
    #         return (base + 0.3, f"Partial validation: {with_validation}/{total_requirements} rows have validation")
    #     else:
    #         return (base + 0.1, f"Weak validation: only {with_validation}/{total_requirements} rows with validation")


# ── Deterministic check functions ────────────────────────────────

PLACEHOLDER_PATTERNS = [
    (re.compile(r'<<[^>]*>>'), 'template_placeholder', 'Template instruction placeholder'),
    (re.compile(r'\bTBD\b', re.IGNORECASE), 'tbd_placeholder', 'TBD (to be determined) placeholder'),
    (re.compile(r'\bXXX\b'), 'xxx_placeholder', 'XXX placeholder value'),
    (re.compile(r'<<\s*\(\*\).*?>>'), 'template_instruction', 'Template instruction with (*) marker'),
]

NA_PATTERN = re.compile(r'^N/?A$|^Not\s+Applicable$', re.IGNORECASE)
REQUIREMENT_LIKE_IN_SCOPE = re.compile(
    r'(shall|must|will|should)\s+', re.IGNORECASE
)

# CTS sections expected per the standard plan
CTS_SECTIONS = [
    "PURPOSE", "SCOPE", "SYSTEM DEVELOPMENT CONTEXT",
    "GENERAL DESCRIPTION OF THE SYSTEM", "SYSTEM ROLES",
    "PHYSICAL SYSTEM ARCHITECTURE", "SYSTEM DIVERSITY",
    "QUOTED DOCUMENTS", "REFERENCE DOCUMENTS",
    "UPSTREAM REQUIREMENTS", "CONSTRAINT REQUIREMENTS FROM OTHER DISCIPLINES",
    "REGULATION AND CONSUMERISM", "MANDATORY REQUIREMENTS",
    "APPLICABLE DOCUMENTS", "STANDARDS", "TECHNICAL SPECIFICATIONS",
    "TERMINOLOGY", "GLOSSARY", "ACRONYMS",
    "REQUIREMENTS", "FUNCTIONAL REQUIREMENTS",
    "PERFORMANCE REQUIREMENTS", "EXTERNAL INTERFACES REQUIREMENTS",
    "ELECTRICAL INTERFACES", "MECHANICAL INTERFACES",
    "HUMAN-MACHINE INTERFACES", "OPERATIONAL REQUIREMENTS",
    "MISSION PROFILE", "LIFETIME", "ERGONOMICS AND HUMAN FACTORS",
    "RAMS REQUIREMENTS", "SAFETY REQUIREMENTS",
    "MAINTAINABILITY", "PRODUCT QUALITY",
    "CONSTRAINT REQUIREMENTS", "DESIGN AND MANUFACTURING",
    "MATERIALS", "MANUFACTURING", "ENVIRONMENT CONDITIONS",
    "INTEGRATION AND VALIDATION REQUIREMENTS",
    "DEMONSTRATION OF COMPLIANCE WITH REQUIREMENTS",
    "IMPOSED ELEMENTS OF VALIDATION PLAN",
]


def run_deterministic_checks(user_text: str, req_rows: List[Dict[str, Any]],
                              extracted_blocks: List[Dict[str, Any]]) -> Dict[str, Any]:
    """
    Run all deterministic checks on the user document.
    
    Returns a dict with:
    - findings: List of deterministic finding dicts
    - rubric_scores: Dict of axis -> (score, explanation)
    - stats: Dict of counts and metrics
    """
    findings: List[Dict[str, Any]] = []
    stats: Dict[str, Any] = {}
    
    # ── 1. Placeholder detection ─────────────────────────────
    placeholder_instances: List[Dict] = []
    total_placeholder_count = 0
    total_tbd = 0
    total_xxx = 0
    
    for line_idx, line in enumerate(user_text.split('\n'), 1):
        line = line.strip()
        if not line:
            continue
        for pattern, ptype, plabel in PLACEHOLDER_PATTERNS:
            for match in pattern.finditer(line):
                placeholder_instances.append({
                    "line": line_idx,
                    "type": ptype,
                    "label": plabel,
                    "text": match.group()[:120],
                    "context": line[:200]
                })
                total_placeholder_count += 1
                if ptype == 'tbd_placeholder':
                    total_tbd += 1
                if ptype == 'xxx_placeholder':
                    total_xxx += 1
    
    stats["placeholder_count"] = total_placeholder_count
    stats["tbd_count"] = total_tbd
    stats["xxx_count"] = total_xxx
    stats["placeholder_instances"] = placeholder_instances[:30]  # Cap for output
    
    if placeholder_instances:
        # Group by type
        by_label: Dict[str, List] = {}
        for pi in placeholder_instances:
            by_label.setdefault(pi["label"], []).append(pi)
        
        for label, instances in by_label.items():
            locations = [f"line {i['line']}" for i in instances[:5]]
            findings.append({
                "type": "placeholder_detected",
                "severity": "warning",
                "location": f"Lines: {', '.join(locations[:5])}{' (+more)' if len(instances) > 5 else ''}",
                "status": "present",
                "finding": f"Detected {len(instances)} instance(s) of '{label}': {instances[0]['text']}",
                "why_it_matters": "Template artifacts indicate incomplete or unedited content that should have been removed before submission.",
                "user_document_excerpt": instances[0]["context"][:200] if instances else "",
                "suggested_fix": "Remove or replace all placeholder/template text with finalized content.",
            })
    
    # ── 2. Section coverage check ─────────────────────────────
    sections_found = []
    for block in extracted_blocks:
        text = block.get("text", "").strip()
        heading_level = block.get("heading_level")
        if heading_level and heading_level <= 2:
            sections_found.append(text)
    
    # Also scan for section-like headings (handles ALL formats found in Stellantis docs)
    # ROBUST heading detection — handles:
    #   "PURPOSE", "1 SCOPE", "6. ELECTRICAL INTERFACES", "6- MAINTAINABILITY",
    #   "7) DEMONSTRATION OF COMPLIANCE", "1.1 Reference Documents",
    #   "1.1.1 ELECTRICAL INTERFACES", "### 1.1.1 Title", "TITLE:"
    
    def _clean_section_name(raw: str) -> str:
        """Extract clean section name: strip colons, text after em/en dashes, normalize spaces."""
        name = raw.strip()
        name = re.sub(r'\s*:\s*$', '', name)               # Strip trailing colons
        name = re.split(r'\s*[\u2013\u2014-]\s*', name)[0].strip()  # Cut at em/en dash
        name = re.sub(r'\s+', ' ', name)                    # Normalize spaces
        return name
    
    # Pattern 1: Number + separator + ALL-CAPS (e.g., "6. ELECTRICAL INTERFACES", "1 SCOPE", "7- TITLE")
    num_caps_re = re.compile(
        r'^(?:#+\s*)?'                          # Optional markdown markers
        r'\d+'                                    # Leading number
        r'[.)\-\s/]\s*'                          # Separator: dot, paren, dash, slash, or space
        r'([A-Z][A-Z\s/()\-&,:;\.\u2013\u2014]{3,})$',  # ALL-CAPS title
        re.MULTILINE
    )
    
    # Pattern 2: Pure ALL-CAPS heading (e.g., "PURPOSE", "MAINTAINABILITY:", "ERGONOMICS & HUMAN FACTORS")
    allcaps_re = re.compile(
        r'^([A-Z][A-Z\s/()\-&,:;\.\u2013\u2014]{3,})$',
        re.MULTILINE
    )
    
    # Pattern 3: Multi-level numbered + mixed case (e.g., "1.1 Reference Documents", "1.1.1 Title")
    numbered_re = re.compile(
        r'^(?:#+\s*)?'                          # Optional markdown markers
        r'\d+(?:\.\d+)+\s+'                       # Multi-level: "1.1 " or "1.1.2 "
        r'([A-Z][A-Za-z\s/()\-&,:;\.]{3,})$',   # Title: uppercase start
        re.MULTILINE
    )
    
    for line in user_text.split('\n'):
        clean = re.sub(r'^#+\s*', '', line.strip())
        if not clean or len(clean) < 4 or len(clean) > 120:
            continue
        
        candidate = None
        
        # Try Pattern 1: Number + separator + ALL-CAPS
        m = num_caps_re.match(clean)
        if m:
            candidate = _clean_section_name(m.group(1))
        
        # Try Pattern 2: Pure ALL-CAPS
        if not candidate:
            m = allcaps_re.match(clean)
            if m:
                candidate = _clean_section_name(m.group(1))
        
        # Try Pattern 3: Multi-level numbered
        if not candidate:
            m = numbered_re.match(clean)
            if m:
                candidate = _clean_section_name(m.group(1))
        
        if candidate and 5 <= len(candidate) < 80 and candidate not in sections_found:
            sections_found.append(candidate)
    
    # ── Fallback: also add CTS sections found ANYWHERE in the raw text ──
    # This catches sections whose headings don't match any regex pattern
    # but whose name appears as plain text in the document.
    user_text_lower = user_text.lower()
    for section in CTS_SECTIONS:
        section_lower = section.lower()
        # Check if already found by regex patterns
        already_found = any(section_lower in sf.lower() for sf in sections_found)
        if not already_found:
            # Check if the section name appears anywhere in the raw document text
            if section_lower in user_text_lower:
                sections_found.append(section)
    
    missing_cts = []
    for section in CTS_SECTIONS:
        found = any(section.lower() in sf.lower() for sf in sections_found)
        if not found:
            missing_cts.append(section)
    
    stats["sections_found"] = sections_found
    stats["missing_cts_sections"] = missing_cts
    
    if missing_cts:
        key_missing = [s for s in missing_cts if s in (
            "FUNCTIONAL REQUIREMENTS", "PERFORMANCE REQUIREMENTS",
            "EXTERNAL INTERFACES REQUIREMENTS", "OPERATIONAL REQUIREMENTS",
            "RAMS REQUIREMENTS", "CONSTRAINT REQUIREMENTS",
            "INTEGRATION AND VALIDATION REQUIREMENTS", "VALIDATION PLAN"
        )]
        if key_missing:
            findings.append({
                "type": "missing_section",
                "severity": "error",
                "location": "Document structure",
                "status": "absent",
                "finding": f"Key CTS sections appear missing or unlabeled: {', '.join(key_missing[:5])}",
                "why_it_matters": "Missing sections mean the specification may not address all required aspects of the component.",
                "user_document_excerpt": f"Scanned {len(sections_found)} headings; missing {len(missing_cts)} CTS sections.",
                "suggested_fix": f"Add the missing sections per the CTS standard plan.",
            })
    
    # ── 3. Requirement row checks ─────────────────────────────
    total_reqs = len(req_rows)
    stats["total_requirement_rows"] = total_reqs
    
    if total_reqs > 0:
        # Count rows with N/A input requirement
        na_reqs = []
        no_id_reqs = []
        weak_desc_reqs = []
        
        for r in req_rows:
            req_id = r.get("req_id", "").strip()
            input_req = r.get("input_requirement", "").strip()
            desc = r.get("description", "").strip()
            
            if not req_id:
                no_id_reqs.append(r)
            if input_req and NA_PATTERN.match(input_req):
                na_reqs.append(r)
            if len(desc) < 30 and desc:
                weak_desc_reqs.append(r)
        
        stats["na_input_count"] = len(na_reqs)
        stats["no_id_count"] = len(no_id_reqs)
        stats["weak_desc_count"] = len(weak_desc_reqs)
        
        if no_id_reqs:
            findings.append({
                "type": "missing_requirement_id",
                "severity": "warning",
                "location": f"{len(no_id_reqs)} requirement rows",
                "status": "present_but_incomplete",
                "finding": f"{len(no_id_reqs)} requirement rows have no requirement ID.",
                "why_it_matters": "Requirement IDs are needed for traceability and validation mapping.",
                "user_document_excerpt": f"First affected row: {no_id_reqs[0].get('description', '')[:150]}",
                "suggested_fix": "Assign unique requirement IDs to all requirement rows per the CTS numbering convention.",
            })
        
        if weak_desc_reqs and len(weak_desc_reqs) > total_reqs * 0.2:
            findings.append({
                "type": "weak_requirement_description",
                "severity": "warning",
                "location": f"{len(weak_desc_reqs)} requirement rows",
                "status": "present_but_weak",
                "finding": f"{len(weak_desc_reqs)}/{total_reqs} requirement descriptions are very short (<30 chars) and may be incomplete.",
                "why_it_matters": "Short descriptions often lack sufficient detail for implementation and testing.",
                "user_document_excerpt": f"Example: {weak_desc_reqs[0].get('description', '')[:150]}" if weak_desc_reqs else "",
                "suggested_fix": "Expand requirement descriptions with preconditions, triggers, and observable outcomes.",
            })
    else:
        stats["na_input_count"] = 0
        stats["no_id_count"] = 0
        stats["weak_desc_count"] = 0
    
    # ── 4. Compute rubric scores ─────────────────────────────
    rubric_scores: Dict[str, Tuple[float, str]] = {}
    rubric_scores["structure"] = ScoreRubric.structure(user_text, sections_found)
    rubric_scores["template_cleanliness"] = ScoreRubric.template_cleanliness(
        user_text, total_placeholder_count, total_tbd, total_xxx
    )
    rubric_scores["traceability"] = ScoreRubric.traceability(req_rows, total_reqs)
    # [COMMENTED OUT — Validation readiness scoring call disabled]
    # rubric_scores["validation_readiness"] = ScoreRubric.validation_readiness(req_rows, total_reqs, user_text)
    rubric_scores["validation_readiness"] = (0.5, "Validation readiness scoring disabled — re-enable when needed")
    
    # For axes without deterministic data, provide baseline
    rubric_scores["requirements_quality"] = (
        0.5 if total_reqs > 0 else 0.3,
        f"{total_reqs} requirement rows extracted; LLM will refine based on content quality"
    )
    rubric_scores["mechatronics_fitness"] = (
        0.5,
        "LLM will assess based on system roles, architecture, interfaces, and RAMS content"
    )
    
    return {
        "findings": findings,
        "rubric_scores": {k: {"score": v[0], "rationale": v[1]} for k, v in rubric_scores.items()},
        "stats": stats,
    }


def build_user_document_context(blocks: List[Dict[str, Any]], max_chars: int = 8000) -> str:
    """
    Build a structured representation of the user document with section markers
    that the LLM can cite in its evidence.
    
    Returns a string with [USER §section_name] markers before each section.
    """
    parts = []
    current_section = ""
    char_count = 0
    
    for block in blocks:
        text = block.get("text", "").strip()
        if not text:
            continue
        
        section = block.get("section_context", "")
        block_type = block.get("block_type", "paragraph")
        heading_level = block.get("heading_level")
        
        # Mark section boundaries with explicit labels LLM can cite
        if section and section != current_section:
            current_section = section
            parts.append(f"\n[USER DOCUMENT §{section}]")
        
        # Mark headings explicitly
        if heading_level:
            prefix = "#" * min(heading_level, 4)
            parts.append(f"{prefix} {text}")
        elif block_type == "table_row":
            parts.append(f"[ROW] {text}")
        else:
            parts.append(text)
        
        char_count += len(text)
        if char_count > max_chars:
            parts.append(f"\n[... {len(blocks) - len(parts)} more blocks truncated for length ...]")
            break
    
    return "\n".join(parts)


def find_text_location(user_text: str, excerpt: str) -> Optional[str]:
    """
    Try to locate an excerpt in the user document and return a line-number reference.
    Used for post-validation verification.
    """
    if not excerpt or len(excerpt) < 10:
        return None
    
    # Try exact match
    idx = user_text.find(excerpt)
    if idx >= 0:
        line_num = user_text[:idx].count('\n') + 1
        return f"line ~{line_num}"
    
    # Try first 40 chars
    short = excerpt[:40]
    idx = user_text.find(short)
    if idx >= 0:
        line_num = user_text[:idx].count('\n') + 1
        return f"line ~{line_num} (partial match)"
    
    return None
