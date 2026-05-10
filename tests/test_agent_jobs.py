from pathlib import Path
import re
import sqlite3
from datetime import datetime, timedelta

from fastapi.testclient import TestClient

from app.main import app
from app.models.schemas import Question, UploadedPaper
from app.services.agent_jobs import AgentJob, AgentJobStore, build_agent_pending_result, _remove_job_work_dir
from app.services.comparator import SimilarityComparatorProvider
from app.services.dual_run import PipelineRunResult, ReviewPipeline
from app.services.pdf_parser import TextExtractionProvider
from app.services.question_splitter import QuestionSplitProvider
from app.services.review_store import ReviewStore
from app.services.spellcheck.base import SpellcheckProvider


class FileReadingExtractionProvider(TextExtractionProvider):
    provider_name = "file_reading_extract"
    provider_label = "File Reading Extract"

    def extract(self, pdf_path: Path) -> tuple[str, int]:
        return pdf_path.read_text(encoding="utf-8"), 1


class OneQuestionSplitProvider(QuestionSplitProvider):
    provider_name = "one_question_split"
    provider_label = "One Question Split"

    def split(self, text: str, paper_id: str, *, paper=None) -> list[Question]:
        return [
            Question(
                question_id=f"{paper_id}-1",
                paper_id=paper_id,
                question_no="1",
                order=1,
                content=text,
                raw_block=text,
            )
        ]


class EmptyCompareProvider(SimilarityComparatorProvider):
    provider_name = "empty_compare"
    provider_label = "Empty Compare"

    def compare(self, paper_a_questions, paper_b_questions=None, history_questions=None, *, uploaded_papers=None):
        return []


class EmptySpellcheckProvider(SpellcheckProvider):
    provider_name = "empty_spell"
    provider_label = "Empty Spell"

    def check_questions(self, paper, questions):
        return []


def build_pipeline() -> ReviewPipeline:
    return ReviewPipeline(
        pipeline_name="Agent 版",
        extraction_provider=FileReadingExtractionProvider(),
        split_provider=OneQuestionSplitProvider(),
        compare_provider=EmptyCompareProvider(),
        spellcheck_provider=EmptySpellcheckProvider(),
    )


def test_agent_job_copies_uploads_before_original_temp_file_is_removed(tmp_path) -> None:
    source_pdf = tmp_path / "source.pdf"
    source_pdf.write_text("1. 后台 Agent 题目", encoding="utf-8")
    store = AgentJobStore(tmp_path / "jobs", max_workers=1)

    job = store.submit(
        agent_pipeline=build_pipeline(),
        uploaded_papers=[
            UploadedPaper(
                paper_id="A",
                filename="source.pdf",
                subject="chinese",
                temp_path=str(source_pdf),
            )
        ],
    )
    source_pdf.unlink()
    job.future.result(timeout=5)

    completed = store.get(job.job_id)
    assert completed is not None
    assert completed.status == "completed"
    assert completed.result is not None
    assert completed.result.questions[0].content == "1. 后台 Agent 题目"
    assert Path(completed.result.uploaded_papers[0].temp_path).exists()
    assert Path(completed.result.uploaded_papers[0].temp_path).parent.name == job.job_id


def test_agent_pending_result_includes_job_id() -> None:
    result = build_agent_pending_result(
        "Agent 版",
        [],
        history_bank_summary={"loaded_files": 1},
        job_id="job-1",
    )

    assert result.history_bank_summary["loaded_files"] == 1
    assert result.module_metadata["split"]["is_placeholder"] is True
    assert "job-1" in result.module_metadata["split"]["provider_note"]


def test_agent_job_status_route_returns_completed_summary(tmp_path) -> None:
    source_pdf = tmp_path / "source.pdf"
    source_pdf.write_text("1. 后台 Agent 题目", encoding="utf-8")
    store = AgentJobStore(tmp_path / "jobs", max_workers=1)
    job = store.submit(
        agent_pipeline=build_pipeline(),
        uploaded_papers=[
            UploadedPaper(
                paper_id="A",
                filename="source.pdf",
                subject="chinese",
                temp_path=str(source_pdf),
            )
        ],
    )
    job.future.result(timeout=5)

    with TestClient(app) as client:
        client.app.state.agent_job_store = store
        response = client.get(f"/api/agent-jobs/{job.job_id}")

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "completed"
    assert payload["result"]["question_count"] == 1
    assert payload["result"]["duplicate_count"] == 0
    assert payload["result_payload"]["questions"][0]["content"] == "1. 后台 Agent 题目"


