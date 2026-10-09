"""PEP rosters from European parliaments and governments (UK, EU, DE, FR, ES, PT).

Why official rosters: a parliament's own member list is the highest-quality PEP
evidence (exact names, mandate dates, party) and costs one small download.
Positions carry current/ended status so a dossier can tell a sitting minister
from a former MP.

This module also hosts the small helpers shared by the other ``pep_*`` modules
(``mk_person``, ``iso``, ``position``) so those modules need no common file
outside this family.
"""

from __future__ import annotations

import csv
import io
import json
import re
import xml.etree.ElementTree as ET
import zipfile
from datetime import date
from pathlib import Path
from typing import Any, Dict, Iterable, Iterator, List, Optional

from venombot.countries import to_iso2
from venombot.entities import Entity, Position
from venombot.lists import ListSource
from venombot.lists._util import add_dates, clean, read_text

# EP/Akamai-style WAFs answer 202 with an empty body to non-browser agents.
BROWSER_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)

_PLACEHOLDER = re.compile(r"données non publiées|non publi", re.I)


# --------------------------------------------------------------------------
# shared helpers (also imported by pep_americas / pep_other / pep_wikidata)
# --------------------------------------------------------------------------
def iso(value: Optional[str]) -> str:
    """Normalise a roster date (ISO, dd/mm/yyyy, dd.mm.yyyy, mm/yyyy) to partial ISO.

    Rosters mix formats; keeping precision lets dossiers show "2024-07" when
    the day is unknown. Empty/garbage input gives "".
    """
    text = clean(value)
    if not text:
        return ""
    m = re.match(r"^(\d{4})-(\d{2})-(\d{2})", text)
    if m:
        return f"{m.group(1)}-{m.group(2)}-{m.group(3)}"
    m = re.match(r"^(\d{1,2})[./](\d{1,2})[./](\d{4})", text)
    if m:
        return f"{m.group(3)}-{int(m.group(2)):02d}-{int(m.group(1)):02d}"
    m = re.match(r"^(\d{1,2})[./](\d{4})$", text)
    if m:
        return f"{m.group(2)}-{int(m.group(1)):02d}"
    m = re.match(r"^(\d{4})-(\d{2})$", text)
    if m:
        return text
    m = re.match(r"^(\d{4})$", text)
    return text if m else ""


def status_for(start: str, end: str, today: Optional[str] = None) -> str:
    """"current" when there is no end date (or it lies in the future), else "ended"."""
    today = today or date.today().isoformat()
    if not end:
        return "current"
    return "current" if end > today else "ended"


def position(title: str, country: str = "", start: Any = "", end: Any = "",
             status: str = "") -> Position:
    """Build a Position, normalising dates and deriving status from the end date."""
    s, e = iso(start), iso(end)
    return Position(clean(title), country, s, e, status or status_for(s, e))


def mk_person(source: ListSource, sid: str, name: str, *, schema: str = "Person",
              list_type: str = "", url: str = "") -> Entity:
    """Create an Entity for a roster row with its primary name set."""
    ent = Entity(source=source.key, source_id=str(sid), schema=schema,
                 list_type=list_type or source.list_type, url=url)
    ent.add_name(name, "primary")
    return ent


def iso2(value: Optional[str]) -> str:
    """ISO2 code (lower-case) for a country name/code, "" when unknown."""
    return to_iso2(value) or ""


def csv_rows(path: Path, delimiter: str = ",", encoding: Optional[str] = None) -> Iterator[Dict[str, str]]:
    """Stream CSV rows as dicts without loading the file (rosters can be 100 MB+)."""
    csv.field_size_limit(1 << 30)
    enc = encoding or "utf-8-sig"
    with open(path, "r", encoding=enc, errors="replace", newline="") as fh:
        for row in csv.DictReader(fh, delimiter=delimiter):
            yield {(k or "").strip(): (v if isinstance(v, str) else "") for k, v in row.items()}


def load_json(path: Path) -> Any:
    """Read a JSON roster tolerating a UTF-8 BOM."""
    return json.loads(read_text(path))


def _tx(el: Optional[ET.Element], tag: str) -> str:
    if el is None:
        return ""
    child = el.find(tag)
    return clean(child.text) if child is not None and child.text else ""


