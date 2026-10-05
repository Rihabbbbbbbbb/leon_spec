"""Document ingestion for multi-supplier TDR benchmarks.

Every page keeps its 1-based page/slide number so that every downstream fact
can be traced back to the original document. Boilerplate repeated on most
pages (confidentiality banners, copyright footers) is removed because it is
noise for both retrieval and the LLM.
"""
from __future__ import annotations

import collections
import io
import re
import threading
from dataclasses import dataclass, field
from pathlib import Path

from app.qa.tdr_bench_taxonomy import COMMERCIAL_PATTERN, PARAMETER_PATTERNS, classify_text

SUPPORTED_SUFFIXES = {".pdf", ".pptx", ".docx", ".txt"}
MAX_PAGE_CHARS = 9000
PDF_LOCK = threading.RLock()

KNOWN_SUPPLIERS: tuple[tuple[str, str], ...] = (
    (r"aumovio", "Aumovio"), (r"continental", "Continental"), (r"visteon", "Visteon"),
    (r"valeo", "Valeo"), (r"tianma", "Tianma"), (r"\btyw\b", "TYW"), (r"car ?ux", "CarUX"),
    (r"\bboe\b", "BOE"), (r"lg ?display", "LG Display"), (r"\blge\b|lg electronics", "LG Electronics"),
    (r"bosch", "Bosch"), (r"marelli", "Marelli"), (r"harman", "Harman"),
    (r"panasonic", "Panasonic"), (r"alps ?alpine", "Alps Alpine"), (r"denso", "Denso"),
    (r"forvia", "Forvia"), (r"faurecia", "Faurecia"), (r"hyundai ?mobis|\bmobis\b", "Hyundai Mobis"),
    (r"innolux", "Innolux"), (r"\bauo\b", "AUO"), (r"\bjdi\b|japan display", "JDI"),
    (r"sharp", "Sharp"), (r"preh", "Preh"), (r"yanfeng", "Yanfeng"), (r"desay", "Desay SV"),
    (r"pioneer", "Pioneer"), (r"kostal", "Kostal"), (r"nippon seiki", "Nippon Seiki"),
    (r"joyson", "Joyson"), (r"\bzf\b", "ZF"), (r"aptiv", "Aptiv"), (r"magna", "Magna"),
    (r"samsung", "Samsung"), (r"huawei", "Huawei"), (r"pateo", "PATEO"), (r"hangsheng", "Hangsheng"),
)


@dataclass
class Page:
    number: int
    text: str
    images: int = 0
    image_ratio: float = 0.0
    vision_text: str = ""
    commercial: bool = False
    domain_hits: dict[str, int] = field(default_factory=dict)

    @property
    def full_text(self) -> str:
        if self.vision_text:
            return f"{self.text}\n[VISION TRANSCRIPT OF SLIDE GRAPHICS]\n{self.vision_text}"
        return self.text

    def needs_vision(self) -> bool:
        chars = len(self.text.strip())
        if self.images == 0:
            return False
        return chars < 250 or (self.image_ratio >= 0.35 and chars < 900)


@dataclass
class DocumentText:
    doc_id: str
    file_name: str
    kind: str
    pages: list[Page]
    warnings: list[str] = field(default_factory=list)
    title: str = ""

    @property
    def char_count(self) -> int:
        return sum(len(p.text) for p in self.pages)

    def page(self, number: int) -> Page | None:
        if 1 <= number <= len(self.pages) and self.pages[number - 1].number == number:
            return self.pages[number - 1]
        return next((p for p in self.pages if p.number == number), None)


_BULLETS = re.compile(r"^[\s\u2022\u25cf\u25aa\u25a0\u25ba\u27a2\u2751\u00d8\u00fc\uf0a7\uf0d8\uf0fc\-–•·]+")


def _clean_lines(text: str) -> list[str]:
    lines = []
    previous = None
    for raw in text.replace("\r", "\n").split("\n"):
        line = re.sub(r"[ \t\u00a0]+", " ", raw).strip()
        line = _BULLETS.sub("", line).strip() if len(line) > 1 else line
        if not line:
            continue
        # Some PDF exporters duplicate every text run ("Agenda\nAgenda").
        if line == previous:
            continue
        lines.append(line)
        previous = line
    return lines


def _remove_boilerplate(pages_lines: list[list[str]]) -> list[str]:
    """Drop short lines that appear on more than 40% of pages (headers/footers)."""
    if len(pages_lines) < 5:
        return ["\n".join(lines) for lines in pages_lines]
    counter: collections.Counter[str] = collections.Counter()
    for lines in pages_lines:
        counter.update({ln for ln in lines if len(ln) <= 120})
    threshold = max(3, int(len(pages_lines) * 0.4))
    boiler = {ln for ln, n in counter.items() if n >= threshold and not re.fullmatch(r"\d{1,4}", ln)}
    page_numbers = re.compile(r"^\d{1,3}$")
    cleaned = []
    for lines in pages_lines:
        kept = [ln for ln in lines if ln not in boiler and not page_numbers.match(ln)]
        cleaned.append("\n".join(kept))
    return cleaned


