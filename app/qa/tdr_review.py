"""Local proposal layer around the existing deterministic AERIS engine."""
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
import os
import re
from xml.etree.ElementTree import ParseError
from zipfile import BadZipFile

from lxml.etree import XMLSyntaxError
from openpyxl.utils.exceptions import InvalidFileException

from app.qa.aeris_crosscheck import crosscheck_analysis, report_to_dict
from app.qa.aeris_evidence import parse_evidence_bytes
from app.qa.conformity_analyzer import extract_conformity_data
from app.qa.tdr_review_models import AnalysisContext, DocumentScope, ProposalStatus, ScopeStatus
from app.qa.tdr_scope import compare_scopes, resolved_scope


ENGINE_VERSION = "tdr-review-1"
_PENDING = re.compile(
    r"\b(?:tbd|tbc|pending|ongoing|under\s+simulation|to\s+be\s+confirmed|"
    r"not\s+available|not\s+yet\s+(?:tested|validated)|en\s+cours|"
    r"[àa]\s+confirmer|non\s+disponible|[àa]\s+d[ée]finir|planned|"
    r"to\s+be\s+(?:tested|measured|validated)|"
    r"(?:will|shall|must)\s+(?:be\s+)?(?:measured|tested|simulated|validated|confirmed|calculated))\b", re.I,
)
_EVIDENCE_TYPES = [
    ("PENDING_STATEMENT", _PENDING),
    ("TEST_RESULT", re.compile(
        r"\b(?:measured|mesur[ée])\b.{0,60}\d|"
        r"\b(?:test\s+result|r[ée]sultat\s+d.essai)\b", re.I,
    )),
    ("SIMULATION", re.compile(r"\b(?:simulation|simulated|simul[ée])\b", re.I)),
    ("CALCULATION", re.compile(r"\b(?:calculated|calculation|calcul[ée])\b", re.I)),
    ("CERTIFICATE", re.compile(r"\b(?:certificate|certificat)\b", re.I)),
    ("DESIGN_DESCRIPTION", re.compile(r"\b(?:design|architecture|conception)\b", re.I)),
    ("DRAWING", re.compile(r"\b(?:drawing|dessin|sch[ée]ma)\b", re.I)),
    ("REFERENCE_ONLY", re.compile(r"\b(?:see|refer\s+to|voir)\s+(?:document|report|annex|rapport|annexe)\b", re.I)),
]


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def input_fingerprint(matrix: tuple[str, bytes], evidence: list[tuple[str, bytes]],
                      context: AnalysisContext) -> str:
    # Filenames affect references and scope declarations, so they are part of identity.
    parts = [ENGINE_VERSION, matrix[0], hashlib.sha256(matrix[1]).hexdigest(),
             json.dumps(context.model_dump(), sort_keys=True, ensure_ascii=False)]
    parts.extend(f"{name}\0{hashlib.sha256(content).hexdigest()}" for name, content in evidence)
    return hashlib.sha256("\0".join(parts).encode()).hexdigest()


def evidence_type(text: str) -> str:
    for name, pattern in _EVIDENCE_TYPES:
        if pattern.search(text):
            return name
    return "SUPPLIER_STATEMENT"


def identifier_method(item: dict, excerpt: str) -> str:
    def contains(identifier: str, text: str) -> bool:
        return bool(identifier and re.search(
            r"(?<!\w)" + re.escape(identifier) + r"(?!\w)", text, re.I,
        ))

    req_id = item["req_id"]
    if contains(req_id, excerpt):
        return "EXACT_ID"
    def normalize(value: str) -> str:
        return re.sub(r"\s*[-_]\s*", "-", value.strip()).upper()

    if contains(normalize(req_id), normalize(excerpt)):
        return "NORMALIZED_ID"
    if contains(item.get("external_reference") or "", excerpt):
        return "EXTERNAL_REFERENCE"
    return "CONTEXTUAL"


