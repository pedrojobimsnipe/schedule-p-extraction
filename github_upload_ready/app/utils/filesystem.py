"""Filesystem helpers for batch PDF processing."""

from __future__ import annotations

from pathlib import Path
from typing import Iterable


def iter_pdfs(input_dir: Path) -> Iterable[Path]:
    """Yield sorted ``*.pdf`` files directly under ``input_dir``."""
    return sorted(path for path in input_dir.glob("*.pdf") if path.is_file())
