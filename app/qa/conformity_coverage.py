"""
Spec ↔ Conformity Matrix coverage & traceability.

Answers the gate-review question: "did the supplier answer every requirement
of the specification?" by linking each spec requirement to its matrix row.

Matching is deterministic and auditable:
  - the matrix REQ-ID (column C) is matched directly against spec IDs
  - the matrix "Reference" column (column B) is scanned for spec-side IDs
    (REF-… / APP-… / GEN-… / REQ-…) and matched against the spec
  - matrix rows that match no spec requirement are reported as "unmatched"
  - spec requirements with no matrix row are reported as "unanswered"

The report is a pure function of (spec text, ConformityAnalysis), so it is
trivially testable and reusable by the API, the UI and the Excel generator.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from app.qa.spec_to_matrix import Requirement, extract_requirements

# Requirement-ID schemes: REF-… / APP-… / GEN-… (spec) and REQ-… (matrix).
_ID_RE = re.compile(
    r"\b((?:REF|APP|GEN)-[A-Za-z0-9][A-Za-z0-9_.-]{1,40}|REQ-\d{4,10})\b",
    re.IGNORECASE,
)


def normalize_id(text: str) -> str:
    """Canonical form of a requirement ID: uppercase, no whitespace, no
    trailing version suffix like '(0)'."""
    if not text:
        return ""
    t = re.sub(r"\s+", "", str(text)).upper()
    t = re.sub(r"\(\d{1,3}\)$", "", t)
    return t


def extract_id_tokens(text: str) -> List[str]:
    """Every requirement-ID token found in a string (e.g. the matrix
    'Reference' column: '[STA12] RA19 | [M8] GEN-HW-ST-SSC.009(0)')."""
    return [normalize_id(m) for m in _ID_RE.findall(str(text or ""))]


def canonical_key(req_id: str, reference: str) -> str:
    """Stable identity for a matrix row: prefer the REQ-ID, else the first
    spec-side reference token found in column B."""
    rid = normalize_id(req_id)
    if rid:
        return rid
    refs = extract_id_tokens(reference)
    return refs[0] if refs else ""


@dataclass
class CoverageReport:
    """Result of linking a specification to a conformity matrix.

    Each spec requirement ends up in exactly one of three states:
      - answered : present in the matrix AND the supplier gave a verdict
                   (OK / NOK / NA)
      - pending  : present in the matrix but no verdict yet (EMPTY)
      - missing  : no matrix row at all
    """
    spec_name: str = ""
    matrix_name: str = ""
    spec_total: int = 0
    spec_with_id: int = 0
    spec_without_id: int = 0
    matched: int = 0
    answered: int = 0
    pending: int = 0
    missing: int = 0
    coverage_rate: float = 0.0   # matched / spec_with_id  (traceability)
    answer_rate: float = 0.0     # answered / spec_with_id (actual answers)
    matrix_requirement_rows: int = 0
    matches: List[Dict] = field(default_factory=list)      # in-matrix (answered + pending)
    missing_list: List[Dict] = field(default_factory=list)  # not in matrix
    untraceable: List[Dict] = field(default_factory=list)   # spec reqs without an ID
    unmatched_rows: List[Dict] = field(default_factory=list)
    report_text: str = ""


def _token_overlap(a: str, b: str) -> float:
    """Jaccard overlap of the significant tokens of two texts (0..1)."""
    ta = set(re.findall(r"[a-z0-9]{3,}", (a or "").lower()))
    tb = set(re.findall(r"[a-z0-9]{3,}", (b or "").lower()))
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


def _find_spec_match(item, spec_by_id: Dict[str, Requirement], spec_reqs: List[Requirement]) -> Optional[Tuple[Requirement, str]]:
    """Return (spec_requirement, matched_by) for a matrix item, or None.

    Matching tiers, in order:
      1. req_id      — exact REQ-ID match against the spec
      2. reference   — any spec-side ID token found in column B
      3. description — conservative token-overlap fallback for rows whose
                       reference column is empty (the supplier copied the
                       requirement text into the matrix)
    """
    rid = normalize_id(item.req_id)
    if rid and rid in spec_by_id:
        return spec_by_id[rid], "req_id"
    for token in extract_id_tokens(item.reference):
        if token in spec_by_id:
            return spec_by_id[token], "reference"
    desc = (item.description or "").strip()
    if len(desc) >= 20:
        best: Optional[Requirement] = None
        best_score = 0.0
        for r in spec_reqs:
            if not r.text:
                continue
            score = _token_overlap(desc, r.text)
            if score > best_score:
                best, best_score = r, score
        if best is not None and best_score >= 0.5:
            return best, "description"
    return None


def build_coverage_report(
    spec_text: str,
    analysis,
    spec_name: str = "",
    matrix_name: str = "",
) -> CoverageReport:
    """Link every spec requirement to its matrix row (if any)."""
    reqs = extract_requirements(spec_text)
    spec_by_id: Dict[str, Requirement] = {}
    for r in reqs:
        if r.req_id:
            spec_by_id.setdefault(normalize_id(r.req_id), r)

    report = CoverageReport(
        spec_name=spec_name,
        matrix_name=matrix_name,
        spec_total=len(reqs),
        spec_with_id=len(spec_by_id),
        spec_without_id=sum(1 for r in reqs if not r.req_id),
    )

    matched_spec_ids: set = set()
    matches_by_spec: Dict[str, List[Dict]] = {}
    for item in analysis.items:
        if not item.is_requirement:
            continue
        report.matrix_requirement_rows += 1
        found = _find_spec_match(item, spec_by_id, reqs)
        if found is None:
            report.unmatched_rows.append({
                "reqId": item.req_id,
                "reference": item.reference,
                "category": item.conformity_category,
                "comment": item.comment,
            })
            continue
        spec_req, matched_by = found
        sid = normalize_id(spec_req.req_id)
        matched_spec_ids.add(sid)
        matches_by_spec.setdefault(sid, []).append({
            "specReqId": spec_req.req_id,
            "specText": spec_req.text,
            "matrixReqId": item.req_id,
            "matrixReference": item.reference,
            "category": item.conformity_category,
            "comment": item.comment,
            "matchedBy": matched_by,
        })

    # answered = the supplier actually gave a verdict (OK/NOK/NA) in at least
    # one of the requirement's rows; present-but-empty = pending.
    for ms in matches_by_spec.values():
        if any(m["category"] in ("OK", "NOK", "NA") for m in ms):
            report.answered += 1
        else:
            report.pending += 1
    report.matched = report.answered + report.pending

    # Flatten to one row per spec requirement (prefer an answered row),
    # keeping the spec's own document order.
    for r in reqs:
        if not r.req_id:
            continue
        ms = matches_by_spec.get(normalize_id(r.req_id))
        if not ms:
            continue
        best = next((m for m in ms if m["category"] in ("OK", "NOK", "NA")), ms[0])
        report.matches.append(best)

    report.missing_list = [
        {"reqId": r.req_id, "text": r.text}
        for r in reqs
        if r.req_id and normalize_id(r.req_id) not in matched_spec_ids
    ]
    report.missing = len(report.missing_list)
    report.untraceable = [{"reqId": "", "text": r.text} for r in reqs if not r.req_id]

    denom = report.spec_with_id
    report.coverage_rate = round(report.matched / denom, 4) if denom else 0.0
    report.answer_rate = round(report.answered / denom, 4) if denom else 0.0
    report.report_text = _coverage_report_text(report)
    return report


def coverage_to_dict(report: CoverageReport) -> dict:
    """JSON-serializable coverage report."""
    return {
        "specName": report.spec_name,
        "matrixName": report.matrix_name,
        "specTotal": report.spec_total,
        "specWithId": report.spec_with_id,
        "specWithoutId": report.spec_without_id,
        "matchedCount": report.matched,
        "answeredCount": report.answered,
        "pendingCount": report.pending,
        "missingCount": report.missing,
        "coverageRate": report.coverage_rate,
        "answerRate": report.answer_rate,
        "matrixRequirementRows": report.matrix_requirement_rows,
        "unmatchedRowsCount": len(report.unmatched_rows),
        "matches": report.matches,
        "missing": report.missing_list,
        "untraceable": report.untraceable,
        "unmatchedRows": report.unmatched_rows,
        "reportText": report.report_text,
    }


def _coverage_report_text(report: CoverageReport) -> str:
    lines = [
        "=" * 70,
        "LEON — Spec ↔ Matrix Coverage & Traceability Report",
        "=" * 70,
        "",
        f"  Specification : {report.spec_name or '(inline text)'}",
        f"  Matrix        : {report.matrix_name or '(analysis)'}",
        "",
        "─" * 50,
        "COVERAGE",
        "─" * 50,
        f"  Spec requirements (with ID) : {report.spec_with_id}",
        f"  In the matrix (traced)      : {report.matched}  ({report.coverage_rate * 100:.1f}%)",
        f"  Answered by the supplier    : {report.answered}  ({report.answer_rate * 100:.1f}%)",
        f"  In the matrix, not answered : {report.pending}",
        f"  Missing from the matrix     : {report.missing}",
        f"  Spec requirements (no ID)   : {report.spec_without_id} (cannot be traced by ID)",
        f"  Matrix requirement rows     : {report.matrix_requirement_rows}",
        f"  Matrix rows with no match   : {len(report.unmatched_rows)}",
        "",
    ]
    if report.pending:
        pending = [m for m in report.matches if m["category"] == "EMPTY"]
        lines.append("─" * 50)
        lines.append(f"PENDING — IN THE MATRIX BUT NOT ANSWERED — {report.pending}")
        lines.append("─" * 50)
        for m in pending[:50]:
            lines.append(f"  ⏳ {m['specReqId']}: {m['specText'][:90]}")
        lines.append("")
    if report.missing_list:
        lines.append("─" * 50)
        lines.append(f"MISSING FROM THE MATRIX — {report.missing}")
        lines.append("─" * 50)
        for u in report.missing_list[:50]:
            lines.append(f"  ❌ {u['reqId']}: {u['text'][:90]}")
        lines.append("")
    if report.unmatched_rows:
        lines.append("─" * 50)
        lines.append(f"MATRIX ROWS WITH NO SPEC MATCH — {len(report.unmatched_rows)}")
        lines.append("─" * 50)
        for u in report.unmatched_rows[:50]:
            lines.append(f"  ⚠ {u['reqId'] or u['reference']}: {u['category']} — {u['comment'][:60]}")
        lines.append("")
    lines.append("=" * 70)
    lines.append("End of report — LEON Coverage & Traceability")
    lines.append("=" * 70)
    return "\n".join(lines)