from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from app.models.schemas import Question, SimilarityMatch, SpellcheckIssue, UploadedPaper
from app.services.comparator import SimilarityComparatorProvider
from app.services.pdf_parser import TextExtractionProvider
from app.services.question_splitter import QuestionSplitProvider
from app.services.spellcheck.base import SpellcheckProvider


@dataclass
class PipelineRunResult:
    """Holds the structured output of one local pipeline run."""

    pipeline_name: str
    uploaded_papers: list[UploadedPaper] = field(default_factory=list)
    questions: list[Question] = field(default_factory=list)
    similarity_matches: list[SimilarityMatch] = field(default_factory=list)
    spellcheck_issues: list[SpellcheckIssue] = field(default_factory=list)
    module_metadata: dict[str, dict] = field(default_factory=dict)
    history_bank_summary: dict = field(default_factory=dict)


class ReviewPipeline:
    """Runs the local code pipeline over the uploaded papers."""

    def __init__(
        self,
        *,
        pipeline_name: str,
        extraction_provider: TextExtractionProvider,
        split_provider: QuestionSplitProvider,
        compare_provider: SimilarityComparatorProvider,
        spellcheck_provider: SpellcheckProvider,
    ) -> None:
        self.pipeline_name = pipeline_name
        self.extraction_provider = extraction_provider
        self.split_provider = split_provider
        self.compare_provider = compare_provider
        self.spellcheck_provider = spellcheck_provider

    def run(
        self,
        uploaded_papers: list[UploadedPaper],
        *,
        history_questions: list[Question] | None = None,
        history_bank_summary: dict | None = None,
    ) -> PipelineRunResult:
        papers = [paper.model_copy(deep=True) for paper in uploaded_papers]
        questions_by_paper: dict[str, list[Question]] = {}

        for paper in papers:
            text_content, page_count = self.extraction_provider.extract(Path(paper.temp_path))
            paper.text_content = text_content
            paper.page_count = page_count
            paper.parse_note = str(
                getattr(
                    self.extraction_provider,
                    "last_note",
                    getattr(self.extraction_provider, "provider_note", ""),
                )
                or ""
            ).strip()
            snapshot = getattr(self.extraction_provider, "last_snapshot", None)
            if snapshot is not None:
                paper.image_count = int(getattr(snapshot, "image_count", 0) or 0)
                paper.ocr_attempted = bool(getattr(snapshot, "ocr_attempted", False))
                paper.ocr_succeeded = bool(getattr(snapshot, "ocr_succeeded", False))
            paper.requires_manual_review = bool(
                paper.image_count > 0 or (paper.ocr_attempted and not paper.ocr_succeeded)
            )
            questions_by_paper[paper.paper_id] = self.split_provider.split(
                paper.text_content,
                paper.paper_id,
                paper=paper,
            )

        all_questions = [
            question
            for paper_id in sorted(questions_by_paper.keys())
            for question in questions_by_paper[paper_id]
        ]

        similarity_matches = self.compare_provider.compare(
            questions_by_paper.get("A", []),
            questions_by_paper.get("B", []),
            history_questions,
            uploaded_papers=papers,
        )

        spellcheck_issues: list[SpellcheckIssue] = []
        for paper in papers:
            spellcheck_issues.extend(
                self.spellcheck_provider.check_questions(
                    paper,
                    questions_by_paper.get(paper.paper_id, []),
                )
            )

        return PipelineRunResult(
            pipeline_name=self.pipeline_name,
            uploaded_papers=papers,
            questions=all_questions,
            similarity_matches=similarity_matches,
            spellcheck_issues=spellcheck_issues,
            module_metadata={
                "extract": _provider_metadata(self.extraction_provider),
                "split": _provider_metadata(self.split_provider),
                "compare": _provider_metadata(self.compare_provider),
                "spellcheck": _provider_metadata(self.spellcheck_provider),
            },
            history_bank_summary=history_bank_summary or {},
        )


def _provider_metadata(provider) -> dict:
    return {
        "provider_name": getattr(provider, "provider_name", provider.__class__.__name__),
        "provider_label": getattr(provider, "provider_label", provider.__class__.__name__),
        "is_placeholder": bool(getattr(provider, "is_placeholder", False)),
        "provider_note": getattr(provider, "provider_note", ""),
    }
