from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace

from fastapi.testclient import TestClient

from app.main import app
from app.routes.web import _get_history_bank_service, _resolve_history_bank_pdf_path
from app.services.pdf_parser import RoutedPdfParser


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
