"""Wikidata and LittleSis identity / PEP-context lookups.

Wikidata evidence is *identity or pep_info only*: label, description, QID,
dates of birth, positions held. It disambiguates and flags possible PEPs; it
never carries adverse topics. Positions with end dates let a reviewer apply a
"former PEP" rule.
"""

from __future__ import annotations

import re
from typing import Dict, List, Optional, Tuple
from urllib.parse import urlencode

from venombot.evidence import Evidence
from venombot.fetch import get_text
from venombot.live import LiveProvider
from venombot.live._freecommon import (
    THRESHOLD, best_name, cap, clip, contact_ua, iso_date, make, parse_json, subject_names,
)
from venombot.normalize import has_arabic
from venombot.screening import Subject

API = "https://www.wikidata.org/w/api.php"
WDQS = "https://query.wikidata.org/sparql"

# Description words that suggest public office; used only to choose pep_info
# over identity, never to assert PEP status.
_OFFICE = re.compile(
    r"\b(president|prime minister|minister|emir|king|queen|sultan|crown prince|prince|princess|senator|"
    r"member of (?:the )?(?:parliament|congress|assembly)|governor|mayor|ambassador|judge|general|"
    r"chairman|politician|diplomat|head of state|speaker|commissioner|deputy|secretary of state)\b", re.I)


def _headers() -> Dict[str, str]:
    return {"User-Agent": contact_ua(), "Accept": "application/json"}


def _json(url: str, **kw):
    return parse_json(get_text(url, headers=kw.pop("headers", _headers()), **kw))


def _search(subject: Subject, name: str) -> List[dict]:
    lang = "ar" if has_arabic(name) else "en"
    url = API + "?" + urlencode({"action": "wbsearchentities", "search": name, "language": lang,
                                 "uselang": lang, "type": "item", "limit": 7, "format": "json"})
    return _json(url).get("search", [])


def _candidates(subject: Subject, names: List[str]) -> List[Tuple[dict, str, float]]:
    """Search results whose label or matched text agrees with the subject name."""
    seen, out = set(), []
    for n in names:
        for r in _search(subject, n):
            qid = r.get("id")
            if qid in seen:
                continue
            texts = [r.get("label") or "", (r.get("match") or {}).get("text") or ""]
            matched, score = best_name(subject, texts)
            if score >= THRESHOLD:
                seen.add(qid)
                out.append((r, matched, score))
    return out[:5]


def _kind(description: str, positions: bool = False) -> str:
    return "pep_info" if positions or _OFFICE.search(description or "") else "identity"


def _evidence(provider: str, r: dict, matched: str, score: float, extra: Optional[dict] = None,
              positions: bool = False, snippet: str = "") -> Evidence:
    qid = r.get("id", "")
    label, desc = r.get("label") or matched, r.get("description") or ""
    ex = {"qid": qid, "description": desc}
    ex.update(extra or {})
    return make(provider, _kind(desc, positions), f"{label} ({qid})", url=f"https://www.wikidata.org/wiki/{qid}",
                snippet=snippet or f"{label}: {desc}", matched=matched, score=score, extra=ex, topics=False)


def search_entities(subject: Subject, key: Optional[str]) -> List[Evidence]:
    """wbsearchentities on the primary name only."""
    return [_evidence("WIKIDATA_WBSEARCHENTITIES", r, m, s) for r, m, s in _candidates(subject, [subject.name])]


def entity_search(subject: Subject, key: Optional[str]) -> List[Evidence]:
    """Same search over aliases and the Arabic-script form too (finds more spellings)."""
    return [_evidence("WIKIDATA_ENTITY_SEARCH", r, m, s) for r, m, s in _candidates(subject, subject_names(subject, 3))]


# --- claims ----------------------------------------------------------------

def _claim_ids(entity: dict, prop: str) -> List[str]:
    out = []
    for c in (entity.get("claims") or {}).get(prop, []):
        v = ((c.get("mainsnak") or {}).get("datavalue") or {}).get("value")
        if isinstance(v, dict) and v.get("id"):
            out.append(v["id"])
    return out


