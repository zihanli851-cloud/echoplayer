import json
from pathlib import Path

from app.models.schemas import Question
from app.services.history_bank_rebuilder import rebuild_history_bank_from_pdf
from app.services.pdf_parser import PdfParseError, TextExtractionProvider
from app.services.question_splitter import QuestionSplitProvider


class FakeExtractionProvider(TextExtractionProvider):
    provider_name = "fake_rebuild_extract"
    provider_label = "Fake Rebuild Extract"

    def extract(self, pdf_path: Path) -> tuple[str, int]:
        if "broken" in pdf_path.name:
            raise PdfParseError(f"{pdf_path.name} 无法提取文本")
        return "某大学期末考试\n课程：离散数学\n一、判断题\n（1）判断命题。", 3


class FakeSplitProvider(QuestionSplitProvider):
    provider_name = "fake_rebuild_split"
    provider_label = "Fake Rebuild Split"

    def split(self, text: str, paper_id: str, *, paper=None) -> list[Question]:
        return [
            Question(
                question_id=f"{paper_id}-1",
                paper_id=paper_id,
                question_no="1",
                order=1,
                content="一、判断题\n（1）判断命题。",
                raw_block="一、判断题\n（1）判断命题。",
            )
        ]


def test_rebuild_history_bank_creates_txt_backup_and_manifest(tmp_path) -> None:
    root_dir = tmp_path / "historicdatabase"
    pdf_dir = root_dir / "pdf"
    txt_dir = root_dir / "txt"
    pdf_dir.mkdir(parents=True)
    txt_dir.mkdir(parents=True)

    pdf_path = pdf_dir / "2025+A+离散数学.pdf"
    pdf_path.write_bytes(b"%PDF-1.4 fake")
    existing_txt_path = txt_dir / "2025+A+离散数学.coze.txt"
    existing_txt_path.write_text("old content", encoding="utf-8")

    result = rebuild_history_bank_from_pdf(
        root_dir,
        extraction_provider=FakeExtractionProvider(),
        split_provider=FakeSplitProvider(),
    )

    assert result.total_pdfs == 1
    assert result.rebuilt == 1
    assert result.failed == 0
    assert result.backup_dir is not None

    rebuilt_text = existing_txt_path.read_text(encoding="utf-8")
    assert "###QUESTION###" in rebuilt_text
    assert "subject: 离散数学" in rebuilt_text

    backup_path = Path(result.backup_dir) / "2025+A+离散数学.coze.txt"
    assert backup_path.exists()
    assert backup_path.read_text(encoding="utf-8") == "old content"

    manifest = json.loads(Path(result.manifest_path).read_text(encoding="utf-8"))
    assert manifest["run_id"] == result.run_id
    assert manifest["rebuilt"] == 1
    assert manifest["failed"] == 0
    assert manifest["items"][0]["backup_txt"] == str(backup_path)
    assert manifest["items"][0]["status"] == "ok"


def test_rebuild_history_bank_dry_run_does_not_write_files(tmp_path) -> None:
    root_dir = tmp_path / "historicdatabase"
    pdf_dir = root_dir / "pdf"
    txt_dir = root_dir / "txt"
    pdf_dir.mkdir(parents=True)
    txt_dir.mkdir(parents=True)

    pdf_path = pdf_dir / "2025+A+程序设计.pdf"
    pdf_path.write_bytes(b"%PDF-1.4 fake")
    existing_txt_path = txt_dir / "2025+A+程序设计.coze.txt"
    existing_txt_path.write_text("legacy content", encoding="utf-8")

    result = rebuild_history_bank_from_pdf(
        root_dir,
        extraction_provider=FakeExtractionProvider(),
        split_provider=FakeSplitProvider(),
        dry_run=True,
    )

    assert result.total_pdfs == 1
    assert result.rebuilt == 1
    assert result.failed == 0
    assert result.backup_dir is None
    assert not Path(result.manifest_path).exists()
    assert existing_txt_path.read_text(encoding="utf-8") == "legacy content"
    assert len(list(txt_dir.rglob("*.txt"))) == 1


def test_rebuild_history_bank_uses_source_key_to_match_existing_txt(tmp_path) -> None:
    root_dir = tmp_path / "historicdatabase"
    pdf_dir = root_dir / "pdf"
    txt_dir = root_dir / "txt" / "archive"
    pdf_dir.mkdir(parents=True)
    txt_dir.mkdir(parents=True)

    pdf_path = pdf_dir / "2025+A+数据结构.pdf"
    pdf_path.write_bytes(b"%PDF-1.4 fake")
    existing_txt_path = txt_dir / "2025+A+数据结构.coze.txt"
    existing_txt_path.write_text("legacy archive", encoding="utf-8")

    result = rebuild_history_bank_from_pdf(
        root_dir,
        extraction_provider=FakeExtractionProvider(),
        split_provider=FakeSplitProvider(),
    )

    assert result.records[0].backup_txt is not None
    backup_path = Path(result.records[0].backup_txt)
    assert backup_path.exists()
    assert backup_path.read_text(encoding="utf-8") == "legacy archive"


def test_rebuild_history_bank_marks_failed_pdf_in_manifest(tmp_path) -> None:
    root_dir = tmp_path / "historicdatabase"
    pdf_dir = root_dir / "pdf"
    pdf_dir.mkdir(parents=True)

    good_pdf = pdf_dir / "2025+A+线性代数.pdf"
    broken_pdf = pdf_dir / "2025+A+broken+编译原理.pdf"
    good_pdf.write_bytes(b"%PDF-1.4 good")
    broken_pdf.write_bytes(b"%PDF-1.4 bad")

    result = rebuild_history_bank_from_pdf(
        root_dir,
        extraction_provider=FakeExtractionProvider(),
        split_provider=FakeSplitProvider(),
    )

    assert result.total_pdfs == 2
    assert result.rebuilt == 1
    assert result.failed == 1
    assert any(record.status == "failed" for record in result.records)

    manifest = json.loads(Path(result.manifest_path).read_text(encoding="utf-8"))
    failed_items = [item for item in manifest["items"] if item["status"] == "failed"]
    assert len(failed_items) == 1
    assert "无法提取文本" in failed_items[0]["error"]
