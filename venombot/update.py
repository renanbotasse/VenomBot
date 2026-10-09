"""Download -> parse -> store pipeline for structured list sources."""

from __future__ import annotations

import hashlib
import os
import shutil
import tempfile
import traceback
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

from venombot.fetch import download
from venombot.lists import ListSource
from venombot.store import EntityStore, SourceStatus

Log = Callable[[str], None]


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _status(src: ListSource) -> SourceStatus:
    return SourceStatus(
        key=src.key, name=src.name, jurisdiction=src.jurisdiction, list_type=src.list_type,
        url=next(iter(src.urls.values()), ""), license=src.license, fetched_at=_now(),
    )


def fetch_source(src: ListSource, workdir: Path) -> Dict[str, Path]:
    """Download every file of a source; raises on any failure."""
    key = src.api_key() or ""
    headers = {k: v.replace("{api_key}", key) for k, v in src.headers.items()}
    paths: Dict[str, Path] = {}
    for name, url in src.urls.items():
        url = url.replace("{api_key}", key)
        res = download(url, workdir / f"{src.key}.{name}", headers=headers, timeout=src.timeout,
                       data=src.post_data)
        if not res.ok or res.path is None:
            raise RuntimeError(f"{name}: {res.error or 'download failed'} ({url})")
        if res.bytes == 0:
            raise RuntimeError(f"{name}: empty response ({url})")
        paths[name] = res.path
    return paths


def _keep_previous(store: EntityStore, st: SourceStatus) -> None:
    # A failed refresh keeps serving the last good version; the status row
    # records both the failure and the age of what is still being served.
    prev = store.status(st.key)
    if prev and prev[0].entity_count:
        st.entity_count, st.sha256, st.bytes = prev[0].entity_count, prev[0].sha256, prev[0].bytes
        st.source_updated = prev[0].source_updated or prev[0].fetched_at


def ingest(store: EntityStore, src: ListSource, paths: Dict[str, Path], log: Log = print,
           keep_raw: Optional[Path] = None) -> SourceStatus:
    """Parse downloaded files into the store and record the outcome.

    @param keep_raw directory to archive raw downloads into (evidence trail)
    """
    st = _status(src)
    try:
        digest = hashlib.sha256()
        total = 0
        for p in paths.values():
            with open(p, "rb") as fh:
                for chunk in iter(lambda: fh.read(1 << 20), b""):
                    digest.update(chunk)
                    total += len(chunk)
        st.sha256, st.bytes = digest.hexdigest(), total
        count = store.replace_source(src.key, src.parser(paths, src))
        if count == 0:
            raise RuntimeError("parser produced 0 entities (format change?)")
        st.entity_count, st.status, st.complete = count, "ok", True
        st.source_updated = st.fetched_at
        if keep_raw:
            dest = Path(keep_raw) / src.key / st.fetched_at.replace(":", "-")
            dest.mkdir(parents=True, exist_ok=True)
            for p in paths.values():
                shutil.copy2(p, dest / p.name)
        log(f"  [ok]   {src.key}: {count} entities ({total / 1e6:.1f} MB)")
    except Exception as exc:  # noqa: BLE001 - one bad source must not stop the run
        st.status, st.complete = "failed", False
        st.error = f"{type(exc).__name__}: {exc}"[:500]
        _keep_previous(store, st)
        log(f"  [fail] {src.key}: {st.error}")
        if os.environ.get("VENOMBOT_DEBUG"):
            traceback.print_exc()
    store.set_status(st)
    return st


def _record_fetch_failure(store: EntityStore, src: ListSource, err: Exception, log: Log) -> SourceStatus:
    st = _status(src)
    st.status, st.error = "failed", f"{type(err).__name__}: {err}"[:500]
    _keep_previous(store, st)
    store.set_status(st)
    log(f"  [fail] {src.key}: {st.error}")
    return st


def _record_skip(store: EntityStore, src: ListSource, log: Log) -> SourceStatus:
    st = _status(src)
    st.status, st.error = "skipped", f"set {src.api_key_env} to enable"
    _keep_previous(store, st)
    store.set_status(st)
    log(f"  [skip] {src.key}: {st.error}")
    return st


def update_sources(store: EntityStore, sources: List[ListSource], log: Log = print,
                   workers: int = 4, keep_raw: Optional[Path] = None) -> List[SourceStatus]:
    """Refresh many sources: downloads run in parallel, DB writes serialised.

    @param workers concurrent downloads
    @return one status per source
    """
    log(f"Updating {len(sources)} list source(s)…")
    results: List[SourceStatus] = []
    with tempfile.TemporaryDirectory(prefix="venombot-") as tmp:
        workdir = Path(tmp)

        def fetch(src: ListSource) -> Tuple[ListSource, Optional[Dict[str, Path]], Optional[Exception]]:
            if src.needs_key and not src.api_key():
                return src, None, None
            try:
                return src, fetch_source(src, workdir), None
            except Exception as exc:  # noqa: BLE001
                return src, None, exc

        with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
            for src, paths, err in pool.map(fetch, sources):
                if paths is None and err is None:
                    results.append(_record_skip(store, src, log))
                elif paths is None:
                    results.append(_record_fetch_failure(store, src, err or RuntimeError("?"), log))
                else:
                    results.append(ingest(store, src, paths, log, keep_raw))
                    for p in paths.values():
                        p.unlink(missing_ok=True)
    ok = sum(1 for r in results if r.status == "ok")
    log(f"Done: {ok}/{len(results)} ok, {store.count()} entities in store.")
    return results
