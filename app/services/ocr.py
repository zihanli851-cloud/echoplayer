from __future__ import annotations

from abc import ABC, abstractmethod
import os
from pathlib import Path


class OcrError(Exception):
    """Raised when OCR is configured but cannot complete."""


class OcrProvider(ABC):
    provider_name = "unknown_ocr"
    provider_label = "未命名 OCR"

    @abstractmethod
    def extract_text(self, pdf_path: Path) -> str:
        """Extract OCR text from a PDF."""


class TesseractOcrProvider(OcrProvider):
    """Optional OCR provider backed by pdf2image + pytesseract."""

    provider_name = "tesseract_ocr"
    provider_label = "Tesseract OCR"

    def __init__(self, *, dpi: int = 200, lang: str = "chi_sim+eng") -> None:
        self.dpi = dpi
        self.lang = lang

    def extract_text(self, pdf_path: Path) -> str:
        try:
            from pdf2image import convert_from_path
            import pytesseract
        except ImportError as exc:
            raise OcrError(
                "OCR_ENGINE=tesseract 需要安装 pytesseract 和 pdf2image，并配置 Tesseract/Poppler。"
            ) from exc

        try:
            images = convert_from_path(str(pdf_path), dpi=self.dpi)
            page_texts = [
                pytesseract.image_to_string(image, lang=self.lang).strip()
                for image in images
            ]
        except Exception as exc:
            raise OcrError(f"Tesseract OCR 识别失败：{exc}") from exc

        return "\n\n".join(text for text in page_texts if text).strip()


class UnavailableOcrProvider(OcrProvider):
    provider_name = "unavailable_ocr"
    provider_label = "不可用 OCR"

    def __init__(self, message: str) -> None:
        self.message = message

    def extract_text(self, pdf_path: Path) -> str:
        raise OcrError(self.message)


def build_ocr_provider_from_env() -> OcrProvider | None:
    engine = os.getenv("OCR_ENGINE", "").strip().lower()
    if not engine or engine in {"none", "off", "disabled", "false", "0"}:
        return None

    if engine == "tesseract":
        return TesseractOcrProvider(
            dpi=_read_int_env("OCR_DPI", default=200),
            lang=os.getenv("OCR_LANG", "chi_sim+eng").strip() or "chi_sim+eng",
        )

    return UnavailableOcrProvider(f"不支持的 OCR_ENGINE：{engine}")


def _read_int_env(name: str, *, default: int) -> int:
    raw_value = os.getenv(name, "").strip()
    if not raw_value:
        return default
    try:
        return int(raw_value)
    except ValueError:
        return default
