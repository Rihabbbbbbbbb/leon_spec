"""
LEON — Databricks Apps entry point (single FastAPI process, single port).

Consolidates the three local entry points into one app:
  GET /        → Conformity Matrix Analyzer UI   (was conformity_server.py :8012)
  GET /qa      → Spec Q&A Assistant UI           (was qa_server.py :8010)
  /api/*       → shared LEON API (app/qa/route.py router, 19 routes)
  GET /health  → health + Azure service configuration status

Databricks Apps injects DATABRICKS_APP_PORT and expects the server bound
to 0.0.0.0. Locally you can run it with:
    python databricks_main.py            (defaults to port 8080)

IMPORTANT — import order matters:
  1. BESTANDARD_AUTO_RESOLVE is disabled via env var BEFORE `app.config`
     is imported (config reads it at import time). beStandard is a
     Stellantis intranet service unreachable from Databricks.
  2. Writable paths (uploads, generated index) are redirected to /tmp
     BEFORE `app.qa.route` is imported, because some modules (e.g.
     app/bestandard_ingest.py) derive their paths from app.config at
     import time. The bundled data/refs/ documents are copied into that
     redirected folder so the Q&A index still finds them.
"""
from __future__ import annotations

import os
import shutil
from pathlib import Path

# --- 1. Environment overrides that must happen before any app.* import ----------
# beStandard (bestandard.fcagroup.com) is a Stellantis corporate-intranet
# service: unreachable from Databricks serverless compute. Disable auto-resolve
# so validation does not hang on network timeouts.
os.environ.setdefault("BESTANDARD_AUTO_RESOLVE", "false")

IS_DATABRICKS = bool(os.getenv("DATABRICKS_APP_PORT"))

import app.config as _config  # noqa: E402  (import after env overrides)

if IS_DATABRICKS:
    # Databricks Apps deploy the source tree read-only; /tmp is the writable
    # ephemeral volume (user-approved: ephemeral storage, no UC Volume).
    _EPHEMERAL = Path(os.getenv("LEON_EPHEMERAL_DIR", "/tmp/leon"))
    # Bundled reference documents (spec template, writing guide, conformity
    # matrix template) ship read-only alongside the app source.
    _BUNDLED_REFS = _config.REFS_DIR

    _config.DATA_DIR = _EPHEMERAL / "data"
    _config.UPLOADS_DIR = _config.DATA_DIR / "uploads"
    _config.REFS_DIR = _config.DATA_DIR / "refs"
    _config.INDEX_PATH = _config.DATA_DIR / "reference_index.json"

    # REFS_DIR must follow DATA_DIR, and the bundled docs must be seeded.
    # app/qa/retrieval.py::discover_accessible_files looks for specifications
    # under DATA_DIR/"refs" and app/qa/spec_to_matrix.py resolves its template
    # from REFS_DIR. Redirecting DATA_DIR without seeding therefore empties the
    # Q&A index and every question answers "No accessible specification files
    # are currently indexed". Copying the read-only bundled files into the
    # writable dir restores parity with the validated local layout.
    _config.UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
    _config.REFS_DIR.mkdir(parents=True, exist_ok=True)
    if _BUNDLED_REFS.is_dir():
        for _ref in _BUNDLED_REFS.iterdir():
            _target = _config.REFS_DIR / _ref.name
            if _ref.is_file() and not _target.exists():
                shutil.copy2(_ref, _target)

    # The Conformity endpoints resolve their files with paths RELATIVE to the
    # current working directory (e.g. Path("data/uploads") in app/qa/route.py),
    # whereas the Q&A endpoints use the absolute UPLOADS_DIR. Locally the CWD is
    # the project root so both point at <project>/data and agree; on Databricks
    # they would diverge and the Conformity UI would never find an uploaded
    # matrix. Moving the working directory to the ephemeral root makes
    # "data/uploads" resolve to exactly the same directory as UPLOADS_DIR,
    # restoring the validated local behaviour. Module imports are unaffected:
    # sys.path holds absolute entries and every bundled asset is addressed
    # through APP_DIR / __file__.
    os.chdir(_EPHEMERAL)

from fastapi import FastAPI  # noqa: E402
from fastapi.responses import FileResponse  # noqa: E402

from app.qa.route import router as qa_router  # noqa: E402  (after path patch)

APP_DIR = Path(__file__).resolve().parent

app = FastAPI(
    title="LEON — Spec AI (Databricks)",
    version="1.0.0",
    description="AI quality analysis for Stellantis mechatronics CTS specifications",
)

# Shared LEON API — router already carries the /api prefix (19 routes).
app.include_router(qa_router)


@app.get("/")
def conformity_ui() -> FileResponse:
    """Conformity Matrix Analyzer UI (also serves spec validation via tabs)."""
    return FileResponse(APP_DIR / "app" / "conformity_ui" / "index.html")


@app.get("/qa")
def qa_ui() -> FileResponse:
    """Spec Q&A Assistant UI."""
    return FileResponse(APP_DIR / "app" / "qa_ui" / "index.html")


@app.get("/health")
def health() -> dict:
    """Health + configuration status.

    `azure_openai` / `azure_search` show whether the credentials reached the
    app. If either is false, LEON silently falls back to keyword retrieval /
    deterministic validation — check the secret scope wiring if that happens.
    """
    return {
        "status": "ok",
        "service": "leon-databricks",
        "mode": "databricks" if IS_DATABRICKS else "local",
        "azure_openai_configured": bool(
            _config.AZURE_OPENAI_ENDPOINT and _config.AZURE_OPENAI_API_KEY
        ),
        "azure_search_configured": bool(
            _config.AZURE_SEARCH_ENDPOINT and _config.AZURE_SEARCH_API_KEY
        ),
        "bestandard_auto_resolve": _config.BESTANDARD_AUTO_RESOLVE,
        "uploads_dir": str(_config.UPLOADS_DIR),
    }


if __name__ == "__main__":
    import uvicorn

    port = int(os.getenv("DATABRICKS_APP_PORT", os.getenv("UVICORN_PORT", "8080")))
    uvicorn.run(app, host="0.0.0.0", port=port)
