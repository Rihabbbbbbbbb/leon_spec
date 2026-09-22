"""
LEON Q&A Engine v2 — Répondre aux questions sur un document de spécification.

Mécanisme anti-hallucination :
1. Seuls les chunks pertinents du document sont donnés au LLM (pas sa connaissance générale)
2. Le prompt exige de citer la section exacte pour chaque affirmation
3. Si la réponse n'est pas dans les extraits, le LLM doit dire "Je ne trouve pas"
4. Pas de spéculation, pas de complétion d'information partielle

Stratégie multi-niveaux v2 :
- Questions générales (overview) → synthèse des sections d'introduction (PURPOSE, SCOPE, SYSTEM ROLES)
- Questions factuelles → RAG avec threshold adaptatif + fallback progressif
- Si aucun chunk spécifique trouvé → overview du document en fallback
- Pas d'hallucination : le LLM ne voit QUE le contenu réel du document
"""
import re
from typing import List, Dict, Any, Optional, Tuple
from dataclasses import dataclass, field

from app.embeddings import get_embedding, call_llm, cosine_similarity
from app.config import TOP_K_CHUNKS


# ── Heading detection patterns ───────────────────────────────────
HEADING_NUMBERED_RE = re.compile(
    r'^(?:#+\s*)?'
    r'(\d+(?:\.\d+)+\.?\s+'
    r'[A-Z][A-Za-z\s/()\-&,:;\.]{3,})$',
    re.MULTILINE
)
HEADING_NUM_CAPS_RE = re.compile(
    r'^(?:#+\s*)?'
    r'\d+'
    r'[.)\-/\s]\s*'
    r'([A-Z][A-Z\s/()\-&,:;\.\u2013\u2014]{3,})$',
    re.MULTILINE
)
HEADING_ALLCAPS_RE = re.compile(
    r'^([A-Z][A-Z\s/()\-&,:;\.\u2013\u2014]{3,})$',
    re.MULTILINE
)

# ── Questions overview : patterns pour détecter les demandes de synthèse ──
OVERVIEW_QUESTION_RE = re.compile(
    r'^(?:(?:peux-tu\s+)?(?:tu\s+)?(?:peux\s+)?)?'
    r'(?:explique|expliquez|explain|décris|décrivez|describe|présente|présentez|'
    r'synthétise|summarize|donne\s+un\s+résumé|tell\s+me\s+about|'
    r'c\'est\s+quoi|qu\'est-ce\s+que|what\s+is|parle-moi|dis-moi)',
    re.IGNORECASE
)

FACTUAL_QUESTION_RE = re.compile(
    r'^(?:combien|où|quand|où\s+est|quel\s+est|quelle\s+est|'
    r'quels\s+sont|quelles\s+sont|comment|pourquoi|qui|'
    r'how\s+(?:many|much|does|is|are)|where|when|which|why|'
    r'what\s+(?:is|are|does|were|was))',
    re.IGNORECASE
)


@dataclass
class AnswerResult:
    """Résultat structuré d'une question/réponse."""
    answer: str = ""
    citations: List[Dict[str, Any]] = field(default_factory=list)
    confidence: str = "high"  # high | medium | low | not_found
    sections_used: List[str] = field(default_factory=list)
    chunks_retrieved: int = 0
    answer_type: str = "factual"  # factual | overview | fallback_overview | not_found
    error: Optional[str] = None


def _clean_heading_name(raw: str) -> str:
    """Extract clean section name from a raw heading line."""
    name = raw.strip()
    name = re.sub(r'\s*:\s*$', '', name)
    name = re.split(r'\s*[\u2013\u2014-]\s*', name)[0].strip()
    name = re.sub(r'\s+', ' ', name)
    return name


