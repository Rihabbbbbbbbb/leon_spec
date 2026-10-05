"""SQLite local-MVP cases and append-only decisions with optimistic concurrency."""
from collections import Counter
from contextlib import contextmanager
import json
import logging
import os
from pathlib import Path
import sqlite3
from typing import Generator
from uuid import uuid4

from app.qa.tdr_review import utc_now
from app.qa.tdr_review_models import ProposalStatus, ReviewDecision


logger = logging.getLogger(__name__)


class ReviewConflict(ValueError):
    pass


class ReviewStore:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as connection:
            connection.executescript("""
                CREATE TABLE IF NOT EXISTS cases (
                    id TEXT PRIMARY KEY, fingerprint TEXT UNIQUE NOT NULL,
                    created_at TEXT NOT NULL, payload TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS decisions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    case_id TEXT NOT NULL REFERENCES cases(id) ON DELETE CASCADE,
                    row_key TEXT NOT NULL, revision INTEGER NOT NULL,
                    recorded_at TEXT NOT NULL, decision TEXT NOT NULL,
                    UNIQUE(case_id, row_key, revision)
                );
                CREATE TABLE IF NOT EXISTS deletion_events (
                    case_id TEXT PRIMARY KEY, deleted_at TEXT NOT NULL,
                    reason TEXT NOT NULL
                );
            """)

    @contextmanager
    def connect(self) -> Generator[sqlite3.Connection, None, None]:
        connection = sqlite3.connect(self.path, timeout=30)
        connection.row_factory = sqlite3.Row
        try:
            connection.execute("PRAGMA foreign_keys = ON")
            with connection:
                yield connection
        finally:
            connection.close()

    def create(self, fingerprint: str, payload: dict) -> tuple[dict, bool]:
        case_id = str(uuid4())
        payload = {**payload, "caseId": case_id}
        encoded = json.dumps(payload, ensure_ascii=False, allow_nan=False)
        with self.connect() as connection:
            connection.execute(
                "INSERT OR IGNORE INTO cases VALUES (?, ?, ?, ?)",
                (case_id, fingerprint, payload["createdAt"], encoded),
            )
            saved = connection.execute(
                "SELECT id FROM cases WHERE fingerprint = ?", (fingerprint,),
            ).fetchone()
        if saved is None:
            raise RuntimeError("Case insert did not persist")
        reused = saved["id"] != case_id
        logger.info("tdr_case_saved", extra={"case_id": saved["id"], "reused": reused})
        return self.get(saved["id"]), reused

    def get(self, case_id: str) -> dict:
        with self.connect() as connection:
            case = connection.execute("SELECT payload FROM cases WHERE id = ?", (case_id,)).fetchone()
            if case is None:
                raise KeyError(case_id)
            decisions = connection.execute(
                "SELECT row_key, decision FROM decisions WHERE case_id = ? ORDER BY id", (case_id,),
            ).fetchall()
        payload = json.loads(case["payload"])
        histories: dict[str, list] = {}
        for decision in decisions:
            histories.setdefault(decision["row_key"], []).append(json.loads(decision["decision"]))
        for item in payload["items"]:
            history = histories.get(item["row_key"], [])
            item["review_history"] = history
            item["human_decision"] = history[-1] if history else None
            item["review_revision"] = history[-1]["revision"] if history else 0
        payload["reviewedCount"] = sum(
            bool(i["human_decision"]) for i in payload["items"]
        )
        payload["humanStatusSummary"] = dict(Counter(
            i["human_decision"]["result_status"] if i["human_decision"] else "NOT_REVIEWED"
            for i in payload["items"]
        ))
        return payload

    def list_cases(self, limit: int = 25, offset: int = 0) -> dict:
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT id, created_at, json_extract(payload, '$.matrixFile') AS matrix_file "
                "FROM cases ORDER BY created_at DESC, id DESC LIMIT ? OFFSET ?",
                (limit, offset),
            ).fetchall()
            total = connection.execute("SELECT COUNT(*) FROM cases").fetchone()[0]
        cases = [
            {"caseId": row["id"], "createdAt": row["created_at"],
             "matrixFile": row["matrix_file"]}
            for row in rows
        ]
        return {"cases": cases, "total": total, "offset": offset, "hasMore": offset + len(cases) < total}

    def decide(self, case_id: str, row_key: str, decision: ReviewDecision) -> dict:
        with self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute("SELECT payload FROM cases WHERE id = ?", (case_id,)).fetchone()
            if row is None:
                raise KeyError(case_id)
            payload = json.loads(row["payload"])
            item = next((i for i in payload["items"] if i["row_key"] == row_key), None)
            if item is None:
                raise KeyError(row_key)
            current = connection.execute(
                "SELECT COALESCE(MAX(revision), 0) FROM decisions WHERE case_id = ? AND row_key = ?",
                (case_id, row_key),
            ).fetchone()[0]
            if decision.expected_revision != current:
                raise ReviewConflict("Review changed since it was loaded; reload the case before saving")
            if decision.action == "VALIDATE" and item["proposal_status"] in (
                ProposalStatus.MAUVAIS_PERIMETRE.value,
                ProposalStatus.ERREUR_EXTRACTION.value,
                ProposalStatus.ANALYSE_MANUELLE_REQUISE.value,
                ProposalStatus.NON_APPLICABLE_A_JUSTIFIER.value,
            ):
                raise ReviewConflict("This proposal requires CORRECT, REQUEST_EVIDENCE or a justified applicability decision")
            result_status = {
                "VALIDATE": item["proposal_status"],
                "CORRECT": decision.corrected_status.value if decision.corrected_status else "",
                "REJECT": "REJECTED_PROPOSAL",
                "REQUEST_EVIDENCE": ProposalStatus.REPONSE_SANS_PREUVE.value,
                "MARK_NOT_APPLICABLE": "HUMAN_NOT_APPLICABLE",
            }[decision.action]
            if result_status == ProposalStatus.CONFORME_AVEC_PREUVE.value and (
                item["scope_status"] in ("INCOMPATIBLE", "INSUFFICIENT_INFORMATION")
                or item["proposal_status"] == ProposalStatus.ERREUR_EXTRACTION.value
            ):
                raise ReviewConflict("Resolve scope/extraction and reanalyze before recording evidence-backed conformity")
            recorded = {
                **decision.model_dump(mode="json"), "revision": current + 1,
                "recordedAt": utc_now(), "original_proposal": item["proposal_status"],
                "result_status": result_status, "reviewerIdentityVerified": False,
                "technicalAcceptance": False,
            }
            connection.execute(
                "INSERT INTO decisions (case_id, row_key, revision, recorded_at, decision) VALUES (?, ?, ?, ?, ?)",
                (case_id, row_key, current + 1, recorded["recordedAt"],
                 json.dumps(recorded, ensure_ascii=False, allow_nan=False)),
            )
        logger.info("tdr_review_recorded", extra={
            "case_id": case_id, "row_key": row_key, "revision": current + 1, "action": decision.action,
        })
        return self.get(case_id)

    def delete(self, case_id: str) -> None:
        with self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            deleted = connection.execute("DELETE FROM cases WHERE id = ?", (case_id,))
            if not deleted.rowcount:
                raise KeyError(case_id)
            connection.execute(
                "INSERT INTO deletion_events VALUES (?, ?, ?)",
                (case_id, utc_now(), "User-confirmed local case deletion"),
            )
        logger.info("tdr_case_deleted", extra={"case_id": case_id})


def review_store() -> ReviewStore:
    default = Path(__file__).resolve().parents[2] / "data" / "tdr_review" / "reviews.sqlite3"
    return ReviewStore(os.getenv("TDR_REVIEW_DB", str(default)))
