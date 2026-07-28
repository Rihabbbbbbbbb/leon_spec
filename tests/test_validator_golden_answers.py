"""
Golden-answer tests for the spec validator (app/qa/evidence_comparator.py).

Unlike test_validator.py (which exercises individual functions with hand-
picked snippets), this file asks: **are the validator's ANSWERS actually
correct?** It builds a document that is genuinely, verifiably compliant
with the real extracted template/guide rules, confirms the validator
recognizes it as such (true negative — no false errors), then mutates it
one defect at a time and confirms the validator flags EXACTLY that defect
(true positive, isolated) without corrupting the rest of the analysis.

It also pins down the verdict/score algorithm itself (independent of any
single check) and re-verifies the real ASU spec's known-correct findings.
"""
import re
import sys
from pathlib import Path

import pytest

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

from app.qa.evidence_comparator import (
    validate_with_evidence,
    check_standards_consistency,
    _extract_standard_refs,
    _split_declaration_and_body,
)
from app.qa.rule_extractor import extract_all_rules


ASU_PATH = Path(__file__).resolve().parent.parent / "data" / "uploads" / \
    "00692_25_01250_ASU_Technical_Specification_SPX _1_.docx"


@pytest.fixture(scope="module")
def rules():
    return extract_all_rules()


def _asu_heading_texts():
    """
    Independently collect every paragraph in the real ASU spec that could
    legitimately be used as a table's section label — derived straight from
    python-docx, NOT from the code under test, so it can catch a mapping
    that invents or borrows a heading.
    """
    from docx import Document
    doc = Document(str(ASU_PATH))
    texts = {""}
    for para in doc.paragraphs:
        t = para.text.strip()
        if t and len(t) < 120:
            texts.add(t)
    return texts


def _build_clean_spec(rules) -> str:
    """
    Build a specification text that is genuinely compliant with the REAL
    rules extracted from the template/writing guide: every level-1
    mandatory section present (in template order, exact ALLCAPS heading),
    document identification (title/revision/writer), and a well-formed
    15-row requirement table (ID + 'shall' + upstream N/A) — enough rows
    to satisfy the R22 table-format heuristic (>10 pipe-rows).
    """
    lines = [
        "REQUIREMENTS DOCUMENT OF THE TEST COMPONENT (TC) MODULE",
        "",
        "Table of updates",
        "Version 1.0 | 2026-01-01 | J. Doe | Creation",
        "",
        "Written by: J. Doe    Checked by: A. Smith    Approved by: B. Jones",
        "",
    ]
    mandatory = sorted(
        (s for s in rules.mandatory_sections if s.level == 1),
        key=lambda s: s.order,
    )
    for sec in mandatory:
        lines.append(sec.name.upper())
        lines.append(f"This section fully describes {sec.name.lower()} for the test component.")
        lines.append("")

    lines.append("REQUIREMENTS TABLE")
    lines.append("Requirement ID | Description | Input Requirement")
    for i in range(1, 16):
        lines.append(
            f"REF-PSP-TEST-{i:03d} | The system shall perform function {i} "
            f"within the specified operating range. | N/A"
        )
    return "\n".join(lines)


@pytest.fixture(scope="module")
def clean_spec_text(rules):
    return _build_clean_spec(rules)


@pytest.fixture(scope="module")
def clean_report(clean_spec_text):
    return validate_with_evidence("clean_spec.txt", clean_spec_text)


def _findings_by_severity(report, severity):
    return [f for f in report["findings"] if f["severity"] == severity]


def _finding_signature(f):
    return (f["check"], f["severity"], f["rule_id"])


# ── 1. True negative: a genuinely compliant document ──────────────

class TestCleanSpecIsRecognizedAsCompliant:

    def test_zero_errors(self, clean_report):
        errors = _findings_by_severity(clean_report, "error")
        assert errors == [], f"Unexpected errors on a compliant doc: {errors}"

    def test_verdict_is_good_or_acceptable(self, clean_report):
        # With every mandatory section present and zero errors, the
        # document must not be downgraded to NOT_RELIABLE/NON_COMPLIANT.
        assert clean_report["verdict"] in ("GOOD", "ACCEPTABLE_WITH_FIXES")

    def test_structure_score_is_high(self, clean_report):
        assert clean_report["scores"]["structure"] >= 0.9

    def test_no_missing_mandatory_sections(self, clean_report):
        assert clean_report["sectionsMissing"] == []

    def test_requirements_have_ids_and_shall_and_traceability(self, clean_report):
        checks = {f["check"] for f in clean_report["findings"] if f["severity"] == "pass"}
        assert "E_REQUIREMENT_LANGUAGE" in checks  # shall used
        assert "F_REQUIREMENT_IDS" in checks       # IDs present
        assert "G_TRACEABILITY" in checks          # N/A traceability present

    def test_warning_volume_is_reasonable(self, clean_report):
        # Not chasing absolute zero: recommended sections (ERGONOMICS,
        # SAFETY, TRACEABILITY...) and deep-content checks (R15 design
        # file, R41 random noise, P10 RAMS/SdF content) legitimately warn
        # because this synthetic doc has headings but no real engineering
        # content for those topics — that IS correct behavior, not noise.
        # The point of this test is that a compliant doc doesn't drown in
        # warnings the way a genuinely bad spec does (dozens+).
        warnings = _findings_by_severity(clean_report, "warning")
        assert len(warnings) <= 10, f"Too many warnings on a compliant doc: {warnings}"


# ── 2. True positives: one injected defect → exactly that signal fires ──

class TestInjectedDefectsAreDetected:

    def test_missing_mandatory_section_flagged(self, rules, clean_spec_text, clean_report):
        mandatory = sorted(
            (s for s in rules.mandatory_sections if s.level == 1),
            key=lambda s: s.order,
        )
        target = mandatory[-1]  # remove the last one (minimizes order side-effects)
        heading = target.name.upper()
        mutated = "\n".join(
            l for l in clean_spec_text.split("\n")
            if l.strip() != heading
            and f"this section fully describes {target.name.lower()}" not in l.lower()
        )
        report = validate_with_evidence("missing_section.txt", mutated)

        baseline_sigs = {_finding_signature(f) for f in clean_report["findings"]}
        new_errors = [
            f for f in report["findings"]
            if f["severity"] == "error" and _finding_signature(f) not in baseline_sigs
        ]
        assert any(target.name in f["message"] for f in new_errors), (
            f"Expected an error naming '{target.name}' as missing; got: {new_errors}"
        )
        assert report["scores"]["structure"] < clean_report["scores"]["structure"]
        assert target.name in report["sectionsMissing"]

    def test_unresolved_double_bracket_placeholder_flagged(self, clean_spec_text, clean_report):
        mutated = clean_spec_text + "\n\nPending value: <<TBD_PLACEHOLDER_VALUE>>\n"
        report = validate_with_evidence("placeholder.txt", mutated)

        c_warnings = [
            f for f in report["findings"]
            if f["check"] == "C_PLACEHOLDER_RESIDUE" and f["severity"] == "warning"
        ]
        assert any("placeholders" in f["message"].lower() for f in c_warnings)
        assert report["scores"]["template_cleanliness"] < clean_report["scores"]["template_cleanliness"]

    def test_tbd_marker_flagged(self, clean_spec_text, clean_report):
        mutated = clean_spec_text + "\n\nCalibration value: TBD\n"
        report = validate_with_evidence("tbd.txt", mutated)

        c_warnings = [
            f for f in report["findings"]
            if f["check"] == "C_PLACEHOLDER_RESIDUE" and f["severity"] == "warning"
        ]
        assert any("TBD" in f["message"] for f in c_warnings)
        assert report["scores"]["template_cleanliness"] < clean_report["scores"]["template_cleanliness"]

    def test_no_shall_language_flagged_as_error(self, clean_spec_text):
        mutated = re.sub(r"\bshall\b", "should", clean_spec_text, flags=re.IGNORECASE)
        report = validate_with_evidence("no_shall.txt", mutated)

        errors = _findings_by_severity(report, "error")
        assert any(
            f["check"] == "E_REQUIREMENT_LANGUAGE" and "shall" in f["message"].lower()
            for f in errors
        )
        assert report["verdict"] != "GOOD"

    def test_subjective_word_flagged(self, clean_spec_text, clean_report):
        mutated = clean_spec_text.replace(
            "The system shall perform function 1",
            "The system shall perform several function 1",
        )
        assert mutated != clean_spec_text  # sanity: substitution actually happened
        report = validate_with_evidence("subjective.txt", mutated)

        baseline_sigs = {_finding_signature(f) for f in clean_report["findings"]}
        new_warnings = [
            f for f in report["findings"]
            if f["severity"] == "warning" and _finding_signature(f) not in baseline_sigs
        ]
        assert any(
            f["check"] == "E_REQUIREMENT_LANGUAGE" and "subjective" in f["message"].lower()
            for f in new_warnings
        )

    def test_missing_requirement_ids_flagged(self, clean_spec_text):
        mutated = re.sub(r"REF-PSP-TEST-\d{3}\s*\|\s*", "", clean_spec_text)
        report = validate_with_evidence("no_ids.txt", mutated)

        warnings = _findings_by_severity(report, "warning")
        assert any(
            f["check"] == "F_REQUIREMENT_IDS" and "none have formal requirement ids" in f["message"].lower()
            for f in warnings
        )

    def test_missing_traceability_flagged(self, clean_spec_text):
        # Remove every traceability signal: the "N/A" upstream values AND
        # the "Input Requirement" column header — the latter alone
        # satisfies the check's nearby-context match (\binput requirement\b)
        # even with the values gone, so it must go too for a clean negative.
        mutated = (
            clean_spec_text
            .replace("| N/A", "")
            .replace("Input Requirement", "Notes")
        )
        report = validate_with_evidence("no_trace.txt", mutated)

        warnings = _findings_by_severity(report, "warning")
        assert any(f["check"] == "G_TRACEABILITY" for f in warnings)

    def test_color_dependent_reference_flagged(self, clean_spec_text, clean_report):
        mutated = clean_spec_text.replace(
            "The system shall perform function 2",
            "The system shall display the fault status in red and the standby status in blue, function 2",
        )
        report = validate_with_evidence("color_ref.txt", mutated)

        baseline_sigs = {_finding_signature(f) for f in clean_report["findings"]}
        new_findings = [
            f for f in report["findings"] if _finding_signature(f) not in baseline_sigs
        ]
        assert any(f["rule_id"] == "R02" for f in new_findings), (
            f"Expected an R02 (color reference) finding; new findings: {new_findings}"
        )


# ── 3. Verdict/score algorithm itself (independent of any single check) ──

class TestVerdictAlgorithmIsConsistent:

    WEIGHTS = {
        "structure": 0.25, "section_order": 0.05, "template_cleanliness": 0.10,
        "requirements_quality": 0.35, "writing_guide_compliance": 0.25,
    }

    def _expected_verdict(self, scores, errors):
        overall = sum(scores.get(k, 0) * w for k, w in self.WEIGHTS.items())
        if overall >= 0.80 and errors == 0:
            return "GOOD"
        elif overall >= 0.60 and errors <= 2:
            return "ACCEPTABLE_WITH_FIXES"
        elif overall >= 0.35:
            return "NOT_RELIABLE"
        return "NON_COMPLIANT"

    @pytest.mark.parametrize("mutation", [
        "clean", "no_shall", "empty", "missing_all_sections",
    ])
    def test_verdict_matches_documented_thresholds(self, rules, clean_spec_text, mutation):
        if mutation == "clean":
            text = clean_spec_text
        elif mutation == "no_shall":
            text = re.sub(r"\bshall\b", "should", clean_spec_text, flags=re.IGNORECASE)
        elif mutation == "empty":
            text = ""
        else:  # missing_all_sections
            text = "REQUIREMENTS TABLE\nReq | Desc | Input\nREF-X-1 | The system shall work. | N/A\n" * 12

        report = validate_with_evidence(f"{mutation}.txt", text)
        errors = sum(1 for f in report["findings"] if f["severity"] == "error")
        expected = self._expected_verdict(report["scores"], errors)
        assert report["verdict"] == expected, (
            f"[{mutation}] overallScore={report['overallScore']} errors={errors} "
            f"scores={report['scores']} -> got {report['verdict']}, expected {expected}"
        )

    def test_overall_score_is_weighted_sum_of_axis_scores(self, clean_report):
        computed = sum(
            clean_report["scores"].get(k, 0) * w for k, w in self.WEIGHTS.items()
        )
        assert abs(computed - clean_report["overallScore"]) < 0.01


