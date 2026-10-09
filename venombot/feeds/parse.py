"""Tolerant RSS 2.0 / Atom 1.0 / RDF (RSS 1.0) parser, stdlib only.

Real-world feeds are messy: BOMs, a wrong declared encoding, HTML entities
such as ``&nbsp;`` that XML does not define, stray control characters. A
regulator feed that fails strict XML parsing must still yield its items, so
we try ElementTree first and fall back to regex extraction instead of raising.
"""

from __future__ import annotations

import html
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import List, Optional, Tuple
import xml.etree.ElementTree as ET

_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")
_BLOCK_RE = re.compile(r"</?(p|div|br|li|ul|ol|h[1-6]|tr|table|blockquote)\b[^>]*>", re.I)
_SCRIPT_RE = re.compile(r"<(script|style)\b.*?</\1>", re.I | re.S)
_CTRL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")
_ENTITY_RE = re.compile(r"&(?!(?:amp|lt|gt|quot|apos|#\d+|#x[0-9a-fA-F]+);)([A-Za-z][A-Za-z0-9]*);")


@dataclass
class ParsedItem:
    guid: str
    title: str
    url: str
    published: str  # ISO-8601 UTC, "" when unknown
    summary: str
    language: str = ""


@dataclass
class ParsedFeed:
    title: str
    language: str
    items: List[ParsedItem]
    format: str  # rss | atom | rdf | regex


def strip_html(text: str) -> str:
    """Plain text from an HTML fragment.

    Block tags become spaces (not glued words) so sentence-level matching
    does not fuse "Doe</p><p>Smith" into one token.
    """
    if not text:
        return ""
    text = _SCRIPT_RE.sub(" ", text)
    text = _BLOCK_RE.sub(" ", text)
    text = _TAG_RE.sub("", text)
    text = html.unescape(text).replace("\xa0", " ")
    return _WS_RE.sub(" ", text).strip()


def normalise_date(value: str) -> str:
    """RFC-822 / ISO-8601 / bare date to ISO-8601 UTC ("" if unparseable)."""
    value = (value or "").strip()
    if not value:
        return ""
    dt: Optional[datetime] = None
    try:
        dt = parsedate_to_datetime(value)
    except (TypeError, ValueError, IndexError):
        dt = None
    if dt is None:
        v = value.replace("Z", "+00:00").replace("z", "+00:00")
        v = re.sub(r"([+-]\d{2})(\d{2})$", r"\1:\2", v)
        for cand in (v, v.replace(" ", "T", 1)):
            try:
                dt = datetime.fromisoformat(cand)
                break
            except ValueError:
                continue
    if dt is None:  # "Thursday, October 8, 2026 - 13:00" (Drupal, FCA)
        m = re.match(r"(?:\w+,\s*)?(\w+)\s+(\d{1,2}),\s*(\d{4})(?:\s*-\s*(\d{1,2}):(\d{2}))?", value)
        if m:
            for fmt in ("%B", "%b"):
                try:
                    mon = datetime.strptime(m.group(1), fmt).month
                    dt = datetime(int(m.group(3)), mon, int(m.group(2)), int(m.group(4) or 0), int(m.group(5) or 0))
                    break
                except ValueError:
                    continue
    if dt is None:
        m = re.match(r"(\d{4})-(\d{2})-(\d{2})", value)
        if m:
            try:
                dt = datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)))
            except ValueError:
                return ""
        else:
            return ""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def decode_bytes(raw: bytes) -> str:
    """Decode feed bytes: BOM, declared encoding, then utf-8 / cp1256 / latin-1."""
    if raw.startswith(b"\xef\xbb\xbf"):
        return raw[3:].decode("utf-8", errors="replace")
    if raw[:2] in (b"\xff\xfe", b"\xfe\xff"):
        return raw.decode("utf-16", errors="replace")
    declared = ""
    m = re.match(rb"\s*<\?xml[^>]*encoding=[\"']([A-Za-z0-9_.:-]+)[\"']", raw[:200])
    if m:
        declared = m.group(1).decode("ascii", "ignore")
    # utf-8 first even when another encoding is declared: a wrong declaration
    # (utf-8 bytes labelled iso-8859-1) is the common failure, and valid
    # multi-byte utf-8 is vanishingly unlikely in genuine legacy text.
    for enc in ("utf-8", declared, "cp1256", "cp1252"):
        if not enc:
            continue
        try:
            return raw.decode(enc)
        except (UnicodeDecodeError, LookupError):
            continue
    return raw.decode("utf-8", errors="replace")


def _local(tag: object) -> str:
    if not isinstance(tag, str):
        return ""
    return tag.rsplit("}", 1)[-1].lower()


def _text(el: Optional[ET.Element]) -> str:
    return "".join(el.itertext()).strip() if el is not None else ""


def _child(el: ET.Element, *names: str) -> Optional[ET.Element]:
    for c in el:
        if _local(c.tag) in names:
            return c
    return None


def _children(el: ET.Element, name: str) -> List[ET.Element]:
    return [c for c in el if _local(c.tag) == name]


