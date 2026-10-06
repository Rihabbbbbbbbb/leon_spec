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


TOTAL = 11
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
    add_textbox(s, Inches(0.7), Inches(0.80), Inches(11.5), Inches(0.28),
                "STELLANTIS  ·  MECHATRONICS ENGINEERING", size=13, bold=True,
                color=MUTED_LINE)
    add_textbox(s, Inches(0.7), Inches(1.18), Inches(11.5), Inches(0.30),
                "INTRODUCTORY MEETING  ·  OCTOBER 2026", size=13, bold=True, color=BLUE)
    add_textbox(s, Inches(0.7), Inches(1.55), Inches(12), Inches(0.70),
                "AERIS", size=48, bold=True, color=WHITE)
    add_textbox(s, Inches(0.7), Inches(2.28), Inches(12), Inches(0.42),
                "Automated Engineering Review & Integrity System", size=18, bold=True,
                color=RGBColor(0xE8, 0xEE, 0xFB))
    add_textbox(s, Inches(0.7), Inches(2.78), Inches(11.8), Inches(0.70),
                "Project presentation: review of specifications, conformity matrices\n"
                "and technical design dossiers (TDR) — without requiring an AI background.",
                size=16, color=RGBColor(0xC5, 0xD4, 0xE8))
    add_rect(s, Inches(0.7), Inches(3.58), Inches(3.4), Inches(0.07), BLUE)
    add_textbox(s, Inches(0.7), Inches(3.78), Inches(11.8), Inches(0.70),
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
        add_rect(s, px, Inches(4.70), Inches(3.4), Inches(1.15), RGBColor(0x15, 0x38, 0x62), None, rounded=True)
        add_rect(s, px, Inches(4.70), Inches(0.10), Inches(1.15), color)
        add_textbox(s, px + Inches(0.28), Inches(4.82), Inches(3.0), Inches(0.42),
                    label, size=18, bold=True, color=WHITE, anchor=MSO_ANCHOR.MIDDLE)
        add_textbox(s, px + Inches(0.28), Inches(5.26), Inches(3.0), Inches(0.40),
                    hint, size=14, color=MUTED_LINE, anchor=MSO_ANCHOR.TOP)
        px += Inches(3.65)
    add_textbox(s, Inches(0.7), Inches(6.10), Inches(11.8), Inches(0.70),
                "Status: development in progress.\n"
                "After completion of this phase, deployment will start so that the whole team has access.",
                size=14, color=MUTED_LINE)
    notes(s, "Introductory meeting. Present the project, then collaboration, status and organisation.")

    # ── 2. Agenda ───────────────────────────────────────────────
    s = blank(prs)
    header_bar(s, "Agenda", "Meeting agenda",
               "Project explanation, work completed, then organisational points.")
    agenda = [
        ("01", "The project",
         "Purpose of AERIS, the three documents (specification, matrix, TDR) and the application."),
        ("02", "Work completed",
         "Specification validation, conformity matrix, Matrix ↔ TDR, TDR benchmark."),
        ("03", "Collaboration",
         "Imane El Brouji and Patrick Garcia — mechatronics input and explanations."),
        ("04", "Status and deployment",
         "Work in progress. Team access after completion of this phase."),
        ("05", "AI training",
         "Upcoming training and use within the Mechatronics team."),
        ("06", "Working arrangements",
         "Télétravail Wednesday and Friday; first school period over the next two weeks."),
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

    # ── 3. Project + three documents ────────────────────────────
    s = blank(prs)
    header_bar(s, "The project", "What we are working on",
               "AERIS supports Mechatronics Engineering in the review of three documents that must remain consistent.")
    add_rect(s, Inches(0.45), Inches(1.45), Inches(12.4), Inches(1.35), BLUE_SOFT, BLUE, rounded=True)
    add_textbox(s, Inches(0.70), Inches(1.55), Inches(11.9), Inches(1.15),
                "Objective: provide a structured first analysis of supplier files, reduce review time and highlight "
                "inconsistencies — while the engineer remains responsible for the technical decision.\n"
                "AERIS does not approve a supplier and does not replace Quality or Purchasing.",
                size=14, color=NAVY)
    cards = [
        ("1. Specification (CTS)", BLUE,
         "Issued by Stellantis.\n\n"
         "Defines the requirements of the component: performance, interfaces, environment, safety, quality.\n\n"
         "Contractual reference of what “compliant” means."),
        ("2. Conformity matrix", WARN,
         "Completed by the supplier.\n\n"
         "One row per requirement. Status OK, NOK or NA, plus a comment.\n\n"
         "Official supplier declaration."),
        ("3. TDR", OK,
         "Technical Design Review / dossier.\n\n"
         "PowerPoint or PDF: architecture, measured values, tests, design choices.\n\n"
         "Supporting evidence behind the declaration."),
    ]
    x = Inches(0.45)
    for title, accent, body in cards:
        add_rect(s, x, Inches(2.98), Inches(3.95), Inches(3.95), CARD, LINE, rounded=True)
        add_rect(s, x, Inches(2.98), Inches(3.95), Inches(0.10), accent)
        add_textbox(s, x + Inches(0.22), Inches(3.18), Inches(3.50), Inches(0.55),
                    title, size=15, bold=True, color=NAVY)
        add_textbox(s, x + Inches(0.22), Inches(3.75), Inches(3.50), Inches(2.95),
                    body, size=13, color=INK)
        x += Inches(4.15)
    footer(s, 3)
    notes(s, "This is the core of the project. The three documents must tell the same story.")

    # ── 4. Why + what LEON does ─────────────────────────────────
    s = blank(prs)
    header_bar(s, "The project", "Why AERIS exists, and what it does",
               "The review is still largely manual. AERIS performs a first reading of the files in a browser.")
    left = [
        ("Review time", "A specification, a matrix of several hundred rows and a TDR of dozens of slides are reviewed mainly by hand."),
        ("Incomplete OK", "A status marked OK with the comment “pending validation” is not confirmed conformity."),
        ("File inconsistency", "The matrix may declare OK while the TDR shows a lower value (example: ≥ 400:1 required, 380:1 presented)."),
        ("Supplier comparison", "Comparing several technical offers on the same criteria is currently time-consuming."),
    ]
    y = Inches(1.48)
    for title, body in left:
        add_rect(s, Inches(0.45), y, Inches(6.10), Inches(1.28), CARD, LINE, rounded=True)
        add_rect(s, Inches(0.45), y, Inches(0.10), Inches(1.28), NOK)
        add_textbox(s, Inches(0.75), y + Inches(0.10), Inches(5.60), Inches(0.32),
                    title, size=14, bold=True, color=NAVY)
        add_textbox(s, Inches(0.75), y + Inches(0.44), Inches(5.60), Inches(0.74),
                    body, size=12, color=INK)
        y += Inches(1.38)
    add_card_text(s, Inches(6.80), Inches(1.48), Inches(6.05), Inches(5.42),
                  "What AERIS does",
                  "The specification, the matrix and the TDR are uploaded in the browser.\n\n"
                  "The application identifies each requirement, its status, and the matching page or slide in the TDR.\n\n"
                  "It highlights inconsistencies and produces a colour-coded Excel report for the review meeting.\n\n"
                  "No installation is required for the reviewer.\n\n"
                  "The technical decision remains human. AERIS does not issue a certificate of conformity.",
                  BLUE, 17, 13)
    footer(s, 4)

    # ── 5. Interface ────────────────────────────────────────────
    s = blank(prs)
    header_bar(s, "The application", "The interface — six functions",
               "A single browser page. Only the relevant function is used.")
    tabs = [
        ("Specification validation",
         "Checks completeness and writing quality of the CTS before it is issued."),
        ("Conformity matrix",
         "Analyses OK / NOK / NA statuses and flags contradictory OK comments."),
        ("Matrix ↔ TDR",
         "Compares the supplier declaration with the evidence in the TDR."),
        ("TDR benchmark",
         "Compares several suppliers on the same technical criteria, excluding price."),
        ("Version follow-up",
         "Identifies changes between successive versions of the same matrix."),
        ("Evidence location",
         "Associates each requirement with the corresponding page or slide."),
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
    footer(s, 5)

    # ── 6. Work done: spec + matrix ─────────────────────────────
    s = blank(prs)
    header_bar(s, "Work completed", "Specification and conformity matrix",
               "Two functions already available before opening the TDR.")
    add_rect(s, Inches(0.45), Inches(1.52), Inches(6.05), Inches(5.38), CARD, LINE, rounded=True)
    add_rect(s, Inches(0.45), Inches(1.52), Inches(6.05), Inches(0.52), BLUE)
    add_textbox(s, Inches(0.65), Inches(1.58), Inches(5.7), Inches(0.42),
                "Specification validation", size=18, bold=True, color=WHITE, anchor=MSO_ANCHOR.MIDDLE)
    add_textbox(s, Inches(0.70), Inches(2.20), Inches(5.55), Inches(4.4),
                "The CTS file is uploaded (Word, PDF or text).\n\n"
                "AERIS verifies:\n"
                "• mandatory sections (Purpose, Scope, Requirements…);\n"
                "• remaining placeholders (<<name>>, TBD, XXX);\n"
                "• requirement identifiers and binding language (“shall”).\n\n"
                "Output: a verdict (compliant / acceptable / not ready) and a Word report.\n\n"
                "The empty conformity matrix can be generated from the specification.",
                size=13, color=INK)
    add_rect(s, Inches(6.75), Inches(1.52), Inches(6.05), Inches(5.38), CARD, LINE, rounded=True)
    add_rect(s, Inches(6.75), Inches(1.52), Inches(6.05), Inches(0.52), WARN)
    add_textbox(s, Inches(6.95), Inches(1.58), Inches(5.7), Inches(0.42),
                "Conformity matrix analysis", size=18, bold=True, color=WHITE, anchor=MSO_ANCHOR.MIDDLE)
    add_textbox(s, Inches(7.00), Inches(2.20), Inches(5.55), Inches(4.4),
                "The supplier matrix is uploaded (Excel / ODS).\n\n"
                "AERIS counts OK / NOK / NA statuses and lists every requirement.\n\n"
                "Each OK comment is reviewed. A point of attention is raised when:\n"
                "• the comment contradicts the status;\n"
                "• conformity is partial or still pending.\n\n"
                "Output: a colour-coded Excel report for the review meeting.",
                size=13, color=INK)
    footer(s, 6)

    # ── 7. Work done: TDR ───────────────────────────────────────
    s = blank(prs)
    header_bar(s, "Work completed", "Matrix versus TDR, and supplier comparison",
               "The TDR is the evidence. AERIS compares the declaration with that evidence.")
    add_rect(s, Inches(0.45), Inches(1.48), Inches(12.4), Inches(1.28), AMBER_BG, WARN, rounded=True)
    add_textbox(s, Inches(0.70), Inches(1.56), Inches(12.0), Inches(0.28),
                "Example of inconsistency — display contrast", size=14, bold=True, color=WARN)
    add_textbox(s, Inches(0.70), Inches(1.88), Inches(12.0), Inches(0.70),
                "Requirement: contrast ≥ 400:1     ·     Matrix: OK     ·     TDR, slide 14: 380:1\n"
                "Inconsistencies are presented first. Consistent requirements are not listed by default.",
                size=14, color=INK)
    add_card_text(s, Inches(0.45), Inches(2.95), Inches(6.05), Inches(3.92),
                  "Matrix ↔ TDR",
                  "The matrix and the TDR (PowerPoint, PDF or Word) are uploaded together.\n\n"
                  "For each requirement: the Stellantis requirement, the matrix declaration, the TDR statement, and the source page or slide.\n\n"
                  "The engineer confirms, corrects or requests additional evidence. No result is accepted automatically.",
                  NAVY_2, 16, 13)
    add_card_text(s, Inches(6.75), Inches(2.95), Inches(6.05), Inches(3.92),
                  "TDR benchmark",
                  "Several supplier dossiers are compared on the same technical domains (mechanics, display, electronics, safety, software, validation, industrialisation).\n\n"
                  "Commercial content is excluded.\n\n"
                  "Supplier selection remains a human decision.",
                  OK, 16, 13)
    footer(s, 7)

    # ── 8. Collaboration ────────────────────────────────────────
    s = blank(prs)
    header_bar(s, "Collaboration", "Working arrangement",
               "Imane El Brouji and Patrick Garcia provide the mechatronics expertise. I implement AERIS.")
    add_rect(s, Inches(0.45), Inches(1.52), Inches(6.05), Inches(3.40), CARD, LINE, rounded=True)
    add_rect(s, Inches(0.45), Inches(1.52), Inches(6.05), Inches(0.12), BLUE)
    add_textbox(s, Inches(0.70), Inches(1.80), Inches(5.55), Inches(0.40),
                "Imane El Brouji", size=22, bold=True, color=NAVY)
    add_textbox(s, Inches(0.70), Inches(2.28), Inches(5.55), Inches(0.32),
                "Mechatronics Engineering", size=14, bold=True, color=BLUE)
    add_textbox(s, Inches(0.70), Inches(2.75), Inches(5.55), Inches(1.85),
                "Defines the operational needs of the team, the documents received, and the analyses that are useful during a review.",
                size=15, color=INK)
    add_rect(s, Inches(6.75), Inches(1.52), Inches(6.05), Inches(3.40), CARD, LINE, rounded=True)
    add_rect(s, Inches(6.75), Inches(1.52), Inches(6.05), Inches(0.12), OK)
    add_textbox(s, Inches(7.00), Inches(1.80), Inches(5.55), Inches(0.40),
                "Patrick Garcia", size=22, bold=True, color=NAVY)
    add_textbox(s, Inches(7.00), Inches(2.28), Inches(5.55), Inches(0.32),
                "Mechatronics Engineering", size=14, bold=True, color=OK)
    add_textbox(s, Inches(7.00), Inches(2.75), Inches(5.55), Inches(1.85),
                "Explains the mechatronics context: how the specification, the matrix and the TDR are used, and what a technical review must cover.",
                size=15, color=INK)
    add_rect(s, Inches(0.45), Inches(5.12), Inches(12.35), Inches(1.82), CARD, LINE, rounded=True)
    add_rect(s, Inches(0.45), Inches(5.12), Inches(0.12), Inches(1.82), NAVY_2)
    add_textbox(s, Inches(0.80), Inches(5.28), Inches(11.7), Inches(0.36),
                "Division of roles", size=16, bold=True, color=NAVY)
    add_textbox(s, Inches(0.80), Inches(5.70), Inches(11.7), Inches(1.00),
                "They provide the ideas and the technical meaning on the mechatronics side. I develop and iterate on AERIS. "
                "The application is designed from engineering practice, not as a standalone IT development.",
                size=15, color=INK)
    footer(s, 8)

    # ── 9. Status / deployment ──────────────────────────────────
    s = blank(prs)
    header_bar(s, "Status", "Development in progress — then team deployment",
               "The whole team will have access once this phase is completed.")
    steps = [
        (BLUE, "01  Now", "In progress",
         "Completion of the functions presented: specification, matrix, Matrix ↔ TDR and TDR benchmark."),
        (WARN, "02  Next", "Close this phase",
         "Remaining development, tests on representative files, alignment with Imane El Brouji and Patrick Garcia."),
        (OK, "03  Then", "Deployment",
         "Deployment will start so that Mechatronics Engineering can open AERIS in a browser, without installation."),
    ]
    x = Inches(0.45)
    for color, kicker, title, body in steps:
        add_rect(s, x, Inches(1.55), Inches(4.05), Inches(4.00), CARD, LINE, rounded=True)
        add_rect(s, x, Inches(1.55), Inches(4.05), Inches(0.12), color)
        add_textbox(s, x + Inches(0.25), Inches(1.85), Inches(3.55), Inches(0.36),
                    kicker, size=13, bold=True, color=color)
        add_textbox(s, x + Inches(0.25), Inches(2.28), Inches(3.55), Inches(0.80),
                    title, size=22, bold=True, color=NAVY)
        add_textbox(s, x + Inches(0.25), Inches(3.20), Inches(3.55), Inches(2.05),
                    body, size=14, color=INK)
        x += Inches(4.20)
    add_rect(s, Inches(0.45), Inches(5.75), Inches(12.35), Inches(1.20), GREEN_BG, OK, rounded=True)
    add_textbox(s, Inches(0.70), Inches(5.92), Inches(11.9), Inches(0.90),
                "Target: any colleague who reviews a specification, a matrix or a TDR can use AERIS.",
                size=16, bold=True, color=NAVY)
    footer(s, 9)

    # ── 10. AI training ─────────────────────────────────────────
    s = blank(prs)
    header_bar(s, "Upcoming training", "AI training for the Mechatronics team",
               "Operational training on the use of AI in daily document review.")
    add_card_text(s, Inches(0.45), Inches(1.52), Inches(6.05), Inches(5.38),
                  "Objectives",
                  "Clarify what AI can and cannot do on specifications, matrices and TDR files.\n\n"
                  "Position AERIS as a first analysis, not as a substitute for the engineer.\n\n"
                  "Establish a common practice: where AI reduces review time, and where a technical decision is required.\n\n"
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
    footer(s, 10)

    # ── 11. Organisation ────────────────────────────────────────
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
                "Submitted for confirmation, so that on-site presence is clear for the team.",
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
    footer(s, 11)
    notes(s, "Ask for confirmation of TT Wednesday and Friday, and of the school period.")

    out.parent.mkdir(parents=True, exist_ok=True)
    prs.save(str(out))
    return out


if __name__ == "__main__":
    target = Path(__file__).resolve().parents[1] / "docs" / "LEON_intro_for_non_specialists.pptx"
    path = build(target)
    print(f"Wrote {path} ({path.stat().st_size} bytes)")
