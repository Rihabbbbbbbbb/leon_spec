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
def _decode_file_content(content_str) -> bytes:
    """
    Decode file content that may come as:
    1. Data URI:  data:<mime>;base64,<base64data>
    2. Plain base64 string
    3. JSON-serialized blob (e.g. from Power Fx JSON()): "UEsDBBQ..."
    4. Raw text content
    5. Bytes directly (from Power Automate)
    6. Dict/object with 'content' or 'contentBytes' key (Copilot Studio file object)
    7. URL-safe base64 (uses - and _ instead of + and /)
    8. JSON string representing a file object (from Copilot Studio JSON() function):
       '{"content":"UEsDBBQ...","name":"file.xlsm","contentType":"..."}'
    9. Raw binary data (from application/octet-stream) — bytes passed directly
    
    Returns the decoded bytes.
    """
    import base64
    import re
    import json as _json
    
    if not content_str:
        raise ValueError("Empty or invalid content string")
    
    # If already bytes, return as-is (Case 5 / Case 9)
    if isinstance(content_str, bytes):
        return content_str
    
    # Case 6: Dict/object with content key (Copilot Studio file object)
    if isinstance(content_str, dict):
        b64 = (content_str.get("content", "") or
               content_str.get("contentBytes", "") or
               content_str.get("Content", "") or
               content_str.get("data", "") or
               content_str.get("$base64", ""))
        if b64:
            return _decode_file_content(b64)
        raise ValueError("Dict content has no 'content' or 'contentBytes' key")
    
    if not isinstance(content_str, str):
        raise ValueError(f"Invalid content type: {type(content_str)}")
    
    s = content_str.strip()
    
    # Case 8: JSON string representing a file object
    # This happens when Copilot Studio uses =JSON(Topic.ConformityFile, JSONFormat.IncludeBinaryData)
    # The output is a JSON string like: {"content":"UEsDBBQ...","name":"file.xlsm"}
    if len(s) > 20 and s.startswith('{') and s.endswith('}'):
        try:
            obj = _json.loads(s)
            if isinstance(obj, dict):
                # Try to extract file content from the JSON object
                b64 = (obj.get("content", "") or
                       obj.get("contentBytes", "") or
                       obj.get("Content", "") or
                       obj.get("data", "") or
                       obj.get("$base64", ""))
                if b64:
                    logging.info(f"Decoded JSON file object, content length: {len(b64)}")
                    return _decode_file_content(b64)
                # Maybe the object itself has nested content
                for key in obj:
                    val = obj[key]
                    if isinstance(val, str) and len(val) > 100:
                        # Try to decode as base64
                        try:
                            decoded = base64.b64decode(val)
                            if len(decoded) > 100:
                                logging.info(f"Decoded base64 from key '{key}', size: {len(decoded)}")
                                return decoded
                        except Exception:
                            pass
        except (_json.JSONDecodeError, ValueError):
            pass  # Not valid JSON, try other cases
    
    # Case 1: Data URI  (data:application/...;base64,UEsDBBQ...)
    if s.startswith("data:") and ";base64," in s:
        b64_part = s.split(";base64,", 1)[1]
        return base64.b64decode(b64_part)
    
    # Case 2: Data URI without base64 (data:text/plain,Hello)
    if s.startswith("data:") and "," in s:
        raw_part = s.split(",", 1)[1]
        return raw_part.encode("utf-8")
    
    # Case 3: JSON-serialized string (from Power Fx JSON() function)
    # JSON() wraps strings in double quotes: "UEsDBBQ..."
    if len(s) >= 2 and s.startswith('"') and s.endswith('"'):
        inner = s[1:-1]  # Remove surrounding quotes
        # Check if it's a JSON object inside the quotes
        if inner.startswith('{') and inner.endswith('}'):
            return _decode_file_content(inner)
        # Check if it's base64 inside the quotes
        if len(inner) > 50 and re.match(r'^[A-Za-z0-9+/=\r\n]+$', inner):
            clean = inner.replace('\r', '').replace('\n', '').replace(' ', '')
            try:
                decoded = base64.b64decode(clean)
                if len(decoded) > 0:
                    return decoded
            except Exception:
                pass
        # Not base64 inside quotes — treat as raw text
        return inner.encode("utf-8")
    
    # Case 4: Plain base64 (starts with typical base64 chars and is long enough)
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
    
    # Case 7: URL-safe base64 (uses - and _ instead of + and /)
    if len(s) > 50 and re.match(r'^[A-Za-z0-9\-_=\r\n]+$', s):
        clean = s.replace('\r', '').replace('\n', '').replace(' ', '')
        # Convert URL-safe to standard base64
        clean = clean.replace('-', '+').replace('_', '/')
        # Add padding if needed
        padding = 4 - (len(clean) % 4)
        if padding != 4:
            clean += '=' * padding
        try:
            decoded = base64.b64decode(clean)
            if len(decoded) > 0:
                return decoded
        except Exception:
            pass
    
    # Case 5: Raw text content (treat as text file content)
    logging.warning(f"Could not decode as base64, treating as raw text (len={len(s)})")
    return s.encode("utf-8")


