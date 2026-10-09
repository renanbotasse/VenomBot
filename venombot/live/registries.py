"""Keyless company-registry lookups (corporate evidence).

A registry hit says "a company with this name exists, here are its
identifiers" — context for disambiguation and shell-company checks, never a
finding. Evidence.extra carries the registry identifiers (company number,
LEI, status, country) so a reviewer can tell namesakes apart.

Privacy: registries are queried with the subject name only, and only for
organisations (and France, whose search also indexes directors) so a person's
name is not sent to a company registry that cannot answer for it.
"""

from __future__ import annotations

import os
import re
from functools import wraps
from typing import Callable, Dict, List, Optional
from urllib.parse import quote, urlencode

from venombot.evidence import Evidence
from venombot.fetch import get_text
from venombot.live import LiveProvider
from venombot.live._freecommon import (
    THRESHOLD, best_name, cap, clip, contact_ua, is_org, iso_date, make, parse_json,
)
from venombot.screening import Subject

MAX = 15
Search = Callable[[Subject, Optional[str]], List[Evidence]]


def _json(url: str, **kw):
    return parse_json(get_text(url, **kw))


def orgs_only(fn: Search) -> Search:
    """Skip persons: a company registry cannot answer for them (and we avoid
    sending a private person's name to a third party for nothing)."""
    @wraps(fn)
    def wrapper(subject: Subject, key: Optional[str]) -> List[Evidence]:
        if subject.kind == "person":
            return []
        return fn(subject, key)
    return wrapper


def _corp(provider: str, subject: Subject, names: List[str], title: str, url: str,
          snippet: str, extra: Dict[str, object], published: str = "") -> Optional[Evidence]:
    """Evidence for a registry record when any of its names matches the subject."""
    matched, score = best_name(subject, names)
    if score < THRESHOLD:
        return None
    return make(provider, "corporate", title, url=url, snippet=snippet, published=published,
                matched=matched, score=score, extra=extra, topics=False)


# --- Australia -------------------------------------------------------------

@orgs_only
def asic(subject: Subject, key: Optional[str]) -> List[Evidence]:
    # The CKAN resource id changes monthly, so resolve the current one each run.
    pkg = _json("https://data.gov.au/data/api/3/action/package_show?id=asic-companies")
    res = [r for r in pkg["result"]["resources"]
           if r.get("name") == "Company Dataset - Current" and r.get("datastore_active")]
    if not res:
        raise RuntimeError("ASIC datastore resource not found in package_show")
    url = ("https://data.gov.au/data/api/3/action/datastore_search?"
           + urlencode({"resource_id": res[0]["id"], "q": subject.name, "limit": 50}))
    out = []
    for r in _json(url)["result"]["records"]:
        name = r.get("Company Name", "")
        ev = _corp("AU_ASIC_COMPANY_DATASTORE", subject, [name], name, url,
                   f"{name} (ACN {r.get('ACN')}), status {r.get('Status')}, registered {r.get('Date of Registration')}",
                   {"company_number": r.get("ACN"), "abn": r.get("ABN"), "status": r.get("Status"),
                    "company_type": r.get("Type"), "country": "AU",
                    "deregistered": r.get("Date of Deregistration")},
                   iso_date(_au_date(r.get("Date of Registration"))))
        if ev:
            out.append(ev)
    return cap(out, MAX)


def _au_date(value: object) -> str:
    m = re.match(r"(\d{2})/(\d{2})/(\d{4})", str(value or ""))
    return f"{m.group(3)}-{m.group(2)}-{m.group(1)}" if m else ""


# --- Czech Republic --------------------------------------------------------

