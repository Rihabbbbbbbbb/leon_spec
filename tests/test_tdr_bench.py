"""Tests of the multi-supplier TDR technical benchmark (engine, reports, API)."""
from __future__ import annotations

import io
import json
import re
import time
from pathlib import Path
from types import SimpleNamespace

import fitz
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.qa import tdr_bench_route
from app.qa.tdr_bench_engine import (BenchJobManager, BenchmarkRun, BenchOptions, InputFile, answer_question,
                                     apply_overrides, group_suppliers, sanitize_overrides)
from app.qa.tdr_bench_llm import LLM, verify_quote
from app.qa.tdr_bench_report import build_docx, build_excel

SUPPLIER_PAGES = {
    "Alpha": [
        ["Technical Design Review - Alpha Displays", "Display module 12.3 inch TFT LCD with local dimming",
         "Luminance 1000 cd/m2 typical at center", "Contrast ratio 1500:1 with optical bonding",
         "Resolution 1920 x 720 pixels"],
        ["Hardware architecture based on SerDes FPD-Link III", "Thermal simulation shows max 85 C at backlight",
         "Software bootloader supports secure flashing", "Open point: EMC filter TBD pending customer harness",
         "Functional safety ASIL B for warning telltales"],
    ],
    "Beta": [
        ["Beta Electronics technical proposal TDR", "Display module 12.3 inch TFT LCD edge lit backlight",
         "Luminance 800 cd/m2 typical at center", "Contrast ratio 1200:1 air gap design",
         "Resolution 1920 x 720 pixels"],
        ["Hardware architecture based on GMSL2 SerDes", "Deviation: cannot meet 85 C operating with full brightness",
         "Validation plan DV PV according to customer standard", "Industrialization in Plant Monterrey with SMT line",
         "Cybersecurity secure boot and key storage in HSM"],
    ],
}

DOMAIN_HINTS = [
    ("display_optical", ("luminance", "contrast", "resolution", "display", "lcd")),
    ("hardware", ("hardware", "serdes",)),
    ("thermal", ("thermal", " c ")),
    ("software", ("software", "bootloader")),
    ("functional_safety", ("asil", "safety")),
    ("cybersecurity", ("cyber", "hsm")),
    ("validation", ("validation",)),
    ("industrialization", ("plant", "smt", "industrial")),
]


def _make_pdf(path: Path, pages: list[list[str]]) -> Path:
    doc = fitz.open()
    for lines in pages:
        page = doc.new_page()
        y = 72
        for line in lines:
            page.insert_text((72, y), line, fontsize=11)
            y += 22
    doc.save(path)
    doc.close()
    return path


# ── Fake Azure OpenAI client returning schema-valid payloads ───────────

