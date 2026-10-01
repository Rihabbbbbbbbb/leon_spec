"""
Unified DOCX report generator for specification validation.

This is the STANDARDIZED TEMPLATE that LEON always uses to deliver
specification validation reports to the user as a downloadable document.

Designed to be COMPACT and ENGINEER-FOCUSED (target: 3-6 pages):
  1. Summary          — verdict banner, key metadata, scores per axis, counts
  2. Issues to Fix    — errors then warnings, each with location,
                         evidence excerpt and suggested fix (the actionable core)
  3. Requirements Traceability — every requirement missing an upstream reference
  4. Semantic Analysis (AI) — meaning-based findings, human review required
  5. Analysis Scope   — rules checked, sources, method (audit trail)

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
    from docx.shared import Pt, RGBColor
    footer = section.footer
    footer.is_linked_to_previous = False
    p = footer.paragraphs[0] if footer.paragraphs else footer.add_paragraph()
    p.alignment = 1  # CENTER
    run = p.add_run("LEON — Validation Report | Page ")
    run.font.size = Pt(8)
    run.font.color.rgb = RGBColor(108, 117, 125)
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


def _add_running_header(section, file_name: str):
    """Add a slim running header (document title + file name) to every
    page after the cover, so context survives printing or scrolling to
    the middle of a long report."""
    from docx.shared import Pt, RGBColor
    header = section.header
    header.is_linked_to_previous = False
    p = header.paragraphs[0] if header.paragraphs else header.add_paragraph()
    p.alignment = 3  # RIGHT
    run = p.add_run(f"LEON Validation Report  ·  {file_name}")
    run.font.size = Pt(8)
    run.italic = True
    run.font.color.rgb = RGBColor(108, 117, 125)


def _set_default_font(doc, name: str = "Calibri", size: int = 10):
    """Apply a single consistent enterprise-standard font across the whole
    document (python-docx otherwise falls back to whatever the Normal
    style's underlying template default is, which reads as generic)."""
    from docx.oxml.ns import qn
    from docx.oxml import OxmlElement
    from docx.shared import Pt
    style = doc.styles["Normal"]
    style.font.name = name
    style.font.size = Pt(size)
    # Word looks up the East-Asian/complex-script font slot separately —
    # without this, some viewers keep rendering the old default typeface.
    rpr = style.element.get_or_add_rPr()
    rFonts = rpr.find(qn("w:rFonts"))
    if rFonts is None:
        rFonts = OxmlElement("w:rFonts")
        rpr.append(rFonts)
    rFonts.set(qn("w:eastAsia"), name)


def _add_masthead(doc, title: str, subtitle: str):
    """A full-width shaded banner (navy, matching the web UI's masthead)
    instead of plain centered text — the first thing the reader sees sets
    the tone of a designed report rather than a bare Word default."""
    from docx.shared import Pt, RGBColor
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.enum.table import WD_TABLE_ALIGNMENT

    table = doc.add_table(rows=2, cols=1)
    table.alignment = WD_TABLE_ALIGNMENT.CENTER

    title_cell = table.cell(0, 0)
    title_cell.text = title
    run = title_cell.paragraphs[0].runs[0]
    run.font.size = Pt(20)
    run.bold = True
    run.font.color.rgb = RGBColor(255, 255, 255)
    title_cell.paragraphs[0].alignment = WD_ALIGN_PARAGRAPH.CENTER
    _set_cell_shading(title_cell, "0B2545")

    subtitle_cell = table.cell(1, 0)
    subtitle_cell.text = subtitle
    run = subtitle_cell.paragraphs[0].runs[0]
    run.font.size = Pt(9)
    run.italic = True
    run.font.color.rgb = RGBColor(169, 188, 212)
    subtitle_cell.paragraphs[0].alignment = WD_ALIGN_PARAGRAPH.CENTER
    _set_cell_shading(subtitle_cell, "123061")
    return table


_VERDICT_LABELS = {
    "GOOD": "GOOD — Specification is compliant",
    "ACCEPTABLE_WITH_FIXES": "ACCEPTABLE — fixes needed before use",
    "NOT_RELIABLE": "NOT RELIABLE — thorough revision required",
    "NON_COMPLIANT": "NON-COMPLIANT — does not follow the CTS template",
}


def _verdict_color(verdict: str) -> tuple:
    """Return RGB color for a verdict."""
    return {
        "GOOD": (40, 167, 69),
        "ACCEPTABLE_WITH_FIXES": (176, 122, 0),
        "NOT_RELIABLE": (204, 85, 0),
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
        "warning": (176, 122, 0),
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
# PLAIN-LANGUAGE EXPLANATIONS
# ═══════════════════════════════════════════════════════════════════
# Translates a finding's technical message into a short, jargon-free
# sentence an engineer can understand without knowing the rule catalogue
# by heart. Matched by (check, rule_id) or check alone; falls back to the
# finding's own message when no specific phrasing is defined below.

import re as _re_plain


def _plain_language_explanation(f: Dict) -> str:
    check = f.get("check", "")
    rule_id = f.get("rule_id", "")
    message = f.get("message", "") or ""

    if check == "A_SECTION_COVERAGE":
        section = f.get("section", "")
        if f.get("severity") == "error":
            return (
                f"The section “{section}” does not exist anywhere in the document. "
                f"The CTS template requires it — even if there is nothing to say, the "
                f"section must be present (write “Not applicable” if needed)."
            )
        return (
            f"The recommended section “{section}” is missing. It is not "
            f"mandatory, but leaving it out makes the specification less complete."
        )

    if check == "B_SECTION_ORDER":
        return "The order of the sections does not follow the standard CTS template layout."

    if check == "C_PLACEHOLDER_RESIDUE":
        m = _re_plain.match(r"(\d+)\s+template placeholders", message)
        if m:
            return (
                f"{m.group(1)} template placeholder(s) of the form “<<...>>” "
                f"are still present and have not been replaced with real text."
            )
        m = _re_plain.match(r"(\d+)\s+unfilled template variable", message)
        if m:
            return (
                f"{m.group(1)} template variable(s), such as "
                f"“<component name>”, have not been filled in."
            )
        m = _re_plain.match(r"(\d+)\s+TBD", message)
        if m:
            return (
                f"{m.group(1)} “TBD/TBC/TODO/XXX” mention(s) remain — "
                f"values that are still waiting on a decision."
            )
        return message

    if check == "E_REQUIREMENT_LANGUAGE":
        if "No 'shall'" in message:
            return (
                "No requirement uses the word “shall”, the only word recognized "
                "as mandatory in a specification. Without it, there is no way to tell "
                "what is required from what is merely described."
            )
        m = _re_plain.search(r"Subjective words found.*?\['([^']+)'\]", message)
        if m:
            return (
                f"The word “{m.group(1)}” is too vague for a requirement "
                f"(not measurable, not testable) — a precise value is needed instead."
            )
        return message

    if check == "F_REQUIREMENT_IDS":
        return (
            "One or more requirements have no unique identifier "
            "(e.g. REF-PSP-XXX-001) — without one, they cannot be referenced "
            "elsewhere (tests, reviews, tracking)."
        )

    if check == "J_STANDARDS_CONSISTENCY":
        m = _re_plain.search(r"Standard/norm '([^']+)'.*?never cited", message)
        if m:
            return (
                f"The standard “{m.group(1)}” is declared as applicable, but "
                f"no requirement actually refers to it — the declaration is probably "
                f"outdated, or a requirement is missing."
            )
        m = _re_plain.search(r"Standard/norm '([^']+)'.*?NOT declared", message)
        if m:
            return (
                f"The standard “{m.group(1)}” is used by a requirement, but "
                f"it does not appear in the list of applicable documents — the "
                f"document's dependency list is therefore incomplete."
            )
        m = _re_plain.search(r"Standard/norm '([^']+)'.*?reference field is empty", message)
        if m:
            return (
                f"The standard “{m.group(1)}” is declared, but its "
                f"“Reference” field (the document/drawing number) is empty — "
                f"the actual document cannot be located."
            )
        m = _re_plain.search(r"Standard/norm '([^']+)'.*?unfilled placeholder", message)
        if m:
            return (
                f"The standard “{m.group(1)}” is declared, but its "
                f"“Reference” field still contains an unfilled template "
                f"placeholder (“<<...>>”) — the document number or revision "
                f"index was never entered."
            )
        return message

    if rule_id == "R02":
        return (
            "A requirement refers to a color (e.g. “red circle”) to convey "
            "meaning — but that information is lost if the document is printed in "
            "black and white."
        )

    if rule_id == "R13":
        return (
            "The Diversity section exists, but it does not present its variants as "
            "a table (possible values per criterion), as required by the writing guide."
        )

    if rule_id == "R15":
        return (
            "No “Design file” (the drawing or design record this component "
            "derives from) is cited among the reference documents. This document is "
            "what lets someone trace the component's technical choices back to their "
            "origin — without it, traceability to the upstream design is broken."
        )

    return message


# ═══════════════════════════════════════════════════════════════════
# MAIN DOCUMENT GENERATOR
# ═══════════════════════════════════════════════════════════════════

# Caps to keep the report short — the full machine-readable detail
# stays available in the JSON validationReport.
_MAX_PROBLEM_BLOCKS = 40      # detailed error/warning blocks
_EXCERPT_MAX = 180
_MSG_MAX = 300

# Findings are split into separate, clearly labeled tables by category —
# easier to scan than one long undifferentiated table. G_TRACEABILITY is
# deliberately excluded: it already gets its own full dedicated section
# ("Requirements Traceability") with a complete itemized list.
_PROBLEM_CATEGORIES = [
    (
        "Document Structure",
        ("A_SECTION_COVERAGE", "B_SECTION_ORDER"),
        "Mandatory sections that are missing, or sections that do not appear "
        "in the order expected by the CTS template.",
    ),
    (
        "Template Content Not Finalized",
        ("C_PLACEHOLDER_RESIDUE",),
        "Placeholders or “to be defined” markers left over from the CTS "
        "template that have not yet been replaced with real content.",
    ),
    (
        "Requirement Wording",
        ("D_REQUIREMENT_FORMAT", "E_REQUIREMENT_LANGUAGE", "F_REQUIREMENT_IDS"),
        "How the requirements are written: the verb used, their unique "
        "identifier, and how they are presented in a table.",
    ),
    (
        "Standards Cited",
        ("J_STANDARDS_CONSISTENCY",),
        "Consistency between the standards cited in requirements and the list "
        "of applicable documents: every standard used must be declared, and "
        "every standard declared must actually be used.",
    ),
    (
        "Other Writing Guide Rules",
        ("H_WRITING_GUIDE_RULES", "I_EXTENDED_WG_RULES"),
        "Specific points from the Stellantis writing guide: presentation, "
        "content expected per section, documentary consistency.",
    ),
]

def _add_annotated_spec_note(doc, _add_para) -> None:
    """
    Tell the reader which highlight colour marks the findings in the
    companion "highlighted spec" document, and why it is not yellow.
    """
    try:
        from app.qa.spec_annotator import HIGHLIGHT_COLOR_LABEL as colour
    except Exception:
        colour = "violet"
    _add_para(
        f"Note — “highlighted spec” document: every passage that needs "
        f"correction is highlighted there in {colour.upper()}. This color is "
        f"reserved for LEON: it is not used anywhere in the original specification "
        f"(which already uses yellow, pink, turquoise and green). Any {colour} "
        f"highlight therefore corresponds to a point raised in this report, and to "
        f"nothing else.",
        italic=True, size=9, color=(112, 48, 160)
    )


def generate_spec_validation_document(report: Dict) -> bytes:
    """
    Generate the standardized LEON validation report (compact DOCX).

    Args:
        report: The validation report dict from validate_with_evidence()

    Returns:
        DOCX file as bytes
    """
    from docx import Document
    from docx.shared import Pt, RGBColor, Cm
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.enum.table import WD_TABLE_ALIGNMENT

    doc = Document()
    _set_default_font(doc, "Calibri", 10)

    # ── Page setup ─────────────────────────────────────────
    section = doc.sections[0]
    section.top_margin = Cm(1.8)
    section.bottom_margin = Cm(1.8)
    section.left_margin = Cm(2)
    section.right_margin = Cm(2)

    # ── Helpers ────────────────────────────────────────────
    def _add_heading(text, level=1, color=(0, 51, 102)):
        h = doc.add_heading(text, level=level)
        h.paragraph_format.space_before = Pt(14)
        h.paragraph_format.space_after = Pt(6)
        for run in h.runs:
            run.font.color.rgb = RGBColor(*color)
            run.font.name = "Calibri"
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

    def _style_header_cell(cell, size=9):
        for run in cell.paragraphs[0].runs:
            run.bold = True
            run.font.color.rgb = RGBColor(255, 255, 255)
            run.font.size = Pt(size)
        _set_cell_shading(cell, "003366")

    # ── Report data ────────────────────────────────────────
    file_name = report.get("fileName", "N/A")
    verdict = report.get("verdict", "UNKNOWN")
    overall_score = report.get("overallScore", 0)
    counts = report.get("summaryCounts", {})
    scores = report.get("scores", {})
    findings = report.get("findings", [])
    detailed = report.get("detailed", {})
    errors = detailed.get("errors") or [f for f in findings if f.get("severity") == "error"]
    warnings = detailed.get("warnings") or [f for f in findings if f.get("severity") == "warning"]
    sections_missing = report.get("sectionsMissing", [])
    rules_used = report.get("rulesUsed", {})

    # ═══════════════════════════════════════════════════════
    # COVER — shaded masthead banner instead of plain centered text
    # ═══════════════════════════════════════════════════════
    _add_masthead(
        doc,
        "LEON — Specification Validation Report",
        f"{file_name}  ·  {datetime.datetime.now().strftime('%Y-%m-%d %H:%M')}  ·  "
        f"Stellantis Mechatronics Engineering",
    )
    doc.add_paragraph()
    _add_running_header(section, file_name)

    # ── How to read this report (orientation for first-time readers) ──
    _add_para(
        "How to read this report — Section 1 gives the overall verdict and "
        "scores. Section 2 lists every issue to fix, grouped by theme, each "
        "with its exact location, a plain-language explanation and how to fix "
        "it: this is the actionable core of the report. Section 3 (when "
        "present) lists requirements with no traceability to an upstream "
        "requirement or standard. Section 4 (when present) contains "
        "AI-assisted findings on meaning, which must be verified by a human "
        "reviewer before acting on them. Section 5 documents exactly what was "
        "checked, for audit purposes. Colors are consistent throughout: red = "
        "error, yellow = warning, green = passed/good.",
        italic=True, size=9, color=(90, 98, 108)
    )
    doc.add_page_break()

    # ═══════════════════════════════════════════════════════
    # 1. SUMMARY — verdict banner + scores + counts
    # ═══════════════════════════════════════════════════════
    _add_heading("1. Summary", level=2)

    # Verdict banner (single shaded cell)
    banner = doc.add_table(rows=1, cols=1)
    banner.style = "Table Grid"
    cell = banner.cell(0, 0)
    score_txt = f"{overall_score:.0%}" if isinstance(overall_score, (int, float)) else str(overall_score)
    cell.text = f"{_VERDICT_LABELS.get(verdict, verdict)}   —   Overall score: {score_txt}"
    for run in cell.paragraphs[0].runs:
        run.bold = True
        run.font.size = Pt(13)
        run.font.color.rgb = RGBColor(*_verdict_color(verdict))
    cell.paragraphs[0].alignment = WD_ALIGN_PARAGRAPH.CENTER
    _set_cell_shading(cell, _verdict_hex(verdict))
    doc.add_paragraph()

    # Counts (one horizontal row)
    count_data = [
        ("Errors", counts.get("errors", 0), "FFC7CE"),
        ("Warnings", counts.get("warnings", 0), "FFEB9C"),
        ("Passed Checks", counts.get("pass", 0), "C6EFCE"),
        ("Missing Sections", len(sections_missing), "FFC7CE" if sections_missing else "C6EFCE"),
    ]
    counts_table = doc.add_table(rows=2, cols=len(count_data))
    counts_table.style = "Table Grid"
    counts_table.alignment = WD_TABLE_ALIGNMENT.CENTER
    for ci, (label, value, hex_c) in enumerate(count_data):
        head = counts_table.cell(0, ci)
        head.text = label
        _style_header_cell(head)
        val_cell = counts_table.cell(1, ci)
        val_cell.text = str(value)
        val_cell.paragraphs[0].alignment = WD_ALIGN_PARAGRAPH.CENTER
        for run in val_cell.paragraphs[0].runs:
            run.bold = True
            run.font.size = Pt(12)
        _set_cell_shading(val_cell, hex_c)
    doc.add_paragraph()

    # Scores per axis (compact)
    score_rows = [
        ("structure", "Structure (template sections)", 0.25),
        ("section_order", "Section Order", 0.05),
        ("template_cleanliness", "Template Cleanliness", 0.10),
        ("requirements_quality", "Requirements Quality", 0.35),
        ("writing_guide_compliance", "Writing Guide Compliance", 0.25),
    ]
    score_table = doc.add_table(rows=len(score_rows) + 1, cols=3)
    score_table.style = "Table Grid"
    score_table.alignment = WD_TABLE_ALIGNMENT.CENTER
    for ci, header in enumerate(["Evaluation Axis", "Weight", "Score"]):
        _style_header_cell(score_table.cell(0, ci))
        score_table.cell(0, ci).text = header
        _style_header_cell(score_table.cell(0, ci))
    for ri, (key, label, weight) in enumerate(score_rows, 1):
        val = scores.get(key, 0)
        val_f = val if isinstance(val, (int, float)) else 0
        score_table.cell(ri, 0).text = label
        score_table.cell(ri, 1).text = f"{weight:.0%}"
        score_table.cell(ri, 2).text = f"{val_f:.0%}"
        hex_color = _score_hex(val_f)
        for ci in range(3):
            for run in score_table.cell(ri, ci).paragraphs[0].runs:
                run.font.size = Pt(9)
            _set_cell_shading(score_table.cell(ri, ci), hex_color)
    doc.add_paragraph()

    # ═══════════════════════════════════════════════════════
    # 2. ISSUES TO FIX — grouped by category for clarity
    # ═══════════════════════════════════════════════════════
    all_problems = [("ERROR", f) for f in errors] + [("WARNING", f) for f in warnings]
    # G_TRACEABILITY has its own dedicated section further down — never
    # repeat it here. K_SEMANTIC_ANALYSIS (AI-assisted, requires human
    # verification) also gets its own clearly-separate section — mixing
    # it into these deterministic tables would blur the trust boundary
    # between "a pattern was matched" and "an AI judged this".
    all_problems = [
        (lbl, f) for lbl, f in all_problems
        if f.get("check") not in ("G_TRACEABILITY", "K_SEMANTIC_ANALYSIS")
    ]
    n_problems = len(all_problems)
    doc.add_page_break()
    _add_heading(f"2. Issues to Fix ({n_problems})", level=2)

    if n_problems == 0:
        _add_para(
            "✅ No issues found — the document complies with the CTS "
            "template and the writing guide.",
            bold=True, size=11, color=(40, 167, 69)
        )
    else:
        _add_para(
            "Issues are grouped by theme below. For each one: where it is, a "
            "plain-language explanation, and how to fix it.",
            italic=True, size=9, color=(108, 117, 125)
        )
        _add_annotated_spec_note(doc, _add_para)
        doc.add_paragraph()

        col_widths = [Cm(0.9), Cm(2.2), Cm(3.2), Cm(6.2), Cm(4.4)]
        p_headers = ["#", "Severity", "Where (section / location)",
                     "Issue (plain language)", "How to Fix"]

        remaining_budget = _MAX_PROBLEM_BLOCKS

        def _render_problem_table(rows):
            """rows: list of (sev_label, finding) → renders one table, returns count shown."""
            nonlocal remaining_budget
            take = rows[:max(remaining_budget, 0)]
            if not take:
                return 0
            tbl = doc.add_table(rows=len(take) + 1, cols=5)
            tbl.style = "Table Grid"
            for ci, header in enumerate(p_headers):
                cell = tbl.cell(0, ci)
                cell.text = header
                _style_header_cell(cell)
                cell.width = col_widths[ci]
            for ri, (sev_label, f) in enumerate(take, 1):
                sev = f.get("severity", "warning")
                hex_c = _severity_hex(sev)
                where = f.get("section", "") or f.get("user_location", "") or "Entire document"
                explanation = _plain_language_explanation(f)[:_MSG_MAX]
                excerpt = (f.get("user_excerpt", "") or "")[:_EXCERPT_MAX]
                problem_txt = explanation + (f'\n“{excerpt}”' if excerpt else "")
                fix = f.get("fix_suggestion", "") or (f.get("why", "") or "")[:_MSG_MAX]
                rule_id = f.get("rule_id", "")
                sev_txt = sev_label + (f"\n{rule_id}" if rule_id else "")
                row_data = [str(ri), sev_txt, where, problem_txt, fix or "—"]
                for ci, val in enumerate(row_data):
                    cell = tbl.cell(ri, ci)
                    cell.text = str(val)
                    cell.width = col_widths[ci]
                    for para in cell.paragraphs:
                        for run in para.runs:
                            run.font.size = Pt(8)
                            if ci == 1:
                                run.bold = True
                                run.font.color.rgb = RGBColor(*_severity_rgb(sev))
                    _set_cell_shading(cell, hex_c)
            remaining_budget -= len(take)
            return len(take)

        for cat_idx, (cat_title, cat_checks, cat_intro) in enumerate(_PROBLEM_CATEGORIES, 1):
            cat_rows = [(lbl, f) for lbl, f in all_problems if f.get("check") in cat_checks]
            if not cat_rows:
                continue
            _add_heading(f"2.{cat_idx} {cat_title} ({len(cat_rows)})", level=3)
            _add_para(cat_intro, italic=True, size=8, color=(108, 117, 125))
            n_shown = _render_problem_table(cat_rows)
            if n_shown < len(cat_rows):
                _add_para(
                    f"({len(cat_rows) - n_shown} additional finding(s) in this "
                    f"category not detailed here — full list available in the "
                    f"online analysis.)",
                    italic=True, size=8, color=(108, 117, 125)
                )
            doc.add_paragraph()

        # Anything not covered by a known category (safety net for future check types)
        uncategorized = [
            (lbl, f) for lbl, f in all_problems
            if not any(f.get("check") in checks for _, checks, _ in _PROBLEM_CATEGORIES)
        ]
        if uncategorized:
            _add_heading(f"2.{len(_PROBLEM_CATEGORIES) + 1} Other Findings ({len(uncategorized)})", level=3)
            _render_problem_table(uncategorized)
            doc.add_paragraph()
    doc.add_paragraph()

    # NOTE: the former "3. Template Section Coverage" section was removed on
    # request — missing mandatory sections are already reported, with their
    # fix, in "2.1 Document Structure", so the standalone section only
    # repeated the same information.

    # ═══════════════════════════════════════════════════════
    # 3. REQUIREMENTS TRACEABILITY — every requirement with no input/
    # upstream reference, with its exact placement in the document.
    # Shown whenever ANY such requirement exists, even if the overall
    # traceability ratio is good enough to "pass" — a good overall score
    # must never hide the individual requirements that still lack one.
    # ═══════════════════════════════════════════════════════
    trace_findings = [f for f in findings if f.get("check") == "G_TRACEABILITY"]
    untraced_items = next((f.get("items") for f in trace_findings if f.get("items")), [])
    trace_finding = trace_findings[0] if trace_findings else {}

    if untraced_items:
        doc.add_page_break()
        _add_heading(
            f"3. Requirements Traceability ({len(untraced_items)} with no upstream reference)",
            level=2,
        )
        _add_para(
            "“Upstream reference” (or “Input requirement”) means the "
            "requirement, standard or document a requirement derives from — allowing "
            "its origin to be traced. Each row below is a requirement that has none, "
            "nor the mention “N/A” (the correct way to state that it "
            "deliberately has none). Without this, it is impossible to know where the "
            "requirement comes from or to assess the impact of an upstream change.",
            italic=True, size=9, color=(108, 117, 125)
        )
        if trace_finding.get("message"):
            _add_para(
                f"Overall summary: {trace_finding['message']}",
                italic=True, size=9, color=(108, 117, 125)
            )
        doc.add_paragraph()

        # Generous cap: on a real CTS spec the structural check can legitimately
        # find 150+ requirements with an empty "Input requirement" cell, and
        # truncating that list would hide real gaps the engineer has to fix.
        _MAX_TRACE_ROWS = 300
        shown_items = untraced_items[:_MAX_TRACE_ROWS]
        trace_table = doc.add_table(rows=len(shown_items) + 1, cols=4)
        trace_table.style = "Table Grid"
        trace_headers = ["Requirement", "Location", "Excerpt", "How to Fix"]
        for ci, header in enumerate(trace_headers):
            cell = trace_table.cell(0, ci)
            cell.text = header
            _style_header_cell(cell)
        col_widths_trace = [Cm(2.6), Cm(3.6), Cm(6.8), Cm(4.0)]
        fix_text = "Add a reference to the upstream requirement/document, or write “N/A” if this requirement deliberately derives from none."
        for ri, item in enumerate(shown_items, 1):
            row_data = [item.get("id", ""), item.get("location", ""), item.get("excerpt", ""), fix_text]
            for ci, val in enumerate(row_data):
                cell = trace_table.cell(ri, ci)
                cell.text = str(val)
                cell.width = col_widths_trace[ci]
                for run in cell.paragraphs[0].runs:
                    run.font.size = Pt(8)
        for ci in range(4):
            trace_table.cell(0, ci).width = col_widths_trace[ci]

        if len(untraced_items) > _MAX_TRACE_ROWS:
            doc.add_paragraph()
            _add_para(
                f"({len(untraced_items) - _MAX_TRACE_ROWS} additional requirement(s) "
                f"not listed here — full list available in the online analysis.)",
                italic=True, size=8, color=(108, 117, 125)
            )
        doc.add_paragraph()

    # NOTE: the former "5. Recommendations" section was removed on request —
    # it only restated counts already shown in the Summary and repeated the
    # per-issue "How to Fix" column of section 2.

    # ═══════════════════════════════════════════════════════
    # 4. SEMANTIC ANALYSIS (AI) — only rendered when
    # include_semantic_analysis actually produced findings. Kept
    # STRICTLY separate from section 2's deterministic tables: every row
    # here is an AI judgment on MEANING, not a pattern match, and must
    # never be mistaken for the same level of certainty.
    # ═══════════════════════════════════════════════════════
    semantic_findings = [f for f in findings if f.get("check") == "K_SEMANTIC_ANALYSIS"]
    if semantic_findings:
        doc.add_page_break()
        _add_heading(f"4. Semantic Analysis — AI ({len(semantic_findings)})", level=2)
        _add_para(
            "These findings come from an AI-assisted analysis, for writing-guide "
            "rules that require judging the MEANING of a text (not just whether it "
            "is present) — something a deterministic analysis cannot do reliably. "
            "Unlike the rest of this report, they are NOT guaranteed to be accurate: "
            "every row must be verified by a human reviewer before any action is taken.",
            italic=True, size=9, color=(155, 89, 24)
        )
        doc.add_paragraph()

        sem_table = doc.add_table(rows=len(semantic_findings) + 1, cols=4)
        sem_table.style = "Table Grid"
        sem_headers = ["Rule", "AI Verdict", "Explanation", "Excerpt Cited by the AI"]
        for ci, header in enumerate(sem_headers):
            cell = sem_table.cell(0, ci)
            cell.text = header
            _style_header_cell(cell)
        sem_col_widths = [Cm(1.8), Cm(2.6), Cm(7.6), Cm(4.9)]
        sem_verdict_labels = {
            "pass": "Compliant", "warning": "Possible Issue",
            "info": "Not Applicable / To Verify",
        }
        for ri, f in enumerate(semantic_findings, 1):
            sev = f.get("severity", "info")
            hex_c = _severity_hex(sev)
            verdict_label = sem_verdict_labels.get(sev, sev)
            explanation = (f.get("message", "") or "").replace("[AI analysis — to verify] ", "")[:_MSG_MAX]
            excerpt = (f.get("user_excerpt", "") or "")[:_EXCERPT_MAX]
            location = f.get("user_location", "") or ""
            row_data = [f.get("rule_id", ""), verdict_label, explanation]
            for ci, val in enumerate(row_data):
                cell = sem_table.cell(ri, ci)
                cell.text = str(val)
                cell.width = sem_col_widths[ci]
                for para in cell.paragraphs:
                    for run in para.runs:
                        run.font.size = Pt(8)
                _set_cell_shading(cell, hex_c)
            # Excerpt column: the excerpt text, plus a separate, visually
            # distinct paragraph naming WHERE it was extracted from — a
            # reviewer verifying an AI-cited excerpt needs to know which
            # document section to go check, not just the text (a plain
            # embedded "\n" inside cell.text does not render as a real
            # line break in Word — a genuine second paragraph is needed).
            excerpt_cell = sem_table.cell(ri, 3)
            excerpt_cell.text = excerpt or "—"
            excerpt_cell.width = sem_col_widths[3]
            for run in excerpt_cell.paragraphs[0].runs:
                run.font.size = Pt(8)
            if excerpt and location:
                loc_para = excerpt_cell.add_paragraph()
                loc_run = loc_para.add_run(f"Extracted from: {location}")
                loc_run.font.size = Pt(7)
                loc_run.font.italic = True
                loc_run.font.color.rgb = RGBColor(102, 102, 102)
            _set_cell_shading(excerpt_cell, hex_c)
        for ci in range(4):
            sem_table.cell(0, ci).width = sem_col_widths[ci]
        doc.add_paragraph()

    # ═══════════════════════════════════════════════════════
    # 5. ANALYSIS SCOPE (audit trail, compact)
    # ═══════════════════════════════════════════════════════
    doc.add_page_break()
    _add_heading("5. Analysis Scope", level=2)

    wg_count = rules_used.get("writing_guide_rules_count", 0)
    wg_checked = rules_used.get("writing_guide_rules_checked", 0)
    mandatory_count = rules_used.get("mandatory_sections_count", 0)
    checked_ids = rules_used.get("checked_rule_ids", [])
    source_docs = rules_used.get("source_documents", [])
    extraction_ok = rules_used.get("extraction_ok", True)
    text_length = report.get("textLength", 0)

    _add_para(
        f"Document analyzed in full ({text_length:,} characters extracted). "
        f"Checks: {mandatory_count} mandatory template sections + "
        f"{wg_checked}/{wg_count} writing guide rules "
        f"({counts.get('pass', 0)} passed checks). "
        f"Rule extraction: {'successful' if extraction_ok else 'WITH ERRORS'}.",
        size=9
    )
    if checked_ids:
        _add_para("Rules checked: " + ", ".join(checked_ids),
                  size=8, color=(108, 117, 125))
    unchecked_ids = rules_used.get("unchecked_rule_ids", [])
    if unchecked_ids:
        _add_para(
            f"Writing guide rules not covered by the automated analysis "
            f"({len(unchecked_ids)}): " + ", ".join(unchecked_ids)
            + " — to be checked manually.",
            size=8, color=(176, 122, 0)
        )
    if source_docs:
        _add_para("Reference documents: " + " ; ".join(source_docs),
                  size=8, color=(108, 117, 125))
    _add_para(
        "Method: deterministic evidence-based analysis — every finding cites "
        "the source rule and the exact excerpt from the document (double "
        "evidence). Full detail available in the online analysis (LEON interface).",
        italic=True, size=8, color=(108, 117, 125)
    )

    extraction_errors = rules_used.get("errors", [])
    if extraction_errors:
        _add_para("Rule extraction errors:", bold=True, size=8, color=(220, 53, 69))
        for err in extraction_errors:
            p = doc.add_paragraph(style="List Bullet")
            run = p.add_run(str(err))
            run.font.size = Pt(8)
            run.font.color.rgb = RGBColor(220, 53, 69)

    # ── Footer (page numbers) ──────────────────────────────
    _add_page_number_footer(section)

    # ── Save to bytes ───────────────────────────────────────
    buf = io.BytesIO()
    doc.save(buf)
    buf.seek(0)
    return buf.read()


# NOTE: the recommendation generator was removed together with the
# "5. Recommendations" report section — it only restated counts already
# shown in the Summary and the per-issue "How to Fix" column.
