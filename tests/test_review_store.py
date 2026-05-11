from types import SimpleNamespace

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
        level="高强度重复",
    )


def test_review_store_creates_and_updates_review_item(tmp_path) -> None:
    store = ReviewStore(tmp_path / "echopaper.db")
    session_id = store.create_session(
        teacher_id="T001",
        teacher_name="李老师",
        subject="语文",
        paper_a_path="a.pdf",
    )
    item_id = store.create_items(session_id, [build_match()])["m1"]

    assert store.get_item(item_id)["status"] == "待确认"

    updated = store.update_item_status(item_id, "确认重复")

    assert updated["status"] == "确认重复"
    assert store.get_item(item_id)["status"] == "确认重复"


def test_review_persistence_attaches_item_ids_to_duplicate_rows(tmp_path) -> None:
    store = ReviewStore(tmp_path / "echopaper.db")
    match = build_match()
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(review_store=store)))
    template_context = {
        "duplicate_rows": [{"match_id": match.match_id}],
        "export_payload": {"duplicate_comparison": {"code_rows": [{"match_id": match.match_id}]}},
    }
    code_run_result = SimpleNamespace(uploaded_papers=[], similarity_matches=[match])

    _attach_review_persistence(
        request,
        template_context,
        teacher_id="T001",
        teacher_name="李老师",
        subject="语文",
        code_run_result=code_run_result,
    )

    row = template_context["duplicate_rows"][0]
    export_row = template_context["export_payload"]["duplicate_comparison"]["code_rows"][0]
    assert row["review_session_id"]
    assert row["review_item_id"]
    assert export_row["review_item_id"] == row["review_item_id"]
    assert template_context["export_payload"]["review_session"]["review_item_count"] == 1


def test_review_store_persists_report_snapshot(tmp_path) -> None:
    store = ReviewStore(tmp_path / "echopaper.db")
    session_id = store.create_session(teacher_id="T001", teacher_name="李老师", subject="语文")
    payload = {
        "review_session": {"session_id": session_id, "review_item_count": 0},
        "report": {"teacher_name": "李老师", "subject": "语文"},
    }

    store.upsert_report_snapshot(session_id, payload)

    snapshot = store.get_report_snapshot(session_id)
    assert snapshot is not None
    assert snapshot["session_id"] == session_id
    assert snapshot["payload"]["report"]["teacher_name"] == "李老师"


def test_report_snapshot_api_returns_saved_payload(tmp_path) -> None:
    store = ReviewStore(tmp_path / "echopaper.db")
    session_id = store.create_session(teacher_id="T001", teacher_name="李老师", subject="语文")
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
    assert response.json()["payload"]["report"]["subject"] == "语文"


def test_review_store_lists_report_snapshots_with_summary(tmp_path) -> None:
    store = ReviewStore(tmp_path / "echopaper.db")
    session_id = store.create_session(teacher_id="T001", teacher_name="李老师", subject="语文")
    store.upsert_report_snapshot(
        session_id,
        {
            "review_session": {"session_id": session_id},
            "report": {
                "teacher_name": "李老师",
                "teacher_id": "T001",
                "subject": "语文",
                "dashboard": {"paper_a_question_count": 2, "paper_b_question_count": 1},
                "uploaded_papers": [{"paper_id": "A"}, {"paper_id": "B"}],
            },
            "duplicate_comparison": {
                "summary": {},
                "code_rows": [{"review_status": "待确认"}, {"review_status": "排除误报"}],
            },
            "spellcheck_comparison": {"summary": {}, "code_rows": [{"issue_text": "错字"}]},
        },
    )
    store.record_export(session_id=session_id, export_format="pdf", file_path="first.pdf")
    store.record_export(session_id=session_id, export_format="json", file_path="second.json")

    rows = store.list_report_snapshots()

    assert len(rows) == 1
    assert rows[0]["paper_count"] == 2
    assert rows[0]["question_count"] == 3
    assert rows[0]["duplicate_count"] == 2
    assert rows[0]["spellcheck_count"] == 1
    assert rows[0]["pending_review_count"] == 1
    assert rows[0]["export_count"] == 2
    assert rows[0]["last_export_format"] == "json"


