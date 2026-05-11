from __future__ import annotations

from dataclasses import asdict, dataclass
import json
from pathlib import Path
import re

from app.models.schemas import Question, UploadedPaper
from app.services.ocr import build_ocr_provider_from_env
from app.services.pdf_parser import PdfParseError, RoutedPdfParser, TextExtractionProvider
from app.services.question_splitter import RuleQuestionSplitter, normalize_formula_glyphs


QUESTION_START_MARKER = "###QUESTION###"
QUESTION_END_MARKER = "###END###"
DEFAULT_NL_TOKEN = " [NL] "
GENERIC_PARENT_NAMES = {
    "data",
    "datasets",
    "desktop",
    "downloads",
    "echopaper",
    "echoplayer",
    "history_bank",
    "history_bank_verify_tmp",
    "history_bank_verify_tmp2",
    "temp",
    "tmp",
    "st",
    "users",
}
PAPER_SIDE_NAMES = {"a", "b"}


@dataclass(slots=True)
class HistoryBankExportRecord:
    source_pdf: str
    output_txt: str | None
    paper_label: str
    subject: str
    page_count: int
    question_count: int
    status: str
    error: str = ""

    def to_dict(self) -> dict[str, str | int]:
        return asdict(self)


def infer_subject(pdf_path: Path) -> str:
    from_filename = _infer_subject_from_filename(pdf_path.stem)
    if from_filename:
        return from_filename

    parents = [pdf_path.parent] if str(pdf_path.parent) not in {"", "."} else []
    for parent in parents:
        name = parent.name.strip()
        if not name:
            continue
        if name.lower() in GENERIC_PARENT_NAMES:
            continue
        if _looks_like_year_term(name):
            continue
        return name
    return "unknown"


def export_pdf_to_txt(
    pdf_path: Path,
    output_path: Path,
    *,
    extraction_provider: TextExtractionProvider | None = None,
    split_provider: RuleQuestionSplitter | None = None,
    subject_override: str | None = None,
    paper_id: str = "H1",
    nl_token: str = DEFAULT_NL_TOKEN,
) -> HistoryBankExportRecord:
    extraction_provider = extraction_provider or RoutedPdfParser(ocr_provider=build_ocr_provider_from_env())
    split_provider = split_provider or RuleQuestionSplitter()
    subject = (subject_override or infer_subject(pdf_path)).strip() or "unknown"
    paper_label = pdf_path.stem

    try:
        text_content, page_count = extraction_provider.extract(pdf_path)
        text_content = normalize_formula_glyphs(text_content)
        paper = UploadedPaper(
            paper_id=paper_id,
            filename=pdf_path.name,
            subject=subject,
            temp_path=str(pdf_path),
            text_content=text_content,
            page_count=page_count,
        )
        questions = split_provider.split(text_content, paper_id, paper=paper)
    except (PdfParseError, Exception) as exc:
        return HistoryBankExportRecord(
            source_pdf=str(pdf_path),
            output_txt=None,
            paper_label=paper_label,
            subject=subject,
            page_count=0,
            question_count=0,
            status="failed",
            error=str(exc),
        )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        build_history_document(paper=paper, questions=questions, source_pdf=pdf_path, nl_token=nl_token),
        encoding="utf-8",
    )

    return HistoryBankExportRecord(
        source_pdf=str(pdf_path),
        output_txt=str(output_path),
        paper_label=paper_label,
        subject=subject,
        page_count=paper.page_count,
        question_count=len(questions),
        status="ok",
    )


def export_pdf_tree_to_txt(
    input_root: Path,
    output_root: Path,
    *,
    extraction_provider: TextExtractionProvider | None = None,
    split_provider: RuleQuestionSplitter | None = None,
    subject_override: str | None = None,
    nl_token: str = DEFAULT_NL_TOKEN,
    limit: int | None = None,
    progress_callback=None,
) -> list[HistoryBankExportRecord]:
    records: list[HistoryBankExportRecord] = []
    pdf_files = sorted(input_root.rglob("*.pdf"), key=lambda current: str(current).lower())
    if limit is not None and limit > 0:
        pdf_files = pdf_files[:limit]
    total = len(pdf_files)

    for index, pdf_path in enumerate(pdf_files, start=1):
        if progress_callback is not None:
            progress_callback(index, total, pdf_path)
        relative_path = pdf_path.relative_to(input_root)
        output_path = (output_root / relative_path).with_suffix(".txt")
        records.append(
            export_pdf_to_txt(
                pdf_path,
                output_path,
                extraction_provider=extraction_provider,
                split_provider=split_provider,
                subject_override=subject_override,
                paper_id=f"H{index}",
                nl_token=nl_token,
            )
        )
    return records


def build_history_document(
    *,
    paper: UploadedPaper,
    questions: list[Question],
    source_pdf: Path,
    nl_token: str = DEFAULT_NL_TOKEN,
) -> str:
    paper_label = source_pdf.stem
    subject = paper.subject or "unknown"

    if not questions:
        return (
            _build_question_line(
                paper_id=paper.paper_id,
                paper_label=paper_label,
                subject=subject,
                question_no="1",
                order=1,
                content=paper.text_content,
                nl_token=nl_token,
            )
            + "\n"
        )

    return (
        "\n".join(
            _build_question_line(
                paper_id=paper.paper_id,
                paper_label=paper_label,
                subject=subject,
                question_no=question.question_no,
                order=question.order,
                content=question.content,
                nl_token=nl_token,
            )
            for question in questions
        ).strip()
        + "\n"
    )


def write_manifest(records: list[HistoryBankExportRecord], manifest_path: Path) -> None:
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "total": len(records),
        "succeeded": sum(1 for record in records if record.status == "ok"),
        "failed": sum(1 for record in records if record.status != "ok"),
        "items": [record.to_dict() for record in records],
    }
    manifest_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def sanitize_block(text: str) -> str:
    normalized = text.replace("\r\n", "\n").replace("\r", "\n").replace("\u3000", " ")
    normalized = normalize_formula_glyphs(normalized)
    normalized = re.sub(r"[ \t]+", " ", normalized)
    normalized = re.sub(r"\n{3,}", "\n\n", normalized)
    lines = [line.strip() for line in normalized.split("\n")]
    return "\n".join(line for line in lines if line).strip()


def _build_question_line(
    *,
    paper_id: str,
    paper_label: str,
    subject: str,
    question_no: str,
    order: int,
    content: str,
    nl_token: str,
) -> str:
    flattened_content = sanitize_block(content).replace("\n", nl_token)
    return (
        f"{QUESTION_START_MARKER} "
        f"paper_id: {paper_id} | "
        f"paper_label: {paper_label} | "
        f"subject: {subject} | "
        f"question_no: {question_no} | "
        f"order: {order} | "
        f"content: {flattened_content} "
        f"{QUESTION_END_MARKER}"
    )


def _infer_subject_from_filename(stem: str) -> str:
    parts = [part.strip() for part in stem.split("+") if part.strip()]
    if len(parts) >= 3:
        candidate = parts[2]
        if candidate.lower() not in PAPER_SIDE_NAMES and not _looks_like_year_term(candidate):
            return candidate
    return ""


def _looks_like_year_term(value: str) -> bool:
    compact = re.sub(r"\s+", "", value)
    return bool(re.fullmatch(r"[（]?\d{2,4}-\d{2,4}-\d[）]?", compact))
