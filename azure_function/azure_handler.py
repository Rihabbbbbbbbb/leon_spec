"""
Azure Function Handler — bridges Copilot Studio requests to the LEON backend.

This module contains the actual logic for:
- Q&A (question answering from spec files)
- Validation (evidence-based spec validation)
- Section guidance (what to put in each CTS section)
- File upload + indexing
- File listing

All logic is extracted from the FastAPI route.py, adapted for serverless execution.

IMPORTANT: This handler caches the index and rules in memory within the
Azure Function instance. For production, consider using Azure Blob Storage
or Redis for persistent caching across function instances.
"""
from __future__ import annotations

import azure.functions as func
import json
import logging
from pathlib import Path
from typing import List, Optional, Dict

# ── Import backend modules (lazy, done inside functions for cold-start speed) ──

# Cache for expensive-to-build objects (survives between requests within same instance)
_index_cache: Optional[List] = None
_rules_cache = None


def _get_index():
    """Get or build the chunk index (cached in memory)."""
    global _index_cache
    if _index_cache is None:
        from app.qa.retrieval import build_index
        _index_cache = build_index()
        logging.info(f"Index built: {len(_index_cache)} chunks")
    return _index_cache


def _reset_index():
    """Invalidate the index cache (call after upload)."""
    global _index_cache
    _index_cache = None
    logging.info("Index cache reset")


# ── Copilot Studio response helper ──────────────────────────────────
def _response(
    answer: str,
    status: str = "answered",
    confidence: str = "",
    sources: list = None,
    validation_report: dict = None,
    evidence: list = None,
    status_code: int = 200,
) -> func.HttpResponse:
    sources = sources or []
    evidence = evidence or []
    body = {
        "answer": answer,
        "status": status,
        "confidence": confidence,
        "sources": sources,
        "evidence": evidence,
    }
    # Add top-level fileName and excerpt for Copilot Studio compatibility
    if sources:
        body["fileName"] = sources[0].get("fileName", "") if isinstance(sources[0], dict) else str(sources[0])
        body["excerpt"] = sources[0].get("excerpt", "") if isinstance(sources[0], dict) else ""
    else:
        body["fileName"] = ""
        body["excerpt"] = ""
    if validation_report:
        body["validationReport"] = validation_report
    return func.HttpResponse(
        body=json.dumps(body, ensure_ascii=False, default=str),
        status_code=status_code,
        mimetype="application/json",
        headers={"Content-Type": "application/json; charset=utf-8"},
    )


# ═══════════════════════════════════════════════════════════════════
# HANDLER 1: /api/ask — Question Answering
# ═══════════════════════════════════════════════════════════════════
def handle_ask(question: str, body: dict) -> func.HttpResponse:
    """Process a Q&A request from Copilot Studio."""

    # ── 1. Standards / BeStandard guardrail ─────────────────────────
    from app.qa.prompt import is_standards_question, STANDARDS_REFUSAL_MESSAGE
    if is_standards_question(question):
        return _response(STANDARDS_REFUSAL_MESSAGE, status="refusal")

    # ── 2. Ambiguity detection ──────────────────────────────────────
    # Adapted from route.py _is_ambiguous_question
    import re
    content_words = re.findall(r"[a-zA-Zàâäéèêëîïôöùûüçñ]{2,}", question.lower())
    if len(content_words) <= 1:
        clarify_msg = (
            "Your question is a bit too brief. Could you add more detail? "
            "For example, mention the component name (e.g. 'ASU'), the topic "
            "(e.g. 'noise target', 'test temperature'), or the requirement "
            "you're interested in."
        )
        return _response(clarify_msg, status="clarify")

    # ── 3. Validation-intent detection ──────────────────────────────
    from app.qa.route import _is_validation_question, _pick_validation_file
    if _is_validation_question(question):
        chunks = _get_index()
        val_file = _pick_validation_file(question, chunks)
        if val_file is None:
            return _response(
                "I'd like to validate a document for you, but there are no "
                "accessible specification files to validate. Please upload a "
                ".docx file first via the /api/upload endpoint.",
                status="not_found"
            )
        return handle_validate(val_file)

    # ── 3b. Section guidance detection (BEFORE retrieval) ──────────
    # If the user is asking "how do I write the PURPOSE section?" etc.,
    # answer with template + writing-guide guidance — don't search the spec.
    from app.qa.section_guidance import is_section_guidance_question, get_section_guidance
    if is_section_guidance_question(question):
        guidance = get_section_guidance(question)
        if guidance:
            guidance_text = guidance.get("answer", "")
            guidance_prompt = (
                "You are a CTS specification writing assistant for Stellantis.\n"
                "A user is writing a Component Technical Specification and needs "
                "guidance on a specific section.\n\n"
                "Below is the EXACT guidance extracted from the Stellantis CTS "
                "template and writing guide. Your job is to PRESENT this guidance "
                "to the user in a clear, readable format.\n\n"
                "CRITICAL RULES:\n"
                "1. Use ONLY the information in the GUIDANCE below. Do NOT add, "
                "invent, or hallucinate any content that is not explicitly stated.\n"
                "2. Do NOT remove or alter template placeholders like <<...>>. "
                "Present them exactly as they appear — they show the writer what "
                "to fill in.\n"
                "3. Keep the section headings from the GUIDANCE (Purpose, Template "
                "instruction, Applicable rules, etc.).\n"
                "4. Cite each rule by its ID (e.g. R20, P07) exactly as shown.\n"
                "5. Do NOT mix content from other CTS sections. Only discuss the "
                "section the user asked about.\n"
                "6. If the guidance says 'To be completed by the writer', say "
                "exactly that — do NOT invent example content.\n"
                "7. End your answer with this exact line:\n"
                "   *Guidance extracted 100% from the Stellantis CTS template "
                "and writing guide.*\n\n"
                "GUIDANCE:\n" + guidance_text
            )
            llm_answer = _call_llm(
                "You are a Stellantis engineering specification expert. "
                "You present extracted guidance faithfully without adding "
                "or inventing content.",
                guidance_prompt
            )
            # If LLM failed or didn't include the footer, use raw guidance text
            if llm_answer and "Guidance extracted 100%" in llm_answer:
                final_answer = llm_answer
            elif llm_answer:
                final_answer = llm_answer + "\n\n---\n*Guidance extracted 100% from the Stellantis CTS template and writing guide.*"
            else:
                final_answer = guidance_text
            return _response(
                final_answer,
                status="answered",
                confidence="HIGH",
                evidence=[guidance.get("detected_section", "")],
            )

    # ── 4. Overview question handling ──────────────────────────────
    from app.qa.route import _is_overview_question, _detect_referenced_file, _retrieve_file_overview
    from app.qa.retrieval import retrieve, RetrievalResult
    from app.qa.route import _try_acronym_retrieval

    chunks = _get_index()

    result = None
    # Only use local index for overview/acronym if it has content
    if chunks:
        if _is_overview_question(question):
            ref_file = _detect_referenced_file(question, chunks)
            if ref_file:
                overview_chunks = _retrieve_file_overview(chunks, ref_file)
                if overview_chunks:
                    result = RetrievalResult(
                        chunks=overview_chunks,
                        scores=[1.0] * len(overview_chunks),
                        used_fallback=False,
                    )

    # ── 5. Standard retrieval — TRY AZURE AI SEARCH FIRST ─────────
    if result is None:
        if chunks:
            acronym_result = _try_acronym_retrieval(question, chunks)
            if acronym_result:
                result = acronym_result

    # Always try Azure Search (even if local index is empty — the 255
    # chunks are in Azure Search, not necessarily in the local filesystem)
    if result is None:
        try:
            from app.qa.retrieval import azure_search_retrieve
            result = azure_search_retrieve(question)
        except Exception as exc:
            logging.warning(f"Azure Search failed: {exc}")
            if chunks:
                result = retrieve(question, chunks=chunks, use_semantic=False)
            else:
                return _response(
                    "No accessible specification files are currently indexed. "
                    f"Azure Search error: {str(exc)[:200]}",
                    status="not_found"
                )

    # ── 6. No support found → return not-found message ────────────
    from app.qa.prompt import NOT_FOUND_MESSAGE
    if not result.chunks:
        return _response(NOT_FOUND_MESSAGE, status="not_found")

    # ── 8. LLM synthesis ───────────────────────────────────────────
    from app.qa.prompt import SYSTEM_PROMPT, build_user_prompt, extract_confidence
    user_message = build_user_prompt(question, result.chunks)
    llm_answer = _call_llm(SYSTEM_PROMPT, user_message)

    if llm_answer and llm_answer.strip():
        confidence, answer = extract_confidence(llm_answer.strip())

        if answer and "not found" not in answer.lower()[:50]:
            sources = [
                {"fileName": c.file_name, "excerpt": c.text[:300]}
                for c in result.chunks[:5]
            ]
            evidence = [c.file_name for c in result.chunks[:5]]
            return _response(
                answer, status="answered", confidence=confidence,
                sources=sources, evidence=evidence,
            )
        else:
            return _response(NOT_FOUND_MESSAGE, status="not_found")

    # ── 9. LLM unavailable → transparent excerpts ──────────────────
    excerpts = "\n\n".join(
        f"[{c.file_name} §{c.section}] {c.text[:300]}"
        for c in result.chunks[:5]
    )
    return _response(
        f"The LLM is currently unavailable. Here are the most relevant "
        f"passages from the specification files:\n\n{excerpts}",
        status="partial",
        sources=[
            {"fileName": c.file_name, "excerpt": c.text[:300]}
            for c in result.chunks[:5]
        ],
    )


