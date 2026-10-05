"""
AERIS statement crosswalk — what the supplier *said*.

Industrial review practice (speXcompl.ai Auditing Mode, VDA 2 / PPAP
dimensional-results vs declaration, Booma/Regilient “a claim without an
evidence reference is an assertion”) is a three-way comparison:

    Stellantis requirement  ↔  matrix declaration  ↔  TDR statement

The existing numeric engine answers “does the TDR meet the requirement?”.
This module answers the question the reviewer actually asks next:

    Does what the supplier wrote in the TDR agree with what they wrote
    in the matrix — polarity, numbers, and the target they restated?

It never invents values. Every statement is extracted from text that is
already on the row or on a retrieved TDR slide/page.
"""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from app.qa.aeris_constraints import (
    Constraint,
    Measurement,
    classify_unit,
    conditions_compatible,
    quantities_compatible,
    extract_constraints,
    extract_measurements,
    operator_symbol,
    to_canonical,
)
from app.qa.aeris_contradictions import Contradiction, tdr_polarity


# Words that introduce a *restated spec* rather than a measured result.
_RESTATE_LEAD = re.compile(
    r"\b(?:target|objectif|consigne|requirement|exigence|"
    r"spec(?:ification)?|limit|seuil|design\s+target)\b"
    r"(?:\s*(?:is|of|de|=|:))?\s*(?:≤|≥|<=|>=|<|>)?\s*$",
    re.I,
)

_RESTATE_FULL = re.compile(
    r"\b(?:target|objectif|consigne|requirement|exigence|"
    r"spec(?:ification)?|limit|seuil|design\s+target)\b"
    r"\s*(?:is|of|de|=|:)?\s*"
    r"(≤|≥|<=|>=|<|>)?"
    r"\s*(-?\d+(?:[.,]\d+)?)\s*"
    r"([µuμ]?[A-Za-z%°]+(?:\s*[Cc])?|:1)?",
    re.I,
)

_REQ_ID_RE = re.compile(r"\bREQ[-_ ]?\d{4,}\b", re.I)

_ABS_TOL = {
    "current": 2.0,
    "temperature": 0.5,
    "percent": 1.0,
    "time": 5.0,
    "ratio": 5.0,
    "length": 0.05,
    "voltage": 0.05,
    "power": 5.0,
    "frequency": 1.0,
    "angle": 0.5,
}

_REL_TOL = 0.08


@dataclass
class RestatedTarget:
    raw: str
    value: float
    operator: str
    unit_family: str
    unit: str
    quantity: str
    location: str
    source: str  # tdr | matrix_comment


@dataclass
class StatementCrosswalk:
    req_id: str
    domain: str
    requirement: str
    stellatis_target: str
    matrix_status: str
    matrix_said: str
    matrix_polarity: str
    tdr_said: str
    tdr_polarity: str
    tdr_location: str
    tdr_excerpt: str
    values_agree: str          # AGREE | DISAGREE | INCOMPARABLE | NO_TDR | NO_MATRIX_VALUE
    polarity_agree: str        # AGREE | OPPOSITE | INCOMPARABLE
    restated_target: str
    restated_target_match: str  # MATCH | WRONG_TARGET | NONE
    tdr_internal_conflict: str
    alignment: str             # ALIGNED | OPPOSITE | VALUE_MISMATCH | WRONG_TARGET | TDR_CONFLICT | UNVERIFIABLE
    incompliance_type: str
    incompliance_severity: str
    incompliance_title: str
    action: str
    confidence: str
    extra_types: List[str] = field(default_factory=list)


def is_restated_target_prefix(prefix: str) -> bool:
    """True when the number that follows is a quoted spec, not a result."""
    return bool(prefix and _RESTATE_LEAD.search(prefix))