# --------------------------------------------------------------------------
# UK Parliament
# --------------------------------------------------------------------------
def _uk_member_entity(source: ListSource, val: Dict[str, Any], title: str = "") -> Optional[Entity]:
    mid = val.get("id")
    name = clean(val.get("nameDisplayAs"))
    if not mid or not name:
        return None
    ent = mk_person(source, f"member-{mid}", name, url=f"https://members.parliament.uk/member/{mid}/contact")
    ent.add_name(clean(val.get("nameListAs")), "alias")
    ent.countries.append("gb")
    gender = clean(val.get("gender")).lower()
    ent.gender = {"m": "male", "f": "female"}.get(gender, gender)
    party = (val.get("latestParty") or {}).get("name")
    if party:
        ent.extra["party"] = party
    hm = val.get("latestHouseMembership") or {}
    house = hm.get("house")
    seat = clean(hm.get("membershipFrom"))
    if house == 1:
        t = f"Member of Parliament (House of Commons){', ' + seat if seat else ''}"
    else:
        t = "Member of the House of Lords"
    start = hm.get("membershipStartDate") or ""
    end = hm.get("membershipEndDate") or ""
    st = (hm.get("membershipStatus") or {})
    status = "current" if st.get("statusIsActive") else ("ended" if end else "")
    ent.positions.append(position(t, "gb", start, end, status or ""))
    if title:
        ent.positions.append(position(title, "gb", "", "", "current"))
    ent.remarks = clean(val.get("nameFullTitle"))
    return ent


def parse_uk_members(paths: Dict[str, Path], source: ListSource) -> Iterator[Entity]:
    """UK Members API pages (take is capped at 20, hence one file per page)."""
    seen = set()
    for key in sorted(paths):
        data = load_json(paths[key])
        for item in data.get("items", []):
            ent = _uk_member_entity(source, item.get("value") or {})
            if ent and ent.source_id not in seen:
                seen.add(ent.source_id)
                yield ent


def parse_uk_government_posts(paths: Dict[str, Path], source: ListSource) -> Iterator[Entity]:
    """Ministers: one entity per holder, with every government post they hold."""
    people: Dict[str, Entity] = {}
    for post in load_json(paths["main"]):
        pv = post.get("value") or {}
        pname = clean(pv.get("name"))
        depts = ", ".join(clean(d.get("name")) for d in pv.get("governmentDepartments") or [])
        title = f"{pname} ({depts})" if depts and depts not in pname else pname
        for holder in pv.get("postHolders") or []:
            val = ((holder.get("member") or {}).get("value")) or {}
            ent = people.get(str(val.get("id")))
            if ent is None:
                ent = _uk_member_entity(source, val)
                if ent is None:
                    continue
                ent.source_id = f"minister-{val.get('id')}"
                ent.positions = []
                people[str(val.get("id"))] = ent
            ent.positions.append(position(title, "gb", holder.get("startDate"), holder.get("endDate")))
    yield from people.values()


def parse_uk_interests_family(paths: Dict[str, Path], source: ListSource) -> Iterator[Entity]:
    """Family members employed/lobbying per the MPs' register: RCA, linked to the MP."""
    for key in sorted(paths):
        for item in load_json(paths[key]).get("items", []):
            fields = {f.get("name"): f.get("value") for f in item.get("fields") or []}
            who = clean(fields.get("PersonName"))
            member = item.get("member") or {}
            mp = clean(member.get("nameDisplayAs"))
            if not who or not mp:
                continue
            ent = mk_person(source, f"interest-{item.get('id')}", who, list_type="PEP_RCA",
                            url=f"https://interests.parliament.uk/")
            ent.linked_to.append(mp)
            rel = clean(fields.get("FamilyRelationType"))
            cat = clean((item.get("category") or {}).get("name"))
            ent.remarks = f"{rel or 'Family member'} of {mp} MP ({cat}); {clean(item.get('summary'))}".strip()
            ent.countries.append("gb")
            ent.extra.update({"pep": mp, "relationship": rel, "job": clean(fields.get("JobTitle"))})
            ent.listed_on = clean(item.get("registrationDate"))
            yield ent


# --------------------------------------------------------------------------
# European Union
# --------------------------------------------------------------------------
def _mep_names(ent: Entity, full: str) -> None:
    ent.add_name(full, "primary")