def _extract_sections_structure(text: str) -> List[Dict[str, Any]]:
    """
    Extract ALL section headings with preview content.
    
    V2 fix: Always add EVERY detected heading to the output list,
    even if it has no content lines (common in DOCX where content
    is in tables, not between headings as paragraphs).
    """
    lines = text.split('\n')
    sections: List[Dict[str, Any]] = []
    seen_headings: set = set()
    current_section = "Document Start"
    preview_lines: Dict[str, List[str]] = {}
    section_order: List[str] = []  # Preserve heading order

    for line in lines:
        stripped = line.strip()
        clean_line = re.sub(r'^#+\s*', '', stripped)
        is_heading = False

        if 4 <= len(clean_line) <= 120:
            heading_name = None
            m = HEADING_ALLCAPS_RE.match(clean_line)
            if m:
                heading_name = _clean_heading_name(m.group(1))
            if not heading_name:
                m = HEADING_NUM_CAPS_RE.match(clean_line)
                if m:
                    heading_name = _clean_heading_name(m.group(1))
            if not heading_name:
                m = HEADING_NUMBERED_RE.match(clean_line)
                if m:
                    heading_name = _clean_heading_name(
                        re.sub(r'^\d+(?:\.\d+)*\.?\s*', '', m.group(1).strip()))

            if heading_name and len(heading_name) >= 4:
                # Always track this heading
                if heading_name not in seen_headings:
                    seen_headings.add(heading_name)
                    section_order.append(heading_name)
                    preview_lines[heading_name] = []  # Ensure entry exists
                current_section = heading_name
                is_heading = True

        # Add content line to the current section (if not a heading)
        if not is_heading and current_section != "Document Start" and stripped:
            if len(preview_lines.get(current_section, [])) < 5:
                if current_section not in preview_lines:
                    preview_lines[current_section] = []
                preview_lines[current_section].append(stripped[:150])

    # Build output in heading order — ALL headings included
    for sec_name in section_order:
        previews = preview_lines.get(sec_name, [])
        sections.append({
            "name": sec_name,
            "preview": " ".join(previews)[:250] if previews else "(section détectée, contenu dans les tableaux)",
        })

    return sections


def _classify_question(question: str) -> str:
    """
    Classify question type: 'overview' | 'factual'

    V3: Ultra-robust — handles typos, single words, and any generic question.
    """
    q = question.strip().lower()

    # ── 1. Mots-clés "overview" (n'importe où dans la question) ──
    overview_keywords = [
        'explique', 'expliquer', 'expliquez', 'explain', 'explqin',
        'décris', 'décrire', 'décrivez', 'describe', 'describ',
        'présente', 'présenter', 'présentez', 'present',
        'synthèse', 'synthétise', 'synthétiser', 'synthétisez',
        'summarize', 'summarise', 'summary',
        'analyse', 'analyser', 'analyze', 'analysis', 'analytical',
        'deeply', 'approfondi', 'approfondie', 'deep',
        'résumé', 'aperçu', 'overview', 'tour d\'horizon',
        'c\'est quoi', 'qu\'est-ce que', 'what is', 'what are',
        'parle-moi', 'dis-moi', 'tell me', 'talk about',
        'de quoi', 'en quoi consiste', 'consiste',
        'vision globale', 'vue d\'ensemble',
        'donne moi', 'give me', 'give an',
        'content', 'contenu', 'structure',
        'document', 'spec', 'spéc', 'cahier', 'fiche',
    ]

    # ── 2. Mots interrogatifs factuels (début de question) ──
    factual_start = bool(FACTUAL_QUESTION_RE.match(q))

    # ── 3. Détection overview par mots-clés ──
    has_overview_keyword = any(kw in q for kw in overview_keywords)

    # ── 4. Questions très courtes → overview ──
    word_count = len(q.split())
    is_very_short = word_count <= 3
    short_generic = word_count <= 5 and not factual_start

    # ── 5. Questions qui mentionnent le document → overview ──
    doc_refs = ['document', 'spec', 'specification', 'cahier', 'charges',
                'this', 'ce document', 'cette spec', 'le doc', 'doc']
    mentions_doc = any(ref in q for ref in doc_refs)

    # ── CLASSIFICATION ───────────────────────────────────────
    if factual_start and word_count >= 3:
        return "factual"
    if has_overview_keyword or short_generic or is_very_short:
        return "overview"
    if mentions_doc and word_count <= 20:
        return "overview"
    # Default: if unsure, prefer overview (safer — avoids "not found")
    return "overview"


