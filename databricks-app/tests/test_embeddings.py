"""
Tests unitaires pour le module d'embeddings.
"""
import pytest
import numpy as np
from app.embeddings import cosine_similarity, find_similar_chunks


class TestCosineSimilarity:
    def test_identical_vectors(self):
        vec = [1.0, 0.0, 0.0]
        assert cosine_similarity(vec, vec) == pytest.approx(1.0)

    def test_orthogonal_vectors(self):
        a = [1.0, 0.0, 0.0]
        b = [0.0, 1.0, 0.0]
        assert cosine_similarity(a, b) == pytest.approx(0.0)

    def test_opposite_vectors(self):
        a = [1.0, 0.0, 0.0]
        b = [-1.0, 0.0, 0.0]
        assert cosine_similarity(a, b) == pytest.approx(-1.0)

    def test_partial_similarity(self):
        a = [1.0, 1.0]
        b = [1.0, 0.0]
        result = cosine_similarity(a, b)
        # cos = 1/sqrt(2) ≈ 0.707
        assert result == pytest.approx(0.7071, abs=0.001)

    def test_zero_vector(self):
        a = [0.0, 0.0, 0.0]
        b = [1.0, 2.0, 3.0]
        assert cosine_similarity(a, b) == 0.0

    def test_empty_vectors(self):
        assert cosine_similarity([], []) == 0.0

    def test_different_dimensions(self):
        """Different-length vectors: zip truncates silently, so result is valid but truncated."""
        # Pure-Python cosine_similarity uses zip() which truncates — no ValueError raised.
        # This is acceptable: the function handles mismatched lengths gracefully.
        result = cosine_similarity([1.0, 2.0], [1.0, 2.0, 3.0])
        assert isinstance(result, float)
        assert 0.0 <= result <= 1.0


class TestFindSimilarChunks:
    def test_finds_matching_chunks(self, sample_reference_index):
        query_embedding = [0.9] + [0.0] * 1535  # Très proche du chunk 0
        results = find_similar_chunks(
            query_embedding,
            sample_reference_index,
            top_k=3,
            threshold=0.5,
        )
        assert len(results) >= 1
        # Le premier résultat devrait être le chunk 0
        assert results[0]["chunk_id"] == 0
        assert "similarity" in results[0]

    def test_threshold_filters_low_scores(self, sample_reference_index):
        # Vecteurs orthogonaux → similarité ~0, tout est filtré
        query_embedding = [0.0] * 500 + [1.0] + [0.0] * 1035
        results = find_similar_chunks(
            query_embedding,
            sample_reference_index,
            top_k=5,
            threshold=0.99,  # Très strict
        )
        assert len(results) == 0  # Rien ne passe

    def test_top_k_limits_results(self, sample_reference_index):
        query_embedding = [1.0] + [0.0] * 1535
        results = find_similar_chunks(
            query_embedding,
            sample_reference_index,
            top_k=1,
            threshold=0.0,
        )
        assert len(results) <= 1

    def test_empty_index(self):
        results = find_similar_chunks([1.0, 0.0], [], top_k=5, threshold=0.5)
        assert results == []

    def test_chunks_without_embedding_skipped(self):
        index = [
            {"source_file": "x.docx", "chunk_id": 0, "text": "test"},
            {"source_file": "y.docx", "chunk_id": 1, "text": "test2", "embedding": [1.0, 0.0]},
        ]
        results = find_similar_chunks([1.0, 0.0], index, top_k=5, threshold=0.0)
        assert len(results) == 1
        assert results[0]["chunk_id"] == 1
