from pathlib import Path

from fastapi.testclient import TestClient

from app.main import app
from app.models.schemas import Question, SimilarityMatch, SpellcheckIssue, UploadedPaper
from app.services.comparator import SimilarityComparatorProvider
from app.services.dual_run import DualRunReviewService, PipelineRunResult, ReviewPipeline
from app.services.pdf_parser import TextExtractionProvider
from app.services.question_splitter import QuestionSplitProvider
from app.services.report_builder import ReportBuilder
from app.services.spellcheck.base import SpellcheckProvider


class FakeExtractionProvider(TextExtractionProvider):
    provider_name = "fake_extract"
    provider_label = "Fake Extract"

    def __init__(self, text: str) -> None:
        self.text = text
        self.calls = 0

    def extract(self, pdf_path: Path) -> tuple[str, int]:
        self.calls += 1
        return self.text, 1


class FakeSplitProvider(QuestionSplitProvider):
    provider_name = "fake_split"
    provider_label = "Fake Split"

    def __init__(self) -> None:
        self.calls = 0

    def split(self, text: str, paper_id: str, *, paper=None) -> list[Question]:
        self.calls += 1
        return [
            Question(
                question_id=f"{paper_id}-1",
                paper_id=paper_id,
                question_no="1",
                order=1,
                content=text,
                raw_block=text,
            )
        ]


class FakeCompareProvider(SimilarityComparatorProvider):
    provider_name = "fake_compare"
    provider_label = "Fake Compare"

    def __init__(self, score: float = 96) -> None:
        self.score = score
        self.calls = 0
        self.last_history_count = 0

    def compare(
        self,
        paper_a_questions: list[Question],
        paper_b_questions: list[Question] | None = None,
        history_questions: list[Question] | None = None,
        *,
        uploaded_papers: list[UploadedPaper] | None = None,
    ) -> list[SimilarityMatch]:
        self.calls += 1
        self.last_history_count = len(history_questions or [])
        if not paper_b_questions:
            return []

        return [
            SimilarityMatch(
                match_id="cross-A-1-B-1",
                comparison_type="cross_paper",
                source_paper_id="A",
                source_question_id="A-1",
                source_question_no="1",
                source_text=paper_a_questions[0].content,
                target_paper_id="B",
                target_question_id="B-1",
                target_question_no="1",
                target_text=paper_b_questions[0].content,
                similarity_score=self.score,
                level="高度重复" if self.score >= 95 else "疑似重复",
            )
        ]


class FakeSpellcheckProvider(SpellcheckProvider):
    provider_name = "fake_spell"
    provider_label = "Fake Spell"

    def __init__(self, issues: list[SpellcheckIssue] | None = None, is_placeholder: bool = False) -> None:
        self.issues = issues or []
        self.calls = 0
        self.is_placeholder = is_placeholder
        self.provider_note = "当前 Agent 未返回错字结果。" if is_placeholder else ""

    def check_questions(
        self,
        paper: UploadedPaper,
        questions: list[Question],
    ) -> list[SpellcheckIssue]:
        self.calls += 1
        return [
            issue.model_copy(update={"paper_id": paper.paper_id, "question_id": questions[0].question_id})
            for issue in self.issues
        ]


