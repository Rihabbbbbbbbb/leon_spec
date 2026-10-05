"""
AERIS contradiction / audit layer.

Enterprise supplier-review practice (Booma compliance matrices, speXcompl.ai
Auditing Mode, REACH/RoHS declaration audits, automotive quality-document
spine arXiv:2607.04924) treats the matrix row as a *claim* and the TDR as
*evidence*. A claim without evidence is an assertion. A claim that says OK
while the TDR misses the target is a documentation failure, not a debate.

This module does not invent measurements. It classifies the already-computed
evidence verdict against the supplier's declaration and, when both sides
have numbers, compares the comment to the TDR.
"""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from typing import Dict, List, Optional

from app.qa.aeris_constraints import (
    Constraint,
    Measurement,
    compare_constraint,
    extract_measurements,
    summarize_verdicts,
)


# TDR polarity — words the supplier wrote in the dossier itself.
_TDR_FAIL = re.compile(
    r"\b(nok|n\.?o\.?k\.?|non[\s-]?conform|not\s+conform|fail(?:ed|ure)?|"
    r"deviation|d[ée]viation|[ée]cart|out\s+of\s+spec|does\s+not\s+meet|"
    r"cannot\s+meet|exceeds?\s+the\s+limit|above\s+the\s+limit)\b",
    re.I,
)
_TDR_PASS = re.compile(
    r"\b(ok|okay|conform(?:e|s|ing)?|pass(?:ed|es)?|within\s+spec|"
    r"meets?\s+the\s+(?:req|target|limit)|compliant)\b",
    re.I,
)

# Severity: 3 = stop-the-line for a quality reviewer, 1 = clarify.
_SEVERITY_RANK = {"critical": 3, "high": 2, "medium": 1, "info": 0}


@dataclass
class Contradiction:
    req_id: str
    type: str
    severity: str
    title: str
    action: str
    matrix_status: str
    evidence_status: str
    target: str
    claimed: str
    evidenced: str
    location: str
    excerpt: str
    domain: str
    confidence: str


def tdr_polarity(text: str) -> str:
    """Return FAIL / PASS / MIXED / NONE from the evidence passage wording."""
    if not text:
        return "NONE"
    text = re.sub(
        r"\b(?:no|without)\s+(?:known\s+)?(?:failures?|deviations?)\b"
        r"|\b(?:failures?|deviations?)\s*:\s*(?:none|0)\b",
        "", text, flags=re.I,
    )
    text = re.sub(
        r"\b(?:not|non)[\s-]+(?:compliant|conform(?:e|ing)?|ok|pass(?:ed)?)\b"
        r"|\bdoes\s+not\s+meet\b",
        "NOK", text, flags=re.I,
    )
    fail = bool(_TDR_FAIL.search(text))
    pass_ = bool(_TDR_PASS.search(text))
    if fail and pass_:
        return "MIXED"
    if fail:
        return "FAIL"
    if pass_:
        return "PASS"
    return "NONE"


def comment_vs_tdr(
    constraints: List[Constraint],
    comment: str,
    tdr_measurements: List[Measurement],
) -> Optional[str]:
    """
    If the supplier comment and the TDR both have comparable numbers for the
    same target, say whether they agree. Returns None when incomparable.
    """
    if not comment or not constraints or not tdr_measurements:
        return None
    comment_m = extract_measurements(comment, location="matrix-comment")
    if not comment_m:
        return None
    comment_v = []
    tdr_v = []
    for c in constraints:
        comment_v.extend(compare_constraint(c, comment_m))
        tdr_v.extend(compare_constraint(c, tdr_measurements))
    if not comment_v or not tdr_v:
        return None
    c_st = summarize_verdicts(comment_v)
    t_st = summarize_verdicts(tdr_v)
    judged = {"CONFORME", "NON_CONFORME", "PARTIELLEMENT_CONFORME"}
    if c_st not in judged or t_st not in judged:
        return None
    if c_st == t_st:
        return "COMMENT_MATCHES_TDR"
    return "COMMENT_CONTRADICTS_TDR"


