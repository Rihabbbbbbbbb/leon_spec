"""LEON — consolidated Databricks Apps entry point.

Databricks Apps run a single HTTP server bound to ``DATABRICKS_APP_PORT`` on
``0.0.0.0``. LEON ships three separate FastAPI apps upstream
(``app/main.py``, ``app/conformity_server.py``, ``app/qa_server.py``); this
module merges the two user-facing surfaces into ONE app:

- ``/``      → Conformity Matrix Analyzer + Spec Validation UI
- ``/qa``    → Spec Q&A Assistant UI
- ``/api/*`` → the shared REST API (``app/qa/route.py``): ask, validate, upload,
               files, conformity(+excel/batch/compare/powerbi), spec-to-matrix, ...
- ``/health``→ health check

Authentication is handled entirely by the Databricks Apps platform (workspace
SSO + app CAN_USE permission); there is no application-level API key.
"""
from __future__ import annotations

import os
from pathlib import Path

# IMPORTANT: configure writable/ephemeral paths before importing app.* modules.
import databricks_config  # noqa: F401  (side effect: configure_for_databricks())

from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse

from app.qa.route import router as qa_router

APP_DIR = Path(__file__).resolve().parent
CONFORMITY_UI = APP_DIR / "app" / "conformity_ui" / "index.html"
QA_UI = APP_DIR / "app" / "qa_ui" / "index.html"

app = FastAPI(
    title="LEON — Quality Analysis (Databricks)",
    version="1.0.0",
    description="Conformity matrix analysis, spec validation, and grounded Q&A.",
)

# All REST endpoints live under /api (matches the upstream Azure Function surface).
app.include_router(qa_router)


@app.get("/", include_in_schema=False)
def conformity_ui() -> FileResponse:
    """Serve the Conformity Matrix Analyzer / Spec Validation UI."""
    return FileResponse(CONFORMITY_UI)


@app.get("/qa", include_in_schema=False)
def qa_ui() -> FileResponse:
    """Serve the Spec Q&A Assistant UI."""
    return FileResponse(QA_UI)


@app.get("/health", include_in_schema=False)
@app.get("/healthz", include_in_schema=False)
def health() -> JSONResponse:
    """Liveness/readiness probe."""
    return JSONResponse(
        {
            "status": "ok",
            "service": "leon-quality-analysis",
            "surfaces": ["/", "/qa", "/api"],
        }
    )


if __name__ == "__main__":
    import uvicorn

    # Databricks injects the port; fall back to 8000 for local dev.
    port = int(os.environ.get("DATABRICKS_APP_PORT", "8000"))
    uvicorn.run(app, host="0.0.0.0", port=port)