def test_dual_run_service_executes_code_and_agent_pipelines() -> None:
    uploaded_papers = [
        UploadedPaper(
            paper_id="A",
            filename="a.pdf",
            subject="chinese",
            temp_path="a.pdf",
        ),
        UploadedPaper(
            paper_id="B",
            filename="b.pdf",
            subject="chinese",
            temp_path="b.pdf",
        ),
    ]

    code_extract = FakeExtractionProvider("1. 代码题目")
    agent_extract = FakeExtractionProvider("1. Agent题目")
    code_split = FakeSplitProvider()
    agent_split = FakeSplitProvider()
    code_compare = FakeCompareProvider(score=96)
    agent_compare = FakeCompareProvider(score=90)
    history_questions = [
        Question(
            question_id="H1-1",
            paper_id="H1",
            paper_label="历史卷",
            question_no="1",
            order=1,
            content="历史题目",
            raw_block="历史题目",
        )
    ]
    code_spell = FakeSpellcheckProvider(
        issues=[
            SpellcheckIssue(
                issue_id="i1",
                paper_id="A",
                question_id="A-1",
                question_no="1",
                issue_type="常见错别字",
                original_text="循序渐近地分析问题。",
                issue_text="循序渐近",
                suggestion="循序渐进",
            )
        ]
    )
    agent_spell = FakeSpellcheckProvider(is_placeholder=True)

    dual_run_service = DualRunReviewService(
        code_pipeline=ReviewPipeline(
            pipeline_name="代码版",
            extraction_provider=code_extract,
            split_provider=code_split,
            compare_provider=code_compare,
            spellcheck_provider=code_spell,
        ),
        agent_pipeline=ReviewPipeline(
            pipeline_name="Agent 版",
            extraction_provider=agent_extract,
            split_provider=agent_split,
            compare_provider=agent_compare,
            spellcheck_provider=agent_spell,
        ),
    )

    code_result, agent_result = dual_run_service.run(
        uploaded_papers,
        history_questions=history_questions,
        history_bank_summary={"loaded_files": 1},
    )

    assert code_extract.calls == 2
    assert agent_extract.calls == 2
    assert code_split.calls == 2
    assert agent_split.calls == 2
    assert code_compare.calls == 1
    assert agent_compare.calls == 1
    assert code_compare.last_history_count == 1
    assert agent_compare.last_history_count == 1
    assert len(code_result.spellcheck_issues) == 2
    assert agent_result.spellcheck_issues == []
    assert code_result.history_bank_summary["loaded_files"] == 1


def test_report_builder_marks_code_only_and_score_difference() -> None:
    report_builder = ReportBuilder()

    uploaded_papers = [
        UploadedPaper(
            paper_id="A",
            filename="a.pdf",
            subject="语文",
            temp_path="a.pdf",
            text_content="1. 第一题",
            page_count=1,
        ),
        UploadedPaper(
            paper_id="B",
            filename="b.pdf",
            subject="语文",
            temp_path="b.pdf",
            text_content="1. 第一题",
            page_count=1,
        ),
    ]
    questions = [
        Question(
            question_id="A-1",
            paper_id="A",
            question_no="1",
            order=1,
            content="小明以 5m/s 的速度前进 10 秒，求路程。",
            raw_block="小明以 5m/s 的速度前进 10 秒，求路程。",
        ),
        Question(
            question_id="B-1",
            paper_id="B",
            question_no="1",
            order=1,
            content="小明以 5m/s 的速度前进 10 秒，求路程。",
            raw_block="小明以 5m/s 的速度前进 10 秒，求路程。",
        ),
    ]
    code_match = SimilarityMatch(
        match_id="m1",
        comparison_type="cross_paper",
        source_paper_id="A",
        source_question_id="A-1",
        source_question_no="1",
        source_text=questions[0].content,
        target_paper_id="B",
        target_question_id="B-1",
        target_question_no="1",
        target_text=questions[1].content,
        similarity_score=96,
        level="高度重复",
    )
    agent_match = code_match.model_copy(update={"similarity_score": 90, "level": "疑似重复"})
    code_issue = SpellcheckIssue(
        issue_id="s1",
        paper_id="A",
        question_id="A-1",
        question_no="1",
        issue_type="常见错别字",
        original_text="循序渐近地分析问题。",
        issue_text="循序渐近",
        suggestion="循序渐进",
    )

    report = report_builder.build_report(
        teacher_name="李老师",
        teacher_id="T001",
        subject="语文",
        uploaded_papers=uploaded_papers,
        questions=questions,
        similarity_matches=[code_match],
        spellcheck_issues=[code_issue],
    )

    code_run = PipelineRunResult(
        pipeline_name="代码版",
        uploaded_papers=uploaded_papers,
        questions=questions,
        similarity_matches=[code_match],
        spellcheck_issues=[code_issue],
        module_metadata={
            "extract": {"provider_note": "", "is_placeholder": False},
            "split": {"provider_note": "", "is_placeholder": False},
            "compare": {"provider_note": "", "is_placeholder": False},
            "spellcheck": {"provider_note": "", "is_placeholder": False},
        },
    )
    agent_run = PipelineRunResult(
        pipeline_name="Agent 版",
        uploaded_papers=uploaded_papers,
        questions=questions,
        similarity_matches=[agent_match],
        spellcheck_issues=[],
        module_metadata={
            "extract": {"provider_note": "当前为占位实现。", "is_placeholder": True},
            "split": {"provider_note": "当前为占位实现。", "is_placeholder": True},
            "compare": {"provider_note": "当前为占位实现。", "is_placeholder": True},
            "spellcheck": {"provider_note": "当前 Agent 未返回错字结果。", "is_placeholder": True},
        },
    )

    context = report_builder.build_template_context(
        report,
        code_run_result=code_run,
        agent_run_result=agent_run,
    )

    assert context["spellcheck_rows"][0]["compare_status"] == "代码独有"
    assert context["duplicate_rows"][0]["compare_status"] == "评分不同"
    assert any(section["status"] == "Agent 未返回" for section in context["dual_run_sections"])