@orgs_only
def ares(subject: Subject, key: Optional[str]) -> List[Evidence]:
    import json as _j
    body = _j.dumps({"obchodniJmeno": subject.name, "pocet": 25}).encode()
    data = _json("https://ares.gov.cz/ekonomicke-subjekty-v-be/rest/ekonomicke-subjekty/vyhledat",
                 data=body, headers={"Content-Type": "application/json"})
    out = []
    for r in data.get("ekonomickeSubjekty", []):
        name = r.get("obchodniJmeno", "")
        sidlo = r.get("sidlo") or {}
        ev = _corp("CZ_ARES", subject, [name], name,
                   f"https://ares.gov.cz/ekonomicke-subjekty-v-be/rest/ekonomicke-subjekty/{r.get('ico')}",
                   f"{name}, ICO {r.get('ico')}, {sidlo.get('textovaAdresa', '')}",
                   {"company_number": r.get("ico"), "legal_form": r.get("pravniForma"),
                    "country": sidlo.get("kodStatu"), "address": sidlo.get("textovaAdresa"),
                    "dissolved": r.get("datumZaniku")},
                   iso_date(r.get("datumVzniku")))
        if ev:
            out.append(ev)
    return cap(out, MAX)


# --- Finland ---------------------------------------------------------------

@orgs_only
def prh(subject: Subject, key: Optional[str]) -> List[Evidence]:
    url = "https://avoindata.prh.fi/opendata-ytj-api/v3/companies?" + urlencode({"name": subject.name})
    out = []
    for c in _json(url).get("companies", [])[:200]:
        names = c.get("names") or []
        # Type 1 = current legal name; the API also matches auxiliary/trade names.
        legal = next((n["name"] for n in names if n.get("type") == "1" and not n.get("endDate")),
                     names[0]["name"] if names else "")
        biz = (c.get("businessId") or {}).get("value", "")
        status = "ceased" if c.get("endDate") else "active"
        addr = (c.get("addresses") or [{}])[0]
        city = ((addr.get("postOffices") or [{}])[0]).get("city", "")
        ev = _corp("FI_PRH_YTJ", subject, [n.get("name", "") for n in names], legal,
                   f"https://www.ytj.fi/en/index/businessid.html?id={quote(biz)}" if biz else url,
                   f"{legal}, business ID {biz}, {status}",
                   {"company_number": biz, "status": status, "country": "FI", "city": city,
                    "registered": (c.get("businessId") or {}).get("registrationDate")},
                   iso_date((c.get("businessId") or {}).get("registrationDate")))
        if ev:
            out.append(ev)
    return cap(out, MAX)


# --- France ----------------------------------------------------------------

def fr_recherche(subject: Subject, key: Optional[str]) -> List[Evidence]:
    # q matches company names AND directors' names, so persons are screened too.
    url = "https://recherche-entreprises.api.gouv.fr/search?" + urlencode({"q": subject.name, "per_page": 25})
    out = []
    for r in _json(url).get("results", []):
        siren = r.get("siren")
        page = f"https://annuaire-entreprises.data.gouv.fr/entreprise/{siren}"
        siege = r.get("siege") or {}
        base = {"company_number": siren, "country": "FR", "address": siege.get("adresse"),
                "status": "active" if r.get("etat_administratif") == "A" else "closed",
                "legal_form_code": r.get("nature_juridique")}
        ev = _corp("FR_RECHERCHE_ENTREPRISES", subject, [r.get("nom_complet", ""), r.get("nom_raison_sociale", "")],
                   r.get("nom_complet", ""), page, f"{r.get('nom_complet')}, SIREN {siren}, {siege.get('adresse', '')}",
                   base, iso_date(r.get("date_creation")))
        if ev:
            out.append(ev)
            continue
        for d in r.get("dirigeants") or []:
            if d.get("type_dirigeant") != "personne physique":
                continue
            full = f"{(d.get('prenoms') or '').split(',')[0]} {d.get('nom', '')}".strip()
            matched, score = best_name(subject, [full])
            if score >= THRESHOLD:
                extra = dict(base, role=d.get("qualite"), birth=d.get("date_de_naissance") or d.get("annee_de_naissance"),
                             nationality=d.get("nationalite"), company=r.get("nom_complet"))
                out.append(make("FR_RECHERCHE_ENTREPRISES", "corporate",
                                f"{full} - {d.get('qualite')} of {r.get('nom_complet')}", url=page,
                                snippet=f"{full} listed as {d.get('qualite')} of {r.get('nom_complet')} (SIREN {siren})",
                                matched=matched, score=score, extra=extra, topics=False))
    return cap(out, MAX)


