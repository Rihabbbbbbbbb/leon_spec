"""HTTP API of the multi-supplier TDR technical benchmark (technical offers only)."""
from __future__ import annotations

import json
import os
import re
import secrets
import threading
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Body, Depends, File, Form, Header, HTTPException, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse, Response

from app.qa.tdr_bench_engine import BenchJobManager, BenchOptions, InputFile, answer_question
from app.qa.tdr_bench_ingest import SUPPORTED_SUFFIXES, detect_supplier, render_page_png
from app.qa.tdr_bench_llm import LLM
from app.qa.tdr_bench_report import build_docx, build_excel
from app.qa.tdr_bench_taxonomy import DOMAINS, PARAMETERS, weights_default

MAX_FILE_BYTES = 150 * 1024 * 1024
MAX_TOTAL_BYTES = 600 * 1024 * 1024
MAX_FILES = 40
CHUNK = 1024 * 1024
_manager: BenchJobManager | None = None
_manager_lock = threading.Lock()


def local_access(x_tdr_review_key: str | None = Header(default=None)) -> None:
    required = os.getenv("TDR_REVIEW_API_KEY")
    if required and not secrets.compare_digest((x_tdr_review_key or "").encode(), required.encode()):
        raise HTTPException(status_code=401, detail="Access key required")


router = APIRouter(prefix="/api/tdr-bench", tags=["tdr-bench"], dependencies=[Depends(local_access)])


def get_manager() -> BenchJobManager:
    global _manager
    with _manager_lock:
        if _manager is None:
            base = os.getenv("TDR_BENCH_DIR") or str(Path(__file__).resolve().parents[2] / "data" / "tdr_bench")
            from app.qa.tdr_bench_sync import blob_sync_from_env
            _manager = BenchJobManager(Path(base), sync=blob_sync_from_env())
        return _manager


def _llm(manager: BenchJobManager) -> LLM | None:
    try:
        llm = manager.llm_factory(manager.cache_dir)
    except Exception:
        return None
    return llm if llm is not None and llm.available else None


def _safe_name(name: str) -> str:
    name = (name or "").replace("\\", "/").rsplit("/", 1)[-1]
    name = re.sub(r"[^\w.\- ()&+]", "_", name).strip(" .")
    return name[:150]


def _json_field(raw: str, default: Any, label: str) -> Any:
    if not raw or not raw.strip():
        return default
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail=f"Invalid JSON for {label}")


def _job(manager: BenchJobManager, job_id: str) -> dict:
    try:
        return manager.public_state(job_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="Unknown job")


@router.get("/config")
def config() -> dict:
    llm = _llm(get_manager())
    return {
        "llmAvailable": llm is not None,
        "model": getattr(llm, "deployment", None),
        "domains": [{"key": d.key, "label_fr": d.label("fr"), "label_en": d.label("en"), "weight": d.weight}
                    for d in DOMAINS],
        "defaultWeights": weights_default(),
        "parameters": [{"key": p.key, "label_fr": p.label("fr"), "label_en": p.label("en"), "unit": p.unit}
                       for p in PARAMETERS],
        "limits": {"fileMB": MAX_FILE_BYTES // (1024 * 1024), "totalMB": MAX_TOTAL_BYTES // (1024 * 1024),
                   "files": MAX_FILES, "suffixes": sorted(SUPPORTED_SUFFIXES)},
    }


@router.post("/detect-supplier")
def detect(payload: dict = Body(...)) -> dict:
    names = payload.get("fileNames") if isinstance(payload, dict) else None
    if not isinstance(names, list):
        raise HTTPException(status_code=400, detail="fileNames list expected")
    return {"suppliers": {str(n): detect_supplier(str(n)) for n in names[:MAX_FILES]}}


@router.post("/jobs")
async def create_job(
    files: list[UploadFile] = File(...),
    suppliers: str = Form(""),
    language: str = Form("fr"),
    vision: str = Form("auto"),
    visionMaxPages: int = Form(40),
    projectContext: str = Form(""),
    focus: str = Form(""),
    weights: str = Form(""),
    title: str = Form(""),
    useLlm: bool = Form(True),
) -> dict:
    manager = get_manager()
    if not files:
        raise HTTPException(status_code=400, detail="No files")
    if len(files) > MAX_FILES:
        raise HTTPException(status_code=400, detail=f"Too many files (max {MAX_FILES})")
    supplier_list = _json_field(suppliers, [], "suppliers")
    if not isinstance(supplier_list, list):
        raise HTTPException(status_code=400, detail="suppliers must be a JSON list (one name per file)")
    weights_map = _json_field(weights, {}, "weights")
    if not isinstance(weights_map, dict):
        raise HTTPException(status_code=400, detail="weights must be a JSON object")

    job_id, job_dir = manager.new_job_dir()
    inputs: list[InputFile] = []
    total = 0
    try:
        for index, upload in enumerate(files):
            name = _safe_name(upload.filename or "")
            if not name or Path(name).suffix.lower() not in SUPPORTED_SUFFIXES:
                raise HTTPException(status_code=400, detail=f"Unsupported file type: {upload.filename} "
                                                            f"(accepted: {', '.join(sorted(SUPPORTED_SUFFIXES))})")
            doc_id = f"D{index + 1}"
            target = job_dir / "inputs" / f"{doc_id}__{name}"
            size = 0
            with target.open("wb") as handle:
                while True:
                    chunk = await upload.read(CHUNK)
                    if not chunk:
                        break
                    size += len(chunk)
                    total += len(chunk)
                    if size > MAX_FILE_BYTES:
                        raise HTTPException(status_code=413, detail=f"File exceeds {MAX_FILE_BYTES >> 20} MB: {name}")
                    if total > MAX_TOTAL_BYTES:
                        raise HTTPException(status_code=413, detail=f"Upload exceeds {MAX_TOTAL_BYTES >> 20} MB in total")
                    handle.write(chunk)
            if size == 0:
                raise HTTPException(status_code=400, detail=f"Empty file: {name}")
            supplier = str(supplier_list[index]).strip()[:80] if index < len(supplier_list) and supplier_list[index] else ""
            inputs.append(InputFile(doc_id, name, target, supplier or detect_supplier(name)))
        options = BenchOptions(language=language, vision=vision, vision_max_pages=visionMaxPages,
                               project_context=projectContext[:4000], focus=focus[:2000],
                               weights={**weights_default(), **weights_map}, use_llm=useLlm)
        return manager.submit(job_id, inputs, options, title)
    except HTTPException:
        import shutil
        shutil.rmtree(job_dir, ignore_errors=True)
        raise