def parse_ep_meps_xml(paths: Dict[str, Path], source: ListSource) -> Iterator[Entity]:
    """Current MEPs plus outgoing ones (kept as former PEPs with mandate dates)."""
    seen = set()
    for key, current in (("current", True), ("outgoing", False)):
        if key not in paths:
            continue
        for _, el in ET.iterparse(str(paths[key]), events=("end",)):
            if el.tag != "mep":
                continue
            mid = _tx(el, "id")
            full = _tx(el, "fullName")
            if mid and full and mid not in seen:
                seen.add(mid)
                ent = mk_person(source, mid, full, url=f"https://www.europarl.europa.eu/meps/en/{mid}")
                c = el.find("country")
                code = (c.get("countryCode") if c is not None else "") or ""
                country = iso2(code or (c.text if c is not None else ""))
                if country:
                    ent.countries.append(country)
                group = _tx(el, "politicalGroup")
                natl = _tx(el, "nationalPoliticalGroup")
                ent.extra.update({"eu_group": group, "national_party": natl})
                start, end = iso(_tx(el, "mandate-start")), iso(_tx(el, "mandate-end"))
                ent.positions.append(position("Member of the European Parliament", "eu", start, end,
                                              "current" if current else "ended"))
                yield ent
            el.clear()


def parse_ep_opendata(paths: Dict[str, Path], source: ListSource) -> Iterator[Entity]:
    """EP open-data /meps/show-current list (JSON-LD): name, country, political group."""
    data = load_json(paths["main"]).get("data", [])
    for it in data:
        ident = clean(it.get("identifier"))
        label = clean(it.get("label"))
        if not ident or not label:
            continue
        ent = mk_person(source, ident, label, url=f"https://www.europarl.europa.eu/meps/en/{ident}")
        given, family = clean(it.get("givenName")), clean(it.get("familyName"))
        if given and family:
            ent.add_name(f"{given} {family}", "alias")
        c = iso2(it.get("api:country-of-representation"))
        if c:
            ent.countries.append(c)
        ent.extra["eu_group"] = clean(it.get("api:political-group"))
        ent.positions.append(position("Member of the European Parliament", "eu", "", "", "current"))
        yield ent


_WW_SKIP = re.compile(r"\bhead of unit\b|\bassistant\b|\badviser\b|\badvisor\b|\bsecretary to\b", re.I)


def parse_eu_whoiswho(paths: Dict[str, Path], source: ListSource) -> Iterator[Entity]:
    """Senior EU officials (Whoiswho SPARQL CSV): one entity per person, all posts."""
    people: Dict[str, Entity] = {}
    for row in csv_rows(paths["main"]):
        uri = row.get("person", "")
        title = clean(row.get("position"))
        if not uri or not title or _WW_SKIP.search(title):
            continue
        sid = uri.rsplit("/", 1)[-1]
        ent = people.get(sid)
        if ent is None:
            given, fam = clean(row.get("givenN")), clean(row.get("familyN"))
            ent = mk_person(source, sid, f"{given} {fam}".strip(), url=uri)
            people[sid] = ent
        org = clean(row.get("orgL"))
        ent.positions.append(position(f"{title}, {org}" if org else title, "eu", "", "", "current"))
    yield from people.values()


# --------------------------------------------------------------------------
# Germany: Bundestag MdB-Stammdaten
# --------------------------------------------------------------------------
def parse_bundestag(paths: Dict[str, Path], source: ListSource) -> Iterator[Entity]:
    """All living members since 1949; positions are their Wahlperioden (terms)."""
    with zipfile.ZipFile(paths["main"]) as zf:
        member = next(n for n in zf.namelist() if n.lower().endswith(".xml"))
        stream = zf.open(member)
        for _, el in ET.iterparse(stream, events=("end",)):
            if el.tag != "MDB":
                continue
            bio = el.find("BIOGRAFISCHE_ANGABEN")
            if bio is not None and _tx(bio, "STERBEDATUM"):
                el.clear()
                continue
            mid = _tx(el, "ID")
            names = []
            for n in el.findall("NAMEN/NAME"):
                fam = _tx(n, "NACHNAME")
                giv = _tx(n, "VORNAME")
                if not fam:
                    continue
                full = " ".join(x for x in (_tx(n, "AKAD_TITEL"), giv, _tx(n, "ADEL"), _tx(n, "PRAEFIX"), fam) if x)
                plain = " ".join(x for x in (giv, _tx(n, "ADEL"), _tx(n, "PRAEFIX"), fam) if x)
                names.extend([plain, full])
            if not mid or not names:
                el.clear()
                continue
            ent = mk_person(source, mid, names[0], url=f"https://www.bundestag.de/abgeordnete/biografien")
            for n in names[1:]:
                ent.add_name(n, "alias")
            ent.countries.append("de")
            ent.nationalities.append("de")
            if bio is not None:
                add_dates(ent, _tx(bio, "GEBURTSDATUM"))
                if _tx(bio, "GEBURTSORT"):
                    ent.birth_places.append(_tx(bio, "GEBURTSORT"))
                ent.extra.update({"party": _tx(bio, "PARTEI_KURZ"), "occupation": _tx(bio, "BERUF")})
                g = _tx(bio, "GESCHLECHT").lower()
                ent.gender = {"männlich": "male", "weiblich": "female"}.get(g, g)
            terms = []
            for wp in el.findall("WAHLPERIODEN/WAHLPERIODE"):
                terms.append((iso(_tx(wp, "MDBWP_VON")), iso(_tx(wp, "MDBWP_BIS")), _tx(wp, "WP")))
            # Old terms add noise; keep the latest four and flag open ones as current.
            for start, end, wp in sorted(terms)[-4:]:
                ent.positions.append(position(f"Member of the Bundestag (Wahlperiode {wp})", "de", start, end))
            yield ent
            el.clear()


