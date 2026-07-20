"""
Tests for the evidence-based spec validator (app/qa/evidence_comparator.py).

Covers:
  1. validate_with_evidence — full pipeline (empty, minimal, real spec)
  2. Individual check functions (A-I)
  3. Scoring logic (_compute_scores)
  4. Verdict thresholds
  5. Response structure completeness
"""
import sys
import pytest
from pathlib import Path

# Ensure UTF-8 output on Windows consoles
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

from app.qa.evidence_comparator import (
    validate_with_evidence,
    EvidenceFinding,
    check_section_coverage,
    check_placeholder_residue,
    check_requirement_format,
    check_requirement_language,
    check_requirement_ids,
    check_traceability,
    check_writing_guide_rules,
    check_extended_writing_guide_rules,
    check_section_order,
    _compute_scores,
    _detect_user_sections,
    _section_matches,
    _has_traceability,
    REQ_ID_RE,
    SHALL_RE,
    PLACEHOLDER_RE,
)
from app.qa.rule_extractor import extract_all_rules, ExtractedRules


# ── Fixtures ──────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def rules():
    """Extract rules once for all tests in this module."""
    return extract_all_rules()


@pytest.fixture(scope="module")
def asu_text():
    """Load the real ASU spec text if available."""
    asu_path = Path(__file__).resolve().parent.parent / "data" / "uploads" / "00692_25_01250_ASU_Technical_Specification_SPX _1_.docx"
    if not asu_path.exists():
        pytest.skip("ASU spec DOCX not found in data/uploads/")
    from app.qa.retrieval import extract_text_from_file
    return extract_text_from_file(asu_path)


# ── Sample texts ───────────────────────────────────────────────────

MINIMAL_GOOD_SPEC = """\
REQUIREMENTS DOCUMENT
OF THE TEST COMPONENT
MODULE

Table of contents
Table of updates

PURPOSE
The goal of this specification is to define the requirements for the test component.

SCOPE
This specification covers the test component and its interfaces.

SYSTEM ROLES
The system shall provide the following roles: driver, passenger.

DEVELOPMENT CONTEXT
The component shall be developed in the context of the vehicle architecture.

GENERAL DESCRIPTION
The test component shall provide alarm signaling functionality.

PHYSICAL ARCHITECTURE
The component shall be housed in a sealed enclosure.

SYSTEM DIVERSITY
The system shall support multiple variants.

QUOTED DOCUMENTS
The following documents are referenced: ISO 26262.

REFERENCE DOCUMENTS
The following standards apply: ISO 9001.

APPLICABLE DOCUMENTS
All applicable documents are listed in the reference section.

TERMINOLOGY
Terms used in this document are defined below.

GLOSSARY
Key terms are defined in this section.

ACRONYMS
ASU: Alarm Siren Unit.

REQUIREMENTS
The system shall meet all functional and performance requirements.

FUNCTIONAL REQUIREMENTS
The system shall provide an audible alarm signal REF-ASU-001.
The alarm shall be audible at 85 dB.

PERFORMANCE REQUIREMENTS
The system shall respond within 100 ms.

EXTERNAL INTERFACES REQUIREMENTS
The system shall interface with the vehicle network.

OPERATIONAL REQUIREMENTS
The system shall operate from -40°C to +85°C.

MISSION PROFILE
The component shall survive 15 years of operation.

LIFETIME
The component shall have a 15-year lifetime.

ERGONOMICS
The component shall meet human factors requirements.

HUMAN FACTORS
The component shall be operable by all users.

RAMS
The component shall meet reliability targets.

SAFETY
The component shall meet ASIL B requirements.

MAINTAINABILITY
The component shall be serviceable.

PRODUCT QUALITY
The component shall meet quality standards.

CONSTRAINT
The component shall fit within 100mm x 100mm x 50mm.

DESIGN
The component shall use a dual-board architecture.

MANUFACTURING
The component shall be manufactured according to Stellantis standards.

ENVIRONMENTAL CONDITIONS
The component shall operate in all climate zones.

INTEGRATION
The component shall integrate with the vehicle electrical system.

VALIDATION
The component shall be validated according to the validation plan.

DEMONSTRATION
The component shall demonstrate compliance with all requirements.

COMPLIANCE
The component shall comply with all applicable standards.

TRACEABILITY
All requirements shall be traced to upstream requirements.

CONFIGURATION
The component shall be configured via software.

NETWORK INTERFACES
The component shall communicate via CAN bus.

ELECTRICAL INTERFACES
The component shall operate at 12V DC.

MECHANICAL INTERFACES
The component shall mount on a standard bracket.

MACHINE
The component shall be assembled by robot.

WEIGHT
The component shall weigh less than 500g.

PHYSICAL WITHDRAWAL
The component shall be removable without tools.

FLEXIBILITY
The component shall support firmware updates.

EXTENSION
The component shall support future feature extensions.

TRANSPORTABILITY
The component shall survive transport vibration.

STORAGE
The component shall be stored at -20°C to +60°C.

PACKAGING
The component shall be packaged in ESD-safe material.

PROTECTION
The component shall be protected against reverse polarity.

HOSTILITY
The component shall resist chemical exposure.

RESOURCES
The component shall use less than 2W power.

RESERVE
The component shall have 20% processing reserve.

CAPACITY
The component shall support 100 concurrent operations.

DOCUMENT
This document is the test component specification.
"""

