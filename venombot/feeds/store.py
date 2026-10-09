"""Article store: sqlite tables inside the same database as EntityStore.

Own tables only (``articles``, ``articles_fts``, ``feed_status``) so entity
refreshes and feed refreshes never touch each other's data.
"""

from __future__ import annotations

import re
import sqlite3
import unicodedata
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from venombot.feeds.parse import ParsedItem

DEFAULT_DB = Path("venombot_data") / "venombot.db"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS articles (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    guid TEXT NOT NULL, feed TEXT NOT NULL,
    title TEXT, url TEXT, published TEXT, summary TEXT, language TEXT,
    fetched_at TEXT, title_norm TEXT,
    UNIQUE (feed, guid)
);
CREATE INDEX IF NOT EXISTS articles_pub ON articles(published);
CREATE INDEX IF NOT EXISTS articles_tn ON articles(title_norm);
CREATE TABLE IF NOT EXISTS feed_status (
    feed TEXT PRIMARY KEY, last_fetch TEXT, ok INTEGER, error TEXT,
    count INTEGER, etag TEXT, last_modified TEXT
);
"""


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def normalise_title(title: str) -> str:
    """Casefolded, punctuation-free title used for cross-feed dedup."""
    t = unicodedata.normalize("NFKC", title).casefold()
    t = re.sub(r"[^\w]+", " ", t)
    return t.strip()


def _fts_query(terms: Sequence[str]) -> str:
    safe = []
    for t in terms:
        t = re.sub(r"[^\w]", "", t)
        if t:
            safe.append('"%s"' % t)
    return " OR ".join(safe)


class ArticleStore:
    """Persistent article index with FTS5 (LIKE fallback)."""

    def __init__(self, path: "Path | str" = DEFAULT_DB) -> None:
        self.path = Path(path)
        if str(path) != ":memory:":
            self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(path))
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(_SCHEMA)
        self.fts = self._init_fts()

    def _init_fts(self) -> bool:
        try:
            self.conn.execute(
                "CREATE VIRTUAL TABLE IF NOT EXISTS articles_fts USING fts5("
                "title, summary, content='articles', content_rowid='id', "
                "tokenize='unicode61 remove_diacritics 2')")
            return True
        except sqlite3.OperationalError:
            return False

    def close(self) -> None:
        self.conn.close()

    def add_items(self, feed: str, items: Iterable[ParsedItem], default_language: str = "") -> int:
        """Insert new articles; skip (feed, guid) repeats and titles already
        stored from another feed. Returns the number inserted.
        """
        n = 0
        now = _now()
        for it in items:
            tn = normalise_title(it.title)
            c = self.conn
            if c.execute("SELECT 1 FROM articles WHERE feed=? AND guid=?", (feed, it.guid)).fetchone():
                continue
            # Short titles ("News") collide too easily to be a dedup key.
            if len(tn) >= 20 and c.execute(
                    "SELECT 1 FROM articles WHERE title_norm=? AND feed<>?", (tn, feed)).fetchone():
                continue
            cur = c.execute(
                "INSERT INTO articles(guid,feed,title,url,published,summary,language,fetched_at,title_norm) "
                "VALUES (?,?,?,?,?,?,?,?,?)",
                (it.guid, feed, it.title, it.url, it.published, it.summary,
                 it.language or default_language, now, tn))
            if self.fts:
                c.execute("INSERT INTO articles_fts(rowid,title,summary) VALUES (?,?,?)",
                          (cur.lastrowid, it.title, it.summary))
            n += 1
        self.conn.commit()
        return n

    def count(self, feed: Optional[str] = None) -> int:
        if feed:
            return self.conn.execute("SELECT COUNT(*) FROM articles WHERE feed=?", (feed,)).fetchone()[0]
        return self.conn.execute("SELECT COUNT(*) FROM articles").fetchone()[0]

    def candidates(self, terms: Sequence[str], days: Optional[int] = None, limit: int = 2000) -> List[sqlite3.Row]:
        """Articles containing any of ``terms`` (FTS5, or LIKE fallback)."""
        terms = [t for t in terms if len(t) >= 2]
        if not terms:
            return []
        where, params = "", []  # type: str, list
        if days is not None:
            cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")
            # Undated items are kept: many regulator feeds omit pubDate.
            where = " AND (a.published='' OR a.published>=?)"
            params.append(cutoff)
        if self.fts:
            q = _fts_query(terms)
            if not q:
                return []
            try:
                return self.conn.execute(
                    "SELECT a.* FROM articles_fts f JOIN articles a ON a.id=f.rowid "
                    "WHERE articles_fts MATCH ?" + where + " LIMIT ?", [q] + params + [limit]).fetchall()
            except sqlite3.OperationalError:
                pass
        like = " OR ".join(["(a.title LIKE ? OR a.summary LIKE ?)"] * len(terms))
        lp: list = []
        for t in terms:
            lp += ["%" + t + "%"] * 2
        return self.conn.execute(
            "SELECT a.* FROM articles a WHERE (" + like + ")" + where + " LIMIT ?", lp + params + [limit]).fetchall()

    def purge(self, days: int = 365) -> int:
        """Delete articles published (or, if undated, fetched) before the cutoff."""
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")
        rows = self.conn.execute(
            "SELECT id,title,summary FROM articles WHERE COALESCE(NULLIF(published,''),fetched_at)<?",
            (cutoff,)).fetchall()
        for r in rows:
            if self.fts:
                self.conn.execute(
                    "INSERT INTO articles_fts(articles_fts,rowid,title,summary) VALUES('delete',?,?,?)",
                    (r["id"], r["title"], r["summary"]))
            self.conn.execute("DELETE FROM articles WHERE id=?", (r["id"],))
        self.conn.commit()
        return len(rows)

    # -- per-feed status --------------------------------------------------
    def set_status(self, feed: str, ok: bool, error: str = "", count: int = 0,
                   etag: Optional[str] = None, last_modified: Optional[str] = None) -> None:
        """Record a fetch; conditional-GET validators are kept when not given."""
        old = self.get_status(feed)
        if etag is None:
            etag = old.get("etag", "")
        if last_modified is None:
            last_modified = old.get("last_modified", "")
        self.conn.execute(
            "INSERT OR REPLACE INTO feed_status(feed,last_fetch,ok,error,count,etag,last_modified) "
            "VALUES (?,?,?,?,?,?,?)",
            (feed, _now(), 1 if ok else 0, error[:300], count, etag or "", last_modified or ""))
        self.conn.commit()

    def get_status(self, feed: str) -> Dict[str, object]:
        r = self.conn.execute("SELECT * FROM feed_status WHERE feed=?", (feed,)).fetchone()
        return dict(r) if r else {}

    def all_status(self) -> List[Dict[str, object]]:
        return [dict(r) for r in self.conn.execute("SELECT * FROM feed_status ORDER BY feed")]
