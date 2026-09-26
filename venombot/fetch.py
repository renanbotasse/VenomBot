"""HTTP fetch and HTML cleanup helpers."""

from __future__ import annotations

import re
import urllib.error
import urllib.request
from typing import Optional

DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (compatible; VenomBot/1.0; +https://github.com/renanbotasse/VenomBot)"
)
DEFAULT_TIMEOUT = 90


def fetch_url(
    url: str,
    *,
    user_agent: str = DEFAULT_USER_AGENT,
    timeout: int = DEFAULT_TIMEOUT,
    max_bytes: Optional[int] = None,
) -> Optional[str]:
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": user_agent,
            "Accept": "text/csv,application/xml,text/xml,application/rss+xml,application/json,text/html,*/*",
            "Accept-Language": "en-US,en;q=0.9,ar;q=0.8",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            if max_bytes is not None and max_bytes > 0:
                raw = resp.read(max_bytes)
            else:
                raw = resp.read()
            for encoding in ("utf-8", "utf-8-sig", "latin-1", "cp1256"):
                try:
                    return raw.decode(encoding)
                except UnicodeDecodeError:
                    continue
            return raw.decode("utf-8", errors="replace")
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, OSError) as exc:
        print(f"  [warn] fetch failed: {url} ({exc})")
        return None


def _looks_like_markup(content: str) -> bool:
    head = content.lstrip()[:800].lower()
    if head.startswith("<?xml") or head.startswith("<!doctype") or head.startswith("<html"):
        return True
    if head.startswith("<rss") or head.startswith("<feed"):
        return True
    if "<consolidated_list" in head or "<html" in head:
        return True
    if head[:1].isdigit() or head.startswith('"id"') or head.startswith("{") or head.startswith("["):
        return False
    if "," in head[:40] and "<" not in head[:40]:
        return False
    return False


def strip_html(content: str) -> str:
    """Strip tags from HTML/RSS while preserving CSV/JSON/UN XML for parsers."""
    if not content:
        return content
    head = content.lstrip()[:800]
    if head.startswith("<?xml") or "<CONSOLIDATED_LIST" in head:
        return content
    if head.startswith("{") or head.startswith("["):
        return content
    if not _looks_like_markup(content):
        return content
    text = re.sub(r"<script[^>]*>.*?</script>", " ", content, flags=re.I | re.S)
    text = re.sub(r"<style[^>]*>.*?</style>", " ", text, flags=re.I | re.S)
    text = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", text).strip()
