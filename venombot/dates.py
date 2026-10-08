"""Date parsing into partial ISO strings (YYYY, YYYY-MM, YYYY-MM-DD).

Lists record birth dates as "07 Oct 1952", "1952-10-07", "circa 1960",
"1960 to 1962" or "Oct 1952". Keeping the precision lets screening tell an
exact DOB agreement from a year-only one.
"""

from __future__ import annotations

import re
from typing import List, Optional

_MONTHS = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6, "jul": 7,
    "aug": 8, "sep": 9, "sept": 9, "oct": 10, "nov": 11, "dec": 12,
    "january": 1, "february": 2, "march": 3, "april": 4, "june": 6, "july": 7,
    "august": 8, "september": 9, "october": 10, "november": 11, "december": 12,
}

_ISO = re.compile(r"\b(1[89]\d\d|20\d\d)-(\d{1,2})(?:-(\d{1,2}))?\b")
_DMY_NUM = re.compile(r"\b(\d{1,2})[./](\d{1,2})[./](1[89]\d\d|20\d\d)\b")
_DMY_TXT = re.compile(r"\b(?:(\d{1,2})\s+)?([A-Za-z]{3,9})\.?,?\s+(1[89]\d\d|20\d\d)\b")
_MDY_TXT = re.compile(r"\b([A-Za-z]{3,9})\.?\s+(\d{1,2}),\s*(1[89]\d\d|20\d\d)\b")
_RANGE = re.compile(r"\b(1[89]\d\d|20\d\d)\s*(?:-|to|–|until)\s*(1[89]\d\d|20\d\d)\b")
_YEAR = re.compile(r"\b(1[89]\d\d|20\d\d)\b")


def _fmt(y: int, m: Optional[int] = None, d: Optional[int] = None) -> Optional[str]:
    if m is None:
        return f"{y:04d}"
    if not 1 <= m <= 12:
        return f"{y:04d}"
    if d is None:
        return f"{y:04d}-{m:02d}"
    if not 1 <= d <= 31:
        return f"{y:04d}-{m:02d}"
    return f"{y:04d}-{m:02d}-{d:02d}"


def parse_dates(text: Optional[str]) -> List[str]:
    """Extract every date in a free-text field as partial ISO strings.

    @param text e.g. "DOB 07 Oct 1952; alt. DOB 1953" or "1960 to 1962"
    @return de-duplicated list, most precise forms first
    """
    if not text:
        return []
    text = str(text)
    out: List[str] = []

    def add(v: Optional[str]) -> None:
        if v and v not in out:
            out.append(v)

    consumed = text
    for m in _ISO.finditer(text):
        add(_fmt(int(m.group(1)), int(m.group(2)), int(m.group(3)) if m.group(3) else None))
        consumed = consumed.replace(m.group(0), " ")
    for m in _DMY_NUM.finditer(consumed):
        add(_fmt(int(m.group(3)), int(m.group(2)), int(m.group(1))))
        consumed = consumed.replace(m.group(0), " ")
    for m in _MDY_TXT.finditer(consumed):
        mon = _MONTHS.get(m.group(1).lower().rstrip("."))
        if mon:
            add(_fmt(int(m.group(3)), mon, int(m.group(2))))
            consumed = consumed.replace(m.group(0), " ")
    for m in _DMY_TXT.finditer(consumed):
        mon = _MONTHS.get(m.group(2).lower().rstrip("."))
        if mon:
            day = int(m.group(1)) if m.group(1) else None
            add(_fmt(int(m.group(3)), mon, day))
            consumed = consumed.replace(m.group(0), " ")
    for m in _RANGE.finditer(consumed):
        a, b = int(m.group(1)), int(m.group(2))
        if 0 <= b - a <= 10:
            for y in range(a, b + 1):
                add(_fmt(y))
            consumed = consumed.replace(m.group(0), " ")
    for m in _YEAR.finditer(consumed):
        add(_fmt(int(m.group(1))))
    return out


def compare_dates(subject: str, listed: List[str]) -> str:
    """Compare a subject DOB with listed DOBs.

    @return "exact" (same day), "month", "year", "near" (within 1 year),
            "conflict" (all listed dates further apart) or "unknown"
    """
    if not subject or not listed:
        return "unknown"
    best = "conflict"
    rank = {"exact": 4, "month": 3, "year": 2, "near": 1, "conflict": 0}
    sy = int(subject[:4])
    for d in listed:
        try:
            ly = int(d[:4])
        except ValueError:
            continue
        if ly != sy:
            res = "near" if abs(ly - sy) <= 1 else "conflict"
        elif len(subject) >= 10 and len(d) >= 10:
            res = "exact" if subject[:10] == d[:10] else ("month" if subject[:7] == d[:7] else "year")
        elif len(subject) >= 7 and len(d) >= 7:
            res = "month" if subject[:7] == d[:7] else "year"
        else:
            res = "year"
        if rank[res] > rank[best]:
            best = res
    return best