def _mk_item(title: str, link: str, guid: str, date: str, summary: str, lang: str) -> Optional[ParsedItem]:
    title = strip_html(title)
    link = (link or "").strip()
    summary = strip_html(summary)
    if not (title or summary):
        return None
    guid = (guid or "").strip() or link or title
    return ParsedItem(guid=guid, title=title, url=link, published=normalise_date(date),
                      summary=summary, language=lang)


def _xml_parse(text: str) -> ET.Element:
    text = _CTRL_RE.sub("", text)
    text = re.sub(r"^\s*<\?xml[^>]*\?>", "", text)  # declared encoding is moot on str
    text = text.lstrip("﻿ \t\r\n")
    # Undefined HTML entities (&nbsp;) make strict XML fail; map the known ones.
    def fix(m: "re.Match[str]") -> str:
        ch = html.unescape(m.group(0))
        return ch if ch != m.group(0) else "&amp;" + m.group(1) + ";"
    text = _ENTITY_RE.sub(fix, text)
    return ET.fromstring(text)


def _from_tree(root: ET.Element, default_lang: str) -> ParsedFeed:
    kind = _local(root.tag)
    items: List[ParsedItem] = []
    if kind == "feed":  # Atom
        lang = root.get("{http://www.w3.org/XML/1998/namespace}lang") or default_lang
        for e in _children(root, "entry"):
            link = ""
            for l in _children(e, "link"):
                if l.get("rel", "alternate") == "alternate" and l.get("href"):
                    link = l.get("href", "")
                    break
                link = link or l.get("href", "")
            summ = _text(_child(e, "summary")) or _text(_child(e, "content"))
            it = _mk_item(_text(_child(e, "title")), link, _text(_child(e, "id")),
                          _text(_child(e, "updated", "published")) or _text(_child(e, "published")),
                          summ, e.get("{http://www.w3.org/XML/1998/namespace}lang") or lang)
            if it:
                items.append(it)
        return ParsedFeed(_text(_child(root, "title")), lang, items, "atom")
    channel = _child(root, "channel") if kind in ("rss", "rdf") else None
    chan = channel if channel is not None else root
    lang = _text(_child(chan, "language")) or default_lang
    seq = _children(chan, "item") + (_children(root, "item") if channel is not None else [])
    for e in seq:
        link = _text(_child(e, "link"))
        if not link:
            # Atom-style link inside RSS
            l = _child(e, "link")
            link = l.get("href", "") if l is not None else ""
        desc = _text(_child(e, "encoded")) or _text(_child(e, "description")) or _text(_child(e, "summary"))
        base = _text(_child(e, "description"))
        guid = _text(_child(e, "guid")) or _text(_child(e, "identifier"))
        date = _text(_child(e, "pubdate", "date", "published", "updated", "issued"))
        it = _mk_item(_text(_child(e, "title")), link, guid, date,
                      base or desc, lang)
        if it:
            items.append(it)
    fmt = "rdf" if kind == "rdf" else "rss"
    return ParsedFeed(_text(_child(chan, "title")), lang, items, fmt)


def _tag_value(block: str, names: Tuple[str, ...]) -> str:
    for n in names:
        m = re.search(r"<(?:[\w.-]+:)?%s\b[^>]*>(.*?)</(?:[\w.-]+:)?%s>" % (n, n), block, re.S | re.I)
        if m:
            v = m.group(1).strip()
            c = re.match(r"<!\[CDATA\[(.*?)\]\]>$", v, re.S)
            return c.group(1) if c else html.unescape(v) if "<" not in v else v
    return ""


def _from_regex(text: str, default_lang: str) -> ParsedFeed:
    """Last resort for feeds ElementTree rejects: pull item blocks by regex."""
    items: List[ParsedItem] = []
    blocks = re.findall(r"<item\b.*?</item>", text, re.S | re.I)
    atom = False
    if not blocks:
        blocks = re.findall(r"<entry\b.*?</entry>", text, re.S | re.I)
        atom = True
    lang = default_lang
    m = re.search(r"<language>\s*([^<\s]+)\s*</language>", text, re.I)
    if m:
        lang = m.group(1)
    for b in blocks:
        link = _tag_value(b, ("link",))
        if atom or not link:
            h = re.search(r"<link\b[^>]*href=[\"']([^\"']+)", b, re.I)
            if h:
                link = html.unescape(h.group(1))
        it = _mk_item(
            _tag_value(b, ("title",)), link, _tag_value(b, ("guid", "id")),
            _tag_value(b, ("pubDate", "updated", "published", "date")),
            _tag_value(b, ("description", "summary", "encoded", "content")), lang)
        if it:
            items.append(it)
    t = re.search(r"<title>(.*?)</title>", text, re.S | re.I)
    return ParsedFeed(strip_html(t.group(1)) if t else "", lang, items, "regex")


def parse_feed(raw: "bytes | str", default_language: str = "") -> ParsedFeed:
    """Parse an RSS/Atom/RDF document into items; never raises on bad XML.

    Falls back to regex extraction when ElementTree fails or finds no items
    in a document that visibly contains some.
    """
    text = decode_bytes(raw) if isinstance(raw, (bytes, bytearray)) else str(raw).lstrip("﻿")
    try:
        feed = _from_tree(_xml_parse(text), default_language)
        if feed.items or not re.search(r"<(item|entry)\b", text, re.I):
            return feed
    except ET.ParseError:
        pass
    return _from_regex(text, default_language)
