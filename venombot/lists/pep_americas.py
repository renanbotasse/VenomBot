"""PEP rosters for the Americas: US, Canada, Brazil, Colombia.

Official legislator/judge/PEP registers with mandate dates. The Brazilian CGU
and Colombian DAFP registers are the legal PEP lists obliged entities must
consult, so they carry the most weight; the US/Canadian files are rosters.
"""

from __future__ import annotations

import hashlib
import io
import json
import re
import xml.etree.ElementTree as ET
import zipfile
from datetime import date
from pathlib import Path
from typing import Any, Dict, Iterator, List

from venombot.entities import Entity
from venombot.lists import ListSource
from venombot.lists._util import add_dates, add_identifier, clean
from venombot.lists.pep_europe import (
    csv_rows, iso, iso2, load_json, mk_person, position, status_for, _tx,
)

_TODAY = date.today().isoformat()


# --------------------------------------------------------------------------
# United States
# --------------------------------------------------------------------------
def parse_us_legislators(paths: Dict[str, Path], source: ListSource) -> Iterator[Entity]:
    """congress-legislators JSON: sitting senators and representatives."""
    for rec in load_json(paths["main"]):
        ids, nm, bio = rec.get("id") or {}, rec.get("name") or {}, rec.get("bio") or {}
        bio_id = ids.get("bioguide")
        full = clean(nm.get("official_full")) or f"{nm.get('first', '')} {nm.get('last', '')}"
        if not bio_id or not clean(full):
            continue
        ent = mk_person(source, bio_id, full, url=f"https://bioguide.congress.gov/search/bio/{bio_id}")
        ent.add_name(f"{nm.get('first', '')} {nm.get('middle', '')} {nm.get('last', '')} {nm.get('suffix', '')}", "alias")
        ent.add_name(f"{nm.get('first', '')} {nm.get('last', '')}", "alias")
        if nm.get("nickname"):
            ent.add_name(f"{nm['nickname']} {nm.get('last', '')}", "weak")
        add_dates(ent, bio.get("birthday"))
        ent.gender = {"M": "male", "F": "female"}.get(bio.get("gender", ""), "")
        ent.nationalities.append("us")
        ent.countries.append("us")
        add_identifier(ent, "Bioguide", bio_id, "us")
        add_identifier(ent, "Wikidata", ids.get("wikidata"))
        terms = rec.get("terms") or []
        for t in terms[-3:]:
            chamber = "U.S. Senator" if t.get("type") == "sen" else "U.S. Representative"
            where = t.get("state", "") + (f"-{t['district']}" if t.get("type") == "rep" and t.get("district") is not None else "")
            ent.positions.append(position(f"{chamber} ({where})", "us", t.get("start"), t.get("end")))
        if terms:
            ent.extra["party"] = terms[-1].get("party", "")
        yield ent


def parse_us_executive(paths: Dict[str, Path], source: ListSource) -> Iterator[Entity]:
    """Presidents and vice presidents; only people born 1925+ (living candidates)."""
    for rec in load_json(paths["main"]):
        bio, nm = rec.get("bio") or {}, rec.get("name") or {}
        born = clean(bio.get("birthday"))
        if not born or int(born[:4]) < 1925:
            continue
        ids = rec.get("id") or {}
        sid = str(ids.get("bioguide") or ids.get("govtrack") or f"{nm.get('first')}-{nm.get('last')}")
        ent = mk_person(source, sid, f"{nm.get('first', '')} {nm.get('middle', '')} {nm.get('last', '')}")
        ent.add_name(f"{nm.get('first', '')} {nm.get('last', '')}", "alias")
        add_dates(ent, born)
        ent.gender = {"M": "male", "F": "female"}.get(bio.get("gender", ""), "")
        ent.nationalities.append("us")
        ent.countries.append("us")
        for t in rec.get("terms") or []:
            title = {"prez": "President of the United States", "viceprez": "Vice President of the United States"}.get(t.get("type"), t.get("type", ""))
            ent.positions.append(position(title, "us", t.get("start"), t.get("end")))
        if ent.positions:
            yield ent


