import re
from abc import ABC, abstractmethod

from app.models.schemas import Question


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
    "学 号",
)


class QuestionSplitProvider(ABC):
    """Provider interface for code-based or Agent-based question splitting."""

    provider_name = "unknown"
    provider_label = "未命名切题器"
    is_placeholder = False
    provider_note = ""

    @abstractmethod
    def split(self, text: str, paper_id: str) -> list[Question]:
        """Split a paper text into structured questions."""


class RuleQuestionSplitter(QuestionSplitProvider):
    """Rule-based question splitter used by the code pipeline."""

    provider_name = "rule_question_splitter"
    provider_label = "代码版规则切题"
    is_placeholder = False

    def split(self, text: str, paper_id: str) -> list[Question]:
        return _split_questions_impl(text, paper_id)


class AgentQuestionSplitter(QuestionSplitProvider):
    """Placeholder Agent-side splitter that reuses the rule-based logic."""

    provider_name = "agent_question_splitter"
    provider_label = "Agent 版切题"
    is_placeholder = True
    provider_note = "当前为占位实现，复用代码版切题逻辑。"

    def __init__(self, fallback_provider: QuestionSplitProvider | None = None) -> None:
        self.fallback_provider = fallback_provider or RuleQuestionSplitter()

    def split(self, text: str, paper_id: str) -> list[Question]:
        return self.fallback_provider.split(text, paper_id)


def normalize_question_text(text: str) -> str:
    """
    Normalize raw extracted text before splitting.

    The MVP keeps this step conservative: it only normalizes line breaks and
    inserts a newline before obvious question markers when they are separated by spaces.
    """

    normalized = text.replace("\r\n", "\n").replace("\r", "\n").replace("\u3000", " ")
    normalized = re.sub(r"[ \t]+", " ", normalized)
    normalized = re.sub(r" +([一二三四五六七八九十]+[、.．])", r"\n\1", normalized)
    normalized = re.sub(r" +((?:\d+、)|(?:\d+[.．](?!\d)))", r"\n\1", normalized)
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
