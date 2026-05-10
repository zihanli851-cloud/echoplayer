from __future__ import annotations

import argparse
from pathlib import Path
import sys


BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from app.services.history_bank_rebuilder import rebuild_history_bank_from_pdf  # noqa: E402


DEFAULT_ROOT_DIR = BASE_DIR / "historicdatabase"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Rebuild historicdatabase/txt from historicdatabase/pdf with backup and manifest output.",
    )
    parser.add_argument(
        "--root-dir",
        default=str(DEFAULT_ROOT_DIR),
        help="History bank root directory. Defaults to ./historicdatabase.",
    )
    parser.add_argument(
        "--output-txt-dir",
        default="",
        help="Optional output txt directory. Defaults to <root-dir>/txt.",
    )
    parser.add_argument(
        "--backup-root-dir",
        default="",
        help="Optional backup root directory. Defaults to <root-dir>/_rebuild_backups.",
    )
    parser.add_argument(
        "--manifest-path",
        default="",
        help="Optional manifest path. Defaults to <backup-dir>/rebuild_manifest.json.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate extraction and splitting without writing txt, backup, or manifest files.",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    root_dir = Path(args.root_dir).resolve()

    if not root_dir.exists():
        print(f"History bank root does not exist: {root_dir}")
        return 1

    result = rebuild_history_bank_from_pdf(
        root_dir,
        output_txt_dir=Path(args.output_txt_dir).resolve() if args.output_txt_dir else None,
        backup_root_dir=Path(args.backup_root_dir).resolve() if args.backup_root_dir else None,
        manifest_path=Path(args.manifest_path).resolve() if args.manifest_path else None,
        dry_run=args.dry_run,
    )

    print(f"Root: {result.root_dir}")
    print(f"PDF total: {result.total_pdfs}")
    print(f"Rebuilt: {result.rebuilt}")
    print(f"Failed: {result.failed}")
    if result.backup_dir:
        print(f"Backup dir: {result.backup_dir}")
    print(f"Manifest: {result.manifest_path}")

    for record in result.records:
        if record.status != "ok":
            print(f"[FAILED] {record.source_pdf}: {record.error}")

    return 0 if result.failed == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