def extract_restated_targets(text: str, location: str = "", source: str = "tdr") -> List[RestatedTarget]:
    """Pull ‘target ≤150mA’ / ‘limit 800ms’ phrases out of a TDR or comment."""
    if not text:
        return []
    found: List[RestatedTarget] = []
    for m in _RESTATE_FULL.finditer(text):
        op_tok, raw_num, unit_tok = m.group(1) or "", m.group(2), m.group(3) or ""
        window = text[max(0, m.start() - 24): m.end() + 24]
        family, unit, factor = classify_unit(unit_tok, window)
        if not family:
            continue
        op = {
            "≤": "le", "<=": "le", "<": "lt",
            "≥": "ge", ">=": "ge", ">": "gt",
        }.get(op_tok, "")
        if not op:
            # Bare “target 800ms” is still a restated limit; default by family.
            op = "le" if family in ("current", "temperature", "power", "time") else "ge"
        value = to_canonical(float(raw_num.replace(",", ".")), family, factor)
        qty = ""
        low = window.lower()
        if "current" in low or "consum" in low:
            qty = "current"
        elif "temp" in low:
            qty = "temperature"
        elif "time" in low or "startup" in low:
            qty = "time"
        found.append(RestatedTarget(
            raw=m.group(0).strip(),
            value=value,
            operator=op,
            unit_family=family,
            unit=unit or family,
            quantity=qty or family,
            location=location,
            source=source,
        ))
    # Dedupe
    seen = set()
    out = []
    for t in found:
        key = (round(t.value, 6), t.unit_family, t.operator)
        if key in seen:
            continue
        seen.add(key)
        out.append(t)
    return out


def cited_req_ids(text: str) -> List[str]:
    if not text:
        return []
    return sorted({m.group(0).replace(" ", "").replace("_", "-").upper() for m in _REQ_ID_RE.finditer(text)})


def matrix_polarity(status: str, comment: str) -> str:
    """PASS / FAIL / NA / NONE from the matrix row (status + comment wording)."""
    if (status or "").upper() == "NA":
        return "NA"
    comment_pol = tdr_polarity(comment or "")
    if (status or "").upper() in ("NOK", "DEVIATION"):
        return "FAIL"
    if (status or "").upper() == "OK":
        if comment_pol == "FAIL":
            return "FAIL"
        return "PASS"
    if comment_pol != "NONE":
        return comment_pol
    return "NONE"


def format_measurements(measurements: Sequence[Measurement], limit: int = 4) -> str:
    bits = []
    for m in measurements[:limit]:
        q = f"{m.qualifier} " if m.qualifier else ""
        cond = ""
        if m.condition:
            cond = m.condition if m.condition.lstrip().startswith("@") else f" @{m.condition}"
        bound = operator_symbol(m.bound) if m.bound else ""
        bits.append(f"{bound}{q}{m.value:g} {m.unit}{cond}".strip())
    return ", ".join(bits)


def values_relation(
    matrix_m: Sequence[Measurement],
    tdr_m: Sequence[Measurement],
) -> str:
    """AGREE / DISAGREE / INCOMPARABLE / NO_TDR / NO_MATRIX_VALUE."""
    if not tdr_m:
        return "NO_TDR" if not matrix_m else "NO_TDR"
    if not matrix_m:
        return "NO_MATRIX_VALUE"
    pairs = _pair_measurements(matrix_m, tdr_m)
    if not pairs:
        return "INCOMPARABLE"
    disagreed = any(_values_disagree(a, b) for a, b in pairs)
    if disagreed:
        return "DISAGREE"
    if any((a.bound or b.bound) and (a.bound != b.bound or a.value != b.value) for a, b in pairs):
        return "INCOMPARABLE"
    return "AGREE"


def polarity_relation(matrix_pol: str, tdr_pol: str) -> str:
    if matrix_pol in ("PASS", "FAIL") and tdr_pol in ("PASS", "FAIL"):
        return "AGREE" if matrix_pol == tdr_pol else "OPPOSITE"
    return "INCOMPARABLE"


def restated_vs_requirement(
    restated: Sequence[RestatedTarget],
    constraints: Sequence[Constraint],
) -> Tuple[str, str]:
    """
    Return (display, MATCH|WRONG_TARGET|NONE).
    WRONG_TARGET = the supplier is answering a different limit than Stellantis wrote.
    """
    if not restated or not constraints:
        return ("", "NONE")
    display = "; ".join(
        f"{operator_symbol(t.operator)}{t.value:g} {t.unit}" + (f" ({t.location})" if t.location else "")
        for t in restated[:3]
    )
    matched = False
    for t in restated:
        candidates = [
            c for c in constraints if t.unit_family == c.unit_family
            and quantities_compatible(t.quantity, c.quantity)
        ]
        if not candidates:
            continue
        if not any(
            abs(t.value - c.value) <= 1e-9 and t.operator == c.operator
            for c in candidates
        ):
            return display, "WRONG_TARGET"
        matched = True
    return display, "MATCH" if matched else "NONE"


