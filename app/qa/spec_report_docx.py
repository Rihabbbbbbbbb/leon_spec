"""
Unified DOCX report generator for specification validation.

This is the STANDARDIZED TEMPLATE that LEON always uses to deliver
specification validation reports to the user as a downloadable document.

The document contains ALL analysis details in a consistent layout:
  1.  Title page header (LEON branding + report title)
  2.  Document metadata (file name, date, text length)
  3.  Executive summary (auto-generated text + verdict)
  4.  Score breakdown (5-axis scores with visual bars)
  5.  Summary counts (errors / warnings / passes / info)
  6.  Detailed findings (full double-evidence: source rule + user excerpt)
  7.  Section coverage (found vs missing mandatory sections)
  8.  Writing guide compliance (checked rules)
  9.  Recommendations (auto-generated action items)
  10. Validation metadata (rules used, source documents)
  11. Footer (LEON branding + page numbers)

Uses python-docx (already in requirements.txt) for Azure Function compatibility.
"""
from __future__ import annotations

import io
import datetime
from typing import Dict, List


# ═══════════════════════════════════════════════════════════════════
# DOCX HELPER FUNCTIONS
# ═══════════════════════════════════════════════════════════════════

def _set_cell_shading(cell, hex_color: str):
    """Apply background shading to a DOCX table cell."""
    from docx.oxml.ns import qn
    from docx.oxml import OxmlElement
    shading = OxmlElement("w:shd")
    shading.set(qn("w:fill"), hex_color)
    shading.set(qn("w:val"), "clear")
    cell._tc.get_or_add_tcPr().append(shading)


def _add_page_number_footer(section):
    """Add page numbers to the footer of a DOCX section."""
    from docx.oxml.ns import qn
    from docx.oxml import OxmlElement
    footer = section.footer
    footer.is_linked_to_previous = False
    p = footer.paragraphs[0] if footer.paragraphs else footer.add_paragraph()
    p.alignment = 1  # CENTER
    run = p.add_run("Page ")
    # Add PAGE field
    fldChar1 = OxmlElement("w:fldChar")
    fldChar1.set(qn("w:fldCharType"), "begin")
    instrText = OxmlElement("w:instrText")
    instrText.set(qn("xml:space"), "preserve")
    instrText.text = "PAGE"
    fldChar2 = OxmlElement("w:fldChar")
    fldChar2.set(qn("w:fldCharType"), "end")
    run._r.append(fldChar1)
    run._r.append(instrText)
    run._r.append(fldChar2)


def _verdict_color(verdict: str) -> tuple:
    """Return RGB color for a verdict."""
    return {
        "GOOD": (40, 167, 69),
        "ACCEPTABLE_WITH_FIXES": (255, 193, 7),
        "NOT_RELIABLE": (253, 126, 20),
        "NON_COMPLIANT": (220, 53, 69),
    }.get(verdict, (108, 117, 125))


def _verdict_hex(verdict: str) -> str:
    """Return hex color for a verdict (for cell shading)."""
    return {
        "GOOD": "C6EFCE",
        "ACCEPTABLE_WITH_FIXES": "FFEB9C",
        "NOT_RELIABLE": "FFD580",
        "NON_COMPLIANT": "FFC7CE",
    }.get(verdict, "D9D9D9")


def _severity_hex(severity: str) -> str:
    """Return hex color for a severity level (for cell shading)."""
    return {
        "error": "FFC7CE",
        "warning": "FFEB9C",
        "pass": "C6EFCE",
        "info": "D9E2F3",
    }.get(severity, "FFFFFF")


def _severity_rgb(severity: str) -> tuple:
    """Return RGB color for a severity level."""
    return {
        "error": (220, 53, 69),
        "warning": (255, 140, 0),
        "pass": (40, 167, 69),
        "info": (23, 162, 184),
    }.get(severity, (108, 117, 125))


