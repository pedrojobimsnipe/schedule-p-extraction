"""Text normalization and Schedule P part detection (no I/O)."""

from __future__ import annotations

import re
from typing import List, Optional, Set, Tuple

from app.core.constants import (
    PART_RE,
    PARTS_REQUIRING_SECTION,
    REQUIRED_PARTS_SET,
    SECTION_HINT_RE,
)


def normalize_whitespace(value: str) -> str:
    return re.sub(r"\s+", " ", value.strip())


def normalize_for_part_regex(value: str) -> str:
    normalized = value.upper()
    normalized = normalized.replace("SCHEDULE", "SCH")
    normalized = normalized.replace("PART", "PT")
    normalized = re.sub(r"SECTION\s*([12])", r"S\1", normalized)
    normalized = normalized.replace("-", " ")
    return normalize_whitespace(normalized)


def normalize_row_label(raw_label: str) -> str:
    label = raw_label.strip()
    if label.lower() == "prior":
        return "Prior"
    if label.lower() == "totals":
        return "Totals"
    return label


def infer_section(text: str) -> Optional[str]:
    match = SECTION_HINT_RE.search(text)
    return match.group(1) if match else None


def normalize_detected_part(part_token: str, context_text: str) -> Optional[str]:
    token = normalize_whitespace(part_token.upper())
    match = re.fullmatch(r"(1[A-Z])(?:\s*S(\d))?", token)
    if not match:
        return None

    base_part, section = match.groups()

    if base_part in PARTS_REQUIRING_SECTION:
        resolved_section = section or infer_section(context_text)
        if resolved_section not in {"1", "2"}:
            return None
        normalized = f"{base_part} S{resolved_section}"
        return normalized if normalized in REQUIRED_PARTS_SET else None

    return base_part if base_part in REQUIRED_PARTS_SET else None


def is_none_line(value: str) -> bool:
    return re.sub(r"[^A-Z]", "", value.upper()) == "NONE"


def detect_page_parts_with_indices(page_lines: List[str]) -> List[Tuple[int, str]]:
    detected: List[Tuple[int, str]] = []
    seen: Set[Tuple[int, str]] = set()

    for idx, line in enumerate(page_lines):
        normalized_line = normalize_for_part_regex(line)
        for match in PART_RE.finditer(normalized_line):
            normalized = normalize_detected_part(match.group(1), normalized_line)
            if not normalized:
                continue
            key = (idx, normalized)
            if key not in seen:
                seen.add(key)
                detected.append((idx, normalized))

    return detected


def detect_page_parts(page_text: str, page_lines: List[str]) -> List[str]:
    detected_in_order: List[str] = []
    for _, part in detect_page_parts_with_indices(page_lines):
        if part not in detected_in_order:
            detected_in_order.append(part)

    if detected_in_order:
        return detected_in_order

    collapsed = normalize_for_part_regex(page_text)
    for match in PART_RE.finditer(collapsed):
        normalized = normalize_detected_part(match.group(1), collapsed)
        if normalized and normalized not in detected_in_order:
            detected_in_order.append(normalized)

    return detected_in_order


def detect_none_parts(page_lines: List[str]) -> Set[str]:
    none_parts: Set[str] = set()
    part_markers = detect_page_parts_with_indices(page_lines)
    if not part_markers:
        return none_parts

    for idx, part in part_markers:
        line = page_lines[idx]
        if is_none_line(line):
            none_parts.add(part)
            continue

        look_ahead = 0
        probe_idx = idx + 1
        while probe_idx < len(page_lines) and look_ahead < 3:
            probe_line = page_lines[probe_idx]
            if not probe_line.strip():
                probe_idx += 1
                continue
            look_ahead += 1
            if is_none_line(probe_line):
                none_parts.add(part)
                break
            # Stop if another part marker starts before we find NONE.
            if detect_page_parts(probe_line, [probe_line]):
                break
            probe_idx += 1

    return none_parts