def test_agent_job_cleanup_removes_only_expired_finished_work_dirs(tmp_path) -> None:
    source_pdf = tmp_path / "source.pdf"
    source_pdf.write_text("1. 后台 Agent 题目", encoding="utf-8")
    review_store = ReviewStore(tmp_path / "echopaper.db")
    store = AgentJobStore(tmp_path / "jobs", max_workers=1, review_store=review_store, retention_seconds=60)
    completed_job = store.submit(
        agent_pipeline=build_pipeline(),
        uploaded_papers=[
            UploadedPaper(
                paper_id="A",
                filename="source.pdf",
                subject="chinese",
                temp_path=str(source_pdf),
            )
        ],
    )
    completed_job.future.result(timeout=5)
    completed = store.get(completed_job.job_id)
    assert completed is not None
    completed.updated_at = (datetime.now() - timedelta(minutes=5)).isoformat(timespec="seconds")

    running_dir = tmp_path / "jobs" / "running-job"
    running_dir.mkdir()
    store._jobs["running-job"] = AgentJob(
        job_id="running-job",
        status="running",
        created_at=completed.created_at,
        updated_at=(datetime.now() - timedelta(minutes=5)).isoformat(timespec="seconds"),
        pipeline_name=completed.pipeline_name,
        paper_count=completed.paper_count,
        work_dir=str(running_dir),
    )

    summary = store.cleanup_finished_jobs(now=datetime.now())

    assert summary["removed_jobs"] == 1
    assert summary["removed_dirs"] == 1
    assert store.get(completed_job.job_id) is None
    assert not Path(completed.work_dir).exists()
    assert store.get("running-job") is not None
    assert running_dir.exists()
    assert review_store.get_agent_job(completed_job.job_id)["status"] == "completed"


def test_agent_job_cleanup_rejects_paths_outside_job_dir(tmp_path) -> None:
    job_dir = tmp_path / "jobs"
    job_dir.mkdir()
    outside_dir = tmp_path / "outside"
    outside_dir.mkdir()
    (outside_dir / "keep.txt").write_text("keep", encoding="utf-8")

    assert _remove_job_work_dir(str(job_dir), job_dir) is False
    assert _remove_job_work_dir(str(outside_dir), job_dir) is False
    assert outside_dir.exists()
    assert (outside_dir / "keep.txt").exists()


def test_agent_job_persists_status_for_route_fallback(tmp_path) -> None:
    source_pdf = tmp_path / "source.pdf"
    source_pdf.write_text("1. 鍚庡彴 Agent 棰樼洰", encoding="utf-8")
    review_store = ReviewStore(tmp_path / "echopaper.db")
    store = AgentJobStore(tmp_path / "jobs", max_workers=1, review_store=review_store)
    job = store.submit(
        agent_pipeline=build_pipeline(),
        uploaded_papers=[
            UploadedPaper(
                paper_id="A",
                filename="source.pdf",
                subject="chinese",
                temp_path=str(source_pdf),
            )
        ],
    )
    job.future.result(timeout=5)

    persisted = review_store.get_agent_job(job.job_id)
    assert persisted is not None
    assert persisted["status"] == "completed"
    assert persisted["has_result"] is True
    assert persisted["result"]["question_count"] == 1
    assert persisted["result_payload"]["questions"][0]["content"] == "1. 鍚庡彴 Agent 棰樼洰"

    with TestClient(app) as client:
        client.app.state.review_store = review_store
        client.app.state.agent_job_store = AgentJobStore(tmp_path / "empty_jobs", max_workers=1)
        response = client.get(f"/api/agent-jobs/{job.job_id}")

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "completed"
    assert payload["result"]["question_count"] == 1
    assert payload["result_payload"]["questions"][0]["paper_id"] == "A"


def test_review_store_lists_agent_jobs(tmp_path) -> None:
    review_store = ReviewStore(tmp_path / "echopaper.db")
    review_store.upsert_agent_job_summary(
        {
            "job_id": "job-queued",
            "status": "queued",
            "created_at": "2026-01-01T00:00:00",
            "updated_at": "2026-01-01T00:00:00",
            "pipeline_name": "Agent",
            "paper_count": 1,
            "error": "",
            "has_result": False,
            "work_dir": "data/agent_jobs/job-queued",
        }
    )
    review_store.upsert_agent_job_summary(
        {
            "job_id": "job-completed",
            "status": "completed",
            "created_at": "2026-01-01T00:00:00",
            "updated_at": "2026-01-01T00:00:01",
            "pipeline_name": "Agent",
            "paper_count": 2,
            "error": "",
            "has_result": True,
            "work_dir": "data/agent_jobs/job-completed",
        },
        result_summary={
            "pipeline_name": "Agent",
            "question_count": 3,
            "duplicate_count": 1,
            "spellcheck_count": 2,
            "module_metadata": {},
        },
        result_payload={
            "pipeline_name": "Agent",
            "uploaded_papers": [],
            "questions": [{"question_id": "A-1", "paper_id": "A", "question_no": "1", "order": 1, "content": "Question"}],
            "similarity_matches": [],
            "spellcheck_issues": [],
            "module_metadata": {},
            "history_bank_summary": {},
        },
    )

    jobs = review_store.list_agent_jobs(limit=10)

    assert [job["job_id"] for job in jobs] == ["job-completed", "job-queued"]
    assert jobs[0]["paper_count"] == 2
    assert jobs[0]["has_result"] is True
    assert jobs[0]["result"]["question_count"] == 3
    assert jobs[0]["result_payload"]["questions"][0]["question_id"] == "A-1"


