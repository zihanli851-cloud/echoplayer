from __future__ import annotations

from abc import ABC, abstractmethod
import re

from app.models.schemas import Question, UploadedPaper


CHINESE_MAJOR_PATTERN = re.compile(r"^(?P<label>[一二三四五六七八九十]+)[、.，]?\s*(?P<body>.*)$")
ARABIC_MAJOR_PATTERN = re.compile(r"^(?P<label>[1-9]\d*)[、.，]\s*(?P<body>.*)$")
SUBQUESTION_PATTERN = re.compile(r"^(?P<label>[（(]\d+[)）])\s*(?P<body>.*)$")
PAGE_NUMBER_PATTERN = re.compile(r"^\d{1,3}$")
TABLE_HEADER_SEPARATOR_PATTERN = re.compile(r"[:：\s]*")
SCHOOL_HEADER_PATTERN = re.compile(
    r"(大学|学院|学校|课程|科目|考试|试卷|教务|学期|班级|专业|姓名|学号|老师|系部)"
)

SECTION_HEADING_KEYWORDS = (
    "选择题",
    "选题",
    "单项选择题",
    "单项选题",
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
    "综合题",
    "阅读题",
    "翻译题",
    "实验题",
    "操作题",
)

PREAMBLE_SECTION_KEYWORDS = (
    "考试准备",
    "答题要求",
    "考试要求",
    "考试说明",
    "试卷说明",
    "考生注意事项",
    "注意事项",
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
    "本套试题",
    "考试用品",
    "以下各项由学生填写",
    "答题纸",
    "试题册",
    "不得携带",
    "考场纪律",
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
        raise NotImplementedError


class RuleQuestionSplitter(QuestionSplitProvider):
    provider_name = "rule_question_splitter"
    provider_label = "规则切题"

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
    normalized = re.sub(r" +([一二三四五六七八九十]+[、.，])", r"\n\1", normalized)
    normalized = re.sub(r" +([1-9]\d*[、，]|[1-9]\d*\.(?!\d))", r"\n\1", normalized)
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
        marker = match_major_question_marker(line)
        if marker and _is_preamble_section_body(marker[1]):
            in_preamble = True
            start_index = index + 1
            continue

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

        if in_preamble and marker:
            start_index = index + 1
            continue

        if marker:
            return lines[index:]

        if in_preamble:
            start_index = index + 1

    return lines[start_index:]


def _is_preamble_section_body(body: str) -> bool:
    compact = re.sub(r"\s+", "", body or "")
    return any(keyword in compact for keyword in PREAMBLE_SECTION_KEYWORDS)


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
    if re.fullmatch(r"[A-Za-z0-9_\-（）（）().、.\s]+", compact) and SCHOOL_HEADER_PATTERN.search(compact):
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
            "未识别到明显大题边界，已按兜底策略切题，请人工复核。",
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
