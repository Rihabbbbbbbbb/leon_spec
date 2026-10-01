"""
AERIS matrix ↔ TDR evidence engine.

Takes a supplier conformity matrix (already parsed by conformity_analyzer)
and one or more technical-evidence documents (TDR / PPT / PDF / DOCX / TXT),
then for each requirement:

  1. Extract quantitative constraints from the requirement + supplier comment.
  2. Retrieve the most relevant evidence passages (hybrid: REQ-ID, keywords,
     unit family, optional embeddings — never required).
  3. Extract measurements from those passages.
  4. Compare numbers deterministically.
  5. Reconcile the matrix declaration (OK / NOK / NA / DEVIATION) with
     the evidence verdict.
  6. Produce a quality synthesis (rate, coverage, top risks).

The LLM is OPTIONAL and never overrides a numeric verdict. If Azure OpenAI
is unavailable the engine still returns correct answers for every
quantitative requirement — that is the industrial-grade path used by
speXcompl.ai-style RFP checkers and the automotive RAG literature.

Statuses
--------
CONFORME                 evidence meets the target
NON_CONFORME             evidence misses the target
PARTIELLEMENT_CONFORME   some operating points pass, others fail
DEVIATION                gap confirmed and supplier/matrix already declared it
PREUVE_INSUFFISANTE      related text found, no comparable number
MANQUANT                 no evidence passage retrieved
NA                       matrix marked not applicable
"""
from __future__ import annotations

import re
from collections import Counter
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

from app.qa.aeris_constraints import (
    Constraint,
    Measurement,
    compare_constraint,
    extract_constraints,
    extract_measurements,
    operator_symbol,
    pretty_value,
    summarize_verdicts,
)
from app.qa.aeris_contradictions import (
    Contradiction,
    build_review_queue,
    classify_contradiction,
    contradiction_summary,
    contradictions_to_dicts,
    tdr_polarity,
)
from app.qa.aeris_incoherences import build_incoherences, incoherence_summary
from app.qa.aeris_statements import (
    StatementCrosswalk,
    build_crosswalk,
    build_deviation_register,
    build_tdr_ledger,
    coverage_map,
    crosswalk_summary,
    crosswalks_to_dicts,
    merge_contradictions,
    statement_contradictions,
)
from app.qa.aeris_evidence import EvidenceChunk, EvidenceDocument, parse_evidence_bytes
from app.qa.conformity_analyzer import (
    ConformityAnalysis,
    ConformityItem,
    analyze_conformity_matrix,
    analysis_to_dict,
)


# Tokens too generic to help matching.
_STOP = {
    "the", "and", "for", "with", "from", "that", "this", "shall", "must",
    "should", "will", "into", "onto", "than", "then", "when", "where",
    "les", "des", "une", "dans", "pour", "avec", "sur", "par", "est",
    "req", "requirement", "exigence", "value", "mode", "test",
}

_RISK_DOMAINS = [
    ("Power consumption", r"current|consommation|consumption|power|mA|amp"),
    ("Thermal performance", r"temp(?:erature)?|thermal|°C|degC"),
    ("Optical / LCF", r"lcf|attenuat|luminance|optical|contrast|color|chromatic"),
    ("Mechanical tolerances", r"mechanical|toleran|dimension|mm\b|fit|gap"),
    ("Functional safety", r"fusa|asil|iso\s*26262|safety"),
    ("Software / CPU", r"\bcpu\b|software|sw\b|load|aspice"),
    ("Hardware / EE", r"\bee\b|hardware|voltage|hw\b"),
    ("EMC", r"\bemc\b|immunity|emission|radiat"),
]


