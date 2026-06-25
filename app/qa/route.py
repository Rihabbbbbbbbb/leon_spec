"""
FastAPI router for the Q&A assistant.

POST /api/ask
  body: { question: str, fileContext?: str, useMock?: bool }
  response: { answer: str, sources: [{ fileName: str, excerpt: str }] }

The route:
1. Detects standards/BeStandard questions → fixed refusal message.
2. Retrieves relevant passages from accessible spec files.
3. If no support found → fixed "not found" message.
4. Otherwise calls the LLM with the strict prompt (or returns a mock answer).
"""
from __future__ import annotations

from pathlib import Path
from typing import List, Optional

from fastapi import APIRouter, HTTPException, UploadFile, File
from pydantic import BaseModel, Field

from app.qa.prompt import (
    SYSTEM_PROMPT,
    build_user_prompt,
    is_standards_question,
    NOT_FOUND_MESSAGE,
    STANDARDS_REFUSAL_MESSAGE,
    PARTIAL_INFO_MESSAGE,
    extract_confidence,
    compute_confidence_from_scores,
)
from app.qa.retrieval import (
    retrieve, build_index, Chunk, RetrievalResult,
    save_uploaded_file, delete_uploaded_file, extract_text_from_file,
)
from app.qa.mock_data import mock_answer
from app.qa.spec_validator import validate_specification, report_to_dict
from app.qa.evidence_comparator import validate_with_evidence
from app.qa.metrics import metrics_store, QaRecord


router = APIRouter(prefix="/api", tags=["qa"])


class AskRequest(BaseModel):
    question: str = Field(..., min_length=1)
    fileContext: Optional[str] = None   # optional selected file name / context
    useMock: bool = False               # force mock mode (no LLM, no embeddings)


class SourceItem(BaseModel):
    fileName: str
    excerpt: str


class AskResponse(BaseModel):
    answer: str
    sources: List[SourceItem] = Field(default_factory=list)
    confidence: str = ""  # HIGH | MEDIUM | LOW | "" (empty for refusals)
    validationReport: Optional[dict] = None  # populated when user asks to validate
    # Enterprise structured-output fields (always populated for real answers)
    evidence: List[str] = Field(default_factory=list)  # excerpt(s) used as evidence
    status: str = "answered"  # answered | not_found | refusal | partial | clarify
    metrics: Optional[dict] = None  # per-answer quality indicators


# Cache the chunk index in memory (rebuilt on first call or after uploads)
_index_cache: Optional[List[Chunk]] = None


def _get_index() -> List[Chunk]:
    global _index_cache
    if _index_cache is None:
        _index_cache = build_index()
    return _index_cache


def _reset_index() -> None:
    """Invalidate the in-memory index so it rebuilds on next request."""
    global _index_cache
    _index_cache = None


# Accepted upload extensions
_ACCEPTED_EXT = {".txt", ".docx", ".pdf"}
_MAX_UPLOAD_BYTES = 25 * 1024 * 1024  # 25 MB


def _call_llm_safe(system_prompt: str, user_message: str) -> str:
    """Call the LLM; return None if unavailable so we can fall back to mock."""
    try:
        from app.embeddings import call_llm
        return call_llm(system_prompt, user_message, temperature=0.2, max_tokens=800)
    except Exception:
        return ""


# ── Overview question detection ────────────────────────────────────
import re as _re