# ── 5. Coverage accounting: "unchecked" must mean genuinely unimplemented ──

class TestUncheckedRuleCoverageIsHonest:
    """
    Regression for a real reporting bug: several writing-guide checks
    (R06, R08, R10, R14, R31, R49, R50, R52, R53) only appended a finding
    when their trigger condition was met in the document (e.g. R06 only
    fires if the text contains BOTH 'all projects' and 'generic'). On a
    document that never mentions the topic at all, they emitted NOTHING —
    making them look identical to rules with NO check implemented, even
    though the code fully handles them. Every one of these rules must now
    emit a finding (pass/info/warning) on ANY document, so 'unchecked'
    reports only the rules that are genuinely not automatable.
    """

    CONDITIONALLY_IMPLEMENTED = [
        "R06", "R08", "R10", "R14", "R17", "R31", "R49", "R50", "R52", "R53",
    ]

    def test_conditional_rules_fire_even_when_topic_absent(self):
        # A document with none of R06/R08/R10/R14/R31/R49/R50/R52/R53's
        # trigger topics (no 'all projects', no writer/checker labels, no
        # network interfaces, no constraint section, no I/O list...).
        bare_text = "PURPOSE\nThe system shall exist.\n"
        report = validate_with_evidence("bare.txt", bare_text)
        fired_ids = {f["rule_id"] for f in report["findings"]}
        missing = [r for r in self.CONDITIONALLY_IMPLEMENTED if r not in fired_ids]
        assert not missing, (
            f"These implemented rules stayed silent (would wrongly appear "
            f"'unchecked'): {missing}"
        )

    def test_these_rules_never_appear_in_unchecked_list(self, clean_report):
        unchecked = set(clean_report["rulesUsed"]["unchecked_rule_ids"])
        overlap = unchecked & set(self.CONDITIONALLY_IMPLEMENTED)
        assert not overlap, f"Implemented rules wrongly reported as unchecked: {overlap}"


# ── 6. R17 — standards/norms declared vs. actually used ────────────

class TestStandardsConsistency:

    def test_declared_but_never_used_is_flagged_one_finding_per_standard(self, rules):
        # Declaration is a genuine "Mark | Reference | Title" table (2+
        # bracket rows) — matches the real-world structure, not narrative.
        text = (
            "APPLICABLE DOCUMENTS\nSTANDARDS\n"
            "[STA20] | 98037030 | Original part drawing\n"
            "[N41] | CS.00244 | EMC performance requirements\n\n"
            "REQUIREMENTS\nThe system shall operate correctly.\n"
        )
        findings = check_standards_consistency(text, rules)
        warnings = [f for f in findings if f.severity == "warning"]
        # One INDEPENDENT finding per standard — not one aggregated message
        assert len(warnings) == 2
        messages = {f.message for f in warnings}
        assert any("'STA20'" in m and "never cited" in m for m in messages)
        assert any("'N41'" in m and "never cited" in m for m in messages)

    def test_used_but_not_declared_is_flagged(self, rules):
        text = (
            "APPLICABLE DOCUMENTS\nSTANDARDS\n"
            "[N41] | CS.00244 | EMC performance requirements\n"
            "[N42] | CS.00263 | Environmental specification\n\n"
            "REQUIREMENTS\nThe system shall comply with [N41] and [N42], "
            "and with [STA20] during operation.\n"
        )
        findings = check_standards_consistency(text, rules)
        warnings = [f for f in findings if f.severity == "warning"]
        # N41/N42 are both declared AND used -> consistent, no warning for them.
        # Only STA20 (used, never declared) should be flagged.
        assert len(warnings) == 1
        assert "'STA20'" in warnings[0].message
        assert "not declared" in warnings[0].message.lower()

    def test_consistent_declaration_and_usage_passes(self, rules):
        text = (
            "APPLICABLE DOCUMENTS\nSTANDARDS\n"
            "[STA20] | 98037030 | Original part drawing\n"
            "[N41] | CS.00244 | EMC performance requirements\n\n"
            "REQUIREMENTS\nThe system shall comply with [STA20] and per [N41] during operation.\n"
        )
        findings = check_standards_consistency(text, rules)
        assert len(findings) == 1
        assert findings[0].severity == "pass"

    def test_narrative_declaration_without_table_still_works(self, rules):
        """Fallback path: a document that declares a Mark as plain prose
        under the heading (no table) must still be recognized."""
        text = (
            "APPLICABLE DOCUMENTS\nSTANDARDS\n"
            "This document applies [STA20].\n\n"
            "REQUIREMENTS\nThe system shall comply with [STA20].\n"
        )
        findings = check_standards_consistency(text, rules)
        assert len(findings) == 1
        assert findings[0].severity == "pass"

    def test_single_bracket_alone_is_not_a_declaration_row(self, rules):
        """A lone bracket citation mid-sentence ('...the [STA20] document')
        must NOT be mistaken for a declaration table row — only a real
        multi-column 'Mark | Reference | Title' row counts."""
        text = (
            "REQUIREMENTS\nRefer to the [STA20] document for details. "
            "The system shall comply with [STA20].\n"
        )
        declaration_text, body_text = _split_declaration_and_body(text)
        assert "STA20" not in _extract_standard_refs(declaration_text)
        assert "STA20" in _extract_standard_refs(body_text)

    def test_mark_cross_referenced_inside_another_rows_title_counts_as_declared(self, rules):
        """Regression for a real document: [N43] never has its own
        declaration row, but appears cited inside a NEIGHBORING row's
        title ('[M15] | Justification table [N43] | ...') — since that
        whole block is a genuine multi-row reference table, [N43] must
        count as declared there, not as an undeclared usage elsewhere."""
        text = (
            "APPLICABLE DOCUMENTS\n"
            "[M14] | 00893_16_00629 | Justification synthesis [N42]\n"
            "[M15] | Justification table [N43]\n"
            "[M16] | 01300_09_00024 | EE components list\n\n"
            "REQUIREMENTS\nThe system shall complete the standards [N41], [N42] and [N43].\n"
        )
        findings = check_standards_consistency(text, rules)
        assert not any(
            f.severity == "warning" and "'N43'" in f.message and "not declared" in f.message.lower()
            for f in findings
        )

    def test_no_standards_anywhere_produces_info_not_applicable(self, rules):
        # Must still emit SOMETHING (info) — an empty return would make R17
        # wrongly look "unchecked" on documents with no external standards,
        # the same reporting bug fixed for R06/R08/R10/R14/R31/R49/R50/R52/R53.
        text = "REQUIREMENTS\nThe system shall work reliably.\n"
        findings = check_standards_consistency(text, rules)
        assert len(findings) == 1
        assert findings[0].severity == "info"
        assert findings[0].rule_id == "R17"

    def test_reference_column_text_is_not_treated_as_a_standard(self, rules):
        """The real bug this guards against: '[N9] | NF EN 60352 |
        CONNEXIONS SANS SOUDURE' must be tracked ONLY as Mark 'N9' — the
        spelled-out Reference-column name ('EN 60352' / 'NF EN 60352')
        must NOT become its own independently-tracked standard, since no
        requirement in these documents ever cites a standard by that
        name — only by its Mark."""
        text = (
            "APPLICABLE DOCUMENTS\n"
            "[N8] | B25 1110 | NTS - CONVENTIONAL ELECTRICAL CONDUCTORS\n"
            "[N9] | NF EN 60352 | CONNEXIONS SANS SOUDURE\n"
            "[N10] | B14 2900 | ELECTRICAL CONNECTORS SEALING\n\n"
            "REQUIREMENTS\nThe system shall comply with [N9].\n"
        )
        findings = check_standards_consistency(text, rules)
        assert not any("EN60352" in f.message or "EN 60352" in f.message for f in findings)
        assert not any("'N9'" in f.message and "warning" == f.severity for f in findings)
        # N8 and N10 ARE genuinely declared-but-unused Marks — that stays.
        assert any(f.severity == "warning" and "'N8'" in f.message for f in findings)
        assert any(f.severity == "warning" and "'N10'" in f.message for f in findings)

    def test_bracket_refs_outside_sta_n_convention_not_matched(self):
        # [M8], [LIN1], [SSD_AUE] are internal upstream-requirement/test
        # reference tags in this corpus, NOT standards — must not match.
        refs = _extract_standard_refs("See [M8], [LIN1] and [SSD_AUE] for details.")
        assert refs == set()

    def test_wired_into_full_pipeline_and_scored(self):
        text = (
            "PURPOSE\nDefine the component.\n\n"
            "APPLICABLE DOCUMENTS\nSTANDARDS\nNo standards declared.\n\n"
            "REQUIREMENTS\nThe system shall comply with [STA20].\n"
        )
        report = validate_with_evidence("std_test.txt", text)
        assert any(f["check"] == "J_STANDARDS_CONSISTENCY" for f in report["findings"])

    def test_real_asu_spec_flags_genuinely_undeclared_standards(self):
        """
        STA19/STA20/N47/STA10 were manually verified against the real
        document: each is cited by a requirement (e.g. '...requirements
        in the document [STA20]') yet has ZERO matching row anywhere in
        any 'Mark | Reference | Title' reference table in the whole
        ~1500-line document — a genuine, confirmed compliance gap.
        """
        if not ASU_PATH.exists():
            pytest.skip("ASU spec not found")
        from app.qa.retrieval import extract_text_from_file
        text = extract_text_from_file(ASU_PATH)
        report = validate_with_evidence(ASU_PATH.name, text)
        j_findings = [f for f in report["findings"] if f["check"] == "J_STANDARDS_CONSISTENCY"]
        assert j_findings, "Expected R17 to fire on the real ASU spec"
        undeclared_msgs = [
            f["message"] for f in j_findings
            if "not declared" in f["message"].lower()
        ]
        assert any("'STA20'" in m for m in undeclared_msgs)
        assert any("'STA19'" in m for m in undeclared_msgs)

    def test_real_asu_spec_never_flags_reference_column_names(self):
        """Regression for the reported false positive: 'NF EN 60352' /
        'EN 60352' is only ever the spelled-out Reference-column text for
        Mark [N9] — no requirement cites it by that name — so it must
        never appear as its own finding. Same for ISO 26262 (declared as
        [M19]'s Reference text) and IATF 16949 (declared as [N80]'s):
        only their Mark tags are tracked, never the descriptive name."""
        if not ASU_PATH.exists():
            pytest.skip("ASU spec not found")
        from app.qa.retrieval import extract_text_from_file
        text = extract_text_from_file(ASU_PATH)
        report = validate_with_evidence(ASU_PATH.name, text)
        j_findings = [f for f in report["findings"] if f["check"] == "J_STANDARDS_CONSISTENCY"]
        all_messages = " ".join(f["message"] for f in j_findings)
        for named in ("EN60352", "EN 60352", "ISO26262", "ISO 26262", "IATF16949", "IATF 16949"):
            assert named not in all_messages, f"'{named}' should never be independently tracked"

    def test_real_asu_spec_declared_but_unused_standards_are_individually_listed(self):
        """Each declared-but-unused standard must be its OWN finding (a
        real list the report can render row-by-row), not one summary."""
        if not ASU_PATH.exists():
            pytest.skip("ASU spec not found")
        from app.qa.retrieval import extract_text_from_file
        text = extract_text_from_file(ASU_PATH)
        report = validate_with_evidence(ASU_PATH.name, text)
        j_findings = [f for f in report["findings"] if f["check"] == "J_STANDARDS_CONSISTENCY"]
        declared_unused = [f for f in j_findings if "never cited" in f["message"].lower()]
        assert len(declared_unused) >= 5
        # Each finding names exactly one standard and carries its own excerpt
        for f in declared_unused:
            assert f["user_excerpt"], f"Missing excerpt: {f}"


