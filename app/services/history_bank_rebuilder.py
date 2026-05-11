from __future__ import annotations

from dataclasses import dataclass, asdict
from datetime import datetime
import json
from pathlib import Path
import shutil

from app.models.schemas import UploadedPaper
from app.services.history_bank_export import HistoryBankExportRecord, export_pdf_to_txt, infer_subject
from app.services.history_bank import build_source_key
from app.services.ocr import build_ocr_provider_from_env
from app.services.pdf_parser import PdfParseError, RoutedPdfParser, TextExtractionProvider
from app.services.question_splitter import RuleQuestionSplitter, normalize_formula_glyphs


@dataclass(slots=True)
class HistoryRebuildRecord:
    source_key: str
    source_pdf: str
    target_txt: str
    backup_txt: str | None
    question_count: int
    page_count: int
    status: str
    error: str = ""

    def to_dict(self) -> dict[str, str | int]:
        return asdict(self)


@dataclass(slots=True)
class HistoryRebuildResult:
    run_id: str
    root_dir: str
    total_pdfs: int
    rebuilt: int
    failed: int
    backup_dir: str | None
    manifest_path: str
    records: list[HistoryRebuildRecord]

    def to_dict(self) -> dict:
        return {
            "run_id": self.run_id,
            "root_dir": self.root_dir,
            "total_pdfs": self.total_pdfs,
            "rebuilt": self.rebuilt,
            "failed": self.failed,
            "backup_dir": self.backup_dir,
            "manifest_path": self.manifest_path,
            "records": [record.to_dict() for record in self.records],
        }


def rebuild_history_bank_from_pdf(
    root_dir: Path,
    *,
    extraction_provider: TextExtractionProvider | None = None,
    split_provider: RuleQuestionSplitter | None = None,
    output_txt_dir: Path | None = None,
    backup_root_dir: Path | None = None,
    manifest_path: Path | None = None,
    dry_run: bool = False,
) -> HistoryRebuildResult:
    pdf_dir = root_dir / "pdf"
    txt_dir = output_txt_dir or (root_dir / "txt")
    run_id = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_dir = (backup_root_dir or (root_dir / "_rebuild_backups")) / run_id
    manifest_path = manifest_path or (backup_dir / "rebuild_manifest.json")

    pdf_files = sorted(pdf_dir.rglob("*.pdf"), key=lambda current: str(current).lower()) if pdf_dir.exists() else []
    records: list[HistoryRebuildRecord] = []
    if not dry_run:
        txt_dir.mkdir(parents=True, exist_ok=True)
        backup_dir.mkdir(parents=True, exist_ok=True)

    for index, pdf_path in enumerate(pdf_files, start=1):
        relative_pdf_path = pdf_path.relative_to(pdf_dir)
        target_txt_path = (txt_dir / relative_pdf_path).with_suffix(".txt")
        backup_txt_path = (backup_dir / relative_pdf_path).with_suffix(".txt")
        existing_txt = _find_existing_txt_by_source_key(txt_dir, build_source_key(pdf_path.stem))
        try:
            if existing_txt is not None and not dry_run:
                backup_txt_path.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(existing_txt, backup_txt_path)

            if dry_run:
                export_record = _preview_export_record(
                    pdf_path,
                    extraction_provider=extraction_provider,
                    split_provider=split_provider,
                    paper_id=f"H{index}",
                )
            else:
                export_record = export_pdf_to_txt(
                    pdf_path,
                    target_txt_path,
                    extraction_provider=extraction_provider,
                    split_provider=split_provider,
                    paper_id=f"H{index}",
                )
            records.append(
                HistoryRebuildRecord(
                    source_key=build_source_key(pdf_path.stem),
                    source_pdf=str(pdf_path),
                    target_txt=str(target_txt_path),
                    backup_txt=str(backup_txt_path) if existing_txt is not None else None,
                    question_count=export_record.question_count,
                    page_count=export_record.page_count,
                    status=export_record.status,
                    error=export_record.error,
                )
            )
        except Exception as exc:  # pragma: no cover
            records.append(
                HistoryRebuildRecord(
                    source_key=build_source_key(pdf_path.stem),
                    source_pdf=str(pdf_path),
                    target_txt=str(target_txt_path),
                    backup_txt=str(backup_txt_path) if existing_txt is not None else None,
                    question_count=0,
                    page_count=0,
                    status="failed",
                    error=str(exc),
                )
            )

    if not dry_run:
        _write_rebuild_manifest(records, manifest_path, run_id=run_id, root_dir=root_dir, backup_dir=backup_dir)

    rebuilt = sum(1 for record in records if record.status == "ok")
    failed = sum(1 for record in records if record.status != "ok")
    return HistoryRebuildResult(
        run_id=run_id,
        root_dir=str(root_dir),
        total_pdfs=len(pdf_files),
        rebuilt=rebuilt,
        failed=failed,
        backup_dir=None if dry_run else str(backup_dir),
        manifest_path=str(manifest_path),
        records=records,
    )


def _find_existing_txt_by_source_key(txt_dir: Path, source_key: str) -> Path | None:
    if not txt_dir.exists():
        return None
    for txt_path in txt_dir.rglob("*.txt"):
        if build_source_key(txt_path.stem) == source_key:
            return txt_path
    return None


def _preview_export_record(
    pdf_path: Path,
    *,
    extraction_provider: TextExtractionProvider | None,
    split_provider: RuleQuestionSplitter | None,
    paper_id: str,
) -> HistoryBankExportRecord:
    extraction_provider = extraction_provider or RoutedPdfParser(ocr_provider=build_ocr_provider_from_env())
    split_provider = split_provider or RuleQuestionSplitter()
    subject = infer_subject(pdf_path)

    try:
        text_content, page_count = extraction_provider.extract(pdf_path)
        text_content = normalize_formula_glyphs(text_content)
        paper = UploadedPaper(
            paper_id=paper_id,
            filename=pdf_path.name,
            subject=subject,
            temp_path=str(pdf_path),
            text_content=text_content,
            page_count=page_count,
        )
        questions = split_provider.split(text_content, paper_id, paper=paper)
    except (PdfParseError, Exception) as exc:
        return HistoryBankExportRecord(
            source_pdf=str(pdf_path),
            output_txt=None,
            paper_label=pdf_path.stem,
            subject=subject,
            page_count=0,
            question_count=0,
            status="failed",
            error=str(exc),
        )

    return HistoryBankExportRecord(
        source_pdf=str(pdf_path),
        output_txt=None,
        paper_label=pdf_path.stem,
        subject=subject,
        page_count=page_count,
        question_count=len(questions),
        status="ok",
    )


def _write_rebuild_manifest(
    records: list[HistoryRebuildRecord],
    manifest_path: Path,
    *,
    run_id: str,
    root_dir: Path,
    backup_dir: Path,
) -> None:
    payload = {
        "run_id": run_id,
        "root_dir": str(root_dir),
        "backup_dir": str(backup_dir),
        "total": len(records),
        "rebuilt": sum(1 for record in records if record.status == "ok"),
        "failed": sum(1 for record in records if record.status != "ok"),
        "items": [record.to_dict() for record in records],
    }
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
