"""Regex patterns, required Schedule P parts, and unpaid-section phrases."""

from __future__ import annotations

import re
from typing import List, Set

INVALID_FILENAME_CHARS_RE = re.compile(r'[<>:"/\\|?*]+')

REQUIRED_PARTS: List[str] = [
    "1A",
    "1B",
    "1C",
    "1D",
    "1E",
    "1F S1",
    "1F S2",
    "1G",
    "1H S1",
    "1H S2",
    "1I",
    "1J",
    "1K",
    "1L",
    "1M",
    "1N",
    "1O",
    "1P",
    "1R S1",
    "1R S2",
    "1S",
    "1T",
]
REQUIRED_PARTS_SET: Set[str] = set(REQUIRED_PARTS)
PARTS_REQUIRING_SECTION = {"1F", "1H", "1R"}

PART_RE = re.compile(r"SCH\s*P[, ]+\s*PT\s*(1[A-Z](?:\s*S\d)?)", re.IGNORECASE)
ROW_RE = re.compile(r"^\s*\d+\.\s*(Prior|20\d{2}|Totals)\b", re.IGNORECASE)
TOKEN_RE = re.compile(r"\(\d[\d,]*\)|-?\d[\d,]*")
SECTION_HINT_RE = re.compile(r"\b(?:SECTION|SEC|S)\s*([12])\b", re.IGNORECASE)

UNPAID_INCLUDE_PHRASES = (
    "LOSSES UNPAID",
    "DEFENSE AND COST CONTAINMENT UNPAID",
)
UNPAID_EXCLUDE_PHRASES = (
    "PREMIUMS EARNED",
    "LOSS AND LOSS EXPENSE PAYMENTS",
    "TOTAL NET PAID",
)
UNPAID_SECTION_END_PHRASES = (
    "TOTAL LOSS AND LOSS EXPENSE",
    "TOTAL LOSSES AND LOSS EXPENSES INCURRED",
)
