import json

from app.models.schemas import Question
from app.models.schemas import UploadedPaper
from app.services.comparator import AgentSimilarityComparator, CodeSimilarityComparator
from app.services.coze_service import CozeServiceError
from app.services.coze_service import CozeService
from app.services.question_splitter import AgentQuestionSplitter


class FakeCozeSplitService:
    def __init__(self) -> None:
        self.paper_content = None
        self.paper_id = None

    def execute_split(
        self,
        paper_content: str,
        *,
        paper_id: str = "unknown",
        subject: str = "",
        filename: str = "",
        questions: list | None = None,
    ) -> dict:
        self.paper_content = paper_content
        self.paper_id = paper_id
        return {
            "split_result": [
                {
                    "question_no": "1",
                    "content": "第一题内容",
                }
            ]
        }


def test_agent_question_splitter_calls_coze_with_text_content() -> None:
    coze_service = FakeCozeSplitService()
    provider = AgentQuestionSplitter(coze_service=coze_service)
    paper = UploadedPaper(
        paper_id="A",
        filename="a.pdf",
        subject="chinese",
        temp_path="a.pdf",
        text_content="1. 第一题",
        page_count=1,
    )

    questions = provider.split(paper.text_content, paper.paper_id, paper=paper)

    assert coze_service.paper_content == "1. 第一题"
    assert coze_service.paper_id == "A"
    assert questions[0].content == "第一题内容"


class ErrorCozeSplitService:
    @staticmethod
    def execute_split(
        paper_content: str,
        *,
        paper_id: str = "unknown",
        subject: str = "",
        filename: str = "",
        questions: list | None = None,
    ) -> dict:
        raise CozeServiceError("Coze 内部错误")


def test_agent_question_splitter_reports_coze_error_without_local_fallback() -> None:
    provider = AgentQuestionSplitter(coze_service=ErrorCozeSplitService())
    paper = UploadedPaper(
        paper_id="A",
        filename="a.pdf",
        subject="chinese",
        temp_path="a.pdf",
        text_content="1. 第一题",
        page_count=1,
    )

    questions = provider.split(paper.text_content, paper.paper_id, paper=paper)

    assert questions == []
    assert provider.is_placeholder is True
    assert "Coze 切题调用失败" in provider.provider_note


class FakeCozeCompareService:
    def execute_compare(self, questions_data: dict) -> dict:
        return {
            "_parsed_data": {
                "output_report": {
                    "plagiarism_details": [
                        json.dumps(
                            {
                                "question_number": 1,
                                "similarity_level": "90%-95%",
                                "matched_historical_question": "历史题目内容",
                                "diff_highlight": "题干相近",
                            },
                            ensure_ascii=False,
                        )
                    ]
                }
            }
        }


def test_agent_similarity_comparator_parses_coze_json_string_details() -> None:
    comparator = AgentSimilarityComparator(
        coze_service=FakeCozeCompareService(),
        fallback_provider=CodeSimilarityComparator(threshold=100),
    )
    paper = UploadedPaper(
        paper_id="A",
        filename="a.pdf",
        subject="chinese",
        temp_path="a.pdf",
    )
    questions = [
        Question(
            question_id="A-1",
            paper_id="A",
            question_no="1",
            order=1,
            content="第一题内容",
            raw_block="第一题内容",
        )
    ]

    matches = comparator.compare(questions, uploaded_papers=[paper])

    assert len(matches) == 1
    assert matches[0].target_paper_label == "Coze 知识库"
    assert matches[0].target_text == "历史题目内容"
    assert matches[0].similarity_score == 92.5


def test_coze_service_split_payload_includes_both_text_fields(monkeypatch) -> None:
    captured_payloads = []

    class FakeResponse:
        ok = True
        status_code = 200
        text = "{}"

        @staticmethod
        def json() -> dict:
            return {"code": 0, "data": "{}"}

    def fake_post(url, *, json, headers, timeout):
        captured_payloads.append(json)
        return FakeResponse()

    monkeypatch.setattr("app.services.coze_service.requests.post", fake_post)
    service = CozeService(
        api_url="https://example.test/workflow/run",
        split_workflow_id="split-workflow",
        bot_token="token",
    )

    service.execute_split(
        "题目文本",
        paper_id="A",
        subject="chinese",
        filename="a.pdf",
    )

    paper_text_data = captured_payloads[0]["parameters"]["paper_text_data"]
    assert captured_payloads[0]["workflow_id"] == "split-workflow"
    assert paper_text_data["content"] == "题目文本"
    assert paper_text_data["text_content"] == "题目文本"
    assert paper_text_data["subject"] == "chinese"
    assert paper_text_data["filename"] == "a.pdf"


def test_coze_service_cache_is_scoped_by_workflow_id(monkeypatch) -> None:
    call_count = 0

    class FakeResponse:
        ok = True
        status_code = 200
        text = "{}"

        @staticmethod
        def json() -> dict:
            return {"code": 0, "data": "{}"}

    def fake_post(url, *, json, headers, timeout):
        nonlocal call_count
        call_count += 1
        return FakeResponse()

    monkeypatch.setattr("app.services.coze_service.requests.post", fake_post)
    service = CozeService(
        api_url="https://example.test/workflow/run",
        workflow_id="default-workflow",
        bot_token="token",
    )
    params = {"same": "parameters"}

    service.execute_workflow(params, workflow_id="workflow-a")
    service.execute_workflow(params, workflow_id="workflow-b")
    service.execute_workflow(params, workflow_id="workflow-a")

    assert call_count == 2
