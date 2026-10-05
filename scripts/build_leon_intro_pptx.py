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


def footer(slide, page, total=8):
    add_rect(slide, 0, Inches(7.22), W, Inches(0.28), NAVY)
    add_textbox(slide, Inches(0.4), Inches(7.22), Inches(10), Inches(0.28),
                "LEON  ·  Quality Analysis  ·  Internal briefing for non-specialists",
                size=11, color=RGBColor(0xA9, 0xBC, 0xD4), anchor=MSO_ANCHOR.MIDDLE)
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
    add_textbox(s, Inches(0.7), Inches(1.55), Inches(11.5), Inches(0.35),
                "STELLANTIS  ·  MECHATRONICS ENGINEERING", size=13, bold=True,
                color=RGBColor(0xA9, 0xBC, 0xD4))
    add_textbox(s, Inches(0.7), Inches(2.00), Inches(12), Inches(1.0),
                "LEON", size=60, bold=True, color=WHITE)
    add_textbox(s, Inches(0.7), Inches(3.05), Inches(11.5), Inches(0.9),
                "A short briefing: how we review supplier documents", size=26,
                color=RGBColor(0xE8, 0xEE, 0xFB))
    add_textbox(s, Inches(0.7), Inches(4.05), Inches(11.5), Inches(0.9),
                "No AI background needed. This is about three papers that must tell the same story:\n"
                "what we ask, what the supplier claims, and what their technical file actually shows.",
                size=16, color=RGBColor(0xC5, 0xD4, 0xE8))
    add_rect(s, Inches(0.7), Inches(5.35), Inches(3.4), Inches(0.08), BLUE)
    add_textbox(s, Inches(0.7), Inches(5.55), Inches(11.5), Inches(0.35),
                "Internal  ·  8 slides  ·  For managers, quality, purchasing and engineering partners",
                size=14, color=RGBColor(0xA9, 0xBC, 0xD4))
    pills = [
        (BLUE, "SPEC", "What we ask"),
        (WARN, "MATRIX", "What they tick"),
        (OK, "TDR", "What they prove"),
    ]
    px = Inches(0.7)
    for color, label, hint in pills:
        add_rect(s, px, Inches(6.10), Inches(3.4), Inches(0.85), RGBColor(0x15, 0x38, 0x62), None, rounded=True)
        add_rect(s, px, Inches(6.10), Inches(0.10), Inches(0.85), color)
        add_textbox(s, px + Inches(0.28), Inches(6.16), Inches(3.0), Inches(0.38),
                    label, size=16, bold=True, color=WHITE, anchor=MSO_ANCHOR.MIDDLE)
        add_textbox(s, px + Inches(0.28), Inches(6.50), Inches(3.0), Inches(0.35),
                    hint, size=13, color=RGBColor(0xA9, 0xBC, 0xD4), anchor=MSO_ANCHOR.TOP)
        px += Inches(3.65)
    notes(s,
          "Open by saying LEON is a reading assistant for supplier files, not a science project.\n"
          "The rest of the deck never uses the words model, algorithm or prompt.")

    # ── 2. Three documents ──────────────────────────────────────
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
    footer(s, 2)
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
    footer(s, 3)
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
    footer(s, 4)
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
    footer(s, 5)
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
    footer(s, 6)
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
    footer(s, 7)
    notes(s,
          "Stress the example. Non-specialists remember one number (400 vs 380) better than a process.\n"
          "Mention Version Delta only if asked: it shows NOK→OK between two Excel versions.")

    # ── 8. Close ────────────────────────────────────────────────
    s = blank(prs)
    header_bar(s, "What to remember", "LEON prepares the review. People remain in charge.",
               "A first reading in minutes, so the meeting is about the real issues.")

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
    footer(s, 8)
    notes(s,
          "Close: happy to open the live page and drop a sample matrix if there are 5 minutes left.\n"
          "Do not oversell accuracy. The product is a first reading plus a human decision.")

    out.parent.mkdir(parents=True, exist_ok=True)
    prs.save(str(out))
    return out


if __name__ == "__main__":
    target = Path(__file__).resolve().parents[1] / "docs" / "LEON_intro_for_non_specialists.pptx"
    path = build(target)
    print(f"Wrote {path} ({path.stat().st_size} bytes)")
