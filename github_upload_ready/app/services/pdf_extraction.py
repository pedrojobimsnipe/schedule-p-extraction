"""Extract Schedule P unpaid columns from PDF text."""

from __future__ import annotations

import logging
import re
import time
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Mapping, MutableMapping, Optional, Set, Tuple

import pdfplumber

logger = logging.getLogger(__name__)

"""
Optional UI/diagnostics callback: ``(phase, info_dict) -> None``.

Phases: ``opened``, ``page``, ``finished``; upload layer may add ``workbook``.
"""
PdfProgressHook = Optional[Callable[[str, Mapping[str, Any]], None]]

from app.core.constants import (
    REQUIRED_PARTS,
    REQUIRED_PARTS_SET,
    ROW_RE,
    TOKEN_RE,
    UNPAID_EXCLUDE_PHRASES,
    UNPAID_INCLUDE_PHRASES,
    UNPAID_SECTION_END_PHRASES,
)
from app.core.parsing import (
    detect_none_parts,
    detect_page_parts,
    normalize_row_label,
    normalize_whitespace,
)

# Row index prefix in Schedule P Part 1 grid cells (handles ``11..2025`` glyph-split PDFs).
SCHEDULE_P_GRID_ROW_HEAD_RE = re.compile(r"^\s*(\d{1,2})\.(?:\s|\.)+")


def _invoke_pdf_hook(hook: PdfProgressHook, phase: str, **info: Any) -> None:
    if hook is None:
        return
    try:
        hook(phase, info)
    except Exception:
        logger.exception("schedule_p.pdf.progress_hook_failed phase=%s", phase)


def _init_run_stats(run_stats: Optional[MutableMapping[str, Any]]) -> None:
    if run_stats is None:
        return
    run_stats.setdefault("total_pages", 0)
    run_stats.setdefault("pages_processed", 0)
    run_stats.setdefault("timed_out", False)


def get_unpaid_section_lines(page_lines: list[str]) -> list[str]:
    normalized_lines = [normalize_whitespace(line.upper()) for line in page_lines]
    first_unpaid_idx: Optional[int] = None

    for idx, normalized_line in enumerate(normalized_lines):
        if any(phrase in normalized_line for phrase in UNPAID_INCLUDE_PHRASES):
            first_unpaid_idx = idx
            break

    if first_unpaid_idx is None:
        return []

    last_paid_before_unpaid = -1
    for idx in range(first_unpaid_idx):
        if any(phrase in normalized_lines[idx] for phrase in UNPAID_EXCLUDE_PHRASES):
            last_paid_before_unpaid = idx

    if first_unpaid_idx <= last_paid_before_unpaid:
        return []

    end_idx = len(page_lines)
    for idx in range(first_unpaid_idx + 1, len(normalized_lines)):
        normalized_line = normalized_lines[idx]
        if any(phrase in normalized_line for phrase in UNPAID_SECTION_END_PHRASES):
            end_idx = idx
            break

    return page_lines[first_unpaid_idx + 1 : end_idx]


def normalize_table_numeric_cell(value: Optional[str]) -> str:
    """
    Collapse junk dots inserted between digit glyphs inside a **single table cell**.

    Some carriers' PDFs render numbers like ``3..3,5..8..8``; pdfplumber keeps those inside one cell,
    so merging ``(\\d)\\.+(\\d)`` here is safe (unlike doing it on a whole text line that may glue
    adjacent columns).
    """
    if value is None:
        return ""
    s = str(value).strip()
    if not s:
        return ""
    if s in {"—", "-", "–"}:
        return ""
    if "�" in s and not re.search(r"\d", s):
        return ""
    if re.fullmatch(r"[Xx.\s\u2022\u2212]+", s):
        return ""
    s = re.sub(r"^[.\sXx]+", "", s)
    s = re.sub(r"[.\sXx]+$", "", s)
    prev = None
    while prev != s:
        prev = s
        s = re.sub(r"(\d)\.+(\d)", r"\1\2", s)
        s = re.sub(r"(?<=\d)\.+(?=,)", "", s)
    return s.strip()


