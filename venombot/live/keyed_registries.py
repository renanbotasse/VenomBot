"""Key-gated registry and enforcement search APIs.

UK Companies House (companies, officers, disqualified directors),
OpenCorporates, Brazil's Portal da Transparencia (CEIS/CNEP sanctions and
PEP), the UK FCA Register, GovInfo federal court opinions, OCCRP Aleph and
the US Consolidated Screening List search.

None of these can be called without an account, so the request/response
shapes follow each vendor's documentation (kept in the discovery checkpoint
samples) and are verified by mocked unit tests only. Any HTTP error, auth
error or unexpected shape raises so the framework records it instead of
quietly reporting "no hits". Results are context (Evidence), filtered with
the same fuzzy name check as the news providers.
"""

from __future__ import annotations

import base64
import json
import os
import re
from typing import Any, Dict, List, Optional
from urllib.parse import quote, urlencode

from venombot.evidence import Evidence
from venombot.fetch import get_text
from venombot.live import LiveProvider
from venombot.live.media_apis import MAX_RESULTS, _dedupe_cap, _iso, make_evidence
from venombot.screening import Subject


def _json(url: str, provider: str, **kw: Any) -> Any:
    """GET/POST and decode JSON; non-JSON bodies (WAF pages, errors) raise."""
    raw = get_text(url, **kw)
    try:
        return json.loads(raw)
    except ValueError as exc:
        raise RuntimeError(f"{provider}: non-JSON response: {raw[:120]}") from exc


def _digits(subject: Subject, lengths: tuple) -> str:
    """First subject identifier that is purely digits of an accepted length (CPF 11 / CNPJ 14)."""
    for ident in subject.identifiers:
        d = re.sub(r"\D", "", ident)
        if len(d) in lengths:
            return d
    return ""


# ------------------------------------------------------- Companies House

_CH_BASE = "https://api.company-information.service.gov.uk"
_CH_PUBLIC = "https://find-and-update.company-information.service.gov.uk"


def _ch_headers(key: str) -> Dict[str, str]:
    # Companies House: HTTP Basic, API key as the username, empty password.
    return {"Authorization": "Basic " + base64.b64encode(f"{key}:".encode()).decode()}


def _ch_get(path: str, params: Dict[str, Any], key: str, provider: str) -> Dict[str, Any]:
    url = _CH_BASE + path + "?" + urlencode(params, quote_via=quote)
    data = _json(url, provider, headers=_ch_headers(key))
    if isinstance(data, dict) and data.get("error"):
        raise RuntimeError(f"{provider}: {data.get('error')}")
    return data


def search_ch(subject: Subject, key: Optional[str]) -> List[Evidence]:
    """Companies and officers whose registered name matches the subject."""
    if not key:
        raise RuntimeError("COMPANIES_HOUSE_API_KEY is not set")
    out: List[Evidence] = []
    if subject.kind in ("org", "any"):
        data = _ch_get("/search/companies", {"q": subject.name, "items_per_page": 20}, key, "UK_COMPANIES_HOUSE_API")
        for it in data.get("items", []) or []:
            ev = make_evidence(
                subject, "UK_COMPANIES_HOUSE_API", "corporate", it.get("title", ""),
                text=f"{it.get('company_status', '')} {it.get('address_snippet', '')}",
                url=f"{_CH_PUBLIC}/company/{it.get('company_number', '')}", published=it.get("date_of_creation", ""),
                language="en",
                extra={"company_number": it.get("company_number", ""), "status": it.get("company_status", ""),
                       "company_type": it.get("company_type", ""), "address": it.get("address_snippet", "")},
            )
            if ev:
                out.append(ev)
    if subject.kind in ("person", "any"):
        data = _ch_get("/search/officers", {"q": subject.name, "items_per_page": 20}, key, "UK_COMPANIES_HOUSE_API")
        for it in data.get("items", []) or []:
            link = (it.get("links") or {}).get("self", "")
            ev = make_evidence(
                subject, "UK_COMPANIES_HOUSE_API", "corporate", it.get("title", ""),
                text=it.get("description", ""), url=_CH_PUBLIC + link.replace("/appointments", ""), language="en",
                extra={"appointments": it.get("appointment_count"), "date_of_birth": it.get("date_of_birth") or {},
                       "address": it.get("address_snippet", "")},
            )
            if ev:
                out.append(ev)
    return _dedupe_cap(out)


