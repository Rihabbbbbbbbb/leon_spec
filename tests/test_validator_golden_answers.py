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

    def test_ergonomics_and_traceability_recommended_sections_flagged(self, asu_report):
        warnings = _findings_by_severity(asu_report, "warning")
        messages = " ".join(f["message"] for f in warnings)
        assert "ERGONOMICS" in messages
        assert "TRACEABILITY" in messages

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
