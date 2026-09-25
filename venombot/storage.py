"""JSON snapshot storage with integrity checksums."""

from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path
from typing import List

from venombot.models import Snapshot, TranslatedSnapshot


class SnapshotDatabase:
    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def _safe_ts(iso_ts: str) -> str:
        return iso_ts.replace(":", "-").replace(".", "-")

    def save_snapshot(self, snapshot: Snapshot) -> Path:
        ts = self._safe_ts(snapshot.fetched_at)
        filename = f"{snapshot.source_name}_{snapshot.language}_{ts}.json"
        path = self.root / filename
        path.write_text(
            json.dumps(asdict(snapshot), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return path

    def save_translated(self, translated: TranslatedSnapshot) -> Path:
        ts = self._safe_ts(translated.translated_at)
        filename = f"{translated.original_source_name}_TRANSLATED_{ts}.json"
        path = self.root / filename
        path.write_text(
            json.dumps(asdict(translated), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return path

    def load_all_snapshots(self) -> List[Snapshot]:
        snapshots: List[Snapshot] = []
        fields = Snapshot.__dataclass_fields__
        for path in sorted(self.root.glob("*.json")):
            if "_TRANSLATED_" in path.name:
                continue
            data = json.loads(path.read_text(encoding="utf-8"))
            if "content" in data and "checksum" in data:
                snapshots.append(Snapshot(**{k: data[k] for k in fields if k in data}))
        return snapshots

    def load_all_translated(self) -> List[TranslatedSnapshot]:
        items: List[TranslatedSnapshot] = []
        fields = TranslatedSnapshot.__dataclass_fields__
        for path in sorted(self.root.glob("*_TRANSLATED_*.json")):
            data = json.loads(path.read_text(encoding="utf-8"))
            items.append(TranslatedSnapshot(**{k: data[k] for k in fields if k in data}))
        return items
