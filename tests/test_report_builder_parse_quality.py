from app.models.schemas import UploadedPaper
from app.services.report_builder import ReportBuilder


def test_report_builder_exports_parse_quality_rows_without_fastapi() -> None:
    report_builder = ReportBuilder()
    uploaded_papers = [
        UploadedPaper(
            paper_id="A",
            filename="scan-a.pdf",
            subject="语文",
            temp_path="a.pdf",
            text_content="[IMAGE page=1 index=1 bbox=1,2,3,4]\n[OCR_TEXT]\n识别文本",
            page_count=1,
            image_count=1,
            ocr_attempted=True,
            ocr_succeeded=True,
            requires_manual_review=True,
            parse_note="已触发 OCR 并追加识别文本。",
        ),
        UploadedPaper(
            paper_id="B",
            filename="scan-b.pdf",
            subject="语文",
            temp_path="b.pdf",
            text_content="[IMAGE page=1 index=1 bbox=1,2,3,4]",
            page_count=1,
            image_count=2,
            ocr_attempted=True,
            ocr_succeeded=False,
            requires_manual_review=True,
            parse_note="OCR 调用失败：依赖缺失",
        ),
    ]

    report = report_builder.build_report(
        teacher_name="李老师",
        teacher_id="T001",
        subject="语文",
        uploaded_papers=uploaded_papers,
        questions=[],
        similarity_matches=[],
        spellcheck_issues=[],
    )

    context = report_builder.build_template_context(report)

    assert len(context["parse_quality_rows"]) == 2
    assert context["parse_quality_rows"][0]["paper_label"] == "A 卷"
    assert context["parse_quality_rows"][0]["ocr_status"] == "已触发 OCR 并成功"
    assert context["parse_quality_rows"][1]["risk_level"] == "高风险"
    assert context["export_payload"]["parse_quality"][1]["filename"] == "scan-b.pdf"