def parse_house_clerk(paths: Dict[str, Path], source: ListSource) -> Iterator[Entity]:
    """House Clerk MemberData.xml: authoritative sitting members (no DOB)."""
    for _, el in ET.iterparse(str(paths["main"]), events=("end",)):
        if el.tag != "member":
            continue
        info = el.find("member-info")
        bid, last = _tx(info, "bioguideID"), _tx(info, "lastname")
        if info is not None and bid and last:
            ent = mk_person(source, bid, _tx(info, "official-name") or f"{_tx(info, 'firstname')} {last}",
                            url=f"https://bioguide.congress.gov/search/bio/{bid}")
            ent.add_name(f"{_tx(info, 'firstname')} {last}", "alias")
            ent.countries.append("us")
            ent.nationalities.append("us")
            state = info.find("state")
            code = state.get("postal-code", "") if state is not None else ""
            sworn = info.find("sworn-date")
            start = sworn.get("date", "") if sworn is not None else ""
            ent.positions.append(position(f"U.S. Representative ({el.findtext('statedistrict') or code})", "us",
                                          f"{start[:4]}-{start[4:6]}-{start[6:8]}" if len(start) == 8 else "", "", "current"))
            ent.extra["party"] = _tx(info, "party")
            add_identifier(ent, "Bioguide", bid, "us")
            yield ent
        el.clear()


def parse_fjc_judges(paths: Dict[str, Path], source: ListSource) -> Iterator[Entity]:
    """Federal judges (FJC): living judges; appointment columns repeat as "(1)".."(6)"."""
    for row in csv_rows(paths["main"]):
        if clean(row.get("Death Year")):
            continue
        last, first = clean(row.get("Last Name")), clean(row.get("First Name"))
        if not last:
            continue
        middle, suffix = clean(row.get("Middle Name")), clean(row.get("Suffix"))
        ent = mk_person(source, clean(row.get("jid")) or clean(row.get("nid")), f"{first} {middle} {last} {suffix}",
                        url="https://www.fjc.gov/history/judges")
        ent.add_name(f"{first} {last}", "alias")
        y, m, d = (clean(row.get(k)) for k in ("Birth Year", "Birth Month", "Birth Day"))
        if y:
            ent.birth_dates.append(f"{y}-{m.zfill(2)}-{d.zfill(2)}" if m and d else (f"{y}-{m.zfill(2)}" if m else y))
        city = ", ".join(x for x in (clean(row.get("Birth City")), clean(row.get("Birth State"))) if x)
        if city:
            ent.birth_places.append(city)
        ent.gender = clean(row.get("Gender")).lower()
        ent.nationalities.append("us")
        ent.countries.append("us")
        for i in range(1, 7):
            court = clean(row.get(f"Court Name ({i})"))
            if not court:
                continue
            title = clean(row.get(f"Appointment Title ({i})")) or "Judge"
            ent.positions.append(position(f"{title}, {court}", "us", row.get(f"Commission Date ({i})"),
                                          row.get(f"Termination Date ({i})")))
        if ent.positions:
            yield ent


def parse_openstates(paths: Dict[str, Path], source: ListSource) -> Iterator[Entity]:
    """OpenStates current legislators, one CSV per state (logical name = state code)."""
    for st, path in sorted(paths.items()):
        for row in csv_rows(path):
            name, pid = clean(row.get("name")), clean(row.get("id"))
            if not name or not pid or clean(row.get("death_date")):
                continue
            ent = mk_person(source, pid, name)
            ent.add_name(f"{row.get('given_name', '')} {row.get('family_name', '')}", "alias")
            add_dates(ent, row.get("birth_date"))
            ent.gender = clean(row.get("gender")).lower()
            ent.nationalities.append("us")
            ent.countries.append("us")
            add_identifier(ent, "Wikidata", row.get("wikidata"))
            chamber = {"upper": "State Senator", "lower": "State Representative"}.get(clean(row.get("current_chamber")), "State Legislator")
            dist = clean(row.get("current_district"))
            ent.positions.append(position(f"{chamber} ({st.upper()}{', district ' + dist if dist else ''})", "us", "", "", "current"))
            ent.extra.update({"party": clean(row.get("current_party")), "level": "regional"})
            yield ent