def search_ch_disqualified(subject: Subject, key: Optional[str]) -> List[Evidence]:
    """Disqualified directors (a Companies House enforcement register)."""
    if not key:
        raise RuntimeError("COMPANIES_HOUSE_API_KEY is not set")
    data = _ch_get("/search/disqualified-officers", {"q": subject.name, "items_per_page": 20}, key, "UK_CH_DISQUALIFIED")
    out: List[Evidence] = []
    for it in data.get("items", []) or []:
        link = (it.get("links") or {}).get("self", "")
        ev = make_evidence(
            subject, "UK_CH_DISQUALIFIED", "enforcement", it.get("title", ""),
            text=f"Disqualified director. {it.get('description', '')}", url=_CH_PUBLIC + link, language="en",
            extra={"date_of_birth": it.get("date_of_birth", ""), "address": it.get("address_snippet", "")},
        )
        if ev:
            out.append(ev)
    return _dedupe_cap(out)


# ----------------------------------------------------------- OpenCorporates

def search_opencorporates(subject: Subject, key: Optional[str]) -> List[Evidence]:
    """Companies (orgs) or officers (persons) across registries; ODbL, attribute OpenCorporates."""
    if not key:
        raise RuntimeError("OPENCORPORATES_API_TOKEN is not set")
    out: List[Evidence] = []
    base = "https://api.opencorporates.com/v0.4"
    if subject.kind in ("org", "any"):
        url = f"{base}/companies/search?" + urlencode({"q": subject.name, "per_page": 20, "api_token": key}, quote_via=quote)
        data = _json(url, "OPENCORPORATES_API")
        if data.get("error"):
            raise RuntimeError(f"OPENCORPORATES_API: {data['error']}")
        for row in ((data.get("results") or {}).get("companies") or []):
            c = row.get("company", row)
            ev = make_evidence(
                subject, "OPENCORPORATES_API", "corporate", c.get("name", ""),
                text=f"{c.get('current_status') or ''} {c.get('registered_address_in_full') or ''}",
                url=c.get("opencorporates_url", ""), published=c.get("incorporation_date") or "",
                extra={"jurisdiction_code": c.get("jurisdiction_code", ""), "company_number": c.get("company_number", ""),
                       "status": c.get("current_status") or "", "source": "OpenCorporates (ODbL)"},
            )
            if ev:
                out.append(ev)
    if subject.kind in ("person", "any"):
        url = f"{base}/officers/search?" + urlencode({"q": subject.name, "per_page": 20, "api_token": key}, quote_via=quote)
        data = _json(url, "OPENCORPORATES_API")
        if data.get("error"):
            raise RuntimeError(f"OPENCORPORATES_API: {data['error']}")
        for row in ((data.get("results") or {}).get("officers") or []):
            o = row.get("officer", row)
            company = (o.get("company") or {}).get("name", "")
            ev = make_evidence(
                subject, "OPENCORPORATES_API", "corporate", o.get("name", ""),
                text=f"{o.get('position') or 'officer'} of {company}", url=o.get("opencorporates_url", ""),
                extra={"position": o.get("position") or "", "company": company,
                       "jurisdiction_code": o.get("jurisdiction_code", ""), "source": "OpenCorporates (ODbL)"},
            )
            if ev:
                out.append(ev)
    return _dedupe_cap(out)


# -------------------------------------------------- Portal da Transparencia

_PT_BASE = "https://api.portaldatransparencia.gov.br/api-de-dados"


def _pt_get(path: str, params: Dict[str, Any], key: str, provider: str) -> List[Dict[str, Any]]:
    url = f"{_PT_BASE}{path}?" + urlencode(params, quote_via=quote)
    data = _json(url, provider, headers={"chave-api-dados": key, "Accept": "application/json"})
    if isinstance(data, dict):  # errors come back as {"Erro na API": "..."}
        raise RuntimeError(f"{provider}: {next(iter(data.values()), data)}")
    return data


