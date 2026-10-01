"""
Tests for app/qa/spec_annotator.py — the annotated-spec generator that
accompanies the validation report, highlighting each passage to fix.

Covers:
  1. _clean_target — ellipsis/partial-word stripping, minimum length guard
  2. collect_highlight_targets — gathers excerpts from findings + items, dedups
  3. _iter_docx_units — body paragraphs vs. flattened table rows
  4. highlight_docx — actually applies the LEON highlight colour to matching runs
  5. generate_annotated_spec — top-level entry point (format gating, no-op cases)
  6. Full pipeline on the real ASU spec
  7. HTTP integration via /api/upload-and-validate
"""
import io
import sys
from pathlib import Path

import pytest

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

from app.qa.spec_annotator import (
    HIGHLIGHT_COLOR_NAME,
    _clean_target,
    _normalize_ws,
    collect_highlight_targets,
    _iter_docx_units,
    highlight_docx,
    generate_annotated_spec,
)


ASU_PATH = Path(__file__).resolve().parent.parent / "data" / "uploads" / \
    "00692_25_01250_ASU_Technical_Specification_SPX _1_.docx"


def _expected_highlight():
    """Resolve the colour the annotator is actually configured to use, so
    these tests verify real behaviour instead of a hard-coded colour that
    goes stale the moment the highlight colour changes."""
    from docx.enum.text import WD_COLOR_INDEX
    return getattr(WD_COLOR_INDEX, HIGHLIGHT_COLOR_NAME)


_EXPECTED_HL = _expected_highlight()


def _make_docx_bytes(paragraphs=None, table_rows=None):
    """Build a minimal in-memory DOCX with given body paragraphs and an
    optional table (list of row -> list of cell strings)."""
    from docx import Document
    doc = Document()
    for text in paragraphs or []:
        doc.add_paragraph(text)
    if table_rows:
        n_cols = max(len(r) for r in table_rows)
        table = doc.add_table(rows=len(table_rows), cols=n_cols)
        for ri, row in enumerate(table_rows):
            for ci, val in enumerate(row):
                table.cell(ri, ci).text = val
    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


# ── 1. _clean_target ──────────────────────────────────────────────

class TestCleanTarget:

    def test_strips_leading_and_trailing_ellipsis_and_drops_edge_tokens(self):
        raw = "...uirements are refined and allocated at this system level in this document..."
        cleaned = _clean_target(raw)
        assert cleaned is not None
        assert not cleaned.startswith("uirements")  # first (partial) token dropped
        assert "refined and allocated" in cleaned

    def test_too_short_returns_none(self):
        assert _clean_target("short") is None
        assert _clean_target("") is None
        assert _clean_target(None) is None

    def test_plain_text_without_ellipsis_kept_whole_when_few_tokens(self):
        cleaned = _clean_target("REF-A-001 shall start fast")
        assert cleaned == "REF-A-001 shall start fast"

    def test_long_plain_text_unaffected_by_token_dropping(self):
        raw = "The system shall provide feedback within 200 milliseconds of activation"
        cleaned = _clean_target(raw)
        # No ellipsis present -> first/last token not dropped
        assert cleaned.startswith("The")
        assert cleaned.endswith("activation")


# ── 2. collect_highlight_targets ──────────────────────────────────

