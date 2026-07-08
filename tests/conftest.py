"""
Fixtures partagées pour les tests du projet Leon Spec Validator.
"""
import pytest
import json
from pathlib import Path


@pytest.fixture
def sample_blocks():
    """Blocs de paragraphes simulés pour les tests de chunking."""
    return [
        {"source_file": "test.docx", "block_id": 0, "text": "Introduction"},
        {"source_file": "test.docx", "block_id": 1, "text": "Ce document décrit les spécifications."},
        {"source_file": "test.docx", "block_id": 2, "text": "1. Objet"},
        {"source_file": "test.docx", "block_id": 3, "text": "Définir les exigences du composant X."},
        {"source_file": "test.docx", "block_id": 4, "text": "2. Domaine d'application"},
        {"source_file": "test.docx", "block_id": 5, "text": "Applicable à tous les lots de production."},
        {"source_file": "test.docx", "block_id": 6, "text": "3. Exigences techniques"},
        {"source_file": "test.docx", "block_id": 7, "text": "La pièce doit résister à 500°C pendant 10 minutes."},
        {"source_file": "test.docx", "block_id": 8, "text": "La tolérance dimensionnelle est de ±0.01 mm."},
        {"source_file": "test.docx", "block_id": 9, "text": "4. Méthodes de vérification"},
        {"source_file": "test.docx", "block_id": 10, "text": "Contrôle visuel et mesure au micromètre."},
    ]


@pytest.fixture
def sample_blocks_short():
    """Quelques blocs courts."""
    return [
        {"source_file": "short.docx", "block_id": 0, "text": "Para 1"},
        {"source_file": "short.docx", "block_id": 1, "text": "Para 2"},
        {"source_file": "short.docx", "block_id": 2, "text": "Para 3"},
    ]


@pytest.fixture
def sample_embedding():
    """Un faux embedding pour les tests."""
    return [0.1] * 1536


@pytest.fixture
def sample_reference_index():
    """Un faux index de référence pour les tests de similarité."""
    return [
        {
            "source_file": "ref_template.docx",
            "chunk_id": 0,
            "text": "La spécification doit contenir un objet clair.",
            "embedding": [0.9] + [0.0] * 1535,
        },
        {
            "source_file": "ref_template.docx",
            "chunk_id": 1,
            "text": "Les exigences doivent être mesurables et vérifiables.",
            "embedding": [0.0] + [0.9] + [0.0] * 1534,
        },
        {
            "source_file": "ref_guide.docx",
            "chunk_id": 0,
            "text": "Éviter les formulations ambigües comme 'si possible'.",
            "embedding": [0.0] * 2 + [0.9] + [0.0] * 1533,
        },
    ]


@pytest.fixture
def temp_index_file(tmp_path, monkeypatch):
    """Redirige INDEX_PATH vers un fichier temporaire."""
    temp_index = tmp_path / "reference_index.json"
    import app.config
    monkeypatch.setattr(app.config, "INDEX_PATH", temp_index)
    # Also patch in ingest_refs
    import app.ingest_refs
    monkeypatch.setattr(app.ingest_refs, "INDEX_PATH", temp_index)
    return temp_index
