"""Screen a subject against the entity store and explain every result.

Two separate questions are answered for each candidate:

* match class — is this listed party the same as the subject?
  (name agreement + DOB / nationality / identifier corroboration)
* severity — if it is, how serious is the listing? (comes from the list
  type, never from keywords in free text)

Nothing here blocks anyone automatically: the strongest outcome is
HOLD_FOR_REVIEW, which tells a human to confirm before acting.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from venombot.countries import countries_in
from venombot.dates import compare_dates, parse_dates
from venombot.entities import Entity
from venombot.matching import NameMatch, compare_tokens
from venombot.normalize import blocking_keys, has_arabic, tokens
from venombot.store import EntityStore

# Every threshold and weight lives here and is stamped into each report, so a
# past result can always be explained by the rules that produced it.
SCORING: Dict[str, Any] = {
    "version": "2026.10-1",
    "name_kind_weight": {"primary": 1.0, "original": 1.0, "alias": 0.97, "weak": 0.85},
    "dob": {"exact": 15, "month": 10, "year": 8, "near": 2, "conflict": -35},
    "nationality": {"match": 5, "conflict": -10},
    "identifier_match": 30,
    "thresholds": {"confirmed_name": 0.92, "probable_name": 0.88, "possible_name": 0.80},
    "common_token_ratio": 0.001,
}

SEVERITY = {
    "SANCTIONS": "CRITICAL",
    "TERRORISM": "CRITICAL",
    "EXPORT_CONTROL": "HIGH",
    "WANTED": "HIGH",
    "CRIME": "HIGH",
    "SANCTIONS_LINKED": "HIGH",
    "DEBARMENT": "MEDIUM",
    "ENFORCEMENT": "MEDIUM",
    "PEP": "MEDIUM",
    "PEP_RCA": "MEDIUM",
    "COUNTER_SANCTIONS": "LOW",
    "CORPORATE": "INFO",
    "OTHER": "LOW",
}
_SEV_RANK = {"CRITICAL": 4, "HIGH": 3, "MEDIUM": 2, "LOW": 1, "INFO": 0, "NONE": -1}
_CLASS_RANK = {"CONFIRMED": 3, "PROBABLE": 2, "POSSIBLE": 1, "DISCOUNTED": 0}


@dataclass
class Subject:
    name: str
    dob: str = ""  # any format; normalised to partial ISO
    countries: List[str] = field(default_factory=list)  # ISO2
    kind: str = "any"  # person | org | any
    identifiers: List[str] = field(default_factory=list)
    aliases: List[str] = field(default_factory=list)

    @staticmethod
    def build(name: str, dob: str = "", country: str = "", kind: str = "any",
              identifiers: Optional[List[str]] = None, aliases: Optional[List[str]] = None) -> "Subject":
        dates = parse_dates(dob)
        return Subject(
            name=name.strip(),
            dob=dates[0] if dates else "",
            countries=countries_in(country),
            kind=kind,
            identifiers=[i for i in (identifiers or []) if i.strip()],
            aliases=[a for a in (aliases or []) if a.strip()],
        )


@dataclass
class Hit:
    entity: Entity
    matched_name: str
    matched_name_kind: str
    query_name: str
    name_score: float
    confidence: float
    match_class: str
    severity: str
    action: str
    evidence: List[str] = field(default_factory=list)
    conflicts: List[str] = field(default_factory=list)
    name_explain: str = ""

    def to_dict(self) -> Dict[str, Any]:
        e = self.entity
        return {
            "uid": e.uid,
            "source": e.source,
            "source_id": e.source_id,
            "list_type": e.list_type,
            "schema": e.schema,
            "caption": e.caption,
            "matched_name": self.matched_name,
            "matched_name_kind": self.matched_name_kind,
            "query_name": self.query_name,
            "name_score": round(self.name_score, 4),
            "confidence": round(self.confidence, 1),
            "match_class": self.match_class,
            "severity": self.severity,
            "action": self.action,
            "evidence": self.evidence,
            "conflicts": self.conflicts,
            "name_explain": self.name_explain,
            "entity": e.to_dict(),
        }


def _norm_id(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9]", "", value).upper()


def _schema_ok(subject: Subject, ent: Entity) -> bool:
    if subject.kind == "person":
        return ent.schema in ("Person", "Unknown")
    if subject.kind == "org":
        return ent.schema != "Person"
    return True


def _action(match_class: str, severity: str) -> str:
    if match_class == "DISCOUNTED":
        return "NO_ACTION"
    if match_class in ("CONFIRMED", "PROBABLE"):
        if severity == "CRITICAL":
            return "HOLD_FOR_REVIEW"
        if severity == "HIGH":
            return "ESCALATE"
        return "ENHANCED_DUE_DILIGENCE" if severity == "MEDIUM" else "REVIEW"
    return "REVIEW"


def evaluate(subject: Subject, ent: Entity, query: str, nm: NameMatch, name_kind: str,
             matched_name: str, token_freq: Dict[str, int], common_limit: int) -> Hit:
    """Combine name agreement with secondary identifiers into one Hit."""
    t = SCORING["thresholds"]
    name_score = nm.score * SCORING["name_kind_weight"].get(name_kind, 0.9)
    confidence = name_score * 100
    evidence: List[str] = [f"name {name_score:.2f} via {name_kind} name '{matched_name}' ({nm.explain()})"]
    conflicts: List[str] = []
    strong = False

    dob_res = compare_dates(subject.dob, ent.birth_dates)
    if dob_res != "unknown":
        confidence += SCORING["dob"][dob_res]
        msg = f"DOB {dob_res}: subject {subject.dob} vs listed {', '.join(ent.birth_dates[:4])}"
        (conflicts if dob_res == "conflict" else evidence).append(msg)
        strong = strong or dob_res == "exact"

    listed_countries = set(ent.nationalities) | set(ent.countries)
    if subject.countries and ent.nationalities:
        if set(subject.countries) & listed_countries:
            confidence += SCORING["nationality"]["match"]
            evidence.append(f"country match: {', '.join(sorted(set(subject.countries) & listed_countries))}")
        else:
            confidence += SCORING["nationality"]["conflict"]
            conflicts.append(f"nationality differs: subject {','.join(subject.countries)} vs listed {','.join(ent.nationalities)}")

    if subject.identifiers and ent.identifiers:
        listed_ids = {_norm_id(i.value): i for i in ent.identifiers}
        for sid in subject.identifiers:
            if _norm_id(sid) in listed_ids:
                ident = listed_ids[_norm_id(sid)]
                confidence += SCORING["identifier_match"]
                evidence.append(f"identifier match: {ident.type} {ident.value}")
                strong = True

    confidence = max(0.0, min(100.0, confidence))

    # A name made only of very common tokens ("Mohammed Ali") is not enough
    # on its own, however perfect the spelling.
    matched_tokens = [a for a, _, _ in nm.pairs]
    common_only = bool(matched_tokens) and all(
        token_freq.get(tok, 0) > common_limit for tok in matched_tokens
    )

    if any(c.startswith("DOB") for c in conflicts):
        match_class = "DISCOUNTED"
    elif name_score >= t["confirmed_name"] and strong and not conflicts:
        match_class = "CONFIRMED"
    elif name_score >= t["probable_name"] and not conflicts and not common_only:
        match_class = "PROBABLE"
    else:
        match_class = "POSSIBLE"
    if common_only and match_class == "POSSIBLE":
        evidence.append("all matched name tokens are very common; needs DOB/ID to corroborate")

    severity = SEVERITY.get(ent.list_type, "LOW")
    return Hit(
        entity=ent, matched_name=matched_name, matched_name_kind=name_kind, query_name=query,
        name_score=name_score, confidence=confidence, match_class=match_class,
        severity=severity, action=_action(match_class, severity),
        evidence=evidence, conflicts=conflicts, name_explain=nm.explain(),
    )


def screen(store: EntityStore, subject: Subject, *, min_name_score: Optional[float] = None,
           sources: Optional[List[str]] = None, include_discounted: bool = True,
           limit: int = 200) -> List[Hit]:
    """Screen one subject (and its aliases) against every stored list.

    @param min_name_score floor for reporting a candidate (default POSSIBLE threshold)
    @param sources restrict to these source keys
    @return hits sorted by class, severity and confidence
    """
    floor = min_name_score if min_name_score is not None else SCORING["thresholds"]["possible_name"]
    is_org = subject.kind == "org"
    # "Common" is relative to what is loaded: 1 in 1,000 entities, at least 25.
    common_limit = max(25, int(store.count() * SCORING["common_token_ratio"]))
    queries = [subject.name] + subject.aliases
    best: Dict[str, Hit] = {}
    for query in queries:
        q_tokens = tokens(query, is_org=is_org)
        if not q_tokens:
            continue
        q_arabic = has_arabic(query)
        keys = sorted(blocking_keys(query, is_org=is_org))
        token_freq = store.key_frequencies(q_tokens)
        min_hits = 2 if len(q_tokens) >= 2 else 1
        cands = store.candidates(keys, min_hits=min_hits)
        ents = store.get([uid for uid, _ in cands])
        for ent in ents:
            if sources and ent.source not in sources:
                continue
            if not _schema_ok(subject, ent):
                continue
            org = is_org or ent.is_org
            top = None
            for n in ent.names:
                nm = compare_tokens(q_tokens, tokens(n.value, is_org=org), is_org=org,
                                    consonantal=q_arabic or has_arabic(n.value))
                weighted = nm.score * SCORING["name_kind_weight"].get(n.kind, 0.9)
                if top is None or weighted > top[0]:
                    top = (weighted, nm, n)
            if top is None or top[0] < floor:
                continue
            hit = evaluate(subject, ent, query, top[1], top[2].kind, top[2].value, token_freq,
                           common_limit)
            prev = best.get(ent.uid)
            if prev is None or hit.confidence > prev.confidence:
                best[ent.uid] = hit
    hits = [h for h in best.values() if include_discounted or h.match_class != "DISCOUNTED"]
    hits.sort(key=lambda h: (-_CLASS_RANK[h.match_class], -_SEV_RANK[h.severity], -h.confidence))
    return hits[:limit]


def overall(hits: List[Hit]) -> Dict[str, Any]:
    """Subject-level rating and recommended next step."""
    live = [h for h in hits if h.match_class in ("CONFIRMED", "PROBABLE")]
    possible = [h for h in hits if h.match_class == "POSSIBLE"]
    reasons: List[str] = []
    if live:
        worst = max(live, key=lambda h: _SEV_RANK[h.severity])
        rating = worst.severity
        decision = worst.action
        for h in live:
            reasons.append(f"{h.match_class} {h.entity.list_type} match on {h.entity.source} ({h.entity.caption})")
    elif possible:
        worst = max(possible, key=lambda h: _SEV_RANK[h.severity])
        rating = "MEDIUM" if _SEV_RANK[worst.severity] >= _SEV_RANK["HIGH"] else "LOW"
        decision = "REVIEW"
        reasons.append(f"{len(possible)} possible match(es) need analyst review")
    else:
        rating, decision = "NONE", "NO_MATCH_IN_SCREENED_LISTS"
        reasons.append("no candidate above threshold in the lists screened")
    return {"rating": rating, "decision": decision, "reasons": reasons[:20]}