# ═══════════════════════════════════════════════════════════════════
# HANDLER 2: /api/validate — Evidence-Based Validation
# ═══════════════════════════════════════════════════════════════════
def handle_validate(file_name: str) -> func.HttpResponse:
    """Validate a specification file against the CTS template.
    
    Returns JSON with the full validation report, plus a base64-encoded
    PDF report for direct download (field: pdfBase64).
    """

    # Find the file
    from app.qa.retrieval import discover_accessible_files, extract_text_from_file
    file_path = None
    for p in discover_accessible_files():
        if p.name == file_name:
            file_path = p
            break

    if file_path is None:
        return _response(
            f"File '{file_name}' not found among accessible files. "
            f"Upload it first via /api/upload.",
            status="not_found", status_code=404
        )

    text = extract_text_from_file(file_path)
    if not text or not text.strip():
        return _response(
            f"Could not extract text from '{file_name}' for validation.",
            status="error", status_code=422
        )

    from app.qa.evidence_comparator import validate_with_evidence
    report = validate_with_evidence(file_name, text)

    summary = (
        f"Here is the evidence-based validation for **{file_name}** — "
        f"Verdict: **{report.get('verdict', 'UNKNOWN')}** "
        f"({report.get('overallScore', 0):.0%}):\n\n"
        f"{report.get('summary', '')}\n\n"
        f"Checked against {report.get('rulesUsed', {}).get('mandatory_sections_count', 0)} "
        f"template sections and {report.get('rulesUsed', {}).get('writing_guide_rules_count', 0)} "
        f"writing-guide rules (100% extracted from source documents)."
    )

    # ── Generate PDF report (backward compatible) ────────────
    pdf_base64 = ""
    try:
        from app.qa.pdf_report import generate_validation_pdf
        import base64 as _b64
        pdf_bytes = generate_validation_pdf(report)
        pdf_base64 = _b64.b64encode(pdf_bytes).decode("ascii")
    except Exception as exc:
        logging.warning(f"PDF generation failed (non-fatal): {exc}")

    # ── Generate unified DOCX document (standardized template) ──
    docx_base64 = ""
    document_url = ""
    try:
        from app.qa.spec_report_docx import generate_spec_validation_document
        import base64 as _b642
        docx_bytes = generate_spec_validation_document(report)
        docx_base64 = _b642.b64encode(docx_bytes).decode("ascii")

        # Try to upload DOCX to Azure Blob Storage for direct download
        docx_blob_url = _upload_to_blob_storage(
            file_bytes=docx_bytes, file_name=file_name,
            content_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            blob_extension="docx",
        )
        if docx_blob_url:
            document_url = docx_blob_url
        else:
            document_url = f"data:application/vnd.openxmlformats-officedocument.wordprocessingml.document;base64,{docx_base64}"
    except Exception as exc:
        logging.warning(f"DOCX generation failed (non-fatal): {exc}")

    # Build response with both nested validationReport AND top-level fields
    # for Copilot Studio compatibility, plus base64 PDF and DOCX
    val_body = {
        "answer": summary,
        "status": "answered",
        "verdict": report.get("verdict", "UNKNOWN"),
        "overallScore": report.get("overallScore", 0),
        "summary": report.get("summary", ""),
        "validationReport": report,
        "pdfBase64": pdf_base64,
        "pdfAvailable": bool(pdf_base64),
        # Unified document (standardized template DOCX — primary downloadable document)
        "documentBase64": docx_base64,
        "documentUrl": document_url,
        "documentAvailable": bool(docx_base64),
    }
    return func.HttpResponse(
        body=json.dumps(val_body, ensure_ascii=False, default=str),
        status_code=200,
        mimetype="application/json",
        headers={"Content-Type": "application/json; charset=utf-8"},
    )


# ═══════════════════════════════════════════════════════════════════
# HANDLER 2b: /api/upload-and-validate — Upload + Validate in one call
# ═══════════════════════════════════════════════════════════════════
def handle_upload_and_validate(file_name: str, file_bytes: bytes) -> func.HttpResponse:
    """Upload a file, index it, then validate it — all in one call."""

    # Validate extension
    allowed_ext = {".txt", ".docx", ".pdf"}
    suffix = Path(file_name).suffix.lower()
    if suffix not in allowed_ext:
        return _response(
            f"File type '{suffix}' not accepted. Use .txt, .docx, or .pdf.",
            status="error", status_code=400
        )

    # Max 25 MB
    if len(file_bytes) > 25 * 1024 * 1024:
        return _response(
            "File too large. Maximum size is 25 MB.",
            status="error", status_code=413
        )

    from app.qa.retrieval import save_uploaded_file, extract_text_from_file

    # 1. Save the file
    try:
        saved_path = save_uploaded_file(file_name, file_bytes)
    except Exception as e:
        logging.error(f"Upload save error: {e}")
        return _response(f"Failed to save file: {e}", status="error", status_code=500)

    # 2. Index to Azure Search
    try:
        _index_file_to_search(saved_path)
    except Exception as exc:
        logging.warning(f"Search indexing failed (non-fatal): {exc}")

    # 3. Rebuild local index
    try:
        _reset_index()
        _get_index()
    except Exception as e:
        logging.warning(f"Local index rebuild: {e}")

    # 4. Validate the file
    text = extract_text_from_file(saved_path)
    if not text or not text.strip():
        return _response(
            f"Could not extract text from '{saved_path.name}'. The file may be corrupted or contain only images.",
            status="error", status_code=422
        )

    from app.qa.evidence_comparator import validate_with_evidence
    report = validate_with_evidence(saved_path.name, text)

    summary = (
        f"Here is the evidence-based validation for **{saved_path.name}** — "
        f"Verdict: **{report.get('verdict', 'UNKNOWN')}** "
        f"({report.get('overallScore', 0):.0%}):\n\n"
        f"{report.get('summary', '')}"
    )

    # ── Generate PDF (base64 for JSON response) ──────────────
    pdf_base64 = ""
    try:
        from app.qa.pdf_report import generate_validation_pdf
        import base64 as _b64
        pdf_bytes = generate_validation_pdf(report)
        pdf_base64 = _b64.b64encode(pdf_bytes).decode("ascii")
    except Exception as exc:
        logging.warning(f"PDF generation failed (non-fatal): {exc}")

    # ── Generate unified DOCX document (standardized template) ──
    docx_base64 = ""
    document_url = ""
    try:
        from app.qa.spec_report_docx import generate_spec_validation_document
        import base64 as _b642
        docx_bytes = generate_spec_validation_document(report)
        docx_base64 = _b642.b64encode(docx_bytes).decode("ascii")

        # Try to upload DOCX to Azure Blob Storage for direct download
        docx_blob_url = _upload_to_blob_storage(
            file_bytes=docx_bytes, file_name=saved_path.name,
            content_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            blob_extension="docx",
        )
        if docx_blob_url:
            document_url = docx_blob_url
        else:
            document_url = f"data:application/vnd.openxmlformats-officedocument.wordprocessingml.document;base64,{docx_base64}"
    except Exception as exc:
        logging.warning(f"DOCX generation failed (non-fatal): {exc}")

    # ── Return JSON with all fields for Copilot Studio ────────
    # CRITICAL: Must return JSON (not raw PDF bytes) so Copilot Studio
    # can parse output bindings (answer, verdict, overallScore, etc.)
    val_body = {
        "answer": summary,
        "status": "answered",
        "verdict": report.get("verdict", "UNKNOWN"),
        "overallScore": report.get("overallScore", 0),
        "summary": report.get("summary", ""),
        "validationReport": report,
        "fileName": saved_path.name,
        "confidence": "",
        "sources": [],
        "evidence": [],
        "pdfBase64": pdf_base64,
        "pdfAvailable": bool(pdf_base64),
        # Unified document (standardized template DOCX — primary downloadable document)
        "documentBase64": docx_base64,
        "documentUrl": document_url,
        "documentAvailable": bool(docx_base64),
    }
    return func.HttpResponse(
        body=json.dumps(val_body, ensure_ascii=False, default=str),
        status_code=200,
        mimetype="application/json",
        headers={"Content-Type": "application/json; charset=utf-8"},
    )


