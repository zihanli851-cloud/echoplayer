from __future__ import annotations

from abc import ABC, abstractmethod
from collections import Counter
from difflib import SequenceMatcher
from itertools import combinations
import re
import unicodedata

from app.models.schemas import Question, SimilarityMatch, UploadedPaper
from app.services.question_normalizer import template_similarity


IMAGE_PLACEHOLDER_PATTERN = re.compile(r"\[IMAGE[^\]]*\]", re.IGNORECASE)
OCR_TEXT_MARKER_PATTERN = re.compile(r"\[OCR_TEXT\]", re.IGNORECASE)
BOILERPLATE_LINE_PATTERNS = [
    re.compile(pattern)
    for pattern in (
        r"链条?测试",
        r"答题要求",
        r"考试准备",
        r"学生注意事项",
        r"本套试题",
        r"完卷时间",
        r"考生不得携带",
        r"不得携带",
        r"进入考场",
    )
]
BOILERPLATE_KEYWORDS = (
    "考试准备",
    "答题要求",
    "学生注意事项",
    "本套试题",
    "完卷时间",
    "考生不得携带",
    "不得携带",
    "进入考场",
)

try:
    from rapidfuzz import fuzz
except ImportError:  # pragma: no cover
    class _FallbackFuzz:
        @staticmethod
        def ratio(left: str, right: str) -> float:
            return SequenceMatcher(None, left, right).ratio() * 100

    fuzz = _FallbackFuzz()


class SimilarityComparatorProvider(ABC):
    """Provider interface for duplicate detection and cross-paper comparison."""

    provider_name = "unknown"
    provider_label = "Unknown Comparator"
    is_placeholder = False
    provider_note = ""

    @abstractmethod
    def compare(
        self,
        paper_a_questions: list[Question],
        paper_b_questions: list[Question] | None = None,
        history_questions: list[Question] | None = None,
        *,
        uploaded_papers: list[UploadedPaper] | None = None,
    ) -> list[SimilarityMatch]:
        raise NotImplementedError


class CodeSimilarityComparator(SimilarityComparatorProvider):
    """Local comparator based on text and template normalization."""

    provider_name = "code_similarity_comparator"
    provider_label = "代码版查重比对"

    def __init__(
        self,
        *,
        threshold: float = 85,
        history_top_k: int = 3,
        use_lightweight_vector: bool = True,
    ) -> None:
        self.threshold = threshold
        self.history_top_k = history_top_k
        self.use_lightweight_vector = use_lightweight_vector

    def compare(
        self,
        paper_a_questions: list[Question],
        paper_b_questions: list[Question] | None = None,
        history_questions: list[Question] | None = None,
        *,
        uploaded_papers: list[UploadedPaper] | None = None,
    ) -> list[SimilarityMatch]:
        matches: list[SimilarityMatch] = []
        matches.extend(compare_within_paper(paper_a_questions, "within_paper_a", threshold=self.threshold))
        if paper_b_questions:
            matches.extend(compare_within_paper(paper_b_questions, "within_paper_b", threshold=self.threshold))
            matches.extend(compare_cross_papers(paper_a_questions, paper_b_questions, threshold=self.threshold))

        if history_questions:
            subject = _resolve_subject(uploaded_papers, "A")
            matches.extend(
                compare_against_history_bank(
                    paper_a_questions,
                    history_questions,
                    threshold=self.threshold,
                    top_k_per_question=self.history_top_k,
                    use_lightweight_vector=self.use_lightweight_vector,
                    course_filter=subject,
                )
            )
            if paper_b_questions:
                subject_b = _resolve_subject(uploaded_papers, "B") or subject
                matches.extend(
                    compare_against_history_bank(
                        paper_b_questions,
                        history_questions,
                        threshold=self.threshold,
                        top_k_per_question=self.history_top_k,
                        use_lightweight_vector=self.use_lightweight_vector,
                        course_filter=subject_b,
                    )
                )
        return sorted(matches, key=lambda item: item.similarity_score, reverse=True)


def strip_compare_boilerplate(text: str) -> str:
    lines = [line.strip() for line in str(text or "").splitlines()]
    kept: list[str] = []
    for line in lines:
        if not line:
            continue
        compact = re.sub(r"\s+", "", line)
        if any(keyword in compact for keyword in BOILERPLATE_KEYWORDS):
            continue
        if any(pattern.search(compact) for pattern in BOILERPLATE_LINE_PATTERNS):
            continue
        kept.append(line)
    return "\n".join(kept).strip()


