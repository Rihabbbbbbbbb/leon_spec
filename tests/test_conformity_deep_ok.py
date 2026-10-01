"""
Tests for the Deep-OK analysis requirement-awareness (app/qa/conformity_analyzer.py).

Covers:
  1. Pattern-based mismatch detection — an OK comment that answers something
     OTHER than the requirement (low vocabulary overlap + pending/discussion
     signal) is flagged as "hors_sujet".
  2. No false positives — legitimate domain confirmations ("EMC OK",
     "DQ: ok,20260413") and confirming comments are never flagged.
  3. The LLM prompt now includes the requirement description so the model
     can judge whether the comment addresses the requirement.
  4. The HORS_SUJET verdict maps to a warning finding.
"""
import sys
from pathlib import Path

import pytest

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

import app.qa.conformity_analyzer as ca
from app.qa.conformity_analyzer import (
    ConformityItem,
    _detect_requirement_mismatch,
    _pattern_finding_for_item,
    _token_overlap,
)


def _item(req_id, desc, comment, ref="", conf="/"):
    return ConformityItem(
        row_index=0, req_id=req_id, reference=ref,
        description=desc, conformity_raw=conf, conformity_category="OK",
        comment=comment,
    )


class TestTokenOverlap:
    def test_overlap_detects_shared_vocabulary(self):
        assert _token_overlap(
            "The display shall have a luminance of at least 500 cd/m2.",
            "Luminance measured at 520 cd/m2.",
        ) > 0.0

    def test_overlap_zero_for_unrelated(self):
        assert _token_overlap(
            "No parting line on the style surfaces.",
            "detailed position will discuss with STLA",
        ) == 0.0


class TestRequirementMismatch:
    def test_off_topic_pending_comment_is_flagged(self):
        """Requirement about parting lines, comment about discussing a
        position with STLA — the comment answers something else."""
        item = _item(
            "REQ-0307989",
            "No parting line, injection point or gate or ejectors traces "
            "must be on the style surfaces and on contact zones.",
            "20260410 ME: detailed position will discuss with STLA",
        )
        m = _detect_requirement_mismatch(item)
        assert m is not None
        assert m[0] == "hors_sujet"

    def test_emc_domain_confirmation_not_flagged(self):
        """'EMC 2026/03/18 OK' has zero overlap with the EMC test
        requirement but is a legitimate domain confirmation."""
        item = _item(
            "REQ-0945110",
            "C_TI_05_V: Immunity to transients on signal lines - CCC method. "
            "Perform the test according to document [DA_ELEC].",
            "EMC 2026/03/18 OK",
        )
        assert _detect_requirement_mismatch(item) is None

    def test_dq_domain_confirmation_not_flagged(self):
        item = _item(
            "REQ-0307825",
            "The following are considered appearance defects: Level 0: a "
            "point with a dimension between 0.05 mm and 0.2 mm",
            "DQ: ok,2026/4/13",
        )
        assert _detect_requirement_mismatch(item) is None

    def test_confirming_comment_not_flagged(self):
        """A comment that confirms the requirement (shared vocabulary) is
        never flagged."""
        item = _item(
            "REQ-1",
            "The display shall have a luminance of at least 500 cd/m2.",
            "Luminance measured at 520 cd/m2, meets the 500 cd/m2 target.",
        )
        assert _detect_requirement_mismatch(item) is None

    def test_short_comment_never_flagged(self):
        """Short confirmations cannot be off-topic."""
        item = _item(
            "REQ-2",
            "The device shall withstand 10 assembly/disassembly cycles.",
            "will follow connector spec.",
        )
        assert _detect_requirement_mismatch(item) is None

    def test_pattern_finding_includes_hors_sujet(self):
        item = _item(
            "REQ-0307989",
            "No parting line, injection point or gate or ejectors traces "
            "must be on the style surfaces and on contact zones.",
            "20260410 ME: detailed position will discuss with STLA",
        )
        f = _pattern_finding_for_item(item)
        assert f is not None
        assert "hors_sujet" in f["signals"]
        assert f["severity"] in ("error", "warning")
        assert "does not address this requirement" in f["aiComment"]


