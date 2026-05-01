"""
Schedule P extraction — Streamlit entrypoint (enterprise dashboard UI).

Run locally::

    python -m streamlit run streamlit_app.py
"""

from __future__ import annotations

import logging

import app
from app.config import WebSettings, load_web_settings
from app.ui.streamlit_dashboard import run_dashboard

logger = logging.getLogger(__name__)


def _configure_logging(debug: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if debug else logging.INFO,
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
        datefmt="%H:%M:%S",
        force=True,
    )


def _log_startup_validation(web: WebSettings) -> None:
    """Resolve settings once at boot; warn on misconfiguration (non-fatal)."""
    timeout_disp = "none" if web.extraction_timeout_sec is None else str(web.extraction_timeout_sec)
    logger.info(
        "schedule_p.startup version=%s max_upload_mb=%s extraction_timeout_sec=%s debug=%s",
        getattr(app, "__version__", "unknown"),
        web.max_upload_mb,
        timeout_disp,
        web.debug_logging,
    )
    try:
        from streamlit.config import get_option

        server_mb = float(get_option("server.maxUploadSize"))
        if server_mb < web.max_upload_mb:
            logger.warning(
                "schedule_p.config_mismatch Streamlit server.maxUploadSize=%s MB is below "
                "SCHEDULE_P_MAX_UPLOAD_MB=%s — browser uploads may fail before app validation. "
                "Raise maxUploadSize in .streamlit/config.toml or pass --server.maxUploadSize.",
                server_mb,
                web.max_upload_mb,
            )
    except Exception:
        logger.debug("schedule_p.startup could not read server.maxUploadSize", exc_info=True)


def main() -> None:
    web = load_web_settings()
    _configure_logging(web.debug_logging)
    _log_startup_validation(web)
    run_dashboard(web)


main()