# --- GLEIF -----------------------------------------------------------------

@orgs_only
def gleif_fuzzy(subject: Subject, key: Optional[str]) -> List[Evidence]:
    url = "https://api.gleif.org/api/v1/fuzzycompletions?" + urlencode({"field": "entity.legalName", "q": subject.name})
    rows = _json(url).get("data", [])
    if not rows:
        # fuzzycompletions answered empty even for known names on 2026-10-09;
        # the prefix/token autocompletion on the same index still works.
        url = "https://api.gleif.org/api/v1/autocompletions?" + urlencode({"field": "fulltext", "q": subject.name})
        rows = _json(url).get("data", [])
    out = []
    for r in rows:
        name = (r.get("attributes") or {}).get("value", "")
        lei = (((r.get("relationships") or {}).get("lei-records") or {}).get("data") or {}).get("id", "")
        ev = _corp("GLEIF_FUZZY_SEARCH", subject, [name], name, f"https://search.gleif.org/#/record/{lei}",
                   f"{name}, LEI {lei}", {"lei": lei})
        if ev:
            out.append(ev)
    return cap(out, MAX)


@orgs_only
def gleif_filter(subject: Subject, key: Optional[str]) -> List[Evidence]:
    # Brackets in filter keys must be percent-encoded.
    url = "https://api.gleif.org/api/v1/lei-records?" + urlencode(
        {"filter[entity.legalName]": subject.name, "page[size]": 20})
    out = []
    for r in _json(url).get("data", []):
        a = r.get("attributes") or {}
        ent = a.get("entity") or {}
        lei = a.get("lei") or r.get("id", "")
        name = (ent.get("legalName") or {}).get("name", "")
        others = [o.get("name", "") for o in (ent.get("otherNames") or []) + (ent.get("transliteratedOtherNames") or [])]
        addr = ent.get("legalAddress") or {}
        ev = _corp("GLEIF_LEI_RECORDS_FILTER", subject, [name] + others, name,
                   f"https://search.gleif.org/#/record/{lei}",
                   f"{name}, LEI {lei}, {addr.get('city', '')} {addr.get('country', '')}, {ent.get('status')}",
                   {"lei": lei, "status": ent.get("status"), "country": addr.get("country"),
                    "jurisdiction": ent.get("jurisdiction"), "registered_as": ent.get("registeredAs"),
                    "registration_authority": (ent.get("registeredAt") or {}).get("id"),
                    "city": addr.get("city")},
                   iso_date(ent.get("creationDate")))
        if ev:
            out.append(ev)
    return cap(out, MAX)


# --- OpenCorporates / OpenFIGI ---------------------------------------------

@orgs_only
def opencorporates(subject: Subject, key: Optional[str]) -> List[Evidence]:
    params = {"query": subject.name}
    token = os.environ.get("OPENCORPORATES_API_TOKEN")
    if token:  # documented terms ask for a token; it is optional here
        params["api_token"] = token
    out = []
    for r in _json("https://opencorporates.com/reconcile?" + urlencode(params)).get("result", []):
        name = r.get("name", "")
        parts = str(r.get("id", "")).strip("/").split("/")  # companies/<jurisdiction>/<number>
        juris, number = (parts[1], "/".join(parts[2:])) if len(parts) >= 3 else ("", "")
        ev = _corp("OPENCORPORATES_RECONCILE", subject, [name], name, r.get("uri", ""),
                   f"{name}, {juris.upper()} company {number}",
                   {"company_number": number, "jurisdiction": juris, "country": juris.split("_")[0].upper(),
                    "reconcile_score": r.get("score")})
        if ev:
            out.append(ev)
    return cap(out, MAX)


