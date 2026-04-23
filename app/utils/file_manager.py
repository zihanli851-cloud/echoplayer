import shutil
from collections.abc import Iterable
from pathlib import Path
from uuid import uuid4

from fastapi import UploadFile


def ensure_directory(path: Path) -> Path:
    """Create a directory if it does not already exist."""

    path.mkdir(parents=True, exist_ok=True)
    return path


def create_processing_dir(base_temp_dir: Path) -> Path:
    """Create one isolated processing directory for a single review request."""

    ensure_directory(base_temp_dir)
    processing_dir = base_temp_dir / f"session_{uuid4().hex}"
    processing_dir.mkdir(parents=True, exist_ok=True)
    return processing_dir


async def save_upload_file(upload: UploadFile, destination_dir: Path, prefix: str) -> Path:
    """Persist an uploaded file into the processing directory."""

    ensure_directory(destination_dir)
    suffix = Path(upload.filename or "").suffix or ".pdf"
    destination = destination_dir / f"{prefix}_{uuid4().hex}{suffix}"

    with destination.open("wb") as file_handle:
        while True:
            chunk = await upload.read(1024 * 1024)
            if not chunk:
                break
            file_handle.write(chunk)

    await upload.close()
    return destination


def delete_file(file_path: Path) -> None:
    """Delete one file if it exists."""

    if file_path.exists() and file_path.is_file():
        file_path.unlink()


def cleanup_paths(paths: Iterable[Path]) -> None:
    """Delete a collection of files or folders, ignoring already-missing paths."""

    for path in paths:
        if not path.exists():
            continue
        if path.is_dir():
            shutil.rmtree(path, ignore_errors=True)
        else:
            path.unlink(missing_ok=True)


def cleanup_processing_dir(processing_dir: Path) -> None:
    """Remove the whole processing directory and all intermediate files."""

    cleanup_paths([processing_dir])
