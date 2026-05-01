"""Upload validation and PDF→Excel pipeline for web/UI callers."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, BinaryIO, Dict, List, MutableMapping, Optional

from app.core.constants import REQUIRED_PARTS
from app.core.user_messages import PDF_SCREENSHOT_FALLBACK_HINT
from app.services.excel_export import build_output_path, safe_filename_stem, write_workbook
from app.services.pdf_extraction import PdfProgressHook, extract_pdf_parts

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ExtractionResult:
    """Outcome for a single PDF processed through the extraction pipeline."""

    source_filename: str
    success: bool
    excel_filename: Optional[str]
    excel_bytes: Optional[bytes]
    error_message: Optional[str]
    part_status: Optional[Dict[str, str]]
    worksheets_created: int
    warn_message: Optional[str] = None


def pdf_header_is_valid(prefix: bytes) -> bool:
    """Return True if bytes begin with a PDF signature (``%PDF``)."""
    return len(prefix) >= 4 and prefix.startswith(b"%PDF")


def read_upload_prefix(stream: BinaryIO, n: int = 1024) -> bytes:
    """Read the first ``n`` bytes from a seekable stream and rewind to start."""
    stream.seek(0)
    prefix = stream.read(n)
    stream.seek(0)
    return prefix


def summarize_part_status(part_status: Dict[str, str]) -> Dict[str, int]:
    """Count parts by extraction status (data / none / missing)."""
    return {
        "data": sum(1 for p in REQUIRED_PARTS if part_status.get(p) == "data"),
        "none": sum(1 for p in REQUIRED_PARTS if part_status.get(p) == "none"),
        "missing": sum(1 for p in REQUIRED_PARTS if part_status.get(p) == "missing"),
    }


def _allocate_unique_pdf_path(temp_dir: Path, original_name: str, stem_counts: Dict[str, int]) -> Path:
    stem = safe_filename_stem(Path(original_name).stem)
    count = stem_counts.get(stem, 0)
    stem_counts[stem] = count + 1
    if count:
        stem = f"{stem}_{count}"
    return temp_dir / f"{stem}.pdf"


def extract_uploaded_pdf_to_excel(
    pdf_path: Path,
    original_filename: str,
    *,
    progress_hook: PdfProgressHook = None,
    timeout_seconds: Optional[float] = None,
    run_stats: Optional[MutableMapping[str, Any]] = None,
) -> ExtractionResult:
    """
    Run extraction for ``pdf_path`` and return Excel payload in memory.

    ``original_filename`` is used for logging/download naming context only.
    """
    try:
        part_rows, part_status, years_seen = extract_pdf_parts(
            pdf_path,
            progress_hook=progress_hook,
            timeout_seconds=timeout_seconds,
            run_stats=run_stats,
        )
        if progress_hook is not None:
            try:
                progress_hook(
                    "workbook",
                    {
                        "message": "Building Excel workbook",
                        "total_pages": (run_stats or {}).get("total_pages", 0),
                        "page_index": (run_stats or {}).get("pages_processed", 0),
                    },
                )
            except Exception:
                logger.debug("workbook progress_hook failed", exc_info=True)

        out_path = build_output_path(pdf_path.parent, pdf_path)
        worksheets_created = write_workbook(out_path, part_rows, part_status, years_seen)
        excel_bytes = out_path.read_bytes()
        excel_name = out_path.name
        try:
            out_path.unlink(missing_ok=True)
        except OSError:
            logger.warning("schedule_p.temp_excel_cleanup_failed path=%s", out_path)
        logger.info(
            "schedule_p.extraction.success file=%s worksheets=%s",
            original_filename,
            worksheets_created,
        )
        warn_message: Optional[str] = None
        if run_stats and run_stats.get("timed_out"):
            warn_message = (
                f"Stopped after {run_stats.get('pages_processed', 0)} of "
                f"{run_stats.get('total_pages', '?')} pages (time limit). "
                "Workbook may be incomplete — increase SCHEDULE_P_EXTRACTION_TIMEOUT_SEC or split the PDF."
            )
        return ExtractionResult(
            source_filename=original_filename,
            success=True,
            excel_filename=excel_name,
            excel_bytes=excel_bytes,
            error_message=None,
            part_status=dict(part_status),
            worksheets_created=worksheets_created,
            warn_message=warn_message,
        )
    except Exception as exc:
        logger.exception("schedule_p.extraction.failed file=%s", original_filename)
        return ExtractionResult(
            source_filename=original_filename,
            success=False,
            excel_filename=None,
            excel_bytes=None,
            error_message=str(exc) + PDF_SCREENSHOT_FALLBACK_HINT,
            part_status=None,
            worksheets_created=0,
            warn_message=None,
        )


def process_one_uploaded_pdf(
    temp_dir: Path,
    display_name: str,
    stream: BinaryIO,
    stem_counts: Dict[str, int],
    *,
    max_upload_mb: float,
    progress_hook: PdfProgressHook = None,
    timeout_seconds: Optional[float] = None,
) -> ExtractionResult:
    """
    Validate, persist, and extract a single uploaded PDF stream.

    The temporary PDF on disk is removed after extraction attempts (success or failure).
    """
    max_bytes = int(max_upload_mb * 1024 * 1024)
    prefix = read_upload_prefix(stream)
    if not pdf_header_is_valid(prefix):
        logger.info("schedule_p.upload.rejected file=%s reason=invalid_pdf_signature", display_name)
        return ExtractionResult(
            source_filename=display_name,
            success=False,
            excel_filename=None,
            excel_bytes=None,
            error_message=(
                "Not a valid PDF (missing %PDF signature). Rename does not change content."
                + PDF_SCREENSHOT_FALLBACK_HINT
            ),
            part_status=None,
            worksheets_created=0,
            warn_message=None,
        )

    stream.seek(0, 2)
    size = stream.tell()
    stream.seek(0)
    if size > max_bytes:
        logger.info(
            "schedule_p.upload.rejected file=%s reason=oversize size_bytes=%s limit_mb=%s",
            display_name,
            size,
            max_upload_mb,
        )
        limit_disp = int(max_upload_mb) if float(max_upload_mb).is_integer() else max_upload_mb
        return ExtractionResult(
            source_filename=display_name,
            success=False,
            excel_filename=None,
            excel_bytes=None,
            error_message=(
                f"File exceeds the configured limit of {limit_disp} MB per PDF. "
                "Ask your administrator to raise SCHEDULE_P_MAX_UPLOAD_MB and set Streamlit "
                "`server.maxUploadSize` (in .streamlit/config.toml) to at least that value."
                + PDF_SCREENSHOT_FALLBACK_HINT
            ),
            part_status=None,
            worksheets_created=0,
            warn_message=None,
        )

    dest = _allocate_unique_pdf_path(temp_dir, display_name, stem_counts)
    dest.write_bytes(stream.read())

    try:
        run_stats: Dict[str, Any] = {}
        return extract_uploaded_pdf_to_excel(
            dest,
            display_name,
            progress_hook=progress_hook,
            timeout_seconds=timeout_seconds,
            run_stats=run_stats,
        )
    finally:
        try:
            dest.unlink(missing_ok=True)
        except OSError:
            logger.warning("schedule_p.temp_pdf_cleanup_failed path=%s", dest)


def process_uploaded_pdf_files(
    temp_dir: Path,
    items: List[tuple[str, BinaryIO]],
    *,
    max_upload_mb: float,
) -> List[ExtractionResult]:
    """
    Validate and process uploaded PDF streams written under ``temp_dir``.

    Each item is ``(display_name, readable_stream)``. Streams must be seekable.
    """
    results: List[ExtractionResult] = []
    stem_counts: Dict[str, int] = {}
    for display_name, stream in items:
        results.append(
            process_one_uploaded_pdf(
                temp_dir,
                display_name,
                stream,
                stem_counts,
                max_upload_mb=max_upload_mb,
            )
        )
    return results