@orgs_only
def openfigi(subject: Subject, key: Optional[str]) -> List[Evidence]:
    import json as _j
    headers = {"Content-Type": "application/json"}
    token = os.environ.get("OPENFIGI_API_KEY")
    if token:
        headers["X-OPENFIGI-APIKEY"] = token
    data = _json("https://api.openfigi.com/v3/search", data=_j.dumps({"query": subject.name}).encode(),
                 headers=headers)
    out, seen = [], set()
    for r in data.get("data", []):
        name = r.get("name", "")
        ident = r.get("compositeFIGI") or r.get("figi")
        if ident in seen:
            continue
        seen.add(ident)
        ev = _corp("OPENFIGI_SEARCH", subject, [name], f"{name} ({r.get('ticker')}, {r.get('exchCode')})",
                   f"https://www.openfigi.com/id/{r.get('figi')}",
                   f"{name} listed as {r.get('securityType')} {r.get('ticker')} on {r.get('exchCode')}",
                   {"figi": r.get("figi"), "composite_figi": r.get("compositeFIGI"), "ticker": r.get("ticker"),
                    "exchange": r.get("exchCode"), "security_type": r.get("securityType"),
                    "market_sector": r.get("marketSector")})
        if ev:
            out.append(ev)
    return cap(out, MAX)


# --- Hong Kong, Ireland, Israel, Norway, Singapore, New York ----------------

@orgs_only
def hk(subject: Subject, key: Optional[str]) -> List[Evidence]:
    # Only begins_with works on Comp_name ("contains"/"equal" return 400).
    url = "https://data.cr.gov.hk/cr/api/api/v1/api_builder/json/local/search?" + urlencode(
        {"query[0][key1]": "Comp_name", "query[0][key2]": "begins_with", "query[0][key3]": subject.name},
        quote_via=quote)  # the API rejects '+' for spaces
    try:
        rows = _json(url)
    except RuntimeError as exc:
        # An empty result is reported as HTTP 400 {"message": "No result found."}.
        if "HTTP 400" in str(exc):
            return []
        raise
    out = []
    for r in rows if isinstance(rows, list) else []:
        en, zh = r.get("English_Company_Name") or "", r.get("Chinese_Company_Name") or ""
        ev = _corp("HK_COMPANIES_REGISTRY_API", subject, [en, zh], en or zh, url,
                   f"{en} ({zh}), BRN {r.get('Brn')}, {r.get('Address_of_Registered_Office', '')}",
                   {"company_number": r.get("Brn"), "company_type": r.get("Company_Type"), "country": "HK",
                    "address": r.get("Address_of_Registered_Office"), "chinese_name": zh},
                   _dmy(r.get("Date_of_Incorporation")))
        if ev:
            out.append(ev)
    return cap(out, MAX)


def _dmy(value: object) -> str:
    m = re.match(r"(\d{2})-(\d{2})-(\d{4})", str(value or ""))
    return f"{m.group(3)}-{m.group(2)}-{m.group(1)}" if m else ""


@orgs_only
def ie_cro(subject: Subject, key: Optional[str]) -> List[Evidence]:
    url = "https://opendata.cro.ie/api/3/action/datastore_search?" + urlencode(
        {"resource_id": "3fef41bc-b8f4-4b10-8434-ce51c29b1bba", "q": subject.name, "limit": 50})
    out = []
    for r in _json(url)["result"]["records"]:
        name = r.get("company_name", "")
        addr = ", ".join(str(r.get(f"company_address_{i}") or "").strip() for i in range(1, 5) if r.get(f"company_address_{i}"))
        ev = _corp("IE_CRO_COMPANIES_DATASTORE", subject, [name], name, url,
                   f"{name}, CRO {r.get('company_num')}, {str(r.get('company_status', '')).strip()}",
                   {"company_number": r.get("company_num"), "status": str(r.get("company_status") or "").strip(),
                    "company_type": r.get("company_type"), "country": "IE", "address": addr,
                    "dissolved": r.get("comp_dissolved_date")},
                   iso_date(r.get("company_reg_date")))
        if ev:
            out.append(ev)
    return cap(out, MAX)