def test_review_route_renders_dual_run_report(monkeypatch) -> None:
    class FakeHistorySnapshot:
        questions = [
            Question(
                question_id="H1-1",
                paper_id="H1",
                paper_label="历史卷",
                question_no="1",
                order=1,
                content="历史题目",
                raw_block="历史题目",
            )
        ]

        @staticmethod
        def to_summary() -> dict:
            return {
                "bank_dir": "data/datasets/history_bank",
                "total_files": 1,
                "loaded_files": 1,
                "failed_files": 0,
                "question_count": 1,
                "papers": [],
                "failures": [],
            }

    class FakeHistoryService:
        @staticmethod
        def get_snapshot():
            return FakeHistorySnapshot()

    def fake_run(self, uploaded_papers, *, history_questions=None, history_bank_summary=None):
        uploaded_papers = [
            paper.model_copy(update={"text_content": "1. 第一题", "page_count": 1})
            for paper in uploaded_papers
        ]
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
        code_issue = SpellcheckIssue(
            issue_id="s1",
            paper_id="A",
            question_id="A-1",
            question_no="1",
            issue_type="常见错别字",
            original_text="循序渐近地分析问题。",
            issue_text="循序渐近",
            suggestion="循序渐进",
        )
        code_result = PipelineRunResult(
            pipeline_name="代码版",
            uploaded_papers=uploaded_papers,
            questions=questions,
            similarity_matches=[],
            spellcheck_issues=[code_issue],
            module_metadata={
                "extract": {"provider_note": "", "is_placeholder": False},
                "split": {"provider_note": "", "is_placeholder": False},
                "compare": {"provider_note": "", "is_placeholder": False},
                "spellcheck": {"provider_note": "", "is_placeholder": False},
            },
            history_bank_summary=history_bank_summary or {},
        )
        agent_result = PipelineRunResult(
            pipeline_name="Agent 版",
            uploaded_papers=uploaded_papers,
            questions=questions,
            similarity_matches=[],
            spellcheck_issues=[],
            module_metadata={
                "extract": {"provider_note": "当前为占位实现。", "is_placeholder": True},
                "split": {"provider_note": "当前为占位实现。", "is_placeholder": True},
                "compare": {"provider_note": "当前为占位实现。", "is_placeholder": True},
                "spellcheck": {"provider_note": "当前 Agent 未返回错字结果。", "is_placeholder": True},
            },
            history_bank_summary=history_bank_summary or {},
        )
        assert history_questions is not None
        return code_result, agent_result

    monkeypatch.setattr(DualRunReviewService, "run", fake_run)

    with TestClient(app) as client:
        client.app.state.history_bank_service = FakeHistoryService()
        response = client.post(
            "/review",
            data={"teacher_name": "李老师", "teacher_id": "T001", "subject": "chinese"},
            files={"paper_a": ("a.pdf", b"%PDF-1.4 fake", "application/pdf")},
        )

    assert response.status_code == 200
    assert "代码版 / Agent 版模块对照" in response.text
    assert "历史题库状态" in response.text
    assert "导出当前对照 JSON" in response.text
