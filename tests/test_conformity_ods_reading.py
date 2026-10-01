"""
Tests for app/qa/conformity_analyzer.py's ODS reading (_read_ods,
_read_ods_sheet_names) and XLSX hidden-row handling (_read_xlsx).

Covers three confirmed, severe bugs found auditing real supplier
submissions:
  1. Rows nested inside <table:table-row-group> (LibreOffice/Excel row
     OUTLINE GROUPING, common on large real matrices) were invisible to a
     direct-children search — a real supplier .ods lost 936 of 975 real
     rows (its entire answer matrix) this way.
  2. A horizontally-merged cell's <table:covered-table-cell> placeholder
     was skipped entirely, shifting every following cell in the row left
     by (N-1) columns and turning a real NOK answer into unrelated text.
  3. Rows/cells hidden by an AutoFilter view or a collapsed outline group
     (ODS visibility="filter"/"collapse", XLSX row_dimensions.hidden) were
     skipped outright — that state only reflects how the file was last
     viewed in a spreadsheet app, never whether the data is real. A real
     Gentex submission had 176 of 211 real answer rows hidden this way in
     its .xlsx, reporting zero OK answers out of a mostly-compliant matrix.

Also locks in that odfpy is no longer used at all: ODS reading goes
directly through lxml (recover=True), which parses real malformed XML
(duplicate attributes) that odfpy's own strict SAX parser silently
truncates without ever raising.
"""
import io
import sys
import zipfile
from pathlib import Path

import pytest

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

from app.qa.conformity_analyzer import (
    _read_ods,
    _read_ods_sheet_names,
    _read_xlsx,
    read_spreadsheet,
    analyze_conformity_matrix,
)

DATA_DIR = Path(__file__).resolve().parent.parent / "data" / "uploads"
GENTEX_ODS = DATA_DIR / "Generic_Conformity_Matrix_of_IRDM_TS_Gentex_reponse.ods"
GENTEX_XLSX = DATA_DIR / "Generic_Conformity_Matrix_of_IRDM_TS_Gentex_reponse.xlsx"

_TABLE_NS = "urn:oasis:names:tc:opendocument:xmlns:table:1.0"
_OFFICE_NS = "urn:oasis:names:tc:opendocument:xmlns:office:1.0"
_TEXT_NS = "urn:oasis:names:tc:opendocument:xmlns:text:1.0"

_CONTENT_TEMPLATE = f"""<?xml version="1.0" encoding="UTF-8"?>
<office:document-content xmlns:office="{_OFFICE_NS}" xmlns:table="{_TABLE_NS}" xmlns:text="{_TEXT_NS}">
<office:body><office:spreadsheet>
{{tables}}
</office:spreadsheet></office:body>
</office:document-content>"""


def _make_ods_bytes(tables_xml: str) -> bytes:
    """Build a minimal in-memory .ods (just a zip with content.xml) — all
    of _read_ods/_read_ods_sheet_names only ever read that one entry."""
    content = _CONTENT_TEMPLATE.format(tables=tables_xml)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("content.xml", content)
    return buf.getvalue()


def _write_temp_ods(tmp_path, tables_xml: str) -> str:
    data = _make_ods_bytes(tables_xml)
    p = tmp_path / "test.ods"
    p.write_bytes(data)
    return str(p)


def _cell(text, **attrs):
    attr_str = "".join(f' table:{k.replace("_", "-")}="{v}"' for k, v in attrs.items())
    return f'<table:table-cell office:value-type="string"{attr_str}><text:p>{text}</text:p></table:table-cell>'


