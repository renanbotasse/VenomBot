"""Per-subject lookups against search APIs (adverse media, courts, registries).

Bulk lists are downloaded and indexed (``venombot.lists``). Many useful
sources cannot be downloaded — they are search endpoints you query with the
subject's name. A LiveProvider wraps one such endpoint and returns Evidence.

Privacy: a live lookup sends the subject's name to a third party. Providers
are therefore opt-in (``screen --live``) and every query is recorded in the
dossier. Providers needing an API key declare ``api_key_env`` and are skipped
when it is not set.

Modules in this package declare ``PROVIDERS: List[LiveProvider]`` and are
discovered automatically.
"""

from __future__ import annotations

import importlib
import os
import pkgutil
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, List, Optional

from venombot.evidence import Evidence
from venombot.screening import Subject

# search(subject, key) -> evidence
SearchFn = Callable[[Subject, Optional[str]], List[Evidence]]


@dataclass
class LiveProvider:
    key: str
    name: str
    kind: str  # an evidence kind
    jurisdiction: str
    search: SearchFn
    url: str = ""  # documentation / endpoint template, for the report
    license: str = "terms"
    api_key_env: str = ""
    groups: List[str] = field(default_factory=list)  # e.g. ["media", "court", "corporate", "free"]
    # Minimum seconds between calls to this provider (politeness / limits).
    min_interval: float = 1.0
    notes: str = ""

    def api_key(self) -> Optional[str]:
        return os.environ.get(self.api_key_env) if self.api_key_env else None

    @property
    def available(self) -> bool:
        return not self.api_key_env or bool(self.api_key())


@dataclass
class LookupRecord:
    provider: str
    ok: bool
    count: int = 0
    error: str = ""
    skipped: str = ""
    seconds: float = 0.0


_REGISTRY: Optional[Dict[str, LiveProvider]] = None


def all_providers() -> Dict[str, LiveProvider]:
    """All registered live providers keyed by provider key."""
    global _REGISTRY
    if _REGISTRY is None:
        found: Dict[str, LiveProvider] = {}
        for mod in sorted(pkgutil.iter_modules([str(Path(__file__).parent)]), key=lambda m: m.name):
            if mod.name.startswith("_"):
                continue
            module = importlib.import_module(f"{__name__}.{mod.name}")
            for p in getattr(module, "PROVIDERS", []):
                if p.key in found:
                    raise ValueError(f"duplicate live provider {p.key} in {mod.name}")
                found[p.key] = p
        _REGISTRY = found
    return _REGISTRY


def select_providers(keys: Optional[List[str]] = None, groups: Optional[List[str]] = None) -> List[LiveProvider]:
    """Pick providers by key and/or group; default is every available provider."""
    reg = all_providers()
    if not keys and not groups:
        return sorted((p for p in reg.values() if p.available), key=lambda p: p.key)
    chosen: Dict[str, LiveProvider] = {}
    for k in keys or []:
        if k not in reg:
            raise KeyError(f"unknown live provider {k}")
        chosen[k] = reg[k]
    for g in groups or []:
        for p in reg.values():
            if g == "all" or g in p.groups or g == p.kind or g.upper() == p.jurisdiction.upper():
                chosen[p.key] = p
    return sorted(chosen.values(), key=lambda p: p.key)


_last_call: Dict[str, float] = {}


def lookup(subject: Subject, providers: List[LiveProvider]) -> "tuple[List[Evidence], List[LookupRecord]]":
    """Run each provider for the subject; one failing provider never stops the rest.

    @return (evidence, per-provider records for the audit trail)
    """
    evidence: List[Evidence] = []
    records: List[LookupRecord] = []
    for p in providers:
        if not p.available:
            records.append(LookupRecord(p.key, False, skipped=f"set {p.api_key_env} to enable"))
            continue
        wait = p.min_interval - (time.monotonic() - _last_call.get(p.key, 0.0))
        if wait > 0:
            time.sleep(wait)
        start = time.monotonic()
        try:
            found = p.search(subject, p.api_key()) or []
            _last_call[p.key] = time.monotonic()
            for ev in found:
                ev.provider = p.key
            evidence.extend(found)
            records.append(LookupRecord(p.key, True, len(found), seconds=round(time.monotonic() - start, 2)))
        except Exception as exc:  # noqa: BLE001 - network/API errors are expected
            _last_call[p.key] = time.monotonic()
            records.append(LookupRecord(p.key, False, error=f"{type(exc).__name__}: {exc}"[:300],
                                        seconds=round(time.monotonic() - start, 2)))
    return evidence, records