_OVERVIEW_RE = _re.compile(
    r"\b(what\s+is\s+(?:the\s+)?(?:document|file|spec|specification)\s+(?:about|of)"
    r"|tell\s+me\s+about\s+(?:the\s+)?(?:document|file|spec)"
    r"|summar(?:y|ize|ise)\s+(?:the\s+)?(?:document|file|spec)"
    r"|overview\s+of\s+(?:the\s+)?(?:document|file|spec)"
    r"|describe\s+(?:the\s+)?(?:document|file|spec)"
    r"|explain\s+(?:the\s+)?(?:document|file|spec)"
    r"|what\s+(?:does|is)\s+this\s+(?:document|file|spec)"
    r"|what\s+is\s+.*\s+about\b"
    # Natural-language variations: "what system/component/product is this document about"
    r"|what\s+\w+\s+(?:this|the)\s+(?:document|file|spec|specification)\s+(?:is\s+)?(?:about|describing|covering|for)\b"
    r"|(?:this|the)\s+document\s+is\s+about\b"
    r"|what\s+is\s+this\s+about\b"
    r"|what\s+(?:does|do)\s+this\s+(?:document|file|spec)\s+(?:describe|cover|contain|define)\b"
    r"|about\s+this\s+(?:document|file|spec|specification)\b"
    r"|(?:this|the)\s+(?:document|file|spec)\s+(?:is\s+)?(?:about|for)\s+what\b"
    r"|what\s+(?:component|system|product|part|assembly)\s+(?:is\s+)?(?:this|the)\s+(?:document|file|spec|specification)\s+(?:about|for|describing|covering)\b"
    r"|which\s+(?:component|system|product)\s+(?:does\s+)?(?:this|the)\s+(?:document|file|spec|specification)\s+(?:describe|cover)\b"
    # "what is the purpose/role/function of the [name] specification"
    r"|what\s+is\s+the\s+(?:purpose|role|function|goal|objective)\s+of\b"
    # "what is this specification about"
    r"|what\s+is\s+(?:this|the)\s+(?:document|file|spec|specification)\s+about\b"
    r"|what\s+is\s+(?:the\s+)?(?:document|file|spec|specification)\s+(?:purpose|role|function)\b)",
    _re.IGNORECASE,
)


def _is_overview_question(question: str) -> bool:
    """Return True if the question is a general overview question about a document."""
    return bool(_OVERVIEW_RE.search(question or ""))


# ── Validation-intent detection ────────────────────────────────────
_VALIDATION_RE = _re.compile(
    r"\b(validate|check)\s+(the\s+)?(document|this\s+(document|file|spec|specification)s?)\b"
    r"|\b(check|validate)\s+(the\s+)?(structure|template|writing\s*guide|guidelines?)\b"
    r"|\brun\s+(the\s+)?validation\b"
    r"|\bvalidate\s+(file\s+|the\s+file\s+|this\s+file\s+)?(structure|template|compliance)\b"
    r"|\bcheck\s+(if\s+|whether\s+)?the\s+(document|file|spec)\s+(is\s+)?(compliant|valid|correct)\b"
    r"|\bverify\s+(the\s+)?(document|file|spec|specification)\b"
    r"|\bvalidation\s+(of|for|on)\s+(the\s+)?(document|file|spec)\b"
    r"|\bdocument\s+(validation|check)\b"
    r"|\bvalidate\s+\S",  # "validate <filename>" (any single-token validate command)
    _re.IGNORECASE,
)


def _is_validation_question(question: str) -> bool:
    """Return True if the user is asking to validate a document."""
    q = (question or "").strip()
    # Quick check: single-word "validate" — yes, validate
    if q.lower() in ("validate", "validate!", "validate.", "validate?"):
        return True
    return bool(_VALIDATION_RE.search(q))


# ── Ambiguity detection ────────────────────────────────────────────
# Very short questions (≤2 content words) or single generic words are too
# vague for reliable retrieval. We ask the user to clarify instead of
# returning a misleading "not found" or a low-relevance answer.
_AMBIGUOUS_SINGLE_WORDS = {
    "hello", "hi", "hey", "help", "test", "ok", "yes", "no",
    "what", "why", "how", "who", "where", "when", "thanks", "thank",
}


def _is_ambiguous_question(question: str) -> bool:
    """Return True if the question is too vague for reliable retrieval."""
    q = (question or "").strip().lower().rstrip("?!.")
    if not q:
        return True
    # Single word that's a greeting or interrogative
    if q in _AMBIGUOUS_SINGLE_WORDS:
        return True
    # Overview questions are NEVER ambiguous — they have a clear intent
    # even with few content tokens ("what is this document about")
    if _is_overview_question(q):
        return False
    # Count ORIGINAL content tokens (before French expansion) to detect vagueness
    from app.qa.retrieval import _tokenize, _STOP
    raw_tokens = _tokenize(q) - _STOP
    # ≤1 distinctive content token is too vague (e.g. "composant", "system")
    if len(raw_tokens) <= 1:
        return True
    return False