class TestTableRowGroupRecursion:

    def test_rows_nested_in_table_row_group_are_found(self, tmp_path):
        """
        Regression: a real supplier .ods had its entire answer matrix (936
        of 975 rows) wrapped in <table:table-row-group> (LibreOffice/Excel
        row outline grouping) — invisible to a direct-children-only search,
        which reported 0 real rows even though every answer was present.
        """
        tables_xml = f"""<table:table table:name="Sheet1">
<table:table-row><table:table-cell office:value-type="string"><text:p>Header</text:p></table:table-cell></table:table-row>
<table:table-row-group>
<table:table-row>{_cell("REQ-001")}{_cell("OK")}</table:table-row>
<table:table-row>{_cell("REQ-002")}{_cell("NOK")}</table:table-row>
</table:table-row-group>
</table:table>"""
        path = _write_temp_ods(tmp_path, tables_xml)
        sheets = _read_ods(path)
        assert len(sheets) == 1
        rows = sheets[0]
        assert len(rows) == 3  # header + 2 grouped rows
        assert rows[1][0] == "REQ-001"
        assert rows[1][1] == "OK"
        assert rows[2][0] == "REQ-002"
        assert rows[2][1] == "NOK"

    def test_multi_level_nested_groups_all_found(self, tmp_path):
        """Outline groups can nest (sub-groups within a group) — every
        level must still be found, not just the first."""
        tables_xml = f"""<table:table table:name="Sheet1">
<table:table-row-group>
<table:table-row>{_cell("REQ-001")}{_cell("OK")}</table:table-row>
<table:table-row-group>
<table:table-row>{_cell("REQ-002")}{_cell("NOK")}</table:table-row>
</table:table-row-group>
</table:table-row-group>
</table:table>"""
        path = _write_temp_ods(tmp_path, tables_xml)
        rows = _read_ods(path)[0]
        assert len(rows) == 2
        assert [r[0] for r in rows] == ["REQ-001", "REQ-002"]


class TestCoveredTableCellAlignment:

    def test_covered_table_cell_preserves_column_alignment(self, tmp_path):
        """
        Regression: a horizontally-merged cell is ONE real <table:table-cell>
        (number-columns-spanned="N") followed by (N-1)
        <table:covered-table-cell/> placeholders. Skipping those placeholders
        shifted every following real cell left by (N-1) columns — on a real
        file this turned a genuine NOK answer into an unrelated column's
        text, misclassifying the row as EMPTY.
        """
        tables_xml = f"""<table:table table:name="Sheet1">
<table:table-row>
{_cell("Merged Header", number_columns_spanned="2")}
<table:covered-table-cell/>
{_cell("REQ-001")}
{_cell("NOK")}
</table:table-row>
</table:table>"""
        path = _write_temp_ods(tmp_path, tables_xml)
        row = _read_ods(path)[0][0]
        # Column 0 = merged header, column 1 = covered placeholder (empty),
        # column 2 = REQ-001, column 3 = NOK — never shifted left.
        assert row[0] == "Merged Header"
        assert row[1] == ""
        assert row[2] == "REQ-001"
        assert row[3] == "NOK"


class TestVisibilityNeverSkipsRealData:

    def test_collapse_visibility_row_is_still_read(self, tmp_path):
        """
        Regression: a real supplier .ods had its ENTIRE answer matrix
        saved with the outline collapsed (visibility="collapse" on every
        real row) — skipping those rows, as this code used to, silently
        discarded 100% of the real answers. visibility only reflects the
        last-viewed UI state, never data validity.
        """
        tables_xml = f"""<table:table table:name="Sheet1">
<table:table-row table:visibility="collapse">{_cell("REQ-001")}{_cell("OK")}</table:table-row>
</table:table>"""
        path = _write_temp_ods(tmp_path, tables_xml)
        rows = _read_ods(path)[0]
        assert len(rows) == 1
        assert rows[0][0] == "REQ-001"
        assert rows[0][1] == "OK"

    def test_filter_visibility_row_is_still_read(self, tmp_path):
        """Same as above for visibility="filter" (hidden by an active
        AutoFilter view, not deleted data)."""
        tables_xml = f"""<table:table table:name="Sheet1">
<table:table-row table:visibility="filter">{_cell("REQ-002")}{_cell("NOK")}</table:table-row>
</table:table>"""
        path = _write_temp_ods(tmp_path, tables_xml)
        rows = _read_ods(path)[0]
        assert len(rows) == 1
        assert rows[0][0] == "REQ-002"
        assert rows[0][1] == "NOK"


