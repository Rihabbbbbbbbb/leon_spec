"""
Client Azure OpenAI pour la génération d'embeddings et l'appel au LLM.
Utilise l'API OpenAI standard pointant vers Azure.
"""
from typing import List, Optional
import numpy as np
from openai import OpenAI

from app.config import (
    AZURE_OPENAI_API_KEY,
    AZURE_OPENAI_ENDPOINT,
    AZURE_OPENAI_LLM_DEPLOYMENT,
    AZURE_OPENAI_EMBEDDING_DEPLOYMENT,
    EMBEDDING_MODEL_MAX_INPUT,
)

# Client OpenAI pointé vers Azure
_client: Optional[OpenAI] = None


def _get_client() -> OpenAI:
    """Retourne l'instance du client OpenAI (singleton)."""
    global _client
    if _client is None:
        _client = OpenAI(
            api_key=AZURE_OPENAI_API_KEY,
            base_url=AZURE_OPENAI_ENDPOINT,
        )
    return _client


def get_embedding(text: str) -> List[float]:
    """
    Génère un embedding pour un texte donné via Azure OpenAI.

    Args:
        text: Le texte à embedder

    Returns:
        Vecteur d'embedding (liste de floats)

    Raises:
        ValueError: Si le texte est vide ou dépasse la limite
        RuntimeError: Si l'appel API échoue
    """
    if not text or not text.strip():
        raise ValueError("Impossible d'embedder un texte vide")

    # Vérification de sécurité : tronquer si trop long
    if len(text) > EMBEDDING_MODEL_MAX_INPUT * 4:  # estimation chars
        text = text[:EMBEDDING_MODEL_MAX_INPUT * 4]

    client = _get_client()
    try:
        response = client.embeddings.create(
            model=AZURE_OPENAI_EMBEDDING_DEPLOYMENT,
            input=[text],
        )
        return response.data[0].embedding
    except Exception as e:
        raise RuntimeError(f"Échec de l'appel embedding Azure OpenAI : {e}")


def get_embeddings_batch(texts: List[str]) -> List[List[float]]:
    """
    Génère des embeddings par lot (batch).

    Args:
        texts: Liste de textes à embedder

    Returns:
        Liste de vecteurs d'embedding
    """
    if not texts:
        return []

    # Filtrer les textes vides
    valid_texts = [t for t in texts if t and t.strip()]
    if not valid_texts:
        return []

    # Tronquer les textes trop longs
    max_chars = EMBEDDING_MODEL_MAX_INPUT * 4
    valid_texts = [t[:max_chars] for t in valid_texts]

    client = _get_client()
    try:
        response = client.embeddings.create(
            model=AZURE_OPENAI_EMBEDDING_DEPLOYMENT,
            input=valid_texts,
        )
        return [item.embedding for item in response.data]
    except Exception as e:
        raise RuntimeError(f"Échec de l'appel embedding batch Azure OpenAI : {e}")


def cosine_similarity(vec_a: List[float], vec_b: List[float]) -> float:
    """
    Calcule la similarité cosinus entre deux vecteurs.

    Args:
        vec_a: Premier vecteur
        vec_b: Second vecteur

    Returns:
        Score de similarité entre 0 et 1
    """
    a = np.array(vec_a)
    b = np.array(vec_b)
    norm_a = np.linalg.norm(a)
    norm_b = np.linalg.norm(b)
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return float(np.dot(a, b) / (norm_a * norm_b))


def find_similar_chunks(
    query_embedding: List[float],
    reference_chunks: List[dict],
    top_k: int = 5,
    threshold: float = 0.75,
) -> List[dict]:
    """
    Trouve les chunks de référence les plus similaires à une requête.

    Args:
        query_embedding: Embedding de la requête
        reference_chunks: Liste de chunks avec clé 'embedding'
        top_k: Nombre de résultats à retourner
        threshold: Seuil minimal de similarité

    Returns:
        Liste triée des chunks les plus similaires (avec score)
    """
    scored = []
    for chunk in reference_chunks:
        if "embedding" not in chunk or not chunk["embedding"]:
            continue
        score = cosine_similarity(query_embedding, chunk["embedding"])
        if score >= threshold:
            scored.append({**chunk, "similarity": score})

    scored.sort(key=lambda x: x["similarity"], reverse=True)
    return scored[:top_k]


def call_llm(
    system_prompt: str,
    user_message: str,
    temperature: float = 0.3,
    max_tokens: int = 2000,
) -> str:
    """
    Appelle le LLM Azure OpenAI pour une tâche de génération.

    Args:
        system_prompt: Instructions système
        user_message: Message utilisateur
        temperature: Température de génération (0-1)
        max_tokens: Nombre max de tokens en sortie

    Returns:
        Texte généré par le LLM
    """
    client = _get_client()
    try:
        response = client.chat.completions.create(
            model=AZURE_OPENAI_LLM_DEPLOYMENT,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_message},
            ],
            temperature=temperature,
            max_tokens=max_tokens,
        )
        return response.choices[0].message.content or ""
    except Exception as e:
        raise RuntimeError(f"Échec de l'appel LLM Azure OpenAI : {e}")
