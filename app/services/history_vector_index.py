from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import re
import unicodedata

from app.models.schemas import Question


IMAGE_PLACEHOLDER_PATTERN = re.compile(r"\[IMAGE[^\]]*\]", re.IGNORECASE)
OCR_TEXT_MARKER_PATTERN = re.compile(r"\[OCR_TEXT\]", re.IGNORECASE)
INDEX_VERSION = 1


@dataclass(slots=True)
class HistoryIndexHit:
    question: Question
    score: float
    match_source: str


class IndexedHistoryQuestions(list):
    """List of history questions carrying an optional vector index."""

    def __init__(self, questions: list[Question], vector_index: "HistoryVectorIndex | None" = None) -> None:
        super().__init__(questions)
        self.vector_index = vector_index


class HistoryVectorIndex:
    """Persistent lightweight vector index for history-bank questions."""

    def __init__(self, records: list[dict], *, signature: str) -> None:
        self.records = records
        self.signature = signature

    @classmethod
    def build(cls, questions: list[Question]) -> "HistoryVectorIndex":
        records = []
        for question in questions:
            normalized_text = normalize_for_index(question.content)
            records.append(
                {
                    "question": question.model_dump(mode="json"),
                    "normalized_text": normalized_text,
                    "vector": dict(char_ngram_vector(normalized_text, ngram_sizes=(1, 2, 3))),
                    "unigram_vector": dict(char_ngram_vector(normalized_text, ngram_sizes=(1,))),
                }
            )
        return cls(records, signature=build_questions_signature(questions))

    @classmethod
    def load(cls, index_path: Path, *, expected_signature: str) -> "HistoryVectorIndex | None":
        if not index_path.exists():
            return None
        try:
            payload = json.loads(index_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        if payload.get("version") != INDEX_VERSION:
            return None
        if payload.get("signature") != expected_signature:
            return None
        records = payload.get("records")
        if not isinstance(records, list):
            return None
        return cls(records, signature=expected_signature)

    def save(self, index_path: Path) -> None:
        index_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": INDEX_VERSION,
            "signature": self.signature,
            "record_count": len(self.records),
            "records": self.records,
        }
        index_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    def search(self, source: Question, *, threshold: float, top_k: int) -> list[HistoryIndexHit]:
        source_text = normalize_for_index(source.content)
        if not source_text:
            return []
        source_vector = char_ngram_vector(source_text, ngram_sizes=(1, 2, 3))
        source_unigram_vector = char_ngram_vector(source_text, ngram_sizes=(1,))
        hits: list[HistoryIndexHit] = []

        for record in self.records:
            target_text = str(record.get("normalized_text", ""))
            text_score = text_similarity_score(source_text, target_text)
            vector_score = lightweight_vector_score(
                source_vector,
                Counter(record.get("vector", {})),
                source_unigram_vector,
                Counter(record.get("unigram_vector", {})),
            )
            score = max(text_score, vector_score)
            if score < threshold:
                continue
            question_payload = record.get("question", {})
            if not isinstance(question_payload, dict):
                continue
            hits.append(
                HistoryIndexHit(
                    question=Question.model_validate(question_payload),
                    score=score,
                    match_source="vector_index" if vector_score > text_score else "text_index",
                )
            )

        hits.sort(key=lambda hit: hit.score, reverse=True)
        return hits[:top_k] if top_k > 0 else hits


def build_or_load_history_vector_index(
    questions: list[Question],
    *,
    index_path: Path,
) -> HistoryVectorIndex:
    signature = build_questions_signature(questions)
    cached = HistoryVectorIndex.load(index_path, expected_signature=signature)
    if cached is not None:
        return cached
    index = HistoryVectorIndex.build(questions)
    index.save(index_path)
    return index


def build_questions_signature(questions: list[Question]) -> str:
    digest = hashlib.sha256()
    for question in questions:
        digest.update(question.paper_id.encode("utf-8", errors="ignore"))
        digest.update(b"\0")
        digest.update(question.question_id.encode("utf-8", errors="ignore"))
        digest.update(b"\0")
        digest.update(str(question.paper_label or "").encode("utf-8", errors="ignore"))
        digest.update(b"\0")
        digest.update(question.question_no.encode("utf-8", errors="ignore"))
        digest.update(b"\0")
        digest.update(question.content.encode("utf-8", errors="ignore"))
        digest.update(b"\0")
    return digest.hexdigest()


def normalize_for_index(text: str) -> str:
    normalized = IMAGE_PLACEHOLDER_PATTERN.sub(" ", text)
    normalized = OCR_TEXT_MARKER_PATTERN.sub(" ", normalized)
    normalized = unicodedata.normalize("NFKC", normalized).lower()
    normalized = re.sub(r"[（(]\s*\d+\s*分\s*[)）]", " ", normalized)
    normalized = re.sub(r"\s+", "", normalized)
    return normalized.strip()


def text_similarity_score(left: str, right: str) -> float:
    if not left or not right:
        return 0.0
    return _sequence_ratio(left, right)


def char_ngram_vector(text: str, *, ngram_sizes: tuple[int, ...]) -> Counter[str]:
    vector: Counter[str] = Counter()
    if not text:
        return vector
    for ngram_size in ngram_sizes:
        if len(text) < ngram_size:
            continue
        for index in range(0, len(text) - ngram_size + 1):
            vector[text[index : index + ngram_size]] += 1
    return vector


def lightweight_vector_score(
    left_vector: Counter[str],
    right_vector: Counter[str],
    left_unigram_vector: Counter[str],
    right_unigram_vector: Counter[str],
) -> float:
    mixed_ngram_score = cosine_score(left_vector, right_vector)
    unigram_score = cosine_score(left_unigram_vector, right_unigram_vector)
    return min(100.0, max(mixed_ngram_score, unigram_score * 1.15))


def cosine_score(left_vector: Counter[str], right_vector: Counter[str]) -> float:
    if not left_vector or not right_vector:
        return 0.0
    dot = sum(left_vector[key] * right_vector.get(key, 0) for key in left_vector)
    left_norm = sum(value * value for value in left_vector.values()) ** 0.5
    right_norm = sum(value * value for value in right_vector.values()) ** 0.5
    if left_norm == 0 or right_norm == 0:
        return 0.0
    return (dot / (left_norm * right_norm)) * 100


def _sequence_ratio(left: str, right: str) -> float:
    try:
        from rapidfuzz import fuzz

        return float(fuzz.ratio(left, right))
    except ImportError:
        from difflib import SequenceMatcher

        return SequenceMatcher(None, left, right).ratio() * 100
