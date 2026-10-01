"""
Conformity Matrix Analyzer — intelligent extraction & AI consistency check.

Reads an ODS or XLSX conformity matrix, auto-detects the sheet and the
"Conformité FNR" / "Commentaires FNR" columns (even if names change),
extracts every requirement with its conformity status and comment, then
uses GPT-4o to flag inconsistencies (e.g. status=OK but comment says
"not tested", or status=NOK but comment says "all good").

Output: structured JSON + pie-chart image (base64 PNG) + PDF report.

Designed for the LEON Copilot Studio integration:
  Copilot Studio → Power Automate → Azure Function /api/conformity → this module
"""
from __future__ import annotations

import base64
import io
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from app.qa.conformity_coverage import extract_id_tokens, normalize_id

# ── Spreadsheet reading ────────────────────────────────────────────

# ODS reading is done via lxml directly on content.xml, NOT via odfpy's own
# Document/SAX loader. odfpy's strict SAX parser catches malformed-XML
# exceptions (a duplicate attribute is common in real-world .ods files —
# e.g. exported by some non-LibreOffice tools) INTERNALLY and just prints
# "SAX FAILED TO PARSE" — it never re-raises, and there is no reliable way
# for a caller to detect the truncation from the outside (odfpy's own
# lxml-repair-and-retry recipe re-serializes the "fixed" XML and hands it
# BACK to the same strict SAX parser, which still chokes on it in
# practice). Confirmed on a real supplier .ods: raw content.xml genuinely
# contains 1164 <table:table-row> elements, but odfpy — even after the
# repair-and-retry — only ever recovers 767 of them (a 34% silent data
# loss, with no error, warning, or flag surfaced anywhere). lxml's
# recover=True mode parses the WHOLE malformed document in one pass with
# no data loss, so building the sheet structure directly from its tree
# avoids the problem at the source instead of working around odfpy.
_ODS_TABLE_NS = "urn:oasis:names:tc:opendocument:xmlns:table:1.0"
_ODS_TEXT_NS = "urn:oasis:names:tc:opendocument:xmlns:text:1.0"


def _ods_tag(local: str, ns: str = _ODS_TABLE_NS) -> str:
    return f"{{{ns}}}{local}"


def _load_ods_content_root(filepath: str):
    """Parse an ODS file's content.xml via lxml (recover=True) and return
    the root element — see the _read_ods module comment for why this
    bypasses odfpy's own loader entirely."""
    import zipfile
    from lxml import etree

    with zipfile.ZipFile(filepath) as z:
        data = z.read("content.xml")
    parser = etree.XMLParser(recover=True, huge_tree=True)
    return etree.fromstring(data, parser=parser)


def _read_ods_sheet_names(filepath: str) -> List[str]:
    root = _load_ods_content_root(filepath)
    return [
        table.get(_ods_tag("name")) or f"Sheet_{i}"
        for i, table in enumerate(root.iter(_ods_tag("table")))
    ]


def _read_ods(filepath: str) -> List[List[List[str]]]:
    """
    Read an ODS file's content.xml directly via lxml and return a list of
    sheets. Each sheet is a list of rows; each row is a list of cell
    strings. Handles number-columns-repeated, number-rows-repeated,
    number-rows-spanned (vertically merged cells), and covered-table-cell
    (horizontally merged placeholder) correctly.
    """
    from lxml.etree import QName

    root = _load_ods_content_root(filepath)
    sheets: List[List[List[str]]] = []

    for table in root.iter(_ods_tag("table")):
        sheet_data: List[List[str]] = []

        # Track rowspan (merged cell) values: {col_index: (value, remaining_rows)}
        # When a cell has numberrowsspanned > 1, its value should be propagated
        # to the same column in subsequent rows.
        rowspan_values: Dict[int, Tuple[str, int]] = {}

        # Rows can be wrapped in a <table:table-row-group> (LibreOffice/Excel
        # row OUTLINE grouping — a collapsible section) — a real supplier
        # matrix with hundreds of requirements is a prime candidate for this,
        # and it nests every one of its rows a level deeper than table's own
        # direct children. table.findall("table-row") (direct children only)
        # silently missed all of them: confirmed on a real supplier .ods
        # where the entire 425-requirement matrix — 936 of the sheet's 975
        # real rows — lived inside 2 such groups, leaving only 39 unrelated
        # rows visible to the old direct-children search (reported as "0
        # requirements found" even though every answer was genuinely
        # present in the file). iter() recurses through any nesting depth.
        #
        # Row visibility ('filter' = hidden by an AutoFilter view, 'collapse'
        # = a collapsed outline group) is likewise just the state of
        # whoever last viewed the file in a spreadsheet app — never a
        # signal that the data is invalid or should be ignored. The SAME
        # real .ods had its ENTIRE answer matrix marked visibility="collapse"
        # (it was saved with the outline collapsed) — skipping those rows,
        # as earlier code did, silently discarded 100% of the real answers.
        # An automated reader's job is to see every real answer regardless
        # of how it was last displayed, so no row is ever skipped here.
        for row in table.iter(_ods_tag("table-row")):
            # A horizontally-merged cell is written as ONE real
            # <table:table-cell> (holding the value, with
            # number-columns-spanned="N") immediately followed by (N-1)
            # <table:covered-table-cell/> placeholders that occupy the
            # remaining spanned columns. Skipping those placeholders would
            # shift every following cell in the row left by (N-1) columns —
            # confirmed on a real supplier .ods where this silently turned
            # a genuine NOK answer into an unrelated column's text,
            # misclassifying the row as EMPTY. Iterating the row's direct
            # children in document order and including covered-table-cell
            # (as an empty placeholder, still advancing col_idx) keeps
            # every real cell in its true column.
            expanded: List[str] = []
            col_idx = 0
            new_rowspan_values: Dict[int, Tuple[str, int]] = {}

            for cell in row:
                local = QName(cell).localname
                if local not in ("table-cell", "covered-table-cell"):
                    continue
                is_covered = local == "covered-table-cell"
                if is_covered:
                    text = ""
                else:
                    # Extract text from all paragraphs (itertext() also
                    # picks up text nested inside formatting spans, unlike
                    # a shallow one-level child check).
                    text_parts = ["".join(p.itertext()) for p in cell.iter(_ods_tag("p", _ODS_TEXT_NS))]
                    text = " ".join(text_parts).strip()

                repeat = int(cell.get(_ods_tag("number-columns-repeated")) or "1")
                # Cap repeat to avoid huge memory usage (empty trailing cells)
                repeat = min(repeat, 500)
                rowspan = 1 if is_covered else int(cell.get(_ods_tag("number-rows-spanned")) or "1")

                for _ in range(repeat):
                    # Check if this column has an active rowspan value
                    if col_idx in rowspan_values and rowspan_values[col_idx][1] > 0:
                        # Use the rowspan value if the current cell is empty
                        rs_val, rs_remaining = rowspan_values[col_idx]
                        if not text and rs_val:
                            expanded.append(rs_val)
                        else:
                            expanded.append(text)
                    else:
                        expanded.append(text)

                    # If this cell has rowspan > 1, register it for subsequent rows
                    if rowspan > 1 and text:
                        new_rowspan_values[col_idx] = (text, rowspan - 1)

                    col_idx += 1

            # Merge new rowspan values with existing ones (decrement remaining)
            for ci, (v, r) in rowspan_values.items():
                if ci not in new_rowspan_values and r > 1:
                    new_rowspan_values[ci] = (v, r - 1)
            rowspan_values = new_rowspan_values

            # Handle number-rows-repeated attribute (empty rows can be repeated)
            row_repeat = row.get(_ods_tag("number-rows-repeated"))
            row_repeat = int(row_repeat) if row_repeat else 1
            row_repeat = min(row_repeat, 10000)  # Cap to avoid memory issues
            for _ in range(row_repeat):
                sheet_data.append(expanded)
        sheets.append(sheet_data)

    return sheets


def _read_xlsx(filepath: str) -> List[List[List[str]]]:
    """
    Read an XLSX/Excel file using openpyxl and return a list of sheets.
    Each sheet is a list of rows; each row is a list of cell strings.

    Handles:
    - Hidden columns — skipped (replaced with empty string)
    - Merged cells — values propagated from top-left to all cells in range

    Rows are NEVER skipped for being hidden (AutoFilter view or manually
    hidden): that state only reflects how whoever last viewed the file in
    Excel had it displayed, not whether the data is real. Confirmed on a
    real supplier submission (Gentex): the .xlsx had an AutoFilter/manual
    hide active that skipped 176 of 211 real answer rows — every one of
    them a genuine "OK" — reporting only the 35 NOK/NA rows that happened
    to stay visible, and silently making a mostly-compliant matrix look
    like it had zero OK answers at all.
    """
    from openpyxl import load_workbook
    from openpyxl.utils import get_column_letter

    wb = _load_xlsx_workbook(filepath, data_only=True)
    sheets: List[List[List[str]]] = []

    try:
        worksheets = wb.worksheets
        for ws in worksheets:
            # Detect hidden columns
            hidden_cols: set = set()
            for ci in range(1, ws.max_column + 1):
                col_letter = get_column_letter(ci)
                col_dim = ws.column_dimensions.get(col_letter)
                if col_dim and col_dim.hidden:
                    hidden_cols.add(ci)  # 1-indexed

            # Build merged cell value map: (row, col) → value
            # Propagate the top-left cell value to all cells in the merge range
            merged_values: Dict[Tuple[int, int], str] = {}
            for mc in ws.merged_cells.ranges:
                top_left = ws.cell(row=mc.min_row, column=mc.min_col)
                val = str(top_left.value).strip() if top_left.value is not None else ""
                if val:
                    for ri in range(mc.min_row, mc.max_row + 1):
                        for ci in range(mc.min_col, mc.max_col + 1):
                            merged_values[(ri, ci)] = val

            sheet_data: List[List[str]] = []
            for row_idx in range(1, ws.max_row + 1):
                row_values: List[str] = []
                for col_idx in range(1, ws.max_column + 1):
                    # Skip hidden columns — replace with empty string
                    if col_idx in hidden_cols:
                        row_values.append("")
                        continue
                    # Check merged cell value first
                    if (row_idx, col_idx) in merged_values:
                        row_values.append(merged_values[(row_idx, col_idx)])
                        continue
                    cell = ws.cell(row=row_idx, column=col_idx)
                    val = str(cell.value).strip() if cell.value is not None else ""
                    row_values.append(val)
                sheet_data.append(row_values)
            sheets.append(sheet_data)
    finally:
        wb.close()
    return sheets


def _load_xlsx_workbook(filepath: str, *, data_only: bool = True):
    """Load an OOXML workbook, tolerating invalid non-filtering filter metadata.

    Excel files from some suppliers store a space as the value of a custom
    AutoFilter criterion. That is not a valid criterion according to OOXML,
    and openpyxl 3.1.2 rejects the *entire workbook* while parsing worksheet
    XML—even though the cells and the rest of the workbook are valid. Filter
    state is only a display preference for this analyzer, so when that specific
    unsupported metadata is present, retry a sanitized in-memory package. The
    uploaded file itself is never changed.
    """
    from zipfile import ZIP_DEFLATED, ZipFile

    from openpyxl import load_workbook
    from openpyxl.utils.exceptions import InvalidFileException

    load_error = None
    try:
        return load_workbook(filepath, data_only=data_only)
    except (ValueError, InvalidFileException) as exc:
        # Limit compatibility retry to the precise openpyxl validation failure.
        cause = exc
        is_invalid_filter_value = False
        while cause is not None:
            if "Value must be either numerical or a string containing a wildcard" in str(cause):
                is_invalid_filter_value = True
                break
            cause = cause.__cause__
        if not is_invalid_filter_value:
            raise
        load_error = exc

    import io
    import re

    repaired = io.BytesIO()
    changed = False
    with ZipFile(filepath, "r") as source, ZipFile(repaired, "w", ZIP_DEFLATED) as target:
        for entry in source.infolist():
            data = source.read(entry.filename)
            if entry.filename.startswith("xl/worksheets/") and entry.filename.endswith(".xml"):
                # Custom-filter values are XML attributes; preserve every
                # other byte and remove only whitespace-only invalid values.
                data, count = re.subn(
                    rb'<customFilter\b(?=[^>]*\bval\s*=\s*["\']\s+["\'])[^>]*/>',
                    b"",
                    data,
                )
                changed = changed or count > 0
            target.writestr(entry, data)

    if not changed:
        # The error was not caused by the known supplier filter metadata.
        raise load_error
    repaired.seek(0)
    return load_workbook(repaired, data_only=data_only)


def read_spreadsheet(filepath: str) -> Tuple[List[str], List[List[List[str]]]]:
    """
    Read any supported spreadsheet (ODS or XLSX).
    Returns (sheet_names, sheets_data).
    """
    ext = filepath.lower().rsplit(".", 1)[-1]
    if ext == "ods":
        sheet_names = _read_ods_sheet_names(filepath)
        sheets_data = _read_ods(filepath)
    elif ext in ("xlsx", "xlsm", "xls"):
        wb = _load_xlsx_workbook(filepath, data_only=True)
        sheet_names = wb.sheetnames
        wb.close()
        sheets_data = _read_xlsx(filepath)
    else:
        raise ValueError(f"Unsupported file extension: .{ext}")

    return sheet_names, sheets_data


# ── Intelligent column detection ───────────────────────────────────

# Canonical names and their fuzzy variants
_CONFORMITY_PATTERNS = [
    r"conformit[eé]\s*fnr",
    r"conformity\s*fnr",
    r"supplier\s*conformity",
    r"conformit[eé]\s*(supplier|fournisseur)",
    r"^conformity\s*matrix$",
    r"^matrice\s*de\s*conformit[eé]$",
    r"^(?:conformit[eé]|conformity)\s*/\s*(?:commentaires?|comments?)$",
    r"^conformit[eé]\s*/\s*commentaires?\s*/\s*conformity\s*/\s*comments?$",
    r"^conformity\s*/\s*comments?\s*/\s*conformit[eé]\s*/\s*commentaires?$",
    r"statut\s*fnr",
    r"validation\s*fnr",
    r"conformit[eé]\s*(g[eé]n[eé]ral|global)",
    r"^(?!.*(?:matrix|matrice))conformit[eé](?:\s*fnr)?$",
    r"supplier\s*response",
    r"supplier\s*status",
    r"^(?!.*(?:stellantis|psa|test|supplier test)).*\bstatus\b.*$",
    r"\banswer\b",
    r"\bresponse\b",
    r"^(supplier\s*)?(answer|reply|response)$",
    r"^supplier\s*answer\b",
    r"^conformit[eé]\s*/\s*commentaires?(?:\s*/\s*conformity\s*/\s*comments?)?$",
    r"^conformity\s*/\s*comments?(?:\s*/\s*conformit[eé]\s*/\s*commentaires?)?$",
    r"^conformit[eé]\s*/\s*commentaires?(?:\s*/\s*conformity\s*/\s*comments?)?$",
    r"^conformity\s*/\s*comments?(?:\s*/\s*conformit[eé]\s*/\s*commentaires?)?$",
    r"supplier\s*(assessment|evaluation|response|answer|declaration)",
    r"(compliance|conformity)\s*(status|result|assessment|level)",
    r"(assessment|evaluation|verification)\s*(result|status|outcome)",
    r"^(assessment|evaluation|status|result|answer|verdict|rating)$",
    r"\b(compliance|conformity)\s*(assessment|evaluation|declaration|level|rating|result|status)\b",
    r"^(compliance|conformity)(\s*(assessment|evaluation|declaration|level|rating|result|status))?$",
    r"^assessment$",
    r"^evaluation$",
    r"^supplier\s+evaluation$",
    r"^supplier\s+assessment$",
    r"^(supplier\s*)?(compliant|compliance\s*status)$",
    r"^meets?\s*(requirement)?$",
    r"^(status|result|answer|response|verdict|rating|compliance|conformity)$",
    r"statut\s*(supplier|fournisseur)",
    r"^(gentex\s+)?conformity$",
    r"^conformity\s+gentex$",
    r"^gentex\s+(response|answer|status)$",
    r"^engagement(?!\s*minimum)",       # "Engagement" / "Engagement\nCommitment" — supplier conformity status
    r"^commitment$",                    # exact "Commitment" (not "Minimum commitment")
    r"^conformit[eé]",                  # bare "Conformité" / "Conformité\nConformity" column header
    r"^conformity",                     # bare "Conformity" column header
    r"^ok$",
    r"^nok$",
    r"^gentex\s*conformity$",
    r"^conformity\s*gentex$",
    r"^gentex\s*response$",
]

_COMMENT_PATTERNS = [
    r"^commentaires?\b",                # starts with "Commentaire(s)" — "Commentaires\nComments", "Commentaires FNR", …
    r"^comments?\b",                    # starts with "Comment" / "Comments"
    r"^(?:conformit[eé]|conformity)\s*/\s*(?:commentaires?|comments?)$",
    r"^conformit[eé]\s*/\s*commentaires?\s*/\s*conformity\s*/\s*comments?$",
    r"^conformity\s*/\s*comments?\s*/\s*conformit[eé]\s*/\s*commentaires?$",
    r"^conformit[eé]\s*/\s*commentaires?(?:\s*/\s*conformity\s*/\s*comments?)?$",
    r"^conformity\s*/\s*comments?(?:\s*/\s*conformit[eé]\s*/\s*commentaires?)?$",
    r"^remarques?\b",
    r"^supplier\s*comment$",
    r"^(supplier\s*)?(notes?|remarks?|observations?|justification|rationale|evidence|explanation|feedback|deviation|action|response)(\s*/\s*(evidence|details?|proof))?$",
    r"(supplier|vendor|manufacturer)\s*(notes?|remarks?|observations?|justification|rationale|evidence|explanation|feedback|deviation|action)",
    r"^(reason|details?|proof|evidence(?:\s*/\s*proof)?|verification\s*evidence|implementation\s*notes?)$",
    r"^evidence\s*/\s*proof$",
    r"comments?\s*(from|by)\s*(supplier|fournisseur)",  # "Comments from supplier"
    r"\bsupplier\s*comments?\b",
    r"commentaires?\s*fnr",
    r"comments?\s*fnr",
    r"supplier\s*comments?",
    r"commentaires?\s*(supplier|fournisseur)",
    r"observations?\s*fnr",
    r"supplier\s*remark",
    r"remarks?\s*(supplier|fournisseur)",
    r"supplier\s*note",
    r"commentaires?\s*supplier",
    r"if\s*nok.*commitment",
    r"minimum\s*commitment",
    r"engagement\s*minimum",
]

# Stellantis verdict columns — "Commentaires STELLANTIS" / "Statut STELLANTIS"
# These columns contain the Stellantis-side OK/NOK verdict and must be checked.
_STELLANTIS_VERDICT_PATTERNS = [
    r"commentaires?\s*stellantis",
    r"statut\s*stellantis",
    r"statut\s*status\s*stellantis",
    r"^statut\s*/\s*status\s*/\s*stellantis$",
    r"^statut\s*stellantis\s*/\s*stellantis'?s\s*status$",
    r"(?:statut|status)(?:\s+status)?\s*(?:stellantis|psa)",
    r"(?:statut|status)\s+stellantis",
    r"stellantis\s*comments?",
    r"stellantis\s*status",
    r"stellantis\s*remark",
    r"statut\s*psa",          # "Statut PSA / PSA's status"
    r"psa['’]?s\s*status",
    r"psa\s*status",
]

# Test-result fields are separate from the supplier's overall conformity
# commitment. Do not let broad `status` matching fold them into the main
# conformity result.
_TEST_STATUS_PATTERNS = [
    r"(?:supplier\s+)?test'?s?\s*status",
    r"statut\s*test\s*fnr",
]

# Version applicable column — "Version Version" / "Version appliquée Applied version"
# These match the DOCUMENT version column (e.g., "Version / Version")
_VERSION_PATTERNS = [
    r"^version\s*version$",
    r"^version\s*appliqu",
    r"^applied\s*version$",
    r"^version$",
]

# Version APPLICABLE column — "Version applicable / Applicable version"
# This is the SUPPLIER's applicable version, distinct from the document version.
# Must be detected separately and prioritized over _VERSION_PATTERNS.
_VERSION_APPLICABLE_PATTERNS = [
    r"version\s*applic",            # "Version applicable"
    r"applicable\s*version",        # "Applicable version"
    r"version\s*appliqu",           # "Version appliquée" (also an applied version)
    r"applied\s*version",           # "Applied version"
]

_REQ_ID_PATTERNS = [
    r"^(requirement\s*(id|identifier|no\.?|number|#)|req\s*(id|identifier|no\.?|number|#))$",
    r"req[-_]?\d",
    r"exigence",
    r"requirement",
    r"liste\s*des\s*doc",
    r"r[eé]f[eé]rence",
    r"reference",
    r"^id$",
    r"^feature$",
]

# Description column — "Libellé de la dernière version de l'exigence" / "Last
# Requirement Description" (the requirement text, used for display + coverage).
_DESCRIPTION_PATTERNS = [
    r"libell[eé]",
    r"description",
    r"descriptif",
    r"requirement\s*description",
    r"last\s*requirement\s*description",
    r"(safety|system|technical|functional)?\s*requirement\s*(text|statement|details?|description)",
    r"^(requirement|specification)\s*(text|details?|statement)$",
    r"^requirement$",
    r"^wording$",
    r"title\s*of\s*requirement",
    r"^requirement\s*text$",
    r"^(safety|system|technical|functional)\s+requirement$",
    r"^designation$",
]

# Reference column — "Référence" (spec-side requirement id, column B in the
# standard Stellantis layout).
_REFERENCE_PATTERNS = [
    r"r[eé]f[eé]rence",
    r"reference",
    r"^ref$",
    r"^document$",
    r"^reference$",
]


