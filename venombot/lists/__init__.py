"""Registry of structured list sources.

Each module in this package declares ``SOURCES: List[ListSource]``; the
registry discovers them automatically, so adding a jurisdiction means adding
one module and never touching a central table.

A ListSource names one or more files to download (``urls``) and a parser that
turns those files into Entity records.
"""

from __future__ import annotations

import importlib
import os
import pkgutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, Iterable, List, Optional

from venombot.entities import Entity

# parser(paths_by_name, source) -> entities
Parser = Callable[[Dict[str, Path], "ListSource"], Iterable[Entity]]


@dataclass
class ListSource:
    key: str
    name: str
    jurisdiction: str  # ISO2, "EU", "UN", "GLOBAL", or issuing body
    list_type: str  # one of venombot.entities.LIST_TYPES
    urls: Dict[str, str]  # logical file name -> URL
    parser: Parser
    homepage: str = ""
    license: str = "public"  # public | OGL | CC-BY | CC-BY-NC | terms
    # Groups let users update slices: "core", "sanctions", "pep", "wanted",
    # "debarment", "enforcement", "mena", "opensanctions", ...
    groups: List[str] = field(default_factory=list)
    # Env var holding an API key; source is skipped when unset.
    api_key_env: str = ""
    headers: Dict[str, str] = field(default_factory=dict)
    # Expected refresh cadence; reports flag lists older than this.
    max_age_days: int = 7
    notes: str = ""
    timeout: int = 180

    def api_key(self) -> Optional[str]:
        return os.environ.get(self.api_key_env) if self.api_key_env else None

    @property
    def needs_key(self) -> bool:
        return bool(self.api_key_env)


_REGISTRY: Optional[Dict[str, ListSource]] = None


def _discover() -> Dict[str, ListSource]:
    found: Dict[str, ListSource] = {}
    pkg_path = [str(Path(__file__).parent)]
    for mod in sorted(pkgutil.iter_modules(pkg_path), key=lambda m: m.name):
        if mod.name.startswith("_"):
            continue
        module = importlib.import_module(f"{__name__}.{mod.name}")
        for src in getattr(module, "SOURCES", []):
            if src.key in found:
                raise ValueError(f"duplicate list source key {src.key} in {mod.name}")
            found[src.key] = src
    return found


def all_list_sources() -> Dict[str, ListSource]:
    """All registered list sources keyed by source key."""
    global _REGISTRY
    if _REGISTRY is None:
        _REGISTRY = _discover()
    return _REGISTRY


def select_sources(
    keys: Optional[List[str]] = None,
    groups: Optional[List[str]] = None,
) -> List[ListSource]:
    """Pick sources by explicit key and/or group (union). None -> "core"."""
    reg = all_list_sources()
    if not keys and not groups:
        groups = ["core"]
    chosen: Dict[str, ListSource] = {}
    for k in keys or []:
        if k not in reg:
            raise KeyError(f"unknown source {k}")
        chosen[k] = reg[k]
    for g in groups or []:
        for s in reg.values():
            if g == "all" or g in s.groups or g.upper() == s.list_type or g.upper() == s.jurisdiction.upper():
                chosen[s.key] = s
    return sorted(chosen.values(), key=lambda s: s.key)