EMPTY_SPEC = ""

PLACEHOLDER_SPEC = """\
PURPOSE
<<Insert purpose here>>
SCOPE
<<Define scope>>
Some requirement TBD.
Another TODO item.
"""


# ── 1. validate_with_evidence — full pipeline ─────────────────────

class TestValidateWithEvidence:
    """Tests for the main validate_with_evidence function."""

    def test_empty_document(self):
        """Empty document should return NON_COMPLIANT with 0 score."""
        r = validate_with_evidence("empty.docx", EMPTY_SPEC)
        assert r["overallScore"] == 0.0
        assert r["verdict"] == "NON_COMPLIANT"
        assert r["summaryCounts"]["errors"] == 1
        assert len(r["findings"]) == 1
        assert r["findings"][0]["check"] == "content"

    def test_minimal_good_spec_returns_valid_response(self, rules):
        """A well-structured spec should return a valid response with all fields."""
        r = validate_with_evidence("test.docx", MINIMAL_GOOD_SPEC)
        assert "fileName" in r
        assert "overallScore" in r
        assert "verdict" in r
        assert "scores" in r
        assert "summary" in r
        assert "summaryCounts" in r
        assert "findings" in r
        assert "detailed" in r
        assert "sectionsFound" in r
        assert "sectionsMissing" in r
        assert "rulesUsed" in r
        assert "textLength" in r

    def test_minimal_good_spec_score_in_range(self, rules):
        """Score should be between 0 and 1."""
        r = validate_with_evidence("test.docx", MINIMAL_GOOD_SPEC)
        assert 0.0 <= r["overallScore"] <= 1.0

    def test_minimal_good_spec_verdict_valid(self, rules):
        """Verdict should be one of the 4 valid values."""
        r = validate_with_evidence("test.docx", MINIMAL_GOOD_SPEC)
        assert r["verdict"] in ("GOOD", "ACCEPTABLE_WITH_FIXES", "NOT_RELIABLE", "NON_COMPLIANT")

    def test_minimal_good_spec_has_scores(self, rules):
        """All 5 score axes should be present."""
        r = validate_with_evidence("test.docx", MINIMAL_GOOD_SPEC)
        scores = r["scores"]
        assert "structure" in scores
        assert "section_order" in scores
        assert "template_cleanliness" in scores
        assert "requirements_quality" in scores
        assert "writing_guide_compliance" in scores

    def test_minimal_good_spec_no_placeholders(self, rules):
        """A spec without placeholders should have good cleanliness score."""
        r = validate_with_evidence("test.docx", MINIMAL_GOOD_SPEC)
        assert r["scores"]["template_cleanliness"] == 1.0

    def test_placeholder_spec_low_cleanliness(self, rules):
        """A spec with placeholders should have low cleanliness score."""
        r = validate_with_evidence("placeholder.docx", PLACEHOLDER_SPEC)
        assert r["scores"]["template_cleanliness"] < 1.0

    def test_findings_have_evidence_fields(self, rules):
        """Every finding should have double-evidence fields."""
        r = validate_with_evidence("test.docx", MINIMAL_GOOD_SPEC)
        for f in r["findings"]:
            assert "source_rule" in f
            assert "source_doc" in f
            assert "user_excerpt" in f
            assert "user_location" in f
            assert "why" in f
            assert "fix_suggestion" in f

    def test_detailed_has_all_severities(self, rules):
        """The detailed dict should have all 4 severity lists."""
        r = validate_with_evidence("test.docx", MINIMAL_GOOD_SPEC)
        assert "errors" in r["detailed"]
        assert "warnings" in r["detailed"]
        assert "info" in r["detailed"]
        assert "pass" in r["detailed"]

    def test_summary_counts_match_detailed(self, rules):
        """summaryCounts should match the lengths of the detailed lists."""
        r = validate_with_evidence("test.docx", MINIMAL_GOOD_SPEC)
        assert r["summaryCounts"]["errors"] == len(r["detailed"]["errors"])
        assert r["summaryCounts"]["warnings"] == len(r["detailed"]["warnings"])
        assert r["summaryCounts"]["info"] == len(r["detailed"]["info"])
        assert r["summaryCounts"]["pass"] == len(r["detailed"]["pass"])

    def test_rules_used_metadata(self, rules):
        """rulesUsed should contain extraction metadata."""
        r = validate_with_evidence("test.docx", MINIMAL_GOOD_SPEC)
        ru = r["rulesUsed"]
        assert "extraction_ok" in ru
        assert "mandatory_sections_count" in ru
        assert "writing_guide_rules_count" in ru
        assert "source_documents" in ru

    def test_asu_spec_validates_successfully(self, asu_text):
        """The real ASU spec should validate with a reasonable score."""
        r = validate_with_evidence("asu.docx", asu_text)
        assert r["overallScore"] > 0.0
        assert r["verdict"] in ("GOOD", "ACCEPTABLE_WITH_FIXES", "NOT_RELIABLE", "NON_COMPLIANT")
        assert len(r["findings"]) > 0
        assert r["rulesUsed"]["extraction_ok"] is True


