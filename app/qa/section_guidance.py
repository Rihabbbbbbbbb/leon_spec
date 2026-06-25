"""
Section Guidance Engine — provides structured guidance for CTS sections.

When a user asks "what should I put in the PURPOSE section?" or
"how do I write the FUNCTIONAL REQUIREMENTS?", this engine:
1. Detects which CTS section the user is asking about
2. Retrieves template instructions (from the template DOCX)
3. Retrieves writing guide rules and content (from the writing guide DOCX)
4. Returns a structured answer with:
   - Section purpose (from writing guide)
   - Template instructions (what the template says to put there)
   - Writing guide rules applicable to this section
   - Examples from the guide
   - Key dos and don'ts

All content is 100% extracted from the real source documents.
"""
from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from app.qa.rule_extractor import (
    extract_all_rules,
    ExtractedRules,
    SectionRule,
    WritingGuideRule,
    get_rule_by_id,
)


# ── Canonical CTS section names (with aliases for question matching) ──
# Maps user-friendly names to the canonical template section names.
SECTION_ALIASES: Dict[str, List[str]] = {
    "PURPOSE": ["purpose", "object", "objective", "goal of the spec"],
    "SCOPE": ["scope", "domain", "application domain", "what the spec covers"],
    "SYSTEM DEVELOPMENT CONTEXT": ["system development context", "development context", "context", "over-systems"],
    "GENERAL DESCRIPTION OF THE SYSTEM": ["general description", "system description", "description of the system"],
    "SYSTEM ROLES": ["system roles", "roles", "operational role", "use case", "functional role"],
    "PHYSICAL SYSTEM ARCHITECTURE": ["physical architecture", "system architecture", "physical connections", "architecture diagram"],
    "SYSTEM DIVERSITY": ["system diversity", "diversity", "variants", "functional diversity", "architecture diversity", "complexity"],
    "QUOTED DOCUMENTS": ["quoted documents", "documents quoted", "document references"],
    "REFERENCE DOCUMENTS": ["reference documents", "reference docs", "design files", "upstream requirements"],
    "APPLICABLE DOCUMENTS": ["applicable documents", "applicable docs", "standards section", "technical specifications"],
    "TERMINOLOGY": ["terminology", "terms", "vocabulary"],
    "GLOSSARY": ["glossary", "definitions", "term definitions"],
    "ACRONYMS": ["acronyms", "abbreviations", "sigles"],
    "REQUIREMENTS": ["requirements", "requirement section", "how to write requirements", "requirement template"],
    "FUNCTIONAL REQUIREMENTS": ["functional requirements", "functional req", "functions", "behavioral requirements", "functional behavior"],
    "PERFORMANCE REQUIREMENTS": ["performance requirements", "performance", "efficiency", "response time", "timing"],
    "EXTERNAL INTERFACES": ["external interfaces", "interfaces", "network interfaces", "electrical interfaces", "mechanical interfaces"],
    "EXTERNAL INTERFACES REQUIREMENTS": ["external interfaces requirements", "interface requirements", "external interface spec"],
    "NETWORK INTERFACES": ["network interfaces", "can", "lin", "multiplex", "communication protocol", "network"],
    "ELECTRICAL INTERFACES": ["electrical interfaces", "power supply", "wiring", "connectors", "voltage", "current"],
    "MECHANICAL INTERFACES": ["mechanical interfaces", "mounting", "fastening", "mechanical connections"],
    "HUMAN-MACHINE INTERFACES": ["human machine interfaces", "hmi", "mmi", "buttons", "display", "warning lights"],
    "OPERATIONAL REQUIREMENTS": ["operational requirements", "mission profile", "lifetime", "ergonomics", "rams", "safety", "reliability", "maintainability"],
    "MISSION PROFILE": ["mission profile", "operating conditions", "usage conditions", "life situations"],
    "LIFETIME": ["lifetime", "durability", "life duration", "service life"],
    "ERGONOMICS AND HUMAN FACTORS": ["ergonomics", "human factors", "random noise", "noise requirement", "non-cutting edges"],
    "RAMS REQUIREMENTS": ["rams", "safety requirements", "reliability requirements", "dependability", "sdf", "ASIL"],
    "PRODUCT QUALITY": ["product quality", "quality requirements", "fault rate", "reliability target"],
    "CONSTRAINT REQUIREMENTS": ["constraint requirements", "constraints", "design constraints", "regulation", "consumerism"],
    "DESIGN AND MANUFACTURING": ["design and manufacturing", "design", "manufacturing", "materials", "marking"],
    "TRACEABILITY AND CONFIGURATION": ["traceability", "configuration", "marking requirements"],
    "ENVIRONMENT CONDITIONS": ["environment conditions", "environment", "environmental", "temperature", "humidity", "vibration", "sealing", "IP"],
    "INTEGRATION AND VALIDATION": ["integration and validation", "validation", "testing", "test plan", "validation plan", "verification"],
    "INTEGRATION AND VALIDATION REQUIREMENTS": ["integration and validation requirements", "test requirements", "validation requirements", "essais", "tests"],
    "DEMONSTRATION OF COMPLIANCE": ["demonstration of compliance", "compliance demonstration", "proof of compliance"],
    "IMPOSED ELEMENTS OF VALIDATION PLAN": ["validation plan", "imposed tests", "test plan elements", "validation program"],
    "TABLE OF UPDATES": ["table of updates", "revision history", "history", "version history", "modification tracking"],
    "DOCUMENT IDENTIFICATION": ["document identification", "title", "reference", "document id", "RSP"],
    "CHECKING AND APPROVAL": ["checking", "approval", "writer", "auditor", "approver", "signatures"],
    "PAGE FOOTERS": ["page footer", "footer", "header", "page numbering"],
    "WRITING CONVENTIONS": ["writing conventions", "conventions", "requirement numbering", "pciee"],
}