def _pick_validation_file(question: str, chunks: List[Chunk]) -> Optional[str]:
    """
    Pick the most appropriate file to validate from the user's question.

    1. If the question names a specific file, use that.
    2. Otherwise, prefer the last uploaded (non-built-in) file.
    3. Fall back to the first DOCX file.
    4. Fall back to any accessible file.
    """
    from app.qa.retrieval import discover_accessible_files

    # Try to detect a file name in the question
    q_lower = question.lower()
    file_names = sorted(set(c.file_name for c in chunks), key=len, reverse=True)
    for fname in file_names:
        stem = Path(fname).stem.lower()
        stem_simple = _re.sub(r"[_\-]+", " ", stem).strip()
        stem_tokens = [t for t in stem_simple.split() if len(t) > 2]
        if stem_tokens:
            matched = sum(1 for t in stem_tokens if t in q_lower)
            if matched >= min(2, len(stem_tokens)):
                return fname

    # Prefer uploaded files (non-built-in), then DOCX, then any
    accessible = list(discover_accessible_files())
    builtin = {"spec_extracted.txt", "template_extracted.txt"}
    
    for p in accessible:
        if p.name not in builtin:
            return p.name
    for p in accessible:
        if p.suffix.lower() == ".docx":
            return p.name
    if accessible:
        return accessible[0].name
    return None


def _detect_referenced_file(question: str, chunks: List[Chunk]) -> Optional[str]:
    """
    Detect if the question references a specific uploaded file by name.

    Also handles "this document" / "the document" references by returning
    the most recently uploaded file (if any).

    Returns the matched file name, or None.
    """
    q_lower = question.lower()

    # Check for "this document" / "the document" / "ce document" references
    _THIS_DOC_RE = _re.compile(
        r"\b(this|the|ce|cette)\s+(document|file|spec|specification|fichier)\b",
        _re.IGNORECASE,
    )
    refers_to_current = bool(_THIS_DOC_RE.search(q_lower))

    # Collect all unique file names from the index
    file_names = sorted(set(c.file_name for c in chunks), key=len, reverse=True)
    for fname in file_names:
        # Build a searchable version of the file name (without extension, simplified)
        stem = Path(fname).stem.lower()
        # Remove common separators for flexible matching
        stem_simple = _re.sub(r"[_\-]+", " ", stem).strip()
        stem_tokens = [t for t in stem_simple.split() if len(t) > 2]
        # Check if enough distinctive tokens from the file name appear in the question
        if stem_tokens:
            matched = sum(1 for t in stem_tokens if t in q_lower)
            # Require at least 2 distinctive tokens or 1 if the stem is short
            threshold = 2 if len(stem_tokens) > 2 else 1
            if matched >= threshold:
                return fname

    # If no specific file named but user says "this document", return the
    # most recently uploaded (non-built-in) file
    if refers_to_current:
        from app.qa.retrieval import discover_accessible_files
        builtin = {"spec_extracted.txt", "template_extracted.txt"}
        accessible = list(discover_accessible_files())
        for p in accessible:
            if p.name not in builtin:
                return p.name    # first uploaded file = most relevant "this"
        # Fallback: any DOCX
        for p in accessible:
            if p.suffix.lower() == ".docx":
                return p.name
    return None


def _retrieve_file_overview(chunks: List[Chunk], file_name: str, top_k: int = 7) -> List[Chunk]:
    """
    Retrieve the first chunks of a file for overview questions.

    These typically contain the PURPOSE, SCOPE, and table of contents sections.
    """
    file_chunks = [c for c in chunks if c.file_name == file_name]
    if not file_chunks:
        return []
    # Return the first N chunks (document beginning = overview content)
    return file_chunks[:top_k]


# ── Acronym/definition retrieval ──────────────────────────────────
_ACRONYM_Q_RE = _re.compile(
    r"\b(?:what\s+(?:does|is|do)\s+|what\s+is\s+the\s+meaning\s+of\s+|"
    r"what\s+stands?\s+|define\s+|definition\s+of\s+)"
    r"([A-Z][A-Z0-9_]{1,10})\b",
    _re.IGNORECASE,
)


def _try_acronym_retrieval(question: str, chunks: List[Chunk]) -> Optional[RetrievalResult]:
    """
    Detect acronym/definition questions and search for the pattern
    "ACRONYM (Full Name)" or "ACRONYM — Full Name" in the document.

    Returns a RetrievalResult with matching chunks, or None if the question
    is not an acronym/definition question.
    """
    m = _ACRONYM_Q_RE.search(question)
    if not m:
        return None

    acronym = m.group(1).upper()
    # Search for patterns like "ASU (Alarm Siren Unit)" or "ASU — Alarm Siren Unit"
    # or "OF THE ALARM SIREN UNIT (ASU)" in chunk text
    pattern = _re.compile(
        r"(?:\b" + _re.escape(acronym) + r"\s*[\(—\-:]\s*([A-Z][^\)]{3,60})"
        r"|\b([A-Z][a-z]+(?:\s+[A-Z][a-z]+){1,5})\s*\(\s*" + _re.escape(acronym) + r"\s*\))",
        _re.IGNORECASE,
    )

    matched_chunks: List[Chunk] = []
    for ch in chunks:
        if pattern.search(ch.text):
            matched_chunks.append(ch)

    if matched_chunks:
        # Also include the first chunk of the file (title area) for context
        return RetrievalResult(
            chunks=matched_chunks[:5],
            scores=[2.0] * len(matched_chunks[:5]),
            used_fallback=False,
        )
    return None


