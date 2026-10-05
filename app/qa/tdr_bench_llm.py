"""LLM layer for the multi-supplier TDR benchmark.

Design rules (anti-hallucination):
* every extracted fact carries a verbatim quote and its page; the quote is
  verified against the page text in code (`verify_quote`) – unverifiable
  facts are kept for audit but excluded from scoring and synthesis;
* numbers reported in a fact value must also appear in the source page;
* comparison/profile/synthesis outputs cite fact ids which are validated
  against the supplier's verified facts;
* strict JSON-schema structured outputs, temperature 0, disk cache keyed by
  model + prompt version + inputs so re-runs are reproducible and cheap.
"""
from __future__ import annotations

import base64
import hashlib
import json
import logging
import re
import threading
import time
import unicodedata
from pathlib import Path
from typing import Any

from app.qa.tdr_bench_taxonomy import DOMAIN_KEYS, FACT_KIND_KEYS, PARAMETER_KEYS

logger = logging.getLogger(__name__)
PROMPT_VERSION = "tdr-bench-v2"


class LLMUnavailable(RuntimeError):
    pass


def _strict(schema: dict) -> dict:
    """Make every object strict (all properties required, no extras)."""
    if schema.get("type") == "object":
        schema["additionalProperties"] = False
        schema["required"] = list(schema.get("properties", {}))
        for value in schema.get("properties", {}).values():
            _strict(value)
    elif schema.get("type") == "array":
        _strict(schema["items"])
    return schema


def _s(desc: str = "") -> dict:
    return {"type": "string", "description": desc} if desc else {"type": "string"}


def _arr(items: dict) -> dict:
    return {"type": "array", "items": items}


def _obj(**props) -> dict:
    return {"type": "object", "properties": props}


def _enum(values, desc: str = "") -> dict:
    out = {"type": "string", "enum": list(values)}
    if desc:
        out["description"] = desc
    return out


_CITED = _obj(text=_s(), fact_ids=_arr(_s()))
_SEVERITY = _enum(["high", "medium", "low"])

FACTS_SCHEMA = _strict(_obj(facts=_arr(_obj(
    page=_s("page number from the [PAGE n] marker, digits only"),
    domain=_enum(DOMAIN_KEYS),
    kind=_enum(FACT_KIND_KEYS),
    topic=_s("short topic, 2-6 words"),
    parameter=_enum(list(PARAMETER_KEYS) + ["none"]),
    value=_s("normalized value with unit when the fact is quantitative, else empty"),
    variant=_s("product / variant the fact applies to when stated (e.g. DM12F, R8U RSE), else empty"),
    statement=_s("self-contained technical statement"),
    quote=_s("verbatim excerpt copied exactly from the page text, 4-30 words"),
    importance=_SEVERITY,
))))

DOMAIN_SCHEMA = _strict(_obj(
    summary=_s(),
    key_differentiators=_arr(_s()),
    comparison_points=_arr(_obj(
        aspect=_s(),
        best_supplier_ids=_arr(_s()),
        positions=_arr(_obj(supplier_id=_s(), position={
            "type": "string",
            "description": "Concrete sentence with the supplier's solution/values on this aspect (not a one-word rating)"},
            fact_ids=_arr(_s()))),
    )),
    assessments=_arr(_obj(
        supplier_id=_s(),
        score={"type": "integer", "description": "0-5 per rubric"},
        coverage=_enum(["complete", "partial", "minimal", "absent"]),
        summary=_s(),
        strengths=_arr(_CITED),
        weaknesses=_arr(_CITED),
        risks=_arr(_obj(text=_s(), severity=_SEVERITY, fact_ids=_arr(_s()))),
    )),
    ranking=_arr(_s()),
))

PROFILE_SCHEMA = _strict(_obj(
    overview=_s(),
    technical_positioning=_s(),
    top_strengths=_arr(_obj(text=_s(), domain=_enum(DOMAIN_KEYS), fact_ids=_arr(_s()))),
    top_weaknesses=_arr(_obj(text=_s(), domain=_enum(DOMAIN_KEYS), fact_ids=_arr(_s()))),
    top_risks=_arr(_obj(text=_s(), domain=_enum(DOMAIN_KEYS), severity=_SEVERITY, fact_ids=_arr(_s()))),
    declared_deviations=_arr(_CITED),
    assumptions_and_dependencies=_arr(_CITED),
    open_points=_arr(_CITED),
    clarification_questions=_arr(_obj(
        question=_s(), domain=_enum(DOMAIN_KEYS), priority=_SEVERITY, rationale=_s(),
    )),
))