def parse_schedule_p_grid_row_label_line(line: str) -> Optional[str]:
    """Parse Prior / calendar year / Totals from a Part 1 grid row label (plain or dot-split)."""
    line = line.strip()
    m = SCHEDULE_P_GRID_ROW_HEAD_RE.match(line)
    if not m:
        return None
    rest = line[m.end() :].lstrip(". ")
    if not rest:
        return None
    if re.match(r"^P(?:\.+[A-Za-z]){3}", rest):
        return "Prior"
    if re.match(r"^T(?:\.+[A-Za-z]){4}", rest):
        return "Totals"
    pm = re.match(r"^(Prior|Totals)\b", rest, re.IGNORECASE)
    if pm:
        return "Prior" if pm.group(1).lower() == "prior" else "Totals"
    ym = re.match(r"^(20\d{2})\b", rest)
    if ym:
        return ym.group(1)
    # Glyph-split years: ``2..0..2..5`` (spacing dots between digits).
    dm = re.match(r"^(\d(?:\.+\d){3})(?:\b|[.\s]|$)", rest)
    if dm:
        digits = re.sub(r"\.", "", dm.group(1))
        if len(digits) == 4 and digits.startswith("20"):
            return digits
    return None


def _find_unpaid_grid_column_indices(table: List[List[Optional[str]]]) -> Optional[Tuple[int, int]]:
    """Return (col23_idx, col24_idx) for NAIC Part 1 unpaid grid, or None."""
    for row in table[:10]:
        col23 = col24 = None
        for ci, cell in enumerate(row or []):
            u = (cell or "").upper().replace("\n", " ")
            if "SALVAGE" in u and "SUBROGATION" in u and "ANTICIPATED" in u:
                col23 = ci
            if (
                "TOTAL NET" in u
                and "UNPAID" in u
                and "LOSS" in u
                and ("EXPENSE" in u or "EXPENSES" in u)
            ):
                col24 = ci
        if col23 is not None and col24 is not None:
            return col23, col24
    return None


def extract_unpaid_col23_col24_from_page_tables(page: pdfplumber.page.Page) -> Dict[str, Tuple[str, str]]:
    """
    Extract columns 23–24 from Part 1 **Losses Unpaid** grid via ``extract_tables``.

    Preferred when ``extract_text`` injects dots between glyphs (breaking regex tokenization).
    """
    tables = page.extract_tables() or []
    merged: Dict[str, Tuple[str, str]] = {}
    for table in tables:
        block = _extract_unpaid_block_from_single_table(table)
        if block:
            merged.update(block)
    return merged


def _extract_unpaid_block_from_single_table(table: List[List[Optional[str]]]) -> Dict[str, Tuple[str, str]]:
    cols = _find_unpaid_grid_column_indices(table)
    if not cols:
        return {}
    i23, i24 = cols
    out: Dict[str, Tuple[str, str]] = {}

    for row in table:
        if not row or max(i23, i24) >= len(row):
            continue
        c0 = row[0]
        if not c0 or not str(c0).strip():
            continue
        raw_lines = [ln.strip() for ln in str(c0).split("\n") if ln.strip()]
        grid_lines = [ln for ln in raw_lines if SCHEDULE_P_GRID_ROW_HEAD_RE.match(ln)]

        if len(grid_lines) >= 2:
            s23 = (row[i23] or "").split("\n")
            s24 = (row[i24] or "").split("\n")
            if len(s23) < len(grid_lines) or len(s24) < len(grid_lines):
                continue
            for idx, gl in enumerate(grid_lines):
                raw_label = parse_schedule_p_grid_row_label_line(gl)
                if not raw_label:
                    continue
                label = normalize_row_label(raw_label)
                v23 = normalize_table_numeric_cell(s23[idx].strip() if idx < len(s23) else "")
                v24 = normalize_table_numeric_cell(s24[idx].strip() if idx < len(s24) else "")
                out[label] = (v23, v24)
            continue

        if len(raw_lines) == 1 and SCHEDULE_P_GRID_ROW_HEAD_RE.match(raw_lines[0]):
            raw_label = parse_schedule_p_grid_row_label_line(raw_lines[0])
            if raw_label and normalize_row_label(raw_label) == "Totals":
                out["Totals"] = (
                    normalize_table_numeric_cell(row[i23]),
                    normalize_table_numeric_cell(row[i24]),
                )
    return out


