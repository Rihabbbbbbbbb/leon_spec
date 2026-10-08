"""API routes for reviewable conformity-matrix evidence links."""
from __future__ import annotations

import base64
from pathlib import Path
from tempfile import TemporaryDirectory
from zipfile import BadZipFile

from fastapi import APIRouter, Body, File, HTTPException, UploadFile
from fastapi.concurrency import run_in_threadpool
from openpyxl.utils.exceptions import InvalidFileException
from pptx.exc import PackageNotFoundError
from PyPDF2.errors import PdfReadError

from app.qa.conformity_analyzer import analyze_conformity_matrix
from app.qa.conformity_evidence_report import generate_evidence_excel
from app.qa.pdf_evidence import analyze_matrix_against_pdf
from app.qa.pptx_evidence_matching import analyze_matrix_against_pptx

router = APIRouter(prefix="/api", tags=["conformity-evidence"])
MAX_UPLOAD_BYTES = 40 * 1024 * 1024
MATRIX_EXTENSIONS = {".ods", ".xlsx", ".xlsm"}


async def _read_upload(file: UploadFile, field: str, allowed: set[str]) -> tuple[str, bytes]:
    name = (file.filename or "").replace("\\", "/").rsplit("/", 1)[-1]
    suffix = Path(name).suffix.lower()
    if field == "PowerPoint evidence" and suffix == ".ppt":
        raise HTTPException(
            status_code=400,
            detail="Legacy .ppt files are not supported; convert legacy .ppt files to .pptx or PDF first.",
        )
    if not name or suffix not in allowed:
        accepted = ", ".join(sorted(allowed))
        raise HTTPException(status_code=400, detail=f"Unsupported {field} file. Accepted: {accepted}")
    content = await file.read(MAX_UPLOAD_BYTES + 1)
    if len(content) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail=f"{field.capitalize()} file exceeds 40 MB")
    if not content:
        raise HTTPException(status_code=400, detail=f"{field.capitalize()} file is empty")
    return name, content


def _analyze(matrix_name: str, matrix_bytes: bytes, evidence_name: str,
             evidence_bytes: bytes, *, powerpoint: bool) -> dict:
    try:
        with TemporaryDirectory(prefix="aeris_evidence_") as folder:
            matrix_path = Path(folder) / matrix_name
            evidence_path = Path(folder) / evidence_name
            matrix_path.write_bytes(matrix_bytes)
            evidence_path.write_bytes(evidence_bytes)
            matrix = analyze_conformity_matrix(str(matrix_path), matrix_name)
            # Older deployed analyzer versions treat every parsed item as a requirement.
            for item in matrix.items:
                if not hasattr(item, "is_requirement"):
                    item.is_requirement = True
            analysis = (
                analyze_matrix_against_pptx(matrix, evidence_path)
                if powerpoint
                else analyze_matrix_against_pdf(matrix, evidence_path)
            )
    except (BadZipFile, InvalidFileException, PackageNotFoundError, PdfReadError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=f"Evidence analysis failed: {exc}") from exc

    analysis["reportExcel"] = base64.b64encode(generate_evidence_excel(analysis)).decode("ascii")
    return analysis


@router.post("/conformity-pdf-evidence")
async def conformity_pdf_evidence(
    matrix: UploadFile = File(...),
    tdr: UploadFile = File(...),
) -> dict:
    matrix_name, matrix_bytes = await _read_upload(matrix, "matrix", MATRIX_EXTENSIONS)
    evidence_name, evidence_bytes = await _read_upload(tdr, "PDF evidence", {".pdf"})
    return await run_in_threadpool(
        _analyze, matrix_name, matrix_bytes, evidence_name, evidence_bytes,
        powerpoint=False,
    )


@router.post("/conformity-pptx-evidence")
async def conformity_pptx_evidence(
    matrix: UploadFile = File(...),
    pptx: UploadFile = File(...),
) -> dict:
    matrix_name, matrix_bytes = await _read_upload(matrix, "matrix", MATRIX_EXTENSIONS)
    evidence_name, evidence_bytes = await _read_upload(
        pptx, "PowerPoint evidence", {".pptx"},
    )
    return await run_in_threadpool(
        _analyze, matrix_name, matrix_bytes, evidence_name, evidence_bytes,
        powerpoint=True,
    )


@router.post("/conformity-pdf-evidence/report")
def conformity_evidence_report(payload: dict = Body(...)) -> dict:
    analysis = payload.get("analysis")
    if not isinstance(analysis, dict):
        raise HTTPException(status_code=422, detail="An analysis object is required")
    report = generate_evidence_excel(analysis)
    return {"reportExcel": base64.b64encode(report).decode("ascii")}