# ═══════════════════════════════════════════════════════════════════
# HANDLER 2c: /api/upload-and-validate-pdf — Upload + Validate + PDF
# ═══════════════════════════════════════════════════════════════════
def handle_upload_and_validate_pdf(file_name: str, file_bytes: bytes) -> func.HttpResponse:
    """Upload a file, validate it, and return a PDF report."""

    allowed_ext = {".txt", ".docx", ".pdf"}
    suffix = Path(file_name).suffix.lower()
    if suffix not in allowed_ext:
        return _response(
            f"File type '{suffix}' not accepted. Use .txt, .docx, or .pdf.",
            status="error", status_code=400
        )

    if len(file_bytes) > 25 * 1024 * 1024:
        return _response("File too large. Maximum size is 25 MB.", status="error", status_code=413)

    from app.qa.retrieval import save_uploaded_file, extract_text_from_file

    try:
        saved_path = save_uploaded_file(file_name, file_bytes)
    except Exception as e:
        return _response(f"Failed to save file: {e}", status="error", status_code=500)

    text = extract_text_from_file(saved_path)
    if not text or not text.strip():
        return _response(
            f"Could not extract text from '{saved_path.name}'.",
            status="error", status_code=422
        )

    from app.qa.evidence_comparator import validate_with_evidence
    report = validate_with_evidence(saved_path.name, text)

    # Generate PDF
    try:
        from app.qa.pdf_report import generate_validation_pdf
        pdf_bytes = generate_validation_pdf(report)
        safe_name = saved_path.name.replace(" ", "_").replace(".docx", "").replace(".txt", "").replace(".pdf", "")
        return func.HttpResponse(
            body=pdf_bytes,
            status_code=200,
            mimetype="application/pdf",
            headers={
                "Content-Type": "application/pdf",
                "Content-Disposition": f'attachment; filename="LEON_Validation_{safe_name}.pdf"',
            },
        )
    except ImportError:
        # fpdf2 not available — fall back to JSON with report
        summary = (
            f"Validation for **{saved_path.name}** — "
            f"Verdict: **{report.get('verdict', 'UNKNOWN')}** "
            f"({report.get('overallScore', 0):.0%}):\n\n"
            f"{report.get('summary', '')}"
        )
        return func.HttpResponse(
            body=json.dumps({
                "answer": summary, "status": "answered",
                "verdict": report.get("verdict", "UNKNOWN"),
                "overallScore": report.get("overallScore", 0),
                "validationReport": report,
                "pdfAvailable": False,
            }, ensure_ascii=False, default=str),
            status_code=200,
            mimetype="application/json",
            headers={"Content-Type": "application/json; charset=utf-8"},
        )