# French aliases (add to the alias lists above)
FRENCH_ALIASES = {
    "PURPOSE": ["objet", "but", "objectif"],
    "SCOPE": ["domaine d'application", "périmètre", "portée"],
    "SYSTEM ROLES": ["rôles du système", "rôle fonctionnel", "cas d'utilisation"],
    "FUNCTIONAL REQUIREMENTS": ["exigences fonctionnelles", "exigence fonctionnelle"],
    "PERFORMANCE REQUIREMENTS": ["exigences de performance", "performance"],
    "EXTERNAL INTERFACES": ["interfaces externes", "interface externe"],
    "OPERATIONAL REQUIREMENTS": ["exigences opérationnelles"],
    "CONSTRAINT REQUIREMENTS": ["exigences de contrainte", "contraintes"],
    "GLOSSARY": ["glossaire", "définitions"],
    "ACRONYMS": ["acronymes", "sigles", "abréviations"],
    "REQUIREMENTS": ["exigences", "cdc", "cahier des charges"],
    "TERMINOLOGY": ["terminologie"],
    "REFERENCE DOCUMENTS": ["documents de référence", "documents de reférence"],
    "APPLICABLE DOCUMENTS": ["documents applicables"],
    "MISSION PROFILE": ["profil de mission", "profil mission"],
    "LIFETIME": ["durée de vie", "duree de vie"],
    "SYSTEM DIVERSITY": ["diversité du système", "diversite"],
    "PHYSICAL SYSTEM ARCHITECTURE": ["architecture physique", "architecture du système"],
    "GENERAL DESCRIPTION OF THE SYSTEM": ["description générale", "description generale"],
    "SYSTEM DEVELOPMENT CONTEXT": ["contexte de développement", "contexte de developpement"],
    "QUOTED DOCUMENTS": ["documents cités", "documents cites"],
    "WRITING CONVENTIONS": ["conventions d'écriture", "conventions d'ecriture"],
    "INTEGRATION AND VALIDATION": ["intégration et validation", "integration et validation"],
    "RAMS REQUIREMENTS": ["sûreté de fonctionnement", "surete", "sdf", "securité", "fiabilité"],
    "DESIGN AND MANUFACTURING": ["conception et fabrication"],
    "ENVIRONMENT CONDITIONS": ["conditions d'environnement", "environnement"],
    "TABLE OF UPDATES": ["historique", "historique des modifications", "tableau des mises à jour"],
    "DOCUMENT IDENTIFICATION": ["identification du document", "référence du document"],
    "CHECKING AND APPROVAL": ["vérification et approbation", "approbation", "vérification"],
    "HUMAN-MACHINE INTERFACES": ["interface homme-machine", "ihm", "interface homme machine"],
    "ELECTRICAL INTERFACES": ["interfaces électriques", "alimentation"],
    "MECHANICAL INTERFACES": ["interfaces mécaniques", "fixation"],
    "NETWORK INTERFACES": ["interfaces réseau", "interfaces reseau", "réseau", "can", "lin"],
}
# Merge French aliases
for canonical, french_aliases in FRENCH_ALIASES.items():
    if canonical in SECTION_ALIASES:
        SECTION_ALIASES[canonical].extend(french_aliases)

# Build reverse lookup: alias → canonical name (rebuilt after French merge)
_ALIAS_TO_SECTION: Dict[str, str] = {}
for canonical, aliases in SECTION_ALIASES.items():
    _ALIAS_TO_SECTION[canonical.lower()] = canonical
    for alias in aliases:
        _ALIAS_TO_SECTION[alias.lower()] = canonical

