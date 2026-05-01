"""PwC-inspired Streamlit styling (non-official brand recreation)."""

from __future__ import annotations

from pathlib import Path


def _project_root() -> Path:
    return Path(__file__).resolve().parent.parent.parent


def load_streamlit_css() -> str:
    """Return combined CSS for injection via ``st.markdown(..., unsafe_allow_html=True)``."""
    path = _project_root() / "assets" / "streamlit_theme.css"
    if not path.is_file():
        return ""
    return path.read_text(encoding="utf-8")


def streamlit_theme_markup() -> str:
    """Full ``<style>`` block for Streamlit."""
    css = load_streamlit_css()
    return f"<style>{css}</style>"