# --------------------------------------------------------------------------
# France: HATVP and RNE
# --------------------------------------------------------------------------
_HATVP_TYPE_EN = {
    "depute": "Member of the National Assembly", "senateur": "Senator",
    "gouvernement": "Government member", "europe": "Member of the European Parliament",
    "commune": "Municipal official", "region": "Regional official",
    "departement": "Departmental official", "epci": "Inter-municipal official",
    "collaborateurpresident": "Presidential staff", "collaborateurministre": "Ministerial staff",
    "autorite": "Independent authority member",
}


def parse_hatvp_liste(paths: Dict[str, Path], source: ListSource) -> Iterator[Entity]:
    """HATVP list of officials with filed declarations (one row per declaration)."""
    seen = set()
    for row in csv_rows(paths["main"], ";"):
        key = clean(row.get("classement")) or f"{row.get('nom')}-{row.get('prenom')}"
        if not key or key in seen:
            continue
        name = f"{clean(row.get('prenom'))} {clean(row.get('nom'))}".strip()
        if not name:
            continue
        seen.add(key)
        ent = mk_person(source, key, name, url="https://www.hatvp.fr" + clean(row.get("url_dossier")))
        ent.countries.append("fr")
        mtype = clean(row.get("type_mandat"))
        title = clean(row.get("qualite")) or _HATVP_TYPE_EN.get(mtype, mtype)
        dep = clean(row.get("departement"))
        ent.positions.append(position(f"{title}{' (' + dep + ')' if dep else ''}", "fr", "", "", "unknown"))
        ent.extra["mandate_type"] = mtype
        ent.listed_on = iso(row.get("date_depot"))
        yield ent


def parse_hatvp_declarations(paths: Dict[str, Path], source: ListSource) -> Iterator[Entity]:
    """HATVP full declarations: declarant (with DOB) and published spouse = RCA.

    Streams the 80 MB XML; spouses whose name is the "[Données non publiées]"
    placeholder are skipped because there is nothing to screen.
    """
    people: Dict[str, Entity] = {}
    spouses: List[Entity] = []
    for _, el in ET.iterparse(str(paths["main"]), events=("end",)):
        if el.tag != "declaration":
            continue
        d = el.find("general/declarant")
        nom, pre = _tx(d, "nom"), _tx(d, "prenom")
        if d is not None and nom and pre:
            dob = iso(_tx(d, "dateNaissance"))
            key = f"{nom}|{pre}|{dob}".lower()
            ent = people.get(key)
            if ent is None:
                ent = mk_person(source, key.replace(" ", "-"), f"{pre} {nom}")
                ent.countries.append("fr")
                add_dates(ent, dob)
                ent.url = "https://www.hatvp.fr/consulter-les-declarations/"
                people[key] = ent
            qual = _tx(el.find("general"), "qualiteDeclarant")
            kind = _tx(el.find("general/qualiteMandat"), "labelTypeMandat")
            start, end = _tx(el.find("general"), "dateDebutMandat"), _tx(el.find("general"), "dateFinMandat")
            title = qual or kind
            if title and not any(p.title == title for p in ent.positions):
                ent.positions.append(position(title, "fr", start, end))
            for sp in el.iter("items"):
                nc = sp.find("nomConjoint")
                if nc is None or not nc.text or _PLACEHOLDER.search(nc.text):
                    continue
                sname = clean(nc.text)
                if sname:
                    rc = mk_person(source, f"rca-{key}-{sname}".replace(" ", "-").lower(), sname, list_type="PEP_RCA")
                    rc.linked_to.append(f"{pre} {nom}")
                    rc.countries.append("fr")
                    emp = _tx(sp, "employeurConjoint")
                    rc.remarks = f"Spouse/partner of {pre} {nom}" + (f"; employer {emp}" if emp else "")
                    spouses.append(rc)
        el.clear()
    yield from people.values()
    seen = set()
    for rc in spouses:
        if rc.source_id not in seen:
            seen.add(rc.source_id)
            yield rc