# English stop words to filter from word-overlap matching
_STOP_WORDS = {"the", "a", "an", "is", "are", "was", "were", "be", "been", "being",
               "have", "has", "had", "do", "does", "did", "will", "would", "could",
               "should", "may", "might", "can", "shall", "must", "to", "of", "in",
               "for", "on", "with", "at", "by", "from", "as", "into", "about",
               "what", "how", "when", "where", "which", "who", "whom", "whose",
               "i", "me", "my", "we", "our", "you", "your", "he", "she", "it", "its",
               "they", "them", "their", "this", "that", "these", "those", "not", "no",
               "if", "then", "else", "and", "or", "but", "so", "just", "also",
               "very", "really", "only", "some", "any", "all", "each", "every",
               "le", "la", "les", "de", "des", "du", "un", "une", "dans", "pour",
               "avec", "sur", "sous", "que", "qui", "quoi", "est", "sont", "pas",
               "je", "tu", "il", "elle", "nous", "vous", "ils", "elles", "mon", "ma",
               "mes", "ton", "ta", "tes", "son", "sa", "ses", "ce", "cet", "cette",
               "ces", "leur", "leurs", "faire", "mettre", "écrire", "rédiger", "dois"}

# Sort by key length descending so longer aliases match first
_ALIAS_TO_SECTION_SORTED = sorted(_ALIAS_TO_SECTION.items(), key=lambda x: -len(x[0]))


def _content_words(text: str) -> set:
    """Extract content words (excluding stop words) from text."""
    words = set(re.findall(r"[a-zàâäéèêëîïôöùûüçñ]+", text.lower()))
    return words - _STOP_WORDS


# ── Section guidance data structure ─────────────────────────────────
@dataclass
class SectionGuidance:
    """Complete guidance for a single CTS section."""
    section_name: str
    # From template
    template_instruction: str = ""          # the red text / <<...>> instruction
    template_preview: str = ""              # first 500 chars of template section
    template_placeholders: List[str] = field(default_factory=list)
    # From writing guide
    guide_purpose: str = ""                 # what this section is for
    guide_content: List[str] = field(default_factory=list)  # key guidance paragraphs
    guide_rules: List[Tuple[str, str]] = field(default_factory=list)  # (rule_id, rule_text)
    guide_examples: List[str] = field(default_factory=list)
    guide_dos: List[str] = field(default_factory=list)
    guide_donts: List[str] = field(default_factory=list)
    # Source metadata
    source_template: bool = False
    source_guide: bool = False


# ── Extract template instructions for each section ──────────────────
def _extract_template_section_instructions(rules: ExtractedRules) -> Dict[str, str]:
    """Extract the <<...>> instruction text for each section from the template."""
    instructions: Dict[str, str] = {}
    tmpl_lines = rules.template_text.split("\n")
    # Build section positions
    allcaps_re = re.compile(r"^([A-Z][A-Z0-9\s/()\-&,:;.\u2013\u2014]{2,})$")
    sections_pos = []
    for i, line in enumerate(tmpl_lines):
        stripped = line.strip()
        m = allcaps_re.match(stripped)
        if m:
            name = m.group(1).strip().rstrip(":")
            letters = [c for c in name if c.isalpha()]
            if letters:
                upper_ratio = sum(1 for c in letters if c.isupper()) / len(letters)
                if upper_ratio >= 0.8 and len(name) >= 3:
                    sections_pos.append((name, i))

    for idx, (sec_name, line_num) in enumerate(sections_pos):
        next_line = sections_pos[idx + 1][1] if idx + 1 < len(sections_pos) else len(tmpl_lines)
        section_text = "\n".join(tmpl_lines[line_num + 1:next_line])
        # Extract <<...>> text
        placeholder_re = re.compile(r"<<([^>]*)>>")
        instr_parts = []
        for m in placeholder_re.finditer(section_text):
            instr_parts.append(m.group(1).strip())
        instructions[sec_name] = " | ".join(instr_parts) if instr_parts else section_text[:300]

    return instructions


