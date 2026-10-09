"""Adverse-media search: GDELT DOC 2.0 (keyless) and key-gated news APIs.

Why one module: every news API has a different request/response shape but the
same job — find articles that mention the subject, drop the ones where the
name is only a fuzzy accident, and attach adverse topics. The shared helpers
here (``name_hit``, ``make_evidence``) are also reused by the registry and
Interpol modules so the noise filter is identical everywhere.

An article is context, never a finding. Keys come only from the environment
variable named in each provider's ``api_key_env``.

Response shapes for the key-gated APIs are taken from their documentation
(recorded in the discovery checkpoint); they could not be run live without
keys, so unit tests with recorded-shape fixtures are the verification.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Tuple
from urllib.parse import quote, urlencode

from venombot.evidence import Evidence, topics_in
from venombot.fetch import get_text
from venombot.live import LiveProvider
from venombot.matching import compare_names
from venombot.normalize import tokens
from venombot.screening import Subject

NAME_THRESHOLD = 0.85
MAX_RESULTS = 25

# Adverse keywords sent to GDELT (English: it matches on machine translation,
# so Arabic keywords return nothing).
GDELT_KEYWORDS = ("laundering OR fraud OR bribery OR corruption OR sanctions OR sanctioned OR "
                  "arrested OR indicted OR convicted OR terrorism OR trafficking OR embezzlement")


def _sleep(seconds: float) -> None:
    """Indirection so tests can skip the politeness delay."""
    time.sleep(seconds)


def name_hit(subject: Subject, text: str) -> Tuple[float, str]:
    """Best fuzzy match of the subject name (or an alias) inside free text.

    Slides a token window the size of the name over ``text`` because
    comparing the name with a whole sentence would be punished for every
    extra word.

    @param subject the person/organisation being screened
    @param text title / description / snippet to look in
    @return (best score 0..1, the words that matched)
    """
    is_org = subject.kind == "org"
    words = text.split()
    best: Tuple[float, str] = (0.0, "")
    for name in [subject.name] + list(subject.aliases):
        n = max(1, len(name.split()))
        if not words:
            break
        for size in {max(1, n - 1), n, n + 1}:
            for i in range(0, max(1, len(words) - size + 1)):
                chunk = " ".join(words[i:i + size]).strip(".,;:!?\"'()[]")
                if not tokens(chunk, is_org=is_org):
                    continue
                score = compare_names(name, chunk, is_org=is_org).score
                if score > best[0]:
                    best = (score, chunk)
    return best


def make_evidence(subject: Subject, provider: str, kind: str, title: str, *, text: str = "",
                  url: str = "", published: str = "", language: str = "",
                  extra: Optional[Dict[str, Any]] = None, require_name: bool = True,
                  field_texts: Optional[List[str]] = None) -> Optional[Evidence]:
    """Build Evidence if the name really appears in title/text, else None.

    @param field_texts additional fields (e.g. a result's name field) to look in
    @param require_name False when the API already did exact-phrase matching on
           text we do not receive (GDELT full-text search)
    """
    best_score, best_chunk, snippet = 0.0, "", ""
    for part in [title, text] + list(field_texts or []):
        if not part:
            continue
        score, chunk = name_hit(subject, part)
        if score > best_score:
            best_score, best_chunk, snippet = score, chunk, part
    if require_name and best_score < NAME_THRESHOLD:
        return None
    if not require_name and best_score < NAME_THRESHOLD:
        best_chunk, snippet = subject.name, title
        best_score = NAME_THRESHOLD  # phrase match upstream, body not returned
    return Evidence(
        provider=provider, kind=kind, title=title.strip(), url=url, snippet=snippet.strip()[:400],
        published=published, language=language, matched_name=best_chunk,
        name_score=round(best_score, 3), topics=topics_in(f"{title} {text}"), extra=extra or {},
    )


def _dedupe_cap(items: List[Evidence]) -> List[Evidence]:
    seen, out = set(), []
    for ev in items:
        k = ev.url or ev.title
        if k in seen:
            continue
        seen.add(k)
        out.append(ev)
    return out[:MAX_RESULTS]


def _iso(date: str) -> str:
    """Normalise 20261001T160000Z / 2026-10-01T... to YYYY-MM-DD."""
    d = (date or "").strip()
    if len(d) >= 8 and d[:8].isdigit():
        return f"{d[:4]}-{d[4:6]}-{d[6:8]}"
    return d[:10]


# ---------------------------------------------------------------- GDELT

def _gdelt_json(url: str) -> Dict[str, Any]:
    # retries=0: GDELT's limit is about one request per 5 s per IP, and the generic
    # retry/backoff would count against that same quota and make a 429 last longer.
    body = get_text(url, timeout=45, retries=0).strip()
    if not body:
        return {}
    try:
        return json.loads(body)
    except ValueError as exc:
        # GDELT answers plain-text error messages (bad query, rate limit).
        raise RuntimeError(f"GDELT non-JSON response: {body[:120]}") from exc


def search_gdelt(subject: Subject, key: Optional[str] = None) -> List[Evidence]:
    """Phrase search + adverse keywords, English and Arabic source languages.

    GDELT searches article bodies but returns only titles, so the name often
    is not in the title; every hit still contains the exact phrase together
    with an adverse keyword, which is why the title filter is not applied.
    """
    out: List[Evidence] = []
    for i, lang in enumerate(("english", "arabic")):
        if i:
            _sleep(5.0)  # GDELT asks for about one request per 5 seconds
        q = f'"{subject.name}" ({GDELT_KEYWORDS}) sourcelang:{lang}'
        params = urlencode({"query": q, "mode": "ArtList", "format": "json", "maxrecords": 75,
                            "timespan": "1y", "sort": "DateDesc"}, quote_via=quote)
        url = "https://api.gdeltproject.org/api/v2/doc/doc?" + params
        try:
            data = _gdelt_json(url)
        except RuntimeError as exc:
            if "429" not in str(exc):
                raise
            _sleep(12.0)  # one patient retry: the limit window is short
            try:
                data = _gdelt_json(url)
            except RuntimeError as exc2:
                raise RuntimeError(
                    "GDELT_DOC_API: rate limited (HTTP 429; GDELT allows about one request per 5 s per IP and "
                    "is stricter when shared). Run again in a few minutes, or use a news API key "
                    "(NEWSAPI_KEY, GUARDIAN_API_KEY, ...)") from exc2
        for art in data.get("articles", []) or []:
            title = art.get("title") or ""
            ev = make_evidence(
                subject, "GDELT_DOC_API", "adverse_media", title, url=art.get("url", ""),
                published=_iso(art.get("seendate", "")), language=art.get("language", ""),
                require_name=False,
                extra={"domain": art.get("domain", ""), "source_country": art.get("sourcecountry", ""),
                       "name_in_title": name_hit(subject, title)[0] >= NAME_THRESHOLD},
            )
            if ev:
                out.append(ev)
    return _dedupe_cap(out)


# ----------------------------------------------------------- news APIs

@dataclass
class _NewsSpec:
    key: str
    name: str
    env: str
    jurisdiction: str
    doc_url: str
    # (subject, api key, lang) -> (url, headers, post_body)
    request: Callable[[Subject, str, str], Tuple[str, Dict[str, str], Optional[bytes]]]
    # decoded JSON -> list of dicts: title, text, url, published, language, source
    parse: Callable[[Dict[str, Any]], List[Dict[str, str]]]
    langs: Tuple[str, ...] = ("en", "ar")
    notes: str = ""
    license: str = "terms"


def _q(subject: Subject) -> str:
    return '"' + subject.name.replace('"', "") + '"'


def _req_newsapi(s: Subject, k: str, lang: str):
    p = urlencode({"q": _q(s), "language": lang, "sortBy": "publishedAt", "pageSize": 50}, quote_via=quote)
    return "https://newsapi.org/v2/everything?" + p, {"X-Api-Key": k}, None


def _parse_newsapi(d):
    if d.get("status") not in (None, "ok"):
        raise RuntimeError(f"NewsAPI error {d.get('code')}: {d.get('message')}")
    return [{"title": a.get("title") or "", "text": a.get("description") or "", "url": a.get("url") or "",
             "published": a.get("publishedAt") or "", "source": (a.get("source") or {}).get("name", "")}
            for a in d.get("articles", [])]


def _req_gnews(s, k, lang):
    p = urlencode({"q": _q(s), "lang": lang, "max": 10, "sortby": "publishedAt", "apikey": k}, quote_via=quote)
    return "https://gnews.io/api/v4/search?" + p, {}, None


def _parse_gnews(d):
    if d.get("errors"):
        raise RuntimeError(f"GNews error: {d['errors']}")
    return [{"title": a.get("title") or "", "text": a.get("description") or "", "url": a.get("url") or "",
             "published": a.get("publishedAt") or "", "source": (a.get("source") or {}).get("name", "")}
            for a in d.get("articles", [])]


def _req_newsdata(s, k, lang):
    p = urlencode({"apikey": k, "qInTitle": _q(s), "language": lang}, quote_via=quote)
    return "https://newsdata.io/api/1/latest?" + p, {}, None


def _parse_newsdata(d):
    if d.get("status") == "error":
        raise RuntimeError(f"NewsData error: {(d.get('results') or {}).get('message', d)}")
    return [{"title": a.get("title") or "", "text": a.get("description") or "", "url": a.get("link") or "",
             "published": a.get("pubDate") or "", "source": a.get("source_id") or "",
             "language": a.get("language") or ""} for a in d.get("results", []) or []]


def _req_mediastack(s, k, lang):
    # The free tier only serves plain http; the key is a query parameter by design.
    p = urlencode({"access_key": k, "keywords": s.name, "languages": lang, "sort": "published_desc",
                   "limit": 100}, quote_via=quote)
    return "https://api.mediastack.com/v1/news?" + p, {}, None


def _parse_mediastack(d):
    if d.get("error"):
        raise RuntimeError(f"mediastack error: {d['error'].get('message', d['error'])}")
    return [{"title": a.get("title") or "", "text": a.get("description") or "", "url": a.get("url") or "",
             "published": a.get("published_at") or "", "source": a.get("source") or "",
             "language": a.get("language") or ""} for a in d.get("data", []) or []]


def _req_currents(s, k, lang):
    p = urlencode({"keywords": _q(s), "language": lang, "apiKey": k}, quote_via=quote)
    return "https://api.currentsapi.services/v1/search?" + p, {}, None


def _parse_currents(d):
    if str(d.get("status", "ok")).lower() not in ("ok", "200"):
        raise RuntimeError(f"Currents error: {d.get('msg', d)}")
    return [{"title": a.get("title") or "", "text": a.get("description") or "", "url": a.get("url") or "",
             "published": a.get("published") or "", "language": a.get("language") or ""}
            for a in d.get("news", []) or []]


def _req_thenewsapi(s, k, lang):
    p = urlencode({"api_token": k, "search": _q(s), "language": lang, "sort": "published_at"}, quote_via=quote)
    return "https://api.thenewsapi.com/v1/news/all?" + p, {}, None


def _parse_thenewsapi(d):
    if d.get("error"):
        raise RuntimeError(f"TheNewsAPI error: {d['error'].get('message', d['error'])}")
    return [{"title": a.get("title") or "", "text": a.get("description") or a.get("snippet") or "",
             "url": a.get("url") or "", "published": a.get("published_at") or "",
             "source": a.get("source") or "", "language": a.get("language") or ""}
            for a in d.get("data", []) or []]


def _req_worldnews(s, k, lang):
    p = urlencode({"text": _q(s), "language": lang, "number": 50}, quote_via=quote)
    return "https://api.worldnewsapi.com/search-news?" + p, {"x-api-key": k}, None


def _parse_worldnews(d):
    if d.get("status") == "failure":
        raise RuntimeError(f"World News API error {d.get('code')}: {d.get('message')}")
    return [{"title": a.get("title") or "", "text": (a.get("text") or "")[:1500], "url": a.get("url") or "",
             "published": a.get("publish_date") or "", "language": a.get("language") or ""}
            for a in d.get("news", []) or []]


def _req_guardian(s, k, lang):
    # Guardian content is English; one call only (langs=("en",)).
    p = urlencode({"q": _q(s), "order-by": "newest", "page-size": 50, "show-fields": "trailText",
                   "api-key": k}, quote_via=quote)
    return "https://content.guardianapis.com/search?" + p, {}, None


def _parse_guardian(d):
    resp = d.get("response") or {}
    if resp.get("status") not in (None, "ok"):
        raise RuntimeError(f"Guardian error: {resp.get('message', resp)}")
    return [{"title": a.get("webTitle") or "", "text": (a.get("fields") or {}).get("trailText") or "",
             "url": a.get("webUrl") or "", "published": a.get("webPublicationDate") or "",
             "source": a.get("sectionName") or "", "language": "en"} for a in resp.get("results", [])]


def _req_nyt(s, k, lang):
    p = urlencode({"q": _q(s), "sort": "newest", "api-key": k}, quote_via=quote)
    return "https://api.nytimes.com/svc/search/v2/articlesearch.json?" + p, {}, None


def _parse_nyt(d):
    if d.get("fault"):
        raise RuntimeError(f"NYT error: {d['fault'].get('faultstring', d['fault'])}")
    docs = (d.get("response") or {}).get("docs", [])
    return [{"title": (a.get("headline") or {}).get("main") or "", "text": a.get("abstract") or "",
             "url": a.get("web_url") or "", "published": a.get("pub_date") or "", "language": "en"}
            for a in docs]


def _req_eventregistry(s, k, lang):
    body = json.dumps({"keyword": _q(s), "keywordLoc": "body,title", "lang": "ara" if lang == "ar" else "eng",
                       "articlesSortBy": "date", "articlesCount": 50, "resultType": "articles",
                       "apiKey": k}).encode()
    return "https://eventregistry.org/api/v1/article/getArticles", {"Content-Type": "application/json"}, body


def _parse_eventregistry(d):
    if d.get("error"):
        raise RuntimeError(f"Event Registry error: {d['error']}")
    return [{"title": a.get("title") or "", "text": (a.get("body") or "")[:1500], "url": a.get("url") or "",
             "published": a.get("dateTime") or a.get("date") or "", "language": a.get("lang") or "",
             "source": (a.get("source") or {}).get("title", "")}
            for a in (d.get("articles") or {}).get("results", [])]


def _req_mediacloud(s, k, lang):
    p = urlencode({"q": f'{_q(s)} AND language:{lang}', "start": time.strftime("%Y-%m-%d", time.gmtime(time.time() - 365 * 86400)),
                   "end": time.strftime("%Y-%m-%d", time.gmtime()), "platform": "onlinenews-mediacloud"},
                  quote_via=quote)
    return "https://search.mediacloud.org/api/search/story-list?" + p, {"Authorization": f"Token {k}"}, None


def _parse_mediacloud(d):
    if d.get("detail"):
        raise RuntimeError(f"Media Cloud error: {d['detail']}")
    stories = d.get("stories") if isinstance(d.get("stories"), list) else d.get("results", [])
    return [{"title": a.get("title") or "", "text": "", "url": a.get("url") or "",
             "published": a.get("publish_date") or "", "language": a.get("language") or "",
             "source": a.get("media_name") or ""} for a in stories or []]


_SPECS: List[_NewsSpec] = [
    _NewsSpec("EVENT_REGISTRY_API", "Event Registry article search", "EVENT_REGISTRY_API_KEY", "GLOBAL",
              "https://eventregistry.org/api/v1/article/getArticles", _req_eventregistry, _parse_eventregistry,
              notes="Env EVENT_REGISTRY_API_KEY. POST JSON, lang eng and ara as two calls. Strong Arabic coverage."),
    _NewsSpec("NEWSAPI_ORG", "NewsAPI.org /v2/everything", "NEWSAPI_KEY", "GLOBAL",
              "https://newsapi.org/v2/everything", _req_newsapi, _parse_newsapi,
              notes="Env NEWSAPI_KEY (header X-Api-Key). Free plan is development-only and delayed."),
    _NewsSpec("NEWSDATA_IO", "NewsData.io latest news", "NEWSDATA_API_KEY", "GLOBAL",
              "https://newsdata.io/api/1/latest", _req_newsdata, _parse_newsdata,
              notes="Env NEWSDATA_API_KEY. qInTitle search (free plan has no full text query)."),
    _NewsSpec("GNEWS_IO", "GNews API v4 search", "GNEWS_API_KEY", "GLOBAL",
              "https://gnews.io/api/v4/search", _req_gnews, _parse_gnews,
              notes="Env GNEWS_API_KEY. Free tier non-commercial, small daily quota."),
    _NewsSpec("MEDIASTACK", "mediastack news", "MEDIASTACK_ACCESS_KEY", "GLOBAL",
              "https://api.mediastack.com/v1/news", _req_mediastack, _parse_mediastack,
              notes="Env MEDIASTACK_ACCESS_KEY. Free tier may only allow http://; the HTTPS endpoint is used."),
    _NewsSpec("CURRENTS_API", "Currents API search", "CURRENTS_API_KEY", "GLOBAL",
              "https://api.currentsapi.services/v1/search", _req_currents, _parse_currents,
              notes="Env CURRENTS_API_KEY."),
    _NewsSpec("THENEWSAPI", "TheNewsAPI /v1/news/all", "THENEWSAPI_TOKEN", "GLOBAL",
              "https://api.thenewsapi.com/v1/news/all", _req_thenewsapi, _parse_thenewsapi,
              notes="Env THENEWSAPI_TOKEN."),
    _NewsSpec("WORLD_NEWS_API", "World News API search-news", "WORLD_NEWS_API_KEY", "GLOBAL",
              "https://api.worldnewsapi.com/search-news", _req_worldnews, _parse_worldnews,
              notes="Env WORLD_NEWS_API_KEY (header x-api-key). Costs points per call."),
    _NewsSpec("MEDIA_CLOUD_SEARCH", "Media Cloud story search", "MEDIA_CLOUD_API_KEY", "GLOBAL",
              "https://search.mediacloud.org/api/search/story-list", _req_mediacloud, _parse_mediacloud,
              notes="Env MEDIA_CLOUD_API_KEY (Authorization: Token). Story-list endpoint shape is "
                    "unverified (only total-count was probed); metadata only, no text."),
    _NewsSpec("GUARDIAN_OPEN_PLATFORM", "The Guardian Open Platform search", "GUARDIAN_API_KEY", "GB",
              "https://content.guardianapis.com/search", _req_guardian, _parse_guardian, langs=("en",),
              notes="Env GUARDIAN_API_KEY. English-only archive back to 1999; non-commercial key."),
    _NewsSpec("NYT_ARTICLE_SEARCH", "New York Times Article Search", "NYT_API_KEY", "US",
              "https://api.nytimes.com/svc/search/v2/articlesearch.json", _req_nyt, _parse_nyt, langs=("en",),
              notes="Env NYT_API_KEY. English-only; 5 requests/minute, 500/day on the free key."),
]


def _make_news_search(spec: _NewsSpec) -> Callable[[Subject, Optional[str]], List[Evidence]]:
    def search(subject: Subject, key: Optional[str]) -> List[Evidence]:
        if not key:
            raise RuntimeError(f"{spec.env} is not set")
        out: List[Evidence] = []
        for i, lang in enumerate(spec.langs):
            if i:
                _sleep(1.0)
            url, headers, body = spec.request(subject, key, lang)
            raw = get_text(url, headers=headers, data=body) if (headers or body) else get_text(url)
            try:
                data = json.loads(raw)
            except ValueError as exc:
                raise RuntimeError(f"{spec.key}: non-JSON response: {raw[:120]}") from exc
            for a in spec.parse(data):
                ev = make_evidence(
                    subject, spec.key, "adverse_media", a["title"], text=a.get("text", ""), url=a.get("url", ""),
                    published=_iso(a.get("published", "")), language=a.get("language") or lang,
                    extra={"source": a.get("source", "")} if a.get("source") else None,
                )
                if ev:
                    out.append(ev)
        return _dedupe_cap(out)
    return search


PROVIDERS: List[LiveProvider] = [
    LiveProvider(
        key="GDELT_DOC_API", name="GDELT DOC 2.0 adverse-media search", kind="adverse_media",
        jurisdiction="GLOBAL", search=search_gdelt, url="https://api.gdeltproject.org/api/v2/doc/doc",
        license="GDELT open data, cite the GDELT Project; articles remain publishers' copyright",
        groups=["media", "news", "free"], min_interval=5.0,
        notes="Keyless. Two calls (sourcelang english and arabic, timespan 1y). GDELT matches on its English "
              "machine translation so keywords are English and the name must be Latin transliteration. "
              "Only titles are returned; name_in_title in extra says whether the title holds the name."),
] + [
    LiveProvider(
        key=s.key, name=s.name, kind="adverse_media", jurisdiction=s.jurisdiction,
        search=_make_news_search(s), url=s.doc_url, license=s.license, api_key_env=s.env,
        groups=["media", "news", "keyed"], min_interval=2.0, notes=s.notes,
    )
    for s in _SPECS
]