SYNTHESIS_SCHEMA = _strict(_obj(
    executive_summary=_s(),
    recommendation=_obj(
        preferred_supplier_id=_s(),
        runner_up_supplier_ids=_arr(_s()),
        rationale=_s(),
        conditions=_arr(_s()),
    ),
    supplier_verdicts=_arr(_obj(
        supplier_id=_s(),
        verdict=_enum(["strong_candidate", "candidate_with_reservations", "weak_candidate"]),
        headline=_s(),
    )),
    cross_cutting_findings=_arr(_s()),
    major_risks=_arr(_obj(supplier_id=_s(), text=_s(), severity=_SEVERITY)),
    next_steps=_arr(_s()),
    confidence_note=_s(),
))

ASK_SCHEMA = _strict(_obj(
    answer=_s(),
    per_supplier=_arr(_obj(
        supplier_id=_s(),
        found={"type": "boolean"},
        answer=_s(),
        citations=_arr(_obj(doc_id=_s(), page={"type": "integer"})),
    )),
))


def _lang_name(lang: str) -> str:
    return "French" if lang == "fr" else "English"


SCORE_RUBRIC = (
    "Scoring rubric (integer 0-5), judged ONLY from the cited facts:\n"
    "0 = topic not addressed at all in the supplier documents\n"
    "1 = only mentioned, no technical substance\n"
    "2 = partial: generic description, key data missing or many open points/deviations\n"
    "3 = adequate: concrete solution with key data, some gaps or open points\n"
    "4 = strong: detailed and quantified, supported by evidence (simulation, test, carry-over), few open points\n"
    "5 = excellent: comprehensive, quantified, proven/mature solution, risks mitigated, clear advantage\n"
    "Declared deviations, TBDs, unproven claims and missing data lower the score. Marketing claims without "
    "data are not evidence. Scores must be relative-consistent: the same evidence level gets the same score."
)


DOMAIN_GUIDE = {
    "architecture": "product/system architecture: product family & variants, block diagrams, system "
                    "partitioning, electrical interfaces, connectors, pin-out, harness, carry-over/platform reuse",
    "mechanical": "housing, brackets, materials, dimensions, tolerances, stack-up, mass, CAD fit, head impact, "
                  "mechanical robustness",
    "display_optical": "panel, backlight, luminance, contrast, gamut, viewing angle, reflectance, cover lens, "
                       "optical bonding, optical films, image quality",
    "touch_hmi": "touch sensor & controller, touch performance, haptics, buttons, HMI features",
    "hardware": "electronics: SoC/MCU/TCON, deserializer, PCB, power supply, EMC design, components",
    "thermal": "thermal simulation and design, heat dissipation, temperature derating, thermal tests",
    "software": "software architecture, OS/stack, bootloader, diagnostics, flashing/OTA, SW development",
    "functional_safety": "ISO 26262, ASIL, safety concept, safety mechanisms, FMEDA, safety analyses",
    "cybersecurity": "ISO 21434, TARA, secure boot, HSM, encryption, CSMS, security features",
    "validation": "DV/PV plans, test matrices, environmental/EMC/reliability tests and results, simulations",
    "quality_process": "ASPICE, APQP, PPAP, IATF, DFMEA/PFMEA, quality organisation, development process, "
                       "lessons learned",
    "industrialization": "manufacturing sites, production lines, capacity, process steps, EOL tests, "
                         "traceability, supply chain",
    "planning": "timing, milestones, SOP, sample phases (A/B/C/D), resources, project team organisation",
    "options": "alternatives, VAVE ideas, optional features, cost-down technical proposals",
}


def _domain_guide() -> str:
    return "\n".join(f"- {key}: {text}" for key, text in DOMAIN_GUIDE.items())


