from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import re

from app.models.schemas import Question, UploadedPaper
from app.services.history_vector_index import IndexedHistoryQuestions, build_or_load_history_vector_index
from app.services.ocr import build_ocr_provider_from_env
from app.services.pdf_parser import PdfParseError, RoutedPdfParser, TextExtractionProvider
from app.services.question_splitter import QuestionSplitProvider, RuleQuestionSplitter


GENERIC_HISTORY_PARENT_NAMES = {
    "data",
    "datasets",
    "history_bank",
    "history_bank_verify_tmp",
    "history_bank_verify_tmp2",
    "temp",
    "tmp",
}
PAPER_SIDE_NAMES = {"a", "b"}


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

    def filtered_summary(self, *, subject: str = "", keyword: str = "") -> dict:
        """Build a template summary filtered by subject and filename/label keyword."""

        summary = self.to_summary()
        subject = subject.strip()
        keyword = keyword.strip().lower()
        papers = list(summary["papers"])
        failures = list(summary["failures"])

        if subject:
            papers = [paper for paper in papers if str(paper.get("subject", "")) == subject]
            failures = [failure for failure in failures if str(failure.get("subject", "")) == subject]
        if keyword:
            papers = [
                paper
                for paper in papers
                if keyword in str(paper.get("filename", "")).lower()
                or keyword in str(paper.get("paper_label", "")).lower()
            ]
            failures = [
                failure
                for failure in failures
                if keyword in str(failure.get("filename", "")).lower()
                or keyword in str(failure.get("paper_label", "")).lower()
            ]

        subjects = sorted(
            {
                str(paper.get("subject", "")).strip()
                for paper in summary["papers"]
                if str(paper.get("subject", "")).strip()
            }
        )
        summary["subjects"] = subjects
        summary["papers"] = papers
        summary["failures"] = failures
        summary["visible_files"] = len(papers) + len(failures)
        summary["active_subject"] = subject
        summary["active_keyword"] = keyword
        return summary


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
        index_dir: Path | None = None,
    ) -> None:
        self.bank_dir = bank_dir
        self.extraction_provider = extraction_provider or RoutedPdfParser(ocr_provider=build_ocr_provider_from_env())
        self.split_provider = split_provider or RuleQuestionSplitter()
        self.index_dir = index_dir
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

    def get_cached_or_directory_summary(self) -> HistoryBankSnapshot:
        """
        Return the parsed cache when available, otherwise a fast file-list summary.

        The management page should not parse a large PDF bank just to render.
        Explicit refreshes and review runs still call `get_snapshot()`.
        """

        signature = self._build_signature()
        if self._cached_snapshot and signature == self._cached_signature:
            return self._cached_snapshot

        pdf_files = self._list_pdf_files()
        return HistoryBankSnapshot(
            bank_dir=str(self.bank_dir),
            total_files=len(pdf_files),
            loaded_files=0,
            failed_files=0,
            question_count=0,
            papers=[
                {
                    "paper_id": "",
                    "paper_label": pdf_path.stem,
                    "subject": infer_history_subject(pdf_path, self.bank_dir),
                    "filename": pdf_path.name,
                    "relative_path": _relative_history_path(pdf_path, self.bank_dir),
                    "page_count": "未扫描",
                    "question_count": "未扫描",
                }
                for pdf_path in pdf_files
            ],
        )

    def invalidate_cache(self) -> None:
        """Drop the parsed snapshot after file changes without parsing PDFs."""

        self._cached_signature = None
        self._cached_snapshot = None

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
            subject = infer_history_subject(pdf_path, self.bank_dir)
            relative_path = _relative_history_path(pdf_path, self.bank_dir)

            try:
                text_content, page_count = self.extraction_provider.extract(pdf_path)
                questions = self.split_provider.split(
                    text_content,
                    paper_id,
                    paper=UploadedPaper(
                        paper_id=paper_id,
                        filename=pdf_path.name,
                        subject=subject,
                        temp_path=str(pdf_path),
                        text_content=text_content,
                        page_count=page_count,
                    ),
                )
            except PdfParseError as exc:
                snapshot.failed_files += 1
                snapshot.failures.append(
                    {
                        "filename": pdf_path.name,
                        "paper_label": paper_label,
                        "subject": subject,
                        "relative_path": relative_path,
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
                    "subject": subject,
                    "filename": pdf_path.name,
                    "relative_path": relative_path,
                    "page_count": page_count,
                    "question_count": len(normalized_questions),
                }
            )

        self._attach_vector_index(snapshot)
        return snapshot

    def _attach_vector_index(self, snapshot: HistoryBankSnapshot) -> None:
        if not snapshot.questions or self.index_dir is None:
            return
        index_path = self.index_dir / "history_bank_lightweight_index.json"
        try:
            vector_index = build_or_load_history_vector_index(snapshot.questions, index_path=index_path)
        except (OSError, ValueError, TypeError):
            return
        snapshot.questions = IndexedHistoryQuestions(snapshot.questions, vector_index=vector_index)

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


def infer_history_subject(pdf_path: Path, bank_dir: Path | None = None) -> str:
    """Infer subject from the EchoPaper history filename convention."""

    from_filename = _infer_subject_from_filename(pdf_path.stem)
    if from_filename:
        return from_filename

    for parent in [pdf_path.parent]:
        if bank_dir is not None:
            try:
                if parent.resolve() == bank_dir.resolve():
                    continue
            except OSError:
                pass
        name = parent.name.strip()
        if not name:
            continue
        if name.lower() in GENERIC_HISTORY_PARENT_NAMES:
            continue
        if _looks_like_year_term(name):
            continue
        return name
    return "unknown"


def _infer_subject_from_filename(stem: str) -> str:
    parts = [part.strip() for part in stem.split("+") if part.strip()]
    if len(parts) >= 3:
        candidate = parts[2]
        if candidate.lower() not in PAPER_SIDE_NAMES and not _looks_like_year_term(candidate):
            return candidate
    return ""


def _looks_like_year_term(value: str) -> bool:
    compact = re.sub(r"\s+", "", value)
    return bool(re.fullmatch(r"[（(]?\d{2,4}-\d{2,4}-\d[）)]?", compact))


def _relative_history_path(pdf_path: Path, bank_dir: Path) -> str:
    try:
        return pdf_path.relative_to(bank_dir).as_posix()
    except ValueError:
        return pdf_path.name
