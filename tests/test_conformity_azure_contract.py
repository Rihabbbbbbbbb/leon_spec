"""Validate the production batch response without network or model calls."""
import importlib.util
import json
from io import BytesIO
from pathlib import Path

from openpyxl import Workbook

from app.qa import conformity_analyzer as ca


def test_azure_batch_exposes_review_coverage(tmp_path, monkeypatch):
    import app.config as config
    monkeypatch.setattr(config, "UPLOADS_DIR", tmp_path)
    monkeypatch.setattr(ca, "_analyze_ok_deep_llm", lambda items: ([], set()))
    path = Path(__file__).resolve().parents[1] / "azure_function" / "azure_handler.py"
    spec = importlib.util.spec_from_file_location("conformity_azure_contract_handler", path)
    handler = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(handler)
    monkeypatch.setattr(handler, "_upload_to_blob_storage", lambda **kwargs: "")
    workbook = Workbook()
    ws = workbook.active
    ws.append(["Requirement ID", "Description", "Supplier conformity", "Supplier comments"])
    ws.append(["REQ-00001", "Verify operation at 85 C", "OK", "Thermal simulation is needed"])
    content = BytesIO()
    workbook.save(content)
    response = handler.handle_conformity_batch([("synthetic.xlsx", content.getvalue())])
    assert response.status_code == 200
    data = json.loads(response.get_body())
    coverage = data["files"][0]["reviewCoverage"]
    assert coverage["eligibleItems"] == 1
    assert coverage["aiReviewedItems"] == 0
    assert coverage["patternReviewedItems"] == 1
    assert coverage["limitations"]