def schedule_p_row_numeric_tokens(line: str) -> Optional[Tuple[str, List[str]]]:
    """
    Split a Schedule P style row into its year/Prior/Totals label and numeric tokens **after** that label.

    Stripping the ``ROW_RE`` prefix avoids counting the leading ``N.`` row index as a table column,
    which previously shifted ``TOKEN_RE`` positions and mis-mapped columns 23–24 on standard 24-column layouts.
    """
    row_match = ROW_RE.match(line)
    if not row_match:
        return None
    label = normalize_row_label(row_match.group(1))
    remainder = line[row_match.end() :]
    tokens = TOKEN_RE.findall(remainder)
    return label, tokens


def max_numeric_token_count_among_lines(lines: Iterable[str]) -> int:
    """Maximum count of post-label numeric tokens across matching rows (within one unpaid block)."""
    max_n = 0
    for line in lines:
        got = schedule_p_row_numeric_tokens(line)
        if got:
            max_n = max(max_n, len(got[1]))
    return max_n


def columns_23_24_from_tokens(tokens: List[str], section_max_tokens: int) -> Optional[Tuple[str, str]]:
    """
    Map trailing numeric tokens to NAIC Schedule P Part 1 unpaid **columns 23 and 24**.

    - **Standard layout (~24 numeric columns after the row label):** column 24 is the **rightmost**
      numeric (Total Net Losses and Loss Expenses Unpaid); column 23 is immediately before it
      (Salvage and Subrogation Anticipated). Use ``tokens[-2]`` and ``tokens[-1]``.

    - **Wide layout (25+ numeric columns):** some carriers append an extra numeric after column 24.
      In that case columns 23–24 correspond to ``tokens[-3]`` and ``tokens[-2]``, matching the
      legacy heuristic.

    - **Blank column 23:** PDF text often **omits** empty cells, producing one fewer token than peer
      rows. When ``len(tokens) == section_max_tokens - 1``, treat column 23 as blank (``""``) and
      ``tokens[-1]`` as column 24.
    """
    if not tokens:
        return None

    # Wide tables: extra trailing numeric column beyond 24.
    if section_max_tokens >= 25:
        if len(tokens) >= 3:
            return tokens[-3], tokens[-2]
        if len(tokens) >= 2:
            return tokens[-2], tokens[-1]
        return "", tokens[-1]

    # Standard ≤24 numeric columns; column 24 is rightmost.
    if section_max_tokens >= 1 and len(tokens) == section_max_tokens - 1:
        return "", tokens[-1]
    if len(tokens) >= 2:
        return tokens[-2], tokens[-1]
    return "", tokens[-1]


