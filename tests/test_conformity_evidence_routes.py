from __future__ import annotations

import base64
from io import BytesIO

from fastapi.testclient import TestClient
from openpyxl import Workbook, load_workbook
from pptx import Presentation
from pptx.util import Inches
from PyPDF2 import PdfWriter

from app.conformity_server import app


client = TestClient(app)


def _matrix_bytes() -> bytes:
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Conformity"
    sheet.append(["Requirement", "Description", "Conformity FNR", "Comments FNR"])
    sheet.append(["REQ-12345", "The display shall operate at 12 V nominal.", "OK", "Supplier says verified."])
    output = BytesIO()
    workbook.save(output)
    return output.getvalue()


def test_pdf_evidence_endpoint_returns_citations_and_excel_report():
    writer = PdfWriter()
    writer.add_blank_page(width=612, height=792)
    pdf = BytesIO()
    writer.write(pdf)

    response = client.post(
        "/api/conformity-pdf-evidence",
        files={
            "matrix": ("supplier.xlsx", _matrix_bytes(), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
            "tdr": ("technical-report.pdf", pdf.getvalue(), "application/pdf"),
        },
    )

    assert response.status_code == 200, response.text
    result = response.json()
    assert result["tdrFile"] == "technical-report.pdf"
    assert result["summary"]["complianceVerdictsMade"] == 0
    assert result["decisionSummary"]["supplierDeclared"]["OK"] == 1
    workbook_bytes = base64.b64decode(result["reportExcel"])
    report = load_workbook(BytesIO(workbook_bytes), data_only=True)
    assert report["Requirements"]["B2"].value == "REQ-12345"


def test_pptx_evidence_endpoint_returns_slide_citations_and_excel_report():
    presentation = Presentation()
    slide = presentation.slides.add_slide(presentation.slide_layouts[6])
    textbox = slide.shapes.add_textbox(Inches(1), Inches(1), Inches(6), Inches(1))
    textbox.name = "Requirement measurement"
    textbox.text_frame.text = "REQ-12345 measured input voltage 12 V nominal"
    pptx = BytesIO()
    presentation.save(pptx)

    response = client.post(
        "/api/conformity-pptx-evidence",
        files={
            "matrix": ("supplier.xlsx", _matrix_bytes(), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
            "pptx": ("technical-presentation.pptx", pptx.getvalue(), "application/vnd.openxmlformats-officedocument.presentationml.presentation"),
        },
    )

    assert response.status_code == 200, response.text
    result = response.json()
    assert result["presentationFile"] == "technical-presentation.pptx"
    assert result["requirements"][0]["matrixRowNumber"] == 2
    assert result["requirements"][0]["evidence"][0]["slide_number"] == 1
    assert result["requirements"][0]["evidence"][0]["file_name"] == "technical-presentation.pptx"
    assert result["summary"]["complianceVerdictsMade"] == 0
    report = load_workbook(BytesIO(base64.b64decode(result["reportExcel"])), data_only=True)
    assert report["Evidence Candidates"]["G2"].value == 1


def test_pptx_route_rejects_invalid_package_with_clear_400():
    response = client.post(
        "/api/conformity-pptx-evidence",
        files={
            "matrix": ("supplier.xlsx", _matrix_bytes(), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
            "pptx": ("bad.pptx", b"PK\x03\x04not a real presentation", "application/vnd.openxmlformats-officedocument.presentationml.presentation"),
        },
    )

    assert response.status_code == 422
    assert "Evidence analysis failed" in response.json()["detail"]


def test_pptx_route_rejects_legacy_extension_with_clear_message():
    response = client.post(
        "/api/conformity-pptx-evidence",
        files={
            "matrix": ("matrix.xlsm", _matrix_bytes(), "application/vnd.ms-excel.sheet.macroEnabled.12"),
            "pptx": ("presentation.ppt", b"legacy", "application/vnd.ms-powerpoint"),
        },
    )

    assert response.status_code == 400
    assert "convert legacy .ppt files" in response.json()["detail"]


def test_evidence_report_regeneration_endpoint_returns_xlsx():
    response = client.post(
        "/api/conformity-pdf-evidence/report",
        json={"analysis": {
            "matrixFile": "supplier.xlsx",
            "tdrFile": "report.pdf",
            "summary": {"requirements": 0},
            "requirements": [],
        }},
    )

    assert response.status_code == 200, response.text
    payload = base64.b64decode(response.json()["reportExcel"])
    assert payload.startswith(b"PK")
