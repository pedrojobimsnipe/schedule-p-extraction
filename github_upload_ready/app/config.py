"""Application configuration (CLI overrides env; env overrides repo defaults)."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

# Canonical default for web uploads (MB). Must stay aligned with ``server.maxUploadSize``
# in ``.streamlit/config.toml`` unless operators intentionally split limits (not recommended).
DEFAULT_MAX_UPLOAD_MB: float = 500.0


def _project_root() -> Path:
    """Directory containing the `app` package (repository root when installed from source)."""
    return Path(__file__).resolve().parent.parent


@dataclass(frozen=True)
class Settings:
    """Resolved default paths before CLI overrides."""

    input_dir: Path
    output_dir: Path


def parse_extraction_timeout_sec(raw: str | None) -> float | None:
    """
    Parse ``SCHEDULE_P_EXTRACTION_TIMEOUT_SEC``.

    - ``0`` or empty: no limit (wait until extraction finishes).
    - Positive float: max seconds for PDF parsing; partial workbook may be returned on timeout.
    """
    if raw is None or not str(raw).strip():
        return 900.0
    try:
        v = float(str(raw).strip())
        if v <= 0:
            return None
        return v
    except ValueError:
        return 900.0


@dataclass(frozen=True)
class WebSettings:
    """Settings for the Streamlit / web UI."""

    max_upload_mb: float
    app_title: str
    app_subtitle: str
    debug_logging: bool
    extraction_timeout_sec: float | None


def load_settings(project_root: Path | None = None) -> Settings:
    """
    Load defaults from environment variables with sensible fallbacks.

    Environment variables (optional):
        SCHEDULE_P_INPUT_DIR  — default input folder for PDFs
        SCHEDULE_P_OUTPUT_DIR — default output folder for Excel files
    """
    root = project_root if project_root is not None else _project_root()
    input_raw = os.getenv("SCHEDULE_P_INPUT_DIR")
    output_raw = os.getenv("SCHEDULE_P_OUTPUT_DIR")
    input_dir = Path(input_raw).expanduser() if input_raw else root / "input"
    output_dir = Path(output_raw).expanduser() if output_raw else root / "output"
    return Settings(input_dir=input_dir, output_dir=output_dir)


def parse_max_upload_mb(raw: str | None) -> float:
    """
    Parse ``SCHEDULE_P_MAX_UPLOAD_MB`` with fallback to :data:`DEFAULT_MAX_UPLOAD_MB`.

    Invalid or non-positive values fall back to the default (never silently cap below app expectation).
    """
    if raw is None or not str(raw).strip():
        return DEFAULT_MAX_UPLOAD_MB
    try:
        value = float(str(raw).strip())
        if value <= 0:
            return DEFAULT_MAX_UPLOAD_MB
        return value
    except ValueError:
        return DEFAULT_MAX_UPLOAD_MB


def load_web_settings() -> WebSettings:
    """
    Web UI configuration via environment variables.

        SCHEDULE_P_MAX_UPLOAD_MB — per-file upload limit in megabytes (default matches
            :data:`DEFAULT_MAX_UPLOAD_MB`; must align with Streamlit ``server.maxUploadSize``)
        SCHEDULE_P_APP_TITLE      — header title (optional)
        SCHEDULE_P_APP_SUBTITLE   — header subtitle (optional)
        SCHEDULE_P_DEBUG          — if ``1`` / ``true``, enable DEBUG logging on the server
        SCHEDULE_P_EXTRACTION_TIMEOUT_SEC — max seconds for PDF extraction in the web UI (default 900;
            use ``0`` to disable). When hit, a partial workbook may still be produced.
    """
    max_upload_mb = parse_max_upload_mb(os.getenv("SCHEDULE_P_MAX_UPLOAD_MB"))

    title = os.getenv(
        "SCHEDULE_P_APP_TITLE",
        "Schedule P — Unpaid extraction",
    )
    subtitle = os.getenv(
        "SCHEDULE_P_APP_SUBTITLE",
        "Part 1 unpaid columns 23–24 · Internal processing tool",
    )
    debug_raw = os.getenv("SCHEDULE_P_DEBUG", "").strip().lower()
    debug_logging = debug_raw in ("1", "true", "yes", "on")
    extraction_timeout_sec = parse_extraction_timeout_sec(os.getenv("SCHEDULE_P_EXTRACTION_TIMEOUT_SEC"))
    return WebSettings(
        max_upload_mb=max_upload_mb,
        app_title=title,
        app_subtitle=subtitle,
        debug_logging=debug_logging,
        extraction_timeout_sec=extraction_timeout_sec,
    )
