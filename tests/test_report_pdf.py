from app.services.report_pdf import build_report_pdf
import pdfplumber


def build_payload() -> dict:
    return {
        "report": {
            "teacher_name": "李老师",
            "teacher_id": "T001",
            "subject": "语文",
            "generated_at": "2026-05-10T10:00:00",
            "uploaded_papers": [
                {"paper_id": "A", "filename": "a.pdf", "page_count": 2},
            ],
            "dashboard": {
                "paper_a_question_count": 1,
                "paper_b_question_count": 0,
                "paper_a_internal_high": 0,
                "paper_b_internal_high": 0,
                "cross_paper_high": 1,
                "history_high": 0,
                "suspected_duplicate_total": 1,
                "history_match_total": 0,
                "spellcheck_issue_total": 1,
                "pending_review_total": 1,
            },
        },
        "history_bank": {
            "bank_dir": "data/datasets/history_bank",
            "total_files": 10,
            "loaded_files": 8,
            "question_count": 100,
        },
        "duplicate_comparison": {
            "summary": {"matched": 0, "score_diff": 0, "code_only": 1, "agent_only": 0},
            "code_rows": [
                {
                    "comparison_label": "A/B 交叉查重",
                    "level": "高度重复",
                    "score": 98,
                    "source_label": "A 卷第 1 题",
                    "target_label": "B 卷第 1 题",
                    "review_status": "待确认",
                }
            ],
        },
        "spellcheck_comparison": {
            "summary": {"matched": 0, "code_only": 1, "agent_only": 0},
            "code_rows": [
                {
                    "paper_label": "A 卷",
                    "question_no": "1",
                    "issue_type": "错别字",
                    "issue_text": "春添",
                    "suggestion": "春天",
                }
            ],
        },
        "dual_run_sections": [
            {"module_name": "文本提取", "status": "一致", "diff_summary": "代码版与 Agent 版一致。"},
        ],
    }


def test_build_report_pdf_returns_valid_pdf_bytes() -> None:
    result = build_report_pdf(build_payload())

    assert result.filename.endswith(".pdf")
    assert result.content.startswith(b"%PDF-1.4")
    assert b"%%EOF" in result.content
    assert b"/Type /Catalog" in result.content


def test_build_report_pdf_can_be_opened_by_pdf_parser(tmp_path) -> None:
    result = build_report_pdf(build_payload())
    pdf_path = tmp_path / result.filename
    pdf_path.write_bytes(result.content)

    with pdfplumber.open(str(pdf_path)) as pdf:
        assert len(pdf.pages) >= 1
