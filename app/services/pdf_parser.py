from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path


class PdfParseError(Exception):
    """Raised when PDF text extraction fails in a user-facing way."""


class TextExtractionProvider(ABC):
    """Provider interface for code-based or Agent-based PDF text extraction."""

    provider_name = "unknown"
    provider_label = "未命名解析器"
    is_placeholder = False
    provider_note = ""

    @abstractmethod
    def extract(self, pdf_path: Path) -> tuple[str, int]:
        """Extract text and page count from a PDF path."""


class CodePdfParser(TextExtractionProvider):
    """Default code implementation backed by pdfplumber."""

    provider_name = "code_pdf_parser"
    provider_label = "代码版文本解析"
    is_placeholder = False

    def extract(self, pdf_path: Path) -> tuple[str, int]:
        return _extract_text_with_pdfplumber(pdf_path)


class AgentPdfParser(TextExtractionProvider):
    """
    Agent-side PDF parser.

    The Coze workflow chain still relies on local PDF text extraction first,
    so the Agent pipeline intentionally reuses the same parser implementation.
    """

    provider_name = "agent_pdf_parser"
    provider_label = "Agent 版文本解析"
    is_placeholder = False
    provider_note = "Agent 流程先复用本地 PDF 解析，再调用 Coze 工作流。"

    def __init__(self, fallback_provider: TextExtractionProvider | None = None) -> None:
        self.fallback_provider = fallback_provider or CodePdfParser()

    def extract(self, pdf_path: Path) -> tuple[str, int]:
        return self.fallback_provider.extract(pdf_path)


def extract_text_from_pdf(pdf_path: Path) -> tuple[str, int]:
    """
    Extract plain text from a PDF with pdfplumber.

    Returns:
        tuple[str, int]: (joined_text, page_count)

    Raises:
        PdfParseError: When the file is missing, unreadable, or no text can be extracted.
    """

    return CodePdfParser().extract(pdf_path)


def _extract_text_with_pdfplumber(pdf_path: Path) -> tuple[str, int]:
    """Internal helper used by the code parser implementation."""

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
        with pdfplumber.open(str(pdf_path)) as pdf:
            page_count = len(pdf.pages)
            for page in pdf.pages:
                text = page.extract_text() or ""
                if text.strip():
                    page_texts.append(text.strip())

        if not page_texts:
            raise PdfParseError(
                f"{pdf_path.name} 未提取到可用文本。当前 MVP 仅支持可直接提取文本的 PDF。"
            )

        return "\n\n".join(page_texts), page_count
    except PdfParseError:
        raise
    except Exception as exc:
        raise PdfParseError(f"{pdf_path.name} 解析失败：{exc}") from exc
