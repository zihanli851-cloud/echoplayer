from __future__ import annotations

from collections import Counter
from difflib import SequenceMatcher
from html import escape

from app.models.schemas import (
    DualRunSectionComparison,
    ReportData,
    SimilarityMatch,
    SpellcheckIssue,
    UploadedPaper,
)
from app.services.dual_run import PipelineRunResult


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
        """Assemble the main code-side report model."""

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
        """Build macro counters shown in the main dashboard."""

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
        """Build the full Jinja template context for the report page."""

        dashboard_cards = self._build_dashboard_cards(report.dashboard)

        if not code_run_result or not agent_run_result:
            return {
                "report": report,
                "dashboard_cards": dashboard_cards,
                "spellcheck_rows": self._build_basic_spellcheck_rows(report.spellcheck_issues),
                "duplicate_rows": self._build_basic_duplicate_rows(report.similarity_matches),
                "dual_run_sections": [],
                "agent_dashboard_cards": [],
                "agent_only_spellcheck_rows": [],
                "agent_only_duplicate_rows": [],
                "history_bank_summary": {},
                "export_payload": {"report": report.model_dump(mode="json")},
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

        return {
            "report": report,
            "dashboard_cards": dashboard_cards,
            "agent_dashboard_cards": self._build_agent_dashboard_cards(
                spellcheck_context,
                duplicate_context,
            ),
            "spellcheck_rows": spellcheck_context["code_rows"],
            "agent_only_spellcheck_rows": spellcheck_context["agent_only_rows"],
            "duplicate_rows": duplicate_context["code_rows"],
            "agent_only_duplicate_rows": duplicate_context["agent_only_rows"],
            "dual_run_sections": [section.model_dump() for section in dual_run_sections],
            "history_bank_summary": code_run_result.history_bank_summary,
            "export_payload": self.build_export_payload(
                report=report,
                dual_run_sections=dual_run_sections,
                spellcheck_context=spellcheck_context,
                duplicate_context=duplicate_context,
                code_run_result=code_run_result,
                agent_run_result=agent_run_result,
            ),
        }

    def build_dual_run_sections(
        self,
        code_run_result: PipelineRunResult,
        agent_run_result: PipelineRunResult,
    ) -> list[DualRunSectionComparison]:
        """Build module-level code-vs-Agent comparison blocks."""

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
        """Build code-main and Agent-only views for spellcheck results."""

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
        """Build code-main and Agent-only views for duplicate comparison results."""

        remaining_agent_matches = list(agent_matches)
        code_rows: list[dict] = []

        for match in code_matches:
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
                "score_diff": len(
                    [row for row in code_rows if row["compare_status"] == "评分不同"]
                ),
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
        """Build the JSON payload exported from the current report page."""

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
            "spellcheck_comparison": spellcheck_context,
            "duplicate_comparison": duplicate_context,
        }

    def _build_dashboard_cards(self, dashboard: dict[str, int]) -> list[dict]:
        """Build the main dashboard cards shown for the code-side report."""

        return [
            {
                "label": "A 卷题目数",
                "value": dashboard.get("paper_a_question_count", 0),
                "hint": "代码版成功切分出的 A 卷题目数量",
            },
            {
                "label": "B 卷题目数",
                "value": dashboard.get("paper_b_question_count", 0),
                "hint": "代码版成功切分出的 B 卷题目数量",
            },
            {
                "label": "高度重复",
                "value": (
                    dashboard.get("paper_a_internal_high", 0)
                    + dashboard.get("paper_b_internal_high", 0)
                    + dashboard.get("cross_paper_high", 0)
                    + dashboard.get("history_high", 0)
                ),
                "hint": "代码版相似度 >= 95 的高风险题目对",
            },
            {
                "label": "疑似重复",
                "value": dashboard.get("suspected_duplicate_total", 0),
                "hint": "代码版相似度 85-94 的待复核题目对",
            },
            {
                "label": "错字问题",
                "value": dashboard.get("spellcheck_issue_total", 0),
                "hint": "代码版命中的错字、标点和重复字问题",
            },
            {
                "label": "历史题库命中",
                "value": dashboard.get("history_match_total", 0),
                "hint": "上传试卷与 history_bank 的命中题目对数量",
            },
            {
                "label": "待人工确认",
                "value": dashboard.get("pending_review_total", 0),
                "hint": "代码版重复明细默认进入人工复核状态",
            },
        ]

    def _build_agent_dashboard_cards(self, spellcheck_context: dict, duplicate_context: dict) -> list[dict]:
        """Build small comparison cards that summarize code vs Agent differences."""

        return [
            {
                "label": "错字结果一致",
                "value": spellcheck_context["summary"]["matched"],
                "hint": "代码版与 Agent 版都识别到的问题数",
            },
            {
                "label": "Agent 错字新增",
                "value": spellcheck_context["summary"]["agent_only"],
                "hint": "只在 Agent 结果中出现的错字问题数",
            },
            {
                "label": "重复结果一致",
                "value": duplicate_context["summary"]["matched"],
                "hint": "代码版与 Agent 版都命中的题目对",
            },
            {
                "label": "重复评分不同",
                "value": duplicate_context["summary"]["score_diff"],
                "hint": "两边识别到同一题目对但评分或等级不同",
            },
            {
                "label": "Agent 重复新增",
                "value": duplicate_context["summary"]["agent_only"],
                "hint": "只在 Agent 结果中出现的重复题目对",
            },
            {
                "label": "代码独有结论",
                "value": (
                    spellcheck_context["summary"]["code_only"]
                    + duplicate_context["summary"]["code_only"]
                ),
                "hint": "当前 Agent 结果中未覆盖的代码版发现数",
            },
        ]

    def _compare_extraction_section(
        self,
        code_run_result: PipelineRunResult,
        agent_run_result: PipelineRunResult,
    ) -> DualRunSectionComparison:
        """Compare module-level extraction output between code and Agent pipelines."""

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
        """Compare module-level splitting output between code and Agent pipelines."""

        code_result = self._question_summary(
            code_run_result.questions,
            uploaded_papers=code_run_result.uploaded_papers,
        )
        agent_result = self._question_summary(
            agent_run_result.questions,
            uploaded_papers=code_run_result.uploaded_papers,
        )
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
        """Compare duplicate detection summaries between code and Agent pipelines."""

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
        """Compare spellcheck summaries between code and Agent pipelines."""

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
        """Summarize question counts and question numbers by paper."""

        summary: dict[str, dict] = {}
        for paper in uploaded_papers or []:
            summary.setdefault(paper.paper_id, {"count": 0, "question_nos": []})
        for question in questions:
            paper_summary = summary.setdefault(question.paper_id, {"count": 0, "question_nos": []})
            paper_summary["count"] += 1
            paper_summary["question_nos"].append(question.question_no)
        return summary

    def _duplicate_summary(self, matches: list[SimilarityMatch]) -> dict:
        """Summarize duplicate counts by comparison type and level."""

        summary: dict[str, dict] = {}
        for match in matches:
            type_summary = summary.setdefault(
                match.comparison_type,
                {"total": 0, "high": 0, "suspect": 0},
            )
            type_summary["total"] += 1
            if match.level == "高度重复":
                type_summary["high"] += 1
            elif match.level == "疑似重复":
                type_summary["suspect"] += 1
        return summary

    def _spellcheck_summary(self, issues: list[SpellcheckIssue]) -> dict:
        """Summarize spellcheck issues by paper and issue type."""

        summary: dict[str, dict] = {}
        for issue in issues:
            paper_summary = summary.setdefault(issue.paper_id, {"total": 0, "types": {}})
            paper_summary["total"] += 1
            paper_summary["types"][issue.issue_type] = paper_summary["types"].get(issue.issue_type, 0) + 1
        return summary

    def _build_basic_spellcheck_rows(self, issues: list[SpellcheckIssue]) -> list[dict]:
        """Fallback renderer for non-dual-run spellcheck rows."""

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
        """Fallback renderer for non-dual-run duplicate rows."""

        rows: list[dict] = []
        for match in matches:
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

    def _pop_matching_spellcheck_issue(
        self,
        issue: SpellcheckIssue,
        issue_pool: list[SpellcheckIssue],
    ) -> SpellcheckIssue | None:
        """Remove and return the matching Agent issue if it exists."""

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
        """Remove and return the matching Agent duplicate row if it exists."""

        signature = similarity_signature(match)
        for index, agent_match in enumerate(match_pool):
            if similarity_signature(agent_match) == signature:
                return match_pool.pop(index)
        return None

    def _same_match_level(self, code_match: SimilarityMatch, agent_match: SimilarityMatch) -> bool:
        """Check whether two matching duplicate rows carry effectively the same judgment."""

        same_level = code_match.level == agent_match.level
        score_diff = abs(code_match.similarity_score - agent_match.similarity_score)
        return same_level and score_diff < 1