@router.post("/ask", response_model=AskResponse)
def ask(req: AskRequest) -> AskResponse:
    question = req.question.strip()

    # 1. Standards / BeStandard guardrail — fixed refusal, no retrieval
    if is_standards_question(question):
        rec = QaRecord(question=question, answer=STANDARDS_REFUSAL_MESSAGE,
                       confidence="", source_count=0, was_not_found=False,
                       was_refusal=True, had_sources=False)
        metrics_store.record(rec)
        return AskResponse(answer=STANDARDS_REFUSAL_MESSAGE, sources=[],
                           status="refusal")

    # 1a. Ambiguity check — very short or vague questions need clarification
    # (enterprise requirement: "Ambiguous request → ask clarification")
    if _is_ambiguous_question(question):
        clarify_msg = (
            "Your question is a bit too brief for me to find the right information. "
            "Could you add more detail? For example, mention the component name "
            "(e.g. 'ASU', 'door handle'), the topic (e.g. 'noise target', 'test "
            "temperature'), or the requirement you're interested in."
        )
        rec = QaRecord(question=question, answer=clarify_msg, confidence="",
                       source_count=0, was_not_found=False, was_refusal=False,
                       had_sources=False)
        metrics_store.record(rec)
        return AskResponse(answer=clarify_msg, sources=[], status="clarify")

    # 1b. Validation-intent detection — run the spec validator directly
    if _is_validation_question(question):
        chunks = _get_index()
        val_file = _pick_validation_file(question, chunks)
        if val_file is None:
            return AskResponse(
                answer="I'd like to validate a document for you, but there are no "
                       "accessible specification files to validate. Please upload a "
                       ".docx, .txt, or .pdf file first using the '+ Upload spec file' button above.",
                sources=[],
            )
        # Run the validator on the chosen file
        from app.qa.retrieval import discover_accessible_files, extract_text_from_file as _extract
        file_path = None
        for p in discover_accessible_files():
            if p.name == val_file:
                file_path = p
                break
        if file_path is None:
            return AskResponse(
                answer=f"Could not find file '{val_file}' for validation.",
                sources=[],
            )
        text = _extract(file_path)
        if not text or not text.strip():
            return AskResponse(
                answer=f"Could not extract text from '{val_file}' for validation.",
                sources=[],
            )
        report = validate_with_evidence(val_file, text)
        val_dict = report
        return AskResponse(
            answer=f"Here is the evidence-based validation for "
                   f"**{val_file}** — Verdict: **{report.get('verdict', 'UNKNOWN')}** "
                   f"({report.get('overallScore', 0):.0%}):\n\n"
                   f"{report.get('summary', '')}\n\n"
                   f"Checked against {report.get('rulesUsed', {}).get('mandatory_sections_count', 0)} "
                   f"template sections and {report.get('rulesUsed', {}).get('writing_guide_rules_count', 0)} "
                   f"writing-guide rules (100% extracted from source documents).",
            confidence="",
            validationReport=val_dict,
        )

    # 1c. Section-guidance detection — user asks about what to put in a CTS section
    from app.qa.section_guidance import is_section_guidance_question, get_section_guidance
    if is_section_guidance_question(question):
        guidance = get_section_guidance(question)
        if guidance:
            # Try the LLM to synthesize a helpful answer from the guidance
            guidance_prompt = (
                "You are a CTS specification writing assistant for Stellantis. "
                "A user is writing a Component Technical Specification and needs guidance "
                "on a specific section. Below is the EXACT guidance extracted from the "
                "Stellantis CTS template and writing guide. Synthesize a clear, structured, "
                "helpful answer that tells the user exactly what to put in this section.\n\n"
                "IMPORTANT RULES:\n"
                "- Only use the guidance provided below. Do NOT invent anything.\n"
                "- Structure your answer with: 1) Section purpose, 2) What to include, "
                "3) Key rules to follow, 4) Common mistakes to avoid.\n"
                "- Be concise and actionable. The user is actively writing their spec.\n"
                "- If template instructions mention placeholders (<<...>>), explain what "
                "real content should replace them.\n"
                "- Answer in the same language as the guidance (English or French).\n\n"
                "GUIDANCE:\n" + guidance.get("answer", "")
            )
            llm_answer = _call_llm_safe(
                "You are a Stellantis engineering specification expert helping an engineer write a CTS.",
                guidance_prompt,
            )

            if llm_answer and llm_answer.strip():
                final_answer = llm_answer.strip()
            else:
                final_answer = guidance.get("answer", "")

            return AskResponse(
                answer=final_answer,
                sources=[],
                confidence="HIGH",
                status="answered",
                evidence=[guidance.get("detected_section", "")],
            )

    # 2. Real retrieval from accessible spec files (default path)
    chunks = _get_index()
    if not chunks:
        return AskResponse(
            answer="No accessible specification files are currently indexed.",
            sources=[],
        )

    # 2a. Overview question handling: if the user asks a general question
    # about a document/file, retrieve the first chunks of that file
    # (which contain PURPOSE, SCOPE, table of contents) instead of relying
    # on keyword matching which fails for general questions.
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

    # 2b. Standard keyword retrieval if not an overview question or no file matched
    if result is None:
        # 2b-1. Acronym/definition detection: "What does X stand for?" or "What is X?"
        # Search for the pattern "X (Full Name)" or "X — Full Name" in the document
        acronym_result = _try_acronym_retrieval(question, chunks)
        if acronym_result:
            result = acronym_result
        else:
            result = retrieve(question, chunks=chunks, use_semantic=False)

    # 3. No support found → fixed message, no confidence
    if not result.chunks:
        rec = QaRecord(question=question, answer=NOT_FOUND_MESSAGE, confidence="",
                       source_count=0, was_not_found=True, was_refusal=False,
                       had_sources=False)
        metrics_store.record(rec)
        return AskResponse(answer=NOT_FOUND_MESSAGE, sources=[], confidence="",
                           status="not_found")

    # 4. Try the LLM for a synthesized grounded answer
    user_message = build_user_prompt(question, result.chunks)
    llm_answer = _call_llm_safe(SYSTEM_PROMPT, user_message)

    if llm_answer and llm_answer.strip():
        # Extract the confidence line the LLM was instructed to emit
        confidence, answer = extract_confidence(llm_answer.strip())

        # If the LLM determined there is no support, respect its verdict:
        # return the not-found message with no confidence and no sources.
        if NOT_FOUND_MESSAGE.lower() in answer.lower():
            return AskResponse(answer=NOT_FOUND_MESSAGE, sources=[], confidence="")

        # Fallback: compute confidence from retrieval scores if LLM omitted it
        if not confidence:
            confidence = compute_confidence_from_scores(result.scores, len(result.chunks))
    else:
        # LLM unavailable → build a transparent grounded answer from passages.
        # This is NOT a hardcoded/generative response: it quotes the actual
        # retrieved excerpts with their source citations.
        answer = _build_grounded_excerpt(question, result.chunks)
        confidence = compute_confidence_from_scores(result.scores, len(result.chunks))

    sources = [
        SourceItem(fileName=c.file_name, excerpt=c.text) for c in result.chunks
    ]
    # Enterprise structured output: evidence excerpts + status
    evidence = [c.text.strip()[:500] for c in result.chunks[:3]]
    # Detect partial information (low confidence or weak retrieval scores)
    status = "answered"
    if confidence == "LOW":
        status = "partial"
        answer = PARTIAL_INFO_MESSAGE + "\n\n" + answer
    # Per-answer quality indicators
    answer_metrics = {
        "grounded": len(sources) > 0,
        "sourceCount": len(sources),
        "confidence": confidence or "N/A",
        "retrievalScores": [round(s, 4) for s in result.scores[:5]],
    }
    rec = QaRecord(question=question, answer=answer, confidence=confidence,
                   source_count=len(sources), was_not_found=False,
                   was_refusal=False, had_sources=len(sources) > 0)
    metrics_store.record(rec)
    return AskResponse(answer=answer, sources=sources, confidence=confidence,
                       evidence=evidence, status=status, metrics=answer_metrics)