class TestNewSuspicionPatterns:
    """The pattern engine must detect OK comments that the LLM catches but
    the old regex library missed — 'no guarantee', 'will base/follow on',
    'is needed' — so the deep-OK analysis works even without the LLM."""

    def test_no_guarantee_is_flagged(self):
        item = _item(
            "REQ-0538428",
            "The chromatic coordinates of the color of the active area "
            "must follow CIE 1976.",
            "OPT : 0331\nGlass is okay\nASF no guarantee",
        )
        f = _pattern_finding_for_item(item)
        assert f is not None
        assert "no_guarantee" in f["signals"]

    def test_will_base_on_is_flagged_as_pending_not_hors_sujet(self):
        """'will base on connector spec' is a pending signal about the SAME
        subject — it must be flagged as will_depend, NOT as hors_sujet."""
        item = _item(
            "REQ-0307874",
            "The device shall withstand 10 assembly/disassembly cycles.",
            "20260410 ME: will base on connector spec.",
        )
        f = _pattern_finding_for_item(item)
        assert f is not None
        assert "will_depend" in f["signals"]
        assert "hors_sujet" not in f["signals"]

    def test_will_follow_is_flagged_as_pending_not_hors_sujet(self):
        item = _item(
            "REQ-0308004",
            "No deformation or deterioration is allowed on the connectors "
            "after 10 assemblies/disassemblies.",
            "20260410 ME: will follow connector spec.",
        )
        f = _pattern_finding_for_item(item)
        assert f is not None
        assert "will_depend" in f["signals"]
        assert "hors_sujet" not in f["signals"]

    def test_is_needed_is_flagged(self):
        item = _item(
            "REQ-0994059",
            "The device shall meet the thermal requirements.",
            "20260410 ME: thermal simulation is needed",
        )
        f = _pattern_finding_for_item(item)
        assert f is not None
        assert "needed" in f["signals"]

    def test_confirming_comment_not_flagged_by_new_patterns(self):
        """A genuine confirmation must not be flagged by the new patterns."""
        item = _item(
            "REQ-1",
            "The display shall have a luminance of at least 500 cd/m2.",
            "Luminance measured at 520 cd/m2, meets the 500 cd/m2 target.",
        )
        f = _pattern_finding_for_item(item)
        assert f is None


class TestLLMPromptIncludesRequirement:
    def test_prompt_sends_description(self):
        """The LLM prompt must include the requirement description so the
        model can judge whether the comment addresses the requirement."""
        captured = {}

        class FakeClient:
            class chat:
                class completions:
                    @staticmethod
                    def create(**kwargs):
                        captured["messages"] = kwargs.get("messages", [])
                        payload = {
                            "resultats": [
                                {"id": 0, "verdict": "COHERENT", "gravite": "none",
                                 "explication": "", "citation": ""},
                            ]
                        }
                        class R:
                            choices = [type("C", (), {"message": type(
                                "M", (), {"content": __import__("json").dumps(payload)})()})()]
                        return R()

        # Patch config so the LLM path is taken
        import app.qa.conformity_analyzer as mod
        # The module reads config lazily inside the function; monkeypatch the
        # config module values instead.
        import app.config as cfg
        old_cfg = (cfg.AZURE_OPENAI_API_KEY, cfg.AZURE_OPENAI_ENDPOINT, cfg.AZURE_OPENAI_LLM_DEPLOYMENT)
        cfg.AZURE_OPENAI_API_KEY = "test-key"
        cfg.AZURE_OPENAI_ENDPOINT = "https://test"
        cfg.AZURE_OPENAI_LLM_DEPLOYMENT = "test-deploy"

        import app.embeddings as emb
        old_get_client = emb._get_client
        emb._get_client = lambda: FakeClient()

        try:
            items = [
                _item("REQ-1", "The display shall have a luminance of at least 500 cd/m2.",
                      "Luminance measured at 520 cd/m2."),
            ]
            findings, analyzed = mod._analyze_ok_deep_llm(items)
            assert analyzed == {0}
            user_msg = captured["messages"][1]["content"]
            assert "Requirement: The display shall have a luminance" in user_msg
            assert "Comment: Luminance measured at 520 cd/m2." in user_msg
        finally:
            cfg.AZURE_OPENAI_API_KEY, cfg.AZURE_OPENAI_ENDPOINT, cfg.AZURE_OPENAI_LLM_DEPLOYMENT = old_cfg
            emb._get_client = old_get_client

    def test_hors_sujet_verdict_maps_to_warning(self):
        """A HORS_SUJET verdict from the LLM must produce a warning finding."""
        import json

        class FakeClient:
            class chat:
                class completions:
                    @staticmethod
                    def create(**kwargs):
                        payload = {
                            "resultats": [
                                {"id": 0, "verdict": "HORS_SUJET", "gravite": "warning",
                                 "explication": "The comment discusses a meeting, not the requirement.",
                                 "citation": "discuss with STLA"},
                            ]
                        }
                        class R:
                            choices = [type("C", (), {"message": type(
                                "M", (), {"content": json.dumps(payload)})()})()]
                        return R()

        import app.config as cfg
        old_cfg = (cfg.AZURE_OPENAI_API_KEY, cfg.AZURE_OPENAI_ENDPOINT, cfg.AZURE_OPENAI_LLM_DEPLOYMENT)
        cfg.AZURE_OPENAI_API_KEY = "test-key"
        cfg.AZURE_OPENAI_ENDPOINT = "https://test"
        cfg.AZURE_OPENAI_LLM_DEPLOYMENT = "test-deploy"

        import app.embeddings as emb
        old_get_client = emb._get_client
        emb._get_client = lambda: FakeClient()

        try:
            items = [
                _item("REQ-1", "No parting line on the style surfaces.",
                      "20260410 ME: detailed position will discuss with STLA"),
            ]
            findings, analyzed = ca._analyze_ok_deep_llm(items)
            assert analyzed == {0}
            assert len(findings) == 1
            assert findings[0]["severity"] == "warning"
            assert "hors_sujet" in findings[0]["signals"]
        finally:
            cfg.AZURE_OPENAI_API_KEY, cfg.AZURE_OPENAI_ENDPOINT, cfg.AZURE_OPENAI_LLM_DEPLOYMENT = old_cfg
            emb._get_client = old_get_client