def facts_prompt(supplier: str, project_context: str, lang: str, focus_domain: str = "") -> str:
    from app.qa.tdr_bench_taxonomy import PARAMETERS
    params = ", ".join(f"{p.key} ({p.label_en})" for p in PARAMETERS)
    focus = ""
    if focus_domain in DOMAIN_GUIDE:
        focus = (f"\nFOCUSED PASS: these pages were selected because they likely describe the domain "
                 f"'{focus_domain}' ({DOMAIN_GUIDE[focus_domain]}). Extract every fact relevant to this domain "
                 f"and set domain='{focus_domain}' for them; ignore facts of other domains. Return an empty list "
                 "if the pages contain nothing about this domain.")
    return (
        "You are a senior automotive electronics / display-module engineer at an OEM, reviewing a supplier "
        "Technical Design Review (TDR) / technical proposal submitted for an RFQ. Your job is EXHAUSTIVE, "
        f"faithful technical fact extraction. Supplier: {supplier}.\n"
        f"OUTPUT LANGUAGE: `statement` and `topic` MUST be written in {_lang_name(lang)} (translate them); "
        "`quote` stays verbatim in the source language.\n"
        + (f"Project context given by the OEM buyer: {project_context}\n" if project_context else "")
        + "Rules:\n"
        "1. Extract EVERY technically relevant fact on the given pages: specifications and measured/simulated "
        "values, design choices and component/part selections (with part numbers and makers), architecture, "
        "materials, interfaces, software/safety/security concepts, validation plans and results, "
        "manufacturing/industrialization, planning/samples, options/VAVE, and especially declared deviations, "
        "assumptions, risks and open points (TBD/TBC). Be exhaustive: a content-rich slide typically yields "
        "3-12 facts; every value with a unit and every table row must appear in at least one fact.\n"
        "2. Use only what is written. Never infer, never complete with general knowledge. Ignore commercial "
        "prices/costs, agenda/section-title slides, team member names and copyright boilerplate.\n"
        "3. `page` must be the number in the [PAGE n] marker where the quote appears.\n"
        "4. `quote` must be copied EXACTLY (same words, numbers and order) from that page, 4-30 words (a short "
        "contiguous excerpt, not a whole paragraph). Content under [VISION TRANSCRIPT ...] may be quoted too.\n"
        f"5. `parameter`: one of [{params}] when the fact gives that parameter, else 'none'. "
        "`value`: the value with unit exactly as stated (keep operators like >=, typ., min.), else ''.\n"
        "6. `variant`: the product/variant the fact applies to if stated (e.g. 12F, 12E, R8U RSE, J1X), else ''.\n"
        "7. kind: specification | design_choice | capability | plan | risk | deviation (non-compliance or "
        "exception to the OEM request) | assumption (prerequisite or OEM dependency) | open_point (TBD, to be "
        "confirmed, under study) | option (alternative or VAVE proposal).\n"
        "8. importance high = decisive for technical selection (key performance, architecture, safety, "
        "deviation, major risk); medium = useful detail; low = minor.\n"
        "9. One fact per distinct piece of information; no duplicates. Return an empty list for pages without "
        "technical content.\n"
        "10. `domain`: choose the most specific domain using this guide:\n" + _domain_guide() + focus
    )


VISION_PROMPT = (
    "This is one slide of an automotive supplier technical proposal. Transcribe ONLY the technical content "
    "visible in graphics, tables, diagrams, charts and pictures: values with units, table rows, labels, part "
    "numbers, block-diagram components and connections, chart axes and key data points, legends. Do not "
    "describe colours or layout and do not interpret. Write plain text lines (use ' | ' between table cells). "
    "If the slide has no technical content beyond its title, answer exactly: NO_TECHNICAL_CONTENT"
)


