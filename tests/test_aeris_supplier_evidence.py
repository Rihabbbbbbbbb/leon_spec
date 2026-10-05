"""Supplier results must remain distinct from copied specifications and bounds."""
import pytest
from dataclasses import replace

from app.qa.aeris_constraints import compare_constraint, extract_constraints, extract_measurements
from app.qa.aeris_evidence import EvidenceChunk, _table_row_blocks
from app.qa.aeris_crosscheck import _check_one, _EvidenceIndex
from app.qa.conformity_analyzer import ConformityItem


@pytest.mark.parametrize("target,result,status", [
    (">=400:1", ">380:1", "INCOMPARABLE"),
    (">=400:1", ">500:1", "CONFORME"),
    (">=400:1", "<400:1", "NON_CONFORME"),
    (">=400:1", "<=400:1", "INCOMPARABLE"),
    (">400:1", ">400:1", "CONFORME"),
    (">400:1", ">=400:1", "INCOMPARABLE"),
    ("<=400:1", ">400:1", "NON_CONFORME"),
    ("<=400:1", ">=400:1", "INCOMPARABLE"),
    ("<400:1", "<400:1", "CONFORME"),
    ("<400:1", "<=400:1", "INCOMPARABLE"),
    ("=400:1", ">400:1", "NON_CONFORME"),
    ("=400:1", ">=400:1", "INCOMPARABLE"),
])
def test_numeric_bounds_are_not_exact_measurements(target, result, status):
    constraints = extract_constraints("Contrast ratio " + target)
    if target.startswith("="):
        constraints = [replace(constraints[0], operator="eq")]
    values = extract_measurements("Measured contrast ratio " + result)
    verdict = compare_constraint(constraints[0], values)[0]
    assert verdict.status == status
    assert verdict.gap is None


def test_supplier_typ_without_space_does_not_become_decimal_suffix():
    values = extract_measurements("Typ119.9mA, Max192.7mA @13.5V")
    assert [m.value for m in values if m.unit_family == "current"] == [119.9, 192.7]


@pytest.mark.parametrize("text", [
    "The current consumption shall be <=100mA",
    "The minimum contrast ratio of a TFT display is 400:1",
    "The luminance must be >=500 cd/m2",
    "NOK versus the 95% @32° target",
])
def test_plain_text_copied_requirements_are_not_observations(text):
    assert extract_measurements(text) == []


def test_unicode_contrast_conditions_are_not_contrast_measurements():
    values = extract_measurements("Contrast ratio (w/o LCF)\n＞500@25℃\n>410@70℃\n>380@85℃")
    assert [(m.value, m.bound, m.condition) for m in values] == [
        (500, "gt", "@25°C"), (410, "gt", "@70°C"), (380, "gt", "@85°C"),
    ]


def test_attenuation_angle_is_a_condition_not_an_extra_target():
    constraints = extract_constraints(
        "The shutter attenuates at least 95% under an angle of 32°."
    )
    assert len(constraints) == 1
    values = extract_measurements("lum attenuates＞85%\n@ V32°\nlum attenuates>95%\n@ V44°")
    verdicts = compare_constraint(constraints[0], values)
    assert len(verdicts) == 1
    assert verdicts[0].status == "INCOMPARABLE"
    assert verdicts[0].gap is None


def test_multicolumn_supplier_row_excludes_specification_measurements():
    rows = [
        ["Req ID", "Description", "Supplier proposal", "Feedback"],
        ["REQ-1234", "Reduced consumption current <=100mA", "Typ119.9mA Max192.7mA", "NOK"],
    ]
    text, measured = _table_row_blocks(rows)[0]
    chunk = EvidenceChunk("vendor.pdf", "Page 3", text, 1, "table_row", measured)
    req = ConformityItem(
        row_index=1, req_id="REQ-1234", description="Reduced consumption current <=100mA",
        conformity_category="NOK", comment="Typ119.9mA Max192.7mA",
    )
    item, _, _ = _check_one(req, [chunk])
    assert item.evidence_status == "NON_CONFORME"
    assert "100 mA" not in item.supplier_result
    assert "192.7" in item.supplier_result


def test_outer_table_with_multiple_requirements_is_not_one_evidence_row():
    assert _table_row_blocks([["REQ-1111 current 100mA REQ-2222 current 200mA"]]) == []


def test_copied_specification_without_supplier_result_is_not_measurement():
    _, measured = _table_row_blocks([
        ["Req ID", "Description", "Feedback"],
        ["REQ-1234", "Current <=100mA", "OK"],
    ])[0]
    assert measured == ""


def test_overlapping_bounds_do_not_create_statement_conflicts():
    from app.qa.aeris_statements import tdr_self_conflicts, values_relation
    first = extract_measurements("Contrast >380:1")
    second = extract_measurements("Contrast >500:1")
    assert values_relation(first, second) == "INCOMPARABLE"
    assert tdr_self_conflicts(first + second) == ""
    assert values_relation(first, extract_measurements("Contrast <300:1")) == "DISAGREE"


def test_supplier_comment_cannot_become_its_own_acceptance_target():
    req = ConformityItem(
        row_index=1, req_id="REQ-1234", description="Provide a current consumption report",
        conformity_category="OK", comment="Measured current 192.7mA",
    )
    chunk = EvidenceChunk("vendor.txt", "Page 1", "REQ-1234 measured current 192.7mA", 1)
    item, _, _ = _check_one(req, [chunk])
    assert item.evidence_status == "PREUVE_INSUFFISANTE"
    assert item.target == ""


def test_distinct_signed_temperatures_and_axes_are_not_the_same_condition():
    from app.qa.aeris_constraints import conditions_compatible
    assert not conditions_compatible("@-30°C", "@30°C")
    assert not conditions_compatible("@V=32°", "@H32°")
    assert conditions_compatible("under an angle of 32°", "@V32°")


def test_measurements_are_parsed_once_per_report_index(monkeypatch):
    from app.qa import aeris_crosscheck
    original = aeris_crosscheck.extract_measurements
    calls = []

    def counted(text, **kwargs):
        calls.append(text)
        return original(text, **kwargs)

    monkeypatch.setattr(aeris_crosscheck, "extract_measurements", counted)
    chunks = [EvidenceChunk("vendor.txt", "Page 1", "Measured current 90mA", 1)]
    index = _EvidenceIndex(chunks)
    for number in range(5):
        req = ConformityItem(
            row_index=number, req_id=f"REQ-{number}", description="Current <=100mA",
            conformity_category="OK",
        )
        _check_one(req, chunks, index)
    assert len(calls) == 1