def parse_congress_gov(paths: Dict[str, Path], source: ListSource) -> Iterator[Entity]:
    """api.congress.gov /member pages (current members, fresher than the GitHub dataset)."""
    seen = set()
    for key in sorted(paths):
        for m in load_json(paths[key]).get("members", []):
            bid = m.get("bioguideId")
            if not bid or bid in seen:
                continue
            seen.add(bid)
            ent = mk_person(source, bid, clean(m.get("name")), url=f"https://www.congress.gov/member/{bid}")
            ent.countries.append("us")
            ent.nationalities.append("us")
            terms = (m.get("terms") or {}).get("item") or []
            if isinstance(terms, dict):
                terms = [terms]
            for t in terms[-2:]:
                ent.positions.append(position(f"{'U.S. Senator' if 'Senate' in clean(t.get('chamber')) else 'U.S. Representative'} ({m.get('state', '')})",
                                              "us", str(t.get("startYear") or ""), str(t.get("endYear") or "")))
            ent.extra["party"] = clean(m.get("partyName"))
            yield ent


# --------------------------------------------------------------------------
# Canada
# --------------------------------------------------------------------------
def parse_ca_commons(paths: Dict[str, Path], source: ListSource) -> Iterator[Entity]:
    """House of Commons members: nil ToDateTime means the member is sitting."""
    for _, el in ET.iterparse(str(paths["main"]), events=("end",)):
        if el.tag != "MemberOfParliament":
            continue
        pid = _tx(el, "PersonId")
        first, last = _tx(el, "PersonOfficialFirstName"), _tx(el, "PersonOfficialLastName")
        if pid and last:
            ent = mk_person(source, pid, f"{first} {last}", url=f"https://www.ourcommons.ca/members/en/{pid}")
            ent.countries.append("ca")
            ent.nationalities.append("ca")
            seat, prov = _tx(el, "ConstituencyName"), _tx(el, "ConstituencyProvinceTerritoryName")
            ent.positions.append(position(f"Member of Parliament, {seat} ({prov})", "ca",
                                          _tx(el, "FromDateTime")[:10], _tx(el, "ToDateTime")[:10]))
            ent.extra["party"] = _tx(el, "CaucusShortName")
            yield ent
        el.clear()


_FACFOA = re.compile(r"^(?P<name>.+?),\s*born on\s+(?P<dob>[A-Z][a-z]+ \d{1,2}, \d{4})(?:,\s*(?P<role>.*))?$")
_RELATION = re.compile(r"\b(son|daughter|brother|sister|wife|husband|spouse|father|mother|relative|close associate|associate)\b", re.I)


def parse_facfoa(paths: Dict[str, Path], source: ListSource) -> Iterator[Entity]:
    """Schedule items "Name, born on <date>, <role>" of the Ukraine FACFOA regulations.

    These are asset-freeze designations of corrupt foreign officials, so the
    entities are SANCTIONS targets; relatives/associates are marked in remarks.
    """
    root = ET.parse(str(paths["main"])).getroot()
    for prov in root.iter():
        if not prov.tag.endswith("Provision"):
            continue
        label = prov.find("Label")
        text_el = prov.find("Text")
        if text_el is None:
            continue
        text = clean("".join(text_el.itertext()))
        if not text or text.startswith("[Repealed") or label is None:
            continue
        m = _FACFOA.match(text)
        if not m:
            continue
        ent = mk_person(source, f"SOR-2014-44-{clean(label.text)}", m.group("name"),
                        url="https://laws-lois.justice.gc.ca/eng/regulations/SOR-2014-44/")
        add_dates(ent, m.group("dob"))
        role = clean(m.group("role"))
        ent.remarks = role
        ent.programs.append("CA-FACFOA-UA")
        ent.countries.append("ua")
        ent.nationalities.append("ua")
        if role and not _RELATION.search(role):
            ent.positions.append(position(role, "ua", "", "", "ended" if role.lower().startswith("former") else "unknown"))
        ent.extra["relation_note"] = role if _RELATION.search(role) else ""
        yield ent


