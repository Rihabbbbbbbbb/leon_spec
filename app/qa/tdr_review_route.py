"""Versioned local review workflow; the existing AERIS API remains unchanged."""
import base64
import hashlib
import io
import json
import logging
import os
from pathlib import Path
import secrets
import tempfile
import zipfile

from fastapi import APIRouter, Depends, File, Form, Header, HTTPException, Query, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import Response
from pydantic import ValidationError

from app.qa.aeris_report import generate_aeris_excel
from app.qa.tdr_review import analyze_review, input_fingerprint
from app.qa.tdr_review_models import AnalysisContext, ReviewDecision
from app.qa.tdr_review_store import ReviewConflict, ReviewStore, review_store


logger = logging.getLogger(__name__)
MAX_FILE_BYTES = 40 * 1024 * 1024
MAX_TOTAL_BYTES = 100 * 1024 * 1024
MAX_EVIDENCE_FILES = 12
MAX_ARCHIVE_BYTES = 200 * 1024 * 1024


def local_access(x_tdr_review_key: str | None = Header(default=None)) -> None:
    required = os.getenv("TDR_REVIEW_API_KEY")
    if required and not secrets.compare_digest(
        (x_tdr_review_key or "").encode(), required.encode(),
    ):
        raise HTTPException(status_code=401, detail="TDR review access key required")


router = APIRouter(
    prefix="/api/tdr-review", tags=["tdr-review"],
    dependencies=[Depends(local_access)],
)


async def read_upload(file: UploadFile, accepted: set[str],
                      require_archive: bool = False) -> tuple[str, bytes]:
    name = (file.filename or "").replace("\\", "/").rsplit("/", 1)[-1]
    if not name or Path(name).suffix.lower() not in accepted:
        raise HTTPException(status_code=400, detail="Unsupported or missing filename: " + name)
    content = await file.read(MAX_FILE_BYTES + 1)
    if len(content) > MAX_FILE_BYTES:
        raise HTTPException(status_code=413, detail=f"File exceeds 40 MB: {name}")
    if not content:
        raise HTTPException(status_code=400, detail=f"Empty file: {name}")
    if Path(name).suffix.lower() in {".xlsx", ".xlsm", ".ods", ".pptx", ".docx"}:
        try:
            with zipfile.ZipFile(io.BytesIO(content)) as archive:
                entries = archive.infolist()
                if len(entries) > 10000 or sum(e.file_size for e in entries) > MAX_ARCHIVE_BYTES:
                    raise HTTPException(status_code=413, detail=f"Expanded archive exceeds processing limits: {name}")
                if any(e.flag_bits & 1 for e in entries):
                    raise HTTPException(status_code=422, detail=f"Encrypted archives are not supported: {name}")
        except zipfile.BadZipFile:
            if require_archive:
                raise HTTPException(status_code=422, detail=f"Invalid spreadsheet archive: {name}")
    return name, content


def with_export(payload: dict) -> dict:
    return {
        **payload,
        "reportExcel": base64.b64encode(generate_aeris_excel(payload)).decode("ascii"),
        "reportFileName": Path(payload["matrixFile"]).stem + "_TDR_review.xlsx",
    }


@router.post("/analyze")
async def analyze(
    matrix: UploadFile = File(...),
    evidence: list[UploadFile] = File(...),
    context: str = Form(default="{}"),
    store: ReviewStore = Depends(review_store),
) -> dict:
    try:
        parsed_context = AnalysisContext.model_validate_json(context)
    except ValidationError:
        raise HTTPException(status_code=422, detail="Invalid scope context; use the documented AnalysisContext fields")
    matrix_input = await read_upload(matrix, {".xlsx", ".xlsm", ".ods"}, require_archive=True)
    if not evidence or len(evidence) > MAX_EVIDENCE_FILES:
        raise HTTPException(status_code=400, detail="Upload between 1 and 12 evidence documents")
    evidence_inputs = []
    names = set()
    hashes = set()
    total = len(matrix_input[1])
    for file in evidence:
        name, content = await read_upload(file, {".pdf", ".pptx", ".docx", ".txt"})
        digest = hashlib.sha256(content).hexdigest()
        if name.casefold() in names or digest in hashes:
            raise HTTPException(status_code=409, detail=f"Duplicate evidence filename/content: {name}")
        names.add(name.casefold())
        hashes.add(digest)
        total += len(content)
        if total > MAX_TOTAL_BYTES:
            raise HTTPException(status_code=413, detail="Combined upload exceeds 100 MB")
        evidence_inputs.append((name, content))

    def execute():
        with tempfile.TemporaryDirectory(prefix="tdr_review_") as folder:
            path = Path(folder) / ("matrix" + Path(matrix_input[0]).suffix.lower())
            path.write_bytes(matrix_input[1])
            payload = analyze_review(str(path), matrix_input, evidence_inputs, parsed_context)
            fingerprint = input_fingerprint(matrix_input, evidence_inputs, parsed_context)
            # Validate export before persisting a success-shaped result.
            generate_aeris_excel(payload)
            saved, reused = store.create(fingerprint, payload)
            return {**with_export(saved), "reusedCase": reused}
    try:
        return await run_in_threadpool(execute)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    except Exception:
        logger.exception("tdr_analysis_failed")
        raise HTTPException(status_code=500, detail="TDR analysis failed; consult server logs")


@router.get("/cases")
def list_cases(store: ReviewStore = Depends(review_store), limit: int = Query(25, ge=1, le=100),
               offset: int = Query(0, ge=0)) -> dict:
    return {**store.list_cases(limit, offset), "localMvp": True, "identityVerified": False}


@router.get("/cases/{case_id}")
def get_case(case_id: str, store: ReviewStore = Depends(review_store)) -> dict:
    try:
        return with_export(store.get(case_id))
    except KeyError:
        raise HTTPException(status_code=404, detail="Review case not found")


@router.post("/cases/{case_id}/requirements/{row_key}/decisions")
def decide(case_id: str, row_key: str, decision: ReviewDecision,
           store: ReviewStore = Depends(review_store)) -> dict:
    try:
        return with_export(store.decide(case_id, row_key, decision))
    except KeyError:
        raise HTTPException(status_code=404, detail="Case or requirement row not found")
    except ReviewConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc))


@router.get("/cases/{case_id}/export")
def export_case(case_id: str, format: str = "json",
                store: ReviewStore = Depends(review_store)) -> Response:
    try:
        payload = store.get(case_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="Review case not found")
    if format == "json":
        body = json.dumps(payload, ensure_ascii=False, allow_nan=False).encode()
        media = "application/json"
    elif format == "xlsx":
        body = generate_aeris_excel(payload)
        media = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    else:
        raise HTTPException(status_code=400, detail="Export format must be json or xlsx")
    return Response(body, media_type=media, headers={
        "Content-Disposition": f'attachment; filename="tdr-review-{case_id}.{format}"',
        "Cache-Control": "no-store",
    })


@router.delete("/cases/{case_id}")
def delete_case(case_id: str, confirm: bool = False,
                store: ReviewStore = Depends(review_store)) -> dict:
    if not confirm:
        raise HTTPException(status_code=400, detail="Explicit deletion confirmation required")
    try:
        store.delete(case_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="Review case not found")
    return {"deleted": True, "caseId": case_id}