# ── 4. Real ASU spec — known, previously-verified true answers ────

class TestRealAsuSpecGoldenAnswers:

    @pytest.fixture(scope="class")
    def asu_report(self):
        if not ASU_PATH.exists():
            pytest.skip("ASU spec not found in data/uploads/")
        from app.qa.retrieval import extract_text_from_file
        text = extract_text_from_file(ASU_PATH)
        return validate_with_evidence(ASU_PATH.name, text)

    def test_network_interfaces_section_missing_is_a_true_error(self, asu_report):
        errors = _findings_by_severity(asu_report, "error")
        assert any(
            f["check"] == "A_SECTION_COVERAGE" and "NETWORK INTERFACES" in f["message"]
            for f in errors
        ), "Known true finding (NETWORK INTERFACES missing) not detected"
        assert "NETWORK INTERFACES" in asu_report["sectionsMissing"]

    def test_ergonomics_and_traceability_are_recognized_as_present(self, asu_report):
        """
        Regression for a real false positive: the recommended section
        'ERGONOMICS' was wrongly reported missing even though the document
        has '6.4.3 ERGONOMICS AND HUMAN FACTORS' (same for 'TRACEABILITY'
        vs. 'TRACEABILITY AND CONFIGURATION') — _section_matches used to
        reject any heading with more than one extra word beyond the
        required keyword. Fixed to match whole-word-anywhere-in-heading;
        both must now be recognized as present, not flagged as missing.
        """
        warnings = _findings_by_severity(asu_report, "warning")
        messages = " ".join(f["message"] for f in warnings)
        assert "Recommended section 'ERGONOMICS'" not in messages
        assert "Recommended section 'TRACEABILITY'" not in messages
        assert any("ERGONOMICS AND HUMAN FACTORS" in s for s in asu_report["sectionsFound"])
        assert any("TRACEABILITY" in s for s in asu_report["sectionsFound"])

    def test_verdict_is_in_expected_band(self, asu_report):
        # Known since the engine fixes: not GOOD (real gaps exist), not
        # NON_COMPLIANT (score is still fairly high) — bounded, not exact,
        # so this survives minor unrelated scoring tweaks.
        assert asu_report["verdict"] in ("ACCEPTABLE_WITH_FIXES", "NOT_RELIABLE")
        assert 0.55 <= asu_report["overallScore"] <= 0.95

    def test_hundreds_of_requirements_detected(self, asu_report):
        # The real spec has ~100+ 'shall'/'must' statements — the language
        # and ID/traceability checks must have real data to work with.
        req_findings = [
            f for f in asu_report["findings"]
            if f["check"] in ("E_REQUIREMENT_LANGUAGE", "F_REQUIREMENT_IDS", "G_TRACEABILITY")
        ]
        assert req_findings, "Requirement-level checks produced nothing on a 100+ page real spec"

    def test_every_error_and_warning_has_complete_evidence(self, asu_report):
        """The double-evidence policy must hold on REAL data, not just
        hand-crafted test snippets: every error/warning must cite a source
        rule and explain why it matters."""
        for f in asu_report["findings"]:
            if f["severity"] in ("error", "warning"):
                assert f["source_rule"].strip(), f"Missing source_rule: {f}"
                assert f["why"].strip(), f"Missing why: {f}"
                assert f["rule_id"].strip(), f"Missing rule_id: {f}"


# ── 7. Section matching: numbered/multi-word headings ──────────────

class TestSectionMatchingWholeWord:
    """
    Regression for a real false positive: a recommended section like
    'ERGONOMICS' was reported missing even though the document has it as
    '6.4.3 ERGONOMICS AND HUMAN FACTORS'. _section_matches used to reject
    any match where the found heading had more than one extra word beyond
    the required keyword — fixed to whole-word-anywhere-in-heading.
    """

    def test_short_keyword_matches_numbered_multiword_heading(self):
        from app.qa.evidence_comparator import _section_matches
        found = _section_matches("ERGONOMICS", ["ERGONOMICS AND HUMAN FACTORS"])
        assert found == "ERGONOMICS AND HUMAN FACTORS"

    def test_short_keyword_matches_heading_with_many_extra_words(self):
        from app.qa.evidence_comparator import _section_matches
        found = _section_matches(
            "TRACEABILITY", ["TRACEABILITY AND CONFIGURATION MANAGEMENT PLAN"]
        )
        assert found == "TRACEABILITY AND CONFIGURATION MANAGEMENT PLAN"

    def test_does_not_match_unrelated_word_containing_substring(self):
        from app.qa.evidence_comparator import _section_matches
        # 'scope' must not match inside an unrelated word like 'telescope'
        found = _section_matches("SCOPE", ["TELESCOPE CALIBRATION PROCEDURE"])
        assert found is None

    def test_numbered_heading_with_tab_is_detected_and_matched(self):
        from app.qa.evidence_comparator import _detect_user_sections, _section_matches
        text = "Some intro text.\n6.4.3\tERGONOMICS AND HUMAN FACTORS\nBody text here.\n"
        sections = [name for name, _ in _detect_user_sections(text)]
        assert "ERGONOMICS AND HUMAN FACTORS" in sections
        assert _section_matches("ERGONOMICS", sections) == "ERGONOMICS AND HUMAN FACTORS"


# ── 8. Traceability: itemized list of untraced requirements ────────

class TestTraceabilityItemization:

    def test_untraced_requirements_are_individually_located(self, rules):
        # Real documents split ONE requirement's own trace marker across
        # several physical lines (a multi-paragraph table cell), so the
        # check looks ±10 lines around each 'shall' line, not just the
        # exact same line — this fixture pads enough unrelated filler
        # between requirements (12 lines, more than the ±10 window) so
        # REF-A-002/003's absence of a marker isn't masked by REF-A-001's
        # nearby 'N/A' bleeding across the gap.
        filler = "\n".join(f"Unrelated filler content line number {i} here." for i in range(12))
        text = (
            "REQUIREMENTS\n"
            "REF-A-001 | The system shall start within 2 seconds. | N/A\n"
            + filler + "\n"
            "REF-A-002 | The system shall stop within 1 second.\n"
            + filler + "\n"
            "REF-A-003 | The system shall log every event that occurs during operation and store it.\n"
        )
        from app.qa.evidence_comparator import check_traceability
        results = check_traceability(text, rules)
        assert len(results) == 1
        finding = results[0]
        assert finding.severity == "warning"
        # 1/3 traced (REF-A-001 has N/A) -> the other 2 must be itemized
        assert len(finding.items) == 2
        ids = {item["id"] for item in finding.items}
        assert "REF-A-002" in ids
        assert "REF-A-003" in ids
        for item in finding.items:
            assert "REQUIREMENTS" in item["location"]
            assert item["excerpt"]

    def test_traceability_marker_a_few_lines_away_still_counts(self, rules):
        """
        Regression for a real false positive: a requirement's description
        spans several bullet lines (one 'shall' per bullet), and its
        Input Requirement value is appended to the LAST bullet's line —
        several lines below an EARLIER 'shall' bullet in the same cell.
        That earlier bullet must NOT be reported as its own untraced
        requirement; the whole block shares one real upstream reference.
        """
        from app.qa.evidence_comparator import check_traceability
        text = (
            "REQUIREMENTS\n"
            "REF-A-001\n"
            "The function shall manage the following states:\n"
            "- Idle State: the system shall wait for activation;\n"
            "- Active State: the system shall monitor for events; | [SSD_AUE]\n"
        )
        results = check_traceability(text, rules)
        assert results[0].severity == "pass"
        assert results[0].items == []

    def test_untraced_item_uses_line_number_when_no_id_present(self, rules):
        from app.qa.evidence_comparator import check_traceability
        text = "REQUIREMENTS\nThe system shall behave correctly under all conditions tested.\n"
        results = check_traceability(text, rules)
        assert len(results) == 1
        assert len(results[0].items) == 1
        assert results[0].items[0]["id"].startswith("Line")

    def test_untraced_item_uses_owning_requirement_id_not_a_bare_line_number(self, rules):
        """
        Regression: a real requirement row is flattened as one ID-bearing
        line followed by several continuation "shall" bullets with no ID
        of their own (e.g. a multi-state description). The untraced item
        for one of THOSE bullet lines must show the requirement's real ID
        (found by looking back to the nearest preceding ID-bearing line),
        not a meaningless "Line N" — the user should see WHICH requirement
        is missing traceability, not just a line number.
        """
        from app.qa.evidence_comparator import check_traceability
        text = (
            "REQUIREMENTS\n"
            "REF-ASU-CD-EXINTER-0007(0) | In the standby operating situation of the ASU:\n"
            "- Average consumption of an EE component shall be 125 uA at the maximum for a power supply.\n"
        )
        results = check_traceability(text, rules)
        assert len(results[0].items) == 1
        # REQ_ID_RE's own trailing \d+ never includes the "(0)" version
        # suffix (true for every ID in this codebase, not just this test).
        assert results[0].items[0]["id"] == "REF-ASU-CD-EXINTER-0007"

    def test_untraced_item_resolves_id_with_the_real_documents_embedded_space_typo(self, rules):
        """
        Regression: the real ASU spec has a recurring typo where one ID
        segment is wrapped in stray spaces instead of dashes — e.g.
        "REF-ASU-CD- EXINTER -0005(0)" instead of "REF-ASU-CD-EXINTER-0005"
        — which the ORIGINAL REQ_ID_RE failed to match at all (it has no
        way to bridge an embedded whitespace), silently falling back to
        "Line N" for every single one of these real, ID-bearing
        requirements. REQ_ID_RE now bridges exactly one such space-wrapped
        ALL-CAPS segment.
        """
        from app.qa.evidence_comparator import check_traceability
        text = (
            "REQUIREMENTS\n"
            "REF-ASU-CD- EXINTER -0007(0) | In the standby operating situation of the ASU:\n"
            "- Average consumption of an EE component shall be 125 uA at the maximum for a power supply.\n"
        )
        results = check_traceability(text, rules)
        assert len(results[0].items) == 1
        assert results[0].items[0]["id"] == "REF-ASU-CD- EXINTER -0007"

    def test_real_asu_spec_untraced_items_use_real_ids_not_line_numbers(self):
        """
        On the real ASU spec, every untraced 'shall' bullet is a
        continuation line inside a requirement's table row (the ID sits
        on an earlier line of that same row, sometimes with the
        document's embedded-space typo) — the itemized list must resolve
        the owning requirement ID for ALL of them, never falling back to
        a meaningless 'Ligne N'.
        """
        if not ASU_PATH.exists():
            pytest.skip("ASU spec not found")
        from app.qa.retrieval import extract_text_from_file
        from app.qa.evidence_comparator import check_traceability
        text = extract_text_from_file(ASU_PATH)
        rules = extract_all_rules()
        results = check_traceability(text, rules)
        items = results[0].items
        assert items
        bare_line_fallbacks = [it for it in items if it["id"].startswith("Ligne")]
        assert bare_line_fallbacks == []

    def test_fully_traced_document_has_no_untraced_items(self, rules):
        from app.qa.evidence_comparator import check_traceability
        text = (
            "REQUIREMENTS\n"
            "REF-A-001 | The system shall start within 2 seconds. | N/A\n"
            "REF-A-002 | The system shall stop within 1 second. | N/A\n"
        )
        results = check_traceability(text, rules)
        assert results[0].severity == "pass"
        assert results[0].items == []

    def test_real_asu_spec_traceability_is_accurate_once_context_is_considered(self):
        """
        Before the ±10-line context check was applied unconditionally,
        this reported "12/105 (11%)" — because most requirements' actual
        upstream reference sat a few lines below an EARLIER 'shall'
        bullet in the same multi-paragraph table cell (confirmed on
        REF-ASU-CD-EXIFUNC-002: has both a VF_xxx ref and [SSD_AUE], yet
        its 'Idle State' sub-bullet was flagged as its own untraced
        requirement). The true ratio is much higher once nearby-line
        context is always considered, not only as an all-or-nothing
        document-wide fallback.
        """
        if not ASU_PATH.exists():
            pytest.skip("ASU spec not found")
        from app.qa.retrieval import extract_text_from_file
        from app.qa.evidence_comparator import check_traceability
        text = extract_text_from_file(ASU_PATH)
        rules = extract_all_rules()
        results = check_traceability(text, rules)
        trace_finding = results[0]
        assert trace_finding.severity == "pass"
        assert "requirements reference upstream requirements" in trace_finding.message
        # Sanity: the ratio must be well above the old (bugged) 11%.
        import re as _re
        m = _re.search(r"(\d+)/(\d+)", trace_finding.message)
        assert m and int(m.group(1)) / int(m.group(2)) >= 0.6
        for item in trace_finding.items[:10]:
            assert item["id"]
            assert item["location"]

    def test_items_serialized_into_report_dict(self):
        text = (
            "REQUIREMENTS\n"
            "REF-A-001 | The system shall start within 2 seconds.\n"
        )
        report = validate_with_evidence("trace.txt", text)
        trace_findings = [f for f in report["findings"] if f["check"] == "G_TRACEABILITY"]
        assert trace_findings
        assert "items" in trace_findings[0]
        assert trace_findings[0]["items"][0]["id"] == "REF-A-001"

    def test_passing_document_still_itemizes_its_remaining_untraced_requirements(self, rules):
        """
        Regression: a document can have an overall traceability ratio good
        enough to "pass" (>= 50%) while still containing individual
        requirements with NO input/upstream reference at all. Those must
        stay visible as itemized entries — previously the "pass" branch
        never attached `items`, so any document above the 50% threshold
        silently hid every one of its remaining untraced requirements from
        the report, no matter how many there were.
        """
        filler = "\n".join(f"Unrelated filler content line number {i} here." for i in range(12))
        text = (
            "REQUIREMENTS\n"
            "REF-A-001 | The system shall start within 2 seconds. | N/A\n"
            + filler + "\n"
            "REF-A-002 | The system shall stop within 1 second.\n"
        )
        from app.qa.evidence_comparator import check_traceability
        results = check_traceability(text, rules)
        finding = results[0]
        assert finding.severity == "pass"  # 1/2 traced = 50% -> passes overall
        assert len(finding.items) == 1
        assert finding.items[0]["id"] == "REF-A-002"