def tdr_self_conflicts(tdr_m: Sequence[Measurement]) -> str:
    """
    Same quantity + compatible condition + same qualifier (or both empty),
    but values disagree. Typ vs Max is *not* a conflict.
    """
    items = list(tdr_m)
    for i, a in enumerate(items):
        for b in items[i + 1:]:
            if a.unit_family != b.unit_family:
                continue
            if not quantities_compatible(a.quantity, b.quantity):
                continue
            if a.unit_family == "angle":
                continue  # viewing-angle conditions, not product results
            if not conditions_compatible(a.condition, b.condition):
                continue
            qa, qb = a.qualifier or "", b.qualifier or ""
            if qa and qb and qa != qb:
                continue  # typ vs max
            if _values_disagree(a, b):
                return (
                    f"{a.qualifier + ' ' if a.qualifier else ''}{a.value:g} {a.unit}"
                    f" vs {b.qualifier + ' ' if b.qualifier else ''}{b.value:g} {b.unit}"
                    + (f" ({a.location}/{b.location})" if a.location or b.location else "")
                )
    return ""


def build_crosswalk(
    *,
    req_id: str,
    domain: str,
    description: str,
    matrix_status: str,
    comment: str,
    constraints: Sequence[Constraint],
    tdr_measurements: Sequence[Measurement],
    tdr_text: str,
    tdr_location: str,
    confidence: str,
) -> StatementCrosswalk:
    matrix_m = extract_measurements(comment or "", location="matrix-comment")
    restated = extract_restated_targets(tdr_text or "", location=tdr_location, source="tdr")
    restated += extract_restated_targets(comment or "", location="matrix-comment", source="matrix_comment")

    m_pol = matrix_polarity(matrix_status, comment)
    t_pol = tdr_polarity(tdr_text or "")
    val = values_relation(matrix_m, tdr_measurements)
    pol = polarity_relation(m_pol, t_pol)
    rest_disp, rest_match = restated_vs_requirement(restated, constraints)
    internal = tdr_self_conflicts(tdr_measurements)

    target = ""
    if constraints:
        c0 = constraints[0]
        target = f"{operator_symbol(c0.operator)}{c0.value:g} {c0.unit}"
        if c0.condition:
            target += f" ({c0.condition})"

    matrix_said = matrix_status or "EMPTY"
    if comment:
        matrix_said += " — " + comment.strip()[:180]

    tdr_vals = format_measurements(tdr_measurements)
    if tdr_vals:
        tdr_said = tdr_vals
        if t_pol != "NONE":
            tdr_said += f" [{t_pol}]"
        if tdr_location:
            tdr_said += f" ({tdr_location})"
    elif tdr_text:
        tdr_said = tdr_text.strip()[:180]
        if tdr_location:
            tdr_said += f" ({tdr_location})"
    else:
        tdr_said = "(no TDR passage retrieved)"

    alignment, itype, sev, title, action = _classify_statement(
        matrix_status=matrix_status,
        m_pol=m_pol,
        t_pol=t_pol,
        val=val,
        pol=pol,
        rest_match=rest_match,
        internal=internal,
        has_tdr=bool(tdr_text or tdr_measurements),
    )

    extras: List[str] = []
    if val == "DISAGREE" and itype != "VALUE_MISMATCH":
        extras.append("VALUE_MISMATCH")
    if rest_match == "WRONG_TARGET" and itype != "TDR_RESTATES_WRONG_TARGET":
        extras.append("TDR_RESTATES_WRONG_TARGET")
    if internal and itype != "TDR_INTERNAL_CONFLICT":
        extras.append("TDR_INTERNAL_CONFLICT")

    return StatementCrosswalk(
        req_id=req_id,
        domain=domain,
        requirement=(description or "")[:240],
        stellatis_target=target,
        matrix_status=matrix_status,
        matrix_said=matrix_said,
        matrix_polarity=m_pol,
        tdr_said=tdr_said,
        tdr_polarity=t_pol,
        tdr_location=tdr_location,
        tdr_excerpt=(tdr_text or "")[:420],
        values_agree=val,
        polarity_agree=pol,
        restated_target=rest_disp,
        restated_target_match=rest_match,
        tdr_internal_conflict=internal,
        alignment=alignment,
        incompliance_type=itype,
        incompliance_severity=sev,
        incompliance_title=title,
        action=action,
        confidence=confidence,
        extra_types=extras,
    )


