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
    body = {
        "answer": answer,
        "status": status,
        "confidence": confidence,
        "sources": sources or [],
        "evidence": evidence or [],
    }
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

    # ── 4. Section-guidance detection ───────────────────────────────
    from app.qa.section_guidance import is_section_guidance_question, get_section_guidance
    if is_section_guidance_question(question):
        guidance = get_section_guidance(question)
        if guidance:
            # Try LLM synthesis
            guidance_text = guidance.get("answer", "")
            guidance_prompt = (
                "You are a CTS specification writing assistant for Stellantis. "
                "A user is writing a Component Technical Specification and needs "
                "guidance on a specific section. Below is the EXACT guidance "
                "extracted from the Stellantis CTS template and writing guide. "
                "Synthesize a clear, structured, helpful answer that tells the "
                "user exactly what to put in this section.\n\n"
                "IMPORTANT: Only use the guidance provided below. Do NOT invent. "
                "Structure: 1) Section purpose, 2) What to include, "
                "3) Key rules to follow, 4) Common mistakes to avoid.\n\n"
                "GUIDANCE:\n" + guidance_text
            )
            llm_answer = _call_llm(
                "You are a Stellantis engineering specification expert.",
                guidance_prompt
            )
            final_answer = llm_answer if llm_answer else guidance_text
            return _response(
                final_answer,
                status="answered",
                confidence="HIGH",
                evidence=[guidance.get("detected_section", "")],
            )

    # ── 5. Overview question handling ──────────────────────────────
    from app.qa.route import _is_overview_question, _detect_referenced_file, _retrieve_file_overview
    from app.qa.retrieval import retrieve, RetrievalResult
    from app.qa.route import _try_acronym_retrieval

    chunks = _get_index()
    if not chunks:
        return _response(
            "No accessible specification files are currently indexed.",
            status="not_found"
        )

    result = None
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

    # ── 6. Standard retrieval ──────────────────────────────────────
    if result is None:
        acronym_result = _try_acronym_retrieval(question, chunks)
        if acronym_result:
            result = acronym_result
        else:
            # TRY AZURE AI SEARCH FIRST (hybrid vector+text).
            # Falls back silently to local keyword retrieval if Azure
            # Search is not configured or unreachable.
            try:
                from app.qa.retrieval import azure_search_retrieve
                result = azure_search_retrieve(question)
            except Exception:
                result = retrieve(question, chunks=chunks, use_semantic=False)

    # ── 7. No support found ────────────────────────────────────────
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
    """Validate a specification file against the CTS template."""

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

    return _response(
        summary,
        status="answered",
        confidence="",
        validation_report=report,
    )


# ═══════════════════════════════════════════════════════════════════
# HANDLER 3: /api/upload — File Upload
# ═══════════════════════════════════════════════════════════════════
def handle_upload(file_name: str, file_bytes: bytes) -> func.HttpResponse:
    """Save an uploaded spec file and rebuild the index."""

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

    from app.qa.retrieval import save_uploaded_file, build_index
    try:
        saved_path = save_uploaded_file(file_name, file_bytes)
    except Exception as e:
        logging.error(f"Upload save error: {e}")
        return _response(f"Failed to save uploaded file: {e}", status="error", status_code=500)

    # Rebuild index
    try:
        _reset_index()
        chunks = _get_index()
        chunk_count = sum(1 for c in chunks if c.file_name == saved_path.name)
    except Exception as e:
        logging.error(f"Index rebuild error: {e}")
        chunk_count = 0

    return _response(
        f"Uploaded and indexed '{saved_path.name}' ({chunk_count} passages). "
        f"You can now ask questions about this specification.",
        status="answered",
        confidence="HIGH",
    )


# ═══════════════════════════════════════════════════════════════════
# HANDLER 4: /api/files — List Files
# ═══════════════════════════════════════════════════════════════════
def handle_list_files() -> func.HttpResponse:
    """List all accessible specification files."""
    from app.qa.retrieval import discover_accessible_files
    files = [p.name for p in discover_accessible_files()]
    return func.HttpResponse(
        body=json.dumps({"files": files, "count": len(files)}),
        status_code=200,
        mimetype="application/json",
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