# ── Extract writing guide content for each section ──────────────────
def _extract_guide_section_content(rules: ExtractedRules) -> Dict[str, List[str]]:
    """
    Extract the writing guide's content for each section.
    Returns a dict mapping canonical section name → list of relevant guide lines.
    """
    guide_lines = rules.guide_text.split("\n")

    # Find the drafting guide start (after TOC/meta)
    drafting_start = 0
    for i, line in enumerate(guide_lines):
        if "drafting guide" in line.lower() or ("0.9" in line and "general writing rules" in line.lower()):
            drafting_start = i
            break

    # Section boundaries in the writing guide (from its own TOC numbering)
    # Key guide sections (numbered as they appear in the writing guide TOC):
    # 0. PREAMBLE (document identification, history, etc.)
    # 0.9 GENERAL WRITING RULES
    # 1. PURPOSE
    # 2. SCOPE → 2.1 System development context, 2.2 General description
    # 3. Quoted documents → 3.1 Reference docs, 3.2 Applicable docs
    # 4. Terminology → 4.1 Glossary, 4.2 Acronyms, 4.3 Writing conventions
    # 5. Requirements → 5.1-5.6 (all requirement types)

    # Build a mapping of heading patterns to canonical names
    GUIDE_HEADING_MAP = [
        # (pattern regex, canonical section, priority)
        (re.compile(r"^\s*(?:0\.?\s*)?(?:PREAMBLE|0\.\s+PREAMBLE)", re.IGNORECASE), "PREAMBLE", 0),
        (re.compile(r"^\s*0\.\d+\.?\s+TITLE OF THE CTS", re.IGNORECASE), "DOCUMENT IDENTIFICATION", 1),
        (re.compile(r"^\s*0\.\d+\.?\s+REFERENCE", re.IGNORECASE), "DOCUMENT REFERENCE", 2),
        (re.compile(r"^\s*0\.\d+\.?\s+PAGE FOOTERS", re.IGNORECASE), "PAGE FOOTERS", 3),
        (re.compile(r"^\s*0\.\d+\.?\s+(?:CONFIDENTIALITY|CHECKING AND APPROVAL)", re.IGNORECASE), "CHECKING AND APPROVAL", 4),
        (re.compile(r"^\s*0\.\d+\.?\s+HISTORY", re.IGNORECASE), "TABLE OF UPDATES", 5),
        (re.compile(r"^\s*0\.\d+\.?\s+PARTICIPANTS", re.IGNORECASE), "PARTICIPANTS", 6),
        (re.compile(r"^\s*0\.\d+\.?\s+SUMMARY", re.IGNORECASE), "PREAMBLE", 7),
        (re.compile(r"^\s*0\.\d+\.?\s+GENERAL WRITING RULES", re.IGNORECASE), "GENERAL WRITING RULES", 8),
        (re.compile(r"^\s*(?:1\.\s*)?(?:OBJET|PURPOSE)\b", re.IGNORECASE), "PURPOSE", 10),
        (re.compile(r"^\s*(?:2\.\s*)?(?:DOMAINE|SCOPE)\b", re.IGNORECASE), "SCOPE", 11),
        (re.compile(r"^\s*2\.\d+\.?\s*(?:contexte|system development context)", re.IGNORECASE), "SYSTEM DEVELOPMENT CONTEXT", 12),
        (re.compile(r"^\s*2\.\d+\.?\s*(?:description generale|general description)", re.IGNORECASE), "GENERAL DESCRIPTION OF THE SYSTEM", 13),
        (re.compile(r"^\s*2\.\d+\.\d+\.?\s*(?:role fonctionnel|system.*operational role)", re.IGNORECASE), "SYSTEM ROLES", 14),
        (re.compile(r"^\s*2\.\d+\.\d+\.?\s*(?:architecture physique|physical system architecture)", re.IGNORECASE), "PHYSICAL SYSTEM ARCHITECTURE", 15),
        (re.compile(r"^\s*2\.\d+\.\d+\.?\s*(?:diversite|system diversity)", re.IGNORECASE), "SYSTEM DIVERSITY", 16),
        (re.compile(r"^\s*(?:3\.\s*)?(?:DOCUMENTS CITES|QUOTED DOCUMENTS)\b", re.IGNORECASE), "QUOTED DOCUMENTS", 20),
        (re.compile(r"^\s*3\.\d+\.?\s*(?:documents de reference|reference documents)", re.IGNORECASE), "REFERENCE DOCUMENTS", 21),
        (re.compile(r"^\s*3\.\d+\.?\s*(?:documents applicables|applicable documents)", re.IGNORECASE), "APPLICABLE DOCUMENTS", 22),
        (re.compile(r"^\s*(?:4\.\s*)?(?:TERMINOLOGIE|TERMINOLOGY)\b", re.IGNORECASE), "TERMINOLOGY", 30),
        (re.compile(r"^\s*4\.\d+\.?\s*(?:glossaire|glossary)", re.IGNORECASE), "GLOSSARY", 31),
        (re.compile(r"^\s*4\.\d+\.?\s*(?:sigles|abbreviations|acronyms)", re.IGNORECASE), "ACRONYMS", 32),
        (re.compile(r"^\s*4\.\d+\.?\s*(?:conventions|writing conventions)", re.IGNORECASE), "WRITING CONVENTIONS", 33),
        (re.compile(r"^\s*(?:5\.\s*)?(?:EXIGENCES|REQUIREMENTS)\b", re.IGNORECASE), "REQUIREMENTS", 40),
        (re.compile(r"^\s*5\.\d+\.?\s*(?:exigences fonctionnelles|functional requirements)", re.IGNORECASE), "FUNCTIONAL REQUIREMENTS", 41),
        (re.compile(r"^\s*5\.\d+\.?\s*(?:exigences de performance|performance requirements)", re.IGNORECASE), "PERFORMANCE REQUIREMENTS", 42),
        (re.compile(r"^\s*5\.\d+\.?\s*(?:exigences d.interfaces externes|external interfaces requirements)", re.IGNORECASE), "EXTERNAL INTERFACES REQUIREMENTS", 43),
        (re.compile(r"^\s*5\.\d+\.\d+\.?\s*(?:interfaces reseaux|network interfaces)", re.IGNORECASE), "NETWORK INTERFACES", 44),
        (re.compile(r"^\s*5\.\d+\.\d+\.?\s*(?:interfaces electriques|electrical interfaces)", re.IGNORECASE), "ELECTRICAL INTERFACES", 45),
        (re.compile(r"^\s*5\.\d+\.\d+\.?\s*(?:interfaces mecaniques|mechanical interfaces)", re.IGNORECASE), "MECHANICAL INTERFACES", 46),
        (re.compile(r"^\s*5\.\d+\.\d+\.?\s*(?:interface homme|man.machine|hmi|mmi)", re.IGNORECASE), "HUMAN-MACHINE INTERFACES", 47),
        (re.compile(r"^\s*5\.\d+\.?\s*(?:exigences operationnelles|operational requirements)", re.IGNORECASE), "OPERATIONAL REQUIREMENTS", 50),
        (re.compile(r"^\s*5\.\d+\.\d+\.?\s*(?:profil de mission|mission profile)", re.IGNORECASE), "MISSION PROFILE", 51),
        (re.compile(r"^\s*5\.\d+\.\d+\.?\s*(?:duree de vie|lifetime)", re.IGNORECASE), "LIFETIME", 52),
        (re.compile(r"^\s*5\.\d+\.\d+\.?\s*(?:ergonomie|ergonomics)", re.IGNORECASE), "ERGONOMICS AND HUMAN FACTORS", 53),
        (re.compile(r"^\s*5\.\d+\.\d+\.?\s*(?:surete|dependability|safety|rams)", re.IGNORECASE), "RAMS REQUIREMENTS", 54),
        (re.compile(r"^\s*5\.\d+\.\d+\.?\s*(?:qualite produit|product quality)", re.IGNORECASE), "PRODUCT QUALITY", 55),
        (re.compile(r"^\s*5\.\d+\.?\s*(?:exigences de contrainte|constraint requirements)", re.IGNORECASE), "CONSTRAINT REQUIREMENTS", 60),
        (re.compile(r"^\s*5\.\d+\.\d+\.?\s*(?:conception et fabrication|design and manufacturing)", re.IGNORECASE), "DESIGN AND MANUFACTURING", 61),
        (re.compile(r"^\s*5\.\d+\.\d+\.?\s*(?:tracabilite|traceability)", re.IGNORECASE), "TRACEABILITY AND CONFIGURATION", 62),
        (re.compile(r"^\s*5\.\d+\.\d+\.?\s*(?:conditions d.environnement|environment conditions)", re.IGNORECASE), "ENVIRONMENT CONDITIONS", 63),
        (re.compile(r"^\s*5\.\d+\.?\s*(?:exigences d.integration|integration and validation)", re.IGNORECASE), "INTEGRATION AND VALIDATION REQUIREMENTS", 70),
        (re.compile(r"^\s*A\d+\s+LISTE DES ESSAIS", re.IGNORECASE), "IMPOSED ELEMENTS OF VALIDATION PLAN", 80),
        (re.compile(r"^\s*ANNEXE.*CHECK.?LIST.*ROBUSTESSE", re.IGNORECASE), "CHECKLIST", 90),
    ]
    GUIDE_HEADING_MAP.sort(key=lambda x: x[2])  # sort by priority

    # Walk guide lines and assign each to its section
    current_section = "PREAMBLE"
    content: Dict[str, List[str]] = defaultdict(list)

    for i in range(drafting_start, len(guide_lines)):
        line = guide_lines[i].strip()
        if not line:
            continue

        # Check if this line is a new section heading
        matched = False
        for pattern, canonical, _ in GUIDE_HEADING_MAP:
            if pattern.match(line) and len(line) < 100:
                current_section = canonical
                matched = True
                break
        if matched:
            continue

        # Skip TOC lines (contain tab + page number pattern)
        if re.search(r"\t\d+$", line):
            continue
        # Skip table-of-rules summary lines (start with |)
        if line.startswith("|"):
            continue

        content[current_section].append(line)

    return dict(content)