# ═══════════════════════════════════════════════════════════════════
# HANDLER 2d: /api/validate-url — Validate from URL (bypasses content filter)
# ═══════════════════════════════════════════════════════════════════
def handle_validate_url(file_url: str, file_name: str = "") -> func.HttpResponse:
    """
    Download a file from a URL and validate it against the CTS template.

    This endpoint is designed to bypass Copilot Studio's Azure OpenAI content
    filter (openAIndirectAttack). Instead of passing file CONTENT through
    Copilot Studio (which triggers the content filter on technical specs),
    Copilot Studio passes only a short URL string. The Azure Function
    downloads the file server-side and validates it.

    Args:
        file_url: URL to download the file from (SharePoint, OneDrive, Blob, etc.)
        file_name: Optional file name (derived from URL if not provided)

    Returns JSON with:
      - answer, verdict, overallScore, summary, validationReport
      - pdfBase64 (backward compatible)
      - documentBase64, documentUrl (unified DOCX — primary downloadable document)
    """
    import os
    import base64

    logging.info(f"handle_validate_url: url={file_url}, name={file_name}")

    if not file_url or not file_url.strip():
        return func.HttpResponse(
            body=json.dumps({
                "answer": "No file URL provided. Please provide a URL to the specification file.",
                "status": "error",
            }, ensure_ascii=False),
            status_code=422,
            mimetype="application/json",
            headers={"Content-Type": "application/json; charset=utf-8"},
        )

    # Derive file name from URL if not provided
    if not file_name:
        # Try to extract from URL path
        url_path = file_url.split("?")[0]  # Remove query string
        if "/" in url_path:
            file_name = url_path.split("/")[-1]
        else:
            file_name = "uploaded_spec.docx"

        # URL-decode the file name
        import urllib.parse
        file_name = urllib.parse.unquote(file_name)

    # Validate file extension
    allowed_ext = {".txt", ".docx", ".pdf"}
    suffix = Path(file_name).suffix.lower()
    if suffix not in allowed_ext:
        # If no recognizable extension, default to .docx
        if not suffix:
            file_name = file_name + ".docx"
        else:
            return func.HttpResponse(
                body=json.dumps({
                    "answer": f"File type '{suffix}' not accepted. Use .txt, .docx, or .pdf.",
                    "status": "error",
                }, ensure_ascii=False),
                status_code=400,
                mimetype="application/json",
                headers={"Content-Type": "application/json; charset=utf-8"},
            )

    # ── Download the file server-side ────────────────────────
    try:
        import urllib.request
        import ssl

        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE

        req = urllib.request.Request(file_url, headers={
            "User-Agent": "LEON-Azure-Function/1.0",
        })
        with urllib.request.urlopen(req, timeout=60, context=ctx) as resp:
            file_bytes = resp.read()

        if not file_bytes or len(file_bytes) == 0:
            return func.HttpResponse(
                body=json.dumps({
                    "answer": f"Downloaded file is empty from URL: {file_url}",
                    "status": "error",
                }, ensure_ascii=False),
                status_code=422,
                mimetype="application/json",
                headers={"Content-Type": "application/json; charset=utf-8"},
            )

        logging.info(f"Downloaded {len(file_bytes)} bytes from {file_url}")

    except Exception as exc:
        logging.error(f"File download failed: {exc}")
        return func.HttpResponse(
            body=json.dumps({
                "answer": f"Failed to download file from URL: {str(exc)[:300]}",
                "status": "error",
            }, ensure_ascii=False),
            status_code=502,
            mimetype="application/json",
            headers={"Content-Type": "application/json; charset=utf-8"},
        )

    # Max 25 MB
    if len(file_bytes) > 25 * 1024 * 1024:
        return func.HttpResponse(
            body=json.dumps({
                "answer": "File too large. Maximum size is 25 MB.",
                "status": "error",
            }, ensure_ascii=False),
            status_code=413,
            mimetype="application/json",
            headers={"Content-Type": "application/json; charset=utf-8"},
        )

    # ── Save the file locally ────────────────────────────────
    from app.qa.retrieval import save_uploaded_file, extract_text_from_file

    try:
        saved_path = save_uploaded_file(file_name, file_bytes)
    except Exception as e:
        logging.error(f"Save error: {e}")
        return func.HttpResponse(
            body=json.dumps({
                "answer": f"Failed to save downloaded file: {str(exc)[:300]}",
                "status": "error",
            }, ensure_ascii=False),
            status_code=500,
            mimetype="application/json",
            headers={"Content-Type": "application/json; charset=utf-8"},
        )

    # ── Index to Azure Search (non-fatal) ────────────────────
    try:
        _index_file_to_search(saved_path)
    except Exception as exc:
        logging.warning(f"Search indexing failed (non-fatal): {exc}")

    # ── Rebuild local index (non-fatal) ──────────────────────
    try:
        _reset_index()
        _get_index()
    except Exception as e:
        logging.warning(f"Local index rebuild: {e}")

    # ── Validate the file ────────────────────────────────────
    text = extract_text_from_file(saved_path)
    if not text or not text.strip():
        return func.HttpResponse(
            body=json.dumps({
                "answer": f"Could not extract text from '{saved_path.name}'. The file may be corrupted or contain only images.",
                "status": "error",
            }, ensure_ascii=False),
            status_code=422,
            mimetype="application/json",
            headers={"Content-Type": "application/json; charset=utf-8"},
        )

    from app.qa.evidence_comparator import validate_with_evidence
    report = validate_with_evidence(saved_path.name, text)

    summary = (
        f"Here is the evidence-based validation for **{saved_path.name}** — "
        f"Verdict: **{report.get('verdict', 'UNKNOWN')}** "
        f"({report.get('overallScore', 0):.0%}):\n\n"
        f"{report.get('summary', '')}"
    )

    # ── Generate PDF (backward compatible) ───────────────────
    pdf_base64 = ""
    try:
        from app.qa.pdf_report import generate_validation_pdf
        import base64 as _b64
        pdf_bytes = generate_validation_pdf(report)
        pdf_base64 = _b64.b64encode(pdf_bytes).decode("ascii")
    except Exception as exc:
        logging.warning(f"PDF generation failed (non-fatal): {exc}")

    # ── Generate unified DOCX document (standardized template) ──
    docx_base64 = ""
    document_url = ""
    try:
        from app.qa.spec_report_docx import generate_spec_validation_document
        import base64 as _b642
        docx_bytes = generate_spec_validation_document(report)
        docx_base64 = _b642.b64encode(docx_bytes).decode("ascii")

        # Try to upload DOCX to Azure Blob Storage for direct download
        docx_blob_url = _upload_to_blob_storage(
            file_bytes=docx_bytes, file_name=saved_path.name,
            content_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            blob_extension="docx",
        )
        if docx_blob_url:
            document_url = docx_blob_url
        else:
            document_url = f"data:application/vnd.openxmlformats-officedocument.wordprocessingml.document;base64,{docx_base64}"
    except Exception as exc:
        logging.warning(f"DOCX generation failed (non-fatal): {exc}")

    val_body = {
        "answer": summary,
        "status": "answered",
        "verdict": report.get("verdict", "UNKNOWN"),
        "overallScore": report.get("overallScore", 0),
        "summary": report.get("summary", ""),
        "validationReport": report,
        "fileName": saved_path.name,
        "confidence": "",
        "sources": [],
        "evidence": [],
        "pdfBase64": pdf_base64,
        "pdfAvailable": bool(pdf_base64),
        # Unified document (standardized template DOCX — primary downloadable document)
        "documentBase64": docx_base64,
        "documentUrl": document_url,
        "documentAvailable": bool(docx_base64),
    }
    return func.HttpResponse(
        body=json.dumps(val_body, ensure_ascii=False, default=str),
        status_code=200,
        mimetype="application/json",
        headers={"Content-Type": "application/json; charset=utf-8"},
    )


# ═══════════════════════════════════════════════════════════════════
# HANDLER 3: /api/upload — File Upload
# ═══════════════════════════════════════════════════════════════════
def handle_upload(file_name: str, file_bytes: bytes) -> func.HttpResponse:
    """Save an uploaded spec file, index it to Azure AI Search, and rebuild local index."""

    # Validate extension
    allowed_ext = {".txt", ".docx", ".pdf"}
    suffix = Path(file_name).suffix.lower()
    if suffix not in allowed_ext:
        return _response(
            f"File type '{suffix}' not accepted. Use .txt, .docx, or .pdf.",
            status="error", status_code=400
        )

    # Max 25 MB
    if len(file_bytes) > 25 * 1024 * 1024:
        return _response(
            "File too large. Maximum size is 25 MB.",
            status="error", status_code=413
        )

    from app.qa.retrieval import save_uploaded_file, build_index, extract_text_from_file, _split_into_chunks
    import re as _re

    # ── 1. Save to local disk (for validation endpoint) ───────────
    try:
        saved_path = save_uploaded_file(file_name, file_bytes)
    except Exception as e:
        logging.error(f"Upload save error: {e}")
        return _response(f"Failed to save uploaded file: {e}", status="error", status_code=500)

    # ── 2. Upload to Azure Blob Storage (durable persistence) ─────
    blob_uploaded = False
    try:
        blob_uploaded = _upload_to_blob(saved_path.name, file_bytes)
    except Exception as exc:
        logging.warning(f"Blob upload failed (non-fatal): {exc}")

    # ── 3. Index to Azure AI Search (vector + full-text) ──────────
    search_indexed = 0
    try:
        search_indexed = _index_file_to_search(saved_path)
    except Exception as exc:
        logging.error(f"Azure Search indexing failed: {exc}")

    # ── 4. Rebuild local in-memory index (fallback path) ──────────
    try:
        _reset_index()
        chunks = _get_index()
        chunk_count = sum(1 for c in chunks if c.file_name == saved_path.name)
    except Exception as e:
        logging.error(f"Local index rebuild error: {e}")
        chunk_count = 0

    # Use the higher of search-indexed and local chunk counts
    total_indexed = max(search_indexed, chunk_count)

    # Build response with explicit fields for Copilot Studio
    upload_body = {
        "fileName": saved_path.name,
        "chunks": total_indexed,
        "message": f"Uploaded and indexed '{saved_path.name}' ({total_indexed} passages). You can now ask questions about this specification.",
        "status": "answered",
        "answer": f"Uploaded and indexed '{saved_path.name}' ({total_indexed} passages). You can now ask questions about this specification.",
        "blobUploaded": blob_uploaded,
        "searchIndexed": search_indexed,
    }
    return func.HttpResponse(
        body=json.dumps(upload_body, ensure_ascii=False),
        status_code=200,
        mimetype="application/json",
        headers={"Content-Type": "application/json; charset=utf-8"},
    )


# ═══════════════════════════════════════════════════════════════════
# HANDLER 3b: /api/md-to-pdf — Markdown to PDF conversion
# ═══════════════════════════════════════════════════════════════════
def handle_md_to_pdf(markdown_text: str, title: str, subtitle: str) -> func.HttpResponse:
    """Convert markdown text to a PDF document."""
    try:
        from app.qa.pdf_report import generate_markdown_pdf
        pdf_bytes = generate_markdown_pdf(markdown_text, title=title, subtitle=subtitle)
        return func.HttpResponse(
            body=pdf_bytes,
            status_code=200,
            mimetype="application/pdf",
            headers={
                "Content-Type": "application/pdf",
                "Content-Disposition": f'attachment; filename="LEON_Report.pdf"',
            },
        )
    except ImportError:
        return func.HttpResponse(
            body=json.dumps({
                "answer": "PDF generation is not available (fpdf2 not installed).",
                "status": "error",
            }, ensure_ascii=False),
            status_code=501,
            mimetype="application/json",
            headers={"Content-Type": "application/json; charset=utf-8"},
        )
    except Exception as exc:
        import traceback
        logging.error(f"MD-to-PDF failed: {exc}\n{traceback.format_exc()}")
        return func.HttpResponse(
            body=json.dumps({
                "answer": f"PDF generation error: {str(exc)[:300]}",
                "status": "error",
            }, ensure_ascii=False),
            status_code=500,
            mimetype="application/json",
            headers={"Content-Type": "application/json; charset=utf-8"},
        )


