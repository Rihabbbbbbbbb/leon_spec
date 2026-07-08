"""
Tests unitaires pour le module d'ingestion de références.
"""
import json
import pytest
from unittest.mock import patch, MagicMock
from app.ingest_refs import (
    extract_docx_text,
    load_reference_index,
    save_reference_index,
    build_index,
    ingest_reference_doc,
)


class TestExtractDocxText:
    def test_extract_paragraphs(self):
        """Simule l'extraction de paragraphes d'un .docx."""
        mock_doc = MagicMock()
        mock_doc.paragraphs = [
            MagicMock(text="  Premier paragraphe  "),
            MagicMock(text=""),
            MagicMock(text="  Second paragraphe  "),
        ]
        mock_doc.tables = []

        with patch("app.ingest_refs.Document", return_value=mock_doc):
            blocks = extract_docx_text("fake.docx")

        assert len(blocks) == 2
        assert blocks[0]["text"] == "Premier paragraphe"
        assert blocks[0]["source_file"] == "fake.docx"
        assert blocks[1]["text"] == "Second paragraphe"

    def test_extract_tables(self):
        """Simule l'extraction de tableaux."""
        mock_doc = MagicMock()
        mock_doc.paragraphs = []
        mock_cell_1 = MagicMock(text="Cellule A")
        mock_cell_2 = MagicMock(text="Cellule B")
        mock_row = MagicMock(cells=[mock_cell_1, mock_cell_2])
        mock_table = MagicMock(rows=[mock_row])
        mock_doc.tables = [mock_table]

        with patch("app.ingest_refs.Document", return_value=mock_doc):
            blocks = extract_docx_text("fake.docx")

        assert len(blocks) == 1
        assert "Cellule A" in blocks[0]["text"]
        assert "Cellule B" in blocks[0]["text"]

    def test_extract_empty_document(self):
        """Document vide = aucun bloc."""
        mock_doc = MagicMock()
        mock_doc.paragraphs = []
        mock_doc.tables = []

        with patch("app.ingest_refs.Document", return_value=mock_doc):
            blocks = extract_docx_text("fake.docx")

        assert blocks == []


class TestLoadSaveIndex:
    def test_load_nonexistent_index(self, temp_index_file):
        """Charger un index qui n'existe pas retourne une liste vide."""
        # temp_index_file n'existe pas encore
        index = load_reference_index()
        assert index == []

    def test_save_and_load_index(self, temp_index_file):
        """Sauvegarder puis charger l'index."""
        data = [
            {"source_file": "test.docx", "chunk_id": 0, "text": "Hello", "embedding": [1.0, 2.0]},
        ]
        save_reference_index(data)
        assert temp_index_file.exists()

        loaded = load_reference_index()
        assert len(loaded) == 1
        assert loaded[0]["text"] == "Hello"
        assert loaded[0]["embedding"] == [1.0, 2.0]

    def test_save_overwrites_index(self, temp_index_file):
        """Sauvegarder plusieurs fois écrase l'index."""
        save_reference_index([{"chunk_id": 1}])
        save_reference_index([{"chunk_id": 2}])
        loaded = load_reference_index()
        assert len(loaded) == 1
        assert loaded[0]["chunk_id"] == 2


class TestIngestReferenceDoc:
    @patch("app.ingest_refs.get_embeddings_batch")
    def test_ingest_flow(self, mock_embeddings, temp_index_file):
        """Test du flux complet ingest_reference_doc avec mock embedding."""
        # Mock : retourne des faux embeddings
        mock_embeddings.return_value = [[0.5, 0.5], [0.6, 0.6]]

        # Mock du document
        mock_doc = MagicMock()
        mock_doc.paragraphs = [
            MagicMock(text="Para 1"),
            MagicMock(text="Para 2"),
            MagicMock(text="Para 3"),
            MagicMock(text="Para 4"),
            MagicMock(text="Para 5"),
            MagicMock(text="Para 6"),
        ]
        mock_doc.tables = []

        with patch("app.ingest_refs.Document", return_value=mock_doc):
            result = ingest_reference_doc("test.docx", chunk_strategy="paragraphs", chunk_size=3)

        assert len(result) == 2  # 6 paragraphes / 3 = 2 chunks
        assert result[0]["source_file"] == "test.docx"
        assert result[0]["chunk_id"] == 0
        assert "embedding" in result[0]
        assert result[1]["chunk_id"] == 1


class TestBuildIndex:
    @patch("app.ingest_refs.get_embeddings_batch")
    def test_build_index_no_docx_files(self, mock_embeddings, tmp_path, monkeypatch, temp_index_file):
        """Erreur si aucun .docx dans le dossier de refs."""
        import app.ingest_refs
        monkeypatch.setattr(app.ingest_refs, "REFS_DIR", tmp_path)

        with pytest.raises(FileNotFoundError, match="Aucun fichier .docx"):
            build_index(refs_folder=str(tmp_path))

    @patch("app.ingest_refs.get_embeddings_batch")
    def test_build_index_with_files(self, mock_embeddings, tmp_path, monkeypatch, temp_index_file):
        """Construction d'index avec des fichiers mockés."""
        # Créer un faux .docx
        fake_docx = tmp_path / "ref.docx"
        fake_docx.write_text("fake")  # Pas un vrai docx, mais on mock Document

        mock_embeddings.return_value = [[0.1, 0.2]]

        mock_doc = MagicMock()
        mock_doc.paragraphs = [MagicMock(text="Hello world")]
        mock_doc.tables = []

        import app.ingest_refs
        monkeypatch.setattr(app.ingest_refs, "REFS_DIR", tmp_path)

        with patch("app.ingest_refs.Document", return_value=mock_doc):
            result = build_index(refs_folder=str(tmp_path))

        assert len(result) == 1
        assert result[0]["text"] == "Hello world"