# ── Build the complete guidance knowledge base ──────────────────────
_guidance_cache: Optional[Dict[str, SectionGuidance]] = None


def _build_guidance_kb(force: bool = False) -> Dict[str, SectionGuidance]:
    """Build the section guidance knowledge base from source documents."""
    global _guidance_cache
    if _guidance_cache is not None and not force:
        return _guidance_cache

    rules = extract_all_rules(force=True)
    tmpl_instructions = _extract_template_section_instructions(rules)
    guide_content = _extract_guide_section_content(rules)

    kb: Dict[str, SectionGuidance] = {}

    for canonical_name in SECTION_ALIASES:
        guidance = SectionGuidance(section_name=canonical_name)

        # 1. Template instructions
        # Find matching template section
        for tmpl_name, instr_text in tmpl_instructions.items():
            if canonical_name.lower() in tmpl_name.lower() or tmpl_name.lower() in canonical_name.lower():
                guidance.template_instruction = instr_text
                guidance.source_template = True
                break

        # 2. Writing guide content
        # Direct match
        if canonical_name in guide_content:
            guide_lines = guide_content[canonical_name]
            guidance.guide_content = guide_lines[:30]  # first 30 lines
            guidance.source_guide = True

            # Extract section purpose from first few lines
            purpose_lines = []
            for line in guide_lines[:10]:
                if line.startswith("Standard") or line.startswith("This") or line.startswith("The"):
                    purpose_lines.append(line)
                elif len(purpose_lines) == 0:
                    purpose_lines.append(line)
                if len(purpose_lines) >= 3:
                    break
            guidance.guide_purpose = " ".join(purpose_lines)[:400]

            # Extract rules applicable to this section
            for line in guide_lines:
                m = re.match(r"^(R\d{1,2}|P\d{1,2})\s*:?\s*(.+)", line)
                if m:
                    rid = m.group(1)
                    rule = get_rule_by_id(rid)
                    if rule:
                        guidance.guide_rules.append((rid, rule.text[:200]))
                    else:
                        guidance.guide_rules.append((rid, m.group(2)[:200]))

            # Extract examples
            for j, line in enumerate(guide_lines):
                if line.lower().startswith("example"):
                    example_text = line
                    # Gather next few lines
                    for k in range(j + 1, min(j + 5, len(guide_lines))):
                        if guide_lines[k].strip() and not guide_lines[k].startswith(("R", "P", "Standard")):
                            example_text += " | " + guide_lines[k].strip()
                    guidance.guide_examples.append(example_text[:300])
                    if len(guidance.guide_examples) >= 3:
                        break

            # Extract dos and don'ts
            for line in guide_lines:
                if "prohibited" in line.lower() or "do not" in line.lower() or "should not" in line.lower() or "never" in line.lower():
                    if len(guidance.guide_donts) < 5:
                        guidance.guide_donts.append(line[:200])
                if "mandatory" in line.lower() or "must" in line.lower() or "shall" in line.lower() or "required" in line.lower():
                    if len(guidance.guide_dos) < 5:
                        guidance.guide_dos.append(line[:200])

        # 3. For the massive REQUIREMENTS section, also check sub-sections
        # that map to the requirements block
        if canonical_name in ("FUNCTIONAL REQUIREMENTS", "PERFORMANCE REQUIREMENTS",
                              "EXTERNAL INTERFACES REQUIREMENTS", "OPERATIONAL REQUIREMENTS",
                              "CONSTRAINT REQUIREMENTS", "INTEGRATION AND VALIDATION REQUIREMENTS"):
            if canonical_name in guide_content and guide_content[canonical_name]:
                sub_lines = guide_content[canonical_name]
                guidance.guide_content = sub_lines[:50]
                guidance.source_guide = True
                # Extract rules
                for line in sub_lines:
                    m = re.match(r"^(R\d{1,2}|P\d{1,2})\s*:?\s*(.+)", line)
                    if m:
                        rid = m.group(1)
                        rule = get_rule_by_id(rid)
                        if rule:
                            guidance.guide_rules.append((rid, rule.text[:200]))
                        else:
                            guidance.guide_rules.append((rid, m.group(2)[:200]))

        # Also check the huge REQUIREMENTS section for rules that belong here
        if "REQUIREMENTS" in guide_content:
            req_lines = guide_content["REQUIREMENTS"]
            if canonical_name not in ("REQUIREMENTS", "PREAMBLE"):
                # Look for subsection headers in the REQUIREMENTS block
                subsection_keywords = {
                    "FUNCTIONAL REQUIREMENTS": ["fonctionnelles", "functional requirement"],
                    "PERFORMANCE REQUIREMENTS": ["performance requirement", "de performance"],
                    "EXTERNAL INTERFACES REQUIREMENTS": ["interface", "interfaces externes"],
                    "OPERATIONAL REQUIREMENTS": ["operationnel", "operational requirement", "mission profile"],
                    "CONSTRAINT REQUIREMENTS": ["contrainte", "constraint requirement"],
                    "INTEGRATION AND VALIDATION REQUIREMENTS": ["integration", "validation"],
                }
                kw = subsection_keywords.get(canonical_name, [])
                for line in req_lines:
                    if any(k in line.lower() for k in kw):
                        m = re.match(r"^(R\d{1,2}|P\d{1,2})\s*:?\s*(.+)", line)
                        if m:
                            rid = m.group(1)
                            rule = get_rule_by_id(rid)
                            if rule and rid not in [r[0] for r in guidance.guide_rules]:
                                guidance.guide_rules.append((rid, rule.text[:200]))

        kb[canonical_name] = guidance

    _guidance_cache = kb
    return kb