def _build_grounded_excerpt(question: str, chunks: List[Chunk]) -> str:
    """Build a transparent answer from retrieved passages (no LLM, no invention)."""
    lines = [f"Based on the accessible specification files, here is what I found "
             f"for \"{question}\":"]
    for i, ch in enumerate(chunks, 1):
        section = f" ({ch.section})" if ch.section else ""
        excerpt = ch.text.strip()
        if len(excerpt) > 500:
            excerpt = excerpt[:497] + "..."
        lines.append(f"\n[{i}] From {ch.file_name}{section}:\n{excerpt}")
    lines.append(
        "\nNote: the LLM is currently unavailable, so the retrieved passages "
        "are shown directly. No content was generated or inferred."
    )
    return "\n".join(lines)


@router.get("/files")
def list_accessible_files() -> dict:
    """Return the list of accessible spec file names (for UI display)."""
    from app.qa.retrieval import discover_accessible_files
    files = [p.name for p in discover_accessible_files()]
    return {"files": files}


@router.post("/upload")
async def upload_file(file: UploadFile = File(...)) -> dict:
    """
    Upload a technical specification file (TXT, DOCX, or PDF).

    The file is saved to data/uploads/, extracted to text, chunked, and
    added to the retrieval index. Returns the file name + chunk count.
    """
    if not file.filename:
        raise HTTPException(status_code=400, detail="No file name provided")

    suffix = Path(file.filename).suffix.lower()
    if suffix not in _ACCEPTED_EXT:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported file type '{suffix}'. Accepted: {sorted(_ACCEPTED_EXT)}",
        )

    content = await file.read()
    if not content:
        raise HTTPException(status_code=400, detail="Empty file")
    if len(content) > _MAX_UPLOAD_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"File too large ({len(content)} bytes). Max {_MAX_UPLOAD_BYTES} bytes.",
        )

    saved_path = save_uploaded_file(file.filename, content)

    # Verify we can extract text from it
    text = extract_text_from_file(saved_path)
    if not text.strip():
        # Remove the unusable file
        delete_uploaded_file(saved_path.name)
        raise HTTPException(
            status_code=422,
            detail="Could not extract any text from the uploaded file.",
        )

    # Rebuild the index so the new file is included
    _reset_index()
    new_index = _get_index()
    chunk_count = sum(1 for c in new_index if c.file_name == saved_path.name)

    return {
        "fileName": saved_path.name,
        "chunks": chunk_count,
        "message": f"Uploaded and indexed '{saved_path.name}' ({chunk_count} passages).",
    }


