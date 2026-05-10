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
    "historicdatabase",
    "txt",
    "pdf",
}
PAPER_SIDE_NAMES = {"a", "b"}
QUESTION_BLOCK_PATTERN = re.compile(
    r"###QUESTION###\s*(.*?)\s*###END###",
    re.DOTALL,
)


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
                or keyword in str(paper.get("course", "")).lower()
            ]
            failures = [
                failure
                for failure in failures
                if keyword in str(failure.get("filename", "")).lower()
                or keyword in str(failure.get("paper_label", "")).lower()
                or keyword in str(failure.get("course", "")).lower()
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
    Loads and caches questions from a local history bank.

    Supports two layouts:
    - legacy single-directory PDF bank
    - dual-source `historicdatabase/{txt,pdf}` bank
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
        signature = self._build_signature()
        if not force_refresh and self._cached_snapshot and signature == self._cached_signature:
            return self._cached_snapshot
        snapshot = self._load_snapshot()
        self._cached_signature = signature
        self._cached_snapshot = snapshot
        return snapshot

    def get_cached_or_directory_summary(self) -> HistoryBankSnapshot:
        signature = self._build_signature()
        if self._cached_snapshot and signature == self._cached_signature:
            return self._cached_snapshot

        records = self._build_history_records()
        papers = [
            {
                "paper_id": "",
                "paper_label": record["paper_label"],
                "subject": record["subject"],
                "course": record["course"],
                "filename": record["filename"],
                "relative_path": record["relative_path"],
                "page_count": "未扫描",
                "question_count": "未扫描",
                "source_key": record["source_key"],
                "source_txt_path": record.get("source_txt_path"),
                "source_pdf_path": record.get("source_pdf_path"),
            }
            for record in records
            if record.get("source_pdf_path")
        ]
        return HistoryBankSnapshot(
            bank_dir=str(self.bank_dir),
            total_files=len(records),
            loaded_files=0,
            failed_files=0,
            question_count=0,
            papers=papers,
        )

    def invalidate_cache(self) -> None:
        self._cached_signature = None
        self._cached_snapshot = None

    def _load_snapshot(self) -> HistoryBankSnapshot:
        records = self._build_history_records()
        snapshot = HistoryBankSnapshot(
            bank_dir=str(self.bank_dir),
            total_files=len(records),
        )

        for index, record in enumerate(records, start=1):
            paper_id = f"H{index}"
            try:
                questions, page_count = self._load_record_questions(record, paper_id)
            except PdfParseError as exc:
                snapshot.failed_files += 1
                snapshot.failures.append(
                    {
                        "filename": record["filename"],
                        "paper_label": record["paper_label"],
                        "subject": record["subject"],
                        "course": record["course"],
                        "relative_path": record["relative_path"],
                        "reason": str(exc),
                    }
                )
                continue

            snapshot.loaded_files += 1
            snapshot.question_count += len(questions)
            snapshot.questions.extend(questions)
            snapshot.papers.append(
                {
                    "paper_id": paper_id,
                    "paper_label": record["paper_label"],
                    "subject": record["subject"],
                    "course": record["course"],
                    "filename": record["filename"],
                    "relative_path": record["relative_path"],
                    "page_count": page_count,
                    "question_count": len(questions),
                    "source_key": record["source_key"],
                    "source_txt_path": record.get("source_txt_path"),
                    "source_pdf_path": record.get("source_pdf_path"),
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
        records = self._build_history_records()
        signature: list[tuple[str, int, int]] = []
        for record in records:
            for path_key in ("source_txt_path", "source_pdf_path"):
                raw_path = record.get(path_key)
                if not raw_path:
                    continue
                path = Path(raw_path)
                if path.exists():
                    stat = path.stat()
                    signature.append((str(path), stat.st_size, stat.st_mtime_ns))
        return tuple(sorted(signature))

    def _build_history_records(self) -> list[dict]:
        dual_txt_dir = self.bank_dir / "txt"
        dual_pdf_dir = self.bank_dir / "pdf"
        if dual_txt_dir.exists() or dual_pdf_dir.exists():
            return _build_dual_source_records(self.bank_dir)

        pdf_files = sorted(self.bank_dir.rglob("*.pdf"), key=lambda path: path.name.lower())
        records: list[dict] = []
        for pdf_path in pdf_files:
            course = infer_history_subject(pdf_path, self.bank_dir)
            paper_label = pdf_path.stem
            records.append(
                {
                    "source_key": build_source_key(pdf_path.stem),
                    "paper_label": paper_label,
                    "subject": course,
                    "course": course,
                    "filename": pdf_path.name,
                    "relative_path": _relative_history_path(pdf_path, self.bank_dir),
                    "source_pdf_path": str(pdf_path),
                    "source_txt_path": None,
                }
            )
        return records

    def _load_record_questions(self, record: dict, paper_id: str) -> tuple[list[Question], int]:
        txt_path = record.get("source_txt_path")
        pdf_path = record.get("source_pdf_path")

        questions: list[Question] = []
        page_count = 0

        if txt_path:
            questions = _parse_history_txt_file(Path(txt_path), paper_id, record)

        if not questions and pdf_path:
            text_content, page_count = self.extraction_provider.extract(Path(pdf_path))
            questions = self.split_provider.split(
                text_content,
                paper_id,
                paper=UploadedPaper(
                    paper_id=paper_id,
                    filename=record["filename"],
                    subject=record["subject"],
                    temp_path=str(pdf_path),
                    text_content=text_content,
                    page_count=page_count,
                ),
            )

        normalized_questions = [
            question.model_copy(
                update={
                    "paper_label": record["paper_label"],
                    "source_key": record["source_key"],
                    "course": record["course"],
                    "source_txt_path": record.get("source_txt_path"),
                    "source_pdf_path": record.get("source_pdf_path"),
                }
            )
            for question in questions
        ]
        return normalized_questions, page_count


def build_source_key(value: str) -> str:
    compact = value.lower()
    compact = compact.replace(".coze", "")
    compact = compact.replace(".txt", "")
    compact = compact.replace(".pdf", "")
    compact = compact.replace("（", "(").replace("）", ")")
    compact = re.sub(r"\s+", "", compact)
    return compact


def infer_history_subject(pdf_path: Path, bank_dir: Path | None = None) -> str:
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
    return bool(re.fullmatch(r"[（(]?\d{2,4}-\d{2,4}-\d[)）]?", compact))


def _relative_history_path(path: Path, bank_dir: Path) -> str:
    try:
        return path.relative_to(bank_dir).as_posix()
    except ValueError:
        return path.name


def _build_dual_source_records(bank_dir: Path) -> list[dict]:
    txt_dir = bank_dir / "txt"
    pdf_dir = bank_dir / "pdf"
    record_map: dict[str, dict] = {}

    if txt_dir.exists():
        for txt_path in sorted(txt_dir.rglob("*.txt"), key=lambda path: path.name.lower()):
            source_key = build_source_key(txt_path.stem)
            course = infer_history_subject(txt_path, txt_dir)
            record = record_map.setdefault(
                source_key,
                {
                    "source_key": source_key,
                    "paper_label": txt_path.stem,
                    "subject": course,
                    "course": course,
                    "filename": txt_path.name,
                    "relative_path": _relative_history_path(txt_path, bank_dir),
                    "source_txt_path": None,
                    "source_pdf_path": None,
                },
            )
            record["source_txt_path"] = str(txt_path)
            record["filename"] = txt_path.name
            record["relative_path"] = _relative_history_path(txt_path, bank_dir)

    if pdf_dir.exists():
        for pdf_path in sorted(pdf_dir.rglob("*.pdf"), key=lambda path: path.name.lower()):
            source_key = build_source_key(pdf_path.stem)
            course = infer_history_subject(pdf_path, pdf_dir)
            record = record_map.setdefault(
                source_key,
                {
                    "source_key": source_key,
                    "paper_label": pdf_path.stem,
                    "subject": course,
                    "course": course,
                    "filename": pdf_path.name,
                    "relative_path": _relative_history_path(pdf_path, bank_dir),
                    "source_txt_path": None,
                    "source_pdf_path": None,
                },
            )
            record["paper_label"] = pdf_path.stem
            record["subject"] = record.get("subject") or course
            record["course"] = record.get("course") or course
            record["source_pdf_path"] = str(pdf_path)
            if not record.get("filename"):
                record["filename"] = pdf_path.name
            if not record.get("relative_path"):
                record["relative_path"] = _relative_history_path(pdf_path, bank_dir)

    return sorted(record_map.values(), key=lambda item: item["paper_label"].lower())


def _parse_history_txt_file(txt_path: Path, paper_id: str, record: dict) -> list[Question]:
    text = txt_path.read_text(encoding="utf-8", errors="ignore")
    blocks = QUESTION_BLOCK_PATTERN.findall(text)
    questions: list[Question] = []
    for order, block in enumerate(blocks, start=1):
        metadata = _parse_question_block(block)
        content = metadata.get("content", "").replace("[NL]", "\n").strip()
        if not content:
            continue
        question_no = str(metadata.get("question_no", order)).strip() or str(order)
        questions.append(
            Question(
                question_id=f"{paper_id}-{order}",
                paper_id=paper_id,
                paper_label=record["paper_label"],
                source_key=record["source_key"],
                course=record["course"],
                source_txt_path=str(txt_path),
                source_pdf_path=record.get("source_pdf_path"),
                question_no=question_no,
                order=order,
                content=content,
                raw_block=content,
            )
        )
    return questions


def _parse_question_block(block: str) -> dict[str, str]:
    metadata: dict[str, str] = {}
    for part in block.split("|"):
        if ":" not in part:
            continue
        key, value = part.split(":", 1)
        metadata[key.strip()] = value.strip()
    return metadata
