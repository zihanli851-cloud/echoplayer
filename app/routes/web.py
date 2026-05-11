from __future__ import annotations

from datetime import datetime
from pathlib import Path
import json
from urllib.parse import quote

from fastapi import APIRouter, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, Response
from fastapi.templating import Jinja2Templates

from app.models.schemas import UploadedPaper
from app.services.comparator import CodeSimilarityComparator
from app.services.document_parser import DocumentParseError, RoutedDocumentParser
from app.services.review_pipeline import ReviewPipeline
from app.services.history_bank import GENERIC_HISTORY_PARENT_NAMES, HistoryBankService
from app.services.history_bank_jobs import HistoryBankRefreshJobStore
from app.services.ocr import build_ocr_provider_from_env
from app.services.pdf_parser import PdfParseError, RoutedPdfParser
from app.services.question_splitter import RuleQuestionSplitter
from app.services.report_builder import ReportBuilder
from app.services.report_pdf import build_report_pdf
from app.services.review_store import REVIEW_STATUS_OPTIONS, ReviewStore
from app.services.spellcheck.local_provider import LocalSpellcheckProvider
from app.utils.file_manager import cleanup_processing_dir, create_processing_dir, ensure_directory, save_upload_file


BASE_DIR = Path(__file__).resolve().parent.parent.parent
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))
router = APIRouter()


ALL_SUBJECT_VALUE = "__all__"
ALL_SUBJECT_LABEL = "全科目匹配"

LEGACY_SUBJECT_OPTIONS = [
    ("chinese", "语文"),
    ("math", "数学"),
    ("english", "英语"),
    ("physics", "物理"),
    ("chemistry", "化学"),
    ("politics", "政治"),
    ("history", "历史"),
    ("geography", "地理"),
]


def is_pdf_file(upload: UploadFile | None) -> bool:
    if upload is None:
        return False
    filename = (upload.filename or "").lower()
    content_type = (upload.content_type or "").lower()
    return filename.endswith(".pdf") or content_type == "application/pdf"


