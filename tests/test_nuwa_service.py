from __future__ import annotations

import pytest

from app.services.nuwa_service import NuwaService, NuwaServiceError


class FakeResponse:
    def __init__(self, payload: dict, *, status_code: int = 200) -> None:
        self._payload = payload
        self.status_code = status_code
        self.ok = 200 <= status_code < 300
        self.text = ""

    def json(self) -> dict:
        return self._payload


def test_execute_workflow_raises_when_nuwa_returns_success_false(monkeypatch) -> None:
    def fake_post(*args, **kwargs):
        return FakeResponse(
            {
                "code": "4000",
                "displayCode": "4000",
                "message": "无效的 API Key",
                "data": None,
                "success": False,
            }
        )

    monkeypatch.setattr("app.services.nuwa_service.requests.post", fake_post)

    service = NuwaService(
        workflow_url="https://example.com/workflow",
        workflow_id="1975",
        api_key="fake-api-key",
    )

    with pytest.raises(NuwaServiceError, match="无效的 API Key"):
        service.execute_workflow({"text": "hello"})


def test_execute_workflow_posts_direct_inputs_and_rewrites_workflow_id_in_url(monkeypatch) -> None:
    captured: dict = {}

    def fake_post(url, *, json=None, headers=None, timeout=None):
        captured["url"] = url
        captured["json"] = json
        captured["headers"] = headers
        captured["timeout"] = timeout
        return FakeResponse({"code": "0000", "success": True, "data": {"ok": True}})

    monkeypatch.setattr("app.services.nuwa_service.requests.post", fake_post)

    service = NuwaService(
        workflow_url="https://api.nuwax.com/api/v1/workflow/1976/execute",
        workflow_id="1976",
        api_key="ak-test",
        timeout=12,
    )

    result = service.execute_workflow({"questions_data": {"paper_id": "A"}}, workflow_id="1975")

    assert result["data"]["ok"] is True
    assert captured["url"] == "https://api.nuwax.com/api/v1/workflow/1975/execute"
    assert captured["json"] == {"questions_data": {"paper_id": "A"}}
    assert captured["headers"]["Authorization"] == "Bearer ak-test"
    assert captured["timeout"] == 12