@dataclass
class CrossCheckItem:
    req_id: str
    description: str
    comment: str
    matrix_status: str
    evidence_status: str
    final_status: str
    coherence: str
    confidence: str
    target: str
    supplier_result: str
    gap: str
    evidence_location: str
    evidence_excerpt: str
    evidence_file: str
    rationale: str
    domain: str
    condition_verdicts: List[Dict] = field(default_factory=list)
    match_score: float = 0.0
    contradiction_type: str = ""
    contradiction_severity: str = ""
    recommended_action: str = ""
    tdr_polarity: str = "NONE"
    matrix_said: str = ""
    tdr_said: str = ""
    declaration_alignment: str = ""


@dataclass
class CrossCheckReport:
    matrix_file: str
    evidence_files: List[str]
    items: List[CrossCheckItem] = field(default_factory=list)
    summary: Dict = field(default_factory=dict)
    top_risks: List[Dict] = field(default_factory=list)
    matrix_analysis: Dict = field(default_factory=dict)
    evidence_meta: List[Dict] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)
    contradictions: List[Contradiction] = field(default_factory=list)
    contradiction_summary: Dict = field(default_factory=dict)
    crosswalk: List[StatementCrosswalk] = field(default_factory=list)
    crosswalk_summary: Dict = field(default_factory=dict)
    tdr_statements: List[Dict] = field(default_factory=list)
    deviations: List[Dict] = field(default_factory=list)
    coverage: Dict = field(default_factory=dict)
    incoherences: List[Dict] = field(default_factory=list)
    incoherence_summary: Dict = field(default_factory=dict)


def run_crosscheck(
    matrix_path: str,
    evidence_files: Sequence[Tuple[str, bytes]],
    matrix_file_name: str = "",
) -> CrossCheckReport:
    """
    Full pipeline: parse matrix → parse evidence → match → verdict → synthesis.

    Args:
        matrix_path: path to the ODS/XLSX/XLSM conformity matrix.
        evidence_files: list of (file_name, bytes) for TDR/PPT/PDF/…
        matrix_file_name: display name (defaults to basename).
    """
    analysis = analyze_conformity_matrix(matrix_path, matrix_file_name or Path(matrix_path).name)
    documents = [parse_evidence_bytes(name, blob) for name, blob in evidence_files]
    return crosscheck_analysis(analysis, documents)


def crosscheck_analysis(
    analysis: ConformityAnalysis,
    documents: List[EvidenceDocument],
) -> CrossCheckReport:
    chunks = [c for d in documents for c in d.chunks]
    report = CrossCheckReport(
        matrix_file=analysis.file_name,
        evidence_files=[d.file_name for d in documents],
        matrix_analysis=analysis_to_dict(analysis),
        evidence_meta=[
            {
                "fileName": d.file_name,
                "kind": d.kind,
                "chunks": len(d.chunks),
                "pagesOrSlides": d.page_count,
                "parseError": d.parse_error,
                "warnings": d.warnings,
            }
            for d in documents
        ],
    )
    for doc in documents:
        if doc.parse_error:
            report.notes.append(f"{doc.file_name}: {doc.parse_error}")
        report.notes.extend(f"{doc.file_name}: {warning}" for warning in doc.warnings)
    if not chunks:
        report.notes.append(
            "No extractable evidence in the uploaded files; check the parse errors "
            "and OCR warnings above. No numeric verdict can be established."
        )

    items: List[CrossCheckItem] = []
    found: List[Contradiction] = []
    walks: List[StatementCrosswalk] = []
    for req in analysis.items:
        if not req.req_id and not req.description:
            continue
        item, contras, walk = _check_one(req, chunks)
        items.append(item)
        found.extend(contras)
        walks.append(walk)

    report.items = items
    report.crosswalk = walks
    report.crosswalk_summary = crosswalk_summary(walks)
    report.tdr_statements = build_tdr_ledger(chunks)
    report.deviations = build_deviation_register(items, walks)
    report.coverage = coverage_map(items, walks, report.tdr_statements)
    report.contradictions = build_review_queue(found)
    report.contradiction_summary = contradiction_summary(report.contradictions)
    report.incoherences = build_incoherences(report)
    report.incoherence_summary = incoherence_summary(report.incoherences)
    report.summary = _build_summary(
        items, report.contradiction_summary, report.crosswalk_summary
    )
    report.summary["incoherences"] = report.incoherence_summary.get("total", 0)
    report.summary["bloquant"] = report.incoherence_summary.get("bloquant", 0)
    report.summary["majeur"] = report.incoherence_summary.get("majeur", 0)
    report.summary["aClarifier"] = report.incoherence_summary.get("aClarifier", 0)
    report.top_risks = _rank_risks(items)
    return report