def _normalize(text: str) -> str:
    """Normalize text for fuzzy matching: lowercase, strip accents, collapse spaces."""
    if not text:
        return ""
    text = text.lower().strip()
    # Remove accents
    replacements = {"é": "e", "è": "e", "ê": "e", "ë": "e",
                    "à": "a", "â": "a", "ä": "a",
                    "ù": "u", "û": "u", "ü": "u",
                    "î": "i", "ï": "i",
                    "ô": "o", "ö": "o",
                    "ç": "c", "ñ": "n"}
    for old, new in replacements.items():
        text = text.replace(old, new)
    text = re.sub(r"\s+", " ", text)
    return text


def _match_any(text: str, patterns: List[str]) -> bool:
    """Check if normalized text matches any of the patterns."""
    norm = _normalize(text)
    if not norm:
        return False
    for pat in patterns:
        if re.search(pat, norm):
            return True
    return False


def _find_header_row(sheet: List[List[str]], max_scan: int = 50) -> Optional[int]:
    """
    Find the header row by searching for rows containing both
    'Conformité FNR' and 'Commentaires FNR' (or their variants).

    Also detects Gentex-style headers with separate 'OK'/'NOK' columns
    and 'Supplier comment' columns.
    """
    best_row = None
    best_score = 0
    has_labeled_header = any(
        _match_any(cell, _COMMENT_PATTERNS)
        for row in sheet[:max_scan]
        for cell in row
    )
    has_combined_header = any(
        _match_any(cell, _CONFORMITY_PATTERNS)
        and _match_any(cell, _COMMENT_PATTERNS)
        for row in sheet[:max_scan]
        for cell in row
    )
    has_verdict_header = any(
        _match_any(cell, _STELLANTIS_VERDICT_PATTERNS)
        or _match_any(cell, _TEST_STATUS_PATTERNS)
        for row in sheet[:max_scan]
        for cell in row
    )
    # Score header rows using both recognizable labels and the data beneath
    # them. Real supplier files often rename every column, so content evidence
    # (status-like cells below a candidate header) is a first-class signal.
    for ri, row in enumerate(sheet[:max_scan]):
        # A row containing both an identifier and a verdict is data, not a
        # possible header, even if its text matches a broad label pattern.
        row_has_id = any(_looks_like_req_id_value(_normalize(cell)) for cell in row if cell)
        row_has_status = any(_looks_like_conformity_value(_normalize(cell)) for cell in row if cell)
        if row_has_id and row_has_status:
            continue
        has_conformity = False
        has_comment = False
        has_requirement_context = False
        has_ok_nok = False
        has_non_status_context = False
        has_verdict_label = False
        has_test_label = False
        for cell in row:
            norm = _normalize(cell)
            if norm == "ok" or norm == "nok":
                has_ok_nok = True
            # Status words are common row values (e.g. "Compliant", "Pass")
            # and must not make a data row look like a header. Header labels
            # such as "Compliant status" remain eligible because they are not
            # themselves conformity values.
            is_status_value = _looks_like_conformity_value(norm)
            # A merged group title such as "Conformity Matrix" describes
            # the table but is not a response-column header. Treating it as
            # one lets cover pages and summary bands outrank the real header.
            group_title = (
                "conformity matrix" in norm
                or "matrice de conformite" in norm
            )
            if (
                not is_status_value
                and not group_title
                and not _match_any(cell, _STELLANTIS_VERDICT_PATTERNS)
                and not _match_any(cell, _TEST_STATUS_PATTERNS)
                and _match_any(cell, _CONFORMITY_PATTERNS)
            ):
                has_conformity = True
            if _match_any(cell, _COMMENT_PATTERNS):
                has_comment = True
            if _match_any(cell, _STELLANTIS_VERDICT_PATTERNS):
                has_verdict_label = True
            if _match_any(cell, _TEST_STATUS_PATTERNS):
                has_test_label = True
            if (_match_any(cell, _REQ_ID_PATTERNS)
                    or _match_any(cell, _DESCRIPTION_PATTERNS)
                    or _match_any(cell, _REFERENCE_PATTERNS)):
                has_requirement_context = True
            if (not is_status_value
                    and not _match_any(cell, _CONFORMITY_PATTERNS)
                    and not _match_any(cell, _COMMENT_PATTERNS)
                    and not _match_any(cell, _STELLANTIS_VERDICT_PATTERNS)
                    and not _match_any(cell, _TEST_STATUS_PATTERNS)
                    and norm):
                has_non_status_context = True
        content_conf, content_comments = _detect_conformity_columns_by_content(
            sheet, ri + 1, max_rows=40
        )
        # A row is likely the header when its following rows contain a
        # categorical conformity column and a distinct free-text column.
        content_score = (3 if content_conf else 0) + (1 if content_comments else 0)
        has_explicit_verdict_label = has_verdict_label or has_test_label
        # If any row has explicit comment/verdict labels, do not allow a
        # merged group-title row to outrank the actual column header based
        # only on statuses/text found below it.
        has_verdict_label = has_verdict_label or any(
            _match_any(cell, _STELLANTIS_VERDICT_PATTERNS)
            or _match_any(cell, _TEST_STATUS_PATTERNS)
            for cell in row
        )
        if has_labeled_header and not (has_comment or has_verdict_label or has_combined_header):
            content_score = 0
        if has_labeled_header and has_comment and not has_explicit_verdict_label:
            content_score += 4
        if has_verdict_header and not has_explicit_verdict_label:
            content_score = 0
        if has_verdict_header and has_explicit_verdict_label:
            content_score += 8
        if group_title:
            content_score = 0
        if has_explicit_verdict_label:
            label_score_boost = 3
        else:
            label_score_boost = 0
        label_score = (4 * int(has_conformity) + 3 * int(has_comment)
                       + 2 * int(has_requirement_context) + label_score_boost)
        if has_non_status_context and not group_title:
            label_score += 4
        if has_conformity and has_comment and has_requirement_context:
            label_score += 8
        if has_conformity and has_comment and has_non_status_context:
            label_score += 2
        if (has_combined_header and has_conformity and has_requirement_context
            and not group_title):
            label_score += 5
        if has_labeled_header and not has_comment:
            # Prefer the actual column header with supplier-comment labels
            # over an upper merged title that names only the status group.
            label_score -= 2
        # Exact OK/NOK split columns are a meaningful status signal, but only
        # count them as a header when a separate comment or data profile also
        # supports that interpretation (to avoid matching ordinary prose).
        if has_ok_nok and (has_comment or content_conf):
            label_score += 4
        score = label_score + content_score
        if score > best_score:
            best_score = score
            best_row = ri
    return best_row if best_score > 0 else None


def _find_columns(header_row: List[str]) -> Dict[str, List[int]]:
    """
    Find column indices for conformity, comment, Stellantis verdict, and requirement ID columns.
    Returns dict with 'conformity', 'comment', 'stellantis_verdict', 'req_id' keys
    (lists of indices since there may be multiple sets).

    Also detects Gentex-style OK/NOK separate column pairs (where 'OK' and 'NOK'
    are individual column headers, and the conformity is determined by which
    column has a value).

    Version applicable columns are detected separately from document version columns.
    'version_applicable' = "Version applicable / Applicable version" (supplier's applicable version)
    'version' = "Version / Version" (document version)
    """
    conformity_cols: List[int] = []
    comment_cols: List[int] = []
    stellantis_verdict_cols: List[int] = []
    req_id_cols: List[int] = []
    version_cols: List[int] = []
    version_applicable_cols: List[int] = []
    description_cols: List[int] = []
    reference_cols: List[int] = []
    ok_cols: List[int] = []  # Gentex-style: separate OK column
    nok_cols: List[int] = []  # Gentex-style: separate NOK column

    for ci, cell in enumerate(header_row):
        norm = _normalize(cell)
        # Check for exact "OK" and "NOK" column headers (Gentex style)
        if norm == "ok":
            ok_cols.append(ci)
        elif norm == "nok":
            nok_cols.append(ci)
        elif (
            _match_any(cell, _CONFORMITY_PATTERNS)
            and "conformity matrix" not in norm
            and "matrice de conformite" not in norm
            and not _match_any(cell, _COMMENT_PATTERNS)
            and not _match_any(cell, _STELLANTIS_VERDICT_PATTERNS)
            and not _match_any(cell, _TEST_STATUS_PATTERNS)
        ):
            conformity_cols.append(ci)
        # Check version APPLICABLE first (before comment) — in some ODS files,
        # the "Version applicable" column header from a sub-header row says
        # "Commentaires FNR", but the actual column contains version data.
        # We detect it via the _VERSION_APPLICABLE_PATTERNS on the real header.
        if _match_any(cell, _VERSION_APPLICABLE_PATTERNS):
            version_applicable_cols.append(ci)
        if _match_any(cell, _COMMENT_PATTERNS):
            comment_cols.append(ci)
        if (_match_any(cell, _STELLANTIS_VERDICT_PATTERNS)
                and not _match_any(cell, _TEST_STATUS_PATTERNS)):
            stellantis_verdict_cols.append(ci)
        if _match_any(cell, _REQ_ID_PATTERNS):
            req_id_cols.append(ci)
        if _match_any(cell, _VERSION_PATTERNS):
            version_cols.append(ci)
        if _match_any(cell, _DESCRIPTION_PATTERNS):
            description_cols.append(ci)
        if _match_any(cell, _REFERENCE_PATTERNS):
            reference_cols.append(ci)

    # "Reference" and the abbreviated "Ref" identify source-spec references,
    # not supplier requirement IDs. The broad ID-header patterns also match
    # the word "reference", so remove these columns from the ID candidates.
    req_id_cols = [ci for ci in req_id_cols if ci not in reference_cols
                   or _normalize(header_row[ci]) in {"id", "requirement id", "requirement identifier"}]

    # Remove version_applicable_cols from version_cols (they overlap with "Version appliquée")
    version_cols = [v for v in version_cols if v not in version_applicable_cols]

    # Remove version_applicable_cols from comment_cols (in ODS, the sub-header
    # may label the "Version applicable" column as "Commentaires FNR")
    comment_cols = [c for c in comment_cols if c not in version_applicable_cols]

    # Remove overlapping classifications. A column like "Statut Test FNR /
    # Supplier Test's status" matches generic conformity wording, but it is a
    # per-delivery test result, not the primary conformity response.
    conformity_cols = [
        c for c in conformity_cols
        if c not in stellantis_verdict_cols
        and not _match_any(header_row[c], _TEST_STATUS_PATTERNS)
    ]

    # If we found OK/NOK separate column pairs, add them to conformity_cols
    # as pairs (ok_col, nok_col) — the extraction logic will handle them
    if ok_cols or nok_cols:
        # Pair them: each OK column with the next NOK column
        for ok_ci in ok_cols:
            conformity_cols.append(ok_ci)
        for nok_ci in nok_cols:
            conformity_cols.append(nok_ci)

    return {
        "conformity": conformity_cols,
        "comment": comment_cols,
        "stellantis_verdict": stellantis_verdict_cols,
        "req_id": req_id_cols,
        "version": version_cols,
        "version_applicable": version_applicable_cols,
        "description": description_cols,
        "reference": reference_cols,
        "ok_cols": ok_cols,
        "nok_cols": nok_cols,
    }


def _find_data_start(sheet: List[List[str]], header_row_idx: int,
                     col_mapping: Optional[Dict[str, List[int]]] = None) -> int:
    """
    Find the first data row after the header.
    A data row is one that has a non-empty value in the first column
    (typically a REQ-ID or reference).

    If col_mapping is provided, also checks req_id columns and conformity columns
    (some files like Gentex have col 0 empty but data in col 2+).
    """
    # A structural marker is more reliable than a non-empty first column: many
    # matrices place legends and status totals above the requirements table.
    for ri in range(header_row_idx + 1, min(len(sheet), header_row_idx + 100)):
        if any("debut exigences" in _normalize(cell)
             or "debut exigence" in _normalize(cell)
             or "requirements start" in _normalize(cell)
             for cell in sheet[ri] if cell):
            return ri + 1

    # Determine which columns to check for data presence
    check_cols = [0]  # Always check col 0
    if col_mapping:
        check_cols.extend(col_mapping.get("req_id", []))
        check_cols.extend(col_mapping.get("conformity", []))
        check_cols.extend(col_mapping.get("comment", []))
    # Deduplicate
    check_cols = list(dict.fromkeys(check_cols))

    for ri in range(header_row_idx + 1, len(sheet)):
        row = sheet[ri]
        if not row:
            continue
        # Check if any of the relevant columns has a non-empty value
        found_val = ""
        for ci in check_cols:
            val = row[ci].strip() if ci < len(row) and row[ci] else ""
            if val:
                found_val = val
                break
        if not found_val:
            continue
        if found_val.startswith("Template") or found_val.startswith("STOP"):
            continue
        # Skip summary rows (BLOCK, CONVERGED, etc.)
        if found_val.upper() in ("BLOCK", "CONVERGED", "NOT CONVERGED",
                                 "NO STELLANTIS ANSWER", "NO SUPPLIER ANSWER",
                                 "NB OF REQ. TO CONV."):
            continue
        return ri
    return header_row_idx + 1


# ── Conformity value classification ────────────────────────────────

_OK_VALUES = {
    "ok", "conforme", "conform", "c", "yes", "oui", "/", "ko→ok",
    "pass", "passed", "compliant", "complies", "compliance", "conforms",
    "conformant", "meets", "met", "satisfied", "acceptable", "approved",
    "fully compliant", "fully conforms", "yes - compliant",
    "accepted", "agreed", "provided",
}
_NOK_VALUES = {
    "nok", "non conforme", "non conform", "nc", "no", "non", "ko",
    "fail", "failed", "not compliant", "non compliant", "non-compliant", "noncompliant",
    "does not comply", "does not conform", "not met", "not satisfied",
    "not acceptable", "rejected", "not approved", "partial", "partially compliant",
    "partially conforms",
    "not accepted", "rejected",
}
_NA_VALUES = {"na", "n/a", "not applicable", "non applicable", "non app",
              "non implémenté", "non implemente", "non implante", "sans objet"}

# Stellantis domain responsibility codes — when these appear alone (without ": ok"),
# they are just domain assignments (EMPTY — no conformity assessment yet).
# When followed by ": ok" they are classified as OK.
_DOMAIN_CODES = {
    "ee", "me", "sw", "od", "ve", "sys", "dq", "tp", "emc", "opt", "cg",
    "fusa", "function safety", "hw", "mechanical", "electrical", "software",
    "touch", "all",
}

# Negation phrasing that indicates genuine non-conformity even when embedded
# in longer free text or prefixed by a domain code (e.g. "Not OK, needs
# rework", "EE: NOK", "currently not compliant"). Checked BEFORE the OK
# regex below so a negated statement is never misread as OK just because the
# bare word "ok" also appears elsewhere in it (a 2026 audit against real
# Stellantis phrasing found "Not OK" and "EE: NOK" both silently fell through
# to EMPTY, and "not compliant" text containing a stray "ok" was misread as
# OK — this single check fixes both directions).
_NOK_NEGATION_RE = re.compile(
    r"\bnot\s+ok(?:ay)?\b|\bnot\s+conform|\bnot\s+compliant\b|"
    r"\bdoes(?:n'?t| not)\s+(?:meet|comply|conform)|\bfails?\s+to\b|"
    r"\bnon\s+conforme?\b|\bnok\b"
)

# "Uncertain/pending" language — the item hasn't actually been assessed yet
# (as opposed to genuinely N/A or genuinely conforming). Whether this counts
# as NOK or EMPTY depends on the caller's `is_assessment` flag (see below).
_UNCERTAIN_PENDING_RE = re.compile(
    r"\btbd\b|\bto\s+be\s+(?:determined|verified|confirmed|checked)\b|"
    r"\bpending\b|\bin\s+progress\b|\bunder\s+review\b|\ba\s+verifier\b|"
    r"\ben\s+cours\b|\bnot\s+yet\b"
)


def classify_conformity(value: str, is_assessment: bool = True) -> str:
    """
    Classify a conformity value into a normalized category.
    Returns one of: OK, NOK, NA, EMPTY

    Handles Stellantis-specific patterns:
    - "/" = conform (OK)
    - "EE: ok", "SW: ok", "TP: ok", "ME: ok" = domain-specific OK
    - "EE: NOK", "not compliant", "not ok", "does not meet" = NOK, even when
      embedded in longer free text or prefixed by a domain code
    - "EE", "SW", "ME", "OD", "VE", "SYS", "DQ" (domain codes without ": ok") = EMPTY (just domain assignment)
    - "NA", "N/A", "Sans objet" = not applicable (tolerant of trailing
      punctuation/free text, e.g. "N/A - not required for this variant")
    - Single letters A-H = version codes (EMPTY)
    - Uncertain/pending language ("TBD", "pending", "en cours") = NOK if
      is_assessment else EMPTY (not confirmed conform, but severity depends
      on whether this column is a genuine assessment column)

    Args:
        value: The raw conformity value from the spreadsheet cell.
        is_assessment: If True, uncertain/pending language patterns are NOK.
                       If False, they are EMPTY.
    """
    if not value or not value.strip():
        return "EMPTY"

    norm = _normalize(value).strip()
    # Local-only punctuation normalization for classification — NOT applied
    # to the shared _normalize() since that function is also used for
    # header/column matching elsewhere in this file, where punctuation can
    # be meaningful. Periods are REMOVED entirely (so abbreviation-style
    # "N.A." collapses to "na", not "n a "); hyphens become spaces instead
    # (so "Non-conforme" still matches "non conforme" as two words).
    class_norm = norm.replace(".", "")
    class_norm = re.sub(r"-", " ", class_norm)
    class_norm = re.sub(r"\s+", " ", class_norm).strip()

    # Negation-based NOK — checked FIRST so a negated statement is never
    # misclassified as OK just because it also contains the bare word "ok".
    if (class_norm in _NOK_VALUES or class_norm.startswith("nok")
            or _NOK_NEGATION_RE.search(class_norm)
            or re.search(r"\b(?:non|not)\s+(?:compliant|conformant|conforming)\b", class_norm)
            or re.search(r"\bdoes\s+not\s+(?:comply|conform|meet)\b", class_norm)
            or re.search(r"\b(?:fail|failed|fails|partial(?:ly)?)\b", class_norm)):
        return "NOK"

    # Domain-specific OK patterns: "EE: ok", "SW: ok", "TP: ok", "ME: ok", "OPT: OK"
    # Also "EE: ok SW: ok" (multi-domain), "DQ: ok", "CG 20260316:OK"
    # Also "Glass is okay", "okay"
    if re.search(r"\b(ok|okay|pass|passed|compliant|conformant|satisf(?:y|ies|ied)|approved|meets?|met)\b", class_norm):
        return "OK"

    # Patterns like "DQ: ok,20260413 ME:" or "CG 20260316:OK"
    if re.search(r":\s*ok", class_norm):
        return "OK"

    if class_norm in _OK_VALUES:
        return "OK"
    if (class_norm in _NA_VALUES or class_norm.startswith("not applicable")
            or class_norm.startswith("non applic") or class_norm.startswith("n/a")):
        return "NA"

    if _UNCERTAIN_PENDING_RE.search(class_norm):
        return "NOK" if is_assessment else "EMPTY"

    # Stellantis domain codes without ": ok" — just domain assignments (EMPTY)
    # These do NOT indicate non-conformity; they indicate which domain is responsible.
    # The conformity status comes from the primary conformity column (e.g., "/" = OK).
    if class_norm in _DOMAIN_CODES:
        return "EMPTY"

    # Domain codes with trailing colon (e.g., "DQ:", "EE:") — incomplete assessment (EMPTY)
    norm_stripped = class_norm.rstrip(":").strip()
    if norm_stripped in _DOMAIN_CODES:
        return "EMPTY"

    # Check for slash-separated domain codes like "ME/VE", "EE/VE/ME" (EMPTY)
    slash_parts = [p.strip() for p in class_norm.split("/") if p.strip()]
    if len(slash_parts) >= 2 and all(p in _DOMAIN_CODES for p in slash_parts):
        return "EMPTY"

    # Single letter versions (A-H) are version codes, not conformity (EMPTY)
    if len(class_norm) == 1 and class_norm in "abcdefgh":
        return "EMPTY"

    return "EMPTY"


# ── Data structures ─────────────────────────────────────────────────

@dataclass
class ConformityItem:
    """A single requirement row from the conformity matrix."""
    row_index: int
    req_id: str = ""
    reference: str = ""
    description: str = ""
    conformity_raw: str = ""
    conformity_category: str = "EMPTY"
    comment: str = ""
    version: str = ""  # Version applicable (from "Version applicable" or "Version" column)
    column_set: int = 0  # which set of conformity/comment columns (0-based)
    needs_review: bool = False  # True for UNKNOWN/STANDBY items needing manual verification
    classification_confidence: str = "high"  # high/medium/low — confidence in the classification
    is_requirement: bool = True  # False for document/category rows (non-"REQ-" IDs in Stellantis matrices)


@dataclass
class ConformityAnalysis:
    """Complete analysis result."""
    sheet_name: str = ""
    header_row: int = -1
    data_start_row: int = -1
    total_rows: int = 0
    sheet_total_rows: int = 0  # Rows up to the last non-empty row (trailing empties excluded)
    items: List[ConformityItem] = field(default_factory=list)
    # Statistics
    stats: Dict[str, int] = field(default_factory=dict)
    # AI inconsistency findings
    inconsistencies: List[Dict] = field(default_factory=list)
    # AI deep analysis of OK responses (FNR says OK but comment is suspicious)
    ok_deep_findings: List[Dict] = field(default_factory=list)
    # How the deep analysis was performed: "ia", "ia+motifs" or "motifs"
    ok_deep_method: str = ""
    # Column mapping
    column_mapping: Dict[str, List[int]] = field(default_factory=dict)
    # Chart (base64 PNG)
    chart_base64: str = ""
    # Report text
    report_text: str = ""
    # File name
    file_name: str = ""


