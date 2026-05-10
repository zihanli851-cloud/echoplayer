from app.services.pdf_parser import (
    AgentPdfParser,
    PdfTextSnapshot,
    RoutedPdfParser,
    TextExtractionProvider,
    _build_page_text_with_image_placeholders,
    _build_parser_note,
)
from app.services.ocr import OcrError, OcrProvider
import app.services.pdf_parser as pdf_parser_module


class FakeProvider(TextExtractionProvider):
    provider_name = "fake_provider"
    provider_label = "Fake Provider"
    provider_note = "本地解析提示。"

    def extract(self, pdf_path):
        return "1. 第一题", 1


class FakeOcrProvider(OcrProvider):
    def __init__(self, text: str, *, error: str = "") -> None:
        self.text = text
        self.error = error
        self.calls = 0

    def extract_text(self, pdf_path):
        self.calls += 1
        if self.error:
            raise OcrError(self.error)
        return self.text


def test_build_page_text_with_image_placeholders_keeps_text_and_images() -> None:
    text = "1. 阅读图片并回答问题。"
    images = [
        {"x0": 12.345, "top": 20, "x1": 300.6, "bottom": 420.2},
        {"x0": 40, "top": 80, "x1": 120, "bottom": 160},
    ]

    page_text = _build_page_text_with_image_placeholders(text, images, page_number=2)

    assert "1. 阅读图片并回答问题。" in page_text
    assert "[IMAGE page=2 index=1 bbox=12.35,20,300.6,420.2]" in page_text
    assert "[IMAGE page=2 index=2 bbox=40,80,120,160]" in page_text


def test_build_page_text_with_image_placeholders_keeps_image_only_page() -> None:
    page_text = _build_page_text_with_image_placeholders(
        "",
        [{"x0": 1, "top": 2, "x1": 3, "bottom": 4}],
        page_number=1,
    )

    assert page_text == "[IMAGE page=1 index=1 bbox=1,2,3,4]"


def test_build_parser_note_marks_text_pdf() -> None:
    snapshot = PdfTextSnapshot(
        text="1. 第一题内容",
        page_count=1,
        text_char_count=20,
        image_count=0,
    )

    assert _build_parser_note(snapshot, ocr_threshold=50) == "该 PDF 可直接提取文字。"


def test_build_parser_note_marks_image_rich_pdf_as_ocr_candidate() -> None:
    snapshot = PdfTextSnapshot(
        text="少量文字\n[IMAGE page=1 index=1 bbox=1,2,3,4]",
        page_count=2,
        text_char_count=4,
        image_count=3,
    )

    note = _build_parser_note(snapshot, ocr_threshold=50)

    assert "疑似扫描版或图片题较多" in note
    assert "已保留图片占位符" in note


def test_build_parser_note_marks_image_only_pdf_without_ocr_claim() -> None:
    snapshot = PdfTextSnapshot(
        text="[IMAGE page=1 index=1 bbox=1,2,3,4]",
        page_count=1,
        text_char_count=0,
        image_count=1,
    )

    note = _build_parser_note(snapshot, ocr_threshold=50)

    assert "未提取到文字" in note
    assert "需要 OCR" in note


def test_agent_pdf_parser_keeps_fallback_parser_note() -> None:
    parser = AgentPdfParser(fallback_provider=FakeProvider())

    text, page_count = parser.extract("fake.pdf")

    assert text == "1. 第一题"
    assert page_count == 1
    assert "Agent 流程先复用本地 PDF 解析" in parser.provider_note
    assert "本地解析提示" in parser.provider_note


def test_routed_pdf_parser_accumulates_notes_for_multiple_papers(monkeypatch, tmp_path) -> None:
    snapshots = [
        PdfTextSnapshot(text="1. 第一题", page_count=1, text_char_count=20, image_count=0),
        PdfTextSnapshot(text="[IMAGE page=1 index=1 bbox=1,2,3,4]", page_count=1, text_char_count=0, image_count=1),
    ]

    def fake_extract(_pdf_path):
        return snapshots.pop(0)

    monkeypatch.setattr(pdf_parser_module, "_extract_snapshot_with_pdfplumber", fake_extract)
    parser = RoutedPdfParser()

    parser.extract(tmp_path / "a.pdf")
    parser.extract(tmp_path / "b.pdf")

    assert "a.pdf: 该 PDF 可直接提取文字" in parser.provider_note
    assert "b.pdf: 该 PDF 未提取到文字" in parser.provider_note


def test_routed_pdf_parser_appends_ocr_text_for_scan_candidate(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(
        pdf_parser_module,
        "_extract_snapshot_with_pdfplumber",
        lambda _pdf_path: PdfTextSnapshot(
            text="[IMAGE page=1 index=1 bbox=1,2,3,4]",
            page_count=1,
            text_char_count=0,
            image_count=1,
        ),
    )
    ocr_provider = FakeOcrProvider("1. OCR 识别出的题干")
    parser = RoutedPdfParser(ocr_provider=ocr_provider)

    text, page_count = parser.extract(tmp_path / "scan.pdf")

    assert page_count == 1
    assert "[IMAGE page=1 index=1 bbox=1,2,3,4]" in text
    assert "[OCR_TEXT]" in text
    assert "OCR 识别出的题干" in text
    assert ocr_provider.calls == 1
    assert "OCR 已识别图片内文字" in parser.provider_note
    assert "OCR 未接入" not in parser.provider_note


def test_routed_pdf_parser_reports_missing_ocr_provider(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(
        pdf_parser_module,
        "_extract_snapshot_with_pdfplumber",
        lambda _pdf_path: PdfTextSnapshot(
            text="[IMAGE page=1 index=1 bbox=1,2,3,4]",
            page_count=1,
            text_char_count=0,
            image_count=1,
        ),
    )
    parser = RoutedPdfParser()

    text, _page_count = parser.extract(tmp_path / "scan.pdf")

    assert text == "[IMAGE page=1 index=1 bbox=1,2,3,4]"
    assert "OCR Provider 未配置" in parser.provider_note


def test_routed_pdf_parser_reports_ocr_failure_without_losing_placeholders(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(
        pdf_parser_module,
        "_extract_snapshot_with_pdfplumber",
        lambda _pdf_path: PdfTextSnapshot(
            text="[IMAGE page=1 index=1 bbox=1,2,3,4]",
            page_count=1,
            text_char_count=0,
            image_count=1,
        ),
    )
    parser = RoutedPdfParser(ocr_provider=FakeOcrProvider("", error="依赖缺失"))

    text, _page_count = parser.extract(tmp_path / "scan.pdf")

    assert text == "[IMAGE page=1 index=1 bbox=1,2,3,4]"
    assert "OCR 调用失败：依赖缺失" in parser.provider_note
