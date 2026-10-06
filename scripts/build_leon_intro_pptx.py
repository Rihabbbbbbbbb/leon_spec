#!/usr/bin/env python3
"""Build a short, non-technical briefing deck about LEON.

Audience: colleagues who do not work with AI.
Source of truth: the LEON Quality Analysis interface (spec, matrix, TDR).
"""
from __future__ import annotations

from pathlib import Path

from pptx import Presentation
from pptx.dml.color import RGBColor
from pptx.enum.shapes import MSO_SHAPE
from pptx.enum.text import MSO_ANCHOR, PP_ALIGN
from pptx.oxml.ns import qn
from pptx.util import Inches, Pt
from lxml import etree


# ── Stellantis / LEON visual language ────────────────────────────
NAVY = RGBColor(0x0B, 0x25, 0x45)
NAVY_2 = RGBColor(0x12, 0x30, 0x61)
BLUE = RGBColor(0x1D, 0x4E, 0xD8)
BLUE_SOFT = RGBColor(0xE8, 0xEE, 0xFB)
WHITE = RGBColor(0xFF, 0xFF, 0xFF)
INK = RGBColor(0x1C, 0x2B, 0x3A)
MUTED = RGBColor(0x5B, 0x6B, 0x7C)
LINE = RGBColor(0xDD, 0xE3, 0xEC)
OK = RGBColor(0x15, 0x73, 0x47)
NOK = RGBColor(0xB3, 0x26, 0x1E)
WARN = RGBColor(0x9A, 0x5B, 0x00)
PAGE = RGBColor(0xF4, 0xF7, 0xFB)
CARD = RGBColor(0xFF, 0xFF, 0xFF)
GREEN_BG = RGBColor(0xE8, 0xF5, 0xEE)
RED_BG = RGBColor(0xFB, 0xEC, 0xEA)
AMBER_BG = RGBColor(0xFB, 0xF3, 0xE6)

W = Inches(13.333)
H = Inches(7.5)
SLIDE_W = 13_333_000  # EMU used by pptx 16:9
SLIDE_H = 7_500_000


def rgb_hex(c: RGBColor) -> str:
    return f"{c[0]:02X}{c[1]:02X}{c[2]:02X}"


def set_run(run, text, size=18, bold=False, color=INK, font="Calibri"):
    run.text = text
    run.font.size = Pt(size)
    run.font.bold = bold
    run.font.color.rgb = color
    run.font.name = font


def add_textbox(slide, l, t, w, h, text, size=18, bold=False, color=INK,
                align=PP_ALIGN.LEFT, font="Calibri", anchor=MSO_ANCHOR.TOP):
    box = slide.shapes.add_textbox(l, t, w, h)
    tf = box.text_frame
    tf.word_wrap = True
    tf.auto_size = None
    try:
        tf._txBody.bodyPr.set("anchor", {
            MSO_ANCHOR.TOP: "t",
            MSO_ANCHOR.MIDDLE: "ctr",
            MSO_ANCHOR.BOTTOM: "b",
        }.get(anchor, "t"))
    except Exception:
        pass
    p = tf.paragraphs[0]
    p.alignment = align
    run = p.add_run()
    set_run(run, text, size, bold, color, font)
    return box


def set_shape_fill(shape, color: RGBColor, line=None):
    shape.fill.solid()
    shape.fill.fore_color.rgb = color
    if line is None:
        shape.line.fill.background()
    else:
        shape.line.color.rgb = line
        shape.line.width = Pt(1)


def add_rect(slide, l, t, w, h, fill, line=None, rounded=False):
    kind = MSO_SHAPE.ROUNDED_RECTANGLE if rounded else MSO_SHAPE.RECTANGLE
    sh = slide.shapes.add_shape(kind, l, t, w, h)
    set_shape_fill(sh, fill, line)
    if rounded:
        # Slightly tighter corners
        try:
            spPr = sh._element.spPr
            prst = spPr.find(qn("a:prstGeom"))
            if prst is not None:
                av = prst.find(qn("a:avLst"))
                if av is None:
                    av = etree.SubElement(prst, qn("a:avLst"))
                gd = etree.SubElement(av, qn("a:gd"))
                gd.set("name", "adj")
                gd.set("fmla", "val 8000")
        except Exception:
            pass
    return sh