def _check_one(
    req: ConformityItem, chunks: List[EvidenceChunk]
) -> Tuple[CrossCheckItem, List[Contradiction], StatementCrosswalk]:
    blob = " ".join(p for p in (req.description, req.comment, req.reference) if p)
    constraints = extract_constraints(req.description or "", source="requirement")
    if not constraints and req.comment:
        # Some matrices only put the measurable target in the comment.
        constraints = extract_constraints(req.comment, source="comment")

    ranked = _retrieve(req, constraints, chunks)
    best = ranked[0] if ranked else None
    evidence_text = best[0].text if best else ""
    evidence_loc = best[0].location if best else ""
    evidence_file = best[0].file_name if best else ""
    match_score = best[1] if best else 0.0

    measurements = _gather_measurements(ranked, constraints)

    condition_verdicts: List[Dict] = []
    evidence_status = "MANQUANT"
    target = ""
    supplier_result = ""
    gap = ""

    if req.conformity_category == "NA":
        evidence_status = "NA"
    elif constraints and measurements:
        all_v = []
        for c in constraints:
            vs = compare_constraint(c, measurements)
            all_v.extend(vs)
            if not target:
                target = f"{operator_symbol(c.operator)}{c.shown()}"
                if c.condition:
                    target += f" ({c.condition})"
        condition_verdicts = [
            {
                "condition": v.condition,
                "target": v.target,
                "measured": v.measured,
                "status": v.status,
                "gap": v.gap,
            }
            for v in all_v
        ]
        evidence_status = summarize_verdicts(all_v)
        if all_v:
            supplier_result = "; ".join(f"{v.condition + ': ' if v.condition else ''}{v.measured}" for v in all_v)
            failed = [v for v in all_v if v.status == "NON_CONFORME" and v.gap is not None]
            if failed:
                # Report the most severe miss (largest absolute gap).
                worst = max(failed, key=lambda v: abs(v.gap or 0))
                sign = "+" if (worst.gap or 0) > 0 else ""
                gap = f"{sign}{worst.gap:.4g} {worst.gap_unit}".strip()
    elif constraints and ranked:
        evidence_status = "PREUVE_INSUFFISANTE"
        c0 = constraints[0]
        target = f"{operator_symbol(c0.operator)}{c0.value:g} {c0.unit}"
    elif ranked and match_score >= 4:
        evidence_status = "PREUVE_INSUFFISANTE"
    elif req.conformity_category == "NA":
        evidence_status = "NA"
    else:
        evidence_status = "MANQUANT"

    # Declared deviation in the matrix or comment, with a confirmed numeric miss.
    declared_dev = (
        req.conformity_category == "DEVIATION"
        or bool(re.search(r"\bdeviation\b|\bd[ée]viation\b|\b[ée]cart\b", req.comment or "", re.I))
    )
    if declared_dev and evidence_status == "NON_CONFORME":
        evidence_status = "DEVIATION"

    final_status, coherence = _reconcile(req.conformity_category, evidence_status)
    confidence = _confidence(constraints, measurements, match_score, evidence_status)
    domain = _domain_of(blob)

    rationale = _rationale(
        req, evidence_status, final_status, coherence,
        constraints, measurements, condition_verdicts, match_score,
    )

    excerpt = ""
    if evidence_text:
        excerpt = evidence_text.strip()
        if len(excerpt) > 420:
            excerpt = excerpt[:417] + "…"

    polarity = tdr_polarity(evidence_text)
    walk = build_crosswalk(
        req_id=req.req_id,
        domain=domain,
        description=req.description or "",
        matrix_status=req.conformity_category,
        comment=req.comment or "",
        constraints=constraints,
        tdr_measurements=measurements,
        tdr_text=evidence_text,
        tdr_location=evidence_loc,
        confidence=confidence,
    )
    primary = classify_contradiction(
        req_id=req.req_id,
        matrix_status=req.conformity_category,
        evidence_status=evidence_status,
        coherence=coherence,
        target=target,
        supplier_result=supplier_result,
        comment=req.comment or "",
        evidence_excerpt=excerpt,
        evidence_location=evidence_loc,
        domain=domain,
        confidence=confidence,
        constraints=constraints,
        measurements=measurements,
    )
    contras = merge_contradictions(primary, statement_contradictions(walk))
    # Highest-severity finding drives the row-level badge.
    top = contras[0] if contras else None
    if len(contras) > 1:
        rank = {"critical": 3, "high": 2, "medium": 1, "info": 0}
        top = max(contras, key=lambda c: (rank.get(c.severity, 0), c.type))

    item = CrossCheckItem(
        req_id=req.req_id,
        description=(req.description or "")[:240],
        comment=(req.comment or "")[:240],
        matrix_status=req.conformity_category,
        evidence_status=evidence_status,
        final_status=final_status,
        coherence=coherence,
        confidence=confidence,
        target=pretty_value(target),
        supplier_result=pretty_value(supplier_result),
        gap=pretty_value(gap),
        evidence_location=evidence_loc,
        evidence_excerpt=excerpt,
        evidence_file=evidence_file,
        rationale=rationale,
        domain=domain,
        condition_verdicts=condition_verdicts,
        match_score=round(match_score, 3),
        contradiction_type=top.type if top else "",
        contradiction_severity=top.severity if top else "",
        recommended_action=top.action if top else "",
        tdr_polarity=polarity,
        matrix_said=walk.matrix_said,
        tdr_said=walk.tdr_said,
        declaration_alignment=walk.alignment,
    )
    return item, contras, walk


