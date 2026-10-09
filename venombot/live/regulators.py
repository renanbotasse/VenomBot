"""Government and regulator search (kind ``enforcement`` unless noted).

A regulator page or press release naming the subject is context: it may be a
prosecution, a victim, or a mere mention. Evidence keeps the title and source
so the reviewer decides. Adverse topics are tagged from the title/snippet only.
"""

from __future__ import annotations

import datetime as _dt
import json
from typing import Dict, List, Optional
from urllib.parse import urlencode

from venombot.evidence import Evidence
from venombot.fetch import get_text
from venombot.live import LiveProvider
from venombot.live._freecommon import (
    THRESHOLD, best_name, cap, clip, find_in_text, iso_date, make, parse_json, is_org,
)
from venombot.screening import Subject

MAX = 15
GOVUK = "https://www.gov.uk"
GOVUK_ORGS = ("serious-fraud-office", "office-of-financial-sanctions-implementation", "insolvency-service",
              "hm-revenue-customs", "crown-prosecution-service", "companies-house")


def _json(url: str, **kw):
    return parse_json(get_text(url, **kw))


def _in_fields(subject: Subject, *fields: str):
    """Best name match over several title/description fields, with its sentence."""
    top = ("", 0.0, "")
    for f in fields:
        m = find_in_text(subject, f or "")
        if m[1] > top[1]:
            top = m
    return top


def uk_govuk_search(subject: Subject, key: Optional[str]) -> List[Evidence]:
    params = [("q", f'"{subject.name}"'), ("count", 30),
              ("fields", "title"), ("fields", "link"), ("fields", "public_timestamp"),
              ("fields", "description"), ("fields", "format")]
    params += [("filter_organisations", o) for o in GOVUK_ORGS]
    url = f"{GOVUK}/api/search.json?" + urlencode(params)
    out = []
    for r in _json(url).get("results", []):
        title, desc = r.get("title") or "", r.get("description") or ""
        matched, score, sent = _in_fields(subject, title, desc)
        if score < THRESHOLD:
            continue
        out.append(make("UK_GOVUK_SEARCH_API", "enforcement", title, url=GOVUK + (r.get("link") or ""),
                        snippet=sent or desc, published=iso_date(r.get("public_timestamp")), language="en",
                        matched=matched, score=score,
                        extra={"format": r.get("format") or r.get("document_type"), "country": "GB"}))
    return cap(out, MAX)


# Notice codes seen in the discovery sample; unknown codes are passed through raw.
GAZETTE_CODES = {"2441": "winding-up petition", "2510": "bankruptcy order"}


def uk_gazette(subject: Subject, key: Optional[str]) -> List[Evidence]:
    url = "https://www.thegazette.co.uk/all-notices/notice/data.json?" + urlencode(
        {"text": f'"{subject.name}"', "results-page-size": 25})
    out = []
    data = _json(url)
    entries = data.get("entry") or []
    if isinstance(entries, dict):
        entries = [entries]
    for e in entries:
        title = e.get("title") or ""
        matched, score = best_name(subject, [title])
        if score < THRESHOLD:
            continue
        code = str(e.get("f:notice-code") or "")
        nid = str(e.get("id") or "").rsplit("/", 1)[-1]
        out.append(make("UK_GAZETTE_NOTICES", "corporate", f"Gazette notice: {title}",
                        url=f"https://www.thegazette.co.uk/notice/{nid}",
                        snippet=f"The Gazette notice {nid} (code {code}{', ' + GAZETTE_CODES[code] if code in GAZETTE_CODES else ''}) concerns {title}",
                        published=iso_date(e.get("published") or e.get("updated")), language="en",
                        matched=matched, score=score,
                        extra={"notice_code": code, "notice_type": GAZETTE_CODES.get(code), "country": "GB"}, topics=False))
    return cap(out, MAX)


