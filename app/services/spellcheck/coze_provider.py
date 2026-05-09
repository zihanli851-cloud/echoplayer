"""
Coze 智能体驱动的错别字检查 Provider

通过 Coze Workflow API 执行错别字检查任务。
"""

from __future__ import annotations

import json
import re
from typing import Any
from uuid import uuid4

from app.models.schemas import Question, SpellcheckIssue, UploadedPaper
from app.services.coze_service import CozeService, CozeServiceError
from app.services.spellcheck.base import SpellcheckProvider
from app.services.spellcheck.local_provider import LocalSpellcheckProvider


def build_questions_data(paper: UploadedPaper, questions: list[Question]) -> dict:
    """构建 Coze 工作流所需的 questions_data 输入格式。"""
    return {
        "paper_id": paper.paper_id,
        "subject": paper.subject or "未知",
        "questions": [
            {
                "question_id": q.question_id,
                "question_no": q.question_no,
                "order": idx,
                "content": q.content,
            }
            for idx, q in enumerate(questions)
        ],
    }


class CozeSpellcheckProvider(SpellcheckProvider):
    """Spellcheck provider backed by a Coze workflow with local fallback."""

    provider_name = "coze_spellcheck_provider"
    provider_label = "Coze 智能体错字检查"
    is_placeholder = False
    provider_note = ""

    def __init__(
        self,
        *,
        coze_service: CozeService | None = None,
        fallback_provider: SpellcheckProvider | None = None,
    ) -> None:
        self.coze_service = coze_service or CozeService()
        self.fallback_provider = fallback_provider or LocalSpellcheckProvider()
        self.is_placeholder = False

    def check_questions(
        self,
        paper: UploadedPaper,
        questions: list[Question],
    ) -> list[SpellcheckIssue]:
        """Run the Coze spellcheck workflow and normalize results."""

        if not questions:
            self.is_placeholder = True
            self.provider_note = "当前试卷未切分出题目，Coze 错字检查跳过。"
            return []

        questions_data = build_questions_data(paper, questions)
        try:
            response = self.coze_service.execute_spellcheck(questions_data)
        except CozeServiceError as exc:
            self.is_placeholder = True
            self.provider_note = f"Coze 错字工作流调用失败：{exc}"
            return []

        # 从响应中提取 error_checklist
        error_checklist = _find_first_list(response, "error_checklist")
        if error_checklist is None:
            # 尝试其他可能的字段名
            error_checklist = _find_first_list(response, "typo_list")
            if error_checklist is None:
                error_checklist = _find_first_list(response, "mistakes")
                if error_checklist is None:
                    self.is_placeholder = True
                    self.provider_note = "Coze 错字工作流未返回 error_checklist。"
                    return []

        issues = self._normalize_issues(error_checklist, paper, questions)
        self.is_placeholder = False
        self.provider_note = "Coze 智能体错字检查已接入。"
        return issues

    def _normalize_issues(
        self,
        error_checklist: list,
        paper: UploadedPaper,
        questions: list[Question],
    ) -> list[SpellcheckIssue]:
        question_by_no = {question.question_no: question for question in questions}
        issues: list[SpellcheckIssue] = []
        seen_signatures: set[tuple[str, str, str]] = set()

        for item in error_checklist:
            if not isinstance(item, dict):
                continue

            question_number = str(item.get("question_number", "")).strip()
            question = question_by_no.get(question_number)
            if question is None:
                if len(questions) == 1:
                    question = questions[0]
                else:
                    continue

            issue_text = str(item.get("suspected_error", item.get("error", ""))).strip()
            suggestion = str(item.get("correction_suggestion", item.get("correction", ""))).strip()
            signature = (question.question_id, issue_text, suggestion)
            if signature in seen_signatures:
                continue
            seen_signatures.add(signature)

            issues.append(
                SpellcheckIssue(
                    issue_id=uuid4().hex,
                    paper_id=paper.paper_id,
                    question_id=question.question_id,
                    question_no=question.question_no,
                    issue_type="疑似错误",
                    original_text=question.content,
                    issue_text=issue_text,
                    suggestion=suggestion,
                    confidence=None,
                )
            )

        return sorted(
            issues,
            key=lambda current: (current.paper_id, current.question_no, current.start_index or 0),
        )


def _find_first_list(data: Any, key: str) -> list | None:
    """Recursively find a list value by key in nested dict/list structures."""
    if isinstance(data, str):
        parsed = _parse_json_value(data)
        if parsed is not None:
            return _find_first_list(parsed, key)
        return None

    if isinstance(data, dict):
        for k, v in data.items():
            if k == key and isinstance(v, list):
                return v
            result = _find_first_list(v, key)
            if result is not None:
                return result
    elif isinstance(data, list):
        for item in data:
            result = _find_first_list(item, key)
            if result is not None:
                return result
    return None


def _parse_json_value(value: str) -> Any | None:
    stripped = value.strip()
    if not stripped:
        return None
    if stripped.startswith("```"):
        stripped = re.sub(r"^```(?:json)?\s*", "", stripped)
        stripped = re.sub(r"\s*```$", "", stripped)
    try:
        return json.loads(stripped)
    except ValueError:
        return None


class SkippedCozeSpellcheckProvider(SpellcheckProvider):
    """Spellcheck placeholder used when the Coze spellcheck workflow is disabled."""

    provider_name = "skipped_coze_spellcheck_provider"
    provider_label = "Coze 智能体错字检查"
    is_placeholder = True
    provider_note = "Coze 错字工作流暂未启用；当前仅运行 Agent 切题。"

    def check_questions(
        self,
        paper: UploadedPaper,
        questions: list[Question],
    ) -> list[SpellcheckIssue]:
        return []
