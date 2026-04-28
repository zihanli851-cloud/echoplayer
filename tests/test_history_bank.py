from pathlib import Path

from app.models.schemas import Question
from app.services.history_bank import HistoryBankService
from app.services.pdf_parser import PdfParseError, TextExtractionProvider
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
