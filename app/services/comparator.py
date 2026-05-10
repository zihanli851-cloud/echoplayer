from __future__ import annotations

from abc import ABC, abstractmethod
from collections import Counter
from difflib import SequenceMatcher
from itertools import combinations
import json
import re
import unicodedata
from typing import Any

from app.models.schemas import Question, SimilarityMatch, UploadedPaper
from app.services.coze_service import CozeService, CozeServiceError
from app.services.nuwa_service import NuwaService, NuwaServiceError
from app.services.question_normalizer import template_similarity
from app.services.spellcheck.nuwa_provider import build_questions_data


IMAGE_PLACEHOLDER_PATTERN = re.compile(r"\[IMAGE[^\]]*\]", re.IGNORECASE)
OCR_TEXT_MARKER_PATTERN = re.compile(r"\[OCR_TEXT\]", re.IGNORECASE)

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
        """Run internal, cross-paper, and history-bank comparison."""


class CodeSimilarityComparator(SimilarityComparatorProvider):
    """Code pipeline comparator based on text and template normalization."""

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


class AgentSimilarityComparator(SimilarityComparatorProvider):
    """Agent-side comparator that uses Coze/Nuwa history matches and local fallback."""

    provider_name = "agent_similarity_comparator"
    provider_label = "Agent 查重比对"

    def __init__(
        self,
        *,
        coze_service: CozeService | None = None,
        nuwa_service: NuwaService | None = None,
        fallback_provider: SimilarityComparatorProvider | None = None,
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
        self.fallback_provider = fallback_provider or CodeSimilarityComparator()

    def compare(
        self,
        paper_a_questions: list[Question],
        paper_b_questions: list[Question] | None = None,
        history_questions: list[Question] | None = None,
        *,
        uploaded_papers: list[UploadedPaper] | None = None,
    ) -> list[SimilarityMatch]:
        local_matches = self.fallback_provider.compare(
            paper_a_questions,
            paper_b_questions,
            history_questions,
            uploaded_papers=uploaded_papers,
        )
        if self._service_type == "coze" and not paper_a_questions and not paper_b_questions:
            self.is_placeholder = True
            self.provider_note = "Coze 查重比对跳过：Agent 切题未返回可比对题目。"
            return []

        local_non_history = [match for match in local_matches if match.comparison_type != "history_bank"]
        local_history_by_paper: dict[str, list[SimilarityMatch]] = {"A": [], "B": []}
        for match in local_matches:
            if match.comparison_type == "history_bank":
                local_history_by_paper.setdefault(match.source_paper_id, []).append(match)

        paper_by_id = {paper.paper_id: paper for paper in uploaded_papers or []}
        history_matches: list[SimilarityMatch] = []
        note_parts: list[str] = []
        service_name = "Coze" if self._service_type == "coze" else "Nuwa"

        for paper_id, questions in (("A", paper_a_questions), ("B", paper_b_questions or [])):
            if not questions:
                continue
            paper = paper_by_id.get(paper_id)
            if paper is None:
                history_matches.extend(local_history_by_paper.get(paper_id, []))
                note_parts.append(f"{paper_id} 卷缺少试卷元数据，历史题库对比已回退本地规则。")
                continue

            questions_data = build_questions_data(paper, questions)
            try:
                if self._service_type == "coze":
                    response = self._service.execute_compare(questions_data)
                else:
                    response = self._service.execute_compare_workflow(questions_data)
            except (CozeServiceError, NuwaServiceError) as exc:
                if self._service_type == "coze":
                    self.is_placeholder = True
                    self.provider_note = f"{paper_id} 卷 Coze 智能对比调用失败：{exc}"
                    return []
                history_matches.extend(local_history_by_paper.get(paper_id, []))
                note_parts.append(f"{paper_id} 卷 {service_name} 智能对比调用失败，历史题库结果已回退本地规则：{exc}")
                continue

            plagiarism_details = _find_first_list(response, "plagiarism_details")
            if plagiarism_details is None:
                if self._service_type == "coze":
                    self.is_placeholder = True
                    self.provider_note = f"{paper_id} 卷 Coze 未返回 plagiarism_details。"
                    return []
                history_matches.extend(local_history_by_paper.get(paper_id, []))
                note_parts.append(f"{paper_id} 卷 {service_name} 未返回 plagiarism_details，历史题库结果已回退本地规则。")
                continue

            target_label = "Coze 知识库" if self._service_type == "coze" else "Nuwa 知识库"
            parsed_matches = _parse_plagiarism_details(
                plagiarism_details,
                questions,
                paper_id,
                target_label=target_label,
                match_source=self._service_type,
            )
            history_matches.extend(parsed_matches)
            note_parts.append(f"{paper_id} 卷历史题库智能对比已接入 {service_name} 工作流。")

        note_parts.append("卷内与 A/B 交叉查重仍使用本地规则计算。")
        self.provider_note = "".join(note_parts)
        self.is_placeholder = False

        return sorted([*local_non_history, *history_matches], key=lambda item: item.similarity_score, reverse=True)


class SkippedAgentSimilarityComparator(SimilarityComparatorProvider):
    """Agent comparator placeholder used when the compare workflow is disabled."""

    provider_name = "skipped_agent_similarity_comparator"
    provider_label = "Agent 查重比对"
    is_placeholder = True
    provider_note = "Agent 查重工作流暂未启用；当前仅运行 Agent 切题。"

    def compare(
        self,
        paper_a_questions: list[Question],
        paper_b_questions: list[Question] | None = None,
        history_questions: list[Question] | None = None,
        *,
        uploaded_papers: list[UploadedPaper] | None = None,
    ) -> list[SimilarityMatch]:
        return []


def normalize_for_compare(text: str) -> str:
    normalized = IMAGE_PLACEHOLDER_PATTERN.sub(" ", text)
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
        literal_score = _text_similarity_score(source.content, target.content)
        template_score_value = template_similarity(source.content, target.content)
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
            literal_score = _text_similarity_score(source.content, target.content)
            template_score_value = template_similarity(source.content, target.content)
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
                literal_score = _text_similarity_score(source.content, hit.question.content)
                template_score_value = template_similarity(source.content, hit.question.content)
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
            literal_score = _text_similarity_score(source.content, target.content)
            vector_score = (
                lightweight_vector_similarity(source.content, target.content)
                if use_lightweight_vector
                else 0.0
            )
            template_score_value = template_similarity(source.content, target.content)
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


def _find_first_list(value: Any, target_key: str):
    if isinstance(value, str):
        parsed = _parse_json_value(value)
        if parsed is not None:
            return _find_first_list(parsed, target_key)
        return None

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


def _parse_json_value(value: str):
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


def _parse_plagiarism_details(
    plagiarism_details: list,
    questions: list[Question],
    paper_id: str,
    *,
    target_label: str = "历史知识库",
    match_source: str = "agent",
) -> list[SimilarityMatch]:
    question_by_no = {question.question_no: question for question in questions}
    matches: list[SimilarityMatch] = []

    for index, item in enumerate(plagiarism_details, start=1):
        if isinstance(item, str):
            parsed_item = _parse_json_value(item)
            item = parsed_item if isinstance(parsed_item, dict) else item
        if not isinstance(item, dict):
            continue

        question_number = str(item.get("question_number", "")).strip()
        source_question = question_by_no.get(question_number)
        if source_question is None:
            if len(questions) == 1:
                source_question = questions[0]
            else:
                continue

        matched_historical_question = str(item.get("matched_historical_question", "")).strip()
        if not matched_historical_question:
            matched_historical_question = str(item.get("diff_highlight", "")).strip()
        if not matched_historical_question:
            continue

        score = _parse_similarity_level(item.get("similarity_level"))
        template_score_value = template_similarity(source_question.content, matched_historical_question)
        matches.append(
            SimilarityMatch(
                match_id=f"history_bank-{source_question.question_id}-{match_source}-{index}",
                comparison_type="history_bank",
                source_paper_id=paper_id,
                source_paper_label=paper_id,
                source_question_id=source_question.question_id,
                source_question_no=source_question.question_no,
                source_text=source_question.content,
                target_paper_id="H",
                target_paper_label=target_label,
                target_question_id=f"H-{paper_id}-{index}",
                target_question_no=str(index),
                target_text=matched_historical_question,
                similarity_score=round(score, 2),
                literal_score=round(score, 2),
                template_score=round(template_score_value, 2),
                final_score=round(max(score, template_score_value), 2),
                is_same_source_question=template_score_value >= 92 and score < 95,
                level=classify_similarity(max(score, template_score_value), template_score_value=template_score_value),
            )
        )

    return sorted(matches, key=lambda current: current.similarity_score, reverse=True)


def _parse_similarity_level(value: Any) -> float:
    text = str(value or "").strip()
    if not text:
        return 85.0

    exact_match = re.fullmatch(r"(\d+(?:\.\d+)?)%", text)
    if exact_match:
        return float(exact_match.group(1))

    range_match = re.fullmatch(r"(\d+(?:\.\d+)?)%\s*-\s*(\d+(?:\.\d+)?)%", text)
    if range_match:
        low = float(range_match.group(1))
        high = float(range_match.group(2))
        return (low + high) / 2

    values = [float(match) for match in re.findall(r"\d+(?:\.\d+)?", text)]
    if len(values) >= 2:
        return (values[0] + values[1]) / 2
    if len(values) == 1:
        return values[0]
    return 85.0
