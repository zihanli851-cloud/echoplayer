"""
Coze 智能体 API 路由

通过 Coze Workflow API 执行试卷审查任务。
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field

from app.services.coze_service import CozeService, CozeServiceError


router = APIRouter(prefix="/api/coze", tags=["coze"])


class CozeQuestionItem(BaseModel):
    question_id: str = Field(..., min_length=1)
    question_no: str = Field(..., min_length=1)
    order: int
    content: str = Field(..., min_length=1)


class CozeQuestionsData(BaseModel):
    paper_id: str = Field(..., min_length=1)
    subject: str = Field(..., min_length=1)
    questions: list[CozeQuestionItem] = Field(default_factory=list)


class CozeWorkflowRequest(BaseModel):
    workflow_id: str | None = Field(default=None, description="可选，覆盖默认 workflowId")
    questions_data: CozeQuestionsData


@router.post("/execute")
def execute_coze_workflow(payload: CozeWorkflowRequest) -> dict[str, Any]:
    """Proxy a `questions_data` request to the Coze workflow API."""

    service = CozeService()
    try:
        return service.execute_spellcheck(
            payload.questions_data.model_dump(mode="json"),
        )
    except CozeServiceError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=str(exc),
        ) from exc


@router.post("/split")
def execute_coze_split(paper_content: str, paper_id: str = "unknown") -> dict[str, Any]:
    """Execute Coze paper splitting workflow."""

    service = CozeService()
    try:
        return service.execute_split(paper_content, paper_id=paper_id)
    except CozeServiceError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=str(exc),
        ) from exc


@router.get("/health")
def coze_health() -> dict[str, Any]:
    """Check Coze service configuration status."""
    service = CozeService()
    return {
        "status": "configured",
        "workflow_id": service.workflow_id,
        "api_url": service.api_url,
        "has_token": bool(service.bot_token),
    }
