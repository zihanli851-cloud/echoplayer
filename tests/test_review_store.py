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
        "export_payload": {},
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