class TestSheetNames:

    def test_sheet_names_read_from_table_name_attribute(self, tmp_path):
        tables_xml = (
            '<table:table table:name="Summary"><table:table-row>'
            + _cell("x") + "</table:table-row></table:table>"
            '<table:table table:name="Details"><table:table-row>'
            + _cell("y") + "</table:table-row></table:table>"
        )
        path = _write_temp_ods(tmp_path, tables_xml)
        assert _read_ods_sheet_names(path) == ["Summary", "Details"]

    def test_read_spreadsheet_ods_dispatches_correctly(self, tmp_path):
        tables_xml = (
            '<table:table table:name="Sheet1"><table:table-row>'
            + _cell("REQ-001") + _cell("OK") + "</table:table-row></table:table>"
        )
        path = _write_temp_ods(tmp_path, tables_xml)
        names, sheets = read_spreadsheet(path)
        assert names == ["Sheet1"]
        assert sheets[0][0] == ["REQ-001", "OK"]


class TestXlsxHiddenRowsNeverSkipped:

    def test_hidden_row_is_still_read(self, tmp_path):
        """
        Regression: a real Gentex .xlsx had an AutoFilter/manual-hide
        active that skipped 176 of 211 real answer rows — every one a
        genuine "OK" — reporting only the 35 rows that stayed visible and
        making a mostly-compliant matrix look like it had zero OK answers.
        """
        from openpyxl import Workbook
        wb = Workbook()
        ws = wb.active
        ws.append(["Req ID", "Status"])
        ws.append(["REQ-001", "OK"])
        ws.append(["REQ-002", "NOK"])
        ws.row_dimensions[2].hidden = True  # hide the REQ-001/OK row
        path = tmp_path / "test.xlsx"
        wb.save(str(path))

        sheets = _read_xlsx(str(path))
        rows = sheets[0]
        assert ["REQ-001", "OK"] in rows
        assert ["REQ-002", "NOK"] in rows


class TestRealGentexFileConsistency:
    """The definitive whole-pipeline guard: the ODS and XLSX submissions of
    the SAME real supplier file must now produce IDENTICAL classification
    results. Before these fixes: ODS reported 27 rows with corrupted
    column-shifted values, XLSX reported only 35 (176 real OK rows hidden
    by AutoFilter) — completely different, both wrong."""

    def test_ods_and_xlsx_produce_identical_stats(self):
        if not (GENTEX_ODS.exists() and GENTEX_XLSX.exists()):
            pytest.skip("Gentex fixtures not found")
        a_ods = analyze_conformity_matrix(str(GENTEX_ODS), "gentex.ods")
        a_xlsx = analyze_conformity_matrix(str(GENTEX_XLSX), "gentex.xlsx")
        assert a_ods.total_rows == a_xlsx.total_rows
        assert a_ods.stats == a_xlsx.stats
        # The full matrix includes introductory checklist items as well as
        # RETRO_* technical requirements; preserve the established fixture
        # ground truth for every extracted response row.
        assert a_ods.stats == {"OK": 175, "NOK": 28, "NA": 7}


class TestTwoRowHeader:
    """A matrix whose group-header row (title) sits ABOVE the real column
    headers must pick the row that has BOTH a conformity column and a
    comment column — not the title row. Regression: the DM11.7E matrix
    (01843_26_00003_...) has row 0 = 'Matrice de conformité / Conformity
    Matrix' (conformity only) and row 1 = 'Conformité\\nConformity' +
    'Remarques Fournisseur\\nSupplier comments'. The bare 'Conformité'
    header was not matched by any conformity pattern, so the header row
    resolved to row 0 and the 'Supplier comments' column was never read."""

    def test_bare_conformite_header_is_detected(self):
        from app.qa.conformity_analyzer import _match_any, _CONFORMITY_PATTERNS
        assert _match_any("Conformité\nConformity", _CONFORMITY_PATTERNS)
        assert _match_any("Conformity", _CONFORMITY_PATTERNS)

    def test_two_row_header_picks_comment_row(self):
        from app.qa.conformity_analyzer import _find_header_row, _find_columns
        sheet = [
            ["SUPPLIER NAME", "Exigences / Requirements", "", "",
             "", "Matrice de conformité / Conformity Matrix",
             "Matrice de conformité / Conformity Matrix",
             "Matrice de conformité / Conformity Matrix",
             "Matrice de conformité / Conformity Matrix"],
            ["DESIGNATION", "REFERENCE", "ID", "DOCUMENT",
             "Exigences / Requirements", "",
             "Conformité\nConformity",
             "Remarques Fournisseur\nSupplier comments",
             "Statut Status\n STELLANTIS",
             "Commentaires\n STELLANTIS Comments"],
            ["Planning\nTiming Plan", "DEV_REQ_DM11.7E_Part.001", "0",
             "Timing plan", "Le planning...", "", "OK", "updated", "OK",
             "03/08/2026: no remark"],
        ]
        hdr = _find_header_row(sheet)
        assert hdr == 1, f"Expected header row 1, got {hdr}"
        cols = _find_columns(sheet[hdr])
        assert 6 in cols.get("conformity", [])
        assert 7 in cols.get("comment", [])

    def test_real_dm117e_matrix_extracts_supplier_comments(self):
        """The real DM11.7E matrix must extract the 'Supplier comments'
        column (col 7) — regression for the user's report."""
        fp = DATA_DIR / "01843_26_00003_V1-2_Dev_Req_DM11.7E_CARUX_20260918.xlsx"
        if not fp.exists():
            pytest.skip("DM11.7E matrix not found")
        a = analyze_conformity_matrix(str(fp), fp.name)
        assert 7 in a.column_mapping.get("comment", [])
        with_comment = [it for it in a.items if it.comment.strip()]
        assert len(with_comment) > 0, "No supplier comments extracted"
        # The first requirement's comment must contain the supplier remark.
        assert any("CarUX" in it.comment or "updated" in it.comment
                   for it in with_comment)


