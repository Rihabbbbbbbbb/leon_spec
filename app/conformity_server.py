"""
Standalone entry point for the Conformity Matrix Analyzer UI.

Runs a FastAPI server that:
- serves the conformity matrix UI at /
- exposes all conformity API endpoints under /api

Endpoints:
  GET  /                          → Conformity Matrix UI
  GET  /health                    → Health check
  POST /api/conformity-report     → Upload + analyze + PDF report
  POST /api/conformity-excel      → Upload + analyze + Excel report
  POST /api/conformity            → Analyze by fileName (JSON body)
  POST /api/conformity-compare    → Compare 2+ matrices
  POST /api/conformity-powerbi    → Generate Power BI dataset
  POST /api/upload-conformity     → Upload matrix file
  GET  /api/conformity-files      → List uploaded matrix files

Run:
    python -m app.conformity_server
or:
    uvicorn app.conformity_server:app --reload --port 8012
"""
from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.qa.route import router as qa_router
from app.qa.tdr_bench_route import router as tdr_bench_router
from app.qa.tdr_review_route import router as tdr_review_router

UI_DIR = Path(__file__).resolve().parent / "conformity_ui"

app = FastAPI(title="LEON — Conformity Matrix Analyzer", version="1.0.0")

# API routes (all under /api)
app.include_router(qa_router)
app.include_router(tdr_review_router)
app.include_router(tdr_bench_router)


@app.get("/tdr-bench-ui.js")
def tdr_bench_ui_script():
    return FileResponse(UI_DIR / "tdr_bench.js", media_type="application/javascript",
                        headers={"Cache-Control": "no-cache"})


@app.get("/tdr-review-ui.js")
def tdr_review_ui_script():
    return FileResponse(
        Path(__file__).parent / "conformity_ui" / "tdr_review.js",
        media_type="application/javascript",
    )


@app.get("/")
def serve_ui() -> FileResponse:
    """Serve the conformity matrix UI."""
    return FileResponse(UI_DIR / "index.html")


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "service": "conformity-matrix-analyzer"}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app.conformity_server:app", host="0.0.0.0", port=8012, reload=True)
