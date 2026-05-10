from __future__ import annotations

from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
import shutil
from threading import Lock
from typing import TYPE_CHECKING
from uuid import uuid4

from app.models.schemas import Question, SimilarityMatch, SpellcheckIssue, UploadedPaper
from app.services.dual_run import PipelineRunResult, ReviewPipeline, _agent_error_result

if TYPE_CHECKING:
    from app.services.review_store import ReviewStore


@dataclass(slots=True)
class AgentJob:
    job_id: str
    status: str
    created_at: str
    updated_at: str
    pipeline_name: str
    paper_count: int
    result: PipelineRunResult | None = None
    error: str = ""
    work_dir: str = ""
    future: Future | None = field(default=None, repr=False)

    def to_summary(self) -> dict:
        return {
            "job_id": self.job_id,
            "status": self.status,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "pipeline_name": self.pipeline_name,
            "paper_count": self.paper_count,
            "error": self.error,
            "has_result": self.result is not None,
            "work_dir": self.work_dir,
        }


TERMINAL_STATUSES = {"completed", "failed"}
DEFAULT_JOB_RETENTION_SECONDS = 7 * 24 * 60 * 60


class AgentJobStore:
    """Small in-memory job store for asynchronous Agent pipeline runs."""

    def __init__(
        self,
        job_dir: Path,
        *,
        max_workers: int = 2,
        review_store: "ReviewStore | None" = None,
        retention_seconds: int | None = DEFAULT_JOB_RETENTION_SECONDS,
    ) -> None:
        self.job_dir = job_dir
        self.job_dir.mkdir(parents=True, exist_ok=True)
        self._executor = ThreadPoolExecutor(max_workers=max_workers)
        self._jobs: dict[str, AgentJob] = {}
        self._lock = Lock()
        self.review_store = review_store
        self.retention_seconds = retention_seconds

    def submit(
        self,
        *,
        agent_pipeline: ReviewPipeline,
        uploaded_papers: list[UploadedPaper],
        history_questions: list[Question] | None = None,
        history_bank_summary: dict | None = None,
    ) -> AgentJob:
        self.cleanup_finished_jobs()
        job_id = uuid4().hex
        work_dir = self.job_dir / job_id
        work_dir.mkdir(parents=True, exist_ok=True)
        agent_papers = _copy_papers_for_job(uploaded_papers, work_dir)
        now = _now()
        job = AgentJob(
            job_id=job_id,
            status="queued",
            created_at=now,
            updated_at=now,
            pipeline_name=agent_pipeline.pipeline_name,
            paper_count=len(agent_papers),
            work_dir=str(work_dir),
        )
        with self._lock:
            self._jobs[job_id] = job
        self._persist(job)

        future = self._executor.submit(
            self._run_job,
            job_id,
            agent_pipeline,
            agent_papers,
            history_questions,
            history_bank_summary,
        )
        job.future = future
        return job

    def get(self, job_id: str) -> AgentJob | None:
        with self._lock:
            return self._jobs.get(job_id)

    def cleanup_finished_jobs(
        self,
        *,
        retention_seconds: int | None = None,
        now: datetime | None = None,
    ) -> dict:
        """Remove expired completed/failed job work directories while keeping SQLite records."""

        seconds = self.retention_seconds if retention_seconds is None else retention_seconds
        if seconds is None or seconds < 0:
            return {"removed_jobs": 0, "removed_dirs": 0, "skipped_jobs": 0}

        cutoff = (now or datetime.now()) - timedelta(seconds=seconds)
        expired_jobs: list[AgentJob] = []
        skipped_jobs = 0
        with self._lock:
            for job in list(self._jobs.values()):
                if job.status not in TERMINAL_STATUSES:
                    skipped_jobs += 1
                    continue
                if _parse_timestamp(job.updated_at) > cutoff:
                    continue
                expired_jobs.append(job)
                self._jobs.pop(job.job_id, None)

        removed_dirs = 0
        for job in expired_jobs:
            if _remove_job_work_dir(job.work_dir, self.job_dir):
                removed_dirs += 1
        return {
            "removed_jobs": len(expired_jobs),
            "removed_dirs": removed_dirs,
            "skipped_jobs": skipped_jobs,
        }

    def _run_job(
        self,
        job_id: str,
        agent_pipeline: ReviewPipeline,
        agent_papers: list[UploadedPaper],
        history_questions: list[Question] | None,
        history_bank_summary: dict | None,
    ) -> None:
        self._update(job_id, status="running")
        try:
            result = agent_pipeline.run(
                agent_papers,
                history_questions=history_questions,
                history_bank_summary=history_bank_summary,
            )
        except Exception as exc:
            message = f"Agent 后台链路运行失败：{exc}"
            result = _agent_error_result(
                agent_pipeline.pipeline_name,
                agent_papers,
                history_bank_summary=history_bank_summary,
                message=message,
            )
            self._update(job_id, status="failed", result=result, error=message)
            return

        self._update(job_id, status="completed", result=result)

    def _update(
        self,
        job_id: str,
        *,
        status: str,
        result: PipelineRunResult | None = None,
        error: str = "",
    ) -> None:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                return
            job.status = status
            job.updated_at = _now()
            if result is not None:
                job.result = result
            if error:
                job.error = error
            self._persist(job)

    def _persist(self, job: AgentJob) -> None:
        if self.review_store is None:
            return
        try:
            self.review_store.upsert_agent_job_summary(
                job.to_summary(),
                result_summary=_pipeline_result_summary(job.result) if job.result is not None else None,
                result_payload=pipeline_result_payload(job.result) if job.result is not None else None,
            )
        except Exception:
            # Job persistence is a recoverability aid; it should not break background execution.
            return


