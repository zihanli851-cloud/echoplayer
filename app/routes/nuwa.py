from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field

from app.services.nuwa_service import NuwaService, NuwaServiceError


router = APIRouter(prefix="/api/nuwa", tags=["nuwa"])


class NuwaQuestionItem(BaseModel):
    question_id: str = Field(..., min_length=1)
    question_no: str = Field(..., min_length=1)
    order: int
    content: str = Field(..., min_length=1)


class NuwaQuestionsData(BaseModel):
    paper_id: str = Field(..., min_length=1)
    subject: str = Field(..., min_length=1)
    questions: list[NuwaQuestionItem] = Field(default_factory=list)


class NuwaWorkflowRequest(BaseModel):
    workflow_id: str | None = Field(default=None, description="可选，覆盖默认 workflowId")
    questions_data: NuwaQuestionsData


@router.post("/execute")
def execute_nuwa_workflow(payload: NuwaWorkflowRequest) -> dict[str, Any]:
    """Proxy a `questions_data` request to the Nuwa workflow API."""

    service = NuwaService()
    try:
        return service.execute_questions_workflow(
            payload.questions_data.model_dump(mode="json"),
            workflow_id=payload.workflow_id,
        )
    except NuwaServiceError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=str(exc),
        ) from exc
