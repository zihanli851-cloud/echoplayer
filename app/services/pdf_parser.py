from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
import os

from app.services.ocr import OcrError, OcrProvider


class PdfParseError(Exception):
    """Raised when PDF text extraction fails in a user-facing way."""


class TextExtractionProvider(ABC):
    """Provider interface for local PDF text extraction."""

    provider_name = "unknown"
    provider_label = "未知解析器"
    is_placeholder = False
    provider_note = ""

    @abstractmethod
    def extract(self, pdf_path: Path) -> tuple[str, int]:
        """Extract text and page count from a PDF path."""


@dataclass(slots=True)
class PdfTextSnapshot:
    text: str
    page_count: int
    text_char_count: int
    image_count: int
    ocr_attempted: bool = False
    ocr_succeeded: bool = False


class CodePdfParser(TextExtractionProvider):
    """Default code implementation backed by pdfplumber."""

    provider_name = "code_pdf_parser"
    provider_label = "代码版文本解析"
    is_placeholder = False

    def extract(self, pdf_path: Path) -> tuple[str, int]:
        snapshot = _extract_snapshot_with_pdfplumber(pdf_path)
        return snapshot.text, snapshot.page_count


class RoutedPdfParser(TextExtractionProvider):
    """PDF parser router that detects text PDFs versus image-heavy PDFs."""

    provider_name = "routed_pdf_parser"
    provider_label = "代码版解析路由器"
    is_placeholder = False

    def __init__(
        self,
        *,
        ocr_threshold: int | None = None,
        ocr_provider: OcrProvider | None = None,
    ) -> None:
        self.ocr_threshold = _resolve_ocr_threshold(ocr_threshold)
        self.ocr_provider = ocr_provider
        self.provider_note = ""
        self.last_note = ""
        self._paper_notes: dict[str, str] = {}
        self.last_snapshot: PdfTextSnapshot | None = None

    def extract(self, pdf_path: Path) -> tuple[str, int]:
        snapshot = _extract_snapshot_with_pdfplumber(pdf_path)
        note = _build_parser_note(snapshot, self.ocr_threshold)
        if _should_run_ocr(snapshot, self.ocr_threshold):
            snapshot, ocr_note = self._try_ocr(pdf_path, snapshot)
            note = f"{note}{ocr_note}"
        self.last_snapshot = snapshot
        self.last_note = note
        self._paper_notes[pdf_path.name] = note
        self.provider_note = "；".join(
            f"{name}: {self._paper_notes[name]}" for name in sorted(self._paper_notes)
        )
        return snapshot.text, snapshot.page_count

    def _try_ocr(self, pdf_path: Path, snapshot: PdfTextSnapshot) -> tuple[PdfTextSnapshot, str]:
        snapshot = PdfTextSnapshot(
            text=snapshot.text,
            page_count=snapshot.page_count,
            text_char_count=snapshot.text_char_count,
            image_count=snapshot.image_count,
            ocr_attempted=True,
            ocr_succeeded=False,
        )
        if self.ocr_provider is None:
            return snapshot, " OCR Provider 未配置，已回退到图片占位符。"

        try:
            ocr_text = self.ocr_provider.extract_text(pdf_path)
        except OcrError as exc:
            return snapshot, f" OCR 调用失败：{exc}"

        if not ocr_text.strip():
            return snapshot, " OCR 已调用，但未识别到可用文字。"

        merged_text = "\n\n".join(part for part in (snapshot.text, "[OCR_TEXT]\n" + ocr_text.strip()) if part.strip())
        return (
            PdfTextSnapshot(
                text=merged_text,
                page_count=snapshot.page_count,
                text_char_count=snapshot.text_char_count + len(ocr_text.strip()),
                image_count=snapshot.image_count,
                ocr_attempted=True,
                ocr_succeeded=True,
            ),
            " OCR 已识别图片内文字，并追加到文本末尾。",
        )


def extract_text_from_pdf(pdf_path: Path) -> tuple[str, int]:
    return CodePdfParser().extract(pdf_path)