def test_agent_jobs_page_lists_persisted_jobs(tmp_path) -> None:
    review_store = ReviewStore(tmp_path / "echopaper.db")
    review_store.upsert_agent_job_summary(
        {
            "job_id": "job-completed",
            "status": "completed",
            "created_at": "2026-01-01T00:00:00",
            "updated_at": "2026-01-01T00:00:01",
            "pipeline_name": "Agent",
            "paper_count": 2,
            "error": "",
            "has_result": True,
            "work_dir": "data/agent_jobs/job-completed",
        },
        result_summary={
            "pipeline_name": "Agent",
            "question_count": 3,
            "duplicate_count": 1,
            "spellcheck_count": 2,
            "module_metadata": {},
        },
    )

    with TestClient(app) as client:
        client.app.state.review_store = review_store
        response = client.get("/agent-jobs")

    assert response.status_code == 200
    assert "Agent 后台任务列表" in response.text
    assert "job-completed" in response.text
    assert "题目 3 道，重复 1 条，错字 2 条" in response.text
    assert 'href="/api/agent-jobs/job-completed"' in response.text


def test_review_route_uses_async_agent_job_by_default(monkeypatch, tmp_path) -> None:
    class EmptyHistorySnapshot:
        questions = []

        @staticmethod
        def to_summary() -> dict:
            return {"total_files": 0, "loaded_files": 0, "question_count": 0, "papers": [], "failures": []}

    class EmptyHistoryService:
        @staticmethod
        def get_snapshot():
            return EmptyHistorySnapshot()

    def fake_pipeline_run(self, uploaded_papers, *, history_questions=None, history_bank_summary=None):
        questions = [
            Question(
                question_id="A-1",
                paper_id="A",
                question_no="1",
                order=1,
                content=f"{self.pipeline_name} 题目",
                raw_block=f"{self.pipeline_name} 题目",
            )
        ]
        return PipelineRunResult(
            pipeline_name=self.pipeline_name,
            uploaded_papers=[
                paper.model_copy(update={"text_content": questions[0].content, "page_count": 1})
                for paper in uploaded_papers
            ],
            questions=questions,
            similarity_matches=[],
            spellcheck_issues=[],
            module_metadata={
                "extract": {"provider_note": "", "is_placeholder": False},
                "split": {"provider_note": "", "is_placeholder": False},
                "compare": {"provider_note": "", "is_placeholder": False},
                "spellcheck": {"provider_note": "", "is_placeholder": False},
            },
            history_bank_summary=history_bank_summary or {},
        )

    monkeypatch.delenv("ENABLE_ASYNC_AGENT", raising=False)
    monkeypatch.setattr(ReviewPipeline, "run", fake_pipeline_run)

    with TestClient(app) as client:
        review_store = ReviewStore(tmp_path / "echopaper.db")
        client.app.state.review_store = review_store
        client.app.state.history_bank_service = EmptyHistoryService()
        client.app.state.agent_job_store = AgentJobStore(tmp_path / "agent_jobs", max_workers=1, review_store=review_store)
        response = client.post(
            "/review",
            data={"teacher_name": "李老师", "teacher_id": "T001", "subject": "chinese"},
            files={"paper_a": ("a.pdf", b"%PDF-1.4 fake", "application/pdf")},
        )
        assert response.status_code == 200
        assert "agent-job-panel" in response.text
        assert "agent-job-detail" in response.text
        assert "打开历史报告" in response.text
        assert "/api/reports/export-json" in response.text
        assert "renderAgentResultPayload" in response.text
        assert "完整对照回填将在下一阶段接入" not in response.text
        match = re.search(r'data-agent-job-id="([^"]+)"', response.text)
        assert match is not None
        job_id = match.group(1)

        with sqlite3.connect(review_store.db_path) as connection:
            snapshot_row = connection.execute(
                "SELECT session_id FROM report_snapshots"
            ).fetchone()
        assert snapshot_row is not None
        snapshot = review_store.get_report_snapshot(snapshot_row[0])
        assert snapshot is not None
        assert snapshot["payload"]["agent_job"]["job_id"] == job_id

        job = client.app.state.agent_job_store.get(job_id)
        assert job is not None
        job.future.result(timeout=5)

        status_response = client.get(f"/api/agent-jobs/{job_id}")

    assert status_response.status_code == 200
    payload = status_response.json()
    assert payload["status"] == "completed"
    assert payload["result"]["question_count"] == 1
