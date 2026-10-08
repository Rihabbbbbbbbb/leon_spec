"""Regression coverage for charts with a single populated status."""
import base64
import builtins
from io import BytesIO
from xml.etree import ElementTree as ET

import pytest
from PIL import Image

from app.qa import conformity_analyzer as ca
from app.qa.conformity_report import generate_conformity_pdf


def without_matplotlib(monkeypatch):
    real_import = builtins.__import__

    def import_without_matplotlib(name, *args, **kwargs):
        if name == "matplotlib" or name.startswith("matplotlib."):
            raise ImportError("Simulating the production SVG fallback")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", import_without_matplotlib)


@pytest.mark.parametrize("status", ["OK", "NOK", "DEVIATION", "NA", "EMPTY"])
def test_single_status_svg_is_a_visible_full_circle(monkeypatch, status):
    without_matplotlib(monkeypatch)
    analysis = ca.ConformityAnalysis(stats={status: 33})
    svg = base64.b64decode(ca.generate_pie_chart(analysis))
    root = ET.fromstring(svg)
    ns = {"s": "http://www.w3.org/2000/svg"}
    circles = root.findall("s:circle", ns)
    assert len(circles) == 1
    assert circles[0].get("r") == "130"
    assert circles[0].get("fill") == ca._CHART_COLORS[status]
    assert root.findall("s:path", ns) == []
    text = "".join(root.itertext())
    assert "100.0%" in text
    assert f"{status}: 33" in text
    assert "(33 requirements)" in text
    assert analysis.chart_base64


def test_svg_mixed_distribution_preserves_slices(monkeypatch):
    without_matplotlib(monkeypatch)
    analysis = ca.ConformityAnalysis(stats={"OK": 50, "NA": 4, "EMPTY": 2})
    root = ET.fromstring(base64.b64decode(ca.generate_pie_chart(analysis)))
    ns = {"s": "http://www.w3.org/2000/svg"}
    assert len(root.findall("s:path", ns)) == 2
    assert root.findall("s:circle", ns) == []
    text = "".join(root.itertext())
    assert "OK: 50" in text and "NA: 4" in text
    assert "(54 requirements)" in text


def test_empty_distribution_has_no_chart():
    assert ca.generate_pie_chart(ca.ConformityAnalysis(stats={})) == ""


def test_single_status_png_contains_the_status_color():
    pytest.importorskip("matplotlib", exc_type=ImportError)
    analysis = ca.ConformityAnalysis(stats={"OK": 33})
    image = Image.open(BytesIO(base64.b64decode(ca.generate_pie_chart(analysis)))).convert("RGB")
    target = ca._CHART_COLORS["OK"].lstrip("#")
    rgb = tuple(int(target[i:i + 2], 16) for i in (0, 2, 4))
    assert sum(pixel == rgb for pixel in image.getdata()) > 10000


def test_svg_single_status_embeds_in_pdf(monkeypatch):
    without_matplotlib(monkeypatch)
    analysis = ca.ConformityAnalysis(stats={"OK": 33}, total_rows=33)
    ca.generate_pie_chart(analysis)
    pdf = generate_conformity_pdf(ca.analysis_to_dict(analysis))
    from PyPDF2 import PdfReader
    reader = PdfReader(BytesIO(pdf))
    text = " ".join(page.extract_text() for page in reader.pages)
    assert "Chart not available" not in text
    assert b"0.1569 0.6549 0.2706" in b"".join(
        page.get_contents().get_data() for page in reader.pages
    )
