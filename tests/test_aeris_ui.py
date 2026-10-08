"""Presentation contracts for the AERIS interfaces; no service calls."""

from html.parser import HTMLParser
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
UI = ROOT / "app" / "conformity_ui" / "index.html"
QA_UI = ROOT / "app" / "qa_ui" / "index.html"


class Elements(HTMLParser):
    def __init__(self):
        super().__init__()
        self.by_id = {}
        self.navigation = []
        self.filters = []

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        if "id" in attributes:
            assert attributes["id"] not in self.by_id, "Duplicate element ID"
            self.by_id[attributes["id"]] = (tag, attributes)
        if "data-tab" in attributes:
            self.navigation.append(attributes)
        if "data-filter" in attributes or "data-sfilter" in attributes:
            self.filters.append(attributes)


@pytest.fixture
def elements():
    parser = Elements()
    parser.feed(UI.read_text(encoding="utf-8"))
    return parser


@pytest.mark.parametrize(
    ("zone", "input_id", "formats", "multiple"),
    [
        ("upload-zone", "file-input", ".ods,.xlsx,.xlsm", True),
        ("spec-upload-zone", "spec-file-input", ".docx,.pdf,.txt", False),
        ("delta-zone", "delta-input", ".ods,.xlsx,.xlsm", True),
    ],
)
def test_upload_contract(elements, zone, input_id, formats, multiple):
    _, upload = elements.by_id[zone]
    assert upload["role"] == "button"
    assert upload["tabindex"] == "0"
    assert upload["aria-label"]
    assert upload["onkeydown"] == f"activateUpload(event, '{input_id}')"
    tag, file_input = elements.by_id[input_id]
    assert tag == "input"
    assert file_input["type"] == "file"
    assert file_input["accept"] == formats
    assert ("multiple" in file_input) is multiple
    assert file_input["onclick"] == "event.stopPropagation()"


def test_existing_workflows_remain_available(elements):
    assert [item["data-tab"] for item in elements.navigation] == [
        "analyze", "validator", "aeris", "bench", "trace"
    ]
    for item in elements.navigation:
        assert item["aria-controls"] == "tab-" + item["data-tab"]
        assert item["aria-controls"] in elements.by_id
        assert item["onclick"] == f"switchTab('{item['data-tab']}')"
    for button in ("analyze-btn", "excel-btn", "validate-btn", "matrix-btn", "delta-btn"):
        assert "disabled" in elements.by_id[button][1]


def test_live_feedback_is_accessible(elements):
    for element_id in ("loading-analyze", "loading-validate", "loading-delta", "toast"):
        assert elements.by_id[element_id][1]["role"] == "status"
        assert elements.by_id[element_id][1]["aria-live"] == "polite"
    assert elements.by_id["search-box"][1]["aria-label"]


def test_review_tools_retain_deployment_availability_markers(elements):
    for item in elements.navigation:
        assert ("data-local-only" in item) is (
            item["data-tab"] in {"aeris", "bench", "trace"}
        )


def test_filter_controls_expose_selection(elements):
    assert len(elements.filters) == 10
    for button in elements.filters:
        value = button.get("data-filter", button.get("data-sfilter"))
        assert button["aria-pressed"] == ("true" if value == "ALL" else "false")


def test_guidance_and_technical_disclosure(elements):
    tag, attributes = elements.by_id["extraction-details"]
    assert tag == "details"
    assert "open" not in attributes
    for element_id in ("matrix-action-hint", "spec-action-hint", "items-count"):
        assert elements.by_id[element_id][1]["role"] == "status"
        assert elements.by_id[element_id][1]["aria-live"] == "polite"


def test_evidence_linking_is_removed_and_matrix_tdr_is_under_development(elements):
    html = UI.read_text(encoding="utf-8")
    assert not any(item["data-tab"] == "evidence" for item in elements.navigation)
    assert "Evidence Linking" not in html
    assert "Matrix–TDR Review is in development." in html
    assert 'id="tab-evidence" class="hidden" hidden' in html
    assert 'aria-label="Matrix–TDR Review, in development"' in html
    assert '<div hidden aria-hidden="true">' in html


def test_hidden_review_controls_remain_wired_for_future_use(elements):
    for element_id in (
        "aeris-matrix-input", "aeris-evidence-input", "aeris-btn",
        "tdr-scope-inputs", "tdr-saved-cases", "tdr-json-btn",
        "evidence-matrix-input", "evidence-pdf-input", "evidence-pptx-input",
        "evidence-run-btn", "evidence-report-btn",
    ):
        assert element_id in elements.by_id
    assert elements.by_id["aeris-btn"][1]["onclick"] == "doAeris()"
    assert elements.by_id["evidence-run-btn"][1]["onclick"] == "doEvidenceAnalysis()"


def test_restored_review_scripts_and_availability_are_wired():
    html = UI.read_text(encoding="utf-8")
    assert "const scriptPath = IS_AZURE_FUNCTION_UI ? '/api/' : '/';" in html
    assert "loadReviewScript(scriptPath + 'tdr-review-ui.js')" in html
    assert "loadReviewScript(scriptPath + 'tdr-bench-ui.js')" in html
    assert "window.TdrBench.reloadJobs()" in html
    assert "classList.toggle('hidden', IS_AZURE_FUNCTION_UI)" not in html


def test_version_comparison_sends_selected_files_in_one_request():
    html = UI.read_text(encoding="utf-8")
    assert "fd.append('files', entry.file)" in html
    assert "apiUrl('/aeris-conformity-compare')" in html
    assert "X-AERIS-Session" not in html


def test_matrix_review_is_explicit_about_coverage_and_preserves_full_findings():
    html = UI.read_text(encoding="utf-8")
    assert "a.reviewCoverage" in html
    assert "AI semantic analysis + pattern fallback" in html
    assert "All OK engagements are consistent" not in html
    assert "findings.slice(0, 50)" not in html
    assert "${escapeHtml(f.explanation || f.aiComment)}" in html
    assert "${escapeHtml(f.evidenceExcerpt)}" in html
    assert "${escapeHtml(f.nextAction)}" in html
    assert "Verification pending" in html
    assert "Status/comment conflict" in html
    assert "${escapeHtml(item.description" in html
    assert "if (matrixBusy) return;" in html
    assert "if (added) invalidateMatrixResults();" in html
    assert "fileInput.disabled = matrixBusy;" in html


@pytest.mark.parametrize("path", [UI, QA_UI])
def test_aeris_branding_and_responsive_presentation(path):
    html = path.read_text(encoding="utf-8")
    assert "<title>AERIS |" in html
    assert "Automated Engineering Review &amp; Integrity System" in html or (
        "Automated Engineering Review & Integrity System" in html
    )
    assert "prefers-reduced-motion: reduce" in html
    assert "@media (max-width:" in html
    assert "LEON" not in html