class TestStructuralTraceability:
    """
    The CTS requirement tables have an explicit "Input requirement" column.
    Reading that cell is EXACT; the flattened-text heuristic can only guess
    (extract_text_from_file drops empty cells, so a blank upstream column is
    invisible, and a neighbour's reference bleeds into the ±10-line window).
    """

    @staticmethod
    def _build_req_docx(rows, header=("Requirement Number (v)",
                                      "Description of the requirement",
                                      "Input requirement (v)")):
        """rows: list of (id, description, upstream)."""
        import io
        from docx import Document
        doc = Document()
        doc.add_heading("FUNCTIONAL REQUIREMENTS", level=1)
        table = doc.add_table(rows=len(rows) + 1, cols=3)
        for ci, h in enumerate(header):
            table.cell(0, ci).text = h
        for ri, (rid, desc, up) in enumerate(rows, 1):
            table.cell(ri, 0).text = rid
            table.cell(ri, 1).text = desc
            table.cell(ri, 2).text = up
        buf = io.BytesIO()
        doc.save(buf)
        return buf.getvalue()

    def _write(self, tmp_path, data, name="spec.docx"):
        p = tmp_path / name
        p.write_bytes(data)
        return p

    def test_empty_upstream_cell_is_untraced(self, tmp_path):
        from app.qa.evidence_comparator import extract_requirement_rows
        data = self._build_req_docx([
            ("REF-A-CD-FUNC-0001(0)", "The system shall start.", ""),
            ("REF-A-CD-FUNC-0002(0)", "The system shall stop.", "[SSD_X] REQ-123"),
        ])
        rows = extract_requirement_rows(self._write(tmp_path, data))
        assert len(rows) == 2
        assert rows[0].traced is False
        assert rows[1].traced is True

    def test_na_upstream_counts_as_traced_per_r22(self, tmp_path):
        """R22: 'When there is no input requirement, the field is filled with
        N/A.' — an explicit N/A is compliant, not a gap."""
        from app.qa.evidence_comparator import extract_requirement_rows
        for na in ("N/A", "n/a", "NA", "None", "sans objet", "-"):
            data = self._build_req_docx([("REF-A-CD-FUNC-0001(0)", "shall x.", na)])
            rows = extract_requirement_rows(self._write(tmp_path, data, f"s_{na.replace('/','')}.docx"))
            assert len(rows) == 1
            assert rows[0].traced is True, f"{na!r} should count as traced"

    def test_tables_without_an_upstream_column_are_ignored(self, tmp_path):
        """A glossary/description table must never pollute the statistics."""
        from app.qa.evidence_comparator import extract_requirement_rows
        data = self._build_req_docx(
            [("REF-A-CD-FUNC-0001(0)", "shall x.", "")],
            header=("Term", "Definition", "Comment"),
        )
        assert extract_requirement_rows(self._write(tmp_path, data)) == []

    def test_rows_without_a_requirement_id_are_ignored(self, tmp_path):
        from app.qa.evidence_comparator import extract_requirement_rows
        data = self._build_req_docx([
            ("REF-A-CD-FUNC-0001(0)", "shall x.", ""),
            ("", "a continuation note with no ID", ""),
            ("see above", "another non-requirement row", ""),
        ])
        rows = extract_requirement_rows(self._write(tmp_path, data))
        assert len(rows) == 1
        assert rows[0].req_id == "REF-A-CD-FUNC-0001"

    def test_section_is_the_heading_above_the_table(self, tmp_path):
        from app.qa.evidence_comparator import extract_requirement_rows
        data = self._build_req_docx([("REF-A-CD-FUNC-0001(0)", "shall x.", "")])
        rows = extract_requirement_rows(self._write(tmp_path, data))
        assert rows[0].section == "FUNCTIONAL REQUIREMENTS"

    def test_non_docx_source_returns_empty_so_caller_falls_back(self, tmp_path):
        from app.qa.evidence_comparator import extract_requirement_rows
        p = tmp_path / "spec.txt"
        p.write_text("REF-A-001 | The system shall start. | N/A", encoding="utf-8")
        assert extract_requirement_rows(p) == []

    def test_check_uses_structural_rows_when_supplied(self, tmp_path, rules):
        from app.qa.evidence_comparator import (
            extract_requirement_rows, check_traceability,
        )
        data = self._build_req_docx([
            ("REF-A-CD-FUNC-0001(0)", "The system shall start.", ""),
            ("REF-A-CD-FUNC-0002(0)", "The system shall stop.", ""),
            ("REF-A-CD-FUNC-0003(0)", "The system shall log.", "[SSD_X]"),
        ])
        path = self._write(tmp_path, data)
        rows = extract_requirement_rows(path)
        results = check_traceability("", rules, req_rows=rows)
        assert len(results) == 1
        f = results[0]
        assert f.severity == "warning"
        assert "2 of 3" in f.message
        assert {i["id"] for i in f.items} == {
            "REF-A-CD-FUNC-0001", "REF-A-CD-FUNC-0002",
        }

    def test_fully_declared_document_passes(self, tmp_path, rules):
        from app.qa.evidence_comparator import (
            extract_requirement_rows, check_traceability,
        )
        data = self._build_req_docx([
            ("REF-A-CD-FUNC-0001(0)", "shall start.", "N/A"),
            ("REF-A-CD-FUNC-0002(0)", "shall stop.", "[SSD_X]"),
        ])
        rows = extract_requirement_rows(self._write(tmp_path, data))
        f = check_traceability("", rules, req_rows=rows)[0]
        assert f.severity == "pass"
        assert f.items == []

    def test_real_asu_spec_structural_counts_are_exact(self):
        """
        Ground truth, verified by reading the raw table cells directly:
        272 requirement rows, 116 with a filled 'Input requirement' cell,
        156 empty. The old text heuristic reported only 20 untraced — it
        silently missed 136 real gaps.
        """
        if not ASU_PATH.exists():
            pytest.skip("ASU spec not found")
        from app.qa.evidence_comparator import extract_requirement_rows
        rows = extract_requirement_rows(ASU_PATH)
        assert len(rows) == 272
        assert sum(1 for r in rows if r.traced) == 116
        assert sum(1 for r in rows if not r.traced) == 156

    def test_every_requirement_id_cell_in_the_real_spec_is_recognised(self):
        """
        Completeness guard: a requirement whose ID the regex fails to parse
        is dropped from the traceability statistics ENTIRELY — silently
        under-reporting gaps. Every non-empty ID cell in every requirement
        table must therefore be recognised, with zero exceptions.
        """
        if not ASU_PATH.exists():
            pytest.skip("ASU spec not found")
        from docx import Document
        from app.qa.evidence_comparator import (
            REQ_ID_RE, _UPSTREAM_HEADER_RE, _normalize_ws_lower,
        )
        doc = Document(str(ASU_PATH))
        unmatched = []
        for ti, table in enumerate(doc.tables):
            if not table.rows:
                continue
            hdr = [_normalize_ws_lower(c.text) for c in table.rows[0].cells]
            if not any(_UPSTREAM_HEADER_RE.search(h) for h in hdr):
                continue
            for ri, row in enumerate(table.rows):
                if ri == 0:
                    continue
                raw = row.cells[0].text.strip()
                if raw and not REQ_ID_RE.search(raw):
                    unmatched.append((ti, ri, raw[:60]))
        assert unmatched == []

    def test_user_reported_exinter_requirements_are_all_flagged(self):
        """
        The engineer pasted EXINTER-0001..0006 — every one has an empty
        'Input requirement' cell. 0001/0002/0004 were previously missed
        because their ID uses a space on only ONE side of a separator
        ("REF-ASU-CD- EXINTER-0001"), which the old regex could not parse.
        """
        if not ASU_PATH.exists():
            pytest.skip("ASU spec not found")
        from app.qa.evidence_comparator import extract_requirement_rows
        rows = extract_requirement_rows(ASU_PATH)
        untraced = {r.req_id.replace(" ", "") for r in rows if not r.traced}
        for n in range(1, 7):
            assert f"REF-ASU-CD-EXINTER-{n:04d}" in untraced

    def test_real_asu_spec_sections_are_correctly_mapped(self):
        """
        Regression for an lxml id()-reuse bug: keying the table→heading map
        on id(element) silently returned ANOTHER table's heading (the PERF
        requirements were labelled 'FLEXIBILITY AND EXTENSION'). The map is
        positional now.
        """
        if not ASU_PATH.exists():
            pytest.skip("ASU spec not found")
        from app.qa.evidence_comparator import extract_requirement_rows
        rows = extract_requirement_rows(ASU_PATH)
        # PERF-0001..0005 live in one table under "ASU performance
        # requirements"; PERF-0006..0009 are in a LATER table under a
        # different heading ("Time requirements") — so this asserts on the
        # first table only. With the id()-keyed map these were mislabelled
        # "FLEXIBILITY AND EXTENSION", a heading from elsewhere entirely.
        first_perf = [
            r for r in rows
            if r.req_id.startswith("APP-ASU-CD-PERF")
            and int(r.req_id.rsplit("-", 1)[1]) <= 5
        ]
        assert len(first_perf) == 5
        assert all(r.section == "ASU performance requirements" for r in first_perf)
        # And the mapped heading must genuinely be a heading in the document,
        # never a value borrowed from an unrelated table.
        assert {r.section for r in rows} <= set(_asu_heading_texts())

    def test_real_asu_spec_end_to_end_report_uses_structural_result(self):
        if not ASU_PATH.exists():
            pytest.skip("ASU spec not found")
        from app.qa.retrieval import extract_text_from_file
        text = extract_text_from_file(ASU_PATH)
        report = validate_with_evidence(ASU_PATH.name, text, source_path=ASU_PATH)
        trace = [f for f in report["findings"] if f["check"] == "G_TRACEABILITY"]
        assert len(trace) == 1
        assert trace[0]["severity"] == "warning"
        assert len(trace[0]["items"]) == 156
        assert "272" in trace[0]["message"]

    def test_without_source_path_the_text_heuristic_is_still_used(self):
        """Backwards compatibility: PDF/TXT callers pass no source_path."""
        if not ASU_PATH.exists():
            pytest.skip("ASU spec not found")
        from app.qa.retrieval import extract_text_from_file
        text = extract_text_from_file(ASU_PATH)
        report = validate_with_evidence(ASU_PATH.name, text)
        trace = [f for f in report["findings"] if f["check"] == "G_TRACEABILITY"]
        assert len(trace) == 1
        # heuristic path -> different (approximate) numbers, must not crash
        assert "requirements" in trace[0]["message"]


