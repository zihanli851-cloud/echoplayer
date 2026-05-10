from __future__ import annotations

from collections import Counter
from difflib import SequenceMatcher
from html import escape
import re

from app.models.schemas import (
    DualRunSectionComparison,
    ReportData,
    SimilarityMatch,
    SpellcheckIssue,
    UploadedPaper,
)
from app.services.dual_run import PipelineRunResult


IMAGE_PLACEHOLDER_PATTERN = re.compile(r"\[IMAGE [^\]]+\]")
OCR_TEXT_MARKER_PATTERN = re.compile(r"\[OCR_TEXT\]")
FORMULA_TOKEN_PATTERN = re.compile(r"(∑|∫|√|∞|≈|≠|≤|≥|∈|∂|λ|μ|σ|π|Δ|β|α|→|←|↔|±)")
EQUATION_PATTERN = re.compile(r"[A-Za-z]\s*=\s*[^=\n]+")
FRACTION_PATTERN = re.compile(r"\b\d+\s*/\s*\d+\b")

CHART_KEYWORDS = (
    "如图",
    "下图",
    "图中",
    "图示",
    "图表",
    "示意图",
    "坐标图",
    "函数图像",
    "折线图",
    "柱状图",
    "饼图",
    "流程图",
    "结构图",
    "电路图",
    "表格",
    "见表",
    "如下表",
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
    "概率密度",
    "标准差",
    "化学方程式",
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
            "paper_a_internal_high": count_matches("within_paper_a", "高度重复"),
            "paper_b_internal_high": count_matches("within_paper_b", "高度重复"),
            "cross_paper_high": count_matches("cross_paper", "高度重复"),
            "history_high": count_matches("history_bank", "高度重复"),
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
        agent_run_result: PipelineRunResult | None = None,
    ) -> dict:
        dashboard_cards = self._build_dashboard_cards(report.dashboard)

        if not code_run_result or not agent_run_result:
            question_quality_rows = self.build_question_quality_rows(report.questions)
            parse_quality_rows = self.build_parse_quality_rows(report.uploaded_papers)
            complex_question_rows = self.build_complex_question_rows(report.questions, report.uploaded_papers)
            same_source_rows = self.build_same_source_rows(report.similarity_matches)
            risk_navigation = self.build_risk_navigation(
                parse_quality_rows=parse_quality_rows,
                complex_question_rows=complex_question_rows,
                question_quality_rows=question_quality_rows,
                same_source_rows=same_source_rows,
                duplicate_rows=self._build_basic_duplicate_rows(report.similarity_matches),
                spellcheck_rows=self._build_basic_spellcheck_rows(report.spellcheck_issues),
                dual_run_sections=[],
            )
            basic_duplicate_rows = self.sort_duplicate_rows(self._build_basic_duplicate_rows(report.similarity_matches))
            basic_same_source_rows = self.sort_same_source_rows(same_source_rows)
            return {
                "report": report,
                "dashboard_cards": dashboard_cards,
                "spellcheck_rows": self._build_basic_spellcheck_rows(report.spellcheck_issues),
                "duplicate_rows": basic_duplicate_rows,
                "same_source_rows": basic_same_source_rows,
                "question_quality_rows": question_quality_rows,
                "parse_quality_rows": parse_quality_rows,
                "complex_question_rows": complex_question_rows,
                "dual_run_sections": [],
                "agent_dashboard_cards": [],
                "agent_only_spellcheck_rows": [],
                "agent_only_duplicate_rows": [],
                "history_bank_summary": {},
                "risk_navigation": risk_navigation,
                "report_filters": self.build_report_filters(),
                "pending_review_summary": self.build_pending_review_summary(
                    duplicate_rows=basic_duplicate_rows,
                    same_source_rows=basic_same_source_rows,
                ),
                "export_payload": {
                    "report": report.model_dump(mode="json"),
                    "same_source_matches": same_source_rows,
                    "question_quality": question_quality_rows,
                    "parse_quality": parse_quality_rows,
                    "complex_question_quality": complex_question_rows,
                },
            }

        dual_run_sections = self.build_dual_run_sections(code_run_result, agent_run_result)
        spellcheck_context = self.build_spellcheck_comparison(
            code_run_result.spellcheck_issues,
            agent_run_result.spellcheck_issues,
        )
        duplicate_context = self.build_duplicate_comparison(
            code_run_result.similarity_matches,
            agent_run_result.similarity_matches,
        )
        parse_quality_rows = self.build_parse_quality_rows(code_run_result.uploaded_papers)
        question_quality_rows = self.build_question_quality_rows(code_run_result.questions)
        complex_question_rows = self.build_complex_question_rows(
            code_run_result.questions,
            code_run_result.uploaded_papers,
        )
        same_source_rows = self.build_same_source_rows(code_run_result.similarity_matches)
        dual_run_section_payloads = [section.model_dump() for section in dual_run_sections]
        risk_navigation = self.build_risk_navigation(
            parse_quality_rows=parse_quality_rows,
            complex_question_rows=complex_question_rows,
            question_quality_rows=question_quality_rows,
            same_source_rows=same_source_rows,
            duplicate_rows=duplicate_context["code_rows"],
            spellcheck_rows=spellcheck_context["code_rows"],
            dual_run_sections=dual_run_section_payloads,
        )
        sorted_duplicate_rows = self.sort_duplicate_rows(duplicate_context["code_rows"])
        sorted_same_source_rows = self.sort_same_source_rows(same_source_rows)

        return {
            "report": report,
            "dashboard_cards": dashboard_cards,
            "agent_dashboard_cards": self._build_agent_dashboard_cards(
                spellcheck_context,
                duplicate_context,
            ),
            "spellcheck_rows": spellcheck_context["code_rows"],
            "agent_only_spellcheck_rows": spellcheck_context["agent_only_rows"],
            "duplicate_rows": sorted_duplicate_rows,
            "agent_only_duplicate_rows": duplicate_context["agent_only_rows"],
            "same_source_rows": sorted_same_source_rows,
            "parse_quality_rows": parse_quality_rows,
            "question_quality_rows": question_quality_rows,
            "complex_question_rows": complex_question_rows,
            "dual_run_sections": dual_run_section_payloads,
            "history_bank_summary": code_run_result.history_bank_summary,
            "risk_navigation": risk_navigation,
            "report_filters": self.build_report_filters(),
            "pending_review_summary": self.build_pending_review_summary(
                duplicate_rows=sorted_duplicate_rows,
                same_source_rows=sorted_same_source_rows,
            ),
            "export_payload": self.build_export_payload(
                report=report,
                dual_run_sections=dual_run_sections,
                spellcheck_context=spellcheck_context,
                duplicate_context=duplicate_context,
                code_run_result=code_run_result,
                agent_run_result=agent_run_result,
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
        dual_run_sections: list[dict],
    ) -> list[dict]:
        return [
            {
                "id": "section-parse-quality",
                "label": "高风险解析",
                "count": len([row for row in parse_quality_rows if row.get("risk_level") == "高风险"]),
                "tone": "high",
            },
            {
                "id": "section-complex-questions",
                "label": "复杂题复核",
                "count": len(complex_question_rows),
                "tone": "suspect",
            },
            {
                "id": "section-question-quality",
                "label": "低置信度切题",
                "count": len(question_quality_rows),
                "tone": "suspect",
            },
            {
                "id": "section-same-source",
                "label": "疑似原题",
                "count": len(same_source_rows),
                "tone": "warn",
            },
            {
                "id": "section-duplicate-rows",
                "label": "重复题",
                "count": len(duplicate_rows),
                "tone": "high",
            },
            {
                "id": "section-spellcheck-rows",
                "label": "错字问题",
                "count": len(spellcheck_rows),
                "tone": "info",
            },
            {
                "id": "section-dual-run",
                "label": "双链路状态",
                "count": len([row for row in dual_run_sections if row.get("status") != "一致"]),
                "tone": "info",
            },
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
                    "label": "重复题待确认",
                    "count": len(pending_duplicates),
                    "target": "#section-duplicate-rows",
                    "description": "优先复核高度重复和评分不同的题目对。",
                    "tone": "high",
                    "high_count": len([row for row in pending_duplicates if row.get("level") == "高度重复"]),
                }
            )
        if pending_same_source:
            summary.append(
                {
                    "label": "疑似原题待确认",
                    "count": len(pending_same_source),
                    "target": "#section-same-source",
                    "description": "优先核查仅改数字、参数或样例数据的同源题。",
                    "tone": "warn",
                    "high_count": len([row for row in pending_same_source if row.get("same_source_flag") == "疑似原题"]),
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
                0 if row.get("same_source_flag") == "疑似原题" else 1,
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
                risk_reason = "已触发 OCR，建议对照原卷检查识别准确性。"
            elif ocr_attempted and not ocr_succeeded:
                risk_level = "高风险"
                risk_reason = "已触发 OCR 但未成功识别，建议直接查看原卷 PDF。"
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
                review_reasons.append("题干包含图片对象占位，需结合原卷核对图片内容。")

            if OCR_TEXT_MARKER_PATTERN.search(content):
                flags.append("OCR回填")
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
            formula_score = len(formula_hits) + formula_symbol_count + equation_count + fraction_count
            if formula_score >= 2:
                flags.append("复杂公式题")
                formula_parts: list[str] = []
                if formula_hits:
                    formula_parts.append(f"关键词：{_unique_preview(formula_hits)}")
                if formula_symbol_count:
                    formula_parts.append(f"公式符号 {formula_symbol_count} 处")
                if equation_count:
                    formula_parts.append(f"等式表达 {equation_count} 处")
                if fraction_count:
                    formula_parts.append(f"分式表达 {fraction_count} 处")
                details.append("；".join(formula_parts))
                review_reasons.append("题干包含较多公式或参数表达，建议回看原卷核对公式排版。")

            if not flags:
                continue

            paper = paper_map.get(question.paper_id)
            review_level = _complex_review_level(flags)
            recommendation = "建议查看原卷 PDF"
            if "OCR回填" in flags and "图片题" not in flags:
                recommendation = "建议核对 OCR 回填与原卷"

            rows.append(
                {
                    "paper_label": f"{question.paper_id} 卷",
                    "question_no": question.question_no,
                    "question_id": question.question_id,
                    "flags": flags,
                    "flag_summary": " / ".join(flags),
                    "review_level": review_level,
                    "recommendation": recommendation,
                    "reason": "；".join(review_reasons),
                    "detail": "；".join(details),
                    "content_preview": content[:180],
                    "paper_filename": paper.filename if paper else "",
                }
            )

        return rows

    def build_same_source_rows(self, matches: list[SimilarityMatch]) -> list[dict]:
        rows: list[dict] = []
        for match in matches:
            if not self._is_same_source_match(match):
                continue
            left_html, right_html = highlight_diff(match.source_text, match.target_text)
            rows.append(
                {
                    "match_id": match.match_id,
                    "comparison_label": comparison_label(match.comparison_type),
                    "score": match.similarity_score,
                    "literal_score": match.literal_score,
                    "template_score": match.template_score,
                    "final_score": match.final_score,
                    "level": match.level,
                    "same_source_flag": "同源题" if match.is_same_source_question else "疑似原题",
                    "source_label": format_match_endpoint_label(match, side="source"),
                    "target_label": format_match_endpoint_label(match, side="target"),
                    "source_html": left_html,
                    "target_html": right_html,
                    "review_status": match.review_status,
                    "review_options": ["待确认", "确认重复", "排除误报"],
                    "reason": self._build_same_source_reason(match),
                }
            )
        return rows

    def build_dual_run_sections(
        self,
        code_run_result: PipelineRunResult,
        agent_run_result: PipelineRunResult,
    ) -> list[DualRunSectionComparison]:
        return [
            self._compare_extraction_section(code_run_result, agent_run_result),
            self._compare_split_section(code_run_result, agent_run_result),
            self._compare_duplicate_section(code_run_result, agent_run_result),
            self._compare_spellcheck_section(code_run_result, agent_run_result),
        ]

    def build_spellcheck_comparison(
        self,
        code_issues: list[SpellcheckIssue],
        agent_issues: list[SpellcheckIssue],
    ) -> dict:
        remaining_agent_issues = list(agent_issues)
        code_rows: list[dict] = []

        for issue in code_issues:
            matching_issue = self._pop_matching_spellcheck_issue(issue, remaining_agent_issues)
            if matching_issue:
                compare_status = "一致"
                compare_note = "代码版与 Agent 版都识别到了该问题。"
            else:
                compare_status = "代码独有"
                compare_note = "当前 Agent 结果中未出现该问题。"

            code_rows.append(
                {
                    "paper_label": f"{issue.paper_id} 卷",
                    "question_no": issue.question_no,
                    "issue_type": issue.issue_type,
                    "issue_text": issue.issue_text,
                    "suggestion": issue.suggestion,
                    "original_preview": issue.original_text[:80],
                    "confidence": issue.confidence,
                    "compare_status": compare_status,
                    "compare_note": compare_note,
                }
            )

        agent_only_rows = [
            {
                "paper_label": f"{issue.paper_id} 卷",
                "question_no": issue.question_no,
                "issue_type": issue.issue_type,
                "issue_text": issue.issue_text,
                "suggestion": issue.suggestion,
                "original_preview": issue.original_text[:80],
                "compare_status": "Agent 新增",
                "compare_note": "该问题只在 Agent 结果中出现。",
            }
            for issue in remaining_agent_issues
        ]

        return {
            "code_rows": code_rows,
            "agent_only_rows": agent_only_rows,
            "summary": {
                "matched": len([row for row in code_rows if row["compare_status"] == "一致"]),
                "code_only": len([row for row in code_rows if row["compare_status"] == "代码独有"]),
                "agent_only": len(agent_only_rows),
            },
        }

    def build_duplicate_comparison(
        self,
        code_matches: list[SimilarityMatch],
        agent_matches: list[SimilarityMatch],
    ) -> dict:
        remaining_agent_matches = list(agent_matches)
        code_rows: list[dict] = []

        for match in code_matches:
            if self._is_same_source_match(match):
                continue

            matching_agent_match = self._pop_matching_similarity_match(match, remaining_agent_matches)
            compare_status = "代码独有"
            compare_note = "当前 Agent 结果中未出现该题目对。"
            agent_score = None

            if matching_agent_match:
                agent_score = matching_agent_match.similarity_score
                if self._same_match_level(match, matching_agent_match):
                    compare_status = "一致"
                    compare_note = "代码版与 Agent 版对该题目对的判断一致。"
                else:
                    compare_status = "评分不同"
                    compare_note = (
                        f"代码版相似度 {match.similarity_score}% ，"
                        f"Agent 版相似度 {matching_agent_match.similarity_score}% 。"
                    )

            left_html, right_html = highlight_diff(match.source_text, match.target_text)
            code_rows.append(
                {
                    "match_id": match.match_id,
                    "comparison_label": comparison_label(match.comparison_type),
                    "score": match.similarity_score,
                    "agent_score": agent_score,
                    "level": match.level,
                    "source_label": format_match_endpoint_label(match, side="source"),
                    "target_label": format_match_endpoint_label(match, side="target"),
                    "source_html": left_html,
                    "target_html": right_html,
                    "review_status": match.review_status,
                    "review_options": ["待确认", "确认重复", "排除误报"],
                    "compare_status": compare_status,
                    "compare_note": compare_note,
                }
            )

        agent_only_rows: list[dict] = []
        for match in remaining_agent_matches:
            if self._is_same_source_match(match):
                continue
            left_html, right_html = highlight_diff(match.source_text, match.target_text)
            agent_only_rows.append(
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
                    "compare_status": "Agent 新增",
                    "compare_note": "该题目对只在 Agent 结果中出现。",
                }
            )

        return {
            "code_rows": code_rows,
            "agent_only_rows": agent_only_rows,
            "summary": {
                "matched": len([row for row in code_rows if row["compare_status"] == "一致"]),
                "score_diff": len([row for row in code_rows if row["compare_status"] == "评分不同"]),
                "code_only": len([row for row in code_rows if row["compare_status"] == "代码独有"]),
                "agent_only": len(agent_only_rows),
            },
        }

    def build_export_payload(
        self,
        *,
        report: ReportData,
        dual_run_sections: list[DualRunSectionComparison],
        spellcheck_context: dict,
        duplicate_context: dict,
        code_run_result: PipelineRunResult,
        agent_run_result: PipelineRunResult,
    ) -> dict:
        return {
            "report": report.model_dump(mode="json"),
            "pipelines": {
                "code": {
                    "pipeline_name": code_run_result.pipeline_name,
                    "module_metadata": code_run_result.module_metadata,
                },
                "agent": {
                    "pipeline_name": agent_run_result.pipeline_name,
                    "module_metadata": agent_run_result.module_metadata,
                },
            },
            "history_bank": code_run_result.history_bank_summary,
            "dual_run_sections": [section.model_dump(mode="json") for section in dual_run_sections],
            "same_source_matches": self.build_same_source_rows(code_run_result.similarity_matches),
            "parse_quality": self.build_parse_quality_rows(code_run_result.uploaded_papers),
            "question_quality": self.build_question_quality_rows(code_run_result.questions),
            "complex_question_quality": self.build_complex_question_rows(
                code_run_result.questions,
                code_run_result.uploaded_papers,
            ),
            "spellcheck_comparison": spellcheck_context,
            "duplicate_comparison": duplicate_context,
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
            return "已触发 OCR 并成功"
        if ocr_attempted and not ocr_succeeded:
            return "已触发 OCR 但未成功"
        if image_count > 0:
            return "有图片占位，未做 OCR"
        return "未知"

    def _build_dashboard_cards(self, dashboard: dict[str, int]) -> list[dict]:
        return [
            {
                "label": "A 卷题目数",
                "value": dashboard.get("paper_a_question_count", 0),
                "hint": "代码版成功切分出的 A 卷题目数量。",
            },
            {
                "label": "B 卷题目数",
                "value": dashboard.get("paper_b_question_count", 0),
                "hint": "代码版成功切分出的 B 卷题目数量。",
            },
            {
                "label": "高度重复",
                "value": (
                    dashboard.get("paper_a_internal_high", 0)
                    + dashboard.get("paper_b_internal_high", 0)
                    + dashboard.get("cross_paper_high", 0)
                    + dashboard.get("history_high", 0)
                ),
                "hint": "代码版相似度 >= 95 的高风险题目对。",
            },
            {
                "label": "疑似重复",
                "value": dashboard.get("suspected_duplicate_total", 0),
                "hint": "代码版相似度 85-94 的待复核题目对。",
            },
            {
                "label": "疑似原题",
                "value": dashboard.get("same_source_total", 0),
                "hint": "模板分明显高于字面分的疑似原题或同源题。",
            },
            {
                "label": "错字问题",
                "value": dashboard.get("spellcheck_issue_total", 0),
                "hint": "代码版命中的错字、标点和重复字问题。",
            },
            {
                "label": "历史题库命中",
                "value": dashboard.get("history_match_total", 0),
                "hint": "上传试卷在 history_bank 中的命中题目对数量。",
            },
            {
                "label": "待人工确认",
                "value": dashboard.get("pending_review_total", 0),
                "hint": "代码版重复明细默认进入人工复核状态。",
            },
        ]

    def _build_agent_dashboard_cards(self, spellcheck_context: dict, duplicate_context: dict) -> list[dict]:
        return [
            {
                "label": "错字结果一致",
                "value": spellcheck_context["summary"]["matched"],
                "hint": "代码版与 Agent 版都识别到的问题数。",
            },
            {
                "label": "Agent 错字新增",
                "value": spellcheck_context["summary"]["agent_only"],
                "hint": "只在 Agent 结果中出现的错字问题数。",
            },
            {
                "label": "重复结果一致",
                "value": duplicate_context["summary"]["matched"],
                "hint": "代码版与 Agent 版都命中的题目对。",
            },
            {
                "label": "重复评分不同",
                "value": duplicate_context["summary"]["score_diff"],
                "hint": "两边识别到同一题目对但评分或等级不同。",
            },
            {
                "label": "Agent 重复新增",
                "value": duplicate_context["summary"]["agent_only"],
                "hint": "只在 Agent 结果中出现的重复题目对。",
            },
            {
                "label": "代码独有结论",
                "value": spellcheck_context["summary"]["code_only"] + duplicate_context["summary"]["code_only"],
                "hint": "当前 Agent 结果中未覆盖的代码版发现数。",
            },
        ]

    def _compare_extraction_section(
        self,
        code_run_result: PipelineRunResult,
        agent_run_result: PipelineRunResult,
    ) -> DualRunSectionComparison:
        code_result = [
            {
                "paper_id": paper.paper_id,
                "page_count": paper.page_count,
                "text_length": len(paper.text_content),
            }
            for paper in code_run_result.uploaded_papers
        ]
        agent_result = [
            {
                "paper_id": paper.paper_id,
                "page_count": paper.page_count,
                "text_length": len(paper.text_content),
            }
            for paper in agent_run_result.uploaded_papers
        ]
        status = "一致" if code_result == agent_result else "存在差异"
        diff_summary = "代码版与 Agent 版提取摘要一致。"
        provider_note = agent_run_result.module_metadata["extract"].get("provider_note", "")
        if status != "一致":
            diff_summary = "两边提取的页数或文本长度不同。"
        elif provider_note:
            diff_summary = f"{diff_summary}{provider_note}"
        return DualRunSectionComparison(
            module_name="文本提取",
            code_result=code_result,
            agent_result=agent_result,
            status=status,
            diff_summary=diff_summary,
        )

    def _compare_split_section(
        self,
        code_run_result: PipelineRunResult,
        agent_run_result: PipelineRunResult,
    ) -> DualRunSectionComparison:
        code_result = self._question_summary(code_run_result.questions, uploaded_papers=code_run_result.uploaded_papers)
        agent_result = self._question_summary(agent_run_result.questions, uploaded_papers=code_run_result.uploaded_papers)
        status = "一致" if code_result == agent_result else "存在差异"
        diff_summary = "两边切题结果一致。"
        agent_metadata = agent_run_result.module_metadata["split"]
        provider_note = agent_metadata.get("provider_note", "")
        if agent_metadata.get("is_placeholder"):
            status = "Agent 未返回"
            diff_summary = provider_note or "Agent 切题未返回结果。"
        elif status != "一致":
            diff_summary = "两边切题数量或题号集合不同。"
        elif provider_note:
            diff_summary = f"{diff_summary}{provider_note}"
        return DualRunSectionComparison(
            module_name="切题",
            code_result=code_result,
            agent_result=agent_result,
            status=status,
            diff_summary=diff_summary,
        )

    def _compare_duplicate_section(
        self,
        code_run_result: PipelineRunResult,
        agent_run_result: PipelineRunResult,
    ) -> DualRunSectionComparison:
        code_result = self._duplicate_summary(code_run_result.similarity_matches)
        agent_result = self._duplicate_summary(agent_run_result.similarity_matches)
        status = "一致" if code_result == agent_result else "存在差异"
        diff_summary = "两边重复题目统计一致。"
        agent_metadata = agent_run_result.module_metadata["compare"]
        provider_note = agent_metadata.get("provider_note", "")
        if agent_metadata.get("is_placeholder"):
            status = "Agent 未返回"
            diff_summary = provider_note or "Agent 查重比对未返回结果。"
        elif status != "一致":
            diff_summary = "两边重复题目数量或等级分布不同。"
        elif provider_note:
            diff_summary = f"{diff_summary}{provider_note}"
        return DualRunSectionComparison(
            module_name="查重比对",
            code_result=code_result,
            agent_result=agent_result,
            status=status,
            diff_summary=diff_summary,
        )

    def _compare_spellcheck_section(
        self,
        code_run_result: PipelineRunResult,
        agent_run_result: PipelineRunResult,
    ) -> DualRunSectionComparison:
        code_result = self._spellcheck_summary(code_run_result.spellcheck_issues)
        agent_result = self._spellcheck_summary(agent_run_result.spellcheck_issues)
        status = "一致" if code_result == agent_result else "存在差异"
        diff_summary = "两边错字检查统计一致。"
        provider_note = agent_run_result.module_metadata["spellcheck"].get("provider_note", "")
        if agent_run_result.module_metadata["spellcheck"].get("is_placeholder"):
            status = "Agent 未返回"
            diff_summary = provider_note or "Agent 侧当前未返回错字结果。"
        elif status != "一致":
            diff_summary = "两边错字问题数量或类型分布不同。"
        return DualRunSectionComparison(
            module_name="错字检查",
            code_result=code_result,
            agent_result=agent_result,
            status=status,
            diff_summary=diff_summary,
        )

    def _question_summary(self, questions, *, uploaded_papers=None) -> dict:
        summary: dict[str, dict] = {}
        for paper in uploaded_papers or []:
            summary.setdefault(paper.paper_id, {"count": 0, "question_nos": []})
        for question in questions:
            paper_summary = summary.setdefault(question.paper_id, {"count": 0, "question_nos": []})
            paper_summary["count"] += 1
            paper_summary["question_nos"].append(question.question_no)
        return summary

    def _duplicate_summary(self, matches: list[SimilarityMatch]) -> dict:
        summary: dict[str, dict] = {}
        for match in matches:
            type_summary = summary.setdefault(match.comparison_type, {"total": 0, "high": 0, "suspect": 0, "same_source": 0})
            type_summary["total"] += 1
            if self._is_same_source_match(match):
                type_summary["same_source"] += 1
            elif match.level == "高度重复":
                type_summary["high"] += 1
            elif match.level == "疑似重复":
                type_summary["suspect"] += 1
        return summary

    def _spellcheck_summary(self, issues: list[SpellcheckIssue]) -> dict:
        summary: dict[str, dict] = {}
        for issue in issues:
            paper_summary = summary.setdefault(issue.paper_id, {"total": 0, "types": {}})
            paper_summary["total"] += 1
            paper_summary["types"][issue.issue_type] = paper_summary["types"].get(issue.issue_type, 0) + 1
        return summary

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
                    "agent_score": None,
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
        return bool(match.is_same_source_question or match.level == "疑似原题")

    def _build_same_source_reason(self, match: SimilarityMatch) -> str:
        literal_score = match.literal_score if match.literal_score is not None else match.similarity_score
        template_score = match.template_score if match.template_score is not None else match.similarity_score
        if match.is_same_source_question:
            return f"模板分 {template_score}% 明显高于字面分 {literal_score}% ，更像同题干改参数或改数字后的同源题。"
        return f"模板分 {template_score}% 达到原题阈值，字面分 {literal_score}% 未完全重合，建议按疑似原题人工复核。"

    def _pop_matching_spellcheck_issue(
        self,
        issue: SpellcheckIssue,
        issue_pool: list[SpellcheckIssue],
    ) -> SpellcheckIssue | None:
        signature = spellcheck_signature(issue)
        for index, agent_issue in enumerate(issue_pool):
            if spellcheck_signature(agent_issue) == signature:
                return issue_pool.pop(index)
        return None

    def _pop_matching_similarity_match(
        self,
        match: SimilarityMatch,
        match_pool: list[SimilarityMatch],
    ) -> SimilarityMatch | None:
        signature = similarity_signature(match)
        for index, agent_match in enumerate(match_pool):
            if similarity_signature(agent_match) == signature:
                return match_pool.pop(index)
        return None

    def _same_match_level(self, code_match: SimilarityMatch, agent_match: SimilarityMatch) -> bool:
        same_level = code_match.level == agent_match.level
        score_diff = abs(code_match.similarity_score - agent_match.similarity_score)
        return same_level and score_diff < 1

    def _duplicate_sort_key(self, row: dict) -> tuple:
        return (
            0 if row.get("review_status") == "待确认" else 1,
            0 if row.get("level") == "高度重复" else 1,
            0 if row.get("compare_status") == "评分不同" else 1,
            -float(row.get("score") or 0),
        )


def spellcheck_signature(issue: SpellcheckIssue) -> tuple:
    return (
        issue.paper_id,
        issue.question_no,
        issue.issue_type,
        issue.issue_text,
        issue.suggestion,
    )


def similarity_signature(match: SimilarityMatch) -> tuple:
    return (
        match.comparison_type,
        match.source_paper_id,
        match.source_question_no,
        match.target_paper_id,
        match.target_question_no,
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
        return f"历史库《{paper_label}》第 {question_no} 题"
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
    if "图片题" in flags and "OCR回填" in flags:
        return "高风险"
    if "图片题" in flags or "图表题" in flags:
        return "需复核"
    return "提示"