# ── Helper: download file from URL ────────────────────────────────
def _download_file_from_url(url: str, timeout: int = 60, auth_token: str = None) -> bytes:
    """
    Download a file from a URL (e.g., SharePoint, OneDrive, Teams attachment URL).
    Used when Copilot Studio provides a fileUrl instead of base64 content.
    Handles redirects, SSL, and optional authentication (bearer token).
    """
    import urllib.request
    import ssl
    
    logging.info(f"Downloading file from URL: {url[:120]}...")
    
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    
    req = urllib.request.Request(url)
    req.add_header("User-Agent", "LEON-Spec-Azure-Function/3.0")
    # If an auth token is provided (e.g., from Copilot Studio), use it
    if auth_token:
        req.add_header("Authorization", f"Bearer {auth_token}")
    
    # Follow redirects (SharePoint/OneDrive URLs often redirect)
    opener = urllib.request.build_opener(
        urllib.request.HTTPSHandler(context=ctx),
        urllib.request.HTTPRedirectHandler()
    )
    
    with opener.open(req, timeout=timeout) as resp:
        data = resp.read()
        logging.info(f"Downloaded {len(data)} bytes from URL (status={resp.status})")
        return data


# ── Helper: extract file from request body (all formats) ───────────
def _extract_file_from_body(body: dict, req: func.HttpRequest = None) -> tuple:
    """
    Extract file_name and file_bytes from request body.
    Handles all input formats:
    1. { "fileName": "file.ods", "fileContent": "<base64>" }
    2. { "fileName": "file.ods", "fileUrl": "https://..." }
    3. { "file": { "name": "file.ods", "contentBytes": "<base64>" } }
    4. { "file": "<base64>", "fileName": "file.ods" }
    5. { "fileName": "file.ods" }  (file already uploaded)
    6. Copilot Studio file object: { "fileContent": { "name": "...", "contentUrl": "...", "content": "..." } }
    7. Copilot Studio file object at root: { "name": "...", "contentUrl": "...", "content": "..." }
    
    Returns (file_name, file_bytes) — file_bytes may be None if only fileName is provided.
    """
    file_name = None
    file_bytes = None
    
    if not body:
        return (None, None)
    
    # Extract auth token from request headers (for authenticated URL downloads)
    auth_token = None
    if req:
        auth_header = req.headers.get("Authorization", "") or req.headers.get("authorization", "")
        if auth_header.startswith("Bearer "):
            auth_token = auth_header[7:]
    
    # Case: fileUrl provided — download from URL (PREFERRED for Copilot Studio)
    if "fileUrl" in body and body["fileUrl"]:
        url = body["fileUrl"]
        file_name = body.get("fileName", "")
        if not file_name:
            # Try to extract filename from URL
            from urllib.parse import urlparse, unquote
            parsed = urlparse(url)
            file_name = unquote(parsed.path.split("/")[-1]) or "conformity_matrix.ods"
        try:
            file_bytes = _download_file_from_url(url, auth_token=auth_token)
            logging.info(f"Downloaded {len(file_bytes)} bytes from URL")
        except Exception as e:
            logging.error(f"Failed to download from URL: {e}")
            # Return a clear error instead of crashing
            raise ValueError(f"Impossible de telecharger le fichier depuis l'URL fournie. Erreur: {str(e)[:200]}")
        return (file_name, file_bytes)
    
    # Case: fileContent provided — decode base64 (or extract from dict/JSON)
    if "fileContent" in body and body["fileContent"]:
        fc = body["fileContent"]
        # If fileContent is a dict (Copilot Studio file object), extract name + content
        if isinstance(fc, dict):
            file_name = (fc.get("name", "") or fc.get("Name", "") or
                         fc.get("fileName", "") or body.get("fileName", "") or
                         "conformity_matrix.xlsx")
            # Try contentUrl first (download from URL)
            content_url = fc.get("contentUrl", "") or fc.get("ContentUrl", "") or fc.get("url", "")
            if content_url:
                try:
                    file_bytes = _download_file_from_url(content_url, auth_token=auth_token)
                    logging.info(f"Downloaded {len(file_bytes)} bytes from fileContent.contentUrl")
                    return (file_name, file_bytes)
                except Exception as e:
                    logging.warning(f"Failed to download from fileContent.contentUrl: {e}")
            # Fall back to content/base64
            file_bytes = _decode_file_content(fc)
            return (file_name, file_bytes)
        # If fileContent is a string, decode it
        file_bytes = _decode_file_content(fc)
        file_name = body.get("fileName", "") or "conformity_matrix.xlsx"
        return (file_name, file_bytes)
    
    # Case: file object provided (Copilot Studio attachment format)
    if "file" in body and body["file"]:
        file_obj = body["file"]
        if isinstance(file_obj, dict):
            file_name = file_obj.get("name", "") or file_obj.get("Name", "") or "conformity_matrix.ods"
            # Try contentUrl first
            content_url = file_obj.get("contentUrl", "") or file_obj.get("ContentUrl", "") or file_obj.get("url", "")
            if content_url:
                try:
                    file_bytes = _download_file_from_url(content_url, auth_token=auth_token)
                    logging.info(f"Downloaded {len(file_bytes)} bytes from file.contentUrl")
                    return (file_name, file_bytes)
                except Exception as e:
                    logging.warning(f"Failed to download from file.contentUrl: {e}")
            b64 = (file_obj.get("contentBytes", "") or 
                   file_obj.get("Content", "") or 
                   file_obj.get("content", ""))
            if b64:
                file_bytes = _decode_file_content(b64)
            return (file_name, file_bytes)
        elif isinstance(file_obj, str):
            file_bytes = _decode_file_content(file_obj)
            file_name = body.get("fileName", "conformity_matrix.ods")
            return (file_name, file_bytes)
    
    # Case: Copilot Studio file object at root level (no wrapper key)
    # This happens when the entire body IS the file object
    if "contentUrl" in body and body["contentUrl"]:
        url = body["contentUrl"]
        file_name = body.get("name", "") or body.get("Name", "") or body.get("fileName", "") or "conformity_matrix.ods"
        try:
            file_bytes = _download_file_from_url(url, auth_token=auth_token)
            logging.info(f"Downloaded {len(file_bytes)} bytes from root contentUrl")
            return (file_name, file_bytes)
        except Exception as e:
            logging.error(f"Failed to download from root contentUrl: {e}")
            raise ValueError(f"Could not download file from URL: {str(e)[:200]}")
    
    if "content" in body and body["content"] and isinstance(body["content"], str):
        file_name = body.get("name", "") or body.get("Name", "") or body.get("fileName", "") or "conformity_matrix.xlsx"
        file_bytes = _decode_file_content(body["content"])
        return (file_name, file_bytes)
    
    # Case: only fileName provided (file already uploaded)
    if "fileName" in body and body["fileName"]:
        return (body["fileName"], None)
    
    return (None, None)


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
                    # file is a data URI or base64 string
                    file_bytes = _decode_file_content(body["file"])
                    file_name = body.get("fileName", "uploaded_spec.docx")
                elif isinstance(body["file"], dict):
                    # Copilot Studio passes file as {"name":"...","contentBytes":"..."}
                    file_name = body["file"].get("name", "uploaded_spec.docx")
                    b64_content = body["file"].get("contentBytes", "") or body["file"].get("content", "")
                    if b64_content:
                        file_bytes = _decode_file_content(b64_content)
            elif body and "fileUrl" in body:
                import requests as req_lib
                url = body["fileUrl"]
                file_name = body.get("fileName", url.split("/")[-1] if "/" in url else "uploaded_spec.docx")
                logging.info(f"Downloading file from URL: {url[:100]}...")
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
        # Try standard filename="..." first
        filename_match = re.search(r'filename="([^"]*)"', header_bytes)
        if not filename_match:
            # Try RFC 5987 encoding: filename*=UTF-8''filename.xlsm
            filename_match = re.search(r"filename\*=[^']*''([^;\r\n]+)", header_bytes)
        if not filename_match:
            # Try unquoted filename
            filename_match = re.search(r'filename=([^;\r\n]+)', header_bytes)
        if not filename_match:
            # No filename found — but this might still be a file field
            # Check if it has a Content-Type header (file fields usually do)
            if "content-type" in header_bytes.lower():
                filename = "conformity_matrix.xlsx"
            else:
                # This is a non-file field (e.g., form data) — skip
                continue
        else:
            filename = filename_match.group(1).strip().strip('"')
        
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
    
    # ── Diagnostic: Python path and file system ──────────────────
    try:
        diag["sys_path"] = sys.path[:10]
        diag["cwd"] = os.getcwd()
        diag["__file__"] = str(Path(__file__).resolve())
        diag["script_root"] = os.environ.get("AzureWebJobsScriptRoot", "NOT_SET")
        _parent = str(Path(__file__).resolve().parent)
        diag["function_dir"] = _parent
        diag["dir_contents"] = os.listdir(_parent)[:30] if os.path.exists(_parent) else "DIR_NOT_FOUND"
        diag["app_init_exists"] = os.path.exists(os.path.join(_parent, "app", "__init__.py"))
        diag["app_dir_exists"] = os.path.exists(os.path.join(_parent, "app"))
        if os.path.exists(os.path.join(_parent, "app")):
            diag["app_dir_contents"] = os.listdir(os.path.join(_parent, "app"))[:20]
    except Exception as e:
        diag["diag_error"] = str(e)[:300]
    
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


