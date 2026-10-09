"""Sentence-level confirmation of a subject's name inside stored articles.

FTS only proposes candidates. An article counts only if the name appears
inside one sentence ("Jane" in one sentence and "Doe" in another is not a
mention), which is what keeps adverse-media noise down.
"""

from __future__ import annotations

import re
from typing import List, Optional, Sequence, Tuple

from venombot.evidence import Evidence, topics_in
from venombot.feeds.store import ArticleStore
from venombot.matching import compare_names
from venombot.normalize import tokens as _tokens

_SENT_RE = re.compile(r"(?<=[.!?؟;])\s+|\n+|\s[|–—]\s")
_WORD_RE = re.compile(r"[^\W_]+(?:['’.-][^\W_]+)*", re.U)
THRESHOLD = 0.85


def split_sentences(text: str) -> List[str]:
    """Split on sentence punctuation (Latin and Arabic), dropping empties."""
    return [s.strip() for s in _SENT_RE.split(text or "") if s and s.strip()]


def _best_window(name: str, sentence: str, is_org: bool, min_tokens: int) -> Tuple[float, str]:
    words = _WORD_RE.findall(sentence)
    n = max(1, len(name.split()))
    best, best_win = 0.0, ""
    for size in {n, n + 1}:
        for i in range(0, max(1, len(words) - size + 1)):
            win = " ".join(words[i:i + size])
            m = compare_names(name, win, is_org=is_org)
            if m.score < THRESHOLD:
                continue
            if sum(1 for a, b, _ in m.pairs if len(a) > 1) < min_tokens:
                continue
            if m.score > best:
                best, best_win = m.score, win
    return best, best_win


def search_articles(store: ArticleStore, subject, max_results: int = 25,
                    days: Optional[int] = None, feed_kinds: Optional[dict] = None) -> List[Evidence]:
    """Evidence for articles that mention ``subject`` (name or aliases).

    @param subject venombot.screening.Subject (name, aliases, kind)
    @param feed_kinds optional {feed key: kind}; defaults to registry lookup
    @return adverse-topic hits first, then newest first
    """
    if feed_kinds is None:
        try:
            from venombot.feeds import all_feeds
            feed_kinds = {k: f.kind for k, f in all_feeds().items()}
        except Exception:  # noqa: BLE001
            feed_kinds = {}
    is_org = getattr(subject, "kind", "any") == "org"
    names = [subject.name] + list(getattr(subject, "aliases", []) or [])
    results: List[Evidence] = []
    seen = set()
    for name in names:
        words = [w for w in _WORD_RE.findall(name) if len(w) > 1]
        if not words:
            continue
        min_tokens = 2 if len(_tokens(name, is_org=is_org)) >= 2 else 1
        # Any-token OR keeps recall (word order, transliteration of one
        # token); the sentence check below restores precision.
        for row in store.candidates(words, days=days):
            if row["id"] in seen:
                continue
            for sent in split_sentences((row["title"] or "") + ". " + (row["summary"] or "")):
                score, win = _best_window(name, sent, is_org, min_tokens)
                if not score:
                    continue
                seen.add(row["id"])
                kind = feed_kinds.get(row["feed"], "adverse_media")
                if kind not in ("adverse_media", "enforcement"):
                    kind = "adverse_media"
                results.append(Evidence(
                    provider=row["feed"], kind=kind, title=row["title"] or "", url=row["url"] or "",
                    snippet=sent[:500], published=row["published"] or "", language=row["language"] or "",
                    matched_name=win, name_score=score, topics=topics_in(sent)))
                break
    results.sort(key=lambda e: e.published, reverse=True)
    results.sort(key=lambda e: 0 if e.topics else 1)
    return results[:max_results]
