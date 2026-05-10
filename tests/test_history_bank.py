from pathlib import Path

from app.models.schemas import Question
from app.services.history_bank import HistoryBankService, build_source_key, infer_history_subject
from app.services.pdf_parser import PdfParseError, RoutedPdfParser, TextExtractionProvider
from app.services.question_splitter import QuestionSplitProvider


class FakeExtractionProvider(TextExtractionProvider):
    provider_name = "fake_history_extract"
    provider_label = "Fake History Extract"

    def extract(self, pdf_path: Path) -> tuple[str, int]:
        if "bad" in pdf_path.name:
            raise PdfParseError(f"{pdf_path.name} 无法提取文本")
        return "1. 历史题目", 2


class FakeSplitProvider(QuestionSplitProvider):
    provider_name = "fake_history_split"
    provider_label = "Fake History Split"

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


def test_history_bank_service_loads_successes_and_collects_failures(tmp_path) -> None:
    (tmp_path / "good.pdf").write_bytes(b"%PDF-1.4 good")
    (tmp_path / "bad.pdf").write_bytes(b"%PDF-1.4 bad")

    service = HistoryBankService(
        tmp_path,
        extraction_provider=FakeExtractionProvider(),
        split_provider=FakeSplitProvider(),
    )

    snapshot = service.get_snapshot()

    assert snapshot.total_files == 2
    assert snapshot.loaded_files == 1
    assert snapshot.failed_files == 1
    assert snapshot.question_count == 1
    assert snapshot.questions[0].paper_label == "good"
    assert snapshot.failures[0]["filename"] == "bad.pdf"


def test_history_bank_directory_summary_does_not_parse_pdfs(tmp_path) -> None:
    (tmp_path / "large.pdf").write_bytes(b"%PDF-1.4")

    class FailingExtractionProvider(FakeExtractionProvider):
        def extract(self, pdf_path: Path) -> tuple[str, int]:
            raise AssertionError("directory summary should not parse PDFs")

    service = HistoryBankService(
        tmp_path,
        extraction_provider=FailingExtractionProvider(),
        split_provider=FakeSplitProvider(),
    )

    snapshot = service.get_cached_or_directory_summary()

    assert snapshot.total_files == 1
    assert snapshot.loaded_files == 0
    assert snapshot.papers[0]["filename"] == "large.pdf"
    assert snapshot.papers[0]["page_count"] == "未扫描"


def test_history_bank_service_defaults_to_routed_parser(tmp_path) -> None:
    service = HistoryBankService(tmp_path)

    assert isinstance(service.extraction_provider, RoutedPdfParser)


def test_history_bank_summary_infers_subject_and_filters(tmp_path) -> None:
    (tmp_path / "2025+A+math.pdf").write_bytes(b"%PDF-1.4 math")
    (tmp_path / "2025+A+chinese.pdf").write_bytes(b"%PDF-1.4 chinese")

    service = HistoryBankService(
        tmp_path,
        extraction_provider=FakeExtractionProvider(),
        split_provider=FakeSplitProvider(),
    )

    snapshot = service.get_cached_or_directory_summary()
    summary = snapshot.filtered_summary(subject="math", keyword="2025")

    assert infer_history_subject(tmp_path / "2025+A+math.pdf", tmp_path) == "math"
    assert summary["subjects"] == ["chinese", "math"]
    assert len(summary["papers"]) == 1
    assert summary["papers"][0]["subject"] == "math"


def test_history_bank_service_attaches_persistent_vector_index(tmp_path) -> None:
    (tmp_path / "2025+A+math.pdf").write_bytes(b"%PDF-1.4 math")
    index_dir = tmp_path / "index"

    service = HistoryBankService(
        tmp_path,
        extraction_provider=FakeExtractionProvider(),
        split_provider=FakeSplitProvider(),
        index_dir=index_dir,
    )

    snapshot = service.get_snapshot()

    assert getattr(snapshot.questions, "vector_index", None) is not None
    assert (index_dir / "history_bank_lightweight_index.json").exists()


def test_history_bank_service_supports_dual_source_records(tmp_path) -> None:
    txt_dir = tmp_path / "txt"
    pdf_dir = tmp_path / "pdf"
    txt_dir.mkdir()
    pdf_dir.mkdir()
    txt_name = "（24-25-2）+计算机与人工智能学院+程序设计及应用（Java）+韩延明+A.coze.txt"
    pdf_name = "（24-25-2）+计算机与人工智能学院+程序设计及应用（Java）+韩延明+A.pdf"
    (txt_dir / txt_name).write_text(
        "###QUESTION### paper_id: H1 | question_no: 1 | content: 原始题目内容 ###END###",
        encoding="utf-8",
    )
    (pdf_dir / pdf_name).write_bytes(b"%PDF-1.4")

    service = HistoryBankService(
        tmp_path,
        extraction_provider=FakeExtractionProvider(),
        split_provider=FakeSplitProvider(),
    )

    snapshot = service.get_snapshot()

    assert snapshot.total_files == 1
    assert snapshot.question_count == 1
    assert snapshot.questions[0].source_txt_path is not None
    assert snapshot.questions[0].source_pdf_path is not None
    assert snapshot.questions[0].course == "程序设计及应用（Java）"
    assert build_source_key(txt_name) == build_source_key(pdf_name)