def _time(snak_list: Optional[list]) -> str:
    for s in snak_list or []:
        t = ((s.get("datavalue") or {}).get("value") or {}).get("time", "")
        m = re.match(r"[+-](\d{4}-\d{2}-\d{2})", t)
        if m:
            return m.group(1)[:10]
    return ""


def _dob(entity: dict) -> str:
    for c in (entity.get("claims") or {}).get("P569", []):
        t = (((c.get("mainsnak") or {}).get("datavalue") or {}).get("value") or {}).get("time", "")
        m = re.match(r"\+(\d{4}-\d{2}-\d{2})", t)
        if m:
            return m.group(1)
    return ""


def _positions(entity: dict) -> List[dict]:
    out = []
    for c in (entity.get("claims") or {}).get("P39", []):
        pid = (((c.get("mainsnak") or {}).get("datavalue") or {}).get("value") or {}).get("id")
        q = c.get("qualifiers") or {}
        if pid:
            out.append({"qid": pid, "start": _time(q.get("P580")), "end": _time(q.get("P582"))})
    return out


def _labels(qids: List[str]) -> Dict[str, str]:
    qids = sorted(set(qids))[:50]
    if not qids:
        return {}
    url = API + "?" + urlencode({"action": "wbgetentities", "ids": "|".join(qids), "props": "labels",
                                 "languages": "en", "format": "json"})
    ents = _json(url).get("entities", {})
    return {q: ((e.get("labels") or {}).get("en") or {}).get("value", q) for q, e in ents.items()}


def get_entities(subject: Subject, key: Optional[str]) -> List[Evidence]:
    """Search, then fetch claims: date of birth, citizenship, positions held, aliases."""
    cands = _candidates(subject, [subject.name])
    if not cands:
        return []
    url = API + "?" + urlencode({"action": "wbgetentities", "ids": "|".join(r["id"] for r, _, _ in cands),
                                 "props": "labels|aliases|claims", "languages": "en|ar", "format": "json"})
    ents = _json(url).get("entities", {})
    refs = [p["qid"] for e in ents.values() for p in _positions(e)] + [q for e in ents.values() for q in _claim_ids(e, "P27")]
    labels = _labels(refs)
    out = []
    for r, matched, score in cands:
        e = ents.get(r["id"]) or {}
        pos = [dict(p, label=labels.get(p["qid"], p["qid"])) for p in _positions(e)]
        cit = [labels.get(q, q) for q in _claim_ids(e, "P27")]
        aliases = [a.get("value") for lang in (e.get("aliases") or {}).values() for a in lang][:8]
        current = [p for p in pos if not p["end"]]
        bits = [f"{p['label']} ({p['start'] or '?'}-{p['end'] or 'present'})" for p in pos[:6]]
        snippet = f"{r.get('label')}: {r.get('description') or ''}" + (f"; positions: {'; '.join(bits)}" if bits else "")
        out.append(_evidence("WIKIDATA_WBGETENTITIES", r, matched, score, positions=bool(pos), snippet=snippet,
                             extra={"date_of_birth": _dob(e), "citizenship": cit, "positions": pos,
                                    "current_positions": len(current), "aliases": aliases,
                                    "has_family_claims": any(p in (e.get("claims") or {}) for p in ("P22", "P25", "P26", "P40", "P3373"))}))
    return out