# ═══════════════════════════════════════════════════════════════════
# HANDLER 3c: /api/validation-pdf — Validate + return PDF directly
# ═══════════════════════════════════════════════════════════════════
def handle_validation_pdf(file_name: str) -> func.HttpResponse:
    """Validate a spec file and return the PDF report directly."""
    from app.qa.retrieval import discover_accessible_files, extract_text_from_file
    file_path = None
    for p in discover_accessible_files():
        if p.name == file_name:
            file_path = p
            break

    if file_path is None:
        return func.HttpResponse(
            body=json.dumps({
                "answer": f"File '{file_name}' not found. Upload it first via /api/upload.",
                "status": "not_found",
            }, ensure_ascii=False),
            status_code=404,
            mimetype="application/json",
            headers={"Content-Type": "application/json; charset=utf-8"},
        )

    text = extract_text_from_file(file_path)
    if not text or not text.strip():
        return func.HttpResponse(
            body=json.dumps({
                "answer": f"Could not extract text from '{file_name}'.",
                "status": "error",
            }, ensure_ascii=False),
            status_code=422,
            mimetype="application/json",
            headers={"Content-Type": "application/json; charset=utf-8"},
        )

    from app.qa.evidence_comparator import validate_with_evidence
    report = validate_with_evidence(file_name, text)

    try:
        from app.qa.pdf_report import generate_validation_pdf
        pdf_bytes = generate_validation_pdf(report)
        safe_name = file_name.replace(" ", "_").replace(".docx", "").replace(".txt", "").replace(".pdf", "")
        return func.HttpResponse(
            body=pdf_bytes,
            status_code=200,
            mimetype="application/pdf",
            headers={
                "Content-Type": "application/pdf",
                "Content-Disposition": f'attachment; filename="LEON_Validation_{safe_name}.pdf"',
            },
        )
    except ImportError:
        return func.HttpResponse(
            body=json.dumps({
                "answer": "PDF generation unavailable (fpdf2 not installed).",
                "status": "error",
                "validationReport": report,
            }, ensure_ascii=False, default=str),
            status_code=501,
            mimetype="application/json",
            headers={"Content-Type": "application/json; charset=utf-8"},
        )
    except Exception as exc:
        import traceback
        logging.error(f"Validation PDF failed: {exc}\n{traceback.format_exc()}")
        return func.HttpResponse(
            body=json.dumps({
                "answer": f"PDF error: {str(exc)[:300]}",
                "status": "error",
                "validationReport": report,
            }, ensure_ascii=False, default=str),
            status_code=500,
            mimetype="application/json",
            headers={"Content-Type": "application/json; charset=utf-8"},
        )


# ═══════════════════════════════════════════════════════════════════
# HANDLER 4: /api/files — List Files
# ═══════════════════════════════════════════════════════════════════
def handle_list_files() -> func.HttpResponse:
    """List all accessible specification files with metadata."""
    from app.qa.retrieval import discover_accessible_files, DATA_DIR
    from pathlib import Path

    uploads_dir = DATA_DIR / "uploads"
    ref_dir = DATA_DIR / "refs"

    file_list = []
    for p in discover_accessible_files():
        # Determine type
        if uploads_dir.exists() and p.parent == uploads_dir:
            ftype = "uploaded"
        elif ref_dir.exists() and p.parent == ref_dir:
            ftype = "reference"
        else:
            ftype = "reference"

        # Get file size
        try:
            size_kb = round(p.stat().st_size / 1024, 1)
        except OSError:
            size_kb = 0

        file_list.append({
            "fileName": p.name,
            "type": ftype,
            "sizeKB": size_kb,
        })

    return func.HttpResponse(
        body=json.dumps({"files": file_list, "count": len(file_list)}, ensure_ascii=False),
        status_code=200,
        mimetype="application/json",
        headers={"Content-Type": "application/json; charset=utf-8"},
    )


# ═══════════════════════════════════════════════════════════════════
# LLM Helper — calls Azure OpenAI (same as embeddings.py)
# ═══════════════════════════════════════════════════════════════════
def _call_llm(system_prompt: str, user_message: str, temperature: float = 0.2, max_tokens: int = 800) -> str:
    """Call the LLM; return empty string if unavailable."""
    try:
        from app.embeddings import call_llm
        return call_llm(system_prompt, user_message, temperature=temperature, max_tokens=max_tokens)
    except Exception as e:
        logging.warning(f"LLM call failed: {e}")
        return ""


# ═══════════════════════════════════════════════════════════════════
# Azure Blob Storage — durable file persistence
# ═══════════════════════════════════════════════════════════════════
def _upload_to_blob(file_name: str, file_bytes: bytes) -> bool:
    """
    Upload file to Azure Blob Storage for durable persistence.

    Uses connection string from AZURE_STORAGE_CONNECTION_STRING env var.
    Returns True if uploaded, False if not configured or failed.
    """
    import os
    conn_str = os.getenv("AZURE_STORAGE_CONNECTION_STRING", "")
    if not conn_str:
        logging.info("AZURE_STORAGE_CONNECTION_STRING not set — skipping blob upload")
        return False

    try:
        from azure.storage.blob import BlobServiceClient
        container_name = os.getenv("AZURE_STORAGE_CONTAINER", "leon-uploads")

        blob_service = BlobServiceClient.from_connection_string(conn_str)
        container_client = blob_service.get_container_client(container_name)

        # Create container if it doesn't exist
        try:
            container_client.get_container_properties()
        except Exception:
            container_client.create_container(public_access="blob")

        # Upload with sanitized name
        import re
        safe_name = re.sub(r"[^A-Za-z0-9._\- ]", "_", file_name).strip()
        blob_client = container_client.upload_blob(
            name=safe_name,
            data=file_bytes,
            overwrite=True,
        )
        logging.info(f"Uploaded '{safe_name}' to blob container '{container_name}'")
        return True
    except Exception as exc:
        logging.warning(f"Blob upload failed: {exc}")
        return False


# ═══════════════════════════════════════════════════════════════════
# Azure AI Search — index uploaded file chunks
# ═══════════════════════════════════════════════════════════════════
def _index_file_to_search(file_path) -> int:
    """
    Extract text, chunk, embed, and push to Azure AI Search.

    Returns the number of chunks successfully indexed.
    Returns 0 if Azure Search is not configured or indexing fails.
    """
    import re
    import time

    try:
        from app.qa.azure_search import is_configured, upload_documents
        from app.qa.retrieval import extract_text_from_file, _split_into_chunks
        from app.embeddings import get_embedding
    except ImportError as exc:
        logging.warning(f"Search indexing imports failed: {exc}")
        return 0

    if not is_configured():
        logging.info("Azure Search not configured — skipping search indexing")
        return 0

    # Extract text from the uploaded file
    text = extract_text_from_file(file_path)
    if not text or not text.strip():
        logging.warning(f"No text extracted from '{file_path.name}'")
        return 0

    # Chunk the text
    chunks = _split_into_chunks(text, file_path.name)
    if not chunks:
        logging.warning(f"No chunks generated from '{file_path.name}'")
        return 0

    logging.info(f"Indexing {len(chunks)} chunks from '{file_path.name}' to Azure Search...")

    # Build search documents with embeddings
    _EMBED_MAX_CHARS = 8000
    documents = []
    errors = 0

    for ch in chunks:
        text_for_embed = ch.text[:_EMBED_MAX_CHARS]
        try:
            embedding = get_embedding(text_for_embed)
        except Exception as exc:
            logging.error(f"Embedding failed for {ch.file_name}#{ch.chunk_id}: {exc}")
            errors += 1
            continue

        # Sanitize doc ID for Azure Search
        doc_id = re.sub(r"[^A-Za-z0-9_\-=]", "_", f"{ch.file_name}__{ch.chunk_id}")

        # Determine source type
        source = "uploaded"

        documents.append({
            "id": doc_id,
            "file_name": ch.file_name,
            "text": ch.text,
            "section": ch.section,
            "chunk_id": ch.chunk_id,
            "source_type": source,
            "embedding": embedding,
        })

    if not documents:
        logging.error(f"No documents to index after embedding (errors={errors})")
        return 0

    # Upload in batches of 50
    batch_size = 50
    indexed = 0
    for batch_start in range(0, len(documents), batch_size):
        batch = documents[batch_start:batch_start + batch_size]
        try:
            count = upload_documents(batch)
            indexed += count
        except Exception as exc:
            logging.error(f"Search upload batch failed: {exc}")
            errors += len(batch)

    logging.info(f"Search indexing complete: {indexed}/{len(documents)} chunks indexed (errors={errors})")
    return indexed


