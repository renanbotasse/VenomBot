"""Shared helpers for list parsers."""

from __future__ import annotations

import csv
import io
import re
from pathlib import Path
from typing import Dict, Iterator, List, Optional

from venombot.countries import countries_in
from venombot.dates import parse_dates
from venombot.entities import Entity, Identifier

csv.field_size_limit(1 << 30)

NULLS = {"", "-0-", "-0- ", "-", "n/a", "na", "none", "null", "unknown", "nil"}


def clean(value: Optional[str]) -> str:
    """Collapse whitespace and map list null markers ("-0-", "N/A") to ""."""
    if value is None:
        return ""
    text = " ".join(str(value).replace("\x1a", " ").split())
    return "" if text.lower() in NULLS else text


def read_text(path: Path) -> str:
    """Decode a downloaded file trying the encodings lists actually use."""
    raw = Path(path).read_bytes()
    for enc in ("utf-8-sig", "utf-8", "cp1256", "cp1252", "latin-1"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def iter_csv(path: Path, *, fieldnames: Optional[List[str]] = None, skip_until: str = "",
             delimiter: str = ",") -> Iterator[Dict[str, str]]:
    """Iterate CSV rows as dicts.

    @param fieldnames header for header-less files (OFAC)
    @param skip_until skip preamble lines until one starting with this text
    """
    text = read_text(path)
    if skip_until:
        idx = text.find(skip_until)
        if idx > 0:
            text = text[idx:]
    reader = csv.DictReader(io.StringIO(text), fieldnames=fieldnames, delimiter=delimiter)
    for row in reader:
        yield {(k or "").strip(): (v if isinstance(v, str) else "") for k, v in row.items()}


def add_dates(ent: Entity, text: Optional[str]) -> None:
    for d in parse_dates(text):
        if d not in ent.birth_dates:
            ent.birth_dates.append(d)


def add_countries(target: List[str], text: Optional[str]) -> None:
    for c in countries_in(text):
        if c not in target:
            target.append(c)


def add_identifier(ent: Entity, id_type: str, value: Optional[str], country: str = "") -> None:
    value = clean(value)
    if not value:
        return
    if any(i.value == value and i.type == id_type for i in ent.identifiers):
        return
    ent.identifiers.append(Identifier(id_type, value, country))


_PAREN_COUNTRY = re.compile(r"^(.*?)\s*\(([^()]*)\)\s*$")


def split_value_country(text: str) -> tuple:
    """"A1234567 (Iran)" -> ("A1234567", "ir")."""
    m = _PAREN_COUNTRY.match(text.strip())
    if not m:
        return text.strip(), ""
    codes = countries_in(m.group(2))
    return m.group(1).strip(), codes[0] if codes else ""
