from fastapi.testclient import TestClient

from app.main import app
from app.services.nuwa_service import NuwaService


def test_nuwa_execute_route_proxies_service_response(monkeypatch) -> None:
    expected_payload = {
        "success": True,
        "data": {
            "task_id": "task-123",
            "result": {"summary": "ok"},
        },
    }

    def fake_execute(self, questions_data: dict, *, workflow_id=None, workflow_url=None):
        assert workflow_id == "1976"
        assert questions_data["paper_id"] == "A"
        assert questions_data["questions"][0]["question_id"] == "A-1"
        return expected_payload

    monkeypatch.setattr(NuwaService, "execute_questions_workflow", fake_execute)

    with TestClient(app) as client:
        response = client.post(
            "/api/nuwa/execute",
            json={
                "workflow_id": "1976",
                "questions_data": {
                    "paper_id": "A",
                    "subject": "chinese",
                    "questions": [
                        {
                            "question_id": "A-1",
                            "question_no": "1",
                            "order": 1,
                            "content": "这里写一段测试用的题目文本",
                        }
                    ],
                },
            },
        )

    assert response.status_code == 200
    assert response.json() == expected_payload