def parse_rne(paths: Dict[str, Path], source: ListSource) -> Iterator[Entity]:
    """Répertoire national des élus: deputies, senators, mayors (ISO birth dates)."""
    labels = {"deputes": "Member of the National Assembly", "senateurs": "Senator", "maires": "Mayor"}
    for key, path in paths.items():
        title = labels.get(key, "Elected official")
        for i, row in enumerate(csv_rows(path, ";")):
            nom, pre = clean(row.get("Nom de l'élu")), clean(row.get("Prénom de l'élu"))
            if not nom:
                continue
            dob = clean(row.get("Date de naissance"))
            place = clean(row.get("Libellé de la commune")) or clean(row.get("Libellé de la circonscription législative")) \
                or clean(row.get("Libellé du département"))
            sid = f"{key}-{row.get('Code du département', '')}-{row.get('Code de la commune', '')}-{nom}-{pre}-{dob}".replace(" ", "-")
            ent = mk_person(source, sid, f"{pre} {nom}".strip(), url="https://www.data.gouv.fr/fr/datasets/repertoire-national-des-elus-1/")
            add_dates(ent, dob)
            ent.countries.append("fr")
            ent.nationalities.append("fr")
            ent.gender = {"M": "male", "F": "female"}.get(clean(row.get("Code sexe")), "")
            start = row.get("Date de début de la fonction") or row.get("Date de début du mandat")
            ent.positions.append(position(f"{title}, {place}" if place else title, "fr", start, "", "current"))
            ent.extra["occupation"] = clean(row.get("Libellé de la catégorie socio-professionnelle"))
            yield ent


# --------------------------------------------------------------------------
# Spain / Portugal
# --------------------------------------------------------------------------
def parse_es_congreso(paths: Dict[str, Path], source: ListSource) -> Iterator[Entity]:
    """Active deputies JSON. The landing page rotates a daily timestamped file name,
    so a stale/HTML response is rejected loudly instead of yielding nothing."""
    text = read_text(paths["main"])
    if text.lstrip()[:1] != "[":
        raise ValueError("Congreso roster: expected the DiputadosActivos__<timestamp>.json file, got HTML; "
                         "refresh the timestamp in the source URL")
    for i, row in enumerate(json.loads(text)):
        name = clean(row.get("NOMBRE"))
        if not name:
            continue
        ent = mk_person(source, f"{name}-{row.get('CIRCUNSCRIPCION', '')}".replace(" ", "-"), name,
                        url="https://www.congreso.es/es/busqueda-de-diputados")
        ent.countries.append("es")
        circ = clean(row.get("CIRCUNSCRIPCION"))
        ent.positions.append(position(f"Diputado al Congreso{', ' + circ if circ else ''}", "es",
                                      row.get("FECHAALTA") or row.get("FECHACONDICIONPLENA"), "", "current"))
        ent.extra["party"] = clean(row.get("FORMACIONELECTORAL"))
        ent.extra["group"] = clean(row.get("GRUPOPARLAMENTARIO"))
        ent.remarks = clean(row.get("BIOGRAFIA"))
        yield ent


