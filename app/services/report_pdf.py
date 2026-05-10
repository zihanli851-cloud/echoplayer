from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from io import BytesIO
import re
import textwrap


PAGE_WIDTH = 595
PAGE_HEIGHT = 842
MARGIN_X = 48
MAX_LINES_PER_PAGE = 43


@dataclass(slots=True)
class PdfExportResult:
    filename: str
    content: bytes


def build_report_pdf(export_payload: dict) -> PdfExportResult:
    """Build a compact PDF summary from the report export payload."""

    report = export_payload.get("report", {}) if isinstance(export_payload, dict) else {}
    teacher_name = str(report.get("teacher_name") or "unknown")
    subject = str(report.get("subject") or "unknown")
    generated_at = str(report.get("generated_at") or datetime.now().isoformat(timespec="seconds"))
    filename = _safe_pdf_filename(f"EchoPaper_{teacher_name}_{subject}_{generated_at[:10]}.pdf")

    lines = _build_report_lines(export_payload)
    return PdfExportResult(filename=filename, content=_render_pdf_lines(lines))


def _build_report_lines(payload: dict) -> list[str]:
    report = payload.get("report", {}) if isinstance(payload, dict) else {}
    dashboard = report.get("dashboard", {}) if isinstance(report, dict) else {}
    papers = report.get("uploaded_papers", []) if isinstance(report, dict) else []
    duplicate_context = payload.get("duplicate_comparison", {}) if isinstance(payload, dict) else {}
    spellcheck_context = payload.get("spellcheck_comparison", {}) if isinstance(payload, dict) else {}
    history_bank = payload.get("history_bank", {}) if isinstance(payload, dict) else {}
    dual_run_sections = payload.get("dual_run_sections", []) if isinstance(payload, dict) else []
    same_source_matches = payload.get("same_source_matches", []) if isinstance(payload, dict) else []
    parse_quality = payload.get("parse_quality", []) if isinstance(payload, dict) else []
    question_quality = payload.get("question_quality", []) if isinstance(payload, dict) else []
    complex_question_quality = payload.get("complex_question_quality", []) if isinstance(payload, dict) else []

    lines = [
        "EchoPaper 智能审查报告",
        "",
        f"教师：{report.get('teacher_name', '')}（{report.get('teacher_id', '')}）",
        f"科目：{report.get('subject', '')}",
        f"生成时间：{_format_datetime(report.get('generated_at', ''))}",
        "",
        "一、总览",
        f"A 卷题目数：{dashboard.get('paper_a_question_count', 0)}",
        f"B 卷题目数：{dashboard.get('paper_b_question_count', 0)}",
        f"高度重复：{_sum_dashboard(dashboard, ['paper_a_internal_high', 'paper_b_internal_high', 'cross_paper_high', 'history_high'])}",
        f"疑似重复：{dashboard.get('suspected_duplicate_total', 0)}",
        f"疑似原题/同源题：{dashboard.get('same_source_total', 0)}",
        f"历史题库命中：{dashboard.get('history_match_total', 0)}",
        f"错字/标点问题：{dashboard.get('spellcheck_issue_total', 0)}",
        f"待人工复核：{dashboard.get('pending_review_total', 0)}",
        "",
        "二、上传试卷",
    ]

    if papers:
        for paper in papers:
            lines.append(
                f"- {paper.get('paper_id', '')}：{paper.get('filename', '')}，页数 {paper.get('page_count', 0)}"
            )
    else:
        lines.append("- 无上传试卷信息")

    lines.extend(["", "三、解析风险提示"])
    if parse_quality:
        for index, row in enumerate(parse_quality[:8], start=1):
            lines.extend(
                [
                    (
                        f"{index}. {row.get('paper_label', '')} | 风险：{row.get('risk_level', '')} | "
                        f"OCR：{row.get('ocr_status', '')} | 图片数：{row.get('image_count', 0)}"
                    ),
                    f"   文件：{row.get('filename', '')}",
                    f"   原因：{row.get('risk_reason', '')}",
                    f"   备注：{row.get('parse_note', '')}",
                ]
            )
        if len(parse_quality) > 8:
            lines.append(f"... 其余 {len(parse_quality) - 8} 条解析风险明细请查看页面 JSON。")
    else:
        lines.append("- 未发现需要额外提示的解析风险。")

    lines.extend(["", "四、低置信度切题提示"])
    if question_quality:
        for index, row in enumerate(question_quality[:8], start=1):
            lines.extend(
                [
                    (
                        f"{index}. {row.get('paper_label', '')}第 {row.get('question_no', '')} 题 | "
                        f"置信度：{row.get('confidence', '')}"
                    ),
                    f"   提示：{row.get('warning', '')}",
                    f"   预览：{row.get('content_preview', '')}",
                ]
            )
        if len(question_quality) > 8:
            lines.append(f"... 其余 {len(question_quality) - 8} 条切题提示请查看页面 JSON。")
    else:
        lines.append("- 未发现低置信度切题结果。")

    lines.extend(["", "五、疑似原题 / 同源题"])
    if same_source_matches:
        for index, row in enumerate(same_source_matches[:8], start=1):
            score = row.get("final_score", row.get("score", ""))
            lines.extend(
                [
                    (
                        f"{index}. {row.get('comparison_label', '')} | {row.get('same_source_flag', '')} | "
                        f"综合分：{score}% | 字面分：{row.get('literal_score', '')}% | "
                        f"模板分：{row.get('template_score', '')}%"
                    ),
                    f"   对比：{row.get('source_label', '')} -> {row.get('target_label', '')}",
                    f"   原因：{row.get('reason', '')}",
                    f"   复核：{row.get('review_status', '')}",
                ]
            )
        if len(same_source_matches) > 8:
            lines.append(f"... 其余 {len(same_source_matches) - 8} 条疑似原题结果请查看页面 JSON。")
    else:
        lines.append("- 未发现疑似原题或同源题。")

    lines.extend(["", "六、复杂题复核提示"])
    if complex_question_quality:
        for index, row in enumerate(complex_question_quality[:8], start=1):
            lines.extend(
                [
                    (
                        f"{index}. {row.get('paper_label', '')}第 {row.get('question_no', '')} 题 | "
                        f"{row.get('flag_summary', '')} | 风险：{row.get('review_level', '')}"
                    ),
                    f"   依据：{row.get('detail', '')}",
                    f"   原因：{row.get('reason', '')}",
                    f"   建议：{row.get('recommendation', '')}",
                ]
            )
        if len(complex_question_quality) > 8:
            lines.append(f"... 其余 {len(complex_question_quality) - 8} 条复杂题提示请查看页面 JSON。")
    else:
        lines.append("- 未发现需要单独提示的图片题、图表题或复杂公式题。")

    lines.extend(["", "七、查重摘要"])
    duplicate_summary = duplicate_context.get("summary", {}) if isinstance(duplicate_context, dict) else {}
    lines.extend(
        [
            f"代码/Agent 一致：{duplicate_summary.get('matched', 0)}",
            f"评分不同：{duplicate_summary.get('score_diff', 0)}",
            f"代码独有：{duplicate_summary.get('code_only', 0)}",
            f"Agent 新增：{duplicate_summary.get('agent_only', 0)}",
        ]
    )
    duplicate_rows = duplicate_context.get("code_rows", []) if isinstance(duplicate_context, dict) else []
    for index, row in enumerate(duplicate_rows[:8], start=1):
        lines.append(
            f"{index}. {row.get('comparison_label', '')} | {row.get('level', '')} | {row.get('score', '')}% | "
            f"{row.get('source_label', '')} -> {row.get('target_label', '')} | 复核：{row.get('review_status', '')}"
        )
    if len(duplicate_rows) > 8:
        lines.append(f"... 其余 {len(duplicate_rows) - 8} 条查重明细请查看页面 JSON。")

    lines.extend(["", "八、错字检查摘要"])
    spellcheck_summary = spellcheck_context.get("summary", {}) if isinstance(spellcheck_context, dict) else {}
    lines.extend(
        [
            f"代码/Agent 一致：{spellcheck_summary.get('matched', 0)}",
            f"代码独有：{spellcheck_summary.get('code_only', 0)}",
            f"Agent 新增：{spellcheck_summary.get('agent_only', 0)}",
        ]
    )
    spellcheck_rows = spellcheck_context.get("code_rows", []) if isinstance(spellcheck_context, dict) else []
    for index, row in enumerate(spellcheck_rows[:8], start=1):
        lines.append(
            f"{index}. {row.get('paper_label', '')}第 {row.get('question_no', '')} 题 | "
            f"{row.get('issue_type', '')}：{row.get('issue_text', '')} -> {row.get('suggestion', '')}"
        )
    if len(spellcheck_rows) > 8:
        lines.append(f"... 其余 {len(spellcheck_rows) - 8} 条错字明细请查看页面 JSON。")

    lines.extend(["", "九、历史题库"])
    if history_bank:
        lines.extend(
            [
                f"目录：{history_bank.get('bank_dir', '')}",
                f"PDF 文件：{history_bank.get('total_files', 0)}",
                f"成功加载：{history_bank.get('loaded_files', 0)}",
                f"题目总数：{history_bank.get('question_count', 0)}",
            ]
        )
        if history_bank.get("load_error"):
            lines.append(f"加载错误：{history_bank.get('load_error')}")
    else:
        lines.append("- 未提供历史题库摘要。")

    lines.extend(["", "十、双链路模块状态"])
    if dual_run_sections:
        for section in dual_run_sections:
            lines.append(
                f"- {section.get('module_name', '')}：{section.get('status', '')}；{section.get('diff_summary', '')}"
            )
    else:
        lines.append("- 未提供双链路对照信息。")

    return _wrap_lines(lines)


