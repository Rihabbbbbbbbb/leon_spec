"""Multi-supplier TDR technical benchmark engine.

Pipeline (one background job):
  1. ingest      – read every document, group by supplier, clean boilerplate
  2. vision      – optional GPT-4o transcription of graphic-heavy slides
  3. extract     – exhaustive fact extraction per page-chunk (quote + page)
  4. ground      – verify every quote against the page text in code
  5. compare     – one side-by-side comparison per technical domain (0-5 rubric)
  6. profiles    – one technical profile + clarification questions per supplier
  7. synthesis   – executive synthesis; weighted scores are computed in code

Technical content only: commercial pages are skipped and the prompts exclude
prices / costs. When no LLM is configured (or it fails) a deterministic,
regex-based mode still produces a usable (more limited) comparison.
"""
from __future__ import annotations

import collections
import json
import logging
import math
import re
import shutil
import threading
import time
import traceback
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from app.qa.tdr_bench_ingest import (
    DocumentText,
    Page,
    detect_mentions,
    detect_supplier,
    load_document,
    render_page_png,
)
from app.qa.tdr_bench_llm import (
    ASK_SCHEMA,
    DOMAIN_SCHEMA,
    FACTS_SCHEMA,
    LLM,
    PROFILE_SCHEMA,
    SYNTHESIS_SCHEMA,
    ask_prompt,
    domain_prompt,
    facts_prompt,
    normalize_for_match,
    profile_prompt,
    synthesis_prompt,
    value_numbers_supported,
    verify_quote,
)
from app.qa.tdr_bench_taxonomy import (
    DOMAIN_BY_KEY,
    DOMAIN_KEYS,
    DOMAINS,
    FACT_KINDS,
    KIND_HINTS,
    PARAMETER_BY_KEY,
    PARAMETER_PATTERNS,
    PARAMETERS,
    unit_mismatch,
    weights_default,
)

logger = logging.getLogger(__name__)

ENGINE_VERSION = "1.0"
_TERMINAL_STATUS = {"completed", "failed", "cancelled", "interrupted"}
IMPORTANCE_RANK = {"high": 0, "medium": 1, "low": 2}
KIND_PRIORITY = {"deviation": 0, "risk": 1, "open_point": 2, "specification": 3, "design_choice": 4,
                 "assumption": 5, "option": 6, "plan": 7, "capability": 8}
PARAMETER_DOMAIN = {
    "display_size": "display_optical", "resolution": "display_optical", "panel_technology": "display_optical",
    "luminance": "display_optical", "contrast_ratio": "display_optical", "color_gamut": "display_optical",
    "viewing_angle": "display_optical", "response_time": "display_optical", "reflectance": "display_optical",
    "backlight": "display_optical", "cover_lens": "display_optical", "optical_bonding": "display_optical",
    "touch_technology": "touch_hmi", "video_interface": "hardware", "processor": "hardware",
    "power_consumption": "hardware", "supply_voltage": "hardware", "operating_temperature": "thermal",
    "thickness": "mechanical", "weight": "mechanical", "housing_material": "mechanical",
    "asil_level": "functional_safety", "cybersecurity_standard": "cybersecurity",
    "aspice_level": "software", "software_stack": "software", "manufacturing_site": "industrialization",
    "sop_date": "planning", "reliability_target": "validation",
}
VERDICT_LABELS = {
    "strong_candidate": ("Strong candidate", "Candidat solide"),
    "candidate_with_reservations": ("Candidate with reservations", "Candidat avec réserves"),
    "weak_candidate": ("Weak candidate", "Candidat faible"),
}


class JobCancelled(Exception):
    pass


@dataclass
class BenchOptions:
    language: str = "fr"
    vision: str = "auto"            # off | auto | all
    vision_max_pages: int = 40      # per supplier
    project_context: str = ""
    focus: str = ""
    weights: dict[str, float] = field(default_factory=weights_default)
    use_llm: bool = True
    workers: int = 8

    def normalized(self) -> "BenchOptions":
        self.language = "en" if str(self.language).lower().startswith("en") else "fr"
        self.vision = self.vision if self.vision in {"off", "auto", "all"} else "auto"
        self.vision_max_pages = max(0, min(int(self.vision_max_pages or 0), 300))
        self.project_context = (self.project_context or "").strip()[:2000]
        self.focus = (self.focus or "").strip()[:1000]
        weights = weights_default()
        for key, value in (self.weights or {}).items():
            if key in weights:
                try:
                    weights[key] = max(0.0, min(float(value), 10.0))
                except (TypeError, ValueError):
                    pass
        self.weights = weights
        self.workers = max(1, min(int(self.workers or 8), 16))
        return self


@dataclass
class InputFile:
    doc_id: str
    file_name: str
    path: Path
    supplier: str


@dataclass
class Supplier:
    id: str
    name: str
    docs: list[DocumentText] = field(default_factory=list)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


_NUMBER = re.compile(r"(?<![\d.,])(\d+(?:[.,]\d+)?)")


def _numbers(text: str) -> list[str]:
    """Distinctive numbers of a text (``12,3`` == ``12.3``); single digits are too generic to be meaningful."""
    out = []
    for raw in _NUMBER.findall(text or ""):
        value = raw.replace(",", ".")
        if "." in value:
            value = value.rstrip("0").rstrip(".")  # 12.0 == 12, 12.30 == 12.3
        if len(value.replace(".", "")) >= 2:
            out.append(value)
    return out


def _prefer_shared_state(local: dict | None, remote: dict | None) -> dict | None:
    """Pick the job snapshot another instance should trust.

    A completed job wins over a poll that still sees "running". Otherwise the
    higher progress wins, so a stale copy cannot hide a finished analysis.
    """
    if not isinstance(remote, dict):
        return local
    if not isinstance(local, dict):
        return remote
    local_done = local.get("status") in _TERMINAL_STATUS
    remote_done = remote.get("status") in _TERMINAL_STATUS
    if remote_done and not local_done:
        return remote
    if local_done and not remote_done:
        return local
    remote_progress = float(remote.get("progress") or 0)
    local_progress = float(local.get("progress") or 0)
    if remote_progress > local_progress:
        return remote
    if local_progress > remote_progress:
        return local
    if (remote.get("finishedAt") or "") > (local.get("finishedAt") or ""):
        return remote
    return local


def _atomic_write(path: Path, payload: str, attempts: int = 8) -> bool:
    """Write via a temp file + rename; retries because Windows may briefly lock the target
    (concurrent reader, antivirus scan)."""
    tmp = path.with_suffix(".tmp")
    for attempt in range(attempts):
        try:
            tmp.write_text(payload, encoding="utf-8")
            tmp.replace(path)
            return True
        except PermissionError:
            time.sleep(0.05 * (attempt + 1))
        except OSError as exc:
            logger.warning("Could not write %s: %s", path, exc)
            return False
    return False