# ═══════════════════════════════════════════════════════════════════
# ENDPOINT 8: /api/upload-and-validate-pdf — Upload + Validate + PDF
# ═══════════════════════════════════════════════════════════════════
@app.route(route="upload-and-validate-pdf", methods=["POST"])
def upload_and_validate_pdf(req: func.HttpRequest) -> func.HttpResponse:
    """
    Upload a file, validate it, and return a PDF report.
    Accepts same formats as upload-and-validate (JSON, multipart, octet-stream).
    Returns: application/pdf binary file.
    """
    logging.info("=== /api/upload-and-validate-pdf called ===")

    content_type = req.headers.get("Content-Type", "")
    body_bytes = req.get_body()
    body_len = len(body_bytes) if body_bytes else 0
    file_name = None
    file_bytes = None

    # Reuse the same parsing logic as upload-and-validate
    _is_json_ct = "application/json" in content_type or "text/json" in content_type

    if "multipart/form-data" in content_type or "multipart/mixed" in content_type:
        try:
            file_name, file_bytes = _parse_multipart(body_bytes, content_type)
        except Exception:
            pass

    if not file_bytes and body_len > 0 and not _is_json_ct:
        if body_bytes[:2] == b"PK" or body_bytes[:4] == b"%PDF" or body_len > 10:
            file_bytes = body_bytes
            file_name = req.headers.get("X-File-Name", "")
            if not file_name:
                if body_bytes[:2] == b"PK":
                    file_name = "uploaded_spec.docx"
                elif body_bytes[:4] == b"%PDF":
                    file_name = "uploaded_spec.pdf"
                else:
                    file_name = "uploaded_spec.txt"

    if not file_bytes and req.headers.get("X-File-Name"):
        file_bytes = body_bytes
        file_name = req.headers.get("X-File-Name", "uploaded_spec.docx")

    if not file_bytes:
        try:
            body = _get_body(req)
            if body and "fileContent" in body:
                file_bytes = _decode_file_content(body["fileContent"])
                file_name = body.get("fileName", "uploaded_spec.docx")
            elif body and "file" in body:
                if isinstance(body["file"], str):
                    file_bytes = _decode_file_content(body["file"])
                    file_name = body.get("fileName", "uploaded_spec.docx")
                elif isinstance(body["file"], dict):
                    file_name = body["file"].get("name", "uploaded_spec.docx")
                    b64 = body["file"].get("contentBytes", "") or body["file"].get("content", "")
                    if b64:
                        file_bytes = _decode_file_content(b64)
        except Exception:
            pass

    if not file_bytes and body_len > 0 and not _is_json_ct:
        file_bytes = body_bytes
        file_name = "uploaded_spec.txt"

    if not file_bytes:
        return _error_response(f"No file content found (CT={content_type}, len={body_len}).", 400)

    if not file_name:
        file_name = "uploaded_spec.docx"

    from azure_handler import handle_upload_and_validate_pdf
    return _safe_handler(handle_upload_and_validate_pdf, file_name, file_bytes)


