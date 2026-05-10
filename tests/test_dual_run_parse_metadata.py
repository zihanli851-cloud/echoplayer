from pathlib import Path

from app.models.schemas import Question, UploadedPaper
from app.services.comparator import SimilarityComparatorProvider
from app.services.dual_run import ReviewPipeline
from app.services.pdf_parser import TextExtractionProvider
from app.services.question_splitter import QuestionSplitProvider
from app.services.spellcheck.base import SpellcheckProvider


class FakeExtractionProvider(TextExtractionProvider):
    provider_name = "fake_extract"
    provider_label = "Fake Extract"

    def __init__(self) -> None:
        self.provider_note = ""
        self.last_note = "该 PDF 包含图片对象，建议人工复核。"
        self.last_snapshot = type(
            "Snapshot",
            (),
            {
                "image_count": 2,
                "ocr_attempted": True,
                "ocr_succeeded": False,
            },
        )()

    def extract(self, pdf_path: Path) -> tuple[str, int]:
        return "[IMAGE page=1 index=1 bbox=1,2,3,4]", 1


class FakeSplitProvider(QuestionSplitProvider):
    provider_name = "fake_split"
    provider_label = "Fake Split"

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


class FakeCompareProvider(SimilarityComparatorProvider):
    provider_name = "fake_compare"
    provider_label = "Fake Compare"

    def compare(
        self,
        paper_a_questions,
        paper_b_questions=None,
        history_questions=None,
        *,
        uploaded_papers=None,
    ):
        return []


class FakeSpellcheckProvider(SpellcheckProvider):
    provider_name = "fake_spell"
    provider_label = "Fake Spell"

    def check_questions(self, paper, questions):
        return []


def test_review_pipeline_attaches_parse_metadata_to_uploaded_papers() -> None:
    pipeline = ReviewPipeline(
        pipeline_name="代码版",
        extraction_provider=FakeExtractionProvider(),
        split_provider=FakeSplitProvider(),
        compare_provider=FakeCompareProvider(),
        spellcheck_provider=FakeSpellcheckProvider(),
    )
    uploaded_papers = [
        UploadedPaper(
            paper_id="A",
            filename="scan.pdf",
            subject="语文",
            temp_path="scan.pdf",
        )
    ]

    result = pipeline.run(uploaded_papers)

    paper = result.uploaded_papers[0]
    assert paper.image_count == 2
    assert paper.ocr_attempted is True
    assert paper.ocr_succeeded is False
    assert paper.requires_manual_review is True
    assert "建议人工复核" in paper.parse_note