class TestNestedTableExtraction:
    """
    Real CTS specs routinely put a requirement's actual content — a
    failure-mode table, a fault-list table, a configurable-data table —
    INSIDE the "Description" cell as a NESTED table, not as plain
    paragraphs. python-docx's own Cell.text silently returns "" for that
    content (it only reads the cell's own paragraphs), which made these
    requirements invisible to every check: no text to scan for "shall",
    placeholders, or anything else, and an empty excerpt that could never
    be located or highlighted in the annotated spec.
    """

    @staticmethod
    def _build_nested_docx(outer_rows):
        """
        outer_rows: list of (id, own_paragraph_text_or_None, nested_rows_or_None, upstream)
        nested_rows: list of tuples of cell strings, first tuple is the
        nested table's own header row.
        """
        import io
        from docx import Document
        doc = Document()
        doc.add_heading("FUNCTIONAL REQUIREMENTS", level=1)
        table = doc.add_table(rows=len(outer_rows) + 1, cols=3)
        table.cell(0, 0).text = "Requirement Number (v)"
        table.cell(0, 1).text = "Description of the requirement"
        table.cell(0, 2).text = "Input requirement (v)"
        for ri, (rid, own_text, nested_rows, upstream) in enumerate(outer_rows, 1):
            table.cell(ri, 0).text = rid
            desc_cell = table.cell(ri, 1)
            if own_text:
                desc_cell.paragraphs[0].text = own_text
            if nested_rows:
                n_cols = max(len(r) for r in nested_rows)
                nested = desc_cell.add_table(rows=len(nested_rows), cols=n_cols)
                for nri, nrow in enumerate(nested_rows):
                    for nci, val in enumerate(nrow):
                        nested.cell(nri, nci).text = val
            table.cell(ri, 2).text = upstream
        buf = io.BytesIO()
        doc.save(buf)
        return buf.getvalue()

    def _write(self, tmp_path, data, name="nested.docx"):
        p = tmp_path / name
        p.write_bytes(data)
        return p

    def test_extract_cell_text_recurses_into_a_nested_table(self, tmp_path):
        from app.qa.retrieval import extract_cell_text
        from docx import Document
        data = self._build_nested_docx([
            ("REF-A-001", None,
             [("Failure mode", "Physical failure mode", "Max PPM"),
              ("Loss of communication", "Loss of comm", "100")],
             ""),
        ])
        doc = Document(self._write(tmp_path, data))
        cell = doc.tables[0].rows[1].cells[1]
        assert cell.text.strip() == ""  # python-docx's own property misses it entirely
        full = extract_cell_text(cell)
        assert "Failure mode" in full
        assert "Loss of communication" in full

    def test_description_does_not_drop_a_nested_tables_first_row_as_a_fake_header(self, tmp_path):
        """
        Regression: an earlier version guessed that a nested table's FIRST
        row was always a generic column-header to drop from the excerpt.
        Real requirements disprove that — e.g. a voltage-vs-time profile
        table has no column labels at all, so its first row IS genuine
        data (confirmed on the real ASU spec: REF-ASU-CD-EXINTER-0018's
        first data point "U0 | 11,4V | t0" was being silently discarded).
        The description must always be lossless: every nested row present.
        """
        from app.qa.evidence_comparator import extract_requirement_rows
        data = self._build_nested_docx([
            ("REF-A-001(0)", "shall resist to N restarts with the profile below:",
             [("U0", "11,4V", "t0"),
              ("Umin", "7,6V", "t0+1,5ms"),
              ("U0", "11,4V", "t0+1102ms")],
             ""),
        ])
        rows = extract_requirement_rows(self._write(tmp_path, data))
        r = next(r for r in rows if r.req_id == "REF-A-001")
        assert "U0 | 11,4V | t0" in r.description
        assert "Umin | 7,6V | t0+1,5ms" in r.description
        assert "U0 | 11,4V | t0+1102ms" in r.description

    def test_annotator_provides_a_whole_cell_unit_matching_the_excerpt_exactly(self, tmp_path):
        """
        A requirement's own text often precedes its nested data table
        ("Configurable data:\nTYPE_HEARTBEAT | ..."), so the excerpt spans
        BOTH — no single nested-table ROW can ever contain that combined
        text. The annotator must expose one "whole cell" unit (own text +
        the full nested table) built with the SAME extract_cell_text call
        evidence_comparator.py uses, so the excerpt always finds a match.
        """
        from app.qa.retrieval import extract_cell_text
        from app.qa.spec_annotator import _iter_docx_units, _normalize_ws
        from docx import Document
        data = self._build_nested_docx([
            ("REF-A-001(0)", "Configurable data:",
             [("Parameter name", "Range", "Value by default"),
              ("TYPE_HEARTBEAT", "0-2", "1")],
             ""),
        ])
        path = self._write(tmp_path, data)
        doc = Document(path)
        cell = doc.tables[0].rows[1].cells[1]
        expected = _normalize_ws(extract_cell_text(cell))
        units = [_normalize_ws(t) for _, t in _iter_docx_units(doc)]
        assert any(u == expected for u in units), (
            "no unit exactly matches the cell's own-text-plus-nested-table content"
        )

    def test_extract_requirement_rows_reads_nested_description_and_traces_correctly(self, tmp_path):
        from app.qa.evidence_comparator import extract_requirement_rows
        data = self._build_nested_docx([
            ("REF-A-001(0)", None,
             [("Failure mode", "Physical failure mode", "Max PPM"),
              ("Loss of communication", "Loss of comm", "100")],
             ""),   # empty upstream -> untraced
            ("REF-A-002(0)", None,
             [("Failure mode", "Physical failure mode", "Max PPM"),
              ("Loss of power", "Loss of power", "50")],
             "[SSD_X]"),  # filled upstream -> traced
        ])
        rows = extract_requirement_rows(self._write(tmp_path, data))
        by_id = {r.req_id: r for r in rows}
        assert "Loss of communication" in by_id["REF-A-001"].description
        assert by_id["REF-A-001"].traced is False
        assert by_id["REF-A-002"].traced is True

    def test_annotator_highlights_content_that_only_exists_in_a_nested_table(self, tmp_path):
        """
        End-to-end: a requirement whose entire description is a nested
        table, with an empty Input requirement cell, must be (a) reported
        as untraced with a real excerpt and (b) actually findable and
        highlighted in the annotated copy — not silently invisible.
        """
        import io
        from docx.oxml.ns import qn
        from docx.table import Table
        from app.qa.evidence_comparator import validate_with_evidence
        from app.qa.retrieval import extract_text_from_file
        from app.qa.spec_annotator import generate_annotated_spec
        from docx import Document
        from docx.enum.text import WD_COLOR_INDEX

        data = self._build_nested_docx([
            ("REF-A-001(0)", None,
             [("Failure mode", "Physical failure mode", "Max PPM"),
              ("Loss of a very distinctive sentinel value", "Loss of comm", "100")],
             ""),
        ])
        path = self._write(tmp_path, data)
        text = extract_text_from_file(path)
        assert "Loss of a very distinctive sentinel value" in text  # global extraction sees it too

        report = validate_with_evidence("nested.docx", text, source_path=path)
        trace = [f for f in report["findings"] if f["check"] == "G_TRACEABILITY"][0]
        item = next(i for i in trace["items"] if i["id"] == "REF-A-001")
        assert "Loss of a very distinctive sentinel value" in item["excerpt"]

        annotated_bytes, count = generate_annotated_spec(path.read_bytes(), ".docx", report)
        assert count >= 1
        doc = Document(io.BytesIO(annotated_bytes))
        found = False
        for t in doc.tables:
            for row in t.rows:
                for cell in row.cells:
                    for nested_el in cell._tc.findall(qn("w:tbl")):
                        nested = Table(nested_el, cell)
                        for nrow in nested.rows:
                            for ncell in nrow.cells:
                                for p in ncell.paragraphs:
                                    if "sentinel value" in p.text and any(
                                        r.font.highlight_color == WD_COLOR_INDEX.VIOLET
                                        for r in p.runs
                                    ):
                                        found = True
        assert found, "nested-table content was not highlighted"

    def test_annotator_highlights_own_text_plus_nested_table_together(self, tmp_path):
        """
        Same end-to-end check, but for the OTHER real shape: the cell's own
        paragraph ("Configurable data:") precedes its nested table (e.g.
        REF-ASU-CD-LIN-0014..0019 on the real ASU spec) — the excerpt spans
        both, so only the combined "whole cell" unit can match it.
        """
        import io
        from docx.oxml.ns import qn
        from docx.table import Table
        from app.qa.evidence_comparator import validate_with_evidence
        from app.qa.retrieval import extract_text_from_file
        from app.qa.spec_annotator import generate_annotated_spec
        from docx import Document
        from docx.enum.text import WD_COLOR_INDEX

        data = self._build_nested_docx([
            ("REF-A-001(0)", "Configurable data:",
             [("Parameter name", "Range", "Value by default"),
              ("A_VERY_DISTINCTIVE_PARAM", "0-10", "5")],
             ""),
        ])
        path = self._write(tmp_path, data)
        text = extract_text_from_file(path)
        report = validate_with_evidence("nested2.docx", text, source_path=path)
        trace = [f for f in report["findings"] if f["check"] == "G_TRACEABILITY"][0]
        item = next(i for i in trace["items"] if i["id"] == "REF-A-001")
        assert "A_VERY_DISTINCTIVE_PARAM" in item["excerpt"]
        assert "Configurable data" in item["excerpt"]

        annotated_bytes, count = generate_annotated_spec(path.read_bytes(), ".docx", report)
        assert count >= 1
        doc = Document(io.BytesIO(annotated_bytes))

        def is_highlighted(needle):
            for t in doc.tables:
                for row in t.rows:
                    for cell in row.cells:
                        for p in cell.paragraphs:
                            if needle in p.text and any(
                                r.font.highlight_color == WD_COLOR_INDEX.VIOLET for r in p.runs
                            ):
                                return True
                        for nested_el in cell._tc.findall(qn("w:tbl")):
                            nested = Table(nested_el, cell)
                            for nrow in nested.rows:
                                for ncell in nrow.cells:
                                    for p in ncell.paragraphs:
                                        if needle in p.text and any(
                                            r.font.highlight_color == WD_COLOR_INDEX.VIOLET
                                            for r in p.runs
                                        ):
                                            return True
            return False

        assert is_highlighted("A_VERY_DISTINCTIVE_PARAM"), \
            "own-text-plus-nested-table content was not highlighted"

    def test_real_asu_spec_every_untraced_item_matches_a_real_highlightable_unit(self):
        """
        Exhaustive check on the real document: EVERY one of the 156
        untraced-requirement excerpts must correspond to an actual,
        findable unit in the original docx. Before this fix, 8 of them
        (REF-ASU-CD-LIN-0014..0019, REF-ASU-CD-EXINTER-0004/-0018) silently
        matched nothing, because their excerpt combined the cell's own
        preceding text with its nested table — a combination no unit
        exposed. A verdict that "looks" complete but silently drops 8 real
        gaps from the visual annotation is exactly the failure mode this
        guards against.
        """
        if not ASU_PATH.exists():
            pytest.skip("ASU spec not found")
        from docx import Document
        from app.qa.retrieval import extract_text_from_file
        from app.qa.evidence_comparator import validate_with_evidence
        from app.qa.spec_annotator import (
            _iter_docx_units, _normalize_ws, _clean_target,
        )

        text = extract_text_from_file(ASU_PATH)
        report = validate_with_evidence(ASU_PATH.name, text, source_path=ASU_PATH)
        trace = [f for f in report["findings"] if f["check"] == "G_TRACEABILITY"][0]
        assert len(trace["items"]) == 156

        doc = Document(str(ASU_PATH))
        unit_texts = [_normalize_ws(t) for _, t in _iter_docx_units(doc)]

        unmatched = []
        for item in trace["items"]:
            cleaned = _clean_target(item["excerpt"])
            if not cleaned:
                continue  # too short to be a meaningful target; not this bug
            nt = _normalize_ws(cleaned)
            if not any(nt in ut for ut in unit_texts):
                unmatched.append(item["id"])
        assert unmatched == []