def _plain(name: str) -> str:
    """Drop punctuation: data.gov.sg answers HTTP 409 to parentheses in q."""
    return re.sub(r"[^\w\s-]", " ", name).strip()


def _rarest_word(name: str) -> str:
    """data.gov.sg ranks multi-word q as OR, flooding results; query one long word, filter locally."""
    # ACRA answers HTTP 409 to punctuation ("al-Zawahiri"), so keep letters and digits only.
    words = [re.sub(r"[^A-Za-z0-9]", "", w) for w in _plain(name).split()]
    words = [w for w in words if w]
    return max(words, key=len) if words else re.sub(r"[^A-Za-z0-9]", "", name) or "a"


def _first_words(name: str, n: int) -> str:
    """The IL datastore returns nothing for 3+ word queries, so query the head and filter locally."""
    return " ".join(_plain(name).lower().split()[:n])


@orgs_only
def il_registrar(subject: Subject, key: Optional[str]) -> List[Evidence]:
    url = "https://data.gov.il/api/3/action/datastore_search?" + urlencode(
        {"resource_id": "f004176c-b85f-4542-8901-7b3176f9a054", "q": _first_words(subject.name, 2), "limit": 50})
    out = []
    for r in _json(url)["result"]["records"]:
        he = (r.get("שם חברה") or "").replace("~", '"')
        en = r.get("שם באנגלית") or ""
        # 'מפרה' is the registrar's "company in breach" flag (e.g. unpaid fees).
        breach = bool((r.get("מפרה") or "").strip())
        ev = _corp("IL_COMPANIES_REGISTRAR", subject, [en, he], en or he, url,
                   f"{en} / {he}, company {r.get('מספר חברה')}, {r.get('סטטוס חברה')}"
                   + (", flagged in breach by the registrar" if breach else ""),
                   {"company_number": r.get("מספר חברה"), "status": r.get("סטטוס חברה"),
                    "company_type": r.get("סוג תאגיד"), "country": "IL", "hebrew_name": he,
                    "government_company": (r.get("חברה ממשלתית") or "") == "כן",
                    "registrar_breach_flag": breach, "city": r.get("שם עיר")},
                   _dmy(str(r.get("תאריך התאגדות") or "").replace("/", "-")))
        if ev:
            out.append(ev)
    return cap(out, MAX)


@orgs_only
def brreg(subject: Subject, key: Optional[str]) -> List[Evidence]:
    url = "https://data.brreg.no/enhetsregisteret/api/enheter?" + urlencode({"navn": subject.name, "size": 25})
    out = []
    for r in (_json(url).get("_embedded") or {}).get("enheter", []):
        name = r.get("navn", "")
        former = [h.get("navn", "") for h in r.get("historiskeNavn") or []]
        addr = r.get("forretningsadresse") or {}
        org = r.get("organisasjonsnummer")
        status = ("bankrupt" if r.get("konkurs") else "under liquidation" if r.get("underAvvikling")
                  or r.get("underTvangsavviklingEllerTvangsopplosning") else "active")
        ev = _corp("NO_BRREG_ENHETSREGISTERET", subject, [name] + former, name,
                   f"https://data.brreg.no/enhetsregisteret/api/enheter/{org}",
                   f"{name}, org no. {org}, {status}",
                   {"company_number": org, "status": status, "country": addr.get("landkode") or "NO",
                    "company_type": (r.get("organisasjonsform") or {}).get("beskrivelse"),
                    "city": addr.get("poststed"), "former_names": former[:5]},
                   iso_date(r.get("registreringsdatoEnhetsregisteret")))
        if ev:
            out.append(ev)
    return cap(out, MAX)


