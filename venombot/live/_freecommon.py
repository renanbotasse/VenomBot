"""Shared helpers for the keyless per-subject providers (registries, courts, ...).

Underscore-prefixed so ``all_providers()`` does not treat it as a provider
module. Nothing here touches the network: each provider module calls
``venombot.fetch.get_text`` itself so tests can patch it per module.
"""

from __future__ import annotations

import json
import os
import re
from typing import Any, Dict, Iterable, List, Optional, Tuple

from venombot.evidence import Evidence, topics_in
from venombot.matching import compare_names
from venombot.normalize import tokens
from venombot.screening import Subject

THRESHOLD = 0.85
DEFAULT_CONTACT = "VenomBot (set VENOMBOT_CONTACT)"


def contact_ua() -> str:
    """User-Agent naming a contact, as SEC/Wikimedia ask automated clients to do."""
    contact = os.environ.get("VENOMBOT_CONTACT", "").strip()
    return f"VenomBot/1.0 ({contact})" if contact else DEFAULT_CONTACT


def parse_json(text: str) -> Any:
    """json.loads that raises a clear error when an API answers with HTML."""
    try:
        return json.loads(text)
    except ValueError as exc:
        raise RuntimeError(f"expected JSON, got {text[:80]!r}") from exc


def is_org(subject: Subject) -> bool:
    return subject.kind == "org"


def subject_names(subject: Subject, aliases: int = 2) -> List[str]:
    """Primary name plus a few aliases (queries stay cheap, matching tolerant)."""
    out = [subject.name]
    for a in subject.aliases[:aliases]:
        if a not in out:
            out.append(a)
    return out


def best_name(subject: Subject, candidates: Iterable[str]) -> Tuple[str, float]:
    """Best (candidate, score) over the subject name/aliases x candidates."""
    best, top = "", 0.0
    org = is_org(subject)
    for cand in candidates:
        if not cand:
            continue
        for sname in subject_names(subject):
            s = compare_names(sname, cand, is_org=org).score
            if s > top:
                best, top = cand, s
    return best, top


def find_in_text(subject: Subject, text: str) -> Tuple[str, float, str]:
    """Look for the subject name inside free text with a sliding word window.

    Titles and abstracts hold the name inside a longer sentence, so comparing
    the whole string would never reach the threshold.

    @return (matched words, score, sentence containing them)
    """
    if not text:
        return "", 0.0, ""
    org = is_org(subject)
    words = re.findall(r"\S+", text)
    top, best, at = 0.0, "", 0
    for sname in subject_names(subject):
        n = max(1, len(tokens(sname, is_org=org)))
        for size in {n, n + 1, max(1, n - 1)}:
            for i in range(0, max(1, len(words) - size + 1)):
                chunk = " ".join(words[i:i + size])
                s = compare_names(sname, chunk, is_org=org).score
                if s > top:
                    top, best, at = s, chunk, i
                    if s >= 1.0:
                        break
    if top <= 0.0:
        return "", 0.0, ""
    return best, top, sentence_around(text, best)


def sentence_around(text: str, needle: str, width: int = 280) -> str:
    """The sentence (or a clipped window) of ``text`` that contains ``needle``."""
    idx = text.find(needle.split(" ")[0]) if needle else -1
    if idx < 0:
        return clip(text, width)
    start = max(text.rfind(". ", 0, idx) + 2, idx - width // 2, 0)
    end = min(len(text), start + width)
    return text[start:end].strip()


def clip(text: str, n: int = 300) -> str:
    text = re.sub(r"\s+", " ", text or "").strip()
    return text if len(text) <= n else text[: n - 1] + "…"


def strip_tags(html: str) -> str:
    import html as _html
    return clip(_html.unescape(re.sub(r"<[^>]+>", " ", html or "")), 400)


def make(provider: str, kind: str, title: str, *, url: str = "", snippet: str = "",
         published: str = "", language: str = "", matched: str = "", score: float = 0.0,
         extra: Optional[Dict[str, Any]] = None, topics: bool = True) -> Evidence:
    """Build an Evidence; adverse topics come from title+snippet only if asked."""
    return Evidence(
        provider=provider, kind=kind, title=clip(title, 240), url=url, snippet=clip(snippet),
        published=published, language=language, matched_name=matched,
        name_score=round(score, 3),
        topics=topics_in(f"{title} {snippet}") if topics else [],
        extra={k: v for k, v in (extra or {}).items() if v not in (None, "", [])},
    )


def iso_date(value: Any) -> str:
    """First 10 chars of an ISO-ish timestamp, '' when it does not look like one."""
    m = re.match(r"\d{4}-\d{2}-\d{2}", str(value or ""))
    return m.group(0) if m else ""


def cap(items: List[Evidence], n: int) -> List[Evidence]:
    """Best-scoring first, capped, so noisy endpoints never flood a dossier."""
    return sorted(items, key=lambda e: -e.name_score)[:n]
