"""
Mock data so the Q&A interface can run before the real retrieval is connected.

Provides:
- a small in-memory set of accessible spec passages
- a mock retrieve() that returns relevant passages by keyword
- a mock answer generator that returns a grounded answer + sources
"""
from __future__ import annotations

from typing import List

from app.qa.retrieval import Chunk, RetrievalResult


MOCK_PASSAGES: List[Chunk] = [
    Chunk(
        file_name="spec_extracted.txt",
        section="PURPOSE",
        chunk_id=0,
        text=(
            "PURPOSE: The goal of the present Specification is to list and refine the "
            "requirements that have an impact on the alarm ASU unit functionality, "
            "locate the part in its environment via the contextual diagram, specify the "
            "external interfaces, and to specify the performance, operational, "
            "constraint, integration, and validation requirements for the alarm ASU unit."
        ),
    ),
    Chunk(
        file_name="spec_extracted.txt",
        section="SYSTEM ROLES",
        chunk_id=1,
        text=(
            "SYSTEM ROLES: The role of this ASU, which is part of the system carrying out "
            "the function 'Alerting in case of intrusion attempt', is to produce the sound "
            "generator that alerts you to the intrusion and attempted theft of a vehicle. "
            "The ASU is located under the hood, in the plenum, or at the rear left arch."
        ),
    ),
    Chunk(
        file_name="spec_extracted.txt",
        section="HeartBeat",
        chunk_id=2,
        text=(
            "To verify that the cable between the ASU and the master is not cut, the master "
            "(ZCU_CL) emits a signal called Heartbeat 3 times a second. If the ASU does not "
            "receive this signal for 1 second, it starts ringing."
        ),
    ),
    Chunk(
        file_name="spec_extracted.txt",
        section="RAMS REQUIREMENTS",
        chunk_id=3,
        text=(
            "The standard ISO 26262 defines the state of the art for the development of "
            "reliable electronic products in the automotive field. Its requirements should "
            "be respected according to the effectiveness and application of the standard "
            "schedule."
        ),
    ),
    Chunk(
        file_name="spec_extracted.txt",
        section="Random noise",
        chunk_id=4,
        text=(
            "Random noise is the noise heard by the customers, random in nature, generated "
            "by the road profile forces. The rating of the defects is made by subjective "
            "listening when new and after ageing, on a 4 levels scale: A, B, C and D."
        ),
    ),
]


def mock_retrieve(question: str, top_k: int = 3) -> RetrievalResult:
    """Keyword-based mock retrieval over the mock passages."""
    import re
    q_tokens = set(re.findall(r"[a-z0-9]+", question.lower()))
    stop = {"the", "a", "an", "is", "are", "what", "who", "where", "when",
            "why", "how", "of", "to", "in", "on", "for", "and", "or", "with",
            "does", "do", "can", "could", "please", "tell", "me", "about"}
    q_tokens -= stop

    scored = []
    for ch in MOCK_PASSAGES:
        c_tokens = set(re.findall(r"[a-z0-9]+", ch.text.lower()))
        overlap = q_tokens & c_tokens
        if overlap:
            scored.append((len(overlap) / max(len(q_tokens), 1), ch))
    scored.sort(key=lambda x: x[0], reverse=True)
    top = scored[:top_k]
    return RetrievalResult(
        chunks=[c for _, c in top],
        scores=[s for s, _ in top],
        used_fallback=True,
    )


def mock_answer(question: str) -> dict:
    """Return a mock {answer, sources} response for demo purposes."""
    from app.qa.prompt import is_standards_question, NOT_FOUND_MESSAGE, STANDARDS_REFUSAL_MESSAGE

    if is_standards_question(question):
        return {"answer": STANDARDS_REFUSAL_MESSAGE, "sources": []}

    result = mock_retrieve(question)
    if not result.chunks:
        return {"answer": NOT_FOUND_MESSAGE, "sources": []}

    # Build a simple grounded answer from the top passage
    top = result.chunks[0]
    answer = f"According to [{top.file_name}] ({top.section}): {top.text}"
    sources = [{"fileName": c.file_name, "excerpt": c.text} for c in result.chunks]
    return {"answer": answer, "sources": sources}
