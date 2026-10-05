"""Conservative scope extraction: explicit labels, never inferred product identity."""
import re

from app.qa.tdr_review_models import DocumentScope, ScopeStatus


_LABELS = {
    "project": r"project|projet",
    "component": r"component|composant",
    "product": r"product|produit",
    "variant": r"variant|variante|configuration",
    "supplier": r"supplier|fournisseur",
    "rfq": r"rfq|rfi|rfq/rfi",
    "document_reference": r"document\s+(?:reference|ref)|r[ée]f[ée]rence\s+document",
    "version": r"version|revision|r[ée]vision",
    "date": r"document\s+date|date",
    "language": r"language|langue",
    "confidentiality": r"confidentiality|confidentialit[ée]|classification",
}
_IDENTITY = ("project", "component", "product", "variant", "supplier", "rfq")


def extract_scope(text: str) -> tuple[DocumentScope, dict, list[str]]:
    values = {}
    sources = {}
    warnings = []
    for field, label in _LABELS.items():
        matches = list(re.finditer(
            rf"(?im)^\s*(?:{label})\s*[:=]\s*([^\n|;]{{1,160}})",
            text[:100_000],
        ))
        unique = {m.group(1).strip() for m in matches if m.group(1).strip()}
        if len(unique) == 1:
            value = next(iter(unique))
            maximum = 160 if field == "document_reference" else 120 if field in _IDENTITY or field == "confidentiality" else 80
            if len(value) <= maximum:
                values[field] = value
                sources[field] = {"method": "EXPLICIT_LABEL", "excerpt": matches[0].group(0).strip()}
            else:
                warnings.append(f"Scope label for {field} is too long; confirm the document metadata")
        elif len(unique) > 1:
            warnings.append(f"Conflicting document scope labels for {field}: manual confirmation required")
    return DocumentScope.model_validate(values), sources, warnings


def resolved_scope(text: str, confirmed: DocumentScope) -> dict:
    scope, sources, warnings = extract_scope(text)
    values = scope.model_dump()
    for name, value in confirmed.model_dump(exclude_none=True).items():
        if values[name] and _normalize(values[name]) != _normalize(value):
            warnings.append(f"User-confirmed {name} differs from extracted document label")
        values[name] = value
        sources[name] = {"method": "USER_CONFIRMED", "excerpt": None}
    return {
        "values": DocumentScope.model_validate(values).model_dump(),
        "sources": sources,
        "warnings": warnings,
    }


def _normalize(value: str) -> str:
    return re.sub(r"\s+", " ", value.strip()).casefold()


def compare_scopes(matrix: dict, evidence: dict) -> dict:
    mismatches = []
    shared = []
    missing = []
    for name in _IDENTITY:
        a, b = matrix["values"].get(name), evidence["values"].get(name)
        if a and b:
            shared.append(name)
            if _normalize(a) != _normalize(b):
                mismatches.append({"field": name, "matrix": a, "evidence": b})
        else:
            missing.append(name)
    warnings = matrix["warnings"] + evidence["warnings"]
    if mismatches:
        status = ScopeStatus.INCOMPATIBLE
    elif not any(name in shared for name in ("project", "component", "product", "variant")):
        status = ScopeStatus.INSUFFICIENT_INFORMATION
    elif missing or warnings:
        status = ScopeStatus.COMPATIBLE_WITH_RESERVATIONS
    else:
        status = ScopeStatus.COMPATIBLE
    return {
        "status": status.value,
        "sharedFields": shared,
        "missingFields": missing,
        "mismatches": mismatches,
        "warnings": warnings,
        "explanation": (
            "Document identity fields disagree; results cannot establish compliance."
            if mismatches else
            "No shared project/component/product/variant identity is available; confirm scope."
            if status == ScopeStatus.INSUFFICIENT_INFORMATION else
            "Compared identity fields agree. Missing metadata and document versions still require review."
        ),
        "matrix": matrix,
        "evidence": evidence,
    }
