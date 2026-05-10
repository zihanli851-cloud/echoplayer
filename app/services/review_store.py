from __future__ import annotations

import sqlite3
import json
from datetime import datetime
from pathlib import Path
from uuid import uuid4

from app.models.schemas import SimilarityMatch


REVIEW_STATUS_OPTIONS = {"待确认", "确认重复", "排除误报"}


class ReviewStore:
    """SQLite persistence for report review sessions and item statuses."""

    def __init__(self, db_path: Path | str) -> None:
        self.db_path = Path(db_path)
        self.initialize()

    def initialize(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.db_path) as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS review_sessions (
                    session_id TEXT PRIMARY KEY,
                    teacher_id TEXT NOT NULL,
                    teacher_name TEXT NOT NULL,
                    subject TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    paper_a_path TEXT,
                    paper_b_path TEXT
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS review_items (
                    item_id TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL,
                    match_id TEXT NOT NULL,
                    question_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY(session_id) REFERENCES review_sessions(session_id)
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS export_history (
                    export_id TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL,
                    format TEXT NOT NULL,
                    exported_at TEXT NOT NULL,
                    file_path TEXT,
                    FOREIGN KEY(session_id) REFERENCES review_sessions(session_id)
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS report_snapshots (
                    session_id TEXT PRIMARY KEY,
                    payload_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY(session_id) REFERENCES review_sessions(session_id)
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS agent_jobs (
                    job_id TEXT PRIMARY KEY,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    pipeline_name TEXT NOT NULL,
                    paper_count INTEGER NOT NULL,
                    error TEXT,
                    work_dir TEXT,
                    has_result INTEGER NOT NULL DEFAULT 0,
                    result_summary_json TEXT,
                    result_payload_json TEXT
                )
                """
            )
            _ensure_column(connection, "agent_jobs", "result_payload_json", "TEXT")
            connection.commit()

    def create_session(
        self,
        *,
        teacher_id: str,
        teacher_name: str,
        subject: str,
        paper_a_path: str | None = None,
        paper_b_path: str | None = None,
    ) -> str:
        session_id = uuid4().hex
        now = _now()
        with sqlite3.connect(self.db_path) as connection:
            connection.execute(
                """
                INSERT INTO review_sessions (
                    session_id, teacher_id, teacher_name, subject, created_at, paper_a_path, paper_b_path
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (session_id, teacher_id, teacher_name, subject, now, paper_a_path, paper_b_path),
            )
            connection.commit()
        return session_id

    def create_items(self, session_id: str, matches: list[SimilarityMatch]) -> dict[str, str]:
        """Persist duplicate review items and return a match_id -> item_id map."""

        now = _now()
        item_ids: dict[str, str] = {}
        rows: list[tuple[str, str, str, str, str, str]] = []
        for match in matches:
            item_id = uuid4().hex
            item_ids[match.match_id] = item_id
            rows.append(
                (
                    item_id,
                    session_id,
                    match.match_id,
                    match.source_question_id,
                    match.review_status or "待确认",
                    now,
                )
            )

        if not rows:
            return item_ids

        with sqlite3.connect(self.db_path) as connection:
            connection.executemany(
                """
                INSERT INTO review_items (
                    item_id, session_id, match_id, question_id, status, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                rows,
            )
            connection.commit()
        return item_ids

    def update_item_status(self, item_id: str, status: str) -> dict:
        status = status.strip()
        if status not in REVIEW_STATUS_OPTIONS:
            raise ValueError("无效的复核状态。")

        now = _now()
        with sqlite3.connect(self.db_path) as connection:
            cursor = connection.execute(
                """
                UPDATE review_items
                SET status = ?, updated_at = ?
                WHERE item_id = ?
                """,
                (status, now, item_id),
            )
            connection.commit()
            if cursor.rowcount == 0:
                raise KeyError("复核项不存在。")
        return {"item_id": item_id, "status": status, "updated_at": now}

    def get_item(self, item_id: str) -> dict | None:
        with sqlite3.connect(self.db_path) as connection:
            connection.row_factory = sqlite3.Row
            row = connection.execute(
                """
                SELECT item_id, session_id, match_id, question_id, status, updated_at
                FROM review_items
                WHERE item_id = ?
                """,
                (item_id,),
            ).fetchone()
        return dict(row) if row else None

    def record_export(
        self,
        *,
        session_id: str,
        export_format: str,
        file_path: str | None = None,
    ) -> str:
        export_id = uuid4().hex
        now = _now()
        with sqlite3.connect(self.db_path) as connection:
            connection.execute(
                """
                INSERT INTO export_history (
                    export_id, session_id, format, exported_at, file_path
                )
                VALUES (?, ?, ?, ?, ?)
                """,
                (export_id, session_id, export_format, now, file_path),
            )
            connection.commit()
        return export_id

    def upsert_report_snapshot(self, session_id: str, payload: dict) -> None:
        """Persist the current report export payload for later session restore."""

        now = _now()
        payload_json = json.dumps(payload, ensure_ascii=False)
        with sqlite3.connect(self.db_path) as connection:
            connection.execute(
                """
                INSERT INTO report_snapshots (
                    session_id, payload_json, created_at, updated_at
                )
                VALUES (?, ?, ?, ?)
                ON CONFLICT(session_id) DO UPDATE SET
                    payload_json = excluded.payload_json,
                    updated_at = excluded.updated_at
                """,
                (session_id, payload_json, now, now),
            )
            connection.commit()

    def get_report_snapshot(self, session_id: str) -> dict | None:
        with sqlite3.connect(self.db_path) as connection:
            connection.row_factory = sqlite3.Row
            row = connection.execute(
                """
                SELECT session_id, payload_json, created_at, updated_at
                FROM report_snapshots
                WHERE session_id = ?
                """,
                (session_id,),
            ).fetchone()
        if row is None:
            return None

        payload = dict(row)
        payload["payload"] = json.loads(payload.pop("payload_json"))
        return payload

    def upsert_agent_job_summary(
        self,
        summary: dict,
        *,
        result_summary: dict | None = None,
        result_payload: dict | None = None,
    ) -> None:
        """Persist an Agent background job summary for lookup after process memory is lost."""

        result_json = json.dumps(result_summary, ensure_ascii=False) if result_summary else None
        payload_json = json.dumps(result_payload, ensure_ascii=False) if result_payload else None
        with sqlite3.connect(self.db_path) as connection:
            connection.execute(
                """
                INSERT INTO agent_jobs (
                    job_id, status, created_at, updated_at, pipeline_name, paper_count,
                    error, work_dir, has_result, result_summary_json, result_payload_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(job_id) DO UPDATE SET
                    status = excluded.status,
                    updated_at = excluded.updated_at,
                    pipeline_name = excluded.pipeline_name,
                    paper_count = excluded.paper_count,
                    error = excluded.error,
                    work_dir = excluded.work_dir,
                    has_result = excluded.has_result,
                    result_summary_json = excluded.result_summary_json,
                    result_payload_json = excluded.result_payload_json
                """,
                (
                    str(summary["job_id"]),
                    str(summary["status"]),
                    str(summary["created_at"]),
                    str(summary["updated_at"]),
                    str(summary["pipeline_name"]),
                    int(summary.get("paper_count", 0)),
                    str(summary.get("error", "")),
                    str(summary.get("work_dir", "")),
                    1 if bool(summary.get("has_result")) else 0,
                    result_json,
                    payload_json,
                ),
            )
            connection.commit()

    def get_agent_job(self, job_id: str) -> dict | None:
        with sqlite3.connect(self.db_path) as connection:
            connection.row_factory = sqlite3.Row
            row = connection.execute(
                """
                SELECT job_id, status, created_at, updated_at, pipeline_name, paper_count,
                       error, work_dir, has_result, result_summary_json, result_payload_json
                FROM agent_jobs
                WHERE job_id = ?
                """,
                (job_id,),
            ).fetchone()
        if row is None:
            return None

        payload = dict(row)
        payload["paper_count"] = int(payload["paper_count"])
        payload["has_result"] = bool(payload["has_result"])
        result_summary_json = payload.pop("result_summary_json", None)
        if result_summary_json:
            payload["result"] = json.loads(result_summary_json)
        result_payload_json = payload.pop("result_payload_json", None)
        if result_payload_json:
            payload["result_payload"] = json.loads(result_payload_json)
        return payload


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _ensure_column(connection: sqlite3.Connection, table_name: str, column_name: str, column_type: str) -> None:
    columns = {row[1] for row in connection.execute(f"PRAGMA table_info({table_name})").fetchall()}
    if column_name not in columns:
        connection.execute(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {column_type}")