SG_RESOURCES = ("d_3f960c10fed6145404ca7b821f263b87", "d_b1d2b840ab9e993570c037b706b39bb8")


@orgs_only
def acra(subject: Subject, key: Optional[str]) -> List[Evidence]:
    out = []
    for res in SG_RESOURCES:  # the second resource holds embassies/other agencies
        url = "https://data.gov.sg/api/action/datastore_search?" + urlencode(
            {"resource_id": res, "q": _rarest_word(subject.name), "limit": 100})
        for r in _json(url)["result"]["records"]:
            name = r.get("entity_name", "")
            ev = _corp("SG_ACRA_UEN_DATASTORE", subject, [name], name, url,
                       f"{name}, UEN {r.get('uen')}, {r.get('uen_status_desc')}",
                       {"company_number": r.get("uen"), "status": r.get("uen_status_desc"),
                        "company_type": r.get("entity_type_desc"), "country": "SG",
                        "issuer": r.get("issuance_agency_desc"), "street": r.get("reg_street_name")},
                       iso_date(r.get("uen_issue_date")))
            if ev:
                out.append(ev)
    return cap(out, MAX)


@orgs_only
def ny_dos(subject: Subject, key: Optional[str]) -> List[Evidence]:
    # $q is full-text across columns, including the service-of-process name,
    # which surfaces hidden links (an LLC served "c/o <bank>").
    url = "https://data.ny.gov/resource/n9v6-gdp6.json?" + urlencode({"$q": subject.name, "$limit": 50})
    out = []
    for r in _json(url):
        name = r.get("current_entity_name", "")
        proc = r.get("dos_process_name", "")
        base = {"company_number": r.get("dos_id"), "entity_type": r.get("entity_type"),
                "jurisdiction": r.get("jurisdiction"), "country": "US", "county": r.get("county")}
        url_r = f"https://apps.dos.ny.gov/publicInquiry/#search?dosid={r.get('dos_id')}"
        ev = _corp("US_NY_DOS_CORPORATIONS", subject, [name], name, url_r,
                   f"{name}, NY DOS {r.get('dos_id')}", dict(base, match_field="current_entity_name"),
                   iso_date(r.get("initial_dos_filing_date")))
        if ev:
            out.append(ev)
            continue
        matched, score = best_name(subject, [proc])
        if score >= THRESHOLD:
            out.append(make("US_NY_DOS_CORPORATIONS", "corporate", f"{name} (service of process c/o {proc})",
                            url=url_r, snippet=f"{name} designates {proc} for service of process",
                            published=iso_date(r.get("initial_dos_filing_date")), matched=matched, score=score,
                            extra=dict(base, match_field="dos_process_name"), topics=False))
    return cap(out, MAX)


# --- SEC EDGAR (entity index and full text) ---------------------------------

def _edgar_headers() -> Dict[str, str]:
    return {"User-Agent": contact_ua(), "Accept": "application/json"}


@orgs_only
def edgar_entity(subject: Subject, key: Optional[str]) -> List[Evidence]:
    url = "https://efts.sec.gov/LATEST/search-index?" + urlencode({"keysTyped": subject.name})
    out = []
    for h in (_json(url, headers=_edgar_headers()).get("hits") or {}).get("hits", []):
        name = (h.get("_source") or {}).get("entity", "")
        cik = str(h.get("_id", ""))
        ev = _corp("SEC_EDGAR_ENTITY_SEARCH", subject, [name], name,
                   f"https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK={cik}",
                   f"{name}, SEC CIK {cik}", {"cik": cik, "country": "US"})
        if ev:
            out.append(ev)
    return cap(out, MAX)


