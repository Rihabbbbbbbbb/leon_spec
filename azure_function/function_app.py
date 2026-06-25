"""
Azure Function App — LEON Spec Q&A + Validation Assistant.

This is the serverless entry point for the Copilot Studio integration.
Exposes HTTP-triggered endpoints that Copilot Studio calls via Power Automate.

Endpoints:
  POST /api/ask        — Q&A: answer questions about uploaded specs
  POST /api/validate   — Validation: validate a spec against CTS template
  POST /api/upload     — Upload: receive a spec file for indexing
  GET  /api/files      — List accessible files
  GET  /api/health     — Health check

Architecture:
  Copilot Studio (Teams) → Power Automate (HTTP) → Azure Function → Backend Logic

Authentication: x-api-key header or AzureAD via function auth settings.
"""
import azure.functions as func
import logging
import json
import sys
import os
from pathlib import Path

# Auto-configure paths and settings for Azure Functions environment
try:
    import azure_config  # noqa: F401 — side-effect: configure_for_azure()
except ImportError:
    pass

# Ensure the app module is importable
sys.path.insert(0, str(Path(__file__).resolve().parent))

app = func.FunctionApp(http_auth_level=func.AuthLevel.FUNCTION)


# ── Helper: extract request body safely ────────────────────────────
def _get_body(req: func.HttpRequest) -> dict:
    try:
        return req.get_json()
    except (ValueError, TypeError):
        return {}


# ── Helper: Copilot Studio response format ─────────────────────────
def _copilot_response(
    answer: str,
    status: str = "answered",
    confidence: str = "",
    sources: list = None,
    validation_report: dict = None,
) -> func.HttpResponse:
    """Build a response compatible with Copilot Studio / Power Automate."""
    body = {
        "answer": answer,
        "status": status,
        "confidence": confidence,
        "sources": sources or [],
    }
    if validation_report:
        body["validationReport"] = validation_report
    return func.HttpResponse(
        body=json.dumps(body, ensure_ascii=False, default=str),
        status_code=200,
        mimetype="application/json",
        headers={"Content-Type": "application/json; charset=utf-8"},
    )


# ── Error response ─────────────────────────────────────────────────
def _error_response(message: str, status_code: int = 400) -> func.HttpResponse:
    return func.HttpResponse(
        body=json.dumps({"answer": message, "status": "error"}),
        status_code=status_code,
        mimetype="application/json",
    )


# ═══════════════════════════════════════════════════════════════════
# ENDPOINT 1: /api/ask — Q&A (the main Copilot Studio endpoint)
# ═══════════════════════════════════════════════════════════════════
@app.route(route="api/ask", methods=["POST"])
def ask(req: func.HttpRequest) -> func.HttpResponse:
    """
    Main Q&A endpoint called by Copilot Studio via Power Automate.

    Request body (from Copilot Studio):
      { "question": "What is the heartbeat signal?" }

    Response (to Copilot Studio):
      { "answer": "...", "status": "answered", "confidence": "HIGH", "sources": [...] }

    Also supports: validation questions, section guidance questions,
    file uploads, and ambiguity detection.
    """
    logging.info("=== /api/ask called ===")
    body = _get_body(req)
    question = (body.get("question") or "").strip()

    if not question:
        return _copilot_response(
            "Please provide a question to ask about the specification files.",
            status="clarify"
        )

    # Lazy import to keep cold-start fast
    from azure_handler import handle_ask
    return handle_ask(question, body)


# ═══════════════════════════════════════════════════════════════════
# ENDPOINT 2: /api/validate — Spec Validation
# ═══════════════════════════════════════════════════════════════════
@app.route(route="api/validate", methods=["POST"])
def validate(req: func.HttpRequest) -> func.HttpResponse:
    """
    Validate an uploaded specification file against the CTS template.

    Request body:
      { "fileName": "ASU_Spec.docx" }

    Response:
      { "answer": "Verdict: ACCEPTABLE_WITH_FIXES (83%)",
        "validationReport": { ... full evidence-backed report ... } }
    """
    logging.info("=== /api/validate called ===")
    body = _get_body(req)
    file_name = (body.get("fileName") or "").strip()

    if not file_name:
        return _error_response("Missing 'fileName' in request body.", 422)

    from azure_handler import handle_validate
    return handle_validate(file_name)


# ═══════════════════════════════════════════════════════════════════
# ENDPOINT 3: /api/upload — File Upload (for spec files)
# ═══════════════════════════════════════════════════════════════════
@app.route(route="api/upload", methods=["POST"])
def upload(req: func.HttpRequest) -> func.HttpResponse:
    """
    Upload a specification file for indexing.

    Multipart form: field "file" with the .docx/.txt/.pdf file.

    Response:
      { "fileName": "...", "chunks": 92, "message": "Uploaded and indexed..." }
    """
    logging.info("=== /api/upload called ===")

    # Azure Functions handle multipart differently — check for file in body
    try:
        file_bytes = req.get_body()
    except Exception:
        return _error_response("Could not read request body.", 400)

    # Try to get filename from headers
    content_type = req.headers.get("Content-Type", "")
    file_name = req.headers.get("X-File-Name", "uploaded_spec.docx")

    if not file_bytes:
        return _error_response("No file content found in request.", 400)

    from azure_handler import handle_upload
    return handle_upload(file_name, file_bytes)


# ═══════════════════════════════════════════════════════════════════
# ENDPOINT 4: /api/files — List accessible files
# ═══════════════════════════════════════════════════════════════════
@app.route(route="api/files", methods=["GET"])
def list_files(req: func.HttpRequest) -> func.HttpResponse:
    """List accessible specification files."""
    from azure_handler import handle_list_files
    return handle_list_files()


# ═══════════════════════════════════════════════════════════════════
# ENDPOINT 5: /api/health — Health check
# ═══════════════════════════════════════════════════════════════════
@app.route(route="api/health", methods=["GET"])
def health(req: func.HttpRequest) -> func.HttpResponse:
    """Health check — returns OK if the function is running."""
    return func.HttpResponse(
        body=json.dumps({"status": "healthy", "version": "2.0.0-enterprise"}),
        status_code=200,
        mimetype="application/json",
    )
