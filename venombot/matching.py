"""Order-insensitive fuzzy name comparison.

The score answers "how well does the screened name agree with this listed
name?" on 0..1. It is deliberately independent of DOB/country evidence, which
is combined later in screening.py so that every factor stays explainable.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Sequence, Tuple

from venombot.normalize import has_arabic, skeleton, tokens


def jaro_winkler(a: str, b: str, prefix_scale: float = 0.1) -> float:
    """Jaro-Winkler similarity in [0, 1]."""
    if a == b:
        return 1.0
    la, lb = len(a), len(b)
    if not la or not lb:
        return 0.0
    window = max(max(la, lb) // 2 - 1, 0)
    a_flags = [False] * la
    b_flags = [False] * lb
    matches = 0
    for i, ch in enumerate(a):
        lo, hi = max(0, i - window), min(i + window + 1, lb)
        for j in range(lo, hi):
            if not b_flags[j] and b[j] == ch:
                a_flags[i] = b_flags[j] = True
                matches += 1
                break
    if not matches:
        return 0.0
    transpositions = 0
    j = 0
    for i in range(la):
        if a_flags[i]:
            while not b_flags[j]:
                j += 1
            if a[i] != b[j]:
                transpositions += 1
            j += 1
    m = float(matches)
    jaro = (m / la + m / lb + (m - transpositions / 2) / m) / 3
    prefix = 0
    for x, y in zip(a[:4], b[:4]):
        if x != y:
            break
        prefix += 1
    return jaro + prefix * prefix_scale * (1 - jaro)


def token_similarity(a: str, b: str, *, consonantal: bool = False) -> float:
    """Similarity of two normalised tokens, tolerant of transliteration.

    @param consonantal one side was transliterated from Arabic script, which
           omits short vowels, so an equal consonant skeleton is accepted even
           when the spelled-out forms look different ("aimn" vs "ayman")
    """
    if a == b:
        return 1.0
    # A single initial ("V.") is compatible with any token starting with it,
    # but it is weak evidence.
    if len(a) == 1 or len(b) == 1:
        return 0.8 if a[0] == b[0] else 0.0
    sa, sb = skeleton(a), skeleton(b)
    jw = jaro_winkler(a, b)
    # Requiring a decent spelled-out similarity keeps Putin/Potanin ("ptn")
    # apart while still merging Mohammed/Muhammad.
    if sa and sa == sb and len(sa) >= 2 and (consonantal or jw >= 0.80):
        return max(jw, 0.92)
    # Jaro-Winkler is generous on very short strings ("un" vs "jung" = 0.83).
    if min(len(a), len(b)) <= 3 and jw < 0.9:
        return min(jw, 0.75)
    return jw


@dataclass
class NameMatch:
    score: float
    subject_tokens: List[str]
    candidate_tokens: List[str]
    pairs: List[Tuple[str, str, float]] = field(default_factory=list)
    unmatched_subject: List[str] = field(default_factory=list)
    unmatched_candidate: List[str] = field(default_factory=list)

    def explain(self) -> str:
        parts = [f"{a}~{b}:{s:.2f}" for a, b, s in self.pairs]
        if self.unmatched_subject:
            parts.append("missing:" + ",".join(self.unmatched_subject))
        if self.unmatched_candidate:
            parts.append("extra:" + ",".join(self.unmatched_candidate))
        return " ".join(parts)


# Below this a token pair is treated as "not the same token" at all.
_PAIR_FLOOR = 0.80
# Extra listed tokens on a person (middle names, patronymics) cost little:
# lists record full legal names while people are screened by common name.
# On organisations every extra word distinguishes a different company.
_SUBJECT_WEIGHT = {"person": 0.85, "org": 0.5}


def compare_tokens(subj: Sequence[str], cand: Sequence[str], *, is_org: bool = False,
                   consonantal: bool = False) -> NameMatch:
    """Greedy best-pair alignment of two token lists, ignoring order.

    @param is_org weigh unmatched listed tokens as distinguishing words
    @param consonantal one side came from Arabic script (see token_similarity)
    """
    if not subj or not cand:
        return NameMatch(0.0, list(subj), list(cand))
    scored = []
    for i, a in enumerate(subj):
        for j, b in enumerate(cand):
            s = token_similarity(a, b, consonantal=consonantal)
            if s >= _PAIR_FLOOR:
                scored.append((s, i, j))
    scored.sort(key=lambda x: (-x[0], -len(subj[x[1]])))
    used_i, used_j, pairs = set(), set(), []
    for s, i, j in scored:
        if i in used_i or j in used_j:
            continue
        used_i.add(i)
        used_j.add(j)
        pairs.append((subj[i], cand[j], s))

    # Length-weighted so a matched surname counts more than a matched initial.
    def coverage(toks: Sequence[str], side: int) -> float:
        total = sum(len(t) for t in toks)
        got = sum(len(p[side]) * p[2] for p in pairs)
        return got / total if total else 0.0

    s_subj = coverage(subj, 0)
    s_cand = coverage(cand, 1)
    w = _SUBJECT_WEIGHT["org" if is_org else "person"]
    score = w * s_subj + (1 - w) * s_cand
    # A multi-token subject needs at least two full-token agreements; an
    # initial ("P.") or a shared first name alone is never a strong match.
    full_pairs = sum(1 for a, b, _ in pairs if len(a) > 1 and len(b) > 1)
    needed = min(2, sum(1 for t in subj if len(t) > 1))
    if full_pairs < needed:
        score = min(score, 0.6)
    return NameMatch(
        score=round(score, 4),
        subject_tokens=list(subj),
        candidate_tokens=list(cand),
        pairs=pairs,
        unmatched_subject=[t for k, t in enumerate(subj) if k not in used_i],
        unmatched_candidate=[t for k, t in enumerate(cand) if k not in used_j],
    )


def compare_names(subject: str, candidate: str, *, is_org: bool = False) -> NameMatch:
    """Compare two raw names after normalisation."""
    return compare_tokens(
        tokens(subject, is_org=is_org),
        tokens(candidate, is_org=is_org),
        is_org=is_org,
        consonantal=has_arabic(subject) or has_arabic(candidate),
    )