# ── Main extraction function ───────────────────────────────────────

def _detect_assessment_columns(
    sheet: List[List[str]],
    conformity_cols: List[int],
    data_start: int,
) -> set:
    """
    Detect which conformity columns are 'assessment columns' (contain ': ok' patterns).

    A column is an assessment column if a significant proportion (>10%) of its
    non-empty data cells contain ': ok' patterns (e.g., 'EE: ok', 'ME: ok').
    This indicates the column is used for conformity assessment, where domain
    codes without ': ok' mean NOK.

    Columns without enough ': ok' patterns are 'domain assignment columns' where
    domain codes are just assignments, not conformity statuses.

    Returns a set of column indices that are assessment columns.
    """
    assessment_cols = set()
    for ci in conformity_cols:
        total_non_empty = 0
        ok_count = 0
        for ri in range(data_start, len(sheet)):
            row = sheet[ri]
            val = row[ci].strip() if ci < len(row) else ""
            if val:
                total_non_empty += 1
                norm = _normalize(val)
                # Check for ': ok' pattern (e.g., 'ee: ok', 'me: ok', 'dq: ok')
                if re.search(r":\s*ok", norm) and "nok" not in norm:
                    ok_count += 1
                elif re.search(r"\b(ok|okay)\b", norm) and "nok" not in norm and "not ok" not in norm:
                    ok_count += 1
        # A column is an assessment column if >10% of non-empty cells have ': ok' patterns
        if total_non_empty > 0 and ok_count / total_non_empty > 0.10:
            assessment_cols.add(ci)
    return assessment_cols


def _is_bare_domain_code(text: str) -> bool:
    """True when a comment is just a domain code ('SYS', 'SW', 'EE', 'ME', …)
    or a short list of them ('ME/EE', 'SYS | SW') rather than a real supplier
    comment. These are domain-ASSIGNMENT markers that pollute the comment field
    and make comment comparisons noisy."""
    norm = _normalize(text).strip()
    if not norm:
        return False
    # exact domain code
    if norm in _DOMAIN_CODES:
        return True
    # a short list of domain codes joined by | / , ; & or a space:
    # 'SYS | SW', 'ME/EE', 'VE, ME', 'EE ME', 'SYSTEM/SW'
    parts = [p for p in re.split(r"[\s|/,\u0026;]+", norm) if p]
    if parts and len(parts) <= 4 and all(p in _DOMAIN_CODES for p in parts):
        return True
    return False


def _clean_comments(comments: List[str]) -> List[str]:
    """Drop empty, version, and bare domain-code values from a comment list."""
    cleaned: List[str] = []
    for c in comments:
        if not c or not c.strip():
            continue
        if _is_bare_domain_code(c):
            continue
        cleaned.append(c.strip())
    return cleaned


# Category priority for combining multiple column sets (higher = worse)
_CATEGORY_PRIORITY = {
    "NOK": 6,
    "NA": 1,
    "EMPTY": 0,
    "OK": -1,
}


def _looks_like_conformity_value(norm: str) -> bool:
    """True when a normalized cell value looks like a conformity status
    (/, OK, NOK, NA, 'EE: ok', 'non conforme', …) rather than free text."""
    if norm in _OK_VALUES or norm in _NOK_VALUES or norm in _NA_VALUES:
        return True
    if norm in ("/", "ko→ok", "ko->ok"):
        return True
    if re.search(r":\s*(ok|nok|na|pass|fail|compliant|non.?compliant)\b", norm):
        return True
    if re.match(
        r"^(ok|nok|na|non\s*conform|conform|not\s*applicable|sans\s*objet|"
        r"pass(?:ed)?\b|fail(?:ed)?\b|compliant\b|non.?compliant\b|"
        r"does\s+not\s+(?:comply|conform)|meets?\b|not\s+met\b|"
        r"satisfied\b|not\s+satisfied\b|acceptable\b|not\s+acceptable\b|"
        r"approved\b|rejected\b|partial(?:ly)?\b|"
        r"satisf(?:y|ies|ied)\b|does\s+not\s+meet\b)",
        norm,
    ):
        return True
    return False


def _is_requirement_id(req_id: str) -> bool:
    """True for requirement-ID patterns (REQ-…, REF-…, APP-…, GEN-…).
    Document titles / category rows ('Allocation matrix of the LVDS…',
    'RETRO_511_001', 'GEN') do not match."""
    return bool(re.match(r"^(REQ|REF|APP|GEN)-", req_id or ""))


def _looks_like_gentex_requirement_id(req_id: str) -> bool:
    """Recognize the Gentex technical-specification IDs, not section titles."""
    value = (req_id or "").strip()
    return bool(re.match(r"^RETRO_\d+(?:_\d+)+$", value, re.IGNORECASE))


def _looks_like_version_value(norm: str) -> bool:
    """True when a normalized cell value looks like a version code rather
    than a comment: a single letter A-I, a 'v\\d' pattern, or a date."""
    if not norm:
        return False
    if re.fullmatch(r"[a-i]", norm):
        return True
    if re.match(r"^v?\d", norm):
        return True
    if re.match(r"^\d{1,2}[/-]\d{1,2}[/-]\d{2,4}", norm):
        return True
    return False


def _looks_like_req_id_value(norm: str) -> bool:
    """True when a normalized cell value looks like a requirement identifier
    (REQ-…, REF-…, APP-…, GEN-…) rather than a comment."""
    return bool(
        re.match(r"^(req|ref|app|gen)-", norm)
        or re.match(r"^[a-z][a-z0-9]{1,15}[-_/.]\d", norm)
        or re.match(r"^\d{2,}[-_]\d", norm)
        # Supplier identifiers often contain several alphabetic segments,
        # e.g. CR_RAMS_01, SAF-REQ-12, or IEC.61508.4. A stable all-ID column
        # profile (checked by the caller) distinguishes these from prose.
        or re.fullmatch(r"[a-z]{1,12}(?:[-_.][a-z0-9]{1,16}){1,5}[-_.]?\d{1,6}[a-z]?", norm)
    )


def _is_non_requirement_row_text(value: str) -> bool:
    """Identify document titles, summaries, and header labels in ID columns.

    A supplier matrix may repeat a merged cover-page statement down the
    requirement-ID column. Keep those labels out of the extracted item list
    even when the same row has a conformity value in a neighbouring column.
    """
    normalized = _normalize(value).strip(" :.-")
    if not normalized:
        return False
    exact_labels = {
        "requirement", "requirements", "requirement identifier",
        "requirement id", "requirement title", "requirement description",
        "requirement reference", "requirement status", "requirement summary",
    }
    if normalized in exact_labels:
        return True
    if normalized.startswith((
        "therefore the following requirements are applied",
        "the following requirements are applied",
        "requirements applied",
        "requirements ok", "requirements nok",
        "number of requirements", "total requirements",
    )):
        return True
    # Repeated merged titles/intro paragraphs are prose, not identifiers.
    if len(normalized) > 80 and not _looks_like_req_id_value(normalized):
        return True
    return False


def _detect_requirement_id_columns_by_content(
    sheet: List[List[str]], data_start: int, max_rows: int = 200
) -> List[int]:
    """Identify supplier-specific ID columns (e.g. RAMS-001) by value shape.

    Header labels are inconsistent across vendors, but IDs usually have a
    stable repeated token pattern. Require a clear majority so ordinary
    descriptions and numeric measurements do not become IDs.
    """
    n_cols = max((len(row) for row in sheet), default=0)
    matches: List[Tuple[int, float, int]] = []
    for ci in range(n_cols):
        total = 0
        ids = 0
        for row in sheet[data_start:min(data_start + max_rows, len(sheet))]:
            value = row[ci].strip() if ci < len(row) else ""
            if not value:
                continue
            total += 1
            if _looks_like_req_id_value(_normalize(value)):
                ids += 1
        if total >= 4 and ids / total >= 0.65:
            matches.append((ci, ids / total, ids))
    matches.sort(key=lambda candidate: (candidate[1], candidate[2]), reverse=True)
    return [ci for ci, _, _ in matches]


def _detect_description_columns_by_content(
    sheet: List[List[str]],
    data_start: int,
    excluded_cols: set[int],
    max_rows: int = 200,
) -> List[int]:
    """Infer a requirement-description column when the supplier renamed it.

    Descriptions tend to be longer prose than identifiers/statuses and, in
    conventional matrices, appear before the supplier's status column. Return
    candidates in confidence order; never reuse already identified ID/status
    columns.
    """
    n_cols = max((len(row) for row in sheet), default=0)
    candidates: List[Tuple[int, float, float]] = []
    for ci in range(n_cols):
        if ci in excluded_cols:
            continue
        values = [
            row[ci].strip()
            for row in sheet[data_start:min(data_start + max_rows, len(sheet))]
            if ci < len(row) and row[ci].strip()
        ]
        if len(values) < 3:
            continue
        prose = [
            value for value in values
            if len(value) >= 35
            and not _looks_like_conformity_value(_normalize(value))
            and not _looks_like_req_id_value(_normalize(value))
            and not _looks_like_version_value(_normalize(value))
        ]
        ratio = len(prose) / len(values)
        if ratio >= 0.65:
            average_length = sum(len(value) for value in prose) / len(prose)
            candidates.append((ci, ratio, average_length))
    candidates.sort(key=lambda candidate: (candidate[1], candidate[2]), reverse=True)
    return [ci for ci, _, _ in candidates]


def _detect_conformity_columns_by_content(sheet, data_start: int, max_rows: int = 200):
    """Find conformity/comment columns by their VALUES when the header names
    are non-standard (e.g. 'Engagement', 'Commitment', 'Comments').

    A column is a COMMENT column when most of its non-empty values are
    free text — NOT conformity statuses, NOT version codes, NOT requirement
    ids, and NOT long descriptions. This makes detection work for ANY header
    a supplier happens to use ('Remarques', 'Notes', 'Observations', …).
    """
    conformity_cols: List[int] = []
    comment_cols: List[int] = []
    n_cols = max((len(r) for r in sheet), default=0)
    profiles = []
    for ci in range(n_cols):
        total = 0
        conf = 0
        version = 0
        reqid = 0
        long_text = 0
        text = 0
        for ri in range(data_start, min(data_start + max_rows, len(sheet))):
            row = sheet[ri]
            val = row[ci].strip() if ci < len(row) else ""
            if not val:
                continue
            total += 1
            norm = _normalize(val)
            if _looks_like_conformity_value(norm):
                conf += 1
            elif _looks_like_version_value(norm):
                version += 1
            elif _looks_like_req_id_value(norm):
                reqid += 1
            elif len(val) > 80:
                long_text += 1
            elif len(val) > 3:
                text += 1
        profiles.append({
            "column": ci, "total": total, "conf": conf, "version": version,
            "reqid": reqid, "long_text": long_text, "text": text,
        })
        conf_ratio = conf / total if total else 0
        if total >= 3 and conf_ratio > 0.5:
            conformity_cols.append(ci)
        elif total >= 4 and conf_ratio > 0.25:
            # Some suppliers mix compact categorical answers with a minority
            # of explanations in the same status column. Still classify it
            # as a status field when categorical answers are the clear
            # majority of all observed values.
            conformity_cols.append(ci)
    status_right_edge = max(conformity_cols, default=-1)
    for profile in profiles:
        ci = profile["column"]
        total = profile["total"]
        text = profile["text"]
        long_text = profile["long_text"]
        # Free text after the detected status fields is more likely to be
        # supplier evidence/rationale than the requirement description. This
        # permits long evidence text when its header is unfamiliar while
        # protecting the common pre-status requirement text.
        if total >= 3 and ci > status_right_edge and profile["reqid"] / total < 0.25:
            if (text + long_text) / total > 0.5 and profile["version"] / total < 0.5:
                comment_cols.append(ci)
                continue
        if total >= 3 and (text + long_text) / total > 0.5:
            # A description column is mostly long paragraphs; a comment
            # column is mostly short/medium free text. Require the short
            # text to dominate so descriptions are never misread as comments.
            if long_text / total < 0.5 and text / total > 0.3 and ci > status_right_edge:
                comment_cols.append(ci)
    return conformity_cols, comment_cols