# ── Section name detection from user question ───────────────────────
def detect_section_from_question(question: str) -> Optional[str]:
    """
    Detect which CTS section the user is asking about.
    Returns the canonical section name, or None if no section detected.
    """
    q_lower = question.lower().strip()

    # Try exact alias matches first (longest match wins, sorted by length desc)
    for alias, canonical in _ALIAS_TO_SECTION_SORTED:
        if alias in q_lower:
            return canonical

    # Try partial word matches using content words only (no stop words)
    best_match = None
    best_score = 0
    q_words = _content_words(q_lower)

    for canonical, aliases in SECTION_ALIASES.items():
        for alias in aliases:
            alias_words = _content_words(alias)
            if not alias_words:
                continue
            overlap = q_words & alias_words
            score = len(overlap) / len(alias_words)
            if score > best_score:
                best_score = score
                best_match = canonical

    if best_score >= 0.4:
        return best_match

    return None


# ── Exclusion patterns: questions that contain section keywords but are NOT guidance ──
_FACTUAL_EXCLUSION_RE = re.compile(
    r"\b(?:"
    r"what\s+is\s+the\s+(?:voltage|current|power|temperature|frequency|speed|weight|size|price|cost|color|range|value|level|rate)\b"
    r"|how\s+(?:much|many|long|far|fast|heavy|big|often)\b"
    r"|is\s+the\s+\w+\s+(?:located|positioned|situated|placed|found|mounted)\b"
    r"|where\s+is\b"
    r"|when\s+(?:does|is|will)\b"
    r"|who\s+(?:is|writes|checks|approves)\b"
    r"|what\s+does\s+\w+\s+(?:mean|do|look like|contain|have)\b"
    r"|what\s+are\s+the\s+(?:dimensions|measurements|tolerances|specifications|parameters|inputs|outputs)\b"
    r"|how\s+does\s+(?:the|it|this|a)\s+\w+\s+(?:work|function|operate|behave|respond)\b"
    r"|what\s+happens\s+(?:when|if|during|after|before)\b"
    r"|what\s+is\s+(?:the\s+)?(?:price|cost|color|weight|size|meaning|definition)\b"
    r"|what\s+is\s+(?:the\s+)?\w+\s+(?:signal|protocol|voltage|current|frequency|speed|power)\b"
    r"|how\s+(?:does|is)\s+(?:the\s+)?\w+\s+(?:signal|protocol)\s+(?:work|function|operate)\b"
    r"|what\s+(?:triggers|activates|causes|drives|controls)\s+the\b"
    r"|what\s+is\s+the\s+\w+\s+(?:used\s+for|made\s+of|composed\s+of)\b"
    r")",
    re.IGNORECASE,
)

