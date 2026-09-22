"""
Quality metrics tracking for the Spec Q&A Assistant.

Records every Q&A interaction to compute the 4 mandatory enterprise metrics:
- Grounding Rate: % of answers supported by at least one source
- Faithfulness Score: answer is grounded (no hallucination indicators)
- Relevance Score: answer addresses the question (not a refusal when support exists)
- Not-Found Accuracy: system correctly says "not found" when needed

All metrics are computed from actual request/response pairs, not simulated.
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import List, Optional


@dataclass
class QaRecord:
    """A single Q&A interaction for metrics computation."""
    question: str
    answer: str
    confidence: str           # HIGH | MEDIUM | LOW | ""
    source_count: int         # number of sources cited
    was_not_found: bool       # answer was a "not found" refusal
    was_refusal: bool         # answer was a standards refusal
    had_sources: bool         # at least 1 source returned
    timestamp: float = field(default_factory=time.time)


class MetricsStore:
    """Thread-safe store of Q&A records for metrics computation."""

    def __init__(self) -> None:
        self._records: List[QaRecord] = []
        self._lock = threading.Lock()

    def record(self, rec: QaRecord) -> None:
        with self._lock:
            self._records.append(rec)
            # Keep last 1000 records to bound memory
            if len(self._records) > 1000:
                self._records = self._records[-1000:]

    def clear(self) -> None:
        with self._lock:
            self._records = []

    def compute(self) -> dict:
        """Compute the 4 mandatory enterprise quality metrics."""
        with self._lock:
            records = list(self._records)

        total = len(records)
        if total == 0:
            return {
                "totalQuestions": 0,
                "groundingRate": None,
                "faithfulnessScore": None,
                "relevanceScore": None,
                "notFoundAccuracy": None,
                "breakdown": {
                    "answered": 0,
                    "notFound": 0,
                    "refusals": 0,
                    "withSources": 0,
                    "withConfidence": 0,
                },
            }

        answered = [r for r in records if not r.was_not_found and not r.was_refusal]
        not_found = [r for r in records if r.was_not_found]
        refusals = [r for r in records if r.was_refusal]
        with_sources = [r for r in answered if r.had_sources]
        with_confidence = [r for r in answered if r.confidence]

        # 1. Grounding Rate: % of answered questions that had ≥1 source
        # (not-found and refusals are excluded — they correctly have no sources)
        grounding_rate = (len(with_sources) / len(answered)) if answered else None

        # 2. Faithfulness Score: % of answered questions with confidence
        # (confidence is only assigned when the answer is grounded in evidence;
        #  a missing confidence on an "answered" question signals a problem)
        faithfulness = (len(with_confidence) / len(answered)) if answered else None

        # 3. Relevance Score: % of questions that got a substantive answer
        # (answered / total, excluding refusals which are correct behavior
        #  for out-of-scope questions but not "relevant" to the user's goal)
        relevance = (len(answered) / total) if total else None

        # 4. Not-Found Accuracy: when the system says "not found", did it
        # correctly have 0 sources? (a "not found" WITH sources would be a bug)
        nf_correct = sum(1 for r in not_found if not r.had_sources)
        not_found_accuracy = (nf_correct / len(not_found)) if not_found else None

        return {
            "totalQuestions": total,
            "groundingRate": round(grounding_rate, 4) if grounding_rate is not None else None,
            "faithfulnessScore": round(faithfulness, 4) if faithfulness is not None else None,
            "relevanceScore": round(relevance, 4) if relevance is not None else None,
            "notFoundAccuracy": round(not_found_accuracy, 4) if not_found_accuracy is not None else None,
            "breakdown": {
                "answered": len(answered),
                "notFound": len(not_found),
                "refusals": len(refusals),
                "withSources": len(with_sources),
                "withConfidence": len(with_confidence),
            },
        }


# Singleton store
metrics_store = MetricsStore()
