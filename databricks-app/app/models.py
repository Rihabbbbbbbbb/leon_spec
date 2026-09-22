"""
Modèles de données Pydantic pour l'API de validation de spécifications.
"""
try:
    from pydantic import BaseModel, Field
except ImportError:
    BaseModel = object
    Field = None
from typing import List, Optional, Dict, Any
from datetime import datetime, timezone


class ChunkRef(BaseModel):
    """Un chunk de document de référence avec son embedding."""
    source_file: str
    chunk_id: int
    text: str
    embedding: List[float]


class ValidationRequest(BaseModel):
    """Requête de validation d'une spécification."""
    document_text: str = Field(..., description="Texte complet de la spécification à valider")
    filename: Optional[str] = Field(None, description="Nom du fichier source (optionnel)")


class SectionFeedback(BaseModel):
    """Retour sur une section spécifique (format legacy)."""
    section_name: str = Field("", description="Nom de la section concernée")
    status: str = Field("ok", description="ok, warning, ou error")
    message: str = Field("", description="Message détaillé")
    matched_refs: List[str] = Field(default_factory=list, description="Références correspondantes")


class EvidenceRef(BaseModel):
    """Référence à un chunk du corpus pour justifier un finding."""
    source_reference_document: str = ""
    source_section_or_chunk_id: str = ""
    user_document_excerpt_or_location: str = ""
    support: str = ""


class MajorFinding(BaseModel):
    """Un constat majeur de validation avec évidence."""
    type: str = Field("other", description="missing_section | weak_section | ambiguous_requirement | unverifiable_requirement | placeholder_detected | missing_traceability | validation_gap | misplaced_requirement | other")
    severity: str = Field("info", description="info | warning | error")
    location: str = ""
    status: str = Field("cannot_verify", description="present | present_but_weak | present_but_incomplete | absent | cannot_verify")
    finding: str = ""
    why_it_matters: str = ""
    evidence: List[EvidenceRef] = Field(default_factory=list)
    suggested_fix: str = ""


class ScoreBreakdown(BaseModel):
    """Scores détaillés par axe de validation."""
    structure: float = Field(0.0, ge=0.0, le=1.0)
    requirements_quality: float = Field(0.0, ge=0.0, le=1.0)
    traceability: float = Field(0.0, ge=0.0, le=1.0)
    # [NOTE: validation_readiness is DISABLED — always returns 0.5 default; re-enable when validation plans needed]
    validation_readiness: float = Field(0.0, ge=0.0, le=1.0)
    template_cleanliness: float = Field(0.0, ge=0.0, le=1.0)
    mechatronics_fitness: float = Field(0.0, ge=0.0, le=1.0)


class RequirementIssue(BaseModel):
    """A per-requirement finding for actionable engineering feedback."""
    req_id: str = ""
    req_description: str = ""
    issue_type: str = Field("other", description="missing_id | placeholder_in_desc | missing_input_ref | missing_validation | ambiguous | unverifiable | weak_description | indirect_requirement | template_artifact | other")
    severity: str = Field("warning", description="info | warning | error")
    finding: str = ""
    suggested_fix: str = ""
    location: str = ""  # e.g., "row 15 in table 3"
    section: str = ""   # e.g., "FUNCTIONAL REQUIREMENTS > Fct_Detect_ASU_Status"


class LeonValidationResponse(BaseModel):
    """Réponse complète de validation LEON (format strict)."""
    document_name: str = ""
    global_verdict: str = Field("CANNOT_VERIFY", description="GOOD | ACCEPTABLE_WITH_FIXES | NOT_RELIABLE | NON_COMPLIANT | CANNOT_VERIFY")
    overall_assessment: str = ""
    scores: ScoreBreakdown = Field(default_factory=ScoreBreakdown)
    major_findings: List[MajorFinding] = Field(default_factory=list)
    missing_sections: List[str] = Field(default_factory=list)
    weak_sections: List[str] = Field(default_factory=list)
    ambiguous_phrases: List[str] = Field(default_factory=list)
    placeholder_or_template_artifacts: List[str] = Field(default_factory=list)
    recommendations: List[str] = Field(default_factory=list)
    validated_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    # Per-requirement findings (actionable for engineers)
    requirement_issues: List[RequirementIssue] = Field(default_factory=list)
    # Section-by-section coverage summary
    section_summary: List[Dict[str, Any]] = Field(default_factory=list)
    # Human-readable engineering report (plain text, readable without tools)
    human_report: str = ""
    # Image/diagram analysis summary
    image_analysis: Dict[str, Any] = Field(default_factory=dict)
    # Resolved external standards (beStandard integration)
    resolved_standards: Dict[str, Any] = Field(default_factory=dict)
    # Detected standard codes in the document
    standard_codes_detected: List[str] = Field(default_factory=list)
    # Legacy fields for backward compatibility
    overall_score: float = Field(0.0, ge=0.0, le=1.0)
    summary: str = ""
    sections: List[SectionFeedback] = Field(default_factory=list)


# Alias backward-compatible pour l'ancienne API
ValidationResponse = LeonValidationResponse


class IndexStatus(BaseModel):
    """Statut de l'index de référence."""
    indexed: bool = False
    total_chunks: int = 0
    source_files: List[str] = Field(default_factory=list)
    last_updated: Optional[str] = None


class HealthResponse(BaseModel):
    """Réponse du health check."""
    status: str = "ok"
    version: str = "1.0.0"
    azure_configured: bool = False
    index_status: IndexStatus = Field(default_factory=IndexStatus)
