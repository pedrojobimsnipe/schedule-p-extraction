"""Write extracted Schedule P data to Excel workbooks."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Dict, Set, Tuple

from openpyxl import Workbook

from app.core.constants import INVALID_FILENAME_CHARS_RE, REQUIRED_PARTS


def safe_filename_stem(stem: str) -> str:
    cleaned = INVALID_FILENAME_CHARS_RE.sub("_", stem).strip().rstrip(".")
    if not cleaned:
        return "schedule_p_output"
    return cleaned[:120]


def build_output_path(output_dir: Path, pdf_path: Path) -> Path:
    return output_dir / f"{safe_filename_stem(pdf_path.stem)}_ScheduleP_Part1_Unpaid_Col23_24.xlsx"


def write_workbook(
    output_path: Path,
    part_rows: Dict[str, Dict[str, Tuple[str, str]]],
    part_status: Dict[str, str],
    years_seen: Set[str],
) -> int:
    wb = Workbook()
    summary_ws = wb.active
    summary_ws.title = "Validation"
    summary_ws.append(["Part", "Status", "Rows Extracted", "Years Present"])

    year_order = ["Prior"] + sorted(years_seen) + ["Totals"]
    if len(year_order) == 2:
        year_order = ["Prior", "Totals"]

    for part_id in REQUIRED_PARTS:
        part_years = sorted([year for year in part_rows[part_id] if re.fullmatch(r"20\d{2}", year)])
        summary_ws.append(
            [
                part_id,
                part_status[part_id].upper(),
                len(part_rows[part_id]),
                ", ".join(part_years),
            ]
        )

        ws = wb.create_sheet(title=part_id)
        ws.append(
            [
                "Year",
                "Column 23 - Salvage and Subrogation Anticipated",
                "Column 24 - Total Net Losses and Expenses Unpaid",
            ]
        )

        for year in year_order:
            if part_status[part_id] == "none":
                col23, col24 = ("NONE", "NONE")
            elif part_status[part_id] == "missing":
                col23, col24 = ("NOT FOUND", "NOT FOUND")
            else:
                col23, col24 = part_rows[part_id].get(year, ("", ""))
            ws.append([year, col23, col24])

        ws.column_dimensions["A"].width = 14
        ws.column_dimensions["B"].width = 54
        ws.column_dimensions["C"].width = 54

    summary_ws.column_dimensions["A"].width = 12
    summary_ws.column_dimensions["B"].width = 14
    summary_ws.column_dimensions["C"].width = 16
    summary_ws.column_dimensions["D"].width = 40
    wb.save(str(output_path))
    return len(REQUIRED_PARTS)