# --------------------------------------------------------------------------
# Brazil
# --------------------------------------------------------------------------
_LEGISLATURE = {55: ("2015-02-01", "2019-01-31"), 56: ("2019-02-01", "2023-01-31"), 57: ("2023-02-01", "2027-01-31"),
                58: ("2027-02-01", "2031-01-31")}


def parse_br_camara(paths: Dict[str, Path], source: ListSource) -> Iterator[Entity]:
    """Deputies of the last two legislatures (56, 57); 57 is sitting."""
    for row in csv_rows(paths["main"], ";"):
        try:
            last_leg = int(row.get("idLegislaturaFinal") or 0)
        except ValueError:
            continue
        if last_leg < 56 or clean(row.get("dataFalecimento")):
            continue
        uri = clean(row.get("uri"))
        sid = uri.rsplit("/", 1)[-1]
        ent = mk_person(source, sid, row.get("nomeCivil") or row.get("nome") or "", url=uri)
        ent.add_name(row.get("nome"), "alias")
        add_dates(ent, row.get("dataNascimento"))
        ent.gender = {"M": "male", "F": "female"}.get(clean(row.get("siglaSexo")), "")
        place = ", ".join(x for x in (clean(row.get("municipioNascimento")), clean(row.get("ufNascimento"))) if x)
        if place:
            ent.birth_places.append(place)
        add_identifier(ent, "CPF", row.get("cpf"), "br")
        ent.nationalities.append("br")
        ent.countries.append("br")
        start = _LEGISLATURE.get(int(row.get("idLegislaturaInicial") or last_leg), ("", ""))[0]
        end = _LEGISLATURE.get(last_leg, ("", ""))[1]
        ent.positions.append(position("Deputado Federal", "br", start, end if last_leg < 57 else "",
                                      "current" if last_leg >= 57 else "ended"))
        ent.extra["level"] = "national"
        yield ent


def parse_br_senado(paths: Dict[str, Path], source: ListSource) -> Iterator[Entity]:
    """Senators in office (JSON content-negotiated)."""
    data = load_json(paths["main"])
    for p in data["ListaParlamentarEmExercicio"]["Parlamentares"]["Parlamentar"]:
        ident, mand = p.get("IdentificacaoParlamentar") or {}, p.get("Mandato") or {}
        code = clean(ident.get("CodigoParlamentar"))
        name = clean(ident.get("NomeCompletoParlamentar")) or clean(ident.get("NomeParlamentar"))
        if not code or not name:
            continue
        ent = mk_person(source, code, name, url=clean(ident.get("UrlPaginaParlamentar")))
        ent.add_name(ident.get("NomeParlamentar"), "alias")
        ent.gender = {"masculino": "male", "feminino": "female"}.get(clean(ident.get("SexoParlamentar")).lower(), "")
        ent.nationalities.append("br")
        ent.countries.append("br")
        first = (mand.get("PrimeiraLegislaturaDoMandato") or {}).get("DataInicio", "")
        second = (mand.get("SegundaLegislaturaDoMandato") or {}).get("DataFim", "")
        ent.positions.append(position(f"Senador(a), {clean(ident.get('UfParlamentar'))}", "br", first, second, "current"))
        ent.extra.update({"party": clean(ident.get("SiglaPartidoParlamentar")), "level": "national"})
        yield ent


