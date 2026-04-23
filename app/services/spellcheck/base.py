from abc import ABC, abstractmethod

from app.models.schemas import Question, SpellcheckIssue, UploadedPaper


class SpellcheckProvider(ABC):
    """Unified spellcheck provider interface used by the MVP."""

    provider_name = "unknown"
    provider_label = "未命名错字检查器"
    is_placeholder = False
    provider_note = ""

    @abstractmethod
    def check_questions(
        self,
        paper: UploadedPaper,
        questions: list[Question],
    ) -> list[SpellcheckIssue]:
        """Run spellcheck for a paper and return normalized issue objects."""
