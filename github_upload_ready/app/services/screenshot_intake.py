"""
Screenshot / image intake for manual-review fallback (no OCR or vision extraction).

Accepted formats: PNG and JPEG (magic-byte validated). Images are validated, retained in
memory for session download packaging only — Schedule P is **not** auto-extracted from images.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import BinaryIO, List, Optional

from app.services.upload_processing import read_upload_prefix

logger = logging.getLogger(__name__)

# Product honesty flag — wire future OCR/vision here when implemented.
AUTOMATED_SCREENSHOT_EXTRACTION_ENABLED = False


@dataclass(frozen=True)
class ScreenshotIntakeResult:
    """Outcome for one screenshot after validation (bytes kept for ZIP export only)."""

    source_filename: str
    success: bool
    error_message: Optional[str]
    image_bytes: Optional[bytes]


def image_magic_is_valid(prefix: bytes) -> bool:
    """Return True if bytes start with PNG or JPEG signatures."""
    if len(prefix) >= 8 and prefix.startswith(b"\x89PNG\r\n\x1a\n"):
        return True
    if len(prefix) >= 3 and prefix.startswith(b"\xff\xd8\xff"):
        return True
    return False


def allowed_image_extension(name: str) -> bool:
    lower = name.lower().strip()
    return lower.endswith(".png") or lower.endswith(".jpg") or lower.endswith(".jpeg")


def process_one_screenshot_upload(
    display_name: str,
    stream: BinaryIO,
    *,
    max_upload_mb: float,
) -> ScreenshotIntakeResult:
    """
    Validate image type/size and read bytes into memory.

    Does not perform OCR or table detection.
    """
    if not allowed_image_extension(display_name):
        return ScreenshotIntakeResult(
            source_filename=display_name,
            success=False,
            error_message="Filename must end with .png, .jpg, or .jpeg.",
            image_bytes=None,
        )

    max_bytes = int(max_upload_mb * 1024 * 1024)
    prefix = read_upload_prefix(stream, n=32)
    if not image_magic_is_valid(prefix):
        logger.info("schedule_screenshot.upload.rejected file=%s reason=invalid_magic", display_name)
        return ScreenshotIntakeResult(
            source_filename=display_name,
            success=False,
            error_message="Not a valid PNG or JPEG file (header check failed).",
            image_bytes=None,
        )

    stream.seek(0, 2)
    size = stream.tell()
    stream.seek(0)
    if size > max_bytes:
        limit_disp = int(max_upload_mb) if float(max_upload_mb).is_integer() else max_upload_mb
        logger.info(
            "schedule_screenshot.upload.rejected file=%s reason=oversize size=%s",
            display_name,
            size,
        )
        return ScreenshotIntakeResult(
            source_filename=display_name,
            success=False,
            error_message=(
                f"Image exceeds the configured limit of {limit_disp} MB per file "
                "(same cap as PDF uploads)."
            ),
            image_bytes=None,
        )

    data = stream.read()
    logger.info(
        "schedule_screenshot.upload.accepted file=%s bytes=%s automation=%s",
        display_name,
        len(data),
        AUTOMATED_SCREENSHOT_EXTRACTION_ENABLED,
    )
    return ScreenshotIntakeResult(
        source_filename=display_name,
        success=True,
        error_message=None,
        image_bytes=data,
    )


def process_screenshot_batch(
    items: List[tuple[str, BinaryIO]],
    *,
    max_upload_mb: float,
) -> List[ScreenshotIntakeResult]:
    """Validate and buffer each uploaded image."""
    return [process_one_screenshot_upload(name, stream, max_upload_mb=max_upload_mb) for name, stream in items]


def screenshot_row_payload(r: ScreenshotIntakeResult) -> dict[str, object]:
    """JSON-safe summary for the technical panel."""
    return {
        "source_filename": r.source_filename,
        "success": r.success,
        "error_message": r.error_message,
        "image_bytes_length": len(r.image_bytes) if r.image_bytes else 0,
        "manual_review_only": True,
        "automated_extraction_enabled": AUTOMATED_SCREENSHOT_EXTRACTION_ENABLED,
    }
