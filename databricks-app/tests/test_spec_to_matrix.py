"""
Tests for the Spec → Conformity Matrix generator (app/qa/spec_to_matrix.py).

Covers:
  1. Template integrity — single 'new version' sheet, no macros, formulas
     and dropdowns preserved
  2. Requirement extraction — block anchors, DOORS/internal ID coalescing,
     inline table rows, shall/must statements, history mentions, dedup
  3. Matrix generation — cells written at the right place, template intact
  4. Full pipeline on the real ASU spec (skipped if the file is absent)
"""
import io
import re
import sys
import zipfile
from pathlib import Path

import pytest

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

from app.qa.spec_to_matrix import (
    TEMPLATE_PATH,
    DATA_START_ROW,
    COL_DESCRIPTION,
    COL_REQ_ID,
    Requirement,
    extract_requirements,
    generate_conformity_matrix,
    spec_to_matrix,
)


ASU_PATH = Path(__file__).resolve().parent.parent / "data" / "uploads" / \
    "00692_25_01250_ASU_Technical_Specification_SPX _1_.docx"


# ── 1. Template integrity ─────────────────────────────────────────

class TestTemplate:

    def test_template_exists(self):
        assert TEMPLATE_PATH.exists(), f"Template missing: {TEMPLATE_PATH}"

    def test_template_has_no_macros(self):
        names = zipfile.ZipFile(TEMPLATE_PATH).namelist()
        assert not any("vba" in n.lower() for n in names)

    def test_template_single_new_version_sheet(self):
        from openpyxl import load_workbook
        wb = load_workbook(TEMPLATE_PATH)
        assert wb.sheetnames == ["new version"]

    def test_template_headers_and_formulas(self):
        from openpyxl import load_workbook
        ws = load_workbook(TEMPLATE_PATH)["new version"]
        assert "Libell" in (ws.cell(row=9, column=COL_DESCRIPTION).value or "")
        assert "exigence" in (ws.cell(row=9, column=COL_REQ_ID).value or "")
        assert (ws["F5"].value or "").startswith("=COUNTIF")

    def test_template_keeps_dropdowns(self):
        from openpyxl import load_workbook
        ws = load_workbook(TEMPLATE_PATH)["new version"]
        assert len(ws.data_validations.dataValidation) > 0


# ── 2. Requirement extraction ─────────────────────────────────────