class FakeCompletions:
    def __init__(self):
        self.calls: list[str] = []

    def create(self, **kwargs):
        fmt = kwargs.get("response_format") or {}
        name = (fmt.get("json_schema") or {}).get("name", "vision")
        self.calls.append(name)
        user = kwargs["messages"][-1]["content"]
        if isinstance(user, list):
            content = "Vision transcript"
        else:
            content = json.dumps(getattr(self, f"_{name}")(user))
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content),
                                                        finish_reason="stop")],
                               usage=SimpleNamespace(prompt_tokens=10, completion_tokens=5))

    @staticmethod
    def _tdr_facts(user: str) -> dict:
        facts = []
        for number, body in re.findall(r"\[PAGE (\d+)\]\n(.*?)(?=\n\[PAGE |\n\n\(Reminder|\Z)", user, re.S):
            for line in [l.strip() for l in body.splitlines() if len(l.strip()) > 12]:
                low = f" {line.lower()} "
                domain = next((d for d, keys in DOMAIN_HINTS if any(k in low for k in keys)), "architecture")
                parameter, value = "none", ""
                m = re.search(r"luminance (\d+) cd/m2", low)
                if m:
                    parameter, value = "luminance", f"{m.group(1)} cd/m²"
                m = re.search(r"contrast ratio (\d+):1", low)
                if m:
                    parameter, value = "contrast_ratio", f"{m.group(1)}:1"
                kind = "deviation" if "deviation" in low else "open_point" if "tbd" in low else "specification"
                facts.append({"page": number, "domain": domain, "kind": kind, "topic": line[:30],
                              "parameter": parameter, "value": value, "variant": "",
                              "statement": line, "quote": line, "importance": "high"})
        facts.append({"page": "1", "domain": "mechanical", "kind": "specification", "topic": "hallucination",
                      "parameter": "none", "value": "", "variant": "", "statement": "Invented magnesium housing",
                      "quote": "the housing is made of die cast magnesium alloy AZ91", "importance": "low"})
        return {"facts": facts}

    @staticmethod
    def _sids(user: str) -> list[str]:
        return list(dict.fromkeys(re.findall(r"\bS\d+\b", user)))

    def _tdr_domain(self, user: str) -> dict:
        sids = self._sids(user)
        fact_ids = re.findall(r"\b(S\d+-F\d{4})\b", user)
        by_sid = {s: [f for f in fact_ids if f.startswith(s + "-")] for s in sids}
        return {
            "summary": "Alpha offers the stronger optical stack.",
            "key_differentiators": ["Optical bonding vs air gap"],
            "comparison_points": [{"aspect": "Optical stack", "best_supplier_ids": sids[:1],
                                   "positions": [{"supplier_id": s, "position": f"{s} concrete position",
                                                  "fact_ids": by_sid[s][:2]} for s in sids]}],
            "assessments": [{"supplier_id": s, "score": 4 - i, "coverage": "partial", "summary": "ok",
                             "strengths": [{"text": "documented", "fact_ids": by_sid[s][:1]}],
                             "weaknesses": [{"text": "gaps", "fact_ids": []}],
                             "risks": [{"text": "thermal", "severity": "medium", "fact_ids": by_sid[s][:1]}]}
                            for i, s in enumerate(sids)],
            "ranking": sids,
        }

    @staticmethod
    def _tdr_profile(user: str) -> dict:
        ids = re.findall(r"\b(S\d+-F\d{4})\b", user)[:2]
        return {"overview": "Profile overview", "technical_positioning": "Premium",
                "top_strengths": [{"text": "Bonding", "domain": "display_optical", "fact_ids": ids}],
                "top_weaknesses": [{"text": "Thermal", "domain": "thermal", "fact_ids": []}],
                "top_risks": [{"text": "EMC", "domain": "hardware", "severity": "high", "fact_ids": ids}],
                "declared_deviations": [], "assumptions_and_dependencies": [],
                "open_points": [{"text": "EMC filter", "fact_ids": ids[:1]}],
                "clarification_questions": [{"question": "Confirm EMC filter?", "domain": "hardware",
                                             "priority": "high", "rationale": "TBD"}]}

    def _tdr_synthesis(self, user: str) -> dict:
        sids = self._sids(user)
        return {"executive_summary": "Alpha leads.",
                "recommendation": {"preferred_supplier_id": sids[0], "runner_up_supplier_ids": sids[1:],
                                   "rationale": "Better optics", "conditions": ["Close EMC point"]},
                "supplier_verdicts": [{"supplier_id": s, "verdict": "strong_candidate", "headline": "h"}
                                      for s in sids],
                "cross_cutting_findings": ["All use 1920x720"],
                "major_risks": [{"supplier_id": sids[-1], "text": "Thermal derating", "severity": "high"}],
                "next_steps": ["Clarification meeting"], "confidence_note": "Synthetic data"}

    def _tdr_ask(self, user: str) -> dict:
        refs = re.findall(r"\[(D\d+) p\.(\d+)\]", user)
        sids = list(dict.fromkeys(re.findall(r"### Supplier (S\d+)", user)))
        return {"answer": "Luminance differs.", "per_supplier": [
            {"supplier_id": s, "found": True, "answer": "see page",
             "citations": [{"doc_id": d, "page": int(p)} for d, p in refs[:1]] + [{"doc_id": "D99", "page": 1}]}
            for s in sids]}


def fake_llm_factory(fake: FakeCompletions):
    client = SimpleNamespace(chat=SimpleNamespace(completions=fake))
    return lambda cache: LLM(None, client=client, deployment="fake-gpt")


@pytest.fixture()
def pdfs(tmp_path):
    out = []
    for i, (name, pages) in enumerate(SUPPLIER_PAGES.items(), start=1):
        out.append(_make_pdf(tmp_path / f"TDR_{name}_DM12.pdf", pages))
    return out