def extract_conformity_data(filepath: str, file_name: str = "") -> ConformityAnalysis:
    """
    Main entry point: read a spreadsheet and extract conformity data.

    Auto-detects:
    - The correct sheet (searches all sheets for Conformité FNR columns)
    - The header row
    - The conformity and comment columns (fuzzy matching, handles name changes)
    - The data start row

    Returns a ConformityAnalysis with all items, statistics, and inconsistencies.
    """
    analysis = ConformityAnalysis(file_name=file_name or filepath)

    sheet_names, sheets_data = read_spreadsheet(filepath)

    # Search all sheets for the one with Conformité FNR columns
    best_sheet_idx = -1
    best_header_row = -1
    best_col_mapping = None
    best_score = -1

    for si, sheet in enumerate(sheets_data):
        # Evaluate all plausible header rows on a sheet. A title/header band
        # may appear first (e.g. the top of a cover page); the correct table
        # header later in that same sheet has stronger requirement/data
        # evidence and should be allowed to compete.
        header_candidates: List[int] = []
        candidate_header_maps: Dict[int, Dict[str, List[int]]] = {}
        requirement_start_markers = [
            ri for ri, row in enumerate(sheet[:200])
            if any("debut exigences" in _normalize(cell)
                   or "debut exigence" in _normalize(cell)
                   or "requirements start" in _normalize(cell)
                   for cell in row if cell)
        ]
        requirement_region_start = requirement_start_markers[0] if requirement_start_markers else None
        matrix_candidate_window = range(min(50, len(sheet)))
        for candidate_row in matrix_candidate_window:
            # A merged group-title band (e.g. "Conformity Matrix") can sit
            # immediately above the real leaf headers. When the next row
            # already names the response/comment roles, do not let values
            # below the group band make that title row win as the header.
            if candidate_row + 1 < len(sheet):
                has_group_title = any(
                    "conformity matrix" in _normalize(cell)
                    or "matrice de conformite" in _normalize(cell)
                    for cell in sheet[candidate_row] if cell
                )
                next_row_map = _find_columns(sheet[candidate_row + 1])
                if (has_group_title
                        and (next_row_map.get("conformity")
                             or next_row_map.get("comment")
                             or next_row_map.get("stellantis_verdict"))):
                    continue
            candidate_map = _find_columns(sheet[candidate_row])
            merged_candidate = list(sheet[candidate_row])
            for context_idx in range(max(0, candidate_row - 4), candidate_row):
                context_row = sheet[context_idx]
                if not any(_match_any(cell, _CONFORMITY_PATTERNS)
                           or _match_any(cell, _COMMENT_PATTERNS)
                           or _match_any(cell, _STELLANTIS_VERDICT_PATTERNS)
                           for cell in context_row):
                    continue
                for ci, cell in enumerate(context_row):
                    if ci < len(merged_candidate) and cell.strip() and not merged_candidate[ci].strip():
                        merged_candidate[ci] = cell
            candidate_map = _find_columns(merged_candidate)
            # An actual requirement identifier header is stronger evidence
            # than broad words such as "requirement" in cover prose. Do not
            # allow generic title rows to compete as table headers.
            if any(
                _normalize(cell).strip() in (
                    "requirement identifier", "requirement id",
                    "req id", "req identifier",
                )
                for cell in merged_candidate if cell
            ):
                candidate_map["_concrete_req_id_header"] = [1]
            candidate_header_maps[candidate_row] = candidate_map
            row_has_id = any(
                _looks_like_req_id_value(_normalize(cell))
                for cell in sheet[candidate_row] if cell
            )
            if row_has_id:
                continue
            content_conf, _ = _detect_conformity_columns_by_content(
                sheet, candidate_row + 1, max_rows=80
            )
            if (candidate_map.get("conformity")
                    or candidate_map.get("comment")
                    or candidate_map.get("stellantis_verdict")
                    or content_conf):
                header_candidates.append(candidate_row)
        detected_header = _find_header_row(sheet)
        if detected_header is not None and detected_header not in header_candidates:
            header_candidates.append(detected_header)
        header_row = None
        header_row_score = -1
        for candidate_row in header_candidates:
            candidate_map = candidate_header_maps.get(candidate_row) or _find_columns(sheet[candidate_row])
            merged_candidate = list(sheet[candidate_row])
            for context_idx in range(max(0, candidate_row - 4), candidate_row):
                context_row = sheet[context_idx]
                if not any(_match_any(cell, _CONFORMITY_PATTERNS)
                           or _match_any(cell, _COMMENT_PATTERNS)
                           or _match_any(cell, _STELLANTIS_VERDICT_PATTERNS)
                           for cell in context_row):
                    continue
                for ci, cell in enumerate(context_row):
                    if ci < len(merged_candidate) and cell.strip() and not merged_candidate[ci].strip():
                        merged_candidate[ci] = cell
            candidate_map = _find_columns(merged_candidate)
            if any(
                _normalize(cell).strip() in (
                    "requirement identifier", "requirement id",
                    "req id", "req identifier",
                )
                for cell in merged_candidate if cell
            ):
                candidate_map["_concrete_req_id_header"] = [1]
            if (candidate_map.get("conformity")
                    and candidate_map.get("comment")
                    and candidate_map.get("stellantis_verdict")):
                candidate_map["_complete_header"] = [1]
            candidate_ids = _detect_requirement_id_columns_by_content(
                sheet, candidate_row + 1, max_rows=80
            )
            candidate_conf, _ = _detect_conformity_columns_by_content(
                sheet, candidate_row + 1, max_rows=80
            )
            if not candidate_map.get("req_id") and candidate_ids:
                candidate_map["req_id"] = candidate_ids[:1]
            candidate_stellantis = list(candidate_map.get("stellantis_verdict", []))
            candidate_map["stellantis_verdict"] = candidate_stellantis
            candidate_map["conformity"] = [
                ci for ci in candidate_map.get("conformity", [])
                if ci not in candidate_stellantis
                and not _match_any(merged_candidate[ci], _TEST_STATUS_PATTERNS)
            ]
            if not candidate_map["conformity"] and candidate_conf:
                candidate_map["conformity"] = [
                    ci for ci in candidate_conf
                    if ci not in candidate_stellantis
                    and not _match_any(merged_candidate[ci], _TEST_STATUS_PATTERNS)
                ]
            if not (candidate_map.get("conformity")
                    or candidate_map.get("stellantis_verdict")
                    or candidate_conf):
                continue
            score = (6 * int(bool(candidate_map.get("conformity") or candidate_conf))
                     + 4 * int(bool(candidate_map.get("comment")))
                     + 3 * int(bool(candidate_map.get("stellantis_verdict")))
                     + 5 * int(bool(candidate_map.get("req_id") or candidate_ids)))
            if candidate_map.get("_complete_header"):
                score += 8
            if candidate_map.get("_concrete_req_id_header"):
                score += 20
            if candidate_ids:
                score += min(len(candidate_ids), 3)
            # Prefer the actual table header over an earlier group title by
            # checking whether the immediately following rows look like
            # requirement records: an identifier plus supplier-like status.
            record_rows = 0
            id_columns = candidate_map.get("req_id", []) or candidate_ids
            status_columns = candidate_map.get("conformity", []) or candidate_conf
            for data_row in sheet[candidate_row + 1:candidate_row + 41]:
                has_id_value = any(
                    ci < len(data_row) and _looks_like_req_id_value(_normalize(data_row[ci]))
                    for ci in id_columns
                )
                has_status_value = any(
                    ci < len(data_row) and _looks_like_conformity_value(_normalize(data_row[ci]))
                    for ci in status_columns
                )
                if has_id_value and has_status_value:
                    record_rows += 1
            score += min(record_rows, 20)
            # Some supplier workbooks have a dense legend above the actual
            # matrix. The legend's status codes and IDs can mimic data rows,
            # but a structural "requirements start" marker identifies where
            # real records begin. Headers before that marker should not win.
            if requirement_region_start is not None:
                if candidate_row < requirement_region_start - 1:
                    score -= 100
                elif candidate_row == requirement_region_start - 1:
                    score += 12
            # A cover paragraph may be merged down a detected ID column and
            # adjacent to generic "Conformity"/"Comments" labels. Do not let
            # that repeated cover band become the selected header when a later
            # row has the concrete requirement identifier header.
            candidate_ids_after_header = sum(
                1 for data_row in sheet[candidate_row + 1:candidate_row + 41]
                if any(
                    ci < len(data_row)
                    and _looks_like_req_id_value(_normalize(data_row[ci]))
                    for ci in (candidate_map.get("req_id", []) or candidate_ids)
                )
            )
            if candidate_ids_after_header:
                score += min(candidate_ids_after_header, 20)
            elif candidate_map.get("req_id") and candidate_row + 1 < len(sheet):
                first_candidate_id = candidate_map["req_id"][0]
                next_value = (sheet[candidate_row + 1][first_candidate_id]
                              if first_candidate_id < len(sheet[candidate_row + 1]) else "")
                if next_value and not _looks_like_req_id_value(_normalize(next_value)):
                    score -= 20
            if score > header_row_score:
                header_row = candidate_row
                header_row_score = score
        if header_row is None:
            header_row = detected_header
        if header_row is None:
            continue
        col_mapping = dict(candidate_header_maps.get(header_row) or _find_columns(sheet[header_row]))
        merged_header_row = list(sheet[header_row])
        # Excel often stores field labels in vertically merged cells one row
        # above a finer subheader. Carry those labels down by column before
        # mapping, but preserve explicit child labels in the lower row.
        header_context_range = range(max(0, header_row - 4), header_row)
        concrete_leaf_header = False
        if requirement_region_start is not None:
            for context_idx in range(requirement_region_start - 1, max(-1, requirement_region_start - 6), -1):
                if context_idx < 0:
                    continue
                context_map = _find_columns(sheet[context_idx])
                if context_map.get("conformity") and (
                    context_map.get("comment") or context_map.get("stellantis_verdict")
                ):
                    header_row = context_idx
                    concrete_leaf_header = True
                    break
        if concrete_leaf_header:
            header_context_range = range(header_row, header_row)
            merged_header_row = list(sheet[header_row])
            col_mapping = _find_columns(merged_header_row)
            # The requirements marker may be followed by a document-title row
            # before the first REQ-ID. Use the actual ID-bearing row as the
            # classification profile start; otherwise the early legend rows
            # contaminate content inference and resemble extra status fields.
            first_requirement_row = next((
                ri for ri in range(requirement_region_start + 1, len(sheet))
                if any(_looks_like_req_id_value(_normalize(cell)) for cell in sheet[ri] if cell)
            ), None)
            profile_start = first_requirement_row if first_requirement_row is not None else header_row + 1
        else:
            profile_start = header_row + 1
        context_range = header_context_range
        for context_idx in header_context_range:
            context_row = sheet[context_idx]
            if not any(_match_any(cell, _CONFORMITY_PATTERNS)
                       or _match_any(cell, _COMMENT_PATTERNS)
                       or _match_any(cell, _STELLANTIS_VERDICT_PATTERNS)
                       for cell in context_row):
                continue
            for ci, cell in enumerate(context_row):
                if ci >= len(merged_header_row):
                    continue
                if cell.strip() and not merged_header_row[ci].strip():
                    merged_header_row[ci] = cell
        col_mapping = _find_columns(merged_header_row)
        # Header rows often span several rows: an upper band says
        # "Conformity Matrix" while the actual supplier verdict label lives
        # in a later row. Inspect up to three rows above the chosen header and
        # merge only role-specific labels into the mapping (without allowing
        # group titles or test-result columns to become primary answers).
        context_range = range(max(0, header_row - 3), header_row)
        if concrete_leaf_header:
            # This is already the concrete leaf-header row immediately above
            # the marker; earlier labels belong to group bands/summary rows.
            context_range = range(header_row, header_row)
        for context_idx in context_range:
            context_map = _find_columns(sheet[context_idx])
            for key in ("conformity", "comment", "stellantis_verdict", "req_id",
                        "version", "version_applicable", "description", "reference"):
                existing = set(col_mapping.get(key, []))
                col_mapping[key] = list(col_mapping.get(key, [])) + [
                    ci for ci in context_map.get(key, [])
                    if ci not in existing
                    and ci < len(merged_header_row)
                    and not merged_header_row[ci].strip()
                ]
        # Combined top-level labels such as "Conformité / Commentaires" can
        # identify a supplier field whose subheader is blank or generic. Use
        # content evidence to assign those columns, but avoid fields already
        # positively identified as Stellantis verdicts or test results.
        for context_idx in header_context_range:
            for ci, cell in enumerate(sheet[context_idx]):
                if (ci < len(merged_header_row)
                        and _match_any(cell, _CONFORMITY_PATTERNS)
                        and _match_any(cell, _COMMENT_PATTERNS)
                        and ci not in col_mapping.get("stellantis_verdict", [])
                        and ci not in col_mapping.get("conformity", [])):
                    profile = _detect_conformity_columns_by_content(
                        sheet, profile_start, max_rows=80
                    )
                    if ci in profile[0]:
                        col_mapping["conformity"].append(ci)
                    if ci in profile[1] and ci not in col_mapping.get("comment", []):
                        col_mapping["comment"].append(ci)
        col_mapping["stellantis_verdict"] = [
            ci for ci in col_mapping.get("stellantis_verdict", [])
            if ci not in col_mapping.get("comment", [])
        ]
        # On matrices with explicit supplier and customer fields, keep only
        # the primary supplier comment adjacent to the supplier conformity
        # column. Later test-delivery comments are not conformity rationale.
        if concrete_leaf_header:
            supplier_comment = [
                ci for ci in col_mapping.get("comment", [])
                if ci > min(col_mapping.get("conformity", [ci]))
                and ci < min(col_mapping.get("stellantis_verdict", [ci]))
            ]
            if supplier_comment:
                col_mapping["comment"] = supplier_comment
        col_mapping["comment"] = [
            ci for ci in col_mapping.get("comment", [])
            if not _match_any(merged_header_row[ci], _TEST_STATUS_PATTERNS)
        ]
        col_mapping["conformity"] = [
            ci for ci in col_mapping.get("conformity", [])
            if ci not in col_mapping.get("stellantis_verdict", [])
            and not _match_any(merged_header_row[ci], _TEST_STATUS_PATTERNS)
        ]
        conf_by_content, comm_by_content = _detect_conformity_columns_by_content(
            sheet, profile_start
        )
        if not col_mapping["conformity"] and conf_by_content:
            col_mapping["conformity"] = conf_by_content
        if col_mapping["conformity"]:
            col_mapping["conformity"] = [
                ci for ci in col_mapping["conformity"]
                if ci not in col_mapping.get("stellantis_verdict", [])
                and not _match_any(merged_header_row[ci], _TEST_STATUS_PATTERNS)
            ]
        if not col_mapping["comment"] and comm_by_content:
            blocked = set(col_mapping.get("conformity", []))
            col_mapping["comment"] = [ci for ci in comm_by_content if ci not in blocked]
        # When supplier-specific IDs have unfamiliar labels, identify them
        # from repeated value structure instead of assuming column A.
        if not col_mapping.get("req_id"):
            inferred_ids = _detect_requirement_id_columns_by_content(
                sheet, profile_start
            )
            if inferred_ids:
                col_mapping["req_id"] = inferred_ids[:1]
        # Score every viable sheet/header rather than accepting the first
        # mention of "conformity" in a cover page. Strong requirement IDs,
        # descriptions, and actual categorical answers identify the matrix;
        # sheet names and row content help keep help/audit tabs out.
        requirement_ids = _detect_requirement_id_columns_by_content(sheet, profile_start)
        if not col_mapping.get("req_id") and requirement_ids:
            col_mapping["req_id"] = requirement_ids[:1]
        requirement_density = 0
        id_col_candidates = col_mapping.get("req_id", []) or requirement_ids
        for rci in id_col_candidates:
            requirement_density = max(requirement_density, sum(
                1 for row in sheet[header_row + 1:header_row + 201]
                if rci < len(row) and _looks_like_req_id_value(_normalize(row[rci]))
            ))
        status_density = sum(
            1 for row in sheet[header_row + 1:header_row + 201]
            if any(ci < len(row) and _looks_like_conformity_value(_normalize(row[ci]))
                   for ci in col_mapping.get("conformity", [])
                   + col_mapping.get("stellantis_verdict", []))
        )
        if not col_mapping["conformity"] and not col_mapping.get("stellantis_verdict"):
            continue
        id_anchor = bool(col_mapping.get("req_id"))
        status_anchor = bool(col_mapping["conformity"] or col_mapping.get("stellantis_verdict"))
        if not id_anchor or not status_anchor:
            # Cover pages and instructional tabs often mention “conformity”
            # and contain free text, but lack an actual requirement-ID field.
            continue
        score = (10 * int(bool(col_mapping["conformity"]))
             + 8 * int(bool(col_mapping.get("stellantis_verdict")))
                 + 4 * int(bool(col_mapping["comment"]))
                 + 3 * int(bool(col_mapping.get("req_id")))
                 + 3 * int(bool(col_mapping.get("description")))
                 + min(requirement_density, 20)
                 + min(status_density, 20))
        sheet_label = _normalize(sheet_names[si] if si < len(sheet_names) else "")
        if any(marker in sheet_label for marker in ("help", "audit", "check", "config", "first page")):
            score -= 25
        if score > best_score:
            best_score = score
            best_sheet_idx = si
            best_header_row = header_row
            best_col_mapping = col_mapping
            analysis.sheet_name = sheet_names[si] if si < len(sheet_names) else f"Sheet_{si}"

    # Content-based fallback: when the header names are non-standard (e.g.
    # 'Engagement', 'Commitment', 'Comments'), detect the conformity and
    # comment columns from their VALUES instead of their names.
    if best_sheet_idx == -1 or best_col_mapping is None or not best_col_mapping["conformity"]:
        for si, sheet in enumerate(sheets_data):
            header_row = _find_header_row(sheet)
            data_start = (header_row + 1) if header_row is not None else 1
            conf_cols, comm_cols = _detect_conformity_columns_by_content(sheet, data_start)
            if not conf_cols:
                continue
            if header_row is None:
                header_row = 0
            col_mapping = _find_columns(sheet[header_row])
            col_mapping["conformity"] = conf_cols
            if comm_cols:
                col_mapping["comment"] = comm_cols
            best_sheet_idx = si
            best_header_row = header_row
            best_col_mapping = col_mapping
            analysis.sheet_name = sheet_names[si] if si < len(sheet_names) else f"Sheet_{si}"
            break

    if best_sheet_idx == -1 or best_col_mapping is None or not best_col_mapping["conformity"]:
        raise ValueError(
            "Could not find 'Conformité FNR' and 'Commentaires FNR' columns "
            "in any sheet of the spreadsheet."
        )

    sheet = sheets_data[best_sheet_idx]
    analysis.header_row = best_header_row
    analysis.column_mapping = best_col_mapping
    analysis.data_start_row = _find_data_start(sheet, best_header_row, best_col_mapping)
    inferred_ids = _detect_requirement_id_columns_by_content(
        sheet, analysis.data_start_row
    )
    if inferred_ids:
        # Value-shape evidence disambiguates broad labels such as "Requirement"
        # (often the description column) from the actual supplier ID column.
        best_col_mapping["req_id"] = inferred_ids[:1]
        analysis.column_mapping = best_col_mapping
    if not best_col_mapping.get("description"):
        excluded_for_description = set(best_col_mapping.get("req_id", []))
        excluded_for_description.update(best_col_mapping.get("conformity", []))
        excluded_for_description.update(best_col_mapping.get("comment", []))
        inferred_descriptions = _detect_description_columns_by_content(
            sheet, analysis.data_start_row, excluded_for_description
        )
        if inferred_descriptions:
            best_col_mapping["description"] = inferred_descriptions[:1]
            analysis.column_mapping = best_col_mapping

    # ── Intelligent content-based comment detection ─────────────────────
    # The header may name the comment column anything ('Remarques', 'Notes',
    # 'Observations', 'Feedback', …) or nothing at all. As a complement to
    # header matching, scan the DATA values: any column whose values are
    # mostly free text (not conformity statuses, version codes, requirement
    # ids, or long descriptions) is treated as a supplier-comment column.
    # This makes the agent work for ANY matrix a user uploads, without
    # requiring the exact header 'Commentaires FNR'.
    if best_col_mapping["comment"]:
        # Only add content-detected columns when the header found none —
        # the header names are the authoritative signal when present.
        pass
    else:
        conf_by_content, comm_by_content = _detect_conformity_columns_by_content(
            sheet, analysis.data_start_row
        )
        if comm_by_content:
            # Never treat conformity / version / req-id / description
            # columns as comments.
            excluded = set(best_col_mapping["conformity"]) \
                | set(best_col_mapping.get("version", [])) \
                | set(best_col_mapping.get("version_applicable", [])) \
                | set(best_col_mapping.get("req_id", [])) \
                | set(best_col_mapping.get("description", [])) \
                | set(best_col_mapping.get("reference", [])) \
                | set(best_col_mapping.get("ok_cols", [])) \
                | set(best_col_mapping.get("nok_cols", []))
            extra = [c for c in comm_by_content if c not in excluded]
            if extra:
                best_col_mapping["comment"] = extra
                analysis.column_mapping = best_col_mapping

    conformity_cols = best_col_mapping["conformity"]
    comment_cols = best_col_mapping["comment"]
    req_id_cols = best_col_mapping["req_id"]
    stellantis_verdict_cols = best_col_mapping.get("stellantis_verdict", [])
    version_cols = best_col_mapping.get("version", [])
    version_applicable_cols = best_col_mapping.get("version_applicable", [])
    description_cols = best_col_mapping.get("description", [])
    reference_cols = best_col_mapping.get("reference", [])
    ok_cols = best_col_mapping.get("ok_cols", [])
    nok_cols = best_col_mapping.get("nok_cols", [])
    # The description column ("Libellé de la dernière version de l'exigence")
    # also matches the req-id patterns ("exigence"/"requirement") — it must
    # never be treated as a requirement-id column.
    req_id_cols = [c for c in req_id_cols if c not in description_cols]
    best_col_mapping["req_id"] = req_id_cols
    analysis.column_mapping = best_col_mapping

    # Ignore generic preamble/header rows in the extraction range when a
    # reliable requirement-ID column has already been identified. This also
    # protects matrices whose cover-page text is repeated down a merged ID
    # column above the actual table header.
    if req_id_cols:
        first_real_id_row = next((
            ri for ri in range(analysis.data_start_row, len(sheet))
            if any(
                ci < len(sheet[ri])
                and _looks_like_req_id_value(_normalize(sheet[ri][ci]))
                for ci in req_id_cols
            )
        ), None)
        if first_real_id_row is not None:
            analysis.data_start_row = first_real_id_row

    # Detect the matrix format: Stellantis matrices use "REQ-…" requirement ids;
    # spec-style matrices (e.g. the ASU conformity matrix) use "REF-…"/"APP-…".
    # This decides how requirement rows are recognised (see the row filter and
    # the is_requirement flag below).
    uses_req_ids = False
    uses_supplier_ids = False
    for ri in range(analysis.data_start_row, min(analysis.data_start_row + 200, len(sheet))):
        row = sheet[ri]
        for rci in req_id_cols:
            if rci >= len(row) or not row[rci].strip():
                continue
            value = row[rci].strip()
            if value.startswith("REQ-"):
                uses_req_ids = True
                break
            if _looks_like_req_id_value(_normalize(value)):
                uses_supplier_ids = True
        if uses_req_ids:
            break

    # Heuristic: detect "comment" columns that actually contain version applicable data.
    # In some ODS files, the sub-header row labels the "Version applicable" column as
    # "Commentaires FNR", but the data is actually version letters (G, E, D, v4, etc.).
    # If a comment column has >60% version-like values (single letters A-I or v\d+),
    # reclassify it as a version_applicable column.
    # This runs regardless of whether version_applicable_cols is already detected,
    # because the ODS sub-header may mislabel multiple columns.
    if comment_cols:
        data_start = _find_data_start(sheet, best_header_row, best_col_mapping)
        for cmi in list(comment_cols):
            total_non_empty = 0
            version_like = 0
            for ri in range(data_start, min(data_start + 100, len(sheet))):
                row = sheet[ri]
                val = row[cmi].strip() if cmi < len(row) else ""
                if val:
                    total_non_empty += 1
                    cnorm = _normalize(val).strip()
                    if (len(cnorm) == 1 and cnorm in "abcdefghi") or re.match(r"^v\d+", cnorm):
                        version_like += 1
            if total_non_empty > 5 and version_like / total_non_empty > 0.60:
                # This comment column is actually a version applicable column
                comment_cols.remove(cmi)
                if cmi not in version_applicable_cols:
                    version_applicable_cols.append(cmi)

    # Heuristic: detect "conformity" columns that actually contain domain codes
    # (SYS, SW, VE, EE, etc.) rather than conformity values (/, OK, NOK).
    # In ODS files where the sub-header mislabels columns, a "Conformité FNR" column
    # in the second set may actually contain domain assignments (SYS, SW, VE).
    # If a conformity column has >50% domain-code values and <10% actual conformity
    # values (/, OK, NOK), reclassify it as a comment column.
    if len(conformity_cols) > 1:
        data_start = _find_data_start(sheet, best_header_row, best_col_mapping)
        for ci in list(conformity_cols):
            total_non_empty = 0
            domain_count = 0
            conformity_count = 0
            for ri in range(data_start, min(data_start + 100, len(sheet))):
                row = sheet[ri]
                val = row[ci].strip() if ci < len(row) else ""
                if val:
                    total_non_empty += 1
                    cnorm = _normalize(val).strip()
                    # Check if it's a domain code (SYS, SW, VE, EE, ME, etc.)
                    if cnorm in _DOMAIN_CODES or cnorm in ("sys", "sw", "ve", "ee", "me", "od", "opt", "cg", "tp"):
                        domain_count += 1
                    # Check if it's an actual conformity value
                    elif cnorm in _OK_VALUES or cnorm in _NOK_VALUES or cnorm in _NA_VALUES:
                        conformity_count += 1
            if total_non_empty > 5 and domain_count / total_non_empty > 0.50 and conformity_count / total_non_empty < 0.10:
                # This conformity column is actually a comment/domain column
                conformity_cols.remove(ci)
                if ci not in comment_cols:
                    comment_cols.append(ci)

    # Gentex-style format: separate OK and NOK columns
    # In this format, the conformity is determined by which column has a value:
    # if OK column is non-empty → OK, if NOK column is non-empty → NOK
    is_gentex_style = bool(ok_cols or nok_cols)

    # Determine the number of column sets (usually 2: first version + second version)
    # If no comment columns, use the number of conformity columns
    if comment_cols:
        num_sets = min(len(conformity_cols), len(comment_cols))
    else:
        num_sets = len(conformity_cols)

    # Detect which conformity columns are assessment columns (have ": ok" patterns)
    # This determines whether domain codes should be classified as NOK or EMPTY
    assessment_cols = _detect_assessment_columns(sheet, conformity_cols, analysis.data_start_row)

    # Find the last row with any data to avoid counting trailing empty rows
    last_data_row = analysis.data_start_row
    for ri in range(analysis.data_start_row, len(sheet)):
        row = sheet[ri]
        if row and any(c.strip() for c in row if c):
            last_data_row = ri

    # Rows up to the last non-empty row (trailing empty rows excluded)
    analysis.sheet_total_rows = last_data_row + 1

    # Extract data rows — ONE item per row (combining all column sets)
    items: List[ConformityItem] = []
    for ri in range(analysis.data_start_row, last_data_row + 1):
        row = sheet[ri]
        if not row:
            # Empty row within data range — count as EMPTY
            items.append(ConformityItem(
                row_index=ri, req_id="", conformity_category="EMPTY",
                is_requirement=False,
            ))
            continue
        if all(not c.strip() for c in row if c):
            # Completely empty row within data range — count as EMPTY
            items.append(ConformityItem(
                row_index=ri, req_id="", conformity_category="EMPTY",
                is_requirement=False,
            ))
            continue

        # Get requirement ID from detected identifier columns only. Falling
        # back to the first cell can turn a merged cover title or description
        # into the row's ID when a header-like cover band precedes the matrix.
        req_id = ""
        if req_id_cols:
            detected_id_values = []
            for rci in req_id_cols:
                if rci < len(row) and row[rci].strip():
                    detected_id_values.append(row[rci].strip())
            req_id = next(
                (value for value in detected_id_values
                 if _looks_like_req_id_value(_normalize(value))),
                detected_id_values[0] if detected_id_values else "",
            )
        if not req_id and not req_id_cols and row:
            req_id = row[0].strip() if row[0] else ""

        # Skip non-data rows (section headers, summary rows)
        if req_id and req_id.upper() in (
            "BLOCK", "CONVERGED", "NOT CONVERGED", "NO STELLANTIS ANSWER",
            "NO SUPPLIER ANSWER", "NB OF REQ. TO CONV.", "STANDBY",
            "DEVIATION", "OK", "NOK", "NA", "ATT_RESP@FULL", "ATT_RESP@SW",
            "ATT_RESP@DEV", "ATT_RESP@NONE", "ATT_RESP@INT", "ATT_RESP@PTF",
            "ATT_RESP@DEV_INT",
        ):
            continue
        if _is_non_requirement_row_text(req_id):
            continue
        if req_id and (req_id.startswith("Template") or req_id.startswith("STOP")):
            continue

        # Skip non-requirement rows: section headers (e.g., "3.2 Applicable documents"),
        # document names (e.g., "STLA DIAGNOSTIC REQUIREMENT STANDARD - UDS"),
        # category headers (e.g., "GEN"), and document references (e.g., "02017_...RSP-...").
        # Only create items for rows that have a REQ-ID OR conformity-related data.
        has_req_id = (
            req_id.startswith("REQ-") if uses_req_ids
            else (_looks_like_req_id_value(_normalize(req_id)) if uses_supplier_ids
                  else (_is_requirement_id(req_id) or _looks_like_gentex_requirement_id(req_id)))
        )
        if uses_supplier_ids and not has_req_id:
            # A generic "Requirement title"/"Description" header can be
            # misidentified as the ID column. Recover the actual supplier ID
            # from any other detected ID column before discarding the row.
            for rci in req_id_cols:
                if rci < len(row) and _looks_like_req_id_value(_normalize(row[rci])):
                    req_id = row[rci].strip()
                    has_req_id = True
                    break
        has_conformity_data = any(
            (row[ci].strip() if ci < len(row) else "")
            for ci in conformity_cols + stellantis_verdict_cols
        )
        if is_gentex_style and ok_cols and nok_cols:
            # Commitment/comment cells explain a decision; they do not mean a
            # supplier supplied an answer when both explicit checkboxes are
            # empty.
            has_conformity_data = has_conformity_data or any(
                ci < len(row) and row[ci].strip() for ci in ok_cols + nok_cols
            )
        elif not uses_supplier_ids:
            has_conformity_data = has_conformity_data or any(
                (row[ci].strip() if ci < len(row) else "") for ci in comment_cols
            )
        elif not has_req_id:
            # In a supplier-specific identifier matrix, a footer/note row with
            # free-text in the comment column is not a requirement record.
            continue
        if not has_req_id and not has_conformity_data:
            continue

        # Get reference — prefer a detected "Référence" column, else column B
        reference = ""
        if reference_cols:
            for rci in reference_cols:
                if rci < len(row) and row[rci].strip():
                    reference = row[rci].strip()
                    break
        if not reference:
            # Standard Stellantis allocation matrices commonly have the
            # reference in column B while the detected ID is in column F.
            # Some headers label B "Requirement title" or leave it generic,
            # so retain the value-shape fallback even when a header mapping
            # exists. A value equal to the requirement ID is not a reference.
            reference_candidates = [1]
            reference_candidates.extend(ci for ci in range(0, min(len(row), 8))
                                        if ci not in req_id_cols)
            for rci in dict.fromkeys(reference_candidates):
                if rci >= len(row):
                    continue
                candidate_reference = row[rci].strip()
                if (candidate_reference
                        and candidate_reference != req_id
                        and _looks_like_req_id_value(_normalize(candidate_reference))):
                    reference = candidate_reference
                    break
            # An explicit reference/title column may contain a human-readable
            # reference name rather than a coded identifier. Use it when no
            # coded reference was found in the conventional reference column.
            if not reference:
                for rci in reference_cols:
                    candidate_reference = row[rci].strip() if rci < len(row) else ""
                    if candidate_reference and candidate_reference != req_id:
                        reference = candidate_reference
                        break
            if not reference and len(row) > 2:
                # In allocation matrices, column B may contain only a dash
                # for requirements without a source-spec clause, while the
                # adjacent title column still carries their usable reference
                # label (for example, "Deserializer approved by Stellantis").
                title_fallback = row[2].strip()
                if (title_fallback and title_fallback != req_id
                    and len(title_fallback) >= 15
                    and " " in title_fallback
                        and (not description_cols or 2 not in description_cols)):
                    reference = title_fallback
        # Get description — prefer a detected description column (e.g.
        # "Libellé de la dernière version de l'exigence"), else col 5
        # (Stellantis format) or col 3 (Gentex format)
        description = ""
        if description_cols:
            for dci in description_cols:
                if dci < len(row) and row[dci].strip():
                    description = row[dci].strip()
                    break
        if not description:
            if len(row) > 5 and row[5].strip():
                description = row[5].strip()
            elif is_gentex_style and len(row) > 3 and row[3].strip():
                description = row[3].strip()

        # ── Gentex-style: OK/NOK separate columns ──
        # In this format, OK and NOK are separate columns.
        # If OK column has a value → classify it (could be OK, NA, etc.)
        # If NOK column has a value → classify it (could be NOK, NA, etc.)
        # NOK column takes priority if both present (but NA overrides both)
        if is_gentex_style:
            conf_raw = ""
            comment = ""
            best_category = "EMPTY"

            # Check OK columns first — classify the value properly
            ok_cat = None
            ok_raw = ""
            for ok_ci in ok_cols:
                ok_val = row[ok_ci].strip() if ok_ci < len(row) else ""
                if ok_val:
                    ok_raw = ok_val
                    ok_cat = classify_conformity(ok_val, is_assessment=False)
                    break

            # Check NOK columns — classify the value properly
            nok_cat = None
            nok_raw = ""
            for nok_ci in nok_cols:
                nok_val = row[nok_ci].strip() if nok_ci < len(row) else ""
                if nok_val:
                    nok_raw = nok_val
                    nok_cat = classify_conformity(nok_val, is_assessment=False)
                    break

            # Determine final category:
            # - NA takes highest priority (if either column says NA, it's NA)
            # - NOK takes priority over OK
            # - If both present, use the more severe (NOK > OK)
            if ok_cat == "NA" or nok_cat == "NA":
                best_category = "NA"
                conf_raw = ok_raw if ok_cat == "NA" else nok_raw
            if nok_cat == "NOK":
                best_category = "NOK"
                conf_raw = nok_raw
            elif ok_cat == "OK":
                best_category = "OK"
                conf_raw = ok_raw
            elif nok_cat:
                best_category = nok_cat
                conf_raw = nok_raw
            elif ok_cat:
                best_category = ok_cat
                conf_raw = ok_raw

            # Get comment from comment columns
            all_comments = []
            for cmi in comment_cols:
                cval = row[cmi].strip() if cmi < len(row) else ""
                if cval:
                    all_comments.append(cval)
            combined_comment = " | ".join(all_comments) if all_comments else ""

            # Get version — prioritize version_applicable columns over version columns
            version = ""
            if version_applicable_cols:
                for vci in version_applicable_cols:
                    if vci < len(row) and row[vci].strip():
                        version = row[vci].strip()
                        break
            if not version and version_cols:
                for vci in version_cols:
                    if vci < len(row) and row[vci].strip():
                        version = row[vci].strip()
                        break

            if conf_raw or combined_comment:
                item = ConformityItem(
                    row_index=ri,
                    req_id=req_id,
                    reference=reference,
                    description=description[:200],
                    conformity_raw=conf_raw,
                    conformity_category=best_category,
                    comment=combined_comment,
                    version=version,
                    column_set=0,
                    needs_review=(best_category == "EMPTY" and bool(combined_comment)),
                    classification_confidence="medium" if best_category == "EMPTY" else "high",
                    is_requirement=True,
                )
                items.append(item)
            continue

        # Collect conformity values from all column sets
        conf_values = []  # list of (raw_value, comment, column_index, set_idx)
        for set_idx in range(num_sets):
            ci = conformity_cols[set_idx]
            cmi = comment_cols[set_idx] if set_idx < len(comment_cols) else -1

            conf_raw = row[ci].strip() if ci < len(row) else ""
            comment = row[cmi].strip() if cmi >= 0 and cmi < len(row) else ""

            if conf_raw or comment:
                conf_values.append((conf_raw, comment, ci, set_idx))

        # Also collect from unpaired comment columns (ODS reclassified columns)
        paired_comment_indices = set()
        for set_idx in range(num_sets):
            if set_idx < len(comment_cols):
                paired_comment_indices.add(comment_cols[set_idx])
        for cmi in comment_cols:
            if cmi not in paired_comment_indices:
                cval = row[cmi].strip() if cmi < len(row) else ""
                if cval:
                    conf_values.append(("", cval, cmi, len(conf_values)))

        # Create item even if no conformity values (as long as we have a reqId)
        # This ensures all data rows are counted in the total

        # Determine the overall conformity category
        if not conf_values:
            # No conformity or comment values in the paired columns.
            # But we still need to check Stellantis verdict columns —
            # the Environmental Technical Specification row has no supplier
            # conformity data but Stellantis marked it as OK.
            # Fall through to the Stellantis verdict check below instead
            # of creating an EMPTY item immediately.
            pass
        classified = []  # list of (conf_raw, comment, cat, set_idx)
        for conf_raw, comment, ci, set_idx in conf_values:
            is_assessment = ci in assessment_cols
            cat = classify_conformity(conf_raw, is_assessment=is_assessment)
            classified.append((conf_raw, comment, cat, set_idx))

        # Determine overall category:
        # - If there are assessment columns with values, use worst category across assessment columns
        # - If NO assessment columns, use the FIRST non-empty conformity value (primary column)
        # - This ensures domain assignment columns don't override the primary conformity status
        assessment_cats = [(r, c, cat, si) for (r, c, cat, si) in classified
                           if si < len(conformity_cols) and conformity_cols[si] in assessment_cols]
        non_assessment_cats = [(r, c, cat, si) for (r, c, cat, si) in classified
                               if si < len(conformity_cols) and conformity_cols[si] not in assessment_cols and r]

        # ── STELLANTIS verdict columns (highest priority) ──
        # "Commentaires STELLANTIS" / "Statut STELLANTIS" columns contain the
        # Stellantis-side OK/NOK verdict. If any says NOK, the item is NOK.
        # If any says OK (and no NOK), the item is OK.
        stellantis_nok = False
        stellantis_nok_raw = ""
        stellantis_ok = False
        stellantis_ok_raw = ""
        for sci in stellantis_verdict_cols:
            sval = row[sci].strip() if sci < len(row) else ""
            if sval:
                snorm = _normalize(sval)
                if snorm in _NOK_VALUES or snorm.startswith("nok"):
                    stellantis_nok = True
                    stellantis_nok_raw = sval
                    break
                if not stellantis_ok and (snorm in _OK_VALUES or re.search(r"\bok\b", snorm)):
                    if "nok" not in snorm and "not ok" not in snorm:
                        stellantis_ok = True
                        stellantis_ok_raw = sval

        if stellantis_nok:
            # Stellantis verdict NOK overrides everything
            best_conf_raw = stellantis_nok_raw
            best_comment = ""
            best_category = "NOK"
            best_set_idx = 0
        elif non_assessment_cats:
            # Non-assessment (primary) columns take priority — they contain the
            # definitive conformity status (e.g., "/" = OK).
            # Use the FIRST non-empty value (primary column).
            best = non_assessment_cats[0]
            best_conf_raw = best[0]
            best_comment = best[1]
            best_category = best[2]
            best_set_idx = best[3]
            # ── Stellantis OK fallback ──
            # If the non-assessment column only has a domain code (EMPTY), but
            # Stellantis verdict says OK, use the Stellantis OK.
            # This fixes rows like the Environmental Technical Specification
            # where the supplier conformity is a domain code (e.g., 'VE')
            # but Stellantis marked it as OK.
            if best_category == "EMPTY" and stellantis_ok:
                best_conf_raw = stellantis_ok_raw
                best_comment = best_comment  # keep the original comment
                best_category = "OK"
                best_set_idx = 0
        elif assessment_cats:
            # No primary column value — use assessment columns
            best = max(assessment_cats, key=lambda x: _CATEGORY_PRIORITY.get(x[2], 0))
            best_conf_raw = best[0]
            best_comment = best[1]
            best_category = best[2]
            best_set_idx = best[3]
            # ── Stellantis OK fallback ──
            # Same fallback as above for assessment columns
            if best_category == "EMPTY" and stellantis_ok:
                best_conf_raw = stellantis_ok_raw
                best_comment = best_comment  # keep the original comment
                best_category = "OK"
                best_set_idx = 0
        elif stellantis_ok:
            # No supplier conformity value, but Stellantis says OK
            best_conf_raw = stellantis_ok_raw
            best_comment = ""
            best_category = "OK"
            best_set_idx = 0
        else:
            best_conf_raw = ""
            best_comment = ""
            best_category = "EMPTY"
            best_set_idx = 0

        # Extract version applicable — PRIORITIZE "Version applicable" columns
        # over "Version" (document version) columns.
        # The "Version applicable" column (e.g., Col H in Stellantis format)
        # contains the supplier's applicable version, which is what the user wants.
        # The "Version" column (e.g., Col D) contains the document version.
        version = ""
        # 1st priority: version_applicable columns ("Version applicable / Applicable version")
        if version_applicable_cols:
            for vci in version_applicable_cols:
                if vci < len(row) and row[vci].strip():
                    version = row[vci].strip()
                    break
        # 2nd priority: version columns (document version — "Version / Version")
        if not version and version_cols:
            for vci in version_cols:
                if vci < len(row) and row[vci].strip():
                    version = row[vci].strip()
                    break
        # 3rd priority: extract from supplier comment (version is sometimes
        # duplicated in the comment column, especially in ODS files where
        # the "Version applicable" column is mislabeled as "Commentaires FNR")
        if not version and conf_values:
            for conf_raw, comment_val, ci, set_idx in conf_values:
                if comment_val and comment_val.strip():
                    cnorm = _normalize(comment_val).strip()
                    # Single letter version codes (A-I) or version patterns (v1.0, v34.0)
                    if (len(cnorm) == 1 and cnorm in "abcdefghi") or re.match(r"^v\d+", cnorm):
                        version = comment_val.strip()
                        break

        # Combine all comments from all column sets, filtering out version values
        # and bare domain codes (the version is often duplicated in the comment
        # column, and domain-assignment markers like 'SYS'/'SW' are not real
        # comments).
        all_comments = []
        for (_, c, _, _) in conf_values:
            if c and c.strip():
                # Skip if this comment is just the version value (e.g., 'G', 'v1.0')
                if version and c.strip() == version:
                    continue
                # Also skip if the comment is just a single letter (version code)
                # that matches the version
                cnorm = _normalize(c).strip()
                if version and cnorm == _normalize(version).strip():
                    continue
                all_comments.append(c.strip())
        # Note: unpaired comment columns are already in conf_values (added above),
        # so they are collected in the loop above. No separate collection needed.
        # Drop empty values, then dedupe.  Domain codes ('SYS', 'SW', …) are KEPT
        # — they ARE the supplier's comment (a domain assignment).  The deep-OK
        # analysis has its own filter that skips them when looking for suspicious
        # signals, but the detailed table must show every comment the supplier wrote.
        all_comments = [c for c in all_comments if c.strip()]
        # Deduplicate comments (ODS may have same value in multiple comment columns)
        seen = set()
        unique_comments = []
        for c in all_comments:
            if c not in seen:
                seen.add(c)
                unique_comments.append(c)
        combined_comment = " | ".join(unique_comments) if unique_comments else best_comment
        # Filter best_comment if it's just the version (not a domain code)
        if best_comment and (
            best_comment.strip() == (version or "")
            or _normalize(best_comment).strip() == _normalize(version).strip()
        ):
            combined_comment = " | ".join(unique_comments) if unique_comments else ""

        # Determine if this item needs manual review and its confidence level
        needs_review = False
        confidence = "high"
        if best_category == "EMPTY" and combined_comment:
            # Empty conformity but has a comment — might need review
            needs_review = True
            confidence = "medium"

        item = ConformityItem(
            row_index=ri,
            req_id=req_id,
            reference=reference,
            description=description[:200],
            conformity_raw=best_conf_raw,
            conformity_category=best_category,
            comment=combined_comment,
            version=version,
            column_set=best_set_idx,
            needs_review=needs_review,
            classification_confidence=confidence,
            is_requirement=(
                req_id.startswith("REQ-") if uses_req_ids
                else (_looks_like_req_id_value(_normalize(req_id)) if uses_supplier_ids
                      else (_is_requirement_id(req_id) or _looks_like_gentex_requirement_id(req_id)))
            ),
        )
        items.append(item)

    analysis.items = items
    analysis.total_rows = len(items)

    # Compute statistics
    stats: Dict[str, int] = {}
    for item in items:
        cat = item.conformity_category
        stats[cat] = stats.get(cat, 0) + 1
    analysis.stats = stats

    return analysis


