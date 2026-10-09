"""Court and judgment search (kind ``court``).

A case or judgment naming the subject is context, not a finding: the subject
may be a claimant, a victim, a witness or a namesake. Evidence therefore keeps
the case name, court and date so a reviewer can read the source.
"""

from __future__ import annotations

import os
import re
import xml.etree.ElementTree as ET
from typing import Dict, List, Optional
from urllib.parse import quote, urlencode

from venombot.evidence import Evidence
from venombot.fetch import get_text
from venombot.live import LiveProvider
from venombot.live._freecommon import (
    THRESHOLD, best_name, cap, clip, find_in_text, iso_date, make, parse_json, strip_tags,
)
from venombot.matching import compare_names
from venombot.screening import Subject

MAX = 15
CL = "https://www.courtlistener.com"


def _json(url: str, **kw):
    return parse_json(get_text(url, **kw))


def _cl_headers() -> Dict[str, str]:
    token = os.environ.get("COURTLISTENER_TOKEN")
    return {"Authorization": f"Token {token}"} if token else {}


def _parties(case_name: str) -> List[str]:
    """Split "A v. B & Anor" into party names so each can be matched alone."""
    parts = re.split(r"\s+(?:v\.?|vs\.?|and|&)\s+", case_name, flags=re.I)
    return [p.strip() for p in parts if p.strip()]


# --- CourtListener ---------------------------------------------------------

def courtlistener_opinions(subject: Subject, key: Optional[str]) -> List[Evidence]:
    """Free-text opinion search; the name must appear in the case name or snippet."""
    url = f"{CL}/api/rest/v4/search/?" + urlencode({"type": "o", "q": f'"{subject.name}"', "order_by": "dateFiled desc"})
    out = []
    for r in _json(url, headers=_cl_headers()).get("results", []):
        case = r.get("caseName") or ""
        snippet = " ".join((o.get("snippet") or "") for o in (r.get("opinions") or [])[:1])
        matched, score = best_name(subject, _parties(case))
        text = ""
        if score < THRESHOLD:
            matched, score, text = find_in_text(subject, snippet)
        if score < THRESHOLD:
            continue
        out.append(make("COURTLISTENER_SEARCH", "court", case or "(untitled opinion)",
                        url=CL + (r.get("absolute_url") or ""), snippet=text or f"{case} - {r.get('court')}",
                        published=iso_date(r.get("dateFiled")), matched=matched, score=score,
                        extra={"court": r.get("court"), "docket_number": r.get("docketNumber"),
                               "doc_type": "opinion", "country": "US"}))
    return cap(out, MAX)


def courtlistener_party_dockets(subject: Subject, key: Optional[str]) -> List[Evidence]:
    """RECAP dockets where the subject is a listed party (criminal "United States v. X")."""
    url = f"{CL}/api/rest/v4/search/?" + urlencode(
        {"type": "r", "q": f'party:("{subject.name}")', "order_by": "dateFiled desc"})
    out = []
    for r in _json(url, headers=_cl_headers()).get("results", []):
        parties = r.get("party") or []
        matched, score = best_name(subject, list(parties) + _parties(r.get("caseName") or ""))
        if score < THRESHOLD:
            continue
        case = r.get("caseName") or "(docket)"
        out.append(make("US_COURTLISTENER_SEARCH", "court", case,
                        url=CL + (r.get("docket_absolute_url") or ""),
                        snippet=f"{matched} is a party in {case} ({r.get('court')}); {r.get('cause') or ''} {r.get('suitNature') or ''}",
                        published=iso_date(r.get("dateFiled")), matched=matched, score=score,
                        extra={"court": r.get("court"), "docket_number": r.get("docketNumber"),
                               "cause": r.get("cause"), "nature_of_suit": r.get("suitNature"),
                               "terminated": r.get("dateTerminated"), "doc_type": "docket", "country": "US"}))
    return cap(out, MAX)


def courtlistener_opinion_feed(subject: Subject, key: Optional[str]) -> List[Evidence]:
    """Atom fallback for opinions, usable when the API is rate-limited."""
    url = f"{CL}/feed/search/?" + urlencode({"q": f'"{subject.name}"', "type": "o"})
    ns = {"a": "http://www.w3.org/2005/Atom"}
    root = ET.fromstring(get_text(url))
    out = []
    for e in root.findall("a:entry", ns):
        title = (e.findtext("a:title", "", ns) or "").strip()
        link = (e.find("a:link", ns).get("href") if e.find("a:link", ns) is not None else "")
        summary = strip_tags(e.findtext("a:summary", "", ns) or "")
        matched, score = best_name(subject, _parties(title))
        text = ""
        if score < THRESHOLD:
            matched, score, text = find_in_text(subject, summary)
        if score < THRESHOLD:
            continue
        out.append(make("US_COURTLISTENER_OPINION_FEED", "court", title, url=link, snippet=text or summary,
                        published=iso_date(e.findtext("a:published", "", ns)), matched=matched, score=score,
                        extra={"court": e.findtext("a:author/a:name", "", ns), "doc_type": "opinion", "country": "US"}))
    return cap(out, MAX)