def _supplier_key(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", name.casefold()) or "supplier"


def group_suppliers(files: list[InputFile]) -> tuple[list[Supplier], dict[str, str]]:
    """Group files by supplier name (case/punctuation-insensitive)."""
    suppliers: list[Supplier] = []
    by_key: dict[str, Supplier] = {}
    doc_to_supplier: dict[str, str] = {}
    for f in files:
        if not f.supplier.strip():
            try:
                f.supplier = detect_supplier(f.file_name, "") or ""
            except Exception:
                f.supplier = ""
            if not f.supplier.strip():
                f.supplier = Path(f.file_name).stem[:60] or f.doc_id
        key = _supplier_key(f.supplier)
        if key not in by_key:
            sup = Supplier(id=f"S{len(suppliers) + 1}", name=f.supplier.strip() or f"Supplier {len(suppliers) + 1}")
            by_key[key] = sup
            suppliers.append(sup)
        doc_to_supplier[f.doc_id] = by_key[key].id
    return suppliers, doc_to_supplier


def build_chunks(doc: DocumentText, max_chars: int = 10000, max_pages: int = 8) -> list[dict]:
    chunks: list[dict] = []
    pages: list[int] = []
    parts: list[str] = []
    size = 0
    for page in doc.pages:
        if page.commercial:
            continue
        text = page.full_text.strip()
        if len(text) < 30:
            continue
        block = f"[PAGE {page.number}]\n{text}\n"
        if pages and (size + len(block) > max_chars or len(pages) >= max_pages):
            chunks.append({"doc_id": doc.doc_id, "pages": pages, "text": "\n".join(parts)})
            pages, parts, size = [], [], 0
        pages.append(page.number)
        parts.append(block)
        size += len(block)
    if pages:
        chunks.append({"doc_id": doc.doc_id, "pages": pages, "text": "\n".join(parts)})
    return chunks


def _parse_page(value: Any) -> int | None:
    match = re.search(r"\d+", str(value or ""))
    return int(match.group()) if match else None


def ground_fact(raw: dict, doc: DocumentText, chunk_pages: list[int]) -> dict:
    """Locate and verify the quote of an extracted fact; returns grounding info."""
    quote = str(raw.get("quote") or "").strip()
    claimed = _parse_page(raw.get("page"))
    order = ([claimed] if claimed in chunk_pages else []) + [p for p in chunk_pages if p != claimed]
    best = ("unverified", claimed if claimed in chunk_pages else (chunk_pages[0] if chunk_pages else 1), "text")
    for number in order:
        page = doc.page(number)
        if page is None:
            continue
        status_text = verify_quote(quote, page.text)
        status = status_text
        source = "text"
        if status_text == "unverified" and page.vision_text:
            status = verify_quote(quote, page.vision_text)
            source = "vision"
        if status == "verified":
            best = (status, number, source)
            break
        if status == "approximate" and best[0] == "unverified":
            best = (status, number, source)
    status, number, source = best
    page = doc.page(number)
    value_ok = value_numbers_supported(str(raw.get("value") or ""), page.full_text if page else "")
    if status != "unverified" and not value_ok:
        status = "approximate" if status == "verified" else "unverified"
    return {"grounding": status, "page": number, "source": source, "value_check": value_ok,
            "page_moved": claimed is not None and number != claimed}


def _clean_fact(raw: dict, lang: str) -> dict | None:
    statement = re.sub(r"\s+", " ", str(raw.get("statement") or "")).strip()
    quote = re.sub(r"\s+", " ", str(raw.get("quote") or "")).strip()
    if not statement or not quote:
        return None
    domain = raw.get("domain") if raw.get("domain") in DOMAIN_BY_KEY else "architecture"
    kind = raw.get("kind") if raw.get("kind") in FACT_KINDS else "specification"
    parameter = raw.get("parameter") if raw.get("parameter") in PARAMETER_BY_KEY else ""
    importance = raw.get("importance") if raw.get("importance") in IMPORTANCE_RANK else "medium"
    return {
        "domain": domain, "kind": kind, "parameter": parameter, "importance": importance,
        "topic": str(raw.get("topic") or "").strip()[:120],
        "value": str(raw.get("value") or "").strip()[:200],
        "variant": str(raw.get("variant") or "").strip()[:80],
        "statement": statement[:800], "quote": quote[:600],
    }


def dedupe_facts(facts: list[dict]) -> list[dict]:
    seen: set[tuple] = set()
    out = []
    for fact in facts:
        keys = {
            (fact["supplier_id"], fact["domain"], "q", normalize_for_match(fact["quote"])[:160],
             fact.get("parameter", "")),
            (fact["supplier_id"], fact["domain"], "s", normalize_for_match(fact["statement"])[:200]),
        }
        if keys & seen:
            continue
        seen |= keys
        out.append(fact)
    return out


# ── Deterministic (no-LLM) extraction ──────────────────────────────────

def deterministic_facts(doc: DocumentText, lang: str) -> list[dict]:
    facts = []
    for page in doc.pages:
        if page.commercial:
            continue
        top_domain = max(page.domain_hits, key=page.domain_hits.get) if page.domain_hits else "architecture"
        for line in page.text.split("\n"):
            line = line.strip()
            if len(line) < 6 or len(line) > 400:
                continue
            for key, patterns in PARAMETER_PATTERNS.items():
                for pattern in patterns:
                    if pattern.search(line):
                        facts.append({
                            "domain": PARAMETER_DOMAIN.get(key, top_domain), "kind": "specification",
                            "parameter": key, "importance": "medium", "topic": PARAMETER_BY_KEY[key].label(lang),
                            "value": "", "variant": "", "statement": line, "quote": line,
                            "page": page.number, "grounding": "verified", "source": "text",
                            "value_check": True, "page_moved": False,
                        })
                        break
            for kind, pattern in KIND_HINTS.items():
                if pattern.search(line) and len(line) >= 15:
                    facts.append({
                        "domain": top_domain, "kind": kind, "parameter": "", "importance": "high"
                        if kind == "deviation" else "medium", "topic": FACT_KINDS[kind][1 if lang == "fr" else 0],
                        "value": "", "variant": "", "statement": line, "quote": line, "page": page.number,
                        "grounding": "verified", "source": "text", "value_check": True, "page_moved": False,
                    })
                    break
    return facts


# ── Scoring helpers (code, never LLM) ─────────────────────────────────

def weighted_scores(score_matrix: dict[str, dict[str, int]], weights: dict[str, float],
                    supplier_ids: list[str]) -> dict[str, dict]:
    """Weighted mean of domain scores, skipping domains nobody addressed."""
    active = [d for d in DOMAIN_KEYS
              if weights.get(d, 0) > 0 and any(score_matrix.get(d, {}).get(s, 0) > 0 for s in supplier_ids)]
    total_w = sum(weights[d] for d in active)
    out = {}
    for sid in supplier_ids:
        if not total_w:
            out[sid] = {"score": 0.0, "percent": 0.0, "domains": 0}
            continue
        value = sum(weights[d] * score_matrix.get(d, {}).get(sid, 0) for d in active) / total_w
        out[sid] = {"score": round(value, 2), "percent": round(value / 5 * 100, 1), "domains": len(active)}
    ranked = sorted(supplier_ids, key=lambda s: (-out[s]["score"], s))
    for rank, sid in enumerate(ranked, 1):
        out[sid]["rank"] = rank
    return out


def sanitize_overrides(raw: Any, result: dict) -> dict:
    """Keep only valid expert overrides: weights 0-5 per domain, integer scores 0-5 per domain/supplier."""
    raw = raw if isinstance(raw, dict) else {}
    sids = {s["id"] for s in result.get("suppliers", [])}
    weights: dict[str, float] = {}
    for key, value in (raw.get("weights") or {}).items():
        if key in DOMAIN_KEYS:
            try:
                weights[key] = round(max(0.0, min(5.0, float(value))), 2)
            except (TypeError, ValueError):
                continue
    scores: dict[str, dict[str, int]] = {}
    for domain, cells in (raw.get("scores") or {}).items():
        if domain not in DOMAIN_KEYS or not isinstance(cells, dict):
            continue
        for sid, value in cells.items():
            if sid not in sids:
                continue
            try:
                scores.setdefault(domain, {})[sid] = int(max(0, min(5, round(float(value)))))
            except (TypeError, ValueError):
                continue
    comments: dict[str, str] = {}
    for key, value in (raw.get("comments") or {}).items():
        if isinstance(key, str) and len(key) <= 80 and str(value or "").strip():
            comments[key] = str(value).strip()[:2000]
    return {"weights": weights, "scores": scores, "comments": comments,
            "updatedAt": _now() if (weights or scores or comments) else None}


def apply_overrides(result: dict, overrides: dict | None) -> dict:
    """Return the result with expert weight/score overrides applied and totals/ranks recomputed."""
    if not overrides or not (overrides.get("weights") or overrides.get("scores") or overrides.get("comments")):
        return result
    out = json.loads(json.dumps(result))
    sids = [s["id"] for s in out["suppliers"]]
    out["aiScoreMatrix"] = result["scoreMatrix"]
    out["aiTotals"] = result["totals"]
    out["aiWeights"] = {d["key"]: d["weight"] for d in result["domains"]}
    weights = dict(out["aiWeights"])
    weights.update(overrides.get("weights") or {})
    matrix = {d: dict(out["scoreMatrix"].get(d, {})) for d in DOMAIN_KEYS}
    for domain, cells in (overrides.get("scores") or {}).items():
        for sid, value in cells.items():
            if sid in sids:
                matrix.setdefault(domain, {})[sid] = value
                assessment = out.get("domainResults", {}).get(domain, {}).get("assessments", {}).get(sid)
                if isinstance(assessment, dict):
                    assessment["expertScore"] = value
    totals = weighted_scores(matrix, weights, sids)
    out["scoreMatrix"] = matrix
    out["totals"] = totals
    for d in out["domains"]:
        d["weight"] = weights.get(d["key"], d["weight"])
    for s in out["suppliers"]:
        s.update(score=totals[s["id"]]["score"], percent=totals[s["id"]]["percent"], rank=totals[s["id"]]["rank"])
    rec = out["synthesis"]["recommendation"]
    top = min(sids, key=lambda s: totals[s]["rank"]) if sids else ""
    rec["topScored"] = top
    rec["differsFromScore"] = bool(rec.get("preferred") and top and rec["preferred"] != top)
    out["overrides"] = overrides
    out["overridesApplied"] = True
    return out


def _bm25_rank(query: str, pages: list[tuple[str, int, str]], top: int) -> list[tuple[str, int, str]]:
    tokens = [t for t in re.findall(r"[a-z0-9]+", normalize_for_match(query)) if len(t) > 1]
    if not tokens or not pages:
        return pages[:top]
    docs = [re.findall(r"[a-z0-9]+", normalize_for_match(text)) for _, _, text in pages]
    avg = sum(len(d) for d in docs) / max(len(docs), 1) or 1
    df = collections.Counter(t for d in docs for t in set(d))
    n = len(docs)
    scored = []
    for (doc_id, number, text), words in zip(pages, docs):
        tf = collections.Counter(words)
        score = 0.0
        for t in tokens:
            if tf[t]:
                idf = math.log(1 + (n - df[t] + 0.5) / (df[t] + 0.5))
                score += idf * tf[t] * 2.2 / (tf[t] + 1.2 * (0.25 + 0.75 * len(words) / avg))
        scored.append((score, doc_id, number, text))
    scored.sort(key=lambda x: (-x[0], x[1], x[2]))
    return [(d, n_, t) for s, d, n_, t in scored[:top] if s > 0]


# ── Engine ─────────────────────────────────────────────────────────────

class BenchmarkRun:
    def __init__(self, job_dir: Path, files: list[InputFile], options: BenchOptions, llm: LLM | None,
                 progress: Callable[[dict], None] | None = None, cancel: threading.Event | None = None):
        self.job_dir = job_dir
        self.files = files
        self.opt = options.normalized()
        self.llm = llm if (llm is not None and llm.available and self.opt.use_llm) else None
        self.mode = "llm" if self.llm else "deterministic"
        self._progress_cb = progress or (lambda _u: None)
        self.cancel = cancel or threading.Event()
        self.warnings: list[str] = []
        self.log: list[dict] = []
        self.lang = self.opt.language
        self.suppliers: list[Supplier] = []
        self.doc_supplier: dict[str, str] = {}
        self.docs: dict[str, DocumentText] = {}
        self.timings: dict[str, float] = {}

    # progress -----------------------------------------------------------
    STAGES = (("ingest", 5), ("vision", 20), ("extract", 50), ("compare", 13), ("profiles", 6), ("synthesis", 6))

    def _progress(self, stage: str, fraction: float, message: str = "") -> None:
        done = 0
        pct = 0.0
        for name, weight in self.STAGES:
            if name == stage:
                pct = done + weight * max(0.0, min(fraction, 1.0))
                break
            done += weight
        update = {"stage": stage, "progress": round(pct, 1)}
        if message:
            entry = {"time": _now(), "stage": stage, "message": message}
            self.log.append(entry)
            update["logEntry"] = entry
        self._progress_cb(update)

    def _check_cancel(self) -> None:
        if self.cancel.is_set():
            raise JobCancelled()

    def _warn(self, message: str) -> None:
        self.warnings.append(message)
        logger.warning(message)

    # helpers ----------------------------------------------------------------
    def _supplier(self, sid: str) -> Supplier:
        return next(s for s in self.suppliers if s.id == sid)

    def _map_parallel(self, items: list, fn: Callable, stage: str, label: str) -> list:
        results: list = [None] * len(items)
        if not items:
            return results
        done = 0
        with ThreadPoolExecutor(max_workers=self.opt.workers) as pool:
            futures = {pool.submit(self._guarded, fn, item): i for i, item in enumerate(items)}
            try:
                for future in as_completed(futures):
                    idx = futures[future]
                    results[idx] = future.result()
                    done += 1
                    if done == len(items) or done % max(1, len(items) // 20) == 0:
                        self._progress(stage, done / len(items), f"{label} {done}/{len(items)}")
            except JobCancelled:
                for f in futures:
                    f.cancel()
                raise
        self._check_cancel()
        return results

    def _guarded(self, fn: Callable, item: Any) -> Any:
        if self.cancel.is_set():
            return None
        try:
            return fn(item)
        except JobCancelled:
            return None
        except Exception as exc:
            return {"__error__": f"{type(exc).__name__}: {exc}"}

    # stages -----------------------------------------------------------------
    def ingest(self) -> None:
        t0 = time.time()
        self.suppliers, self.doc_supplier = group_suppliers(self.files)
        for idx, f in enumerate(self.files, 1):
            self._check_cancel()
            try:
                doc = load_document(f.path.read_bytes(), f.doc_id, f.file_name)
            except Exception as exc:
                self._warn(f"{f.file_name}: unreadable ({type(exc).__name__}: {exc})")
                doc = DocumentText(f.doc_id, f.file_name, f.path.suffix.lstrip(".").lower(), [],
                                   [f"Unreadable: {exc}"])
            self.docs[f.doc_id] = doc
            self._supplier(self.doc_supplier[f.doc_id]).docs.append(doc)
            for w in doc.warnings:
                self._warn(f"{f.file_name}: {w}")
            self._progress("ingest", idx / len(self.files),
                           f"{f.file_name}: {len(doc.pages)} pages, {doc.char_count} chars")
        self.timings["ingest"] = round(time.time() - t0, 1)

    def vision(self) -> None:
        t0 = time.time()
        if not self.llm or self.opt.vision == "off" or self.opt.vision_max_pages == 0:
            self._progress("vision", 1, "Vision transcription skipped")
            return
        tasks = []
        for sup in self.suppliers:
            candidates = []
            for doc in sup.docs:
                if doc.kind != "pdf":
                    continue
                for page in doc.pages:
                    if page.commercial or page.images == 0:
                        continue
                    if self.opt.vision == "all" or page.needs_vision():
                        candidates.append((doc, page))
            candidates.sort(key=lambda dp: (len(dp[1].text) > 250, -dp[1].image_ratio))
            tasks.extend(candidates[: self.opt.vision_max_pages])
        self._progress("vision", 0, f"Vision transcription of {len(tasks)} graphic slides")
        file_by_doc = {f.doc_id: f for f in self.files}
        content_cache: dict[str, bytes] = {}
        lock = threading.Lock()

        def run(task):
            doc, page = task
            with lock:
                content = content_cache.get(doc.doc_id)
                if content is None:
                    content = file_by_doc[doc.doc_id].path.read_bytes()
                    content_cache[doc.doc_id] = content
            png = render_page_png(content, page.number, dpi=110)
            text = self.llm.vision_transcript(png)
            page.vision_text = text.strip()[:6000]
            return True

        results = self._map_parallel(tasks, run, "vision", "Vision")
        failures = [r for r in results if isinstance(r, dict) and "__error__" in r]
        if failures:
            self._warn(f"Vision transcription failed on {len(failures)} slide(s): {failures[0]['__error__']}")
        content_cache.clear()
        self.timings["vision"] = round(time.time() - t0, 1)

    GAP_MIN_FACTS = 2
    GAP_MAX_PAGES = 8
    GAP_MAX_CHARS = 14000

    def _gap_tasks(self, tasks: list, results: list) -> list:
        """Focused second pass for (supplier, domain) cells left thin by the generic extraction.

        Generic page-group extraction tends to file cross-cutting content (architecture, quality process,
        validation) under neighbouring domains, which would otherwise show up as 'not addressed'."""
        counts: collections.Counter[tuple[str, str]] = collections.Counter()
        for task, result in zip(tasks, results):
            if isinstance(result, dict) and "__error__" not in result:
                for fact in result["facts"]:
                    if fact["grounding"] != "unverified":
                        counts[(task[0].id, fact["domain"])] += 1
        gap = []
        for sup in self.suppliers:
            for domain in DOMAIN_KEYS:
                if counts[(sup.id, domain)] >= self.GAP_MIN_FACTS:
                    continue
                for doc in sup.docs:
                    pages = [p for p in doc.pages if not p.commercial and p.domain_hits.get(domain, 0) >= 2
                             and len(p.full_text.strip()) >= 30]
                    if len(pages) < 2:
                        continue
                    pages.sort(key=lambda p: -p.domain_hits.get(domain, 0))
                    chosen, size = [], 0
                    for page in pages:
                        block = f"[PAGE {page.number}]\n{page.full_text.strip()}\n"
                        if chosen and (size + len(block) > self.GAP_MAX_CHARS or len(chosen) >= self.GAP_MAX_PAGES):
                            break
                        chosen.append((page.number, block))
                        size += len(block)
                    chosen.sort()
                    chunk = {"doc_id": doc.doc_id, "pages": [n for n, _ in chosen],
                             "text": "\n".join(b for _, b in chosen)}
                    gap.append((sup, doc, chunk, domain))
        return gap

    def extract(self) -> list[dict]:
        t0 = time.time()
        facts: list[dict] = []
        if self.llm:
            tasks = []
            for sup in self.suppliers:
                for doc in sup.docs:
                    for chunk in build_chunks(doc):
                        tasks.append((sup, doc, chunk))
            self._progress("extract", 0, f"Fact extraction: {len(tasks)} page groups")

            def run(task):
                sup, doc, chunk = task[:3]
                focus_domain = task[3] if len(task) > 3 else ""
                data = self.llm.chat_json(
                    system=facts_prompt(sup.name, self.opt.project_context, self.lang, focus_domain),
                    user=(f"Document: {doc.file_name}\n\n{chunk['text']}\n\n"
                          f"(Reminder: statement/topic in {'French' if self.lang == 'fr' else 'English'}; "
                          "quotes verbatim; be exhaustive.)"),
                    schema_name="tdr_facts", schema=FACTS_SCHEMA, max_tokens=9000)
                out = []
                for raw in data.get("facts") or []:
                    fact = _clean_fact(raw, self.lang)
                    if not fact:
                        continue
                    if focus_domain:
                        fact["domain"] = focus_domain
                    fact.update(ground_fact(raw, doc, chunk["pages"]))
                    out.append(fact)
                return {"facts": out}

            results = self._map_parallel(tasks, run, "extract", "Extraction")
            gap_tasks = self._gap_tasks(tasks, results)
            if gap_tasks:
                self._progress("extract", 1, f"Targeted re-reading of {len(gap_tasks)} thin domain(s)")
                tasks = tasks + gap_tasks
                results = results + self._map_parallel(gap_tasks, run, "extract", "Targeted extraction")
            errors = 0
            last_error = ""
            for task, result in zip(tasks, results):
                sup, doc = task[0], task[1]
                if not isinstance(result, dict) or "__error__" in result:
                    errors += 1
                    if isinstance(result, dict):
                        last_error = result["__error__"]
                    continue
                for fact in result["facts"]:
                    fact["supplier_id"] = sup.id
                    fact["doc_id"] = doc.doc_id
                    facts.append(fact)
            if errors:
                self._warn(f"Fact extraction failed on {errors}/{len(tasks)} page group(s): {last_error}")
            if tasks and errors == len(tasks):
                self._warn("LLM extraction failed everywhere – switching to deterministic mode.")
                self.llm = None
                self.mode = "deterministic"
                facts = []
        if not self.llm:
            for sup in self.suppliers:
                for doc in sup.docs:
                    for fact in deterministic_facts(doc, self.lang):
                        fact["supplier_id"] = sup.id
                        fact["doc_id"] = doc.doc_id
                        facts.append(fact)
            self._progress("extract", 1, f"Deterministic extraction: {len(facts)} facts")
        facts = dedupe_facts(facts)
        facts.sort(key=lambda f: (f["supplier_id"], f["doc_id"], f["page"]))
        counters: collections.Counter[str] = collections.Counter()
        for fact in facts:
            counters[fact["supplier_id"]] += 1
            fact["id"] = f"{fact['supplier_id']}-F{counters[fact['supplier_id']]:04d}"
        self.timings["extract"] = round(time.time() - t0, 1)
        return facts

    @staticmethod
    def _usable(facts: list[dict]) -> list[dict]:
        return [f for f in facts if f["grounding"] != "unverified"]

    def coverage(self, facts: list[dict]) -> dict[str, dict[str, dict]]:
        cov: dict[str, dict[str, dict]] = {d: {} for d in DOMAIN_KEYS}
        for d in DOMAIN_KEYS:
            for sup in self.suppliers:
                fs = [f for f in facts if f["supplier_id"] == sup.id and f["domain"] == d
                      and f["grounding"] != "unverified"]
                pages = {(f["doc_id"], f["page"]) for f in fs}
                kw_pages = sum(1 for doc in sup.docs for p in doc.pages if p.domain_hits.get(d, 0) >= 2)
                cov[d][sup.id] = {
                    "facts": len(fs), "highFacts": sum(1 for f in fs if f["importance"] == "high"),
                    "pages": len(pages), "keywordPages": kw_pages,
                    "deviations": sum(1 for f in fs if f["kind"] == "deviation"),
                    "openPoints": sum(1 for f in fs if f["kind"] == "open_point"),
                    "risks": sum(1 for f in fs if f["kind"] == "risk"),
                }
        return cov

    @staticmethod
    def _fact_line(f: dict) -> str:
        variant = f" ({f['variant']})" if f.get("variant") else ""
        value = f" || {f['value']}" if f.get("value") else ""
        flag = " [approx-quote]" if f["grounding"] == "approximate" else ""
        return f"{f['id']} [{f['kind']}|{f['importance']}] p.{f['page']}{variant} {f['statement'][:280]}{value}{flag}"

    def _domain_facts(self, facts: list[dict], domain: str, sid: str, limit: int = 70) -> list[dict]:
        fs = [f for f in self._usable(facts) if f["supplier_id"] == sid and f["domain"] == domain]
        fs.sort(key=lambda f: (IMPORTANCE_RANK[f["importance"]], KIND_PRIORITY.get(f["kind"], 9), f["page"]))
        return fs[:limit]

    def _validate_cited(self, items: list[dict], allowed: set[str], text_key: str = "text") -> list[dict]:
        out = []
        for item in items or []:
            if not isinstance(item, dict) or not str(item.get(text_key) or "").strip():
                continue
            ids = [i for i in (item.get("fact_ids") or []) if i in allowed]
            clean = {k: v for k, v in item.items() if k != "fact_ids"}
            clean["fact_ids"] = ids
            clean["grounded"] = bool(ids)
            out.append(clean)
        return out

    def compare_domains(self, facts: list[dict], coverage: dict) -> dict[str, dict]:
        t0 = time.time()
        sids = [s.id for s in self.suppliers]
        domains = [d for d in DOMAIN_KEYS if any(coverage[d][s]["facts"] for s in sids)]
        results: dict[str, dict] = {}
        if self.llm:
            def run(domain):
                blocks = []
                for sup in self.suppliers:
                    fs = self._domain_facts(facts, domain, sup.id)
                    lines = "\n".join(self._fact_line(f) for f in fs) or "(no facts: topic not addressed)"
                    blocks.append(f"### Supplier {sup.id} – {sup.name}\n{lines}")
                return self.llm.chat_json(
                    system=domain_prompt(DOMAIN_BY_KEY[domain].label("en"), self.lang,
                                         self.opt.project_context, self.opt.focus),
                    user="Supplier ids: " + ", ".join(sids) + "\n\n" + "\n\n".join(blocks),
                    schema_name="tdr_domain", schema=DOMAIN_SCHEMA, max_tokens=7000)

            self._progress("compare", 0, f"Side-by-side comparison of {len(domains)} domains")
            raw = self._map_parallel(domains, run, "compare", "Domain comparison")
            for domain, data in zip(domains, raw):
                if not isinstance(data, dict) or "__error__" in data:
                    if isinstance(data, dict):
                        self._warn(f"Comparison failed for {domain}: {data['__error__']}")
                    continue
                results[domain] = self._clean_domain(domain, data, facts, coverage)
        for domain in DOMAIN_KEYS:
            if domain not in results:
                results[domain] = self._deterministic_domain(domain, coverage)
        self.timings["compare"] = round(time.time() - t0, 1)
        return results

    def _clean_domain(self, domain: str, data: dict, facts: list[dict], coverage: dict) -> dict:
        sids = [s.id for s in self.suppliers]
        allowed = {s: {f["id"] for f in self._usable(facts) if f["supplier_id"] == s} for s in sids}
        all_allowed = set().union(*allowed.values()) if allowed else set()
        assessments = {}
        for a in data.get("assessments") or []:
            sid = a.get("supplier_id")
            if sid not in allowed or sid in assessments:
                continue
            n = coverage[domain][sid]["facts"]
            score = int(a.get("score") or 0) if str(a.get("score", "")).lstrip("-").isdigit() else 0
            score = max(0, min(score, 5))
            if n == 0:
                score, cov = 0, "absent"
            else:
                score = max(score, 1)
                cov = a.get("coverage") if a.get("coverage") in {"complete", "partial", "minimal"} else "partial"
            assessments[sid] = {
                "score": score, "coverage": cov, "summary": str(a.get("summary") or "").strip(),
                "strengths": self._validate_cited(a.get("strengths"), allowed[sid]) if n else [],
                "weaknesses": self._validate_cited(a.get("weaknesses"), allowed[sid]),
                "risks": self._validate_cited(a.get("risks"), allowed[sid]) if n else [],
            }
        for sid in sids:
            if sid not in assessments:
                fallback = self._deterministic_domain(domain, coverage)["assessments"][sid]
                fallback["summary"] = fallback["summary"] or ""
                assessments[sid] = fallback
        points = []
        for p in data.get("comparison_points") or []:
            positions = []
            for pos in p.get("positions") or []:
                sid = pos.get("supplier_id")
                if sid in allowed and str(pos.get("position") or "").strip():
                    ids = [i for i in pos.get("fact_ids") or [] if i in allowed[sid]]
                    positions.append({"supplier_id": sid, "position": pos["position"].strip(),
                                      "fact_ids": ids, "grounded": bool(ids)})
            if str(p.get("aspect") or "").strip() and positions:
                points.append({"aspect": p["aspect"].strip(), "positions": positions,
                               "best_supplier_ids": [s for s in p.get("best_supplier_ids") or [] if s in allowed]})
        ranking = [s for s in dict.fromkeys(data.get("ranking") or []) if s in allowed]
        ranking += sorted([s for s in sids if s not in ranking], key=lambda s: -assessments[s]["score"])
        return {
            "domain": domain, "summary": str(data.get("summary") or "").strip(),
            "differentiators": [str(x).strip() for x in data.get("key_differentiators") or [] if str(x).strip()],
            "points": points, "assessments": assessments, "ranking": ranking, "source": "llm",
            "_all_ids": len(all_allowed),
        }

    def _deterministic_domain(self, domain: str, coverage: dict) -> dict:
        sids = [s.id for s in self.suppliers]
        counts = {s: coverage[domain][s]["facts"] + 0.5 * coverage[domain][s]["highFacts"]
                  - 0.5 * coverage[domain][s]["deviations"] for s in sids}
        top = max([c for c in counts.values()] + [0])
        assessments = {}
        for s in sids:
            n = coverage[domain][s]["facts"]
            if n == 0:
                score, cov = 0, "absent"
            else:
                score = max(1, min(5, round(1 + 4 * max(counts[s], 0) / top))) if top > 0 else 1
                cov = "complete" if score >= 4 else "partial" if score >= 2 else "minimal"
            c = coverage[domain][s]
            summary = (f"{c['facts']} faits, {c['pages']} pages, {c['deviations']} écarts, {c['openPoints']} points ouverts"
                       if self.lang == "fr" else
                       f"{c['facts']} facts, {c['pages']} pages, {c['deviations']} deviations, {c['openPoints']} open points")
            assessments[s] = {"score": score, "coverage": cov, "summary": summary,
                              "strengths": [], "weaknesses": [], "risks": []}
        ranking = sorted(sids, key=lambda s: (-assessments[s]["score"], s))
        return {"domain": domain, "summary": "", "differentiators": [], "points": [],
                "assessments": assessments, "ranking": ranking, "source": "coverage"}

    def profiles(self, facts: list[dict], domain_results: dict, totals: dict,
                 parameters: dict) -> dict[str, dict]:
        t0 = time.time()
        out: dict[str, dict] = {}
        if self.llm:
            def run(sup: Supplier):
                lines = []
                for d in DOMAIN_KEYS:
                    a = domain_results[d]["assessments"][sup.id]
                    if a["score"] == 0:
                        lines.append(f"## {d}: score 0 (not addressed)")
                        continue
                    lines.append(f"## {d}: score {a['score']}/5 ({a['coverage']}) – {a['summary']}")
                    for key in ("strengths", "weaknesses", "risks"):
                        for item in a[key]:
                            lines.append(f"- {key[:-1]}: {item['text']} {item['fact_ids']}")
                special = [f for f in self._usable(facts) if f["supplier_id"] == sup.id
                           and f["kind"] in {"deviation", "assumption", "open_point", "risk", "option"}]
                special.sort(key=lambda f: (IMPORTANCE_RANK[f["importance"]], KIND_PRIORITY.get(f["kind"], 9)))
                param_lines = [f"- {k}: " + "; ".join(v["value"] for v in vals[sup.id][:3])
                               for k, vals in parameters.items() if vals.get(sup.id)]
                user = (f"Supplier {sup.id} – {sup.name}. Weighted technical score: {totals[sup.id]['score']}/5 "
                        f"(rank {totals[sup.id]['rank']}/{len(self.suppliers)}).\n\n# Domain assessments\n"
                        + "\n".join(lines) + "\n\n# Key parameters\n" + "\n".join(param_lines[:40])
                        + "\n\n# Deviations / assumptions / open points / risks / options\n"
                        + "\n".join(self._fact_line(f) for f in special[:90]))
                return self.llm.chat_json(system=profile_prompt(self.lang, self.opt.project_context, self.opt.focus),
                                          user=user, schema_name="tdr_profile", schema=PROFILE_SCHEMA,
                                          max_tokens=5000)

            self._progress("profiles", 0, f"Supplier profiles ({len(self.suppliers)})")
            raw = self._map_parallel(self.suppliers, run, "profiles", "Profile")
            for sup, data in zip(self.suppliers, raw):
                if isinstance(data, dict) and "__error__" not in data:
                    allowed = {f["id"] for f in self._usable(facts) if f["supplier_id"] == sup.id}
                    out[sup.id] = {
                        "overview": str(data.get("overview") or "").strip(),
                        "positioning": str(data.get("technical_positioning") or "").strip(),
                        "strengths": self._validate_cited(data.get("top_strengths"), allowed),
                        "weaknesses": self._validate_cited(data.get("top_weaknesses"), allowed),
                        "risks": self._validate_cited(data.get("top_risks"), allowed),
                        "deviations": self._validate_cited(data.get("declared_deviations"), allowed),
                        "assumptions": self._validate_cited(data.get("assumptions_and_dependencies"), allowed),
                        "openPoints": self._validate_cited(data.get("open_points"), allowed),
                        "questions": [q for q in data.get("clarification_questions") or []
                                      if isinstance(q, dict) and str(q.get("question") or "").strip()],
                        "source": "llm",
                    }
                elif isinstance(data, dict):
                    self._warn(f"Profile failed for {sup.name}: {data['__error__']}")
        for sup in self.suppliers:
            if sup.id not in out:
                out[sup.id] = self._deterministic_profile(sup, facts, domain_results)
        self.timings["profiles"] = round(time.time() - t0, 1)
        return out

    def _deterministic_profile(self, sup: Supplier, facts: list[dict], domain_results: dict) -> dict:
        fr = self.lang == "fr"
        mine = [f for f in self._usable(facts) if f["supplier_id"] == sup.id]

        def cited(kind):
            return [{"text": f["statement"], "fact_ids": [f["id"]], "grounded": True}
                    for f in mine if f["kind"] == kind][:15]
        scores = {d: domain_results[d]["assessments"][sup.id]["score"] for d in DOMAIN_KEYS}
        best = sorted([d for d in scores if scores[d] > 0], key=lambda d: -scores[d])[:3]
        worst = sorted(DOMAIN_KEYS, key=lambda d: scores[d])[:3]
        questions = [{"question": (f"Merci de clarifier : {f['statement']}" if fr else f"Please clarify: {f['statement']}"),
                      "domain": f["domain"], "priority": "medium", "rationale": f"p.{f['page']}"}
                     for f in mine if f["kind"] == "open_point"][:10]
        for d in worst:
            if scores[d] == 0:
                label = DOMAIN_BY_KEY[d].label(self.lang)
                questions.append({"question": (f"Le domaine « {label} » n'est pas traité dans le dossier : merci de le détailler."
                                               if fr else f"The domain '{label}' is not covered: please detail your proposal."),
                                  "domain": d, "priority": "high", "rationale": ""})
        return {
            "overview": (f"{sup.name} : {len(mine)} faits techniques extraits de {sum(len(d.pages) for d in sup.docs)} pages."
                         if fr else f"{sup.name}: {len(mine)} technical facts extracted from "
                         f"{sum(len(d.pages) for d in sup.docs)} pages."),
            "positioning": "",
            "strengths": [{"text": DOMAIN_BY_KEY[d].label(self.lang) + f" ({scores[d]}/5)", "domain": d,
                           "fact_ids": [], "grounded": False} for d in best],
            "weaknesses": [{"text": DOMAIN_BY_KEY[d].label(self.lang) + f" ({scores[d]}/5)", "domain": d,
                            "fact_ids": [], "grounded": False} for d in worst],
            "risks": [dict(x, severity="medium") for x in cited("risk")],
            "deviations": cited("deviation"), "assumptions": cited("assumption"),
            "openPoints": cited("open_point"), "questions": questions, "source": "deterministic",
        }

    def synthesis(self, domain_results: dict, totals: dict, profiles: dict, coverage: dict) -> dict:
        t0 = time.time()
        sids = [s.id for s in self.suppliers]
        ranked = sorted(sids, key=lambda s: totals[s]["rank"])
        result = None
        if self.llm:
            lines = ["# Weighted technical scores (computed by the tool, 0-5)"]
            for s in ranked:
                lines.append(f"- {s} {self._supplier(s).name}: {totals[s]['score']} (rank {totals[s]['rank']})")
            lines.append("\n# Domain comparisons (weight, scores, summary)")
            for d in DOMAIN_KEYS:
                r = domain_results[d]
                sc = ", ".join(f"{s}={r['assessments'][s]['score']}" for s in sids)
                lines.append(f"## {DOMAIN_BY_KEY[d].label('en')} (w={self.opt.weights[d]}): {sc}\n{r['summary']}")
                for x in r["differentiators"][:4]:
                    lines.append(f"- differentiator: {x}")
            lines.append("\n# Supplier profiles")
            for s in sids:
                p = profiles[s]
                lines.append(f"## {s} {self._supplier(s).name}\n{p['overview']}\n{p['positioning']}")
                for key in ("strengths", "weaknesses", "risks", "deviations"):
                    for item in p[key][:5]:
                        lines.append(f"- {key}: {item['text']}")
            try:
                self._progress("synthesis", 0.1, "Executive synthesis")
                data = self.llm.chat_json(system=synthesis_prompt(self.lang, self.opt.project_context, self.opt.focus),
                                          user="\n".join(lines), schema_name="tdr_synthesis",
                                          schema=SYNTHESIS_SCHEMA, max_tokens=5000)
                rec = data.get("recommendation") or {}
                preferred = rec.get("preferred_supplier_id") if rec.get("preferred_supplier_id") in sids else ""
                verdicts = {v["supplier_id"]: v for v in data.get("supplier_verdicts") or []
                            if isinstance(v, dict) and v.get("supplier_id") in sids}
                result = {
                    "executiveSummary": str(data.get("executive_summary") or "").strip(),
                    "recommendation": {
                        "preferred": preferred,
                        "runnersUp": [s for s in rec.get("runner_up_supplier_ids") or [] if s in sids and s != preferred],
                        "rationale": str(rec.get("rationale") or "").strip(),
                        "conditions": [str(c) for c in rec.get("conditions") or []],
                    },
                    "verdicts": {s: {"verdict": verdicts[s].get("verdict"), "headline": verdicts[s].get("headline", "")}
                                 for s in verdicts},
                    "crossCutting": [str(x) for x in data.get("cross_cutting_findings") or []],
                    "majorRisks": [r for r in data.get("major_risks") or [] if isinstance(r, dict) and r.get("supplier_id") in sids],
                    "nextSteps": [str(x) for x in data.get("next_steps") or []],
                    "confidenceNote": str(data.get("confidence_note") or "").strip(),
                    "source": "llm",
                }
            except Exception as exc:
                self._warn(f"Executive synthesis failed: {type(exc).__name__}: {exc}")
        if result is None:
            result = self._deterministic_synthesis(ranked, totals, domain_results)
        top = ranked[0] if ranked else ""
        rec = result["recommendation"]
        rec["topScored"] = top
        rec["differsFromScore"] = bool(rec.get("preferred") and top and rec["preferred"] != top)
        for s in sids:
            result["verdicts"].setdefault(s, {"verdict": self._verdict_from_score(totals[s]["score"]), "headline": ""})
        self.timings["synthesis"] = round(time.time() - t0, 1)
        self._progress("synthesis", 1, "Synthesis ready")
        return result

    @staticmethod
    def _verdict_from_score(score: float) -> str:
        return "strong_candidate" if score >= 3.5 else "candidate_with_reservations" if score >= 2.5 else "weak_candidate"

    def _deterministic_synthesis(self, ranked: list[str], totals: dict, domain_results: dict) -> dict:
        fr = self.lang == "fr"
        names = {s.id: s.name for s in self.suppliers}
        parts = []
        for s in ranked:
            best = sorted(DOMAIN_KEYS, key=lambda d: -domain_results[d]["assessments"][s]["score"])[:2]
            labels = ", ".join(DOMAIN_BY_KEY[d].label(self.lang) for d in best)
            parts.append((f"{names[s]} : {totals[s]['score']}/5 (rang {totals[s]['rank']}), points forts : {labels}."
                          if fr else f"{names[s]}: {totals[s]['score']}/5 (rank {totals[s]['rank']}), strongest: {labels}."))
        note = ("Synthèse déterministe (sans LLM) : scores basés sur la couverture documentaire, à confirmer par une revue experte."
                if fr else "Deterministic synthesis (no LLM): scores reflect documentary coverage and must be confirmed by experts.")
        return {
            "executiveSummary": " ".join(parts),
            "recommendation": {"preferred": ranked[0] if ranked else "", "runnersUp": ranked[1:3],
                               "rationale": note, "conditions": []},
            "verdicts": {s: {"verdict": self._verdict_from_score(totals[s]["score"]), "headline": ""} for s in ranked},
            "crossCutting": [], "majorRisks": [], "nextSteps": [], "confidenceNote": note, "source": "deterministic",
        }

    def parameter_matrix(self, facts: list[dict]) -> dict[str, dict[str, list[dict]]]:
        matrix: dict[str, dict[str, list[dict]]] = {p.key: {} for p in PARAMETERS}
        for f in self._usable(facts):
            if not f.get("parameter"):
                continue
            value = f["value"] or f["statement"][:120]
            if unit_mismatch(f["parameter"], value):
                continue
            cell = matrix[f["parameter"]].setdefault(f["supplier_id"], [])
            if any(normalize_for_match(c["value"]) == normalize_for_match(value) and c.get("variant") == f["variant"]
                   for c in cell):
                continue
            cell.append({"value": value, "variant": f["variant"], "doc_id": f["doc_id"], "page": f["page"],
                         "fact_id": f["id"], "source": "llm" if self.mode == "llm" else "pattern",
                         "importance": f["importance"]})
        for sup in self.suppliers:
            for doc in sup.docs:
                for key, mentions in detect_mentions(doc).items():
                    cell = matrix[key].setdefault(sup.id, [])
                    if any(c["source"] == "llm" for c in cell):
                        continue
                    for m in mentions[:6]:
                        value = str(m["value"]).replace("\n", " ")
                        if any(normalize_for_match(c["value"]) == normalize_for_match(value) for c in cell):
                            continue
                        cell.append({"value": value, "variant": "", "doc_id": doc.doc_id, "page": m["page"],
                                     "fact_id": "", "source": "pattern", "importance": "low",
                                     "excerpt": m.get("excerpt", "")})
        context_numbers = self._context_numbers()

        def relevant(c: dict) -> bool:
            return bool(context_numbers & set(_numbers(c["value"])))

        for key in matrix:
            for sid in matrix[key]:
                # the offered product first: LLM facts, then values matching numbers of the project context
                # (e.g. 12.3in) before portfolio/reference figures, then importance
                matrix[key][sid].sort(key=lambda c: (c["source"] != "llm", not relevant(c),
                                                     IMPORTANCE_RANK.get(c["importance"], 2)))
                matrix[key][sid] = matrix[key][sid][:8]
        return {k: v for k, v in matrix.items() if any(v.values())}

    def _context_numbers(self) -> set[str]:
        return set(_numbers(f"{self.opt.project_context} {self.opt.focus}"))

    # main --------------------------------------------------------------------
    def run(self) -> dict:
        started = time.time()
        self.ingest()
        self._save_doc_texts()
        self.vision()
        self._save_doc_texts()
        facts = self.extract()
        coverage = self.coverage(facts)
        domain_results = self.compare_domains(facts, coverage)
        sids = [s.id for s in self.suppliers]
        score_matrix = {d: {s: domain_results[d]["assessments"][s]["score"] for s in sids} for d in DOMAIN_KEYS}
        totals = weighted_scores(score_matrix, self.opt.weights, sids)
        parameters = self.parameter_matrix(facts)
        profiles = self.profiles(facts, domain_results, totals, parameters)
        synthesis = self.synthesis(domain_results, totals, profiles, coverage)
        for d in domain_results.values():
            d.pop("_all_ids", None)
        grounding = collections.Counter(f["grounding"] for f in facts)
        stats = {
            "suppliers": len(self.suppliers), "documents": len(self.docs),
            "pages": sum(len(d.pages) for d in self.docs.values()),
            "visionPages": sum(1 for d in self.docs.values() for p in d.pages if p.vision_text),
            "facts": len(facts), "verifiedFacts": grounding.get("verified", 0),
            "approximateFacts": grounding.get("approximate", 0), "unverifiedFacts": grounding.get("unverified", 0),
            "durationSec": round(time.time() - started, 1), "timings": self.timings,
        }
        if self.llm is not None:
            stats.update(self.llm.stats())
        elif self.mode == "deterministic" and self.opt.use_llm:
            self.warnings.append("No LLM available: deterministic mode (coverage-based scores).")
        return {
            "engineVersion": ENGINE_VERSION, "mode": self.mode, "language": self.lang, "generatedAt": _now(),
            "options": {"language": self.lang, "vision": self.opt.vision, "visionMaxPages": self.opt.vision_max_pages,
                        "projectContext": self.opt.project_context, "focus": self.opt.focus,
                        "weights": self.opt.weights},
            "suppliers": [{
                "id": s.id, "name": s.name, "docIds": [d.doc_id for d in s.docs],
                "pages": sum(len(d.pages) for d in s.docs),
                "facts": sum(1 for f in facts if f["supplier_id"] == s.id),
                "verifiedFacts": sum(1 for f in facts if f["supplier_id"] == s.id and f["grounding"] != "unverified"),
                "deviations": sum(1 for f in facts if f["supplier_id"] == s.id and f["kind"] == "deviation"
                                  and f["grounding"] != "unverified"),
                "openPoints": sum(1 for f in facts if f["supplier_id"] == s.id and f["kind"] == "open_point"
                                  and f["grounding"] != "unverified"),
                "score": totals[s.id]["score"], "percent": totals[s.id]["percent"], "rank": totals[s.id]["rank"],
            } for s in self.suppliers],
            "documents": [{
                "id": d.doc_id, "fileName": d.file_name, "kind": d.kind, "supplierId": self.doc_supplier[d.doc_id],
                "pages": len(d.pages), "chars": d.char_count, "warnings": d.warnings,
                "visionPages": sum(1 for p in d.pages if p.vision_text),
                "commercialPagesSkipped": sum(1 for p in d.pages if p.commercial),
                "renderable": d.kind == "pdf",
            } for d in self.docs.values()],
            "domains": [{"key": d.key, "label": d.label(self.lang), "weight": self.opt.weights[d.key]} for d in DOMAINS],
            "scoreMatrix": score_matrix, "totals": totals, "coverage": coverage,
            "domainResults": domain_results, "parameters": parameters,
            "parameterLabels": {p.key: {"label": p.label(self.lang), "unit": p.unit, "better": p.better}
                                for p in PARAMETERS},
            "profiles": profiles, "synthesis": synthesis, "facts": facts,
            "factKinds": {k: v[1 if self.lang == "fr" else 0] for k, v in FACT_KINDS.items()},
            "verdictLabels": {k: v[1 if self.lang == "fr" else 0] for k, v in VERDICT_LABELS.items()},
            "stats": stats, "warnings": self.warnings,
        }

    def _save_doc_texts(self) -> None:
        target = self.job_dir / "docs"
        target.mkdir(parents=True, exist_ok=True)
        for doc in self.docs.values():
            payload = {"id": doc.doc_id, "fileName": doc.file_name, "kind": doc.kind,
                       "supplierId": self.doc_supplier.get(doc.doc_id),
                       "pages": [{"number": p.number, "text": p.text, "vision": p.vision_text,
                                  "commercial": p.commercial} for p in doc.pages]}
            (target / f"{doc.doc_id}.json").write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


# ── Q&A over the uploaded TDRs ─────────────────────────────────────────

def answer_question(job_dir: Path, result: dict, question: str, llm: LLM | None,
                    supplier_ids: list[str] | None = None, per_supplier: int = 4) -> dict:
    question = (question or "").strip()
    if not question:
        raise ValueError("Question is empty")
    lang = result.get("language", "fr")
    suppliers = [s for s in result["suppliers"] if not supplier_ids or s["id"] in supplier_ids]
    excerpts: dict[str, list[dict]] = {}
    for sup in suppliers:
        pages = []
        for doc_id in sup["docIds"]:
            path = job_dir / "docs" / f"{doc_id}.json"
            if not path.exists():
                continue
            data = json.loads(path.read_text(encoding="utf-8"))
            for p in data["pages"]:
                if p.get("commercial"):
                    continue
                text = (p["text"] + ("\n" + p["vision"] if p.get("vision") else "")).strip()
                if text:
                    pages.append((doc_id, p["number"], text))
        excerpts[sup["id"]] = [{"doc_id": d, "page": n, "text": t[:2500]}
                               for d, n, t in _bm25_rank(question, pages, per_supplier)]
    allowed = {(e["doc_id"], e["page"]) for ex in excerpts.values() for e in ex}
    names = {s["id"]: s["name"] for s in suppliers}
    if llm is not None and llm.available:
        blocks = []
        for sid, ex in excerpts.items():
            body = "\n\n".join(f"[{e['doc_id']} p.{e['page']}] {e['text']}" for e in ex) or "(no relevant page found)"
            blocks.append(f"### Supplier {sid} – {names[sid]}\n{body}")
        data = llm.chat_json(system=ask_prompt(lang), user=f"Question: {question}\n\n" + "\n\n".join(blocks),
                             schema_name="tdr_ask", schema=ASK_SCHEMA, max_tokens=3000)
        per = []
        for item in data.get("per_supplier") or []:
            sid = item.get("supplier_id")
            if sid not in names:
                continue
            cites = [c for c in item.get("citations") or []
                     if (c.get("doc_id"), int(c.get("page") or 0)) in allowed]
            per.append({"supplierId": sid, "found": bool(item.get("found")) and bool(cites),
                        "answer": str(item.get("answer") or ""),
                        "citations": [{"docId": c["doc_id"], "page": int(c["page"])} for c in cites]})
        return {"question": question, "answer": str(data.get("answer") or ""), "perSupplier": per,
                "excerpts": excerpts, "mode": "llm"}
    per = [{"supplierId": sid, "found": bool(ex), "answer": (ex[0]["text"][:600] if ex else ""),
            "citations": [{"docId": e["doc_id"], "page": e["page"]} for e in ex]} for sid, ex in excerpts.items()]
    return {"question": question, "answer": "", "perSupplier": per, "excerpts": excerpts, "mode": "search"}


# ── Job manager ────────────────────────────────────────────────────────

class BenchJobManager:
    """Runs benchmark jobs in background threads, state persisted on disk.

    ``sync`` copies that state to storage every instance can read. Without it,
    a second Azure Functions worker answers ``GET /jobs/{id}`` with Unknown job
    because the files live only on the worker that accepted the upload.
    """

    def __init__(self, base_dir: Path, llm_factory: Callable[[Path], LLM | None] | None = None,
                 max_concurrent: int = 2, sync: Any = None):
        self.base_dir = base_dir
        self.base_dir.mkdir(parents=True, exist_ok=True)
        self.llm_factory = llm_factory or (lambda cache: LLM(cache))
        self.sync = sync
        self._lock = threading.Lock()
        self._jobs: dict[str, dict] = {}
        self._cancels: dict[str, threading.Event] = {}
        self._threads: dict[str, threading.Thread] = {}
        self._slots = threading.Semaphore(max_concurrent)
        self._last_state_sync: dict[str, float] = {}
        self._recover()

    @property
    def cache_dir(self) -> Path:
        return self.base_dir / "_cache"

    def job_dir(self, job_id: str) -> Path:
        if not re.fullmatch(r"[a-f0-9]{12}", job_id or ""):
            raise KeyError(job_id)
        return self.base_dir / job_id

    def _recover(self) -> None:
        for state_file in self.base_dir.glob("*/state.json"):
            try:
                state = json.loads(state_file.read_text(encoding="utf-8"))
            except Exception:
                continue
            if state.get("status") in {"queued", "running"}:
                state["status"] = "interrupted"
                state["error"] = "Server restarted while the job was running – relaunch it."
                payload = json.dumps(state, ensure_ascii=False, indent=1)
                state_file.write_text(payload, encoding="utf-8")
                self._sync_state(state_file.parent.name, payload, force=True)

    def _persist(self, job_id: str) -> str | None:
        state = self._jobs.get(job_id)
        if state is None:
            return None
        payload = json.dumps(state, ensure_ascii=False, indent=1)
        path = self.job_dir(job_id) / "state.json"
        if not _atomic_write(path, payload):
            logger.warning("Could not persist state of job %s (file locked)", job_id)
            return None
        return payload

    def _sync_state(self, job_id: str, payload: str | None, force: bool = False) -> None:
        if self.sync is None or not payload:
            return
        now = time.monotonic()
        if not force and now - self._last_state_sync.get(job_id, 0.0) < 1.0:
            return
        try:
            self.sync.push_bytes(job_id, "state.json", payload.encode("utf-8"))
            self._last_state_sync[job_id] = now
        except Exception:
            logger.warning("Could not share state of job %s", job_id, exc_info=True)

    def _push_file(self, job_id: str, relative: str) -> None:
        if self.sync is None:
            return
        path = self.job_dir(job_id) / relative
        if not path.is_file():
            return
        try:
            self.sync.push_bytes(job_id, relative, path.read_bytes())
        except Exception:
            logger.warning("Could not share %s for job %s", relative, job_id, exc_info=True)

    def _push_tree(self, job_id: str, folder: str) -> None:
        if self.sync is None:
            return
        root = self.job_dir(job_id) / folder
        if not root.is_dir():
            return
        for path in root.rglob("*"):
            if path.is_file():
                self._push_file(job_id, path.relative_to(self.job_dir(job_id)).as_posix())

    def _publish_artifacts(self, job_id: str) -> None:
        """Upload the result and the page sources before advertising completion."""
        self._push_file(job_id, "result.json")
        self._push_tree(job_id, "docs")
        self._push_tree(job_id, "inputs")

    def _pull_file(self, job_id: str, relative: str) -> bool:
        if self.sync is None:
            return False
        dest = self.job_dir(job_id) / relative
        if dest.is_file():
            return True
        try:
            data = self.sync.read_bytes(job_id, relative)
        except Exception:
            logger.warning("Could not read shared %s for job %s", relative, job_id, exc_info=True)
            return False
        if data is None:
            return False
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(data)
        return True

    def _pull_prefix(self, job_id: str, prefix: str) -> None:
        if self.sync is None:
            return
        try:
            names = self.sync.list_relative(job_id, prefix)
        except Exception:
            logger.warning("Could not list shared %s for job %s", prefix, job_id, exc_info=True)
            return
        for relative in names:
            self._pull_file(job_id, relative)

    def _hydrate_state(self, job_id: str) -> None:
        if self.sync is None:
            return
        try:
            remote_raw = self.sync.read_bytes(job_id, "state.json")
        except Exception:
            logger.warning("Could not read shared state of job %s", job_id, exc_info=True)
            return
        if not remote_raw:
            return
        try:
            remote = json.loads(remote_raw)
        except json.JSONDecodeError:
            return
        path = self.job_dir(job_id) / "state.json"
        local = None
        if path.is_file():
            try:
                local = json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                local = None
        chosen = _prefer_shared_state(local, remote)
        if not isinstance(chosen, dict) or chosen is local:
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        _atomic_write(path, json.dumps(chosen, ensure_ascii=False, indent=1))

    def new_job_dir(self) -> tuple[str, Path]:
        job_id = uuid.uuid4().hex[:12]
        path = self.base_dir / job_id
        (path / "inputs").mkdir(parents=True, exist_ok=True)
        return job_id, path

    def submit(self, job_id: str, files: list[InputFile], options: BenchOptions, title: str = "") -> dict:
        options.normalized()
        suppliers, mapping = group_suppliers(files)
        state = {
            "id": job_id, "title": title.strip()[:200] or ", ".join(s.name for s in suppliers),
            "status": "queued", "stage": "queued", "progress": 0.0, "createdAt": _now(), "startedAt": None,
            "finishedAt": None, "error": None, "log": [],
            "suppliers": [{"id": s.id, "name": s.name} for s in suppliers],
            "files": [{"docId": f.doc_id, "fileName": f.file_name, "supplier": f.supplier,
                       "supplierId": mapping[f.doc_id], "size": f.path.stat().st_size} for f in files],
            "options": {"language": options.language, "vision": options.vision,
                        "visionMaxPages": options.vision_max_pages, "projectContext": options.project_context,
                        "focus": options.focus, "weights": options.weights, "useLlm": options.use_llm},
            "hasResult": False,
        }
        with self._lock:
            self._jobs[job_id] = state
            self._cancels[job_id] = threading.Event()
            payload = self._persist(job_id)
        # Publish the queued job before the HTTP response returns, so the next
        # poll — often a different Azure instance — already knows the id.
        self._sync_state(job_id, payload, force=True)
        thread = threading.Thread(target=self._run, args=(job_id, files, options), daemon=True,
                                  name=f"tdr-bench-{job_id}")
        self._threads[job_id] = thread
        thread.start()
        return self.public_state(job_id)

    def _update(self, job_id: str, update: dict) -> None:
        payload = None
        force = False
        with self._lock:
            state = self._jobs[job_id]
            entry = update.pop("logEntry", None)
            if entry:
                state["log"].append(entry)
                state["log"] = state["log"][-200:]
            if "progress" in update:
                update["progress"] = max(state.get("progress", 0), update["progress"])
            state.update(update)
            payload = self._persist(job_id)
            force = "status" in update or state.get("status") in _TERMINAL_STATUS
        self._sync_state(job_id, payload, force=force)

    def _run(self, job_id: str, files: list[InputFile], options: BenchOptions) -> None:
        cancel = self._cancels[job_id]
        with self._slots:
            if cancel.is_set():
                self._update(job_id, {"status": "cancelled", "finishedAt": _now()})
                return
            self._update(job_id, {"status": "running", "startedAt": _now(), "stage": "ingest"})
            job_dir = self.job_dir(job_id)
            try:
                llm = self.llm_factory(self.cache_dir) if options.use_llm else None
                run = BenchmarkRun(job_dir, files, options, llm,
                                   progress=lambda u: self._update(job_id, u), cancel=cancel)
                result = run.run()
                result["jobId"] = job_id
                result["title"] = self._jobs[job_id]["title"]
                (job_dir / "result.json").write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
                # Evidence pages and the result must be readable before any
                # instance is told the job is completed.
                self._update(job_id, {"stage": "publish",
                                      "logEntry": {"time": _now(), "stage": "publish",
                                                   "message": "Publishing the result so it can be opened"}})
                self._publish_artifacts(job_id)
                self._update(job_id, {"status": "completed", "stage": "done", "progress": 100.0,
                                      "finishedAt": _now(), "hasResult": True, "mode": result["mode"],
                                      "warnings": result["warnings"][:50], "stats": result["stats"],
                                      "logEntry": {"time": _now(), "stage": "done", "message": "Completed"}})
            except JobCancelled:
                self._update(job_id, {"status": "cancelled", "finishedAt": _now(),
                                      "logEntry": {"time": _now(), "stage": "cancelled", "message": "Cancelled"}})
            except Exception as exc:
                logger.exception("TDR benchmark job %s failed", job_id)
                self._update(job_id, {"status": "failed", "finishedAt": _now(),
                                      "error": f"{type(exc).__name__}: {exc}",
                                      "trace": traceback.format_exc()[-4000:]})

    def public_state(self, job_id: str) -> dict:
        with self._lock:
            state = self._jobs.get(job_id)
            if state is not None:
                return json.loads(json.dumps(state))
        self._hydrate_state(job_id)
        path = self.job_dir(job_id) / "state.json"
        if not path.exists():
            raise KeyError(job_id)
        for attempt in range(6):
            try:
                return json.loads(path.read_text(encoding="utf-8"))
            except (PermissionError, json.JSONDecodeError):
                if attempt == 5:
                    raise
                time.sleep(0.05 * (attempt + 1))
        raise KeyError(job_id)

    def result(self, job_id: str, raw: bool = False) -> dict:
        path = self.job_dir(job_id) / "result.json"
        if not path.exists():
            self._pull_file(job_id, "result.json")
        if not path.exists():
            raise KeyError(job_id)
        result = json.loads(path.read_text(encoding="utf-8"))
        return result if raw else apply_overrides(result, self.overrides(job_id))

    def overrides(self, job_id: str) -> dict:
        path = self.job_dir(job_id) / "overrides.json"
        if self.sync is not None:
            try:
                remote = self.sync.read_bytes(job_id, "overrides.json")
            except Exception:
                logger.warning("Could not read shared overrides of job %s", job_id, exc_info=True)
                remote = None
            if remote is not None:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(remote)
        if not path.exists():
            return {}
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return {}

    def set_overrides(self, job_id: str, raw: Any) -> dict:
        clean = sanitize_overrides(raw, self.result(job_id, raw=True))
        path = self.job_dir(job_id) / "overrides.json"
        payload = json.dumps(clean, ensure_ascii=False, indent=1)
        if clean["updatedAt"] is None:
            path.unlink(missing_ok=True)
        elif not _atomic_write(path, payload):
            raise OSError("overrides file is locked, retry")
        if self.sync is not None:
            try:
                self.sync.push_bytes(job_id, "overrides.json", payload.encode("utf-8"))
            except Exception:
                logger.warning("Could not share overrides of job %s", job_id, exc_info=True)
        return self.result(job_id)

    def list_jobs(self) -> list[dict]:
        jobs = []
        ids: list[str] = []
        seen: set[str] = set()
        for state_file in self.base_dir.glob("*/state.json"):
            job_id = state_file.parent.name
            if re.fullmatch(r"[a-f0-9]{12}", job_id) and job_id not in seen:
                seen.add(job_id)
                ids.append(job_id)
        if self.sync is not None:
            try:
                shared_ids = self.sync.list_job_ids()
            except Exception:
                logger.warning("Could not list shared benchmark jobs", exc_info=True)
                shared_ids = []
            for job_id in shared_ids:
                if job_id not in seen:
                    seen.add(job_id)
                    ids.append(job_id)
        for job_id in ids:
            try:
                state = self.public_state(job_id)
            except Exception:
                continue
            state.pop("log", None)
            state.pop("trace", None)
            jobs.append(state)
        jobs.sort(key=lambda s: s.get("createdAt") or "", reverse=True)
        return jobs

    def cancel(self, job_id: str) -> dict:
        state = self.public_state(job_id)
        event = self._cancels.get(job_id)
        if event is not None and state["status"] in {"queued", "running"}:
            event.set()
        return self.public_state(job_id)

    def delete(self, job_id: str) -> None:
        path = self.job_dir(job_id)
        if not path.exists():
            raise KeyError(job_id)
        event = self._cancels.get(job_id)
        thread = self._threads.get(job_id)
        if event is not None:
            event.set()
        if thread is not None and thread.is_alive():
            thread.join(timeout=30)
        with self._lock:
            self._jobs.pop(job_id, None)
            self._cancels.pop(job_id, None)
            self._threads.pop(job_id, None)
        shutil.rmtree(path, ignore_errors=True)
        if self.sync is not None:
            try:
                self.sync.delete_job(job_id)
            except Exception:
                logger.warning("Could not delete shared job %s", job_id, exc_info=True)

    def ensure_documents(self, job_id: str) -> Path:
        """Make extracted pages available on this instance (for citations and Q&A)."""
        self.public_state(job_id)
        self._pull_prefix(job_id, "docs/")
        return self.job_dir(job_id)

    def input_path(self, job_id: str, doc_id: str) -> Path:
        state = self.public_state(job_id)
        for f in state["files"]:
            if f["docId"] == doc_id:
                folder = self.job_dir(job_id) / "inputs"
                matches = list(folder.glob(f"{doc_id}__*")) if folder.exists() else []
                if not matches:
                    self._pull_prefix(job_id, "inputs/")
                    matches = list(folder.glob(f"{doc_id}__*")) if folder.exists() else []
                if matches:
                    return matches[0]
        raise KeyError(doc_id)

    def page_text(self, job_id: str, doc_id: str, page: int) -> dict:
        path = self.job_dir(job_id) / "docs" / f"{doc_id}.json"
        if re.fullmatch(r"D\d{1,3}", doc_id or "") and not path.exists():
            self._pull_file(job_id, f"docs/{doc_id}.json")
        if not re.fullmatch(r"D\d{1,3}", doc_id or "") or not path.exists():
            raise KeyError(doc_id)
        data = json.loads(path.read_text(encoding="utf-8"))
        for p in data["pages"]:
            if p["number"] == page:
                return {"docId": doc_id, "fileName": data["fileName"], "page": page, "text": p["text"],
                        "vision": p.get("vision", ""), "pageCount": len(data["pages"])}
        raise KeyError(page)

    def wait(self, job_id: str, timeout: float = 600) -> dict:
        thread = self._threads.get(job_id)
        if thread is not None:
            thread.join(timeout=timeout)
        return self.public_state(job_id)
