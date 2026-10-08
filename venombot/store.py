"""SQLite entity store with a blocking-key index.

Holding every listed party (official lists + OpenSanctions can exceed a
million entities) in JSON files and scanning them per search does not scale.
SQLite ships with Python, gives atomic per-source replacement, and lets
candidate retrieval hit an index instead of reading every record.
"""

from __future__ import annotations

import json
import sqlite3
import zlib
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, Iterator, List, Optional, Sequence, Tuple

from venombot.entities import Entity
from venombot.normalize import blocking_keys

DEFAULT_DB = Path("venombot_data") / "venombot.db"

# Keys shared by more entities than this ("mohammed", "~mhmd", "trading")
# are too common to drive retrieval on their own.
COMMON_KEY_LIMIT = 40_000

_SCHEMA = """
CREATE TABLE IF NOT EXISTS sources (
    key TEXT PRIMARY KEY,
    name TEXT, jurisdiction TEXT, list_type TEXT, url TEXT, license TEXT,
    fetched_at TEXT, sha256 TEXT, bytes INTEGER, entity_count INTEGER,
    status TEXT, complete INTEGER, error TEXT, source_updated TEXT
);
CREATE TABLE IF NOT EXISTS entities (
    uid TEXT PRIMARY KEY,
    source TEXT NOT NULL,
    schema TEXT, caption TEXT, list_type TEXT,
    data BLOB NOT NULL
);
CREATE INDEX IF NOT EXISTS entities_source ON entities(source);
CREATE TABLE IF NOT EXISTS keys (
    key TEXT NOT NULL,
    uid TEXT NOT NULL,
    PRIMARY KEY (key, uid)
) WITHOUT ROWID;
CREATE INDEX IF NOT EXISTS keys_uid ON keys(uid);
"""


@dataclass
class SourceStatus:
    key: str
    name: str = ""
    jurisdiction: str = ""
    list_type: str = ""
    url: str = ""
    license: str = ""
    fetched_at: str = ""
    sha256: str = ""
    bytes: int = 0
    entity_count: int = 0
    status: str = "never"  # ok | failed | never | skipped
    complete: bool = False
    error: str = ""
    source_updated: str = ""


def _pack(entity: Entity) -> bytes:
    return zlib.compress(json.dumps(entity.to_dict(), ensure_ascii=False).encode("utf-8"), 6)


def _unpack(blob: bytes) -> Entity:
    return Entity.from_dict(json.loads(zlib.decompress(blob).decode("utf-8")))