def search_pt_sanctions(subject: Subject, key: Optional[str]) -> List[Evidence]:
    """CEIS (inidoneas/suspended companies) and CNEP (Clean Company Act) by CPF/CNPJ or name."""
    if not key:
        raise RuntimeError("PORTAL_TRANSPARENCIA_API_KEY is not set")
    out: List[Evidence] = []
    ident = _digits(subject, (11, 14))
    for endpoint in ("ceis", "cnep"):
        params: Dict[str, Any] = {"pagina": 1}
        if ident:
            params["codigoSancionado"] = ident
        else:
            params["nomeSancionado"] = subject.name
        for it in _pt_get(f"/{endpoint}", params, key, "BR_PORTAL_TRANSPARENCIA_API")[:MAX_RESULTS]:
            sancionado = it.get("sancionado") or {}
            pessoa = it.get("pessoa") or {}
            names = [sancionado.get("nome", ""), pessoa.get("nome", ""), pessoa.get("razaoSocialReceita", ""),
                     pessoa.get("nomeFantasiaReceita", "")]
            tipo = (it.get("tipoSancao") or {}).get("descricaoResumida", "")
            orgao = (it.get("orgaoSancionador") or {}).get("nome", "")
            title = next((n for n in names if n), "")
            ev = make_evidence(
                subject, "BR_PORTAL_TRANSPARENCIA_API", "enforcement", title,
                text=f"{tipo} {orgao}".strip(), field_texts=names,
                url=f"https://portaldatransparencia.gov.br/sancoes/{endpoint}/{it.get('id', '')}",
                published=it.get("dataInicioSancao", ""), language="pt",
                extra={"register": endpoint.upper(), "sanction": tipo, "authority": orgao,
                       "start": it.get("dataInicioSancao", ""), "end": it.get("dataFimSancao", ""),
                       "id_by_identifier": bool(ident)},
                require_name=not ident,
            )
            if ev:
                if ident:
                    ev.name_score = max(ev.name_score, 1.0)  # matched by tax id
                out.append(ev)
    return _dedupe_cap(out)


def search_pt_pep(subject: Subject, key: Optional[str]) -> List[Evidence]:
    """Brazil's federal PEP register, by CPF when known else by name."""
    if not key:
        raise RuntimeError("PORTAL_TRANSPARENCIA_API_KEY is not set")
    cpf = _digits(subject, (11,))
    params: Dict[str, Any] = {"pagina": 1}
    params["cpf" if cpf else "nome"] = cpf or subject.name
    out: List[Evidence] = []
    for it in _pt_get("/peps", params, key, "BR_PORTAL_TRANSPARENCIA_PEP_API")[:MAX_RESULTS]:
        role = " ".join(x for x in (it.get("descricao_funcao"), it.get("nome_orgao")) if x)
        ev = make_evidence(
            subject, "BR_PORTAL_TRANSPARENCIA_PEP_API", "pep_info", it.get("nome", ""), text=role,
            url="https://portaldatransparencia.gov.br/pep", published=it.get("dt_inicio_exercicio", ""), language="pt",
            extra={"function": it.get("descricao_funcao", ""), "level": it.get("nivel_funcao", ""),
                   "body": it.get("nome_orgao", ""), "start": it.get("dt_inicio_exercicio", ""),
                   "end": it.get("dt_fim_exercicio", ""), "grace_end": it.get("dt_fim_carencia", "")},
            require_name=not cpf,
        )
        if ev:
            if cpf:
                ev.name_score = max(ev.name_score, 1.0)
            out.append(ev)
    return _dedupe_cap(out)


# ----------------------------------------------------------------- FCA

def search_fca(subject: Subject, key: Optional[str]) -> List[Evidence]:
    """FCA Register firms/individuals; needs FCA_REGISTER_KEY plus FCA_REGISTER_EMAIL."""
    email = os.environ.get("FCA_REGISTER_EMAIL")
    if not key or not email:
        raise RuntimeError("FCA_REGISTER_KEY and FCA_REGISTER_EMAIL must both be set")
    types = ["individual"] if subject.kind == "person" else ["firm"] if subject.kind == "org" else ["individual", "firm"]
    out: List[Evidence] = []
    for typ in types:
        url = "https://register.fca.org.uk/services/V0.1/Search?" + urlencode({"q": subject.name, "type": typ}, quote_via=quote)
        data = _json(url, "UK_FCA_REGISTER_API", headers={"X-Auth-Email": email, "X-Auth-Key": key,
                                                         "Content-Type": "application/json"})
        if str(data.get("Success", "true")).lower() == "false" or "Data" not in data:
            raise RuntimeError(f"UK_FCA_REGISTER_API: {str(data)[:150]}")
        for it in data.get("Data") or []:
            ev = make_evidence(
                subject, "UK_FCA_REGISTER_API", "enforcement", it.get("Name", ""),
                text=f"{typ} register status {it.get('Status', '')}", url=it.get("URL", ""), language="en",
                extra={"register_type": typ, "status": it.get("Status", ""),
                       "reference_number": it.get("Reference Number", "")},
            )
            if ev:
                out.append(ev)
    return _dedupe_cap(out)


# -------------------------------------------------------------- GovInfo