class TestRequirementIdRegex:
    """Direct tests for REQ_ID_RE, independent of check_traceability."""

    def test_matches_clean_id(self):
        from app.qa.evidence_comparator import REQ_ID_RE
        m = REQ_ID_RE.search("REF-ASU-CD-EXINTER-0007(0) | some description")
        assert m and m.group(0) == "REF-ASU-CD-EXINTER-0007"

    def test_bridges_the_real_documents_embedded_space_typo(self):
        from app.qa.evidence_comparator import REQ_ID_RE
        m = REQ_ID_RE.search("REF-ASU-CD- EXINTER -0007(0) | some description")
        assert m and m.group(0) == "REF-ASU-CD- EXINTER -0007"

    @pytest.mark.parametrize("raw,expected", [
        # every separator variant that occurs verbatim in the real ASU spec
        ("REF-ASU-CD-EXIFUNC-001", "REF-ASU-CD-EXIFUNC-001"),
        ("REF- ASU-CD-EXIFUNC-023", "REF- ASU-CD-EXIFUNC-023"),
        ("REF-ASU-CD- EXINTER-0001 (0)", "REF-ASU-CD- EXINTER-0001"),
        ("REF-ASU-CD- EXINTER -0005(0)", "REF-ASU-CD- EXINTER -0005"),
        ("REF-ASU-CD- -CONN-0002(0)", "REF-ASU-CD- -CONN-0002"),
        ("REF-SIR-CD ESSAI-0002(0)", "REF-SIR-CD ESSAI-0002"),
        ("APP-ASU-CD-SdF-0001(0)", "APP-ASU-CD-SdF-0001"),
        ("REF-ASU-CD-Safety-0001", "REF-ASU-CD-Safety-0001"),
        ("GEN-ALM-CDC-SDF-042", "GEN-ALM-CDC-SDF-042"),
    ])
    def test_all_real_separator_variants_are_parsed(self, raw, expected):
        from app.qa.evidence_comparator import REQ_ID_RE
        m = REQ_ID_RE.search(raw)
        assert m and m.group(0) == expected

    def test_space_separator_requires_uppercase_next_segment(self):
        """The whitespace-only separator exists for 'REF-SIR-CD ESSAI-0002'.
        It must NOT let ordinary prose after an ID-like prefix be parsed."""
        from app.qa.evidence_comparator import REQ_ID_RE
        assert REQ_ID_RE.search("REF-ASU the value shall be 5") is None
        assert REQ_ID_RE.search("REF-ASU and then 12 items") is None

    def test_does_not_bridge_across_ordinary_lowercase_prose(self):
        """The bridge only fires for a single ALL-CAPS segment (case-
        sensitive regardless of the overall IGNORECASE flag) — it must
        not drift across ordinary lowercase words looking for a stray
        number, which would silently invent fake IDs out of prose."""
        from app.qa.evidence_comparator import REQ_ID_RE
        text = "REF-ASU this is just ordinary lowercase prose with a number -5 far away"
        assert REQ_ID_RE.search(text) is None

    def test_bridge_requires_at_least_two_uppercase_letters(self):
        from app.qa.evidence_comparator import REQ_ID_RE
        # A lone uppercase letter should not count as a bridgeable segment.
        text = "REF-ASU A -002 test"
        m = REQ_ID_RE.search(text)
        # Either no match, or a match that does NOT swallow the lone "A" bridge.
        if m:
            assert " A " not in m.group(0)


# ── 9. Report: old "Detail and Resolution" section removed, traceability section added ──

class TestReportStructureChanges:

    def test_per_problem_detail_section_removed_from_docx(self):
        import io
        import zipfile
        from app.qa.spec_report_docx import generate_spec_validation_document
        text = "The system should log errors sometimes.\n"  # forces errors/warnings
        report = validate_with_evidence("bad.txt", text)
        docx = generate_spec_validation_document(report)
        xml = zipfile.ZipFile(io.BytesIO(docx)).read("word/document.xml").decode("utf-8")
        assert "Detail and resolution of each issue" not in xml

    def test_traceability_section_appears_when_untraced_items_exist(self):
        import io
        import zipfile
        from app.qa.spec_report_docx import generate_spec_validation_document
        text = (
            "REQUIREMENTS\n"
            "REF-A-001 | The system shall start within 2 seconds.\n"
            "REF-A-002 | The system shall stop within 1 second.\n"
        )
        report = validate_with_evidence("trace.txt", text)
        docx = generate_spec_validation_document(report)
        xml = zipfile.ZipFile(io.BytesIO(docx)).read("word/document.xml").decode("utf-8")
        assert "Requirements Traceability" in xml
        assert "REF-A-001" in xml or "REF-A-002" in xml

    def test_traceability_section_absent_when_fully_traced(self):
        import io
        import zipfile
        from app.qa.spec_report_docx import generate_spec_validation_document
        text = (
            "REQUIREMENTS\n"
            "REF-A-001 | The system shall start within 2 seconds. | N/A\n"
            "REF-A-002 | The system shall stop within 1 second. | N/A\n"
        )
        report = validate_with_evidence("trace_clean.txt", text)
        docx = generate_spec_validation_document(report)
        xml = zipfile.ZipFile(io.BytesIO(docx)).read("word/document.xml").decode("utf-8")
        assert "Requirements Traceability" not in xml

    def test_traceability_section_appears_even_when_overall_ratio_passes(self):
        """
        A document whose overall traceability ratio is good enough to
        "pass" must still list its individual remaining gaps in section 4
        — a good aggregate score must never hide specific untraced
        requirements from the reader.
        """
        import io
        import zipfile
        from app.qa.spec_report_docx import generate_spec_validation_document
        filler = "\n".join(f"Unrelated filler content line number {i} here." for i in range(12))
        text = (
            "REQUIREMENTS\n"
            "REF-A-001 | The system shall start within 2 seconds. | N/A\n"
            + filler + "\n"
            "REF-A-002 | The system shall stop within 1 second.\n"
        )
        report = validate_with_evidence("trace_partial_pass.txt", text)
        docx = generate_spec_validation_document(report)
        xml = zipfile.ZipFile(io.BytesIO(docx)).read("word/document.xml").decode("utf-8")
        assert "Requirements Traceability" in xml
        assert "REF-A-002" in xml


# ── 10. Report: "Issues to Fix" split into categorized tables ──

class TestReportProblemCategorization:

    def test_real_asu_spec_report_has_categorized_problem_sections(self):
        import io
        import zipfile
        from app.qa.spec_report_docx import generate_spec_validation_document
        from app.qa.retrieval import extract_text_from_file
        if not ASU_PATH.exists():
            pytest.skip("Real ASU spec fixture not available")
        text = extract_text_from_file(str(ASU_PATH))
        report = validate_with_evidence(ASU_PATH.name, text)
        docx = generate_spec_validation_document(report)
        xml = zipfile.ZipFile(io.BytesIO(docx)).read("word/document.xml").decode("utf-8")
        # The real ASU spec has A_SECTION_COVERAGE and J_STANDARDS_CONSISTENCY
        # problems, so both category headings must be present with their intro text.
        assert "Document Structure" in xml
        assert "Standards Cited" in xml
        assert "every standard used must be declared" in xml

    def test_traceability_findings_excluded_from_categorized_problem_tables(self):
        import io
        import zipfile
        from app.qa.spec_report_docx import generate_spec_validation_document
        text = (
            "REQUIREMENTS\n"
            "REF-A-001 | The system shall start within 2 seconds.\n"
            "REF-A-002 | The system shall stop within 1 second.\n"
        )
        report = validate_with_evidence("trace.txt", text)
        findings = report.get("findings", [])
        trace_problems = [
            f for f in findings
            if f.get("check") == "G_TRACEABILITY" and f.get("severity") in ("error", "warning")
        ]
        assert trace_problems, "fixture must actually produce a traceability problem finding"
        docx = generate_spec_validation_document(report)
        xml = zipfile.ZipFile(io.BytesIO(docx)).read("word/document.xml").decode("utf-8")
        # The traceability finding's own message must not be duplicated inside
        # section 2's categorized tables — it only belongs in section 4.
        for f in trace_problems:
            msg = (f.get("message") or "")[:40]
            if msg:
                assert xml.count(msg) <= 1, (
                    "traceability finding text appears more than once — "
                    "it must only be shown in the dedicated Traceability section"
                )

    def test_uncategorized_findings_get_a_fallback_section(self):
        from app.qa.spec_report_docx import _PROBLEM_CATEGORIES
        known_checks = {c for _, checks, _ in _PROBLEM_CATEGORIES for c in checks}
        # G_TRACEABILITY is deliberately excluded (own dedicated section);
        # every other real check name must be covered by a category.
        all_real_checks = {
            "A_SECTION_COVERAGE", "B_SECTION_ORDER", "C_PLACEHOLDER_RESIDUE",
            "D_REQUIREMENT_FORMAT", "E_REQUIREMENT_LANGUAGE", "F_REQUIREMENT_IDS",
            "H_WRITING_GUIDE_RULES", "I_EXTENDED_WG_RULES", "J_STANDARDS_CONSISTENCY",
        }
        assert all_real_checks <= known_checks