def domain_prompt(domain_label: str, lang: str, project_context: str, focus: str) -> str:
    return (
        "You are the lead OEM engineer comparing the technical offers of several suppliers for the same RFQ, "
        f"for the domain: {domain_label}. You receive, for each supplier, verified facts extracted from its TDR "
        "(format: fact_id [kind|importance] p.page (variant) statement || value).\n"
        + (f"Project context: {project_context}\n" if project_context else "")
        + (f"Buyer priorities: {focus}\n" if focus else "")
        + "Tasks: compare suppliers side by side on the most decisive technical aspects of this domain; "
        "identify differentiators; assess each supplier. `summary`: 3-6 concrete sentences stating who is "
        "ahead and why, with key values.\n"
        + SCORE_RUBRIC + "\n"
        "Rules: base every statement on the facts and cite their fact_ids (only ids given in the input, from the "
        "right supplier). A supplier with no facts gets score 0, coverage 'absent', and no invented strengths. "
        "Do not reward volume of slides, reward technical substance and evidence. Mention when suppliers "
        "address different variants. Provide an assessment for EVERY supplier listed, and a full ranking "
        f"(best first) of all supplier ids. 3-8 comparison points. Write in {_lang_name(lang)}. In prose "
        "(summary, differentiators, positions, strengths...) refer to suppliers by NAME, never by S1/S2 ids; "
        "ids are only for the id fields. Each position must be a concrete sentence with values, not one word."
    )


def profile_prompt(lang: str, project_context: str, focus: str) -> str:
    return (
        "You are the lead OEM engineer writing the technical profile of one supplier offer after a "
        "multi-supplier TDR comparison. Input: the supplier's per-domain assessments (with scores) and its "
        "verified deviation / assumption / risk / open-point facts.\n"
        + (f"Project context: {project_context}\n" if project_context else "")
        + (f"Buyer priorities: {focus}\n" if focus else "")
        + "Produce: overview (3-5 sentences), technical positioning versus competitors, top 3-6 strengths, "
        "top 3-6 weaknesses, top risks with severity, declared deviations, assumptions/dependencies on the "
        "OEM, open points, and 5-12 precise clarification questions the OEM should ask this supplier "
        "(each targeting a missing value, an open point, a risk or an unproven claim). Cite fact_ids from the "
        "input only. Do not invent facts. "
        f"Write in {_lang_name(lang)}."
    )


def synthesis_prompt(lang: str, project_context: str, focus: str) -> str:
    return (
        "You are the OEM technical lead writing the final technical synthesis of a multi-supplier RFQ TDR "
        "benchmark for management. Input: per-domain comparisons, the score matrix and the weighted technical "
        "scores computed by the tool (do not recompute them), and supplier profiles.\n"
        + (f"Project context: {project_context}\n" if project_context else "")
        + (f"Buyer priorities: {focus}\n" if focus else "")
        + "Write: an executive summary (8-14 sentences, concrete and quantified, naming suppliers), a technical "
        "recommendation (preferred supplier id, runners-up, rationale, conditions/clarifications required "
        "before nomination), a verdict and one-line headline per supplier, cross-cutting findings, major "
        "risks, next steps, and a confidence note on limits of the analysis (document-based only, no "
        "commercial aspects, evidence quality). This is decision support for engineers, technical only. "
        "Be consistent with the scores; if the scores are close, say so. "
        f"Write in {_lang_name(lang)}. In JSON id fields use supplier ids exactly as given (e.g. S1), but in "
        "all prose text refer to suppliers by their NAME only (never write S1/S2 in prose)."
    )


def ask_prompt(lang: str) -> str:
    return (
        "You answer an OEM engineer's question about several supplier TDR documents. For each supplier you "
        "receive the most relevant page excerpts (format: [doc_id p.page] text). Answer strictly from the "
        "excerpts; if the information is not present for a supplier, set found=false and say so. Cite the "
        "doc_id and page of every excerpt you used. Then give a short comparative answer across suppliers. "
        f"Write in {_lang_name(lang)}."
    )


# ── Grounding ──────────────────────────────────────────────────────────

_QUOTE_CHARS = str.maketrans({
    "\u2018": "'", "\u2019": "'", "\u201c": '"', "\u201d": '"', "\u2013": "-", "\u2014": "-",
    "\u2212": "-", "\u00a0": " ", "\u2026": "...", "\uff1e": ">", "\uff1c": "<", "\u2264": "<=",
    "\u2265": ">=", "\u00b2": "2", "\u2122": "", "\u00ae": "",
})