class EntityStore:
    """Persistent store of parsed list entities."""

    def __init__(self, path: Path = DEFAULT_DB) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(self.path))
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA synchronous=NORMAL")
        self.conn.executescript(_SCHEMA)

    def close(self) -> None:
        self.conn.close()

    @contextmanager
    def _tx(self) -> Iterator[sqlite3.Connection]:
        try:
            self.conn.execute("BEGIN")
            yield self.conn
            self.conn.execute("COMMIT")
        except BaseException:
            self.conn.execute("ROLLBACK")
            raise

    def replace_source(self, source_key: str, entities: Iterable[Entity]) -> int:
        """Atomically replace all entities of one source.

        The old version stays searchable until the new one fully parses, so a
        failed or partial update never leaves a list half-loaded.

        Parsers must yield each uid once (merge split records themselves);
        entities are streamed straight to disk so million-row lists fit in
        memory. A repeated uid replaces the earlier row.

        @param source_key catalog key, e.g. "OFAC_SDN"
        @param entities parsed entities (consumed lazily)
        @return number of distinct entities stored
        """
        seen = set()
        with self._tx() as c:
            c.execute(
                "DELETE FROM keys WHERE uid IN (SELECT uid FROM entities WHERE source = :s)",
                {"s": source_key},
            )
            c.execute("DELETE FROM entities WHERE source = :s", {"s": source_key})
            batch_e: List[Tuple] = []
            batch_k: List[Tuple[str, str]] = []
            for ent in entities:
                if not ent.names:
                    continue
                ent.source = source_key
                uid = ent.uid
                seen.add(uid)
                batch_e.append((uid, source_key, ent.schema, ent.caption, ent.list_type, _pack(ent)))
                keys = set()
                for n in ent.names:
                    keys |= blocking_keys(n.value, is_org=ent.is_org)
                batch_k.extend((k, uid) for k in keys)
                if len(batch_e) >= 5000:
                    self._flush(c, batch_e, batch_k)
                    batch_e, batch_k = [], []
            self._flush(c, batch_e, batch_k)
        return len(seen)

    @staticmethod
    def _flush(c: sqlite3.Connection, ents: List[Tuple], keys: List[Tuple[str, str]]) -> None:
        if ents:
            c.executemany(
                "INSERT OR REPLACE INTO entities(uid, source, schema, caption, list_type, data)"
                " VALUES (?, ?, ?, ?, ?, ?)",
                ents,
            )
        if keys:
            c.executemany("INSERT OR IGNORE INTO keys(key, uid) VALUES (?, ?)", keys)

    def set_status(self, st: SourceStatus) -> None:
        """Record the outcome of the last update attempt for a source."""
        self.conn.execute(
            "INSERT OR REPLACE INTO sources(key, name, jurisdiction, list_type, url, license,"
            " fetched_at, sha256, bytes, entity_count, status, complete, error, source_updated)"
            " VALUES (:key, :name, :jurisdiction, :list_type, :url, :license, :fetched_at,"
            " :sha256, :bytes, :entity_count, :status, :complete, :error, :source_updated)",
            {**st.__dict__, "complete": int(st.complete)},
        )
        self.conn.commit()

    def status(self, key: Optional[str] = None) -> List[SourceStatus]:
        """Status rows for all sources (or one)."""
        sql = "SELECT * FROM sources"
        params: Dict[str, str] = {}
        if key:
            sql += " WHERE key = :k"
            params["k"] = key
        cur = self.conn.execute(sql + " ORDER BY key", params)
        cols = [d[0] for d in cur.description]
        out = []
        for row in cur.fetchall():
            data = dict(zip(cols, row))
            data["complete"] = bool(data["complete"])
            data["bytes"] = int(data["bytes"] or 0)
            data["entity_count"] = int(data["entity_count"] or 0)
            out.append(SourceStatus(**{k: (v if v is not None else "") for k, v in data.items()}))
        return out

    def key_frequencies(self, keys: Sequence[str]) -> Dict[str, int]:
        """How many entities carry each blocking key (rarity signal)."""
        if not keys:
            return {}
        marks = ",".join("?" * len(keys))
        cur = self.conn.execute(
            f"SELECT key, COUNT(*) FROM keys WHERE key IN ({marks}) GROUP BY key", list(keys)
        )
        freq = {k: 0 for k in keys}
        freq.update({k: int(n) for k, n in cur.fetchall()})
        return freq

    def candidates(self, keys: Sequence[str], min_hits: int, limit: int = 3000) -> List[Tuple[str, int]]:
        """Entities sharing at least `min_hits` blocking keys with the query.

        Very common keys are skipped unless nothing rarer is available, which
        keeps "Mohammed Ali" from pulling in a hundred thousand rows.

        @return (uid, hits) pairs, most hits first
        """
        if not keys:
            return []
        freq = self.key_frequencies(keys)
        usable = [k for k in keys if 0 < freq[k] <= COMMON_KEY_LIMIT]
        if not usable:
            usable = sorted((k for k in keys if freq[k] > 0), key=lambda k: freq[k])[:4]
        if not usable:
            return []
        need = min(min_hits, len(usable))
        marks = ",".join("?" * len(usable))
        cur = self.conn.execute(
            f"SELECT uid, COUNT(*) AS hits FROM keys WHERE key IN ({marks})"
            f" GROUP BY uid HAVING hits >= ? ORDER BY hits DESC LIMIT ?",
            [*usable, need, limit],
        )
        return [(uid, int(h)) for uid, h in cur.fetchall()]

    def get(self, uids: Sequence[str]) -> List[Entity]:
        """Load full entities by uid (order not guaranteed)."""
        out: List[Entity] = []
        for i in range(0, len(uids), 500):
            chunk = list(uids[i : i + 500])
            marks = ",".join("?" * len(chunk))
            cur = self.conn.execute(f"SELECT data FROM entities WHERE uid IN ({marks})", chunk)
            out.extend(_unpack(row[0]) for row in cur.fetchall())
        return out

    def count(self, source_key: Optional[str] = None) -> int:
        if source_key:
            cur = self.conn.execute("SELECT COUNT(*) FROM entities WHERE source = :s", {"s": source_key})
        else:
            cur = self.conn.execute("SELECT COUNT(*) FROM entities")
        return int(cur.fetchone()[0])

    def iter_source(self, source_key: str) -> Iterator[Entity]:
        cur = self.conn.execute("SELECT data FROM entities WHERE source = :s", {"s": source_key})
        for (blob,) in cur:
            yield _unpack(blob)
