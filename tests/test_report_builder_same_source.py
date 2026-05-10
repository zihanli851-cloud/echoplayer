from app.models.schemas import Question, SimilarityMatch
from app.services.report_builder import ReportBuilder


def test_report_builder_separates_same_source_matches() -> None:
    report_builder = ReportBuilder()
    questions = [
        Question(
            question_id="A-1",
            paper_id="A",
            question_no="1",
            order=1,
            content="某汽车以 60km/h 行驶 3 小时，求路程。",
            raw_block="某汽车以 60km/h 行驶 3 小时，求路程。",
        ),
        Question(
            question_id="H-1",
            paper_id="H",
            paper_label="历史卷",
            question_no="1",
            order=1,
            content="某汽车以 80km/h 行驶 2 小时，求路程。",
            raw_block="某汽车以 80km/h 行驶 2 小时，求路程。",
        ),
    ]
    same_source_match = SimilarityMatch(
        match_id="history_bank-text-A-1-H-1",
        comparison_type="history_bank",
        source_paper_id="A",
        source_paper_label="A",
        source_question_id="A-1",
        source_question_no="1",
        source_text=questions[0].content,
        target_paper_id="H",
        target_paper_label="历史卷",
        target_question_id="H-1",
        target_question_no="1",
        target_text=questions[1].content,
        similarity_score=93,
        literal_score=86,
        template_score=95,
        final_score=93,
        is_same_source_question=True,
        level="疑似原题",
    )
    normal_match = SimilarityMatch(
        match_id="cross_paper-A-1-B-1",
        comparison_type="cross_paper",
        source_paper_id="A",
        source_paper_label="A",
        source_question_id="A-1",
        source_question_no="1",
        source_text="请分析传统文化传承与创新之间的关系。",
        target_paper_id="B",
        target_paper_label="B",
        target_question_id="B-1",
        target_question_no="1",
        target_text="请分析传统文化传承和创新之间的关系。",
        similarity_score=90,
        literal_score=90,
        template_score=90,
        final_score=90,
        is_same_source_question=False,
        level="疑似重复",
    )

    report = report_builder.build_report(
        teacher_name="李老师",
        teacher_id="T001",
        subject="语文",
        uploaded_papers=[],
        questions=questions,
        similarity_matches=[same_source_match, normal_match],
        spellcheck_issues=[],
    )

    context = report_builder.build_template_context(report)

    assert len(context["same_source_rows"]) == 1
    assert context["same_source_rows"][0]["same_source_flag"] == "同源题"
    assert "模板分 95" in context["same_source_rows"][0]["reason"]
    assert len(context["duplicate_rows"]) == 1
    assert context["duplicate_rows"][0]["match_id"] == "cross_paper-A-1-B-1"
    assert context["export_payload"]["same_source_matches"][0]["match_id"] == "history_bank-text-A-1-H-1"