def parse_pt_parlamento(paths: Dict[str, Path], source: ListSource) -> Iterator[Entity]:
    """Portuguese deputies of the current legislature (Informação Base JSON)."""
    data = load_json(paths["main"])
    leg = ""
    for d in data.get("Deputados", []):
        full, parl = clean(d.get("DepNomeCompleto")), clean(d.get("DepNomeParlamentar"))
        if not (full or parl):
            continue
        leg = clean(d.get("LegDes")) or leg
        ent = mk_person(source, str(int(d.get("DepId") or d.get("DepCadId") or 0)), full or parl,
                        url="https://www.parlamento.pt/DeputadoGP/Paginas/Deputados.aspx")
        ent.add_name(parl, "alias")
        ent.countries.append("pt")
        sits = d.get("DepSituacao") or []
        # The open (no end date) situation tells whether the deputy sits today:
        # Efetivo/Suspenso = still a deputy, Suplente = substitute not seated,
        # Renunciou/Desistência = gone.
        open_s = [x for x in sits if not x.get("sioDtFim")]
        last = open_s[-1] if open_s else (sits[-1] if sits else {})
        desc = clean(last.get("sioDes")).lower()
        end = last.get("sioDtFim") or ""
        title = "Deputado à Assembleia da República"
        if desc.startswith(("efetivo", "suspenso")) and not end:
            status = "current"
        elif desc.startswith("suplente"):
            status, title = "unknown", "Deputado suplente à Assembleia da República"
        else:
            status = "ended"
        circ = clean(d.get("DepCPDes"))
        ent.positions.append(position(f"{title} ({d.get('LegDes', '')}){', ' + circ if circ else ''}",
                                      "pt", last.get("sioDtInicio") or "", end, status))
        gps = d.get("DepGP") or []
        if gps:
            ent.extra["group"] = clean(gps[-1].get("gpSigla"))
        yield ent


# --------------------------------------------------------------------------
# registry
# --------------------------------------------------------------------------
_UK_MEMBERS = "https://members-api.parliament.uk/api/Members/Search?House={h}&IsCurrentMember=true&skip={s}&take=20"
_UK_INT = "https://interests-api.parliament.uk/api/v1/Interests?CategoryId={c}&Skip={s}&Take=20"
_EP_UA = {"User-Agent": BROWSER_UA, "Accept": "application/xml"}

_WHOISWHO_QUERY = (
    "PREFIX%20euvoc%3A%20%3Chttp%3A%2F%2Fpublications.europa.eu%2Fontology%2Feuvoc%23%3E%20"
    "PREFIX%20org%3A%20%3Chttp%3A%2F%2Fwww.w3.org%2Fns%2Forg%23%3E%20"
    "PREFIX%20skos%3A%20%3Chttp%3A%2F%2Fwww.w3.org%2F2004%2F02%2Fskos%2Fcore%23%3E%20"
    "PREFIX%20foaf%3A%20%3Chttp%3A%2F%2Fxmlns.com%2Ffoaf%2F0.1%2F%3E%20"
    "SELECT%20DISTINCT%20%3Fperson%20%3FgivenN%20%3FfamilyN%20%3ForgL%20%3Fposition%20WHERE%20%7B%20"
    "%3Forg%20org%3AsubOrganizationOf%2B%20%3Chttp%3A%2F%2Fpublications.europa.eu%2Fresource%2Fauthority%2Fcorporate-body%2FCOM%3E%20.%20"
    "%3Forg%20skos%3AprefLabel%20%3ForgL%20.%20FILTER%28LANGMATCHES%28LANG%28%3ForgL%29%2C%22en%22%29%29%20"
    "%3FMS%20org%3Aorganization%20%3Forg%20.%20%3FMS%20euvoc%3ApositionComplement%20%3Fposition%20.%20"
    "FILTER%28LANGMATCHES%28LANG%28%3Fposition%29%2C%22en%22%29%29%20"
    "FILTER%28REGEX%28%3Fposition%2C%20%22Director%7CCommissioner%7CPresident%7CHead%20of%20Cabinet%7CSecretary-General%22%2C%20%22i%22%29%29%20"
    "%3Fperson%20org%3AhasMembership%20%3FMS%20%3B%20foaf%3AfamilyName%20%3FfamilyN%20%3B%20foaf%3AgivenName%20%3FgivenN%20.%20%7D%20LIMIT%202000"
)

