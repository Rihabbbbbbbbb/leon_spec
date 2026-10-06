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


TOTAL = 13
MUTED_LINE = RGBColor(0xA9, 0xBC, 0xD4)


def footer(slide, page, total=TOTAL):
    add_rect(slide, 0, Inches(7.22), W, Inches(0.28), NAVY)
    add_textbox(slide, Inches(0.4), Inches(7.22), Inches(10), Inches(0.28),
                "Khadija Benhamida  ·  Mechatronics Engineering  ·  First meeting",
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

    # ── 1. Title — first meeting with manager ───────────────────
    s = blank(prs)
    add_rect(s, 0, 0, W, H, NAVY)
    add_rect(s, 0, 0, Inches(0.18), H, BLUE)
    add_textbox(s, Inches(0.7), Inches(0.85), Inches(11.5), Inches(0.32),
                "STELLANTIS  ·  MECHATRONICS ENGINEERING", size=13, bold=True,
                color=MUTED_LINE)
    add_textbox(s, Inches(0.7), Inches(1.30), Inches(11.5), Inches(0.32),
                "FIRST MEETING", size=13, bold=True, color=BLUE)
    add_textbox(s, Inches(0.7), Inches(1.62), Inches(12), Inches(0.70),
                "Khadija Benhamida", size=40, bold=True, color=WHITE)
    add_textbox(s, Inches(0.7), Inches(2.40), Inches(12), Inches(0.70),
                "LEON — Quality Analysis", size=28, bold=True,
                color=RGBColor(0xE8, 0xEE, 0xFB))
    add_textbox(s, Inches(0.7), Inches(3.15), Inches(11.8), Inches(0.70),
                "Review of specifications, conformity matrices and technical dossiers (TDR)\n"
                "for the Mechatronics team.",
                size=16, color=RGBColor(0xC5, 0xD4, 0xE8))
    add_rect(s, Inches(0.7), Inches(4.05), Inches(3.4), Inches(0.07), BLUE)
    add_textbox(s, Inches(0.7), Inches(4.28), Inches(11.8), Inches(0.70),
                "In collaboration with Imane El Brouji and Patrick Garcia\n"
                "October 2026",
                size=15, color=MUTED_LINE)
    pills = [
        (BLUE, "SPEC", "What we ask"),
        (WARN, "MATRIX", "What they tick"),
        (OK, "TDR", "What they prove"),
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
                "Project in progress  ·  Deployment for the whole team once this step is finished",
                size=13, color=MUTED_LINE)
    notes(s,
          "Bonjour. This is my first meeting with you.\n"
          "I will present the work I am doing on LEON, the people I work with on the "
          "mechatronics side, the next step (deployment), the AI training, and two "
          "points of availability I would like to confirm with you.")

    # ── 2. Agenda ───────────────────────────────────────────────
    s = blank(prs)
    header_bar(s, "Agenda", "Points I would like to cover with you.",
               "A short status of the work, then the practical points to confirm.")
    agenda = [
        ("01", "The work on LEON",
         "The three documents we review, the tool I am building, and what it already does."),
        ("02", "What I have delivered so far",
         "Spec validation, conformity matrix, Matrix ↔ TDR, TDR benchmark, version follow-up."),
        ("03", "Collaboration",
         "Imane El Brouji and Patrick Garcia — mechatronics expertise, ideas and explanations."),
        ("04", "Status and next step",
         "Work in progress. Once this task is finished, we start the deployment for the team."),
        ("05", "AI training for Mechatronics",
         "The training we will have, and how the team can use it in daily work."),
        ("06", "Availability to confirm",
         "Télétravail Wednesday and Friday, and the first school period over the next two weeks."),
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
    notes(s, "Walk the agenda in 20 seconds. Then go to the three documents.")

    # ── 3. Three documents ──────────────────────────────────────
    s = blank(prs)
    header_bar(s, "The vocabulary", "Three documents. One part. They must agree.",
               "Think of buying a car screen, a camera or a controller from a supplier.")
    cards = [
        ("1.  The SPEC", BLUE,
         "What Stellantis writes.\n\n"
         "Our technical specification: every requirement the part must meet "
         "(brightness, temperature, safety, interfaces…).\n\n"
         "This is our “shopping list” — and the contract of what “good” means."),
        ("2.  The MATRIX", WARN,
         "What the supplier ticks.\n\n"
         "A big Excel / ODS spreadsheet. One row per requirement. "
         "The supplier marks OK, NOK (not OK) or NA (not applicable), plus a comment.\n\n"
         "This is their official yes / no."),
        ("3.  The TDR", OK,
         "What the supplier shows as proof.\n\n"
         "TDR = Technical Design Review / technical dossier. "
         "Usually a PowerPoint or PDF: drawings, numbers, tests, architecture.\n\n"
         "This is their “here is how we actually do it”."),
    ]
    x = Inches(0.45)
    for title, accent, body in cards:
        add_rect(s, x, Inches(1.55), Inches(3.95), Inches(5.35), CARD, LINE, rounded=True)
        add_rect(s, x, Inches(1.55), Inches(3.95), Inches(0.12), accent)
        add_textbox(s, x + Inches(0.22), Inches(1.82), Inches(3.5), Inches(0.5),
                    title, size=20, bold=True, color=NAVY)
        add_textbox(s, x + Inches(0.22), Inches(2.40), Inches(3.5), Inches(4.2),
                    body, size=14, color=INK)
        x += Inches(4.15)
    footer(s, 3)
    notes(s,
          "Spend time here. Most confusion later comes from mixing these three names.\n"
          "SPEC = us. MATRIX = their checklist. TDR = their technical brochure.")

    # ── 3. The problem ──────────────────────────────────────────
    s = blank(prs)
    header_bar(s, "Why we built this", "Today this is still a lot of reading by hand.",
               "A “green” Excel can hide a problem. A nice slide can hide a wrong number.")
    problems = [
        ("Slow", "Hundreds of rows in Excel, dozens of slides, a long spec. "
         "A careful review takes a long time."),
        ("Easy to miss", "A row marked OK with the comment “pending validation” "
         "is not really OK. Humans skip comments when they are tired."),
        ("Easy to contradict", "The matrix says contrast ≥ 400:1 is OK. "
         "The TDR slide shows 380:1. Those two files were never put side by side."),
        ("Hard to compare suppliers", "Each supplier sends their own PowerPoint. "
         "Comparing three offers on the same technical points is a weekend of work."),
    ]
    y = Inches(1.50)
    for title, body in problems:
        add_rect(s, Inches(0.5), y, Inches(12.3), Inches(1.22), CARD, LINE, rounded=True)
        add_rect(s, Inches(0.5), y, Inches(0.12), Inches(1.22), NOK)
        add_textbox(s, Inches(0.85), y + Inches(0.16), Inches(11.7), Inches(0.36),
                    title, size=18, bold=True, color=NAVY)
        add_textbox(s, Inches(0.85), y + Inches(0.52), Inches(11.7), Inches(0.58),
                    body, size=15, color=INK)
        y += Inches(1.35)
    footer(s, 4)
    notes(s, "The punchline: we are not replacing the engineer. We are catching the misses.")

    # ── 4. What LEON is ─────────────────────────────────────────
    s = blank(prs)
    header_bar(s, "In one sentence", "LEON is a web page that does a first reading of the files.",
               "You drop the documents in a browser. You get highlights, then you decide.")
    add_card_text(s, Inches(0.45), Inches(1.55), Inches(6.05), Inches(5.35),
                  "What it does",
                  "Opens the spec, the matrix and the TDR for you.\n\n"
                  "Finds every requirement, every OK / NOK / NA, and the matching page or slide.\n\n"
                  "Puts in red the things that do not match — wrong number, empty proof, "
                  "OK tick with a worrying comment.\n\n"
                  "Gives you an Excel you can share in the review meeting.\n\n"
                  "You still sign. LEON only prepares the file.",
                  BLUE, 18, 15)
    add_card_text(s, Inches(6.75), Inches(1.55), Inches(6.05), Inches(2.50),
                  "What it is not",
                  "Not a robot that approves a supplier.\n"
                  "Not a black box that “says yes”.\n"
                  "Not a replacement for the engineer, quality or purchasing.",
                  NOK, 18, 15)
    add_card_text(s, Inches(6.75), Inches(4.25), Inches(6.05), Inches(2.65),
                  "Who it is for",
                  "Mechatronics engineers reviewing a part.\n"
                  "Quality / FNR looking at a conformity matrix.\n"
                  "Anyone who must walk into a TDR meeting prepared.",
                  OK, 18, 15)
    footer(s, 5)
    notes(s,
          "If someone asks “is it AI?”, answer: it is a reading assistant. "
          "Some checks are simple rules (count the sections, compare two numbers). "
          "A person always remains responsible.")

    # ── 5. The interface ────────────────────────────────────────
    s = blank(prs)
    header_bar(s, "The screen you open", "Six tabs — six jobs. You only use the ones you need.",
               "Same tool, same browser. No installation for the person reviewing.")
    tabs = [
        ("📄  Spec Validation", "Is our spec complete and clear before we send it?"),
        ("📊  Conformity Matrix", "Read the supplier Excel: OK / NOK / NA and catch fake OK."),
        ("🔎  Matrix ↔ TDR", "Do the ticks match the proof in the PowerPoint / PDF?"),
        ("🏁  TDR Benchmark", "Compare several suppliers’ technical offers, side by side."),
        ("📉  Version Delta", "What changed between two versions of the same matrix?"),
        ("🔗  Matrix + evidence", "Find the page that talks about a given requirement."),
    ]
    positions = [
        (0.45, 1.52), (4.55, 1.52), (8.65, 1.52),
        (0.45, 4.15), (4.55, 4.15), (8.65, 4.15),
    ]
    accents = [BLUE, WARN, NAVY_2, OK, MUTED, BLUE]
    for (title, body), (x, y), acc in zip(tabs, positions, accents):
        add_rect(s, Inches(x), Inches(y), Inches(3.90), Inches(2.40), CARD, LINE, rounded=True)
        add_rect(s, Inches(x), Inches(y), Inches(3.90), Inches(0.10), acc)
        add_textbox(s, Inches(x + 0.22), Inches(y + 0.28), Inches(3.46), Inches(0.85),
                    title, size=16, bold=True, color=NAVY)
        add_textbox(s, Inches(x + 0.22), Inches(y + 1.15), Inches(3.46), Inches(1.05),
                    body, size=14, color=INK)
    footer(s, 6)
    notes(s,
          "Walk the tabs left to right as the project timeline:\n"
          "write spec → send matrix → receive filled matrix + TDR → compare suppliers → track versions.")

    # ── 6. Spec + Matrix ────────────────────────────────────────
    s = blank(prs)
    header_bar(s, "Jobs 1 and 2", "First we clean our spec. Then we read their checklist.",
               "Two tabs that already save hours, even before looking at the TDR.")

    add_rect(s, Inches(0.45), Inches(1.52), Inches(6.05), Inches(5.38), CARD, LINE, rounded=True)
    add_rect(s, Inches(0.45), Inches(1.52), Inches(6.05), Inches(0.52), BLUE)
    add_textbox(s, Inches(0.65), Inches(1.58), Inches(5.7), Inches(0.42),
                "📄   Spec Validation", size=18, bold=True, color=WHITE, anchor=MSO_ANCHOR.MIDDLE)
    add_textbox(s, Inches(0.70), Inches(2.20), Inches(5.55), Inches(4.4),
                "Upload our Word / PDF specification.\n\n"
                "LEON checks, like a very patient editor:\n"
                "• Are the mandatory chapters there? (Purpose, Scope, Requirements…)\n"
                "• Are leftover template words still there?  <<name>>, TBD, XXX\n"
                "• Does each requirement have an ID and a clear “shall”?\n\n"
                "You get a verdict (good / acceptable / not ready) and a Word report.\n\n"
                "Bonus: one click builds the empty conformity matrix "
                "so the supplier does not type 400 rows by hand.",
                size=14, color=INK)

    add_rect(s, Inches(6.75), Inches(1.52), Inches(6.05), Inches(5.38), CARD, LINE, rounded=True)
    add_rect(s, Inches(6.75), Inches(1.52), Inches(6.05), Inches(0.52), WARN)
    add_textbox(s, Inches(6.95), Inches(1.58), Inches(5.7), Inches(0.42),
                "📊   Conformity Matrix", size=18, bold=True, color=WHITE, anchor=MSO_ANCHOR.MIDDLE)
    add_textbox(s, Inches(7.00), Inches(2.20), Inches(5.55), Inches(4.4),
                "Upload the supplier’s Excel / ODS — even if they renamed columns.\n\n"
                "LEON counts OK / NOK / NA, draws a pie chart, and lists every row.\n\n"
                "Then it re-reads every OK comment and raises a “point of attention” when:\n"
                "• the comment contradicts the tick  (“OK” + “not validated”)\n"
                "• conformity is only partial or still pending\n\n"
                "You download a colour-coded Excel (green / red / grey) for the meeting.",
                size=14, color=INK)
    footer(s, 7)
    notes(s, "Demo path: drop a matrix, show the pie, then open one red “point of attention”.")

    # ── 7. TDR ──────────────────────────────────────────────────
    s = blank(prs)
    header_bar(s, "Jobs 3 and 4 — the heart of the work",
               "The TDR is the proof. LEON puts the tick next to the proof.",
               "This is the tab to show in a TDR meeting.")

    # Mini example strip
    add_rect(s, Inches(0.45), Inches(1.48), Inches(12.4), Inches(1.35), AMBER_BG, WARN, rounded=True)
    add_textbox(s, Inches(0.70), Inches(1.58), Inches(12.0), Inches(0.32),
                "A typical catch  —  contrast of the display", size=14, bold=True, color=WARN)
    add_textbox(s, Inches(0.70), Inches(1.92), Inches(12.0), Inches(0.72),
                "Stellantis asks:  contrast ≥ 400:1     ·     Matrix:  OK     ·     TDR slide 14:  380:1\n"
                "LEON opens on this kind of incoherence — not on the 200 rows that already match.",
                size=14, color=INK)

    add_card_text(s, Inches(0.45), Inches(3.02), Inches(6.05), Inches(3.85),
                  "🔎  Matrix ↔ TDR",
                  "Upload the matrix and the supplier dossier (PPT / PDF / Word).\n\n"
                  "For each requirement LEON writes four lines:\n"
                  "what we asked · what they ticked · what the TDR says · which page.\n\n"
                  "The engineer can confirm, correct or ask for real proof. "
                  "Nothing is auto-accepted.",
                  NAVY_2, 16, 13)
    add_card_text(s, Inches(6.75), Inches(3.02), Inches(6.05), Inches(3.85),
                  "🏁  TDR Benchmark",
                  "Several suppliers, several technical files — no prices, only the technique.\n\n"
                  "LEON lines them up on the same topics: mechanics, display, electronics, "
                  "safety, software, testing, industrialisation…\n\n"
                  "You see strengths, gaps and open questions, then you still pick the partner.",
                  OK, 16, 13)
    footer(s, 8)
    notes(s,
          "Stress the example. Non-specialists remember one number (400 vs 380) better than a process.\n"
          "Mention Version Delta only if asked: it shows NOK→OK between two Excel versions.")

    # ── 8. Close ────────────────────────────────────────────────
    s = blank(prs)
    header_bar(s, "What I have delivered so far", "LEON prepares the review. People remain in charge.",
               "The functions already in the tool — and the rule I keep: the engineer still signs.")

    takeaways = [
        (OK, "Drop files in a browser",
         "Spec, matrix, TDR. No software to install for the reviewer."),
        (BLUE, "See mismatches first",
         "Fake OK, missing proof, wrong numbers, version changes."),
        (WARN, "Leave with an Excel",
         "Colour-coded report you can send to quality, purchasing or the supplier."),
        (NAVY_2, "Never a certificate",
         "If LEON finds nothing, that is not a stamp of approval. An engineer still signs."),
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
                "In short: we are building a careful reader for the spec, the matrix and the TDR — "
                "so Stellantis walks into supplier reviews with the contradictions already on the table.",
                size=15, bold=True, color=NAVY)
    footer(s, 9)
    notes(s,
          "This is the close of the product part. Next slides: people, status, training, availability.")

    # ── 10. Collaboration ───────────────────────────────────────
    s = blank(prs)
    header_bar(s, "Collaboration", "I do not work on this alone.",
               "Imane El Brouji and Patrick Garcia bring the mechatronics side. I turn it into LEON.")
    add_rect(s, Inches(0.45), Inches(1.52), Inches(6.05), Inches(3.55), CARD, LINE, rounded=True)
    add_rect(s, Inches(0.45), Inches(1.52), Inches(6.05), Inches(0.12), BLUE)
    add_textbox(s, Inches(0.70), Inches(1.80), Inches(5.55), Inches(0.40),
                "Imane El Brouji", size=22, bold=True, color=NAVY)
    add_textbox(s, Inches(0.70), Inches(2.28), Inches(5.55), Inches(0.32),
                "Mechatronics Engineering", size=14, bold=True, color=BLUE)
    add_textbox(s, Inches(0.70), Inches(2.75), Inches(5.55), Inches(2.00),
                "Shares the needs of the team, the real documents we receive, "
                "and the ideas of what would actually help an engineer in a review.",
                size=15, color=INK)

    add_rect(s, Inches(6.75), Inches(1.52), Inches(6.05), Inches(3.55), CARD, LINE, rounded=True)
    add_rect(s, Inches(6.75), Inches(1.52), Inches(6.05), Inches(0.12), OK)
    add_textbox(s, Inches(7.00), Inches(1.80), Inches(5.55), Inches(0.40),
                "Patrick Garcia", size=22, bold=True, color=NAVY)
    add_textbox(s, Inches(7.00), Inches(2.28), Inches(5.55), Inches(0.32),
                "Mechatronics Engineering", size=14, bold=True, color=OK)
    add_textbox(s, Inches(7.00), Inches(2.75), Inches(5.55), Inches(2.00),
                "Explains the mechatronics context: how a spec, a matrix and a TDR "
                "are used in the team, and what a good review must look like.",
                size=15, color=INK)

    add_rect(s, Inches(0.45), Inches(5.25), Inches(12.35), Inches(1.70), CARD, LINE, rounded=True)
    add_rect(s, Inches(0.45), Inches(5.25), Inches(0.12), Inches(1.70), NAVY_2)
    add_textbox(s, Inches(0.80), Inches(5.40), Inches(11.7), Inches(0.36),
                "How we work together", size=16, bold=True, color=NAVY)
    add_textbox(s, Inches(0.80), Inches(5.82), Inches(11.7), Inches(0.90),
                "They give the ideas and the technical meaning. I build and iterate on LEON. "
                "Nothing in the tool is designed without that mechatronics input — "
                "so the result stays useful for the team, not only for an IT exercise.",
                size=15, color=INK)
    footer(s, 10)
    notes(s,
          "Thank Imane and Patrick by name. Make clear you are the builder, they are the "
          "mechatronics experts. The manager should see you are not isolated.")

    # ── 11. Status and deployment ───────────────────────────────
    s = blank(prs)
    header_bar(s, "Status and next step", "The work is in progress. Deployment comes after.",
               "The whole team will have access once this step is finished.")
    steps = [
        (BLUE, "01  Now", "In progress",
         "I am finishing the LEON functions you just saw: spec, matrix, TDR cross-check and benchmark."),
        (WARN, "02  Next", "Finish this task",
         "Close the remaining work, test with real mechatronics files, and align with Imane and Patrick."),
        (OK, "03  Then", "Deployment for the team",
         "Once this task is finished, we start the deployment so the whole Mechatronics team can open LEON in a browser, with no installation."),
    ]
    x = Inches(0.45)
    for color, kicker, title, body in steps:
        add_rect(s, x, Inches(1.55), Inches(4.05), Inches(4.05), CARD, LINE, rounded=True)
        add_rect(s, x, Inches(1.55), Inches(4.05), Inches(0.12), color)
        add_textbox(s, x + Inches(0.25), Inches(1.85), Inches(3.55), Inches(0.36),
                    kicker, size=13, bold=True, color=color)
        add_textbox(s, x + Inches(0.25), Inches(2.28), Inches(3.55), Inches(0.80),
                    title, size=22, bold=True, color=NAVY)
        add_textbox(s, x + Inches(0.25), Inches(3.20), Inches(3.55), Inches(2.10),
                    body, size=15, color=INK)
        x += Inches(4.20)

    add_rect(s, Inches(0.45), Inches(5.80), Inches(12.35), Inches(1.18), GREEN_BG, OK, rounded=True)
    add_textbox(s, Inches(0.70), Inches(5.95), Inches(11.9), Inches(0.90),
                "Objective: every colleague who reviews a spec, a matrix or a TDR can use LEON — "
                "not only the people building it today.",
                size=16, bold=True, color=NAVY)
    footer(s, 11)
    notes(s,
          "Be clear: it is not live for everyone yet. Do not promise a date you do not have. "
          "The commitment is: finish this task, then deploy.")

    # ── 12. AI training ─────────────────────────────────────────
    s = blank(prs)
    header_bar(s, "Upcoming training", "AI training — and how Mechatronics can use it.",
               "A practical training for the team, not a computer-science course.")
    add_card_text(s, Inches(0.45), Inches(1.52), Inches(6.05), Inches(5.38),
                  "What this training is for",
                  "Understand what AI can (and cannot) do on our daily files.\n\n"
                  "See how a tool like LEON helps on specs, matrices and TDRs, "
                  "without replacing the engineer.\n\n"
                  "Give the team a common language: where AI saves time, "
                  "and where a person must still decide.\n\n"
                  "Bring examples back from our real mechatronics documents.",
                  BLUE, 18, 15)
    add_card_text(s, Inches(6.75), Inches(1.52), Inches(6.05), Inches(5.38),
                  "How the team can use it afterwards",
                  "Faster first reading of a supplier file before a TDR meeting.\n\n"
                  "Fewer missed “OK” that are not really OK.\n\n"
                  "Side-by-side comparison of several suppliers on the technique.\n\n"
                  "A habit: AI proposes, the mechatronics engineer validates.\n\n"
                  "I can share a short recap with the team after the training.",
                  OK, 18, 15)
    footer(s, 12)
    notes(s,
          "If the manager asks for the date or the provider, say you will confirm — "
          "do not invent a catalogue name. The message is: the team will be trained "
          "to use AI on our work, with the engineer remaining responsible.")

    # ── 13. Availability ────────────────────────────────────────
    s = blank(prs)
    header_bar(s, "To confirm with you", "Two points of organisation I would like to validate.",
               "Télétravail and the first school period.")
    add_rect(s, Inches(0.45), Inches(1.52), Inches(6.05), Inches(3.70), CARD, LINE, rounded=True)
    add_rect(s, Inches(0.45), Inches(1.52), Inches(6.05), Inches(0.52), BLUE)
    add_textbox(s, Inches(0.65), Inches(1.58), Inches(5.7), Inches(0.42),
                "Télétravail (TT)", size=18, bold=True, color=WHITE, anchor=MSO_ANCHOR.MIDDLE)
    add_textbox(s, Inches(0.70), Inches(2.25), Inches(5.55), Inches(2.70),
                "I take two days of télétravail per week:\n\n"
                "• Wednesday\n"
                "• Friday\n\n"
                "I would like to confirm this rhythm with you, "
                "so the team knows when I am on site and when I am in TT.",
                size=16, color=INK)

    add_rect(s, Inches(6.75), Inches(1.52), Inches(6.05), Inches(3.70), CARD, LINE, rounded=True)
    add_rect(s, Inches(6.75), Inches(1.52), Inches(6.05), Inches(0.52), WARN)
    add_textbox(s, Inches(6.95), Inches(1.58), Inches(5.7), Inches(0.42),
                "Période école", size=18, bold=True, color=WHITE, anchor=MSO_ANCHOR.MIDDLE)
    add_textbox(s, Inches(7.00), Inches(2.25), Inches(5.55), Inches(2.70),
                "Over the next two weeks I will be in my first school period "
                "(période école).\n\n"
                "I will not be on site during that time.\n\n"
                "After that, I am back on the usual company / TT rhythm.",
                size=16, color=INK)

    add_rect(s, Inches(0.45), Inches(5.42), Inches(12.35), Inches(1.52), AMBER_BG, WARN, rounded=True)
    add_textbox(s, Inches(0.70), Inches(5.58), Inches(11.9), Inches(0.36),
                "Thank you — I am happy to adjust if needed", size=16, bold=True, color=WARN)
    add_textbox(s, Inches(0.70), Inches(6.00), Inches(11.9), Inches(0.72),
                "These two points are for your validation. If the TT days or the school period "
                "need a different organisation, I can adapt.",
                size=15, color=INK)
    footer(s, 13)
    notes(s,
          "Ask clearly for confirmation. Do not rush. "
          "TT = mercredi et vendredi. Next two weeks = first school period. "
          "Then thank the manager for the time.")

    out.parent.mkdir(parents=True, exist_ok=True)
    prs.save(str(out))
    return out


if __name__ == "__main__":
    target = Path(__file__).resolve().parents[1] / "docs" / "LEON_intro_for_non_specialists.pptx"
    path = build(target)
    print(f"Wrote {path} ({path.stat().st_size} bytes)")