class TestExtraction:

    def test_inline_table_row(self):
        text = 'REF-ASU-CD-LIN-0001(0) | The FNR must provide a justification folder for the line interface | [LIN1]'
        reqs = extract_requirements(text)
        assert len(reqs) == 1
        assert reqs[0].req_id == "REF-ASU-CD-LIN-0001"
        assert "justification folder" in reqs[0].text

    def test_block_anchor_with_internal_ref(self):
        text = (
            "REQ-0937326  C\n"
            "Requirement Number (v) | Description of the requirement | Input requirement (v)\n"
            "REF-ASU-CD-EXIFUNC-003\n"
            "Att_Sdf@ ASIL_A(A)\n"
            "PSA_Comments@{{ VF087_V2\n"
            "VF_2831}} | The function shall provide feedback within 200 ms. | [SSD_AUE]\n"
        )
        reqs = extract_requirements(text)
        by_id = {r.req_id: r for r in reqs}
        assert "REQ-0937326" in by_id
        req = by_id["REQ-0937326"]
        assert "REF-ASU-CD-EXIFUNC-003" in req.text
        assert "200 ms" in req.text

    def test_multiline_description_between_pipes(self):
        text = (
            "REQ-0937358  B\n"
            "Requirement Number (v) | Description of the requirement | Input requirement (v)\n"
            "REF-ASU-CD-EXIFUNC-004\n"
            "PSA_Comments@{{ X }} | During Idle State,\n"
            " IF\n"
            "Command is equal to Activation THEN the function shall switch state | [SSD]\n"
        )
        reqs = extract_requirements(text)
        req = {r.req_id: r for r in reqs}["REQ-0937358"]
        assert "During Idle State" in req.text
        assert "shall switch state" in req.text

    def test_history_mention_gets_no_description_but_real_definition_wins(self):
        text = (
            "New requirements:\n"
            "REQ-1111111\n"
            "Some unrelated line.\n"
            "REQ-1111111  C\n"
            "Requirement Number (v) | Description of the requirement | Input requirement (v)\n"
            "REF-X-CD-T-001\n"
            "meta@{{ x }} | The system shall do the real thing correctly. | [UP]\n"
        )
        reqs = extract_requirements(text)
        matches = [r for r in reqs if r.req_id == "REQ-1111111"]
        assert len(matches) == 1
        assert "real thing" in matches[0].text

    def test_broken_ref_spacing_repaired(self):
        text = "REF- ASU-CD-MAINT-0017(0) | The unit must survive 5 cycles of assembly. | [M1]"
        reqs = extract_requirements(text)
        assert reqs[0].req_id == "REF-ASU-CD-MAINT-0017"

    def test_shall_without_id_kept(self):
        text = "The ASU must be possible to disassemble and reassemble 20 times minimum without deterioration."
        reqs = extract_requirements(text)
        assert len(reqs) == 1
        assert reqs[0].req_id == ""
        assert "20 times" in reqs[0].text

    def test_template_example_filtered(self):
        text = "REF-PSP-FRONT-AIRBAG-001 | It is mandatory to write a Requirement no like: (free to modify the example) | x"
        reqs = extract_requirements(text)
        assert all("mandatory to write" not in r.text for r in reqs)

    def test_dedup_by_id(self):
        text = (
            "REF-A-CD-X-001 | The device shall blink twice per second. | [U1]\n"
            "REF-A-CD-X-001 | The device shall blink twice per second. | [U1]\n"
        )
        reqs = extract_requirements(text)
        assert len(reqs) == 1

    def test_short_prose_with_must_not_captured(self):
        text = "You must see this."  # < 30 chars, no ID
        reqs = extract_requirements(text)
        assert len(reqs) == 0

    def test_upstream_ref_not_captured_as_requirement(self):
        """'id | desc | upstream' rows: the trailing upstream id must NOT
        become a matrix row of its own."""
        text = "REF-ASU-CD-MAINT-0022(0) | FILL_FAULT_INFO_FRAME shall be activated in all functional states | REQ-0508543 A"
        reqs = extract_requirements(text)
        ids = {r.req_id for r in reqs}
        assert "REF-ASU-CD-MAINT-0022" in ids
        assert "REQ-0508543" not in ids

    def test_desc_then_id_layout_captured(self):
        """'description | id' rows (no leading id): the trailing id IS the
        requirement's own identifier."""
        text = "The failure of a single primary component must not generate the failure mode | GEN-ALM-CDC-SDF_041(0)"
        reqs = extract_requirements(text)
        assert len(reqs) == 1
        assert reqs[0].req_id == "GEN-ALM-CDC-SDF_041"
        assert "single primary component" in reqs[0].text

    def test_multiline_cell_merged_into_one_requirement_bruit_case(self):
        """A description cell continuing over several lines (numbered
        methods) must stay ONE requirement, not split into several rows."""
        text = (
            "Requirement Number (v) | Description of the requirement | Input requirement (v)\n"
            "REF-ASU-CD-BRUIT-0004(1) | Two methods are proposed to validate random noises:\n"
            "Method 1: During the test, emitted noise must be compliant with the Zwicker un-stationary loudness L10<4 sones\n"
            "Method 2: The measurement of random noise should be lower than the absence of random noise chart curve no. 1 + 3dB\n"
            "REF-ASU-CD-BRUIT-0005(0) | The listening for random noise should culminate in a rating. | [N42]\n"
        )
        reqs = extract_requirements(text)
        by_id = {r.req_id: r for r in reqs}
        assert "REF-ASU-CD-BRUIT-0004" in by_id
        assert "Method 1" in by_id["REF-ASU-CD-BRUIT-0004"].text
        assert "Method 2" in by_id["REF-ASU-CD-BRUIT-0004"].text
        # No separate id-less rows for the Method lines
        assert all("Method 1" not in r.text for r in reqs if not r.req_id)
        # Next requirement untouched
        assert "REF-ASU-CD-BRUIT-0005" in by_id

    def test_multiline_cell_merged_maint_case(self):
        """Second paragraph of the same cell (after a blank line, closing
        with '| [M20]') must merge into the requirement, not become its
        own row."""
        text = (
            "APP-ASU-CD-MAINT-0016(0) | The ASU must be possible to disassemble and reassemble 5 times minimum without any deterioration of characteristics defined\n"
            "\n"
            "The ASU must be possible to disassemble and reassemble 20 times minimum without any deterioration of characteristics defined in this document | [M20]\n"
        )
        reqs = extract_requirements(text)
        assert len(reqs) == 1
        req = reqs[0]
        assert req.req_id == "APP-ASU-CD-MAINT-0016"
        assert "5 times" in req.text
        assert "20 times" in req.text

    def test_inline_rows_not_swallowed_by_preceding_block(self):
        """An anchor block must stop at the first self-contained inline
        requirement row instead of swallowing it."""
        text = (
            "REF-A-CD-BLOCK-001\n"
            "some block metadata\n"
            "APP-A-CD-PERF-0001(0) | The unit must allow a sound level between 105 and 118 dB. | [M2]\n"
            "APP-A-CD-PERF-0002(0) | The unit must hold 350 seconds disconnected from supply. | [M2]\n"
        )
        reqs = extract_requirements(text)
        ids = {r.req_id: r for r in reqs}
        assert "APP-A-CD-PERF-0001" in ids
        assert "APP-A-CD-PERF-0002" in ids
        assert "sound level" in ids["APP-A-CD-PERF-0001"].text

    def test_mixed_case_id_segment_extracted(self):
        """
        Regression: the ID character class used to be uppercase-only
        ([A-Z0-9_.-]), so any segment with a lower-case letter — "SdF"
        (Sûreté de Fonctionnement) or "Safety" — truncated the match at
        the first lower-case character, e.g. 'REF-ASU-CD-SdF-0007'
        matched only 'REF-ASU-CD-'. On the real ASU spec this silently
        dropped the ENTIRE SdF (20 reqs) and Safety (7 reqs) families.
        """
        text = "REF-ASU-CD-SdF-0007(0) | Failure mode data: loss of communication shall trigger a fault. | [M1]"
        reqs = extract_requirements(text)
        assert reqs[0].req_id == "REF-ASU-CD-SdF-0007"

        text2 = "REF-ASU-CD-Safety-0001(0) | The device must limit exposure to the safety hazard. | [M2]"
        reqs2 = extract_requirements(text2)
        assert reqs2[0].req_id == "REF-ASU-CD-Safety-0001"

    def test_doors_id_does_not_swallow_a_separate_later_requirement(self):
        """
        Regression: a trailing "REQ-… C" (DOORS export id) that concludes
        an ALREADY-substantial block (the preceding requirement's own real
        "shall" statement) must never absorb the NEXT, unrelated anchor —
        even when they are close together or separated only by a short
        subsection heading + the repeated table-header row. On the real
        ASU spec this exact pattern silently dropped 11+ real
        requirements (the whole point of the coalescing logic is for a
        DOORS id that has NOTHING of its own yet, not one trailing a
        requirement that's already fully described).
        """
        text = (
            "REF-A-CD-FUNC-001\n"
            "meta@{{ x }} | The function shall manage state A. | [U1]\n"
            "REQ-0900001  C\n"
            "Timing Performances\n"
            "Requirement Number (v) | Description of the requirement | Input requirement (v)\n"
            "REF-A-CD-FUNC-002\n"
            "meta@{{ y }} | The function shall provide feedback within 200 ms. | [U2]\n"
        )
        reqs = extract_requirements(text)
        by_id = {r.req_id: r for r in reqs}
        assert "REF-A-CD-FUNC-001" in by_id
        assert "manage state A" in by_id["REF-A-CD-FUNC-001"].text
        assert "REF-A-CD-FUNC-002" in by_id, "the second, unrelated requirement must not be swallowed"
        assert "200 ms" in by_id["REF-A-CD-FUNC-002"].text
        # The trailing DOORS id must not carry a heading as a fake description
        if "REQ-0900001" in by_id:
            assert "Timing Performances" not in by_id["REQ-0900001"].text

    def test_bare_input_requirement_id_never_becomes_its_own_row(self):
        """
        Regression: CTS requirement tables have a 3rd column, "Input
        requirement (v)", whose value is itself often a DOORS-style id
        ("REQ-0937326  C") pointing to an UPSTREAM requirement — it is
        traceability metadata, not a requirement of this spec. The anchor
        scanner also recognises that id as a block anchor (some tables
        place it first), and when its own block never yields a real
        shall/must statement — the normal case, since the real content
        already belongs to the requirement it trails — it must not appear
        in the matrix at all, empty or otherwise.
        """
        text = (
            "REF-A-CD-FUNC-010\n"
            "meta@{{ x }} | The function shall manage state Z. | [U9]\n"
            "REQ-0999999  C\n"
            "Timing Performances\n"
            "Requirement Number (v) | Description of the requirement | Input requirement (v)\n"
            "REF-A-CD-FUNC-011\n"
            "meta@{{ y }} | The function shall provide feedback within 300 ms. | [U9]\n"
        )
        reqs = extract_requirements(text)
        ids = {r.req_id for r in reqs}
        assert "REQ-0999999" not in ids
        assert "REF-A-CD-FUNC-010" in ids
        assert "REF-A-CD-FUNC-011" in ids

    def test_real_asu_spec_matrix_has_no_empty_input_requirement_rows(self):
        """
        On the real ASU spec, the extractor used to emit 31 bare
        "REQ-nnnnnnn" rows with an EMPTY description — pure "Input
        requirement" column artifacts, never real requirements. None of
        these should reach the generated matrix.
        """
        if not ASU_PATH.exists():
            pytest.skip("ASU spec not found")
        from app.qa.retrieval import extract_text_from_file
        text = extract_text_from_file(ASU_PATH)
        reqs = extract_requirements(text)
        bogus = [
            r for r in reqs
            if re.fullmatch(r"REQ-\d{4,10}", r.req_id) and not r.text
        ]
        assert bogus == [], f"empty Input-requirement rows leaked into the matrix: {bogus}"

    def test_real_asu_spec_completeness_against_structural_ground_truth(self):
        """
        Cross-check against evidence_comparator.extract_requirement_rows
        (which reads the "Input requirement" column directly from the
        docx tables — an independent, structural source of truth). Every
        ID it finds must also be found here, with only two known,
        legitimate exceptions:
          - REF-PSP-AIRBAG-FRONT-001: literal, unmodified CTS TEMPLATE
            EXAMPLE content left in the document by mistake (correctly
            filtered by _TEMPLATE_EXAMPLE_RE).
          - GEN-XXX-CDC-5441x.xxx: genuine FMEA/dependability rows with
            NO description text anywhere (only quantitative reliability
            columns — occurrence rate, ASIL level, severity) — there is
            no "shall" statement to extract, a real scope limit of a
            requirement model built around shall/must sentences.
        Before the case-sensitivity and DOORS/REF merge fixes, this gap
        was 52 real requirements — including the ENTIRE SdF and Safety
        families.
        """
        if not ASU_PATH.exists():
            pytest.skip("ASU spec not found")
        from app.qa.retrieval import extract_text_from_file
        from app.qa.evidence_comparator import extract_requirement_rows

        text = extract_text_from_file(ASU_PATH)
        reqs = extract_requirements(text)
        matrix_ids = {r.req_id.replace(" ", "") for r in reqs}

        struct_rows = extract_requirement_rows(ASU_PATH)
        struct_ids = {r.req_id.replace(" ", "") for r in struct_rows}

        known_exceptions = {"REF-PSP-AIRBAG-FRONT-001", "GEN-XXX-CDC-54411", "GEN-XXX-CDC-54421"}
        missing = (struct_ids - matrix_ids) - known_exceptions
        assert missing == set(), f"unexpected new completeness gap: {sorted(missing)}"


