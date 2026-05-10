from pathlib import Path
import sqlite3
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.routes.coze import router as coze_router
from app.routes.nuwa import router as nuwa_router
from app.routes.web import router as web_router
from app.services.agent_jobs import AgentJobStore
from app.services.history_bank import HistoryBankService
from app.services.history_bank_jobs import HistoryBankRefreshJobStore
from app.services.ocr import build_ocr_provider_from_env
from app.services.pdf_parser import RoutedPdfParser
from app.services.review_store import ReviewStore
from app.utils.file_manager import ensure_directory


BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
TEMP_DIR = DATA_DIR / "temp_uploads"
AGENT_JOB_DIR = DATA_DIR / "agent_jobs"
INDEX_DIR = DATA_DIR / "index"
DATASET_DIR = DATA_DIR / "datasets"
HISTORY_BANK_DIR = DATASET_DIR / "history_bank"
DB_PATH = DATA_DIR / "echopaper.db"
DEFAULT_AGENT_JOB_RETENTION_SECONDS = 7 * 24 * 60 * 60


def ensure_runtime_dirs() -> None:
    ensure_directory(DATA_DIR)
    ensure_directory(TEMP_DIR)
    ensure_directory(AGENT_JOB_DIR)
    ensure_directory(INDEX_DIR)
    ensure_directory(DATASET_DIR)
    ensure_directory(HISTORY_BANK_DIR)


def ensure_database() -> None:
    with sqlite3.connect(DB_PATH) as connection:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS app_meta (
                key TEXT PRIMARY KEY,
                value TEXT
            )
            """
        )
        connection.commit()
    ReviewStore(DB_PATH).initialize()


def get_agent_job_retention_seconds() -> int | None:
    raw_value = os.getenv("AGENT_JOB_RETENTION_SECONDS", "").strip()
    if not raw_value:
        return DEFAULT_AGENT_JOB_RETENTION_SECONDS
    if raw_value.lower() in {"none", "off", "disabled"}:
        return None
    try:
        return max(0, int(raw_value))
    except ValueError:
        return DEFAULT_AGENT_JOB_RETENTION_SECONDS


@asynccontextmanager
async def lifespan(app: FastAPI):
    ensure_runtime_dirs()
    ensure_database()
    review_store = ReviewStore(DB_PATH)
    app.state.base_dir = BASE_DIR
    app.state.data_dir = DATA_DIR
    app.state.temp_dir = TEMP_DIR
    app.state.agent_job_dir = AGENT_JOB_DIR
    app.state.agent_job_store = AgentJobStore(
        AGENT_JOB_DIR,
        review_store=review_store,
        retention_seconds=get_agent_job_retention_seconds(),
    )
    app.state.index_dir = INDEX_DIR
    app.state.dataset_dir = DATASET_DIR
    app.state.history_bank_dir = HISTORY_BANK_DIR
    app.state.history_bank_service = HistoryBankService(
        HISTORY_BANK_DIR,
        extraction_provider=RoutedPdfParser(ocr_provider=build_ocr_provider_from_env()),
        index_dir=INDEX_DIR,
    )
    app.state.history_bank_job_store = HistoryBankRefreshJobStore(review_store=review_store)
    app.state.review_store = review_store
    app.state.db_path = DB_PATH
    yield


app = FastAPI(
    title="EchoPaper",
    description="试卷智能审查系统 MVP 骨架",
    version="0.1.0",
    lifespan=lifespan,
)


app.include_router(web_router)
app.include_router(coze_router)
app.include_router(nuwa_router)


static_dir = BASE_DIR / "static"
if static_dir.exists():
    app.mount("/static", StaticFiles(directory=static_dir), name="static")
