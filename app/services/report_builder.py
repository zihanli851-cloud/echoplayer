from __future__ import annotations

from collections import Counter
from difflib import SequenceMatcher
from html import escape
import re

from app.models.schemas import ReportData, SimilarityMatch, SpellcheckIssue, UploadedPaper
from app.services.review_pipeline import PipelineRunResult


IMAGE_PLACEHOLDER_PATTERN = re.compile(r"\[IMAGE [^\]]+\]")
OCR_TEXT_MARKER_PATTERN = re.compile(r"\[OCR_TEXT\]")
FORMULA_TOKEN_PATTERN = re.compile(r"(渭|蟽|蟺|螖|尾|伪|卤)")
EQUATION_PATTERN = re.compile(r"[A-Za-z]\s*=\s*[^=\n]+")
FRACTION_PATTERN = re.compile(r"\b\d+\s*/\s*\d+\b")

CHART_KEYWORDS = (
    "如图",
    "下图",
    "图中",
    "图示",
    "图表",
    "坐标图",
    "折线图",
    "柱状图",
    "流程图",
    "结构图",
    "电路图",
    "表格",
    "统计表",
)

FORMULA_KEYWORDS = (
    "公式",
    "方程",
    "函数",
    "求导",
    "导数",
    "积分",
    "极限",
    "矩阵",
    "向量",
    "概率",
)