def _edgar_fts(provider: str, subject: Subject, extra_params: Dict[str, str]) -> List[Evidence]:
    params = {"q": f'"{subject.name}"'}
    params.update(extra_params)
    url = "https://efts.sec.gov/LATEST/search-index?" + urlencode(params)
    out = []
    for h in (_json(url, headers=_edgar_headers()).get("hits") or {}).get("hits", []):
        s = h.get("_source") or {}
        adsh, _, fname = str(h.get("_id", "")).partition(":")
        cik = str((s.get("ciks") or [""])[0]).lstrip("0")
        names = s.get("display_names") or []
        doc = f"https://www.sec.gov/Archives/edgar/data/{cik}/{adsh.replace('-', '')}/{fname}" if cik and fname else url
        # The hit has no text snippet; the server matched the quoted phrase in the
        # document body, which we cannot see. Report that honestly via extra.
        matched, score = best_name(subject, [re.sub(r"\s*\(.*", "", n) for n in names])
        basis = "name in filer name" if score >= THRESHOLD else "quoted-phrase match in filing text (server side)"
        out.append(make(provider, "corporate", f"{s.get('form')} {s.get('file_type', '')} - {', '.join(names)[:120]}",
                        url=doc, snippet=f"Filing {adsh} ({s.get('form')}, {s.get('file_date')}) mentions \"{subject.name}\"",
                        published=iso_date(s.get("file_date")), matched=matched if score >= THRESHOLD else subject.name,
                        score=max(score, THRESHOLD), topics=False,
                        extra={"match_basis": basis, "form": s.get("form"), "filer_ciks": s.get("ciks"),
                               "filer": names, "accession": adsh, "inc_states": s.get("inc_states"),
                               "biz_locations": s.get("biz_locations")}))
    return out[:10]


def edgar_fulltext(subject: Subject, key: Optional[str]) -> List[Evidence]:
    return _edgar_fts("SEC_EDGAR_FULLTEXT", subject, {})


def edgar_fts_forms(subject: Subject, key: Optional[str]) -> List[Evidence]:
    # Narrower view: only forms that disclose officers, related parties, litigation.
    return _edgar_fts("US_SEC_EDGAR_FTS", subject, {"forms": "8-K,10-K,DEF 14A"})


def _p(key: str, name: str, jur: str, fn: Search, url: str, lic: str, notes: str,
       interval: float = 1.0, groups: Optional[List[str]] = None) -> LiveProvider:
    return LiveProvider(key=key, name=name, kind="corporate", jurisdiction=jur, search=fn, url=url,
                        license=lic, groups=groups or ["corporate", "free"], min_interval=interval,
                        notes=notes)