def pipeline_result_summary(result: PipelineRunResult) -> dict:
    return _pipeline_result_summary(result)


def pipeline_result_payload(result: PipelineRunResult) -> dict:
    return {
        "pipeline_name": result.pipeline_name,
        "uploaded_papers": [_dump_model(paper) for paper in result.uploaded_papers],
        "questions": [_dump_model(question) for question in result.questions],
        "similarity_matches": [_dump_model(match) for match in result.similarity_matches],
        "spellcheck_issues": [_dump_model(issue) for issue in result.spellcheck_issues],
        "module_metadata": result.module_metadata,
        "history_bank_summary": result.history_bank_summary,
    }


def _pipeline_result_summary(result: PipelineRunResult) -> dict:
    return {
        "pipeline_name": result.pipeline_name,
        "question_count": len(result.questions),
        "duplicate_count": len(result.similarity_matches),
        "spellcheck_count": len(result.spellcheck_issues),
        "module_metadata": result.module_metadata,
    }


def pipeline_result_from_payload(payload: dict) -> PipelineRunResult:
    return PipelineRunResult(
        pipeline_name=str(payload.get("pipeline_name", "")),
        uploaded_papers=[UploadedPaper(**item) for item in payload.get("uploaded_papers", [])],
        questions=[Question(**item) for item in payload.get("questions", [])],
        similarity_matches=[SimilarityMatch(**item) for item in payload.get("similarity_matches", [])],
        spellcheck_issues=[SpellcheckIssue(**item) for item in payload.get("spellcheck_issues", [])],
        module_metadata=dict(payload.get("module_metadata", {})),
        history_bank_summary=dict(payload.get("history_bank_summary", {})),
    )


def _dump_model(model) -> dict:
    if hasattr(model, "model_dump"):
        return model.model_dump(mode="json")
    return dict(model)


def build_agent_pending_result(
    pipeline_name: str,
    uploaded_papers: list[UploadedPaper],
    *,
    history_bank_summary: dict | None,
    job_id: str,
) -> PipelineRunResult:
    message = f"Agent 链路已转入后台运行，任务 ID：{job_id}。"
    return _agent_error_result(
        pipeline_name,
        uploaded_papers,
        history_bank_summary=history_bank_summary,
        message=message,
    )


def _copy_papers_for_job(uploaded_papers: list[UploadedPaper], work_dir: Path) -> list[UploadedPaper]:
    copied: list[UploadedPaper] = []
    for paper in uploaded_papers:
        source = Path(paper.temp_path)
        suffix = source.suffix or ".pdf"
        destination = work_dir / f"{paper.paper_id}{suffix}"
        shutil.copy2(source, destination)
        copied.append(paper.model_copy(update={"temp_path": str(destination)}, deep=True))
    return copied


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _parse_timestamp(value: str) -> datetime:
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return datetime.min


def _remove_job_work_dir(work_dir: str, job_dir: Path) -> bool:
    if not work_dir:
        return False
    root = job_dir.resolve()
    target = Path(work_dir).resolve()
    if target == root or root not in target.parents:
        return False
    if not target.exists() or not target.is_dir():
        return False
    try:
        shutil.rmtree(target)
    except OSError:
        return False
    return True
