"""
CLI entrypoint: extract Schedule P Part 1 unpaid Column 23 and Column 24 from PDFs.

Run from repository root::

    python -m app.main --input-dir ./input --output-dir ./output
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path
from typing import Dict, List

from app.config import load_settings
from app.core.constants import REQUIRED_PARTS
from app.services.excel_export import build_output_path, write_workbook
from app.services.pdf_extraction import extract_pdf_parts
from app.utils.filesystem import iter_pdfs


def _configure_logging(verbose: bool) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(levelname)s %(message)s",
        stream=sys.stdout,
    )


def log_validation(pdf_name: str, part_status: Dict[str, str], worksheets_created: int) -> None:
    data_parts = [part for part in REQUIRED_PARTS if part_status[part] == "data"]
    none_parts = [part for part in REQUIRED_PARTS if part_status[part] == "none"]
    missing_parts = [part for part in REQUIRED_PARTS if part_status[part] == "missing"]

    log = logging.getLogger(__name__)
    log.info("")
    log.info("PDF: %s", pdf_name)
    log.info(
        "Parts with extracted data (%d): %s",
        len(data_parts),
        ", ".join(data_parts) if data_parts else "None",
    )
    log.info(
        "Parts marked NONE (%d): %s",
        len(none_parts),
        ", ".join(none_parts) if none_parts else "None",
    )
    log.info(
        "Parts not found (%d): %s",
        len(missing_parts),
        ", ".join(missing_parts) if missing_parts else "None",
    )
    log.info("Total worksheets created: %d", worksheets_created)


def process_all(input_dir: Path, output_dir: Path, max_pdfs: int) -> int:
    """
    Process PDFs in ``input_dir`` and write Excel files to ``output_dir``.

    Returns:
        0 if at least one PDF was processed successfully, or if failures occurred but some succeeded;
        1 if no PDFs found or no successes (matching legacy script semantics for all-failure).
    """
    input_dir.mkdir(parents=True, exist_ok=True)
    output_dir.mkdir(parents=True, exist_ok=True)

    pdf_files = list(iter_pdfs(input_dir))
    if max_pdfs > 0:
        pdf_files = pdf_files[:max_pdfs]

    log = logging.getLogger(__name__)
    if not pdf_files:
        log.warning("No PDF files found in: %s", input_dir)
        return 1

    log.info("Input PDFs found: %d", len(pdf_files))

    success_count = 0
    failure_details: List[str] = []

    for index, pdf_path in enumerate(pdf_files, start=1):
        log.info("")
        log.info("[%d/%d] Processing: %s", index, len(pdf_files), pdf_path.name)
        try:
            part_rows, part_status, years_seen = extract_pdf_parts(pdf_path)
            output_path = build_output_path(output_dir, pdf_path)
            worksheets_created = write_workbook(output_path, part_rows, part_status, years_seen)
            log.info("Saved: %s", output_path)
            log_validation(pdf_path.name, part_status, worksheets_created)
            success_count += 1
        except Exception as exc:
            failure_details.append(f"{pdf_path.name}: {exc}")
            if log.isEnabledFor(logging.DEBUG):
                log.exception("FAILED: %s", pdf_path.name)
            else:
                log.error("FAILED: %s: %s", pdf_path.name, exc)

    log.info("")
    log.info("Processing complete.")
    log.info("Successful files: %d", success_count)
    log.info("Failed files: %d", len(failure_details))

    if failure_details:
        log.info("Failures:")
        for detail in failure_details:
            log.info(" - %s", detail)

    return 0 if success_count > 0 else 1


def parse_args(argv: List[str] | None = None) -> argparse.Namespace:
    defaults = load_settings()
    parser = argparse.ArgumentParser(
        description=(
            "Extract Schedule P Part 1 second-table (unpaid) Column 23 and Column 24 "
            "for all required sections from all PDFs in the input folder."
        )
    )
    parser.add_argument(
        "--input-dir",
        type=Path,
        default=defaults.input_dir,
        help="Folder containing PDF files (default: ./input or SCHEDULE_P_INPUT_DIR).",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=defaults.output_dir,
        help="Folder for Excel output files (default: ./output or SCHEDULE_P_OUTPUT_DIR).",
    )
    parser.add_argument(
        "--max-pdfs",
        type=int,
        default=0,
        help="Optional: process only the first N PDFs (0 means process all).",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Enable debug logging (includes tracebacks for failures).",
    )
    return parser.parse_args(argv)


def main(argv: List[str] | None = None) -> int:
    args = parse_args(argv)
    _configure_logging(args.verbose)
    return process_all(args.input_dir.resolve(), args.output_dir.resolve(), args.max_pdfs)


if __name__ == "__main__":
    raise SystemExit(main())