# ── 3. Matrix generation ──────────────────────────────────────────

class TestGeneration:

    def test_fill_cells_and_preserve_template(self):
        from openpyxl import load_workbook
        reqs = [
            Requirement("REQ-0000001", "The system shall do A."),
            Requirement("REF-X-CD-Y-002", "The system shall do B."),
            Requirement("", "The system shall do C without an ID."),
        ]
        data = generate_conformity_matrix(reqs, "test")
        ws = load_workbook(io.BytesIO(data))["new version"]

        assert ws.cell(row=DATA_START_ROW, column=COL_REQ_ID).value == "REQ-0000001"
        assert ws.cell(row=DATA_START_ROW, column=COL_DESCRIPTION).value == "The system shall do A."
        assert ws.cell(row=DATA_START_ROW + 2, column=COL_REQ_ID).value in (None, "")
        assert "do C" in ws.cell(row=DATA_START_ROW + 2, column=COL_DESCRIPTION).value
        # Header + stats formula untouched
        assert "Libell" in (ws.cell(row=9, column=1).value or "")
        assert (ws["F5"].value or "").startswith("=COUNTIF")
        # Supplier columns stay empty
        for col in (4, 5, 6, 7, 8, 9):
            assert ws.cell(row=DATA_START_ROW, column=col).value in (None, "")

    def test_output_is_valid_macro_free_xlsx(self):
        reqs = [Requirement("REQ-1", "The system shall exist and be testable.")]
        data = generate_conformity_matrix(reqs, "t")
        names = zipfile.ZipFile(io.BytesIO(data)).namelist()
        assert not any("vba" in n.lower() for n in names)


