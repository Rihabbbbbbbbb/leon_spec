"""
Tests d'intégration pour l'API FastAPI (sans appels Azure réels).
"""
import json
import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock
from fastapi.testclient import TestClient

# Patcher les variables d'environnement avant d'importer l'app
import os
os.environ["AZURE_OPENAI_ENDPOINT"] = "https://test.openai.azure.com/openai/v1/"
os.environ["AZURE_OPENAI_API_KEY"] = "test-key"
os.environ["AZURE_OPENAI_LLM_DEPLOYMENT"] = "gpt-4o"
os.environ["AZURE_OPENAI_EMBEDDING_DEPLOYMENT"] = "text-embedding-3-large"

from app.main import app

client = TestClient(app)


class TestHealthCheck:
    def test_health_ok(self):
        response = client.get("/")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "ok"
        assert data["version"] == "1.0.0"
        assert "index_status" in data

    def test_status_endpoint(self):
        response = client.get("/status")
        assert response.status_code == 200
        data = response.json()
        assert "indexed" in data
        assert "total_chunks" in data


class TestValidateWithoutIndex:
    @patch("app.main.load_reference_index")
    def test_validate_no_index_returns_412(self, mock_load):
        """Sans index, la validation doit échouer avec 412."""
        mock_load.return_value = []  # Index vide
        response = client.post(
            "/validate",
            data={"text": "Une spécification test."},
        )
        assert response.status_code == 412
        assert "index" in response.json()["detail"].lower()

    def test_validate_empty_body_returns_400(self):
        response = client.post("/validate")
        assert response.status_code == 400