def propose_status(item: dict, scope_status: str, duplicate: bool,
                   extraction_failed: bool = False) -> tuple[str, list[str]]:
    reasons = []
    if scope_status == ScopeStatus.INCOMPATIBLE.value:
        return ProposalStatus.MAUVAIS_PERIMETRE.value, ["Document identity fields disagree"]
    if duplicate:
        return ProposalStatus.ANALYSE_MANUELLE_REQUISE.value, ["Duplicate requirement ID: review individual matrix rows"]
    if item["matrix_status"] == "NA":
        return ProposalStatus.NON_APPLICABLE_A_JUSTIFIER.value, ["Applicability requires human justification"]
    if extraction_failed and item["final_status"] in ("CONFORME", "MANQUANT", "PREUVE_INSUFFISANTE"):
        return ProposalStatus.ERREUR_EXTRACTION.value, ["Evidence extraction failed or is incomplete"]
    technical = item["final_status"]
    contradiction = item.get("declaration_alignment")
    if contradiction == "TDR_CONFLICT":
        return ProposalStatus.STATUT_CONTRADICTOIRE.value, ["Linked evidence disagrees; select authoritative evidence"]
    # A real numerical miss is not erased by a pending statement or textual OK.
    if technical in ("NON_CONFORME", "DEVIATION", "PARTIELLEMENT_CONFORME"):
        if technical == "DEVIATION" or item["matrix_status"] == "DEVIATION":
            status = ProposalStatus.DEVIATION_DECLAREE
        elif item["matrix_status"] in ("OK", "EMPTY"):
            status = ProposalStatus.DEVIATION_NON_DECLAREE
        else:
            status = ProposalStatus.NON_CONFORME_CONFIRME
        reasons.append(
            "At least one comparable numeric result fails a customer limit"
            if any(v["status"] == "NON_CONFORME" for v in item.get("condition_verdicts", []))
            else "An explicit supplier failure/deviation statement was found"
        )
        if technical == "PARTIELLEMENT_CONFORME":
            reasons.append("Other operating points pass; the requirement is not fully met")
        if item["confidence"] not in ("HIGH", "VERY_HIGH") or scope_status == ScopeStatus.INSUFFICIENT_INFORMATION.value:
            reasons.append("Failure needs validation: document scope or match confidence is insufficient")
            return ProposalStatus.ANALYSE_MANUELLE_REQUISE.value, reasons
        return status.value, reasons
    sources = item.get("evidence_sources", [])
    supplier_text = "\n".join([item.get("comment", "")] + [
        s.get("supplier_excerpt", s["excerpt"]) for s in sources
    ])
    if not sources:
        supplier_text += "\n" + item.get("evidence_excerpt", "")
    if _PENDING.search(supplier_text):
        return ProposalStatus.ANALYSE_EN_COURS_TBD.value, ["Pending or unavailable results are not compliance evidence"]
    if contradiction in ("OPPOSITE", "VALUE_MISMATCH", "WRONG_TARGET"):
        return ProposalStatus.STATUT_CONTRADICTOIRE.value, ["Matrix and evidence statements require reconciliation"]
    if technical == "MANQUANT":
        return ProposalStatus.AUCUNE_REPONSE_TROUVEE.value, ["No relevant TDR response was retrieved"]
    if technical == "PREUVE_INSUFFISANTE":
        return ProposalStatus.REPONSE_SANS_PREUVE.value, ["A statement alone cannot prove all acceptance criteria"]
    if scope_status != ScopeStatus.COMPATIBLE.value:
        reasons.append("Document identity is incomplete or has reservations")
    if item["confidence"] not in ("HIGH", "VERY_HIGH"):
        reasons.append("Low-confidence matching requires manual validation")
    if not any(identifier_method(item, s["excerpt"]) != "CONTEXTUAL" for s in sources):
        reasons.append("Contextual/similarity-only matching needs an explicit requirement link or human review")
    demonstrated = any(
        evidence_type(s.get("supplier_excerpt", s["excerpt"])) in ("TEST_RESULT", "SIMULATION", "CALCULATION", "CERTIFICATE")
        for s in sources
    )
    if not demonstrated:
        reasons.append("Numbers are a supplier statement, not identified test/calculation/simulation evidence")
    if reasons:
        return ProposalStatus.ANALYSE_MANUELLE_REQUISE.value, reasons
    return ProposalStatus.CONFORME_AVEC_PREUVE.value, ["Proposed compliance for the supported extracted limits; human validation required"]