@router.delete("/files/{file_name}")
def remove_uploaded_file(file_name: str) -> dict:
    """Delete an uploaded specification file and rebuild the index."""
    deleted = delete_uploaded_file(file_name)
    if not deleted:
        raise HTTPException(status_code=404, detail=f"File '{file_name}' not found in uploads.")
    _reset_index()
    return {"fileName": file_name, "message": f"Removed '{file_name}'."}


# ── Specification validation ───────────────────────────────────────
class ValidateRequest(BaseModel):
    fileName: str = Field(..., min_length=1)


@router.post("/validate")
def validate_spec(req: ValidateRequest) -> dict:
    """
    Validate an uploaded specification file against the Stellantis CTS
    template structure and writing guide rules.

    Returns a structured report with scores, findings, and verdict.
    """
    from app.qa.retrieval import discover_accessible_files

    # Find the file among accessible files
    file_path = None
    for p in discover_accessible_files():
        if p.name == req.fileName:
            file_path = p
            break

    if file_path is None:
        raise HTTPException(
            status_code=404,
            detail=f"File '{req.fileName}' not found among accessible files.",
        )

    text = extract_text_from_file(file_path)
    if not text or not text.strip():
        raise HTTPException(
            status_code=422,
            detail="Could not extract text from the file for validation.",
        )

    report = validate_with_evidence(req.fileName, text)
    return report


# ── Quality metrics endpoint ───────────────────────────────────────
@router.get("/metrics")
def get_metrics() -> dict:
    """
    Return the 4 mandatory enterprise quality metrics computed from
    actual Q&A interactions:

    - groundingRate:   % of answers supported by ≥1 source
    - faithfulnessScore: % of answers with assigned confidence
    - relevanceScore:  % of questions that got a substantive answer
    - notFoundAccuracy: % of "not found" responses that correctly had 0 sources
    """
    return metrics_store.compute()


@router.delete("/metrics")
def reset_metrics() -> dict:
    """Clear the metrics store (useful for fresh evaluation runs)."""
    metrics_store.clear()
    return {"message": "Metrics store cleared."}