def spellcheck_signature(issue: SpellcheckIssue) -> tuple:
    """Build a stable signature for comparing spellcheck issues."""

    return (
        issue.paper_id,
        issue.question_no,
        issue.issue_type,
        issue.issue_text,
        issue.suggestion,
    )


def similarity_signature(match: SimilarityMatch) -> tuple:
    """Build a stable signature for comparing duplicate results."""

    return (
        match.comparison_type,
        match.source_paper_id,
        match.source_question_no,
        match.target_paper_id,
        match.target_question_no,
    )


def comparison_label(comparison_type: str) -> str:
    """Convert internal comparison types into readable Chinese labels."""

    mapping = {
        "within_paper_a": "A 卷内查重",
        "within_paper_b": "B 卷内查重",
        "cross_paper": "A/B 交叉查重",
        "history_bank": "历史题库比对",
    }
    return mapping.get(comparison_type, comparison_type)


def format_match_endpoint_label(match: SimilarityMatch, *, side: str) -> str:
    """Build readable labels for duplicate rows, including history-bank entries."""

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
    """
    Generate side-by-side highlighted HTML using a SequenceMatcher diff.

    Replaced or removed content on the left is marked with `diff-removed`.
    Replaced or inserted content on the right is marked with `diff-added`.
    """

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
