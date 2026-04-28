from app.models.schemas import Question, UploadedPaper
from app.services.comparator import AgentSimilarityComparator, CodeSimilarityComparator
from app.services.question_splitter import AgentQuestionSplitter
from app.services.spellcheck.nuwa_provider import NuwaSpellcheckProvider


class FakeNuwaSplitService:
    def execute_split_workflow(self, paper_data: dict) -> dict:
        assert paper_data["paper_id"] == "A"
        assert paper_data["subject"] == "chinese"
        assert "第一题" in paper_data["text_content"]
        return {
            "output": {
                "sections": [
                    {
                        "title": "一、选择题",
                        "content": [
                            {"title": "1", "type": "question", "content": "第一题内容"},
                            {"title": "2", "type": "question", "content": "第二题内容"},
                        ],
                    }
                ]
            }
        }


class FakeNuwaSpellcheckService:
    def execute_spellcheck_workflow(self, questions_data: dict) -> dict:
        assert questions_data["paper_id"] == "A"
        assert questions_data["questions"][0]["question_no"] == "1"
        return {
            "output": {
                "error_checklist": [
                    {
                        "question_number": "1",
                        "suspected_error": "循序渐近",
                        "correction_suggestion": "循序渐进",
                    }
                ]
            }
        }


class FakeNuwaCompareService:
    def execute_compare_workflow(self, questions_data: dict) -> dict:
        assert questions_data["paper_id"] == "A"
        assert questions_data["subject"] == "chinese"
        return {
            "output": {
                "plagiarism_details": [
                    {
                        "question_number": "1",
                        "similarity_level": "90%-95%",
                        "matched_historical_question": "这里是知识库里命中的历史题目。",
                        "diff_highlight": "差异点高亮",
                    }
                ]
            }
        }


class EmptyNuwaSplitService:
    def execute_split_workflow(self, paper_data: dict) -> dict:
        return {"output": {"message": "ok"}}


def test_agent_question_splitter_maps_workflow_result() -> None:
    provider = AgentQuestionSplitter(nuwa_service=FakeNuwaSplitService())
    paper = UploadedPaper(
        paper_id="A",
        filename="a.pdf",
        subject="chinese",
        temp_path="a.pdf",
        text_content="1. 第一题\n2. 第二题",
        page_count=1,
    )

    questions = provider.split(paper.text_content, paper.paper_id, paper=paper)

    assert len(questions) == 2
    assert questions[0].question_no == "1"
    assert questions[0].content == "第一题内容"
    assert questions[1].question_no == "2"
    assert provider.provider_note == "A 卷 Agent 切题已接入女娲工作流。"


def test_agent_question_splitter_falls_back_to_rule_split_when_workflow_has_no_questions() -> None:
    provider = AgentQuestionSplitter(nuwa_service=EmptyNuwaSplitService())
    paper = UploadedPaper(
        paper_id="A",
        filename="a.pdf",
        subject="chinese",
        temp_path="a.pdf",
        text_content="1. 第一题\n2. 第二题",
        page_count=1,
    )

    questions = provider.split(paper.text_content, paper.paper_id, paper=paper)

    assert len(questions) == 2
    assert questions[0].question_no == "1"
    assert questions[1].question_no == "2"
    assert "已回退本地规则" in provider.provider_note


def test_nuwa_spellcheck_provider_maps_workflow_result() -> None:
    provider = NuwaSpellcheckProvider(nuwa_service=FakeNuwaSpellcheckService())
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
            content="请判断成语循序渐近是否书写正确。",
            raw_block="请判断成语循序渐近是否书写正确。",
        )
    ]

    issues = provider.check_questions(paper, questions)

    assert len(issues) == 1
    assert issues[0].question_no == "1"
    assert issues[0].issue_text == "循序渐近"
    assert issues[0].suggestion == "循序渐进"
    assert provider.provider_note == "Agent 错字检查已接入女娲工作流。"


def test_agent_similarity_comparator_maps_plagiarism_details_to_history_matches() -> None:
    comparator = AgentSimilarityComparator(
        nuwa_service=FakeNuwaCompareService(),
        fallback_provider=CodeSimilarityComparator(threshold=85),
    )
    uploaded_papers = [
        UploadedPaper(
            paper_id="A",
            filename="a.pdf",
            subject="chinese",
            temp_path="a.pdf",
        )
    ]
    questions = [
        Question(
            question_id="A-1",
            paper_id="A",
            question_no="1",
            order=1,
            content="第一题内容",
            raw_block="第一题内容",
        ),
        Question(
            question_id="A-2",
            paper_id="A",
            question_no="2",
            order=2,
            content="第二题内容",
            raw_block="第二题内容",
        ),
    ]

    matches = comparator.compare(questions, uploaded_papers=uploaded_papers)

    history_matches = [match for match in matches if match.comparison_type == "history_bank"]
    assert len(history_matches) == 1
    assert history_matches[0].source_question_no == "1"
    assert history_matches[0].target_paper_label == "女娲知识库"
    assert history_matches[0].similarity_score == 92.5
    assert "历史题库智能对比已接入女娲工作流" in comparator.provider_note
