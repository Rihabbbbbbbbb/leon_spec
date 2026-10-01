"""
Tests for legacy .doc file rejection across upload entry points.

Legacy .doc (Word 97-2003 binary format) is deliberately rejected rather
than accepted with degraded extraction: python-docx (used for .docx) only
parses the modern zip/XML package, and no pure-Python .doc parser
preserves table structure — which this validator's analysis depends on
heavily (requirement tables, I/O tables). A clear, actionable message is
returned instead of a raw exception or a generic "unsupported type" note.
"""
from fastapi.testclient import TestClient

from app.qa.retrieval import ACCEPTED_UPLOAD_EXTENSIONS, unsupported_file_type_message
from app.qa_server import app as qa_app


client = TestClient(qa_app)


class TestUnsupportedFileTypeMessage:
    def test_doc_gets_specific_actionable_guidance(self):
        msg = unsupported_file_type_message(".doc")
        assert ".doc" in msg
        assert ".docx" in msg
        assert "Save As" in msg or "Enregistrer" in msg

    def test_other_unsupported_types_get_generic_message(self):
        msg = unsupported_file_type_message(".xyz")
        assert "not accepted" in msg
        assert ".txt, .docx, or .pdf" in msg

    def test_accepted_extensions_are_exactly_txt_docx_pdf(self):
        assert ACCEPTED_UPLOAD_EXTENSIONS == {".txt", ".docx", ".pdf"}
        assert ".doc" not in ACCEPTED_UPLOAD_EXTENSIONS


class TestUploadEndpointRejectsDoc:
    def test_upload_endpoint_rejects_doc_with_actionable_message(self):
        response = client.post(
            "/api/upload",
            files={"file": ("spec.doc", b"\xD0\xCF\x11\xE0\xA1\xB1\x1A\xE1fake ole content", "application/msword")},
        )
        assert response.status_code == 400
        detail = response.json()["detail"]
        assert ".doc" in detail
        assert ".docx" in detail

    def test_upload_endpoint_still_accepts_txt(self):
        response = client.post(
            "/api/upload",
            files={"file": ("spec.txt", b"SCOPE\nSome real content here.\n", "text/plain")},
        )
        assert response.status_code == 200