class TestRealDm12fWorkbooks:
    """Cover-page and multi-level headers from the two supplied DM12F files."""

    def test_development_requirements_uses_requirements_tab(self):
        fp = DATA_DIR / "01843_25_00571_V1_Development_Requirements_DM12F_Tianma_Rev1.0_20260427.xlsx"
        if not fp.exists():
            pytest.skip("DM12F development-requirements workbook not found")
        a = analyze_conformity_matrix(str(fp), fp.name)
        assert a.sheet_name == "Requirements"
        assert a.header_row == 1
        assert a.column_mapping["req_id"] == [1]
        assert a.column_mapping["conformity"] == [6]
        assert a.column_mapping["comment"] == [7]
        assert len([item for item in a.items if item.is_requirement]) == 46
        assert all(item.conformity_category == "OK" for item in a.items if item.is_requirement)

    def test_supplier_xlsm_uses_application_matrix_and_supplier_conformity(self):
        fp = DATA_DIR / "01843_26_00005_v1-0_Conf_Matrix_LEVEL1_TS_DM12F_Supplier.xlsm"
        if not fp.exists():
            pytest.skip("DM12F supplier conformity workbook not found")
        a = analyze_conformity_matrix(str(fp), fp.name)
        assert a.sheet_name == "Application & Conformity matrix"
        assert a.header_row == 34
        assert a.column_mapping["req_id"] == [0]
        assert a.column_mapping["conformity"] == [8]
        assert a.column_mapping["stellantis_verdict"] == [10]
        req_items = [item for item in a.items if item.is_requirement]
        assert len(req_items) >= 10
        assert all(item.conformity_category == "OK" for item in req_items)
        assert any(item.conformity_raw == "OK" for item in req_items)


class TestInvalidXlsxFilterMetadata:
    def test_whitespace_custom_filter_does_not_block_workbook_reading(self, tmp_path):
        """Some supplier XLSX files contain an invalid whitespace filter
        criterion. It is display metadata and must not prevent reading cells."""
        from openpyxl import Workbook

        wb = Workbook()
        ws = wb.active
        ws.append(["Requirement", "Status"])
        ws.append(["REQ-001", "OK"])
        source = io.BytesIO()
        wb.save(source)

        corrupted = io.BytesIO()
        with zipfile.ZipFile(source, "r") as src, zipfile.ZipFile(corrupted, "w") as dst:
            for entry in src.infolist():
                data = src.read(entry.filename)
                if entry.filename == "xl/worksheets/sheet1.xml":
                    filter_xml = (
                        b'<autoFilter ref="A1:B2"><filterColumn colId="1">'
                        b'<customFilters><customFilter operator="notEqual" val=" "/>'
                        b'</customFilters></filterColumn></autoFilter>'
                    )
                    data = data.replace(b"</worksheet>", filter_xml + b"</worksheet>")
                dst.writestr(entry, data)

        path = tmp_path / "invalid_filter.xlsx"
        path.write_bytes(corrupted.getvalue())
        names, sheets = read_spreadsheet(str(path))

        assert names == ["Sheet"]
        assert sheets[0][0][:2] == ["Requirement", "Status"]
        assert sheets[0][1][:2] == ["REQ-001", "OK"]


