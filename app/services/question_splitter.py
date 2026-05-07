from __future__ import annotations

from abc import ABC, abstractmethod
import re
from typing import Any, Union

from app.models.schemas import Question, UploadedPaper
from app.services.nuwa_service import NuwaService, NuwaServiceError
from app.services.coze_service import CozeService, CozeServiceError


CHINESE_NUMERAL_PATTERN = re.compile(r"^(?P<label>[一二三四五六七八九十]+)[、.．]\s*(?P<body>.*)$")
CHINESE_SECTION_PATTERN = re.compile(r"^(?P<label>[一二三四五六七八九十]+)(?:[、.．]|\s+)\s*(?P<body>.*)$")
ARABIC_PATTERN = re.compile(r"^(?P<label>\d+)(?:、|[.．](?!\d))\s*(?P<body>.*)$")
SUBQUESTION_PATTERN = re.compile(r"^(?P<label>[（(]\d+[)）])\s*(?P<body>.*)$")
PAGE_NUMBER_PATTERN = re.compile(r"^\d{1,3}$")

SECTION_HEADING_KEYWORDS = (
    "选择题",
    "单项选择题",
    "多项选择题",
    "填空题",
    "判断题",
    "问答题",
    "简答题",
    "论述题",
    "名词解释",
    "编程题",
    "应用题",
    "计算题",
    "证明题",
    "分析题",
    "案例题",
    "案例分析题",
    "综合题",
    "阅读题",
    "翻译题",
    "实验题",
    "操作题",
)

PREAMBLE_TRIGGER_KEYWORDS = (
    "以下各项由命题教师填写",
    "以下各项由学生填写",
    "课程名称",
    "命题教师",
    "适用对象",
    "使用试题的任课教师姓名",
    "试题说明",
    "试卷说明",
    "考试说明",
    "考生注意事项",
    "考试时间",
    "考试类型",
    "考试用品",
    "任课教师",
    "学生姓名",
    "学号",
)

QUESTION_BODY_KEYS = (
    "content",
    "question_content",
    "question_text",
    "text",
    "body",
    "stem",
    "question",
)
QUESTION_NO_KEYS = (
    "question_no",
    "question_number",
    "number",
    "no",
    "label",
    "title",
    "name",
)
QUESTION_CHILD_KEYS = ("content", "children", "items", "questions", "question_list", "sections", "data")


class QuestionSplitProvider(ABC):
    """Provider interface for code-based or Agent-based question splitting."""

    provider_name = "unknown"
    provider_label = "未命名切题器"
    is_placeholder = False
    provider_note = ""

    @abstractmethod
    def split(
        self,
        text: str,
        paper_id: str,
        *,
        paper: UploadedPaper | None = None,
    ) -> list[Question]:
        """Split a paper text into structured questions."""


class RuleQuestionSplitter(QuestionSplitProvider):
    """Rule-based question splitter used by the code pipeline."""

    provider_name = "rule_question_splitter"
    provider_label = "代码版规则切题"
    is_placeholder = False

    def split(
        self,
        text: str,
        paper_id: str,
        *,
        paper: UploadedPaper | None = None,
    ) -> list[Question]:
        return _split_questions_impl(text, paper_id)