def _gather_measurements(
    ranked: List[Tuple[EvidenceChunk, float]],
    constraints: List[Constraint],
) -> List[Measurement]:
    """
    Collect the numbers that legitimately belong to this requirement.

    Without a guard, a requirement asking for ≤100 mA would absorb the
    3.1 A of an unrelated inrush slide simply because both are currents,
    and AERIS would report a fake "TDR self-contradiction". So:

    * the best-matching passage is always trusted;
    * a lower-ranked passage only contributes when it measures the same
      named quantity (standby current, contrast, luminance…), which is
      what lets one requirement be proven across several slides.
    """
    if not ranked:
        return []

    wanted = {c.quantity for c in constraints if c.quantity}
    families = {c.unit_family for c in constraints if c.unit_family}
    best_score = ranked[0][1]

    out: List[Measurement] = []
    for index, (chunk, score) in enumerate(ranked[:6]):
        found = extract_measurements(chunk.text, location=chunk.location)
        if index == 0:
            keep = found
        else:
            # Trop loin du meilleur score : ce n'est plus la même preuve.
            if score < max(1.4, best_score * 0.55):
                continue
            keep = [m for m in found if m.quantity and m.quantity in wanted]
        out.extend(keep)

    if wanted:
        # Une mesure explicitement rattachée à une autre grandeur de la
        # même famille (courant d'appel vs consommation) est écartée.
        out = [
            m for m in out
            if not m.quantity or m.quantity in wanted or m.unit_family not in families
        ]
    return out