def test_review_store_filters_report_snapshots(tmp_path) -> None:
    store = ReviewStore(tmp_path / "echopaper.db")
    chinese_session = store.create_session(teacher_id="T001", teacher_name="李老师", subject="语文")
    math_session = store.create_session(teacher_id="T002", teacher_name="王老师", subject="数学")
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

    subject_rows = store.list_report_snapshots(subject="语文")
    keyword_rows = store.list_report_snapshots(keyword="王")

    assert [row["session_id"] for row in subject_rows] == [chinese_session]
    assert [row["session_id"] for row in keyword_rows] == [math_session]


def test_index_page_links_to_report_list_and_history_bank() -> None:
    with TestClient(app) as client:
        response = client.get("/")

    assert response.status_code == 200
    assert 'href="/reports"' in response.text
    assert 'href="/history-bank"' in response.text


def test_review_item_api_updates_report_snapshot_review_status(tmp_path) -> None:
    store = ReviewStore(tmp_path / "echopaper.db")
    session_id = store.create_session(teacher_id="T001", teacher_name="李老师", subject="语文")
    match = build_match()
    item_id = store.create_items(session_id, [match])["m1"]
    store.upsert_report_snapshot(
        session_id,
        {
            "review_session": {"session_id": session_id},
            "report": {"subject": "语文"},
            "duplicate_comparison": {
                "summary": {},
                "code_rows": [{"match_id": match.match_id, "review_item_id": item_id, "review_status": "待确认"}],
            },
            "spellcheck_comparison": {"summary": {}, "code_rows": []},
        },
    )

    with TestClient(app) as client:
        client.app.state.review_store = store
        response = client.patch(f"/api/review-items/{item_id}", json={"status": "排除误报"})

    snapshot = store.get_report_snapshot(session_id)
    assert response.status_code == 200
    assert snapshot["payload"]["duplicate_comparison"]["code_rows"][0]["review_status"] == "排除误报"


def test_report_snapshot_page_renders_saved_payload(tmp_path) -> None:
    store = ReviewStore(tmp_path / "echopaper.db")
    session_id = store.create_session(teacher_id="T001", teacher_name="李老师", subject="语文")
    store.upsert_report_snapshot(
        session_id,
        {
            "review_session": {"session_id": session_id},
            "report": {
                "teacher_name": "李老师",
                "teacher_id": "T001",
                "subject": "语文",
                "dashboard": {"paper_a_question_count": 1, "paper_b_question_count": 0},
                "uploaded_papers": [{"paper_id": "A", "filename": "a.pdf", "page_count": 1, "text_content": "1. 第一题"}],
            },
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
    assert "/api/reports/export-json" in response.text
    assert "导出记录" in response.text


def test_report_snapshot_page_returns_404_for_missing_session(tmp_path) -> None:
    store = ReviewStore(tmp_path / "echopaper.db")

    with TestClient(app) as client:
        client.app.state.review_store = store
        response = client.get("/reports/missing-session")

    assert response.status_code == 404


def test_review_item_api_rejects_invalid_status(tmp_path) -> None:
    store = ReviewStore(tmp_path / "echopaper.db")
    session_id = store.create_session(teacher_id="T001", teacher_name="李老师", subject="语文")
    item_id = store.create_items(session_id, [build_match()])["m1"]

    with TestClient(app) as client:
        client.app.state.review_store = store
        response = client.patch(f"/api/review-items/{item_id}", json={"status": "随便写"})

    assert response.status_code == 400
    assert store.get_item(item_id)["status"] == "待确认"


def test_report_export_routes_record_export_history(tmp_path) -> None:
    store = ReviewStore(tmp_path / "echopaper.db")
    session_id = store.create_session(teacher_id="T001", teacher_name="李老师", subject="语文")
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
    }

    with TestClient(app) as client:
        client.app.state.review_store = store
        pdf_response = client.post("/api/reports/export-pdf", json=payload)
        json_response = client.post("/api/reports/export-json", json=payload)

    assert pdf_response.status_code == 200
    assert pdf_response.headers["content-type"] == "application/pdf"
    assert json_response.status_code == 200
    rows = store.list_export_history(session_id)
    assert {row["format"] for row in rows} == {"pdf", "json"}
