from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from concurrent.futures import Future, ThreadPoolExecutor
from threading import Lock
from uuid import uuid4

from app.services.history_bank import HistoryBankService
from app.services.review_store import ReviewStore


@dataclass
class HistoryBankRefreshJob:
    """In-memory status for a background history bank refresh."""

    job_id: str
    status: str
    created_at: str
    updated_at: str
    error: str = ""
    result: dict | None = None
    future: Future | None = field(default=None, repr=False)

    def to_summary(self) -> dict:
        payload = {
            "job_id": self.job_id,
            "status": self.status,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "error": self.error,
            "has_result": self.result is not None,
        }
        if self.result is not None:
            payload["result"] = self.result
        return payload


class HistoryBankRefreshJobStore:
    """Run history bank parsing and lightweight index rebuilds in the background."""

    def __init__(self, *, max_workers: int = 1, review_store: ReviewStore | None = None) -> None:
        self._executor = ThreadPoolExecutor(max_workers=max_workers)
        self._jobs: dict[str, HistoryBankRefreshJob] = {}
        self._lock = Lock()
        self.review_store = review_store

    def submit(self, history_bank_service: HistoryBankService) -> HistoryBankRefreshJob:
        now = _now()
        job = HistoryBankRefreshJob(
            job_id=uuid4().hex,
            status="queued",
            created_at=now,
            updated_at=now,
        )
        with self._lock:
            self._jobs[job.job_id] = job
        self._persist(job)

        job.future = self._executor.submit(self._run_job, job.job_id, history_bank_service)
        return job

    def get(self, job_id: str) -> HistoryBankRefreshJob | None:
        with self._lock:
            return self._jobs.get(job_id)

    def _run_job(self, job_id: str, history_bank_service: HistoryBankService) -> None:
        self._update(job_id, status="running")
        try:
            snapshot = history_bank_service.get_snapshot(force_refresh=True)
        except Exception as exc:
            self._update(job_id, status="failed", error=str(exc))
            return

        summary = snapshot.to_summary()
        summary["subjects"] = sorted(
            {
                str(paper.get("subject", "")).strip()
                for paper in summary.get("papers", [])
                if str(paper.get("subject", "")).strip()
            }
        )
        self._update(job_id, status="completed", result=summary)

    def _update(
        self,
        job_id: str,
        *,
        status: str,
        error: str = "",
        result: dict | None = None,
    ) -> None:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                return
            job.status = status
            job.updated_at = _now()
            job.error = error
            if result is not None:
                job.result = result
            self._persist(job)

    def _persist(self, job: HistoryBankRefreshJob) -> None:
        if self.review_store is None:
            return
        try:
            self.review_store.upsert_history_bank_job_summary(
                job.to_summary(),
                result_summary=job.result,
            )
        except Exception:
            # Job status persistence should not break the background refresh.
            pass


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")
