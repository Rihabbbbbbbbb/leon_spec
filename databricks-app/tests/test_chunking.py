"""
Tests unitaires pour le module de chunking.
"""
import pytest
from app.chunking import (
    chunk_by_paragraphs,
    chunk_by_sections,
    chunk_document,
    _is_heading,
    _estimate_tokens,
)


class TestEstimateTokens:
    def test_empty_string(self):
        assert _estimate_tokens("") == 1  # min 1

    def test_short_text(self):
        assert _estimate_tokens("Hello") == 2  # 5//2 = 2

    def test_french_text(self):
        text = "Évaluation des spécifications techniques"
        assert _estimate_tokens(text) == len(text) // 2


class TestIsHeading:
    def test_numbered_heading(self):
        assert _is_heading("1. Objet") is True
        assert _is_heading("1.1 Sous-section") is True
        assert _is_heading("3.2.1 Détail") is True

    def test_uppercase_heading(self):
        assert _is_heading("INTRODUCTION") is True
        assert _is_heading("SPÉCIFICATIONS TECHNIQUES") is True

    def test_step_heading(self):
        assert _is_heading("Étape 1 — Créer le dossier") is True

    def test_not_heading(self):
        assert _is_heading("Ceci est un paragraphe normal.") is False
        assert _is_heading("") is False
        assert _is_heading("123") is False  # juste un nombre


class TestChunkByParagraphs:
    def test_empty_blocks(self):
        assert chunk_by_paragraphs([]) == []

    def test_single_block(self, sample_blocks_short):
        chunks = chunk_by_paragraphs(sample_blocks_short, chunk_size=5)
        assert len(chunks) == 1
        assert "Para 1" in chunks[0]

    def test_chunk_size_grouping(self, sample_blocks):
        chunks = chunk_by_paragraphs(sample_blocks, chunk_size=3)
        # 11 blocs / 3 = 4 chunks (3+3+3+2)
        assert len(chunks) == 4

    def test_no_chunk_size_limit(self, sample_blocks_short):
        chunks = chunk_by_paragraphs(sample_blocks_short, chunk_size=10)
        assert len(chunks) == 1

    def test_skips_empty_text(self):
        blocks = [
            {"text": ""},
            {"text": "   "},
            {"text": "Valide"},
            {"text": "Aussi valide"},
        ]
        chunks = chunk_by_paragraphs(blocks, chunk_size=5)
        assert len(chunks) == 1
        assert "Valide" in chunks[0]
        assert "Aussi valide" in chunks[0]

    def test_token_limit_flush(self):
        """Un texte très long doit forcer un flush prématuré."""
        long_text = "x" * 15000  # > 6000 tokens estimés
        blocks = [
            {"text": "Court"},
            {"text": long_text},
            {"text": "Autre court"},
        ]
        chunks = chunk_by_paragraphs(blocks, chunk_size=10)
        # Le long texte devrait déclencher un flush
        assert len(chunks) >= 2


class TestChunkBySections:
    def test_empty_blocks(self):
        assert chunk_by_sections([]) == []

    def test_splits_on_headings(self, sample_blocks):
        chunks = chunk_by_sections(sample_blocks)
        # Devrait détecter les titres numérotés et créer plusieurs chunks
        assert len(chunks) >= 3  # Au moins les sections 1, 2, 3

    def test_no_headings_single_chunk(self, sample_blocks_short):
        chunks = chunk_by_sections(sample_blocks_short)
        assert len(chunks) == 1


class TestChunkDocument:
    def test_default_strategy(self, sample_blocks):
        chunks = chunk_document(sample_blocks)
        assert len(chunks) > 0

    def test_paragraphs_strategy(self, sample_blocks):
        chunks = chunk_document(sample_blocks, strategy="paragraphs", chunk_size=3)
        assert len(chunks) == 4

    def test_sections_strategy(self, sample_blocks):
        chunks = chunk_document(sample_blocks, strategy="sections")
        assert len(chunks) >= 3

    def test_invalid_strategy_falls_back(self, sample_blocks):
        """Une stratégie inconnue doit utiliser paragraphs par défaut."""
        chunks = chunk_document(sample_blocks, strategy="unknown_xyz")
        assert len(chunks) > 0  # fallback à paragraphs
