"""
Strict prompt template for the Q&A assistant.

The LLM is ONLY allowed to answer from the retrieved passages. It must never
use general model knowledge, never invent details, and must clearly say when
support is not found. Citations are mandatory.

Every answer includes a mandatory reliability check and a confidence score
based ONLY on evidence found in the retrieved content.
"""
from __future__ import annotations

import re
from typing import List, Tuple

from app.qa.retrieval import Chunk


SYSTEM_PROMPT = (
    "You are a precise engineering specification Q&A assistant.\n"
    "You answer ONLY from the provided retrieved passages.\n"
    "\n"
    "MANDATORY RELIABILITY CHECK (run BEFORE answering):\n"
    "1. SUPPORT CHECK: Is the answer explicitly supported by retrieved content?\n"
    "   - If NO → reply exactly: 'I could not find explicit support for that in "
    "the accessible specification files.'\n"
    "2. SOURCE ALIGNMENT: Does the answer match exactly the meaning of the retrieved content?\n"
    "   - If partially aligned → clearly state the uncertainty.\n"
    "   - If misaligned → do not answer.\n"
    "3. COMPLETENESS CHECK: Does the answer fully address the question?\n"
    "   - If incomplete → include: 'The available files provide only partial "
    "information about this.'\n"
    "4. NO HALLUCINATION RULE: Never add assumptions, estimations, or external "
    "knowledge. If unsure → prefer 'not found' over guessing.\n"
    "\n"
    "STRICT RULES:\n"
    "1. Never use your general model knowledge.\n"
    "2. Never invent details or speculate.\n"
    "3. If the answer is not explicitly supported by the passages, reply exactly:\n"
    "   'I could not find explicit support for that in the accessible specification files.'\n"
    "4. For every claim, cite the source file name in square brackets, e.g. [spec_extracted.txt].\n"
    "5. Do not claim compliance with any standard unless an accessible source states it.\n"
    "6. If the question is about standards or BeStandard, reply exactly:\n"
    "   'I can answer only from the accessible specification files currently connected to me. "
    "   I do not yet have access to BeStandard or other unavailable standards sources.'\n"
    "7. Keep answers concise and factual.\n"
    "8. If sources conflict, explain both views clearly with citations.\n"
    "9. If the request is incomplete or ambiguous, ask for clarification.\n"
    "\n"
    "CONFIDENCE SCORE (MANDATORY — append as the last line):\n"
    "Assign a confidence score based ONLY on evidence:\n"
    "- HIGH: a direct explicit statement was found in a file.\n"
    "- MEDIUM: inferred but clearly supported by multiple passages.\n"
    "- LOW: weak or partial support only.\n"
    "Format the last line EXACTLY as: Confidence: HIGH  (or MEDIUM / LOW)\n"
    "If you returned the 'not found' message, do NOT add a confidence line."
)


def build_user_prompt(question: str, chunks: List[Chunk]) -> str:
    """Build the user message containing the question + retrieved passages."""
    if not chunks:
        passages = "(no passages retrieved)"
    else:
        lines = []
        for i, ch in enumerate(chunks, 1):
            lines.append(
                f"[PASSAGE {i}] source: {ch.file_name}\n{ch.text}"
            )
        passages = "\n\n".join(lines)

    return (
        f"QUESTION:\n{question}\n\n"
        f"RETRIEVED PASSAGES (answer ONLY from these):\n{passages}\n\n"
        f"Answer the question using only the passages above.\n"
        f"Run the mandatory reliability check before answering.\n"
        f"If none of them support an answer, say you could not find explicit support.\n"
        f"Cite source file names in square brackets.\n"
        f"Append a confidence score as the last line: Confidence: HIGH / MEDIUM / LOW"
    )


# Guardrail: detect questions about standards / BeStandard as external sources.
# This is intentionally NARROW: we only refuse when the user is asking about
# BeStandard or external standards as a knowledge source — NOT when they ask
# about test standards/norms mentioned within the document itself (e.g.
# "what is the corrosion test standard?" should be answered from the doc).
_STANDARDS_RE = re.compile(
    r"\b(bestandard|be\s+standard)\b"
    r"|\b(?:lookup|look\s+up|consult|search|find|access)\b.*\bstandards?\b"
    r"|\bwhat\s+is\s+(?:the\s+)?iso\s*\d+\b"
    r"|\btell\s+me\s+about\s+(?:the\s+)?(?:iso|ista|sta\d+)\b",
    re.IGNORECASE,
)


def is_standards_question(question: str) -> bool:
    """Return True if the question is about standards / BeStandard as an external source."""
    return bool(_STANDARDS_RE.search(question or ""))


# Fixed refusal messages (used directly by the route to avoid LLM variance)
NOT_FOUND_MESSAGE = (
    "I could not find explicit support for that in the accessible specification files."
)

STANDARDS_REFUSAL_MESSAGE = (
    "I can answer only from the accessible specification files currently connected to me. "
    "I do not yet have access to BeStandard or other unavailable standards sources."
)

PARTIAL_INFO_MESSAGE = (
    "The available files provide only partial information about this."
)


# ── Confidence extraction ──────────────────────────────────────────
_CONFIDENCE_RE = re.compile(
    r"Confidence:\s*(HIGH|MEDIUM|LOW)\b",
    re.IGNORECASE,
)

# Valid confidence levels in descending order
_CONFIDENCE_LEVELS = ("HIGH", "MEDIUM", "LOW")


def extract_confidence(answer: str) -> Tuple[str, str]:
    """
    Extract the confidence score from an LLM answer.

    Returns (confidence_level, cleaned_answer) where confidence_level is
    one of 'HIGH', 'MEDIUM', 'LOW', or '' if not found.
    The cleaned_answer has the confidence line removed.
    """
    if not answer:
        return "", answer
    m = _CONFIDENCE_RE.search(answer)
    if not m:
        return "", answer
    level = m.group(1).upper()
    # Remove the confidence line from the answer text
    cleaned = _CONFIDENCE_RE.sub("", answer).rstrip().rstrip("-—:").rstrip()
    return level, cleaned


def compute_confidence_from_scores(scores: List[float], n_chunks: int) -> str:
    """
    Compute a heuristic confidence level from retrieval scores.

    Used as a fallback when the LLM did not emit a confidence line, or when
    the LLM is unavailable. Based ONLY on retrieval evidence strength.

    - HIGH: strong top score (>= 0.5) and multiple supporting passages
    - MEDIUM: moderate top score (>= 0.25) or single strong passage
    - LOW: weak support (below 0.25)
    """
    if not scores or n_chunks == 0:
        return "LOW"
    top = max(scores)
    supporting = sum(1 for s in scores if s >= 0.2)
    if top >= 0.5 and supporting >= 2:
        return "HIGH"
    if top >= 0.25 or supporting >= 1:
        return "MEDIUM"
    return "LOW"