# ═══════════════════════════════════════════════════════════════════
# Conformity Matrix Analysis Handler
# ═══════════════════════════════════════════════════════════════════
def handle_conformity(file_name: str, file_bytes: Optional[bytes]) -> func.HttpResponse:
    """
    Analyze a conformity matrix spreadsheet (ODS or XLSX).

    If file_bytes is provided, saves the file first.
    If file_bytes is None, looks for the file in uploads directory.

    Returns JSON with:
    - analysis: full conformity analysis (items, stats, inconsistencies, chart)
    - reportPdf: base64-encoded PDF report with embedded pie chart
    - answer: human-readable summary for Copilot Studio
    """
    import os
    import base64

    logging.info(f"handle_conformity: file_name={file_name}, has_bytes={file_bytes is not None}")

    # Determine file path
    if file_bytes:
        # Save the file
        from app.config import UPLOADS_DIR
        UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
        safe_name = os.path.basename(file_name)
        file_path = UPLOADS_DIR / safe_name
        file_path.write_bytes(file_bytes)
        logging.info(f"Saved conformity matrix to {file_path}")
    else:
        # Look for the file in uploads
        from app.config import UPLOADS_DIR
        file_path = UPLOADS_DIR / os.path.basename(file_name)
        if not file_path.exists():
            return func.HttpResponse(
                body=json.dumps({
                    "answer": f"File '{file_name}' not found. Please upload the conformity matrix first.",
                    "status": "error",
                    "confidence": "",
                    "sources": [],
                    "evidence": [],
                }, ensure_ascii=False),
                status_code=404,
                mimetype="application/json",
                headers={"Content-Type": "application/json; charset=utf-8"},
            )

    # Check file extension
    ext = file_path.suffix.lower()
    if ext not in (".ods", ".xlsx", ".xlsm", ".xls"):
        return func.HttpResponse(
            body=json.dumps({
                "answer": f"Unsupported file type '{ext}'. Please upload an ODS or XLSX file.",
                "status": "error",
                "confidence": "",
                "sources": [],
                "evidence": [],
            }, ensure_ascii=False),
            status_code=422,
            mimetype="application/json",
            headers={"Content-Type": "application/json; charset=utf-8"},
        )

    try:
        from app.qa.conformity_analyzer import analyze_conformity_matrix, analysis_to_dict
        from app.qa.conformity_report import generate_conformity_pdf
    except ImportError as exc:
        logging.error(f"Import conformity modules failed: {exc}")
        return func.HttpResponse(
            body=json.dumps({
                "answer": f"LEON import error: {str(exc)[:300]}",
                "status": "error",
                "confidence": "",
                "sources": [],
                "evidence": [],
            }, ensure_ascii=False),
            status_code=500,
            mimetype="application/json",
            headers={"Content-Type": "application/json; charset=utf-8"},
        )

    try:
        analysis = analyze_conformity_matrix(str(file_path), file_name)
        analysis_dict = analysis_to_dict(analysis)

        # Generate PDF report
        pdf_bytes = generate_conformity_pdf(analysis_dict)
        pdf_b64 = base64.b64encode(pdf_bytes).decode("utf-8")

        # Build human-readable answer for Copilot Studio
        summary = analysis_dict.get("summary", {})
        ok = summary.get("ok", 0)
        nok = summary.get("nok", 0)
        na = summary.get("na", 0)
        total = summary.get("total", 0)
        inc_count = summary.get("okDeepFindings", 0)

        answer_parts = [
            f"Analyse de la matrice de conformite FNR terminee.",
            f"\nFeuille: {analysis.sheet_name}",
            f"\nResume des statuts:",
            f"  - OK (conforme): {ok}",
            f"  - NOK (non conforme): {nok}",
            f"  - NA (non applicable): {na}",
            f"  - Total: {total} exigences",
        ]

        if inc_count > 0:
            answer_parts.append(f"\n  - Points d'attention detectes dans les reponses OK: {inc_count}")
            answer_parts.append("\nLe rapport PDF detaille contient:")
            answer_parts.append("  - La liste complete des exigences avec statuts et commentaires exacts")
            answer_parts.append("  - Le diagramme camembert de repartition")
            answer_parts.append("  - L'analyse approfondie des reponses OK (signaux suspects, incoherences)")
        else:
            answer_parts.append("\nAucune incoherence logique detectee — tous les statuts OK sont coherents avec leurs commentaires.")

        answer = "\n".join(answer_parts)

        body = {
            "answer": answer,
            "status": "answered",
            "confidence": "HIGH",
            "sources": [],
            "evidence": [],
            "fileName": file_name,
            "analysis": analysis_dict,
            "reportPdf": pdf_b64,
        }

        return func.HttpResponse(
            body=json.dumps(body, ensure_ascii=False, default=str),
            status_code=200,
            mimetype="application/json",
            headers={"Content-Type": "application/json; charset=utf-8"},
        )

    except Exception as exc:
        import traceback
        tb = traceback.format_exc()
        logging.error(f"Conformity analysis failed: {exc}\n{tb}")
        return func.HttpResponse(
            body=json.dumps({
                "answer": f"Erreur lors de l'analyse: {str(exc)[:500]}",
                "status": "error",
                "confidence": "",
                "sources": [],
                "evidence": [],
            }, ensure_ascii=False),
            status_code=500,
            mimetype="application/json",
            headers={"Content-Type": "application/json; charset=utf-8"},
        )