def search_govinfo(subject: Subject, key: Optional[str]) -> List[Evidence]:
    """US federal court opinions naming the subject; keeps only party-level (title) hits."""
    if not key:
        raise RuntimeError("GOVINFO_API_KEY is not set")
    body = json.dumps({"query": f'collection:USCOURTS AND "{subject.name.replace(chr(34), "")}"',
                       "pageSize": 50, "offsetMark": "*",
                       "sorts": [{"field": "publishdate", "sortOrder": "DESC"}]}).encode()
    data = _json("https://api.govinfo.gov/search?" + urlencode({"api_key": key}), "US_GOVINFO_USCOURTS",
                 data=body, headers={"Content-Type": "application/json"})
    if not isinstance(data, dict) or "results" not in data:
        raise RuntimeError(f"US_GOVINFO_USCOURTS: unexpected response {str(data)[:150]}")
    out: List[Evidence] = []
    for it in data["results"]:
        court = ", ".join(it.get("governmentAuthor") or [])
        ev = make_evidence(
            subject, "US_GOVINFO_USCOURTS", "court", it.get("title", ""), text=court,
            url=it.get("resultLink") or (it.get("download") or {}).get("pdfLink", ""),
            published=it.get("dateIssued", ""), language="en",
            extra={"court": court, "package_id": it.get("packageId", ""), "granule_id": it.get("granuleId", "")},
        )
        if ev:
            out.append(ev)
    return _dedupe_cap(out)


# ---------------------------------------------------------------- Aleph

def search_aleph(subject: Subject, key: Optional[str]) -> List[Evidence]:
    """OCCRP Aleph entities (FollowTheMoney) matching the name; minimal facts only (terms restrict redistribution)."""
    if not key:
        raise RuntimeError("ALEPH_API_KEY is not set")
    params = {"q": f'"{subject.name}"', "limit": 25}
    if subject.kind == "person":
        params["filter:schema"] = "Person"
    data = _json("https://aleph.occrp.org/api/2/entities?" + urlencode(params, quote_via=quote), "OCCRP_ALEPH_API",
                 headers={"Authorization": f"ApiKey {key}"})
    if not isinstance(data, dict) or "results" not in data:
        raise RuntimeError(f"OCCRP_ALEPH_API: {str(data)[:150]}")
    out: List[Evidence] = []
    for r in data["results"]:
        props = r.get("properties") or {}
        names = [str(n) for n in (props.get("name") or []) + (props.get("alias") or [])]
        coll = (r.get("collection") or {}).get("label", "")
        ev = make_evidence(
            subject, "OCCRP_ALEPH_API", "leak", names[0] if names else r.get("caption", ""),
            text=f"{r.get('schema', '')} in {coll}", field_texts=names + [r.get("caption", "")],
            url=f"https://aleph.occrp.org/entities/{r.get('id', '')}", language="en",
            extra={"schema": r.get("schema", ""), "dataset": coll, "countries": props.get("country") or []},
        )
        if ev:
            out.append(ev)
    return _dedupe_cap(out)


# ------------------------------------------------------------------ CSL

def search_csl(subject: Subject, key: Optional[str]) -> List[Evidence]:
    """US Consolidated Screening List fuzzy name search (trade.gov)."""
    if not key:
        raise RuntimeError("TRADE_GOV_API_KEY is not set")
    params = {"name": subject.name, "fuzzy_name": "true", "size": 25}
    data = _json("https://data.trade.gov/consolidated_screening_list/v1/search?" + urlencode(params, quote_via=quote),
                 "US_CSL_SEARCH_API", headers={"subscription-key": key})
    if not isinstance(data, dict) or "results" not in data:
        raise RuntimeError(f"US_CSL_SEARCH_API: {str(data)[:150]}")
    out: List[Evidence] = []
    for r in data["results"]:
        alts = [str(a) for a in (r.get("alt_names") or [])]
        programs = r.get("programs") or []
        ev = make_evidence(
            subject, "US_CSL_SEARCH_API", "enforcement", r.get("name", ""),
            text=f"{r.get('source', '')} {' '.join(programs)} {r.get('remarks') or ''}", field_texts=alts,
            url=r.get("source_list_url") or r.get("source_information_url") or "", published=_iso(r.get("start_date") or ""),
            language="en",
            extra={"list": r.get("source", ""), "programs": programs, "type": r.get("type", ""),
                   "csl_id": r.get("id", ""), "score": r.get("score")},
        )
        if ev:
            out.append(ev)
    return _dedupe_cap(out)