# ── AI inconsistency detection ──────────────────────────────────────

# ── Negative-signal patterns (contradict OK status) ──
# Each pattern is (regex, label, weight) — weight contributes to a severity score.
_NEGATIVE_SIGNALS: List[tuple] = [
    # Direct negation of conformity (allow optional 'be' between not and verb)
    (r"\bnot?\s*(?:be\s+)?(ok|conform|test|implement|done|complete|met|satisf)",
     "negated_conformity", 3),
    (r"\bnok\b", "nok", 3),
    (r"\bko\b", "ko", 3),
    (r"\bfail(ed|ure)?\b", "fail", 3),
    (r"\berror\b", "error", 2),
    (r"\bdefect\b", "defect", 3),
    (r"\bbug\b", "bug", 2),
    (r"\bbroken\b", "broken", 3),
    (r"\bmissing\b", "missing", 2),
    (r"\bincomplete\b", "incomplete", 3),
    (r"\bnon\s*(conform|ok|test|verif|implement)\b", "non_conformity", 3),

    # Cannot / unable / impossible / can not
    (r"\b(cannot|can'?t|can\s+not|unable|impossible|no\s+way|no\s+solution)\b",
     "cannot", 3),
    (r"\b(pas\s+(possible|capable|en\s+mesure))\b", "fr_cannot", 3),

    # Does not meet / comply / satisfy
    (r"\b(does\s+not|do\s+not|doesn'?t|don'?t)\s*(meet|comply|satisf|fulfil)",
     "not_meet", 3),
    (r"\bnot\s+(met|satisfied|achieved|fulfilled|compliant)\b", "not_met", 3),

    # Problem / issue / concern / risk
    (r"\b(problem|issue|concern|risk|trouble|difficulty)\b", "problem", 2),
    (r"\b(problème|souci|préoccupation|risque)\b", "fr_problem", 2),

    # Pending / waiting / blocked / not ready
    (r"\b(pending|wait|block|not\s+ready|not\s+done|not\s+available)\b",
     "pending", 2),
    (r"\b(en\s+cours|en\s+attente|à\s+venir|à\s+faire|à\s+vérifier|à\s+confirmer|à\s+définir)\b",
     "fr_pending", 2),

    # Deviation / derogation / waiver / exception
    (r"\b(deviation|derogation|waiver|exception|exemption|dispense)\b",
     "deviation", 2),
    (r"\b(dérogation|écart|dispense)\b", "fr_deviation", 2),

    # Partial / limited / workaround
    (r"\b(partial|partially|limitation|limited|workaround|interim|temporary)\b",
     "partial", 2),
    (r"\b(partiel|partielle|limité|limitée|solution\s+de\s+rechange)\b",
     "fr_partial", 2),

    # TODO / TBD / TBA / to be defined
    (r"\b(todo|tbd|tba|to\s+be\s+(defined|confirmed|determined|verified|checked))\b",
     "todo", 2),

    # Conflict / contradiction / mismatch / gap
    (r"\b(conflict|contradict|mismatch|discrepancy|gap|inconsisten)\b",
     "conflict", 2),
    (r"\b(conflit|contradiction|écart|incohérent|incohérence)\b",
     "fr_conflict", 2),

    # Rejected / reject
    (r"\b(reject(ed|ion)?|refus(ed|al)?)\b", "rejected", 3),
    (r"\b(refus(é|ée|er))\b", "fr_rejected", 3),

    # Still / remaining / outstanding
    (r"\b(still|remaining|outstanding|not\s+yet)\b", "remaining", 1),
    (r"\b(reste|encore|pas\s+encore)\b", "fr_remaining", 1),

    # Under review / investigation
    (r"\b(under\s+(review|investigation)|being\s+(reviewed|investigated))\b",
     "under_review", 2),

    # French: pas conforme / pas ok / pas terminé
    (r"\bpas\s+(conforme|ok|termin|fait|prêt|pret|valid)\b", "fr_pas", 3),
    (r"\bnon\s*conforme\b", "fr_non_conforme", 3),

    # Instead of / replaced by (supplier proposes alternative — may not meet original req)
    (r"\b(instead\s+of|replaced\s+by|substitut)\b", "instead_of", 1),

    # No X (negation of key nouns)
    (r"\bno\s+(cybersecurity|security|safety|solution|way|support|capability)\b",
     "no_noun", 2),

    # N/A or not applicable in comment when status is OK (not NA)
    (r"\b(n/?a|not\s+applicable|non\s+applicable|hors\s+périmètre|hors\s+scope)\b",
     "na_in_ok", 2),

    # Delay / late / retard
    (r"\b(delay|late|overdue|retard)\b", "delay", 1),
]

# ── Positive-signal patterns (contradict NOK status) ──
# Each pattern is (regex, label, weight).
_POSITIVE_SIGNALS: List[tuple] = [
    (r"\bok\b", "ok", 2),
    (r"\bconform(e|ed)?\b", "conform", 2),
    (r"\bgood\b", "good", 1),
    (r"\bpass(ed|ing)?\b", "pass", 2),
    (r"\bdone\b", "done", 1),
    (r"\bcompleted\b", "complete", 1),
    (r"\bfinish(ed|ing)?\b", "finish", 1),
    (r"\bready\b", "ready", 1),
    (r"\bvalidated\b", "valid", 2),
    (r"\bconforme\b", "fr_conforme", 2),
    (r"\btermin(é|ée|er|e)\b", "fr_termin", 1),
    (r"\bfait\b", "fr_fait", 1),
    (r"\bprêt|pret\b", "fr_pret", 1),
    (r"\bvalide\b", "fr_valide", 2),
    (r"\bsatisf(ied|ies|action)\b", "satisfied", 2),
    (r"\bmeets\b", "meets", 1),
    (r"\bmeeting\s+(the|this|all|requirement|target|spec)", "meets", 1),
    (r"\bcompl(ies|ied|iant)\b", "compliant", 2),
    (r"\bno\s+(issue|problem|defect|error)\b", "no_issue", 2),
]

# ── Domain codes that are NOT real comments (should not trigger inconsistency) ──
_DOMAIN_CODE_RE = re.compile(
    r"^(sys|sw|ve|ee|me|od|opt|cg|tp|hw|mech|dq|fusa|ipm|all)"
    r"(\s*/\s*(sys|sw|ve|ee|me|od|opt|cg|tp|hw|mech|dq|fusa|ipm|all))*\s*$",
    re.IGNORECASE,
)

# ── Patterns that neutralise a positive signal (context matters) ──
# e.g., "not ok" should NOT count as positive "ok"
_POSITIVE_NEUTRALISER = re.compile(
    r"\b(not?|non|pas|no)\s+"
    r"(ok|conform|conforme|good|pass|done|complete|finish|ready|valid|"
    r"termin|fait|prêt|pret|valide|satisf|meet|compl)",
    re.IGNORECASE,
)


def _score_comment(comment: str, signals: List[tuple]) -> List[tuple]:
    """
    Score a comment against a list of signal patterns.
    Returns a list of (label, weight, matched_text) for all matches.
    """
    matches: List[tuple] = []
    cnorm = _normalize(comment)
    for pattern, label, weight in signals:
        m = re.search(pattern, cnorm)
        if m:
            matches.append((label, weight, m.group()))
    return matches


