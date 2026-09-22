"""
PDF report generator for validation reports.

Uses fpdf2 (pure Python, no C extensions) to generate a professional
PDF validation report from the evidence_comparator output.
"""
from __future__ import annotations

import io
from typing import Dict, List

try:
    from fpdf import FPDF
    _HAS_FPDF = True
except ImportError:
    _HAS_FPDF = False


def _severity_color(severity: str) -> tuple:
    """Return RGB color for a severity level."""
    return {
        "error": (220, 53, 69),
        "warning": (255, 193, 7),
        "pass": (40, 167, 69),
        "info": (23, 162, 184),
    }.get(severity, (108, 117, 125))


def _clean(text: str) -> str:
    """Sanitize text for latin-1 compatibility (fpdf2 Helvetica limitation)."""
    if not text:
        return ""
    # Replace common Unicode chars with ASCII equivalents
    replacements = {
        "\u2014": "-", "\u2013": "-", "\u2018": "'", "\u2019": "'", "\u201c": '"',
        "\u201d": '"', "\u2026": "...", "\u00a0": " ", "\u2192": "->",
        "\u2264": "<=", "\u2265": ">=", "\u00b0": " deg ", "\u00b1": "+/-",
        "\u00d7": "x", "\u00f7": "/", "\u2122": "(TM)", "\u00a9": "(c)",
        "\u00ae": "(R)", "\u2022": "-", "\u25cf": "o", "\u2610": "[ ]",
    }
    for old, new in replacements.items():
        text = text.replace(old, new)
    # Remove any remaining non-latin-1 chars
    return text.encode("latin-1", errors="replace").decode("latin-1")


def _verdict_color(verdict: str) -> tuple:
    """Return RGB color for a verdict."""
    return {
        "GOOD": (40, 167, 69),
        "ACCEPTABLE_WITH_FIXES": (255, 193, 7),
        "NOT_RELIABLE": (253, 126, 20),
        "NON_COMPLIANT": (220, 53, 69),
    }.get(verdict, (108, 117, 125))


def _safe_multi_cell(pdf, w, h, text, **kwargs):
    """
    Wrapper around pdf.multi_cell that handles the 'Not enough horizontal space'
    error by resetting X to left margin and truncating text if needed.
    """
    pdf.set_x(pdf.l_margin)
    try:
        pdf.multi_cell(w, h, text, **kwargs)
    except Exception:
        # Fallback: truncate text and try again
        pdf.set_x(pdf.l_margin)
        try:
            pdf.multi_cell(w, h, text[:100] + "...", **kwargs)
        except Exception:
            # Last resort: use cell instead of multi_cell
            pdf.set_x(pdf.l_margin)
            pdf.cell(0, h, text[:80], new_x="LMARGIN", new_y="NEXT")