def _p(key: str, name: str, kind: str, juris: str, fn, url: str, env: str, groups: List[str],
       license: str, notes: str, interval: float = 1.0) -> LiveProvider:
    return LiveProvider(key=key, name=name, kind=kind, jurisdiction=juris, search=fn, url=url, license=license,
                        api_key_env=env, groups=groups, min_interval=interval, notes=notes)


PROVIDERS: List[LiveProvider] = [
    _p("UK_COMPANIES_HOUSE_API", "UK Companies House company/officer search", "corporate", "GB", search_ch,
       _CH_BASE + "/search", "COMPANIES_HOUSE_API_KEY", ["corporate", "keyed", "registry"], "OGL v3",
       "Env COMPANIES_HOUSE_API_KEY (HTTP Basic, key as username). 600 requests per 5 minutes.", 1.0),
    _p("UK_CH_DISQUALIFIED", "UK Companies House disqualified directors", "enforcement", "GB", search_ch_disqualified,
       _CH_BASE + "/search/disqualified-officers", "COMPANIES_HOUSE_API_KEY", ["enforcement", "keyed"], "OGL v3",
       "Env COMPANIES_HOUSE_API_KEY (same key as UK_COMPANIES_HOUSE_API).", 1.0),
    _p("OPENCORPORATES_API", "OpenCorporates company/officer search", "corporate", "GLOBAL", search_opencorporates,
       "https://api.opencorporates.com/v0.4", "OPENCORPORATES_API_TOKEN", ["corporate", "keyed", "registry"],
       "ODbL share-alike with attribution",
       "Env OPENCORPORATES_API_TOKEN (query parameter api_token). Token required; free for open-data projects.", 1.0),
    _p("BR_PORTAL_TRANSPARENCIA_API", "Portal da Transparencia CEIS/CNEP sanctions", "enforcement", "BR",
       search_pt_sanctions, _PT_BASE + "/ceis", "PORTAL_TRANSPARENCIA_API_KEY", ["enforcement", "keyed", "br"],
       "public", "Env PORTAL_TRANSPARENCIA_API_KEY (header chave-api-dados). Uses CPF/CNPJ from subject.identifiers "
       "when present, else nomeSancionado. Rate limit about 90 requests/min.", 1.0),
    _p("BR_PORTAL_TRANSPARENCIA_PEP_API", "Portal da Transparencia PEP lookup", "pep_info", "BR", search_pt_pep,
       _PT_BASE + "/peps", "PORTAL_TRANSPARENCIA_API_KEY", ["pep", "keyed", "br"], "public",
       "Env PORTAL_TRANSPARENCIA_API_KEY (same key as BR_PORTAL_TRANSPARENCIA_API). Filters nome or cpf.", 1.0),
    _p("UK_FCA_REGISTER_API", "FCA Financial Services Register", "enforcement", "GB", search_fca,
       "https://register.fca.org.uk/services/V0.1/Search", "FCA_REGISTER_KEY", ["enforcement", "keyed"],
       "terms", "Env FCA_REGISTER_KEY (header X-Auth-Key) and FCA_REGISTER_EMAIL (header X-Auth-Email, read "
       "inside search, so provider availability only checks the key). Free registration.", 1.0),
    _p("US_GOVINFO_USCOURTS", "GovInfo US federal court opinions", "court", "US", search_govinfo,
       "https://api.govinfo.gov/search", "GOVINFO_API_KEY", ["court", "keyed"], "public",
       "Env GOVINFO_API_KEY (api.data.gov key; DEMO_KEY is heavily rate limited). Full-text search; only "
       "results whose title (parties) contains the name are kept.", 2.0),
    _p("OCCRP_ALEPH_API", "OCCRP Aleph entity search", "leak", "GLOBAL", search_aleph,
       "https://aleph.occrp.org/api/2/entities", "ALEPH_API_KEY", ["leak", "keyed"], "terms",
       "Env ALEPH_API_KEY (Authorization: ApiKey). Account required; terms restrict redistribution so only "
       "minimal facts (name, dataset, link) are kept.", 1.0),
    _p("US_CSL_SEARCH_API", "US Consolidated Screening List search", "enforcement", "US", search_csl,
       "https://data.trade.gov/consolidated_screening_list/v1/search", "TRADE_GOV_API_KEY",
       ["enforcement", "sanctions", "keyed"], "public",
       "Env TRADE_GOV_API_KEY (header subscription-key). Fuzzy name search; keyless alternative is the "
       "US_CSL_DOWNLOAD list.", 1.0),
]
