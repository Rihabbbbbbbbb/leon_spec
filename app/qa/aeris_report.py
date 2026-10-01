"""
Excel synthesis report for an AERIS matrix↔TDR cross-check.
"""
from __future__ import annotations

import io
from typing import Dict, List

from openpyxl import Workbook
from openpyxl.chart import PieChart, Reference
from openpyxl.chart.series import DataPoint
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter


_STATUS_FILL = {
    "CONFORME": "C6EFCE",
    "NON_CONFORME": "FFC7CE",
    "PARTIELLEMENT_CONFORME": "FCE4D6",
    "DEVIATION": "FFF2CC",
    "PREUVE_INSUFFISANTE": "DDEBF7",
    "MANQUANT": "E7E6E6",
    "NA": "D9D9D9",
}
_STATUS_FONT = {
    "CONFORME": "006100",
    "NON_CONFORME": "9C0006",
    "PARTIELLEMENT_CONFORME": "C65911",
    "DEVIATION": "9C6500",
    "PREUVE_INSUFFISANTE": "2F5496",
    "MANQUANT": "595959",
    "NA": "3F3F3F",
}
_COHERENCE_FILL = {
    "ALIGNED": "C6EFCE",
    "MATRIX_TOO_OPTIMISTIC": "FFC7CE",
    "MATRIX_TOO_PESSIMISTIC": "FFF2CC",
    "UNVERIFIABLE": "DDEBF7",
    "MATRIX_SILENT": "FCE4D6",
    "MATRIX_SAYS_NA": "D9D9D9",
}


