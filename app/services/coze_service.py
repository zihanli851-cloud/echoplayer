"""
Coze Workflow API 服务封装

用于调用 Coze 平台的 Workflow API 执行试卷审查任务。
API 文档: https://www.coze.cn/docs/developer_guides/workflow_run

重要: Coze API 端点是 /v1/workflow/run (不是 /v3/workflows/run)
"""

from __future__ import annotations

from copy import deepcopy
import json
import os
from typing import Any

import requests
from dotenv import load_dotenv


load_dotenv()


class CozeServiceError(Exception):
    """Raised when the Coze workflow request cannot be completed."""


class CozeService:
    """Service wrapper for calling Coze Workflow API.

    Coze API 端点: POST https://api.coze.cn/v1/workflow/run
    认证方式: Bearer Token (Personal Access Token)

    支持多个工作流:
    - 切题工作流 (split)
    - 错字检查工作流 (spellcheck)
    - 比对工作流 (compare)
    - 综合审查工作流 (默认)
    """

    # 正确的 API 端点 (v1 不是 v3)
    DEFAULT_API_URL = "https://api.coze.cn/v1/workflow/run"

    # 默认工作流 ID
    DEFAULT_WORKFLOW_ID = "7637135521890959375"       # 综合审查工作流
    DEFAULT_SPLIT_WORKFLOW_ID = "7637166446480506899"  # 切题工作流

    def __init__(
        self,
        api_url: str | None = None,
        workflow_id: str | None = None,
        split_workflow_id: str | None = None,
        spellcheck_workflow_id: str | None = None,
        compare_workflow_id: str | None = None,
        bot_token: str | None = None,
        timeout: float | None = None,
        is_async: bool = False,
    ) -> None:
        self.api_url = (api_url or os.getenv("COZE_API_URL", "")).strip() or self.DEFAULT_API_URL
        self.workflow_id = (workflow_id or os.getenv("COZE_WORKFLOW_ID", "")).strip() or self.DEFAULT_WORKFLOW_ID
        self.split_workflow_id = (split_workflow_id or os.getenv("COZE_SPLIT_WORKFLOW_ID", "")).strip() or self.DEFAULT_SPLIT_WORKFLOW_ID
        self.spellcheck_workflow_id = (spellcheck_workflow_id or os.getenv("COZE_SPELLCHECK_WORKFLOW_ID", "")).strip() or self.workflow_id
        self.compare_workflow_id = (compare_workflow_id or os.getenv("COZE_COMPARE_WORKFLOW_ID", "")).strip() or self.workflow_id
        self.bot_token = (bot_token or os.getenv("COZE_BOT_TOKEN", "")).strip()
        self.timeout = self._resolve_timeout(timeout)
        self.is_async = is_async
        self._cache: dict[str, dict[str, Any]] = {}

    def execute_workflow(
        self,
        parameters: dict[str, Any],
        *,
        workflow_id: str | None = None,
    ) -> dict[str, Any]:
        """Execute a Coze workflow with the supplied parameters.

        Args:
            parameters: 工作流输入参数，格式为 dict
            workflow_id: 可选，覆盖默认 workflow_id

        Returns:
            工作流执行结果，通常包含 code, msg, data 字段
        """
        if not parameters:
            raise CozeServiceError("工作流参数不能为空。")

        resolved_workflow_id = (workflow_id or self.workflow_id).strip()
        if not resolved_workflow_id:
            raise CozeServiceError("未配置 Coze workflow ID。")
        if not self.bot_token:
            raise CozeServiceError("未配置 COZE_BOT_TOKEN。")

        cache_key = json.dumps(parameters, ensure_ascii=False, sort_keys=True)
        if cache_key in self._cache:
            return deepcopy(self._cache[cache_key])

        headers = {
            "Authorization": f"Bearer {self.bot_token}",
            "Content-Type": "application/json",
        }

        payload = {
            "workflow_id": resolved_workflow_id,
            "parameters": parameters,
        }

        try:
            response = requests.post(
                self.api_url,
                json=payload,
                headers=headers,
                timeout=self.timeout,
            )
        except requests.RequestException as exc:
            raise CozeServiceError(f"调用 Coze 工作流失败：{exc}") from exc

        if not response.ok:
            error_detail = self._extract_error_detail(response)
            raise CozeServiceError(
                f"Coze 工作流返回异常状态码 {response.status_code}：{error_detail}"
            )

        try:
            result = response.json()
        except ValueError as exc:
            raise CozeServiceError("Coze 工作流返回的不是有效 JSON。") from exc

        workflow_error = self._extract_workflow_error(result)
        if workflow_error is not None:
            raise CozeServiceError(workflow_error)

        self._cache[cache_key] = deepcopy(result)
        return result

    def execute_split(
        self,
        paper_content: str,
        *,
        paper_id: str = "unknown",
    ) -> dict[str, Any]:
        """执行切题工作流。"""
        parameters = {
            "paper_text_data": {"content": paper_content, "paper_id": paper_id},
        }
        return self.execute_workflow(parameters, workflow_id=self.split_workflow_id)

    def execute_spellcheck(
        self,
        questions_data: dict[str, Any],
    ) -> dict[str, Any]:
        """执行错别字检查工作流。"""
        parameters = {
            "question_data": questions_data,
        }
        return self.execute_workflow(parameters, workflow_id=self.spellcheck_workflow_id)

    def execute_compare(
        self,
        questions_data: dict[str, Any],
        reference_data: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """执行相似度比对工作流。"""
        parameters = {
            "question_data": questions_data,
        }
        if reference_data:
            parameters["reference_data"] = reference_data
        return self.execute_workflow(parameters, workflow_id=self.compare_workflow_id)

    def execute_paper_review(
        self,
        paper_a_content: str,
        paper_b_content: str | None = None,
        paper_a_id: str = "paper_a",
        paper_b_id: str = "paper_b",
        history_bank_path: str | None = None,
    ) -> dict[str, Any]:
        """执行综合审查工作流（切题+错字+比对一体化）。"""
        parameters = {
            "question_data": {
                "paper_a_content": paper_a_content,
                "paper_a_id": paper_a_id,
            },
        }
        if paper_b_content:
            parameters["question_data"]["paper_b_content"] = paper_b_content
            parameters["question_data"]["paper_b_id"] = paper_b_id
        if history_bank_path:
            parameters["question_data"]["history_bank_path"] = history_bank_path
        return self.execute_workflow(parameters)

    def _extract_error_detail(self, response: requests.Response) -> str:
        """Extract user-friendly error message from response."""
        try:
            data = response.json()
            return data.get("msg", response.text)
        except Exception:
            return response.text

    def _extract_workflow_error(self, result: dict[str, Any]) -> str | None:
        """Check if the workflow execution returned an error."""
        code = result.get("code")
        if code is not None and code != 0:
            msg = result.get("msg", "Unknown error")
            return f"Coze 工作流执行失败 (code={code}): {msg}"
        return None

    def _resolve_timeout(self, timeout: float | None) -> float:
        """Resolve timeout from parameter or environment."""
        if timeout is not None:
            return timeout
        env_timeout = os.getenv("COZE_TIMEOUT")
        if env_timeout:
            try:
                return float(env_timeout)
            except ValueError:
                pass
        return 60.0

    @property
    def available_workflows(self) -> dict[str, str]:
        """Return dict of available workflow IDs."""
        return {
            "default": self.workflow_id,
            "split": self.split_workflow_id,
            "spellcheck": self.spellcheck_workflow_id,
            "compare": self.compare_workflow_id,
        }
