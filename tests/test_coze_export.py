import json
from pathlib import Path

import app.services.coze_export as coze_export_module
from app.models.schemas import Question, UploadedPaper
from app.services.coze_export import (
    CozeExportRecord,
    build_coze_document,
    export_pdf_to_coze_txt,
    export_pdf_tree_to_coze_txt,
    infer_subject,
    sanitize_block,
    write_manifest,
)
from app.services.pdf_parser import TextExtractionProvider


class FakeExtractionProvider(TextExtractionProvider):
    def __init__(self, text: str, page_count: int = 1) -> None:
        self.text = text
        self.page_count = page_count

    def extract(self, pdf_path: Path) -> tuple[str, int]:
        return self.text, self.page_count


class FakeDefaultRoutedProvider(TextExtractionProvider):
    def __init__(self, *, ocr_provider=None) -> None:
        self.ocr_provider = ocr_provider

    def extract(self, pdf_path: Path) -> tuple[str, int]:
        return "1. 默认路由解析题目", 1


def test_build_coze_document_outputs_one_question_per_line() -> None:
    paper = UploadedPaper(
        paper_id="H1",
        filename="history.pdf",
        subject="离散数学",
        temp_path="history.pdf",
        text_content="1. 原始文本",
        page_count=1,
    )
    questions = [
        Question(
            question_id="H1-1",
            paper_id="H1",
            question_no="1",
            order=1,
            content="证明 p\uf0aeq。\nA. 正确",
            raw_block="证明 p\uf0aeq。\nA. 正确",
        ),
        Question(
            question_id="H1-2",
            paper_id="H1",
            question_no="2",
            order=2,
            content="判断 A\uf0ceB。",
            raw_block="判断 A\uf0ceB。",
        ),
    ]

    document = build_coze_document(paper=paper, questions=questions, source_pdf=Path("history.pdf"))
    lines = document.strip().splitlines()

    assert len(lines) == 2
    assert lines[0].startswith("###QUESTION### paper_id: H1")
    assert "subject: 离散数学" in lines[0]
    assert "p → q" in lines[0]
    assert " [NL] A. 正确" in lines[0]
    assert "A ∈ B" in lines[1]
    assert lines[1].endswith("###END###")


def test_build_coze_document_preserves_image_placeholders() -> None:
    paper = UploadedPaper(
        paper_id="H1",
        filename="image-paper.pdf",
        subject="语文",
        temp_path="image-paper.pdf",
        text_content="[IMAGE page=1 index=1 bbox=1,2,3,4]",
        page_count=1,
    )

    document = build_coze_document(paper=paper, questions=[], source_pdf=Path("image-paper.pdf"))

    assert "[IMAGE page=1 index=1 bbox=1,2,3,4]" in document
    assert "content: [IMAGE page=1 index=1 bbox=1,2,3,4]" in document


def test_export_pdf_to_coze_txt_uses_project_splitter_and_writes_file(tmp_path) -> None:
    pdf_path = tmp_path / "2025+A+离散数学.pdf"
    pdf_path.write_bytes(b"%PDF fake")
    output_path = tmp_path / "out" / "history.coze.txt"
    provider = FakeExtractionProvider("一、选择题\n1. 判断 p\uf0aeq。\n2. 判断 A\uf0ceB。", page_count=3)

    record = export_pdf_to_coze_txt(
        pdf_path,
        output_path,
        extraction_provider=provider,
        paper_id="H7",
    )

    text = output_path.read_text(encoding="utf-8")
    assert record.status == "ok"
    assert record.subject == "离散数学"
    assert record.page_count == 3
    assert record.question_count == 2
    assert "paper_id: H7" in text
    assert "p → q" in text
    assert "A ∈ B" in text


def test_export_pdf_to_coze_txt_defaults_to_routed_parser(monkeypatch, tmp_path) -> None:
    pdf_path = tmp_path / "history.pdf"
    pdf_path.write_bytes(b"%PDF fake")
    output_path = tmp_path / "out.coze.txt"

    monkeypatch.setattr(coze_export_module, "RoutedPdfParser", FakeDefaultRoutedProvider)
    monkeypatch.setattr(coze_export_module, "build_ocr_provider_from_env", lambda: "ocr-provider")

    record = export_pdf_to_coze_txt(pdf_path, output_path)

    assert record.status == "ok"
    assert "默认路由解析题目" in output_path.read_text(encoding="utf-8")


def test_write_manifest_summarizes_export_records(tmp_path) -> None:
    manifest_path = tmp_path / "manifest.json"
    records = [
        CozeExportRecord("a.pdf", "a.coze.txt", "a", "语文", 1, 2, "ok"),
        CozeExportRecord("b.pdf", None, "b", "数学", 0, 0, "failed", "no text"),
    ]

    write_manifest(records, manifest_path)
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))

    assert payload["total"] == 2
    assert payload["succeeded"] == 1
    assert payload["failed"] == 1
    assert payload["items"][1]["error"] == "no text"


def test_export_pdf_tree_supports_limit_and_progress_callback(tmp_path) -> None:
    input_dir = tmp_path / "pdfs"
    input_dir.mkdir()
    for name in ("a.pdf", "b.pdf", "c.pdf"):
        (input_dir / name).write_bytes(b"%PDF fake")
    output_dir = tmp_path / "out"
    seen: list[tuple[int, int, str]] = []

    records = export_pdf_tree_to_coze_txt(
        input_dir,
        output_dir,
        extraction_provider=FakeExtractionProvider("1. 第一题"),
        limit=2,
        progress_callback=lambda index, total, path: seen.append((index, total, path.name)),
    )

    assert len(records) == 2
    assert seen == [(1, 2, "a.pdf"), (2, 2, "b.pdf")]
    assert (output_dir / "a.coze.txt").exists()
    assert (output_dir / "b.coze.txt").exists()
    assert not (output_dir / "c.coze.txt").exists()


def test_infer_subject_prefers_filename_then_parent(tmp_path) -> None:
    assert infer_subject(Path("2025+A+离散数学.pdf")) == "离散数学"

    subject_dir = tmp_path / "线性代数"
    subject_dir.mkdir()
    assert infer_subject(subject_dir / "期末试卷.pdf") == "线性代数"


def test_infer_subject_ignores_generic_project_parent() -> None:
    assert infer_subject(Path("echoplayer") / "history_bank" / "期末试卷.pdf") == "unknown"


def test_sanitize_block_removes_empty_lines_and_repairs_formula_glyphs() -> None:
    text = "  判断 p\uf0aeq。  \n\n\n A. 正确 "

    assert sanitize_block(text) == "判断 p → q。\nA. 正确"