def doj_press(subject: Subject, key: Optional[str]) -> List[Evidence]:
    # parameters[title] is a substring match on the title only (keyword= is ignored).
    url = "https://www.justice.gov/api/v1/press_releases.json?" + urlencode(
        {"parameters[title]": subject.name, "pagesize": 25, "sort": "created", "direction": "DESC",
         "fields": "title,date,url,topic"})
    out = []
    for r in _json(url).get("results", []):
        title = r.get("title") or ""
        matched, score, sent = find_in_text(subject, title)
        if score < THRESHOLD:
            continue
        try:
            published = _dt.datetime.fromtimestamp(int(r.get("date")), _dt.timezone.utc).date().isoformat()
        except (TypeError, ValueError):
            published = ""
        out.append(make("US_DOJ_PRESS_RELEASES_API", "enforcement", title, url=r.get("url") or "",
                        snippet=title, published=published, language="en", matched=matched, score=score,
                        extra={"topics_doj": [t.get("name") for t in r.get("topic") or []], "country": "US"}))
    return cap(out, MAX)


def federal_register(subject: Subject, key: Optional[str]) -> List[Evidence]:
    params = [("conditions[term]", f'"{subject.name}"'), ("per_page", 25), ("order", "newest")]
    params += [("fields[]", f) for f in ("title", "publication_date", "html_url", "agencies", "type", "abstract")]
    url = "https://www.federalregister.gov/api/v1/documents.json?" + urlencode(params)
    out = []
    for r in _json(url).get("results", []):
        title, abstract = r.get("title") or "", r.get("abstract") or ""
        matched, score, sent = _in_fields(subject, title, abstract)
        basis = "name in title/abstract"
        if score < THRESHOLD:
            # Full-text phrase hit: the name is in the document body (typically an
            # annex list), which the API does not return. Keep, but say so.
            matched, score, sent, basis = subject.name, THRESHOLD, abstract or title, "quoted-phrase match in document text (server side)"
        agencies = [a.get("name") or a.get("raw_name") for a in r.get("agencies") or []]
        out.append(make("US_FEDERAL_REGISTER_API", "enforcement", title, url=r.get("html_url") or "",
                        snippet=sent, published=iso_date(r.get("publication_date")), language="en",
                        matched=matched, score=score,
                        extra={"doc_type": r.get("type"), "agencies": agencies, "match_basis": basis, "country": "US"}))
    return out[:10]


def finra_brokercheck(subject: Subject, key: Optional[str]) -> List[Evidence]:
    out = []
    kinds = ["firm"] if is_org(subject) else ["individual"]
    for scope in kinds:
        url = f"https://api.brokercheck.finra.org/search/{scope}?" + urlencode(
            {"query": subject.name, "hl": "true", "nrows": 12, "start": 0, "r": 25, "wt": "json"})
        for h in ((_json(url).get("hits") or {}).get("hits") or []):
            s = h.get("_source") or {}
            if scope == "individual":
                full = " ".join(p for p in (s.get("ind_firstname"), s.get("ind_middlename"), s.get("ind_lastname")) if p)
                names = [full, f"{s.get('ind_firstname', '')} {s.get('ind_lastname', '')}"] + list(s.get("ind_other_names") or [])
                crd, disclosed = s.get("ind_source_id"), s.get("ind_bc_disclosure_fl") == "Y"
                barred = s.get("ind_permanent_bar") == "Y" or s.get("ind_bc_scope") == "Barred"
                label = full
                emp = [e.get("firm_name") for e in s.get("ind_current_employments") or []]
                extra = {"crd": crd, "scope": s.get("ind_bc_scope"), "permanent_bar": s.get("ind_permanent_bar"),
                         "disclosures": disclosed, "current_firms": emp, "country": "US"}
                link = f"https://brokercheck.finra.org/individual/summary/{crd}"
            else:
                label = s.get("firm_name") or ""
                names = [label] + list(s.get("firm_other_names") or [])
                crd, disclosed, barred = s.get("firm_source_id"), s.get("firm_disclosure_fl") == "Y", False
                extra = {"crd": crd, "scope": s.get("firm_scope"), "disclosures": disclosed,
                         "sec_number": s.get("firm_bd_full_sec_number"), "country": "US"}
                link = f"https://brokercheck.finra.org/firm/summary/{crd}"
            matched, score = best_name(subject, names)
            if score < THRESHOLD:
                continue
            flags = []
            if barred:
                flags.append("barred from the securities industry")
            if disclosed:
                flags.append("has regulatory/criminal/customer disclosures")
            snippet = f"{label} (CRD {crd}) " + ("; ".join(flags) if flags else "registered, no disclosures flagged")
            # A clean registration is identity context, not enforcement.
            out.append(make("US_FINRA_BROKERCHECK", "enforcement" if flags else "identity",
                            f"BrokerCheck: {label}", url=link, snippet=snippet, language="en",
                            matched=matched, score=score, extra=extra, topics=False))
    return cap(out, MAX)


