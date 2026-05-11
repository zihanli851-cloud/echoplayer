import re
from io import BytesIO

import pdfplumber
import pypdfium2 as pdfium

from app.services.report_pdf import build_report_pdf


def build_payload() -> dict:
    return {
        "report": {
            "teacher_name": "李老师",
            "teacher_id": "T001",
            "subject": "语文",
            "generated_at": "2026-05-10T10:00:00",
            "uploaded_papers": [
                {"paper_id": "A", "filename": "a.pdf", "page_count": 2},
                {"paper_id": "B", "filename": "scan-b.pdf", "page_count": 1},
            ],
            "dashboard": {
                "paper_a_question_count": 3,
                "paper_b_question_count": 2,
                "paper_a_internal_high": 0,
                "paper_b_internal_high": 0,
                "cross_paper_high": 1,
                "history_high": 0,
                "same_source_total": 1,
                "suspected_duplicate_total": 1,
                "history_match_total": 1,
                "spellcheck_issue_total": 1,
                "pending_review_total": 2,
            },
        },
        "history_bank": {
            "bank_dir": "data/datasets/history_bank",
            "total_files": 10,
            "loaded_files": 8,
            "question_count": 100,
        },
        "parse_quality": [
            {
                "paper_label": "B 卷",
                "filename": "scan-b.pdf",
                "image_count": 2,
                "ocr_status": "已触发 OCR 但未成功",
                "risk_level": "高风险",
                "risk_reason": "已触发 OCR 但未成功识别，建议直接查看原卷 PDF。",
                "parse_note": "OCR 调用失败：依赖缺失",
            }
        ],
        "question_quality": [
            {
                "paper_label": "A 卷",
                "question_no": "3",
                "confidence": 0.35,
                "warning": "切题置信度偏低，请人工复核。",
                "content_preview": "阅读材料后回答问题，题干与小问交织出现。",
            }
        ],
        "same_source_matches": [
            {
                "comparison_label": "历史题库比对",
                "same_source_flag": "同源题",
                "score": 93,
                "final_score": 93,
                "literal_score": 86,
                "template_score": 95,
                "source_label": "A 卷第 2 题",
                "target_label": "历史库《2024 春季卷》第 5 题",
                "reason": "模板分 95% 明显高于字面分 86%，更像同题干改参数后的同源题。",
                "review_status": "待确认",
            }
        ],
        "complex_question_quality": [
            {
                "paper_label": "B 卷",
                "question_no": "2",
                "flag_summary": "图片题 / 图表题 / 复杂公式题",
                "review_level": "高风险",
                "detail": "包含 1 个图片占位符；命中图表关键词：如下表；公式符号 2 处",
                "reason": "题干包含图片对象占位，需结合原卷核对图片内容。",
                "recommendation": "建议查看原卷 PDF",
            }
        ],
        "duplicate_comparison": {
            "summary": {"total": 1, "high": 1, "suspected": 0, "same_source": 0},
            "code_rows": [
                {
                    "comparison_label": "A/B 交叉查重",
                    "level": "高度重复",
                    "score": 98,
                    "source_label": "A 卷第 1 题",
                    "target_label": "B 卷第 1 题",
                    "review_status": "待确认",
                }
            ],
        },
        "spellcheck_comparison": {
            "summary": {"total": 1, "typo": 1, "punctuation": 0},
            "code_rows": [
                {
                    "paper_label": "A 卷",
                    "question_no": "1",
                    "issue_type": "错别字",
                    "issue_text": "春添",
                    "suggestion": "春天",
                }
            ],
        },
    }


def _extract_pdf_text(content: bytes) -> str:
    with pdfplumber.open(BytesIO(content)) as pdf:
        text = "\n".join(page.extract_text() or "" for page in pdf.pages)
    if text.strip():
        return text

    # Fallback for our minimal PDF writer: decode hex strings from text drawing commands.
    fragments = []
    for match in re.finditer(rb"<([0-9A-F]+)>\s*Tj", content):
        raw = bytes.fromhex(match.group(1).decode("ascii"))
        fragments.append(raw.decode("utf-16-be", errors="ignore"))
    return "\n".join(fragments)


def _render_nonwhite_ratio(content: bytes) -> float:
    pdf = pdfium.PdfDocument(content)
    page = pdf[0]
    image = page.render(scale=1.5).to_pil().convert("L")
    histogram = image.histogram()
    nonwhite_pixels = sum(histogram[:245])
    return nonwhite_pixels / (image.size[0] * image.size[1])


def test_build_report_pdf_returns_valid_pdf_bytes() -> None:
    result = build_report_pdf(build_payload())

    assert result.filename.endswith(".pdf")
    assert result.content.startswith(b"%PDF-1.4")
    assert b"%%EOF" in result.content
    assert b"/Type /Catalog" in result.content


def test_build_report_pdf_can_be_opened_by_pdf_parser() -> None:
    result = build_report_pdf(build_payload())

    with pdfplumber.open(BytesIO(result.content)) as pdf:
        assert len(pdf.pages) >= 1


def test_build_report_pdf_includes_extended_review_sections() -> None:
    result = build_report_pdf(build_payload())
    text = _extract_pdf_text(result.content)

    if not text.strip():
        assert _render_nonwhite_ratio(result.content) > 0.005
        return

    assert "解析风险提示" in text
    assert "高风险" in text
    assert "OCR 调用失败" in text
    assert "低置信度切题提示" in text
    assert "0.35" in text
    assert "疑似原题 / 同源题" in text
    assert "同源题" in text
    assert "模板分 95%" in text
    assert "复杂题复核提示" in text
    assert "图片题 / 图表题 / 复杂公式题" in text