def is_review_document(upload: UploadFile | None) -> bool:
    if upload is None:
        return False
    filename = (upload.filename or "").lower()
    content_type = (upload.content_type or "").lower()
    return (
        filename.endswith(".pdf")
        or filename.endswith(".docx")
        or content_type == "application/pdf"
        or content_type == "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    )


def _render_template(
    request: Request,
    template_name: str,
    context: dict | None = None,
    *,
    status_code: int = 200,
) -> HTMLResponse:
    payload = {
        "app_name": "EchoPaper",
        "page_type": template_name.replace(".html", ""),
        "current_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }
    if context:
        payload.update(context)
    return templates.TemplateResponse(request, template_name, payload, status_code=status_code)


def render_index(
    request: Request,
    *,
    form_data: dict,
    message: str = "",
    message_type: str = "",
    result: dict | None = None,
    status_code: int = 200,
) -> HTMLResponse:
    return _render_template(
        request,
        "index.html",
        {
            "subject_options": _get_upload_subject_options(request),
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
            "subject": ALL_SUBJECT_VALUE,
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
    review_store = _get_review_store(request)
    history_bank_jobs = review_store.list_history_bank_jobs(limit=5)

    return _render_template(
        request,
        "history_bank.html",
        {
            "summary": summary,
            "history_bank_jobs": history_bank_jobs,
            "message": message,
            "message_type": message_type,
        },
    )


@router.post("/history-bank/upload", response_class=HTMLResponse)
async def upload_history_bank(
    request: Request,
    files: list[UploadFile] = File(...),
) -> HTMLResponse:
    history_bank_dir = ensure_directory(
        getattr(request.app.state, "history_bank_dir", BASE_DIR / "data" / "datasets" / "history_bank")
    )
    saved_count = 0
    skipped_names: list[str] = []

    for upload in files:
        if not is_pdf_file(upload):
            skipped_names.append(upload.filename or "未命名文件")
            await upload.close()
            continue
        await _save_history_bank_upload(upload, history_bank_dir)
        saved_count += 1

    service = getattr(request.app.state, "history_bank_service", None)
    if saved_count and service is not None and hasattr(service, "invalidate_cache"):
        service.invalidate_cache()

    if saved_count:
        message = f"已上传 {saved_count} 份历史题库 PDF。"
        message_type = "success"
    else:
        message = "未上传有效 PDF 文件。"
        message_type = "error"
    if skipped_names:
        message = f"{message} 已跳过非 PDF 文件：{', '.join(skipped_names)}。"

    return await history_bank(request, refresh=False, message=message, message_type=message_type)


@router.post("/history-bank/delete", response_class=HTMLResponse)
async def delete_history_bank_file(
    request: Request,
    relative_path: str = Form(...),
    subject: str = Form(""),
    q: str = Form(""),
) -> HTMLResponse:
    history_bank_dir = ensure_directory(
        getattr(request.app.state, "history_bank_dir", BASE_DIR / "data" / "datasets" / "history_bank")
    )
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

    return await history_bank(request, refresh=False, subject=subject, q=q, message=message, message_type=message_type)


@router.post("/history-bank/rebuild")
async def rebuild_history_bank(request: Request) -> JSONResponse:
    history_bank_service = _get_history_bank_service(request)
    job_store = _get_history_bank_job_store(request)
    job = job_store.submit(history_bank_service)
    return JSONResponse(job.to_summary())


@router.get("/api/history-bank/jobs/{job_id}")
async def get_history_bank_job(request: Request, job_id: str) -> JSONResponse:
    job_store = _get_history_bank_job_store(request)
    job = job_store.get(job_id)
    if job is None:
        persisted_job = _get_review_store(request).get_history_bank_job(job_id)
        if persisted_job is not None:
            return JSONResponse(persisted_job)
        raise HTTPException(status_code=404, detail="历史题库任务不存在。")
    return JSONResponse(job.to_summary())


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
    subject_options = _get_upload_subject_options(request)
    subject_values = {value for value, _label in subject_options}
    if subject not in subject_values:
        form_data["subject"] = ALL_SUBJECT_VALUE
        return render_index(
            request,
            form_data=form_data,
            message="请选择历史库中已有的科目，或使用全科目匹配。",
            message_type="error",
            status_code=400,
        )
    selected_subject = "" if subject == ALL_SUBJECT_VALUE else subject
    subject_label = ALL_SUBJECT_LABEL if subject == ALL_SUBJECT_VALUE else subject

    if not form_data["teacher_name"] or not form_data["teacher_id"]:
        return render_index(
            request,
            form_data=form_data,
            message="教师姓名和教师工号不能为空。",
            message_type="error",
            status_code=400,
        )

    if not is_review_document(paper_a):
        return render_index(
            request,
            form_data=form_data,
            message="A 卷必须为 PDF 或 DOCX 文件。",
            message_type="error",
            status_code=400,
        )

    if paper_b and not is_review_document(paper_b):
        return render_index(
            request,
            form_data=form_data,
            message="B 卷必须为 PDF 或 DOCX 文件。",
            message_type="error",
            status_code=400,
        )

    processing_dir = create_processing_dir(request.app.state.temp_dir)

    try:
        saved_a = await save_upload_file(paper_a, processing_dir, "paper_a")
        saved_b = await save_upload_file(paper_b, processing_dir, "paper_b") if paper_b else None

        uploaded_papers = [
            UploadedPaper(
                paper_id="A",
                filename=paper_a.filename or saved_a.name,
                subject=selected_subject,
                temp_path=str(saved_a),
            )
        ]
        if saved_b and paper_b:
            uploaded_papers.append(
                UploadedPaper(
                    paper_id="B",
                    filename=paper_b.filename or saved_b.name,
                    subject=selected_subject,
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

        ocr_provider = build_ocr_provider_from_env()
        pipeline = ReviewPipeline(
            pipeline_name="代码流",
            extraction_provider=RoutedDocumentParser(pdf_parser=RoutedPdfParser(ocr_provider=ocr_provider)),
            split_provider=RuleQuestionSplitter(),
            compare_provider=CodeSimilarityComparator(),
            spellcheck_provider=LocalSpellcheckProvider(),
        )
        run_result = pipeline.run(
            uploaded_papers,
            history_questions=history_questions,
            history_bank_summary=history_bank_summary,
        )

        report_builder = ReportBuilder()
        report = report_builder.build_report(
            teacher_name=form_data["teacher_name"],
            teacher_id=form_data["teacher_id"],
            subject=subject_label,
            uploaded_papers=run_result.uploaded_papers,
            questions=run_result.questions,
            similarity_matches=run_result.similarity_matches,
            spellcheck_issues=run_result.spellcheck_issues,
        )
        template_context = report_builder.build_template_context(report, code_run_result=run_result)
        _attach_review_persistence(
            request,
            template_context,
            teacher_id=form_data["teacher_id"],
            teacher_name=form_data["teacher_name"],
            subject=subject_label,
            code_run_result=run_result,
        )
        _persist_report_snapshot(request, template_context)

        return _render_template(request, "report.html", template_context)
    except (PdfParseError, DocumentParseError) as exc:
        return render_index(request, form_data=form_data, message=str(exc), message_type="error", status_code=400)
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

    review_store = _get_review_store(request)
    try:
        updated = review_store.update_item_status(item_id, status)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="复核项不存在。") from exc

    item = review_store.get_item(item_id)
    if item is not None:
        review_store.update_report_snapshot_review_status(
            session_id=item["session_id"],
            item_id=item_id,
            match_id=item["match_id"],
            status=status,
        )

    return JSONResponse({"ok": True, "item": updated})


@router.post("/api/reports/export-pdf")
async def export_report_pdf(request: Request, payload: dict) -> Response:
    pdf_result = build_report_pdf(payload)
    _record_report_export(request, payload, export_format="pdf", file_path=pdf_result.filename)
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


@router.post("/api/reports/export-json")
async def export_report_json(request: Request, payload: dict) -> Response:
    filename = "echopaper-report.json"
    _record_report_export(request, payload, export_format="json", file_path=filename)
    content = json.dumps(payload, ensure_ascii=False, indent=2)
    return Response(
        content=content,
        media_type="application/json; charset=utf-8",
        headers={
            "Content-Disposition": (
                f"attachment; filename=\"{filename}\"; "
                f"filename*=UTF-8''{quote(filename)}"
            ),
        },
    )


@router.get("/reports", response_class=HTMLResponse)
async def report_snapshots(
    request: Request,
    limit: int = Query(50, ge=1, le=200),
    subject: str = Query(""),
    q: str = Query(""),
) -> HTMLResponse:
    review_store = _get_review_store(request)
    snapshots = review_store.list_report_snapshots(limit=limit, subject=subject, keyword=q)
    return _render_template(
        request,
        "reports.html",
        {
            "snapshots": snapshots,
            "limit": limit,
            "active_subject": subject,
            "active_keyword": q,
            "subject_options": _get_report_filter_subject_options(request),
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

    return _render_template(
        request,
        "report_snapshot.html",
        _build_report_snapshot_context(
            snapshot,
            export_history=review_store.list_export_history(session_id, limit=10),
        ),
    )


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

    export_duplicate_rows = (
        template_context.setdefault("export_payload", {})
        .setdefault("duplicate_comparison", {})
        .setdefault("code_rows", [])
    )
    for row in export_duplicate_rows:
        if not isinstance(row, dict):
            continue
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
    _get_review_store(request).upsert_report_snapshot(session_id, export_payload)


def _record_report_export(request: Request, payload: dict, *, export_format: str, file_path: str) -> None:
    session_id = str(payload.get("review_session", {}).get("session_id", "")).strip()
    if not session_id:
        return
    try:
        _get_review_store(request).record_export(
            session_id=session_id,
            export_format=export_format,
            file_path=file_path,
        )
    except Exception:
        pass


def _build_report_snapshot_context(snapshot: dict, *, export_history: list[dict] | None = None) -> dict:
    payload = snapshot.get("payload", {}) if isinstance(snapshot, dict) else {}
    report = payload.get("report", {}) if isinstance(payload, dict) else {}
    dashboard = report.get("dashboard", {}) if isinstance(report, dict) else {}
    duplicate = payload.get("duplicate_comparison", {}) if isinstance(payload, dict) else {}
    spellcheck = payload.get("spellcheck_comparison", {}) if isinstance(payload, dict) else {}
    duplicate_rows = duplicate.get("code_rows", []) if isinstance(duplicate, dict) else []
    spellcheck_rows = spellcheck.get("code_rows", []) if isinstance(spellcheck, dict) else []
    snapshot_navigation = [
        {"id": "snapshot-overview", "label": "总览", "count": len(report.get("uploaded_papers", []) if isinstance(report, dict) else [])},
        {"id": "snapshot-risks", "label": "风险提示", "count": len(payload.get("parse_quality", []) if isinstance(payload, dict) else [])},
        {"id": "snapshot-duplicates", "label": "重复题", "count": len(duplicate_rows)},
        {"id": "snapshot-spellcheck", "label": "错字问题", "count": len(spellcheck_rows)},
        {"id": "snapshot-exports", "label": "导出记录", "count": len(export_history or [])},
    ]

    return {
        "session_id": snapshot.get("session_id", ""),
        "created_at": snapshot.get("created_at", ""),
        "updated_at": snapshot.get("updated_at", ""),
        "export_history": export_history or [],
        "payload": payload,
        "report": report,
        "dashboard": dashboard,
        "uploaded_papers": report.get("uploaded_papers", []) if isinstance(report, dict) else [],
        "history_bank": payload.get("history_bank", {}) if isinstance(payload, dict) else {},
        "question_quality": payload.get("question_quality", []) if isinstance(payload, dict) else [],
        "duplicate_summary": duplicate.get("summary", {}) if isinstance(duplicate, dict) else {},
        "duplicate_rows": duplicate_rows,
        "spellcheck_summary": spellcheck.get("summary", {}) if isinstance(spellcheck, dict) else {},
        "spellcheck_rows": spellcheck_rows,
        "snapshot_navigation": snapshot_navigation,
    }


def _get_review_store(request: Request) -> ReviewStore:
    review_store = getattr(request.app.state, "review_store", None)
    if review_store is not None:
        return review_store
    review_store = ReviewStore(getattr(request.app.state, "db_path", BASE_DIR / "data" / "echopaper.db"))
    request.app.state.review_store = review_store
    return review_store


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


def _get_history_bank_job_store(request: Request) -> HistoryBankRefreshJobStore:
    store = getattr(request.app.state, "history_bank_job_store", None)
    if store is not None:
        return store
    store = HistoryBankRefreshJobStore(review_store=_get_review_store(request))
    request.app.state.history_bank_job_store = store
    return store


def _get_upload_subject_options(request: Request) -> list[tuple[str, str]]:
    subjects = _get_history_bank_subjects(request)
    options = [(subject, subject) for subject in subjects] or list(LEGACY_SUBJECT_OPTIONS)
    return [(ALL_SUBJECT_VALUE, ALL_SUBJECT_LABEL), *options]


def _get_report_filter_subject_options(request: Request) -> list[tuple[str, str]]:
    subjects = _get_history_bank_subjects(request)
    options = [(ALL_SUBJECT_LABEL, ALL_SUBJECT_LABEL)]
    options.extend((subject, subject) for subject in subjects)
    if len(options) == 1:
        options.extend(LEGACY_SUBJECT_OPTIONS)
    return options


def _get_history_bank_subjects(request: Request) -> list[str]:
    history_bank_dir = Path(getattr(request.app.state, "history_bank_dir", BASE_DIR / "data" / "datasets" / "history_bank"))
    subjects = _list_history_subject_folders(history_bank_dir)
    if subjects:
        return subjects
    service = getattr(request.app.state, "history_bank_service", None)
    if service is None:
        return []
    try:
        snapshot = (
            service.get_cached_or_directory_summary()
            if hasattr(service, "get_cached_or_directory_summary")
            else service.get_snapshot(force_refresh=False)
        )
        summary = snapshot.filtered_summary() if hasattr(snapshot, "filtered_summary") else snapshot.to_summary()
    except Exception:
        return []
    values = [
        str(subject).strip()
        for subject in summary.get("subjects", [])
        if str(subject).strip() and str(subject).strip().lower() != "unknown"
    ]
    return _dedupe_sorted_subjects(values)


def _list_history_subject_folders(history_bank_dir: Path) -> list[str]:
    roots = [path for path in (history_bank_dir / "txt", history_bank_dir / "pdf") if path.exists()]
    if not roots and history_bank_dir.exists():
        roots = [history_bank_dir]

    subjects: list[str] = []
    for root in roots:
        try:
            children = list(root.iterdir())
        except OSError:
            continue
        for child in children:
            if child.is_dir():
                name = child.name.strip()
                if name and name.lower() not in GENERIC_HISTORY_PARENT_NAMES:
                    subjects.append(name)
    return _dedupe_sorted_subjects(subjects)


def _dedupe_sorted_subjects(values: list[str]) -> list[str]:
    by_key: dict[str, str] = {}
    for value in values:
        by_key.setdefault(value.casefold(), value)
    return sorted(by_key.values(), key=lambda item: item.casefold())


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
    safe_stem = "".join(char if char.isalnum() or char in {"-", "_", " ", "+", "(", ")"} else "_" for char in stem)
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
