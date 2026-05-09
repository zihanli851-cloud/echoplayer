from pathlib import Path
import os

from fastapi import APIRouter, File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from app.models.schemas import UploadedPaper
from app.services.comparator import (
    AgentSimilarityComparator,
    CodeSimilarityComparator,
    SkippedAgentSimilarityComparator,
)
from app.services.coze_service import CozeService
from app.services.dual_run import DualRunReviewService, ReviewPipeline
from app.services.pdf_parser import AgentPdfParser, CodePdfParser, PdfParseError
from app.services.question_splitter import AgentQuestionSplitter, RuleQuestionSplitter
from app.services.report_builder import ReportBuilder
from app.services.spellcheck.local_provider import LocalSpellcheckProvider
from app.services.spellcheck.coze_provider import (
    CozeSpellcheckProvider,
    SkippedCozeSpellcheckProvider,
)
from app.utils.file_manager import cleanup_processing_dir, create_processing_dir, save_upload_file


BASE_DIR = Path(__file__).resolve().parent.parent.parent
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))
router = APIRouter()


SUBJECT_OPTIONS = [
    ("chinese", "语文"),
    ("math", "数学"),
    ("english", "英语"),
    ("physics", "物理"),
    ("chemistry", "化学"),
    ("politics", "政治"),
    ("history", "历史"),
    ("geography", "地理"),
]

DEFAULT_AGENT_TIMEOUT = 20.0


def get_agent_timeout() -> float:
    raw_value = os.getenv("AGENT_TIMEOUT", "").strip()
    if not raw_value:
        return DEFAULT_AGENT_TIMEOUT
    try:
        return max(1.0, float(raw_value))
    except ValueError:
        return DEFAULT_AGENT_TIMEOUT


def is_enabled_env(name: str, *, default: bool) -> bool:
    raw_value = os.getenv(name, "").strip().lower()
    if not raw_value:
        return default
    return raw_value in {"1", "true", "yes", "on", "enabled"}


def is_pdf_file(upload: UploadFile | None) -> bool:
    if upload is None:
        return False

    filename = (upload.filename or "").lower()
    content_type = (upload.content_type or "").lower()
    return filename.endswith(".pdf") or content_type == "application/pdf"


def render_index(
    request: Request,
    *,
    form_data: dict,
    message: str = "",
    message_type: str = "",
    result: dict | None = None,
    status_code: int = 200,
) -> HTMLResponse:
    """Render the upload page with a unified context."""

    return templates.TemplateResponse(
        request,
        "index.html",
        {
            "subject_options": SUBJECT_OPTIONS,
            "form_data": form_data,
            "message": message,
            "message_type": message_type,
            "result": result,
        },
        status_code=status_code,
    )


@router.get("/", response_class=HTMLResponse)
async def index(request: Request) -> HTMLResponse:
    return render_index(
        request,
        form_data={
            "teacher_name": "",
            "teacher_id": "",
            "subject": "chinese",
        },
    )


