from __future__ import annotations

from abc import ABC, abstractmethod
import re
from typing import Any

from app.models.schemas import Question, UploadedPaper
from app.services.coze_service import CozeService, CozeServiceError
from app.services.nuwa_service import NuwaService, NuwaServiceError


CHINESE_MAJOR_PATTERN = re.compile(r"^(?P<label>[一二三四五六七八九十]+)[、.．:：)]?\s*(?P<body>.*)$")
ARABIC_MAJOR_PATTERN = re.compile(r"^(?P<label>[1-9]\d*)[、.．:：)]\s*(?P<body>.*)$")
SUBQUESTION_PATTERN = re.compile(r"^(?P<label>[（(]\d+[)）])\s*(?P<body>.*)$")
PAGE_NUMBER_PATTERN = re.compile(r"^\d{1,3}$")
TABLE_HEADER_SEPARATOR_PATTERN = re.compile(r"[:：]\s*")
SCHOOL_HEADER_PATTERN = re.compile(
    r"(大学|学院|学校|课程|科目|考试|试卷|教务|学期|班级|专业|姓名|学号|教师|学院|系|部)"
)

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
    "课程名称",
    "课程代码",
    "命题教师",
    "任课教师",
    "考试说明",
    "试卷说明",
    "考生注意事项",
    "考试时间",
    "考试类型",
    "适用对象",
    "学生姓名",
    "姓名",
    "学号",
    "院系",
    "学院",
    "学校",
    "班级",
    "专业",
    "装订线",
)

NOISE_HEADER_KEYWORDS = (
    "大学",
    "学院",
    "学校",
    "课程名称",
    "科目",
    "考试",
    "试卷",
    "命题教师",
    "任课教师",
    "教研室",
    "班级",
    "姓名",
    "学号",
    "专业",
    "院系",
    "适用对象",
    "考试时间",
    "考试类型",
    "试卷代码",
    "课程代码",
    "座位号",
    "总分",
)

FORMULA_GLYPH_MAP = {
    "\uf02b": ".",
    "\uf02d": "-",
    "\uf03c": "<",
    "\uf03e": ">",
    "\uf071": "0",
}


class QuestionSplitProvider(ABC):
    """Provider interface for code-based or Agent-based question splitting."""

    provider_name = "unknown"
    provider_label = "Unknown Splitter"
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

    def __init__(self, *, split_mode: str = "major_question_mode") -> None:
        self.split_mode = split_mode

    def split(
        self,
        text: str,
        paper_id: str,
        *,
        paper: UploadedPaper | None = None,
    ) -> list[Question]:
        return _split_questions_impl(text, paper_id, split_mode=self.split_mode)


class AgentQuestionSplitter(QuestionSplitProvider):
    """Agent-side splitter backed by Coze/Nuwa with local fallback."""

    provider_name = "agent_question_splitter"
    provider_label = "Agent 切题"

    def __init__(
        self,
        *,
        coze_service: CozeService | None = None,
        nuwa_service: NuwaService | None = None,
        fallback_provider: QuestionSplitProvider | None = None,
    ) -> None:
        self._service: Any = None
        self._service_type = ""
        if coze_service is not None:
            self._service = coze_service
            self._service_type = "coze"
        elif nuwa_service is not None:
            self._service = nuwa_service
            self._service_type = "nuwa"
        else:
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
        stripped = text.strip()
        if not stripped:
            self.is_placeholder = True
            self._set_note(paper_id, f"{paper_id} 卷未提取到文本，Agent 切题跳过。")
            return []

        try:
            if self._service_type == "coze":
                local_questions = self.fallback_provider.split(stripped, paper_id, paper=paper)
                response = self._service.execute_split(
                    stripped,
                    paper_id=paper_id,
                    subject=paper.subject if paper else "",
                    filename=paper.filename if paper else "",
                    questions=[
                        {
                            "content": question.content,
                            "order": question.order,
                            "question_id": question.question_id,
                            "question_no": question.question_no,
                        }
                        for question in local_questions
                    ]
                    or None,
                )
            else:
                response = self._service.execute_split_workflow(
                    build_split_workflow_inputs(paper, paper_id, stripped)
                )
        except (CozeServiceError, NuwaServiceError) as exc:
            if self._service_type == "coze":
                self.is_placeholder = True
                self._set_note(paper_id, f"{paper_id} 卷 Coze 切题调用失败：{exc}")
                return []
            self._set_note(paper_id, f"{paper_id} 卷 Nuwa 切题调用失败，已回退本地规则：{exc}")
            return self.fallback_provider.split(stripped, paper_id, paper=paper)

        questions = self._normalize_questions(response, paper_id)
        if questions:
            self.is_placeholder = False
            service_name = "Coze" if self._service_type == "coze" else "Nuwa"
            self._set_note(paper_id, f"{paper_id} 卷 Agent 切题已接入 {service_name} 工作流。")
            return questions

        if self._service_type == "coze":
            self.is_placeholder = True
            self._set_note(paper_id, f"{paper_id} 卷 Coze 切题未返回可识别题目。")
            return []

        self._set_note(paper_id, f"{paper_id} 卷 Nuwa 切题未返回可识别题目，已回退本地规则。")
        return self.fallback_provider.split(stripped, paper_id, paper=paper)

    def _normalize_questions(self, response: Any, paper_id: str) -> list[Question]:
        items = _find_question_container(response)
        if items is None:
            return []
        flattened: list[Question] = []
        seen: set[tuple[str, str]] = set()
        _append_questions_from_value(items, paper_id, flattened, seen)
        return flattened

    def _set_note(self, paper_id: str, note: str) -> None:
        self._paper_notes[paper_id] = note
        self.provider_note = "；".join(self._paper_notes[key] for key in sorted(self._paper_notes))


