"""Page-cited evidence extraction and conservative matrix linking for PDFs.

This module produces reviewable evidence candidates only. It does not decide
supplier conformity, and an absent text match is not a failure verdict.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Sequence, Tuple

from app.qa.conformity_coverage import extract_id_tokens, normalize_id

from PyPDF2 import PdfReader


@dataclass
class PageEvidence:
    file_name: str
    page_number: int
    text: str
    block_number: int = 1
    evidence_ids: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def extract_pdf_evidence(filepath: str | Path) -> List[PageEvidence]:
    """Extract non-empty text per PDF page while preserving page numbers.

    Scanned images/OCR, visual diagrams, and embedded media are not assessed.
    """
    path = Path(filepath)
    if path.suffix.lower() != ".pdf":
        raise ValueError("Only .pdf documents are supported for TDR evidence.")
    if not path.is_file():
        raise FileNotFoundError(str(path))

    reader = PdfReader(str(path))
    pages: List[PageEvidence] = []
    for number, page in enumerate(reader.pages, start=1):
        lines = [line.strip() for line in (page.extract_text() or "").splitlines() if line.strip()]
        blocks: List[str] = []
        current: List[str] = []
        current_size = 0
        for line in lines:
            # Keep nearby table/text lines together while bounding the lexical
            # search unit; the source page remains attached to every block.
            if current and current_size + len(line) > 1200:
                blocks.append("\n".join(current))
                current = []
                current_size = 0
            current.append(line)
            current_size += len(line) + 1
        if current:
            blocks.append("\n".join(current))
        # A requirement ID applies only to its local passage, not to every
        # block on the page. Propagating page-wide IDs creates false links
        # between unrelated matrix rows when a PDF page contains a table of
        # many requirements. Use each extracted block's own text as citation.
        for block_number, text in enumerate(blocks, start=1):
            # PDF layouts frequently put whitespace around the ID hyphen.
            # Repair only the known REQ-numeric shape before tokenization.
            normalized_text = re.sub(r"\bREQ\s*-\s*(\d{4,10})\b", r"REQ-\1", text, flags=re.IGNORECASE)
            block_ids = extract_id_tokens(normalized_text)
            pages.append(PageEvidence(
                file_name=path.name,
                page_number=number,
                text=text,
                block_number=block_number,
                evidence_ids=block_ids,
            ))
    return pages


_WORD_RE = re.compile(r"[a-z0-9]+", re.IGNORECASE)
_STOP_WORDS = {
    "the", "and", "for", "with", "shall", "must", "will", "can", "les", "des",
    "une", "dans", "pour", "avec", "aux", "est", "sur", "par", "que", "qui",
    "this", "that", "from", "supplier", "requirement", "specification", "shall",
}


def _tokens(text: str) -> set[str]:
    normalized = unicodedata.normalize("NFKD", text or "")
    normalized = "".join(char for char in normalized if not unicodedata.combining(char))
    return {token.lower() for token in _WORD_RE.findall(normalized)
            if len(token) > 2 and token.lower() not in _STOP_WORDS}


def match_requirement_evidence(
    requirement: Dict[str, Any], pages: Sequence[PageEvidence], limit: int = 5,
) -> List[Dict[str, Any]]:
    """Rank ID-exact or lexical candidates; scores are not compliance scores."""
    id_source = " ".join(str(requirement.get(key, "") or "") for key in ("reqId", "reference"))
    requirement_ids = set(extract_id_tokens(id_source))
    req_text = str(requirement.get("description", "") or "")
    req_tokens = _tokens(req_text)

    ranked: List[Tuple[int, float, int, PageEvidence]] = []
    for page in pages:
        # IDs can be broken by OCR/PDF extraction spacing ("REQ -0308287").
        # Compare canonicalized alphanumeric forms, not raw punctuation.
        canonical_requirement_ids = {
            re.sub(r"[^A-Z0-9]", "", value.upper()) for value in requirement_ids
        }
        page_ids = {
            re.sub(r"[^A-Z0-9]", "", normalize_id(value)) for value in page.evidence_ids
        }
        exact = bool(canonical_requirement_ids.intersection(page_ids))
        page_tokens = _tokens(page.text)
        overlap = len(req_tokens & page_tokens) / len(req_tokens | page_tokens) if req_tokens and page_tokens else 0.0
        # ID-linked evidence is still merely a retrieved passage. For
        # non-ID candidates require stronger, non-numeric lexical overlap;
        # otherwise short/common technical phrases and shared numbers create
        # too many weak associations.
        shared_tokens = req_tokens & page_tokens
        if exact or (len(shared_tokens) >= 3 and overlap >= 0.18):
            ranked.append((int(exact), overlap, -page.page_number, page))
    ranked.sort(key=lambda result: (result[0], result[1], result[2]), reverse=True)

    return [
        {
            **page.to_dict(),
            "matchType": "requirement_id" if exact else "text_similarity",
            "matchScore": 1.0 if exact else round(overlap, 4),
            "retrievalOnly": True,
        }
        for exact, overlap, _, page in ranked[:max(0, limit)]
    ]


def analyze_matrix_against_pdf(matrix_analysis: Any, pdf_path: str | Path) -> Dict[str, Any]:
    """Combine existing matrix parsing with page-cited PDF evidence candidates."""
    pages = extract_pdf_evidence(pdf_path)
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
        candidates = match_requirement_evidence(matrix_item, pages)
        requirements.append({
            **matrix_item,
            "evidence": candidates,
            "reviewStatus": "candidate_evidence_found" if candidates else "no_text_candidate_found",
        })

    unique_pages = {page.page_number for page in pages}
    return {
        "matrixFile": matrix_analysis.file_name,
        "tdrFile": Path(pdf_path).name,
        "summary": {
            "requirements": len(requirements),
            "requirementsWithEvidenceCandidates": sum(bool(item["evidence"]) for item in requirements),
            "requirementsWithoutEvidenceCandidates": sum(not item["evidence"] for item in requirements),
            "pagesWithExtractedText": len(unique_pages),
            "evidenceBlocks": len(pages),
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
            "Evidence links are retrieval candidates, not compliance verdicts.",
            "Scanned images, diagrams, charts, and other visual evidence are not assessed.",
            "No text candidate found does not mean the supplier failed the requirement.",
            "Technical values, units, test conditions, and applicability require reviewer verification.",
        ],
        "requirements": requirements,
        "unmatchedEvidencePages": [page.to_dict() for page in pages
                                   if not page.evidence_ids and not _tokens(page.text)],
    }