def statement_contradictions(walk: StatementCrosswalk) -> List[Contradiction]:
    """Turn crosswalk findings into review-queue Contradiction rows."""
    out: List[Contradiction] = []
    kinds = []
    if walk.incompliance_type:
        kinds.append(walk.incompliance_type)
    kinds.extend(walk.extra_types)
    seen = set()
    for kind in kinds:
        if not kind or kind in seen:
            continue
        seen.add(kind)
        title, sev, action = _type_copy(kind, walk)
        out.append(Contradiction(
            req_id=walk.req_id,
            type=kind,
            severity=sev,
            title=title,
            action=action,
            matrix_status=walk.matrix_status,
            evidence_status=walk.alignment,
            target=walk.stellatis_target,
            claimed=walk.matrix_said,
            evidenced=walk.tdr_said,
            location=walk.tdr_location,
            excerpt=walk.tdr_excerpt,
            domain=walk.domain,
            confidence=walk.confidence,
        ))
    return out


def build_tdr_ledger(chunks) -> List[Dict]:
    """Every polarity / measurement / restated target found in the TDR."""
    ledger: List[Dict] = []
    for chunk in chunks:
        text = getattr(chunk, "text", "") or ""
        loc = getattr(chunk, "location", "") or ""
        fname = getattr(chunk, "file_name", "") or ""
        pol = tdr_polarity(text)
        measurement_text = getattr(chunk, "measurement_text", None)
        meas = extract_measurements(
            measurement_text if measurement_text is not None else text, location=loc,
        )
        rest = extract_restated_targets(text, location=loc, source="tdr")
        reqs = cited_req_ids(text)
        if pol == "NONE" and not meas and not rest and not reqs:
            continue
        ledger.append({
            "file": fname,
            "location": loc,
            "polarity": pol,
            "values": format_measurements(meas, limit=8),
            "restatedTargets": "; ".join(
                f"{operator_symbol(t.operator)}{t.value:g} {t.unit}" for t in rest
            ),
            "citedReqIds": reqs,
            "excerpt": text.strip()[:280],
        })
    return ledger


def build_deviation_register(
    items: Sequence,
    walks: Sequence[StatementCrosswalk],
) -> List[Dict]:
    """Union of deviations declared in the matrix and/or admitted in the TDR."""
    by_id = {w.req_id: w for w in walks}
    rows: List[Dict] = []
    for it in items:
        req_id = getattr(it, "req_id", "")
        walk = by_id.get(req_id)
        matrix_dev = (getattr(it, "matrix_status", "") or "").upper() == "DEVIATION"
        evidence_dev = getattr(it, "final_status", "") == "DEVIATION"
        tdr_dev = bool(walk and walk.tdr_polarity == "FAIL" and re.search(
            r"deviation|d[ée]viation|[ée]cart", walk.tdr_excerpt or "", re.I
        ))
        comment_dev = bool(re.search(
            r"deviation|d[ée]viation|[ée]cart", getattr(it, "comment", "") or "", re.I
        ))
        if not (matrix_dev or evidence_dev or tdr_dev or comment_dev):
            continue
        declared_where = []
        if matrix_dev or comment_dev:
            declared_where.append("matrix")
        if tdr_dev:
            declared_where.append("tdr")
        rows.append({
            "req_id": req_id,
            "domain": getattr(it, "domain", ""),
            "target": getattr(it, "target", "") or (walk.stellatis_target if walk else ""),
            "matrix_status": getattr(it, "matrix_status", ""),
            "final_status": getattr(it, "final_status", ""),
            "declaredIn": "+".join(declared_where) or "inferred",
            "matrix_said": walk.matrix_said if walk else getattr(it, "comment", ""),
            "tdr_said": walk.tdr_said if walk else getattr(it, "supplier_result", ""),
            "location": getattr(it, "evidence_location", ""),
            "gap": getattr(it, "gap", ""),
        })
    return rows


