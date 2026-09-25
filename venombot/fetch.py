"""HTTP fetch and HTML cleanup helpers."""

from __future__ import annotations

import re
import urllib.error
import urllib.request
from typing import Optional

DEFAULT_USER_AGENT = "VenomBot-AML-Crawler/1.0 (+compliance screening)"
DEFAULT_TIMEOUT = 20


def fetch_url(
    url: str,
    *,
    user_agent: str = DEFAULT_USER_AGENT,
    timeout: int = DEFAULT_TIMEOUT,
) -> Optional[str]:
    req = urllib.request.Request(url, headers={"User-Agent": user_agent})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
            for encoding in ("utf-8", "latin-1", "cp1256"):
                try:
                    return raw.decode(encoding)
                except UnicodeDecodeError:
                    continue
            return raw.decode("utf-8", errors="replace")
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, OSError) as exc:
        print(f"  [warn] fetch failed: {url} ({exc})")
        return None


def strip_html(content: str) -> str:
    if "<" not in content or ">" not in content:
        return content
    text = re.sub(r"<script[^>]*>.*?</script>", " ", content, flags=re.I | re.S)
    text = re.sub(r"<style[^>]*>.*?</style>", " ", text, flags=re.I | re.S)
    text = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", text).strip()