class TestValidateWithMockedServices:
    @pytest.fixture(autouse=True)
    def setup_mocks(self):
        """Mocker les appels Azure pour les tests d'intégration."""
        self.mock_embedding = patch("app.main.get_embedding").start()
        self.mock_llm = patch("app.main.call_llm").start()
        self.mock_load = patch("app.main.load_reference_index").start()
        yield
        patch.stopall()

    def test_validate_text_with_mocked_llm(self, tmp_path, monkeypatch):
        """Validation complète avec tous les services mockés (format LEON)."""
        # Configurer les mocks
        self.mock_load.return_value = [
            {
                "source_file": "ref.docx",
                "chunk_id": 0,
                "text": "Les spécifications doivent être mesurables.",
                "embedding": [0.5] * 1536,
            }
        ]
        self.mock_embedding.return_value = [0.5] * 1536

        # Format LEON response
        mock_llm_response = json.dumps({
            "document_name": "test_spec",
            "global_verdict": "ACCEPTABLE_WITH_FIXES",
            "overall_assessment": "Bonne spécification avec quelques points mineurs à améliorer.",
            "scores": {
                "structure": 0.8,
                "requirements_quality": 0.7,
                "traceability": 0.6,
                "validation_readiness": 0.5,
                "template_cleanliness": 0.4,
                "mechatronics_fitness": 0.8,
            },
            "major_findings": [
                {
                    "type": "ambiguous_requirement",
                    "severity": "warning",
                    "location": "Exigences",
                    "status": "present_but_weak",
                    "finding": "Certaines exigences manquent de seuils mesurables.",
                    "why_it_matters": "Empêche la vérification objective.",
                    "evidence": [
                        {
                            "source_reference_document": "ref.docx",
                            "source_section_or_chunk_id": "0",
                            "support": "Les spécifications doivent être mesurables.",
                        }
                    ],
                    "suggested_fix": "Ajouter des seuils quantitatifs.",
                }
            ],
            "missing_sections": ["Méthodes de vérification"],
            "weak_sections": ["Exigences"],
            "ambiguous_phrases": ["si possible", "environ"],
            "placeholder_or_template_artifacts": [],
            "recommendations": [
                "Ajouter des seuils quantitatifs pour chaque exigence.",
                "Remplacer 'si possible' par une condition précise.",
            ],
        })
        self.mock_llm.return_value = mock_llm_response

        # Appeler l'API
        response = client.post(
            "/validate",
            data={"text": "Spécification du composant X. Doit être résistant si possible."},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["global_verdict"] == "ACCEPTABLE_WITH_FIXES"
        # Score is blended from rubric (0.5 default) + LLM (0.7) -> 0.4*0.5 + 0.6*0.7 = 0.62
        # But rubric may give 0.5 if no req rows; allow range
        assert 0.4 <= data["scores"]["requirements_quality"] <= 0.8
        # May have 1+ findings (LLM + deterministic merge)
        assert len(data["major_findings"]) >= 1
        assert data["major_findings"][0]["severity"] == "warning"
        assert "si possible" in data["ambiguous_phrases"]
        assert len(data["recommendations"]) == 2
        assert data["document_name"] == "test_spec"
        # Legacy fields still populated
        assert "overall_score" in data
        assert data["sections"][0]["section_name"] == "Exigences"

    def test_validate_llm_json_parse_error_handled(self):
        """Si le LLM renvoie du JSON invalide, on gère gracieusement (format LEON)."""
        self.mock_load.return_value = [
            {
                "source_file": "ref.docx",
                "chunk_id": 0,
                "text": "Référence.",
                "embedding": [0.5] * 1536,
            }
        ]
        self.mock_embedding.return_value = [0.5] * 1536
        self.mock_llm.return_value = "Ceci n'est pas du JSON valide !!"

        response = client.post(
            "/validate",
            data={"text": "Test spec."},
        )

        assert response.status_code == 200
        data = response.json()
        # Fallback : verdict CANNOT_VERIFY, scores à 0
        assert data["global_verdict"] == "CANNOT_VERIFY"
        assert data["scores"]["structure"] == 0.0
        assert len(data["recommendations"]) >= 1
        assert "LLM parse error" in data["recommendations"][0]

    def test_validate_no_text_or_file_returns_400(self):
        response = client.post("/validate", data={})
        assert response.status_code == 400


class TestDeterministicChecks:
    """Tests for the deterministic pre-validation checks."""
    
    def test_placeholder_detection(self):
        """Placeholders should be detected deterministically."""
        from app.deterministic_checks import run_deterministic_checks
        
        user_text = """
        PURPOSE
        The system shall <<insert requirement here>> operate correctly.
        Materials: << Le % on the 'green' materials is given by PMXP >>
        TBD: validation method
        n = xxx samples
        """
        result = run_deterministic_checks(user_text, [], 
            [{"text": p, "block_type": "paragraph", "heading_level": None,
              "section_context": "", "is_template": False}
             for p in user_text.split("\n") if p.strip()])
        
        assert result["stats"]["placeholder_count"] >= 3  # <<>>, TBD, xxx
        assert len(result["findings"]) >= 1
        finding_types = {f["type"] for f in result["findings"]}
        assert "placeholder_detected" in finding_types
    
    def test_placeholder_finding_has_user_excerpt(self):
        """Deterministic placeholder findings must include user document excerpt."""
        from app.deterministic_checks import run_deterministic_checks
        
        user_text = "Materials: <<TBD by supplier>>"
        result = run_deterministic_checks(user_text, [],
            [{"text": p, "block_type": "paragraph", "heading_level": None,
              "section_context": "", "is_template": False}
             for p in user_text.split("\n") if p.strip()])
        
        for f in result["findings"]:
            assert "user_document_excerpt" in f
            assert len(f["user_document_excerpt"]) > 0
    
    def test_rubric_scores_in_range(self):
        """Rubric scores should be between 0 and 1."""
        from app.deterministic_checks import run_deterministic_checks
        
        user_text = "PURPOSE\nSCOPE\nREQUIREMENTS\nFUNCTIONAL REQUIREMENTS"
        result = run_deterministic_checks(user_text, [],
            [{"text": p, "block_type": "paragraph", "heading_level": None,
              "section_context": "", "is_template": False}
             for p in user_text.split("\n") if p.strip()])
        
        for axis, info in result["rubric_scores"].items():
            assert 0.0 <= info["score"] <= 1.0, f"{axis} score {info['score']} out of range"
            assert len(info["rationale"]) > 0, f"{axis} missing rationale"
    
    def test_structure_score_improves_with_more_sections(self):
        """More mandatory sections should improve structure score."""
        from app.deterministic_checks import run_deterministic_checks
        
        minimal = "PURPOSE AND SCOPE"
        result_min = run_deterministic_checks(minimal, [],
            [{"text": p, "block_type": "paragraph", "heading_level": None,
              "section_context": "", "is_template": False}
             for p in minimal.split("\n") if p.strip()])
        
        # Use longer section names (>5 chars) for the regex to match
        richer = ("PURPOSE AND SCOPE DEFINITION\n"
                  "SYSTEM DEVELOPMENT CONTEXT\n"
                  "GENERAL DESCRIPTION OF THE SYSTEM\n"
                  "SYSTEM ROLES AND RESPONSIBILITIES\n"
                  "PHYSICAL SYSTEM ARCHITECTURE\n"
                  "SYSTEM DIVERSITY HANDLING\n"
                  "QUOTED REFERENCE DOCUMENTS\n"
                  "APPLICABLE DOCUMENTS LIST\n"
                  "TERMINOLOGY GLOSSARY\n"
                  "ACRONYMS DEFINITIONS\n"
                  "FUNCTIONAL REQUIREMENTS\n"
                  "PERFORMANCE REQUIREMENTS\n"
                  "EXTERNAL INTERFACES SPEC\n"
                  "OPERATIONAL REQUIREMENTS\n"
                  "MISSION PROFILE DEFINITION\n"
                  "RAMS REQUIREMENTS SAFETY\n"
                  "SAFETY REQUIREMENTS ISO\n"
                  "CONSTRAINT REQUIREMENTS\n"
                  "INTEGRATION AND VALIDATION\n"
                  "DEMONSTRATION OF COMPLIANCE")
        result_rich = run_deterministic_checks(richer, [],
            [{"text": p, "block_type": "paragraph", "heading_level": None,
              "section_context": "", "is_template": False}
             for p in richer.split("\n") if p.strip()])
        
        assert result_rich["rubric_scores"]["structure"]["score"] >= \
               result_min["rubric_scores"]["structure"]["score"]
    
    def test_template_cleanliness_worse_with_placeholders(self):
        """Template cleanliness should decrease with more placeholders."""
        from app.deterministic_checks import run_deterministic_checks
        
        clean = "PURPOSE\nThe system operates at 12V."
        result_clean = run_deterministic_checks(clean, [],
            [{"text": p, "block_type": "paragraph", "heading_level": None,
              "section_context": "", "is_template": False}
             for p in clean.split("\n") if p.strip()])
        
        dirty = "PURPOSE\n<<TBD>> <<insert>> TBD XXX <<choose one>>"
        result_dirty = run_deterministic_checks(dirty, [],
            [{"text": p, "block_type": "paragraph", "heading_level": None,
              "section_context": "", "is_template": False}
             for p in dirty.split("\n") if p.strip()])
        
        assert result_clean["rubric_scores"]["template_cleanliness"]["score"] > \
               result_dirty["rubric_scores"]["template_cleanliness"]["score"]


class TestEvidenceVerification:
    """Tests for evidence integrity in validation output."""
    
    @patch("app.main.load_reference_index")
    @patch("app.main.call_llm")
    @patch("app.main.get_embedding")
    def test_findings_contain_user_document_field(self, mock_emb, mock_llm, mock_load):
        """Every finding must have a user_document_excerpt_or_location field."""
        mock_load.return_value = [{
            "source_file": "ref.docx", "chunk_id": 0,
            "text": "Les exigences doivent être mesurables.",
            "embedding": [0.5] * 1536,
        }]
        mock_emb.return_value = [0.5] * 1536
        
        # LLM response with explicit user document excerpt
        mock_llm.return_value = json.dumps({
            "document_name": "test",
            "global_verdict": "ACCEPTABLE_WITH_FIXES",
            "overall_assessment": "OK",
            "scores": {"structure": 0.8, "requirements_quality": 0.7, "traceability": 0.6,
                       "validation_readiness": 0.5, "template_cleanliness": 0.5, "mechatronics_fitness": 0.7},
            "major_findings": [{
                "type": "placeholder_detected",
                "severity": "error",
                "location": "§5.5.3.2",
                "status": "present",
                "finding": "Template placeholder found",
                "why_it_matters": "Incomplete content",
                "evidence": [{
                    "source_reference_document": "Component_or_Part_Specification_Template 1.docx",
                    "source_section_or_chunk_id": "42",
                    "user_document_excerpt_or_location": "Materials: << Le % on the green materials >>",
                    "support": "Template requires finalized content without placeholders."
                }],
                "suggested_fix": "Remove placeholder."
            }],
            "missing_sections": [], "weak_sections": [], "ambiguous_phrases": [],
            "placeholder_or_template_artifacts": [], "recommendations": [],
        })
        
        response = client.post("/validate", data={"text": "Materials: << Le % on the green materials >>"})
        assert response.status_code == 200
        data = response.json()
        
        for finding in data.get("major_findings", []):
            for ev in finding.get("evidence", []):
                assert "user_document_excerpt_or_location" in ev, \
                    "Evidence missing user_document_excerpt_or_location field"
    
    @patch("app.main.load_reference_index")
    @patch("app.main.call_llm")
    @patch("app.main.get_embedding")
    def test_template_not_cited_as_user_evidence(self, mock_emb, mock_llm, mock_load):
        """source_reference_document must NOT be confused with user document."""
        mock_load.return_value = [{
            "source_file": "ref.docx", "chunk_id": 0,
            "text": "Reference rule text.",
            "embedding": [0.5] * 1536,
        }]
        mock_emb.return_value = [0.5] * 1536
        
        mock_llm.return_value = json.dumps({
            "document_name": "user_spec",
            "global_verdict": "ACCEPTABLE_WITH_FIXES",
            "overall_assessment": "OK",
            "scores": {"structure": 0.8, "requirements_quality": 0.7, "traceability": 0.6,
                       "validation_readiness": 0.5, "template_cleanliness": 0.5, "mechatronics_fitness": 0.7},
            "major_findings": [{
                "type": "missing_section",
                "severity": "warning",
                "location": "Test",
                "status": "absent",
                "finding": "Test finding",
                "why_it_matters": "Test",
                "evidence": [{
                    "source_reference_document": "Component_or_Part_Specification_Writing_guide 1.docx",
                    "source_section_or_chunk_id": "5",
                    "user_document_excerpt_or_location": "Actual text from user document",
                    "support": "Guide rule text"
                }],
                "suggested_fix": "Fix it"
            }],
            "missing_sections": [], "weak_sections": [], "ambiguous_phrases": [],
            "placeholder_or_template_artifacts": [], "recommendations": [],
        })
        
        response = client.post("/validate", data={"text": "Actual text from user document"})
        assert response.status_code == 200
        data = response.json()
        
        for finding in data.get("major_findings", []):
            for ev in finding.get("evidence", []):
                src = ev.get("source_reference_document", "")
                # The source_reference_document should be the REFERENCE doc, not the user doc
                # It's OK if it's the template/guide, but user excerpt should be from user doc
                excerpt = ev.get("user_document_excerpt_or_location", "")
                assert len(excerpt) > 0
                # source_reference_document should contain "Template" or "guide" or "Writing"
                assert any(marker in src.lower() for marker in ["template", "guide", "writing", "deterministic"])
    
    def test_find_text_location(self):
        """find_text_location should correctly locate text in user document."""
        from app.deterministic_checks import find_text_location
        
        user_text = "Line 1 here\nLine 2 here\nLine 3 with specific content here\nLine 4"
        
        # Use excerpts >= 10 chars (function requires minimum 10 chars)
        loc = find_text_location(user_text, "specific content here")
        assert loc is not None, f"Should find 'specific content here' in text"
        assert "3" in loc  # Line 3
        
        loc = find_text_location(user_text, "Line 1 here")
        assert loc is not None
        assert "1" in loc

        loc = find_text_location(user_text, "nonexistent text xyz")
        assert loc is None