class AgentQuestionSplitter(QuestionSplitProvider):
    """Agent-side splitter backed by a Coze workflow (with optional Nuwa fallback) and local fallback."""

    provider_name = "agent_question_splitter"
    provider_label = "Coze 智能体切题"
    is_placeholder = False
    provider_note = ""

    def __init__(
        self,
        *,
        coze_service: CozeService | None = None,
        nuwa_service: NuwaService | None = None,
        fallback_provider: QuestionSplitProvider | None = None,
    ) -> None:
        # 支持 Coze 或 Nuwa 服务
        self._service: Any = None
        self._service_type: str = ""
        if coze_service is not None:
            self._service = coze_service
            self._service_type = "coze"
        elif nuwa_service is not None:
            self._service = nuwa_service
            self._service_type = "nuwa"
        else:
            # 默认创建 CozeService
            try:
                self._service = CozeService()
                self._service_type = "coze"
            except CozeServiceError:
                self._service = NuwaService()
                self._service_type = "nuwa"

        self.fallback_provider = fallback_provider or RuleQuestionSplitter()
        self._paper_notes: dict[str, str] = {}

    def split(
        self,
        text: str,
        paper_id: str,
        *,
        paper: UploadedPaper | None = None,
    ) -> list[Question]:
        stripped_text = text.strip()
        if not stripped_text:
            self._set_note(paper_id, f"{paper_id} 卷未提取到文本，Agent 切题跳过。")
            return []

        paper_payload = build_split_workflow_inputs(paper, paper_id, stripped_text)

        try:
            if self._service_type == "coze":
                response = self._service.execute_split(paper_payload)
            else:
                response = self._service.execute_split_workflow(paper_payload)
        except (CozeServiceError, NuwaServiceError) as exc:
            service_name = "Coze" if self._service_type == "coze" else "女娲"
            self._set_note(paper_id, f"{paper_id} 卷 {service_name} 切题调用失败，已回退本地规则：{exc}")
            return self.fallback_provider.split(stripped_text, paper_id, paper=paper)

        questions = self._normalize_questions(response, paper_id)
        if questions:
            service_name = "Coze" if self._service_type == "coze" else "女娲"
            self._set_note(paper_id, f"{paper_id} 卷 Agent 切题已接入 {service_name} 工作流。")
            return questions

        service_name = "Coze" if self._service_type == "coze" else "女娲"
        self._set_note(paper_id, f"{paper_id} 卷 {service_name} 切题未返回可识别题目，已回退本地规则。")
        return self.fallback_provider.split(stripped_text, paper_id, paper=paper)

    def _normalize_questions(self, response: Any, paper_id: str) -> list[Question]:
        items = _find_question_container(response)
        if items is None:
            return []

        flattened_questions: list[Question] = []
        seen_signatures: set[tuple[str, str]] = set()
        _append_questions_from_value(items, paper_id, flattened_questions, seen_signatures)
        return flattened_questions

    def _set_note(self, paper_id: str, note: str) -> None:
        self._paper_notes[paper_id] = note
        self.provider_note = "；".join(self._paper_notes[key] for key in sorted(self._paper_notes))


def build_split_workflow_inputs(
    paper: UploadedPaper | None,
    paper_id: str,
    text: str,
) -> dict[str, Any]:
    """Build a forgiving split-workflow payload from local PDF extraction output."""

    filename = paper.filename if paper else f"{paper_id}.pdf"
    subject = paper.subject if paper else ""
    page_count = paper.page_count if paper else 0

    return {
        "paper_id": paper_id,
        "subject": subject,
        "filename": filename,
        "page_count": page_count,
        "text_content": text,
        "text": text,
        "content": text,
        "paper_text": text,
    }


def normalize_question_text(text: str) -> str:
    """
    Normalize raw extracted text before splitting.

    The MVP keeps this step conservative: it only normalizes line breaks and
    inserts a newline before obvious question markers when they are separated by spaces.
    """

    normalized = text.replace("\r\n", "\n").replace("\r", "\n").replace("\u3000", " ")
    normalized = re.sub(r"[ \t]+", " ", normalized)
    normalized = re.sub(r" +([一二三四五六七八九十]+[、.．])", r"\n\1", normalized)
    normalized = re.sub(r" +((?:\d+、|(?:\d+[.．](?!\d))))", r"\n\1", normalized)
    normalized = re.sub(r" +([（(]\d+[)）])", r"\n\1", normalized)
    return normalized.strip()


def split_normalized_lines(text: str) -> list[str]:
    """Split normalized text into clean lines and remove standalone page numbers."""

    return [
        line.strip()
        for line in text.split("\n")
        if line.strip() and not PAGE_NUMBER_PATTERN.fullmatch(line.strip())
    ]


def match_question_marker(line: str) -> tuple[str, str] | None:
    """Return a normalized question number and the remaining content if the line starts a question."""

    for pattern in (CHINESE_NUMERAL_PATTERN, ARABIC_PATTERN, SUBQUESTION_PATTERN):
        match = pattern.match(line)
        if match:
            return match.group("label"), match.group("body").strip()
    return None


def is_section_heading(line: str) -> bool:
    """Check whether a line is a section title like `一、问答题` instead of a real question."""

    for pattern in (CHINESE_SECTION_PATTERN, ARABIC_PATTERN):
        match = pattern.match(line)
        if not match:
            continue

        body = match.group("body").strip()
        if any(keyword in body for keyword in SECTION_HEADING_KEYWORDS):
            return True

    return False


def strip_preamble_lines(lines: list[str]) -> list[str]:
    """
    Remove cover-page instructions before the real questions begin.

    Many exam PDFs start with metadata, exam notes, and student instructions.
    We skip those lines until the first clear question section or actual question.
    """

    if not lines:
        return lines

    in_preamble = False

    for index, line in enumerate(lines):
        if any(keyword in line for keyword in PREAMBLE_TRIGGER_KEYWORDS):
            in_preamble = True
            continue

        if is_section_heading(line):
            return lines[index:]

        marker = match_question_marker(line)
        if not marker:
            continue

        label, _ = marker
        if in_preamble and label.isdigit():
            continue

        return lines[index:]

    return lines