def _party_match(subject: Subject, parties: List[str]):
    """Full-name match, else a surname-only match capped at 0.8 and flagged.

    Judgments often name a party by surname only ("JSC BTA Bank v Ablyazov").
    Such a hit is weaker evidence, so it never reaches the 0.85 full-match
    level and says so in ``match_level``.

    @return (matched, score, level) with score 0 when nothing qualifies
    """
    matched, score = best_name(subject, parties)
    if score >= THRESHOLD:
        return matched, score, "full name"
    words = subject.name.split()
    if subject.kind == "org" or len(words) < 2:
        return "", 0.0, ""
    matched, score = best_name(Subject(name=words[-1], kind=subject.kind), parties)
    return (matched, min(score, 0.8), "surname only") if score >= THRESHOLD else ("", 0.0, "")


# --- UK Find Case Law ------------------------------------------------------

def uk_find_case_law(subject: Subject, key: Optional[str]) -> List[Evidence]:
    # ?party= restricts to cases where the name is a party, so the title carries it.
    url = "https://caselaw.nationalarchives.gov.uk/atom.xml?" + urlencode({"party": subject.name})
    ns = {"a": "http://www.w3.org/2005/Atom", "tna": "https://caselaw.nationalarchives.gov.uk"}
    root = ET.fromstring(get_text(url))
    out = []
    for e in root.findall("a:entry", ns):
        title = (e.findtext("a:title", "", ns) or "").strip()
        matched, score, level = _party_match(subject, _parties(title))
        if not score:
            continue
        link = ""
        for l in e.findall("a:link", ns):
            if l.get("rel") == "alternate" and not l.get("type"):
                link = l.get("href", "")
        cite = next((i.text for i in e.findall("tna:identifier", ns) if i.get("type") == "ukncn"), "")
        out.append(make("UK_FIND_CASE_LAW", "court", title, url=link, snippet=f"{title} {cite}",
                        published=iso_date(e.findtext("a:published", "", ns)), language="en",
                        matched=matched, score=score,
                        extra={"court": e.findtext("a:author/a:name", "", ns), "citation": cite, "country": "GB",
                               "match_level": level}))
    return cap(out, MAX)


# --- ECHR HUDOC ------------------------------------------------------------

def _echr_applicants(docname: str) -> List[str]:
    """"CASE OF A AND B v. RUSSIA (No. 2)" -> ["A", "B"]."""
    m = re.match(r"(?:CASE OF |AFFAIRE |)(.+?)\s+v\.?\s+", docname, flags=re.I)
    core = m.group(1) if m else docname
    return [p.strip() for p in re.split(r"\s+(?:AND|ET)\s+", core, flags=re.I) if p.strip()]


def echr_hudoc(subject: Subject, key: Optional[str]) -> List[Evidence]:
    # docname holds the applicant; free text matches every document that merely
    # cites the name. HUDOC often records applicants by surname only, so a
    # surname-only match is accepted but capped and flagged for the reviewer.
    surname = subject.name.split()[-1] if subject.kind != "org" else subject.name
    q = f'contentsitename:ECHR AND docname:"{surname}"'
    url = "https://hudoc.echr.coe.int/app/query/results?" + urlencode(
        {"query": q, "select": "itemid,docname,doctype,kpdate,respondent,conclusion,appno,languageisocode",
         "sort": "kpdate Descending", "start": 0, "length": 50}, quote_via=quote)
    out, seen = [], set()
    try:
        rows = _json(url).get("results", [])
    except RuntimeError as exc:
        # HUDOC answers 403/404 (not an empty list) when nothing matches the docname.
        if "HTTP 403" in str(exc) or "HTTP 404" in str(exc):
            return []
        raise
    for r in rows:
        c = r.get("columns") or {}
        appno = c.get("appno") or c.get("itemid")
        if appno in seen:  # ENG and FRE versions of the same case
            continue
        names = _echr_applicants(c.get("docname", ""))
        matched, score, level = _party_match(subject, names)
        if not score:
            continue
        seen.add(appno)
        out.append(make("COE_ECHR_HUDOC", "court", c.get("docname", ""),
                        url=f"https://hudoc.echr.coe.int/eng?i={c.get('itemid')}",
                        snippet=f"{c.get('docname')} ({c.get('doctype')}, respondent {c.get('respondent')}): {clip(c.get('conclusion') or '', 160)}",
                        published=iso_date(c.get("kpdate")), matched=matched, score=score,
                        extra={"application_no": appno, "respondent": c.get("respondent"),
                               "doc_type": c.get("doctype"), "match_level": level}))
    return cap(out, MAX)