def _score_hex(score: float) -> str:
    """Return hex color for a score value (0-1)."""
    if score >= 0.80:
        return "C6EFCE"
    elif score >= 0.60:
        return "FFEB9C"
    elif score >= 0.35:
        return "FFD580"
    else:
        return "FFC7CE"


# ═══════════════════════════════════════════════════════════════════
# MAIN DOCUMENT GENERATOR
# ═══════════════════════════════════════════════════════════════════

def generate_spec_validation_document(report: Dict) -> bytes:
    """
    Generate a unified DOCX document with ALL specification validation details.

    This is the STANDARDIZED TEMPLATE that LEON always uses to deliver
    specification validation reports to the user.

    Args:
        report: The validation report dict from validate_with_evidence()

    Returns:
        DOCX file as bytes
    """
    from docx import Document
    from docx.shared import Inches, Pt, RGBColor, Cm
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.enum.table import WD_TABLE_ALIGNMENT

    doc = Document()

    # ── Page setup ─────────────────────────────────────────
    section = doc.sections[0]
    section.top_margin = Cm(2)
    section.bottom_margin = Cm(2)
    section.left_margin = Cm(2)
    section.right_margin = Cm(2)

    # ── Helper: add styled heading ─────────────────────────
    def _add_heading(text, level=1, color=(0, 51, 102)):
        h = doc.add_heading(text, level=level)
        for run in h.runs:
            run.font.color.rgb = RGBColor(*color)
        return h

    def _add_para(text, bold=False, italic=False, size=10, color=None, align=None):
        p = doc.add_paragraph()
        run = p.add_run(text)
        run.font.size = Pt(size)
        run.bold = bold
        run.italic = italic
        if color:
            run.font.color.rgb = RGBColor(*color)
        if align is not None:
            p.alignment = align
        return p

    # ═══════════════════════════════════════════════════════
    # 1. TITLE PAGE / HEADER
    # ═══════════════════════════════════════════════════════
    title = doc.add_paragraph()
    title.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run = title.add_run("LEON")
    run.font.size = Pt(28)
    run.bold = True
    run.font.color.rgb = RGBColor(0, 51, 102)

    subtitle = doc.add_paragraph()
    subtitle.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run = subtitle.add_run("Rapport de Validation de Spécification CTS")
    run.font.size = Pt(16)
    run.font.color.rgb = RGBColor(0, 51, 102)

    tagline = doc.add_paragraph()
    tagline.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run = tagline.add_run("Assistant IA de Gouvernance & Validation — Stellantis Mechatronics Engineering")
    run.font.size = Pt(9)
    run.italic = True
    run.font.color.rgb = RGBColor(108, 117, 125)

    # Horizontal rule
    doc.add_paragraph("_" * 85).alignment = WD_ALIGN_PARAGRAPH.CENTER

    # ═══════════════════════════════════════════════════════
    # 2. DOCUMENT METADATA
    # ═══════════════════════════════════════════════════════
    _add_heading("Informations du Document", level=2)

    file_name = report.get("fileName", "N/A")
    text_length = report.get("textLength", 0)
    verdict = report.get("verdict", "UNKNOWN")
    overall_score = report.get("overallScore", 0)

    meta_items = [
        ("Fichier analysé", file_name),
        ("Date d'analyse", datetime.datetime.now().strftime("%Y-%m-%d à %H:%M")),
        ("Taille du texte extrait", f"{text_length:,} caractères"),
        ("Verdict global", verdict),
        ("Score global", f"{overall_score:.0%}" if isinstance(overall_score, (int, float)) else str(overall_score)),
    ]
    meta_table = doc.add_table(rows=len(meta_items), cols=2)
    meta_table.style = "Light List Accent 1"
    for i, (label, value) in enumerate(meta_items):
        meta_table.cell(i, 0).text = label
        meta_table.cell(i, 1).text = str(value)
        for run in meta_table.cell(i, 0).paragraphs[0].runs:
            run.bold = True

    doc.add_paragraph()

    # ═══════════════════════════════════════════════════════
    # 3. EXECUTIVE SUMMARY
    # ═══════════════════════════════════════════════════════
    _add_heading("1. Résumé Exécutif", level=2)

    summary_text = report.get("summary", "")
    if summary_text:
        _add_para(summary_text, size=10)

    doc.add_paragraph()

    # Verdict box (styled paragraph)
    v_color = _verdict_color(verdict)
    _add_para(f"Verdict: {verdict} ({overall_score:.0%})" if isinstance(overall_score, (int, float)) else f"Verdict: {verdict}",
              bold=True, size=14, color=v_color)

    doc.add_paragraph()

    # ═══════════════════════════════════════════════════════
    # 4. SCORE BREAKDOWN
    # ═══════════════════════════════════════════════════════
    _add_heading("2. Détail des Scores par Axe", level=2)

    scores = report.get("scores", {})
    score_labels = {
        "structure": "Structure (Couverture des Sections)",
        "section_order": "Ordre des Sections",
        "template_cleanliness": "Propreté du Template",
        "requirements_quality": "Qualité des Exigences",
        "writing_guide_compliance": "Conformité au Guide d'Écriture",
    }
    score_weights = {
        "structure": 0.25,
        "section_order": 0.05,
        "template_cleanliness": 0.10,
        "requirements_quality": 0.35,
        "writing_guide_compliance": 0.25,
    }

    score_table = doc.add_table(rows=len(score_labels) + 2, cols=4)
    score_table.style = "Table Grid"
    score_table.alignment = WD_TABLE_ALIGNMENT.CENTER

    # Header row
    s_headers = ["Axe d'Évaluation", "Poids", "Score", "Barre Visuelle"]
    for ci, header in enumerate(s_headers):
        cell = score_table.cell(0, ci)
        cell.text = header
        for run in cell.paragraphs[0].runs:
            run.bold = True
            run.font.color.rgb = RGBColor(255, 255, 255)
            run.font.size = Pt(10)
        _set_cell_shading(cell, "003366")

    # Data rows
    for ri, (key, label) in enumerate(score_labels.items(), 1):
        val = scores.get(key, 0)
        weight = score_weights.get(key, 0)
        bar_len = int(val * 20) if isinstance(val, (int, float)) else 0
        bar = "[" + "=" * bar_len + ">" + " " * (20 - bar_len) + "]"

        score_table.cell(ri, 0).text = label
        score_table.cell(ri, 1).text = f"{weight:.0%}"
        score_table.cell(ri, 2).text = f"{val:.0%}" if isinstance(val, (int, float)) else "0%"
        score_table.cell(ri, 3).text = bar

        hex_color = _score_hex(val if isinstance(val, (int, float)) else 0)
        for ci in range(4):
            _set_cell_shading(score_table.cell(ri, ci), hex_color)
        for run in score_table.cell(ri, 0).paragraphs[0].runs:
            run.bold = True
            run.font.size = Pt(9)
        for ci in range(1, 4):
            for run in score_table.cell(ri, ci).paragraphs[0].runs:
                run.font.size = Pt(9)

    # Overall row
    total_row = len(score_labels) + 1
    score_table.cell(total_row, 0).text = "SCORE GLOBAL"
    score_table.cell(total_row, 1).text = "100%"
    score_table.cell(total_row, 2).text = f"{overall_score:.0%}" if isinstance(overall_score, (int, float)) else "0%"
    score_table.cell(total_row, 3).text = ""
    for ci in range(4):
        for run in score_table.cell(total_row, ci).paragraphs[0].runs:
            run.bold = True
            run.font.size = Pt(10)
        _set_cell_shading(score_table.cell(total_row, ci), _verdict_hex(verdict))

    doc.add_paragraph()

    # ═══════════════════════════════════════════════════════
    # 5. SUMMARY COUNTS
    # ═══════════════════════════════════════════════════════
    _add_heading("3. Synthèse des Résultats", level=2)

    counts = report.get("summaryCounts", {})
    count_categories = [
        ("Erreurs", counts.get("errors", 0), (220, 53, 69), "FFC7CE"),
        ("Avertissements", counts.get("warnings", 0), (255, 140, 0), "FFEB9C"),
        ("Conformes", counts.get("pass", 0), (40, 167, 69), "C6EFCE"),
        ("Informations", counts.get("info", 0), (23, 162, 184), "D9E2F3"),
    ]

    count_table = doc.add_table(rows=len(count_categories) + 1, cols=2)
    count_table.style = "Table Grid"
    count_table.alignment = WD_TABLE_ALIGNMENT.CENTER

    c_headers = ["Type", "Nombre"]
    for ci, header in enumerate(c_headers):
        cell = count_table.cell(0, ci)
        cell.text = header
        for run in cell.paragraphs[0].runs:
            run.bold = True
            run.font.color.rgb = RGBColor(255, 255, 255)
            run.font.size = Pt(10)
        _set_cell_shading(cell, "003366")

    for ri, (label, count, rgb, hex_c) in enumerate(count_categories, 1):
        count_table.cell(ri, 0).text = label
        count_table.cell(ri, 1).text = str(count)
        for ci in range(2):
            _set_cell_shading(count_table.cell(ri, ci), hex_c)
        for run in count_table.cell(ri, 0).paragraphs[0].runs:
            run.bold = True
            run.font.size = Pt(10)
        for run in count_table.cell(ri, 1).paragraphs[0].runs:
            run.font.size = Pt(10)

    doc.add_paragraph()

    # ═══════════════════════════════════════════════════════
    # 6. DETAILED FINDINGS (with double evidence)
    # ═══════════════════════════════════════════════════════
    doc.add_page_break()
    _add_heading("4. Constats Détaillés (Double Preuve)", level=2)

    _add_para(
        "Chaque constat ci-dessous inclut la règle source extraite du template/guide "
        "ET l'extrait exact du document utilisateur, garantissant une traçabilité à 100%.",
        italic=True, size=9, color=(108, 117, 125)
    )
    doc.add_paragraph()

    findings = report.get("findings", [])
    if findings:
        # Group findings by severity
        errors = [f for f in findings if f.get("severity") == "error"]
        warnings = [f for f in findings if f.get("severity") == "warning"]
        passes = [f for f in findings if f.get("severity") == "pass"]
        infos = [f for f in findings if f.get("severity") == "info"]

        _add_para(
            f"Total: {len(findings)} constat(s) — "
            f"{len(errors)} erreur(s), {len(warnings)} avertissement(s), "
            f"{len(passes)} conforme(s), {len(infos)} information(s).",
            bold=True, size=10
        )
        doc.add_paragraph()

        # Findings table (compact overview)
        findings_table = doc.add_table(rows=len(findings) + 1, cols=5)
        findings_table.style = "Table Grid"

        f_headers = ["Sévérité", "Règle", "Catégorie", "Section", "Message"]
        for ci, header in enumerate(f_headers):
            cell = findings_table.cell(0, ci)
            cell.text = header
            for run in cell.paragraphs[0].runs:
                run.bold = True
                run.font.color.rgb = RGBColor(255, 255, 255)
                run.font.size = Pt(9)
            _set_cell_shading(cell, "003366")

        for ri, f in enumerate(findings, 1):
            sev = f.get("severity", "info")
            sev_label = sev.upper()
            row_data = [
                sev_label,
                f.get("rule_id", ""),
                f.get("check", ""),
                f.get("section", ""),
                f.get("message", "")[:200],
            ]
            hex_color = _severity_hex(sev)
            for ci, val in enumerate(row_data):
                cell = findings_table.cell(ri, ci)
                cell.text = str(val)
                for run in cell.paragraphs[0].runs:
                    run.font.size = Pt(8)
                _set_cell_shading(cell, hex_color)

        doc.add_page_break()

        # Detailed findings with full double-evidence
        _add_heading("Détail des Constats avec Double Preuve", level=3)

        for idx, f in enumerate(findings, 1):
            sev = f.get("severity", "info")
            sev_rgb = _severity_rgb(sev)
            rule_id = f.get("rule_id", "")
            check = f.get("check", "")
            message = f.get("message", "")
            source_rule = f.get("source_rule", "")
            source_doc = f.get("source_doc", "")
            user_excerpt = f.get("user_excerpt", "")
            user_location = f.get("user_location", "")
            why = f.get("why", "")
            fix = f.get("fix_suggestion", "")

            # Severity badge + rule ID
            _add_para(f"[{idx}] {sev.upper()} — {rule_id} ({check})", bold=True, size=10, color=sev_rgb)

            # Message
            if message:
                _add_para(f"Message: {message}", size=9)

            # Source rule (evidence 1)
            if source_rule:
                _add_para(f"⚖ Règle source ({source_doc}): {source_rule}", italic=True, size=9, color=(100, 100, 100))

            # User excerpt (evidence 2)
            if user_excerpt:
                _add_para(f"📄 Extrait du document: \"{user_excerpt}\"", size=9, color=(60, 60, 60))
            elif user_location:
                _add_para(f"📄 Localisation: {user_location}", size=9, color=(60, 60, 60))

            # Why it matters
            if why:
                _add_para(f"Pourquoi: {why}", size=9, color=(40, 40, 40))

            # Fix suggestion
            if fix:
                _add_para(f"✅ Correction suggérée: {fix}", bold=True, size=9, color=(0, 100, 0))

            doc.add_paragraph()  # spacing between findings
    else:
        _add_para(
            "✓ Aucun constat. Le document est parfaitement conforme au template et au guide d'écriture.",
            italic=True, color=(40, 167, 69), size=10
        )

    doc.add_paragraph()

    # ═══════════════════════════════════════════════════════
    # 7. SECTION COVERAGE
    # ═══════════════════════════════════════════════════════
    doc.add_page_break()
    _add_heading("5. Couverture des Sections Obligatoires", level=2)

    sections_found = report.get("sectionsFound", [])
    sections_missing = report.get("sectionsMissing", [])

    _add_para(
        f"Sections trouvées: {len(sections_found)} | Sections manquantes: {len(sections_missing)}",
        bold=True, size=10
    )
    doc.add_paragraph()

    if sections_found:
        _add_heading("Sections présentes dans le document", level=3)
        for s in sections_found:
            p = doc.add_paragraph(style="List Bullet")
            run = p.add_run(str(s))
            run.font.size = Pt(9)
            run.font.color.rgb = RGBColor(40, 167, 69)

    if sections_missing:
        doc.add_paragraph()
        _add_heading("Sections manquantes (obligatoires selon le template)", level=3)
        for s in sections_missing:
            p = doc.add_paragraph(style="List Bullet")
            run = p.add_run(str(s))
            run.font.size = Pt(9)
            run.font.color.rgb = RGBColor(220, 53, 69)
            run.bold = True

    doc.add_paragraph()

    # ═══════════════════════════════════════════════════════
    # 8. WRITING GUIDE COMPLIANCE
    # ═══════════════════════════════════════════════════════
    _add_heading("6. Conformité au Guide d'Écriture", level=2)

    rules_used = report.get("rulesUsed", {})
    mandatory_count = rules_used.get("mandatory_sections_count", 0)
    wg_rules_count = rules_used.get("writing_guide_rules_count", 0)
    wg_rules_checked = rules_used.get("writing_guide_rules_checked", 0)
    template_instructions = rules_used.get("template_instructions_count", 0)
    checked_ids = rules_used.get("checked_rule_ids", [])

    wg_items = [
        ("Sections obligatoires du template", str(mandatory_count)),
        ("Règles du guide d'écriture (total)", str(wg_rules_count)),
        ("Règles du guide vérifiées", str(wg_rules_checked)),
        ("Instructions du template extraites", str(template_instructions)),
    ]

    wg_table = doc.add_table(rows=len(wg_items) + 1, cols=2)
    wg_table.style = "Table Grid"

    for ci, header in enumerate(["Métrique", "Valeur"]):
        cell = wg_table.cell(0, ci)
        cell.text = header
        for run in cell.paragraphs[0].runs:
            run.bold = True
            run.font.color.rgb = RGBColor(255, 255, 255)
            run.font.size = Pt(10)
        _set_cell_shading(cell, "003366")

    for ri, (label, value) in enumerate(wg_items, 1):
        wg_table.cell(ri, 0).text = label
        wg_table.cell(ri, 1).text = value
        for run in wg_table.cell(ri, 0).paragraphs[0].runs:
            run.bold = True
            run.font.size = Pt(9)
        for run in wg_table.cell(ri, 1).paragraphs[0].runs:
            run.font.size = Pt(9)

    if checked_ids:
        doc.add_paragraph()
        _add_heading("Règles vérifiées (IDs)", level=3)
        # Display in columns of 5
        for i in range(0, len(checked_ids), 5):
            batch = checked_ids[i:i + 5]
            p = doc.add_paragraph()
            run = p.add_run("  ".join(batch))
            run.font.size = Pt(8)
            run.font.color.rgb = RGBColor(80, 80, 80)

    doc.add_paragraph()

    # ═══════════════════════════════════════════════════════
    # 9. RECOMMENDATIONS
    # ═══════════════════════════════════════════════════════
    doc.add_page_break()
    _add_heading("7. Recommandations", level=2)

    recommendations = _generate_recommendations(report)

    for i, rec in enumerate(recommendations, 1):
        p = doc.add_paragraph(style="List Number")
        run = p.add_run(rec)
        run.font.size = Pt(10)

    doc.add_paragraph()

    # ═══════════════════════════════════════════════════════
    # 10. VALIDATION METADATA
    # ═══════════════════════════════════════════════════════
    _add_heading("8. Métadonnées de Validation", level=2)

    source_docs = rules_used.get("source_documents", [])
    extraction_ok = rules_used.get("extraction_ok", True)
    extraction_errors = rules_used.get("errors", [])

    meta2_items = [
        ("Extraction des règles", "Réussie" if extraction_ok else "Erreurs détectées"),
        ("Documents source", ", ".join(source_docs) if source_docs else "N/A"),
        ("Méthode", "100% basée sur des preuves (déterministe, sans LLM)"),
        ("Politique de double preuve", "Chaque constat cite la règle source ET l'extrait utilisateur"),
    ]

    meta2_table = doc.add_table(rows=len(meta2_items), cols=2)
    meta2_table.style = "Light List Accent 1"
    for i, (label, value) in enumerate(meta2_items):
        meta2_table.cell(i, 0).text = label
        meta2_table.cell(i, 1).text = str(value)
        for run in meta2_table.cell(i, 0).paragraphs[0].runs:
            run.bold = True
            run.font.size = Pt(9)
        for run in meta2_table.cell(i, 1).paragraphs[0].runs:
            run.font.size = Pt(9)

    if extraction_errors:
        doc.add_paragraph()
        _add_heading("Erreurs d'extraction", level=3)
        for err in extraction_errors:
            p = doc.add_paragraph(style="List Bullet")
            run = p.add_run(str(err))
            run.font.size = Pt(8)
            run.font.color.rgb = RGBColor(220, 53, 69)

    doc.add_paragraph()

    # ═══════════════════════════════════════════════════════
    # FOOTER
    # ═══════════════════════════════════════════════════════
    doc.add_paragraph("_" * 85).alignment = WD_ALIGN_PARAGRAPH.CENTER

    footer_para = doc.add_paragraph()
    footer_para.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run = footer_para.add_run(
        f"Document généré par LEON — Specification Validation Assistant | "
        f"{datetime.datetime.now().strftime('%Y-%m-%d %H:%M')} | "
        f"Stellantis Mechatronics Engineering"
    )
    run.font.size = Pt(8)
    run.italic = True
    run.font.color.rgb = RGBColor(150, 150, 150)

    # Add page numbers to footer
    _add_page_number_footer(section)

    # ── Save to bytes ───────────────────────────────────────
    buf = io.BytesIO()
    doc.save(buf)
    buf.seek(0)
    return buf.read()


