from __future__ import annotations

from copy import deepcopy
import json
import os
import re
from typing import Any

import requests
from dotenv import load_dotenv


load_dotenv()


class NuwaServiceError(Exception):
    """Raised when the Nuwa workflow request cannot be completed."""


class NuwaService:
    """Service wrapper for calling Nuwa workflow execution APIs."""

    def __init__(
        self,
        workflow_url: str | None = None,
        workflow_id: str | None = None,
        split_workflow_url: str | None = None,
        split_workflow_id: str | None = None,
        spellcheck_workflow_url: str | None = None,
        spellcheck_workflow_id: str | None = None,
        compare_workflow_url: str | None = None,
        compare_workflow_id: str | None = None,
        api_key: str | None = None,
        timeout: float | None = None,
    ) -> None:
        self.workflow_url = (workflow_url or os.getenv("NUWA_WORKFLOW_URL", "")).strip()
        self.workflow_id = (workflow_id or os.getenv("NUWA_WORKFLOW_ID", "")).strip()
        self.split_workflow_url = (
            split_workflow_url or os.getenv("NUWA_SPLIT_WORKFLOW_URL", self.workflow_url)
        ).strip()
        self.split_workflow_id = (
            split_workflow_id or os.getenv("NUWA_SPLIT_WORKFLOW_ID", self.workflow_id)
        ).strip()
        self.spellcheck_workflow_url = (
            spellcheck_workflow_url or os.getenv("NUWA_SPELLCHECK_WORKFLOW_URL", self.workflow_url)
        ).strip()
        self.spellcheck_workflow_id = (
            spellcheck_workflow_id or os.getenv("NUWA_SPELLCHECK_WORKFLOW_ID", self.workflow_id)
        ).strip()
        self.compare_workflow_url = (
            compare_workflow_url or os.getenv("NUWA_COMPARE_WORKFLOW_URL", self.workflow_url)
        ).strip()
        self.compare_workflow_id = (
            compare_workflow_id or os.getenv("NUWA_COMPARE_WORKFLOW_ID", self.workflow_id)
        ).strip()
        self.api_key = (api_key or os.getenv("NUWA_API_KEY", "")).strip()
        self.timeout = self._resolve_timeout(timeout)
        self._cache: dict[tuple[str, str], dict[str, Any]] = {}

    def execute_workflow(
        self,
        inputs: dict[str, Any],
        *,
        workflow_url: str | None = None,
        workflow_id: str | None = None,
    ) -> dict[str, Any]:
        """Execute a Nuwa workflow with the supplied inputs and return JSON."""

        if not inputs:
            raise NuwaServiceError("工作流 inputs 不能为空。")

        resolved_workflow_url = self._resolve_execute_url(workflow_url, workflow_id)
        resolved_workflow_id = (workflow_id or self.workflow_id).strip()
        if not resolved_workflow_url:
            raise NuwaServiceError("未配置女娲工作流地址。")
        if not resolved_workflow_id:
            raise NuwaServiceError("未配置女娲 workflowId。")
        if not self.api_key:
            raise NuwaServiceError("未配置 NUWA_API_KEY。")

        payload = deepcopy(inputs)
        cache_key = (resolved_workflow_url, json.dumps(payload, ensure_ascii=False, sort_keys=True))
        if cache_key in self._cache:
            return deepcopy(self._cache[cache_key])

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

        try:
            response = requests.post(
                resolved_workflow_url,
                json=payload,
                headers=headers,
                timeout=self.timeout,
            )
        except requests.RequestException as exc:
            raise NuwaServiceError(f"调用女娲工作流失败：{exc}") from exc

        if not response.ok:
            error_detail = self._extract_error_detail(response)
            raise NuwaServiceError(
                f"女娲工作流返回异常状态码 {response.status_code}：{error_detail}"
            )

        try:
            result = response.json()
        except ValueError as exc:
            raise NuwaServiceError("女娲工作流返回的不是有效 JSON。") from exc

        workflow_error = self._extract_workflow_error(result)
        if workflow_error is not None:
            raise NuwaServiceError(workflow_error)

        self._cache[cache_key] = deepcopy(result)
        return result

    def execute_questions_workflow(
        self,
        questions_data: dict[str, Any],
        *,
        workflow_url: str | None = None,
        workflow_id: str | None = None,
    ) -> dict[str, Any]:
        """Execute a Nuwa workflow that consumes `inputs.questions_data`."""

        return self.execute_workflow(
            {"questions_data": deepcopy(questions_data)},
            workflow_url=workflow_url,
            workflow_id=workflow_id,
        )

    def execute_split_workflow(self, paper_data: dict[str, Any]) -> dict[str, Any]:
        """Execute the configured Nuwa split workflow."""

        workflow_url = (self.split_workflow_url or self.workflow_url).strip()
        workflow_id = (self.split_workflow_id or self.workflow_id).strip()
        return self.execute_workflow(
            deepcopy(paper_data),
            workflow_url=workflow_url,
            workflow_id=workflow_id,
        )

    def execute_spellcheck_workflow(self, questions_data: dict[str, Any]) -> dict[str, Any]:
        """Execute the configured Nuwa spellcheck workflow."""

        workflow_url = (self.spellcheck_workflow_url or self.workflow_url).strip()
        workflow_id = (self.spellcheck_workflow_id or self.workflow_id).strip()
        return self.execute_questions_workflow(
            questions_data,
            workflow_url=workflow_url,
            workflow_id=workflow_id,
        )

    def execute_compare_workflow(self, questions_data: dict[str, Any]) -> dict[str, Any]:
        """Execute the configured Nuwa comparison workflow."""

        workflow_url = (self.compare_workflow_url or self.workflow_url).strip()
        workflow_id = (self.compare_workflow_id or self.workflow_id).strip()
        return self.execute_questions_workflow(
            questions_data,
            workflow_url=workflow_url,
            workflow_id=workflow_id,
        )

    def _resolve_timeout(self, timeout: float | None) -> float:
        if timeout is not None:
            return timeout

        raw_timeout = os.getenv("NUWA_TIMEOUT", "30").strip()
        try:
            return float(raw_timeout)
        except ValueError:
            return 30.0

    def _extract_error_detail(self, response: requests.Response) -> str:
        try:
            body = response.json()
        except ValueError:
            return response.text.strip() or "无响应体"

        if isinstance(body, dict):
            return str(body.get("message") or body.get("detail") or body)
        return str(body)

    def _extract_workflow_error(self, result: Any) -> str | None:
        if not isinstance(result, dict):
            return None

        success = result.get("success")
        code = result.get("code")
        display_code = result.get("displayCode")
        message = str(result.get("message") or result.get("detail") or "").strip()

        if success is False:
            suffix = f" (code={code or display_code})" if (code or display_code) else ""
            return f"女娲工作流返回失败：{message or '未知错误'}{suffix}"

        error_codes = {"4000", "4001", "401", "403", "500"}
        if str(code).strip() in error_codes and message:
            return f"女娲工作流返回失败：{message} (code={code})"

        return None

    def _resolve_execute_url(
        self,
        workflow_url: str | None,
        workflow_id: str | None,
    ) -> str:
        resolved_workflow_url = (workflow_url or self.workflow_url).strip()
        resolved_workflow_id = (workflow_id or self.workflow_id).strip()
        if not resolved_workflow_url:
            return ""
        if not resolved_workflow_id:
            return resolved_workflow_url

        pattern = re.compile(r"(/api/v1/workflow/)([^/]+)(/execute)$")
        if pattern.search(resolved_workflow_url):
            return pattern.sub(rf"\g<1>{resolved_workflow_id}\g<3>", resolved_workflow_url)

        if "{id}" in resolved_workflow_url:
            return resolved_workflow_url.replace("{id}", resolved_workflow_id)

        return resolved_workflow_url
