"""Tiny stdlib html.parser helpers for list pages that have no machine file.

Why a private module: these pages change layout without notice, so every
parser built on this must fail loudly (ValueError) instead of guessing.
"""

from __future__ import annotations

import html
import re
from html.parser import HTMLParser
from pathlib import Path
from typing import Dict, List, Optional, Tuple

_SKIP = {"script", "style", "noscript", "template", "svg"}
_BLOCK = {"p", "div", "li", "tr", "br", "h1", "h2", "h3", "h4", "h5", "h6", "ul", "ol",
          "table", "section", "article", "header", "footer", "dd", "dt", "dl", "td", "th"}


def read_html(path: Path) -> str:
    """Decode a page as UTF-8 (fall back to cp1252) and unescape nothing yet."""
    raw = Path(path).read_bytes()
    try:
        return raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        return raw.decode("cp1252", errors="replace")


def squash(text: str) -> str:
    """Collapse all whitespace (incl. NBSP and zero-width) to single spaces."""
    text = text.replace("\xa0", " ").replace("​", "").replace("﻿", "")
    return " ".join(text.split())


class _Lines(HTMLParser):
    """Flatten a page into text lines, one per block element, with heading marks."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.lines: List[Tuple[str, str]] = []  # (tag-of-block, text)
        self._buf: List[str] = []
        self._skip = 0
        self._block = "p"

    def _flush(self) -> None:
        text = squash("".join(self._buf))
        if text:
            self.lines.append((self._block, text))
        self._buf = []

    def handle_starttag(self, tag, attrs):
        if tag in _SKIP:
            self._skip += 1
        if tag in _BLOCK:
            self._flush()
            self._block = tag

    def handle_endtag(self, tag):
        if tag in _SKIP and self._skip:
            self._skip -= 1
        if tag in _BLOCK:
            self._flush()
            self._block = "p"

    def handle_data(self, data):
        if not self._skip:
            self._buf.append(data)

    def close(self) -> None:
        super().close()
        self._flush()


def text_lines(markup: str) -> List[Tuple[str, str]]:
    """Return [(block_tag, text)] for every non-empty block of the page."""
    p = _Lines()
    p.feed(markup)
    p.close()
    return p.lines


BREAK = "\u00a6"  # broken bar: marks p/br/li boundaries inside a cell


class _Tables(HTMLParser):
    def __init__(self, keep_breaks: bool = False) -> None:
        super().__init__(convert_charrefs=True)
        self._keep = keep_breaks
        self.tables: List[List[List[str]]] = []
        self._depth = 0
        self._row: Optional[List[str]] = None
        self._cell: Optional[List[str]] = None
        self._skip = 0
        self._stack: List[List[List[str]]] = []

    def handle_starttag(self, tag, attrs):
        if tag in _SKIP:
            self._skip += 1
        elif tag == "table":
            self._stack.append([])
        elif tag == "tr" and self._stack:
            self._row = []
        elif tag in ("td", "th") and self._row is not None:
            self._cell = []
        elif tag in ("br", "p", "div", "li") and self._cell is not None:
            self._cell.append(f" {BREAK} " if self._keep else " ")

    def handle_endtag(self, tag):
        if tag in _SKIP and self._skip:
            self._skip -= 1
        elif tag in ("td", "th") and self._cell is not None and self._row is not None:
            self._row.append(squash("".join(self._cell)))
            self._cell = None
        elif tag == "tr" and self._row is not None and self._stack:
            if any(self._row):
                self._stack[-1].append(self._row)
            self._row = None
        elif tag == "table" and self._stack:
            self.tables.append(self._stack.pop())

    def handle_data(self, data):
        if self._cell is not None and not self._skip:
            self._cell.append(data)


def table_rows(markup: str, keep_breaks: bool = False) -> List[List[List[str]]]:
    """Return every table as a list of rows of cell texts (innermost closes first).

    With keep_breaks, paragraph/line boundaries inside a cell are kept as BREAK
    so callers can tell a name from the "Also known as" paragraph under it.
    """
    p = _Tables(keep_breaks)
    p.feed(markup)
    p.close()
    return p.tables


class _Links(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.links: List[Tuple[str, str]] = []
        self._href: Optional[str] = None
        self._buf: List[str] = []

    def handle_starttag(self, tag, attrs):
        if tag == "a":
            self._href = dict(attrs).get("href") or ""
            self._buf = []

    def handle_endtag(self, tag):
        if tag == "a" and self._href is not None:
            self.links.append((self._href, squash("".join(self._buf))))
            self._href = None

    def handle_data(self, data):
        if self._href is not None:
            self._buf.append(data)


def links(markup: str) -> List[Tuple[str, str]]:
    """Return [(href, text)] for every anchor."""
    p = _Links()
    p.feed(markup)
    p.close()
    return p.links


def require(condition: bool, message: str) -> None:
    """Fail loudly: the update pipeline keeps the previous good version."""
    if not condition:
        raise ValueError(message)


def split_acronym(name: str) -> Tuple[str, Optional[str]]:
    """'Abu Sayyaf Group (ASG)' -> ('Abu Sayyaf Group', 'ASG') when trailing parens are short."""
    m = re.match(r"^(.*?)\s*\(([^()]{2,25})\)\s*$", name)
    if m and m.group(1):
        return m.group(1).strip(), m.group(2).strip()
    return name, None


def unescape(text: str) -> str:
    return html.unescape(text)
