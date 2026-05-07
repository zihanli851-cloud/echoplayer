from pathlib import Path
import sqlite3
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.routes.coze import router as coze_router
from app.routes.nuwa import router as nuwa_router
from app.routes.web import router as web_router
from app.services.history_bank import HistoryBankService
from app.utils.file_manager import ensure_directory


BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
TEMP_DIR = DATA_DIR / "temp_uploads"
DATASET_DIR = DATA_DIR / "datasets"
HISTORY_BANK_DIR = DATASET_DIR / "history_bank"
DB_PATH = DATA_DIR / "echopaper.db"


def ensure_runtime_dirs() -> None:
    ensure_directory(DATA_DIR)
    ensure_directory(TEMP_DIR)
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


@asynccontextmanager
async def lifespan(app: FastAPI):
    ensure_runtime_dirs()
    ensure_database()
    app.state.base_dir = BASE_DIR
    app.state.data_dir = DATA_DIR
    app.state.temp_dir = TEMP_DIR
    app.state.dataset_dir = DATASET_DIR
    app.state.history_bank_dir = HISTORY_BANK_DIR
    app.state.history_bank_service = HistoryBankService(HISTORY_BANK_DIR)
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