def detect_inconsistencies(analysis: ConformityAnalysis) -> List[Dict]:
    """
    Detect logical non-coherence in supplier OK responses.

    FOCUS: Only analyze items where the supplier marked 'OK' — check if the
    comment contradicts the OK status (e.g. comment describes non-conformity
    while status is OK). This is the core non-coherence pattern that matters:
    status=OK but comment tells a different story.

    Checks performed (OK-items only):
    - status=OK but comment contains negative/non-conform language
    - status=OK but comment mentions N/A or not applicable
    - status=OK but no comment provided

    LLM check (optional): deeper semantic analysis of comment vs status.
    """
    inconsistencies: List[Dict] = []

    for item in analysis.items:
        cat = item.conformity_category
        comment = item.comment.strip()
        conf_raw = item.conformity_raw.strip()

        # Only analyze OK items — the focus is on supplier-declared OK
        # with comments that may reveal hidden non-conformity.
        if cat != "OK":
            continue

        # Skip pure domain-code comments (SYS, SW, VE, EE, etc.)
        is_domain_only = bool(_DOMAIN_CODE_RE.match(comment)) if comment else False

        issue = None

        # ── Check A: OK status but negative/non-conform comment ──
        # This is the CORE non-coherence detection: the supplier says OK
        # but the comment language suggests otherwise.
        if comment and not is_domain_only:
            neg_matches = _score_comment(comment, _NEGATIVE_SIGNALS)
            if neg_matches:
                score = sum(w for _, w, _ in neg_matches)
                labels = ", ".join(sorted(set(l for l, _, _ in neg_matches)))
                matched_texts = [t for _, _, t in neg_matches]
                severity = "error" if score >= 4 else "warning"
                issue = {
                    "type": "OK_NEGATIVE_COMMENT",
                    "severity": severity,
                    "req_id": item.req_id,
                    "conformity": conf_raw,
                    "comment": comment,
                    "score": score,
                    "signals": labels,
                    "matched": matched_texts,
                    "explanation": (
                        f"The supplier marked '{conf_raw}' (OK) but the "
                        f"comment contains negative or non-conforming language "
                        f"(signals: {labels}, score: {score}): "
                        f"'{comment[:200]}'. Logical inconsistency — the comment "
                        f"does not match the declared OK status."
                    ),
                }

        # ── Check B: OK status but comment mentions N/A or not applicable ──
        # Logical gap: if it's not applicable, why is it marked OK?
        if comment and not is_domain_only and not issue:
            na_match = re.search(
                r"\b(n/?a|not\s+applicable|non\s+applicable|hors\s+périmètre|hors\s+scope)\b",
                _normalize(comment),
            )
            if na_match:
                issue = {
                    "type": "OK_NA_COMMENT",
                    "severity": "warning",
                    "req_id": item.req_id,
                    "conformity": conf_raw,
                    "comment": comment,
                    "signals": "na_in_ok",
                    "matched": [na_match.group()],
                    "explanation": (
                        f"The supplier marked '{conf_raw}' (OK) but the "
                        f"comment mentions 'N/A' or 'not applicable': "
                        f"'{comment[:200]}'. Inconsistency — if the requirement "
                        f"is not applicable, the OK status is not coherent."
                    ),
                }

        # ── Check C: OK status but no comment ──
        # Warning only — OK without explanation is not a logical contradiction
        # but reduces auditability.
        if not comment and item.column_set == 0:
            if not re.search(r"\bok\b", _normalize(conf_raw)):
                issue = {
                    "type": "OK_NO_COMMENT",
                    "severity": "warning",
                    "req_id": item.req_id,
                    "conformity": conf_raw,
                    "comment": "",
                    "explanation": (
                        f"The supplier marked '{conf_raw}' (OK) without a "
                        f"comment. A comment justifying the conformity "
                        f"is recommended for auditability."
                    ),
                }

        if issue:
            inconsistencies.append(issue)

    analysis.inconsistencies = inconsistencies
    return inconsistencies


# ── Deep OK analysis (FNR says OK, but comment looks suspicious) ───

# Patterns that, when found in an OK item's comment, suggest hidden non-conformity.
# These go beyond simple negative keywords — they detect ambiguous or worrying
# language that warrants human review.
_OK_SUSPICION_PATTERNS: List[tuple] = [
    # Pending / to be confirmed
    (r"\bto\s+be\s+(confirmed|defined|determined|verified|checked|decided|tested)\b",
     "pending_confirmation", 3),
    (r"\b(in\s+development|in\s+progress|en\s+cours|à\s+confirmer|à\s+définir|à\s+vérifier)\b",
     "in_development", 2),

    # Need / require further action
    (r"\b(need(s|ed)?\s+(to|further|more|additional|clarification|review|check|confirm|investigation|discuss)|"
     r"require(s|d)?\s+(further|more|clarification|review|confirmation)|"
     r"gentex\s+to\s+(check|confirm|provide|review|explain|verify))",
     "needs_action", 2),
    (r"\bstla\s+to\s+(check|confirm|provide|review|quantify|decide|define)",
     "stla_action", 2),
    (r"\b(please\s+(provide|clarify|confirm|check)|"
     r"merci\s+de\s+(confirmer|vérifier|préciser|clarifier))",
     "please_clarify", 2),

    # Conformity depends on a future action / document / decision
    (r"\bwill\s+(base|depend|rely)\s+on\b|\bwill\s+follow\b|\bwill\s+use\s+on\b",
     "will_depend", 2),
    (r"\b(is|are|was|were|be)\s+needed\b",
     "needed", 2),

    # Not applicable / out of scope but marked OK
    (r"\b(not\s+applicable|non\s+applicable|n/?a|hors\s+scope|hors\s+périmètre|"
     r"not\s+in\s+scope|no\s+cybersecurity|no\s+solution)",
     "na_language", 3),

    # Instead of / replaced by / deviation
    (r"\b(instead\s+of|replaced\s+by|substitut|deviation|derogation|"
     r"dérogation|waiver|workaround|alternate|alternative)\b",
     "alternative_approach", 2),

    # Not responsible / not our scope
    (r"\b(not\s+responsible|not\s+our\s+scope|not\s+in\s+scope|"
     r"pas\s+responsable|pas\s+de\s+notre\s+ressort)\b",
     "not_responsible", 3),

    # Conflict / contradiction
    (r"\b(conflict|contradiction|incompatible|inconsistent|"
     r"conflit|incohérent)\b",
     "conflict", 2),

    # Partial / limited / under review
    (r"\b(partial|partially|limited\s+to|only\s+for|except\s+for|"
     r"under\s+review|under\s+investigation|"
     r"partiel|partielle|limité\s+à)\b",
     "partial_limited", 2),

    # Cannot / unable
    (r"\b(cannot|can'?t|can\s+not|unable\s+to|not\s+possible|impossible|"
     r"pas\s+possible|impossible\s+de)\b",
     "cannot", 3),

    # Follow same with / same as (may be OK but needs verification)
    (r"\b(follow\s+(same|the\s+same)\s+(as|with)|same\s+as\s+previous)",
     "follow_same", 1),

    # Exception / unless
    (r"\b(exception|unless|sauf|sous\s+réserve|under\s+condition|provided\s+that)\b",
     "conditional", 1),

    # Temporarily / interim / for now
    (r"\b(temporar|interim|for\s+now|provisional|provisoire|temporaire)\b",
     "temporary", 2),

    # Target is X (suggests target not yet met)
    (r"\btarget\s+is\b", "target_is", 1),

    # Risk / concern
    (r"\b(risk|concern|attention|caution|warning|"
     r"risque|préoccupation|attention\s+à)\b",
     "risk_concern", 1),

    # Remaining / outstanding / still
    (r"\b(still\s+(to|need|pending|missing|remaining|outstanding)|"
     r"reste\s+à|encore\s+à)\b",
     "remaining", 2),

    # Discuss / discussion / meeting
    (r"\b(discuss(ed|ion)?\s+(in|with|needed|required)|"
     r"meeting\s+to\s+be\s+organized|"
     r"discuté|à\s+discuter)\b",
     "discussion_needed", 1),

    # Should / shall / must (normative but in comment means not yet done)
    (r"\b(should\s+be|shall\s+be|must\s+be|to\s+be\s+checked|"
     r"devrait\s+être|doit\s+être)\b",
     "normative_future", 1),

    # Rejected / refusal
    (r"\b(reject|refus|dismiss|decline|réfus)\b",
     "rejected", 3),

    # ── Additional patterns from negative signal detection ──
    # Fail / defect / bug / broken (severity: 3 — strong non-conformity signals)
    (r"\b(fail(ed|ure)?|defect|bug|broken)\b",
     "fail_defect", 3),

    # Missing / incomplete (severity: 2)
    (r"\b(missing|incomplete)\b",
     "missing_incomplete", 2),

    # Does not meet / comply / satisfy (severity: 3)
    (r"\b(does\s+not|do\s+not|doesn'?t|don'?t)\s*(meet|comply|satisf|fulfil)",
     "not_meet", 3),
    (r"\bnot\s+(met|satisfied|achieved|fulfilled|compliant)\b",
     "not_met", 3),

    # Non-conformity prefixes (non conform, non ok, non test, not ok)
    (r"\bnon\s*(conform|ok|test|verif|implement)\b",
     "non_conformity", 3),
    (r"\bnot?\s*(?:be\s+)?(ok|conform|test|implement|done|complete|met|satisf)\b",
     "negated_conformity", 3),

    # French: pas conforme / pas ok / pas terminé / pas fait / pas prêt / pas validé
    (r"\bpas\s+(conforme|ok|termin|fait|prêt|pret|valid)\b",
     "fr_pas_conforme", 3),
    (r"\bnon\s*conforme\b",
     "fr_non_conforme", 3),
    (r"\b(pas\s+(possible|capable|en\s+mesure))\b",
     "fr_cannot", 3),

    # Problème / souci (French problem/issue)
    (r"\b(problème|souci|préoccupation)\b",
     "fr_problem", 2),

    # Deviation / derogation / waiver (French)
    (r"\b(dérogation|écart|dispense)\b",
     "fr_deviation", 2),

    # Partial / limited (French)
    (r"\b(partiel|partielle|limité|limitée|solution\s+de\s+rechange)\b",
     "fr_partial", 2),

    # Conflict / contradiction (French)
    (r"\b(conflit|contradiction|incohérent|incohérence)\b",
     "fr_conflict", 2),

    # Refusé / refusée (French rejected)
    (r"\b(refus(é|ée|er))\b",
     "fr_rejected", 3),

    # Delay / late / retard
    (r"\b(delay|late|overdue|retard)\b",
     "delay", 1),

    # Under review / investigation
    (r"\b(under\s+(review|investigation)|being\s+(reviewed|investigated))\b",
     "under_review", 2),

    # TODO / TBD / TBA
    (r"\b(todo|tbd|tba)\b",
     "todo", 1),

    # No cybersecurity / no safety / no solution / no support / no capability
    (r"\bno\s+(cybersecurity|security|safety|solution|way|support|capability)\b",
     "no_noun", 2),

    # No guarantee / no warranty — the supplier does not commit to conformity
    (r"\bno\s+guarantee\b",
     "no_guarantee", 3),

    # Still / remaining / outstanding (single words, broader)
    (r"\b(still|remaining|outstanding|not\s+yet)\b",
     "still_outstanding", 1),
    (r"\b(reste|encore|pas\s+encore)\b",
     "fr_remaining", 1),

    # Pending / waiting / blocked / not ready / not done / not available
    (r"\b(pending|wait|block|not\s+ready|not\s+done|not\s+available|"
     r"not\s+\w+(?:\s+\w+)?\s+yet)\b",
     "pending", 2),

    # KO (French rejection)
    (r"\bko\b", "ko", 3),

    # Error
    (r"\berror\b", "error", 2),
]


def _generate_ai_comment_ok(
    comment: str, matches: List[tuple], item_conformity: str
) -> str:
    """
    Generate an AI-style analysis comment explaining why an OK item's
    comment looks suspicious.
    """
    labels = sorted(set(l for l, _, _ in matches))
    score = sum(w for _, w, _ in matches)

    if "hors_sujet" in labels:
        return (
            "⚠️ The comment does not address this requirement — it discusses "
            "a different subject (meeting, position, another component, a "
            "pending action) while the status is OK. The supplier has not "
            "confirmed conformity for THIS requirement. Check the consistency "
            "between the requirement and the comment."
        )
    if "na_language" in labels:
        return (
            "⚠️ The comment mentions 'N/A' or 'not applicable' but the "
            "status is OK. Logical inconsistency — if the requirement is not "
            "applicable, the OK status does not match the comment. "
            "Check the consistency between the declared status and the content."
        )
    if "cannot" in labels:
        return (
            "⚠️ The comment indicates a technical impossibility or inability "
            "('cannot', 'unable', 'impossible') while the FNR status is OK. "
            "Logical inconsistency — the comment describes a non-conformity "
            "while the status declares OK. Check the consistency."
        )
    if "not_responsible" in labels:
        return (
            "⚠️ The supplier disclaims responsibility in the comment "
            "but marked OK. Logical inconsistency — if the supplier is not "
            "responsible for this requirement, the comment contradicts the "
            "OK status. Check the consistency."
        )
    if "rejected" in labels:
        return (
            "⚠️ The comment contains rejection/refusal language while the "
            "status is OK. Major logical inconsistency — the comment "
            "describes a refusal but the status indicates OK. Check the consistency."
        )
    if "pending_confirmation" in labels or "in_development" in labels:
        return (
            "⚠️ The comment indicates that this point is still under "
            "development or pending confirmation, but the status is OK. "
            "Logical inconsistency — the comment suggests that conformity "
            "has not yet been validated. Check the consistency between the "
            "declared status and the actual state."
        )
    if "needs_action" in labels or "stla_action" in labels:
        return (
            "⚠️ The comment indicates that an action is needed (by the "
            "supplier or STLA), but the status is already OK. Logical "
            "inconsistency — if actions are still required, the comment "
            "contradicts the OK status. Check the consistency."
        )
    if "alternative_approach" in labels:
        return (
            "⚠️ The comment mentions an alternative approach, a deviation, "
            "or a substitution relative to the original requirement. Although the "
            "status is OK, the alternative approach is not documented in "
            "the status. Check the consistency."
        )
    if "conflict" in labels:
        return (
            "⚠️ The comment mentions a conflict or contradiction with "
            "another requirement. The OK status does not reflect this situation "
            "described in the comment — logical inconsistency. "
            "Check the consistency."
        )
    if "partial_limited" in labels:
        return (
            "⚠️ The comment suggests partial or limited conformity "
            "('partial', 'limited to', 'only for') while the status is OK. "
            "Logical inconsistency — the comment describes limitations "
            "that contradict a fully OK status. Check the consistency."
        )
    if "temporary" in labels:
        return (
            "⚠️ The comment mentions a temporary or provisional solution. "
            "The OK status does not mention this temporary condition — "
            "logical inconsistency. Check the consistency between the status "
            "and the comment."
        )
    if "remaining" in labels:
        return (
            "⚠️ The comment indicates that some elements still remain to be completed. "
            "The OK status does not reflect these remaining elements — "
            "logical inconsistency. Check the consistency."
        )

    # Generic fallback based on score
    if score >= 3:
        return (
            f"⚠️ The comment contains several signals ({', '.join(labels)}) "
            f"that contradict the OK status declared by the supplier. "
            f"Logical inconsistency — the comment does not match the "
            f"OK status. A human review is recommended to check "
            f"the consistency."
        )
    if score >= 1:
        return (
            f"ℹ️ The comment shows minor signals "
            f"({', '.join(labels)}) that do not match the OK status. "
            f"Check the consistency between the declared status and the comment."
        )
    return ""


def _significant_tokens(text: str) -> set:
    """Meaningful tokens of a text for overlap comparison: alphanumeric
    runs of >= 4 chars, lowercased. Short words (domain codes, articles,
    units) are ignored so they don't inflate the overlap."""
    return set(re.findall(r"[a-z0-9]{4,}", (text or "").lower()))


def _token_overlap(a: str, b: str) -> float:
    """Jaccard overlap of the significant tokens of two texts (0..1)."""
    ta = _significant_tokens(a)
    tb = _significant_tokens(b)
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


# Signals that indicate the supplier is discussing / planning / deferring
# the requirement in a way that does NOT confirm it. When such a signal
# appears in a comment that shares NO vocabulary with the requirement, the
# comment is almost certainly about a DIFFERENT subject — the supplier did
# not actually address this requirement. Deliberately limited to
# discussion/meeting/action signals: a plain "will follow connector spec"
# shares no vocabulary either but is still about the same subject, so it is
# NOT a mismatch (it is a pending signal, handled by the pattern engine).
_MISMATCH_SIGNAL_LABELS = {
    "discussion_needed", "stla_action", "please_clarify",
}


def _detect_requirement_mismatch(item: ConformityItem) -> Optional[tuple]:
    """Detect when an OK comment answers something OTHER than the requirement.

    A comment that (a) shares no significant vocabulary with the requirement
    description AND (b) carries a pending/discussion/action signal is talking
    about a different subject — the supplier did not confirm THIS requirement.

    Returns a (label, weight, matched_text) tuple for the pattern engine, or
    None. Conservative by design: short domain confirmations ("EMC OK",
    "DQ: ok,20260413") have zero overlap but are legitimate, so they are
    never flagged here (they carry no pending/discussion signal).
    """
    desc = item.description.strip()
    comment = item.comment.strip()
    if not desc or not comment:
        return None
    # Only substantive comments can be off-topic; short confirmations cannot.
    if len(comment) < 15:
        return None
    if _token_overlap(desc, comment) > 0.0:
        return None
    cnorm = _normalize(comment)
    for pattern, label, weight in _OK_SUSPICION_PATTERNS:
        if label in _MISMATCH_SIGNAL_LABELS:
            m = re.search(pattern, cnorm)
            if m:
                return ("hors_sujet", 2, m.group())
    return None


def _pattern_finding_for_item(item: ConformityItem) -> Optional[Dict]:
    """
    Pattern-based (regex) suspicion check for a single OK item.
    Returns a finding dict, or None if the comment raises no signal.
    """
    comment = item.comment.strip()
    conf_raw = item.conformity_raw.strip()

    cnorm = _normalize(comment)
    matches: List[tuple] = []
    for pattern, label, weight in _OK_SUSPICION_PATTERNS:
        m = re.search(pattern, cnorm)
        if m:
            matches.append((label, weight, m.group()))

    # NEW: the comment may answer something else than the requirement.
    mismatch = _detect_requirement_mismatch(item)
    if mismatch:
        matches.append(mismatch)

    if not matches:
        return None

    score = sum(w for _, w, _ in matches)
    ai_comment = _generate_ai_comment_ok(comment, matches, conf_raw)

    return {
        "reqId": item.req_id,
        "reference": item.reference,
        "conformity": conf_raw,
        "comment": comment[:300],
        "score": score,
        "signals": sorted(set(l for l, _, _ in matches)),
        "matched": [t for _, _, t in matches],
        "aiComment": ai_comment,
        "severity": "error" if score >= 4 else "warning" if score >= 2 else "info",
        "source": "motifs",
    }


# ── LLM semantic deep analysis of OK responses ─────────────────────

_LLM_BATCH_SIZE = 25       # items per LLM call
_LLM_MAX_ITEMS = 150       # beyond this, remaining items fall back to patterns
_LLM_COMMENT_MAX_CHARS = 600
_LLM_DESC_MAX_CHARS = 400  # requirement description sent to the LLM

_LLM_SYSTEM_PROMPT = """You are a senior quality auditor specialized in automotive industry supplier conformity matrices (FNR).

For each requirement provided, the supplier has declared the status OK (conform). You are given BOTH the requirement description and the supplier's comment. Your task: judge whether the supplier's COMMENT genuinely justifies this OK status FOR THIS SPECIFIC REQUIREMENT, or whether it actually reveals a hidden problem.

The comment must ADDRESS the requirement: it must confirm, explain or justify conformity for that exact requirement. A comment that talks about a different subject, a different requirement, or only gives a generic statement that says nothing about THIS requirement does NOT justify the OK status.

Give a verdict for EACH requirement:
- "CONTRADICTION": the comment actually describes a non-conformity (refusal, impossibility, missing or unsupported function, not applicable, out of scope, known defect...) → gravite "error"
- "PARTIAL": partial, limited, conditional conformity, with an unvalidated deviation or alternative solution → gravite "warning"
- "PENDING": conformity not yet achieved (in progress, to be confirmed, TBD, depends on a future action, delivery, or test...) → gravite "warning"
- "HORS_SUJET": the comment does NOT address this requirement at all — it talks about a different subject, another requirement, or a generic statement that gives no information about THIS requirement → gravite "warning"
- "AMBIGUOUS": comment too vague or unrelated to justify an OK → gravite "info"
- "COHERENT": the comment confirms or is compatible with conformity for THIS requirement → gravite "none"

Rules:
- Comments may be in French or English.
- A technical comment describing HOW the requirement is satisfied is COHERENT.
- Comments of the type "<domain>: ok" (e.g. "EE: ok", "SW: ok", "Touch: ok", "EE: ok SW: ok", "EMC 2026/03/18 OK", "DQ: ok,20260413"), possibly with a date, are domain-by-domain conformity confirmations: verdict COHERENT, never AMBIGUOUS or HORS_SUJET — even if the wording does not repeat the requirement text.
- Plain references (document numbers, versions, dates, domain codes) are not problems.
- Only flag AMBIGUOUS if the comment genuinely prevents understanding why the requirement would be conform.
- Only flag HORS_SUJET when the comment is a real sentence about a DIFFERENT subject (e.g. the requirement is about appearance defects but the comment discusses a meeting or a different component) — never for short domain confirmations.
- "citation": copy exactly the fragment of the comment (15 words max) that grounds your verdict; "" if COHERENT.
- "explication": 1 to 2 precise, professional sentences in English.

Respond ONLY in strict JSON, with no surrounding text:
{"resultats": [{"id": <int>, "verdict": "...", "gravite": "error|warning|info|none", "explication": "...", "citation": "..."}]}"""

_LLM_SEVERITY_SCORE = {"error": 5, "warning": 3, "info": 1}