# ── 2. Individual check functions ──────────────────────────────────

class TestCheckFunctions:
    """Tests for individual check_* functions."""

    def test_check_section_coverage_finds_present(self, rules):
        """Present sections should produce 'pass' findings."""
        text = "PURPOSE\nThe goal is defined.\nSCOPE\nThe scope is defined.\n"
        findings = check_section_coverage(text, rules)
        passes = [f for f in findings if f.severity == "pass" and f.check == "A_SECTION_COVERAGE"]
        assert len(passes) > 0

    def test_check_section_coverage_finds_missing(self, rules):
        """Missing sections should produce 'error' findings."""
        text = "PURPOSE\nThe goal is defined.\n"
        findings = check_section_coverage(text, rules)
        errors = [f for f in findings if f.severity == "error" and f.check == "A_SECTION_COVERAGE"]
        assert len(errors) > 0

    def test_check_placeholder_residue_detects_placeholders(self, rules):
        """Placeholders should be detected."""
        text = "PURPOSE\n<<Insert purpose here>>\nSome TBD item.\n"
        findings = check_placeholder_residue(text, rules)
        warnings = [f for f in findings if f.severity == "warning"]
        assert len(warnings) >= 1

    def test_check_placeholder_residue_clean_text(self, rules):
        """Clean text without placeholders should produce no warnings."""
        text = "PURPOSE\nThe goal is clearly defined without placeholders.\n"
        findings = check_placeholder_residue(text, rules)
        warnings = [f for f in findings if f.severity == "warning"]
        assert len(warnings) == 0

    def test_check_requirement_format(self, rules):
        """Requirement format check should run without errors."""
        text = "REQUIREMENTS\nThe system shall provide functionality.\n"
        findings = check_requirement_format(text, rules)
        assert isinstance(findings, list)

    def test_check_requirement_language_detects_shall(self, rules):
        """'shall' statements should produce a pass finding."""
        text = "The system shall provide an audible alarm.\n"
        findings = check_requirement_language(text, rules)
        passes = [f for f in findings if f.severity == "pass"]
        assert len(passes) >= 1

    def test_check_requirement_language_detects_subjective(self, rules):
        """Subjective words should produce a warning."""
        text = "The system shall handle various inputs efficiently.\n"
        findings = check_requirement_language(text, rules)
        warnings = [f for f in findings if f.severity == "warning"]
        assert len(warnings) >= 1

    def test_check_requirement_ids_detects_ids(self, rules):
        """Requirement IDs should be detected."""
        text = "REF-ASU-001\nThe system shall provide an alarm.\nAPP-ASU-002\nThe system shall be loud.\n"
        findings = check_requirement_ids(text, rules)
        assert isinstance(findings, list)

    def test_check_traceability(self, rules):
        """Traceability check should run without errors."""
        text = "Input Requirement: VF_12345\nThe system shall trace to upstream.\n"
        findings = check_traceability(text, rules)
        assert isinstance(findings, list)

    def test_check_writing_guide_rules(self, rules):
        """Writing guide rules check should run without errors."""
        text = "PURPOSE\nThe goal is defined.\n"
        findings = check_writing_guide_rules(text, rules)
        assert isinstance(findings, list)

    def test_check_extended_writing_guide_rules(self, rules):
        """Extended writing guide rules check should run without errors."""
        text = "PURPOSE\nThe goal is defined.\n"
        findings = check_extended_writing_guide_rules(text, rules)
        assert isinstance(findings, list)

    def test_check_section_order(self, rules):
        """Section order check should run without errors."""
        text = "PURPOSE\nThe goal.\nSCOPE\nThe scope.\n"
        findings = check_section_order(text, rules)
        assert isinstance(findings, list)