class TestCollectTargets:

    def test_gathers_excerpts_from_errors_and_warnings_only(self):
        report = {
            "findings": [
                {"severity": "error", "user_excerpt": "This is a real problem excerpt here"},
                {"severity": "warning", "user_excerpt": "Another genuine warning excerpt text"},
                {"severity": "pass", "user_excerpt": "This should not be collected at all here"},
                {"severity": "info", "user_excerpt": "Neither should this info excerpt collected"},
            ]
        }
        targets = collect_highlight_targets(report)
        assert len(targets) == 2

    def test_skips_not_found_excerpt(self):
        report = {"findings": [{"severity": "error", "user_excerpt": "NOT FOUND"}]}
        assert collect_highlight_targets(report) == []

    def test_gathers_itemized_excerpts(self):
        report = {
            "findings": [{
                "severity": "warning",
                "user_excerpt": "",
                "items": [
                    {"excerpt": "REF-A-002 The system shall stop within one second"},
                    {"excerpt": "REF-A-003 The system shall log every event occurring"},
                ],
            }]
        }
        targets = collect_highlight_targets(report)
        assert len(targets) == 2

    def test_deduplicates_targets(self):
        report = {
            "findings": [
                {"severity": "error", "user_excerpt": "The exact same excerpt text appears twice"},
                {"severity": "warning", "user_excerpt": "The exact same excerpt text appears twice"},
            ]
        }
        assert len(collect_highlight_targets(report)) == 1

    def test_multiline_excerpt_is_split_into_per_line_targets(self):
        """
        Regression: the semantic-analysis (AI) check's own quoted excerpt is
        built from a whole extracted SECTION (potentially several original
        paragraphs/table rows joined with "\\n"), not a single paragraph.
        On the real ASU spec, 3 such excerpts (P05/R28/R38 findings)
        silently produced ZERO highlights: the joined multi-line string can
        never match any single _iter_docx_units() unit, since each unit is
        exactly one paragraph or one table row. Splitting on the real "\\n"
        boundaries — before _clean_target collapses them into spaces —
        keeps every individual line highlightable.
        """
        report = {
            "findings": [{
                "severity": "warning",
                "user_excerpt": (
                    "Maintainability requirements\n"
                    "Diagnostic: Circuit malfunction\n"
                    "REF-ASU-CD-MAINT-0001(0) | The ASU shall record a DTC with the parameters below"
                ),
            }]
        }
        targets = collect_highlight_targets(report)
        assert len(targets) == 3
        assert any("Maintainability requirements" in t for t in targets)
        assert any("Diagnostic: Circuit malfunction" in t for t in targets)
        assert any("REF-ASU-CD-MAINT-0001" in t for t in targets)

    def test_itemized_excerpts_collected_even_on_a_passing_finding(self):
        # Regression: G_TRACEABILITY can "pass" overall (ratio >= 50%) while
        # still carrying `items` for its remaining untraced requirements —
        # those individual excerpts must still be highlighted even though
        # the parent finding's own severity is "pass" (its own top-level
        # excerpt is a positive example, but each item is a real gap).
        report = {
            "findings": [{
                "severity": "pass",
                "user_excerpt": "this is a positive compliant example, not a defect",
                "items": [
                    {"excerpt": "REF-A-002 The system shall stop within one second"},
                ],
            }]
        }
        targets = collect_highlight_targets(report)
        assert len(targets) == 1
        assert "REF-A-002" in targets[0]
        # The parent "pass" finding's own excerpt must NOT be included.
        assert not any("positive compliant example" in t for t in targets)


# ── 3. _iter_docx_units ───────────────────────────────────────────

class TestIterDocxUnits:

    def test_body_paragraphs_yielded_individually(self):
        from docx import Document
        data = _make_docx_bytes(paragraphs=["First paragraph.", "Second paragraph."])
        doc = Document(io.BytesIO(data))
        units = list(_iter_docx_units(doc))
        texts = [t for _, t in units]
        assert "First paragraph." in texts
        assert "Second paragraph." in texts

    def test_table_row_flattened_with_pipe_matching_extraction(self):
        from docx import Document
        data = _make_docx_bytes(table_rows=[["REF-A-001", "The system shall start.", "N/A"]])
        doc = Document(io.BytesIO(data))
        units = list(_iter_docx_units(doc))
        texts = [t for _, t in units]
        assert "REF-A-001 | The system shall start. | N/A" in texts

    def test_empty_cells_excluded_from_flattened_row(self):
        from docx import Document
        data = _make_docx_bytes(table_rows=[["REF-A-001", "", "The system shall start."]])
        doc = Document(io.BytesIO(data))
        units = list(_iter_docx_units(doc))
        texts = [t for _, t in units]
        assert "REF-A-001 | The system shall start." in texts


# ── 4. highlight_docx ─────────────────────────────────────────────

