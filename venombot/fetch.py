"""HTTP fetch and HTML cleanup helpers."""

from __future__ import annotations

import hashlib
import http.client
import os
import re
import ssl
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Optional

# Some government sites ship incomplete certificate chains. Verification stays
# on; set VENOMBOT_CA_BUNDLE to point at a custom bundle when needed.
_SSL = ssl.create_default_context(cafile=os.environ.get("VENOMBOT_CA_BUNDLE") or None)

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


@dataclass
class FetchResult:
    ok: bool
    url: str
    path: Optional[Path] = None
    status: int = 0
    bytes: int = 0
    sha256: str = ""
    content_type: str = ""
    error: str = ""
    not_modified: bool = False


def download(
    url: str,
    dest: Path,
    *,
    headers: Optional[Dict[str, str]] = None,
    timeout: int = DEFAULT_TIMEOUT,
    retries: int = 3,
    user_agent: str = DEFAULT_USER_AGENT,
    data: Optional[bytes] = None,
) -> FetchResult:
    """Stream a URL to disk in full, atomically, with retries.

    Lists are never truncated: a cut-off sanctions file silently drops every
    party after the cut, which is worse than a visible failure. The file is
    written to a temp name and renamed only after the body completes.

    @param url resource to fetch
    @param dest final file path
    @param headers extra request headers (API keys, Accept)
    @param timeout per-attempt socket timeout in seconds
    @param retries attempts for transient errors (5xx, timeouts, resets)
    @param data optional POST body
    @return FetchResult with sha256 of the raw bytes
    """
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(dest.name + ".part")
    hdrs = {
        "User-Agent": user_agent,
        "Accept": "*/*",
        "Accept-Language": "en-US,en;q=0.9,ar;q=0.8",
    }
    hdrs.update(headers or {})
    last_error = ""
    status = 0
    for attempt in range(1, retries + 1):
        req = urllib.request.Request(url, headers=hdrs, data=data)
        digest = hashlib.sha256()
        size = 0
        try:
            with urllib.request.urlopen(req, timeout=timeout, context=_SSL) as resp:
                status = getattr(resp, "status", 200)
                ctype = resp.headers.get("Content-Type", "")
                encoding = (resp.headers.get("Content-Encoding") or "").lower()
                with open(tmp, "wb") as fh:
                    while True:
                        chunk = resp.read(1 << 16)
                        if not chunk:
                            break
                        digest.update(chunk)
                        size += len(chunk)
                        fh.write(chunk)
            if encoding == "gzip":
                _gunzip_in_place(tmp)
            os.replace(tmp, dest)
            return FetchResult(True, url, dest, status, size, digest.hexdigest(), ctype)
        except urllib.error.HTTPError as exc:
            status = exc.code
            last_error = f"HTTP {exc.code}"
            if exc.code < 500 and exc.code != 429:
                break
        except (urllib.error.URLError, TimeoutError, OSError, http.client.HTTPException) as exc:
            last_error = f"{type(exc).__name__}: {exc}"
        if tmp.exists():
            tmp.unlink()
        if attempt < retries:
            time.sleep(min(2 ** attempt, 20))
    return FetchResult(False, url, None, status, 0, "", "", last_error)


def _gunzip_in_place(path: Path) -> None:
    import gzip
    import shutil

    out = path.with_name(path.name + ".gunz")
    with gzip.open(path, "rb") as src, open(out, "wb") as dst:
        shutil.copyfileobj(src, dst)
    os.replace(out, path)


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
