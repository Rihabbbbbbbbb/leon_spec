"""
Azure Function App — LEON Spec Q&A + Validation Assistant.

This is the serverless entry point for the Copilot Studio integration.
Exposes HTTP-triggered endpoints that Copilot Studio calls via Power Automate.

Endpoints (Azure Functions v2 auto-prepends /api/):
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

# ── Application Insights — custom telemetry (optional) ────────────
try:
    from azure.monitor.opentelemetry import configure_azure_monitor
    _ai_conn = os.getenv("APPINSIGHTS_CONNECTION_STRING") or os.getenv(
        "APPLICATIONINSIGHTS_CONNECTION_STRING"
    )
    if _ai_conn:
        configure_azure_monitor(connection_string=_ai_conn)
        logging.info("Application Insights configured successfully")
except ImportError:
    pass  # azure-monitor-opentelemetry not installed — built-in logging only
except Exception as _ai_exc:
    logging.warning(f"Application Insights setup skipped: {_ai_exc}")

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


# ── Helper: decode file content from Copilot Studio ────────────────
def _decode_file_content(content_str: str) -> bytes:
    """
    Decode file content that may come as:
    1. Data URI:  data:<mime>;base64,<base64data>
    2. Plain base64 string
    3. Raw text content
    
    Returns the decoded bytes.
    """
    import base64
    import re
    
    if not content_str or not isinstance(content_str, str):
        raise ValueError("Empty or invalid content string")
    
    s = content_str.strip()
    
    # Case 1: Data URI  (data:application/...;base64,UEsDBBQ...)
    if s.startswith("data:") and ";base64," in s:
        b64_part = s.split(";base64,", 1)[1]
        return base64.b64decode(b64_part)
    
    # Case 2: Data URI without base64 (data:text/plain,Hello)
    if s.startswith("data:") and "," in s:
        raw_part = s.split(",", 1)[1]
        return raw_part.encode("utf-8")
    
    # Case 3: Plain base64 (starts with typical base64 chars and is long enough)
    if len(s) > 50 and re.match(r'^[A-Za-z0-9+/=\r\n]+$', s):
        # Remove any whitespace/newlines
        clean = s.replace('\r', '').replace('\n', '').replace(' ', '')
        try:
            decoded = base64.b64decode(clean)
            # Verify it decoded to something reasonable
            if len(decoded) > 0:
                return decoded
        except Exception:
            pass
    
    # Case 4: Raw text content (treat as text file content)
    return s.encode("utf-8")


# ── Helper: Copilot Studio response format ─────────────────────────
def _copilot_response(
    answer: str,
    status: str = "answered",
    confidence: str = "",
    sources: list = None,
    validation_report: dict = None,
) -> func.HttpResponse:
    """Build a response compatible with Copilot Studio / Power Automate."""
    sources = sources or []
    body = {
        "answer": answer,
        "status": status,
        "confidence": confidence,
        "sources": sources,
        "evidence": [],
        "fileName": sources[0].get("fileName", "") if sources and isinstance(sources[0], dict) else "",
        "excerpt": sources[0].get("excerpt", "") if sources and isinstance(sources[0], dict) else "",
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
        body=json.dumps({"answer": message, "status": "error", "confidence": "", "sources": [], "evidence": []}),
        status_code=status_code,
        mimetype="application/json",
        headers={"Content-Type": "application/json; charset=utf-8"},
    )


# ── Safe handler wrapper — always returns JSON, never empty 500 ────
def _safe_handler(handler_func, *args, **kwargs):
    """Wrap a handler call so any exception returns a JSON error response."""
    try:
        return handler_func(*args, **kwargs)
    except Exception as exc:
        import traceback
        tb = traceback.format_exc()
        logging.error(f"Handler {handler_func.__name__} crashed: {exc}\n{tb}")
        return func.HttpResponse(
            body=json.dumps({
                "answer": f"LEON internal error: {str(exc)[:500]}",
                "status": "error",
                "confidence": "LOW",
                "sources": [],
                "evidence": [],
            }, ensure_ascii=False, default=str),
            status_code=500,
            mimetype="application/json",
            headers={"Content-Type": "application/json; charset=utf-8"},
        )


# ═══════════════════════════════════════════════════════════════════
# ENDPOINT 1: /api/ask — Q&A (the main Copilot Studio endpoint)
# Route is "ask" → Azure Functions serves it at /api/ask
# ═══════════════════════════════════════════════════════════════════
@app.route(route="ask", methods=["POST"])
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
    logging.info("=== /api/ask (route=ask) called ===")
    body = _get_body(req)
    question = (body.get("question") or "").strip()

    if not question:
        return _copilot_response(
            "Please provide a question to ask about the specification files.",
            status="clarify"
        )

    # Lazy import to keep cold-start fast
    try:
        from azure_handler import handle_ask
    except Exception as exc:
        import traceback
        logging.error(f"Import azure_handler failed: {exc}\n{traceback.format_exc()}")
        return _error_response(
            f"LEON import error: {str(exc)[:300]}", 500
        )
    return _safe_handler(handle_ask, question, body)


# ═══════════════════════════════════════════════════════════════════
# ENDPOINT 2: /api/validate — Spec Validation
# ═══════════════════════════════════════════════════════════════════
@app.route(route="validate", methods=["POST"])
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
    return _safe_handler(handle_validate, file_name)


# ═══════════════════════════════════════════════════════════════════
# ENDPOINT 2b: /api/upload-and-validate — Upload + Validate in one call
# ═══════════════════════════════════════════════════════════════════
@app.route(route="upload-and-validate", methods=["POST"])
def upload_and_validate(req: func.HttpRequest) -> func.HttpResponse:
    """
    Upload a file AND validate it in one call.
    Accepts: multipart/form-data, application/octet-stream, JSON (base64/data-URI).
    Returns the validation report directly.
    """
    logging.info("=== /api/upload-and-validate called ===")

    content_type = req.headers.get("Content-Type", "")
    body_bytes = req.get_body()
    body_len = len(body_bytes) if body_bytes else 0
    logging.info(f"Content-Type: {content_type}, Body length: {body_len}")

    file_name = None
    file_bytes = None

    # ── Path A: multipart/form-data ────────────────────────────────
    if "multipart/form-data" in content_type or "multipart/mixed" in content_type:
        try:
            file_name, file_bytes = _parse_multipart(body_bytes, content_type)
            logging.info(f"Multipart parsed: {file_name}, {len(file_bytes)} bytes")
        except Exception as exc:
            logging.error(f"Multipart parse failed: {exc}")
            # Fall through to other strategies instead of failing
            pass

    # ── Path B: raw binary (application/octet-stream or no content-type) ──
    # IMPORTANT: Skip for application/json — JSON bodies are handled in Path D
    _is_json_ct = "application/json" in content_type or "text/json" in content_type
    if not file_bytes and body_len > 0 and not _is_json_ct:
        # Accept any non-empty body as potential file content
        # (PK = zip/docx, %PDF = PDF, or any other binary/text content)
        if body_bytes[:2] == b"PK" or body_bytes[:4] == b"%PDF" or body_len > 10:
            file_bytes = body_bytes
            # Try to get filename from headers
            file_name = req.headers.get("X-File-Name", "")
            if not file_name:
                # Try Content-Disposition header
                cd = req.headers.get("Content-Disposition", "")
                import re
                fn_match = re.search(r'filename="?([^";\s]+)"?', cd)
                if fn_match:
                    file_name = fn_match.group(1)
            if not file_name:
                # Detect from magic bytes
                if body_bytes[:2] == b"PK":
                    file_name = "uploaded_spec.docx"
                elif body_bytes[:4] == b"%PDF":
                    file_name = "uploaded_spec.pdf"
                else:
                    file_name = "uploaded_spec.txt"
            logging.info(f"Raw binary path: {file_name}, {len(file_bytes)} bytes")

    # ── Path C: X-File-Name header ─────────────────────────────────
    if not file_bytes and req.headers.get("X-File-Name"):
        file_bytes = body_bytes
        file_name = req.headers.get("X-File-Name", "uploaded_spec.docx")
        logging.info(f"X-File-Name path: {file_name}")

    # ── Path D: JSON body (base64, data-URI, or file object) ───────
    if not file_bytes:
        try:
            body = _get_body(req)
            logging.info(f"JSON body keys: {list(body.keys()) if body else 'EMPTY'}")

            if body and "fileContent" in body:
                file_bytes = _decode_file_content(body["fileContent"])
                file_name = body.get("fileName", "uploaded_spec.docx")
            elif body and "file" in body:
                if isinstance(body["file"], str):
                    file_bytes = _decode_file_content(body["file"])
                    file_name = body.get("fileName", "uploaded_spec.docx")
                elif isinstance(body["file"], dict):
                    file_name = body["file"].get("name", "uploaded_spec.docx")
                    b64_content = body["file"].get("contentBytes", "") or body["file"].get("content", "")
                    if b64_content:
                        file_bytes = _decode_file_content(b64_content)
            elif body and "fileUrl" in body:
                import requests as req_lib
                url = body["fileUrl"]
                file_name = body.get("fileName", url.split("/")[-1] if "/" in url else "uploaded_spec.docx")
                resp = req_lib.get(url, timeout=60, allow_redirects=True)
                if resp.status_code == 200:
                    file_bytes = resp.content
                else:
                    return _error_response(f"Failed to download file (HTTP {resp.status_code})", 400)
        except Exception as exc:
            logging.error(f"JSON parse error: {exc}")

    # ── Last resort: try raw body as text ──────────────────────────
    if not file_bytes and body_len > 0:
        try:
            file_bytes = body_bytes
            file_name = "uploaded_spec.txt"
            logging.info("Last resort: using raw body as file")
        except Exception:
            pass

    if not file_bytes:
        logging.error(f"All paths failed. CT={content_type}, body_len={body_len}")
        return _error_response(
            f"No file content found (CT={content_type}, len={body_len}).", 400)

    if not file_name:
        file_name = "uploaded_spec.docx"

    logging.info(f"Proceeding with: {file_name}, {len(file_bytes)} bytes")
    from azure_handler import handle_upload_and_validate
    return _safe_handler(handle_upload_and_validate, file_name, file_bytes)


# ── Helper: parse multipart/form-data (Azure Functions doesn't do this) ──
def _parse_multipart(body: bytes, content_type: str):
    """
    Parse a multipart/form-data body and extract the first file field.
    Returns (filename, file_bytes).
    """
    import re

    # Extract boundary from Content-Type header
    boundary_match = re.search(r'boundary=("?)([^";\s]+)\1', content_type)
    if not boundary_match:
        raise ValueError("Could not find multipart boundary in Content-Type")

    boundary = boundary_match.group(2).encode()
    delimiter = b"--" + boundary

    # Split body by boundary
    parts = body.split(delimiter)
    for part in parts:
        # Skip empty parts and closing boundary
        if not part or part.strip() in (b"", b"--", b"--\r\n"):
            continue

        # Strip leading CRLF
        if part.startswith(b"\r\n"):
            part = part[2:]

        # Split headers from content
        header_end = part.find(b"\r\n\r\n")
        if header_end == -1:
            continue

        header_bytes = part[:header_end].decode("utf-8", errors="replace")
        content = part[header_end + 4:]

        # Strip trailing CRLF
        if content.endswith(b"\r\n"):
            content = content[:-2]

        # Parse Content-Disposition header for filename
        filename_match = re.search(r'filename="([^"]*)"', header_bytes)
        if not filename_match:
            # This is a non-file field (e.g., form data) — skip
            continue

        filename = filename_match.group(1)
        if not filename:
            continue

        return filename, content

    raise ValueError("No file field found in multipart body")
@app.route(route="upload", methods=["POST"])
def upload(req: func.HttpRequest) -> func.HttpResponse:
    """
    Upload a specification file for indexing.

    Multipart form: field "file" with the .docx/.txt/.pdf file.

    Response:
      { "fileName": "...", "chunks": 92, "message": "Uploaded and indexed..." }
    """
    logging.info("=== /api/upload called ===")

    content_type = req.headers.get("Content-Type", "")
    file_name = None
    file_bytes = None

    # ── Path A: multipart/form-data (standard file upload) ────────
    if "multipart/form-data" in content_type or "multipart/mixed" in content_type:
        try:
            file_name, file_bytes = _parse_multipart(req.get_body(), content_type)
        except Exception as exc:
            logging.error(f"Multipart parse error: {exc}")
            return _error_response(f"Could not parse multipart upload: {str(exc)[:200]}", 400)

    # ── Path B: raw body with X-File-Name header (Power Automate fallback) ──
    elif req.headers.get("X-File-Name"):
        try:
            file_bytes = req.get_body()
        except Exception:
            return _error_response("Could not read request body.", 400)
        file_name = req.headers.get("X-File-Name", "uploaded_spec.docx")

    # ── Path C: base64 JSON body (Copilot Studio / Power Automate) ──
    else:
        try:
            body = _get_body(req)
            logging.info(f"Upload JSON body keys: {list(body.keys()) if body else 'EMPTY'}")

            # C1: base64 file content in JSON (Copilot Studio sends data URI)
            if body and "fileContent" in body:
                file_bytes = _decode_file_content(body["fileContent"])
                file_name = body.get("fileName", "uploaded_spec.docx")
            elif body and "file" in body:
                if isinstance(body["file"], str):
                    # file is a data URI or base64 string
                    file_bytes = _decode_file_content(body["file"])
                    file_name = body.get("fileName", "uploaded_spec.docx")
                elif isinstance(body["file"], dict):
                    # Copilot Studio passes file as {"name":"...","contentBytes":"..."}
                    file_name = body["file"].get("name", "uploaded_spec.docx")
                    b64_content = body["file"].get("contentBytes", "") or body["file"].get("content", "")
                    if b64_content:
                        file_bytes = _decode_file_content(b64_content)

            # C2: fileUrl — Copilot Studio sends file as URL (Dataverse/SharePoint)
            elif body and "fileUrl" in body:
                import requests as req_lib
                url = body["fileUrl"]
                file_name = body.get("fileName", url.split("/")[-1] if "/" in url else "uploaded_spec.docx")
                logging.info(f"Downloading file from URL: {url[:100]}...")
                resp = req_lib.get(url, timeout=60, allow_redirects=True)
                if resp.status_code == 200:
                    file_bytes = resp.content
                    import re
                    cd = resp.headers.get("Content-Disposition", "")
                    fn_match = re.search(r'filename="?([^";\s]+)"?', cd)
                    if fn_match and fn_match.group(1):
                        file_name = fn_match.group(1)
                else:
                    logging.error(f"File URL returned {resp.status_code}")
                    return _error_response(f"Failed to download file from URL (HTTP {resp.status_code})", 400)

            # C3: Try to find base64/data-URI content in any field of the JSON body
            elif body:
                for key, val in body.items():
                    if isinstance(val, str) and len(val) > 50 and not val.startswith("http"):
                        try:
                            decoded = _decode_file_content(val)
                            if len(decoded) > 10:
                                file_bytes = decoded
                                file_name = body.get("fileName", body.get("name", "uploaded_spec.docx"))
                                logging.info(f"Found file content in field '{key}'")
                                break
                        except Exception:
                            pass
        except Exception as exc:
            logging.error(f"JSON body parse error: {exc}")

    if not file_bytes:
        # Log what we received for debugging
        logging.error(f"No file content extracted. content_type={content_type}")
        try:
            body_sample = str(req.get_body())[:500]
            logging.error(f"Body sample: {body_sample}")
        except:
            pass
        return _error_response(
            "No file content found. Supported formats: "
            "multipart/form-data, base64 JSON {\"fileName\":\"...\",\"fileContent\":\"<base64>\"}, "
            "or {\"fileUrl\":\"https://...\"} for URL download.", 400)

    if not file_name:
        file_name = "uploaded_spec.docx"

    from azure_handler import handle_upload
    return _safe_handler(handle_upload, file_name, file_bytes)


# ═══════════════════════════════════════════════════════════════════
# ENDPOINT 4: /api/files — List accessible files
# ═══════════════════════════════════════════════════════════════════
@app.route(route="files", methods=["GET"])
def list_files(req: func.HttpRequest) -> func.HttpResponse:
    """List accessible specification files."""
    try:
        from azure_handler import handle_list_files
    except Exception as exc:
        return _error_response(f"LEON import error: {str(exc)[:300]}", 500)
    return _safe_handler(handle_list_files)


# ═══════════════════════════════════════════════════════════════════
# ENDPOINT 5: /api/health — Health check
# ═══════════════════════════════════════════════════════════════════
@app.route(route="health", methods=["GET"])
def health(req: func.HttpRequest) -> func.HttpResponse:
    """Health check — returns OK if the function is running, with diagnostics."""
    diag = {"status": "healthy", "version": "2.0.0-enterprise"}
    # Test critical imports
    try:
        import azure_config
        diag["azure_config"] = "OK"
    except Exception as e:
        diag["azure_config"] = f"FAIL: {str(e)[:200]}"
    try:
        import app.config as cfg
        diag["openai_endpoint"] = bool(cfg.AZURE_OPENAI_ENDPOINT)
        diag["search_endpoint"] = bool(cfg.AZURE_SEARCH_ENDPOINT)
        diag["search_index"] = cfg.AZURE_SEARCH_INDEX_NAME
        diag["data_dir"] = str(cfg.DATA_DIR)
        diag["uploads_dir"] = str(cfg.UPLOADS_DIR)
        diag["uploads_dir_exists"] = cfg.UPLOADS_DIR.exists()
        if cfg.UPLOADS_DIR.exists():
            diag["uploaded_files"] = [p.name for p in cfg.UPLOADS_DIR.iterdir() if p.is_file()]
        else:
            diag["uploaded_files"] = []
        diag["blob_storage"] = bool(getattr(cfg, "AZURE_STORAGE_CONNECTION_STRING", ""))
        diag["key_vault"] = bool(getattr(cfg, "AZURE_KEY_VAULT_URL", ""))
        diag["app_insights"] = bool(
            os.getenv("APPINSIGHTS_CONNECTION_STRING") or
            os.getenv("APPLICATIONINSIGHTS_CONNECTION_STRING")
        )
    except Exception as e:
        diag["app_config"] = f"FAIL: {str(e)[:200]}"
    try:
        from azure_handler import handle_ask
        diag["azure_handler"] = "OK"
    except Exception as e:
        diag["azure_handler"] = f"FAIL: {str(e)[:200]}"
    return func.HttpResponse(
        body=json.dumps(diag, ensure_ascii=False, default=str),
        status_code=200,
        mimetype="application/json",
        headers={"Content-Type": "application/json; charset=utf-8"},
    )


# ═══════════════════════════════════════════════════════════════════
# ENDPOINT 6: /api/debug-request — Echo back request details
# ═══════════════════════════════════════════════════════════════════
@app.route(route="debug-request", methods=["POST", "GET"])
def debug_request(req: func.HttpRequest) -> func.HttpResponse:
    """Echo request for diagnosing Copilot Studio upload format."""
    diag = {
        "method": req.method,
        "url": req.url,
        "headers": dict(req.headers),
        "params": dict(req.params),
        "content_type": req.headers.get("Content-Type", ""),
    }
    try:
        body_bytes = req.get_body()
        diag["body_length"] = len(body_bytes)
        diag["body_text_first_500"] = body_bytes[:500].decode("utf-8", errors="replace")
        try:
            json_body = req.get_json()
            diag["body_json"] = json_body
            if isinstance(json_body, dict):
                diag["body_json_keys"] = list(json_body.keys())
                diag["has_fileUrl"] = "fileUrl" in json_body
                diag["has_fileContent"] = "fileContent" in json_body
                diag["has_file"] = "file" in json_body
                diag["has_fileName"] = "fileName" in json_body
                # Show file field type if present
                if "file" in json_body:
                    diag["file_field_type"] = type(json_body["file"]).__name__
                    if isinstance(json_body["file"], dict):
                        diag["file_field_keys"] = list(json_body["file"].keys())
        except Exception:
            diag["body_json"] = "not_parseable_as_json"
    except Exception as e:
        diag["body_error"] = str(e)[:500]

    return func.HttpResponse(
        body=json.dumps(diag, ensure_ascii=False, default=str),
        status_code=200,
        mimetype="application/json",
        headers={"Content-Type": "application/json; charset=utf-8"},
    )


# ═══════════════════════════════════════════════════════════════════
# ENDPOINT 7: /api/diag — Full diagnostic (tests Search + OpenAI)
# ═══════════════════════════════════════════════════════════════════
@app.route(route="diag", methods=["GET"])
def diag(req: func.HttpRequest) -> func.HttpResponse:
    """Full diagnostic — tests Azure Search and OpenAI connectivity."""
    result = {"step": "init", "details": {}}

    try:
        import sys
        result["details"]["python_version"] = sys.version
        result["details"]["python_path"] = sys.path[:5]
    except:
        pass

    try:
        import app.config as cfg
        result["details"]["openai_endpoint"] = cfg.AZURE_OPENAI_ENDPOINT[:50] + "..." if cfg.AZURE_OPENAI_ENDPOINT else "MISSING"
        result["details"]["openai_key"] = "SET" if cfg.AZURE_OPENAI_API_KEY else "MISSING"
        result["details"]["search_endpoint"] = cfg.AZURE_SEARCH_ENDPOINT[:50] + "..." if cfg.AZURE_SEARCH_ENDPOINT else "MISSING"
        result["details"]["search_key"] = "SET" if cfg.AZURE_SEARCH_API_KEY else "MISSING"
        result["details"]["search_index"] = cfg.AZURE_SEARCH_INDEX_NAME
        result["details"]["blob_storage"] = "SET" if getattr(cfg, "AZURE_STORAGE_CONNECTION_STRING", "") else "MISSING"
        result["details"]["key_vault"] = "SET" if getattr(cfg, "AZURE_KEY_VAULT_URL", "") else "MISSING"
        result["details"]["app_insights"] = "SET" if (os.getenv("APPINSIGHTS_CONNECTION_STRING") or os.getenv("APPLICATIONINSIGHTS_CONNECTION_STRING")) else "MISSING"
    except Exception as e:
        result["step"] = "config"
        result["error"] = str(e)[:300]
        return func.HttpResponse(body=json.dumps(result, default=str), status_code=500, mimetype="application/json")

    # Test 1: Azure Search document count
    try:
        result["step"] = "search_count"
        from app.qa.azure_search import is_configured, get_document_count
        result["details"]["search_configured"] = is_configured()
        count = get_document_count()
        result["details"]["search_doc_count"] = count
    except Exception as e:
        result["error"] = f"Search count failed: {str(e)[:300]}"
        return func.HttpResponse(body=json.dumps(result, default=str), status_code=500, mimetype="application/json")

    # Test 2: Embedding generation
    try:
        result["step"] = "embedding"
        from app.embeddings import get_embedding
        emb = get_embedding("test question about ASU")
        result["details"]["embedding_dim"] = len(emb)
    except Exception as e:
        result["error"] = f"Embedding failed: {str(e)[:300]}"
        return func.HttpResponse(body=json.dumps(result, default=str), status_code=500, mimetype="application/json")

    # Test 3: Hybrid search
    try:
        result["step"] = "hybrid_search"
        from app.qa.azure_search import hybrid_search
        docs = hybrid_search("Where is the ASU located?", emb, top_k=3)
        result["details"]["search_results"] = len(docs)
        for i, d in enumerate(docs[:3]):
            result["details"][f"result_{i}"] = {
                "file": d.get("file_name", "?"),
                "section": d.get("section", "?"),
                "score": d.get("@search.score", 0),
            }
    except Exception as e:
        result["error"] = f"Hybrid search failed: {str(e)[:300]}"
        return func.HttpResponse(body=json.dumps(result, default=str), status_code=500, mimetype="application/json")

    # Test 4: LLM call
    try:
        result["step"] = "llm"
        from app.embeddings import call_llm
        answer = call_llm("You are a test assistant.", "Say OK", temperature=0.1, max_tokens=10)
        result["details"]["llm_response"] = answer.strip()[:100]
    except Exception as e:
        result["error"] = f"LLM failed: {str(e)[:300]}"
        return func.HttpResponse(body=json.dumps(result, default=str), status_code=500, mimetype="application/json")

    result["step"] = "complete"
    result["status"] = "all_ok"
    return func.HttpResponse(
        body=json.dumps(result, ensure_ascii=False, default=str),
        status_code=200,
        mimetype="application/json",
        headers={"Content-Type": "application/json; charset=utf-8"},
    )