# ═══════════════════════════════════════════════════════════════════
# ENDPOINT 9: /api/md-to-pdf — Convert markdown text to PDF
# ═══════════════════════════════════════════════════════════════════
@app.route(route="md-to-pdf", methods=["POST"])
def md_to_pdf(req: func.HttpRequest) -> func.HttpResponse:
    """
    Convert a markdown text (or validation report markdown) to a PDF file.

    Request body:
      { "markdown": "# Report\\n\\n...", "title": "Validation Report", "subtitle": "ASU Spec" }
    OR:
      { "fileName": "ASU_Spec.docx" }  — validates the file and returns PDF of the report

    Returns: application/pdf binary file.
    """
    logging.info("=== /api/md-to-pdf called ===")
    body = _get_body(req)

    # If fileName is provided, validate and return PDF directly
    file_name = (body.get("fileName") or "").strip()
    if file_name:
        from azure_handler import handle_validation_pdf
        return _safe_handler(handle_validation_pdf, file_name)

    # Otherwise, convert the provided markdown text
    markdown_text = (body.get("markdown") or "").strip()
    if not markdown_text:
        return _error_response("Missing 'markdown' field or 'fileName' in request body.", 422)

    title = body.get("title", "LEON Report")
    subtitle = body.get("subtitle", "")

    from azure_handler import handle_md_to_pdf
    return _safe_handler(handle_md_to_pdf, markdown_text, title, subtitle)


# ═══════════════════════════════════════════════════════════════════
# ENDPOINT 10: /api/validation-pdf — Validate + return PDF directly
# ═══════════════════════════════════════════════════════════════════
@app.route(route="validation-pdf", methods=["POST"])
def validation_pdf(req: func.HttpRequest) -> func.HttpResponse:
    """
    Validate a specification file and return the report as a PDF file.

    Request body:
      { "fileName": "ASU_Spec.docx" }

    Returns: application/pdf binary file directly.
    """
    logging.info("=== /api/validation-pdf called ===")
    body = _get_body(req)
    file_name = (body.get("fileName") or "").strip()

    if not file_name:
        return _error_response("Missing 'fileName' in request body.", 422)

    from azure_handler import handle_validation_pdf
    return _safe_handler(handle_validation_pdf, file_name)


# ═══════════════════════════════════════════════════════════════════
# ENDPOINT 10b: /api/validate-url — Validate from URL (bypasses content filter)
# ═══════════════════════════════════════════════════════════════════
@app.route(route="validate-url", methods=["POST"])
def validate_url(req: func.HttpRequest) -> func.HttpResponse:
    """
    Download a file from a URL and validate it against the CTS template.

    This endpoint bypasses Copilot Studio's Azure OpenAI content filter
    (openAIndirectAttack). Copilot Studio passes only a short URL string
    (not file content), so the content filter is not triggered.

    Request body:
      { "fileUrl": "https://sharepoint.com/.../spec.docx" }
      { "fileUrl": "https://...", "fileName": "ASU_Spec.docx" }

    Returns JSON with:
      - answer, verdict, overallScore, summary, validationReport
      - documentBase64, documentUrl (unified DOCX — downloadable document)
      - pdfBase64 (backward compatible)
    """
    logging.info("=== /api/validate-url called ===")
    body = _get_body(req)
    file_url = (body.get("fileUrl") or "").strip()
    file_name = (body.get("fileName") or "").strip()

    if not file_url:
        return _error_response("Missing 'fileUrl' in request body.", 422)

    from azure_handler import handle_validate_url
    return _safe_handler(handle_validate_url, file_url, file_name)


