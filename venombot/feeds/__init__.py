"""Adverse-media and regulator-press feed layer (RSS/Atom, stdlib only).

Feeds are *context*, not list hits: they produce Evidence, never matches.
Each ``sources_*.py`` module declares ``FEEDS: List[FeedSource]`` and is
discovered automatically, like venombot.lists.
"""

from __future__ import annotations

import importlib
import pkgutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

KINDS = ("adverse_media", "enforcement", "news")


@dataclass
class FeedSource:
    key: str
    name: str
    url: str
    language: str = "en"
    region: str = ""
    kind: str = "news"  # adverse_media | enforcement | news
    groups: List[str] = field(default_factory=list)
    license: str = "terms"
    max_age_days: int = 2
    notes: str = ""
    # Extra request headers (e.g. a UA the publisher accepts).
    headers: Dict[str, str] = field(default_factory=dict)
    # SEC asks for a contact in the User-Agent; set env VENOMBOT_CONTACT.
    needs_contact: bool = False


_REGISTRY: Optional[Dict[str, FeedSource]] = None


def _discover() -> Dict[str, FeedSource]:
    found: Dict[str, FeedSource] = {}
    for mod in sorted(pkgutil.iter_modules([str(Path(__file__).parent)]), key=lambda m: m.name):
        if not mod.name.startswith("sources_"):
            continue
        module = importlib.import_module(f"{__name__}.{mod.name}")
        for f in getattr(module, "FEEDS", []):
            if f.key in found:
                raise ValueError(f"duplicate feed key {f.key} in {mod.name}")
            found[f.key] = f
    return found


def all_feeds() -> Dict[str, FeedSource]:
    """All registered feeds keyed by feed key."""
    global _REGISTRY
    if _REGISTRY is None:
        _REGISTRY = _discover()
    return _REGISTRY


def select_feeds(keys: Optional[List[str]] = None, groups: Optional[List[str]] = None) -> List[FeedSource]:
    """Pick feeds by key and/or group (union); nothing given -> all feeds."""
    reg = all_feeds()
    if not keys and not groups:
        return sorted(reg.values(), key=lambda f: f.key)
    chosen: Dict[str, FeedSource] = {}
    for k in keys or []:
        if k not in reg:
            raise KeyError(f"unknown feed {k}")
        chosen[k] = reg[k]
    for g in groups or []:
        for f in reg.values():
            if g == "all" or g in f.groups or g == f.kind or g.lower() == f.region.lower() \
                    or g.lower() == f.language.lower():
                chosen[f.key] = f
    return sorted(chosen.values(), key=lambda f: f.key)


from venombot.feeds.store import ArticleStore  # noqa: E402
from venombot.feeds.update import update_feeds  # noqa: E402
from venombot.feeds.search import search_articles  # noqa: E402

__all__ = ["FeedSource", "all_feeds", "select_feeds", "ArticleStore", "update_feeds", "search_articles"]