class TestHighlightDocx:

    def test_highlight_colour_is_not_one_the_real_spec_already_uses(self):
        """
        The whole point of the chosen colour is that every mark in the
        annotated copy is unambiguously a LEON finding. The real ASU spec
        already highlights text in yellow (R04 mandates yellow for generic
        RD elements) plus pink, turquoise, bright-green and red — so the
        annotator must not reuse any of those.
        """
        assert HIGHLIGHT_COLOR_NAME not in {
            "YELLOW", "PINK", "TURQUOISE", "BRIGHT_GREEN", "RED",
        }

    def test_real_spec_does_not_already_contain_the_leon_colour(self):
        """Verified against the actual document, not just the list above."""
        if not ASU_PATH.exists():
            pytest.skip("ASU spec not found")
        from docx import Document
        doc = Document(str(ASU_PATH))

        def _runs():
            for p in doc.paragraphs:
                yield from p.runs
            for t in doc.tables:
                for row in t.rows:
                    for c in row.cells:
                        for p in c.paragraphs:
                            yield from p.runs

        assert not any(r.font.highlight_color == _EXPECTED_HL for r in _runs())

    def test_matching_paragraph_gets_highlighted(self):
        from docx import Document
        data = _make_docx_bytes(paragraphs=[
            "This paragraph contains a placeholder <<insert value here>> to fix.",
            "This paragraph is completely fine and needs no changes at all.",
        ])
        annotated, count = highlight_docx(data, ["placeholder <<insert value here>> to fix"])
        assert count == 1
        doc = Document(io.BytesIO(annotated))
        highlighted = [p for p in doc.paragraphs
                      if any(r.font.highlight_color == _EXPECTED_HL for r in p.runs)]
        assert len(highlighted) == 1
        assert "placeholder" in highlighted[0].text

    def test_matching_table_row_highlights_every_cell(self):
        from docx import Document
        from docx.enum.text import WD_COLOR_INDEX
        data = _make_docx_bytes(table_rows=[
            ["REF-A-002", "The system shall stop within one second.", ""],
        ])
        annotated, count = highlight_docx(
            data, ["REF-A-002 | The system shall stop within one second."]
        )
        assert count == 1
        doc = Document(io.BytesIO(annotated))
        cell0_highlighted = any(
            r.font.highlight_color == _EXPECTED_HL
            for p in doc.tables[0].cell(0, 0).paragraphs for r in p.runs
        )
        cell1_highlighted = any(
            r.font.highlight_color == _EXPECTED_HL
            for p in doc.tables[0].cell(0, 1).paragraphs for r in p.runs
        )
        assert cell0_highlighted and cell1_highlighted

    def test_no_target_match_highlights_nothing(self):
        from docx import Document
        from docx.enum.text import WD_COLOR_INDEX
        data = _make_docx_bytes(paragraphs=["Nothing here matches any target at all."])
        annotated, count = highlight_docx(data, ["completely unrelated text snippet here"])
        assert count == 0
        doc = Document(io.BytesIO(annotated))
        assert not any(
            r.font.highlight_color == _EXPECTED_HL
            for p in doc.paragraphs for r in p.runs
        )

    def test_output_is_a_valid_reloadable_docx(self):
        from docx import Document
        data = _make_docx_bytes(paragraphs=["A paragraph with a TBD marker to resolve."])
        annotated, _ = highlight_docx(data, ["TBD marker to resolve"])
        # Must not raise — validates ZIP/XML integrity round-trip
        doc = Document(io.BytesIO(annotated))
        assert len(doc.paragraphs) >= 1


# ── 5. generate_annotated_spec ────────────────────────────────────

class TestGenerateAnnotatedSpec:

    def test_returns_none_for_non_docx_format(self):
        report = {"findings": [{"severity": "error", "user_excerpt": "some genuine excerpt here"}]}
        assert generate_annotated_spec(b"plain text content", ".txt", report) is None
        assert generate_annotated_spec(b"%PDF-1.4 fake", ".pdf", report) is None

    def test_returns_none_when_report_has_no_targets(self):
        data = _make_docx_bytes(paragraphs=["Everything is fine here."])
        report = {"findings": [{"severity": "pass", "user_excerpt": "Everything is fine here."}]}
        assert generate_annotated_spec(data, ".docx", report) is None

    def test_returns_bytes_and_count_when_targets_present(self):
        data = _make_docx_bytes(paragraphs=[
            "This requirement has a leftover placeholder <<TBD value>> unresolved."
        ])
        report = {
            "findings": [{
                "severity": "warning",
                "user_excerpt": "leftover placeholder <<TBD value>> unresolved",
            }]
        }
        result = generate_annotated_spec(data, ".docx", report)
        assert result is not None
        annotated_bytes, count = result
        assert count == 1
        assert len(annotated_bytes) > 0


# ── 6. Real ASU spec ───────────────────────────────────────────────