def sparql_positions(subject: Subject, key: Optional[str]) -> List[Evidence]:
    """Search, then one SPARQL query for position-held statements with term dates."""
    cands = _candidates(subject, [subject.name])[:3]
    if not cands:
        return []
    values = " ".join(f"wd:{r['id']}" for r, _, _ in cands)
    query = ("SELECT ?p ?posLabel ?start ?end WHERE { VALUES ?p { %s } ?p p:P39 ?st . ?st ps:P39 ?pos . "
             "OPTIONAL { ?st pq:P580 ?start } OPTIONAL { ?st pq:P582 ?end } "
             'SERVICE wikibase:label { bd:serviceParam wikibase:language "en". } } LIMIT 100' % values)
    url = WDQS + "?" + urlencode({"query": query, "format": "json"})
    bindings = _json(url, headers=dict(_headers(), Accept="application/sparql-results+json")).get("results", {}).get("bindings", [])
    by: Dict[str, List[dict]] = {}
    for b in bindings:
        qid = b["p"]["value"].rsplit("/", 1)[-1]
        by.setdefault(qid, []).append({"label": b.get("posLabel", {}).get("value", ""),
                                       "start": iso_date(b.get("start", {}).get("value")),
                                       "end": iso_date(b.get("end", {}).get("value"))})
    out = []
    for r, matched, score in cands:
        pos = by.get(r["id"], [])
        if not pos:
            continue
        former = all(p["end"] for p in pos)
        bits = [f"{p['label']} ({p['start'] or '?'}-{p['end'] or 'present'})" for p in pos[:6]]
        out.append(_evidence("WIKIDATA_SPARQL_POSITIONS", r, matched, score, positions=True,
                             snippet=f"{r.get('label')}: " + "; ".join(bits),
                             extra={"positions": pos, "all_positions_ended": former}))
    return out


# --- LittleSis -------------------------------------------------------------

_POLITICAL = {"Political Candidate", "Elected Representative", "Government Official", "Public Official",
              "Political Party", "Government Body", "Lobbyist"}


def littlesis(subject: Subject, key: Optional[str]) -> List[Evidence]:
    url = "https://littlesis.org/api/entities/search?" + urlencode({"q": subject.name})
    out = []
    for r in parse_json(get_text(url, headers=_headers())).get("data", []):
        a = r.get("attributes") or {}
        matched, score = best_name(subject, [a.get("name", "")] + list(a.get("aliases") or []))
        if score < THRESHOLD:
            continue
        types = a.get("types") or []
        kind = "pep_info" if _POLITICAL.intersection(types) else "identity"
        link = (r.get("links") or {}).get("self") or f"https://littlesis.org/entities/{a.get('id')}"
        out.append(make("LITTLESIS_API", kind, f"LittleSis: {a.get('name')}", url=link,
                        snippet=a.get("blurb") or "", matched=matched, score=score, topics=False,
                        extra={"littlesis_id": a.get("id"), "types": types, "entity_type": a.get("primary_ext"),
                               "start_date": a.get("start_date"), "end_date": a.get("end_date"),
                               "wikidata_qid": a.get("qid")}))
    return cap(out, 10)


def _p(key: str, name: str, fn, url: str, notes: str, lic: str = "CC0 (data); Wikimedia API etiquette",
      interval: float = 1.0) -> LiveProvider:
    return LiveProvider(key=key, name=name, kind="pep_info", jurisdiction="INT", search=fn, url=url,
                        license=lic, groups=["identity", "free"], min_interval=interval, notes=notes)


PROVIDERS: List[LiveProvider] = [
    _p("WIKIDATA_WBSEARCHENTITIES", "Wikidata wbsearchentities", search_entities, API,
       "Sends the subject name only; returns label/description (step 1 of a PEP check)."),
    _p("WIKIDATA_ENTITY_SEARCH", "Wikidata entity search (aliases, Arabic script)", entity_search, API,
       "Sends the subject name and up to three aliases; overlaps WIKIDATA_WBSEARCHENTITIES (broader queries)."),
    _p("WIKIDATA_WBGETENTITIES", "Wikidata entity claims (DOB, citizenship, positions)", get_entities, API,
       "Sends the subject name only, then fetches the matched QIDs' claims."),
    _p("WIKIDATA_SPARQL_POSITIONS", "Wikidata positions held with term dates (SPARQL)", sparql_positions, WDQS,
       "Sends the subject name only (to resolve QIDs); the SPARQL query carries QIDs, not names.", interval=2.0),
    _p("LITTLESIS_API", "LittleSis power-relationship database", littlesis, "https://littlesis.org/api/entities/search",
       "Sends the subject name only.", lic="CC-BY-SA 4.0"),
]