@router.get("/jobs")
def list_jobs() -> dict:
    return {"jobs": get_manager().list_jobs()}


@router.get("/jobs/{job_id}")
def job_state(job_id: str) -> dict:
    return _job(get_manager(), job_id)


@router.get("/jobs/{job_id}/result")
def job_result(job_id: str) -> dict:
    manager = get_manager()
    _job(manager, job_id)
    try:
        return manager.result(job_id)
    except KeyError:
        raise HTTPException(status_code=409, detail="Result not available yet")


@router.put("/jobs/{job_id}/overrides")
def put_overrides(job_id: str, payload: dict = Body(...)) -> dict:
    manager = get_manager()
    _job(manager, job_id)
    try:
        return manager.set_overrides(job_id, payload)
    except KeyError:
        raise HTTPException(status_code=409, detail="Result not available yet")


@router.get("/jobs/{job_id}/export")
def export(job_id: str, format: str = "xlsx") -> Response:
    manager = get_manager()
    state = _job(manager, job_id)
    try:
        result = manager.result(job_id)
    except KeyError:
        raise HTTPException(status_code=409, detail="Result not available yet")
    stem = re.sub(r"[^\w\-]+", "_", state.get("title") or "benchmark")[:60].strip("_") or "benchmark"
    if format == "xlsx":
        content, media, ext = build_excel(result), \
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", "xlsx"
    elif format == "docx":
        content, media, ext = build_docx(result), \
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document", "docx"
    elif format == "json":
        content, media, ext = json.dumps(result, ensure_ascii=False, indent=1).encode("utf-8"), "application/json", "json"
    else:
        raise HTTPException(status_code=400, detail="format must be xlsx, docx or json")
    return Response(content, media_type=media,
                    headers={"Content-Disposition": f'attachment; filename="TDR_benchmark_{stem}.{ext}"'})


@router.get("/jobs/{job_id}/docs/{doc_id}/pages/{page}.png")
def page_png(job_id: str, doc_id: str, page: int) -> Response:
    manager = get_manager()
    _job(manager, job_id)
    if not re.fullmatch(r"D\d{1,3}", doc_id) or not 1 <= page <= 5000:
        raise HTTPException(status_code=400, detail="Invalid page reference")
    cache = manager.job_dir(job_id) / "pages" / f"{doc_id}_{page}.png"
    if cache.exists():
        return Response(cache.read_bytes(), media_type="image/png")
    try:
        path = manager.input_path(job_id, doc_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="Unknown document")
    if path.suffix.lower() != ".pdf":
        raise HTTPException(status_code=415, detail="Page preview is only available for PDF documents")
    try:
        png = render_page_png(path.read_bytes(), page, dpi=110)
    except IndexError:
        raise HTTPException(status_code=404, detail="Page out of range")
    cache.parent.mkdir(exist_ok=True)
    cache.write_bytes(png)
    return Response(png, media_type="image/png")


@router.get("/jobs/{job_id}/docs/{doc_id}/pages/{page}")
def page_text(job_id: str, doc_id: str, page: int) -> dict:
    manager = get_manager()
    _job(manager, job_id)
    try:
        return manager.page_text(job_id, doc_id, page)
    except KeyError:
        raise HTTPException(status_code=404, detail="Page not found")


@router.post("/jobs/{job_id}/ask")
async def ask(job_id: str, payload: dict = Body(...)) -> dict:
    manager = get_manager()
    _job(manager, job_id)
    question = str((payload or {}).get("question") or "").strip()
    if not question or len(question) > 2000:
        raise HTTPException(status_code=400, detail="Question required (max 2000 characters)")
    supplier_ids = payload.get("supplierIds") or None
    try:
        result = manager.result(job_id)
    except KeyError:
        raise HTTPException(status_code=409, detail="Result not available yet")
    if supplier_ids is not None and not isinstance(supplier_ids, list):
        raise HTTPException(status_code=400, detail="supplierIds must be a list")
    try:
        job_dir = await run_in_threadpool(manager.ensure_documents, job_id)
        return await run_in_threadpool(answer_question, job_dir, result, question,
                                       _llm(manager), supplier_ids)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.post("/jobs/{job_id}/cancel")
def cancel(job_id: str) -> dict:
    manager = get_manager()
    _job(manager, job_id)
    return manager.cancel(job_id)


@router.delete("/jobs/{job_id}")
async def delete(job_id: str) -> JSONResponse:
    manager = get_manager()
    _job(manager, job_id)
    await run_in_threadpool(manager.delete, job_id)
    return JSONResponse({"deleted": job_id})