# ── 3. Scoring logic ──────────────────────────────────────────────

class TestScoring:
    """Tests for _compute_scores."""

    def test_all_pass_findings_produce_good_scores(self, rules):
        """All-pass findings should produce high scores."""
        findings = [
            EvidenceFinding("A_SECTION_COVERAGE", "pass", "PURPOSE", "TEMPLATE", "ok",
                           "rule", "template", "excerpt", "loc", "why"),
            EvidenceFinding("D_REQUIREMENT_FORMAT", "pass", "", "R22", "ok",
                           "rule", "template", "excerpt", "loc", "why"),
            EvidenceFinding("H_WRITING_GUIDE_RULES", "pass", "", "R01", "ok",
                           "rule", "writing_guide", "excerpt", "loc", "why"),
        ]
        scores = _compute_scores(findings, rules)
        assert scores["structure"] > 0
        assert scores["requirements_quality"] > 0
        assert scores["writing_guide_compliance"] > 0

    def test_error_findings_reduce_structure_score(self, rules):
        """Error findings in section coverage should reduce structure score."""
        total = sum(1 for s in rules.mandatory_sections if s.level == 1)
        # Simulate all sections missing
        findings = [
            EvidenceFinding("A_SECTION_COVERAGE", "error", "MISSING", "TEMPLATE", "missing",
                           "rule", "template", "", "NOT FOUND", "why")
            for _ in range(total)
        ]
        scores = _compute_scores(findings, rules)
        assert scores["structure"] < 0.5

    def test_placeholder_findings_reduce_cleanliness(self, rules):
        """Placeholder warnings should reduce cleanliness score."""
        findings = [
            EvidenceFinding("C_PLACEHOLDER_RESIDUE", "warning", "", "TEMPLATE",
                           "50 template placeholders (<<...>>) remaining unfilled.",
                           "rule", "template", "", "", "why"),
        ]
        scores = _compute_scores(findings, rules)
        assert scores["template_cleanliness"] <= 0.35

    def test_scores_in_range(self, rules):
        """All scores should be between 0 and 1."""
        findings = [
            EvidenceFinding("A_SECTION_COVERAGE", "pass", "PURPOSE", "TEMPLATE", "ok",
                           "rule", "template", "excerpt", "loc", "why"),
            EvidenceFinding("A_SECTION_COVERAGE", "error", "MISSING", "TEMPLATE", "missing",
                           "rule", "template", "", "NOT FOUND", "why"),
        ]
        scores = _compute_scores(findings, rules)
        for v in scores.values():
            assert 0.0 <= v <= 1.0