class TestCoverTextIsNotRequirement:
    def test_repeated_long_cover_text_and_requirement_headers_are_excluded(self, tmp_path):
        from openpyxl import Workbook

        wb = Workbook()
        ws = wb.active
        ws.title = "Matrix"
        cover = (
            "DM17F is an LVDS bi-dir slave node which:\n"
            "- comply to the Display use case of the LVDS Bi-Dir Technical specification\n"
            "- has a GMSL3 deserializer\n"
            "Therefore, the following requirements are applied:"
        )
        for _ in range(3):
            ws.append([None, None, None, cover])
        ws.append([None, None, None, "Requirement Description", None, "Requirement Identifier", None, "Conformity", "Comments"])
        ws.append([None, None, None, cover, None, "REQ-0040217", None, "OK", "Verified"])
        path = tmp_path / "cover_text.xlsx"
        wb.save(path)

        analysis = analyze_conformity_matrix(str(path), path.name)

        assert len(analysis.items) == 1
        assert analysis.items[0].req_id == "REQ-0040217"
        assert analysis.items[0].is_requirement is True


class TestReferenceExtractionFallback:
    def test_reference_from_column_b_survives_unmapped_header(self, tmp_path):
        from openpyxl import Workbook

        wb = Workbook()
        ws = wb.active
        ws.title = "Matrix"
        ws.append(["", "Document reference", "Requirement Description", "", "", "Requirement Identifier", "", "Conformity", "Comments"])
        ws.append(["", "GEN-TS-RESEAU-CP_LVDS_BIDIR.0001(4)", "The ECU shall support the requirement.", "", "", "REQ-0040217", "", "OK", "Verified"])
        path = tmp_path / "reference_fallback.xlsx"
        wb.save(path)

        analysis = analyze_conformity_matrix(str(path), path.name)

        assert len(analysis.items) == 1
        assert analysis.items[0].req_id == "REQ-0040217"
        assert analysis.items[0].reference == "GEN-TS-RESEAU-CP_LVDS_BIDIR.0001(4)"


class TestContentBasedCommentDetection:
    """The agent must detect the supplier-comment column by its CONTENT, not
    by requiring the exact header 'Commentaires FNR' — every user uploads a
    differently-named column ('Remarques', 'Notes', 'Observations', …)."""

    def _make_matrix(self, comment_header):
        import io
        from openpyxl import Workbook
        wb = Workbook()
        ws = wb.active
        ws.title = "Matrix"
        ws.append(["REQ ID", "Description", "Conformité", comment_header])
        ws.append(["REQ-1", "The system shall do A.", "OK", "Tested and passed on 2026/01/15"])
        ws.append(["REQ-2", "The system shall do B.", "OK", "Will verify in next delivery"])
        ws.append(["REQ-3", "The system shall do C.", "NOK", "Not yet implemented"])
        ws.append(["REQ-4", "The system shall do D.", "OK", "Discussed with STLA, pending"])
        ws.append(["REQ-5", "The system shall do E.", "OK", "EE: ok"])
        ws.append(["REQ-6", "The system shall do F.", "OK", "20260410 ME: ok"])
        buf = io.BytesIO()
        wb.save(buf)
        buf.seek(0)
        return buf.getvalue()

    @pytest.mark.parametrize("header", [
        "Remarques", "Notes", "Observations", "Feedback",
        "Supplier Feedback", "Justification", "Commentaire",
    ])
    def test_non_standard_comment_header_detected(self, tmp_path, header):
        from app.qa.conformity_analyzer import extract_conformity_data
        fp = tmp_path / f"matrix_{header}.xlsx"
        fp.write_bytes(self._make_matrix(header))
        a = extract_conformity_data(str(fp), fp.name)
        assert a.column_mapping.get("comment"), f"No comment column for header {header!r}"
        with_comment = [it for it in a.items if it.comment.strip()]
        assert len(with_comment) == 6, f"Expected 6 comments for header {header!r}, got {len(with_comment)}"

    def test_description_column_not_misread_as_comment(self, tmp_path):
        """A long description column must never be treated as a comment."""
        import io
        from openpyxl import Workbook
        wb = Workbook()
        ws = wb.active
        ws.title = "Matrix"
        ws.append(["REQ ID", "Description", "Conformité", "Commentaires FNR"])
        for i in range(6):
            ws.append([f"REQ-{i}", "The system shall provide a very long description " * 5,
                       "OK", f"comment {i}"])
        buf = io.BytesIO()
        wb.save(buf)
        buf.seek(0)
        fp = tmp_path / "desc.xlsx"
        fp.write_bytes(buf.getvalue())
        from app.qa.conformity_analyzer import extract_conformity_data
        a = extract_conformity_data(str(fp), fp.name)
        # The comment column must be the LAST column (index 3), not the description.
        assert a.column_mapping.get("comment") == [3]
        assert all(it.comment.strip() for it in a.items if it.conformity_category == "OK")