def _inputs(job_dir: Path, pdfs: list[Path]) -> list[InputFile]:
    files = []
    for i, src in enumerate(pdfs, start=1):
        target = job_dir / "inputs" / f"D{i}__{src.name}"
        target.write_bytes(src.read_bytes())
        files.append(InputFile(f"D{i}", src.name, target, list(SUPPLIER_PAGES)[i - 1]))
    return files


def _run(manager: BenchJobManager, pdfs, **opts) -> tuple[str, dict]:
    job_id, job_dir = manager.new_job_dir()
    manager.submit(job_id, _inputs(job_dir, pdfs), BenchOptions(**opts), "Test bench")
    state = manager.wait(job_id, timeout=120)
    assert state["status"] == "completed", state.get("error") or state.get("trace")
    return job_id, manager.result(job_id)


# ── Unit tests ─────────────────────────────────────────────────────────

def test_verify_quote_levels():
    page = "Luminance 1000 cd/m2 typical at center of the active area"
    assert verify_quote("Luminance 1000 cd/m2 typical at center", page) == "verified"
    assert verify_quote("die cast magnesium housing with ribs", page) == "unverified"


def test_group_suppliers_merges_case_and_detects_blank(tmp_path):
    files = [InputFile("D1", "a.pdf", tmp_path / "a.pdf", "Alpha"),
             InputFile("D2", "b.pdf", tmp_path / "b.pdf", "ALPHA "),
             InputFile("D3", "TDR_Beta_offer.pdf", tmp_path / "c.pdf", "")]
    suppliers, mapping = group_suppliers(files)
    assert mapping["D1"] == mapping["D2"] == "S1"
    assert mapping["D3"] == "S2" and len(suppliers) == 2
    assert suppliers[1].name


def test_deterministic_pipeline(tmp_path, pdfs):
    manager = BenchJobManager(tmp_path / "bench", llm_factory=lambda cache: None)
    job_id, result = _run(manager, pdfs, use_llm=False)
    assert result["mode"] == "deterministic"
    assert [s["name"] for s in result["suppliers"]] == ["Alpha", "Beta"]
    assert result["facts"] and all(f["grounding"] != "unverified" for f in result["facts"])
    assert sorted(s["rank"] for s in result["suppliers"]) == [1, 2]
    assert "luminance" in result["parameters"]
    assert build_excel(result)[:2] == b"PK" and build_docx(result)[:2] == b"PK"


def test_llm_pipeline_grounding_and_synthesis(tmp_path, pdfs):
    fake = FakeCompletions()
    manager = BenchJobManager(tmp_path / "bench", llm_factory=fake_llm_factory(fake))
    job_id, result = _run(manager, pdfs, use_llm=True, vision="off", language="en")
    assert result["mode"] == "llm"
    assert {"tdr_facts", "tdr_domain", "tdr_profile", "tdr_synthesis"} <= set(fake.calls)
    facts = result["facts"]
    assert not any("magnesium" in f["statement"] for f in facts if f["grounding"] == "verified")
    assert any(f["grounding"] == "unverified" for f in facts)
    lum = result["parameters"]["luminance"]
    assert {v["value"] for vals in lum.values() for v in vals} >= {"1000 cd/m²", "800 cd/m²"}
    dr = result["domainResults"]["display_optical"]
    assert dr["source"] == "llm" and dr["points"]
    known = {f["id"] for f in facts}
    for point in dr["points"]:
        for pos in point["positions"]:
            assert set(pos["fact_ids"]) <= known
    assert result["synthesis"]["recommendation"]["preferred"] in {"S1", "S2"}
    assert result["stats"]["llmCalls"] > 0
    assert result["profiles"]["S1"]["questions"]
    data = answer_question(manager.job_dir(job_id), result, "What is the luminance?",
                           fake_llm_factory(fake)(None))
    assert data["mode"] == "llm"
    assert all(c["docId"] != "D99" for p in data["perSupplier"] for c in p["citations"])
    with pytest.raises(ValueError):
        answer_question(manager.job_dir(job_id), result, "  ", None)