# ── Question type detection regex patterns ─────────────────────────
_SECTION_GUIDANCE_RE = re.compile(
    r"\b(?:"
    r"what\s+(?:should|do|must|to|can)\s+(?:I|we|you)\s+(?:put|write|include|add|fill|specify|describe)\s+in\s+(?:the\s+)?"
    r"|how\s+(?:to|do|should|can)\s+(?:I|we|you)\s+(?:write|fill|complete|draft|structure|handle|approach|do)\s+(?:the\s+)?"
    r"|what\s+(?:is|are)\s+(?:the\s+)?(?:content|purpose|goal|objective)\s+of\s+(?:the\s+)?"
    r"|what\s+(?:goes|belongs|should\s+go|should\s+be)\s+in\s+(?:the\s+)?"
    r"|what\s+(?:does|do)\s+(?:the\s+)?.*?(?:section|paragraph|chapter)\s+(?:contain|include|cover|need|require)\b"
    r"|how\s+(?:to|do|should|can)\s+(?:I|we|you)\s+(?:write|fill|complete|draft|structure|handle)\s+(?:the\s+)?"
    r"|guide\s+(?:me\s+)?(?:on|for|about)\s+(?:the\s+)?"
    r"|what\s+(?:should|must|need)\s+(?:the\s+)?.*?(?:section|paragraph|chapter)\s+(?:contain|include|have)\b"
    r"|help\s+(?:me\s+)?(?:with|on|about|write|fill)\s+(?:the\s+)?"
    r")",
    re.IGNORECASE,
)

_SIMPLE_GUIDANCE_RE = re.compile(
    r"\b(?:"
    r"what\s+(?:about|for|in)\s+(?:the\s+)?"
    r"|tell\s+me\s+(?:about|what)\s+(?:the\s+)?"
    r"|explain\s+(?:the\s+)?"
    r"|how\s+(?:about|for)\s+(?:the\s+)?"
    r"|what\s+is\s+(?:the\s+)?"
    r"|comment\s+(?:rédiger|écrire|remplir|faire)\s+(?:la\s+)?(?:section|partie|chapitre)\s+"
    r"|que\s+(?:mettre|écrire|faire)\s+(?:dans|pour)\s+(?:la\s+)?(?:section|partie|chapitre)\s+"
    r")",
    re.IGNORECASE,
)