def generate_aeris_excel(report: dict) -> bytes:
    """Build a 4-sheet workbook: Synthesis, Findings, Risks, Conditions."""
    wb = Workbook()
    header_fill = PatternFill("solid", fgColor="0B2545")
    header_font = Font(color="FFFFFF", bold=True, size=11)
    title_font = Font(color="0B2545", bold=True, size=14)
    thin = Border(
        left=Side(style="thin"), right=Side(style="thin"),
        top=Side(style="thin"), bottom=Side(style="thin"),
    )
    wrap = Alignment(wrap_text=True, vertical="top")

    summary = report.get("summary") or {}
    items: List[Dict] = report.get("items") or []

    ws = wb.active
    ws.title = "Synthesis"
    ws["A1"] = "AERIS — Matrix ↔ TDR Conformity Synthesis"
    ws["A1"].font = title_font
    ws.merge_cells("A1:D1")
    ws["A3"] = "Matrix"
    ws["B3"] = report.get("matrixFile", "")
    ws["A4"] = "Evidence"
    ws["B4"] = ", ".join(report.get("evidenceFiles") or [])
    ws["A5"] = "Conformity rate (judged items)"
    ws["B5"] = f"{summary.get('conformityRate', 0)}%"
    ws["A6"] = "Evidence coverage"
    ws["B6"] = f"{summary.get('evidenceCoverage', 0)}%"

    labels = [
        ("CONFORME", "conforme"),
        ("NON_CONFORME", "nonConforme"),
        ("PARTIELLEMENT_CONFORME", "partiel"),
        ("DEVIATION", "deviation"),
        ("PREUVE_INSUFFISANTE", "preuveInsuffisante"),
        ("MANQUANT", "manquant"),
        ("NA", "na"),
    ]
    ws["A8"] = "Status"
    ws["B8"] = "Count"
    for col in ("A8", "B8"):
        ws[col].fill = header_fill
        ws[col].font = header_font
    for i, (label, key) in enumerate(labels, 9):
        ws[f"A{i}"] = label
        ws[f"B{i}"] = summary.get(key, 0)
        fill = PatternFill("solid", fgColor=_STATUS_FILL.get(label, "FFFFFF"))
        font = Font(color=_STATUS_FONT.get(label, "000000"))
        ws[f"A{i}"].fill = fill
        ws[f"B{i}"].fill = fill
        ws[f"A{i}"].font = font
        ws[f"B{i}"].font = font

    ws["A17"] = "Matrix vs evidence coherence"
    ws["A17"].font = Font(bold=True, color="0B2545")
    ws["A18"] = "Aligned"
    ws["B18"] = summary.get("aligned", 0)
    ws["A19"] = "Matrix too optimistic (OK but evidence fails)"
    ws["B19"] = summary.get("matrixTooOptimistic", 0)
    ws["A20"] = "Matrix too pessimistic (NOK but evidence passes)"
    ws["B20"] = summary.get("matrixTooPessimistic", 0)
    ws["A21"] = "Unverifiable"
    ws["B21"] = summary.get("unverifiable", 0)

    # Pie source (hidden-ish) + chart
    ws["D8"] = "Status"
    ws["E8"] = "Count"
    pie_row = 9
    for label, key in labels:
        n = summary.get(key, 0)
        if n:
            ws[f"D{pie_row}"] = label
            ws[f"E{pie_row}"] = n
            pie_row += 1
    if pie_row > 9:
        pie = PieChart()
        pie.title = "AERIS verdict distribution"
        pie.width = 14
        pie.height = 10
        pie.add_data(Reference(ws, min_col=5, min_row=8, max_row=pie_row - 1), titles_from_data=True)
        pie.set_categories(Reference(ws, min_col=4, min_row=9, max_row=pie_row - 1))
        colors = ["157347", "b3261e", "c65911", "9C6500", "2F5496", "595959", "6c757d"]
        for i, color in enumerate(colors):
            if i < pie_row - 9:
                pt = DataPoint(idx=i)
                pt.graphicalProperties.solidFill = color
                pie.series[0].data_points.append(pt)
        ws.add_chart(pie, "G3")

    notes = report.get("notes") or []
    if notes:
        ws["A23"] = "Notes"
        ws["A23"].font = Font(bold=True)
        ws["A24"] = " ".join(notes)
        ws["A24"].alignment = wrap

    ws.column_dimensions["A"].width = 48
    ws.column_dimensions["B"].width = 55
    ws.column_dimensions["D"].width = 28

    # ── Findings ──────────────────────────────────────────────────
    wf = wb.create_sheet("Findings")
    headers = [
        "REQ-ID", "Final status", "Matrix", "Evidence", "Coherence",
        "Confidence", "Target", "Supplier result", "Gap",
        "Location", "Evidence file", "Rationale", "Description",
    ]
    for ci, h in enumerate(headers, 1):
        cell = wf.cell(1, ci, h)
        cell.fill = header_fill
        cell.font = header_font
        cell.border = thin

    keys = [
        "req_id", "final_status", "matrix_status", "evidence_status", "coherence",
        "confidence", "target", "supplier_result", "gap",
        "evidence_location", "evidence_file", "rationale", "description",
    ]
    for ri, item in enumerate(items, 2):
        status = item.get("final_status", "")
        fill = PatternFill("solid", fgColor=_STATUS_FILL.get(status, "FFFFFF"))
        font = Font(color=_STATUS_FONT.get(status, "000000"))
        for ci, key in enumerate(keys, 1):
            cell = wf.cell(ri, ci, item.get(key, ""))
            cell.fill = fill
            cell.font = font
            cell.border = thin
            cell.alignment = wrap
        # Coherence tint on that column
        coh = item.get("coherence", "")
        if coh in _COHERENCE_FILL:
            wf.cell(ri, 5).fill = PatternFill("solid", fgColor=_COHERENCE_FILL[coh])

    widths = [18, 24, 12, 24, 24, 12, 28, 40, 16, 16, 28, 60, 40]
    for i, w in enumerate(widths, 1):
        wf.column_dimensions[get_column_letter(i)].width = w
    wf.freeze_panes = "A2"
    if items:
        wf.auto_filter.ref = f"A1:M{len(items) + 1}"
    wf.row_dimensions[1].height = 22

    # ── Risks ─────────────────────────────────────────────────────
    wr = wb.create_sheet("Top risks")
    for ci, h in enumerate(["#", "Domain", "Items", "Hard fails", "Example REQ-IDs"], 1):
        cell = wr.cell(1, ci, h)
        cell.fill = header_fill
        cell.font = header_font
    for ri, risk in enumerate(report.get("topRisks") or [], 2):
        wr.cell(ri, 1, ri - 1)
        wr.cell(ri, 2, risk.get("domain", ""))
        wr.cell(ri, 3, risk.get("count", 0))
        wr.cell(ri, 4, risk.get("hardFails", 0))
        wr.cell(ri, 5, ", ".join(risk.get("examples") or []))
        if risk.get("hardFails"):
            wr.cell(ri, 4).fill = PatternFill("solid", fgColor="FFC7CE")
    for i, w in enumerate([6, 28, 12, 14, 50], 1):
        wr.column_dimensions[get_column_letter(i)].width = w

    # ── Per-condition ─────────────────────────────────────────────
    wc = wb.create_sheet("Conditions")
    for ci, h in enumerate(
        ["REQ-ID", "Condition", "Target", "Measured", "Status", "Gap"], 1
    ):
        cell = wc.cell(1, ci, h)
        cell.fill = header_fill
        cell.font = header_font
    row = 2
    for item in items:
        for v in item.get("condition_verdicts") or []:
            st = v.get("status", "")
            fill = PatternFill("solid", fgColor=_STATUS_FILL.get(st, "FFFFFF"))
            vals = [
                item.get("req_id", ""),
                v.get("condition", ""),
                v.get("target", ""),
                v.get("measured", ""),
                st,
                v.get("gap", ""),
            ]
            for ci, val in enumerate(vals, 1):
                cell = wc.cell(row, ci, val)
                cell.fill = fill
                cell.border = thin
            row += 1
    for i, w in enumerate([18, 28, 24, 28, 20, 14], 1):
        wc.column_dimensions[get_column_letter(i)].width = w
    wc.freeze_panes = "A2"

    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()