def normalize_for_match(text: str) -> str:
    text = unicodedata.normalize("NFKC", text or "").translate(_QUOTE_CHARS).casefold()
    text = re.sub(r"[^\w%°.,:<>=+/-]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _tokens(text: str) -> list[str]:
    return [t for t in re.split(r"[\s,.:;/]+", normalize_for_match(text)) if t]


_NUM = re.compile(r"\d+(?:[.,]\d+)?")


def numbers_in(text: str) -> set[str]:
    out = set()
    for raw in _NUM.findall(text or ""):
        out.add(raw.replace(",", "."))
        if "," in raw and re.fullmatch(r"\d{1,3},\d{3}", raw):
            out.add(raw.replace(",", ""))
    return out


def verify_quote(quote: str, page_text: str) -> str:
    """Return 'verified', 'approximate' or 'unverified'."""
    q = normalize_for_match(quote)
    if len(q) < 4:
        return "unverified"
    p = normalize_for_match(page_text)
    if q in p:
        return "verified"
    q_tokens = _tokens(quote)
    if not q_tokens:
        return "unverified"
    p_tokens = set(_tokens(page_text))
    present = sum(1 for t in q_tokens if t in p_tokens)
    ratio = present / len(q_tokens)
    q_numbers = numbers_in(quote)
    if q_numbers and not q_numbers <= numbers_in(page_text):
        return "unverified"
    if ratio >= 0.85 and len(q_tokens) >= 3:
        return "approximate"
    return "unverified"


def value_numbers_supported(value: str, page_text: str) -> bool:
    nums = numbers_in(value)
    if not nums:
        return True
    page_nums = numbers_in(page_text)
    return all(n in page_nums or n.rstrip("0").rstrip(".") in {x.rstrip("0").rstrip(".") for x in page_nums}
               for n in nums)


# ── Client wrapper ─────────────────────────────────────────────────────

_client_lock = threading.Lock()
_clients: dict[tuple[float, int], Any] = {}


def llm_configured() -> bool:
    from app.config import AZURE_OPENAI_API_KEY, AZURE_OPENAI_ENDPOINT
    return bool(AZURE_OPENAI_API_KEY and AZURE_OPENAI_ENDPOINT)


def configured_deployment() -> str:
    from app.config import AZURE_OPENAI_LLM_DEPLOYMENT
    return AZURE_OPENAI_LLM_DEPLOYMENT or "gpt-4o"


def _shared_client(timeout: float, max_retries: int) -> Any:
    """One OpenAI client per process: building one loads the Windows certificate store through a
    GIL-holding C call, which can freeze the whole server when repeated per request."""
    key = (timeout, max_retries)
    with _client_lock:
        client = _clients.get(key)
        if client is None:
            from app.config import AZURE_OPENAI_API_KEY, AZURE_OPENAI_ENDPOINT
            from openai import OpenAI
            client = OpenAI(api_key=AZURE_OPENAI_API_KEY, base_url=AZURE_OPENAI_ENDPOINT,
                            timeout=timeout, max_retries=max_retries)
            _clients[key] = client
        return client

class LLM:
    """Thread-safe Azure OpenAI JSON client with disk cache and usage stats."""

    def __init__(self, cache_dir: Path | None, client: Any = None, deployment: str | None = None,
                 timeout: float = 180.0, max_retries: int = 5):
        self.cache_dir = cache_dir
        if cache_dir:
            cache_dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self.calls = 0
        self.cached_calls = 0
        self.failed_calls = 0
        self.prompt_tokens = 0
        self.completion_tokens = 0
        self._schema_supported = True
        self.deployment = deployment
        self.client = client
        if client is None and llm_configured():
            from app.config import AZURE_OPENAI_LLM_DEPLOYMENT
            self.client = _shared_client(timeout, max_retries)
            self.deployment = deployment or AZURE_OPENAI_LLM_DEPLOYMENT
        self.deployment = self.deployment or "gpt-4o"

    @property
    def available(self) -> bool:
        return self.client is not None

    def stats(self) -> dict:
        return {
            "llmCalls": self.calls, "cachedCalls": self.cached_calls, "failedCalls": self.failed_calls,
            "promptTokens": self.prompt_tokens, "completionTokens": self.completion_tokens,
            "model": self.deployment,
        }

    def _cache_path(self, payload: dict) -> Path | None:
        if not self.cache_dir:
            return None
        digest = hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        return self.cache_dir / f"{digest}.json"

    def _record(self, response) -> None:
        usage = getattr(response, "usage", None)
        with self._lock:
            self.calls += 1
            if usage is not None:
                self.prompt_tokens += int(getattr(usage, "prompt_tokens", 0) or 0)
                self.completion_tokens += int(getattr(usage, "completion_tokens", 0) or 0)

    def _create(self, **kwargs):
        last = None
        for attempt in range(3):
            try:
                return self.client.chat.completions.create(**kwargs)
            except Exception as exc:  # transient network errors beyond SDK retries
                last = exc
                status = getattr(exc, "status_code", None)
                if status is not None and status < 500 and status != 429:
                    raise
                time.sleep(4 * (attempt + 1))
        raise last  # type: ignore[misc]

    def chat_json(self, *, system: str, user: str, schema_name: str, schema: dict,
                  max_tokens: int = 4000) -> dict:
        if not self.available:
            raise LLMUnavailable("Azure OpenAI is not configured")
        key = {"v": PROMPT_VERSION, "m": self.deployment, "s": system, "u": user, "n": schema_name,
               "schema": schema, "t": max_tokens}
        path = self._cache_path(key)
        if path and path.exists():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                with self._lock:
                    self.cached_calls += 1
                return data
            except Exception:
                pass
        messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
        data = None
        error = None
        for attempt in range(2):
            try:
                if self._schema_supported:
                    try:
                        response = self._create(
                            model=self.deployment, messages=messages, temperature=0, max_tokens=max_tokens,
                            response_format={"type": "json_schema", "json_schema": {
                                "name": schema_name, "strict": True, "schema": schema}},
                        )
                    except Exception as exc:
                        if getattr(exc, "status_code", None) == 400 and "response_format" in str(exc):
                            self._schema_supported = False
                            raise
                        raise
                else:
                    schema_hint = "\nReturn ONLY a JSON object matching this JSON schema:\n" + json.dumps(schema)
                    response = self._create(
                        model=self.deployment, temperature=0, max_tokens=max_tokens,
                        messages=[{"role": "system", "content": system + schema_hint},
                                  {"role": "user", "content": user}],
                        response_format={"type": "json_object"},
                    )
                self._record(response)
                choice = response.choices[0]
                text = (choice.message.content or "").strip()
                text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text)
                data = json.loads(text)
                if getattr(choice, "finish_reason", "") == "length":
                    logger.warning("LLM output truncated for %s", schema_name)
                break
            except json.JSONDecodeError as exc:
                error = exc
                max_tokens = min(16000, int(max_tokens * 1.6))
            except Exception as exc:
                error = exc
                if not self._schema_supported and attempt == 0:
                    continue
                break
        if data is None:
            with self._lock:
                self.failed_calls += 1
            raise RuntimeError(f"LLM call failed ({schema_name}): {type(error).__name__}: {error}")
        if path:
            try:
                path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
            except OSError:
                pass
        return data

    def vision_transcript(self, png: bytes, max_tokens: int = 900) -> str:
        if not self.available:
            raise LLMUnavailable("Azure OpenAI is not configured")
        digest = hashlib.sha256(png).hexdigest()
        path = self._cache_path({"v": PROMPT_VERSION, "m": self.deployment, "vision": digest, "p": VISION_PROMPT})
        if path and path.exists():
            with self._lock:
                self.cached_calls += 1
            return json.loads(path.read_text(encoding="utf-8"))["text"]
        response = self._create(
            model=self.deployment, temperature=0, max_tokens=max_tokens,
            messages=[{"role": "user", "content": [
                {"type": "text", "text": VISION_PROMPT},
                {"type": "image_url", "image_url": {
                    "url": "data:image/png;base64," + base64.b64encode(png).decode(), "detail": "high"}},
            ]}],
        )
        self._record(response)
        text = (response.choices[0].message.content or "").strip()
        if text.upper().startswith("NO_TECHNICAL_CONTENT"):
            text = ""
        if path:
            try:
                path.write_text(json.dumps({"text": text}, ensure_ascii=False), encoding="utf-8")
            except OSError:
                pass
        return text