class TestPlainLanguageExplanations:

    def test_standards_never_cited_explanation_is_plain_language(self):
        from app.qa.spec_report_docx import _plain_language_explanation
        f = {
            "check": "J_STANDARDS_CONSISTENCY",
            "message": "Standard/norm 'N9' is declared in Applicable Documents but never cited by any requirement.",
        }
        text = _plain_language_explanation(f)
        assert "N9" in text
        assert "never cited" not in text  # sanity: a paraphrase, not a verbatim echo of the raw message
        assert "declared" in text

    def test_standards_not_declared_explanation_is_plain_language(self):
        from app.qa.spec_report_docx import _plain_language_explanation
        f = {
            "check": "J_STANDARDS_CONSISTENCY",
            "message": "Standard/norm 'N9' is cited by a requirement but NOT declared in Applicable Documents.",
        }
        text = _plain_language_explanation(f)
        assert "N9" in text
        assert "applicable documents" in text

    def test_r15_design_file_explanation_is_understandable(self):
        from app.qa.spec_report_docx import _plain_language_explanation
        f = {"check": "I_EXTENDED_WG_RULES", "rule_id": "R15", "message": "raw technical message"}
        text = _plain_language_explanation(f)
        assert "Design file" in text
        assert "design" in text

    def test_placeholder_residue_explanation_reports_the_count(self):
        from app.qa.spec_report_docx import _plain_language_explanation
        f = {
            "check": "C_PLACEHOLDER_RESIDUE",
            "message": "83 template placeholders (<<...>>) remain unfilled.",
        }
        text = _plain_language_explanation(f)
        assert "83" in text

    def test_unknown_check_falls_back_to_raw_message(self):
        from app.qa.spec_report_docx import _plain_language_explanation
        f = {"check": "SOME_FUTURE_CHECK", "message": "raw fallback message"}
        assert _plain_language_explanation(f) == "raw fallback message"


class TestExcerptsAlwaysMatchARealHighlightableUnit:
    """
    Every excerpt collected for the annotated-spec highlighter must be
    findable as a real, single, contiguous unit in the original document —
    otherwise it silently highlights nothing, with no error or warning
    anywhere to reveal the gap. A full sweep of the real ASU spec found
    three distinct causes, each fixed here and locked in by a test:
      1. R17 standards excerpts used a ±100-char radius window
         (_find_excerpt) that pulled in the NEXT declaration-table row —
         14 of 16 standards findings silently failed to highlight.
      2. C_PLACEHOLDER_RESIDUE joined 3 unrelated placeholder samples
         with "; " into one excerpt spanning 3 separate locations.
      3. G_TRACEABILITY's own top-level excerpt (a "here's a correctly
         traced example" illustration) used a synthesized "ID → ref"
         separator that appears nowhere in the real document.
    """

    def test_find_line_excerpt_returns_one_line_not_a_radius_window(self):
        from app.qa.evidence_comparator import _find_line_excerpt
        text = (
            "[N5] | B21 7050 | CONNECTORS GENERAL REQUIREMENTS\n"
            "[N7] | B12 5220 | METALLIC COATING OF ELECTRICAL CONTACTS\n"
            "[N8] | B25 1110 | NTS CONVENTIONAL ELECTRICAL CONDUCTORS\n"
        )
        excerpt = _find_line_excerpt(text, r"\[N7\]")
        assert excerpt == "[N7] | B12 5220 | METALLIC COATING OF ELECTRICAL CONTACTS"
        # Must NOT bleed into the neighbouring declaration rows.
        assert "N5" not in excerpt
        assert "N8" not in excerpt

    def test_standards_excerpt_is_a_single_declaration_row(self, rules):
        """
        Regression: with short declaration rows, _find_excerpt's 100-char
        radius routinely spans 2-3 rows. The real row alone must be enough
        to identify '[N7]' unambiguously in this fixture.
        """
        from app.qa.evidence_comparator import check_standards_consistency
        text = (
            "APPLICABLE DOCUMENTS\n"
            "[N5] | B21 7050 | CONNECTORS GENERAL REQUIREMENTS\n"
            "[N7] | B12 5220 | METALLIC COATING OF ELECTRICAL CONTACTS\n"
            "[N8] | B25 1110 | NTS CONVENTIONAL ELECTRICAL CONDUCTORS\n"
            "REQUIREMENTS\n"
            "REF-A-001 | The system shall comply with [N5].\n"
        )
        findings = check_standards_consistency(text, rules)
        n7 = next(f for f in findings if "'N7'" in f.message)
        assert "N5" not in n7.user_excerpt
        assert "N8" not in n7.user_excerpt
        assert "N7" in n7.user_excerpt

    def test_placeholder_excerpt_is_one_occurrence_not_several_joined(self, rules):
        from app.qa.evidence_comparator import check_placeholder_residue
        text = "REQUIREMENTS\nThe value is <<TBD_1>> and also <<TBD_2>> and <<TBD_3>>.\n"
        findings = check_placeholder_residue(text, rules)
        f = findings[0]
        assert ";" not in f.user_excerpt
        assert f.user_excerpt in text  # a genuine, contiguous substring

    def test_traceability_finding_excerpt_excluded_from_highlight_targets(self):
        """
        collect_highlight_targets must skip G_TRACEABILITY's own top-level
        excerpt even when its severity is "warning" — real problems are
        always in `items`, and this excerpt is illustrative context that
        (a) usually matches nothing and (b) if it ever did match, would
        wrongly highlight a CORRECTLY traced requirement as a problem.
        """
        from app.qa.spec_annotator import collect_highlight_targets
        report = {
            "findings": [{
                "check": "G_TRACEABILITY",
                "severity": "warning",
                "user_excerpt": "REF-PSP-AIRBAG-FRONT-001 → Nothing in this field",
                "items": [{"excerpt": "REF-A-002 The system shall stop."}],
            }]
        }
        targets = collect_highlight_targets(report)
        assert len(targets) == 1
        assert "REF-A-002" in targets[0]
        assert not any("→" in t for t in targets)

    def test_real_asu_spec_every_highlight_target_matches_a_real_unit(self):
        """
        The definitive, whole-pipeline guard: run the actual production
        collect_highlight_targets against the real ASU spec and confirm
        every single target it produces corresponds to a real, findable
        unit in the original document. Before these three fixes, 16 of
        ~174 targets matched nothing.
        """
        if not ASU_PATH.exists():
            pytest.skip("ASU spec not found")
        from app.qa.retrieval import extract_text_from_file
        from app.qa.spec_annotator import (
            collect_highlight_targets, _iter_docx_units, _normalize_ws,
        )
        from docx import Document

        text = extract_text_from_file(ASU_PATH)
        report = validate_with_evidence(ASU_PATH.name, text, source_path=ASU_PATH)
        targets = collect_highlight_targets(report)
        assert len(targets) > 100  # sanity: this is a real, substantial sweep

        doc = Document(str(ASU_PATH))
        unit_texts = [_normalize_ws(t) for _, t in _iter_docx_units(doc)]
        unmatched = [t for t in targets if not any(_normalize_ws(t) in ut for ut in unit_texts)]
        assert unmatched == []


class TestNewDeterministicChecks:
    """
    Three additional deterministic checks added after a deep-dive into
    the 18 writing-guide rules the analysis previously left fully manual
    (P03, P05, P07, R16, R18, R26, R28, R29, R32, R34, R35, R38, R39,
    R42, R43, R44, R47, R48). Most genuinely require understanding
    MEANING (comparing two sections' wording for consistency, judging
    whether an abstraction level is "too detailed") or external
    knowledge (another document's scope, whether a real collaboration
    happened) — not automatable without an LLM. Three were concrete
    enough to implement as plain presence/structural checks:
      - R29 (reset requirement presence) — same shape as the already-
        deterministic R37/R41 checks.
      - R43 (dreaded event -> associated requirement) — the writing
        guide's own text states the rule precisely enough to check
        structurally.
      - A standards-reference-completeness check (not a single numbered
        rule, but a direct, well-defined extension of R17) — triggered
        by the user finding that [STA2]'s declared reference is an
        unfilled template placeholder ("<<96 xxx xxx 99 xx>>"), which
        was already caught by the generic placeholder count but never
        surfaced as its own clear, standards-specific finding.
    """

    def test_r29_reset_requirement_detected_when_present(self, rules):
        from app.qa.evidence_comparator import check_extended_writing_guide_rules
        text = "FUNCTIONAL REQUIREMENTS\nREF-A-001 | The ECU shall reset within 100 ms of power-on. | [U1]\n"
        findings = check_extended_writing_guide_rules(text, rules)
        r29 = [f for f in findings if f.rule_id == "R29"]
        assert len(r29) == 1
        assert r29[0].severity == "pass"

    def test_r29_absence_reported_as_info_not_error(self, rules):
        from app.qa.evidence_comparator import check_extended_writing_guide_rules
        text = "FUNCTIONAL REQUIREMENTS\nREF-A-001 | The system shall log every event. | [U1]\n"
        findings = check_extended_writing_guide_rules(text, rules)
        r29 = [f for f in findings if f.rule_id == "R29"]
        assert len(r29) == 1
        assert r29[0].severity == "info"  # a recommendation, not a violation

    def test_r43_flags_dreaded_event_with_no_associated_requirement(self, tmp_path):
        from docx import Document
        from app.qa.evidence_comparator import check_dreaded_event_associations
        doc = Document()
        doc.add_heading("DEMONSTRATION OF COMPLIANCE WITH REQUIREMENTS", level=1)
        table = doc.add_table(rows=3, cols=3)
        table.cell(0, 0).text = "Dreaded event of the supply"
        table.cell(0, 1).text = "Associated requirements"
        table.cell(0, 2).text = "Gravity"
        table.cell(1, 0).text = "GEN-ORG-ASU-ST.0001(0)"
        table.cell(1, 1).text = "ETI.0: the hot spot shall not spread"
        table.cell(2, 0).text = "GEN-ORG-ASU-ST.0002(0)"
        table.cell(2, 1).text = ""  # no associated requirement — the violation
        path = tmp_path / "spec.docx"
        doc.save(str(path))

        findings = check_dreaded_event_associations(path)
        warnings = [f for f in findings if f.severity == "warning"]
        assert len(warnings) == 1
        assert "GEN-ORG-ASU-ST.0002" in warnings[0].message

    def test_r43_passes_when_all_events_have_an_association(self, tmp_path):
        from docx import Document
        from app.qa.evidence_comparator import check_dreaded_event_associations
        doc = Document()
        table = doc.add_table(rows=2, cols=3)
        table.cell(0, 0).text = "Dreaded event of the supply"
        table.cell(0, 1).text = "Associated requirements"
        table.cell(0, 2).text = "Gravity"
        table.cell(1, 0).text = "GEN-ORG-ASU-ST.0001(0)"
        table.cell(1, 1).text = "ETI.0: the hot spot shall not spread"
        path = tmp_path / "spec.docx"
        doc.save(str(path))

        findings = check_dreaded_event_associations(path)
        assert any(f.severity == "pass" for f in findings)
        assert not any(f.severity == "warning" for f in findings)

    def test_r43_ignores_tables_without_an_associated_requirements_column(self, tmp_path):
        """A plain 'Reference | Definition' dreaded-events list (no
        Associated-requirements column at all) must never be flagged —
        the column simply doesn't exist for this table shape."""
        from docx import Document
        from app.qa.evidence_comparator import check_dreaded_event_associations
        doc = Document()
        table = doc.add_table(rows=2, cols=2)
        table.cell(0, 0).text = "Dreaded events Reference"
        table.cell(0, 1).text = "Definition"
        table.cell(1, 0).text = "ER ERF.4.01"
        table.cell(1, 1).text = ""
        path = tmp_path / "spec.docx"
        doc.save(str(path))

        findings = check_dreaded_event_associations(path)
        assert not any(f.severity == "warning" for f in findings)
        assert any(f.severity == "info" for f in findings)  # "not applicable"

    def test_standard_with_placeholder_reference_flagged(self, rules):
        """
        Regression: [STA2]'s declared reference is a literal, unfilled
        template placeholder ("<<96 xxx xxx 99 xx>>") — the user found
        this and asked why it wasn't clearly flagged as a standards
        problem. It WAS already counted in the generic placeholder total,
        but never surfaced as its own clear, standards-specific finding.
        """
        from app.qa.evidence_comparator import check_standards_reference_completeness
        text = (
            "APPLICABLE DOCUMENTS\n"
            "[STA2] | <<96 xxx xxx 99 xx>> | | ASU Functional drawing\n"
        )
        findings = check_standards_reference_completeness(text, rules)
        assert len(findings) == 1
        assert "STA2" in findings[0].message
        assert "placeholder" in findings[0].message

    def test_standard_with_empty_reference_flagged(self, rules):
        from app.qa.evidence_comparator import check_standards_reference_completeness
        text = (
            "APPLICABLE DOCUMENTS\n"
            "[N5] |  | | Connectors general requirements\n"
        )
        findings = check_standards_reference_completeness(text, rules)
        assert len(findings) == 1
        assert "N5" in findings[0].message
        assert "empty" in findings[0].message

    def test_standard_with_real_reference_not_flagged(self, rules):
        from app.qa.evidence_comparator import check_standards_reference_completeness
        text = (
            "APPLICABLE DOCUMENTS\n"
            "[N5] | B21 7050 | A | Connectors general requirements\n"
        )
        findings = check_standards_reference_completeness(text, rules)
        assert findings == []

    def test_real_asu_spec_finds_exactly_the_known_reference_gaps(self):
        """
        Ground truth, verified by reading the raw declaration table cells
        directly: [STA2]'s reference is entirely a placeholder, and
        [STA7] appears three times with a real document number but an
        unresolved trailing revision-index placeholder ("<<(1)>>") each
        time — 4 rows total.
        """
        if not ASU_PATH.exists():
            pytest.skip("ASU spec not found")
        from app.qa.retrieval import extract_text_from_file
        from app.qa.evidence_comparator import check_standards_reference_completeness
        text = extract_text_from_file(ASU_PATH)
        findings = check_standards_reference_completeness(text, extract_all_rules())
        assert len(findings) == 4
        marks = [f.message.split("'")[1] for f in findings]
        assert marks.count("STA2") == 1
        assert marks.count("STA7") == 3

    def test_real_asu_spec_r43_passes(self):
        """Table 83 (the real Dreaded-events-with-associations table) has
        every row filled in — verified directly against the raw cells."""
        if not ASU_PATH.exists():
            pytest.skip("ASU spec not found")
        from app.qa.evidence_comparator import check_dreaded_event_associations
        findings = check_dreaded_event_associations(ASU_PATH)
        assert any(f.severity == "pass" and f.rule_id == "R43" for f in findings)
        assert not any(f.severity == "warning" for f in findings)