def classify_contradiction(
    *,
    req_id: str,
    matrix_status: str,
    evidence_status: str,
    coherence: str,
    target: str,
    supplier_result: str,
    comment: str,
    evidence_excerpt: str,
    evidence_location: str,
    domain: str,
    confidence: str,
    constraints: List[Constraint],
    measurements: List[Measurement],
) -> Optional[Contradiction]:
    """
    Return the highest-severity audit finding for this row, or None if the
    claim and the evidence do not conflict.
    """
    polarity = tdr_polarity(evidence_excerpt)
    cvt = comment_vs_tdr(constraints, comment, measurements)

    # 1. Claimed OK / empty, TDR misses the numeric target.
    if matrix_status in ("OK", "EMPTY") and evidence_status in (
        "NON_CONFORME", "PARTIELLEMENT_CONFORME", "DEVIATION",
    ):
        partial = evidence_status == "PARTIELLEMENT_CONFORME"
        return Contradiction(
            req_id=req_id,
            type="CLAIM_OK_EVIDENCE_FAILS" if not partial else "CLAIM_OK_PARTIAL",
            severity="critical" if not partial else "high",
            title=(
                "Matrix says OK but the TDR misses the requirement"
                if not partial
                else "Matrix says OK but the TDR is only partially compliant"
            ),
            action=(
                "Reject the OK engagement. Ask the supplier for a deviation "
                "request or a redesign; do not treat the matrix as contractual OK."
            ),
            matrix_status=matrix_status,
            evidence_status=evidence_status,
            target=target,
            claimed=comment or matrix_status,
            evidenced=supplier_result or evidence_excerpt[:180],
            location=evidence_location,
            excerpt=evidence_excerpt,
            domain=domain,
            confidence=confidence,
        )

    # 2. Claimed OK, TDR itself writes NOK / Deviation (even without a number).
    if matrix_status == "OK" and polarity == "FAIL" and evidence_status in (
        "PREUVE_INSUFFISANTE", "MANQUANT",
    ):
        return Contradiction(
            req_id=req_id,
            type="CLAIM_OK_TDR_SAYS_NOK",
            severity="high",
            title="Matrix says OK but the TDR passage is worded as NOK / deviation",
            action=(
                "Challenge the OK mark. The dossier already admits a miss; "
                "request the measured values and a formal deviation."
            ),
            matrix_status=matrix_status,
            evidence_status=evidence_status,
            target=target,
            claimed=comment or "OK",
            evidenced=evidence_excerpt[:180],
            location=evidence_location,
            excerpt=evidence_excerpt,
            domain=domain,
            confidence=confidence,
        )

    # 3. Comment numbers contradict TDR numbers.
    if cvt == "COMMENT_CONTRADICTS_TDR":
        return Contradiction(
            req_id=req_id,
            type="COMMENT_CONTRADICTS_TDR",
            severity="high",
            title="Supplier comment and TDR numbers do not agree",
            action=(
                "Do not trust either figure until the supplier reconciles "
                "the comment with the TDR page."
            ),
            matrix_status=matrix_status,
            evidence_status=evidence_status,
            target=target,
            claimed=comment[:180],
            evidenced=supplier_result or evidence_excerpt[:180],
            location=evidence_location,
            excerpt=evidence_excerpt,
            domain=domain,
            confidence=confidence,
        )

    # 4. Claimed OK with no evidence — assertion, not compliance
    #    (Booma / Regilient: a claim without an evidence reference is not a position).
    if matrix_status == "OK" and evidence_status in ("MANQUANT", "PREUVE_INSUFFISANTE"):
        return Contradiction(
            req_id=req_id,
            type="CLAIM_OK_NO_EVIDENCE",
            severity="high" if evidence_status == "MANQUANT" else "medium",
            title="Matrix says OK but the TDR has no usable proof",
            action=(
                "Treat as unverified. Ask for the TDR slide / test report "
                "that substantiates this OK before accepting the engagement."
            ),
            matrix_status=matrix_status,
            evidence_status=evidence_status,
            target=target,
            claimed=comment or "OK",
            evidenced="(no comparable evidence)",
            location=evidence_location,
            excerpt=evidence_excerpt,
            domain=domain,
            confidence=confidence,
        )

    # 5. Claimed NOK / DEVIATION but TDR meets the target.
    if matrix_status in ("NOK", "DEVIATION") and evidence_status == "CONFORME":
        return Contradiction(
            req_id=req_id,
            type="CLAIM_NOK_EVIDENCE_PASSES",
            severity="medium",
            title="Matrix says NOK/deviation but the TDR meets the target",
            action=(
                "Clarify with the supplier: either the matrix is stale or "
                "the TDR page is the wrong operating point. Do not close "
                "as NOK without checking."
            ),
            matrix_status=matrix_status,
            evidence_status=evidence_status,
            target=target,
            claimed=comment or matrix_status,
            evidenced=supplier_result or evidence_excerpt[:180],
            location=evidence_location,
            excerpt=evidence_excerpt,
            domain=domain,
            confidence=confidence,
        )

    # 6. TDR polarity FAIL while matrix is OK was already handled.
    #    TDR polarity PASS while matrix is NOK and we have no numbers:
    if (
        matrix_status in ("NOK", "DEVIATION")
        and polarity == "PASS"
        and evidence_status in ("PREUVE_INSUFFISANTE", "MANQUANT")
    ):
        return Contradiction(
            req_id=req_id,
            type="CLAIM_NOK_TDR_SAYS_OK",
            severity="medium",
            title="Matrix says NOK but the TDR passage is worded as OK / pass",
            action="Ask the supplier which document is authoritative.",
            matrix_status=matrix_status,
            evidence_status=evidence_status,
            target=target,
            claimed=comment or matrix_status,
            evidenced=evidence_excerpt[:180],
            location=evidence_location,
            excerpt=evidence_excerpt,
            domain=domain,
            confidence=confidence,
        )

    return None


def build_review_queue(items: List[Contradiction]) -> List[Contradiction]:
    """Critical first, then high, then by REQ-ID — the auditor's worklist."""
    return sorted(
        items,
        key=lambda c: (-_SEVERITY_RANK.get(c.severity, 0), c.type, c.req_id),
    )


def contradiction_summary(items: List[Contradiction]) -> Dict:
    by_type: Dict[str, int] = {}
    by_sev: Dict[str, int] = {}
    for c in items:
        by_type[c.type] = by_type.get(c.type, 0) + 1
        by_sev[c.severity] = by_sev.get(c.severity, 0) + 1
    return {
        "total": len(items),
        "critical": by_sev.get("critical", 0),
        "high": by_sev.get("high", 0),
        "medium": by_sev.get("medium", 0),
        "byType": by_type,
    }


def contradictions_to_dicts(items: List[Contradiction]) -> List[Dict]:
    return [asdict(c) for c in items]