class ReportBuilder:
    """Builds report models and the Jinja context used by the report page."""

    def build_report(
        self,
        *,
        teacher_name: str,
        teacher_id: str,
        subject: str,
        uploaded_papers: list[UploadedPaper],
        questions,
        similarity_matches: list[SimilarityMatch],
        spellcheck_issues: list[SpellcheckIssue],
    ) -> ReportData:
        dashboard = self.build_dashboard(questions, similarity_matches, spellcheck_issues)
        normalized_matches = [
            match.model_copy(update={"review_status": match.review_status or "待确认"})
            for match in similarity_matches
        ]
        return ReportData(
            teacher_name=teacher_name,
            teacher_id=teacher_id,
            subject=subject,
            uploaded_papers=uploaded_papers,
            questions=questions,
            similarity_matches=normalized_matches,
            spellcheck_issues=spellcheck_issues,
            dashboard=dashboard,
        )

    def build_dashboard(
        self,
        questions,
        similarity_matches: list[SimilarityMatch],
        spellcheck_issues: list[SpellcheckIssue],
    ) -> dict[str, int]:
        question_counter = Counter(question.paper_id for question in questions)

        def count_matches(comparison_type: str, level: str | None = None) -> int:
            rows = [row for row in similarity_matches if row.comparison_type == comparison_type]
            if level is not None:
                rows = [row for row in rows if row.level == level]
            return len(rows)

        return {
            "paper_a_question_count": question_counter.get("A", 0),
            "paper_b_question_count": question_counter.get("B", 0),
            "paper_a_internal_high": count_matches("within_paper_a", "高强度重复"),
            "paper_b_internal_high": count_matches("within_paper_b", "高强度重复"),
            "cross_paper_high": count_matches("cross_paper", "高强度重复"),
            "history_high": count_matches("history_bank", "高强度重复"),
            "history_match_total": count_matches("history_bank"),
            "same_source_total": len([match for match in similarity_matches if self._is_same_source_match(match)]),
            "suspected_duplicate_total": len(
                [match for match in similarity_matches if match.level == "疑似重复"]
            ),
            "spellcheck_issue_total": len(spellcheck_issues),
            "pending_review_total": len(
                [match for match in similarity_matches if match.review_status == "待确认"]
            ),
        }

    def build_template_context(
        self,
        report: ReportData,
        *,
        code_run_result: PipelineRunResult | None = None,
    ) -> dict:
        dashboard_cards = self._build_dashboard_cards(report.dashboard)
        question_quality_rows = self.build_question_quality_rows(report.questions)
        parse_quality_rows = self.build_parse_quality_rows(report.uploaded_papers)
        complex_question_rows = self.build_complex_question_rows(report.questions, report.uploaded_papers)
        same_source_rows = self.build_same_source_rows(report.similarity_matches)
        duplicate_rows = self.sort_duplicate_rows(self._build_basic_duplicate_rows(report.similarity_matches))
        same_source_rows = self.sort_same_source_rows(same_source_rows)
        risk_navigation = self.build_risk_navigation(
            parse_quality_rows=parse_quality_rows,
            complex_question_rows=complex_question_rows,
            question_quality_rows=question_quality_rows,
            same_source_rows=same_source_rows,
            duplicate_rows=duplicate_rows,
            spellcheck_rows=self._build_basic_spellcheck_rows(report.spellcheck_issues),
        )

        return {
            "report": report,
            "dashboard_cards": dashboard_cards,
            "spellcheck_rows": self._build_basic_spellcheck_rows(report.spellcheck_issues),
            "duplicate_rows": duplicate_rows,
            "same_source_rows": same_source_rows,
            "question_quality_rows": question_quality_rows,
            "parse_quality_rows": parse_quality_rows,
            "complex_question_rows": complex_question_rows,
            "history_bank_summary": code_run_result.history_bank_summary if code_run_result else {},
            "risk_navigation": risk_navigation,
            "report_filters": self.build_report_filters(),
            "pending_review_summary": self.build_pending_review_summary(
                duplicate_rows=duplicate_rows,
                same_source_rows=same_source_rows,
            ),
            "export_payload": self.build_export_payload(
                report=report,
                code_run_result=code_run_result,
            ),
        }

    def build_report_filters(self) -> dict:
        return {
            "paper_options": [
                {"value": "all", "label": "全部卷别"},
                {"value": "A", "label": "只看 A 卷"},
                {"value": "B", "label": "只看 B 卷"},
                {"value": "history", "label": "只看历史题库"},
            ],
            "default_state": {
                "risk_only": False,
                "pending_only": False,
                "paper_filter": "all",
                "collapsed": True,
            },
        }

    def build_risk_navigation(
        self,
        *,
        parse_quality_rows: list[dict],
        complex_question_rows: list[dict],
        question_quality_rows: list[dict],
        same_source_rows: list[dict],
        duplicate_rows: list[dict],
        spellcheck_rows: list[dict],
    ) -> list[dict]:
        return [
            {"id": "section-parse-quality", "label": "解析风险", "count": len(parse_quality_rows), "tone": "high"},
            {"id": "section-complex-questions", "label": "复杂题复核", "count": len(complex_question_rows), "tone": "suspect"},
            {"id": "section-question-quality", "label": "低置信度切题", "count": len(question_quality_rows), "tone": "suspect"},
            {"id": "section-same-source", "label": "疑似同源", "count": len(same_source_rows), "tone": "warn"},
            {"id": "section-duplicate-rows", "label": "重复题", "count": len(duplicate_rows), "tone": "high"},
            {"id": "section-spellcheck-rows", "label": "错字问题", "count": len(spellcheck_rows), "tone": "info"},
        ]

    def build_pending_review_summary(
        self,
        *,
        duplicate_rows: list[dict],
        same_source_rows: list[dict],
    ) -> list[dict]:
        summary: list[dict] = []
        pending_duplicates = [row for row in duplicate_rows if row.get("review_status") == "待确认"]
        pending_same_source = [row for row in same_source_rows if row.get("review_status") == "待确认"]

        if pending_duplicates:
            summary.append(
                {
                    "label": "重复题待复核",
                    "count": len(pending_duplicates),
                    "target": "#section-duplicate-rows",
                    "description": "优先复核高强度重复和评分不同的题目对。",
                    "tone": "high",
                    "high_count": len([row for row in pending_duplicates if row.get("level") == "高强度重复"]),
                }
            )
        if pending_same_source:
            summary.append(
                {
                    "label": "疑似同源待确认",
                    "count": len(pending_same_source),
                    "target": "#section-same-source",
                    "description": "优先核查仅改数字、参数或样例数据的同源题。",
                    "tone": "warn",
                    "high_count": len([row for row in pending_same_source if row.get("same_source_flag") == "同源题"]),
                }
            )
        return summary

    def sort_duplicate_rows(self, rows: list[dict]) -> list[dict]:
        return sorted(rows, key=self._duplicate_sort_key)

    def sort_same_source_rows(self, rows: list[dict]) -> list[dict]:
        return sorted(
            rows,
            key=lambda row: (
                0 if row.get("review_status") == "待确认" else 1,
                0 if row.get("same_source_flag") == "同源题" else 1,
                -float(row.get("template_score") or 0),
                -float(row.get("score") or 0),
            ),
        )

    def build_question_quality_rows(self, questions) -> list[dict]:
        rows: list[dict] = []
        for question in questions:
            confidence = float(getattr(question, "split_confidence", 1.0) or 0)
            warning = str(getattr(question, "split_warning", "") or "").strip()
            if confidence >= 0.7 and not warning:
                continue
            rows.append(
                {
                    "paper_label": f"{question.paper_id} 卷",
                    "question_no": question.question_no,
                    "confidence": round(confidence, 2),
                    "warning": warning or "切题置信度偏低，请人工复核。",
                    "content_preview": question.content[:160],
                }
            )
        return rows

    def build_parse_quality_rows(self, uploaded_papers: list[UploadedPaper]) -> list[dict]:
        rows: list[dict] = []
        for paper in uploaded_papers:
            image_count = int(getattr(paper, "image_count", 0) or 0)
            ocr_attempted = bool(getattr(paper, "ocr_attempted", False))
            ocr_succeeded = bool(getattr(paper, "ocr_succeeded", False))
            requires_manual_review = bool(getattr(paper, "requires_manual_review", False))
            parse_note = str(getattr(paper, "parse_note", "") or "").strip()

            if not (image_count > 0 or ocr_attempted or requires_manual_review or parse_note):
                continue

            if ocr_attempted and ocr_succeeded:
                risk_level = "需复核"
                risk_reason = "已启用 OCR，建议对照原卷检查识别准确性。"
            elif ocr_attempted and not ocr_succeeded:
                risk_level = "高风险"
                risk_reason = "已启用 OCR 但未成功识别，建议直接查看原卷 PDF。"
            elif image_count > 0:
                risk_level = "需复核"
                risk_reason = "试卷包含图片或疑似扫描内容，建议人工复核。"
            else:
                risk_level = "提示"
                risk_reason = "解析链路给出了额外诊断提示。"

            rows.append(
                {
                    "paper_label": f"{paper.paper_id} 卷",
                    "filename": paper.filename,
                    "page_count": paper.page_count,
                    "image_count": image_count,
                    "ocr_status": self._format_ocr_status(
                        image_count=image_count,
                        ocr_attempted=ocr_attempted,
                        ocr_succeeded=ocr_succeeded,
                    ),
                    "risk_level": risk_level,
                    "risk_reason": risk_reason,
                    "parse_note": parse_note or "建议查看原卷 PDF 进行人工复核。",
                }
            )
        return rows

    def build_complex_question_rows(self, questions, uploaded_papers: list[UploadedPaper]) -> list[dict]:
        rows: list[dict] = []
        paper_map = {paper.paper_id: paper for paper in uploaded_papers}

        for question in questions:
            content = str(getattr(question, "content", "") or "").strip()
            if not content:
                continue

            flags: list[str] = []
            details: list[str] = []
            review_reasons: list[str] = []

            image_count = len(IMAGE_PLACEHOLDER_PATTERN.findall(content))
            if image_count > 0:
                flags.append("图片题")
                details.append(f"包含 {image_count} 个图片占位符")
                review_reasons.append("题干包含图片对象占位，需结合原卷核对。")

            if OCR_TEXT_MARKER_PATTERN.search(content):
                flags.append("OCR 回填")
                details.append("包含 OCR 回填文本")
                review_reasons.append("题干依赖 OCR 回填内容，建议核对识别准确性。")

            chart_hits = [keyword for keyword in CHART_KEYWORDS if keyword in content]
            if chart_hits:
                flags.append("图表题")
                details.append(f"命中图表关键词：{_unique_preview(chart_hits)}")
                review_reasons.append("题干引用图表或表格，建议查看原卷版式与图形细节。")

            formula_hits = [keyword for keyword in FORMULA_KEYWORDS if keyword in content]
            formula_symbol_count = len(FORMULA_TOKEN_PATTERN.findall(content))
            equation_count = len(EQUATION_PATTERN.findall(content))
            fraction_count = len(FRACTION_PATTERN.findall(content))
            if formula_hits or formula_symbol_count >= 3 or equation_count >= 2 or fraction_count >= 2:
                flags.append("复杂公式题")
                if formula_hits:
                    details.append(f"命中公式关键词：{_unique_preview(formula_hits)}")
                if formula_symbol_count:
                    details.append(f"发现 {formula_symbol_count} 个公式符号")
                if equation_count:
                    details.append(f"发现 {equation_count} 个等式片段")
                if fraction_count:
                    details.append(f"发现 {fraction_count} 个分数片段")
                review_reasons.append("题干含有较多公式或数学表达，建议人工复核排版和符号。")

            if not flags:
                continue

            detail = "；".join(details) if details else "未提取到更细的复杂度提示。"
            review_level = _complex_review_level(flags)
            source_paper = paper_map.get(question.paper_id)
            rows.append(
                {
                    "paper_label": f"{question.paper_id} 卷",
                    "filename": getattr(source_paper, "filename", ""),
                    "question_no": question.question_no,
                    "flags": flags,
                    "flag_summary": " / ".join(flags),
                    "detail": detail,
                    "review_level": review_level,
                    "reason": "；".join(review_reasons),
                    "recommendation": "建议查看原卷 PDF" if source_paper else "建议人工复核",
                }
            )
        return rows

    def build_same_source_rows(self, matches: list[SimilarityMatch]) -> list[dict]:
        rows: list[dict] = []
        for match in matches:
            if not self._is_same_source_match(match):
                continue
            rows.append(
                {
                    "match_id": match.match_id,
                    "comparison_label": comparison_label(match.comparison_type),
                    "same_source_flag": "同源题",
                    "score": match.similarity_score,
                    "final_score": match.final_score,
                    "literal_score": match.literal_score,
                    "template_score": match.template_score,
                    "source_label": format_match_endpoint_label(match, side="source"),
                    "target_label": format_match_endpoint_label(match, side="target"),
                    "reason": self._build_same_source_reason(match),
                    "review_status": match.review_status,
                    "compare_status": "同源",
                    "compare_note": "模板分显著高于字面分，疑似同源题。",
                    "source_html": escape(match.source_text),
                    "target_html": escape(match.target_text),
                }
            )
        return rows

    def build_export_payload(
        self,
        *,
        report: ReportData,
        code_run_result: PipelineRunResult | None = None,
    ) -> dict:
        if code_run_result is None:
            spellcheck_rows = self._build_basic_spellcheck_rows(report.spellcheck_issues)
            duplicate_rows = self.sort_duplicate_rows(self._build_basic_duplicate_rows(report.similarity_matches))
            return {
                "report": report.model_dump(mode="json"),
                "duplicate_comparison": self._build_duplicate_payload(duplicate_rows),
                "spellcheck_comparison": self._build_spellcheck_payload(spellcheck_rows),
                "same_source_matches": self.build_same_source_rows(report.similarity_matches),
                "parse_quality": self.build_parse_quality_rows(report.uploaded_papers),
                "question_quality": self.build_question_quality_rows(report.questions),
                "complex_question_quality": self.build_complex_question_rows(
                    report.questions,
                    report.uploaded_papers,
                ),
            }

        spellcheck_rows = self._build_basic_spellcheck_rows(code_run_result.spellcheck_issues)
        duplicate_rows = self.sort_duplicate_rows(self._build_basic_duplicate_rows(code_run_result.similarity_matches))
        return {
            "report": report.model_dump(mode="json"),
            "pipeline": {
                "name": code_run_result.pipeline_name,
                "module_metadata": code_run_result.module_metadata,
            },
            "history_bank": code_run_result.history_bank_summary,
            "duplicate_comparison": self._build_duplicate_payload(duplicate_rows),
            "spellcheck_comparison": self._build_spellcheck_payload(spellcheck_rows),
            "same_source_matches": self.build_same_source_rows(code_run_result.similarity_matches),
            "parse_quality": self.build_parse_quality_rows(code_run_result.uploaded_papers),
            "question_quality": self.build_question_quality_rows(code_run_result.questions),
            "complex_question_quality": self.build_complex_question_rows(
                code_run_result.questions,
                code_run_result.uploaded_papers,
            ),
        }

    def _build_duplicate_payload(self, rows: list[dict]) -> dict:
        return {
            "summary": {
                "total": len(rows),
                "high": len([row for row in rows if row.get("level") == "高强度重复"]),
                "suspect": len([row for row in rows if row.get("level") == "疑似重复"]),
            },
            "code_rows": rows,
        }

    def _build_spellcheck_payload(self, rows: list[dict]) -> dict:
        return {
            "summary": {
                "total": len(rows),
            },
            "code_rows": rows,
        }

    def _format_ocr_status(
        self,
        *,
        image_count: int,
        ocr_attempted: bool,
        ocr_succeeded: bool,
    ) -> str:
        if image_count <= 0 and not ocr_attempted:
            return "纯文本"
        if ocr_attempted and ocr_succeeded:
            return "已启用 OCR 并成功"
        if ocr_attempted and not ocr_succeeded:
            return "已启用 OCR 但未成功"
        if image_count > 0:
            return "有图片占位，未做 OCR"
        return "未知"

    def _build_dashboard_cards(self, dashboard: dict[str, int]) -> list[dict]:
        return [
            {
                "label": "A 卷题数",
                "value": dashboard.get("paper_a_question_count", 0),
                "hint": "代码版成功切分出的 A 卷题目数量。",
            },
            {
                "label": "B 卷题数",
                "value": dashboard.get("paper_b_question_count", 0),
                "hint": "代码版成功切分出的 B 卷题目数量。",
            },
            {
                "label": "高强度重复",
                "value": (
                    dashboard.get("paper_a_internal_high", 0)
                    + dashboard.get("paper_b_internal_high", 0)
                    + dashboard.get("cross_paper_high", 0)
                    + dashboard.get("history_high", 0)
                ),
                "hint": "相似度 >= 95 的高风险重复题目对。",
            },
            {
                "label": "疑似重复",
                "value": dashboard.get("suspected_duplicate_total", 0),
                "hint": "相似度 85-94 的待复核题目对。",
            },
            {
                "label": "疑似同源",
                "value": dashboard.get("same_source_total", 0),
                "hint": "模板分明显高于字面分的同源题或同源题对。",
            },
            {
                "label": "错字问题",
                "value": dashboard.get("spellcheck_issue_total", 0),
                "hint": "命中的错字、标点和重复字问题。",
            },
            {
                "label": "历史题库命中",
                "value": dashboard.get("history_match_total", 0),
                "hint": "在历史题库中命中的题目对数量。",
            },
            {
                "label": "待人工确认",
                "value": dashboard.get("pending_review_total", 0),
                "hint": "重复题中仍保持待确认状态的项目数。",
            },
        ]

    def _build_basic_spellcheck_rows(self, issues: list[SpellcheckIssue]) -> list[dict]:
        return [
            {
                "paper_label": f"{issue.paper_id} 卷",
                "question_no": issue.question_no,
                "issue_type": issue.issue_type,
                "issue_text": issue.issue_text,
                "suggestion": issue.suggestion,
                "original_preview": issue.original_text[:80],
                "confidence": issue.confidence,
                "compare_status": "",
                "compare_note": "",
            }
            for issue in issues
        ]

    def _build_basic_duplicate_rows(self, matches: list[SimilarityMatch]) -> list[dict]:
        rows: list[dict] = []
        for match in matches:
            if self._is_same_source_match(match):
                continue
            left_html, right_html = highlight_diff(match.source_text, match.target_text)
            rows.append(
                {
                    "match_id": match.match_id,
                    "comparison_label": comparison_label(match.comparison_type),
                    "score": match.similarity_score,
                    "level": match.level,
                    "source_label": format_match_endpoint_label(match, side="source"),
                    "target_label": format_match_endpoint_label(match, side="target"),
                    "source_html": left_html,
                    "target_html": right_html,
                    "review_status": match.review_status,
                    "review_options": ["待确认", "确认重复", "排除误报"],
                    "compare_status": "",
                    "compare_note": "",
                }
            )
        return rows

    def _is_same_source_match(self, match: SimilarityMatch) -> bool:
        return bool(match.is_same_source_question or match.level == "疑似同源")

    def _build_same_source_reason(self, match: SimilarityMatch) -> str:
        literal_score = match.literal_score if match.literal_score is not None else match.similarity_score
        template_score = match.template_score if match.template_score is not None else match.similarity_score
        if match.is_same_source_question:
            return f"模板分 {template_score}% 明显高于字面分 {literal_score}% ，更像同题干改参数或改数字后的同源题。"
        return f"模板分 {template_score}% 达到同源阈值，字面分 {literal_score}% 未完全重合，建议按疑似同源人工复核。"

    def _duplicate_sort_key(self, row: dict) -> tuple:
        return (
            0 if row.get("review_status") == "待确认" else 1,
            0 if row.get("level") == "高强度重复" else 1,
            0 if row.get("compare_status") == "评分不同" else 1,
            -float(row.get("score") or 0),
        )


