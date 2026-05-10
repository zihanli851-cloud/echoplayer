from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
import time

from fastapi.testclient import TestClient

from app.main import app
from app.routes.web import _get_history_bank_service, _resolve_history_bank_pdf_path
from app.services.history_bank_jobs import HistoryBankRefreshJobStore
from app.services.pdf_parser import RoutedPdfParser
from app.services.review_store import ReviewStore


@dataclass
class FakeHistorySnapshot:
    bank_dir: str
    total_files: int = 0
    loaded_files: int = 0
    failed_files: int = 0
    question_count: int = 0

    def to_summary(self) -> dict:
        return {
            "bank_dir": self.bank_dir,
            "total_files": self.total_files,
            "loaded_files": self.loaded_files,
            "failed_files": self.failed_files,
            "question_count": self.question_count,
            "papers": [
                {
                    "paper_id": "H1",
                    "filename": "history.pdf",
                    "paper_label": "history",
                    "subject": "chinese",
                    "relative_path": "history.pdf",
                    "page_count": 2,
                    "question_count": 3,
                }
            ],
            "failures": [],
        }


class FakeHistoryBankService:
    def __init__(self, bank_dir: Path) -> None:
        self.bank_dir = bank_dir
        self.refresh_flags: list[bool] = []
        self.invalidated = False

    def get_snapshot(self, *, force_refresh: bool = False) -> FakeHistorySnapshot:
        self.refresh_flags.append(force_refresh)
        pdf_count = len(list(self.bank_dir.glob("*.pdf"))) if self.bank_dir.exists() else 1
        return FakeHistorySnapshot(
            bank_dir=str(self.bank_dir),
            total_files=pdf_count,
            loaded_files=pdf_count,
            question_count=pdf_count * 3,
        )

    def invalidate_cache(self) -> None:
        self.invalidated = True


class FailingHistoryBankService(FakeHistoryBankService):
    def get_snapshot(self, *, force_refresh: bool = False) -> FakeHistorySnapshot:
        self.refresh_flags.append(force_refresh)
        raise RuntimeError("boom")


def test_history_bank_page_renders_summary(tmp_path) -> None:
    fake_service = FakeHistoryBankService(tmp_path)

    with TestClient(app) as client:
        client.app.state.history_bank_dir = tmp_path
        client.app.state.history_bank_service = fake_service

        response = client.get("/history-bank?refresh=true")

    assert response.status_code == 200
    assert "历史题库管理" in response.text
    assert str(tmp_path) in response.text
    assert "history.pdf" in response.text
    assert fake_service.refresh_flags == [True]


def test_history_bank_upload_saves_pdfs_and_skips_other_files(tmp_path) -> None:
    fake_service = FakeHistoryBankService(tmp_path)

    with TestClient(app) as client:
        client.app.state.history_bank_dir = tmp_path
        client.app.state.history_bank_service = fake_service

        response = client.post(
            "/history-bank/upload",
            files=[
                ("files", ("历史 A.pdf", b"%PDF-1.4 fake", "application/pdf")),
                ("files", ("notes.txt", b"not a pdf", "text/plain")),
            ],
        )

    assert response.status_code == 200
    assert (tmp_path / "历史 A.pdf").exists()
    assert not (tmp_path / "notes.pdf").exists()
    assert "已上传 1 份历史题库 PDF" in response.text
    assert "已跳过非 PDF 文件：notes.txt" in response.text
    assert fake_service.refresh_flags == [False]
    assert fake_service.invalidated is True


def test_history_bank_upload_generates_unique_names(tmp_path) -> None:
    (tmp_path / "same.pdf").write_bytes(b"existing")
    fake_service = FakeHistoryBankService(tmp_path)

    with TestClient(app) as client:
        client.app.state.history_bank_dir = tmp_path
        client.app.state.history_bank_service = fake_service

        response = client.post(
            "/history-bank/upload",
            files=[
                ("files", ("same.pdf", b"%PDF-1.4 first", "application/pdf")),
                ("files", ("same.pdf", b"%PDF-1.4 second", "application/pdf")),
            ],
        )

    assert response.status_code == 200
    assert (tmp_path / "same.pdf").read_bytes() == b"existing"
    assert (tmp_path / "same_2.pdf").exists()
    assert (tmp_path / "same_3.pdf").exists()


def test_history_bank_delete_removes_pdf_and_refreshes_cache(tmp_path) -> None:
    target = tmp_path / "delete-me.pdf"
    target.write_bytes(b"%PDF-1.4")
    fake_service = FakeHistoryBankService(tmp_path)

    with TestClient(app) as client:
        client.app.state.history_bank_dir = tmp_path
        client.app.state.history_bank_service = fake_service

        response = client.post(
            "/history-bank/delete",
            data={"relative_path": "delete-me.pdf"},
        )

    assert response.status_code == 200
    assert not target.exists()
    assert "已删除历史题库文件：delete-me.pdf" in response.text
    assert fake_service.refresh_flags == [False]
    assert fake_service.invalidated is True


def test_history_bank_delete_rejects_path_traversal(tmp_path) -> None:
    outside = tmp_path.parent / "outside.pdf"
    outside.write_bytes(b"%PDF-1.4")
    try:
        with TestClient(app) as client:
            client.app.state.history_bank_dir = tmp_path
            client.app.state.history_bank_service = FakeHistoryBankService(tmp_path)

            response = client.post(
                "/history-bank/delete",
                data={"relative_path": "../outside.pdf"},
            )

        assert response.status_code == 200
        assert outside.exists()
        assert "无效的历史题库文件路径" in response.text
    finally:
        outside.unlink(missing_ok=True)