class TestSemanticAnalysis:
    """
    check_semantic_writing_guide_rules (P03, P05, R26, R28, R34, R35, R38,
    R39, R42, R44) is the only LLM-assisted check in this file — every
    other check in the codebase is a deterministic pattern/structure
    match. It must NEVER be silently reachable from the default,
    deterministic path (validate_with_evidence's include_semantic_analysis
    defaults to False), must always degrade to a single informational
    finding rather than raise if the LLM is unavailable or returns
    garbage, and every real finding it DOES produce must say plainly that
    it is an AI judgment requiring human verification.

    The LLM itself is always mocked here — this project has no reliable
    way to reach Azure OpenAI from this test environment (the corporate
    NTLM proxy blocks outbound Python requests), and a real call would
    also be slow, costly, and non-deterministic for a test suite.
    """

    def test_disabled_by_default_makes_no_llm_call(self):
        from unittest.mock import patch
        text = "SCOPE\nThe component provides an alarm function.\n"
        with patch("app.embeddings.call_llm") as mock_llm:
            report = validate_with_evidence("spec.txt", text)
        mock_llm.assert_not_called()
        assert not any(f["check"] == "K_SEMANTIC_ANALYSIS" for f in report["findings"])

    def test_successful_llm_response_produces_mapped_findings(self, rules):
        from unittest.mock import patch
        from app.qa.evidence_comparator import check_semantic_writing_guide_rules
        import json as _json

        mock_response = _json.dumps([
            {"rule_id": "P03", "verdict": "compliant", "explanation": "Service described autonomously.", "excerpt": "The component provides an alarm function."},
            {"rule_id": "P05", "verdict": "violation", "explanation": "Network protocol detail is mixed into the application level.", "excerpt": "CAN frame 0x123 bit 4"},
            {"rule_id": "R26", "verdict": "not_applicable", "explanation": "No I/O table found.", "excerpt": ""},
            {"rule_id": "R28", "verdict": "cannot_verify", "explanation": "Insufficient content.", "excerpt": ""},
            {"rule_id": "R34", "verdict": "compliant", "explanation": "ok", "excerpt": ""},
            {"rule_id": "R35", "verdict": "compliant", "explanation": "ok", "excerpt": ""},
            {"rule_id": "R38", "verdict": "compliant", "explanation": "ok", "excerpt": ""},
            {"rule_id": "R39", "verdict": "not_applicable", "explanation": "ok", "excerpt": ""},
            {"rule_id": "R42", "verdict": "compliant", "explanation": "ok", "excerpt": ""},
            {"rule_id": "R44", "verdict": "compliant", "explanation": "ok", "excerpt": ""},
        ])
        with patch("app.embeddings.call_llm", return_value=mock_response) as mock_llm:
            findings = check_semantic_writing_guide_rules("SCOPE\nThe component provides an alarm function.\n", rules)
        assert mock_llm.called
        assert len(findings) == 10
        by_rule = {f.rule_id: f for f in findings}
        assert by_rule["P03"].severity == "pass"
        assert by_rule["P05"].severity == "warning"
        assert by_rule["R26"].severity == "info"
        assert by_rule["R28"].severity == "info"
        # Every finding must self-identify as an AI judgment needing review.
        assert all("verified by a human reviewer" in f.why for f in findings)
        assert all(f.check == "K_SEMANTIC_ANALYSIS" for f in findings)

    def test_llm_exception_degrades_to_single_info_finding(self, rules):
        from unittest.mock import patch
        from app.qa.evidence_comparator import check_semantic_writing_guide_rules
        with patch("app.embeddings.call_llm", side_effect=RuntimeError("network unreachable")):
            findings = check_semantic_writing_guide_rules("SCOPE\nSome text.\n", rules)
        assert len(findings) == 1
        assert findings[0].severity == "info"
        assert findings[0].check == "K_SEMANTIC_ANALYSIS"

    def test_malformed_json_response_degrades_gracefully(self, rules):
        from unittest.mock import patch
        from app.qa.evidence_comparator import check_semantic_writing_guide_rules
        with patch("app.embeddings.call_llm", return_value="this is not json at all"):
            findings = check_semantic_writing_guide_rules("SCOPE\nSome text.\n", rules)
        assert len(findings) == 1
        assert findings[0].severity == "info"

    def test_non_list_json_response_degrades_gracefully(self, rules):
        from unittest.mock import patch
        from app.qa.evidence_comparator import check_semantic_writing_guide_rules
        with patch("app.embeddings.call_llm", return_value='{"not": "a list"}'):
            findings = check_semantic_writing_guide_rules("SCOPE\nSome text.\n", rules)
        assert len(findings) == 1
        assert findings[0].severity == "info"

    def test_missing_rule_in_response_still_produces_a_placeholder_finding(self, rules):
        """If the LLM only answers 9 of the 10 rules, the 10th must still
        appear as an explicit "not yet reviewed" finding — never silently
        vanish."""
        from unittest.mock import patch
        from app.qa.evidence_comparator import check_semantic_writing_guide_rules
        import json as _json
        partial = _json.dumps([{"rule_id": "P03", "verdict": "compliant", "explanation": "ok", "excerpt": ""}])
        with patch("app.embeddings.call_llm", return_value=partial):
            findings = check_semantic_writing_guide_rules("SCOPE\nSome text.\n", rules)
        assert len(findings) == 10
        p05 = next(f for f in findings if f.rule_id == "P05")
        assert p05.severity == "info"
        assert "did not return a verdict" in p05.message

    def test_wired_into_validate_with_evidence_when_opted_in(self, rules):
        from unittest.mock import patch
        import json as _json
        mock_response = _json.dumps([
            {"rule_id": rid, "verdict": "compliant", "explanation": "ok", "excerpt": ""}
            for rid in ("P03", "P05", "R26", "R28", "R34", "R35", "R38", "R39", "R42", "R44")
        ])
        with patch("app.embeddings.call_llm", return_value=mock_response) as mock_llm:
            report = validate_with_evidence(
                "spec.txt", "SCOPE\nSome text.\n", include_semantic_analysis=True
            )
        assert mock_llm.called
        semantic = [f for f in report["findings"] if f["check"] == "K_SEMANTIC_ANALYSIS"]
        assert len(semantic) == 10
        # These must never influence the deterministic writing-guide score.
        assert 0.0 <= report["scores"]["writing_guide_compliance"] <= 1.0

    def test_semantic_findings_excluded_from_deterministic_problem_tables(self):
        """The Word report's categorized "Issues to Fix" tables
        (section 2) must never include K_SEMANTIC_ANALYSIS rows — they
        get their own separate, clearly-labeled section instead."""
        import io
        import zipfile
        from unittest.mock import patch
        import json as _json
        from app.qa.spec_report_docx import generate_spec_validation_document

        mock_response = _json.dumps([
            {"rule_id": "P05", "verdict": "violation", "explanation": "Abstraction-level inconsistency detected.", "excerpt": "some excerpt"},
        ] + [
            {"rule_id": rid, "verdict": "compliant", "explanation": "ok", "excerpt": ""}
            for rid in ("P03", "R26", "R28", "R34", "R35", "R38", "R39", "R42", "R44")
        ])
        with patch("app.embeddings.call_llm", return_value=mock_response):
            report = validate_with_evidence(
                "spec.txt", "SCOPE\nThe system shall log errors.\n", include_semantic_analysis=True
            )
        docx_bytes = generate_spec_validation_document(report)
        xml = zipfile.ZipFile(io.BytesIO(docx_bytes)).read("word/document.xml").decode("utf-8")
        assert "Semantic Analysis" in xml
        before_section, _, after_section = xml.partition("Semantic Analysis")
        # The AI-sourced P05 explanation must appear ONLY in its own
        # dedicated section — never duplicated into section 2's
        # deterministic, categorized "Issues to Fix" tables.
        assert "Abstraction-level inconsistency" in after_section
        assert "Abstraction-level inconsistency" not in before_section
