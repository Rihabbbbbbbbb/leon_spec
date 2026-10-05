"""Contracts for AI proposals and separately recorded human decisions."""
from enum import Enum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class ProposalStatus(str, Enum):
    CONFORME_AVEC_PREUVE = "CONFORME_AVEC_PREUVE"
    NON_CONFORME_CONFIRME = "NON_CONFORME_CONFIRME"
    DEVIATION_DECLAREE = "DEVIATION_DECLAREE"
    DEVIATION_NON_DECLAREE = "DEVIATION_NON_DECLAREE"
    STATUT_CONTRADICTOIRE = "STATUT_CONTRADICTOIRE"
    REPONSE_SANS_PREUVE = "REPONSE_SANS_PREUVE"
    AUCUNE_REPONSE_TROUVEE = "AUCUNE_REPONSE_TROUVEE"
    ANALYSE_EN_COURS_TBD = "ANALYSE_EN_COURS_TBD"
    NON_APPLICABLE_A_JUSTIFIER = "NON_APPLICABLE_A_JUSTIFIER"
    MAUVAIS_PERIMETRE = "MAUVAIS_PERIMETRE"
    ANALYSE_MANUELLE_REQUISE = "ANALYSE_MANUELLE_REQUISE"
    ERREUR_EXTRACTION = "ERREUR_EXTRACTION"


class ScopeStatus(str, Enum):
    COMPATIBLE = "COMPATIBLE"
    COMPATIBLE_WITH_RESERVATIONS = "COMPATIBLE_WITH_RESERVATIONS"
    INCOMPATIBLE = "INCOMPATIBLE"
    INSUFFICIENT_INFORMATION = "INSUFFICIENT_INFORMATION"


class DocumentScope(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    project: str | None = Field(default=None, max_length=120)
    component: str | None = Field(default=None, max_length=120)
    product: str | None = Field(default=None, max_length=120)
    variant: str | None = Field(default=None, max_length=120)
    supplier: str | None = Field(default=None, max_length=120)
    rfq: str | None = Field(default=None, max_length=120)
    document_reference: str | None = Field(default=None, max_length=160)
    version: str | None = Field(default=None, max_length=80)
    date: str | None = Field(default=None, max_length=80)
    language: str | None = Field(default=None, max_length=80)
    confidentiality: str | None = Field(default=None, max_length=120)

    @model_validator(mode="after")
    def blank_to_null(self):
        for name in type(self).model_fields:
            if getattr(self, name) == "":
                setattr(self, name, None)
        return self


class AnalysisContext(BaseModel):
    model_config = ConfigDict(extra="forbid")
    matrix_scope: DocumentScope = Field(default_factory=DocumentScope)
    evidence_scopes: dict[str, DocumentScope] = Field(default_factory=dict)


class ReviewDecision(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    reviewer: str = Field(min_length=1, max_length=120)
    action: Literal["VALIDATE", "CORRECT", "REJECT", "REQUEST_EVIDENCE", "MARK_NOT_APPLICABLE"]
    comment: str = Field(min_length=1, max_length=4000)
    corrected_status: ProposalStatus | None = None
    expected_revision: int = Field(ge=0)

    @model_validator(mode="after")
    def correction_requires_status(self):
        if self.action == "CORRECT" and self.corrected_status is None:
            raise ValueError("CORRECT requires corrected_status")
        if self.action != "CORRECT" and self.corrected_status is not None:
            raise ValueError("Only CORRECT accepts corrected_status")
        return self