# ═══════════════════════════════════════════════════════════════════
# ENDPOINT 11: /api/conformity — Conformity Matrix Analysis
# ═══════════════════════════════════════════════════════════════════
@app.route(route="conformity", methods=["POST"])
def conformity(req: func.HttpRequest) -> func.HttpResponse:
    """
    Analyze a conformity matrix spreadsheet (ODS or XLSX).

    Auto-detects the sheet, header row, and 'Conformite FNR' / 'Commentaires FNR'
    columns (even if names change). Returns:
    - List of OK / NOK / NA / STANDBY items with exact comments
    - AI deep analysis of OK responses (hidden non-conformity signals)
    - Pie chart (base64 PNG)
    - Summary statistics
    - PDF report (base64)

    Accepts:
    1. JSON: { "fileName": "file.ods" }  (file must be already uploaded)
    2. JSON: { "fileName": "file.ods", "fileContent": "<base64>" }
    3. Multipart form: file field
    4. Raw binary with X-File-Name header
    """
    logging.info("=== /api/conformity called ===")

    content_type = req.headers.get("Content-Type", "")
    body_bytes = req.get_body()
    body_len = len(body_bytes) if body_bytes else 0
    file_name = None
    file_bytes = None

    # Try JSON body first
    body = _get_body(req)
    if body:
        file_name, file_bytes = _extract_file_from_body(body, req)
    
    # Fallback: multipart form data
    if not file_name and "multipart/form-data" in content_type:
        try:
            file_name, file_bytes = _parse_multipart(body_bytes, content_type)
        except Exception:
            pass
    
    # Fallback: raw binary with X-File-Name header
    if not file_name and body_len > 0 and not ("application/json" in content_type):
        file_bytes = body_bytes
        file_name = req.headers.get("X-File-Name", "conformity_matrix.ods")

    if not file_name or not file_bytes:
        return _error_response("Missing 'fileName' or file content in request.", 422)

    from azure_handler import handle_conformity
    return _safe_handler(handle_conformity, file_name, file_bytes)


# ═══════════════════════════════════════════════════════════════════
# ENDPOINT 12: /api/conformity-excel — Color-coded Excel report
# ═══════════════════════════════════════════════════════════════════
@app.route(route="conformity-excel", methods=["POST"], auth_level=func.AuthLevel.ANONYMOUS)
def conformity_excel(req: func.HttpRequest) -> func.HttpResponse:
    """
    Upload a conformity matrix and get a color-coded Excel report.
    Anonymous endpoint — no API key required (for Copilot Studio tool integration).
    Same input formats as /api/conformity.
    Returns JSON with reportExcel (base64 XLSX) and optional downloadUrl.
    """
    logging.info("=== /api/conformity-excel called (anonymous) ===")

    content_type = req.headers.get("Content-Type", "")
    body_bytes = req.get_body()
    body_len = len(body_bytes) if body_bytes else 0
    file_name = None
    file_bytes = None

    logging.info(f"Content-Type: {content_type}, Body length: {body_len}")

    # Path A: JSON body with fileContent/fileUrl/fileName
    body = _get_body(req)
    if body:
        logging.info(f"Body keys: {list(body.keys())}")
        try:
            file_name, file_bytes = _extract_file_from_body(body, req)
            if file_bytes:
                logging.info(f"Extracted file from JSON: name={file_name}, size={len(file_bytes)} bytes")
        except Exception as exc:
            logging.warning(f"JSON body extraction failed: {exc}")
    
    # Path B: multipart form data (file upload from Copilot Studio)
    if not file_bytes and "multipart/form-data" in content_type:
        try:
            file_name, file_bytes = _parse_multipart(body_bytes, content_type)
            logging.info(f"Multipart parsed: name={file_name}, size={len(file_bytes) if file_bytes else 0} bytes")
        except Exception as exc:
            logging.warning(f"Multipart parse failed: {exc}")
    
    # Path C: application/octet-stream (raw binary from Copilot Studio)
    if not file_bytes and body_len > 0 and ("octet-stream" in content_type or "binary" in content_type):
        file_bytes = body_bytes
        file_name = req.headers.get("X-File-Name", "")
        if not file_name:
            # Try to get filename from Content-Disposition header
            cd = req.headers.get("Content-Disposition", "")
            import re as _re
            fn_match = _re.search(r'filename="?([^";\s]+)"?', cd)
            file_name = fn_match.group(1) if fn_match else "conformity_matrix.xlsx"
        logging.info(f"Raw binary upload: {file_name}, {len(file_bytes)} bytes")
    
    # Path D: raw binary with X-File-Name header (non-JSON content)
    if not file_bytes and body_len > 0 and not ("application/json" in content_type):
        file_bytes = body_bytes
        file_name = req.headers.get("X-File-Name", "conformity_matrix.ods")

    # Path E: If we have bytes but no name, try to detect format from content
    if file_bytes and not file_name:
        file_name = "conformity_matrix.xlsx"
        logging.info(f"No file name provided — defaulting to {file_name}")

    # If we have a file name but no extension, try to detect from content
    if file_name and file_bytes:
        ext = os.path.splitext(file_name)[1].lower()
        if ext not in (".ods", ".xlsx", ".xlsm", ".xls"):
            # Check file magic bytes
            if file_bytes[:4] == b"PK\x03\x04":
                # ZIP-based format (XLSX/XLSM)
                file_name = os.path.splitext(file_name)[0] + ".xlsm"
                logging.info(f"Detected ZIP format — setting extension to .xlsm: {file_name}")
            elif file_bytes[:4] == b"PK\x03\x04" or b"mimetypeapplication/vnd" in file_bytes[:100]:
                # ODS format
                file_name = os.path.splitext(file_name)[0] + ".ods"
                logging.info(f"Detected ODS format — setting extension to .ods: {file_name}")
            else:
                # Default to .xlsx
                file_name = os.path.splitext(file_name)[0] + ".xlsx"
                logging.info(f"Unknown format — defaulting to .xlsx: {file_name}")

    if not file_name or not file_bytes:
        logging.error(f"No file content found. CT={content_type}, body_len={body_len}, body_keys={list(body.keys()) if body else 'none'}")
        return _error_response("Missing file content or fileName. Provide fileContent (base64), fileUrl, or file object.", 422)

    logging.info(f"Processing conformity Excel report for: {file_name} ({len(file_bytes)} bytes)")
    from azure_handler import handle_conformity_excel
    return _safe_handler(handle_conformity_excel, file_name, file_bytes)