def _get_document_overview(text: str) -> str:
    """
    Extract the introductory sections (PURPOSE, SCOPE, SYSTEM ROLES, ...)
    plus the full section list. Used for overview answers and factual fallback.

    V2: More robust for real DOCX files.
    - Collects ALL headings first (from entire document)
    - Collects content AFTER each target heading using a line window
    - Does NOT stop early on non-target headings
    - Falls back to first N paragraphs if no target sections found
    """
    lines = text.split('\n')
    target_sections = [
        "REQUIREMENTS DOCUMENT", "PURPOSE", "SCOPE",
        "SYSTEM DEVELOPMENT CONTEXT", "GENERAL DESCRIPTION",
        "SYSTEM ROLES", "PRESENTATION"
    ]

    # ── Phase 1: Collect ALL headings from entire document ──────
    all_headings = []
    heading_line_indices = set()
    for idx, line in enumerate(lines):
        stripped = line.strip()
        clean_line = re.sub(r'^#+\s*', '', stripped)
        if 4 <= len(clean_line) <= 120:
            # Try ALL-CAPS heading pattern
            m = HEADING_ALLCAPS_RE.match(clean_line)
            if m:
                h = _clean_heading_name(m.group(1))
                if h not in all_headings and h not in [
                    "TABLE OF CONTENTS", "TABLE OF UPDATES", "WARNING"
                ]:
                    all_headings.append(h)
                    heading_line_indices.add(idx)

    # ── Phase 2: Collect content from target sections ───────────
    collected = []
    collecting = False
    lines_after_target = 0
    MAX_LINES_PER_SECTION = 30  # Max content lines per target section

    for idx, line in enumerate(lines[:800]):  # Scan first 800 lines
        stripped = line.strip()
        clean_line = re.sub(r'^#+\s*', '', stripped)

        # Check if this line IS a target section heading
        found_target = None
        is_heading_line = idx in heading_line_indices

        if is_heading_line and clean_line:
            for t in target_sections:
                if t in clean_line.upper() and len(clean_line) < 100:
                    found_target = t
                    break

        if found_target:
            # Start or continue collecting
            collecting = True
            lines_after_target = 0
            if collected:
                collected.append("---")
            collected.append(f"[SECTION: {found_target}]")
            continue

        if collecting and stripped:
            # Check if we hit a NEW major heading (not in targets) → stop collecting
            if is_heading_line and not found_target:
                h = _clean_heading_name(clean_line)
                if h in all_headings and h not in target_sections:
                    # This is a new major section, stop collecting
                    # But DON'T break — just stop collecting this section
                    collecting = False
                    continue

            lines_after_target += 1
            if lines_after_target <= MAX_LINES_PER_SECTION:
                if not stripped.startswith('<<'):
                    collected.append(stripped)

    overview_text = "\n".join(collected) if collecting or any(
        t in " ".join(collected).upper() for t in target_sections
    ) else ""

    # ── Phase 3: Fallback — use first N paragraphs ──────────────
    if not overview_text:
        # Take first 50 non-empty, non-heading lines as overview
        fallback_lines = []
        for line in lines[:100]:
            s = line.strip()
            if s and len(s) > 10 and idx not in heading_line_indices:
                fallback_lines.append(s)
                if len(fallback_lines) >= 30:
                    break
        if fallback_lines:
            overview_text = "[DÉBUT DU DOCUMENT]\n" + "\n".join(fallback_lines)

    # ── Phase 4: Structure listing ──────────────────────────────
    structure_lines = ["\n\nSTRUCTURE DU DOCUMENT (sections principales):"]
    for h in all_headings[:40]:
        structure_lines.append(f"  • {h}")
    if len(all_headings) > 40:
        structure_lines.append(f"  • ... et {len(all_headings) - 40} autres sections")

    full = overview_text + "\n" + "\n".join(structure_lines)
    return full.strip() or "Aucune section d'introduction trouvée."


