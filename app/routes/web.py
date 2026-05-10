from pathlib import Path
import os
from urllib.parse import quote

from fastapi import APIRouter, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, Response
from fastapi.templating import Jinja2Templates

from app.models.schemas import UploadedPaper
from app.services.agent_jobs import AgentJobStore, build_agent_pending_result, pipeline_result_payload, pipeline_result_summary
from app.services.comparator import (
    AgentSimilarityComparator,
    CodeSimilarityComparator,
    SkippedAgentSimilarityComparator,
)
from app.services.coze_service import CozeService
from app.services.dual_run import DualRunReviewService, ReviewPipeline
from app.services.history_bank import HistoryBankService
from app.services.ocr import build_ocr_provider_from_env
from app.services.pdf_parser import AgentPdfParser, PdfParseError, RoutedPdfParser
from app.services.question_splitter import AgentQuestionSplitter, RuleQuestionSplitter
from app.services.report_builder import ReportBuilder
from app.services.report_pdf import build_report_pdf
from app.services.review_store import REVIEW_STATUS_OPTIONS, ReviewStore
from app.services.spellcheck.local_provider import LocalSpellcheckProvider
from app.services.spellcheck.coze_provider import (
    CozeSpellcheckProvider,
    SkippedCozeSpellcheckProvider,
)
from app.utils.file_manager import cleanup_processing_dir, create_processing_dir, ensure_directory, save_upload_file


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

DEFAULT_AGENT_TIMEOUT = 60.0  # 1 分钟


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


@router.get("/history-bank", response_class=HTMLResponse)
async def history_bank(
    request: Request,
    refresh: bool = Query(False),
    subject: str = Query(""),
    q: str = Query(""),
    message: str = "",
    message_type: str = "",
) -> HTMLResponse:
    history_bank_service = _get_history_bank_service(request)
    try:
        if refresh:
            snapshot = history_bank_service.get_snapshot(force_refresh=True)
        elif hasattr(history_bank_service, "get_cached_or_directory_summary"):
            snapshot = history_bank_service.get_cached_or_directory_summary()
        else:
            snapshot = history_bank_service.get_snapshot(force_refresh=False)
        if hasattr(snapshot, "filtered_summary"):
            summary = snapshot.filtered_summary(subject=subject, keyword=q)
        else:
            summary = snapshot.to_summary()
            summary["active_subject"] = subject
            summary["active_keyword"] = q
            summary["subjects"] = []
    except Exception as exc:
        summary = {
            "bank_dir": str(getattr(request.app.state, "history_bank_dir", "")),
            "total_files": 0,
            "loaded_files": 0,
            "failed_files": 0,
            "question_count": 0,
            "papers": [],
            "failures": [],
            "load_error": str(exc),
            "subjects": [],
            "active_subject": subject,
            "active_keyword": q,
        }

    return templates.TemplateResponse(
        request,
        "history_bank.html",
        {
            "summary": summary,
            "message": message,
            "message_type": message_type,
        },
    )


@router.post("/history-bank/upload", response_class=HTMLResponse)
async def upload_history_bank(
    request: Request,
    files: list[UploadFile] = File(...),
) -> HTMLResponse:
    history_bank_dir = ensure_directory(getattr(request.app.state, "history_bank_dir", BASE_DIR / "data" / "datasets" / "history_bank"))
    saved_count = 0
    skipped_names: list[str] = []

    for upload in files:
        if not is_pdf_file(upload):
            skipped_names.append(upload.filename or "未命名文件")
            await upload.close()
            continue

        await _save_history_bank_upload(upload, history_bank_dir)
        saved_count += 1

    if saved_count:
        service = getattr(request.app.state, "history_bank_service", None)
        if service is not None and hasattr(service, "invalidate_cache"):
            service.invalidate_cache()

    if saved_count:
        message = f"已上传 {saved_count} 份历史题库 PDF。"
        message_type = "success"
    else:
        message = "未上传有效 PDF 文件。"
        message_type = "error"
    if skipped_names:
        message = f"{message} 已跳过非 PDF 文件：{', '.join(skipped_names)}。"

    return await history_bank(
        request,
        refresh=False,
        message=message,
        message_type=message_type,
    )