def normalize_for_compare(text: str) -> str:
    normalized = strip_compare_boilerplate(text)
    normalized = IMAGE_PLACEHOLDER_PATTERN.sub(" ", normalized)
    normalized = OCR_TEXT_MARKER_PATTERN.sub(" ", normalized)
    normalized = unicodedata.normalize("NFKC", normalized).lower()
    normalized = re.sub(r"[（(]\s*\d+\s*[)）]", " ", normalized)
    normalized = re.sub(r"\s+", "", normalized)
    return normalized.strip()


def classify_similarity(score: float, *, template_score_value: float | None = None) -> str:
    if score >= 95:
        return "高度重复"
    if template_score_value is not None and template_score_value >= 92:
        return "疑似原题"
    if score >= 85:
        return "疑似重复"
    return "差异较大"


def build_match(
    source: Question,
    target: Question,
    comparison_type: str,
    score: float,
    *,
    literal_score: float | None = None,
    template_score_value: float | None = None,
    match_source: str = "text",
) -> SimilarityMatch:
    final_score = round(score, 2)
    literal_score = round(literal_score if literal_score is not None else score, 2)
    template_score_value = round(template_score_value if template_score_value is not None else score, 2)
    is_same_source_question = template_score_value >= 92 and literal_score < 95
    return SimilarityMatch(
        match_id=(
            f"{comparison_type}-{match_source}-{source.question_id}-{target.question_id}"
            if comparison_type == "history_bank"
            else f"{comparison_type}-{source.question_id}-{target.question_id}"
        ),
        comparison_type=comparison_type,
        source_paper_id=source.paper_id,
        source_paper_label=source.paper_label or source.paper_id,
        source_question_id=source.question_id,
        source_question_no=source.question_no,
        source_text=source.content,
        target_paper_id=target.paper_id,
        target_paper_label=target.paper_label or target.paper_id,
        target_question_id=target.question_id,
        target_question_no=target.question_no,
        target_text=target.content,
        similarity_score=final_score,
        literal_score=literal_score,
        template_score=template_score_value,
        final_score=final_score,
        is_same_source_question=is_same_source_question,
        level=classify_similarity(final_score, template_score_value=template_score_value),
    )


def compare_within_paper(
    questions: list[Question],
    comparison_type: str,
    threshold: float = 85,
) -> list[SimilarityMatch]:
    matches: list[SimilarityMatch] = []
    for source, target in combinations(questions, 2):
        source_text = strip_compare_boilerplate(source.content)
        target_text = strip_compare_boilerplate(target.content)
        literal_score = _text_similarity_score(source_text, target_text)
        template_score_value = template_similarity(source_text, target_text)
        score = max(literal_score, template_score_value)
        if score >= threshold:
            matches.append(
                build_match(
                    source,
                    target,
                    comparison_type,
                    score,
                    literal_score=literal_score,
                    template_score_value=template_score_value,
                )
            )
    return sorted(matches, key=lambda item: item.similarity_score, reverse=True)


def compare_cross_papers(
    paper_a_questions: list[Question],
    paper_b_questions: list[Question],
    threshold: float = 85,
) -> list[SimilarityMatch]:
    matches: list[SimilarityMatch] = []
    for source in paper_a_questions:
        for target in paper_b_questions:
            source_text = strip_compare_boilerplate(source.content)
            target_text = strip_compare_boilerplate(target.content)
            literal_score = _text_similarity_score(source_text, target_text)
            template_score_value = template_similarity(source_text, target_text)
            score = max(literal_score, template_score_value)
            if score >= threshold:
                matches.append(
                    build_match(
                        source,
                        target,
                        "cross_paper",
                        score,
                        literal_score=literal_score,
                        template_score_value=template_score_value,
                    )
                )
    return sorted(matches, key=lambda item: item.similarity_score, reverse=True)


