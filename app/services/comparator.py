from itertools import combinations
from difflib import SequenceMatcher
from abc import ABC, abstractmethod
import json
import re
import unicodedata
from typing import Any

from app.models.schemas import Question, SimilarityMatch, UploadedPaper
from app.services.nuwa_service import NuwaService, NuwaServiceError
from app.services.coze_service import CozeService, CozeServiceError
from app.services.spellcheck.nuwa_provider import build_questions_data

try:
    from rapidfuzz import fuzz
except ImportError:
    class _FallbackFuzz:
        """Fallback ratio calculator used only when rapidfuzz is not installed."""

        @staticmethod
        def ratio(left: str, right: str) -> float:
            return SequenceMatcher(None, left, right).ratio() * 100

    fuzz = _FallbackFuzz()


class SimilarityComparatorProvider(ABC):
    """Provider interface for duplicate detection and cross-paper comparison."""

    provider_name = "unknown"
    provider_label = "未命名比对器"
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
    """Code pipeline comparator based on rapidfuzz / fallback difflib."""

    provider_name = "code_similarity_comparator"
    provider_label = "代码版查重比对"
    is_placeholder = False

    def __init__(
        self,
        *,
        threshold: float = 85,
        history_top_k: int = 3,
    ) -> None:
        self.threshold = threshold
        self.history_top_k = history_top_k

    def compare(
        self,
        paper_a_questions: list[Question],
        paper_b_questions: list[Question] | None = None,
        history_questions: list[Question] | None = None,
        *,
        uploaded_papers: list[UploadedPaper] | None = None,
    ) -> list[SimilarityMatch]:
        matches: list[SimilarityMatch] = []
        matches.extend(
            compare_within_paper(
                paper_a_questions,
                "within_paper_a",
                threshold=self.threshold,
            )
        )
        if paper_b_questions:
            matches.extend(
                compare_within_paper(
                    paper_b_questions,
                    "within_paper_b",
                    threshold=self.threshold,
                )
            )
            matches.extend(
                compare_cross_papers(
                    paper_a_questions,
                    paper_b_questions,
                    threshold=self.threshold,
                )
            )
        if history_questions:
            matches.extend(
                compare_against_history_bank(
                    paper_a_questions,
                    history_questions,
                    threshold=self.threshold,
                    top_k_per_question=self.history_top_k,
                )
            )
            if paper_b_questions:
                matches.extend(
                    compare_against_history_bank(
                        paper_b_questions,
                        history_questions,
                        threshold=self.threshold,
                        top_k_per_question=self.history_top_k,
                    )
                )
        return sorted(matches, key=lambda item: item.similarity_score, reverse=True)