@router.post("/history-bank/delete", response_class=HTMLResponse)
async def delete_history_bank_file(
    request: Request,
    relative_path: str = Form(...),
    subject: str = Form(""),
    q: str = Form(""),
) -> HTMLResponse:
    history_bank_dir = ensure_directory(getattr(request.app.state, "history_bank_dir", BASE_DIR / "data" / "datasets" / "history_bank"))
    try:
        deleted_path = _resolve_history_bank_pdf_path(history_bank_dir, relative_path)
        deleted_path.unlink()
        service = getattr(request.app.state, "history_bank_service", None)
        if service is not None and hasattr(service, "invalidate_cache"):
            service.invalidate_cache()
        message = f"已删除历史题库文件：{deleted_path.name}。"
        message_type = "success"
    except (FileNotFoundError, ValueError) as exc:
        message = str(exc)
        message_type = "error"

    return await history_bank(
        request,
        refresh=False,
        subject=subject,
        q=q,
        message=message,
        message_type=message_type,
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
        ocr_provider = build_ocr_provider_from_env()
        enable_agent_compare = is_enabled_env("ENABLE_AGENT_COMPARE", default=False)
        enable_agent_spellcheck = is_enabled_env("ENABLE_AGENT_SPELLCHECK", default=False)
        dual_run_service = DualRunReviewService(
            code_pipeline=ReviewPipeline(
                pipeline_name="代码版",
                extraction_provider=RoutedPdfParser(ocr_provider=ocr_provider),
                split_provider=RuleQuestionSplitter(),
                compare_provider=CodeSimilarityComparator(),
                spellcheck_provider=LocalSpellcheckProvider(),
            ),
            agent_pipeline=ReviewPipeline(
                pipeline_name="Coze 智能体版",
                extraction_provider=AgentPdfParser(
                    fallback_provider=RoutedPdfParser(ocr_provider=ocr_provider),
                ),
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
        if is_enabled_env("ENABLE_ASYNC_AGENT", default=True):
            code_run_result = dual_run_service.code_pipeline.run(
                uploaded_papers,
                history_questions=history_questions,
                history_bank_summary=history_bank_summary,
            )
            agent_job_store = _get_agent_job_store(request)
            agent_job = agent_job_store.submit(
                agent_pipeline=dual_run_service.agent_pipeline,
                uploaded_papers=uploaded_papers,
                history_questions=history_questions,
                history_bank_summary=history_bank_summary,
            )
            agent_run_result = build_agent_pending_result(
                dual_run_service.agent_pipeline.pipeline_name,
                code_run_result.uploaded_papers,
                history_bank_summary=history_bank_summary,
                job_id=agent_job.job_id,
            )
        else:
            agent_job = None
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
        _attach_review_persistence(
            request,
            template_context,
            teacher_id=form_data["teacher_id"],
            teacher_name=form_data["teacher_name"],
            subject=subject_label,
            code_run_result=code_run_result,
        )
        if agent_job is not None:
            template_context["agent_job"] = agent_job.to_summary()
            template_context.setdefault("export_payload", {})["agent_job"] = agent_job.to_summary()
        _persist_report_snapshot(request, template_context)

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


@router.patch("/api/review-items/{item_id}")
async def update_review_item(request: Request, item_id: str, payload: dict) -> JSONResponse:
    status = str(payload.get("status", "")).strip()
    if status not in REVIEW_STATUS_OPTIONS:
        raise HTTPException(status_code=400, detail="无效的复核状态。")

    review_store = getattr(request.app.state, "review_store", None)
    if review_store is None:
        review_store = ReviewStore(getattr(request.app.state, "db_path", BASE_DIR / "data" / "echopaper.db"))
    try:
        updated = review_store.update_item_status(item_id, status)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="复核项不存在。") from exc

    return JSONResponse({"ok": True, "item": updated})


@router.post("/api/reports/export-pdf")
async def export_report_pdf(request: Request, payload: dict) -> Response:
    pdf_result = build_report_pdf(payload)
    session_id = str(payload.get("review_session", {}).get("session_id", "")).strip()
    if session_id:
        review_store = getattr(request.app.state, "review_store", None)
        if review_store is None:
            review_store = ReviewStore(getattr(request.app.state, "db_path", BASE_DIR / "data" / "echopaper.db"))
        try:
            review_store.record_export(
                session_id=session_id,
                export_format="pdf",
                file_path=pdf_result.filename,
            )
        except Exception:
            # Export history is helpful bookkeeping; it should not block the file download.
            pass

    return Response(
        content=pdf_result.content,
        media_type="application/pdf",
        headers={
            "Content-Disposition": (
                f"attachment; filename=\"echopaper-report.pdf\"; "
                f"filename*=UTF-8''{quote(pdf_result.filename)}"
            ),
        },
    )


@router.get("/api/reports/{session_id}")
async def get_report_snapshot(request: Request, session_id: str) -> JSONResponse:
    review_store = _get_review_store(request)
    snapshot = review_store.get_report_snapshot(session_id)
    if snapshot is None:
        raise HTTPException(status_code=404, detail="报告快照不存在。")
    return JSONResponse(snapshot)


@router.get("/reports/{session_id}", response_class=HTMLResponse)
async def report_snapshot(request: Request, session_id: str) -> HTMLResponse:
    review_store = _get_review_store(request)
    snapshot = review_store.get_report_snapshot(session_id)
    if snapshot is None:
        raise HTTPException(status_code=404, detail="报告快照不存在。")

    return templates.TemplateResponse(
        request,
        "report_snapshot.html",
        _build_report_snapshot_context(snapshot),
    )


@router.get("/api/agent-jobs/{job_id}")
async def get_agent_job(request: Request, job_id: str) -> JSONResponse:
    job_store = _get_agent_job_store(request)
    job = job_store.get(job_id)
    if job is None:
        review_store = getattr(request.app.state, "review_store", None)
        if review_store is None:
            review_store = ReviewStore(getattr(request.app.state, "db_path", BASE_DIR / "data" / "echopaper.db"))
        persisted_job = review_store.get_agent_job(job_id)
        if persisted_job is not None:
            return JSONResponse(persisted_job)
        raise HTTPException(status_code=404, detail="Agent 任务不存在。")

    payload = job.to_summary()
    if job.result is not None:
        payload["result"] = pipeline_result_summary(job.result)
        payload["result_payload"] = pipeline_result_payload(job.result)
    return JSONResponse(payload)


def _attach_review_persistence(
    request: Request,
    template_context: dict,
    *,
    teacher_id: str,
    teacher_name: str,
    subject: str,
    code_run_result,
) -> None:
    review_store = _get_review_store(request)

    paper_by_id = {paper.paper_id: paper for paper in code_run_result.uploaded_papers}
    session_id = review_store.create_session(
        teacher_id=teacher_id,
        teacher_name=teacher_name,
        subject=subject,
        paper_a_path=getattr(paper_by_id.get("A"), "temp_path", None),
        paper_b_path=getattr(paper_by_id.get("B"), "temp_path", None),
    )
    item_ids = review_store.create_items(session_id, code_run_result.similarity_matches)

    for row in template_context.get("duplicate_rows", []):
        item_id = item_ids.get(row.get("match_id"))
        row["review_session_id"] = session_id
        row["review_item_id"] = item_id

    template_context["review_session_id"] = session_id
    template_context.setdefault("export_payload", {})["review_session"] = {
        "session_id": session_id,
        "review_item_count": len(item_ids),
    }


def _persist_report_snapshot(request: Request, template_context: dict) -> None:
    export_payload = template_context.get("export_payload")
    if not isinstance(export_payload, dict):
        return

    session_id = str(export_payload.get("review_session", {}).get("session_id", "")).strip()
    if not session_id:
        return

    review_store = _get_review_store(request)
    review_store.upsert_report_snapshot(session_id, export_payload)


def _get_review_store(request: Request) -> ReviewStore:
    review_store = getattr(request.app.state, "review_store", None)
    if review_store is not None:
        return review_store

    review_store = ReviewStore(getattr(request.app.state, "db_path", BASE_DIR / "data" / "echopaper.db"))
    request.app.state.review_store = review_store
    return review_store


def _build_report_snapshot_context(snapshot: dict) -> dict:
    payload = snapshot.get("payload", {}) if isinstance(snapshot, dict) else {}
    report = payload.get("report", {}) if isinstance(payload, dict) else {}
    dashboard = report.get("dashboard", {}) if isinstance(report, dict) else {}
    duplicate = payload.get("duplicate_comparison", {}) if isinstance(payload, dict) else {}
    spellcheck = payload.get("spellcheck_comparison", {}) if isinstance(payload, dict) else {}
    agent_job = payload.get("agent_job", {}) if isinstance(payload, dict) else {}
    agent_payload = payload.get("agent_result_payload", {}) if isinstance(payload, dict) else {}

    return {
        "session_id": snapshot.get("session_id", ""),
        "created_at": snapshot.get("created_at", ""),
        "updated_at": snapshot.get("updated_at", ""),
        "payload": payload,
        "report": report,
        "dashboard": dashboard,
        "uploaded_papers": report.get("uploaded_papers", []) if isinstance(report, dict) else [],
        "history_bank": payload.get("history_bank", {}) if isinstance(payload, dict) else {},
        "dual_run_sections": payload.get("dual_run_sections", []) if isinstance(payload, dict) else [],
        "question_quality": payload.get("question_quality", []) if isinstance(payload, dict) else [],
        "duplicate_summary": duplicate.get("summary", {}) if isinstance(duplicate, dict) else {},
        "duplicate_rows": duplicate.get("code_rows", []) if isinstance(duplicate, dict) else [],
        "spellcheck_summary": spellcheck.get("summary", {}) if isinstance(spellcheck, dict) else {},
        "spellcheck_rows": spellcheck.get("code_rows", []) if isinstance(spellcheck, dict) else [],
        "agent_job": agent_job,
        "agent_payload": agent_payload,
        "agent_questions": agent_payload.get("questions", []) if isinstance(agent_payload, dict) else [],
        "agent_matches": agent_payload.get("similarity_matches", []) if isinstance(agent_payload, dict) else [],
        "agent_issues": agent_payload.get("spellcheck_issues", []) if isinstance(agent_payload, dict) else [],
    }


def _get_history_bank_service(request: Request) -> HistoryBankService:
    service = getattr(request.app.state, "history_bank_service", None)
    if service is not None:
        return service

    history_bank_dir = getattr(request.app.state, "history_bank_dir", BASE_DIR / "data" / "datasets" / "history_bank")
    service = HistoryBankService(
        history_bank_dir,
        extraction_provider=RoutedPdfParser(ocr_provider=build_ocr_provider_from_env()),
        index_dir=getattr(request.app.state, "index_dir", BASE_DIR / "data" / "index"),
    )
    request.app.state.history_bank_service = service
    return service


def _get_agent_job_store(request: Request) -> AgentJobStore:
    store = getattr(request.app.state, "agent_job_store", None)
    if store is not None:
        return store

    agent_job_dir = ensure_directory(getattr(request.app.state, "agent_job_dir", BASE_DIR / "data" / "agent_jobs"))
    review_store = getattr(request.app.state, "review_store", None)
    if review_store is None:
        review_store = ReviewStore(getattr(request.app.state, "db_path", BASE_DIR / "data" / "echopaper.db"))
    store = AgentJobStore(agent_job_dir, review_store=review_store)
    request.app.state.agent_job_store = store
    return store


async def _save_history_bank_upload(upload: UploadFile, destination_dir: Path) -> Path:
    ensure_directory(destination_dir)
    destination = _unique_history_bank_path(destination_dir, upload.filename or "history.pdf")
    with destination.open("wb") as file_handle:
        while True:
            chunk = await upload.read(1024 * 1024)
            if not chunk:
                break
            file_handle.write(chunk)
    await upload.close()
    return destination


def _unique_history_bank_path(destination_dir: Path, filename: str) -> Path:
    safe_name = _safe_history_filename(filename)
    candidate = destination_dir / safe_name
    if not candidate.exists():
        return candidate

    stem = candidate.stem
    suffix = candidate.suffix or ".pdf"
    index = 2
    while True:
        candidate = destination_dir / f"{stem}_{index}{suffix}"
        if not candidate.exists():
            return candidate
        index += 1


def _safe_history_filename(filename: str) -> str:
    name = Path(filename).name.strip() or "history.pdf"
    stem = Path(name).stem.strip() or "history"
    suffix = Path(name).suffix.lower() or ".pdf"
    if suffix != ".pdf":
        suffix = ".pdf"
    safe_stem = "".join(char if char.isalnum() or char in {"-", "_", " ", "+", "(", ")", "（", "）"} else "_" for char in stem)
    safe_stem = safe_stem.strip(" ._") or "history"
    return f"{safe_stem}{suffix}"


def _resolve_history_bank_pdf_path(history_bank_dir: Path, relative_path: str) -> Path:
    root = history_bank_dir.resolve()
    relative = Path(relative_path)
    if relative.is_absolute() or ".." in relative.parts:
        raise ValueError("无效的历史题库文件路径。")

    target = (root / relative).resolve()
    if target.parent != root and root not in target.parents:
        raise ValueError("只能删除历史题库目录内的 PDF 文件。")
    if target.suffix.lower() != ".pdf":
        raise ValueError("只能删除历史题库 PDF 文件。")
    if not target.exists():
        raise FileNotFoundError("历史题库文件不存在，可能已被删除。")
    if not target.is_file():
        raise ValueError("只能删除文件，不能删除目录。")
    return target