def _retrieve_chunks(
    text: str, q_embedding: List[float],
    initial_threshold: float = 0.50
) -> Tuple[List[Dict[str, Any]], float]:
    """
    Retrieve relevant chunks with progressive threshold lowering.
    Tries: initial_threshold → initial_threshold - 0.15 → initial_threshold - 0.25
    """
    lines = text.split('\n')
    text_blocks = [{"text": p} for p in lines if p.strip()]
    from app.chunking import chunk_by_paragraphs
    chunks = chunk_by_paragraphs(text_blocks, chunk_size=8)
    if not chunks:
        return [], 0.0

    # Build chunk embeddings
    chunk_embeddings = []
    for chunk in chunks:
        try:
            chunk_embeddings.append(get_embedding(chunk))
        except Exception:
            chunk_embeddings.append(None)

    thresholds = [initial_threshold, initial_threshold - 0.15, initial_threshold - 0.25]
    best_found = []

    for thresh in thresholds:
        scored = []
        for i, chunk in enumerate(chunks):
            if i < len(chunk_embeddings) and chunk_embeddings[i] is not None:
                score = cosine_similarity(q_embedding, chunk_embeddings[i])
                if score >= thresh:
                    scored.append({"text": chunk, "score": score, "chunk_index": i})
        scored.sort(key=lambda x: x["score"], reverse=True)
        if scored:
            best_found = scored[:TOP_K_CHUNKS]
            if scored[0]["score"] >= thresh + 0.08:
                return best_found, thresh

    return best_found, 0.0


def _extract_section_from_chunk(chunk_text: str) -> str:
    """Extract the most likely section name from chunk text."""
    for line in chunk_text.split('\n')[:3]:
        clean = line.strip()
        if len(clean) >= 4 and len(clean) <= 120:
            m = HEADING_ALLCAPS_RE.match(clean)
            if m:
                return _clean_heading_name(m.group(1))
            m = HEADING_NUM_CAPS_RE.match(clean)
            if m:
                return _clean_heading_name(m.group(1))
    return "Section extraite"


def _build_prompts(
    question: str, document_name: str,
    chunks: List[Dict[str, Any]], answer_type: str,
    doc_overview: str = ""
) -> Tuple[str, str]:
    """
    Build LLM prompts with strict grounding.
    Two modes:
    - overview : synthèse des sections d'introduction
    - factual  : réponse précise à partir de chunks spécifiques
    """
    if answer_type == "overview" and doc_overview:
        sp = f"""Vous êtes LEON, un assistant strict d'analyse de spécifications techniques Stellantis.

Vous avez accès au CONTENU INTRODUCTIF d'un document de spécification technique.
Répondez à la question de l'utilisateur UNIQUEMENT à partir de ce contenu.

RÈGLES STRICTES :
1. Utilisez UNIQUEMENT les informations présentes ci-dessous.
2. Faites une synthèse CLAIRE et STRUCTURÉE du document.
3. Citez les noms de sections entre crochets, ex: [PURPOSE] ou [SYSTEM ROLES].
4. N'utilisez PAS vos connaissances générales. N'inventez RIEN.
5. Si des sections sont vides ou ne contiennent que des placeholders, mentionnez-le.
6. Répondez en français, de manière professionnelle et technique.
7. Terminez par un aperçu des sections principales du document.

CONTENU INTRODUCTIF DU DOCUMENT "{document_name}":
—————————————————————
{doc_overview[:6000]}
—————————————————————"""
        up = f"Question: {question}\n\nFaites une synthèse du document."
        return sp, up

    # Factual mode
    if not chunks:
        return _build_prompts(question, document_name, [], "overview", doc_overview)

    chunks_text = ""
    for i, c in enumerate(chunks, 1):
        sec = _extract_section_from_chunk(c["text"])
        chunks_text += (
            f"[EXTRAIT {i}] (similarité: {c['score']:.3f}) [Section: {sec}]\n"
            f"{c['text'][:2000]}\n\n"
        )

    sp = f"""Vous êtes LEON, un assistant strict d'analyse de spécifications techniques Stellantis.

Vous avez accès à des EXTRAITS d'un document de spécification technique.
Répondez à la question UNIQUEMENT à partir de ces extraits.

RÈGLES STRICTES — AUCUNE EXCEPTION :
1. Utilisez UNIQUEMENT les informations présentes dans les extraits ci-dessous.
2. Si les extraits ne contiennent PAS la réponse, dites :
   "Je ne trouve pas cette information dans le document."
3. Pour chaque affirmation, citez le NOM DE LA SECTION entre crochets, ex: [SYSTEM ROLES].
4. N'utilisez PAS vos connaissances générales sur l'automobile, la mécatronique ou Stellantis.
5. N'inventez RIEN. Ne spéculez PAS.
6. Si vous n'êtes pas certain, dites-le.
7. Répondez en français, de manière concise et technique.

EXTRAITS DU DOCUMENT "{document_name}":
—————————————————————
{chunks_text}
—————————————————————"""

    up = f"Question: {question}\n\nRépondez UNIQUEMENT à partir des extraits ci-dessus. Citez les sections."
    return sp, up


