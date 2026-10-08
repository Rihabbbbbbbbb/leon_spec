"""A benchmark started on one worker must be readable from another.

Azure Functions answers the status poll, the result, and a page citation from
whichever instance is free. Those calls used to 404 with "Unknown job" because
the files lived only in that instance's /tmp.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from app.qa.spec_to_matrix import (
    BUNDLED_TEMPLATE_PATH,
    Requirement,
    generate_conformity_matrix,
    resolve_template_path,
)
from app.qa.tdr_bench_engine import BenchJobManager, BenchOptions, InputFile
from app.qa.tdr_bench_sync import DirectoryJobSync


def test_bundled_template_is_a_workbook():
    assert BUNDLED_TEMPLATE_PATH.is_file()
    assert resolve_template_path() == resolve_template_path()
    assert resolve_template_path().is_file()


def test_matrix_generation_uses_bundled_template_when_refs_are_missing(tmp_path, monkeypatch):
    import app.config as cfg
    import app.qa.spec_to_matrix as mod

    missing = tmp_path / "data" / "refs"
    monkeypatch.setattr(cfg, "REFS_DIR", missing)
    monkeypatch.setattr(mod, "TEMPLATE_PATH", missing / "Conformity_Matrix_Template.xlsx")
    monkeypatch.delenv("AzureWebJobsScriptRoot", raising=False)

    found = mod.resolve_template_path()
    assert found == BUNDLED_TEMPLATE_PATH
    xlsx = generate_conformity_matrix([
        Requirement(req_id="REF-ASU-CD-CONN-0002", text="The connector shall withstand 50 N of mating force.", line_no=3),
    ])
    assert xlsx[:2] == b"PK"


def test_explicit_missing_template_still_fails(tmp_path):
    with pytest.raises(FileNotFoundError, match="Conformity matrix template not found"):
        generate_conformity_matrix([], template_path=tmp_path / "absent.xlsx")


def test_other_instance_opens_result_and_evidence_page(tmp_path: Path):
    sync = DirectoryJobSync(tmp_path / "shared")
    runner = BenchJobManager(tmp_path / "instance-a", sync=sync)
    reader = BenchJobManager(tmp_path / "instance-b", sync=sync)

    job_id, job_dir = runner.new_job_dir()
    offer = job_dir / "inputs" / "D1__Alpha_TDR.txt"
    offer.write_text(
        "Supplier Alpha technical offer\nLuminance: 800 cd/m2\nContrast ratio 1000:1\n",
        encoding="utf-8",
    )
    runner.submit(
        job_id,
        [InputFile("D1", "Alpha_TDR.txt", offer, "Alpha")],
        BenchOptions(language="en", vision="off", use_llm=False),
        "shared probe",
    )
    state = runner.wait(job_id, timeout=60)
    assert state["status"] == "completed", state.get("error") or state.get("trace")

    assert not (tmp_path / "instance-b" / job_id).exists()
    assert reader.public_state(job_id)["status"] == "completed"
    assert reader.public_state(job_id)["hasResult"] is True
    result = reader.result(job_id)
    assert result["jobId"] == job_id
    assert result["stats"]["documents"] == 1

    page = reader.page_text(job_id, "D1", 1)
    assert "800 cd/m2" in page["text"]
    assert reader.input_path(job_id, "D1").read_bytes().startswith(b"Supplier Alpha")
    assert any(job["id"] == job_id for job in reader.list_jobs())

    reader.delete(job_id)
    with pytest.raises(KeyError):
        reader.public_state(job_id)
    fresh = BenchJobManager(tmp_path / "instance-c", sync=sync)
    with pytest.raises(KeyError):
        fresh.public_state(job_id)