def _retrieve(
    req: ConformityItem,
    constraints: List[Constraint],
    chunks: List[EvidenceChunk],
) -> List[Tuple[EvidenceChunk, float]]:
    if not chunks:
        return []

    query_text = " ".join(p for p in (req.req_id, req.description, req.comment) if p)
    q_tokens = _tokens(query_text)
    families = {c.unit_family for c in constraints}
    conditions = [c.condition.lower() for c in constraints if c.condition]

    scored: List[Tuple[EvidenceChunk, float]] = []
    for chunk in chunks:
        score = 0.0
        body = chunk.text
        low = body.lower()

        if req.req_id and re.search(r"(?<!\w)" + re.escape(req.req_id) + r"(?!\w)", body, re.I):
            score += 8.0

        c_tokens = _tokens(body)
        overlap = q_tokens & c_tokens
        if q_tokens:
            score += 4.0 * (len(overlap) / max(3, min(len(q_tokens), 12)))

        chunk_meas = extract_measurements(body, location=chunk.location)
        chunk_families = {m.unit_family for m in chunk_meas}
        qty = {c.quantity for c in constraints if c.quantity}
        chunk_qty = {m.quantity for m in chunk_meas if m.quantity}
        if qty and qty & chunk_qty:
            score += 4.0
        elif (qty and chunk_qty and not (qty & chunk_qty)
              and all(m.quantity and m.quantity not in families for m in chunk_meas)):
            score -= 4.0
        elif families & chunk_families:
            score += 2.5
        elif families and any(f in low for f in families):
            score += 0.8

        for cond in conditions:
            if cond and cond.lower() in low:
                score += 2.0
                break
            # Shared distinctive condition tokens (32°, 25°c, reduced…)
            cond_toks = set(re.findall(r"[a-z0-9°]+", cond.lower()))
            if cond_toks & set(re.findall(r"[a-z0-9°]+", low)):
                score += 1.2
                break

        # Domain keywords
        for _name, pat in _RISK_DOMAINS:
            if re.search(pat, query_text, re.I) and re.search(pat, body, re.I):
                score += 1.0
                break

        # A unit is a type, not an identity: PWM at 40 Hz is not proof of
        # a 40 Hz display refresh rate. Require a named quantity or lexical
        # evidence beyond the unit family before admitting a passage.
        has_anchor = bool(qty & chunk_qty or overlap or (
            req.req_id and re.search(r"(?<!\w)" + re.escape(req.req_id) + r"(?!\w)", body, re.I)
        ))
        if score >= 1.4 and has_anchor:
            scored.append((chunk, score))

    scored.sort(key=lambda x: x[1], reverse=True)

    # Optional embedding rerank of the top lexical hits — never required.
    top = scored[:12]
    reranked = _maybe_embed_rerank(query_text, top)
    return reranked[:6]


def _maybe_embed_rerank(
    query: str,
    ranked: List[Tuple[EvidenceChunk, float]],
) -> List[Tuple[EvidenceChunk, float]]:
    # Evidence can contain confidential supplier material. Do not send it to
    # an external embedding service as a side effect of an ordinary analysis.
    import os
    if os.getenv("AERIS_ENABLE_EMBEDDINGS") != "1":
        return ranked
    if len(ranked) < 2 or not query.strip():
        return ranked
    try:
        from app.embeddings import get_embedding, cosine_similarity
        qv = get_embedding(query[:2000])
        boosted: List[Tuple[EvidenceChunk, float]] = []
        for chunk, lex in ranked:
            ev = get_embedding(chunk.text[:2000])
            sim = cosine_similarity(qv, ev)
            boosted.append((chunk, lex + 2.0 * max(sim, 0.0)))
        boosted.sort(key=lambda x: x[1], reverse=True)
        return boosted
    except Exception:
        return ranked


def _tokens(text: str) -> set:
    raw = re.findall(r"[A-Za-zÀ-ÿ0-9]{3,}", (text or "").lower())
    return {t for t in raw if t not in _STOP}