# ── 4. Full pipeline on the real ASU spec ─────────────────────────

class TestRealSpec:

    @pytest.fixture(scope="class")
    def asu_result(self):
        if not ASU_PATH.exists():
            pytest.skip("ASU spec not found")
        from app.qa.retrieval import extract_text_from_file
        text = extract_text_from_file(ASU_PATH)
        return spec_to_matrix(text, ASU_PATH.name)

    def test_extracts_many_requirements(self, asu_result):
        assert asu_result["requirementsCount"] >= 200
        assert asu_result["withIdCount"] >= 200

    def test_most_ids_have_descriptions(self, asu_result):
        from openpyxl import load_workbook
        ws = load_workbook(io.BytesIO(asu_result["xlsxBytes"]))["new version"]
        with_id_and_desc = 0
        with_id = 0
        for r in range(DATA_START_ROW, ws.max_row + 1):
            rid = ws.cell(row=r, column=COL_REQ_ID).value
            if rid:
                with_id += 1
                if ws.cell(row=r, column=COL_DESCRIPTION).value:
                    with_id_and_desc += 1
        assert with_id > 0
        # At least 80% of identified requirements must carry a description
        assert with_id_and_desc / with_id >= 0.80

    def test_known_requirement_present_and_correct(self, asu_result):
        """
        Regression: "REQ-0937326" is EXIFUNC-002's own TRAILING doors-id —
        it is immediately followed (after a "Timing Performances" heading
        + the repeated table header) by "REF-ASU-CD-EXIFUNC-003", a
        completely separate, unrelated requirement with its own "shall
        provide feedback within 200 ms." statement. An earlier version of
        this extractor wrongly coalesced the two — silently dropping
        EXIFUNC-003 from the matrix and (by coincidence) making
        REQ-0937326 carry EXIFUNC-003's text. The real fix keeps them
        separate: EXIFUNC-003 must be present with its own real content,
        and REQ-0937326 must not use a following heading as its own
        (verified via the "200 ms" check no longer applying to it).
        """
        from openpyxl import load_workbook
        ws = load_workbook(io.BytesIO(asu_result["xlsxBytes"]))["new version"]
        found_exifunc003 = False
        for r in range(DATA_START_ROW, ws.max_row + 1):
            rid = ws.cell(row=r, column=COL_REQ_ID).value
            desc = ws.cell(row=r, column=COL_DESCRIPTION).value or ""
            if rid == "REF-ASU-CD-EXIFUNC-003":
                found_exifunc003 = True
                assert "200 ms" in desc
            if rid == "REQ-0937326":
                assert "Timing Performances" not in desc
        assert found_exifunc003, "REF-ASU-CD-EXIFUNC-003 not found in generated matrix"
