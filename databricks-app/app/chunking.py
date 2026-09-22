"""
Module de chunking intelligent pour découper les documents en chunks
adaptés aux limites des modèles d'embedding Azure OpenAI.

Stratégies supportées :
- chunk_by_paragraphs : regroupement par blocs de N paragraphes
- chunk_by_sections  : découpage basé sur les titres (détection heuristique)
"""
from typing import List, Dict, Any
import re

from app.config import DEFAULT_CHUNK_SIZE, MAX_CHUNK_TOKENS


def _estimate_tokens(text: str) -> int:
    """
    Estimation conservative du nombre de tokens.
    Règle empirique : ~1 token pour 4 caractères en anglais,
    ~1 token pour 2-3 caractères pour le français (langues romanes).
    On prend un ratio prudent de 2.5 chars/token.
    """
    return max(1, len(text) // 2)


def chunk_by_paragraphs(
    blocks: List[Dict[str, Any]],
    chunk_size: int = DEFAULT_CHUNK_SIZE,
    max_tokens: int = MAX_CHUNK_TOKENS
) -> List[str]:
    """
    Découpe une liste de blocs (dict avec clé 'text') en chunks
    de `chunk_size` paragraphes consécutifs.

    Args:
        blocks: Liste de dicts avec au moins la clé 'text'
        chunk_size: Nombre de paragraphes à grouper
        max_tokens: Limite maximale de tokens par chunk

    Returns:
        Liste de chunks textuels
    """
    chunks: List[str] = []
    current: List[str] = []
    current_token_estimate = 0

    for block in blocks:
        text = block.get("text", "").strip()
        if not text:
            continue

        token_est = _estimate_tokens(text)

        # Si ajouter ce paragraphe dépasse la limite tokens, on flush
        if current and (current_token_estimate + token_est > max_tokens):
            chunks.append(" ".join(current))
            current = []
            current_token_estimate = 0

        current.append(text)
        current_token_estimate += token_est

        # Si on atteint le chunk_size, on flush aussi
        if len(current) >= chunk_size:
            chunks.append(" ".join(current))
            current = []
            current_token_estimate = 0

    # Dernier chunk résiduel
    if current:
        chunks.append(" ".join(current))

    return chunks


# Patterns pour détecter les titres de section dans un document
HEADING_PATTERNS = [
    re.compile(r'^\d+(?:\.\d+)*\.?\s+.+'),        # 1. Titre, 1.1 Sous-titre, 1 Titre
    re.compile(r'^(?:[A-ZÀ-ÖØ-Þ][A-ZÀ-ÖØ-Þ\s]{2,}|[A-Z][A-Z\s]{2,})$'),  # TITRE EN MAJUSCULES (Unicode)
    re.compile(r'^(Article|Section|Chapitre)\s+\d+', re.IGNORECASE),
    re.compile(r'^Étape\s+\d+', re.IGNORECASE),
]

# Fallback : détection par ratio majuscules
def _looks_like_heading(text: str) -> bool:
    """Heuristique complémentaire : texte court avec majorité de majuscules."""
    text = text.strip()
    if len(text) < 4 or len(text) > 80:
        return False
    alpha_chars = [c for c in text if c.isalpha()]
    if not alpha_chars:
        return False
    upper_ratio = sum(1 for c in alpha_chars if c.isupper()) / len(alpha_chars)
    return upper_ratio > 0.7


def _is_heading(text: str) -> bool:
    """Détecte si un texte ressemble à un titre de section."""
    text = text.strip()
    if not text:
        return False
    for pattern in HEADING_PATTERNS:
        if pattern.match(text):
            return True
    # Fallback heuristique
    if _looks_like_heading(text):
        return True
    return False


def chunk_by_sections(
    blocks: List[Dict[str, Any]],
    max_tokens: int = MAX_CHUNK_TOKENS
) -> List[str]:
    """
    Découpe en chunks basés sur les titres de section détectés.
    Chaque section devient un chunk (ou est sous-découpée si trop longue).

    Args:
        blocks: Liste de dicts avec au moins la clé 'text'
        max_tokens: Limite maximale de tokens par chunk

    Returns:
        Liste de chunks textuels
    """
    chunks: List[str] = []
    current: List[str] = []
    current_token_estimate = 0

    for block in blocks:
        text = block.get("text", "").strip()
        if not text:
            continue

        # Skip template instructions in content chunks
        if block.get("is_template") or block.get("block_type") == "template_instruction":
            continue

        token_est = _estimate_tokens(text)

        # Nouveau titre détecté → nouveau chunk
        if _is_heading(text) and current:
            chunks.append(" ".join(current))
            current = []
            current_token_estimate = 0

        # Si le chunk courant devient trop gros, on le flush
        if current and (current_token_estimate + token_est > max_tokens):
            chunks.append(" ".join(current))
            current = []
            current_token_estimate = 0

        current.append(text)
        current_token_estimate += token_est

    if current:
        chunks.append(" ".join(current))

    return chunks


def chunk_section_aware(
    blocks: List[Dict[str, Any]],
    max_tokens: int = MAX_CHUNK_TOKENS,
    source_file: str = "",
) -> List[str]:
    """
    Decoupe en chunks bases sur les metadonnees de section (heading_level).
    Garantit que :
    - Les titres restent attaches a leur contenu (jamais seuls)
    - Les blocs template sont filtres
    - Les frontieres de section majeure (niveau 1-2) sont respectees
    - Les chunks ont une taille minimale de ~150 chars
    - Les chunks ne depassent pas max_tokens
    - Pour les guides volumineux, on reduit le max_tokens effectif

    Args:
        blocks: Liste de blocs structures
        max_tokens: Limite maximale de tokens par chunk
        source_file: Nom du fichier source (pour ajustement par type de document)

    Returns:
        Liste de chunks textuels
    """
    MIN_CHUNK_CHARS = 150       # Don't flush tiny chunks
    MAX_CHUNK_CHARS_GUIDE = 4000  # Guide documents: smaller chunks for retrieval precision
    MAX_CHUNK_CHARS_TEMPLATE = 5000  # Template: moderate chunks
    
    # Determine effective max based on source file type
    is_guide = "guide" in source_file.lower() or "writing" in source_file.lower()
    is_template = "template" in source_file.lower()
    
    if is_guide:
        effective_max_chars = min(max_tokens * 2, MAX_CHUNK_CHARS_GUIDE * 2)
        # For guides, also split on level-3 headings for finer granularity
        split_heading_level = 3
    elif is_template:
        effective_max_chars = min(max_tokens * 2, MAX_CHUNK_CHARS_TEMPLATE * 2)
        split_heading_level = 2
    else:
        effective_max_chars = max_tokens * 2
        split_heading_level = 2

    chunks: List[str] = []
    current: List[str] = []
    current_char_count = 0
    last_major_section: str = ""

    def _flush():
        nonlocal current, current_char_count
        if current:
            chunks.append(" ".join(current))
            current = []
            current_char_count = 0

    def _current_size_ok() -> bool:
        """Check if current chunk has meaningful content (not just a heading)."""
        return current_char_count >= MIN_CHUNK_CHARS

    for block in blocks:
        text = block.get("text", "").strip()
        if not text:
            continue

        # Filter template instructions
        if block.get("is_template") or block.get("block_type") == "template_instruction":
            continue

        # Filter table start/end markers
        if block.get("block_type") in ("table", "table_end"):
            continue

        block_type = block.get("block_type", "paragraph")
        heading_level = block.get("heading_level")
        section = block.get("section_context", "")

        text_len = len(text)
        is_split_heading = heading_level and heading_level <= split_heading_level

        # Split on configured heading level, only if we have content
        if is_split_heading and _current_size_ok():
            _flush()
            last_major_section = section

        # Flush if adding this block would exceed effective max chars AND we have content
        if current and (current_char_count + text_len > effective_max_chars) and _current_size_ok():
            _flush()

        # Prefix table rows with their header context if available
        if block_type == "table_row" and block.get("table_header"):
            header_preview = block["table_header"][:100]
            current.append(f"[{header_preview}] {text}")
        else:
            current.append(text)

        current_char_count += text_len

        # Track first major section
        if not last_major_section and is_split_heading:
            last_major_section = section

    # Final flush
    if current:
        chunks.append(" ".join(current))

    return chunks


def chunk_document(
    blocks: List[Dict[str, Any]],
    strategy: str = "paragraphs",
    chunk_size: int = DEFAULT_CHUNK_SIZE,
    max_tokens: int = MAX_CHUNK_TOKENS,
    source_file: str = "",
) -> List[str]:
    """
    Point d'entrée unique pour le chunking d'un document.

    Args:
        blocks: Liste de blocs extraits du document
        strategy: 'paragraphs', 'sections', ou 'section_aware'
        chunk_size: Taille des chunks (pour la stratégie paragraphs)
        max_tokens: Limite tokens par chunk

    Returns:
        Liste de chunks textuels
    """
    if strategy == "section_aware":
        return chunk_section_aware(blocks, max_tokens, source_file=source_file)
    elif strategy == "sections":
        return chunk_by_sections(blocks, max_tokens)
    else:
        return chunk_by_paragraphs(blocks, chunk_size, max_tokens)
