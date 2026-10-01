"""
Evidence document parser for AERIS matrix↔TDR cross-check.

Reads supplier technical proof (TDR / PPT / PDF / DOCX / TXT) into
page-or-slide-aware chunks. Never invents content: a failed parse yields
an empty chunk list so the engine can mark requirements MANQUANT.
"""
from __future__ import annotations

import io
import os
import re
import tempfile
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional
from xml.etree import ElementTree as ET


_PPT_NS = {
    "a": "http://schemas.openxmlformats.org/drawingml/2006/main",
    "p": "http://schemas.openxmlformats.org/presentationml/2006/main",
    "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
}

_EVIDENCE_EXT = {".pptx", ".ppt", ".pdf", ".docx", ".txt"}


@dataclass
class EvidenceChunk:
    """One retrievable passage of supplier evidence."""
    file_name: str
    location: str          # "Slide 12" / "Page 90" / "Section …"
    text: str
    chunk_id: int = 0
    kind: str = "slide"    # slide | page | section | paragraph


@dataclass
class EvidenceDocument:
    file_name: str
    kind: str              # pptx | pdf | docx | txt
    chunks: List[EvidenceChunk] = field(default_factory=list)
    page_count: int = 0
    parse_error: str = ""


def is_evidence_file(name: str) -> bool:
    return Path(name).suffix.lower() in _EVIDENCE_EXT


def parse_evidence_bytes(file_name: str, content: bytes) -> EvidenceDocument:
    """Parse an uploaded evidence file from memory."""
    suffix = Path(file_name).suffix.lower()
    doc = EvidenceDocument(file_name=file_name, kind=suffix.lstrip(".") or "txt")
    try:
        if suffix in (".pptx", ".ppt"):
            chunks = _extract_pptx(content, file_name)
            doc.kind = "pptx"
        elif suffix == ".pdf":
            chunks = _extract_pdf(content, file_name)
            doc.kind = "pdf"
        elif suffix == ".docx":
            chunks = _extract_docx(content, file_name)
            doc.kind = "docx"
        else:
            text = content.decode("utf-8", errors="replace")
            chunks = _chunk_plain(text, file_name)
            doc.kind = "txt"
        doc.chunks = chunks
        doc.page_count = _count_locations(chunks)
    except Exception as exc:
        doc.parse_error = str(exc)
        doc.chunks = []
    return doc


def parse_evidence_path(path: Path) -> EvidenceDocument:
    return parse_evidence_bytes(path.name, path.read_bytes())


def _count_locations(chunks: List[EvidenceChunk]) -> int:
    return len({c.location for c in chunks})


def _extract_pptx(content: bytes, file_name: str) -> List[EvidenceChunk]:
    """
    Extract slide text from a PPTX (Office Open XML).

    Prefer python-pptx when installed (notes + tables). Fall back to a
    zip/XML reader so Azure Functions still work without the extra wheel.
    """
    chunks: List[EvidenceChunk] = []
    try:
        from pptx import Presentation  # type: ignore
        from pptx.enum.shapes import MSO_SHAPE_TYPE

        prs = Presentation(io.BytesIO(content))
        for i, slide in enumerate(prs.slides, 1):
            parts: List[str] = []
            for shape in slide.shapes:
                parts.extend(_shape_text(shape, MSO_SHAPE_TYPE))
            notes = ""
            try:
                if slide.has_notes_slide and slide.notes_slide.notes_text_frame:
                    notes = slide.notes_slide.notes_text_frame.text or ""
            except Exception:
                notes = ""
            if notes.strip():
                parts.append("Notes: " + notes.strip())
            text = _clean_block("\n".join(p for p in parts if p and p.strip()))
            if text:
                chunks.append(EvidenceChunk(
                    file_name=file_name,
                    location=f"Slide {i}",
                    text=text,
                    chunk_id=i,
                    kind="slide",
                ))
    except Exception:
        # Un PPTX exporté par un outil tiers, ou un .ppt renommé, fait
        # échouer python-pptx. Sans ce repli, le TDR entier serait lu
        # comme vide et toutes les exigences deviendraient "sans preuve".
        chunks = []

    if chunks:
        return chunks
    try:
        return _extract_pptx_xml(content, file_name)
    except Exception:
        return []


def _shape_text(shape, MSO_SHAPE_TYPE) -> List[str]:
    parts: List[str] = []
    try:
        if shape.has_text_frame:
            t = shape.text_frame.text
            if t and t.strip():
                parts.append(t.strip())
    except Exception:
        pass
    try:
        if shape.has_table:
            for row in shape.table.rows:
                cells = [c.text.strip() for c in row.cells if c.text and c.text.strip()]
                if cells:
                    parts.append(" | ".join(cells))
    except Exception:
        pass
    try:
        if shape.shape_type == MSO_SHAPE_TYPE.GROUP:
            for inner in shape.shapes:
                parts.extend(_shape_text(inner, MSO_SHAPE_TYPE))
    except Exception:
        pass
    return parts