# ── 4. Verdict thresholds ─────────────────────────────────────────

class TestVerdicts:
    """Tests for verdict computation in validate_with_evidence."""

    def test_empty_document_non_compliant(self):
        """Empty document should be NON_COMPLIANT."""
        r = validate_with_evidence("empty.docx", "")
        assert r["verdict"] == "NON_COMPLIANT"

    def test_good_spec_verdict_is_valid(self, rules):
        """A good spec should produce a valid verdict."""
        r = validate_with_evidence("good.docx", MINIMAL_GOOD_SPEC)
        assert r["verdict"] in ("GOOD", "ACCEPTABLE_WITH_FIXES", "NOT_RELIABLE", "NON_COMPLIANT")


# ── 5. Helper functions ────────────────────────────────────────────

class TestHelpers:
    """Tests for helper functions."""

    def test_detect_user_sections_finds_allcaps(self):
        """ALLCAPS headings should be detected."""
        text = "PURPOSE\nThe goal.\nSCOPE\nThe scope.\n"
        sections = _detect_user_sections(text)
        names = [s[0] for s in sections]
        assert "PURPOSE" in names
        assert "SCOPE" in names

    def test_detect_user_sections_finds_numbered(self):
        """Numbered headings should be detected."""
        text = "1. PURPOSE\nThe goal.\n2. SCOPE\nThe scope.\n"
        sections = _detect_user_sections(text)
        assert len(sections) >= 2

    def test_section_matches_exact(self):
        """Exact match should work."""
        result = _section_matches("PURPOSE", ["PURPOSE"])
        assert result == "PURPOSE"

    def test_section_matches_no_match(self):
        """Non-matching sections should return None."""
        result = _section_matches("PURPOSE", ["SCOPE", "REQUIREMENTS"])
        assert result is None

    def test_has_traceability_detects_input_req(self):
        """Input requirement should be detected as traceability."""
        assert _has_traceability("Input Requirement: VF_12345") is True

    def test_has_traceability_detects_na(self):
        """N/A should be detected as traceability."""
        assert _has_traceability("N/A") is True

    def test_has_traceability_no_match(self):
        """Plain text without traceability should return False."""
        assert _has_traceability("The system shall be loud.") is False

    def test_req_id_re_matches(self):
        """Requirement ID regex should match REF-/APP-/GEN- patterns (PREFIX-COMPONENT-NUMBER)."""
        assert REQ_ID_RE.search("REF-ASU-001") is not None
        assert REQ_ID_RE.search("APP-ASU-002") is not None
        assert REQ_ID_RE.search("GEN-SYS-001") is not None

    def test_req_id_re_no_match(self):
        """Requirement ID regex should not match non-IDs."""
        assert REQ_ID_RE.search("hello world") is None

    def test_shall_re_matches(self):
        """Shall regex should match 'shall'."""
        assert SHALL_RE.search("The system shall provide") is not None

    def test_placeholder_re_matches(self):
        """Placeholder regex should match <<...>>."""
        assert PLACEHOLDER_RE.search("<<insert here>>") is not None

    def test_placeholder_re_no_match(self):
        """Placeholder regex should not match plain text."""
        assert PLACEHOLDER_RE.search("no placeholders here") is None


# ── 6. Rule extraction ────────────────────────────────────────────