def test_history_bank_rebuild_runs_in_background_and_reports_result(tmp_path) -> None:
    (tmp_path / "history.pdf").write_bytes(b"%PDF-1.4")
    fake_service = FakeHistoryBankService(tmp_path)

    with TestClient(app) as client:
        client.app.state.history_bank_dir = tmp_path
        client.app.state.history_bank_service = fake_service

        submit_response = client.post("/history-bank/rebuild")
        assert submit_response.status_code == 200
        job_id = submit_response.json()["job_id"]

        payload = {}
        for _ in range(20):
            job_response = client.get(f"/api/history-bank/jobs/{job_id}")
            assert job_response.status_code == 200
            payload = job_response.json()
            if payload["status"] == "completed":
                break
            time.sleep(0.05)

    assert payload["status"] == "completed"
    assert payload["result"]["total_files"] == 1
    assert payload["result"]["question_count"] == 3
    assert fake_service.refresh_flags == [True]


def test_history_bank_job_api_returns_404_for_missing_job(tmp_path) -> None:
    with TestClient(app) as client:
        client.app.state.history_bank_dir = tmp_path
        response = client.get("/api/history-bank/jobs/missing")

    assert response.status_code == 404


def test_history_bank_job_status_falls_back_to_persisted_store(tmp_path) -> None:
    (tmp_path / "history.pdf").write_bytes(b"%PDF-1.4")
    fake_service = FakeHistoryBankService(tmp_path)
    review_store = ReviewStore(tmp_path / "echopaper.db")

    with TestClient(app) as client:
        client.app.state.history_bank_dir = tmp_path
        client.app.state.history_bank_service = fake_service
        client.app.state.review_store = review_store
        client.app.state.history_bank_job_store = HistoryBankRefreshJobStore(review_store=review_store)

        submit_response = client.post("/history-bank/rebuild")
        assert submit_response.status_code == 200
        job_id = submit_response.json()["job_id"]

        payload = {}
        for _ in range(20):
            job_response = client.get(f"/api/history-bank/jobs/{job_id}")
            assert job_response.status_code == 200
            payload = job_response.json()
            if payload["status"] == "completed":
                break
            time.sleep(0.05)

        client.app.state.history_bank_job_store = HistoryBankRefreshJobStore(review_store=review_store)
        fallback_response = client.get(f"/api/history-bank/jobs/{job_id}")

    assert payload["status"] == "completed"
    assert fallback_response.status_code == 200
    fallback_payload = fallback_response.json()
    assert fallback_payload["status"] == "completed"
    assert fallback_payload["result"]["question_count"] == 3


def test_history_bank_page_lists_recent_rebuild_jobs(tmp_path) -> None:
    fake_service = FakeHistoryBankService(tmp_path)
    review_store = ReviewStore(tmp_path / "echopaper.db")
    review_store.upsert_history_bank_job_summary(
        {
            "job_id": "job-history-1",
            "status": "completed",
            "created_at": "2026-01-01T00:00:00",
            "updated_at": "2026-01-01T00:00:01",
            "error": "",
            "has_result": True,
        },
        result_summary={
            "total_files": 2,
            "loaded_files": 2,
            "failed_files": 0,
            "question_count": 6,
            "papers": [],
            "failures": [],
        },
    )

    with TestClient(app) as client:
        client.app.state.history_bank_dir = tmp_path
        client.app.state.history_bank_service = fake_service
        client.app.state.review_store = review_store

        response = client.get("/history-bank")

    assert response.status_code == 200
    assert "最近重建任务" in response.text
    assert "job-history-1" in response.text
    assert "题目 6 道" in response.text


def test_history_bank_failed_job_is_persisted_and_listed(tmp_path) -> None:
    failing_service = FailingHistoryBankService(tmp_path)
    review_store = ReviewStore(tmp_path / "echopaper.db")

    with TestClient(app) as client:
        client.app.state.history_bank_dir = tmp_path
        client.app.state.history_bank_service = failing_service
        client.app.state.review_store = review_store
        client.app.state.history_bank_job_store = HistoryBankRefreshJobStore(review_store=review_store)

        submit_response = client.post("/history-bank/rebuild")
        assert submit_response.status_code == 200
        job_id = submit_response.json()["job_id"]

        payload = {}
        for _ in range(20):
            job_response = client.get(f"/api/history-bank/jobs/{job_id}")
            assert job_response.status_code == 200
            payload = job_response.json()
            if payload["status"] == "failed":
                break
            time.sleep(0.05)

        client.app.state.history_bank_job_store = HistoryBankRefreshJobStore(review_store=review_store)
        fallback_response = client.get(f"/api/history-bank/jobs/{job_id}")
        page_response = client.get("/history-bank")

    assert payload["status"] == "failed"
    assert payload["error"] == "boom"
    assert fallback_response.status_code == 200
    assert fallback_response.json()["status"] == "failed"
    assert page_response.status_code == 200
    assert job_id in page_response.text
    assert "boom" in page_response.text


def test_resolve_history_bank_pdf_path_rejects_non_pdf(tmp_path) -> None:
    note = tmp_path / "note.txt"
    note.write_text("not a pdf", encoding="utf-8")

    try:
        _resolve_history_bank_pdf_path(tmp_path, "note.txt")
    except ValueError as exc:
        assert "PDF" in str(exc)
    else:
        raise AssertionError("expected non-PDF path to be rejected")


def test_history_bank_service_defaults_to_routed_parser(tmp_path) -> None:
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(history_bank_dir=tmp_path)))

    service = _get_history_bank_service(request)

    assert isinstance(service.extraction_provider, RoutedPdfParser)