def add_card_text(slide, l, t, w, h, title, body, accent=BLUE, title_size=16, body_size=13):
    add_rect(slide, l, t, w, h, CARD, LINE, rounded=True)
    add_rect(slide, l, t, Inches(0.08), h, accent)
    add_textbox(slide, l + Inches(0.22), t + Inches(0.14), w - Inches(0.34), Inches(0.38),
                title, size=title_size, bold=True, color=NAVY)
    add_textbox(slide, l + Inches(0.22), t + Inches(0.50), w - Inches(0.34), h - Inches(0.64),
                body, size=body_size, color=INK)
    return


TOTAL = 14
MUTED_LINE = RGBColor(0xA9, 0xBC, 0xD4)


def footer(slide, page, total=TOTAL):
    add_rect(slide, 0, Inches(7.22), W, Inches(0.28), NAVY)
    add_textbox(slide, Inches(0.4), Inches(7.22), Inches(10), Inches(0.28),
                "Khadija Benhamida  ·  Mechatronics Engineering  ·  Confidential",
                size=11, color=MUTED_LINE, anchor=MSO_ANCHOR.MIDDLE)
    add_textbox(slide, Inches(11.4), Inches(7.22), Inches(1.5), Inches(0.28),
                f"{page}  /  {total}", size=11, color=WHITE, align=PP_ALIGN.RIGHT,
                anchor=MSO_ANCHOR.MIDDLE)


def header_bar(slide, kicker, title, subtitle=None):
    add_rect(slide, 0, 0, W, Inches(1.18), NAVY)
    add_rect(slide, 0, Inches(1.18), W, Inches(0.06), BLUE)
    add_textbox(slide, Inches(0.5), Inches(0.10), Inches(12.3), Inches(0.28),
                kicker.upper(), size=11, bold=True, color=RGBColor(0xA9, 0xBC, 0xD4))
    add_textbox(slide, Inches(0.5), Inches(0.34), Inches(12.3), Inches(0.42),
                title, size=26, bold=True, color=WHITE)
    if subtitle:
        add_textbox(slide, Inches(0.5), Inches(0.78), Inches(12.3), Inches(0.32),
                    subtitle, size=13, color=RGBColor(0xC5, 0xD4, 0xE8))


def notes(slide, text):
    slide.notes_slide.notes_text_frame.text = text


def blank(prs):
    slide = prs.slides.add_slide(prs.slide_layouts[6])  # blank
    add_rect(slide, 0, 0, W, H, PAGE)
    return slide