_BR_LOCAL = re.compile(r"VEREAD|PREFEI|VICE-PREF|C[AÂ]MARA MUNIC|MUNICIPAL", re.I)
_BR_REGIONAL = re.compile(r"GOVERNAD|DEPUTADO ESTADUAL|DEP\.? ?EST|ESTADUAL|SECRET[AÁ]RIO DE ESTADO|ASSEMBL[EÉ]IA LEGISLATIVA", re.I)


def br_level(desc: str, organ: str, nivel: str) -> str:
    """Rough national/regional/local tier from the CGU function text (levels are free text)."""
    blob = f"{desc} {organ}"
    if _BR_LOCAL.search(blob):
        return "local"
    if _BR_REGIONAL.search(blob):
        return "regional"
    return "national"


def parse_br_cgu_pep(paths: Dict[str, Path], source: ListSource) -> Iterator[Entity]:
    """CGU PEP register (zip -> Latin-1 CSV, ~130k rows): one entity per (masked CPF, name).

    Data_Fim_Carencia is the end of the post-office PEP period, kept so a
    dossier can show someone as "ended but still PEP".
    """
    people: Dict[str, Entity] = {}
    with zipfile.ZipFile(paths["main"]) as zf:
        member = next(n for n in zf.namelist() if n.lower().endswith(".csv"))
        text = io.TextIOWrapper(zf.open(member), encoding="latin-1", newline="")
        import csv as _csv
        for row in _csv.DictReader(text, delimiter=";"):
            row = {k.strip(): v for k, v in row.items() if k}
            name, cpf = clean(row.get("Nome_PEP")), clean(row.get("CPF"))
            if not name:
                continue
            key = f"{cpf}|{name}"
            ent = people.get(key)
            if ent is None:
                name = re.sub(r"^\(CUMULATIVAMENTE\)\s*", "", name)
                ent = mk_person(source, hashlib.md5(key.encode("utf-8")).hexdigest()[:16], name,
                                url="https://portaldatransparencia.gov.br/download-de-dados/pep")
                add_identifier(ent, "CPF (masked)", cpf, "br")
                ent.nationalities.append("br")
                ent.countries.append("br")
                people[key] = ent
            func, organ = clean(row.get("Descrição_Função")), clean(row.get("Nome_Órgão"))
            level = br_level(func, organ, clean(row.get("Nível_Função")))
            end_raw = clean(row.get("Data_Fim_Exercício"))
            end = iso(end_raw) if re.match(r"\d", end_raw or "") else ""
            ent.positions.append(position(f"{func} ({level}), {organ}" if organ else f"{func} ({level})", "br",
                                          row.get("Data_Início_Exercício"), end))
            carencia = iso(clean(row.get("Data_Fim_Carência")))
            if carencia and carencia > ent.extra.get("pep_until", ""):
                ent.extra["pep_until"] = carencia
            if level == "national" or "level" not in ent.extra:
                ent.extra["level"] = level
    yield from people.values()