def _render_pdf_lines(lines: list[str]) -> bytes:
    pages = [lines[index : index + MAX_LINES_PER_PAGE] for index in range(0, len(lines), MAX_LINES_PER_PAGE)]
    if not pages:
        pages = [["EchoPaper 智能审查报告"]]

    objects: list[bytes] = []
    objects.append(b"<< /Type /Catalog /Pages 2 0 R >>")
    page_object_numbers = [3 + index * 2 for index in range(len(pages))]
    kids = b" ".join(f"{number} 0 R".encode("ascii") for number in page_object_numbers)
    objects.append(f"<< /Type /Pages /Kids [{kids.decode('ascii')}] /Count {len(pages)} >>".encode("ascii"))

    for page_index, page_lines in enumerate(pages):
        page_obj = page_object_numbers[page_index]
        content_obj = page_obj + 1
        objects.append(
            (
                f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {PAGE_WIDTH} {PAGE_HEIGHT}] "
                f"/Resources << /Font << /F1 << /Type /Font /Subtype /Type0 /BaseFont /STSong-Light "
                f"/Encoding /UniGB-UCS2-H /DescendantFonts [<< /Type /Font /Subtype /CIDFontType0 "
                f"/BaseFont /STSong-Light /CIDSystemInfo << /Registry (Adobe) /Ordering (GB1) /Supplement 2 >> >>] >> >> >> >> "
                f"/Contents {content_obj} 0 R >>"
            ).encode("ascii")
        )
        stream = _build_page_stream(page_lines, page_index + 1, len(pages))
        objects.append(b"<< /Length " + str(len(stream)).encode("ascii") + b" >>\nstream\n" + stream + b"\nendstream")

    buffer = BytesIO()
    buffer.write(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets = [0]
    for index, obj in enumerate(objects, start=1):
        offsets.append(buffer.tell())
        buffer.write(f"{index} 0 obj\n".encode("ascii"))
        buffer.write(obj)
        buffer.write(b"\nendobj\n")

    xref_offset = buffer.tell()
    buffer.write(f"xref\n0 {len(objects) + 1}\n".encode("ascii"))
    buffer.write(b"0000000000 65535 f \n")
    for offset in offsets[1:]:
        buffer.write(f"{offset:010d} 00000 n \n".encode("ascii"))
    buffer.write(
        (
            f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\n"
            f"startxref\n{xref_offset}\n%%EOF\n"
        ).encode("ascii")
    )
    return buffer.getvalue()


def _build_page_stream(lines: list[str], page_no: int, page_total: int) -> bytes:
    commands = ["BT", "/F1 11 Tf", "1 0 0 1 48 790 Tm", "17 TL"]
    for index, line in enumerate(lines):
        if index > 0:
            commands.append("T*")
        commands.append(f"<{_pdf_hex_text(line)}> Tj")
    commands.extend(
        [
            "ET",
            "BT",
            "/F1 9 Tf",
            f"1 0 0 1 {MARGIN_X} 28 Tm",
            f"<{_pdf_hex_text(f'第 {page_no} / {page_total} 页')}> Tj",
            "ET",
        ]
    )
    return "\n".join(commands).encode("ascii")


def _pdf_hex_text(text: str) -> str:
    return text.encode("utf-16-be", errors="replace").hex().upper()


def _wrap_lines(lines: list[str]) -> list[str]:
    wrapped: list[str] = []
    for line in lines:
        cleaned = _plain_text(str(line))
        if not cleaned:
            wrapped.append("")
            continue
        wrapped.extend(textwrap.wrap(cleaned, width=42, break_long_words=True, replace_whitespace=False))
    return wrapped


def _plain_text(value: str) -> str:
    value = re.sub(r"<[^>]+>", "", value)
    value = re.sub(r"\s+", " ", value)
    return value.strip()


def _sum_dashboard(dashboard: dict, keys: list[str]) -> int:
    return sum(int(dashboard.get(key, 0) or 0) for key in keys)


def _format_datetime(value: str) -> str:
    text = str(value or "").strip()
    if "T" in text:
        return text.replace("T", " ")[:19]
    return text


def _safe_pdf_filename(value: str) -> str:
    cleaned = "".join(char if char.isalnum() or char in {"-", "_", ".", " "} else "_" for char in value)
    cleaned = cleaned.strip(" ._") or "EchoPaper_report.pdf"
    if not cleaned.lower().endswith(".pdf"):
        cleaned = f"{cleaned}.pdf"
    return cleaned