def build(out: Path) -> Path:
    prs = Presentation()
    prs.slide_width = Inches(13.333)
    prs.slide_height = Inches(7.5)

    # ── 1. Title ────────────────────────────────────────────────
    s = blank(prs)
    add_rect(s, 0, 0, W, H, NAVY)
    add_rect(s, 0, 0, Inches(0.18), H, BLUE)
    add_textbox(s, Inches(0.7), Inches(0.85), Inches(11.5), Inches(0.32),
                "STELLANTIS  ·  MECHATRONICS ENGINEERING", size=13, bold=True,
                color=MUTED_LINE)
    add_textbox(s, Inches(0.7), Inches(1.30), Inches(11.5), Inches(0.32),
                "STATUS MEETING  ·  OCTOBER 2026", size=13, bold=True, color=BLUE)
    add_textbox(s, Inches(0.7), Inches(1.62), Inches(12), Inches(0.70),
                "Khadija Benhamida", size=40, bold=True, color=WHITE)
    add_textbox(s, Inches(0.7), Inches(2.40), Inches(12), Inches(0.70),
                "LEON — Quality Analysis", size=28, bold=True,
                color=RGBColor(0xE8, 0xEE, 0xFB))
    add_textbox(s, Inches(0.7), Inches(3.15), Inches(11.8), Inches(0.70),
                "Application supporting the review of specifications, conformity matrices\n"
                "and technical design dossiers (TDR).",
                size=16, color=RGBColor(0xC5, 0xD4, 0xE8))
    add_rect(s, Inches(0.7), Inches(4.05), Inches(3.4), Inches(0.07), BLUE)
    add_textbox(s, Inches(0.7), Inches(4.28), Inches(11.8), Inches(0.70),
                "In collaboration with Imane El Brouji and Patrick Garcia\n"
                "Mechatronics Engineering",
                size=15, color=MUTED_LINE)
    pills = [
        (BLUE, "SPEC", "Customer requirements"),
        (WARN, "MATRIX", "Supplier declaration"),
        (OK, "TDR", "Technical evidence"),
    ]
    px = Inches(0.7)
    for color, label, hint in pills:
        add_rect(s, px, Inches(5.35), Inches(3.4), Inches(1.15), RGBColor(0x15, 0x38, 0x62), None, rounded=True)
        add_rect(s, px, Inches(5.35), Inches(0.10), Inches(1.15), color)
        add_textbox(s, px + Inches(0.28), Inches(5.48), Inches(3.0), Inches(0.42),
                    label, size=18, bold=True, color=WHITE, anchor=MSO_ANCHOR.MIDDLE)
        add_textbox(s, px + Inches(0.28), Inches(5.92), Inches(3.0), Inches(0.40),
                    hint, size=14, color=MUTED_LINE, anchor=MSO_ANCHOR.TOP)
        px += Inches(3.65)
    add_textbox(s, Inches(0.7), Inches(6.70), Inches(11.8), Inches(0.35),
                "Status: development in progress  ·  Team deployment after completion of this phase",
                size=13, color=MUTED_LINE)
    notes(s,
          "Introduce the meeting, the project name, and the collaboration. "
          "Then move to the agenda.")

    # ── 2. Agenda ───────────────────────────────────────────────
    s = blank(prs)
    header_bar(s, "Agenda", "Meeting agenda",
               "Project presentation, current status, and organisational points.")
    agenda = [
        ("01", "Project presentation",
         "Purpose of LEON, the three documents concerned, and the functions of the application."),
        ("02", "Work completed to date",
         "Specification validation, conformity matrix analysis, Matrix ↔ TDR, TDR benchmark."),
        ("03", "Collaboration",
         "Working arrangement with Imane El Brouji and Patrick Garcia."),
        ("04", "Status and deployment",
         "Development in progress. Team access will follow completion of this phase."),
        ("05", "AI training",
         "Upcoming training and expected use within Mechatronics Engineering."),
        ("06", "Working arrangements",
         "Télétravail on Wednesday and Friday; first school period over the next two weeks."),
    ]
    y = Inches(1.48)
    for num, title, body in agenda:
        add_rect(s, Inches(0.5), y, Inches(12.3), Inches(0.88), CARD, LINE, rounded=True)
        add_rect(s, Inches(0.5), y, Inches(0.88), Inches(0.88), BLUE)
        add_textbox(s, Inches(0.5), y, Inches(0.88), Inches(0.88),
                    num, size=16, bold=True, color=WHITE, align=PP_ALIGN.CENTER,
                    anchor=MSO_ANCHOR.MIDDLE)
        add_textbox(s, Inches(1.55), y + Inches(0.08), Inches(10.9), Inches(0.34),
                    title, size=16, bold=True, color=NAVY)
        add_textbox(s, Inches(1.55), y + Inches(0.42), Inches(10.9), Inches(0.38),
                    body, size=13, color=INK)
        y += Inches(0.94)
    footer(s, 2)
    notes(s, "Present the agenda briefly, then start with the project.")

    # ── 3. Project purpose ──────────────────────────────────────
    s = blank(prs)
    header_bar(s, "The project", "Purpose of LEON",
               "A structured first analysis of supplier documentation for Mechatronics Engineering.")
    add_card_text(s, Inches(0.45), Inches(1.52), Inches(6.05), Inches(2.45),
                  "Objective",
                  "Reduce the time and the risk of a purely manual review of specifications, "
                  "conformity matrices and TDR files, while keeping the technical decision "
                  "with the engineer.",
                  BLUE, 16, 14)
    add_card_text(s, Inches(6.75), Inches(1.52), Inches(6.05), Inches(2.45),
                  "Scope",
                  "Customer Technical Specifications (CTS), FNR conformity matrices "
                  "(Excel / ODS) and supplier technical dossiers (PowerPoint, PDF, Word).",
                  NAVY_2, 16, 14)
    add_card_text(s, Inches(0.45), Inches(4.15), Inches(6.05), Inches(2.75),
                  "Expected result",
                  "A first reading of the files: requirement status, inconsistencies, "
                  "and the location of the supporting evidence.\n\n"
                  "Export of a colour-coded Excel report for the review meeting.",
                  OK, 16, 14)
    add_card_text(s, Inches(6.75), Inches(4.15), Inches(6.05), Inches(2.75),
                  "Limit of the tool",
                  "LEON prepares the review. It does not approve a supplier and does not "
                  "replace Quality, Purchasing or the responsible engineer.",
                  WARN, 16, 14)
    footer(s, 3)
    notes(s, "This is the project definition. Then explain the three documents.")

    # ── 4. Three documents ──────────────────────────────────────
    s = blank(prs)
    header_bar(s, "Project context", "Three documents must remain consistent",
               "For each component, Stellantis requirements, the supplier declaration and the technical evidence.")
    cards = [
        ("1.  Specification (CTS)", BLUE,
         "Document issued by Stellantis.\n\n"
         "It defines the requirements applicable to the component: performance, "
         "interfaces, environment, safety, quality.\n\n"
         "It is the contractual reference of what “compliant” means."),
        ("2.  Conformity matrix", WARN,
         "Document completed by the supplier.\n\n"
         "One row per requirement. Status OK, NOK (not compliant) or NA "
         "(not applicable), with a comment.\n\n"
         "It is the official supplier declaration."),
        ("3.  TDR", OK,
         "Technical Design Review / technical dossier.\n\n"
         "PowerPoint or PDF presenting architecture, values, tests and design choices.\n\n"
         "It is the supporting evidence behind the declaration."),
    ]
    x = Inches(0.45)
    for title, accent, body in cards:
        add_rect(s, x, Inches(1.55), Inches(3.95), Inches(5.35), CARD, LINE, rounded=True)
        add_rect(s, x, Inches(1.55), Inches(3.95), Inches(0.12), accent)
        add_textbox(s, x + Inches(0.22), Inches(1.82), Inches(3.5), Inches(0.70),
                    title, size=16, bold=True, color=NAVY)
        add_textbox(s, x + Inches(0.22), Inches(2.60), Inches(3.5), Inches(4.00),
                    body, size=14, color=INK)
        x += Inches(4.15)
    footer(s, 4)
    notes(s,
          "Specification = Stellantis. Matrix = supplier declaration. "
          "TDR = technical evidence. The three must tell the same story.")

    # ── 5. Current process ──────────────────────────────────────
    s = blank(prs)
    header_bar(s, "Why the project exists", "The current review remains largely manual",
               "An OK status in Excel, or a complete TDR presentation, can still hide an inconsistency.")
    problems = [
        ("Review time",
         "A specification, a matrix of several hundred rows and a TDR of several dozen slides "
         "are reviewed mainly by hand."),
        ("Incomplete declarations",
         "A requirement marked OK with a comment such as “pending validation” or “partially covered” "
         "is not a confirmed conformity."),
        ("Inconsistency between files",
         "The matrix may declare OK while the TDR presents a value below the requirement "
         "(for example contrast ≥ 400:1 in the specification, 380:1 in the TDR)."),
        ("Comparison of suppliers",
         "Each supplier delivers a different dossier. A structured technical comparison "
         "on the same criteria is currently time-consuming."),
    ]
    y = Inches(1.50)
    for title, body in problems:
        add_rect(s, Inches(0.5), y, Inches(12.3), Inches(1.22), CARD, LINE, rounded=True)
        add_rect(s, Inches(0.5), y, Inches(0.12), Inches(1.22), NOK)
        add_textbox(s, Inches(0.85), y + Inches(0.16), Inches(11.7), Inches(0.36),
                    title, size=18, bold=True, color=NAVY)
        add_textbox(s, Inches(0.85), y + Inches(0.52), Inches(11.7), Inches(0.58),
                    body, size=14, color=INK)
        y += Inches(1.35)
    footer(s, 5)
    notes(s, "The project does not replace the engineer; it highlights the points that require attention.")

    # ── 6. What LEON does ───────────────────────────────────────
    s = blank(prs)
    header_bar(s, "The application", "What LEON does",
               "A browser-based application. Documents are uploaded; the first analysis is displayed; the engineer decides.")
    add_card_text(s, Inches(0.45), Inches(1.55), Inches(6.05), Inches(5.35),
                  "Functions",
                  "Reads the specification, the conformity matrix and the TDR.\n\n"
                  "Identifies each requirement and its status (OK / NOK / NA).\n\n"
                  "Locates the corresponding evidence in the TDR (page or slide).\n\n"
                  "Highlights inconsistencies: contradictory comments, missing evidence, "
                  "values that do not meet the requirement.\n\n"
                  "Produces an Excel report for the review meeting.\n\n"
                  "The technical validation remains a human decision.",
                  BLUE, 18, 14)
    add_card_text(s, Inches(6.75), Inches(1.55), Inches(6.05), Inches(2.50),
                  "Out of scope",
                  "Automatic approval of a supplier.\n"
                  "Replacement of Quality, Purchasing or the responsible engineer.\n"
                  "A certificate of conformity.",
                  NOK, 18, 14)
    add_card_text(s, Inches(6.75), Inches(4.25), Inches(6.05), Inches(2.65),
                  "Users",
                  "Mechatronics engineers in charge of a component.\n"
                  "Quality / FNR for the analysis of a conformity matrix.\n"
                  "Participants preparing a TDR review.",
                  OK, 18, 14)
    footer(s, 6)
    notes(s, "LEON is a first analysis. The engineer remains accountable.")

    # ── 7. Functions ────────────────────────────────────────────
    s = blank(prs)
    header_bar(s, "The application", "Six functions in a single interface",
               "No installation is required for the reviewer. Only the relevant function is used.")
    tabs = [
        ("Specification validation",
         "Checks completeness and writing quality of the CTS before it is issued."),
        ("Conformity matrix",
         "Analyses supplier OK / NOK / NA statuses and flags inconsistent OK comments."),
        ("Matrix ↔ TDR",
         "Compares the declaration in the matrix with the evidence in the TDR."),
        ("TDR benchmark",
         "Compares several suppliers on the same technical criteria, excluding price."),
        ("Version follow-up",
         "Identifies status and comment changes between successive matrix versions."),
        ("Evidence location",
         "Associates each requirement with the corresponding page or slide in the dossier."),
    ]
    positions = [
        (0.45, 1.52), (4.55, 1.52), (8.65, 1.52),
        (0.45, 4.15), (4.55, 4.15), (8.65, 4.15),
    ]
    accents = [BLUE, WARN, NAVY_2, OK, MUTED, BLUE]
    for (title, body), (x, y), acc in zip(tabs, positions, accents):
        add_rect(s, Inches(x), Inches(y), Inches(3.90), Inches(2.40), CARD, LINE, rounded=True)
        add_rect(s, Inches(x), Inches(y), Inches(3.90), Inches(0.10), acc)
        add_textbox(s, Inches(x + 0.22), Inches(y + 0.28), Inches(3.46), Inches(0.70),
                    title, size=15, bold=True, color=NAVY)
        add_textbox(s, Inches(x + 0.22), Inches(y + 1.10), Inches(3.46), Inches(1.10),
                    body, size=13, color=INK)
    footer(s, 7)
    notes(s, "Present the six functions, then detail specification, matrix and TDR.")

    # ── 8. Spec + Matrix ────────────────────────────────────────
    s = blank(prs)
    header_bar(s, "Project functions", "Specification and conformity matrix",
               "Two analyses available before opening the TDR.")

    add_rect(s, Inches(0.45), Inches(1.52), Inches(6.05), Inches(5.38), CARD, LINE, rounded=True)
    add_rect(s, Inches(0.45), Inches(1.52), Inches(6.05), Inches(0.52), BLUE)
    add_textbox(s, Inches(0.65), Inches(1.58), Inches(5.7), Inches(0.42),
                "Specification validation", size=18, bold=True, color=WHITE, anchor=MSO_ANCHOR.MIDDLE)
    add_textbox(s, Inches(0.70), Inches(2.20), Inches(5.55), Inches(4.4),
                "The CTS file is uploaded (Word, PDF or text).\n\n"
                "LEON verifies:\n"
                "• presence of mandatory sections (Purpose, Scope, Requirements…);\n"
                "• remaining template placeholders (<<name>>, TBD, XXX);\n"
                "• requirement identifiers and binding language (“shall”).\n\n"
                "Output: a verdict (compliant / acceptable / not ready) and a Word report.\n\n"
                "The empty conformity matrix can be generated from the specification, "
                "so that the supplier does not recreate the requirement list.",
                size=13, color=INK)

    add_rect(s, Inches(6.75), Inches(1.52), Inches(6.05), Inches(5.38), CARD, LINE, rounded=True)
    add_rect(s, Inches(6.75), Inches(1.52), Inches(6.05), Inches(0.52), WARN)
    add_textbox(s, Inches(6.95), Inches(1.58), Inches(5.7), Inches(0.42),
                "Conformity matrix analysis", size=18, bold=True, color=WHITE, anchor=MSO_ANCHOR.MIDDLE)
    add_textbox(s, Inches(7.00), Inches(2.20), Inches(5.55), Inches(4.4),
                "The supplier matrix is uploaded (Excel / ODS), including files with renamed columns.\n\n"
                "LEON counts OK / NOK / NA statuses and presents the full requirement list.\n\n"
                "Each OK comment is reviewed. A point of attention is raised when:\n"
                "• the comment contradicts the status;\n"
                "• conformity is partial or still pending.\n\n"
                "Output: a colour-coded Excel report (green / red / grey) for the meeting.",
                size=13, color=INK)
    footer(s, 8)
    notes(s, "These two functions already produce a usable report before TDR cross-check.")

    # ── 9. TDR ──────────────────────────────────────────────────
    s = blank(prs)
    header_bar(s, "Project functions", "Matrix versus TDR, and supplier comparison",
               "The TDR is the evidence. LEON compares the declaration with that evidence.")

    add_rect(s, Inches(0.45), Inches(1.48), Inches(12.4), Inches(1.35), AMBER_BG, WARN, rounded=True)
    add_textbox(s, Inches(0.70), Inches(1.58), Inches(12.0), Inches(0.32),
                "Example of inconsistency  —  display contrast", size=14, bold=True, color=WARN)
    add_textbox(s, Inches(0.70), Inches(1.92), Inches(12.0), Inches(0.72),
                "Requirement: contrast ≥ 400:1     ·     Matrix: OK     ·     TDR, slide 14: 380:1\n"
                "LEON presents this type of inconsistency first. Consistent requirements are not listed by default.",
                size=14, color=INK)

    add_card_text(s, Inches(0.45), Inches(3.02), Inches(6.05), Inches(3.85),
                  "Matrix ↔ TDR",
                  "The matrix and the TDR (PowerPoint, PDF or Word) are uploaded together.\n\n"
                  "For each requirement, four elements are displayed: "
                  "the Stellantis requirement, the matrix declaration, the TDR statement, and the source page or slide.\n\n"
                  "The engineer confirms, corrects or requests additional evidence. "
                  "No result is accepted automatically.",
                  NAVY_2, 16, 13)
    add_card_text(s, Inches(6.75), Inches(3.02), Inches(6.05), Inches(3.85),
                  "TDR benchmark",
                  "Several supplier dossiers are compared on the same technical domains "
                  "(mechanics, display, electronics, safety, software, validation, industrialisation).\n\n"
                  "Commercial content is excluded.\n\n"
                  "The output is a structured comparison of strengths, gaps and open points. "
                  "Supplier selection remains a human decision.",
                  OK, 16, 13)
    footer(s, 9)
    notes(s, "Use the contrast example. Mention version follow-up only if asked.")

    # ── 10. Work completed ──────────────────────────────────────
    s = blank(prs)
    header_bar(s, "Work completed to date", "Current capabilities of LEON",
               "The functions below are implemented. Technical acceptance remains with the engineer.")
    takeaways = [
        (OK, "Browser-based access",
         "Specification, matrix and TDR are uploaded in the application. No installation is required for the reviewer."),
        (BLUE, "Detection of inconsistencies",
         "Contradictory OK comments, missing evidence, non-compliant values, and changes between matrix versions."),
        (WARN, "Export for the review",
         "Colour-coded Excel reports for Quality, Purchasing or the supplier."),
        (NAVY_2, "No automatic approval",
         "Absence of a detected inconsistency is not a certificate of conformity."),
    ]
    y = Inches(1.50)
    for color, title, body in takeaways:
        add_rect(s, Inches(0.5), y, Inches(12.3), Inches(1.05), CARD, LINE, rounded=True)
        add_rect(s, Inches(0.5), y, Inches(0.12), Inches(1.05), color)
        add_textbox(s, Inches(0.85), y + Inches(0.12), Inches(11.7), Inches(0.36),
                    title, size=16, bold=True, color=NAVY)
        add_textbox(s, Inches(0.85), y + Inches(0.48), Inches(11.7), Inches(0.44),
                    body, size=14, color=INK)
        y += Inches(1.15)

    add_textbox(s, Inches(0.5), Inches(6.15), Inches(12.3), Inches(0.85),
                "LEON provides a structured first reading of the specification, the matrix and the TDR, "
                "so that review meetings start from identified inconsistencies.",
                size=15, bold=True, color=NAVY)
    footer(s, 10)
    notes(s, "Close the project part. Next: collaboration, status, training, organisation.")

    # ── 11. Collaboration ───────────────────────────────────────
    s = blank(prs)
    header_bar(s, "Collaboration", "Working arrangement within Mechatronics Engineering",
               "Imane El Brouji and Patrick Garcia provide the domain input. Development of LEON is carried out on that basis.")
    add_rect(s, Inches(0.45), Inches(1.52), Inches(6.05), Inches(3.55), CARD, LINE, rounded=True)
    add_rect(s, Inches(0.45), Inches(1.52), Inches(6.05), Inches(0.12), BLUE)
    add_textbox(s, Inches(0.70), Inches(1.80), Inches(5.55), Inches(0.40),
                "Imane El Brouji", size=22, bold=True, color=NAVY)
    add_textbox(s, Inches(0.70), Inches(2.28), Inches(5.55), Inches(0.32),
                "Mechatronics Engineering", size=14, bold=True, color=BLUE)
    add_textbox(s, Inches(0.70), Inches(2.75), Inches(5.55), Inches(2.00),
                "Defines the operational needs of the team, the documents actually received, "
                "and the analyses that are useful during a review.",
                size=15, color=INK)

    add_rect(s, Inches(6.75), Inches(1.52), Inches(6.05), Inches(3.55), CARD, LINE, rounded=True)
    add_rect(s, Inches(6.75), Inches(1.52), Inches(6.05), Inches(0.12), OK)
    add_textbox(s, Inches(7.00), Inches(1.80), Inches(5.55), Inches(0.40),
                "Patrick Garcia", size=22, bold=True, color=NAVY)
    add_textbox(s, Inches(7.00), Inches(2.28), Inches(5.55), Inches(0.32),
                "Mechatronics Engineering", size=14, bold=True, color=OK)
    add_textbox(s, Inches(7.00), Inches(2.75), Inches(5.55), Inches(2.00),
                "Provides the mechatronics context: use of the specification, the matrix and the TDR, "
                "and the expected content of a technical review.",
                size=15, color=INK)

    add_rect(s, Inches(0.45), Inches(5.25), Inches(12.35), Inches(1.70), CARD, LINE, rounded=True)
    add_rect(s, Inches(0.45), Inches(5.25), Inches(0.12), Inches(1.70), NAVY_2)
    add_textbox(s, Inches(0.80), Inches(5.40), Inches(11.7), Inches(0.36),
                "Division of roles", size=16, bold=True, color=NAVY)
    add_textbox(s, Inches(0.80), Inches(5.82), Inches(11.7), Inches(0.90),
                "They provide the ideas and the technical meaning. I implement and iterate on LEON. "
                "The application is designed from mechatronics practice, not as a standalone IT development.",
                size=15, color=INK)
    footer(s, 11)
    notes(s, "Acknowledge both colleagues by name.")

    # ── 12. Status and deployment ───────────────────────────────
    s = blank(prs)
    header_bar(s, "Status and next step", "Development in progress — then deployment",
               "Access for the whole team will be opened after completion of this phase.")
    steps = [
        (BLUE, "01  Current phase", "In progress",
         "Completion of the functions presented: specification, matrix, Matrix ↔ TDR and TDR benchmark."),
        (WARN, "02  Next", "Close this phase",
         "Remaining development, tests on representative mechatronics files, alignment with Imane El Brouji and Patrick Garcia."),
        (OK, "03  Then", "Team deployment",
         "Once this phase is completed, deployment will start so that Mechatronics Engineering can use LEON in a browser, without installation."),
    ]
    x = Inches(0.45)
    for color, kicker, title, body in steps:
        add_rect(s, x, Inches(1.55), Inches(4.05), Inches(4.05), CARD, LINE, rounded=True)
        add_rect(s, x, Inches(1.55), Inches(4.05), Inches(0.12), color)
        add_textbox(s, x + Inches(0.25), Inches(1.85), Inches(3.55), Inches(0.40),
                    kicker, size=13, bold=True, color=color)
        add_textbox(s, x + Inches(0.25), Inches(2.28), Inches(3.55), Inches(0.90),
                    title, size=20, bold=True, color=NAVY)
        add_textbox(s, x + Inches(0.25), Inches(3.25), Inches(3.55), Inches(2.05),
                    body, size=14, color=INK)
        x += Inches(4.20)

    add_rect(s, Inches(0.45), Inches(5.80), Inches(12.35), Inches(1.18), GREEN_BG, OK, rounded=True)
    add_textbox(s, Inches(0.70), Inches(5.95), Inches(11.9), Inches(0.90),
                "Target: any colleague who reviews a specification, a matrix or a TDR can use LEON.",
                size=16, bold=True, color=NAVY)
    footer(s, 12)
    notes(s, "Do not commit to a deployment date. Sequence: finish this phase, then deploy.")

    # ── 13. AI training ─────────────────────────────────────────
    s = blank(prs)
    header_bar(s, "Upcoming training", "AI training for Mechatronics Engineering",
               "Operational training on the use of AI in daily document review.")
    add_card_text(s, Inches(0.45), Inches(1.52), Inches(6.05), Inches(5.38),
                  "Objectives of the training",
                  "Clarify what AI can and cannot do on specifications, matrices and TDR files.\n\n"
                  "Position a tool such as LEON as a first analysis, not as a substitute for the engineer.\n\n"
                  "Establish a common practice: where AI reduces review time, "
                  "and where a technical decision is required.\n\n"
                  "Use examples drawn from mechatronics documents.",
                  BLUE, 18, 14)
    add_card_text(s, Inches(6.75), Inches(1.52), Inches(6.05), Inches(5.38),
                  "Expected use in the team",
                  "Faster preparation of a TDR review.\n\n"
                  "Fewer OK declarations accepted despite a contradictory comment.\n\n"
                  "Structured comparison of several suppliers on technical criteria.\n\n"
                  "Rule of use: the application proposes; the engineer validates.\n\n"
                  "A short feedback to the team can be prepared after the training.",
                  OK, 18, 14)
    footer(s, 13)
    notes(s, "Do not invent a training provider or date if not confirmed.")

    # ── 14. Organisation ────────────────────────────────────────
    s = blank(prs)
    header_bar(s, "Working arrangements", "Points submitted for confirmation",
               "Télétravail and first school period.")
    add_rect(s, Inches(0.45), Inches(1.52), Inches(6.05), Inches(3.70), CARD, LINE, rounded=True)
    add_rect(s, Inches(0.45), Inches(1.52), Inches(6.05), Inches(0.52), BLUE)
    add_textbox(s, Inches(0.65), Inches(1.58), Inches(5.7), Inches(0.42),
                "Télétravail (TT)", size=18, bold=True, color=WHITE, anchor=MSO_ANCHOR.MIDDLE)
    add_textbox(s, Inches(0.70), Inches(2.25), Inches(5.55), Inches(2.70),
                "Proposed weekly pattern: two days of télétravail.\n\n"
                "• Wednesday\n"
                "• Friday\n\n"
                "This pattern is submitted for confirmation, so that on-site presence is clear for the team.",
                size=16, color=INK)

    add_rect(s, Inches(6.75), Inches(1.52), Inches(6.05), Inches(3.70), CARD, LINE, rounded=True)
    add_rect(s, Inches(6.75), Inches(1.52), Inches(6.05), Inches(0.52), WARN)
    add_textbox(s, Inches(6.95), Inches(1.58), Inches(5.7), Inches(0.42),
                "Période école", size=18, bold=True, color=WHITE, anchor=MSO_ANCHOR.MIDDLE)
    add_textbox(s, Inches(7.00), Inches(2.25), Inches(5.55), Inches(2.70),
                "The next two weeks correspond to the first school period (période école).\n\n"
                "Presence on site is not planned during that period.\n\n"
                "The usual company / TT pattern resumes afterwards.",
                size=16, color=INK)

    add_rect(s, Inches(0.45), Inches(5.42), Inches(12.35), Inches(1.52), AMBER_BG, WARN, rounded=True)
    add_textbox(s, Inches(0.70), Inches(5.58), Inches(11.9), Inches(0.36),
                "Submitted for your confirmation", size=16, bold=True, color=WARN)
    add_textbox(s, Inches(0.70), Inches(6.00), Inches(11.9), Inches(0.72),
                "Both points can be adjusted if a different organisation is required.",
                size=15, color=INK)
    footer(s, 14)
    notes(s,
          "Ask for confirmation of TT Wednesday and Friday, and of the school period. "
          "Thank the manager.")

    out.parent.mkdir(parents=True, exist_ok=True)
    prs.save(str(out))
    return out


if __name__ == "__main__":
    target = Path(__file__).resolve().parents[1] / "docs" / "LEON_intro_for_non_specialists.pptx"
    path = build(target)
    print(f"Wrote {path} ({path.stat().st_size} bytes)")