# ═══════════════════════════════════════════════════════════════════
# RECOMMENDATION GENERATOR
# ═══════════════════════════════════════════════════════════════════

def _generate_recommendations(report: Dict) -> List[str]:
    """Generate actionable recommendations based on the validation report."""
    recs: List[str] = []

    verdict = report.get("verdict", "UNKNOWN")
    counts = report.get("summaryCounts", {})
    errors = counts.get("errors", 0)
    warnings = counts.get("warnings", 0)
    scores = report.get("scores", {})
    sections_missing = report.get("sectionsMissing", [])
    findings = report.get("findings", [])

    # Verdict-based recommendations
    if verdict == "GOOD":
        recs.append(
            "✓ Le document est globalement conforme. Aucune action corrective majeure nécessaire. "
            "Continuer à maintenir la qualité pour les futures révisions."
        )
    elif verdict == "ACCEPTABLE_WITH_FIXES":
        recs.append(
            f"Corriger les {errors} erreur(s) identifiée(s) pour atteindre un verdict GOOD. "
            f"Les {warnings} avertissement(s) peuvent être traités en seconde priorité."
        )
    elif verdict == "NOT_RELIABLE":
        recs.append(
            f"Le document nécessite des révisions importantes ({errors} erreurs, {warnings} avertissements). "
            "Une révision approfondie du document est recommandée avant soumission."
        )
    else:  # NON_COMPLIANT
        recs.append(
            f"Le document est non conforme ({errors} erreurs critiques). "
            "Une réécriture significative est nécessaire, en suivant le template CTS et le guide d'écriture."
        )

    # Section coverage recommendations
    if sections_missing:
        recs.append(
            f"Ajouter les {len(sections_missing)} section(s) manquante(s): "
            + ", ".join(str(s) for s in sections_missing[:10])
            + ("..." if len(sections_missing) > 10 else "")
            + ". Ces sections sont obligatoires selon le template CTS."
        )

    # Score-based recommendations
    structure_score = scores.get("structure", 0)
    if structure_score < 0.60:
        recs.append(
            "Améliorer la structure du document en ajoutant les sections obligatoires du template CTS. "
            f"Score actuel: {structure_score:.0%}."
        )

    cleanliness_score = scores.get("template_cleanliness", 0)
    if cleanliness_score < 0.80:
        recs.append(
            "Supprimer les placeholders restants (<<...>>, TBD, XXX) du document. "
            f"Score de propreté: {cleanliness_score:.0%}."
        )

    req_quality_score = scores.get("requirements_quality", 0)
    if req_quality_score < 0.60:
        recs.append(
            "Améliorer la qualité des exigences: utiliser 'shall' pour les exigences obligatoires, "
            "attribuer un ID unique à chaque exigence, et assurer la traçabilité avec les exigences amont. "
            f"Score actuel: {req_quality_score:.0%}."
        )

    wg_score = scores.get("writing_guide_compliance", 0)
    if wg_score < 0.60:
        recs.append(
            "Consulter le guide d'écriture CTS pour les règles de formatage et de rédaction. "
            f"Score de conformité au guide: {wg_score:.0%}."
        )

    # Finding-based recommendations
    error_findings = [f for f in findings if f.get("severity") == "error"]
    if error_findings:
        # Group by check category
        check_groups = {}
        for f in error_findings:
            check = f.get("check", "unknown")
            check_groups.setdefault(check, []).append(f)

        for check, group_findings in check_groups.items():
            recs.append(
                f"Catégorie '{check}': {len(group_findings)} erreur(s) à corrérer. "
                f"Voir les constats détaillés (section 4) pour les corrections suggérées."
            )

    if not recs:
        recs.append(
            "✓ Aucune action corrective nécessaire. Le document est conforme au template et au guide d'écriture."
        )

    return recs