def _reconcile(matrix_status: str, evidence_status: str) -> Tuple[str, str]:
    """
    Combine the supplier's matrix declaration with the evidence verdict.

    The evidence verdict is authoritative for the *technical* status.
    Coherence tells the reviewer whether the matrix can be trusted.
    """
    if evidence_status == "NA" or matrix_status == "NA":
        return "NA", "ALIGNED" if matrix_status == "NA" else "MATRIX_SAYS_NA"

    if evidence_status == "MANQUANT":
        return "MANQUANT", "UNVERIFIABLE"
    if evidence_status == "PREUVE_INSUFFISANTE":
        return "PREUVE_INSUFFISANTE", "UNVERIFIABLE"

    technical = evidence_status
    # Evidence confirmed a miss that the matrix already called NOK / DEVIATION.
    if technical in ("NON_CONFORME", "DEVIATION", "PARTIELLEMENT_CONFORME"):
        if matrix_status in ("NOK", "DEVIATION"):
            return technical, "ALIGNED"
        if matrix_status == "OK":
            return technical, "MATRIX_TOO_OPTIMISTIC"
        return technical, "MATRIX_SILENT"

    if technical == "CONFORME":
        if matrix_status == "OK":
            return "CONFORME", "ALIGNED"
        if matrix_status in ("NOK", "DEVIATION"):
            return "CONFORME", "MATRIX_TOO_PESSIMISTIC"
        return "CONFORME", "MATRIX_SILENT"

    return technical, "UNVERIFIABLE"


def _confidence(
    constraints: List[Constraint],
    measurements: List[Measurement],
    match_score: float,
    evidence_status: str,
) -> str:
    if evidence_status in ("CONFORME", "NON_CONFORME", "PARTIELLEMENT_CONFORME", "DEVIATION"):
        if constraints and measurements and match_score >= 5:
            return "VERY_HIGH"
        if constraints and measurements:
            return "HIGH"
        return "MEDIUM"
    if evidence_status == "PREUVE_INSUFFISANTE":
        return "LOW"
    if evidence_status == "MANQUANT":
        return "NONE"
    return "MEDIUM"


def _domain_of(text: str) -> str:
    for name, pat in _RISK_DOMAINS:
        if re.search(pat, text or "", re.I):
            return name
    return "Other"


def _rationale(
    req: ConformityItem,
    evidence_status: str,
    final_status: str,
    coherence: str,
    constraints: List[Constraint],
    measurements: List[Measurement],
    condition_verdicts: List[Dict],
    match_score: float,
) -> str:
    parts = []
    if constraints:
        c = constraints[0]
        parts.append(
            f"Target {operator_symbol(c.operator)}{c.value:g} {c.unit}"
            + (f" ({c.condition})" if c.condition else "")
            + "."
        )
    if condition_verdicts:
        bits = [f"{v['condition'] or 'nominal'}: {v['measured']} → {v['status']}" for v in condition_verdicts]
        parts.append("Evidence " + "; ".join(bits) + ".")
    elif measurements:
        parts.append("Measurements found but not comparable to the target unit.")
    elif evidence_status == "MANQUANT":
        parts.append("No matching passage in the TDR/PPT.")
    elif evidence_status == "PREUVE_INSUFFISANTE":
        parts.append("Related text found, but no number with a matching unit.")

    if coherence == "MATRIX_TOO_OPTIMISTIC":
        parts.append(f"Matrix is marked {req.conformity_category} while evidence is {evidence_status}.")
    elif coherence == "MATRIX_TOO_PESSIMISTIC":
        parts.append(f"Matrix is marked {req.conformity_category} while evidence meets the target.")
    elif coherence == "ALIGNED":
        parts.append("Matrix declaration matches the evidence.")
    return " ".join(parts)