def generate_validation_pdf(report: Dict) -> bytes:
    """
    Generate a professional PDF validation report.

    Args:
        report: The validation report dict from validate_with_evidence()

    Returns:
        PDF file as bytes
    """
    if not _HAS_FPDF:
        raise ImportError("fpdf2 is not installed. Install with: pip install fpdf2")

    pdf = FPDF(orientation="P", unit="mm", format="A4")
    pdf.set_auto_page_break(auto=True, margin=15)
    pdf.set_margins(10, 10, 10)
    _PAGE_WIDTH = 190  # A4 width 210mm - 2*10mm margins
    pdf.add_page()

    # ── Header ──────────────────────────────────────────────
    pdf.set_font("Helvetica", "B", 16)
    pdf.set_text_color(0, 51, 102)
    pdf.cell(0, 10, _clean("LEON - Specification Validation Report"), new_x="LMARGIN", new_y="NEXT")
    pdf.ln(2)

    # File name
    pdf.set_font("Helvetica", "", 10)
    pdf.set_text_color(80, 80, 80)
    pdf.cell(0, 6, _clean(f"Document: {report.get('fileName', 'N/A')}"), new_x="LMARGIN", new_y="NEXT")

    # Verdict box
    verdict = report.get("verdict", "UNKNOWN")
    score = report.get("overallScore", 0)
    r, g, b = _verdict_color(verdict)
    pdf.set_fill_color(r, g, b)
    pdf.set_text_color(255, 255, 255)
    pdf.set_font("Helvetica", "B", 14)
    pdf.cell(0, 10, _clean(f"  Verdict: {verdict}  |  Score: {score:.0%}"), fill=True, new_x="LMARGIN", new_y="NEXT")
    pdf.ln(5)

    # ── Score breakdown ─────────────────────────────────────
    pdf.set_font("Helvetica", "B", 12)
    pdf.set_text_color(0, 51, 102)
    pdf.cell(0, 8, "Score Breakdown", new_x="LMARGIN", new_y="NEXT")

    scores = report.get("scores", {})
    score_labels = {
        "structure": "Structure (Section Coverage)",
        "section_order": "Section Order",
        "template_cleanliness": "Template Cleanliness",
        "requirements_quality": "Requirements Quality",
        "writing_guide_compliance": "Writing Guide Compliance",
    }
    pdf.set_font("Helvetica", "", 10)
    pdf.set_text_color(0, 0, 0)
    for key, label in score_labels.items():
        val = scores.get(key, 0)
        bar_len = int(val * 20)
        bar = "[" + "=" * bar_len + ">" + " " * (20 - bar_len) + "]"
        pdf.cell(0, 6, _clean(f"  {label:<40} {bar} {val:.0%}"), new_x="LMARGIN", new_y="NEXT")

    pdf.ln(3)

    # ── Summary ─────────────────────────────────────────────
    pdf.set_font("Helvetica", "B", 12)
    pdf.set_text_color(0, 51, 102)
    pdf.cell(0, 8, "Summary", new_x="LMARGIN", new_y="NEXT")

    pdf.set_font("Helvetica", "", 9)
    pdf.set_text_color(50, 50, 50)
    summary = report.get("summary", "")
    _safe_multi_cell(pdf, _PAGE_WIDTH, 5, _clean(summary))
    pdf.ln(3)

    # ── Counts ──────────────────────────────────────────────
    counts = report.get("summaryCounts", {})
    pdf.set_font("Helvetica", "B", 10)
    pdf.set_text_color(0, 0, 0)
    counts_text = (
        f"  Errors: {counts.get('errors', 0)}  |  "
        f"Warnings: {counts.get('warnings', 0)}  |  "
        f"Passes: {counts.get('pass', 0)}  |  "
        f"Info: {counts.get('info', 0)}"
    )
    pdf.cell(0, 6, _clean(counts_text), new_x="LMARGIN", new_y="NEXT")
    pdf.ln(5)

    # ── Findings ────────────────────────────────────────────
    findings = report.get("findings", [])
    if findings:
        pdf.set_font("Helvetica", "B", 12)
        pdf.set_text_color(0, 51, 102)
        pdf.cell(0, 8, "Detailed Findings", new_x="LMARGIN", new_y="NEXT")
        pdf.ln(2)

        for i, f in enumerate(findings):
            severity = f.get("severity", "info")
            r, g, b = _severity_color(severity)
            check = f.get("check", "")
            rule_id = f.get("rule_id", "")
            message = f.get("message", "")
            source_rule = f.get("source_rule", "")
            user_excerpt = f.get("user_excerpt", "")
            why = f.get("why", "")
            fix = f.get("fix_suggestion", "")

            # Severity badge
            pdf.set_fill_color(r, g, b)
            pdf.set_text_color(255, 255, 255)
            pdf.set_font("Helvetica", "B", 8)
            pdf.cell(25, 5, _clean(f" {severity.upper()}"), fill=True)
            pdf.set_text_color(0, 0, 0)
            pdf.set_font("Helvetica", "B", 9)
            pdf.cell(0, 5, _clean(f"  [{rule_id}] {check}"), new_x="LMARGIN", new_y="NEXT")

            # Message
            pdf.set_font("Helvetica", "", 9)
            _safe_multi_cell(pdf, _PAGE_WIDTH, 4, _clean(f"  {message[:300]}"))

            # Source rule
            if source_rule:
                pdf.set_font("Helvetica", "I", 8)
                pdf.set_text_color(100, 100, 100)
                _safe_multi_cell(pdf, _PAGE_WIDTH, 4, _clean(f"  Source rule: {source_rule[:150]}"))

            # User excerpt
            if user_excerpt:
                pdf.set_font("Helvetica", "", 8)
                pdf.set_text_color(60, 60, 60)
                _safe_multi_cell(pdf, _PAGE_WIDTH, 4, _clean(f"  Document excerpt: {user_excerpt[:150]}"))

            # Why
            if why:
                pdf.set_font("Helvetica", "", 8)
                pdf.set_text_color(40, 40, 40)
                _safe_multi_cell(pdf, _PAGE_WIDTH, 4, _clean(f"  Why: {why[:150]}"))

            # Fix suggestion
            if fix:
                pdf.set_font("Helvetica", "B", 8)
                pdf.set_text_color(0, 100, 0)
                _safe_multi_cell(pdf, _PAGE_WIDTH, 4, _clean(f"  Fix: {fix[:150]}"))

            pdf.ln(2)
            pdf.set_draw_color(220, 220, 220)
            pdf.line(10, pdf.get_y(), 200, pdf.get_y())
            pdf.ln(2)

    # ── Rules metadata ──────────────────────────────────────
    rules_used = report.get("rulesUsed", {})
    if rules_used:
        pdf.ln(3)
        pdf.set_font("Helvetica", "B", 10)
        pdf.set_text_color(0, 51, 102)
        pdf.cell(0, 6, "Validation Metadata", new_x="LMARGIN", new_y="NEXT")
        pdf.set_font("Helvetica", "", 8)
        pdf.set_text_color(80, 80, 80)
        pdf.cell(0, 5, _clean(f"  Mandatory sections checked: {rules_used.get('mandatory_sections_count', 0)}"), new_x="LMARGIN", new_y="NEXT")
        pdf.cell(0, 5, _clean(f"  Writing guide rules: {rules_used.get('writing_guide_rules_count', 0)}"), new_x="LMARGIN", new_y="NEXT")
        pdf.cell(0, 5, _clean(f"  Rules checked: {rules_used.get('writing_guide_rules_checked', 0)}"), new_x="LMARGIN", new_y="NEXT")
        sources = rules_used.get("source_documents", [])
        if sources:
            pdf.cell(0, 5, _clean(f"  Source documents: {', '.join(sources)}"), new_x="LMARGIN", new_y="NEXT")

    # ── Footer ──────────────────────────────────────────────
    pdf.ln(5)
    pdf.set_font("Helvetica", "I", 7)
    pdf.set_text_color(150, 150, 150)
    pdf.cell(0, 5, "Generated by LEON - Stellantis CTS Validation Assistant | 100% evidence-based | No hallucination", new_x="LMARGIN", new_y="NEXT")

    # Output as bytes
    output = pdf.output()
    if isinstance(output, str):
        return output.encode("latin-1")
    return bytes(output)