class TestRuleExtraction:
    """Tests for rule extraction from source documents."""

    def test_extract_all_rules_returns_rules(self, rules):
        """extract_all_rules should return an ExtractedRules object."""
        assert isinstance(rules, ExtractedRules)

    def test_extraction_ok(self, rules):
        """Rule extraction should succeed when reference DOCX files exist."""
        assert rules.extraction_ok is True

    def test_mandatory_sections_extracted(self, rules):
        """Mandatory sections should be extracted from the template."""
        assert len(rules.mandatory_sections) > 0

    def test_writing_guide_rules_extracted(self, rules):
        """Writing guide rules should be extracted from the writing guide."""
        assert len(rules.writing_guide_rules) > 0

    def test_template_instructions_extracted(self, rules):
        """Template instructions should be extracted."""
        assert len(rules.template_instructions) > 0

    def test_section_order_extracted(self, rules):
        """Section order should be extracted from the template."""
        assert len(rules.section_order) > 0


# ── 7. Audit fixes (2026-07) ──────────────────────────────────────

class TestAuditFixes:
    """Regression tests for the engine-audit fixes."""

    def test_cleanliness_counts_all_artifact_types(self, rules):
        """Component-variable and TBD counts must enter the cleanliness
        tiers, not only <<...>> placeholder counts (old regex bug)."""
        findings = [
            EvidenceFinding("C_PLACEHOLDER_RESIDUE", "warning", "", "TEMPLATE",
                            "15 unfilled template variables (<component name>, <part name>, etc.) remaining.",
                            "rule", "template", "", "", "why"),
            EvidenceFinding("C_PLACEHOLDER_RESIDUE", "warning", "", "TEMPLATE",
                            "30 TBD/TBC/TODO/XXX markers found — these should be resolved before submission.",
                            "rule", "template", "", "", "why"),
        ]
        scores = _compute_scores(findings, rules)
        # 45 artifacts → worst tier (0.20), not the old 0.85 from count=0
        assert scores["template_cleanliness"] <= 0.35

    def test_leftover_template_instruction_detected(self, rules):
        """Instruction text from the template left in the doc WITHOUT its
        <<>> markers must be flagged (uses rules.template_instructions,
        previously extracted but never checked)."""
        # Find a long real instruction from the extracted template rules
        long_instr = None
        for ti in rules.template_instructions:
            inner = ti.placeholder.strip()
            if inner.startswith("<<") and inner.endswith(">>"):
                inner = inner[2:-2].strip()
            if len(inner) >= 40:
                long_instr = inner
                break
        if long_instr is None:
            pytest.skip("No long template instruction extracted")

        user_text = f"PURPOSE\nSome content.\n{long_instr}\nMore content."
        findings = check_placeholder_residue(user_text, rules)
        instr_warnings = [
            f for f in findings
            if f.severity == "warning" and "instruction sentence" in f.message
        ]
        assert len(instr_warnings) == 1

    def test_rules_used_reports_unchecked_rules(self):
        """rulesUsed must expose the writing-guide rules NOT covered by
        any implemented check (honest coverage reporting)."""
        r = validate_with_evidence("mini.txt", "PURPOSE\nThe system shall work.")
        ru = r["rulesUsed"]
        assert "unchecked_rule_ids" in ru
        unchecked = set(ru["unchecked_rule_ids"])
        checked = set(ru["checked_rule_ids"])
        assert not (unchecked & checked)  # disjoint

    def test_recommended_section_warnings_affect_wg_score(self, rules):
        """Recommended-section warnings (check A, rule_id WRITING_GUIDE)
        must lower the writing-guide score (previously scoreless)."""
        base = [
            EvidenceFinding("H_WRITING_GUIDE_RULES", "pass", "", "R05", "ok",
                            "rule", "writing_guide", "", "", "why"),
        ]
        with_reco_warn = base + [
            EvidenceFinding("A_SECTION_COVERAGE", "warning", "ERGONOMICS",
                            "WRITING_GUIDE", "Recommended section missing",
                            "rule", "writing_guide", "", "", "why"),
        ]
        score_base = _compute_scores(base, rules)["writing_guide_compliance"]
        score_warn = _compute_scores(with_reco_warn, rules)["writing_guide_compliance"]
        assert score_warn < score_base