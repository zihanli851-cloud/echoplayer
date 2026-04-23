from app.models.schemas import Question, SpellcheckIssue, UploadedPaper
from app.services.spellcheck.base import SpellcheckProvider


class NuwaSpellcheckProvider(SpellcheckProvider):
    """
    Placeholder provider for future Nuwa integration.

    Recommended future integration steps:
    1. Build a request payload from UploadedPaper and Question objects.
    2. Call the Nuwa workflow endpoint with authentication headers.
    3. Map the returned issues into SpellcheckIssue objects.
    4. Fall back to the local provider when the external service is unavailable.

    The MVP keeps this provider as a no-op and does not call any external service.
    """

    provider_name = "nuwa_spellcheck_provider"
    provider_label = "Agent 版错字检查"
    is_placeholder = True
    provider_note = "当前未接通女娲，仅保留接口占位，不返回错字结果。"

    def __init__(self, workflow_id: str = "", api_base: str = "", api_key: str = "") -> None:
        self.workflow_id = workflow_id
        self.api_base = api_base
        self.api_key = api_key

    def build_payload(self, paper: UploadedPaper, questions: list[Question]) -> dict:
        """Build the future Nuwa request payload shape without sending it."""

        return {
            "workflow_id": self.workflow_id,
            "paper": paper.model_dump(),
            "questions": [question.model_dump() for question in questions],
        }

    def check_questions(
        self,
        paper: UploadedPaper,
        questions: list[Question],
    ) -> list[SpellcheckIssue]:
        """Return no issues for now because Nuwa is not wired in this MVP."""

        _ = self.build_payload(paper, questions)
        return []