def parse_br_tse(paths: Dict[str, Path], source: ListSource) -> Iterator[Entity]:
    """Elected (and runoff) candidates of the 2026 general election: incoming PEPs."""
    with zipfile.ZipFile(paths["main"]) as zf:
        member = next((n for n in zf.namelist() if n.upper().endswith("_BRASIL.CSV")), None)
        if member is None:
            raise ValueError("TSE zip has no consulta_cand_*_BRASIL.csv")
        import csv as _csv
        text = io.TextIOWrapper(zf.open(member), encoding="latin-1", newline="")
        for row in _csv.DictReader(text, delimiter=";"):
            situ = clean(row.get("DS_SIT_TOT_TURNO")).upper()
            if not (situ.startswith("ELEITO") or "2º TURNO" in situ):
                continue
            sq = clean(row.get("SQ_CANDIDATO"))
            name = clean(row.get("NM_CANDIDATO"))
            if not sq or not name:
                continue
            ent = mk_person(source, sq, name, url="https://divulgacandcontas.tse.jus.br/")
            ent.add_name(row.get("NM_URNA_CANDIDATO"), "alias")
            soc = clean(row.get("NM_SOCIAL_CANDIDATO"))
            if soc and soc != "#NULO":
                ent.add_name(soc, "alias")
            add_dates(ent, row.get("DT_NASCIMENTO"))
            ent.gender = {"MASCULINO": "male", "FEMININO": "female"}.get(clean(row.get("DS_GENERO")).upper(), "")
            cpf = clean(row.get("NR_CPF_CANDIDATO"))
            if cpf.isdigit():
                add_identifier(ent, "CPF", cpf, "br")
            ent.nationalities.append("br")
            ent.countries.append("br")
            cargo, uf = clean(row.get("DS_CARGO")), clean(row.get("SG_UF"))
            tag = "runoff candidate" if "TURNO" in situ and not situ.startswith("ELEITO") else "elected 2026"
            ent.positions.append(position(f"{cargo} ({tag}), {uf}", "br", "", "", "unknown"))
            ent.extra.update({"party": clean(row.get("SG_PARTIDO")),
                              "level": "national" if cargo.upper() in ("PRESIDENTE", "VICE-PRESIDENTE", "SENADOR", "DEPUTADO FEDERAL") else "regional"})
            yield ent


# --------------------------------------------------------------------------
# Colombia
# --------------------------------------------------------------------------
def parse_co_dafp(paths: Dict[str, Path], source: ListSource) -> Iterator[Entity]:
    """DAFP PEP register (Socrata JSON): one entity per cédula, all their positions."""
    people: Dict[str, Entity] = {}
    for row in load_json(paths["main"]):
        doc, name = clean(row.get("numero_documento")), clean(row.get("nombre_pep"))
        if not doc or not name:
            continue
        ent = people.get(doc)
        if ent is None:
            link = (row.get("enlace_hoja_vida_sigep") or {}).get("url", "") if isinstance(row.get("enlace_hoja_vida_sigep"), dict) else ""
            ent = mk_person(source, doc, name, url=link)
            add_identifier(ent, "Cédula de Ciudadanía", doc, "co")
            ent.nationalities.append("co")
            ent.countries.append("co")
            people[doc] = ent
        cargo, entidad = clean(row.get("denominacion_cargo")), clean(row.get("nombre_entidad"))
        ent.positions.append(position(f"{cargo}, {entidad}" if entidad else cargo, "co",
                                      row.get("fecha_vinculacion"), row.get("fecha_desvinculacion")))
    yield from people.values()


# --------------------------------------------------------------------------
# registry
# --------------------------------------------------------------------------
_STATES = ["al", "ak", "az", "ar", "ca", "co", "ct", "de", "dc", "fl", "ga", "hi", "id", "il", "in", "ia", "ks", "ky",
           "la", "me", "md", "ma", "mi", "mn", "ms", "mo", "mt", "ne", "nv", "nh", "nj", "nm", "ny", "nc", "nd", "oh",
           "ok", "or", "pa", "pr", "ri", "sc", "sd", "tn", "tx", "ut", "vt", "va", "wa", "wv", "wi", "wy"]


def _cgu_url(today: date = None) -> str:
    """CGU keeps only the latest monthly file, published about a month late: use month-2."""
    today = today or date.today()
    m = today.year * 12 + today.month - 1 - 2
    return f"https://dadosabertos-download.cgu.gov.br/PortalDaTransparencia/saida/pep/{m // 12}{m % 12 + 1:02d}_PEP.zip"


_CONGRESS_API = "https://api.congress.gov/v3/member?api_key={api_key}&format=json&limit=250&currentMember=true&offset="