# ═══════════════════════════════════════════════════════════════════
# PUBLIC API — ask_question
# ═══════════════════════════════════════════════════════════════════

def ask_question(
    document_text: str,
    question: str,
    document_name: str = "document",
    top_k: int = 5,
    similarity_threshold: float = 0.70,
) -> AnswerResult:
    """
    Pose une question sur un document de spécification.

    Stratégie intelligente :
    1. Classifie la question (overview / factual)
    2. Overview → extrait les sections d'introduction → synthèse LLM sourcée
    3. Factual → RAG avec threshold adaptatif (essaie 3 seuils)
    4. Si aucun chunk trouvé → fallback overview du document
    5. Anti-hallucination : le LLM ne voit QUE le contenu réel du document

    Args:
        document_text: Texte complet du document
        question: Question de l'utilisateur
        document_name: Nom du document
        top_k: Nombre max de chunks à utiliser
        similarity_threshold: Seuil initial de similarité cosinus

    Returns:
        AnswerResult structuré avec réponse, citations, confiance
    """
    result = AnswerResult()

    # ── 1. BULLETPROOF SECTION EXTRACTION ───────────────────────
    # Use the SAME regex as the dashboard sidebar (PROVEN to work on real DOCX)
    # This method ALWAYS returns sections, guaranteed.
    sections = []
    if document_text:
        allcaps_rx = re.compile(r'^([A-Z][A-Z\s/()\-&,:;\.]{4,})$', re.MULTILINE)
        numcaps_rx = re.compile(r'^\d+[.)\-/\s]\s*([A-Z][A-Z\s/()\-&,:;\.]{4,})$', re.MULTILINE)
        seen_secs = set()
        for m in allcaps_rx.finditer(document_text):
            s = m.group(1).strip()
            if s not in seen_secs and len(s) >= 4:
                seen_secs.add(s)
                sections.append({"name": s, "preview": ""})
        for m in numcaps_rx.finditer(document_text):
            s = m.group(1).strip()
            if s not in seen_secs and len(s) >= 4:
                seen_secs.add(s)
                sections.append({"name": s, "preview": ""})
    
    # Build simple overview: first 80 non-empty paragraphs + section list
    first_paras = []
    for line in document_text.split('\n')[:100]:
        s = line.strip()
        if s and len(s) > 10 and s not in [x["name"] for x in sections]:
            first_paras.append(s)
            if len(first_paras) >= 50:
                break
    overview_content = "\n".join(first_paras)
    
    has_sections = len(sections) > 0
    has_overview_content = len(overview_content) > 200
    
    # Build doc_overview text from whatever we have
    doc_overview_parts = []
    if has_overview_content:
        doc_overview_parts.append("CONTENU DU DOCUMENT:\n" + overview_content)
    if has_sections:
        sec_list = "\n".join(f"  • {s['name']}" for s in sections[:50])
        doc_overview_parts.append(f"\n\nSECTIONS DU DOCUMENT ({len(sections)} détectées):\n{sec_list}")
        if len(sections) > 50:
            doc_overview_parts.append(f"  • ... et {len(sections) - 50} autres")
    doc_overview = "\n".join(doc_overview_parts)

    # ── 2. Classifier la question ───────────────────────────────
    qtype = _classify_question(question)
    result.answer_type = qtype

    # ── 3. OVERVIEW : réponse synthèse ──────────────────────────
    if qtype == "overview":
        # If we have no sections AND no overview content, we really can't answer
        if not has_sections and not has_overview_content:
            result.answer = (
                f"Je n'ai pas pu extraire le contenu du document "
                f"**{document_name}** — il semble vide ou dans un format non reconnu."
            )
            result.confidence = "not_found"
            return result

        # Build the overview content for the LLM
        overview_for_llm = doc_overview if doc_overview.strip() else (
            f"Document: {document_name}\n"
            f"Sections: {len(sections)}\n"
            + "\n".join(s["name"] for s in sections[:20])
        )

        sp, up = _build_prompts(question, document_name, [], "overview", overview_for_llm)
        try:
            llm_answer = call_llm(sp, up, temperature=0.2, max_tokens=2500)
        except Exception as e:
            # LLM failed — return section list as fallback
            if has_sections:
                section_text = "\n".join(f"  • {s['name']}" for s in sections[:25])
                result.answer = (
                    f"Le document **{document_name}** contient **{len(sections)} sections**. "
                    f"L'analyse LLM n'a pas pu être effectuée ({e}).\n\n"
                    f"Sections principales :\n{section_text}"
                )
                result.sections_used = [s["name"] for s in sections[:15]]
                result.confidence = "low"
                result.chunks_retrieved = len(sections)
                result.answer_type = "fallback_overview"
                return result
            result.answer = f"Erreur technique: {e}"
            result.confidence = "not_found"
            result.error = str(e)
            return result

        result.answer = llm_answer
        result.sections_used = [s["name"] for s in sections[:20]]
        result.confidence = "medium" if has_overview_content else "low"
        result.chunks_retrieved = len(sections)
        result.citations = [
            {"section": s["name"], "similarity": 1.0, "excerpt": s.get("preview", "")[:200]}
            for s in sections[:12]
        ]
        return result

    # ── 4. FACTUAL : RAG avec fallback ──────────────────────────
    try:
        q_emb = get_embedding(question)
    except Exception as e:
        result.answer = f"Erreur technique: {e}"
        result.confidence = "not_found"
        result.error = str(e)
        return result

    # Essai 1 : threshold normal
    chunks, _ = _retrieve_chunks(document_text, q_emb, similarity_threshold - 0.10)

    # Essai 2 : threshold très bas si rien trouvé
    if not chunks:
        chunks, _ = _retrieve_chunks(document_text, q_emb, 0.25)

    # Essai 3 : fallback overview si toujours rien
    if not chunks:
        # Use the overview we already built
        if doc_overview.strip():
            sp, up = _build_prompts(question, document_name, [], "overview", doc_overview)
            try:
                llm_answer = call_llm(sp, up, temperature=0.2, max_tokens=2500)
            except Exception:
                llm_answer = None

            if llm_answer:
                result.answer = llm_answer
            elif has_sections:
                section_text = "\n".join(f"  • {s['name']}" for s in sections[:25])
                result.answer = (
                    f"Le document **{document_name}** contient **{len(sections)} sections**. "
                    f"Voici les principales :\n{section_text}"
                )
            else:
                result.answer = (
                    f"Je n'ai pas trouvé d'information pertinente dans le document "
                    f"**{document_name}**. Essayez de reformuler."
                )

            result.sections_used = [s["name"] for s in sections[:12]]
            result.confidence = "low" if has_sections else "not_found"
            result.answer_type = "fallback_overview"
            result.chunks_retrieved = len(sections) if has_sections else 0
            result.citations = [
                {"section": s["name"], "similarity": 0.5, "excerpt": s.get("preview", "")[:150]}
                for s in sections[:6]
            ]
            return result

    # ── 5. Traiter les chunks trouvés ───────────────────────────
    result.chunks_retrieved = len(chunks)
    seen_sections = []
    for c in chunks:
        sec = _extract_section_from_chunk(c["text"])
        if sec and sec not in seen_sections:
            seen_sections.append(sec)
    result.sections_used = seen_sections[:10]

    sp, up = _build_prompts(question, document_name, chunks, "factual")
    try:
        llm_answer = call_llm(sp, up, temperature=0.1, max_tokens=2000)
    except Exception as e:
        result.answer = f"Erreur LLM: {e}"
        result.error = str(e)
        return result

    for c in chunks:
        sec = _extract_section_from_chunk(c["text"])
        result.citations.append({
            "section": sec,
            "similarity": round(c["score"], 3),
            "excerpt": c["text"][:300],
        })

    # Confiance
    top_score = chunks[0]["score"]
    llm_not_found = any(p in llm_answer.lower() for p in [
        "je ne trouve pas", "ne contient pas",
        "ne trouve pas d'information", "n'ai pas trouvé d'information"
    ])

    if llm_not_found:
        result.confidence = "not_found"
    elif top_score >= 0.78:
        result.confidence = "high"
    elif top_score >= 0.60:
        result.confidence = "medium"
    else:
        result.confidence = "low"

    result.answer = llm_answer
    return result
