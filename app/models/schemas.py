from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class UploadedPaper(BaseModel):
    """Represents one uploaded paper after it has been stored temporarily."""

    paper_id: str
    filename: str
    subject: str
    temp_path: str
    text_content: str = ""
    page_count: int = 0

    model_config = ConfigDict(extra="ignore")


class Question(BaseModel):
    """Represents one split question unit from a paper."""

    question_id: str
    paper_id: str
    paper_label: str | None = None
    question_no: str
    order: int
    content: str
    raw_block: str = ""

    model_config = ConfigDict(extra="ignore")


class SimilarityMatch(BaseModel):
    """Represents one similarity comparison result between two questions."""

    match_id: str
    comparison_type: str
    source_paper_id: str
    source_paper_label: str | None = None
    source_question_id: str
    source_question_no: str
    source_text: str
    target_paper_id: str
    target_paper_label: str | None = None
    target_question_id: str
    target_question_no: str
    target_text: str
    similarity_score: float
    level: str
    review_status: str = "待确认"

    model_config = ConfigDict(extra="ignore")


class SpellcheckIssue(BaseModel):
    """Represents one spellcheck or punctuation issue found in a question."""

    issue_id: str
    paper_id: str
    question_id: str
    question_no: str
    issue_type: str
    original_text: str
    issue_text: str
    suggestion: str
    start_index: int | None = None
    end_index: int | None = None
    confidence: float | None = None

    model_config = ConfigDict(extra="ignore")


class ReportData(BaseModel):
    """Represents the report payload rendered by the report page."""

    teacher_name: str
    teacher_id: str
    subject: str
    generated_at: datetime = Field(default_factory=datetime.now)
    uploaded_papers: list[UploadedPaper] = Field(default_factory=list)
    questions: list[Question] = Field(default_factory=list)
    similarity_matches: list[SimilarityMatch] = Field(default_factory=list)
    spellcheck_issues: list[SpellcheckIssue] = Field(default_factory=list)
    dashboard: dict[str, int | float] = Field(default_factory=dict)
    review_status_options: list[str] = Field(
        default_factory=lambda: ["待确认", "确认重复", "排除误报"]
    )

    model_config = ConfigDict(extra="ignore")


class DualRunSectionComparison(BaseModel):
    """Represents one module-level comparison between code and Agent results."""

    module_name: str
    code_result: dict[str, Any] | list[dict[str, Any]] | None = None
    agent_result: dict[str, Any] | list[dict[str, Any]] | None = None
    status: str
    diff_summary: str

    model_config = ConfigDict(extra="ignore")