def coverage_map(items: Sequence, walks: Sequence[StatementCrosswalk], ledger: Sequence[Dict]) -> Dict:
    cited = {rid for row in ledger for rid in (row.get("citedReqIds") or [])}
    total = 0
    matched = 0
    missing = 0
    cited_hits = 0
    rows = []
    for it in items:
        req_id = getattr(it, "req_id", "")
        if not req_id:
            continue
        total += 1
        loc = getattr(it, "evidence_location", "") or ""
        status = getattr(it, "final_status", "")
        hit = status not in ("MANQUANT", "NA") and bool(loc or getattr(it, "evidence_excerpt", ""))
        if status == "NA":
            level = "NA"
        elif req_id.upper() in cited:
            level = "CITED"
            cited_hits += 1
            matched += 1
        elif hit:
            level = "MATCHED"
            matched += 1
        else:
            level = "MISSING"
            missing += 1
        rows.append({
            "req_id": req_id,
            "coverage": level,
            "location": loc,
            "final_status": status,
        })
    return {
        "total": total,
        "matched": matched,
        "missing": missing,
        "citedInTdr": cited_hits,
        "coveragePct": round(100.0 * matched / total, 1) if total else 0.0,
        "rows": rows,
    }


def crosswalk_summary(walks: Sequence[StatementCrosswalk]) -> Dict:
    by_align: Dict[str, int] = {}
    by_type: Dict[str, int] = {}
    opposite = value_mismatch = wrong_target = tdr_conflict = 0
    for w in walks:
        by_align[w.alignment] = by_align.get(w.alignment, 0) + 1
        kinds = ([w.incompliance_type] if w.incompliance_type else []) + list(w.extra_types)
        for k in kinds:
            by_type[k] = by_type.get(k, 0) + 1
        if w.alignment == "OPPOSITE" or "DECLARATION_OPPOSITE" in kinds:
            opposite += 1
        if w.alignment == "VALUE_MISMATCH" or "VALUE_MISMATCH" in kinds:
            value_mismatch += 1
        if w.alignment == "WRONG_TARGET" or "TDR_RESTATES_WRONG_TARGET" in kinds:
            wrong_target += 1
        if w.alignment == "TDR_CONFLICT" or "TDR_INTERNAL_CONFLICT" in kinds:
            tdr_conflict += 1
    incompliant = sum(
        1 for w in walks
        if w.alignment in ("OPPOSITE", "VALUE_MISMATCH", "WRONG_TARGET", "TDR_CONFLICT")
    )
    return {
        "total": len(walks),
        "aligned": by_align.get("ALIGNED", 0),
        "opposite": opposite,
        "valueMismatch": value_mismatch,
        "wrongTarget": wrong_target,
        "tdrConflict": tdr_conflict,
        "unverifiable": by_align.get("UNVERIFIABLE", 0),
        "incompliant": incompliant,
        "byAlignment": by_align,
        "byType": by_type,
    }


def crosswalks_to_dicts(walks: Sequence[StatementCrosswalk]) -> List[Dict]:
    return [asdict(w) for w in walks]


# ── internals ─────────────────────────────────────────────────────

def _pair_measurements(
    a: Sequence[Measurement], b: Sequence[Measurement]
) -> List[Tuple[Measurement, Measurement]]:
    pairs = []
    used = set()
    for i, ma in enumerate(a):
        best = None
        best_j = None
        for j, mb in enumerate(b):
            if j in used:
                continue
            if ma.unit_family != mb.unit_family:
                continue
            if ma.quantity and mb.quantity and ma.quantity != mb.quantity:
                continue
            if not conditions_compatible(ma.condition, mb.condition):
                continue
            qa, qb = ma.qualifier or "", mb.qualifier or ""
            if qa and qb and qa != qb:
                continue
            best, best_j = mb, j
            break
        if best is not None:
            used.add(best_j)
            pairs.append((ma, best))
    return pairs


def _values_disagree(a: Measurement, b: Measurement) -> bool:
    if a.bound or b.bound:
        low_a = a.value if a.bound in ("", "ge", "gt") else float("-inf")
        high_a = a.value if a.bound in ("", "le", "lt") else float("inf")
        low_b = b.value if b.bound in ("", "ge", "gt") else float("-inf")
        high_b = b.value if b.bound in ("", "le", "lt") else float("inf")
        if high_a < low_b or high_b < low_a:
            return True
        if high_a == low_b and (a.bound == "lt" or b.bound == "gt"):
            return True
        if high_b == low_a and (b.bound == "lt" or a.bound == "gt"):
            return True
        return False
    return _numeric_disagree(a.value, b.value, a.unit_family)


