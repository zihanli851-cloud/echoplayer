"""
Coze Workflow API 服务封装

用于调用 Coze 平台的 Workflow API 执行试卷审查任务。
API 文档: https://www.coze.cn/docs/api-reference
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

    Coze API 端点: POST https://api.coze.cn/v3/workflows/run
    认证方式: Bearer Token (Bot Token)
    """

    DEFAULT_API_URL = "https://api.coze.cn/v3/workflows/run"
    DEFAULT_WORKFLOW_ID = "7637135521890959375"  # 用户提供的 Coze Workflow ID

    def __init__(
        self,
        api_url: str | None = None,
        workflow_id: str | None = None,
        bot_token: str | None = None,
        timeout: float | None = None,
        is_async: bool = False,
    ) -> None:
        self.api_url = (api_url or os.getenv("COZE_API_URL", "")).strip() or self.DEFAULT_API_URL
        self.workflow_id = (workflow_id or os.getenv("COZE_WORKFLOW_ID", "")).strip() or self.DEFAULT_WORKFLOW_ID
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
            parameters: 工作流输入参数，格式为 dict，会被转换为 JSON 字符串
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

        # 使用请求参数作为缓存 key
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
            "is_async": self.is_async,
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

        # 检查 Coze API 返回的业务错误
        workflow_error = self._extract_workflow_error(result)
        if workflow_error is not None:
            raise CozeServiceError(workflow_error)

        self._cache[cache_key] = deepcopy(result)
        return result

    def execute_paper_review(
        self,
        paper_content: str,
        *,
        paper_id: str = "unknown",
        subject: str = "unknown",
    ) -> dict[str, Any]:
        """执行试卷审查工作流（切题、错字检查、比对）。

        Args:
            paper_content: 试卷文本内容
            paper_id: 试卷 ID
            subject: 科目

        Returns:
            工作流执行结果
        """
        parameters = {
            "paper_content": paper_content,
            "paper_id": paper_id,
            "subject": subject,
        }
        return self.execute_workflow(parameters)

    def execute_split(
        self,
        paper_content: str,
        *,
        paper_id: str = "unknown",
    ) -> dict[str, Any]:
        """执行题目切分工作流。"""
        parameters = {
            "action": "split",
            "paper_content": paper_content,
            "paper_id": paper_id,
        }
        return self.execute_workflow(parameters)

    def execute_spellcheck(
        self,
        questions_data: dict[str, Any],
    ) -> dict[str, Any]:
        """执行错别字检查工作流。"""
        parameters = {
            "action": "spellcheck",
            "questions_data": questions_data,
        }
        return self.execute_workflow(parameters)

    def execute_compare(
        self,
        questions_data: dict[str, Any],
    ) -> dict[str, Any]:
        """执行相似度比对工作流。"""
        parameters = {
            "action": "compare",
            "questions_data": questions_data,
        }
        return self.execute_workflow(parameters)

    def _resolve_timeout(self, timeout: float | None) -> float:
        if timeout is not None:
            return timeout

        raw_timeout = os.getenv("COZE_TIMEOUT", "60").strip()
        try:
            return float(raw_timeout)
        except ValueError:
            return 60.0

    def _extract_error_detail(self, response: requests.Response) -> str:
        try:
            body = response.json()
        except ValueError:
            return response.text.strip() or "无响应体"

        if isinstance(body, dict):
            return str(body.get("msg") or body.get("message") or body.get("detail") or body)
        return str(body)

    def _extract_workflow_error(self, result: Any) -> str | None:
        """检查 Coze API 返回的业务错误码。

        Coze API 常见错误码:
        - 0: 成功
        - 1001: 参数错误
        - 1002: 认证失败
        - 1003: 权限不足
        - 1004: 资源不存在
        - 2001: 工作流不存在
        - 2002: 工作流执行失败
        - 9999: 系统内部错误
        """
        if not isinstance(result, dict):
            return None

        code = result.get("code")
        msg = str(result.get("msg") or result.get("message") or "").strip()

        # Coze 返回 code=0 表示成功
        if code == 0:
            return None

        # 提取工作流内部错误
        data = result.get("data")
        if isinstance(data, dict):
            # 检查 data 中是否有 error 字段
            error = data.get("error") or data.get("Error")
            if error:
                return f"Coze 工作流执行错误：{error}"

        if code is not None:
            return f"Coze API 返回错误 code={code}：{msg or '未知错误'}"

        return None
