from types import SimpleNamespace
import sqlite3

from fastapi.testclient import TestClient

from app.main import app
from app.models.schemas import SimilarityMatch
from app.routes.web import _attach_review_persistence
from app.services.review_store import ReviewStore


def build_match(match_id: str = "m1") -> SimilarityMatch:
    return SimilarityMatch(
        match_id=match_id,
        comparison_type="cross_paper",
        source_paper_id="A",
        source_question_id="A-1",
        source_question_no="1",
        source_text="第一题",
        target_paper_id="B",
        target_question_id="B-1",
        target_question_no="1",
        target_text="第一题",
        similarity_score=96,
        level="高度重复",
    )


def test_review_store_creates_and_updates_review_item(tmp_path) -> None:
    store = ReviewStore(tmp_path / "echopaper.db")
    session_id = store.create_session(
        teacher_id="T001",
        teacher_name="李老师",
        subject="语文",
        paper_a_path="a.pdf",
    )
    item_ids = store.create_items(session_id, [build_match()])
    item_id = item_ids["m1"]

    before = store.get_item(item_id)
    assert before is not None
    assert before["status"] == "待确认"

    updated = store.update_item_status(item_id, "确认重复")
    after = store.get_item(item_id)

    assert updated["status"] == "确认重复"
    assert after is not None
    assert after["status"] == "确认重复"


def test_review_persistence_attaches_item_ids_to_duplicate_rows(tmp_path) -> None:
    store = ReviewStore(tmp_path / "echopaper.db")
    match = build_match()
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(review_store=store)))
    template_context = {
        "duplicate_rows": [{"match_id": match.match_id}],
        "export_payload": {
            "duplicate_comparison": {
                "code_rows": [{"match_id": match.match_id}],
            },
        },
    }
    code_run_result = SimpleNamespace(
        uploaded_papers=[],
        similarity_matches=[match],
    )

    _attach_review_persistence(
        request,
        template_context,
        teacher_id="T001",
        teacher_name="李老师",
        subject="语文",
        code_run_result=code_run_result,
    )

    row = template_context["duplicate_rows"][0]
    assert row["review_session_id"]
    assert row["review_item_id"]
    assert store.get_item(row["review_item_id"])["match_id"] == match.match_id
    assert template_context["export_payload"]["review_session"]["review_item_count"] == 1
    export_row = template_context["export_payload"]["duplicate_comparison"]["code_rows"][0]
    assert export_row["review_session_id"] == row["review_session_id"]
    assert export_row["review_item_id"] == row["review_item_id"]


def test_review_store_persists_report_snapshot(tmp_path) -> None:
    store = ReviewStore(tmp_path / "echopaper.db")
    session_id = store.create_session(
        teacher_id="T001",
        teacher_name="李老师",
        subject="语文",
    )
    payload = {
        "review_session": {"session_id": session_id, "review_item_count": 0},
        "report": {"teacher_name": "李老师", "subject": "语文"},
    }

    store.upsert_report_snapshot(session_id, payload)
    payload["agent_job"] = {"job_id": "job-1", "status": "queued"}
    store.upsert_report_snapshot(session_id, payload)

    snapshot = store.get_report_snapshot(session_id)
    assert snapshot is not None
    assert snapshot["session_id"] == session_id
    assert snapshot["created_at"]
    assert snapshot["updated_at"]
    assert snapshot["payload"]["report"]["teacher_name"] == "李老师"
    assert snapshot["payload"]["agent_job"]["job_id"] == "job-1"


def test_report_snapshot_api_returns_saved_payload(tmp_path) -> None:
    store = ReviewStore(tmp_path / "echopaper.db")
    session_id = store.create_session(
        teacher_id="T001",
        teacher_name="李老师",
        subject="语文",
    )
    store.upsert_report_snapshot(
        session_id,
        {
            "review_session": {"session_id": session_id},
            "report": {"subject": "语文"},
        },
    )

    with TestClient(app) as client:
        client.app.state.review_store = store
        response = client.get(f"/api/reports/{session_id}")

    assert response.status_code == 200
    payload = response.json()
    assert payload["session_id"] == session_id
    assert payload["payload"]["report"]["subject"] == "语文"


