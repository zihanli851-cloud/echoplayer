from __future__ import annotations

import argparse
from pathlib import Path
import sys


BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from app.services.coze_export import (  # noqa: E402
    DEFAULT_NL_TOKEN,
    export_pdf_to_coze_txt,
    export_pdf_tree_to_coze_txt,
    write_manifest,
)
from app.services.ocr import build_ocr_provider_from_env  # noqa: E402
from app.services.pdf_parser import RoutedPdfParser  # noqa: E402

DEFAULT_INPUT_DIR = BASE_DIR / "data" / "datasets" / "history_bank"
DEFAULT_OUTPUT_DIR = BASE_DIR / "data" / "exports" / "coze_history_bank"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Export history-bank PDFs into Coze-friendly one-question-per-line text files.",
    )
    parser.add_argument(
        "input_path",
        nargs="?",
        default=str(DEFAULT_INPUT_DIR),
        help="Source PDF file or directory. Defaults to data/datasets/history_bank.",
    )
    parser.add_argument(
        "--output-dir",
        default=str(DEFAULT_OUTPUT_DIR),
        help="Directory to receive generated .coze.txt files.",
    )
    parser.add_argument("--subject", default="", help="Optional fixed subject override.")
    parser.add_argument(
        "--nl-token",
        default=DEFAULT_NL_TOKEN,
        help="Token used to replace original line breaks inside one question.",
    )
    parser.add_argument(
        "--manifest-name",
        default="manifest.json",
        help="Manifest filename written under the output directory.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=0,
        help="Export only the first N PDFs. Useful for a quick smoke test.",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    input_path = Path(args.input_path).resolve()
    output_dir = Path(args.output_dir).resolve()
    subject_override = args.subject.strip() or None
    extraction_provider = RoutedPdfParser(ocr_provider=build_ocr_provider_from_env())

    if not input_path.exists():
        print(f"Input path does not exist: {input_path}")
        return 1

    if input_path.is_file():
        output_file = output_dir / f"{input_path.stem}.coze.txt"
        records = [
            export_pdf_to_coze_txt(
                input_path,
                output_file,
                extraction_provider=extraction_provider,
                subject_override=subject_override,
                paper_id="H1",
                nl_token=args.nl_token,
            )
        ]
    else:
        records = export_pdf_tree_to_coze_txt(
            input_path,
            output_dir,
            extraction_provider=extraction_provider,
            subject_override=subject_override,
            nl_token=args.nl_token,
            limit=args.limit if args.limit > 0 else None,
            progress_callback=lambda index, total, path: print(
                f"[{index}/{total}] {path.name}",
                flush=True,
            ),
        )

    manifest_path = output_dir / args.manifest_name
    write_manifest(records, manifest_path)

    success_count = sum(1 for record in records if record.status == "ok")
    fail_count = len(records) - success_count

    print(f"Input: {input_path}")
    print(f"Output: {output_dir}")
    print(f"Generated: {success_count}")
    print(f"Failed: {fail_count}")
    print(f"Manifest: {manifest_path}")

    for record in records:
        if record.status != "ok":
            print(f"[FAILED] {record.source_pdf}: {record.error}")

    return 0 if fail_count == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
