"""Helpers shared by the debarment / enforcement list modules.

Stdlib only. Kept private to this family (the leading underscore stops the
registry from treating it as a source module). It holds three things the
parsers all need: a tolerant HTML table reader (these registers are plain
``<table>`` pages), date normalisation with the *source's* day/month order, and
the "expired but kept" rule, because screening must still show history.
"""

from __future__ import annotations

import re
import tempfile
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from html import unescape
from html.parser import HTMLParser
from pathlib import Path
from typing import Callable, Dict, Iterator, List, Optional, Sequence

from venombot.countries import countries_in
from venombot.entities import Entity
from venombot.lists import ListSource
from venombot.lists._util import add_countries, clean

ZERO_WIDTH = dict.fromkeys(map(ord, "​‌‍﻿ "), " ")


def tidy(value: Optional[str]) -> str:
    """Unescape entities, drop zero-width chars/nbsp and collapse whitespace."""
    if value is None:
        return ""
    return clean(unescape(str(value)).translate(ZERO_WIDTH))


def strip_tags(value: Optional[str]) -> str:
    """Remove markup from a fragment (ESMA names arrive wrapped in an anchor)."""
    return tidy(re.sub(r"<[^>]+>", " ", value or ""))


# --------------------------------------------------------------------- tables
@dataclass
class Cell:
    text: str = ""
    href: str = ""


@dataclass
class _Table:
    rows: List[List[Cell]] = field(default_factory=list)