class TestBareDomainCodesKeptAsComments:
    """Regression for the user's report: "it still detects only NOK comments,
    but NO OK comments". The supplier's comment column contained a bare domain
    code ('SYS', 'SW', 'EE', …) on most OK rows — a domain-assignment marker.
    Those were being DROPPED from the extracted comment, so the detailed table
    looked like OK comments were missing. They must be KEPT (they ARE the
    supplier's comment); only the deep-OK analysis may skip them."""

    def _make_matrix(self, tmp_path):
        import io
        from openpyxl import Workbook
        wb = Workbook()
        ws = wb.active
        ws.title = "Matrix"
        ws.append(["REQ ID", "Description", "Conformité FNR", "Commentaires FNR"])
        # OK rows with bare domain codes (must be kept)
        for i, code in enumerate(["SYS", "SW", "EE", "ME", "OD", "ME/EE"]):
            ws.append([f"REQ-{i}", f"Requirement {i} text", "OK", code])
        # An OK row with real prose (must be kept)
        ws.append(["REQ-PROSE", "Some requirement", "OK", "Will verify in next delivery"])
        buf = io.BytesIO()
        wb.save(buf)
        buf.seek(0)
        fp = tmp_path / "domains.xlsx"
        fp.write_bytes(buf.getvalue())
        return fp

    def test_domain_codes_are_kept_as_comments(self, tmp_path):
        from app.qa.conformity_analyzer import extract_conformity_data
        fp = self._make_matrix(tmp_path)
        a = extract_conformity_data(str(fp), fp.name)
        ok_items = [it for it in a.items if it.conformity_category == "OK"]
        ok_with_comment = [it for it in ok_items if it.comment.strip()]
        # ALL 7 OK rows must have a non-empty comment (6 domain codes + 1 prose).
        assert len(ok_with_comment) == 7, (
            f"Expected 7 OK comments, got {len(ok_with_comment)}: "
            f"{[it.comment for it in ok_items]}"
        )
        comments = {it.comment.strip() for it in ok_with_comment}
        assert {"SYS", "SW", "EE", "ME", "OD", "ME/EE"} <= comments

    def test_domain_codes_not_flagged_by_deep_ok(self, tmp_path):
        """Keeping domain codes in the comment must NOT make them look
        suspicious — the deep-OK analysis keeps its own filter."""
        import app.qa.conformity_analyzer as ca
        from app.qa.conformity_analyzer import extract_conformity_data
        # Disable the LLM so only the deterministic pattern engine runs.
        orig = ca._analyze_ok_deep_llm
        ca._analyze_ok_deep_llm = lambda items: ([], set())
        try:
            fp = self._make_matrix(tmp_path)
            a = extract_conformity_data(str(fp), fp.name)
            findings = ca.analyze_ok_deep(a)
        finally:
            ca._analyze_ok_deep_llm = orig
        domain_comments = {"SYS", "SW", "EE", "ME", "OD", "ME/EE"}
        flagged_domains = [f for f in findings if f["comment"].strip() in domain_comments]
        assert not flagged_domains, f"Domain codes wrongly flagged: {flagged_domains}"


