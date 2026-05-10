from __future__ import annotations

import re
import unicodedata

try:
    from rapidfuzz import fuzz
except ImportError:  # pragma: no cover
    from difflib import SequenceMatcher

    class _FallbackFuzz:
        @staticmethod
        def ratio(left: str, right: str) -> float:
            return SequenceMatcher(None, left, right).ratio() * 100

    fuzz = _FallbackFuzz()


IMAGE_PLACEHOLDER_PATTERN = re.compile(r"\[IMAGE[^\]]*\]", re.IGNORECASE)
OCR_TEXT_MARKER_PATTERN = re.compile(r"\[OCR_TEXT\]", re.IGNORECASE)
YEAR_PATTERN = re.compile(r"(19|20)\d{2}")
NUMBER_PATTERN = re.compile(r"\d+(?:\.\d+)?")
SCORE_PATTERN = re.compile(r"\d+(?:\.\d+)?\s*分")
UNIT_PATTERN = re.compile(
    r"\d+(?:\.\d+)?\s*(?:km/h|m/s|kg|g|mg|cm|mm|m|km|s|min|h|小时|分钟|秒|元|万元|%|℃)"
)
PARAMETER_PATTERN = re.compile(r"\b[a-zA-Z]\s*=\s*-?\d+(?:\.\d+)?\b")
WHITESPACE_PATTERN = re.compile(r"\s+")


def normalize_template_text(text: str) -> str:
    """Normalize question text for same-source detection."""

    normalized = unicodedata.normalize("NFKC", text).lower()
    normalized = IMAGE_PLACEHOLDER_PATTERN.sub(" ", normalized)
    normalized = OCR_TEXT_MARKER_PATTERN.sub(" ", normalized)
    normalized = YEAR_PATTERN.sub("<YEAR>", normalized)
    normalized = SCORE_PATTERN.sub("<SCORE>", normalized)
    normalized = UNIT_PATTERN.sub("<UNIT>", normalized)
    normalized = PARAMETER_PATTERN.sub("<PARAM>", normalized)
    normalized = NUMBER_PATTERN.sub("<NUM>", normalized)
    normalized = WHITESPACE_PATTERN.sub("", normalized)
    return normalized.strip()


def template_similarity(left: str, right: str) -> float:
    left_text = normalize_template_text(left)
    right_text = normalize_template_text(right)
    if not left_text or not right_text:
        return 0.0
    return float(fuzz.ratio(left_text, right_text))