def _finalize(doc: DocumentText) -> DocumentText:
    for page in doc.pages:
        if len(page.text) > MAX_PAGE_CHARS:
            page.text = page.text[:MAX_PAGE_CHARS] + "\n[...truncated]"
        page.commercial = bool(COMMERCIAL_PATTERN.search(page.text)) and len(
            COMMERCIAL_PATTERN.findall(page.text)) >= 3
        page.domain_hits = classify_text(page.text)
    if not doc.pages:
        doc.warnings.append("No page could be read from this document.")
    elif doc.char_count < 200:
        doc.warnings.append("Almost no extractable text: the document is probably scanned; enable vision.")
    return doc


def load_pdf(content: bytes, doc_id: str, file_name: str) -> DocumentText:
    # PyMuPDF is not thread-safe (concurrent use from several threads can crash the interpreter),
    # so every open/extract/render/close happens under one process-wide lock.
    with PDF_LOCK:
        return _load_pdf_locked(content, doc_id, file_name)


def _load_pdf_locked(content: bytes, doc_id: str, file_name: str) -> DocumentText:
    import fitz  # PyMuPDF

    doc = DocumentText(doc_id, file_name, "pdf", [])
    try:
        pdf = fitz.open(stream=content, filetype="pdf")
    except Exception as exc:  # corrupted / encrypted files
        doc.warnings.append(f"PDF cannot be opened: {type(exc).__name__}")
        return _finalize(doc)
    try:
        return _read_pdf(pdf, doc)
    finally:
        pdf.close()


def _read_pdf(pdf, doc: DocumentText) -> DocumentText:
    if pdf.needs_pass:
        doc.warnings.append("PDF is password protected; it cannot be analysed.")
        return _finalize(doc)
    doc.title = (pdf.metadata or {}).get("title") or ""
    raw_lines, meta = [], []
    for index, page in enumerate(pdf):
        try:
            text = page.get_text("text")
        except Exception:
            text = ""
            doc.warnings.append(f"Text extraction failed on page {index + 1}.")
        area = abs(page.rect.width * page.rect.height) or 1.0
        covered = 0.0
        images = 0
        try:
            for info in page.get_image_info():
                x0, y0, x1, y1 = info.get("bbox", (0, 0, 0, 0))
                w, h = max(0.0, x1 - x0), max(0.0, y1 - y0)
                if w * h > area * 0.01:  # ignore logos / bullets
                    images += 1
                    covered += w * h
        except Exception:
            pass
        try:
            images += sum(1 for d in page.get_drawings() if d.get("fill")) // 40  # dense vector charts
        except Exception:
            pass
        raw_lines.append(_clean_lines(text))
        meta.append((images, min(1.0, covered / area)))
    for index, text in enumerate(_remove_boilerplate(raw_lines)):
        images, ratio = meta[index]
        doc.pages.append(Page(index + 1, text, images, round(ratio, 3)))
    return _finalize(doc)


def load_pptx(content: bytes, doc_id: str, file_name: str) -> DocumentText:
    from pptx import Presentation
    from pptx.util import Emu

    doc = DocumentText(doc_id, file_name, "pptx", [])
    try:
        prs = Presentation(io.BytesIO(content))
    except Exception as exc:
        doc.warnings.append(f"PowerPoint cannot be opened: {type(exc).__name__}")
        return _finalize(doc)
    slide_area = float(Emu(prs.slide_width or 1) * Emu(prs.slide_height or 1)) or 1.0
    raw_lines, meta = [], []

    def walk(shapes, out, pics):
        for shape in shapes:
            if getattr(shape, "shape_type", None) == 6 and hasattr(shape, "shapes"):  # group
                walk(shape.shapes, out, pics)
                continue
            if getattr(shape, "has_table", False) and shape.has_table:
                for row in shape.table.rows:
                    cells = [c.text.strip().replace("\n", " ") for c in row.cells]
                    if any(cells):
                        out.append(" | ".join(cells))
            elif getattr(shape, "has_text_frame", False) and shape.has_text_frame:
                out.extend(shape.text_frame.text.split("\n"))
            if getattr(shape, "shape_type", None) == 13:  # picture
                try:
                    pics.append(float(shape.width) * float(shape.height))
                except Exception:
                    pics.append(0.0)

    for slide in prs.slides:
        out, pics = [], []
        walk(slide.shapes, out, pics)
        if slide.has_notes_slide and slide.notes_slide.notes_text_frame is not None:
            notes = slide.notes_slide.notes_text_frame.text.strip()
            if notes:
                out.append("[Speaker notes] " + notes)
        raw_lines.append(_clean_lines("\n".join(out)))
        meta.append((len([p for p in pics if p > slide_area * 0.01]), min(1.0, sum(pics) / slide_area)))
    for index, text in enumerate(_remove_boilerplate(raw_lines)):
        images, ratio = meta[index]
        doc.pages.append(Page(index + 1, text, images, round(ratio, 3)))
    if any(p.needs_vision() for p in doc.pages):
        doc.warnings.append("Image-heavy slides in PPTX cannot be rendered for vision; export to PDF for full coverage.")
    return _finalize(doc)