SOURCES: List[ListSource] = [
    ListSource(
        key="US_CONGRESS_LEGISLATORS_CURRENT", name="US Congress - sitting legislators (congress-legislators)",
        jurisdiction="US", list_type="PEP",
        urls={"main": "https://unitedstates.github.io/congress-legislators/legislators-current.json"},
        parser=parse_us_legislators, homepage="https://github.com/unitedstates/congress-legislators", license="CC0",
        groups=["pep", "americas", "us"], max_age_days=14,
        notes="Birthday, bioguide and wikidata ids. Overlaps OpenSanctions us_congress.",
    ),
    ListSource(
        key="US_EXECUTIVE_BRANCH", name="US presidents and vice presidents (congress-legislators executive.json)",
        jurisdiction="US", list_type="PEP",
        urls={"main": "https://unitedstates.github.io/congress-legislators/executive.json"},
        parser=parse_us_executive, homepage="https://github.com/unitedstates/congress-legislators", license="CC0",
        groups=["pep", "americas", "us"], max_age_days=30,
        notes="Only presidents/VPs born 1925 or later (living or recent). Cabinet members are not in this file.",
    ),
    ListSource(
        key="US_HOUSE_CLERK_MEMBERDATA", name="US House Clerk member data", jurisdiction="US", list_type="PEP",
        urls={"main": "https://clerk.house.gov/xml/lists/MemberData.xml"},
        parser=parse_house_clerk, homepage="https://clerk.house.gov/", license="public",
        groups=["pep", "americas", "us"], max_age_days=7,
        notes="Authoritative, frequently republished roster; no DOB. Overlaps US_CONGRESS_LEGISLATORS_CURRENT (same bioguide ids).",
    ),
    ListSource(
        key="US_FJC_JUDGES", name="US Federal Judicial Center - federal judges (living)", jurisdiction="US", list_type="PEP",
        urls={"main": "https://www.fjc.gov/sites/default/files/history/judges.csv"},
        parser=parse_fjc_judges, homepage="https://www.fjc.gov/history/judges", license="public",
        groups=["pep", "americas", "us", "judiciary"], max_age_days=30, timeout=300,
        notes="~5.5 MB CSV; one entity per living judge with their appointments (court, commission and termination dates).",
    ),
    ListSource(
        key="US_OPENSTATES_LEGISLATORS", name="US state legislators (OpenStates people)", jurisdiction="US", list_type="PEP",
        urls={st: f"https://data.openstates.org/people/current/{st}.csv" for st in _STATES},
        parser=parse_openstates, homepage="https://github.com/openstates/people", license="CC0",
        groups=["pep", "americas", "us"], max_age_days=14,
        notes="50 states + DC + PR. Sub-national PEPs (FATF R.12 domestic PEPs). Overlaps OpenSanctions us_plural_legislators.",
    ),
    ListSource(
        key="US_CONGRESS_GOV_API", name="Congress.gov API - current members", jurisdiction="US", list_type="PEP",
        urls={f"p{o:03d}": f"{_CONGRESS_API}{o}" for o in (0, 250, 500)},
        parser=parse_congress_gov, homepage="https://api.congress.gov/", license="public",
        groups=["pep", "americas", "us"], api_key_env="CONGRESS_API_KEY", max_age_days=7,
        notes="Needs a free api.data.gov key in CONGRESS_API_KEY (DEMO_KEY works but is throttled). Fresher than the GitHub roster after special elections.",
    ),
    ListSource(
        key="CA_COMMONS_MEMBERS_XML", name="Canada House of Commons - current Members of Parliament", jurisdiction="CA",
        list_type="PEP", urls={"main": "https://www.ourcommons.ca/Members/en/search/xml"},
        parser=parse_ca_commons, homepage="https://www.ourcommons.ca/members/en", license="terms",
        groups=["pep", "americas", "ca"], max_age_days=14,
        notes="No DOB. House of Commons reproduction terms: attribution, check commercial use.",
    ),
    ListSource(
        key="CA_FACFOA_UKRAINE", name="Canada FACFOA (Ukraine) Regulations - corrupt foreign officials", jurisdiction="CA",
        list_type="SANCTIONS", urls={"main": "https://laws-lois.justice.gc.ca/eng/XML/SOR-2014-44.xml"},
        parser=parse_facfoa, homepage="https://laws-lois.justice.gc.ca/eng/regulations/SOR-2014-44/", license="OGL",
        groups=["pep", "americas", "ca", "sanctions"], max_age_days=90,
        notes="Typed SANCTIONS (not PEP) because the schedule is an asset-freeze designation list; relatives/associates are flagged in "
              "remarks. Rarely amended (last 2021). Same URL pattern works for SOR-2017-233 (Magnitsky-style JVCFOA).",
    ),
    ListSource(
        key="BR_CAMARA_DEPUTADOS", name="Brazil Chamber of Deputies - deputies (legislatures 56-57)", jurisdiction="BR",
        list_type="PEP", urls={"main": "https://dadosabertos.camara.leg.br/arquivos/deputados/csv/deputados.csv"},
        parser=parse_br_camara, homepage="https://dadosabertos.camara.leg.br/", license="public",
        groups=["pep", "americas", "br"], max_age_days=30,
        notes="Legislature 57 (2023-2027) = current, 56 = former. DOB and birthplace; CPF mostly blank.",
    ),
    ListSource(
        key="BR_SENADO_SENADORES", name="Brazil Federal Senate - senators in office", jurisdiction="BR", list_type="PEP",
        urls={"main": "https://legis.senado.leg.br/dadosabertos/senador/lista/atual"},
        parser=parse_br_senado, homepage="https://www25.senado.leg.br/web/senadores", license="public",
        groups=["pep", "americas", "br"], headers={"Accept": "application/json"}, max_age_days=14,
        notes="Must send Accept: application/json and use the path without a .json suffix (that returns 503). No DOB in the list endpoint.",
    ),
    ListSource(
        key="BR_CGU_PEP", name="Brazil CGU Portal da Transparencia - PEP register", jurisdiction="BR", list_type="PEP",
        urls={"main": _cgu_url()},
        parser=parse_br_cgu_pep, homepage="https://portaldatransparencia.gov.br/download-de-dados/pep", license="public",
        groups=["pep", "americas", "br"], max_age_days=35, timeout=300,
        notes="Official COAF/CGU PEP register (~130k rows, Latin-1). CGU keeps only the latest month (published ~1 month late): the URL "
              "is computed as today-2 months and returns 403 when that month is gone, so an integrator resolver should probe backwards. "
              "CPF is masked. Derived OpenSanctions dataset: br_pep.",
    ),
    ListSource(
        key="BR_TSE_CANDIDATOS_2026", name="Brazil TSE 2026 elected candidates (incoming PEPs)", jurisdiction="BR",
        list_type="PEP", urls={"main": "https://cdn.tse.jus.br/estatistica/sead/odsele/consulta_cand/consulta_cand_2026.zip"},
        parser=parse_br_tse, homepage="https://dadosabertos.tse.jus.br/", license="CC-BY",
        groups=["pep", "americas", "br"], max_age_days=3, timeout=300,
        notes="Zip regenerated daily; only the *_BRASIL.csv member is read and only ELEITO/2º TURNO rows are kept (not all candidates). "
              "Status is 'unknown' until they take office in 2027.",
    ),
    ListSource(
        key="CO_DAFP_PEP", name="Colombia DAFP - Personas Expuestas Politicamente", jurisdiction="CO", list_type="PEP",
        urls={"main": "https://www.datos.gov.co/resource/3qxn-uc22.json?%24limit=50000"},
        parser=parse_co_dafp, homepage="https://www.datos.gov.co/resource/3qxn-uc22", license="CC-BY",
        groups=["pep", "americas", "co"], max_age_days=30, timeout=300,
        notes="Official register under Decreto 830/2021 (~37.6k rows); cédula is unmasked. CC BY-SA 4.0 (attribute DAFP).",
    ),
]
