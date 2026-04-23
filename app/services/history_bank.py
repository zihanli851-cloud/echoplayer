from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from app.models.schemas import Question
from app.services.pdf_parser import CodePdfParser, PdfParseError, TextExtractionProvider
from app.services.question_splitter import QuestionSplitProvider, RuleQuestionSplitter


@dataclass
class HistoryBankSnapshot:
    """Cached representation of the local history bank directory."""

    bank_dir: str
    total_files: int = 0
    loaded_files: int = 0
    failed_files: int = 0
    question_count: int = 0
    questions: list[Question] = field(default_factory=list)
    papers: list[dict] = field(default_factory=list)
    failures: list[dict] = field(default_factory=list)

    def to_summary(self) -> dict:
        """Build a template-friendly summary without the full question payload."""

        return {
            "bank_dir": self.bank_dir,
            "total_files": self.total_files,
            "loaded_files": self.loaded_files,
            "failed_files": self.failed_files,
            "question_count": self.question_count,
            "papers": self.papers,
            "failures": self.failures,
        }


class HistoryBankService:
    """
    Loads and caches questions from the local `history_bank` directory.

    The MVP keeps this service intentionally simple:
    - only local PDF files are scanned
    - parse failures are recorded and skipped
    - results are cached in memory and refreshed when the directory changes
    """

    def __init__(
        self,
        bank_dir: Path,
        *,
        extraction_provider: TextExtractionProvider | None = None,
        split_provider: QuestionSplitProvider | None = None,
    ) -> None:
        self.bank_dir = bank_dir
        self.extraction_provider = extraction_provider or CodePdfParser()
        self.split_provider = split_provider or RuleQuestionSplitter()
        self._cached_signature: tuple | None = None
        self._cached_snapshot: HistoryBankSnapshot | None = None

    def get_snapshot(self, *, force_refresh: bool = False) -> HistoryBankSnapshot:
        """Return a cached snapshot and refresh it when the directory changed."""

        signature = self._build_signature()
        if not force_refresh and self._cached_snapshot and signature == self._cached_signature:
            return self._cached_snapshot

        snapshot = self._load_snapshot()
        self._cached_signature = signature
        self._cached_snapshot = snapshot
        return snapshot

    def _load_snapshot(self) -> HistoryBankSnapshot:
        """Scan the history bank directory and build normalized question data."""

        pdf_files = self._list_pdf_files()
        snapshot = HistoryBankSnapshot(
            bank_dir=str(self.bank_dir),
            total_files=len(pdf_files),
        )

        for index, pdf_path in enumerate(pdf_files, start=1):
            paper_id = f"H{index}"
            paper_label = pdf_path.stem

            try:
                text_content, page_count = self.extraction_provider.extract(pdf_path)
                questions = self.split_provider.split(text_content, paper_id)
            except PdfParseError as exc:
                snapshot.failed_files += 1
                snapshot.failures.append(
                    {
                        "filename": pdf_path.name,
                        "reason": str(exc),
                    }
                )
                continue

            normalized_questions = [
                question.model_copy(update={"paper_label": paper_label})
                for question in questions
            ]

            snapshot.loaded_files += 1
            snapshot.question_count += len(normalized_questions)
            snapshot.questions.extend(normalized_questions)
            snapshot.papers.append(
                {
                    "paper_id": paper_id,
                    "paper_label": paper_label,
                    "filename": pdf_path.name,
                    "page_count": page_count,
                    "question_count": len(normalized_questions),
                }
            )

        return snapshot

    def _build_signature(self) -> tuple:
        """Build a lightweight directory signature for cache invalidation."""

        pdf_files = self._list_pdf_files()
        return tuple(
            (path.name, path.stat().st_size, path.stat().st_mtime_ns)
            for path in pdf_files
        )

    def _list_pdf_files(self) -> list[Path]:
        """Return all PDF files from the history bank directory in stable order."""

        if not self.bank_dir.exists():
            return []
        return sorted(self.bank_dir.rglob("*.pdf"), key=lambda path: path.name.lower())