def split_questions(text: str, paper_id: str) -> list[Question]:
    """
    Split one paper's text into basic question blocks.

    Supported markers:
    - 一、二、三
    - 1. 2. 3.
    - （1）（2）
    If no marker is found, the whole text becomes one fallback question.
    """

    return RuleQuestionSplitter().split(text, paper_id)


def _split_questions_impl(text: str, paper_id: str) -> list[Question]:
    """Internal implementation for the rule-based splitter."""

    normalized_text = normalize_question_text(text)
    if not normalized_text:
        return []

    lines = strip_preamble_lines(split_normalized_lines(normalized_text))
    questions: list[Question] = []
    current_label: str | None = None
    current_lines: list[str] = []

    def flush_current() -> None:
        if current_label is None:
            return

        content = "\n".join(current_lines).strip()
        if not content:
            return

        order = len(questions) + 1
        questions.append(
            Question(
                question_id=f"{paper_id}-{order}",
                paper_id=paper_id,
                question_no=current_label,
                order=order,
                content=content,
                raw_block=content,
            )
        )

    for line in lines:
        if is_section_heading(line):
            flush_current()
            current_label = None
            current_lines = []
            continue

        marker = match_question_marker(line)
        if marker:
            flush_current()
            current_label, body = marker
            current_lines = [body] if body else []
        else:
            if current_label is None:
                current_label = "1"
                current_lines = [line]
            else:
                current_lines.append(line)

    flush_current()

    if questions:
        return questions

    return [
        Question(
            question_id=f"{paper_id}-1",
            paper_id=paper_id,
            question_no="1",
            order=1,
            content=normalized_text,
            raw_block=normalized_text,
        )
    ]


def _find_question_container(value: Any) -> Any | None:
    if isinstance(value, list):
        if _looks_like_question_list(value):
            return value
        for child in value:
            nested = _find_question_container(child)
            if nested is not None:
                return nested
        return None

    if isinstance(value, dict):
        for child in value.values():
            if isinstance(child, list) and _looks_like_question_list(child):
                return child
        for child in value.values():
            nested = _find_question_container(child)
            if nested is not None:
                return nested

    return None


def _looks_like_question_list(value: list[Any]) -> bool:
    if not value:
        return False

    dict_items = [item for item in value if isinstance(item, dict)]
    if not dict_items:
        return False

    for item in dict_items:
        if _extract_question_body(item):
            return True
        if any(key in item for key in QUESTION_CHILD_KEYS):
            return True
    return False


def _append_questions_from_value(
    value: Any,
    paper_id: str,
    questions: list[Question],
    seen_signatures: set[tuple[str, str]],
) -> None:
    if isinstance(value, list):
        for child in value:
            _append_questions_from_value(child, paper_id, questions, seen_signatures)
        return

    if not isinstance(value, dict):
        return

    body = _extract_question_body(value)
    if body:
        normalized_body = body.strip()
        if normalized_body:
            question_no = _extract_question_no(value, normalized_body, len(questions) + 1)
            signature = (question_no, normalized_body)
            if signature not in seen_signatures:
                seen_signatures.add(signature)
                order = len(questions) + 1
                questions.append(
                    Question(
                        question_id=f"{paper_id}-{order}",
                        paper_id=paper_id,
                        question_no=question_no,
                        order=order,
                        content=normalized_body,
                        raw_block=normalized_body,
                    )
                )

    for key in QUESTION_CHILD_KEYS:
        child = value.get(key)
        if isinstance(child, (list, dict)):
            _append_questions_from_value(child, paper_id, questions, seen_signatures)


def _extract_question_body(value: dict[str, Any]) -> str:
    for key in QUESTION_BODY_KEYS:
        candidate = value.get(key)
        if isinstance(candidate, str) and candidate.strip():
            return candidate.strip()
    return ""


def _extract_question_no(value: dict[str, Any], body: str, order: int) -> str:
    for key in QUESTION_NO_KEYS:
        candidate = value.get(key)
        if not isinstance(candidate, str):
            continue
        normalized = candidate.strip()
        if normalized and _looks_like_question_no(normalized):
            return normalized

    marker = match_question_marker(body)
    if marker:
        return marker[0]
    return str(order)


def _looks_like_question_no(value: str) -> bool:
    if re.fullmatch(r"(?:第\s*)?\d+\s*题?", value):
        return True
    if re.fullmatch(r"[一二三四五六七八九十]+[、.．题]?", value):
        return True
    if re.fullmatch(r"[（(]\d+[)）]", value):
        return True
    return False