def build_split_workflow_inputs(
    paper: UploadedPaper | None,
    paper_id: str,
    text: str,
) -> dict[str, Any]:
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


def normalize_formula_glyphs(text: str) -> str:
    normalized = text
    for source, target in FORMULA_GLYPH_MAP.items():
        normalized = normalized.replace(source, target)
    normalized = re.sub(r"\ufffd+", " ", normalized)
    normalized = re.sub(r"\s+", " ", normalized)
    return normalized


def normalize_question_text(text: str) -> str:
    normalized = normalize_formula_glyphs(text)
    normalized = normalized.replace("\r\n", "\n").replace("\r", "\n").replace("\u3000", " ")
    normalized = re.sub(r"[ \t]+", " ", normalized)
    normalized = re.sub(r" +([（(]\d+[)）])", r"\n\1", normalized)
    normalized = re.sub(r" +([一二三四五六七八九十]+[、.．:：)])", r"\n\1", normalized)
    normalized = re.sub(r" +([1-9]\d*[、.．:：)])", r"\n\1", normalized)
    return normalized.strip()


def split_normalized_lines(text: str) -> list[str]:
    return [
        line.strip()
        for line in text.split("\n")
        if line.strip() and not PAGE_NUMBER_PATTERN.fullmatch(line.strip())
    ]


def match_major_question_marker(line: str) -> tuple[str, str] | None:
    for pattern in (CHINESE_MAJOR_PATTERN, ARABIC_MAJOR_PATTERN):
        match = pattern.match(line)
        if match:
            return match.group("label"), match.group("body").strip()
    return None


def match_question_marker(line: str) -> tuple[str, str] | None:
    marker = match_major_question_marker(line)
    if marker:
        return marker
    match = SUBQUESTION_PATTERN.match(line)
    if match:
        return match.group("label"), match.group("body").strip()
    return None


def is_section_heading(line: str) -> bool:
    marker = match_major_question_marker(line)
    if not marker:
        return False
    _, body = marker
    return any(keyword in body for keyword in SECTION_HEADING_KEYWORDS)


def strip_preamble_lines(lines: list[str]) -> list[str]:
    if not lines:
        return lines

    start_index = 0
    in_preamble = False

    for index, line in enumerate(lines):
        if _looks_like_header_noise(line):
            in_preamble = True
            start_index = index + 1
            continue

        if any(keyword in line for keyword in PREAMBLE_TRIGGER_KEYWORDS):
            in_preamble = True
            start_index = index + 1
            continue

        if is_section_heading(line):
            return lines[index:]

        marker = match_major_question_marker(line)
        if marker:
            return lines[index:]

        if in_preamble:
            start_index = index + 1

    return lines[start_index:]


def split_questions(text: str, paper_id: str) -> list[Question]:
    return RuleQuestionSplitter().split(text, paper_id)


def _looks_like_header_noise(line: str) -> bool:
    compact = TABLE_HEADER_SEPARATOR_PATTERN.sub("", line).strip()
    if not compact:
        return True
    if len(compact) <= 2 and SCHOOL_HEADER_PATTERN.search(compact):
        return True
    if any(keyword in compact for keyword in NOISE_HEADER_KEYWORDS):
        return True
    if compact.count(" ") >= 3 and SCHOOL_HEADER_PATTERN.search(compact):
        return True
    if re.fullmatch(r"[A-Za-z0-9_\-（）()：:、\s]+", compact) and SCHOOL_HEADER_PATTERN.search(compact):
        return True
    return False


def _split_questions_impl(text: str, paper_id: str, *, split_mode: str) -> list[Question]:
    normalized_text = normalize_question_text(text)
    if not normalized_text:
        return []

    lines = strip_preamble_lines(split_normalized_lines(normalized_text))
    questions: list[Question] = []
    current_label: str | None = None
    current_lines: list[str] = []
    saw_major_marker = False

    def flush_current() -> None:
        nonlocal current_label, current_lines
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

        major_marker = match_major_question_marker(line)
        if major_marker:
            saw_major_marker = True
            flush_current()
            current_label, body = major_marker
            current_lines = [body] if body else []
            continue

        subquestion = SUBQUESTION_PATTERN.match(line)
        if subquestion and split_mode == "subquestion_expand_mode":
            flush_current()
            current_label = subquestion.group("label")
            current_lines = [subquestion.group("body").strip()] if subquestion.group("body").strip() else []
            continue

        if current_label is None:
            current_label = "1"
            current_lines = [line]
        else:
            current_lines.append(line)

    flush_current()

    if not questions:
        return [
            Question(
                question_id=f"{paper_id}-1",
                paper_id=paper_id,
                question_no="1",
                order=1,
                content=normalized_text,
                raw_block=normalized_text,
                split_confidence=0.35,
                split_warning="未识别到明确题号，已按整段文本兜底切为单题，请人工复核。",
            )
        ]

    if not saw_major_marker:
        return _mark_low_confidence_questions(
            questions,
            "未识别到明确大题边界，已按兜底策略切题，请人工复核。",
            confidence=0.5,
        )
    return questions


def _mark_low_confidence_questions(
    questions: list[Question],
    warning: str,
    *,
    confidence: float,
) -> list[Question]:
    return [
        question.model_copy(
            update={
                "split_confidence": confidence,
                "split_warning": warning,
            }
        )
        for question in questions
    ]


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
    dict_items = [item for item in value if isinstance(item, dict)]
    if not dict_items:
        return False
    return any(_extract_question_body(item) or any(key in item for key in QUESTION_CHILD_KEYS) for item in dict_items)


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
        if isinstance(candidate, str) and candidate.strip():
            return candidate.strip()
    marker = match_question_marker(body)
    if marker:
        return marker[0]
    return str(order)
