"""Conservative matching policy for PowerPoint evidence candidates.

The extractor in :mod:`pptx_evidence` provides slide/shape citations. This
module excludes supplier comments from retrieval queries so a supplier claim
cannot retrieve itself as corroboration.
"""
from __future__ import annotations

import re
import unicodedata
from pathlib import Path
from typing import Any, Dict, List, Sequence, Tuple

from app.qa.conformity_coverage import extract_id_tokens, normalize_id
from app.qa.pptx_evidence import SlideEvidence, extract_pptx_evidence

_WORD_RE = re.compile(r"[a-z0-9]+", re.IGNORECASE)
_STOP_WORDS = {
    "the", "and", "for", "with", "shall", "must", "will", "can", "les", "des",
    "une", "dans", "pour", "avec", "aux", "est", "sur", "par", "que", "qui",
    "this", "that", "from", "supplier", "requirement", "specification",
}


def _tokens(text: str) -> set[str]:
    normalized = unicodedata.normalize("NFKD", text or "")
    normalized = "".join(char for char in normalized if not unicodedata.combining(char))
    return {token.lower() for token in _WORD_RE.findall(normalized)
            if len(token) > 2 and token.lower() not in _STOP_WORDS}


def match_requirement_evidence(
    requirement: Dict[str, Any],
    evidence: Sequence[SlideEvidence],
    limit: int = 5,
) -> List[Dict[str, Any]]:
    """Rank exact-ID and strong lexical candidates, without using comments."""
    source_ids = " ".join(str(requirement.get(key, "") or "") for key in ("reqId", "reference"))
    requirement_ids = set(extract_id_tokens(source_ids))
    requirement_tokens = _tokens(str(requirement.get("description", "") or ""))
    ranked: List[Tuple[int, float, int, SlideEvidence]] = []
    for item in evidence:
        exact = bool(requirement_ids.intersection(normalize_id(token) for token in item.evidence_ids))
        passage_ids = set(extract_id_tokens(re.sub(r"\b(REQ)\s*[-‐‑‒–—]\s*(\d{4,10})\b", r"\1-\2", item.text, flags=re.IGNORECASE)))
        if not exact:
            exact = bool(requirement_ids.intersection(passage_ids))
        passage_tokens = _tokens(item.text)
        shared = requirement_tokens & passage_tokens
        overlap = len(shared) / len(requirement_tokens | passage_tokens) if requirement_tokens and passage_tokens else 0.0
        if exact or (len(shared) >= 2 and overlap >= 0.12):
            ranked.append((int(exact), overlap, -item.slide_number, item))
    ranked.sort(key=lambda candidate: (candidate[0], candidate[1], candidate[2]), reverse=True)
    return [
        {
            **evidence_item.to_dict(),
            "matchType": "requirement_id" if exact else "text_similarity",
            "matchScore": 1.0 if exact else round(overlap, 4),
            "retrievalOnly": True,
        }
        for exact, overlap, _, evidence_item in ranked[:max(0, limit)]
    ]


def analyze_matrix_against_pptx(matrix_analysis: Any, pptx_path: str | Path) -> Dict[str, Any]:
    """Combine matrix requirements with conservative, slide-cited candidates."""
    evidence = extract_pptx_evidence(pptx_path)
    requirements = []
    for item in matrix_analysis.items:
        if not item.is_requirement:
            continue
        matrix_item = {
            "rowIndex": item.row_index,
            "matrixRowNumber": item.row_index + 1,
            "reqId": item.req_id,
            "reference": item.reference,
            "description": item.description,
            "matrixStatus": item.conformity_category,
            "matrixStatusRaw": item.conformity_raw,
            "supplierComment": item.comment,
        }
        candidates = match_requirement_evidence(matrix_item, evidence)
        requirements.append({
            **matrix_item,
            "evidence": candidates,
            "reviewStatus": "candidate_evidence_found" if candidates else "no_text_candidate_found",
        })

    return {
        "matrixFile": matrix_analysis.file_name,
        "presentationFile": Path(pptx_path).name,
        "summary": {
            "requirements": len(requirements),
            "requirementsWithEvidenceCandidates": sum(bool(item["evidence"]) for item in requirements),
            "requirementsWithoutEvidenceCandidates": sum(not item["evidence"] for item in requirements),
            "slidesWithExtractedText": len({item.slide_number for item in evidence}),
            "evidenceBlocks": len(evidence),
            "complianceVerdictsMade": 0,
        },
        "decisionSummary": {
            "supplierDeclared": {
                status: sum(item["matrixStatus"] == status for item in requirements)
                for status in ("OK", "NOK", "NA", "EMPTY")
            },
            "interpretation": "Supplier declarations are reported as written; this tool does not independently determine conformity.",
        },
        "limitations": [
            "Results are candidate evidence links, not compliance verdicts.",
            "Embedded images, notes, and chart data are not assessed by this version.",
            "No extracted text candidate found does not mean the supplier failed the requirement.",
            "Technical values, units, test conditions, and applicability require reviewer verification.",
        ],
        "requirements": requirements,
        "unmatchedSlideEvidence": [
            item.to_dict() for item in evidence if not item.evidence_ids and not _tokens(item.text)
        ],
    }