def _llm_finding(item: ConformityItem, verdict: str, severity: str,
                 explication: str, citation: str) -> Dict:
    """Map one LLM verdict onto the standard finding schema."""
    icon = "ℹ️" if severity == "info" else "⚠️"
    ai_comment = f"{icon} {explication.strip()}"
    if citation:
        ai_comment += f" (excerpt: \"{citation.strip()}\")"
    return {
        "reqId": item.req_id,
        "reference": item.reference,
        "conformity": item.conformity_raw.strip(),
        "comment": item.comment.strip()[:300],
        "score": _LLM_SEVERITY_SCORE.get(severity, 1),
        "signals": ["ai_analysis", verdict.lower()],
        "matched": [citation] if citation else [],
        "aiComment": ai_comment,
        "severity": severity,
        "source": "ia",
    }


def _analyze_ok_deep_llm(items: List[ConformityItem]) -> Tuple[List[Dict], set]:
    """
    Semantic deep analysis of OK comments via the Azure OpenAI LLM.

    Sends the OK items (batched) to GPT and collects a verdict per item:
    CONTRADICTION / PARTIAL / PENDING / AMBIGUOUS / COHERENT.

    Returns (findings, analyzed_indices). Items whose batch failed are NOT
    in analyzed_indices — the caller falls back to pattern analysis for them.
    Returns ([], set()) when the LLM is not configured or unreachable.
    """
    import json as _json
    import logging as _logging

    if not items:
        return [], set()

    try:
        from app.config import (
            AZURE_OPENAI_API_KEY,
            AZURE_OPENAI_ENDPOINT,
            AZURE_OPENAI_LLM_DEPLOYMENT,
        )
        if not AZURE_OPENAI_API_KEY or not AZURE_OPENAI_ENDPOINT:
            return [], set()
        from app.embeddings import _get_client
        client = _get_client()
    except Exception as exc:
        _logging.warning(f"Deep-OK LLM unavailable (config/import): {exc}")
        return [], set()

    findings: List[Dict] = []
    analyzed: set = set()
    capped = items[:_LLM_MAX_ITEMS]

    for start in range(0, len(capped), _LLM_BATCH_SIZE):
        batch = capped[start:start + _LLM_BATCH_SIZE]
        lines = []
        for offset, item in enumerate(batch):
            idx = start + offset
            comment = item.comment.strip()[:_LLM_COMMENT_MAX_CHARS]
            desc = item.description.strip()[:_LLM_DESC_MAX_CHARS]
            conf = item.conformity_raw.strip() or "OK"
            lines.append(
                f"[{idx}] Requirement {item.req_id or '(no id)'} — "
                f"declared status: {conf}\n"
                f"Requirement: {desc or '(no description available)'}\n"
                f"Comment: {comment}"
            )
        user_msg = (
            f"Analyze the following {len(batch)} requirements "
            f"(all declared OK by the supplier):\n\n"
            + "\n\n".join(lines)
        )

        try:
            response = client.chat.completions.create(
                model=AZURE_OPENAI_LLM_DEPLOYMENT,
                messages=[
                    {"role": "system", "content": _LLM_SYSTEM_PROMPT},
                    {"role": "user", "content": user_msg},
                ],
                temperature=0.0,
                max_tokens=4000,
                timeout=90,
            )
            text = (response.choices[0].message.content or "").strip()
            text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text)
            data = _json.loads(text)
            results = data.get("resultats", [])
        except Exception as exc:
            _logging.warning(
                f"Deep-OK LLM batch {start}-{start+len(batch)-1} failed: {exc}"
            )
            continue  # this batch falls back to patterns

        by_id = {r.get("id"): r for r in results if isinstance(r, dict)}
        for offset, item in enumerate(batch):
            idx = start + offset
            r = by_id.get(idx)
            if r is None:
                continue  # missing from response → pattern fallback
            analyzed.add(idx)
            verdict = str(r.get("verdict", "")).upper()
            severity = str(r.get("gravite", "none")).lower()
            if verdict == "COHERENT" or severity in ("none", ""):
                continue  # confirmed OK — no finding
            if severity not in ("error", "warning", "info"):
                severity = {
                    "CONTRADICTION": "error",
                    "PARTIAL": "warning",
                    "PENDING": "warning",
                    "HORS_SUJET": "warning",
                }.get(verdict, "info")
            if severity == "info":
                continue  # only real problems (error/warning) are reported
            findings.append(_llm_finding(
                item, verdict, severity,
                str(r.get("explication", "")).strip()
                or "The comment does not clearly justify the OK status.",
                str(r.get("citation", "")).strip()[:120],
            ))

    return findings, analyzed


def analyze_ok_deep(analysis: ConformityAnalysis) -> List[Dict]:
    """
    Deep-analyze OK conformity items to detect hidden non-conformity signals.

    Two-stage analysis:
    1. Semantic LLM analysis (GPT via Azure OpenAI) — each OK comment is
       judged for real coherence with the declared OK status: contradiction,
       partial conformity, pending confirmation, ambiguity, or coherent.
    2. Pattern fallback — the proven regex suspicion library covers items
       the LLM could not analyze (not configured, unreachable, batch error,
       or beyond the per-run cap).

    Also flags OK items with no justifying comment (local check, no LLM).
    Sets analysis.ok_deep_method to "ia", "ia+motifs" or "motifs".
    """
    findings: List[Dict] = []

    # ── Local checks + collect items eligible for deep analysis ──
    deep_items: List[ConformityItem] = []
    for item in analysis.items:
        if item.conformity_category != "OK":
            continue

        comment = item.comment.strip()

        # OK without comment: nothing to analyze (info-level findings
        # are not reported — only real problems).
        if not comment:
            continue

        cnorm = _normalize(comment)

        # Skip pure domain code comments (domain assignments, not real comments)
        if re.match(
            r"^(sys|sw|ve|ee|me|od|opt|cg|tp|hw|mech|dq|fusa|ipm|all)"
            r"(\s*/\s*(sys|sw|ve|ee|me|od|opt|cg|tp|hw|mech|dq|fusa|ipm|all))*\s*$",
            cnorm,
        ):
            continue

        # Skip per-domain OK confirmations (e.g. "EE: ok", "Touch: ok",
        # "EE: ok SW: ok", "20260410 ME: ok") — these confirm conformity
        # domain by domain and are fully consistent with the OK status.
        if re.fullmatch(
            r"(?:[a-z0-9_.&/-]{1,15}\s*:\s*ok(?:ay)?|ok(?:ay)?|\d{2,8}|[\s,;/&+.-])+",
            cnorm,
        ):
            continue

        deep_items.append(item)

    # ── Stage 1: semantic LLM analysis ──
    llm_findings, analyzed_idx = _analyze_ok_deep_llm(deep_items)
    findings.extend(llm_findings)

    # ── Stage 2: pattern fallback for items the LLM did not cover ──
    remaining = [it for i, it in enumerate(deep_items) if i not in analyzed_idx]
    for item in remaining:
        f = _pattern_finding_for_item(item)
        if f:
            findings.append(f)

    if analyzed_idx and not remaining:
        analysis.ok_deep_method = "ia"
    elif analyzed_idx:
        analysis.ok_deep_method = "ia+motifs"
    else:
        analysis.ok_deep_method = "motifs"

    # Only real problems are reported — drop info-level findings
    # (whatever their source: LLM or pattern fallback).
    findings = [f for f in findings if f.get("severity") in ("error", "warning")]

    # Sort: most severe first, then score descending
    _sev_rank = {"error": 0, "warning": 1}
    findings.sort(key=lambda f: (_sev_rank.get(f.get("severity"), 2), -f.get("score", 0)))

    analysis.ok_deep_findings = findings
    # Backward compat: populate inconsistencies with the same unified findings
    analysis.inconsistencies = findings
    return findings


# ── Pie chart generation ────────────────────────────────────────────

# Color mapping for chart
_CHART_COLORS = {
    "OK": "#28a745",        # Green
    "NOK": "#dc3545",       # Red
    "NA": "#6c757d",        # Gray
    "EMPTY": "#e9ecef",     # Light gray
}