def _pseudo_pages(blocks: list[str], size: int = 3500) -> list[str]:
    pages, current = [], []
    length = 0
    for block in blocks:
        if current and length + len(block) > size:
            pages.append("\n".join(current))
            current, length = [], 0
        current.append(block)
        length += len(block) + 1
    if current:
        pages.append("\n".join(current))
    return pages


def load_docx(content: bytes, doc_id: str, file_name: str) -> DocumentText:
    import docx

    doc = DocumentText(doc_id, file_name, "docx", [])
    try:
        document = docx.Document(io.BytesIO(content))
    except Exception as exc:
        doc.warnings.append(f"Word document cannot be opened: {type(exc).__name__}")
        return _finalize(doc)
    blocks = [p.text.strip() for p in document.paragraphs if p.text.strip()]
    for table in document.tables:
        for row in table.rows:
            cells = [c.text.strip().replace("\n", " ") for c in row.cells]
            if any(cells):
                blocks.append(" | ".join(cells))
    for index, text in enumerate(_pseudo_pages(blocks)):
        doc.pages.append(Page(index + 1, "\n".join(_clean_lines(text))))
    doc.warnings.append("Word documents have no fixed pages: 'page' numbers are text sections of ~3500 characters.")
    return _finalize(doc)


def load_txt(content: bytes, doc_id: str, file_name: str) -> DocumentText:
    doc = DocumentText(doc_id, file_name, "txt", [])
    text = content.decode("utf-8", errors="replace")
    for index, chunk in enumerate(_pseudo_pages([b for b in text.split("\n\n") if b.strip()])):
        doc.pages.append(Page(index + 1, "\n".join(_clean_lines(chunk))))
    return _finalize(doc)


def load_document(content: bytes, doc_id: str, file_name: str) -> DocumentText:
    suffix = Path(file_name).suffix.lower()
    loader = {".pdf": load_pdf, ".pptx": load_pptx, ".docx": load_docx, ".txt": load_txt}.get(suffix)
    if loader is None:
        raise ValueError(f"Unsupported file type: {file_name}")
    return loader(content, doc_id, file_name)


def detect_supplier(file_name: str, first_pages_text: str = "") -> str:
    """Best-effort supplier name: known names in the file name, then in the first pages."""
    stem = Path(file_name).stem
    haystack = re.sub(r"[_\-.]+", " ", stem).casefold()
    for pattern, name in KNOWN_SUPPLIERS:
        if re.search(pattern, haystack):
            return name
    if first_pages_text:
        counts = collections.Counter()
        low = first_pages_text.casefold()
        for pattern, name in KNOWN_SUPPLIERS:
            n = len(re.findall(pattern, low))
            if n:
                counts[name] = n
        if counts:
            return counts.most_common(1)[0][0]
    cleaned = re.sub(r"[_\-]+", " ", stem).strip()
    return cleaned[:40] or "Supplier"


def detect_mentions(doc: DocumentText) -> dict[str, list[dict]]:
    """Regex mentions of canonical parameters, with page provenance."""
    found: dict[str, list[dict]] = {}
    for page in doc.pages:
        text = page.full_text
        for key, patterns in PARAMETER_PATTERNS.items():
            for pattern in patterns:
                for match in pattern.finditer(text):
                    value = " ".join(g for g in match.groups() if g) if match.groups() else match.group(0)
                    if key == "resolution":
                        value = f"{match.group(1)} x {match.group(2)}"
                    elif key == "operating_temperature":
                        if int(match.group(2)) > 150:
                            continue
                        low_temp = re.sub(r"\s+", "", match.group(1))
                        value = f"{low_temp} / +{match.group(2)} °C"
                    elif key == "contrast_ratio":
                        value = re.sub(r"[.,\s]", "", match.group(1)) + ":1"
                    elif key == "weight":
                        value = f"{match.group(1)} {match.group(2)}"
                    start = max(0, match.start() - 60)
                    excerpt = text[start:match.end() + 60].replace("\n", " ").strip()
                    found.setdefault(key, []).append({
                        "value": value.strip(), "page": page.number, "excerpt": excerpt[:220],
                    })
    for key, items in found.items():
        seen, unique = set(), []
        for item in items:
            sig = (item["value"].casefold(), item["page"])
            if sig not in seen:
                seen.add(sig)
                unique.append(item)
        found[key] = unique[:40]
    return found


def render_page_png(content: bytes, page_number: int, dpi: int = 110) -> bytes:
    import fitz

    with PDF_LOCK, fitz.open(stream=content, filetype="pdf") as pdf:
        if not 1 <= page_number <= pdf.page_count:
            raise IndexError(page_number)
        return pdf[page_number - 1].get_pixmap(dpi=dpi).tobytes("png")