def compare_against_history_bank(
    source_questions: list[Question],
    history_questions: list[Question],
    *,
    threshold: float = 85,
    top_k_per_question: int = 3,
    use_lightweight_vector: bool = True,
    course_filter: str = "",
) -> list[SimilarityMatch]:
    matches: list[SimilarityMatch] = []
    filtered_history_questions = _filter_history_questions(history_questions, course_filter=course_filter)
    vector_index = getattr(filtered_history_questions, "vector_index", getattr(history_questions, "vector_index", None))

    if vector_index is not None and use_lightweight_vector and not course_filter:
        for source in source_questions:
            for hit in vector_index.search(source, threshold=threshold, top_k=top_k_per_question):
                source_text = strip_compare_boilerplate(source.content)
                target_text = strip_compare_boilerplate(hit.question.content)
                literal_score = _text_similarity_score(source_text, target_text)
                template_score_value = template_similarity(source_text, target_text)
                score = max(hit.score, template_score_value)
                matches.append(
                    build_match(
                        source,
                        hit.question,
                        "history_bank",
                        score,
                        literal_score=literal_score,
                        template_score_value=template_score_value,
                        match_source=hit.match_source,
                    )
                )
        return sorted(matches, key=lambda item: item.similarity_score, reverse=True)

    for source in source_questions:
        source_matches: list[SimilarityMatch] = []
        for target in filtered_history_questions:
            source_text = strip_compare_boilerplate(source.content)
            target_text = strip_compare_boilerplate(target.content)
            literal_score = _text_similarity_score(source_text, target_text)
            vector_score = lightweight_vector_similarity(source_text, target_text) if use_lightweight_vector else 0.0
            template_score_value = template_similarity(source_text, target_text)
            score = max(literal_score, vector_score, template_score_value)
            if score >= threshold:
                if template_score_value >= max(literal_score, vector_score):
                    match_source = "template"
                elif vector_score > literal_score:
                    match_source = "vector"
                else:
                    match_source = "text"
                source_matches.append(
                    build_match(
                        source,
                        target,
                        "history_bank",
                        score,
                        literal_score=literal_score,
                        template_score_value=template_score_value,
                        match_source=match_source,
                    )
                )

        source_matches.sort(key=lambda item: item.similarity_score, reverse=True)
        if top_k_per_question > 0:
            source_matches = source_matches[:top_k_per_question]
        matches.extend(source_matches)

    return sorted(matches, key=lambda item: item.similarity_score, reverse=True)


def _filter_history_questions(history_questions: list[Question], *, course_filter: str = "") -> list[Question]:
    if not course_filter:
        return history_questions
    normalized_course = course_filter.strip().lower()
    filtered = [
        question
        for question in history_questions
        if (question.course or "").strip().lower() == normalized_course
    ]
    if isinstance(history_questions, list) and hasattr(history_questions, "vector_index"):
        return type(history_questions)(filtered, vector_index=None)
    return filtered


def _text_similarity_score(left: str, right: str) -> float:
    left_text = normalize_for_compare(left)
    right_text = normalize_for_compare(right)
    if not left_text or not right_text:
        return 0.0
    return float(fuzz.ratio(left_text, right_text))


def lightweight_vector_similarity(left: str, right: str) -> float:
    left_text = normalize_for_compare(left)
    right_text = normalize_for_compare(right)
    if not left_text or not right_text:
        return 0.0
    unigram_score = _cosine_score(
        _char_ngram_vector(left_text, ngram_sizes=(1,)),
        _char_ngram_vector(right_text, ngram_sizes=(1,)),
    )
    mixed_ngram_score = _cosine_score(
        _char_ngram_vector(left_text, ngram_sizes=(1, 2, 3)),
        _char_ngram_vector(right_text, ngram_sizes=(1, 2, 3)),
    )
    return min(100.0, max(mixed_ngram_score, unigram_score * 1.15))


def _char_ngram_vector(text: str, *, ngram_sizes: tuple[int, ...]) -> Counter[str]:
    vector: Counter[str] = Counter()
    for ngram_size in ngram_sizes:
        if len(text) < ngram_size:
            continue
        for index in range(0, len(text) - ngram_size + 1):
            vector[text[index : index + ngram_size]] += 1
    return vector


def _cosine_score(left_vector: Counter[str], right_vector: Counter[str]) -> float:
    if not left_vector or not right_vector:
        return 0.0
    dot = sum(left_vector[key] * right_vector.get(key, 0) for key in left_vector)
    left_norm = sum(value * value for value in left_vector.values()) ** 0.5
    right_norm = sum(value * value for value in right_vector.values()) ** 0.5
    if left_norm == 0 or right_norm == 0:
        return 0.0
    return (dot / (left_norm * right_norm)) * 100


def _resolve_subject(uploaded_papers: list[UploadedPaper] | None, paper_id: str) -> str:
    for paper in uploaded_papers or []:
        if paper.paper_id == paper_id:
            return paper.subject
    return ""