def _build_summary(
    items: List[CrossCheckItem],
    contra: Optional[Dict] = None,
    walk: Optional[Dict] = None,
) -> Dict:
    counts = Counter(i.final_status for i in items)
    coherence = Counter(i.coherence for i in items)
    total = len(items)
    judged = sum(counts[s] for s in (
        "CONFORME", "NON_CONFORME", "PARTIELLEMENT_CONFORME", "DEVIATION",
    ))
    conforme = counts.get("CONFORME", 0)
    rate = round(100.0 * conforme / judged, 1) if judged else 0.0
    coverage = round(100.0 * judged / total, 1) if total else 0.0
    return {
        "total": total,
        "conforme": counts.get("CONFORME", 0),
        "nonConforme": counts.get("NON_CONFORME", 0),
        "partiel": counts.get("PARTIELLEMENT_CONFORME", 0),
        "deviation": counts.get("DEVIATION", 0),
        "preuveInsuffisante": counts.get("PREUVE_INSUFFISANTE", 0),
        "manquant": counts.get("MANQUANT", 0),
        "na": counts.get("NA", 0),
        "judged": judged,
        "conformityRate": rate,
        "evidenceCoverage": coverage,
        "matrixTooOptimistic": coherence.get("MATRIX_TOO_OPTIMISTIC", 0),
        "matrixTooPessimistic": coherence.get("MATRIX_TOO_PESSIMISTIC", 0),
        "aligned": coherence.get("ALIGNED", 0),
        "unverifiable": coherence.get("UNVERIFIABLE", 0),
        "contradictions": (contra or {}).get("total", 0),
        "contradictionsCritical": (contra or {}).get("critical", 0),
        "contradictionsHigh": (contra or {}).get("high", 0),
        "contradictionTypes": (contra or {}).get("byType", {}),
        "statementIncompliant": (walk or {}).get("incompliant", 0),
        "declarationOpposite": (walk or {}).get("opposite", 0),
        "valueMismatch": (walk or {}).get("valueMismatch", 0),
        "wrongTarget": (walk or {}).get("wrongTarget", 0),
        "tdrConflict": (walk or {}).get("tdrConflict", 0),
        "declarationsAligned": (walk or {}).get("aligned", 0),
    }


def _rank_risks(items: List[CrossCheckItem]) -> List[Dict]:
    risky_status = {"NON_CONFORME", "PARTIELLEMENT_CONFORME", "DEVIATION", "PREUVE_INSUFFISANTE"}
    by_domain: Dict[str, List[CrossCheckItem]] = {}
    for it in items:
        if it.final_status in risky_status:
            by_domain.setdefault(it.domain, []).append(it)

    ranked = []
    for domain, group in by_domain.items():
        hard = sum(1 for g in group if g.final_status in ("NON_CONFORME", "DEVIATION"))
        ranked.append({
            "domain": domain,
            "count": len(group),
            "hardFails": hard,
            "examples": [g.req_id for g in group[:4] if g.req_id],
        })
    ranked.sort(key=lambda r: (r["hardFails"], r["count"]), reverse=True)
    return ranked[:8]


def report_to_dict(report: CrossCheckReport) -> dict:
    return {
        "matrixFile": report.matrix_file,
        "evidenceFiles": report.evidence_files,
        "summary": report.summary,
        "topRisks": report.top_risks,
        "incoherences": report.incoherences,
        "incoherenceSummary": report.incoherence_summary,
        "contradictions": contradictions_to_dicts(report.contradictions),
        "contradictionSummary": report.contradiction_summary,
        "crosswalk": crosswalks_to_dicts(report.crosswalk),
        "crosswalkSummary": report.crosswalk_summary,
        "tdrStatements": report.tdr_statements,
        "deviations": report.deviations,
        "coverage": report.coverage,
        "items": [asdict(i) for i in report.items],
        "matrixAnalysis": {
            "fileName": report.matrix_analysis.get("fileName"),
            "sheetName": report.matrix_analysis.get("sheetName"),
            "totalRows": report.matrix_analysis.get("totalRows"),
            "stats": report.matrix_analysis.get("stats"),
            "summary": report.matrix_analysis.get("summary"),
            "okDeepFindings": report.matrix_analysis.get("okDeepFindings", []),
            "chartBase64": report.matrix_analysis.get("chartBase64", ""),
        },
        "evidenceMeta": report.evidence_meta,
        "notes": report.notes,
    }