def test_report_snapshot_api_backfills_completed_agent_payload(tmp_path) -> None:
    store = ReviewStore(tmp_path / "echopaper.db")
    session_id = store.create_session(
        teacher_id="T001",
        teacher_name="Teacher",
        subject="Chinese",
    )
    store.upsert_report_snapshot(
        session_id,
        {
            "review_session": {"session_id": session_id},
            "agent_job": {"job_id": "job-1", "status": "queued"},
            "report": {"subject": "Chinese"},
        },
    )
    store.upsert_agent_job_summary(
        {
            "job_id": "job-1",
            "status": "completed",
            "created_at": "2026-01-01T00:00:00",
            "updated_at": "2026-01-01T00:00:01",
            "pipeline_name": "Agent",
            "paper_count": 1,
            "error": "",
            "has_result": True,
            "work_dir": "data/agent_jobs/job-1",
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

    with TestClient(app) as client:
        client.app.state.review_store = store
        response = client.get(f"/api/reports/{session_id}")

    assert response.status_code == 200
    payload = response.json()["payload"]
    assert payload["agent_job"]["status"] == "completed"
    assert payload["agent_result_payload"]["questions"][0]["question_id"] == "A-1"
    saved_snapshot = store.get_report_snapshot(session_id)
    assert saved_snapshot["payload"]["agent_result_payload"]["questions"][0]["question_id"] == "A-1"


def test_review_store_lists_report_snapshots_with_summary(tmp_path) -> None:
    store = ReviewStore(tmp_path / "echopaper.db")
    session_id = store.create_session(
        teacher_id="T001",
        teacher_name="李老师",
        subject="语文",
    )
    store.upsert_report_snapshot(
        session_id,
        {
            "review_session": {"session_id": session_id},
            "agent_job": {"job_id": "job-1"},
            "report": {
                "teacher_name": "李老师",
                "teacher_id": "T001",
                "subject": "语文",
                "dashboard": {
                    "paper_a_question_count": 2,
                    "paper_b_question_count": 1,
                },
                "uploaded_papers": [{"paper_id": "A"}, {"paper_id": "B"}],
            },
            "duplicate_comparison": {
                "summary": {},
                "code_rows": [
                    {"review_status": "待确认"},
                    {"review_status": "排除误报"},
                ],
            },
            "spellcheck_comparison": {
                "summary": {},
                "code_rows": [{"issue_text": "错字"}],
            },
        },
    )
    store.record_export(session_id=session_id, export_format="pdf", file_path="first.pdf")
    store.record_export(session_id=session_id, export_format="json", file_path="second.json")

    rows = store.list_report_snapshots()

    assert len(rows) == 1
    assert rows[0]["session_id"] == session_id
    assert rows[0]["teacher_name"] == "李老师"
    assert rows[0]["paper_count"] == 2
    assert rows[0]["question_count"] == 3
    assert rows[0]["duplicate_count"] == 2
    assert rows[0]["spellcheck_count"] == 1
    assert rows[0]["pending_review_count"] == 1
    assert rows[0]["agent_job_id"] == "job-1"
    assert rows[0]["export_count"] == 2
    assert rows[0]["last_export_format"] == "json"
    assert rows[0]["last_export_file_path"] == "second.json"


def test_review_store_filters_report_snapshots(tmp_path) -> None:
    store = ReviewStore(tmp_path / "echopaper.db")
    chinese_session = store.create_session(
        teacher_id="T001",
        teacher_name="李老师",
        subject="语文",
    )
    math_session = store.create_session(
        teacher_id="T002",
        teacher_name="王老师",
        subject="数学",
    )
    store.upsert_report_snapshot(
        chinese_session,
        {
            "review_session": {"session_id": chinese_session},
            "report": {"teacher_name": "李老师", "teacher_id": "T001", "subject": "语文", "dashboard": {}},
            "duplicate_comparison": {"summary": {}, "code_rows": []},
            "spellcheck_comparison": {"summary": {}, "code_rows": []},
        },
    )
    store.upsert_report_snapshot(
        math_session,
        {
            "review_session": {"session_id": math_session},
            "agent_job": {"job_id": "agent-math"},
            "report": {"teacher_name": "王老师", "teacher_id": "T002", "subject": "数学", "dashboard": {}},
            "duplicate_comparison": {"summary": {}, "code_rows": []},
            "spellcheck_comparison": {"summary": {}, "code_rows": []},
        },
    )

    subject_rows = store.list_report_snapshots(subject="语文")
    keyword_rows = store.list_report_snapshots(keyword="agent-math")

    assert [row["session_id"] for row in subject_rows] == [chinese_session]
    assert [row["session_id"] for row in keyword_rows] == [math_session]


def test_review_store_filters_report_snapshots_before_limit(tmp_path) -> None:
    store = ReviewStore(tmp_path / "echopaper.db")
    chinese_session = store.create_session(
        teacher_id="T001",
        teacher_name="李老师",
        subject="语文",
    )
    math_session = store.create_session(
        teacher_id="T002",
        teacher_name="王老师",
        subject="数学",
    )
    store.upsert_report_snapshot(
        math_session,
        {
            "review_session": {"session_id": math_session},
            "report": {"teacher_name": "王老师", "teacher_id": "T002", "subject": "数学", "dashboard": {}},
            "duplicate_comparison": {"summary": {}, "code_rows": []},
            "spellcheck_comparison": {"summary": {}, "code_rows": []},
        },
    )
    store.upsert_report_snapshot(
        chinese_session,
        {
            "review_session": {"session_id": chinese_session},
            "report": {"teacher_name": "李老师", "teacher_id": "T001", "subject": "语文", "dashboard": {}},
            "duplicate_comparison": {"summary": {}, "code_rows": []},
            "spellcheck_comparison": {"summary": {}, "code_rows": []},
        },
    )

    rows = store.list_report_snapshots(limit=1, subject="数学")

    assert [row["session_id"] for row in rows] == [math_session]


def test_report_snapshots_page_lists_saved_reports(tmp_path) -> None:
    store = ReviewStore(tmp_path / "echopaper.db")
    session_id = store.create_session(
        teacher_id="T001",
        teacher_name="李老师",
        subject="语文",
    )
    store.upsert_report_snapshot(
        session_id,
        {
            "review_session": {"session_id": session_id},
            "report": {
                "teacher_name": "李老师",
                "teacher_id": "T001",
                "subject": "语文",
                "dashboard": {"paper_a_question_count": 1},
                "uploaded_papers": [{"paper_id": "A"}],
            },
            "duplicate_comparison": {"summary": {}, "code_rows": [{"review_status": "待确认"}]},
            "spellcheck_comparison": {"summary": {}, "code_rows": []},
        },
    )
    store.record_export(session_id=session_id, export_format="pdf", file_path="echopaper-report.pdf")

    with TestClient(app) as client:
        client.app.state.review_store = store
        response = client.get("/reports")

    assert response.status_code == 200
    assert "历史报告列表" in response.text
    assert "李老师" in response.text
    assert f"/reports/{session_id}" in response.text
    assert "待复核 1" in response.text
    assert "1 次" in response.text
    assert "最近 PDF" in response.text
    assert "当前结果数" in response.text


def test_report_snapshots_page_filters_saved_reports(tmp_path) -> None:
    store = ReviewStore(tmp_path / "echopaper.db")
    chinese_session = store.create_session(
        teacher_id="T001",
        teacher_name="李老师",
        subject="语文",
    )
    math_session = store.create_session(
        teacher_id="T002",
        teacher_name="王老师",
        subject="数学",
    )
    store.upsert_report_snapshot(
        chinese_session,
        {
            "review_session": {"session_id": chinese_session},
            "report": {"teacher_name": "李老师", "teacher_id": "T001", "subject": "语文", "dashboard": {}},
            "duplicate_comparison": {"summary": {}, "code_rows": []},
            "spellcheck_comparison": {"summary": {}, "code_rows": []},
        },
    )
    store.upsert_report_snapshot(
        math_session,
        {
            "review_session": {"session_id": math_session},
            "report": {"teacher_name": "王老师", "teacher_id": "T002", "subject": "数学", "dashboard": {}},
            "duplicate_comparison": {"summary": {}, "code_rows": []},
            "spellcheck_comparison": {"summary": {}, "code_rows": []},
        },
    )

    with TestClient(app) as client:
        client.app.state.review_store = store
        response = client.get("/reports?subject=数学&q=王")

    assert response.status_code == 200
    assert "王老师" in response.text
    assert "李老师" not in response.text


def test_index_page_links_to_report_list_and_history_bank() -> None:
    with TestClient(app) as client:
        response = client.get("/")

    assert response.status_code == 200
    assert 'href="/reports"' in response.text
    assert 'href="/agent-jobs"' in response.text
    assert 'href="/history-bank"' in response.text


def test_review_item_api_updates_report_snapshot_review_status(tmp_path) -> None:
    store = ReviewStore(tmp_path / "echopaper.db")
    session_id = store.create_session(
        teacher_id="T001",
        teacher_name="李老师",
        subject="语文",
    )
    match = build_match()
    item_id = store.create_items(session_id, [match])["m1"]
    store.upsert_report_snapshot(
        session_id,
        {
            "review_session": {"session_id": session_id},
            "report": {"subject": "语文"},
            "duplicate_comparison": {
                "summary": {},
                "code_rows": [
                    {
                        "match_id": match.match_id,
                        "review_item_id": item_id,
                        "review_status": "待确认",
                    }
                ],
            },
            "spellcheck_comparison": {"summary": {}, "code_rows": []},
        },
    )

    with TestClient(app) as client:
        client.app.state.review_store = store
        response = client.patch(
            f"/api/review-items/{item_id}",
            json={"status": "排除误报"},
        )

    snapshot = store.get_report_snapshot(session_id)
    assert response.status_code == 200
    assert snapshot["payload"]["duplicate_comparison"]["code_rows"][0]["review_status"] == "排除误报"


def test_report_snapshot_page_renders_saved_payload(tmp_path) -> None:
    store = ReviewStore(tmp_path / "echopaper.db")
    session_id = store.create_session(
        teacher_id="T001",
        teacher_name="李老师",
        subject="语文",
    )
    store.upsert_report_snapshot(
        session_id,
        {
            "review_session": {"session_id": session_id},
            "report": {
                "teacher_name": "李老师",
                "teacher_id": "T001",
                "subject": "语文",
                "dashboard": {
                    "paper_a_question_count": 1,
                    "paper_b_question_count": 0,
                    "suspected_duplicate_total": 0,
                    "spellcheck_issue_total": 0,
                    "history_match_total": 0,
                },
                "uploaded_papers": [
                    {
                        "paper_id": "A",
                        "filename": "a.pdf",
                        "page_count": 1,
                        "text_content": "1. 第一题",
                    }
                ],
            },
            "dual_run_sections": [
                {
                    "module_name": "切题",
                    "status": "一致",
                    "diff_summary": "两边切题结果一致。",
                }
            ],
            "duplicate_comparison": {"summary": {}, "code_rows": []},
            "spellcheck_comparison": {"summary": {}, "code_rows": []},
            "question_quality": [],
        },
    )

    with TestClient(app) as client:
        client.app.state.review_store = store
        response = client.get(f"/reports/{session_id}")

    assert response.status_code == 200
    assert "EchoPaper / 历史报告" in response.text
    assert "李老师 的审查报告" in response.text
    assert "a.pdf" in response.text
    assert "两边切题结果一致" in response.text
    assert "/api/reports/export-json" in response.text
    assert "导出记录" in response.text
    assert "历史快照 / 只读恢复报告" in response.text
    assert "snapshot-overview" in response.text


def test_report_snapshot_page_backfills_completed_agent_payload(tmp_path) -> None:
    store = ReviewStore(tmp_path / "echopaper.db")
    session_id = store.create_session(
        teacher_id="T001",
        teacher_name="Teacher",
        subject="Chinese",
    )
    store.upsert_report_snapshot(
        session_id,
        {
            "review_session": {"session_id": session_id},
            "agent_job": {"job_id": "job-1", "status": "queued"},
            "report": {
                "teacher_name": "Teacher",
                "teacher_id": "T001",
                "subject": "Chinese",
                "dashboard": {},
                "uploaded_papers": [],
            },
            "dual_run_sections": [],
            "duplicate_comparison": {"summary": {}, "code_rows": []},
            "spellcheck_comparison": {"summary": {}, "code_rows": []},
            "question_quality": [],
        },
    )
    store.upsert_agent_job_summary(
        {
            "job_id": "job-1",
            "status": "completed",
            "created_at": "2026-01-01T00:00:00",
            "updated_at": "2026-01-01T00:00:01",
            "pipeline_name": "Agent",
            "paper_count": 1,
            "error": "",
            "has_result": True,
            "work_dir": "data/agent_jobs/job-1",
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

    with TestClient(app) as client:
        client.app.state.review_store = store
        response = client.get(f"/reports/{session_id}")

    assert response.status_code == 200
    assert "job-1" in response.text
    assert "Agent" in response.text
    saved_snapshot = store.get_report_snapshot(session_id)
    assert saved_snapshot["payload"]["agent_job"]["status"] == "completed"
    assert saved_snapshot["payload"]["agent_result_payload"]["questions"][0]["question_id"] == "A-1"


def test_report_snapshot_page_returns_404_for_missing_session(tmp_path) -> None:
    store = ReviewStore(tmp_path / "echopaper.db")

    with TestClient(app) as client:
        client.app.state.review_store = store
        response = client.get("/reports/missing-session")

    assert response.status_code == 404


def test_report_snapshot_page_shows_review_status(tmp_path) -> None:
    store = ReviewStore(tmp_path / "echopaper.db")
    session_id = store.create_session(
        teacher_id="T001",
        teacher_name="李老师",
        subject="语文",
    )
    store.upsert_report_snapshot(
        session_id,
        {
            "review_session": {"session_id": session_id},
            "report": {
                "teacher_name": "李老师",
                "teacher_id": "T001",
                "subject": "语文",
                "dashboard": {},
                "uploaded_papers": [],
            },
            "dual_run_sections": [],
            "duplicate_comparison": {
                "summary": {},
                "code_rows": [
                    {
                        "comparison_label": "A 卷第 1 题 vs B 卷第 1 题",
                        "score": 96,
                        "level": "高度重复",
                        "compare_status": "一致",
                        "review_status": "排除误报",
                    }
                ],
            },
            "spellcheck_comparison": {"summary": {}, "code_rows": []},
            "question_quality": [],
        },
    )

    with TestClient(app) as client:
        client.app.state.review_store = store
        response = client.get(f"/reports/{session_id}")

    assert response.status_code == 200
    assert "人工复核" in response.text
    assert "排除误报" in response.text


def test_report_snapshot_page_renders_review_status_controls(tmp_path) -> None:
    store = ReviewStore(tmp_path / "echopaper.db")
    session_id = store.create_session(
        teacher_id="T001",
        teacher_name="李老师",
        subject="语文",
    )
    match = build_match()
    item_id = store.create_items(session_id, [match])["m1"]
    store.upsert_report_snapshot(
        session_id,
        {
            "review_session": {"session_id": session_id},
            "report": {
                "teacher_name": "李老师",
                "teacher_id": "T001",
                "subject": "语文",
                "dashboard": {},
                "uploaded_papers": [],
            },
            "dual_run_sections": [],
            "duplicate_comparison": {
                "summary": {},
                "code_rows": [
                    {
                        "match_id": match.match_id,
                        "review_item_id": item_id,
                        "comparison_label": "A 卷第 1 题 vs B 卷第 1 题",
                        "score": 96,
                        "level": "高度重复",
                        "compare_status": "一致",
                        "review_status": "待确认",
                        "review_options": ["待确认", "确认重复", "排除误报"],
                    }
                ],
            },
            "spellcheck_comparison": {"summary": {}, "code_rows": []},
            "question_quality": [],
        },
    )

    with TestClient(app) as client:
        client.app.state.review_store = store
        response = client.get(f"/reports/{session_id}")

    assert response.status_code == 200
    assert f'data-review-item-id="{item_id}"' in response.text
    assert "updateExportPayloadReviewStatus" in response.text
    assert "确认重复" in response.text


def test_review_report_page_shows_pending_review_summary_when_needed(tmp_path, monkeypatch) -> None:
    from app.models.schemas import Question, UploadedPaper
    from app.services.dual_run import PipelineRunResult, ReviewPipeline

    def fake_pipeline_run(self, uploaded_papers, *, history_questions=None, history_bank_summary=None):
        question = Question(
            question_id="A-1",
            paper_id="A",
            question_no="1",
            order=1,
            content="示例题目",
            raw_block="示例题目",
        )
        paper = uploaded_papers[0].model_copy(update={"page_count": 1, "text_content": question.content})
        return PipelineRunResult(
            pipeline_name=self.pipeline_name,
            uploaded_papers=[paper],
            questions=[question],
            similarity_matches=[
                build_match().model_copy(
                    update={
                        "comparison_type": "cross_paper",
                        "source_paper_id": "A",
                        "source_question_no": "1",
                        "target_paper_id": "B",
                        "target_question_no": "1",
                        "review_status": "待确认",
                    }
                )
            ],
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

    class EmptyHistorySnapshot:
        questions = []

        @staticmethod
        def to_summary() -> dict:
            return {"total_files": 0, "loaded_files": 0, "question_count": 0, "papers": [], "failures": []}

    class EmptyHistoryService:
        @staticmethod
        def get_snapshot():
            return EmptyHistorySnapshot()

    with TestClient(app) as client:
        store = ReviewStore(tmp_path / "echopaper.db")
        client.app.state.review_store = store
        client.app.state.history_bank_service = EmptyHistoryService()
        response = client.post(
            "/review",
            data={"teacher_name": "李老师", "teacher_id": "T001", "subject": "chinese"},
            files={"paper_a": ("a.pdf", b"%PDF-1.4 fake", "application/pdf")},
        )

    assert response.status_code == 200
    assert "待确认聚合视图" in response.text
    assert "跳到该区块" in response.text


def test_report_snapshot_page_payload_reflects_review_status_after_patch(tmp_path) -> None:
    store = ReviewStore(tmp_path / "echopaper.db")
    session_id = store.create_session(
        teacher_id="T001",
        teacher_name="李老师",
        subject="语文",
    )
    match = build_match()
    item_id = store.create_items(session_id, [match])["m1"]
    store.upsert_report_snapshot(
        session_id,
        {
            "review_session": {"session_id": session_id},
            "report": {
                "teacher_name": "李老师",
                "teacher_id": "T001",
                "subject": "语文",
                "dashboard": {},
                "uploaded_papers": [],
            },
            "dual_run_sections": [],
            "duplicate_comparison": {
                "summary": {},
                "code_rows": [
                    {
                        "match_id": match.match_id,
                        "review_item_id": item_id,
                        "comparison_label": "A 卷第 1 题 vs B 卷第 1 题",
                        "score": 96,
                        "level": "高度重复",
                        "compare_status": "一致",
                        "review_status": "待确认",
                        "review_options": ["待确认", "确认重复", "排除误报"],
                    }
                ],
            },
            "spellcheck_comparison": {"summary": {}, "code_rows": []},
            "question_quality": [],
        },
    )

    with TestClient(app) as client:
        client.app.state.review_store = store
        patch_response = client.patch(
            f"/api/review-items/{item_id}",
            json={"status": "确认重复"},
        )
        page_response = client.get(f"/reports/{session_id}")

    assert patch_response.status_code == 200
    assert page_response.status_code == 200
    snapshot = store.get_report_snapshot(session_id)
    assert snapshot["payload"]["duplicate_comparison"]["code_rows"][0]["review_status"] == "确认重复"
    assert '<option value="确认重复" selected' in page_response.text


def test_review_item_api_updates_status(tmp_path) -> None:
    store = ReviewStore(tmp_path / "echopaper.db")
    session_id = store.create_session(
        teacher_id="T001",
        teacher_name="李老师",
        subject="语文",
    )
    item_id = store.create_items(session_id, [build_match()])["m1"]

    with TestClient(app) as client:
        client.app.state.review_store = store
        response = client.patch(
            f"/api/review-items/{item_id}",
            json={"status": "排除误报"},
        )

    assert response.status_code == 200
    assert response.json()["item"]["status"] == "排除误报"
    assert store.get_item(item_id)["status"] == "排除误报"


def test_review_item_api_rejects_invalid_status(tmp_path) -> None:
    store = ReviewStore(tmp_path / "echopaper.db")
    session_id = store.create_session(
        teacher_id="T001",
        teacher_name="李老师",
        subject="语文",
    )
    item_id = store.create_items(session_id, [build_match()])["m1"]

    with TestClient(app) as client:
        client.app.state.review_store = store
        response = client.patch(
            f"/api/review-items/{item_id}",
            json={"status": "随便写"},
        )

    assert response.status_code == 400
    assert store.get_item(item_id)["status"] == "待确认"


def test_report_pdf_export_route_records_export_history(tmp_path) -> None:
    store = ReviewStore(tmp_path / "echopaper.db")
    session_id = store.create_session(
        teacher_id="T001",
        teacher_name="李老师",
        subject="语文",
    )
    payload = {
        "review_session": {"session_id": session_id},
        "report": {
            "teacher_name": "李老师",
            "teacher_id": "T001",
            "subject": "语文",
            "dashboard": {},
            "uploaded_papers": [],
        },
        "duplicate_comparison": {"summary": {}, "code_rows": []},
        "spellcheck_comparison": {"summary": {}, "code_rows": []},
        "dual_run_sections": [],
    }

    with TestClient(app) as client:
        client.app.state.review_store = store
        response = client.post("/api/reports/export-pdf", json=payload)

    assert response.status_code == 200
    assert response.headers["content-type"] == "application/pdf"
    assert response.content.startswith(b"%PDF-1.4")

    with store.db_path.open("rb"):
        pass
    import sqlite3

    with sqlite3.connect(store.db_path) as connection:
        row = connection.execute(
            "SELECT session_id, format, file_path FROM export_history WHERE session_id = ?",
            (session_id,),
        ).fetchone()

    assert row is not None
    assert row[0] == session_id
    assert row[1] == "pdf"
    assert row[2].endswith(".pdf")


def test_report_json_export_route_records_export_history(tmp_path) -> None:
    store = ReviewStore(tmp_path / "echopaper.db")
    session_id = store.create_session(
        teacher_id="T001",
        teacher_name="李老师",
        subject="语文",
    )
    payload = {
        "review_session": {"session_id": session_id},
        "report": {
            "teacher_name": "李老师",
            "teacher_id": "T001",
            "subject": "语文",
            "dashboard": {},
            "uploaded_papers": [],
        },
        "duplicate_comparison": {"summary": {}, "code_rows": []},
        "spellcheck_comparison": {"summary": {}, "code_rows": []},
        "dual_run_sections": [],
    }

    with TestClient(app) as client:
        client.app.state.review_store = store
        response = client.post("/api/reports/export-json", json=payload)

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/json")
    assert response.headers["content-disposition"].startswith('attachment; filename="echopaper-report.json"')
    assert response.json()["review_session"]["session_id"] == session_id

    rows = store.list_export_history(session_id)
    assert len(rows) == 1
    assert rows[0]["format"] == "json"
    assert rows[0]["file_path"] == "echopaper-report.json"


def test_review_store_lists_export_history_for_session(tmp_path) -> None:
    store = ReviewStore(tmp_path / "echopaper.db")
    session_id = store.create_session(
        teacher_id="T001",
        teacher_name="李老师",
        subject="语文",
    )
    other_session_id = store.create_session(
        teacher_id="T002",
        teacher_name="王老师",
        subject="数学",
    )

    store.record_export(session_id=session_id, export_format="pdf", file_path="first.pdf")
    store.record_export(session_id=other_session_id, export_format="pdf", file_path="other.pdf")
    store.record_export(session_id=session_id, export_format="json", file_path="second.json")

    rows = store.list_export_history(session_id)

    assert len(rows) == 2
    assert {row["file_path"] for row in rows} == {"first.pdf", "second.json"}
    assert all(row["session_id"] == session_id for row in rows)


def test_report_snapshot_page_shows_export_history(tmp_path) -> None:
    store = ReviewStore(tmp_path / "echopaper.db")
    session_id = store.create_session(
        teacher_id="T001",
        teacher_name="李老师",
        subject="语文",
    )
    store.upsert_report_snapshot(
        session_id,
        {
            "review_session": {"session_id": session_id},
            "report": {
                "teacher_name": "李老师",
                "teacher_id": "T001",
                "subject": "语文",
                "dashboard": {},
                "uploaded_papers": [],
            },
            "duplicate_comparison": {"summary": {}, "code_rows": []},
            "spellcheck_comparison": {"summary": {}, "code_rows": []},
            "dual_run_sections": [],
            "question_quality": [],
        },
    )
    store.record_export(session_id=session_id, export_format="pdf", file_path="echopaper-report.pdf")

    with TestClient(app) as client:
        client.app.state.review_store = store
        response = client.get(f"/reports/{session_id}")

    assert response.status_code == 200
    assert "导出记录" in response.text
    assert "PDF" in response.text
    assert "echopaper-report.pdf" in response.text


def test_review_store_persists_agent_job_summary(tmp_path) -> None:
    store = ReviewStore(tmp_path / "echopaper.db")
    summary = {
        "job_id": "job-1",
        "status": "queued",
        "created_at": "2026-01-01T00:00:00",
        "updated_at": "2026-01-01T00:00:00",
        "pipeline_name": "Agent",
        "paper_count": 1,
        "error": "",
        "has_result": False,
        "work_dir": "data/agent_jobs/job-1",
    }

    store.upsert_agent_job_summary(summary)
    summary["status"] = "completed"
    summary["updated_at"] = "2026-01-01T00:00:01"
    summary["has_result"] = True
    store.upsert_agent_job_summary(
        summary,
        result_summary={
            "pipeline_name": "Agent",
            "question_count": 2,
            "duplicate_count": 0,
            "spellcheck_count": 0,
            "module_metadata": {},
        },
        result_payload={
            "pipeline_name": "Agent",
            "uploaded_papers": [],
            "questions": [{"question_id": "A-1", "paper_id": "A", "question_no": "1", "order": 1, "content": "题目"}],
            "similarity_matches": [],
            "spellcheck_issues": [],
            "module_metadata": {},
            "history_bank_summary": {},
        },
    )

    persisted = store.get_agent_job("job-1")
    assert persisted is not None
    assert persisted["status"] == "completed"
    assert persisted["has_result"] is True
    assert persisted["result"]["question_count"] == 2
    assert persisted["result_payload"]["questions"][0]["question_id"] == "A-1"


def test_review_store_migrates_existing_agent_jobs_table(tmp_path) -> None:
    db_path = tmp_path / "echopaper.db"
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            """
            CREATE TABLE agent_jobs (
                job_id TEXT PRIMARY KEY,
                status TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                pipeline_name TEXT NOT NULL,
                paper_count INTEGER NOT NULL,
                error TEXT,
                work_dir TEXT,
                has_result INTEGER NOT NULL DEFAULT 0,
                result_summary_json TEXT
            )
            """
        )
        connection.commit()

    store = ReviewStore(db_path)
    store.upsert_agent_job_summary(
        {
            "job_id": "job-old",
            "status": "completed",
            "created_at": "2026-01-01T00:00:00",
            "updated_at": "2026-01-01T00:00:01",
            "pipeline_name": "Agent",
            "paper_count": 1,
            "error": "",
            "has_result": True,
            "work_dir": "data/agent_jobs/job-old",
        },
        result_payload={
            "pipeline_name": "Agent",
            "uploaded_papers": [],
            "questions": [],
            "similarity_matches": [],
            "spellcheck_issues": [],
            "module_metadata": {},
            "history_bank_summary": {},
        },
    )

    assert store.get_agent_job("job-old")["result_payload"]["pipeline_name"] == "Agent"