PROVIDERS: List[LiveProvider] = [
    _p("AU_ASIC_COMPANY_DATASTORE", "ASIC company register (data.gov.au)", "AU", asic,
       "https://data.gov.au/data/api/3/action/datastore_search", "CC-BY",
       "Sends the subject name only (organisations only); ASIC dataset has no officers."),
    _p("CZ_ARES", "Czech ARES economic subjects", "CZ", ares,
       "https://ares.gov.cz/ekonomicke-subjekty-v-be/rest/ekonomicke-subjekty/vyhledat", "public",
       "Sends the subject name only (organisations only) by POST."),
    _p("FI_PRH_YTJ", "Finnish PRH/YTJ business information", "FI", prh,
       "https://avoindata.prh.fi/opendata-ytj-api/v3/companies", "CC-BY",
       "Sends the subject name only (organisations only); also matches trade names, post-filtered."),
    _p("FR_RECHERCHE_ENTREPRISES", "France Recherche d'entreprises", "FR", fr_recherche,
       "https://recherche-entreprises.api.gouv.fr/search", "Licence Ouverte / Etalab 2.0",
       "Sends the subject name only; searches company names and directors, so persons are queried too.",
       interval=0.3),
    _p("GLEIF_FUZZY_SEARCH", "GLEIF fuzzy legal-name search", "INT", gleif_fuzzy,
       "https://api.gleif.org/api/v1/fuzzycompletions", "CC0",
       "Sends the subject name only (organisations only); returns name and LEI."),
    _p("GLEIF_LEI_RECORDS_FILTER", "GLEIF LEI records by legal name", "INT", gleif_filter,
       "https://api.gleif.org/api/v1/lei-records", "CC0",
       "Sends the subject name only (organisations only); full LEI record fields."),
    _p("OPENCORPORATES_RECONCILE", "OpenCorporates reconcile", "INT", opencorporates,
       "https://opencorporates.com/reconcile", "ODbL (share-alike, attribution)",
       "Sends the subject name only (organisations only); anonymous use may be throttled, set OPENCORPORATES_API_TOKEN when available.",
       interval=3.0),
    _p("OPENFIGI_SEARCH", "OpenFIGI instrument search", "INT", openfigi,
       "https://api.openfigi.com/v3/search", "terms",
       "Sends the subject name only (organisations only); optional OPENFIGI_API_KEY raises limits.",
       interval=2.5),
    _p("HK_COMPANIES_REGISTRY_API", "Hong Kong Companies Registry", "HK", hk,
       "https://data.cr.gov.hk/cr/api/api/v1/api_builder/json/local/search", "terms",
       "Sends the subject name only (organisations only); begins-with matching on the company name; an empty result arrives as HTTP 400 and is treated as no hits."),
    _p("IE_CRO_COMPANIES_DATASTORE", "Ireland CRO companies", "IE", ie_cro,
       "https://opendata.cro.ie/api/3/action/datastore_search", "CC-BY",
       "Sends the subject name only (organisations only)."),
    _p("IL_COMPANIES_REGISTRAR", "Israel Companies Registrar", "IL", il_registrar,
       "https://data.gov.il/api/3/action/datastore_search", "terms",
       "Sends the subject name only (organisations only); carries the registrar's breach flag."),
    _p("NO_BRREG_ENHETSREGISTERET", "Norway Enhetsregisteret", "NO", brreg,
       "https://data.brreg.no/enhetsregisteret/api/enheter", "NLOD 2.0",
       "Sends the subject name only (organisations only)."),
    _p("SG_ACRA_UEN_DATASTORE", "Singapore ACRA UEN entities", "SG", acra,
       "https://data.gov.sg/api/action/datastore_search", "Singapore Open Data Licence v1.0",
       "Sends the subject name only (organisations only) to two resources.", interval=2.0),
    _p("US_NY_DOS_CORPORATIONS", "New York DOS active corporations", "US", ny_dos,
       "https://data.ny.gov/resource/n9v6-gdp6.json", "public",
       "Sends the subject name only (organisations only); also matches the service-of-process name."),
    _p("SEC_EDGAR_ENTITY_SEARCH", "SEC EDGAR entity index", "US", edgar_entity,
       "https://efts.sec.gov/LATEST/search-index", "public",
       "Sends the subject name only (organisations only); declares VENOMBOT_CONTACT in the User-Agent.",
       interval=0.5),
    _p("SEC_EDGAR_FULLTEXT", "SEC EDGAR full-text search", "US", edgar_fulltext,
       "https://efts.sec.gov/LATEST/search-index", "public",
       "Sends the subject name only, as a quoted phrase; hits are mentions in filings, expect false positives.",
       interval=0.5),
    _p("US_SEC_EDGAR_FTS", "SEC EDGAR full text (8-K/10-K/DEF 14A)", "US", edgar_fts_forms,
       "https://efts.sec.gov/LATEST/search-index", "public",
       "Sends the subject name only, as a quoted phrase; overlaps SEC_EDGAR_FULLTEXT on a narrower set of forms.",
       interval=0.5),
]