def is_section_guidance_question(question: str) -> bool:
    """Return True if the user is asking for guidance about a specific CTS section."""
    q = (question or "").strip()
    if len(q) < 5:
        return False

    # Quick check: single word like "PURPOSE?" or "REQUIREMENTS?"
    if q.rstrip("? !.").upper() in SECTION_ALIASES:
        return True

    # Exclude factual/measurement questions that happen to contain section keywords
    if _FACTUAL_EXCLUSION_RE.search(q):
        return False

    # Check with the structured pattern
    if _SECTION_GUIDANCE_RE.search(q):
        return True

    # Check with the simpler pattern + verify a section is detected
    if _SIMPLE_GUIDANCE_RE.search(q):
        section = detect_section_from_question(q)
        return section is not None

    # Direct "how to write" / "what to put in" patterns (looser matching)
    how_to_patterns = [
        r"\b(?:how|what)\s+to\s+(?:write|put|include|fill|add|draft|create|make|do|structure)\s+(?:the\s+)?",
        r"\bwhat\s+(?:should|must|can|do|could)\s+(?:I|we|you|one)\s+(?:put|write|include|fill|add|draft)\s+(?:in\s+)?(?:the\s+)?",
        r"\bwhat\s+goes\s+in\s+",
        r"\bwhat\s+belongs\s+in\s+",
        r"\bwhat\s+(?:is|are)\s+(?:needed|required|expected)\s+(?:in|for)\s+(?:the\s+)?",
        r"\bhow\s+(?:should|must|can|do)\s+(?:I|we|you)\s+(?:write|fill|complete|draft|structure|approach|handle|do|prepare)\s+(?:the\s+)?",
        r"\bwhat\s+(?:must|should|shall|does)\s+(?:the\s+)?[\w\s]{2,40}?\s+(?:section|paragraph|chapter)\s+(?:contain|include|have|cover|specify|describe)\b",
        r"\bwhat\s+(?:must|should)\s+(?:the\s+)?[\w\s]{2,40}?\s+(?:contain|include)\b",
        r"\bque\s+(?:mettre|écrire|faire|rédiger|remplir)\s+(?:dans|pour|la|le|les)\s+",
        r"\bcomment\s+(?:rédiger|écrire|remplir|faire|structurer)\s+(?:la|le|les|une|un)\s+",
    ]
    for pattern in how_to_patterns:
        if re.search(pattern, q, re.IGNORECASE):
            section = detect_section_from_question(q)
            return section is not None

    return False


# ── Build the guidance answer ───────────────────────────────────────
def get_section_guidance(question: str) -> Optional[Dict]:
    """
    Get structured guidance for a CTS section from the user's question.

    Returns a dict with:
      - detected_section: the canonical section name
      - guidance: structured guidance content
      - answer: a natural-language answer (for the LLM to use)
    Or None if no section was detected.
    """
    section = detect_section_from_question(question)
    if not section:
        return None

    kb = _build_guidance_kb()
    guidance = kb.get(section)
    if not guidance or (not guidance.source_template and not guidance.source_guide):
        return None

    # Build structured guidance
    result = {
        "detected_section": section,
        "has_template": guidance.source_template,
        "has_guide": guidance.source_guide,
        "template_instruction": guidance.template_instruction[:600] if guidance.template_instruction else "",
        "guide_purpose": guidance.guide_purpose,
        "guide_content": guidance.guide_content[:15],
        "guide_rules": [{"rule_id": rid, "text": text} for rid, text in guidance.guide_rules],
        "guide_examples": guidance.guide_examples[:3],
        "guide_dos": guidance.guide_dos[:5],
        "guide_donts": guidance.guide_donts[:5],
    }

    # Build a natural-language answer that the LLM can present
    parts = []

    parts.append(f"## {section} — Section Guidance\n")

    if guidance.guide_purpose:
        parts.append(f"**Purpose**: {guidance.guide_purpose}\n")

    if guidance.template_instruction:
        parts.append(f"**Template instruction** (from the Stellantis CTS template):\n> {guidance.template_instruction}\n")

    if guidance.guide_rules:
        parts.append(f"**Applicable writing-guide rules** ({len(guidance.guide_rules)} rules):")
        for rid, text in guidance.guide_rules[:8]:
            parts.append(f"- **{rid}**: {text}")
        parts.append("")

    if guidance.guide_dos:
        parts.append("**Key requirements (dos):**")
        for do in guidance.guide_dos[:5]:
            parts.append(f"- {do}")
        parts.append("")

    if guidance.guide_donts:
        parts.append("**Things to avoid (don'ts):**")
        for dont in guidance.guide_donts[:5]:
            parts.append(f"- {dont}")
        parts.append("")

    if guidance.guide_examples:
        parts.append("**Examples from the writing guide:**")
        for ex in guidance.guide_examples[:3]:
            parts.append(f"- {ex}")
        parts.append("")

    if guidance.guide_content:
        parts.append("**Additional guidance** (from the writing guide):")
        for line in guidance.guide_content[:8]:
            if line and not line.startswith(("R", "P", "Standard", "Example", "Note:")):
                parts.append(f"- {line[:200]}")
        parts.append("")

    parts.append(f"---\n*Guidance extracted 100% from the Stellantis CTS template and writing guide.*")

    result["answer"] = "\n".join(parts)
    return result