def extract_pdf_parts(
    pdf_path: Path,
    *,
    progress_hook: PdfProgressHook = None,
    timeout_seconds: Optional[float] = None,
    run_stats: Optional[MutableMapping[str, Any]] = None,
) -> Tuple[Dict[str, Dict[str, Tuple[str, str]]], Dict[str, str], Set[str]]:
    """
    Parse ``pdf_path`` and return per-part row values, status labels, and calendar years seen.

    ``progress_hook`` receives ``(phase, info)`` where ``phase`` is ``opened``, ``page``, or ``finished``.
    ``timeout_seconds`` stops page iteration early (partial extraction); see ``run_stats``.

    Raises:
        FileNotFoundError: if ``pdf_path`` does not exist.
        Exception: pdfplumber/pdfminer may raise for corrupt or unreadable PDFs (caller may catch).
    """
    if not pdf_path.is_file():
        raise FileNotFoundError(f"PDF not found: {pdf_path}")

    _init_run_stats(run_stats)
    part_rows: Dict[str, Dict[str, Tuple[str, str]]] = {part: {} for part in REQUIRED_PARTS}
    part_status: Dict[str, str] = {part: "missing" for part in REQUIRED_PARTS}
    years_seen: Set[str] = set()
    current_part: Optional[str] = None

    deadline: Optional[float] = None
    if timeout_seconds is not None and timeout_seconds > 0:
        deadline = time.monotonic() + float(timeout_seconds)

    with pdfplumber.open(str(pdf_path)) as pdf:
        total_pages = len(pdf.pages)
        if run_stats is not None:
            run_stats["total_pages"] = total_pages
        _invoke_pdf_hook(
            progress_hook,
            "opened",
            total_pages=total_pages,
            page_index=0,
            message=f"Opened PDF — {total_pages} page(s)",
        )

        for page_index, page in enumerate(pdf.pages, start=1):
            if deadline is not None and time.monotonic() > deadline:
                if run_stats is not None:
                    run_stats["timed_out"] = True
                    run_stats["pages_processed"] = page_index - 1
                logger.warning(
                    "schedule_p.pdf.timeout path=%s pages_completed=%s limit_sec=%s",
                    pdf_path.name,
                    page_index - 1,
                    timeout_seconds,
                )
                break

            if run_stats is not None:
                run_stats["pages_processed"] = page_index

            page_started = time.perf_counter()
            _invoke_pdf_hook(
                progress_hook,
                "page",
                total_pages=total_pages,
                page_index=page_index,
                message=f"Extracting text & tables — page {page_index} of {total_pages}",
            )

            page_text = (page.extract_text() or "").replace("\ufffe", " ")
            page_lines = [line for line in page_text.splitlines() if line.strip()]
            if not page_lines:
                continue

            normalized_page = normalize_whitespace(page_text.upper())
            page_parts = detect_page_parts(page_text, page_lines)
            page_none_parts = detect_none_parts(page_lines)

            for part in page_none_parts:
                if part_status.get(part) != "data":
                    part_status[part] = "none"

            has_unpaid_phrase = any(phrase in normalized_page for phrase in UNPAID_INCLUDE_PHRASES)
            has_text_rows = any(ROW_RE.match(line) for line in page_lines)
            table_unpaid = extract_unpaid_col23_col24_from_page_tables(page)
            if not table_unpaid and not (has_unpaid_phrase and has_text_rows):
                continue

            if len(page_parts) == 1:
                current_part = page_parts[0]
            elif len(page_parts) > 1:
                # Avoid contents/index pages that list many Schedule P parts.
                current_part = None

            if not current_part or current_part not in REQUIRED_PARTS_SET:
                continue

            if table_unpaid:
                rows_for_part = part_rows[current_part]
                for label, pair in table_unpaid.items():
                    rows_for_part[label] = pair
                    if re.fullmatch(r"20\d{2}", label):
                        years_seen.add(label)
                part_status[current_part] = "data"
                continue

            unpaid_section_lines = get_unpaid_section_lines(page_lines)
            if not unpaid_section_lines:
                continue

            section_max = max_numeric_token_count_among_lines(unpaid_section_lines)
            if section_max == 0:
                continue

            rows_for_part = part_rows[current_part]
            extracted_rows = 0
            for line in unpaid_section_lines:
                row_data = schedule_p_row_numeric_tokens(line)
                if not row_data:
                    continue

                row_label, tokens = row_data
                extracted = columns_23_24_from_tokens(tokens, section_max)
                if extracted is None:
                    continue

                rows_for_part[row_label] = extracted
                extracted_rows += 1

                if re.fullmatch(r"20\d{2}", row_label):
                    years_seen.add(row_label)

            if extracted_rows > 0:
                part_status[current_part] = "data"

            if logger.isEnabledFor(logging.DEBUG):
                logger.debug(
                    "schedule_p.pdf.page_done file=%s page=%s/%s elapsed_sec=%.3f",
                    pdf_path.name,
                    page_index,
                    total_pages,
                    time.perf_counter() - page_started,
                )

        if run_stats is not None and not run_stats.get("timed_out"):
            run_stats["pages_processed"] = run_stats.get("total_pages", 0)

        _invoke_pdf_hook(
            progress_hook,
            "finished",
            total_pages=run_stats.get("total_pages", total_pages) if run_stats else total_pages,
            page_index=run_stats.get("pages_processed", total_pages) if run_stats else total_pages,
            timed_out=bool(run_stats.get("timed_out")) if run_stats else False,
            message="PDF text pass complete",
        )

    return part_rows, part_status, years_seen