# The Parlamento file sits behind an opaque, rotating token (see notes).
_PT_URL = (
    "https://app.parlamento.pt/webutils/docs/doc.txt?path=4LyXUOZSNtTNVKo4xxjv%2bAXjCKLWuqbnK6TZloRaEmaKe0o5TFXmHx%2bXnLrRUBnmuJSxKGny0riwf007vkSMOLvMOoYViIP%2fqT4LpCxcrO1BPt233jMs6fYM0loKs4sgpUANn4K7CvT4oICbmjY2CYwWe3Hyy9ySxBrn%2bj%2b0uaATo1%2fkHbBmK3hqQs9Wc0qdtk5JpJfKTjR8ZndoZVUMg4ZrR53aYtz%2feXCXvC1umV9%2b0ZqWwig4fcaUJBkL83zEOL6RUSZ%2fo%2fJM1Kfh23boe0R61iLXXr7yUsn%2b3TvPfz7OGYoV%2b%2fTHKmA5upi7uiayY9eWgwFuFgk8PsgX2NKdxaj8s9mzxjwb9lyDg1y19DbIVmS2vgxqivPaExIVWXNnqDt0IbhXfH3FLQPoKFUnuw%3d%3d"
    "&fich=InformacaoBaseXVII_json.txt&Inline=true"
)

