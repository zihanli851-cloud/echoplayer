from __future__ import annotations

from uuid import uuid4

from app.models.schemas import Question, SpellcheckIssue, UploadedPaper
from app.services.nuwa_service import NuwaService, NuwaServiceError
from app.services.spellcheck.base import SpellcheckProvider
from app.services.spellcheck.local_provider import LocalSpellcheckProvider


class NuwaSpellcheckProvider(SpellcheckProvider):
    """Spellcheck provider backed by a Nuwa workflow with local fallback."""

    provider_name = "nuwa_spellcheck_provider"
    provider_label = "Agent 版错字检查"
    is_placeholder = False
    provider_note = ""

    def __init__(
        self,
        *,
        nuwa_service: NuwaService | None = None,
        fallback_provider: SpellcheckProvider | None = None,
    ) -> None:
        self.nuwa_service = nuwa_service or NuwaService()
        self.fallback_provider = fallback_provider or LocalSpellcheckProvider()

    def check_questions(
        self,
        paper: UploadedPaper,
        questions: list[Question],
    ) -> list[SpellcheckIssue]:
        """Run the Nuwa spellcheck workflow and normalize `error_checklist`."""

        if not questions:
            self.provider_note = "当前试卷未切分出题目，Agent 错字检查跳过。"
            return []

        questions_data = build_questions_data(paper, questions)
        try:
            response = self.nuwa_service.execute_spellcheck_workflow(questions_data)
        except NuwaServiceError as exc:
            self.provider_note = f"女娲错字工作流调用失败，已回退本地规则：{exc}"
            return self.fallback_provider.check_questions(paper, questions)

        error_checklist = _find_first_list(response, "error_checklist")
        if error_checklist is None:
            self.provider_note = "女娲错字工作流未返回 error_checklist，已回退本地规则。"
            return self.fallback_provider.check_questions(paper, questions)

        issues = self._normalize_issues(error_checklist, paper, questions)
        self.provider_note = "Agent 错字检查已接入女娲工作流。"
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

            issue_text = str(item.get("suspected_error", "")).strip()
            suggestion = str(item.get("correction_suggestion", "")).strip()
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


def build_questions_data(paper: UploadedPaper, questions: list[Question]) -> dict:
    """Build the `inputs.questions_data` payload expected by the Nuwa workflow."""

    return {
        "paper_id": paper.paper_id,
        "subject": paper.subject,
        "questions": [
            {
                "question_id": question.question_id,
                "question_no": question.question_no,
                "order": question.order,
                "content": question.content,
            }
            for question in questions
        ],
    }


def _find_first_list(value, target_key: str):
    if isinstance(value, dict):
        for key, child in value.items():
            if key == target_key and isinstance(child, list):
                return child
            nested = _find_first_list(child, target_key)
            if nested is not None:
                return nested
    elif isinstance(value, list):
        for child in value:
            nested = _find_first_list(child, target_key)
            if nested is not None:
                return nested
    return None