def comparison_label(comparison_type: str) -> str:
    mapping = {
        "within_paper_a": "A 卷内查重",
        "within_paper_b": "B 卷内查重",
        "cross_paper": "A/B 交叉查重",
        "history_bank": "历史题库比对",
    }
    return mapping.get(comparison_type, comparison_type)


def format_match_endpoint_label(match: SimilarityMatch, *, side: str) -> str:
    if side == "source":
        paper_id = match.source_paper_id
        paper_label = match.source_paper_label or paper_id
        question_no = match.source_question_no
    else:
        paper_id = match.target_paper_id
        paper_label = match.target_paper_label or paper_id
        question_no = match.target_question_no

    if paper_id in {"A", "B"}:
        return f"{paper_id} 卷第 {question_no} 题"
    if match.comparison_type == "history_bank":
        return f"历史库 {paper_label} 第 {question_no} 题"
    return f"{paper_label} 第 {question_no} 题"


def highlight_diff(left_text: str, right_text: str) -> tuple[str, str]:
    matcher = SequenceMatcher(a=left_text, b=right_text)
    left_parts: list[str] = []
    right_parts: list[str] = []

    for opcode, a0, a1, b0, b1 in matcher.get_opcodes():
        left_chunk = escape(left_text[a0:a1])
        right_chunk = escape(right_text[b0:b1])

        if opcode == "equal":
            left_parts.append(left_chunk)
            right_parts.append(right_chunk)
        elif opcode == "replace":
            left_parts.append(f'<mark class="diff-removed">{left_chunk}</mark>')
            right_parts.append(f'<mark class="diff-added">{right_chunk}</mark>')
        elif opcode == "delete":
            left_parts.append(f'<mark class="diff-removed">{left_chunk}</mark>')
        elif opcode == "insert":
            right_parts.append(f'<mark class="diff-added">{right_chunk}</mark>')

    return "".join(left_parts), "".join(right_parts)


def _unique_preview(values: list[str], *, limit: int = 3) -> str:
    ordered: list[str] = []
    for value in values:
        if value not in ordered:
            ordered.append(value)
    preview = ordered[:limit]
    if len(ordered) > limit:
        preview.append("等")
    return "、".join(preview)


def _complex_review_level(flags: list[str]) -> str:
    if "图片题" in flags and "OCR 回填" in flags:
        return "高风险"
    if "图片题" in flags or "图表题" in flags:
        return "需复核"
    return "提示"
