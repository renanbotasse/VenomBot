"""Refresh feeds into the ArticleStore with conditional GET."""

from __future__ import annotations

import gzip
import os
import threading
import time
import urllib.parse
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from typing import Callable, Dict, List, Optional, Sequence, Tuple

from venombot import fetch
from venombot.feeds import FeedSource
from venombot.feeds.parse import parse_feed
from venombot.feeds.store import ArticleStore

_MAX_BYTES = 8_000_000


def _conditional_get(url: str, etag: str, last_modified: str, timeout: int = 30,
                     extra: Optional[Dict[str, str]] = None) -> Tuple[Optional[bytes], str, str]:
    """GET with validators. Returns (body or None when 304, etag, last_modified).

    Uses urllib directly because fetch.get_bytes hides response headers.
    """
    hdrs = {"User-Agent": fetch.DEFAULT_USER_AGENT,
            "Accept": "application/rss+xml, application/atom+xml, application/xml, text/xml, */*",
            "Accept-Encoding": "gzip"}
    hdrs.update(extra or {})
    if etag:
        hdrs["If-None-Match"] = etag
    if last_modified:
        hdrs["If-Modified-Since"] = last_modified
    req = urllib.request.Request(url, headers=hdrs)
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=fetch._SSL) as resp:
            body = resp.read(_MAX_BYTES + 1)
            if len(body) > _MAX_BYTES:
                raise RuntimeError("feed larger than limit")
            if (resp.headers.get("Content-Encoding") or "").lower() == "gzip":
                body = gzip.decompress(body)
            return body, resp.headers.get("ETag", "") or "", resp.headers.get("Last-Modified", "") or ""
    except urllib.error.HTTPError as exc:
        if exc.code == 304:
            return None, etag, last_modified
        raise RuntimeError(f"HTTP {exc.code}") from exc


_HOST_LOCKS: Dict[str, threading.Lock] = {}
_LOCKS_GUARD = threading.Lock()


def _host_lock(url: str) -> threading.Lock:
    host = urllib.parse.urlsplit(url).netloc
    with _LOCKS_GUARD:
        return _HOST_LOCKS.setdefault(host, threading.Lock())


def _extra_headers(feed: FeedSource) -> Dict[str, str]:
    h = dict(feed.headers)
    contact = os.environ.get("VENOMBOT_CONTACT", "")
    if feed.needs_contact and contact:
        h["User-Agent"] = f"VenomBot/1.0 (AML research; {contact})"
    return h


def _fetch_one(feed: FeedSource, st: Dict[str, object]) -> Tuple[FeedSource, Optional[bytes], str, str, str]:
    try:
        # One request per host at a time: sec.gov answers parallel hits with 403.
        with _host_lock(feed.url):
            try:
                body, et, lm = _conditional_get(feed.url, str(st.get("etag", "")), str(st.get("last_modified", "")),
                                                  extra=_extra_headers(feed))
            except RuntimeError as exc:
                if not any(c in str(exc) for c in ("403", "429", "50")):
                    raise
                time.sleep(2.0)  # one polite retry on throttling
                body, et, lm = _conditional_get(feed.url, str(st.get("etag", "")), str(st.get("last_modified", "")),
                                                  extra=_extra_headers(feed))
            time.sleep(0.3)
        return feed, body, et, lm, ""
    except Exception as exc:  # noqa: BLE001 - recorded, previous articles kept
        return feed, None, "", "", f"{type(exc).__name__}: {exc}"[:300]


def update_feeds(store: ArticleStore, feeds: Sequence[FeedSource], workers: int = 4,
                 log: Callable[[str], None] = print) -> Dict[str, Dict[str, object]]:
    """Fetch ``feeds`` in parallel and store new articles.

    Network runs in threads; sqlite writes stay on the calling thread. A
    failure never removes stored articles; it is recorded in feed_status.
    Returns {feed key: {"status": "ok"|"not_modified"|"failed", "new": n, "items": n}}.
    """
    results: Dict[str, Dict[str, object]] = {}
    # sqlite connections are thread-bound: read validators here, not in workers.
    statuses = {f.key: store.get_status(f.key) for f in feeds}
    with ThreadPoolExecutor(max_workers=max(1, workers)) as ex:
        for feed, body, et, lm, err in ex.map(lambda f: _fetch_one(f, statuses[f.key]), feeds):
            if err:
                store.set_status(feed.key, False, err, store.count(feed.key))
                results[feed.key] = {"status": "failed", "error": err, "new": 0, "items": 0}
                log(f"[feeds] {feed.key}: FAILED {err}")
                continue
            if body is None:
                store.set_status(feed.key, True, "", store.count(feed.key))
                results[feed.key] = {"status": "not_modified", "new": 0, "items": 0}
                log(f"[feeds] {feed.key}: not modified")
                continue
            try:
                parsed = parse_feed(body, feed.language)
                new = store.add_items(feed.key, parsed.items, feed.language)
            except Exception as exc:  # noqa: BLE001
                msg = f"parse: {exc}"[:300]
                store.set_status(feed.key, False, msg, store.count(feed.key))
                results[feed.key] = {"status": "failed", "error": msg, "new": 0, "items": 0}
                log(f"[feeds] {feed.key}: FAILED {msg}")
                continue
            if not parsed.items:
                store.set_status(feed.key, False, "no items parsed", store.count(feed.key), et, lm)
                results[feed.key] = {"status": "failed", "error": "no items parsed", "new": 0, "items": 0}
                log(f"[feeds] {feed.key}: no items parsed")
                continue
            store.set_status(feed.key, True, "", store.count(feed.key), et, lm)
            results[feed.key] = {"status": "ok", "new": new, "items": len(parsed.items)}
            log(f"[feeds] {feed.key}: {len(parsed.items)} items, {new} new")
    return results
