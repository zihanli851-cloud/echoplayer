from app.models.schemas import Question, UploadedPaper
from app.services.report_builder import ReportBuilder


def test_report_builder_exports_complex_question_rows() -> None:
    report_builder = ReportBuilder()
    uploaded_papers = [
        UploadedPaper(
            paper_id="A",
            filename="paper-a.pdf",
            subject="数学",
            temp_path="a.pdf",
            text_content="试卷文本",
            page_count=2,
            image_count=1,
            ocr_attempted=True,
            ocr_succeeded=True,
        )
    ]
    questions = [
        Question(
            question_id="A-1",
            paper_id="A",
            question_no="1",
            order=1,
            content="[IMAGE page=1 index=1 bbox=1,2,3,4]\n[OCR_TEXT]\n根据下图求函数 y = x^2 + 1 的最大值。",
            raw_block="same",
        ),
        Question(
            question_id="A-2",
            paper_id="A",
            question_no="2",
            order=2,
            content="设矩阵 A 满足 λ1 + λ2 = 3，求特征值并分析如下表数据。",
            raw_block="same",
        ),
        Question(
            question_id="A-3",
            paper_id="A",
            question_no="3",
            order=3,
            content="这是一道普通文本题。",
            raw_block="same",
        ),
    ]

    report = report_builder.build_report(
        teacher_name="李老师",
        teacher_id="T001",
        subject="数学",
        uploaded_papers=uploaded_papers,
        questions=questions,
        similarity_matches=[],
        spellcheck_issues=[],
    )

    context = report_builder.build_template_context(report)

    assert len(context["complex_question_rows"]) == 2
    first_row = context["complex_question_rows"][0]
    assert first_row["question_no"] == "1"
    assert "图片题" in first_row["flags"]
    assert "OCR回填" in first_row["flags"]
    assert first_row["review_level"] == "高风险"
    assert context["export_payload"]["complex_question_quality"][1]["flag_summary"] == "图表题 / 复杂公式题"