class TestSupplierAgnosticRamSColumnDetection:
    """Supplier matrices use different languages, layouts, and column names.
    Detect fields from their meaning and data profile instead of requiring
    Stellantis-specific labels such as 'Conformité FNR'."""

    def _make_matrix(self, tmp_path, headers):
        from openpyxl import Workbook

        wb = Workbook()
        ws = wb.active
        ws.title = "RAMS response"
        ws.append(headers)
        rows = [
            ["RAMS-001", "The emergency stop shall operate under a single fault.", "Compliant", "Verified in the SIL test report."],
            ["RAMS-002", "The controller shall detect an unsafe output state.", "Partially Compliant", "Mitigation is planned for release 2."],
            ["RAMS-003", "The system shall record safety-related faults.", "Not Compliant", "Not implemented; supplier action open."],
            ["RAMS-004", "The unit shall meet the environmental safety limit.", "Not Applicable", "This clause does not apply to this variant."],
            ["RAMS-005", "The monitoring function shall detect loss of supply.", "Pass", "Test evidence: report TR-2026-18."],
            ["RAMS-006", "The design shall satisfy the ISO safety objective.", "Fail", "Corrective action is in progress."],
        ]
        for row in rows:
            ws.append(row)
        path = tmp_path / "rams_supplier.xlsx"
        wb.save(path)
        return path

    def test_semantic_headers_and_status_language(self, tmp_path):
        from app.qa.conformity_analyzer import extract_conformity_data

        path = self._make_matrix(
            tmp_path,
            ["Clause ID", "Requirement statement", "Supplier assessment", "Evidence / proof"],
        )
        analysis = extract_conformity_data(str(path), path.name)

        assert analysis.column_mapping["conformity"] == [2]
        assert analysis.column_mapping["comment"] == [3]
        assert analysis.column_mapping["req_id"] == [0]
        assert analysis.column_mapping["description"] == [1]
        by_id = {item.req_id: item for item in analysis.items}
        assert by_id["RAMS-001"].conformity_category == "OK"
        assert by_id["RAMS-002"].conformity_category == "NOK"
        assert by_id["RAMS-003"].conformity_category == "NOK"
        assert by_id["RAMS-004"].conformity_category == "NA"
        assert by_id["RAMS-005"].conformity_category == "OK"
        assert by_id["RAMS-006"].conformity_category == "NOK"
        assert "Verified in the SIL test report" in by_id["RAMS-001"].comment

    def test_unknown_headers_inferred_from_column_values(self, tmp_path):
        from app.qa.conformity_analyzer import extract_conformity_data

        path = self._make_matrix(
            tmp_path,
            ["Record", "Safety requirement", "Supplier position", "Verification material"],
        )
        analysis = extract_conformity_data(str(path), path.name)

        assert analysis.column_mapping["conformity"] == [2]
        assert analysis.column_mapping["comment"] == [3]
        assert analysis.column_mapping["req_id"] == [0]
        assert analysis.column_mapping["description"] == [1]
        assert len(analysis.items) == 6
        assert all(item.comment for item in analysis.items)

    def test_real_rams_iso_supplier_workbook(self):
        """The supplied RAMS/ISO matrix uses multi-level headers, CR_RAMS_* IDs,
        and Accepted/Not Applicable answers rather than the usual REQ-/OK/NOK."""
        path = DATA_DIR / "02033_17_00001_CR_RAMS-ISO_GEN_EXV1-0_Supplier.xlsx"
        if not path.exists():
            pytest.skip("RAMS-ISO supplier workbook not found")

        from app.qa.conformity_analyzer import extract_conformity_data
        analysis = extract_conformity_data(str(path), path.name)

        assert analysis.column_mapping["req_id"] == [1]
        assert analysis.column_mapping["conformity"] == [8]
        assert analysis.column_mapping["comment"] == [9]
        assert analysis.column_mapping["description"] == [4]
        assert sum(item.is_requirement for item in analysis.items) == 16
        by_id = {item.req_id: item for item in analysis.items}
        assert by_id["CR_RAMS_01"].conformity_category == "OK"
        assert by_id["CR_RAMS_04"].conformity_category == "NA"
        assert "benchmark" in by_id["CR_RAMS_04"].comment