# ═══════════════════════════════════════════════════════════════════
# ENDPOINT 13: /api/conformity-compare — Multi-matrix comparison
# ═══════════════════════════════════════════════════════════════════
@app.route(route="conformity-compare", methods=["POST"])
def conformity_compare(req: func.HttpRequest) -> func.HttpResponse:
    """
    Compare two or more conformity matrices.

    Request body:
      { "fileNames": ["matrix_v1.ods", "matrix_v2.ods"] }

    Returns JSON with comparison data, status changes, and chart.
    """
    logging.info("=== /api/conformity-compare called ===")
    body = _get_body(req)
    file_names = body.get("fileNames", [])

    if not file_names or len(file_names) < 2:
        return _error_response("At least 2 file names required for comparison.", 400)

    from azure_handler import handle_conformity_compare
    return _safe_handler(handle_conformity_compare, file_names)


# ═══════════════════════════════════════════════════════════════════
# ENDPOINT 14: /api/conformity-powerbi — Power BI dataset
# ═══════════════════════════════════════════════════════════════════
@app.route(route="conformity-powerbi", methods=["POST"])
def conformity_powerbi(req: func.HttpRequest) -> func.HttpResponse:
    """
    Generate a Power BI-compatible dataset JSON from a conformity matrix.

    Request body:
      { "fileName": "matrix.ods" }  (file must be already uploaded)
      OR
      { "fileName": "matrix.ods", "fileContent": "<base64>" }  (upload + analyze)

    Returns JSON with dataset definition, data rows, and dashboard config.
    """
    logging.info("=== /api/conformity-powerbi called ===")
    body = _get_body(req)
    file_name, file_bytes = _extract_file_from_body(body, req) if body else (None, None)
    
    if not file_name:
        return _error_response("Missing 'fileName' in request body. Provide fileName, fileContent (base64), or fileUrl.", 422)

    from azure_handler import handle_conformity_powerbi
    return _safe_handler(handle_conformity_powerbi, file_name, file_bytes)


# ═══════════════════════════════════════════════════════════════════
# ENDPOINT 15: /api/upload-page — HTML file upload page (bypasses content filter)
# ═══════════════════════════════════════════════════════════════════
@app.route(route="upload-page", methods=["GET"], auth_level=func.AuthLevel.ANONYMOUS)
def upload_page(req: func.HttpRequest) -> func.HttpResponse:
    """Serve an HTML page where users can upload their spec file and get a URL."""
    html = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>LEON — Upload Specification File</title>