def _numeric_disagree(a: float, b: float, family: str) -> bool:
    if abs(a - b) <= 1e-9:
        return False
    tol = _ABS_TOL.get(family, 0.0)
    if abs(a - b) <= tol:
        return False
    rel = abs(a - b) / max(abs(a), abs(b), 1e-9)
    return rel > _REL_TOL


def _classify_statement(
    *,
    matrix_status: str,
    m_pol: str,
    t_pol: str,
    val: str,
    pol: str,
    rest_match: str,
    internal: str,
    has_tdr: bool,
) -> Tuple[str, str, str, str, str]:
    """alignment, type, severity, title, action — primary statement finding."""
    if matrix_status == "NA":
        return "ALIGNED", "", "", "", ""

    if internal:
        return (
            "TDR_CONFLICT",
            "TDR_INTERNAL_CONFLICT",
            "high",
            f"TDR disagrees with itself ({internal})",
            "Ask the supplier which TDR figure is authoritative before trusting the matrix.",
        )
    if rest_match == "WRONG_TARGET":
        return (
            "WRONG_TARGET",
            "TDR_RESTATES_WRONG_TARGET",
            "high",
            "TDR restates a different target than the Stellantis requirement",
            "The supplier is answering the wrong limit. Reset the target and re-score.",
        )
    if val == "DISAGREE":
        return (
            "VALUE_MISMATCH",
            "VALUE_MISMATCH",
            "high",
            "Matrix comment numbers do not match the TDR numbers",
            "Do not accept either figure until the supplier reconciles the two documents.",
        )
    if pol == "OPPOSITE":
        return (
            "OPPOSITE",
            "DECLARATION_OPPOSITE",
            "critical" if m_pol == "PASS" and t_pol == "FAIL" else "medium",
            "Matrix declaration is the opposite of what the TDR says",
            "Challenge the matrix mark — the dossier says the opposite.",
        )
    if not has_tdr:
        return "UNVERIFIABLE", "", "", "", ""
    if val in ("AGREE", "NO_MATRIX_VALUE") and pol in ("AGREE", "INCOMPARABLE"):
        return "ALIGNED", "", "", "", ""
    if val == "NO_TDR":
        return "UNVERIFIABLE", "", "", "", ""
    return "ALIGNED", "", "", "", ""


def _type_copy(kind: str, walk: StatementCrosswalk) -> Tuple[str, str, str]:
    catalog = {
        "DECLARATION_OPPOSITE": (
            "Matrix declaration is the opposite of what the TDR says",
            "critical" if walk.matrix_polarity == "PASS" and walk.tdr_polarity == "FAIL" else "medium",
            "Challenge the matrix mark — the dossier says the opposite.",
        ),
        "VALUE_MISMATCH": (
            "Matrix comment numbers do not match the TDR numbers",
            "high",
            "Do not accept either figure until the supplier reconciles the two documents.",
        ),
        "TDR_RESTATES_WRONG_TARGET": (
            "TDR restates a different target than the Stellantis requirement",
            "high",
            "The supplier is answering the wrong limit. Reset the target and re-score.",
        ),
        "TDR_INTERNAL_CONFLICT": (
            f"TDR disagrees with itself ({walk.tdr_internal_conflict})"
            if walk.tdr_internal_conflict
            else "TDR disagrees with itself on this requirement",
            "high",
            "Ask the supplier which TDR figure is authoritative before trusting the matrix.",
        ),
    }
    return catalog.get(kind, (walk.incompliance_title or kind, walk.incompliance_severity or "medium", walk.action))


def merge_contradictions(
    primary: Optional[Contradiction],
    extras: Iterable[Contradiction],
) -> List[Contradiction]:
    """Keep the numeric/claim finding and add non-duplicate statement findings."""
    out: List[Contradiction] = []
    seen = set()
    if primary:
        out.append(primary)
        seen.add(primary.type)
    # Skip statement types that restate an already-queued claim-vs-evidence type.
    redundant_if = {
        "DECLARATION_OPPOSITE": {
            "CLAIM_OK_TDR_SAYS_NOK", "CLAIM_NOK_TDR_SAYS_OK",
            "CLAIM_OK_EVIDENCE_FAILS", "CLAIM_NOK_EVIDENCE_PASSES",
        },
    }
    for extra in extras:
        if extra.type in seen:
            continue
        blockers = redundant_if.get(extra.type, set())
        if blockers & seen:
            continue
        seen.add(extra.type)
        out.append(extra)
    return out