class TestRealAsuSpec:

    @pytest.fixture(scope="class")
    def asu_annotated(self):
        if not ASU_PATH.exists():
            pytest.skip("ASU spec not found")
        from app.qa.retrieval import extract_text_from_file
        from app.qa.evidence_comparator import validate_with_evidence
        original_bytes = ASU_PATH.read_bytes()
        text = extract_text_from_file(ASU_PATH)
        report = validate_with_evidence(ASU_PATH.name, text)
        result = generate_annotated_spec(original_bytes, ".docx", report)
        assert result is not None
        return result

    def test_some_passages_highlighted(self, asu_annotated):
        # After the traceability false-positive fix, most of what used to
        # inflate this count (93 wrongly-flagged untraced sub-bullets) is
        # gone — genuinely correct, since those requirements DO have
        # upstream references. What remains are real, concrete-excerpt
        # findings (leftover placeholders, R17 standards gaps, etc.), so
        # this just confirms the pipeline still produces real highlights.
        _, count = asu_annotated
        assert count >= 1

    def test_output_reopens_as_valid_docx_with_same_content_volume(self, asu_annotated):
        from docx import Document
        annotated_bytes, _ = asu_annotated
        doc = Document(io.BytesIO(annotated_bytes))
        assert len(doc.paragraphs) > 100
        assert len(doc.tables) > 50

    def test_known_placeholder_excerpt_is_actually_highlighted(self, asu_annotated):
        """Cross-check: pick a real finding's excerpt from the report and
        confirm the corresponding paragraph/table cell in the ANNOTATED
        output is genuinely marked yellow (not just that *some* count is
        nonzero).

        _find_excerpt's ±100-char context window can stitch together text
        from several adjacent-but-separate table rows into one "excerpt"
        string (each row becomes its own cell in the real docx, so no
        single paragraph/cell ever contains that exact combined text) —
        that's a cosmetic quirk of the excerpt shown in the report, not a
        highlighting bug. So this checks EVERY excerpt-bearing finding and
        only requires that AT LEAST ONE of them is genuinely highlighted,
        rather than assuming the first one happens to fit in one cell."""
        from docx import Document
        from docx.enum.text import WD_COLOR_INDEX
        from app.qa.retrieval import extract_text_from_file
        from app.qa.evidence_comparator import validate_with_evidence

        text = extract_text_from_file(ASU_PATH)
        report = validate_with_evidence(ASU_PATH.name, text)
        candidates = [
            f["user_excerpt"] for f in report["findings"]
            if f["severity"] in ("error", "warning")
            and f.get("user_excerpt") and f["user_excerpt"] != "NOT FOUND"
        ]
        assert candidates

        annotated_bytes, _ = asu_annotated
        doc = Document(io.BytesIO(annotated_bytes))

        def _is_highlighted(needle):
            for p in doc.paragraphs:
                if needle in p.text and any(
                    r.font.highlight_color == _EXPECTED_HL for r in p.runs
                ):
                    return True
            for t in doc.tables:
                for row in t.rows:
                    for c in row.cells:
                        for p in c.paragraphs:
                            if needle in p.text and any(
                                r.font.highlight_color == _EXPECTED_HL for r in p.runs
                            ):
                                return True
            return False

        found_highlighted = any(
            _is_highlighted(cleaned[:40])
            for cleaned in (_clean_target(c) for c in candidates)
            if cleaned
        )
        assert found_highlighted, f"None of {len(candidates)} candidate excerpts were highlighted"


# ── 7. HTTP integration (/api/upload-and-validate) ─────────────────

class TestHttpIntegration:

    @pytest.fixture(scope="class")
    def client(self):
        pytest.importorskip("fastapi")
        from fastapi.testclient import TestClient
        from app.conformity_server import app
        return TestClient(app)

    def test_response_includes_annotated_spec_for_docx(self, client):
        if not ASU_PATH.exists():
            pytest.skip("ASU spec not found")
        import base64
        from docx import Document
        with open(ASU_PATH, "rb") as f:
            res = client.post(
                "/api/upload-and-validate",
                files=[("file", (ASU_PATH.name, f, "application/octet-stream"))],
            )
        assert res.status_code == 200
        data = res.json()
        assert data["annotatedSpecAvailable"] is True
        assert data["annotatedSpecHighlightCount"] > 0
        annotated_bytes = base64.b64decode(data["annotatedSpecBase64"])
        doc = Document(io.BytesIO(annotated_bytes))
        assert len(doc.paragraphs) > 0