<style>
* { margin: 0; padding: 0; box-sizing: border-box; }
body { font-family: 'Segoe UI', system-ui, sans-serif; background: #f0f2f5; min-height: 100vh; display: flex; align-items: center; justify-content: center; }
.container { background: white; border-radius: 16px; box-shadow: 0 4px 24px rgba(0,0,0,0.1); max-width: 600px; width: 90%; padding: 48px; }
h1 { color: #0078d4; font-size: 28px; margin-bottom: 8px; }
.subtitle { color: #605e5c; margin-bottom: 32px; font-size: 15px; }
.upload-area { border: 2px dashed #0078d4; border-radius: 12px; padding: 48px 24px; text-align: center; cursor: pointer; transition: all 0.2s; }
.upload-area:hover { background: #f3f9fd; border-color: #106ebe; }
.upload-area.dragover { background: #deecf9; border-color: #106ebe; }
.upload-icon { font-size: 48px; color: #0078d4; margin-bottom: 16px; }
.upload-text { color: #605e5c; font-size: 15px; margin-bottom: 8px; }
.upload-hint { color: #a19f9d; font-size: 13px; }
input[type="file"] { display: none; }
.btn { background: #0078d4; color: white; border: none; border-radius: 8px; padding: 12px 32px; font-size: 15px; cursor: pointer; margin-top: 24px; width: 100%; transition: background 0.2s; }
.btn:hover { background: #106ebe; }
.btn:disabled { background: #c8c6c4; cursor: not-allowed; }
.result { margin-top: 32px; padding: 24px; border-radius: 12px; display: none; }
.result.success { background: #dff6dd; border: 1px solid #107c10; }
.result.error { background: #fde7e9; border: 1px solid #d13438; }
.result h3 { margin-bottom: 12px; font-size: 16px; }
.url-box { background: white; border: 1px solid #d2d0ce; border-radius: 8px; padding: 16px; margin: 16px 0; word-break: break-all; font-family: monospace; font-size: 13px; color: #0078d4; }
.copy-btn { background: #107c10; color: white; border: none; border-radius: 6px; padding: 8px 20px; font-size: 14px; cursor: pointer; margin-top: 8px; }
.copy-btn:hover { background: #0b6a0b; }
.instructions { margin-top: 24px; padding: 16px; background: #f3f9fd; border-radius: 8px; font-size: 14px; color: #605e5c; line-height: 1.6; }
.instructions ol { padding-left: 20px; }
.instructions li { margin-bottom: 8px; }
.spinner { display: none; width: 40px; height: 40px; border: 4px solid #f3f3f3; border-top: 4px solid #0078d4; border-radius: 50%; animation: spin 1s linear infinite; margin: 24px auto; }
@keyframes spin { 0% { transform: rotate(0deg); } 100% { transform: rotate(360deg); } }
</style>
</head>
<body>
<div class="container">
<h1>LEON Spec Upload</h1>
<p class="subtitle">Upload your specification file to get a shareable link for LEON Copilot Studio</p>

<div class="upload-area" id="dropZone" onclick="document.getElementById('fileInput').click()">
<div class="upload-icon">📄</div>
<div class="upload-text">Click to browse or drag & drop your file here</div>
<div class="upload-hint">Supported: .docx, .pdf, .txt (max 25 MB)</div>
</div>
<input type="file" id="fileInput" accept=".docx,.pdf,.txt">

<div class="spinner" id="spinner"></div>

<button class="btn" id="uploadBtn" onclick="uploadFile()" disabled>Upload & Get Link</button>

<div class="result" id="result">
<h3 id="resultTitle"></h3>
<div id="resultContent"></div>
</div>

<div class="instructions">
<strong>How to use this link in LEON:</strong>
<ol>
<li>Upload your file above and copy the generated link</li>
<li>Go to LEON in Copilot Studio (Teams)</li>
<li>Type "validate spec" or "validate this spec"</li>
<li>When LEON asks for the link, paste the URL you copied</li>
<li>LEON will download and validate your specification</li>
</ol>
</div>
</div>

<script>
const dropZone = document.getElementById('dropZone');
const fileInput = document.getElementById('fileInput');
const uploadBtn = document.getElementById('uploadBtn');
const spinner = document.getElementById('spinner');
const result = document.getElementById('result');
const resultTitle = document.getElementById('resultTitle');
const resultContent = document.getElementById('resultContent');
let selectedFile = null;

fileInput.addEventListener('change', (e) => {
    if (e.target.files.length > 0) {
        selectedFile = e.target.files[0];
        uploadBtn.disabled = false;
        dropZone.querySelector('.upload-text').textContent = selectedFile.name;
    }
});

dropZone.addEventListener('dragover', (e) => { e.preventDefault(); dropZone.classList.add('dragover'); });
dropZone.addEventListener('dragleave', () => dropZone.classList.remove('dragover'));
dropZone.addEventListener('drop', (e) => {
    e.preventDefault();
    dropZone.classList.remove('dragover');
    if (e.dataTransfer.files.length > 0) {
        selectedFile = e.dataTransfer.files[0];
        fileInput.files = e.dataTransfer.files;
        uploadBtn.disabled = false;
        dropZone.querySelector('.upload-text').textContent = selectedFile.name;
    }
});

async function uploadFile() {
    if (!selectedFile) return;
    uploadBtn.disabled = true;
    uploadBtn.textContent = 'Uploading...';
    spinner.style.display = 'block';
    result.style.display = 'none';

    const formData = new FormData();
    formData.append('file', selectedFile);

    try {
        const code = new URLSearchParams(window.location.search).get('code');
        const url = code ? '/api/upload-and-get-url?code=' + encodeURIComponent(code) : '/api/upload-and-get-url';
        const resp = await fetch(url, { method: 'POST', body: formData });
        const data = await resp.json();

        spinner.style.display = 'none';
        result.style.display = 'block';

        if (resp.ok && data.fileUrl) {
            result.className = 'result success';
            resultTitle.textContent = '✅ Upload Successful!';
            resultContent.innerHTML = `
                <p>Your file is ready. Copy this link and paste it into LEON:</p>
                <div class="url-box" id="urlBox">${data.fileUrl}</div>
                <button class="copy-btn" onclick="copyUrl()">📋 Copy Link</button>
                <p style="margin-top:12px;font-size:13px;color:#605e5c;">File: ${data.fileName} (${(data.fileSize/1024).toFixed(0)} KB)</p>
            `;
        } else {
            result.className = 'result error';
            resultTitle.textContent = '❌ Upload Failed';
            resultContent.innerHTML = `<p>${data.error || data.answer || 'Unknown error'}</p>`;
        }
    } catch (err) {
        spinner.style.display = 'none';
        result.style.display = 'block';
        result.className = 'result error';
        resultTitle.textContent = '❌ Upload Failed';
        resultContent.innerHTML = `<p>${err.message}</p>`;
    }

    uploadBtn.disabled = false;
    uploadBtn.textContent = 'Upload & Get Link';
}

function copyUrl() {
    const urlText = document.getElementById('urlBox').textContent;
    navigator.clipboard.writeText(urlText).then(() => {
        const btn = document.querySelector('.copy-btn');
        btn.textContent = '✅ Copied!';
        setTimeout(() => btn.textContent = '📋 Copy Link', 2000);
    });
}
</script>
</body>
</html>"""
    return func.HttpResponse(
        body=html,
        status_code=200,
        mimetype="text/html",
        headers={"Content-Type": "text/html; charset=utf-8"},
    )


# ═══════════════════════════════════════════════════════════════════
# ENDPOINT 16: /api/upload-and-get-url — Upload file to Blob Storage, return URL
# ═══════════════════════════════════════════════════════════════════
@app.route(route="upload-and-get-url", methods=["POST"])
def upload_and_get_url(req: func.HttpRequest) -> func.HttpResponse:
    """
    Upload a specification file to Azure Blob Storage and return a public URL.
    This URL can be pasted into Copilot Studio to validate the file via /api/validate-url.

    Accepts: multipart/form-data (field "file"), or JSON { "fileName": "...", "fileContent": "<base64>" }
    Returns: { "fileUrl": "https://...blob.core.windows.net/...", "fileName": "...", "fileSize": 12345 }
    """
    logging.info("=== /api/upload-and-get-url called ===")

    content_type = req.headers.get("Content-Type", "")
    body_bytes = req.get_body()
    file_name = None
    file_bytes = None

    # ── Path A: multipart/form-data ────────────────────────────────
    if "multipart/form-data" in content_type:
        try:
            file_name, file_bytes = _parse_multipart(body_bytes, content_type)
            logging.info(f"Multipart: {file_name}, {len(file_bytes)} bytes")
        except Exception as exc:
            logging.error(f"Multipart parse failed: {exc}")

    # ── Path B: JSON body (base64) ──────────────────────────────────
    if not file_bytes:
        try:
            body = _get_body(req)
            if body and "fileContent" in body:
                file_bytes = _decode_file_content(body["fileContent"])
                file_name = body.get("fileName", "uploaded_spec.docx")
            elif body and "file" in body and isinstance(body["file"], str):
                file_bytes = _decode_file_content(body["file"])
                file_name = body.get("fileName", "uploaded_spec.docx")
        except Exception as exc:
            logging.error(f"JSON parse error: {exc}")

    # ── Path C: raw binary with X-File-Name header ─────────────────
    if not file_bytes and req.headers.get("X-File-Name"):
        file_bytes = body_bytes
        file_name = req.headers.get("X-File-Name", "uploaded_spec.docx")

    if not file_bytes:
        return _error_response("No file content found. Use multipart/form-data with field 'file'.", 400)

    if not file_name:
        file_name = "uploaded_spec.docx"

    # Validate extension
    suffix = Path(file_name).suffix.lower()
    if suffix not in {".txt", ".docx", ".pdf"}:
        return _error_response(f"File type '{suffix}' not accepted. Use .txt, .docx, or .pdf.", 400)

    # Max 25 MB
    if len(file_bytes) > 25 * 1024 * 1024:
        return _error_response("File too large. Maximum size is 25 MB.", 413)

    # Upload to Blob Storage
    from azure_handler import _upload_to_blob_storage
    ct_map = {
        ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        ".pdf": "application/pdf",
        ".txt": "text/plain",
    }
    content_type_blob = ct_map.get(suffix, "application/octet-stream")
    blob_url = _upload_to_blob_storage(
        file_bytes=file_bytes,
        file_name=file_name,
        content_type=content_type_blob,
        blob_extension=suffix.lstrip("."),
    )

    if not blob_url:
        # Fallback: save locally and return a data URI
        import base64 as _b64
        b64 = _b64.b64encode(file_bytes).decode("ascii")
        data_uri = f"data:{content_type_blob};base64,{b64}"
        logging.warning("Blob upload failed — returning data URI (may be too large for Copilot Studio)")
        return func.HttpResponse(
            body=json.dumps({
                "fileUrl": data_uri,
                "fileName": file_name,
                "fileSize": len(file_bytes),
                "warning": "Blob Storage not configured — returned as data URI. Configure AzureWebJobsStorage for public URLs.",
            }, ensure_ascii=False),
            status_code=200,
            mimetype="application/json",
            headers={"Content-Type": "application/json; charset=utf-8"},
        )

    logging.info(f"Upload-and-get-url success: {file_name} -> {blob_url}")
    return func.HttpResponse(
        body=json.dumps({
            "fileUrl": blob_url,
            "fileName": file_name,
            "fileSize": len(file_bytes),
        }, ensure_ascii=False),
        status_code=200,
        mimetype="application/json",
        headers={"Content-Type": "application/json; charset=utf-8"},
    )