def _demojibake(text: str) -> str:
    """SECO names in sanctions.network are double-encoded UTF-8; undo it when possible."""
    try:
        return text.encode("cp1252").decode("utf-8")
    except (UnicodeEncodeError, UnicodeDecodeError):
        return text


def sanctions_network(subject: Subject, key: Optional[str]) -> List[Evidence]:
    """Trigram search over OFAC/UN/EU/SECO designations (public-domain source lists)."""
    url = "https://api.sanctions.network/rpc/search_sanctions?" + urlencode({"name": subject.name})
    out = []
    rows = _json(url)
    for r in rows if isinstance(rows, list) else []:
        names = [_demojibake(n) for n in r.get("names") or []]
        matched, score = best_name(subject, names)
        if score < THRESHOLD:
            continue
        src = str(r.get("source") or "")
        out.append(make("SANCTIONS_NETWORK_API", "enforcement", f"{matched} ({src.upper()} designation)",
                        url=url, snippet=f"{matched} appears on the {src.upper()} sanctions list (id {r.get('source_id')})"
                        + (f"; {', '.join(r.get('positions') or [])}" if r.get("positions") else ""),
                        published=iso_date(r.get("listed_on")), matched=matched, score=score,
                        extra={"list": src, "source_id": r.get("source_id"), "target_type": r.get("target_type"),
                               "positions": r.get("positions"), "aliases": names[:6]},
                        topics=False))
    return cap(out, MAX)


def _p(key: str, name: str, jur: str, fn, url: str, lic: str, notes: str,
       kind: str = "enforcement", interval: float = 1.0, groups: Optional[List[str]] = None) -> LiveProvider:
    return LiveProvider(key=key, name=name, kind=kind, jurisdiction=jur, search=fn, url=url, license=lic,
                        groups=groups or [kind, "free"], min_interval=interval, notes=notes)


PROVIDERS: List[LiveProvider] = [
    _p("SANCTIONS_NETWORK_API", "sanctions.network (OFAC/UN/EU/SECO)", "INT", sanctions_network,
       "https://api.sanctions.network/rpc/search_sanctions", "public (free to use, AS IS)",
       "Sends the subject name only; a fuzzy cross-check, the downloaded official lists remain authoritative."),
    _p("UK_GOVUK_SEARCH_API", "GOV.UK search (SFO, OFSI, Insolvency Service, HMRC, CPS, Companies House)", "GB",
       uk_govuk_search, f"{GOVUK}/api/search.json", "OGL",
       "Sends the subject name only, quoted, filtered to six enforcement-related organisations."),
    _p("UK_GAZETTE_NOTICES", "The Gazette notices (insolvency, corporate)", "GB", uk_gazette,
       "https://www.thegazette.co.uk/all-notices/notice/data.json", "OGL (personal insolvency data excluded)",
       "Sends the subject name only, quoted; personal-insolvency data is not covered by OGL.",
       kind="corporate"),
    _p("US_DOJ_PRESS_RELEASES_API", "US DOJ press releases", "US", doj_press,
       "https://www.justice.gov/api/v1/press_releases.json", "public",
       "Sends the subject name only (title substring match); limit 4 requests/s.", interval=0.5),
    _p("US_FEDERAL_REGISTER_API", "US Federal Register full text", "US", federal_register,
       "https://www.federalregister.gov/api/v1/documents.json", "public",
       "Sends the subject name only, quoted; body matches are signals, not determinations."),
    _p("US_FINRA_BROKERCHECK", "FINRA BrokerCheck", "US", finra_brokercheck,
       "https://api.brokercheck.finra.org/search/individual", "terms (compliance use only, no bulk)",
       "Sends the subject name only; undocumented backend, one request per subject, no crawling.", interval=2.0),
]