# --- CJEU via the EU Publications Office CELLAR ------------------------------

def _sparql_str(text: str) -> str:
    return re.sub(r"[^\w\s\-']", " ", text).strip().lower()


def cjeu_cellar(subject: Subject, key: Optional[str]) -> List[Evidence]:
    word = _sparql_str(subject.name.split()[-1]) if subject.kind != "org" else _sparql_str(subject.name)
    if len(word) < 3:
        return []
    query = (
        "PREFIX cdm: <http://publications.europa.eu/ontology/cdm#> "
        "SELECT DISTINCT ?work ?title ?date WHERE { "
        "?work cdm:work_has_resource-type <http://publications.europa.eu/resource/authority/resource-type/JUDG> . "
        "?work cdm:work_date_document ?date . "
        "?exp cdm:expression_belongs_to_work ?work ; "
        "cdm:expression_uses_language <http://publications.europa.eu/resource/authority/language/ENG> ; "
        "cdm:expression_title ?title . "
        f'FILTER(CONTAINS(LCASE(STR(?title)), "{word}")) }} ORDER BY DESC(?date) LIMIT 40'
    )
    url = "https://publications.europa.eu/webapi/rdf/sparql?" + urlencode(
        {"query": query, "format": "application/sparql-results+json"})
    out = []
    for b in (_json(url).get("results") or {}).get("bindings", []):
        title = (b.get("title") or {}).get("value", "")
        segs = title.split("#")
        # Title layout: "Judgment of ... .#<Parties>.#<Subject matter>.#Case T-x/y."
        parties = _parties(segs[1]) if len(segs) > 1 else _parties(title)
        matched, score = best_name(subject, parties)
        if score < THRESHOLD:
            continue
        work = (b.get("work") or {}).get("value", "")
        out.append(make("EU_CJEU_CELLAR_SPARQL", "court", " - ".join(s.strip(" .") for s in segs[:2]),
                        url=work, snippet=clip(" ".join(segs[1:])), published=iso_date((b.get("date") or {}).get("value")),
                        language="en", matched=matched, score=score,
                        extra={"case": segs[-1].strip(" .") if len(segs) > 2 else "", "court": "CJEU", "country": "EU"}))
    return cap(out, MAX)


def _p(key: str, name: str, jur: str, fn, url: str, lic: str, notes: str,
       interval: float = 1.5, groups: Optional[List[str]] = None) -> LiveProvider:
    return LiveProvider(key=key, name=name, kind="court", jurisdiction=jur, search=fn, url=url, license=lic,
                        groups=groups or ["court", "free"], min_interval=interval, notes=notes)


PROVIDERS: List[LiveProvider] = [
    _p("COURTLISTENER_SEARCH", "CourtListener opinions (API)", "US", courtlistener_opinions,
       f"{CL}/api/rest/v4/search/", "public (Free Law Project)",
       "Sends the subject name only, quoted; anonymous use is rate-limited, set COURTLISTENER_TOKEN for more."),
    _p("US_COURTLISTENER_SEARCH", "CourtListener RECAP dockets by party", "US", courtlistener_party_dockets,
       f"{CL}/api/rest/v4/search/", "public (Free Law Project)",
       "Sends the subject name only, as a party filter; optional COURTLISTENER_TOKEN."),
    _p("US_COURTLISTENER_OPINION_FEED", "CourtListener opinion Atom feed", "US", courtlistener_opinion_feed,
       f"{CL}/feed/search/", "public (Free Law Project)",
       "Sends the subject name only; lightweight fallback overlapping COURTLISTENER_SEARCH."),
    _p("UK_FIND_CASE_LAW", "UK Find Case Law (National Archives)", "GB", uk_find_case_law,
       "https://caselaw.nationalarchives.gov.uk/atom.xml", "Open Justice Licence v2.0",
       "Sends the subject name only, as a party search; bulk/computational analysis needs TNA's separate licence."),
    _p("COE_ECHR_HUDOC", "ECHR HUDOC judgments", "INT", echr_hudoc,
       "https://hudoc.echr.coe.int/app/query/results", "public (source attribution)",
       "Sends the subject's surname only (applicant docname search); undocumented endpoint, surname-only matches are flagged; zero hits come back as HTTP 403/404 and are treated as empty."),
    _p("EU_CJEU_CELLAR_SPARQL", "CJEU judgments (CELLAR SPARQL)", "EU", cjeu_cellar,
       "https://publications.europa.eu/webapi/rdf/sparql", "EU reuse policy",
       "Sends the subject's surname only (inside a SPARQL filter), then matches parties locally.", interval=2.0),
]