def _extract_snapshot_with_pdfplumber(pdf_path: Path) -> PdfTextSnapshot:
    if not pdf_path.exists():
        raise PdfParseError(f"未找到 PDF 文件：{pdf_path.name}")

    try:
        try:
            import pdfplumber
        except ImportError as exc:
            raise PdfParseError(
                "未安装 pdfplumber，无法解析 PDF。请先执行 `pip install -r requirements.txt`。"
            ) from exc

        page_texts: list[str] = []
        text_char_count = 0
        image_count = 0
        with pdfplumber.open(str(pdf_path)) as pdf:
            page_count = len(pdf.pages)
            for page_number, page in enumerate(pdf.pages, start=1):
                text = page.extract_text() or ""
                text_char_count += len(text.strip())
                images = getattr(page, "images", []) or []
                image_count += len(images)
                page_text = _build_page_text_with_image_placeholders(text, images, page_number=page_number)
                if page_text:
                    page_texts.append(page_text)

        if not page_texts:
            raise PdfParseError(
                f"{pdf_path.name} 未提取到可用文本。当前 MVP 仅支持可直接提取文本的 PDF。"
            )

        return PdfTextSnapshot(
            text="\n\n".join(page_texts),
            page_count=page_count,
            text_char_count=text_char_count,
            image_count=image_count,
        )
    except PdfParseError:
        raise
    except Exception as exc:
        raise PdfParseError(f"{pdf_path.name} 解析失败：{exc}") from exc


def _build_page_text_with_image_placeholders(
    text: str,
    images: list[dict],
    *,
    page_number: int,
) -> str:
    parts: list[str] = []
    if text.strip():
        parts.append(text.strip())

    for image_index, image in enumerate(images, start=1):
        parts.append(_format_image_placeholder(page_number, image_index, image))

    return "\n".join(parts).strip()


def _format_image_placeholder(page_number: int, image_index: int, image: dict) -> str:
    bbox = _format_image_bbox(image)
    return f"[IMAGE page={page_number} index={image_index} bbox={bbox}]"


def _format_image_bbox(image: dict) -> str:
    def value(key: str) -> float:
        raw_value = image.get(key, 0)
        try:
            return round(float(raw_value), 2)
        except (TypeError, ValueError):
            return 0.0

    return ",".join(
        str(item).rstrip("0").rstrip(".")
        for item in (
            value("x0"),
            value("top"),
            value("x1"),
            value("bottom"),
        )
    )


def _resolve_ocr_threshold(ocr_threshold: int | None) -> int:
    if ocr_threshold is not None:
        return max(0, ocr_threshold)
    raw_value = os.getenv("OCR_FALLBACK_THRESHOLD", "").strip()
    if not raw_value:
        return 50
    try:
        return max(0, int(raw_value))
    except ValueError:
        return 50


def _build_parser_note(snapshot: PdfTextSnapshot, ocr_threshold: int) -> str:
    if snapshot.page_count <= 0:
        return "PDF 未解析出有效页数。"

    chars_per_page = snapshot.text_char_count / snapshot.page_count
    if snapshot.text_char_count == 0 and snapshot.image_count > 0:
        return (
            "该 PDF 未提取到文字，但检测到图片对象；当前仅保留图片占位符，"
            "需要 OCR 才能识别图片内文字。"
        )
    if snapshot.image_count > 0 and chars_per_page < ocr_threshold:
        return (
            f"该 PDF 平均每页可提取文字约 {chars_per_page:.1f} 字，且包含图片对象，"
            "疑似扫描版或图片题较多；当前已保留图片占位符。"
        )
    if snapshot.image_count > 0:
        return "该 PDF 包含图片对象，已在文本中保留 [IMAGE ...] 占位符。"
    return "该 PDF 可直接提取文本。"


def _should_run_ocr(snapshot: PdfTextSnapshot, ocr_threshold: int) -> bool:
    if snapshot.page_count <= 0 or snapshot.image_count <= 0:
        return False
    chars_per_page = snapshot.text_char_count / snapshot.page_count
    return snapshot.text_char_count == 0 or chars_per_page < ocr_threshold