def test_gap_pass_targets_thin_domains(tmp_path):
    quality_pages = [["Quality process ASPICE level 2 with APQP and PPAP", "IATF 16949 certified plant audit",
                      "Lessons learned process and KPI tracking"] for _ in range(3)]
    src = _make_pdf(tmp_path / "TDR_Gamma.pdf", quality_pages)
    job_dir = tmp_path / "job"
    (job_dir / "inputs").mkdir(parents=True)
    files = [InputFile("D1", src.name, src, "Gamma")]
    run = BenchmarkRun(job_dir, files, BenchOptions(use_llm=False), None)
    run.ingest()
    sup, doc = run.suppliers[0], run.docs["D1"]
    gap = run._gap_tasks([], [])
    quality = [t for t in gap if t[3] == "quality_process"]
    assert len(quality) == 1 and quality[0][2]["pages"] == [1, 2, 3]
    assert "[PAGE 1]" in quality[0][2]["text"]
    covered = [{"facts": [{"domain": "quality_process", "grounding": "verified"}] * 2}]
    assert not [t for t in run._gap_tasks([(sup, doc, {})], covered) if t[3] == "quality_process"]


def test_pdf_access_is_thread_safe(pdfs):
    from concurrent.futures import ThreadPoolExecutor

    from app.qa.tdr_bench_ingest import PDF_LOCK, load_pdf, render_page_png
    content = pdfs[0].read_bytes()

    def work(i):
        if i % 2:
            return len(load_pdf(content, "D1", "a.pdf").pages)
        return len(render_page_png(content, 1, dpi=40))

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(work, range(64)))
    assert all(r > 0 for r in results)
    assert PDF_LOCK.acquire(blocking=False)  # never left held
    PDF_LOCK.release()


def test_context_numbers_normalize():
    from app.qa.tdr_bench_engine import _numbers
    assert _numbers("DM12 12,3in 12.0\" 1.5 x 7") == ["12", "12.3", "12", "1.5"]


def test_unit_guard_rejects_wrong_row_values():
    from app.qa.tdr_bench_taxonomy import unit_mismatch
    assert unit_mismatch("display_size", "265,7 x 149,4 mm")
    assert not unit_mismatch("display_size", "12.3\"")
    assert not unit_mismatch("display_size", "12in AA: 265.536mm*149.364mm")
    assert not unit_mismatch("luminance", "1000 cd/m² at 25°C")
    assert unit_mismatch("luminance", "35 ms")
    assert not unit_mismatch("panel_technology", "BOE a-Si 12 mm")


def test_overrides_sanitize_and_apply(tmp_path, pdfs):
    manager = BenchJobManager(tmp_path / "bench", llm_factory=lambda cache: None)
    job_id, result = _run(manager, pdfs, use_llm=False)
    raw = manager.result(job_id, raw=True)
    clean = sanitize_overrides({"weights": {"display_optical": 99, "bogus": 3},
                                "scores": {"display_optical": {"S1": 9, "S2": -4, "S9": 3}},
                                "comments": {"general": "x" * 5000}}, raw)
    assert clean["weights"] == {"display_optical": 5}
    assert clean["scores"] == {"display_optical": {"S1": 5, "S2": 0}}
    applied = apply_overrides(raw, clean)
    assert applied["overridesApplied"] and applied["scoreMatrix"]["display_optical"]["S1"] == 5
    assert "aiScoreMatrix" in applied
    updated = manager.set_overrides(job_id, {"scores": {"display_optical": {"S2": 5}}})
    assert updated["overridesApplied"]
    assert build_excel(updated)[:2] == b"PK" and build_docx(updated)[:2] == b"PK"
    reset = manager.set_overrides(job_id, {})
    assert not reset.get("overridesApplied")


# ── API tests ──────────────────────────────────────────────────────────

@pytest.fixture()
def api(tmp_path, monkeypatch):
    fake = FakeCompletions()
    monkeypatch.delenv("TDR_REVIEW_API_KEY", raising=False)
    monkeypatch.setattr(tdr_bench_route, "_manager",
                        BenchJobManager(tmp_path / "bench", llm_factory=fake_llm_factory(fake)))
    app = FastAPI()
    app.include_router(tdr_bench_route.router)
    return TestClient(app)


def _wait_api(client: TestClient, job_id: str) -> dict:
    deadline = time.time() + 120
    while time.time() < deadline:
        state = client.get(f"/api/tdr-bench/jobs/{job_id}").json()
        if state["status"] in {"completed", "failed", "cancelled"}:
            return state
        time.sleep(0.2)
    raise AssertionError("job did not finish")