class AgentSimilarityComparator(SimilarityComparatorProvider):
    """Agent-side comparator that uses Coze/Nuwa history matches and local fallback."""

    provider_name = "agent_similarity_comparator"
    provider_label = "Coze 智能体查重比对"
    is_placeholder = False
    provider_note = ""

    def __init__(
        self,
        *,
        coze_service: CozeService | None = None,
        nuwa_service: NuwaService | None = None,
        fallback_provider: SimilarityComparatorProvider | None = None,
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
        self.fallback_provider = fallback_provider or CodeSimilarityComparator()
        self.is_placeholder = False

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
        local_non_history = [
            match for match in local_matches if match.comparison_type != "history_bank"
        ]
        local_history_by_paper: dict[str, list[SimilarityMatch]] = {"A": [], "B": []}
        for match in local_matches:
            if match.comparison_type == "history_bank":
                local_history_by_paper.setdefault(match.source_paper_id, []).append(match)

        paper_by_id = {paper.paper_id: paper for paper in uploaded_papers or []}
        history_matches: list[SimilarityMatch] = []
        note_parts: list[str] = []
        service_name = "Coze" if self._service_type == "coze" else "女娲"

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

            target_label = "Coze 知识库" if self._service_type == "coze" else "女娲知识库"
            parsed_matches = _parse_plagiarism_details(
                plagiarism_details,
                questions,
                paper_id,
                target_label=target_label,
                match_source=self._service_type,
            )
            history_matches.extend(parsed_matches)
            note_parts.append(f"{paper_id} 卷历史题库智能对比已接入{service_name}工作流。")

        note_parts.append("卷内与 A/B 交叉查重仍使用本地规则计算。")
        self.provider_note = "".join(note_parts)
        self.is_placeholder = False

        return sorted(
            [*local_non_history, *history_matches],
            key=lambda item: item.similarity_score,
            reverse=True,
        )


class SkippedAgentSimilarityComparator(SimilarityComparatorProvider):
    """Agent comparator placeholder used when the Coze compare workflow is disabled."""

    provider_name = "skipped_agent_similarity_comparator"
    provider_label = "Coze 智能体查重比对"
    is_placeholder = True
    provider_note = "Coze 查重工作流暂未启用；当前仅运行 Agent 切题。"

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
    """Normalize text before fuzzy comparison so whitespace noise has less impact."""

    normalized = unicodedata.normalize("NFKC", text).lower()
    normalized = re.sub(r"[（(]\s*\d+\s*分\s*[)）]", " ", normalized)
    normalized = re.sub(r"\s+", "", normalized)
    return normalized.strip()


def classify_similarity(score: float) -> str:
    """Classify a similarity score into MVP report levels."""

    if score >= 95:
        return "高度重复"
    if score >= 85:
        return "疑似重复"
    return "差异较大"


def build_match(
    source: Question,
    target: Question,
    comparison_type: str,
    score: float,
) -> SimilarityMatch:
    """Build one standard SimilarityMatch object from two questions."""

    return SimilarityMatch(
        match_id=f"{comparison_type}-{source.question_id}-{target.question_id}",
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
        similarity_score=round(score, 2),
        level=classify_similarity(score),
    )


def compare_within_paper(
    questions: list[Question],
    comparison_type: str,
    threshold: float = 85,
) -> list[SimilarityMatch]:
    """
    Compare questions within the same paper.

    Only one direction is generated for each pair to avoid duplicate rows.
    """

    matches: list[SimilarityMatch] = []

    for source, target in combinations(questions, 2):
        score = fuzz.ratio(
            normalize_for_compare(source.content),
            normalize_for_compare(target.content),
        )
        if score >= threshold:
            matches.append(build_match(source, target, comparison_type, score))

    return sorted(matches, key=lambda item: item.similarity_score, reverse=True)


def compare_cross_papers(
    paper_a_questions: list[Question],
    paper_b_questions: list[Question],
    threshold: float = 85,
) -> list[SimilarityMatch]:
    """Compare every A-paper question with every B-paper question."""

    matches: list[SimilarityMatch] = []

    for source in paper_a_questions:
        for target in paper_b_questions:
            score = fuzz.ratio(
                normalize_for_compare(source.content),
                normalize_for_compare(target.content),
            )
            if score >= threshold:
                matches.append(build_match(source, target, "cross_paper", score))

    return sorted(matches, key=lambda item: item.similarity_score, reverse=True)


def compare_against_history_bank(
    source_questions: list[Question],
    history_questions: list[Question],
    *,
    threshold: float = 85,
    top_k_per_question: int = 3,
) -> list[SimilarityMatch]:
    """
    Compare uploaded questions with the local history bank.

    The MVP keeps only the top N matches per source question so the report page
    remains readable even when the history bank grows.
    """

    matches: list[SimilarityMatch] = []

    for source in source_questions:
        source_matches: list[SimilarityMatch] = []
        for target in history_questions:
            score = fuzz.ratio(
                normalize_for_compare(source.content),
                normalize_for_compare(target.content),
            )
            if score >= threshold:
                source_matches.append(build_match(source, target, "history_bank", score))

        source_matches.sort(key=lambda item: item.similarity_score, reverse=True)
        if top_k_per_question > 0:
            source_matches = source_matches[:top_k_per_question]
        matches.extend(source_matches)

    return sorted(matches, key=lambda item: item.similarity_score, reverse=True)


def _find_first_list(value, target_key: str):
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
                level=classify_similarity(score),
            )
        )

    return sorted(matches, key=lambda current: current.similarity_score, reverse=True)


def _parse_similarity_level(value) -> float:
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