@router.post("/review", response_class=HTMLResponse)
async def review(
    request: Request,
    teacher_name: str = Form(...),
    teacher_id: str = Form(...),
    subject: str = Form(...),
    paper_a: UploadFile = File(...),
    paper_b: UploadFile | None = File(None),
) -> HTMLResponse:
    form_data = {
        "teacher_name": teacher_name.strip(),
        "teacher_id": teacher_id.strip(),
        "subject": subject,
    }

    if not form_data["teacher_name"] or not form_data["teacher_id"]:
        return render_index(
            request,
            form_data=form_data,
            message="教师姓名和教师工号不能为空。",
            message_type="error",
            status_code=400,
        )

    if not is_pdf_file(paper_a):
        return render_index(
            request,
            form_data=form_data,
            message="A 卷必须为 PDF 文件。",
            message_type="error",
            status_code=400,
        )

    if paper_b and not is_pdf_file(paper_b):
        return render_index(
            request,
            form_data=form_data,
            message="B 卷必须为 PDF 文件。",
            message_type="error",
            status_code=400,
        )

    processing_dir = create_processing_dir(request.app.state.temp_dir)
    saved_a: Path | None = None
    saved_b: Path | None = None

    try:
        saved_a = await save_upload_file(paper_a, processing_dir, "paper_a")
        saved_b = await save_upload_file(paper_b, processing_dir, "paper_b") if paper_b else None

        uploaded_papers = [
            UploadedPaper(
                paper_id="A",
                filename=paper_a.filename or saved_a.name,
                subject=subject,
                temp_path=str(saved_a),
            )
        ]

        if saved_b and paper_b:
            uploaded_papers.append(
                UploadedPaper(
                    paper_id="B",
                    filename=paper_b.filename or saved_b.name,
                    subject=subject,
                    temp_path=str(saved_b),
                )
            )

        history_questions = None
        history_bank_summary = {}
        history_bank_service = getattr(request.app.state, "history_bank_service", None)
        if history_bank_service is not None:
            try:
                history_snapshot = history_bank_service.get_snapshot()
                history_questions = history_snapshot.questions
                history_bank_summary = history_snapshot.to_summary()
            except Exception as exc:
                history_bank_summary = {
                    "bank_dir": str(getattr(request.app.state, "history_bank_dir", "")),
                    "total_files": 0,
                    "loaded_files": 0,
                    "failed_files": 0,
                    "question_count": 0,
                    "papers": [],
                    "failures": [],
                    "load_error": str(exc),
                }

        agent_timeout = get_agent_timeout()
        coze_service = CozeService(timeout=agent_timeout)
        enable_agent_compare = is_enabled_env("ENABLE_AGENT_COMPARE", default=False)
        enable_agent_spellcheck = is_enabled_env("ENABLE_AGENT_SPELLCHECK", default=False)
        dual_run_service = DualRunReviewService(
            code_pipeline=ReviewPipeline(
                pipeline_name="代码版",
                extraction_provider=CodePdfParser(),
                split_provider=RuleQuestionSplitter(),
                compare_provider=CodeSimilarityComparator(),
                spellcheck_provider=LocalSpellcheckProvider(),
            ),
            agent_pipeline=ReviewPipeline(
                pipeline_name="Coze 智能体版",
                extraction_provider=AgentPdfParser(),
                split_provider=AgentQuestionSplitter(coze_service=coze_service),
                compare_provider=(
                    AgentSimilarityComparator(coze_service=coze_service)
                    if enable_agent_compare
                    else SkippedAgentSimilarityComparator()
                ),
                spellcheck_provider=(
                    CozeSpellcheckProvider(coze_service=coze_service)
                    if enable_agent_spellcheck
                    else SkippedCozeSpellcheckProvider()
                ),
            ),
            agent_timeout=agent_timeout,
        )
        code_run_result, agent_run_result = dual_run_service.run(
            uploaded_papers,
            history_questions=history_questions,
            history_bank_summary=history_bank_summary,
        )

        subject_label = next((label for value, label in SUBJECT_OPTIONS if value == subject), subject)
        report_builder = ReportBuilder()
        report = report_builder.build_report(
            teacher_name=form_data["teacher_name"],
            teacher_id=form_data["teacher_id"],
            subject=subject_label,
            uploaded_papers=code_run_result.uploaded_papers,
            questions=code_run_result.questions,
            similarity_matches=code_run_result.similarity_matches,
            spellcheck_issues=code_run_result.spellcheck_issues,
        )
        template_context = report_builder.build_template_context(
            report,
            code_run_result=code_run_result,
            agent_run_result=agent_run_result,
        )

        return templates.TemplateResponse(
            request,
            "report.html",
            template_context,
        )
    except PdfParseError as exc:
        return render_index(
            request,
            form_data=form_data,
            message=str(exc),
            message_type="error",
            status_code=400,
        )
    except Exception as exc:
        return render_index(
            request,
            form_data=form_data,
            message=f"处理失败：{exc}",
            message_type="error",
            status_code=500,
        )
    finally:
        cleanup_processing_dir(processing_dir)