def test_api_full_flow(api, pdfs):
    cfg = api.get("/api/tdr-bench/config").json()
    assert cfg["llmAvailable"] and len(cfg["domains"]) == 14
    det = api.post("/api/tdr-bench/detect-supplier", json={"fileNames": [p.name for p in pdfs]}).json()
    assert set(det["suppliers"]) == {p.name for p in pdfs}

    files = [("files", (p.name, p.read_bytes(), "application/pdf")) for p in pdfs]
    resp = api.post("/api/tdr-bench/jobs", files=files,
                    data={"suppliers": json.dumps(["Alpha", "Beta"]), "language": "fr", "vision": "off",
                          "weights": json.dumps({"display_optical": 3}), "title": "API test"})
    assert resp.status_code == 200, resp.text
    job_id = resp.json()["id"]
    assert api.get(f"/api/tdr-bench/jobs/{job_id}/result").status_code in {200, 409}
    state = _wait_api(api, job_id)
    assert state["status"] == "completed", state.get("error")
    assert any(j["id"] == job_id for j in api.get("/api/tdr-bench/jobs").json()["jobs"])

    result = api.get(f"/api/tdr-bench/jobs/{job_id}/result").json()
    assert result["title"] == "API test" and len(result["suppliers"]) == 2
    assert result["options"]["weights"]["display_optical"] == 3

    for fmt, magic in (("xlsx", b"PK"), ("docx", b"PK"), ("json", b"{")):
        r = api.get(f"/api/tdr-bench/jobs/{job_id}/export", params={"format": fmt})
        assert r.status_code == 200 and r.content[:1] == magic[:1]
        assert "attachment" in r.headers["content-disposition"]
    assert api.get(f"/api/tdr-bench/jobs/{job_id}/export", params={"format": "pdf"}).status_code == 400

    png = api.get(f"/api/tdr-bench/jobs/{job_id}/docs/D1/pages/1.png")
    assert png.status_code == 200 and png.content[:4] == b"\x89PNG"
    assert api.get(f"/api/tdr-bench/jobs/{job_id}/docs/D1/pages/99.png").status_code == 404
    assert api.get(f"/api/tdr-bench/jobs/{job_id}/docs/X1/pages/1.png").status_code == 400
    text = api.get(f"/api/tdr-bench/jobs/{job_id}/docs/D1/pages/1")
    assert text.status_code == 200 and "Luminance" in json.dumps(text.json())

    ask = api.post(f"/api/tdr-bench/jobs/{job_id}/ask", json={"question": "Luminance ?"})
    assert ask.status_code == 200 and ask.json()["perSupplier"]
    assert api.post(f"/api/tdr-bench/jobs/{job_id}/ask", json={"question": ""}).status_code == 400

    ov = api.put(f"/api/tdr-bench/jobs/{job_id}/overrides",
                 json={"scores": {"display_optical": {"S2": 5}}, "comments": {"general": "expert"}})
    assert ov.status_code == 200 and ov.json()["overridesApplied"]
    assert api.get(f"/api/tdr-bench/jobs/{job_id}/result").json()["overridesApplied"]

    assert api.post(f"/api/tdr-bench/jobs/{job_id}/cancel").json()["status"] == "completed"
    assert api.delete(f"/api/tdr-bench/jobs/{job_id}").status_code == 200
    assert api.get(f"/api/tdr-bench/jobs/{job_id}").status_code == 404


def test_api_rejects_bad_inputs(api, pdfs, monkeypatch):
    assert api.get("/api/tdr-bench/jobs/../etc").status_code == 404
    assert api.get("/api/tdr-bench/jobs/zzzzzzzzzzzz").status_code == 404
    bad = api.post("/api/tdr-bench/jobs", files=[("files", ("evil.exe", b"MZ", "application/octet-stream"))])
    assert bad.status_code == 400
    empty = api.post("/api/tdr-bench/jobs", files=[("files", ("a.pdf", b"", "application/pdf"))])
    assert empty.status_code == 400
    badjson = api.post("/api/tdr-bench/jobs", files=[("files", (pdfs[0].name, pdfs[0].read_bytes(), "application/pdf"))],
                       data={"weights": "{oops"})
    assert badjson.status_code == 400
    assert not [p for p in tdr_bench_route._manager.base_dir.iterdir() if p.name != "_cache"]
    monkeypatch.setenv("TDR_REVIEW_API_KEY", "secret")
    assert api.get("/api/tdr-bench/config").status_code == 401
    assert api.get("/api/tdr-bench/config", headers={"x-tdr-review-key": "secret"}).status_code == 200