def _extract_pptx_xml(content: bytes, file_name: str) -> List[EvidenceChunk]:
    """Minimal PPTX reader: concatenate every a:t run per slide XML part."""
    chunks: List[EvidenceChunk] = []
    with zipfile.ZipFile(io.BytesIO(content)) as zf:
        slide_names = sorted(
            n for n in zf.namelist()
            if re.match(r"ppt/slides/slide\d+\.xml$", n)
        )
        for name in slide_names:
            idx = int(re.search(r"slide(\d+)", name).group(1))
            xml = zf.read(name)
            root = ET.fromstring(xml)
            runs = [el.text for el in root.iter("{http://schemas.openxmlformats.org/drawingml/2006/main}t") if el.text]
            text = _clean_block("\n".join(runs))
            if text:
                chunks.append(EvidenceChunk(
                    file_name=file_name,
                    location=f"Slide {idx}",
                    text=text,
                    chunk_id=idx,
                    kind="slide",
                ))
    return chunks


def _extract_pdf(content: bytes, file_name: str) -> List[EvidenceChunk]:
    try:
        from PyPDF2 import PdfReader
        reader = PdfReader(io.BytesIO(content))
    except Exception:
        return []
    chunks: List[EvidenceChunk] = []
    for i, page in enumerate(reader.pages, 1):
        try:
            text = _clean_block(page.extract_text() or "")
        except Exception:
            text = ""
        if text:
            chunks.append(EvidenceChunk(
                file_name=file_name,
                location=f"Page {i}",
                text=text,
                chunk_id=i,
                kind="page",
            ))
    return chunks


def _extract_docx(content: bytes, file_name: str) -> List[EvidenceChunk]:
    # Nom de fichier unique : deux analyses simultanées portant le même
    # nom de TDR écrivaient au même endroit.
    suffix = Path(file_name).suffix or ".docx"
    handle, tmp_name = tempfile.mkstemp(prefix="aeris_", suffix=suffix)
    tmp = Path(tmp_name)
    text = ""
    try:
        with os.fdopen(handle, "wb") as fh:
            fh.write(content)
        from app.qa.retrieval import extract_text_from_file, _split_into_chunks
        text = extract_text_from_file(tmp)
    except Exception:
        text = ""
    finally:
        try:
            tmp.unlink(missing_ok=True)
        except Exception:
            pass
    if not text:
        return _extract_docx_xml(content, file_name)
    from app.qa.retrieval import _split_into_chunks
    inner = _split_into_chunks(text, file_name)
    return [
        EvidenceChunk(
            file_name=file_name,
            location=c.section or f"Chunk {c.chunk_id}",
            text=c.text,
            chunk_id=c.chunk_id,
            kind="section",
        )
        for c in inner if c.text.strip()
    ]


def _extract_docx_xml(content: bytes, file_name: str) -> List[EvidenceChunk]:
    """Repli DOCX : lire les runs w:t directement dans word/document.xml."""
    try:
        with zipfile.ZipFile(io.BytesIO(content)) as zf:
            if "word/document.xml" not in zf.namelist():
                return []
            root = ET.fromstring(zf.read("word/document.xml"))
    except Exception:
        return []
    ns = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
    paragraphs: List[str] = []
    for para in root.iter(f"{ns}p"):
        runs = [el.text for el in para.iter(f"{ns}t") if el.text]
        line = _clean_block(" ".join(runs))
        if line:
            paragraphs.append(line)
    if not paragraphs:
        return []
    return [
        EvidenceChunk(
            file_name=file_name,
            location=f"Paragraph {i}",
            text=line,
            chunk_id=i,
            kind="paragraph",
        )
        for i, line in enumerate(paragraphs, 1)
    ]


def _chunk_plain(text: str, file_name: str) -> List[EvidenceChunk]:
    """
    Split a TXT TDR on blank lines or 'Slide N' / 'Page N' markers so tests
    and exported-PPT text dumps keep location awareness.
    """
    text = text.replace("\r\n", "\n")
    marker = re.compile(
        r"^(?:---\s*)?(slide|page|section)\s+(\d+|[A-Za-z0-9._-]+)\s*:?\s*",
        re.I | re.M,
    )
    matches = list(marker.finditer(text))
    chunks: List[EvidenceChunk] = []
    if matches:
        for i, m in enumerate(matches):
            start = m.end()
            end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
            body = _clean_block(text[start:end])
            if not body:
                continue
            kind = m.group(1).lower()
            loc = f"{kind.capitalize()} {m.group(2)}"
            chunks.append(EvidenceChunk(
                file_name=file_name, location=loc, text=body,
                chunk_id=i + 1, kind=kind if kind != "section" else "section",
            ))
        return chunks

    blocks = [b.strip() for b in re.split(r"\n\s*\n", text) if b.strip()]
    for i, block in enumerate(blocks, 1):
        chunks.append(EvidenceChunk(
            file_name=file_name,
            location=f"Block {i}",
            text=_clean_block(block),
            chunk_id=i,
            kind="paragraph",
        ))
    return chunks


def _clean_block(text: str) -> str:
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()