def analyze_review(matrix_path: str, matrix: tuple[str, bytes],
                   evidence: list[tuple[str, bytes]], context: AnalysisContext) -> dict:
    if os.getenv("AERIS_ENABLE_EMBEDDINGS") == "1":
        raise ValueError("Saved TDR review requires local processing: disable AERIS_ENABLE_EMBEDDINGS")
    unknown = set(context.evidence_scopes) - {name for name, _ in evidence}
    if unknown:
        raise ValueError("Scope metadata names do not match evidence uploads: " + ", ".join(sorted(unknown)))
    try:
        analysis = extract_conformity_data(matrix_path, matrix[0])
    except (BadZipFile, InvalidFileException, ParseError, XMLSyntaxError, KeyError) as exc:
        raise ValueError(f"Cannot extract matrix requirements from {matrix[0]}: {exc}") from exc
    requirements = [r for r in analysis.items if r.is_requirement and (r.req_id or r.description)]
    if not requirements:
        raise ValueError("No requirements found; check matrix sheet and column detection")
    documents = [parse_evidence_bytes(name, content) for name, content in evidence]
    report = crosscheck_analysis(analysis, documents)
    payload = report_to_dict(report)
    if len(payload["items"]) != len(requirements):
        raise RuntimeError("Cross-check results do not match requirement rows")
    matrix_text = "\n".join(
        f"{r.req_id}\n{r.description}\n{r.comment}"
        for r in analysis.items if not r.is_requirement
    )
    matrix_scope = resolved_scope(matrix_text, context.matrix_scope)
    comparisons = {}
    for doc in documents:
        # Only the opening content is considered document metadata, not arbitrary
        # project labels quoted in later requirements or technical comparisons.
        text = "\n".join(c.text for c in doc.chunks[:12])
        resolved = resolved_scope(text, context.evidence_scopes.get(doc.file_name, DocumentScope()))
        comparisons[doc.file_name] = compare_scopes(matrix_scope, resolved)
    statuses = {c["status"] for c in comparisons.values()}
    global_scope = next(
        (s.value for s in (
            ScopeStatus.INCOMPATIBLE, ScopeStatus.INSUFFICIENT_INFORMATION,
            ScopeStatus.COMPATIBLE_WITH_RESERVATIONS, ScopeStatus.COMPATIBLE,
        ) if s.value in statuses),
        ScopeStatus.INSUFFICIENT_INFORMATION.value,
    )
    duplicates = Counter(r.req_id.strip().casefold() for r in requirements if r.req_id.strip())
    ingested_at = utc_now()
    payload["documents"] = [{
        "fileName": matrix[0], "sha256": hashlib.sha256(matrix[1]).hexdigest(),
        "bytes": len(matrix[1]), "role": "CONFORMITY_MATRIX", "classificationMethod": "USER_UPLOAD_ROLE",
        "uploadedAt": ingested_at, "extractionStatus": "EXTRACTED",
        "sheet": analysis.sheet_name, "originalRetained": False,
        "confidentiality": matrix_scope["values"]["confidentiality"],
    }] + [
        {
            "fileName": doc.file_name, "sha256": hashlib.sha256(content).hexdigest(),
            "bytes": len(content), "role": "TDR", "classificationMethod": "USER_UPLOAD_ROLE",
            "uploadedAt": ingested_at, "extractionStatus": "ERROR" if doc.parse_error else
            "PARTIAL" if doc.warnings else "EXTRACTED",
            "parseError": doc.parse_error or None, "warnings": doc.warnings,
            "locationsWithText": doc.page_count,
            "pagesWithText": doc.page_count if doc.kind == "pdf" else None,
            "slidesWithText": doc.page_count if doc.kind == "pptx" else None,
            "originalPageOrSlideCount": None, "originalRetained": False,
            "confidentiality": comparisons[doc.file_name]["evidence"]["values"]["confidentiality"],
        } for doc, (_, content) in zip(documents, evidence)
    ]
    payload["scope"] = {"status": global_scope, "comparisons": comparisons, "matrix": matrix_scope}
    payload["engineVersion"] = ENGINE_VERSION
    payload["createdAt"] = ingested_at
    payload["humanValidationRequired"] = True
    payload["automaticAcceptance"] = False
    payload["schemaMapping"] = {
        "sheet": analysis.sheet_name, "headerRow": analysis.header_row + 1,
        "columns": analysis.column_mapping, "columnIndexBase": 0, "editable": False,
    }
    payload["limitations"] = [
        "Local MVP: reviewer identity is self-declared, not authenticated.",
        "No automatic technical acceptance. Proposals and human decisions are separate.",
        "Scope extraction uses explicit labels/user confirmation; unidentified scope requires review.",
        "Versions/dates are traceability metadata; expiry and cross-document version applicability are not inferred.",
        "Range/tolerance/alternative criteria and unrecognized engineering properties require manual review.",
        "Document-role classification is user-selected, not an automatic classifier.",
        "Source passages are stored locally; originals are not retained. Protect the database with OS permissions/encryption.",
    ]
    source_index = {}
    for document in documents:
        for chunk in document.chunks:
            source_index.setdefault((chunk.file_name, chunk.location), []).append(chunk)
    for item, req in zip(payload["items"], requirements):
        key = f"{analysis.sheet_name}\0{req.row_index}\0{req.column_set}\0{req.req_id}"
        item["row_key"] = hashlib.sha256(key.encode()).hexdigest()[:24]
        item["matrix_source"] = {
            "sheet": analysis.sheet_name, "row": req.row_index + 1,
            "columnSet": req.column_set, "requirement_id_original": req.req_id,
            "requirement_id_normalized": re.sub(r"[\s_]+", "-", req.req_id.strip()).upper(),
        }
        item["description"] = req.description
        item["comment"] = req.comment
        item["external_reference"] = req.reference or None
        item["variant"] = matrix_scope["values"]["variant"]
        item["criticality"] = None
        item["human_decision"] = None
        item["review_revision"] = 0
        duplicate = bool(req.req_id and duplicates[req.req_id.strip().casefold()] > 1)
        item["duplicate_requirement_id"] = duplicate
        sources = item.get("evidence_sources", [])
        if not sources and item.get("evidence_excerpt"):
            sources = [{
                "file_name": item["evidence_file"], "location": item["evidence_location"],
                "excerpt": item["evidence_excerpt"],
            }]
        for source in sources:
            chunk = next((
                chunk for chunk in source_index.get((source["file_name"], source["location"]), [])
                if chunk.text.startswith(source["excerpt"])
            ), None)
            if chunk:
                source["excerpt"] = chunk.text
                source["supplier_excerpt"] = (
                    chunk.measurement_text if chunk.measurement_text is not None else chunk.text
                )
        item["evidence_sources"] = sources
        linked_files = {s["file_name"] for s in sources}
        scope_states = {
            comparisons[name]["status"] for name in linked_files if name in comparisons
        }
        item_scope = next(
            (s.value for s in (
                ScopeStatus.INCOMPATIBLE, ScopeStatus.INSUFFICIENT_INFORMATION,
                ScopeStatus.COMPATIBLE_WITH_RESERVATIONS, ScopeStatus.COMPATIBLE,
            ) if s.value in scope_states), global_scope,
        )
        if global_scope == ScopeStatus.INCOMPATIBLE.value:
            item_scope = ScopeStatus.INCOMPATIBLE.value
        extraction_failed = any(doc.parse_error or not doc.chunks for doc in documents)
        status, reasons = propose_status(item, item_scope, duplicate, extraction_failed)
        if status == ProposalStatus.CONFORME_AVEC_PREUVE.value and any(d.warnings for d in documents):
            status = ProposalStatus.ANALYSE_MANUELLE_REQUISE.value
            reasons = ["Partial extraction/OCR warnings: unread content may change the conclusion"]
        item["proposal_status"] = status
        item["scope_status"] = item_scope
        item["escalation_reasons"] = reasons
        item["evidence_candidates"] = [
            {**s, "evidence_type": evidence_type(s.get("supplier_excerpt", s["excerpt"])),
             "extraction_confidence": "UNVALIDATED",
             "match_method": identifier_method(item, s["excerpt"]),
             "match_factors": ["explicit requirement/reference link"]
             if identifier_method(item, s["excerpt"]) != "CONTEXTUAL"
             else ["lexical/property/unit matching; reviewer must confirm relevance"]}
            for s in sources[:3]
        ]
        item["expected_value"] = item["target"] or None
        item["supplier_value"] = item["supplier_result"] or None
        item["source_location"] = item["evidence_location"] or None
        item["human_acceptance"] = None
    payload["reviewSummary"] = dict(Counter(i["proposal_status"] for i in payload["items"]))
    return payload