def _generate_svg_pie_chart(labels: list, sizes: list, colors: list,
                             title: str, total: int) -> str:
    """
    Generate a pie chart as SVG (pure Python, no matplotlib needed).
    Returns the SVG as a string.
    """
    import math

    cx, cy = 200, 200
    r = 130
    r_label = 165

    svg_parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="500" height="480" '
        f'viewBox="0 0 500 480">',
        f'<rect width="500" height="480" fill="white"/>',
        f'<text x="250" y="25" text-anchor="middle" font-size="14" '
        f'font-weight="bold" font-family="Arial" fill="#003366">'
        f'{_xml_escape(title)}</text>',
        f'<text x="250" y="45" text-anchor="middle" font-size="11" '
        f'font-family="Arial" fill="#666">({total} requirements)</text>',
    ]

    start_angle = -90.0  # Start at top (12 o'clock)
    for i, (label, size, color) in enumerate(zip(labels, sizes, colors)):
        if size == 0:
            continue
        pct = size / total * 100
        angle_span = (size / total) * 360.0
        end_angle = start_angle + angle_span

        # Calculate arc points
        x1 = cx + r * math.cos(math.radians(start_angle))
        y1 = cy + r * math.sin(math.radians(start_angle))
        x2 = cx + r * math.cos(math.radians(end_angle))
        y2 = cy + r * math.sin(math.radians(end_angle))

        large_arc = 1 if angle_span > 180 else 0

        # Pie slice path
        path = (
            f'M {cx},{cy} L {x1:.1f},{y1:.1f} '
            f'A {r},{r} 0 {large_arc} 1 {x2:.1f},{y2:.1f} Z'
        )
        svg_parts.append(
            f'<path d="{path}" fill="{color}" stroke="white" stroke-width="1.5"/>'
        )

        # Label position (midpoint of arc)
        mid_angle = start_angle + angle_span / 2
        lx = cx + r_label * math.cos(math.radians(mid_angle))
        ly = cy + r_label * math.sin(math.radians(mid_angle))

        # Percentage text inside the slice
        tx = cx + (r * 0.6) * math.cos(math.radians(mid_angle))
        ty = cy + (r * 0.6) * math.sin(math.radians(mid_angle))

        svg_parts.append(
            f'<text x="{lx:.1f}" y="{ly:.1f}" text-anchor="middle" '
            f'dominant-baseline="central" font-size="11" font-weight="bold" '
            f'font-family="Arial" fill="#333">{label}</text>'
        )
        svg_parts.append(
            f'<text x="{tx:.1f}" y="{ty:.1f}" text-anchor="middle" '
            f'dominant-baseline="central" font-size="10" font-weight="bold" '
            f'font-family="Arial" fill="white">{pct:.1f}%</text>'
        )

        start_angle = end_angle

    # Legend
    legend_y = 420
    legend_x = 50
    for i, (label, size, color) in enumerate(zip(labels, sizes, colors)):
        if size == 0:
            continue
        lx = legend_x + (i % 4) * 110
        ly = legend_y + (i // 4) * 20
        svg_parts.append(
            f'<rect x="{lx}" y="{ly - 8}" width="12" height="12" fill="{color}"/>'
        )
        svg_parts.append(
            f'<text x="{lx + 16}" y="{ly}" font-size="10" font-family="Arial" '
            f'fill="#333">{label}: {size}</text>'
        )

    svg_parts.append('</svg>')
    return "\n".join(svg_parts)


def _xml_escape(text: str) -> str:
    """Escape XML special characters."""
    return (text.replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;"))


def generate_pie_chart(analysis: ConformityAnalysis) -> str:
    """
    Generate a Camembert (pie) chart of conformity status distribution.
    Returns base64-encoded PNG string (or SVG if matplotlib not available).
    Gracefully returns empty string if no data.
    """
    # Filter out EMPTY for the chart (show meaningful statuses)
    chart_data = {k: v for k, v in analysis.stats.items()
                  if k != "EMPTY" and v > 0}

    if not chart_data:
        # If no meaningful data, show all
        chart_data = {k: v for k, v in analysis.stats.items() if v > 0}

    if not chart_data:
        return ""

    labels = list(chart_data.keys())
    sizes = list(chart_data.values())
    colors = [_CHART_COLORS.get(label, "#adb5bd") for label in labels]
    total = sum(sizes)

    title = (
        f"Conformity Status Breakdown (FNR)"
    )

    # Try matplotlib first (generates PNG)
    try:
        import matplotlib
        matplotlib.use("Agg")  # Non-interactive backend
        import matplotlib.pyplot as plt

        fig, ax = plt.subplots(figsize=(8, 6), dpi=100)

        wedges, texts, autotexts = ax.pie(
            sizes,
            labels=labels,
            colors=colors,
            autopct=lambda pct: f"{pct:.1f}%\n({int(round(pct/100*sum(sizes)))})",
            startangle=90,
            textprops={"fontsize": 11},
        )

        for autotext in autotexts:
            autotext.set_fontsize(9)
            autotext.set_fontweight("bold")

        ax.set_title(
            f"Conformity Status Breakdown (FNR)\n"
            f"({total} requirements — {analysis.file_name or analysis.sheet_name})",
            fontsize=13,
            fontweight="bold",
            pad=20,
        )

        ax.axis("equal")

        buf = io.BytesIO()
        fig.savefig(buf, format="png", bbox_inches="tight", facecolor="white")
        plt.close(fig)
        buf.seek(0)

        chart_b64 = base64.b64encode(buf.read()).decode("utf-8")
        analysis.chart_base64 = chart_b64
        return chart_b64

    except ImportError:
        # matplotlib not available — use pure-Python SVG fallback
        svg = _generate_svg_pie_chart(labels, sizes, colors, title, total)
        chart_b64 = base64.b64encode(svg.encode("utf-8")).decode("utf-8")
        analysis.chart_base64 = chart_b64
        return chart_b64


# ── Report text generation ──────────────────────────────────────────

def generate_report_text(analysis: ConformityAnalysis) -> str:
    """
    Generate a human-readable text report of the conformity analysis.
    """
    lines: List[str] = []

    lines.append("=" * 70)
    lines.append("LEON — Conformity Matrix Analysis Report FNR")
    lines.append("=" * 70)
    lines.append("")
    lines.append(f"File analyzed: {analysis.file_name}")
    lines.append(f"Sheet: {analysis.sheet_name}")
    lines.append(f"Header row: {analysis.header_row + 1}")
    lines.append(f"First data row: {analysis.data_start_row + 1}")
    lines.append(f"Total number of requirements: {analysis.total_rows}")
    lines.append("")

    # Statistics
    lines.append("─" * 50)
    lines.append("BREAKDOWN OF CONFORMITY STATUSES")
    lines.append("─" * 50)
    for cat, count in sorted(analysis.stats.items(), key=lambda x: -x[1]):
        pct = (count / analysis.total_rows * 100) if analysis.total_rows else 0
        lines.append(f"  {cat:12s} : {count:4d} ({pct:5.1f}%)")
    req_count = sum(1 for it in analysis.items if it.is_requirement)
    doc_count = analysis.total_rows - req_count
    if doc_count:
        lines.append(f"  (of which {req_count} requirement rows and "
                     f"{doc_count} document/reference rows)")
    lines.append("")

    # OK items
    ok_items = [item for item in analysis.items if item.conformity_category == "OK"]
    if ok_items:
        lines.append("─" * 50)
        lines.append(f"CONFORMING REQUIREMENTS (OK) — {len(ok_items)}")
        lines.append("─" * 50)
        for item in ok_items:
            comment_str = f" | Comment: {item.comment}" if item.comment else ""
            version_str = f" | Version: {item.version}" if item.version else ""
            lines.append(f"  ✅ {item.req_id}: {item.conformity_raw}{version_str}{comment_str}")
        lines.append("")

    # NOK items
    nok_items = [item for item in analysis.items if item.conformity_category == "NOK"]
    if nok_items:
        lines.append("─" * 50)
        lines.append(f"NON-CONFORMING REQUIREMENTS (NOK) — {len(nok_items)}")
        lines.append("─" * 50)
        for item in nok_items:
            comment_str = f" | Comment: {item.comment}" if item.comment else ""
            version_str = f" | Version: {item.version}" if item.version else ""
            lines.append(f"  ❌ {item.req_id}: {item.conformity_raw}{version_str}{comment_str}")
        lines.append("")

    # NA items
    na_items = [item for item in analysis.items if item.conformity_category == "NA"]
    if na_items:
        lines.append("─" * 50)
        lines.append(f"NOT APPLICABLE REQUIREMENTS (NA) — {len(na_items)}")
        lines.append("─" * 50)
        for item in na_items:
            comment_str = f" | Comment: {item.comment}" if item.comment else ""
            version_str = f" | Version: {item.version}" if item.version else ""
            lines.append(f"  ⬜ {item.req_id}: {item.conformity_raw}{version_str}{comment_str}")
        lines.append("")

    # Items needing review
    review_items = [item for item in analysis.items if item.needs_review]
    if review_items:
        lines.append("─" * 50)
        lines.append(f"REQUIREMENTS TO VERIFY MANUALLY — {len(review_items)}")
        lines.append("─" * 50)
        for item in review_items:
            conf_str = f"Status: {item.conformity_category} ('{item.conformity_raw}')"
            comment_str = f" | Comment: {item.comment}" if item.comment else ""
            lines.append(f"  🔍 {item.req_id}: {conf_str}{comment_str}")
        lines.append("")

    # Deep analysis findings (unified: covers all OK suspicion signals)
    if analysis.inconsistencies:
        lines.append("─" * 50)
        lines.append(f"DEEP ANALYSIS OF OK RESPONSES — {len(analysis.inconsistencies)} point(s) of attention")
        lines.append("─" * 50)
        for inc in analysis.inconsistencies:
            sev = inc.get("severity", "warning")
            icon = "🔴" if sev == "error" else "🟡" if sev == "warning" else "ℹ️"
            signals = ", ".join(inc.get("signals", []))
            lines.append(f"  {icon} [{sev.upper()}] {inc.get('reqId', inc.get('req_id', ''))} (score: {inc.get('score', 0)})")
            if signals:
                lines.append(f"     Signals: {signals}")
            if inc.get('conformity'):
                lines.append(f"     Conformity: '{inc['conformity']}'")
            if inc.get('comment'):
                lines.append(f"     Comment: '{inc['comment'][:200]}'")
            if inc.get('aiComment'):
                lines.append(f"     Analysis: {inc['aiComment']}")
            lines.append("")
    else:
        lines.append("─" * 50)
        lines.append("✅ DEEP ANALYSIS OF OK RESPONSES — No point of attention detected")
        lines.append("─" * 50)
        lines.append("")

    lines.append("=" * 70)
    lines.append("End of report — LEON Conformity Matrix Analyzer")
    lines.append("=" * 70)

    report = "\n".join(lines)
    analysis.report_text = report
    return report


# ── Full analysis pipeline ──────────────────────────────────────────

def analyze_conformity_matrix(filepath: str, file_name: str = "") -> ConformityAnalysis:
    """
    Complete pipeline: extract → classify → deep analyze OK → chart → report.

    Args:
        filepath: Path to the ODS or XLSX file.
        file_name: Display name for the file.

    Returns:
        ConformityAnalysis with all data, stats, findings, chart, and report.
    """
    # 1. Extract data
    analysis = extract_conformity_data(filepath, file_name)

    # 2. Deep-analyze OK responses for hidden non-conformity (unified analysis)
    #    This replaces the old separate detect_inconsistencies() + analyze_ok_deep()
    #    which were analyzing the same OK items with overlapping patterns.
    analyze_ok_deep(analysis)

    # 3. Generate pie chart
    generate_pie_chart(analysis)

    # 4. Generate report text
    generate_report_text(analysis)

    return analysis


def analysis_to_dict(analysis: ConformityAnalysis) -> dict:
    """Convert ConformityAnalysis to a JSON-serializable dict."""
    req_items = [it for it in analysis.items if it.is_requirement]
    doc_items = [it for it in analysis.items if not it.is_requirement]

    def _cat_counts(items):
        counts: Dict[str, int] = {}
        for it in items:
            counts[it.conformity_category] = counts.get(it.conformity_category, 0) + 1
        return counts

    requirement_stats = _cat_counts(req_items)

    return {
        "fileName": analysis.file_name,
        "sheetName": analysis.sheet_name,
        "headerRow": analysis.header_row,
        "dataStartRow": analysis.data_start_row,
        "totalRows": analysis.total_rows,
        "sheetTotalRows": analysis.sheet_total_rows,
        "stats": analysis.stats,
        "requirementStats": requirement_stats,
        "llmMaxItems": _LLM_MAX_ITEMS,
        "llmBatchSize": _LLM_BATCH_SIZE,
        "columnMapping": analysis.column_mapping,
        "items": [
            {
                "rowIndex": item.row_index,
                "reqId": item.req_id,
                "reference": item.reference,
                "description": item.description,
                "conformityRaw": item.conformity_raw,
                "conformityCategory": item.conformity_category,
                "comment": item.comment,
                "version": item.version,
                "versionApplicable": item.version,  # Alias for clarity
                "columnSet": item.column_set,
                "needsReview": item.needs_review,
                "classificationConfidence": item.classification_confidence,
                "isRequirement": item.is_requirement,
            }
            for item in analysis.items
        ],
        "inconsistencies": analysis.inconsistencies,
        "okDeepFindings": analysis.ok_deep_findings,
        "okDeepMethod": analysis.ok_deep_method,
        "chartBase64": analysis.chart_base64,
        "reportText": analysis.report_text,
        "summary": {
            "total": analysis.total_rows,
            "ok": analysis.stats.get("OK", 0),
            "nok": analysis.stats.get("NOK", 0),
            "na": analysis.stats.get("NA", 0),
            "empty": analysis.stats.get("EMPTY", 0),
            "inconsistencies": len(analysis.inconsistencies),
            "okDeepFindings": len(analysis.ok_deep_findings),
            "needsReview": sum(1 for item in analysis.items if item.needs_review),
            "totalRequirements": len(req_items),
            "okRequirements": requirement_stats.get("OK", 0),
            "nokRequirements": requirement_stats.get("NOK", 0),
            "naRequirements": requirement_stats.get("NA", 0),
            "emptyRequirements": requirement_stats.get("EMPTY", 0),
            "totalDocuments": len(doc_items),
            "okDocuments": sum(1 for it in doc_items if it.conformity_category == "OK"),
        },
    }


# ═══════════════════════════════════════════════════════════════════
# MULTI-MATRIX COMPARISON
# ═══════════════════════════════════════════════════════════════════

@dataclass
class MatrixComparison:
    """Result of comparing two or more conformity matrices."""
    matrices: List[Dict] = field(default_factory=list)  # per-matrix summaries
    # Per-requirement comparison: key → {matrix_name → category}
    requirement_comparison: Dict[str, Dict[str, str]] = field(default_factory=dict)
    # Requirements that changed status between consecutive matrices
    status_changes: List[Dict] = field(default_factory=list)
    # Requirements present in one matrix but not the other
    missing_in: Dict[str, List[str]] = field(default_factory=dict)
    # Comparison chart (base64 PNG)
    chart_base64: str = ""
    # Comparison report text
    report_text: str = ""
    # Summary
    total_compared: int = 0
    total_changes: int = 0
    total_missing: int = 0
    # ── Version-to-version delta (any number of matrices) ──
    # Requirements added / removed between consecutive matrices (step → keys)
    new_in: Dict[str, List[str]] = field(default_factory=dict)
    removed_in: Dict[str, List[str]] = field(default_factory=dict)
    # Same-status rows whose comment / applicable version changed
    comment_changes: List[Dict] = field(default_factory=list)
    version_changes: List[Dict] = field(default_factory=list)
    # One summary per consecutive pair (v1→v2, v2→v3, …)
    steps: List[Dict] = field(default_factory=list)


def compare_matrices(filepaths: List[str], file_names: Optional[List[str]] = None) -> MatrixComparison:
    """
    Compare two or more conformity matrices side by side.

    For each requirement ID found in any matrix, shows its status in each matrix.
    Detects:
    - Status changes (e.g., NOK→OK between versions)
    - Requirements present in one matrix but missing in another
    - Overall trend (improvement or regression)

    Args:
        filepaths: List of ODS/XLSX file paths to compare.
        file_names: Optional display names (defaults to file basename).

    Returns:
        MatrixComparison with per-requirement comparison, changes, and chart.
    """
    if not filepaths:
        raise ValueError("At least one file path required for comparison")
    if file_names is None:
        file_names = [Path(f).name for f in filepaths]

    comparison = MatrixComparison()

    # Analyze each matrix
    analyses: List[ConformityAnalysis] = []
    for fp, fn in zip(filepaths, file_names):
        analysis = extract_conformity_data(fp, fn)
        analyze_ok_deep(analysis)
        analyses.append(analysis)

        comparison.matrices.append({
            "fileName": fn,
            "sheetName": analysis.sheet_name,
            "totalRows": analysis.total_rows,
            "stats": analysis.stats,
            "inconsistencies": len(analysis.inconsistencies),
            "summary": {
                "ok": analysis.stats.get("OK", 0),
                "nok": analysis.stats.get("NOK", 0),
                "na": analysis.stats.get("NA", 0),
                "empty": analysis.stats.get("EMPTY", 0),
            },
        })

    # Build per-requirement comparison keyed by a canonical requirement
    # identity: the REQ-ID when present, else the first spec-side reference
    # token found in column B. Only real requirement rows (is_requirement)
    # participate — applicable-document/category rows are not requirements.
    all_keys: set = set()
    per_matrix: Dict[str, Dict[str, dict]] = {}  # matrix_name → key → item info

    for analysis in analyses:
        matrix_name = analysis.file_name
        per_matrix[matrix_name] = {}
        for item in analysis.items:
            if not item.is_requirement:
                continue
            key = _canonical_key(item)
            if not key:
                continue
            per_matrix[matrix_name][key] = {
                "reqId": item.req_id,
                "reference": item.reference,
                "category": item.conformity_category,
                "comment": item.comment,
                "version": item.version,
            }
            all_keys.add(key)

    # requirement_comparison: key → {matrix_name → category}
    for key in sorted(all_keys):
        comparison.requirement_comparison[key] = {
            name: per_matrix[name].get(key, {}).get("category", "MISSING")
            for name in file_names
        }

    comparison.total_compared = len(all_keys)

    # missing_in: for each matrix, the keys present elsewhere but absent here
    for name in file_names:
        missing = sorted(k for k in all_keys if k not in per_matrix[name])
        if missing:
            comparison.missing_in[name] = missing
            comparison.total_missing += len(missing)

    # Consecutive-step deltas (v1→v2, v2→v3, …) — "what changed since the
    # last gate". Works for any number of matrices, not just two.
    (comparison.status_changes, comparison.comment_changes,
     comparison.version_changes, comparison.new_in, comparison.removed_in,
     comparison.steps) = _compute_deltas(per_matrix, file_names, all_keys)
    comparison.total_changes = len(comparison.status_changes)

    # Generate comparison chart
    _generate_comparison_chart(comparison, file_names)

    # Generate comparison report text
    _generate_comparison_report(comparison, file_names)

    return comparison


def _canonical_key(item) -> str:
    """Stable identity for a requirement row across matrix versions:
    the REQ-ID when present, else the first spec-side reference token."""
    rid = normalize_id(item.req_id)
    if rid:
        return rid
    refs = extract_id_tokens(item.reference)
    return refs[0] if refs else ""


def _normalize_comment(text: str) -> str:
    """Normalize a comment for change-detection: collapse every whitespace run
    (line breaks, tabs, multiple spaces) to a single space, trim, and drop
    spaces around measurement/relation symbols (± ≤ ≥ °) and the full-width
    colon (：) that ODS/XLSX render differently.

    ODS readers preserve the cell's line breaks (\\n) while XLSX readers
    collapse them to spaces, so the SAME comment can look different between
    two files. Formatting-only differences must not count as a comment change.
    """
    s = re.sub(r"\s+", " ", (text or "")).strip()
    # 'Center±2mm' vs 'Center ±2mm', '≤1ms' vs ' ≤ 1ms', 'FALD：BLU' vs 'FALD： BLU'
    s = re.sub(r"\s*([±≤≥°：])\s*", r"\1", s)
    return s


def _comments_differ(a: str, b: str) -> bool:
    """True when two comments differ in content (ignoring whitespace/line-break
    formatting)."""
    return _normalize_comment(a) != _normalize_comment(b)


def _compute_deltas(
    per_matrix: Dict[str, Dict[str, dict]],
    file_names: List[str],
    all_keys: set,
) -> Tuple[List[Dict], List[Dict], List[Dict], Dict[str, List[str]], Dict[str, List[str]], List[Dict]]:
    """Consecutive-step deltas between matrices (v1→v2, v2→v3, …).

    Returns (status_changes, comment_changes, version_changes, new_in,
             removed_in, steps). Pure function — unit-testable without files.
    """
    status_changes: List[Dict] = []
    comment_changes: List[Dict] = []
    version_changes: List[Dict] = []
    new_in: Dict[str, List[str]] = {}
    removed_in: Dict[str, List[str]] = {}
    steps: List[Dict] = []

    for step, (a_name, b_name) in enumerate(zip(file_names, file_names[1:]), 1):
        a = per_matrix[a_name]
        b = per_matrix[b_name]
        step_changes: List[Dict] = []
        step_new: List[str] = []
        step_removed: List[str] = []
        step_comment: List[Dict] = []
        step_version: List[Dict] = []

        for key in sorted(all_keys):
            ia = a.get(key)
            ib = b.get(key)
            if ia is None and ib is not None:
                step_new.append(key)
            elif ia is not None and ib is None:
                step_removed.append(key)
            elif ia is not None and ib is not None:
                if ia["category"] != ib["category"]:
                    step_changes.append({
                        "reqId": ib["reqId"] or ia["reqId"],
                        "reference": ib["reference"] or ia["reference"],
                        "from": ia["category"],
                        "to": ib["category"],
                        "matrix1": a_name,
                        "matrix2": b_name,
                        "step": step,
                        "improvement": _is_improvement(ia["category"], ib["category"]),
                        "changeType": _change_type(ia["category"], ib["category"]),
                    })
                # Comment change — detected even when the category also changed
                # (a supplier can reword a comment while flipping the status).
                if _comments_differ(ia["comment"], ib["comment"]):
                    step_comment.append({
                        "reqId": ib["reqId"] or ia["reqId"],
                        "reference": ib["reference"] or ia["reference"],
                        "matrix1": a_name,
                        "matrix2": b_name,
                        "step": step,
                        "fromComment": ia["comment"],
                        "toComment": ib["comment"],
                    })
                # Version change — likewise independent of the status change.
                if (ia["version"] or "").strip() != (ib["version"] or "").strip():
                    step_version.append({
                        "reqId": ib["reqId"] or ia["reqId"],
                        "reference": ib["reference"] or ia["reference"],
                        "matrix1": a_name,
                        "matrix2": b_name,
                        "step": step,
                        "fromVersion": ia["version"],
                        "toVersion": ib["version"],
                    })

        status_changes.extend(step_changes)
        comment_changes.extend(step_comment)
        version_changes.extend(step_version)
        new_in[str(step)] = step_new
        removed_in[str(step)] = step_removed
        answered_before = sum(1 for v in a.values() if v["category"] != "EMPTY")
        answered_after = sum(1 for v in b.values() if v["category"] != "EMPTY")
        steps.append({
            "step": step,
            "matrix1": a_name,
            "matrix2": b_name,
            "statusChanges": len(step_changes),
            "new": len(step_new),
            "removed": len(step_removed),
            "commentChanges": len(step_comment),
            "versionChanges": len(step_version),
            "answeredBefore": answered_before,
            "answeredAfter": answered_after,
        })

    return status_changes, comment_changes, version_changes, new_in, removed_in, steps


def _is_improvement(from_cat: str, to_cat: str) -> bool:
    """Check if a status change is an improvement.

    EMPTY ('no answer') is the WORST state — an unanswered requirement is worse
    than any answered one: X → EMPTY means the supplier WITHDREW their answer
    (a regression), and EMPTY → X means they newly answered it (an improvement).
    Among real answers the order is OK > NA > NOK.
    """
    ranking = {"EMPTY": 0, "NOK": 1, "NA": 2, "OK": 3}
    return ranking.get(to_cat, 0) > ranking.get(from_cat, 0)


def _change_type(from_cat: str, to_cat: str) -> str:
    """Classify a status change into one of four human-readable kinds.

    - "added"    : EMPTY → real answer (newly answered)
    - "removed"  : real answer → EMPTY (answer withdrawn)
    - "improved" : real → real, moved up (OK > NA > NOK)
    - "regressed": real → real, moved down
    """
    if from_cat == "EMPTY" and to_cat != "EMPTY":
        return "added"
    if from_cat != "EMPTY" and to_cat == "EMPTY":
        return "removed"
    return "improved" if _is_improvement(from_cat, to_cat) else "regressed"


def _generate_comparison_chart(comparison: MatrixComparison, file_names: List[str]) -> None:
    """Generate a grouped bar chart comparing conformity status across matrices."""
    categories = ["OK", "NOK", "NA", "EMPTY"]
    n_matrices = len(file_names)
    n_categories = len(categories)

    # Build data matrix: rows=categories, cols=matrices
    data = [[0] * n_matrices for _ in range(n_categories)]
    for mi, matrix_info in enumerate(comparison.matrices):
        stats = matrix_info.get("stats", {})
        for ci, cat in enumerate(categories):
            data[ci][mi] = stats.get(cat, 0)

    color_map = {
        "OK": "#28a745", "NOK": "#dc3545", "NA": "#6c757d",
        "EMPTY": "#e9ecef",
    }

    # Try matplotlib first (generates PNG)
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        import numpy as np

        data_np = np.array(data)

        fig, ax = plt.subplots(figsize=(10, 6), dpi=100)

        x = np.arange(n_categories)
        width = 0.8 / n_matrices

        for mi, matrix_info in enumerate(comparison.matrices):
            name = matrix_info.get("fileName", f"Matrix {mi+1}")
            short_name = name if len(name) <= 20 else name[:17] + "..."
            offset = (mi - n_matrices / 2 + 0.5) * width
            bars = ax.bar(x + offset, data_np[:, mi], width, label=short_name, alpha=0.85)

            for bar in bars:
                height = bar.get_height()
                if height > 0:
                    ax.text(bar.get_x() + bar.get_width() / 2., height,
                            f"{int(height)}", ha="center", va="bottom", fontsize=7)

        ax.set_xlabel("Conformity Status", fontsize=11, fontweight="bold")
        ax.set_ylabel("Number of Requirements", fontsize=11, fontweight="bold")
        ax.set_title("Conformity Matrix Comparison", fontsize=13, fontweight="bold", pad=15)
        ax.set_xticks(x)
        ax.set_xticklabels(categories, fontsize=10)
        ax.legend(fontsize=9, loc="upper right")
        ax.grid(axis="y", alpha=0.3)

        buf = io.BytesIO()
        fig.savefig(buf, format="png", bbox_inches="tight", facecolor="white")
        plt.close(fig)
        buf.seek(0)

        comparison.chart_base64 = base64.b64encode(buf.read()).decode("utf-8")
        return

    except ImportError:
        pass  # Fall through to SVG fallback

    # SVG fallback (pure Python, no matplotlib)
    svg_parts = [
        '<svg xmlns="http://www.w3.org/2000/svg" width="600" height="400" viewBox="0 0 600 400">',
        '<rect width="600" height="400" fill="white"/>',
        '<text x="300" y="25" text-anchor="middle" font-size="14" font-weight="bold" '
        'font-family="Arial" fill="#003366">Conformity Matrix Comparison</text>',
    ]

    chart_left = 60
    chart_top = 50
    chart_w = 500
    chart_h = 280
    bar_area_w = chart_w / n_categories
    bar_w = bar_area_w * 0.8 / n_matrices

    # Find max value for scaling
    max_val = max(max(row) for row in data) if data else 1
    max_val = max(max_val, 1)

    # Y-axis grid lines
    for i in range(5):
        y = chart_top + chart_h * (1 - i / 4)
        val = int(max_val * i / 4)
        svg_parts.append(f'<line x1="{chart_left}" y1="{y:.0f}" x2="{chart_left + chart_w}" y2="{y:.0f}" stroke="#e0e0e0" stroke-width="1"/>')
        svg_parts.append(f'<text x="{chart_left - 5}" y="{y + 3:.0f}" text-anchor="end" font-size="9" font-family="Arial" fill="#666">{val}</text>')

    # Bars
    for ci, cat in enumerate(categories):
        cx = chart_left + ci * bar_area_w + bar_area_w / 2
        for mi in range(n_matrices):
            val = data[ci][mi]
            h = (val / max_val) * chart_h if max_val > 0 else 0
            bx = cx - (n_matrices * bar_w) / 2 + mi * bar_w
            by = chart_top + chart_h - h
            color = color_map.get(cat, "#adb5bd")
            svg_parts.append(f'<rect x="{bx:.1f}" y="{by:.1f}" width="{bar_w:.1f}" height="{h:.1f}" fill="{color}" stroke="white" stroke-width="0.5"/>')
            if val > 0:
                svg_parts.append(f'<text x="{bx + bar_w/2:.1f}" y="{by - 3:.0f}" text-anchor="middle" font-size="8" font-family="Arial" fill="#333">{val}</text>')
        # Category label
        svg_parts.append(f'<text x="{cx:.0f}" y="{chart_top + chart_h + 15}" text-anchor="middle" font-size="10" font-family="Arial" fill="#333">{cat}</text>')

    # Legend
    legend_y = 370
    for mi, matrix_info in enumerate(comparison.matrices):
        name = matrix_info.get("fileName", f"Matrix {mi+1}")
        short_name = name if len(name) <= 25 else name[:22] + "..."
        lx = 50 + mi * 200
        svg_parts.append(f'<rect x="{lx}" y="{legend_y - 8}" width="12" height="12" fill="#003366"/>')
        svg_parts.append(f'<text x="{lx + 16}" y="{legend_y}" font-size="9" font-family="Arial" fill="#333">{_xml_escape(short_name)}</text>')

    svg_parts.append('</svg>')
    svg = "\n".join(svg_parts)
    comparison.chart_base64 = base64.b64encode(svg.encode("utf-8")).decode("utf-8")


def _generate_comparison_report(comparison: MatrixComparison, file_names: List[str]) -> None:
    """Generate a text report for the multi-matrix comparison."""
    lines: List[str] = []

    lines.append("=" * 70)
    lines.append("LEON — Multi-Matrix Conformity Comparison Report")
    lines.append("=" * 70)
    lines.append("")

    # Per-matrix summary
    lines.append("─" * 50)
    lines.append("SUMMARY BY MATRIX")
    lines.append("─" * 50)
    for matrix_info in comparison.matrices:
        lines.append(f"\n  📊 {matrix_info['fileName']}")
        lines.append(f"     Sheet: {matrix_info['sheetName']}")
        lines.append(f"     Total: {matrix_info['totalRows']} requirements")
        summary = matrix_info.get("summary", {})
        lines.append(f"     OK: {summary.get('ok', 0)} | NOK: {summary.get('nok', 0)} | "
                     f"NA: {summary.get('na', 0)} | EMPTY: {summary.get('empty', 0)}")
        lines.append(f"     AI inconsistencies: {matrix_info.get('inconsistencies', 0)}")
    lines.append("")

    # Comparison summary
    lines.append("─" * 50)
    lines.append("COMPARISON")
    lines.append("─" * 50)
    lines.append(f"  Requirements compared: {comparison.total_compared}")
    lines.append(f"  Status changes: {comparison.total_changes}")
    lines.append(f"  Missing requirements: {comparison.total_missing}")
    lines.append("")

    # Status changes
    if comparison.status_changes:
        lines.append("─" * 50)
        lines.append(f"STATUS CHANGES — {len(comparison.status_changes)}")
        lines.append("─" * 50)
        groups = [
            ("🟢 NEWLY ANSWERED (EMPTY → status)",
             [c for c in comparison.status_changes if c.get("changeType") == "added"]),
            ("🔴 ANSWER REMOVED (status → EMPTY)",
             [c for c in comparison.status_changes if c.get("changeType") == "removed"]),
            ("✅ IMPROVED",
             [c for c in comparison.status_changes if c.get("changeType") == "improved"]),
            ("❌ REGRESSED",
             [c for c in comparison.status_changes if c.get("changeType") == "regressed"]),
        ]
        for label, group in groups:
            if group:
                lines.append(f"\n  {label} ({len(group)}):")
                for change in group[:20]:
                    lines.append(f"    {change.get('reqId') or change.get('reference')}: "
                                 f"{change['from']} → {change['to']}")
        lines.append("")

    # Missing requirements
    if comparison.missing_in:
        lines.append("─" * 50)
        lines.append("MISSING REQUIREMENTS")
        lines.append("─" * 50)
        for matrix_name, req_ids in comparison.missing_in.items():
            lines.append(f"\n  Missing in '{matrix_name}': {len(req_ids)} requirements")
            for req_id in req_ids[:20]:
                lines.append(f"    {req_id}")
        lines.append("")

    # Version-to-version delta (consecutive steps)
    if comparison.steps:
        lines.append("─" * 50)
        lines.append("VERSION-TO-VERSION DELTA")
        lines.append("─" * 50)
        for step in comparison.steps:
            lines.append(f"\n  Step {step['step']}: {step['matrix1']} → {step['matrix2']}")
            lines.append(f"     Status changes: {step['statusChanges']} | "
                         f"New: {step['new']} | Removed: {step['removed']} | "
                         f"Comment changes: {step['commentChanges']} | "
                         f"Version changes: {step['versionChanges']}")
            if "answeredBefore" in step:
                before, after = step["answeredBefore"], step["answeredAfter"]
                if before > after:
                    trend = f"lost {before - after}"
                elif after > before:
                    trend = f"gained {after - before}"
                else:
                    trend = "unchanged"
                lines.append(f"     Supplier answers: {before} → {after} ({trend})")
        if comparison.status_changes:
            lines.append(f"\n  STATUS CHANGES ({len(comparison.status_changes)}):")
            arrows = {"added": "🟢", "removed": "🔴", "improved": "✅", "regressed": "❌"}
            for c in comparison.status_changes[:30]:
                arrow = arrows.get(c.get("changeType"), "✅" if c["improvement"] else "❌")
                lines.append(f"    {arrow} step{c['step']} "
                             f"{c.get('reqId') or c.get('reference')}: {c['from']} → {c['to']}")
        if comparison.comment_changes:
            lines.append(f"\n  COMMENT CHANGES ({len(comparison.comment_changes)}):")
            for c in comparison.comment_changes[:20]:
                lines.append(f"    step{c['step']} {c.get('reqId') or c.get('reference')}")
        lines.append("")

    lines.append("=" * 70)
    lines.append("End of report — LEON Multi-Matrix Comparison")
    lines.append("=" * 70)

    comparison.report_text = "\n".join(lines)


def comparison_to_dict(comparison: MatrixComparison) -> dict:
    """Convert MatrixComparison to a JSON-serializable dict."""
    return {
        "matrices": comparison.matrices,
        "requirementComparison": {
            req_id: statuses
            for req_id, statuses in comparison.requirement_comparison.items()
        },
        "statusChanges": comparison.status_changes,
        "missingIn": comparison.missing_in,
        "chartBase64": comparison.chart_base64,
        "reportText": comparison.report_text,
        "totalCompared": comparison.total_compared,
        "totalChanges": comparison.total_changes,
        "totalMissing": comparison.total_missing,
        "newIn": comparison.new_in,
        "removedIn": comparison.removed_in,
        "commentChanges": comparison.comment_changes,
        "versionChanges": comparison.version_changes,
        "steps": comparison.steps,
    }