SOURCES: List[ListSource] = [
    ListSource(
        key="UK_PARLIAMENT_MEMBERS_API", name="UK Parliament current members (Commons and Lords)",
        jurisdiction="GB", list_type="PEP",
        urls={**{f"commons_{s:04d}": _UK_MEMBERS.format(h=1, s=s) for s in range(0, 660, 20)},
              **{f"lords_{s:04d}": _UK_MEMBERS.format(h=2, s=s) for s in range(0, 860, 20)}},
        parser=parse_uk_members, homepage="https://members.parliament.uk/", license="OGL",
        groups=["pep", "europe", "uk", "gb"], max_age_days=14,
        notes="Members API caps take at 20, so one URL per page (33 Commons + 43 Lords). Pages past the end are empty. "
              "Overlaps OpenSanctions gb_parliament; no DOB published.",
    ),
    ListSource(
        key="UK_PARLIAMENT_GOVERNMENT_POSTS", name="UK Government ministerial posts and holders",
        jurisdiction="GB", list_type="PEP",
        urls={"main": "https://members-api.parliament.uk/api/Posts/GovernmentPosts"},
        parser=parse_uk_government_posts, homepage="https://members.parliament.uk/", license="OGL",
        groups=["pep", "europe", "uk", "gb"], max_age_days=7,
        notes="Ministers with every post held; ids prefixed 'minister-' so they do not collide with the member roster.",
    ),
    ListSource(
        key="UK_PARLIAMENT_INTERESTS_FAMILY", name="UK MPs' register: family members employed / lobbying (RCA)",
        jurisdiction="GB", list_type="PEP_RCA",
        urls={**{f"employed_{s:03d}": _UK_INT.format(c=10, s=s) for s in range(0, 60, 20)},
              **{f"lobbying_{s:03d}": _UK_INT.format(c=11, s=s) for s in range(0, 40, 20)}},
        parser=parse_uk_interests_family, homepage="https://interests.parliament.uk/", license="OGL",
        groups=["pep", "europe", "uk", "gb"], max_age_days=30,
        notes="Categories 10 (employed, ~33) and 11 (lobbying, ~23). Linked to the MP via linked_to.",
    ),
    ListSource(
        key="EU_EP_MEPS_XML", name="European Parliament MEPs (current and outgoing)", jurisdiction="EU", list_type="PEP",
        urls={"current": "https://www.europarl.europa.eu/meps/en/full-list/xml",
              "outgoing": "https://www.europarl.europa.eu/meps/en/incoming-outgoing/outgoing/xml"},
        parser=parse_ep_meps_xml, homepage="https://www.europarl.europa.eu/meps/en/home", license="terms",
        groups=["pep", "europe", "eu"], headers=_EP_UA, max_age_days=14,
        notes="Needs a browser User-Agent (non-browser UAs get 202 with an empty body). No DOB; outgoing MEPs have mandate dates.",
    ),
    ListSource(
        key="EU_EP_OPENDATA_API", name="European Parliament open data - current MEPs", jurisdiction="EU", list_type="PEP",
        urls={"main": "https://data.europarl.europa.eu/api/v2/meps/show-current?format=application%2Fld%2Bjson&offset=0&limit=1000"},
        parser=parse_ep_opendata, homepage="https://data.europarl.europa.eu/", license="terms",
        groups=["pep", "europe", "eu"], max_age_days=14,
        notes="List endpoint only (name, country, group). Per-MEP detail has no birth date, so it is not fetched. Overlaps EU_EP_MEPS_XML.",
    ),
    ListSource(
        key="EU_WHOISWHO_SPARQL", name="EU institutions senior officials (Whoiswho, Commission)", jurisdiction="EU", list_type="PEP",
        urls={"main": "https://publications.europa.eu/webapi/rdf/sparql?format=text%2Fcsv&query=" + _WHOISWHO_QUERY},
        parser=parse_eu_whoiswho, homepage="https://op.europa.eu/en/web/who-is-who", license="terms",
        groups=["pep", "europe", "eu"], max_age_days=30,
        notes="Commission directors/commissioners only (query regex); 'Assistant/Adviser' titles are dropped. No DOB. "
              "Change the corporate-body URI in the query for other institutions.",
    ),
    ListSource(
        key="DE_BUNDESTAG_STAMMDATEN", name="Bundestag members since 1949 (living)", jurisdiction="DE", list_type="PEP",
        urls={"main": "https://www.bundestag.de/resource/blob/472878/MdB-Stammdaten.zip"},
        parser=parse_bundestag, homepage="https://www.bundestag.de/services/opendata", license="terms",
        groups=["pep", "europe", "de"], max_age_days=30,
        notes="Zip of MDB_STAMMDATEN.XML with DOB and birth place; deceased members are dropped. Blob id in the URL may change on republication.",
    ),
    ListSource(
        key="FR_HATVP_LISTE", name="France HATVP officials with declarations", jurisdiction="FR", list_type="PEP",
        urls={"main": "https://www.hatvp.fr/livraison/opendata/liste.csv"},
        parser=parse_hatvp_liste, homepage="https://www.hatvp.fr/", license="public",
        groups=["pep", "europe", "fr"], max_age_days=14,
        notes="Etalab licence. One row per declaration, deduplicated on 'classement'. Position status unknown (declarations outlive mandates).",
    ),
    ListSource(
        key="FR_HATVP_DECLARATIONS_XML", name="France HATVP declarations (declarants with DOB, spouses = RCA)",
        jurisdiction="FR", list_type="PEP",
        urls={"main": "https://www.hatvp.fr/livraison/merge/declarations.xml"},
        parser=parse_hatvp_declarations, homepage="https://www.hatvp.fr/", license="public",
        groups=["pep", "europe", "fr"], max_age_days=30, timeout=600,
        notes="80 MB XML, streamed. Spouse entities carry list_type PEP_RCA and only exist when the name is published.",
    ),
    ListSource(
        key="FR_RNE_ELUS", name="France Répertoire national des élus (deputies, senators, mayors)",
        jurisdiction="FR", list_type="PEP",
        urls={"deputes": "https://www.data.gouv.fr/fr/datasets/r/1ac42ff4-1336-44f8-a221-832039dbc142",
              "senateurs": "https://www.data.gouv.fr/fr/datasets/r/b78f8945-509f-4609-a4a7-3048b8370479",
              "maires": "https://www.data.gouv.fr/fr/datasets/r/2876a346-d50c-4911-934e-19ee07b0e503"},
        parser=parse_rne, homepage="https://www.data.gouv.fr/fr/datasets/repertoire-national-des-elus-1/", license="public",
        groups=["pep", "europe", "fr"], max_age_days=30,
        notes="Resource ids are stable permalinks that redirect to the latest file. Councillor files (65 MB) are not included.",
    ),
    ListSource(
        key="ES_CONGRESO_DIPUTADOS", name="Spain Congreso de los Diputados (active deputies)", jurisdiction="ES", list_type="PEP",
        urls={"main": "https://www.congreso.es/webpublica/opendata/diputados/DiputadosActivos__20261009050006.json"},
        parser=parse_es_congreso, homepage="https://www.congreso.es/es/opendata/diputados", license="public",
        groups=["pep", "europe", "es"], max_age_days=1,
        notes="The file name carries a daily timestamp (landing page https://www.congreso.es/es/opendata/diputados lists it), so this "
              "URL goes stale: an integrator resolver should regex 'DiputadosActivos__\\d+\\.json' from the landing page.",
    ),
    ListSource(
        key="PT_PARLAMENTO_INFORMACAO_BASE", name="Portugal Assembleia da República deputies (XVII)", jurisdiction="PT", list_type="PEP",
        urls={"main": _PT_URL},
        parser=parse_pt_parlamento, homepage="https://www.parlamento.pt/Cidadania/Paginas/DAInformacaoBase.aspx", license="public",
        groups=["pep", "europe", "pt"], max_age_days=7,
        notes="The 'path' query token is opaque and may rotate; re-discover from the DAInformacaoBase page link for 'XVII Legislatura'. No DOB.",
    ),
]