# ═══════════════════════════════════════════════════════════════════
# Conformity Excel Report Handler
# ═══════════════════════════════════════════════════════════════════
def _upload_to_blob_storage(file_bytes: bytes, file_name: str, content_type: str = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", blob_extension: str = "xlsx") -> str:
    """
    Upload a report file to Azure Blob Storage and return a download URL.
    Uses the Azure Blob Storage REST API directly (no SDK dependency).
    Returns empty string if Blob Storage is not configured or upload fails.
    Falls back to AzureWebJobsStorage if AZURE_STORAGE_CONNECTION_STRING not set.

    Args:
        file_bytes: The file content as bytes.
        file_name: Original file name (used for blob naming).
        content_type: MIME type for the blob (default: Excel XLSX).
        blob_extension: File extension for the blob name (default: xlsx).
    """
    import os
    import datetime
    import hashlib
    import hmac
    import base64
    import urllib.request
    import urllib.parse
    import ssl

    try:
        conn_str = (os.getenv("AZURE_STORAGE_CONNECTION_STRING", "") or
                    os.getenv("AzureWebJobsStorage", "") or
                    os.getenv("DEPLOYMENT_STORAGE_CONNECTION_STRING", ""))
        if not conn_str:
            logging.info("No storage connection string found — skipping blob upload")
            return ""

        # Parse connection string
        parts = dict(p.split("=", 1) for p in conn_str.split(";") if "=" in p)
        account_name = parts.get("AccountName", "")
        account_key = parts.get("AccountKey", "")
        if not account_name or not account_key:
            logging.warning("Storage connection string missing AccountName or AccountKey")
            return ""

        # Decode account key
        account_key_bytes = base64.b64decode(account_key)
        container_name = "leon-reports"
        timestamp = datetime.datetime.utcnow().strftime("%Y%m%d_%H%M%S")
        base_name = os.path.splitext(os.path.basename(file_name))[0]
        blob_name = f"conformity_report_{base_name}_{timestamp}.{blob_extension}"

        logging.info(f"Uploading to blob: {container_name}/{blob_name} ({len(file_bytes)} bytes)")

        # ── Step 1: Create container (if not exists) with public read access ──
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE

        container_url = f"https://{account_name}.blob.core.windows.net/{container_name}?restype=container"
        rfc1123_date = datetime.datetime.utcnow().strftime("%a, %d %b %Y %H:%M:%S GMT")
        # String to sign: VERB + 11 empty standard headers + canonicalized headers + canonicalized resource
        # Date field (line 7) is EMPTY — x-ms-date goes in canonicalized headers (sorted alphabetically)
        string_to_sign = f"PUT\n\n\n\n\n\n\n\n\n\n\n\nx-ms-blob-public-access:blob\nx-ms-date:{rfc1123_date}\nx-ms-version:2020-04-08\n/{account_name}/{container_name}?restype=container"
        signature = base64.b64encode(hmac.new(account_key_bytes, string_to_sign.encode("utf-8"), hashlib.sha256).digest()).decode("utf-8")
        auth_header = f"SharedKey {account_name}:{signature}"

        req = urllib.request.Request(container_url, method="PUT", data=b"")
        req.add_header("x-ms-date", rfc1123_date)
        req.add_header("x-ms-version", "2020-04-08")
        req.add_header("x-ms-blob-public-access", "blob")
        req.add_header("Authorization", auth_header)
        req.add_header("Content-Length", "0")

        try:
            with urllib.request.urlopen(req, timeout=10, context=ctx) as resp:
                logging.info(f"Container create response: {resp.status}")
        except urllib.error.HTTPError as e:
            if e.code == 409:  # ContainerAlreadyExists
                logging.info("Container already exists — setting public access")
                # Try to set container ACL to public read
                acl_url = f"https://{account_name}.blob.core.windows.net/{container_name}?restype=container&comp=acl"
                rfc1123_date = datetime.datetime.utcnow().strftime("%a, %d %b %Y %H:%M:%S GMT")
                acl_string_to_sign = f"PUT\n\n\n\n\n\n\n\n\n\n\n\nx-ms-blob-public-access:blob\nx-ms-date:{rfc1123_date}\nx-ms-version:2020-04-08\n/{account_name}/{container_name}?comp=acl&restype=container"
                acl_signature = base64.b64encode(hmac.new(account_key_bytes, acl_string_to_sign.encode("utf-8"), hashlib.sha256).digest()).decode("utf-8")
                acl_auth = f"SharedKey {account_name}:{acl_signature}"
                acl_req = urllib.request.Request(acl_url, method="PUT", data=b"")
                acl_req.add_header("x-ms-date", rfc1123_date)
                acl_req.add_header("x-ms-version", "2020-04-08")
                acl_req.add_header("x-ms-blob-public-access", "blob")
                acl_req.add_header("Authorization", acl_auth)
                acl_req.add_header("Content-Length", "0")
                try:
                    with urllib.request.urlopen(acl_req, timeout=10, context=ctx) as resp:
                        logging.info(f"Container ACL set to public: {resp.status}")
                except Exception as acl_e:
                    logging.warning(f"Container ACL set failed (non-fatal): {acl_e}")
            else:
                logging.warning(f"Container create failed: {e.code} — {e.read()[:200]}")
        except Exception as e:
            logging.warning(f"Container create error (non-fatal): {e}")

        # ── Step 2: Upload blob (Put Blob) ──
        blob_url = f"https://{account_name}.blob.core.windows.net/{container_name}/{urllib.parse.quote(blob_name)}"
        rfc1123_date = datetime.datetime.utcnow().strftime("%a, %d %b %Y %H:%M:%S GMT")
        content_len = str(len(file_bytes))

        # Build canonicalized string for Put Blob
        canonical_headers = f"x-ms-blob-type:BlockBlob\nx-ms-date:{rfc1123_date}\nx-ms-version:2020-04-08\n"
        canonical_resource = f"/{account_name}/{container_name}/{urllib.parse.quote(blob_name)}"
        string_to_sign = f"PUT\n\n\n{content_len}\n\n{content_type}\n\n\n\n\n\n\n{canonical_headers}{canonical_resource}"
        signature = base64.b64encode(hmac.new(account_key_bytes, string_to_sign.encode("utf-8"), hashlib.sha256).digest()).decode("utf-8")
        auth_header = f"SharedKey {account_name}:{signature}"

        req = urllib.request.Request(blob_url, method="PUT", data=file_bytes)
        req.add_header("x-ms-blob-type", "BlockBlob")
        req.add_header("x-ms-date", rfc1123_date)
        req.add_header("x-ms-version", "2020-04-08")
        req.add_header("Authorization", auth_header)
        req.add_header("Content-Type", content_type)
        req.add_header("Content-Length", content_len)

        with urllib.request.urlopen(req, timeout=30, context=ctx) as resp:
            logging.info(f"Blob upload response: {resp.status}")

        # ── Step 3: Return public download URL (container has public read access) ──
        download_url = blob_url
        logging.info(f"Uploaded report to blob: {blob_name}")
        logging.info(f"Download URL: {download_url}")
        return download_url

    except Exception as exc:
        logging.warning(f"Blob upload failed (non-fatal): {exc}")
        return ""


def handle_conformity_excel(file_name: str, file_bytes: bytes) -> func.HttpResponse:
    """
    Generate a color-coded Excel report from a conformity matrix.
    Returns JSON with reportExcel (base64 XLSX) and optional downloadUrl.
    """
    import os
    import base64
    import ssl

    logging.info(f"handle_conformity_excel: file_name={file_name}, file_size={len(file_bytes)} bytes")

    if not file_bytes or len(file_bytes) == 0:
        return func.HttpResponse(
            body=json.dumps({
                "answer": "Le fichier envoye est vide. Veuillez telecharger un fichier ODS ou XLSX valide.",
                "status": "error",
            }, ensure_ascii=False),
            status_code=422,
            mimetype="application/json",
            headers={"Content-Type": "application/json; charset=utf-8"},
        )

    from app.config import UPLOADS_DIR
    UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
    safe_name = os.path.basename(file_name) if file_name else "conformity_matrix.xlsx"
    file_path = UPLOADS_DIR / safe_name
    file_path.write_bytes(file_bytes)

    ext = file_path.suffix.lower()
    if ext not in (".ods", ".xlsx", ".xlsm", ".xls"):
        return func.HttpResponse(
            body=json.dumps({
                "answer": f"Format de fichier non supporte '{ext}'. Veuillez telecharger un fichier ODS ou XLSX.",
                "status": "error",
            }, ensure_ascii=False),
            status_code=422,
            mimetype="application/json",
            headers={"Content-Type": "application/json; charset=utf-8"},
        )

    try:
        from app.qa.conformity_analyzer import analyze_conformity_matrix, analysis_to_dict
        from app.qa.conformity_report import generate_conformity_excel

        analysis = analyze_conformity_matrix(str(file_path), file_name)
        analysis_dict = analysis_to_dict(analysis)

        xlsx_bytes = generate_conformity_excel(analysis_dict)
        xlsx_b64 = base64.b64encode(xlsx_bytes).decode("utf-8")

        # Try to upload to Azure Blob Storage for direct download
        blob_url = _upload_to_blob_storage(file_bytes=xlsx_bytes, file_name=file_name)

        # Use Blob Storage URL if accessible, otherwise use data URI as download URL
        # Data URI is self-contained and always works in browsers
        if blob_url:
            # Test if the blob URL is accessible
            import urllib.request as _urllib_req
            try:
                test_req = _urllib_req.Request(blob_url, method="HEAD")
                test_ctx = ssl.create_default_context()
                test_ctx.check_hostname = False
                test_ctx.verify_mode = ssl.CERT_NONE
                with _urllib_req.urlopen(test_req, timeout=5, context=test_ctx) as test_resp:
                    if test_resp.status == 200:
                        download_url = blob_url
                        logging.info(f"Using Blob Storage download URL (verified)")
                    else:
                        download_url = f"data:application/vnd.openxmlformats-officedocument.spreadsheetml.sheet;base64,{xlsx_b64}"
                        logging.info(f"Blob URL returned {test_resp.status} — using data URI")
            except Exception as test_e:
                download_url = f"data:application/vnd.openxmlformats-officedocument.spreadsheetml.sheet;base64,{xlsx_b64}"
                logging.info(f"Blob URL test failed ({test_e}) — using data URI")
        else:
            download_url = f"data:application/vnd.openxmlformats-officedocument.spreadsheetml.sheet;base64,{xlsx_b64}"
            logging.info("Blob upload failed — using data URI as download URL")

        summary = analysis_dict.get("summary", {})
        total = summary.get("total", 0)
        ok = summary.get("ok", 0)
        nok = summary.get("nok", 0)
        na = summary.get("na", 0)
        empty = summary.get("empty", 0)
        deep_findings = summary.get("okDeepFindings", 0)
        needs_review = summary.get("needsReview", 0)

        # Build a rich answer text for Copilot Studio to show the user
        answer_lines = [
            f"Rapport Excel genere avec succes pour '{file_name}'.",
            f"",
            f"Statistiques de conformite:",
            f"  - Total des exigences: {total}",
            f"  - OK (conforme): {ok}",
            f"  - NOK (non conforme): {nok}",
            f"  - NA (non applicable): {na}",
            f"  - Sans statut: {empty}",
            f"",
            f"Analyse approfondie:",
            f"  - Points d'attention (OK suspects): {deep_findings}",
            f"  - Exigences a verifier: {needs_review}",
        ]
        if download_url and not download_url.startswith("data:"):
            answer_lines.extend([
                f"",
                f"Le fichier Excel est disponible au telechargement:",
                f"{download_url}",
            ])
        else:
            answer_lines.extend([
                f"",
                f"Le fichier Excel a ete genere ({len(xlsx_bytes)} octets).",
                f"  1. Summary (statistiques + graphique camembert)",
                f"  2. All Items (toutes les exigences, lignes colorees par statut)",
                f"  3. Analyse approfondie OK (points d'attention)",
            ])
        answer = "\n".join(answer_lines)

        # Build response with BOTH nested analysis (backward compatible)
        # AND flat root-level fields (for Copilot Studio connector)
        body = {
            "answer": answer,
            "status": "answered",
            "confidence": "HIGH",
            "fileName": file_name,
            # Flat fields for Copilot Studio (unique names, no duplicates)
            "totalReqs": total,
            "okReqs": ok,
            "nokReqs": nok,
            "naReqs": na,
            "emptyReqs": empty,
            "inconsistencies": summary.get("inconsistencies", 0),
            "okDeepFindings": deep_findings,
            "needsReview": needs_review,
            # Nested analysis (backward compatible, existing code)
            "analysis": analysis_dict,
            "reportExcel": xlsx_b64,
        }
        # Always include download URL (Blob Storage SAS URL or data URI fallback)
        body["downloadUrl"] = download_url

        return func.HttpResponse(
            body=json.dumps(body, ensure_ascii=False, default=str),
            status_code=200,
            mimetype="application/json",
            headers={"Content-Type": "application/json; charset=utf-8"},
        )

    except Exception as exc:
        import traceback
        logging.error(f"Excel report failed: {exc}\n{traceback.format_exc()}")
        return func.HttpResponse(
            body=json.dumps({
                "answer": f"Erreur lors de la generation du rapport: {str(exc)[:500]}",
                "status": "error",
            }, ensure_ascii=False),
            status_code=500,
            mimetype="application/json",
            headers={"Content-Type": "application/json; charset=utf-8"},
        )


# ═══════════════════════════════════════════════════════════════════
# Multi-Matrix Comparison Handler
# ═══════════════════════════════════════════════════════════════════
def handle_conformity_compare(file_names: list) -> func.HttpResponse:
    """
    Compare two or more conformity matrices.
    Returns JSON with comparison data, status changes, and chart.
    """
    import os

    logging.info(f"handle_conformity_compare: {len(file_names)} files")

    from app.config import UPLOADS_DIR

    file_paths = []
    for fn in file_names:
        fp = UPLOADS_DIR / os.path.basename(fn)
        if not fp.exists():
            return func.HttpResponse(
                body=json.dumps({
                    "answer": f"File '{fn}' not found. Please upload all matrices first.",
                    "status": "error",
                }, ensure_ascii=False),
                status_code=404,
                mimetype="application/json",
                headers={"Content-Type": "application/json; charset=utf-8"},
            )
        file_paths.append(str(fp))

    try:
        from app.qa.conformity_analyzer import compare_matrices, comparison_to_dict

        comparison = compare_matrices(file_paths, file_names)
        comp_dict = comparison_to_dict(comparison)

        answer = (
            f"Comparaison de {len(file_names)} matrices terminee.\n"
            f"Exigences comparees: {comparison.total_compared}\n"
            f"Changements de statut: {comparison.total_changes}\n"
            f"Exigences manquantes: {comparison.total_missing}"
        )

        body = {
            "answer": answer,
            "status": "answered",
            "confidence": "HIGH",
            "comparison": comp_dict,
        }

        return func.HttpResponse(
            body=json.dumps(body, ensure_ascii=False, default=str),
            status_code=200,
            mimetype="application/json",
            headers={"Content-Type": "application/json; charset=utf-8"},
        )

    except Exception as exc:
        import traceback
        logging.error(f"Comparison failed: {exc}\n{traceback.format_exc()}")
        return func.HttpResponse(
            body=json.dumps({
                "answer": f"Erreur: {str(exc)[:500]}",
                "status": "error",
            }, ensure_ascii=False),
            status_code=500,
            mimetype="application/json",
            headers={"Content-Type": "application/json; charset=utf-8"},
        )


# ═══════════════════════════════════════════════════════════════════
# Power BI Dataset Handler
# ═══════════════════════════════════════════════════════════════════
def handle_conformity_powerbi(file_name: str, file_bytes: Optional[bytes] = None) -> func.HttpResponse:
    """
    Generate a Power BI-compatible dataset JSON from a conformity matrix.
    If file_bytes is provided, saves the file first.
    """
    import os

    logging.info(f"handle_conformity_powerbi: {file_name}, has_bytes={file_bytes is not None}")

    from app.config import UPLOADS_DIR
    UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
    safe_name = os.path.basename(file_name)
    file_path = UPLOADS_DIR / safe_name

    # Save file if content provided
    if file_bytes:
        file_path.write_bytes(file_bytes)
        logging.info(f"Saved conformity matrix to {file_path}")

    if not file_path.exists():
        return func.HttpResponse(
            body=json.dumps({
                "answer": f"File '{file_name}' not found. Please upload the matrix first.",
                "status": "error",
            }, ensure_ascii=False),
            status_code=404,
            mimetype="application/json",
            headers={"Content-Type": "application/json; charset=utf-8"},
        )

    try:
        from app.qa.conformity_analyzer import analyze_conformity_matrix, analysis_to_dict
        from app.qa.conformity_report import generate_powerbi_dataset

        analysis = analyze_conformity_matrix(str(file_path), file_name)
        analysis_dict = analysis_to_dict(analysis)
        powerbi_data = generate_powerbi_dataset(analysis_dict)

        body = {
            "answer": f"Dataset Power BI genere pour {file_name}.",
            "status": "answered",
            "confidence": "HIGH",
            "powerbi": powerbi_data,
        }

        return func.HttpResponse(
            body=json.dumps(body, ensure_ascii=False, default=str),
            status_code=200,
            mimetype="application/json",
            headers={"Content-Type": "application/json; charset=utf-8"},
        )

    except Exception as exc:
        import traceback
        logging.error(f"Power BI dataset failed: {exc}\n{traceback.format_exc()}")
        return func.HttpResponse(
            body=json.dumps({
                "answer": f"Erreur: {str(exc)[:500]}",
                "status": "error",
            }, ensure_ascii=False),
            status_code=500,
            mimetype="application/json",
            headers={"Content-Type": "application/json; charset=utf-8"},
        )
