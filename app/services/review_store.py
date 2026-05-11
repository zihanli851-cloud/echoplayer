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
                CREATE TABLE IF NOT EXISTS history_bank_jobs (
                    job_id TEXT PRIMARY KEY,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    error TEXT,
                    has_result INTEGER NOT NULL DEFAULT 0,
                    result_summary_json TEXT
                )
                """
            )
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

    def list_export_history(self, session_id: str, *, limit: int = 10) -> list[dict]:
        """Return recent export records for a review session."""

        limit = max(1, min(int(limit or 10), 50))
        with sqlite3.connect(self.db_path) as connection:
            connection.row_factory = sqlite3.Row
            rows = connection.execute(
                """
                SELECT export_id, session_id, format, exported_at, file_path
                FROM export_history
                WHERE session_id = ?
                ORDER BY exported_at DESC, rowid DESC
                LIMIT ?
                """,
                (session_id, limit),
            ).fetchall()
        return [dict(row) for row in rows]

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

    def list_report_snapshots(self, *, limit: int = 50, subject: str = "", keyword: str = "") -> list[dict]:
        """Return recent report snapshots with compact summary fields for list pages."""

        limit = max(1, min(int(limit or 50), 200))
        with sqlite3.connect(self.db_path) as connection:
            connection.row_factory = sqlite3.Row
            rows = connection.execute(
                """
                SELECT
                    snapshot.session_id,
                    snapshot.payload_json,
                    snapshot.created_at AS snapshot_created_at,
                    snapshot.updated_at AS snapshot_updated_at,
                    session.teacher_id,
                    session.teacher_name,
                    session.subject,
                    session.created_at AS session_created_at
                FROM report_snapshots AS snapshot
                LEFT JOIN review_sessions AS session
                    ON session.session_id = snapshot.session_id
                ORDER BY snapshot.updated_at DESC
                """,
            ).fetchall()
            export_rows = connection.execute(
                """
                SELECT session_id, format, exported_at, file_path
                FROM export_history
                ORDER BY exported_at DESC, rowid DESC
                """,
            ).fetchall()

        summaries = [_build_report_snapshot_summary(dict(row)) for row in rows]
        _attach_export_history_summary(summaries, [dict(row) for row in export_rows])
        return _filter_report_snapshot_summaries(summaries, subject=subject, keyword=keyword)[:limit]

    def update_report_snapshot_review_status(
        self,
        *,
        session_id: str,
        item_id: str,
        match_id: str,
        status: str,
    ) -> bool:
        """Update the saved report snapshot after a manual review status change."""

        snapshot = self.get_report_snapshot(session_id)
        if snapshot is None:
            return False

        payload = snapshot.get("payload", {})
        duplicate_context = payload.get("duplicate_comparison", {}) if isinstance(payload, dict) else {}
        rows = duplicate_context.get("code_rows", []) if isinstance(duplicate_context, dict) else []
        changed = False
        for row in rows:
            if not isinstance(row, dict):
                continue
            if row.get("review_item_id") == item_id or row.get("match_id") == match_id:
                row["review_status"] = status
                row["review_item_id"] = item_id
                changed = True

        if not changed:
            return False

        self.upsert_report_snapshot(session_id, payload)
        return True

    def upsert_history_bank_job_summary(
        self,
        summary: dict,
        *,
        result_summary: dict | None = None,
    ) -> None:
        result_json = json.dumps(result_summary, ensure_ascii=False) if result_summary else None
        with sqlite3.connect(self.db_path) as connection:
            connection.execute(
                """
                INSERT INTO history_bank_jobs (
                    job_id, status, created_at, updated_at, error, has_result, result_summary_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(job_id) DO UPDATE SET
                    status = excluded.status,
                    updated_at = excluded.updated_at,
                    error = excluded.error,
                    has_result = excluded.has_result,
                    result_summary_json = excluded.result_summary_json
                """,
                (
                    str(summary["job_id"]),
                    str(summary["status"]),
                    str(summary["created_at"]),
                    str(summary["updated_at"]),
                    str(summary.get("error", "")),
                    1 if bool(summary.get("has_result")) else 0,
                    result_json,
                ),
            )
            connection.commit()

    def get_history_bank_job(self, job_id: str) -> dict | None:
        with sqlite3.connect(self.db_path) as connection:
            connection.row_factory = sqlite3.Row
            row = connection.execute(
                """
                SELECT job_id, status, created_at, updated_at, error, has_result, result_summary_json
                FROM history_bank_jobs
                WHERE job_id = ?
                """,
                (job_id,),
            ).fetchone()
        if row is None:
            return None

        payload = dict(row)
        payload["has_result"] = bool(payload["has_result"])
        result_summary_json = payload.pop("result_summary_json", None)
        if result_summary_json:
            payload["result"] = json.loads(result_summary_json)
        return payload

    def list_history_bank_jobs(self, *, limit: int = 10) -> list[dict]:
        limit = max(1, min(int(limit or 10), 50))
        with sqlite3.connect(self.db_path) as connection:
            connection.row_factory = sqlite3.Row
            rows = connection.execute(
                """
                SELECT job_id, status, created_at, updated_at, error, has_result, result_summary_json
                FROM history_bank_jobs
                ORDER BY updated_at DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()

        jobs: list[dict] = []
        for row in rows:
            payload = dict(row)
            payload["has_result"] = bool(payload["has_result"])
            result_summary_json = payload.pop("result_summary_json", None)
            if result_summary_json:
                payload["result"] = json.loads(result_summary_json)
            jobs.append(payload)
        return jobs


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _build_report_snapshot_summary(row: dict) -> dict:
    try:
        payload = json.loads(row.get("payload_json") or "{}")
    except json.JSONDecodeError:
        payload = {}

    report = payload.get("report", {}) if isinstance(payload, dict) else {}
    dashboard = report.get("dashboard", {}) if isinstance(report, dict) else {}
    duplicate_context = payload.get("duplicate_comparison", {}) if isinstance(payload, dict) else {}
    spellcheck_context = payload.get("spellcheck_comparison", {}) if isinstance(payload, dict) else {}
    duplicate_rows = duplicate_context.get("code_rows", []) if isinstance(duplicate_context, dict) else []
    spellcheck_rows = spellcheck_context.get("code_rows", []) if isinstance(spellcheck_context, dict) else []

    return {
        "session_id": row.get("session_id", ""),
        "teacher_id": row.get("teacher_id") or report.get("teacher_id", ""),
        "teacher_name": row.get("teacher_name") or report.get("teacher_name", ""),
        "subject": row.get("subject") or report.get("subject", ""),
        "session_created_at": row.get("session_created_at", ""),
        "snapshot_created_at": row.get("snapshot_created_at", ""),
        "snapshot_updated_at": row.get("snapshot_updated_at", ""),
        "paper_count": len(report.get("uploaded_papers", []) or []),
        "question_count": int(dashboard.get("paper_a_question_count", 0) or 0)
        + int(dashboard.get("paper_b_question_count", 0) or 0),
        "duplicate_count": len(duplicate_rows),
        "spellcheck_count": len(spellcheck_rows),
        "pending_review_count": len(
            [row for row in duplicate_rows if isinstance(row, dict) and row.get("review_status", "待确认") == "待确认"]
        ),
        "export_count": 0,
        "last_exported_at": "",
        "last_export_format": "",
        "last_export_file_path": "",
    }


def _attach_export_history_summary(summaries: list[dict], export_rows: list[dict]) -> None:
    summary_by_session = {str(item.get("session_id", "")): item for item in summaries}
    for row in export_rows:
        session_id = str(row.get("session_id", ""))
        summary = summary_by_session.get(session_id)
        if summary is None:
            continue
        summary["export_count"] = int(summary.get("export_count", 0)) + 1
        if not summary.get("last_exported_at"):
            summary["last_exported_at"] = row.get("exported_at", "")
            summary["last_export_format"] = row.get("format", "")
            summary["last_export_file_path"] = row.get("file_path", "")


def _filter_report_snapshot_summaries(summaries: list[dict], *, subject: str = "", keyword: str = "") -> list[dict]:
    subject = subject.strip()
    keyword = keyword.strip().lower()
    filtered = summaries
    if subject:
        filtered = [item for item in filtered if str(item.get("subject", "")) == subject]
    if keyword:
        filtered = [
            item
            for item in filtered
            if any(
                keyword in str(item.get(field, "")).lower()
                for field in ("session_id", "teacher_id", "teacher_name", "subject")
            )
        ]
    return filtered