# ═══════════════════════════════════════════════════════════════════
# Markdown → PDF conversion
# ═══════════════════════════════════════════════════════════════════
import re as _re


def _md_inline_to_text(text: str) -> str:
    """Convert markdown inline formatting to plain text for PDF."""
    # Bold **text** or __text__
    text = _re.sub(r"\*\*(.+?)\*\*", r"\1", text)
    text = _re.sub(r"__(.+?)__", r"\1", text)
    # Italic *text* or _text_
    text = _re.sub(r"\*(.+?)\*", r"\1", text)
    text = _re.sub(r"_(.+?)_", r"\1", text)
    # Inline code `text`
    text = _re.sub(r"`(.+?)`", r"\1", text)
    # Links [text](url) → text
    text = _re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)
    # Images ![alt](url) → alt
    text = _re.sub(r"!\[([^\]]*)\]\([^)]+\)", r"\1", text)
    return text


def generate_markdown_pdf(
    markdown_text: str,
    title: str = "LEON Report",
    subtitle: str = "",
) -> bytes:
    """
    Convert a markdown string to a professional PDF document.

    Supports: headings (#, ##, ###), bullet lists (-, *),
    numbered lists (1.), bold/italic inline, code blocks, tables,
    and horizontal rules (---).

    Args:
        markdown_text: The markdown content to convert.
        title: Document title for the header.
        subtitle: Optional subtitle below the title.

    Returns:
        PDF file as bytes.
    """
    if not _HAS_FPDF:
        raise ImportError("fpdf2 is not installed. Install with: pip install fpdf2")

    pdf = FPDF(orientation="P", unit="mm", format="A4")
    pdf.set_auto_page_break(auto=True, margin=15)
    pdf.set_margins(10, 10, 10)
    _PW = 190  # usable width
    pdf.add_page()

    # ── Header ──────────────────────────────────────────────
    pdf.set_font("Helvetica", "B", 16)
    pdf.set_text_color(0, 51, 102)
    pdf.cell(0, 10, _clean(title), new_x="LMARGIN", new_y="NEXT")
    if subtitle:
        pdf.set_font("Helvetica", "", 10)
        pdf.set_text_color(80, 80, 80)
        pdf.cell(0, 6, _clean(subtitle), new_x="LMARGIN", new_y="NEXT")
    pdf.ln(3)

    # ── Parse markdown line by line ─────────────────────────
    lines = markdown_text.split("\n")
    in_code_block = False
    in_table = False
    table_rows = []

    i = 0
    while i < len(lines):
        raw = lines[i]
        line = raw.rstrip()

        # Code block fence
        if line.strip().startswith("```"):
            if in_code_block:
                in_code_block = False
                pdf.ln(1)
            else:
                in_code_block = True
                pdf.set_font("Courier", "", 8)
                pdf.set_text_color(40, 40, 40)
            i += 1
            continue

        if in_code_block:
            pdf.set_font("Courier", "", 8)
            pdf.set_text_color(40, 40, 40)
            _safe_multi_cell(pdf, _PW, 4, _clean(raw))
            i += 1
            continue

        # Table detection
        if "|" in line and line.strip().startswith("|"):
            # Collect all table rows
            table_rows.append(line)
            # Look ahead for more table rows
            while i + 1 < len(lines) and "|" in lines[i + 1] and lines[i + 1].strip().startswith("|"):
                i += 1
                table_rows.append(lines[i])
            _render_table(pdf, table_rows, _PW)
            table_rows = []
            i += 1
            continue

        # Horizontal rule
        if _re.match(r"^-{3,}$", line.strip()) or _re.match(r"^\*{3,}$", line.strip()):
            pdf.ln(1)
            pdf.set_draw_color(180, 180, 180)
            y = pdf.get_y()
            pdf.line(10, y, 200, y)
            pdf.ln(2)
            i += 1
            continue

        # Headings
        heading_m = _re.match(r"^(#{1,6})\s+(.+)$", line)
        if heading_m:
            level = len(heading_m.group(1))
            text = _md_inline_to_text(heading_m.group(2).strip())
            sizes = {1: 14, 2: 12, 3: 11, 4: 10, 5: 10, 6: 9}
            sz = sizes.get(level, 10)
            pdf.set_font("Helvetica", "B", sz)
            pdf.set_text_color(0, 51, 102)
            pdf.ln(1)
            _safe_multi_cell(pdf, _PW, sz * 0.45, _clean(text))
            pdf.ln(1)
            i += 1
            continue

        # Bullet list item
        bullet_m = _re.match(r"^(\s*)[-*]\s+(.+)$", line)
        if bullet_m:
            indent = len(bullet_m.group(1))
            text = _md_inline_to_text(bullet_m.group(2).strip())
            x_start = 10 + min(indent, 6) * 4
            pdf.set_font("Helvetica", "", 9)
            pdf.set_text_color(40, 40, 40)
            # Draw bullet
            pdf.set_xy(x_start, pdf.get_y())
            pdf.cell(4, 5, "-")
            pdf.set_xy(x_start + 4, pdf.get_y() - 5)
            _safe_multi_cell(pdf, _PW - (x_start - 10) - 4, 5, _clean(text))
            i += 1
            continue

        # Numbered list item
        num_m = _re.match(r"^(\s*)(\d+)\.\s+(.+)$", line)
        if num_m:
            indent = len(num_m.group(1))
            num = num_m.group(2)
            text = _md_inline_to_text(num_m.group(3).strip())
            x_start = 10 + min(indent, 6) * 4
            pdf.set_font("Helvetica", "", 9)
            pdf.set_text_color(40, 40, 40)
            pdf.set_xy(x_start, pdf.get_y())
            pdf.cell(8, 5, f"{num}.")
            pdf.set_xy(x_start + 8, pdf.get_y() - 5)
            _safe_multi_cell(pdf, _PW - (x_start - 10) - 8, 5, _clean(text))
            i += 1
            continue

        # Blockquote
        if line.strip().startswith(">"):
            text = _md_inline_to_text(line.strip().lstrip(">").strip())
            pdf.set_font("Helvetica", "I", 9)
            pdf.set_text_color(100, 100, 100)
            pdf.set_x(15)
            _safe_multi_cell(pdf, _PW - 5, 5, _clean(text))
            i += 1
            continue

        # Empty line
        if not line.strip():
            pdf.ln(2)
            i += 1
            continue

        # Regular paragraph
        text = _md_inline_to_text(line.strip())
        pdf.set_font("Helvetica", "", 9)
        pdf.set_text_color(40, 40, 40)
        _safe_multi_cell(pdf, _PW, 5, _clean(text))
        i += 1

    # ── Footer ──────────────────────────────────────────────
    pdf.ln(5)
    pdf.set_font("Helvetica", "I", 7)
    pdf.set_text_color(150, 150, 150)
    pdf.cell(0, 5, _clean("Generated by LEON - Stellantis CTS Validation Assistant"), new_x="LMARGIN", new_y="NEXT")

    output = pdf.output()
    if isinstance(output, str):
        return output.encode("latin-1")
    return bytes(output)


def _render_table(pdf, rows: list, page_width: int):
    """Render a markdown table in the PDF."""
    # Parse cells
    parsed = []
    for row in rows:
        # Skip separator rows (|---|---|)
        if _re.match(r"^\|[\s\-:|]+\|$", row.strip()):
            continue
        cells = [c.strip() for c in row.strip().strip("|").split("|")]
        parsed.append(cells)

    if not parsed:
        return

    num_cols = max(len(r) for r in parsed)
    col_w = page_width / num_cols

    for ri, row_cells in enumerate(parsed):
        is_header = ri == 0
        for ci in range(num_cols):
            cell_text = _md_inline_to_text(row_cells[ci]) if ci < len(row_cells) else ""
            if is_header:
                pdf.set_font("Helvetica", "B", 8)
                pdf.set_fill_color(220, 230, 240)
                pdf.set_text_color(0, 51, 102)
            else:
                pdf.set_font("Helvetica", "", 8)
                pdf.set_fill_color(255, 255, 255)
                pdf.set_text_color(40, 40, 40)
            # Truncate long cell text
            if len(cell_text) > 60:
                cell_text = cell_text[:57] + "..."
            pdf.cell(col_w, 5, _clean(cell_text), border=1, fill=True)
        pdf.ln()