class _TableParser(HTMLParser):
    """Collect every <table> as rows of Cells; nested tables are flattened."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.tables: List[_Table] = []
        self._depth = 0
        self._row: Optional[List[Cell]] = None
        self._cell: Optional[Cell] = None
        self._buf: List[str] = []

    def handle_starttag(self, tag: str, attrs: list) -> None:
        if tag == "table":
            if self._depth == 0:
                self.tables.append(_Table())
            self._depth += 1
        elif self._depth and tag == "tr":
            self._row = []
        elif self._depth and tag in ("td", "th") and self._row is not None:
            self._close_cell()
            self._cell = Cell()
            self._buf = []
        elif tag == "a" and self._cell is not None and not self._cell.href:
            self._cell.href = dict(attrs).get("href") or ""
        elif tag in ("br", "p") and self._cell is not None:
            self._buf.append(" ")

    def handle_endtag(self, tag: str) -> None:
        if tag in ("td", "th"):
            self._close_cell()
        elif tag == "tr":
            self._close_cell()
            if self._row is not None and self.tables:
                self.tables[-1].rows.append(self._row)
            self._row = None
        elif tag == "table" and self._depth:
            self._depth -= 1

    def handle_data(self, data: str) -> None:
        if self._cell is not None:
            self._buf.append(data)

    def _close_cell(self) -> None:
        if self._cell is not None and self._row is not None:
            self._cell.text = tidy("".join(self._buf))
            self._row.append(self._cell)
        self._cell = None
        self._buf = []


def read_tables(path: Path) -> List[List[List[Cell]]]:
    """Parse an HTML file into tables -> rows -> cells (utf-8, latin-1 fallback)."""
    raw = Path(path).read_bytes()
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        text = raw.decode("latin-1")
    parser = _TableParser()
    parser.feed(text)
    parser.close()
    return [t.rows for t in parser.tables]


# ---------------------------------------------------------------------- dates
_FORMATS = {
    "dmy": ("%d/%m/%Y", "%d-%m-%Y", "%d.%m.%Y", "%d/%m/%y"),
    "mdy": ("%m/%d/%Y", "%m/%d/%y", "%B %d, %Y", "%b %d, %Y", "%B %d,%Y"),
    "ymd": ("%Y-%m-%d", "%Y%m%d", "%Y/%m/%d"),
}
_ALL = _FORMATS["ymd"] + _FORMATS["dmy"] + _FORMATS["mdy"]


def to_iso(value: Optional[str], order: str = "ymd") -> str:
    """Normalise one date to YYYY-MM-DD using the source's own field order.

    The order is explicit because 07/11/2006 is 7 November in Australia and
    July 11 in the US; guessing would silently corrupt debarment windows.

    @param order "dmy" | "mdy" | "ymd" - tried first
    @return ISO date or "" for blanks, zero dates and unparseable text
    """
    text = tidy(value)
    if not text or text in ("00000000", "0") or text.startswith("0000"):
        return ""
    text = text.replace("T00:00:00Z", "").replace("T00:00:00", "")
    if "T" in text and re.match(r"\d{4}-\d{2}-\d{2}T", text):
        text = text.split("T")[0]
    for fmt in _FORMATS[order] + _ALL:
        try:
            return datetime.strptime(text, fmt).date().isoformat()
        except ValueError:
            continue
    return ""


def excel_date(value: Optional[str]) -> str:
    """Excel serial day number ("43215") -> ISO date; other text via to_iso."""
    text = tidy(value)
    if re.fullmatch(r"\d{4,6}(\.0+)?", text):
        n = int(float(text))
        if 20000 < n < 80000:
            return (date(1899, 12, 30) + timedelta(days=n)).isoformat()
    return to_iso(text, "mdy")


def epoch_ms_date(value: object) -> str:
    """Epoch milliseconds -> ISO date (EDES stores midnight local time)."""
    try:
        ms = int(value)  # type: ignore[call-overload]
    except (TypeError, ValueError):
        return ""
    return (datetime(1970, 1, 1) + timedelta(milliseconds=ms, hours=12)).date().isoformat()


def apply_term(ent: Entity, start: str, end: str, permanent: bool = False) -> None:
    """Record the sanction window and flag history without dropping it.

    Expired sanctions stay in the list (``extra["expired"] = True``) because a
    past debarment is still relevant to a screening decision.
    """
    if start:
        ent.extra["debarred_from"] = start
        if not ent.listed_on:
            ent.listed_on = start
    if end:
        ent.extra["debarred_to"] = end
        if end < date.today().isoformat():
            ent.extra["expired"] = True
    if permanent:
        ent.extra["permanent"] = True


# --------------------------------------------------------------------- misc
LEGAL_FORM = re.compile(
    r"\b(ltd|limited|llc|l\.l\.c|inc|incorporated|corp|corporation|gmbh|s\.?a\.?|s\.?r\.?l|"
    r"pty|plc|llp|ag|bv|nv|co\.|company|ltda|eireli|me|epp|cjsc|ooo|jsc|sdn|bhd|pvt|"
    r"s\.?p\.?a|a\.?s\.?|oy|ab|kft|doo|d\.o\.o)\b\.?",
    re.IGNORECASE,
)


def looks_like_company(name: str) -> bool:
    """Legal-form suffix heuristic; only used where a list has no type column."""
    return bool(LEGAL_FORM.search(name))


def stable_id(*parts: object) -> str:
    """Short deterministic id for rows without a key (stable across refreshes)."""
    import hashlib

    return hashlib.sha1("|".join(str(p) for p in parts).encode("utf-8")).hexdigest()[:16]


def person_by_structure(name: str) -> str:
    """"SURNAME, Given" without a legal form -> Person, else Organization.

    Only for registers whose export has no type column (NY OMIG, DDTC).
    """
    if looks_like_company(name):
        return "Organization"
    return "Person" if "," in name else "Organization"


def join_remarks(*parts: str) -> str:
    """Join non-empty remark fragments with ' | ' (reason, authority, case ref).

    A fragment such as "Docket:" whose value was blank is dropped.
    """
    out = []
    for raw in parts:
        text = tidy(raw)
        if text and not text.endswith(":"):
            out.append(text)
    return " | ".join(out)


def unique(func):
    """Decorator: drop entities repeating a source_id (exports repeat rows)."""
    import functools

    @functools.wraps(func)
    def wrapper(paths, source):
        seen = set()
        for ent in func(paths, source):
            if ent.source_id in seen:
                continue
            seen.add(ent.source_id)
            yield ent

    return wrapper


def entity_countries(ent: Entity, *texts: Optional[str]) -> None:
    """Add every country mentioned in the given free-text fields."""
    for t in texts:
        add_countries(ent.countries, t)


def nat_codes(text: Optional[str]) -> List[str]:
    """ISO2 codes mentioned in a nationality field."""
    return list(countries_in(text))


def parser_download(url: str, dest: Path, *, headers: Optional[Dict[str, str]] = None,
                    data: Optional[bytes] = None) -> Path:
    """Fetch one file inside a parser (for registers whose file URL moves daily).

    The ListSource contract only downloads static URLs, but several registers
    publish ``<index page> -> dated file``. The static index is declared in
    ``urls`` and the parser follows it here. Raises so a failed refresh is
    visible rather than silently yielding nothing.
    """
    from venombot import fetch

    res = fetch.download(url, dest, headers=headers, data=data)
    if not res.ok or res.path is None:
        raise RuntimeError(f"download failed: {url} ({res.error})")
    return res.path


def workdir() -> "tempfile.TemporaryDirectory[str]":
    """Scratch directory that is removed even when parsing aborts."""
    return tempfile.TemporaryDirectory(prefix="venombot_c_")


Parser = Callable[[Dict[str, Path], ListSource], Iterator[Entity]]


def numbered(prefix_url: str, count: int, key: str = "p") -> Dict[str, str]:
    """{p00: url0, p01: url1...} for fixed page sets (``{n}`` placeholder)."""
    return {f"{key}{i:02d}": prefix_url.format(n=i) for i in range(count)}


def page_paths(paths: Dict[str, Path], key: str = "p") -> Sequence[Path]:
    """Downloaded page files in page order."""
    return [paths[k] for k in sorted(paths) if k.startswith(key)]
