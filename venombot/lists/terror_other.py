"""Domestic terrorist designation lists outside MENA: Asia, Europe, Americas.

Indonesia DTTOT, Malaysia MOHA, Azerbaijan FIU, Ukraine SFMS, Netherlands,
Canada, Vietnam MPS and Poland art. 118. Helpers (date/country normalisation
for non-English cells) are shared with ``terror_mena``.
"""

from __future__ import annotations

import json
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, Iterator, List, Optional, Set

from venombot.entities import Entity
from venombot.lists import ListSource
from venombot.lists._tables import read_ods, read_xlsx
from venombot.lists._util import clean, read_text
from venombot.lists.terror_mena import (
    add_countries, add_dates, add_ident, countries_local, dict_rows, fold, g, norm_dates, raw, split_list, stable_id,
)
from venombot.lists.un import parse_un

# --------------------------------------------------------------------------
# Indonesia - PPATK DTTOT (xlsx, Indonesian)
# --------------------------------------------------------------------------

_ID_PASSPORT = re.compile(r"(?i)\b(?:nomor\s+)?paspor(?:\s+\w+)?\s*(?:no\.?|nomor)?\s*[:\-]?\s*([A-Z]{0,3}\d{5,}[A-Z0-9]*)")


def parse_id_dttot(paths: Dict[str, Path], source: ListSource) -> Iterator[Entity]:
    """Daftar Terduga Teroris dan Organisasi Teroris.

    Aliases are inline in "Nama" separated by " alias "; "Terduga" says whether
    the row is a person (Orang) or a legal entity (Korporasi). Dates are Excel
    serials or free text ("01/07/1974 atau 01/01/1973"); passport numbers sit in
    the free-text description.
    """
    seen: Set[str] = set()
    for rec in dict_rows(read_xlsx(Path(paths["main"])), ["nama", "terduga"]):
        full = clean(rec.get("nama", ""))
        if not full:
            continue
        kind = fold(g(rec, "terduga"))
        if kind not in ("orang", "korporasi"):
            continue
        parts = [p.strip() for p in re.split(r"(?i)\s+alias\s+|\s+a\.k\.a\.?\s+", full) if p.strip()]
        ref = g(rec, "kode densus")
        ent = Entity(source=source.key, source_id=ref if ref and ref not in seen else stable_id("ID", seen, full, ref),
                     schema="Person" if kind == "orang" else "Organization", list_type=source.list_type,
                     url="https://www.ppatk.go.id/dalam_negeri/read/1400/daftar-terduga-teroris-dan-organisasi-teroris-dttot.html")
        seen.add(ent.source_id)
        ent.add_name(parts[0], "primary", "id")
        for a in parts[1:]:
            ent.add_name(a, "alias", "id")
        desc = raw(rec, "deskripsi")
        for m in _ID_PASSPORT.finditer(desc):
            add_ident(ent, "Passport", m.group(1))
        add_dates(ent, g(rec, "tanggal lahir"))
        for place in split_list(raw(rec, "tempat lahir"), extra_seps="-"):
            ent.birth_places.append(place)
        add_countries(ent.nationalities if kind == "orang" else ent.countries, g(rec, "wn/asal negara"))
        for seg in split_list(raw(rec, "alamat"), extra_seps="-"):
            add_countries(ent.countries, seg.rsplit(",", 1)[-1])
        if ref:
            ent.programs.append(ref.split("-")[0])
        ent.remarks = clean(desc)[:1500]
        yield ent


# --------------------------------------------------------------------------
# Malaysia - MOHA s.66B list (custom xmlResponse)
# --------------------------------------------------------------------------


def _my_label(attr: str) -> str:
    """'(3)\\nName' -> 'name': the field name embeds a column number and newline."""
    return fold(attr.replace("&#10;", "\n").split("\n")[-1])


def parse_my_moha(paths: Dict[str, Path], source: ListSource) -> Iterator[Entity]:
    """Specified entities under the Anti-Money Laundering Act (individuals + groups)."""
    root = ET.parse(str(paths["main"])).getroot()
    seen: Set[str] = set()
    for section in root.iter("section"):
        is_person = "individual" in fold(section.get("type", ""))
        for entry in section.findall("entry"):
            rec = {_my_label(f.get("name", "")): clean(f.text) for f in entry.findall("field")}
            name = rec.get("name", "")
            if not name:
                continue
            ref = rec.get("reference", "") or stable_id("MY", seen, name)
            if ref in seen:
                ref = stable_id("MY", seen, name, ref)
            seen.add(ref)
            ent = Entity(source=source.key, source_id=ref, schema="Person" if is_person else "Organization",
                         list_type=source.list_type, url="https://www.moha.gov.my/")
            ent.add_name(name, "primary", "ms")
            for key in ("other names", "other name", "alias"):
                for a in split_list(rec.get(key, ""), extra_seps="/"):
                    ent.add_name(a, "alias", "ms")
            add_dates(ent, rec.get("date of birth", ""))
            if rec.get("place of birth"):
                ent.birth_places.append(rec["place of birth"])
            add_countries(ent.nationalities, rec.get("nationality", ""))
            add_ident(ent, "Passport", rec.get("passport number", ""))
            add_ident(ent, "NRIC", rec.get("identification card number", ""), "my")
            addr = rec.get("address", "")
            add_countries(ent.countries, addr.rsplit(",", 1)[-1])
            ent.listed_on = (norm_dates(rec.get("date 0f listed") or rec.get("date of listed") or "") or [""])[0]
            if rec.get("designation"):
                ent.extra["designation"] = rec["designation"]
            ent.programs.append("MY-ACT613-66B")
            yield ent


# --------------------------------------------------------------------------
# Azerbaijan - FIU domestic list (UN-consolidated schema)
# --------------------------------------------------------------------------


def parse_az_fiu(paths: Dict[str, Path], source: ListSource) -> Iterator[Entity]:
    """Reuse the UN parser (same tag names) and only fix what differs.

    INDIVIDUALS and ENTITIES each number from 001, so ids are prefixed with the
    record kind or they would collide.
    """
    for ent in parse_un(paths, source):
        ent.source_id = ("I" if ent.schema == "Person" else "E") + ent.source_id
        ent.url = "https://hms.gov.az/en/olkedaxili-siyahi"
        ent.programs = [p for p in ent.programs if p != "UN-"] or ["AZ-DOMESTIC"]
        ent.countries = ent.countries or ["az"]
        yield ent


# --------------------------------------------------------------------------
# Ukraine - SFMS terrorist blacklist (list-terror XML)
# --------------------------------------------------------------------------


def _tx(el: ET.Element, tag: str) -> str:
    child = el.find(tag)
    return clean(child.text) if child is not None and child.text else ""


def parse_ua_sfms(paths: Dict[str, Path], source: ListSource) -> Iterator[Entity]:
    """State Financial Monitoring Service list of persons linked to terrorism.

    Mostly UN-derived plus Ukrainian domestic entries. ``type-entry`` 2 is a
    person and 1 an entity (verified against date-of-birth presence: 672 of 727
    type-2 records carry a DOB, only 14 of 288 type-1 ones). The program text is
    Ukrainian and kept as-is.
    """
    for _, el in ET.iterparse(str(paths["main"]), events=("end",)):
        if el.tag != "acount-list":
            continue
        is_person = _tx(el, "type-entry") == "2"
        number = _tx(el, "number-entry")
        ent = Entity(source=source.key, source_id=number, schema="Person" if is_person else "Organization",
                     list_type=source.list_type, url="https://fiu.gov.ua/en/pages/dokumenti/terror-list",
                     listed_on=(norm_dates(_tx(el, "date-entry")) or [""])[0])
        for aka in el.findall("aka-list"):
            name = " ".join(_tx(aka, f"aka-name{i}") for i in range(1, 5) if _tx(aka, f"aka-name{i}"))
            kind = _tx(aka, "type-aka")
            quality = _tx(aka, "quality-aka")
            lang = "uk" if re.search("[Ѐ-ӿ]", name) else "en"
            if kind == "N":
                ent.add_name(name, "primary", lang)
            elif lang == "uk":
                ent.add_name(name, "original", lang)
            else:
                ent.add_name(name, "weak" if quality == "2" else "alias", lang)
        program = _tx(el, "program-entry")
        if program:
            ent.programs.append(program)
        for tag in ("date-of-birth-list",):
            for node in el.findall(tag):
                add_dates(ent, node.text or "")
        for node in el.findall("place-of-birth-list"):
            if clean(node.text):
                ent.birth_places.append(clean(node.text))
        for tag in ("nationality-list", "citizenchip-list"):
            for node in el.findall(tag):
                add_countries(ent.nationalities, node.text or "")
        for doc in el.findall("document-list"):
            country = (countries_local(_tx(doc, "document-country")) or [""])[0]
            add_ident(ent, "Document", _tx(doc, "document-id"), country)
        for node in el.findall("id-number-list"):
            add_ident(ent, "ID", node.text or "")
        for addr in el.findall("address-list"):
            add_countries(ent.countries, _tx(addr, "country"))
            add_countries(ent.countries, _tx(addr, "address").rsplit(",", 1)[-1])
        for tag in ("designation-list", "title-list", "working-list"):
            for node in el.findall(tag):
                if clean(node.text):
                    ent.extra.setdefault("titles", []).append(clean(node.text))
        ent.remarks = _tx(el, "comments")[:1500]
        el.clear()
        if ent.names:
            yield ent


# --------------------------------------------------------------------------
# Netherlands - national terrorism list (ods)
# --------------------------------------------------------------------------


def parse_nl_terror(paths: Dict[str, Path], source: ListSource) -> Iterator[Entity]:
    """Persons and entities with frozen assets (national list, ods).

    Row 0 is a title, row 1 the header. A row without first names is an
    organisation (its name sits in the surname column). Dates are D-M-YYYY.
    """
    seen: Set[str] = set()
    for rec in dict_rows(read_ods(Path(paths["main"])), ["surname", "first name"]):
        surname, first = g(rec, "surname"), g(rec, "first name")
        if not surname:
            continue
        dob = g(rec, "date of birth")
        ent = Entity(source=source.key, source_id=stable_id("NL", seen, surname, first, dob),
                     schema="Person" if first else "Organization", list_type=source.list_type,
                     url="https://www.government.nl/topics/counterterrorism-and-national-security/national-terrorism-list")
        if first:
            ent.add_name(f"{first} {surname}", "primary", "nl")
            ent.add_name(f"{surname}, {first}", "alias", "nl")
        else:
            ent.add_name(surname, "primary", "nl")
        for a in split_list(raw(rec, "alias")):
            ent.add_name(a, "alias", "nl")
        add_dates(ent, dob)
        place = g(rec, "place of birth")
        if place:
            ent.birth_places.append(place)
            add_countries(ent.countries, place)
        decision = g(rec, "date of ministerial")
        ent.listed_on = (norm_dates(decision) or [""])[0]
        notice = g(rec, "link official")
        if notice:
            ent.remarks = f"Official notification: {notice}"
        ent.programs.append("NL-NATIONAL-TERRORISM-LIST")
        yield ent


# --------------------------------------------------------------------------
# Canada - Criminal Code listed entities (Atom)
# --------------------------------------------------------------------------

_ATOM = "{http://www.w3.org/2005/Atom}"
_ACRONYM = re.compile(r"\s*\(([^()]{2,40})\)\s*$")


def parse_ca_listed(paths: Dict[str, Path], source: ListSource) -> Iterator[Entity]:
    """s.83.05 Criminal Code listed entities: title = name, summary = aliases."""
    root = ET.parse(str(paths["main"])).getroot()
    seen: Set[str] = set()
    for entry in root.iter(f"{_ATOM}entry"):
        title = clean(entry.findtext(f"{_ATOM}title"))
        if not title:
            continue
        sid = clean(entry.findtext(f"{_ATOM}id")) or stable_id("CA", seen, title)
        seen.add(sid)
        ent = Entity(source=source.key, source_id=sid, schema="Organization", list_type=source.list_type,
                     url="https://www.publicsafety.gc.ca/cnt/ntnl-scrt/cntr-trrrsm/lstd-ntts/crrnt-lstd-ntts-en.aspx",
                     listed_on=(norm_dates(clean(entry.findtext(f"{_ATOM}published"))) or [""])[0])
        ent.add_name(title, "primary", "en")
        m = _ACRONYM.search(title)
        if m:
            ent.add_name(title[:m.start()], "alias", "en")
            ent.add_name(m.group(1), "alias", "en")
        summary = clean(entry.findtext(f"{_ATOM}summary"))
        for a in split_list(summary):
            ent.add_name(a, "alias", "en")
        ent.remarks = clean(entry.findtext(f"{_ATOM}content"))[:2000]
        updated = clean(entry.findtext(f"{_ATOM}updated"))
        if updated:
            ent.extra["updated"] = updated
        ent.programs.append("CA-CRIMINAL-CODE-83.05")
        yield ent


# --------------------------------------------------------------------------
# Vietnam - Ministry of Public Security (JSON API, Portal-Id: 22)
# --------------------------------------------------------------------------


def _vn_date(value: str) -> str:
    """"1953-11-19T17:00:00Z" is midnight ICT: add 7 hours to get the local date."""
    try:
        dt = datetime.strptime(value[:19], "%Y-%m-%dT%H:%M:%S") + timedelta(hours=7)
        return dt.date().isoformat()
    except ValueError:
        return (norm_dates(value) or [""])[0]


def parse_vn_mps(paths: Dict[str, Path], source: ListSource) -> Iterator[Entity]:
    """Organisations and listed members from the MPS terrorist portal.

    POLITICAL CAVEAT: the list is a state designation that includes diaspora
    opposition groups; it is data about what Vietnam designates, not a neutral
    terror finding. Only organisations with a members endpoint carry persons.
    """
    seen: Set[str] = set()
    for name, path in sorted(paths.items()):
        body = json.loads(read_text(Path(path)))
        rows = body.get("data") or []
        if isinstance(rows, dict):
            rows = rows.get("content") or rows.get("data") or []
        for row in rows:
            if name.startswith("org"):
                title = clean(row.get("name"))
                if not title:
                    continue
                ent = Entity(source=source.key, source_id=f"ORG-{row.get('id')}", schema="Organization",
                             list_type=source.list_type, url="https://bocongan.gov.vn/")
                ent.add_name(title, "primary", "vi")
                ent.remarks = clean(row.get("description"))
                ent.programs.append("VN-MPS")
                yield ent
            else:
                full = clean(row.get("fullName"))
                if not full:
                    continue
                ent = Entity(source=source.key, source_id=f"M-{row.get('id')}-{stable_id('V', seen, full)[-6:]}",
                             schema="Person", list_type=source.list_type, url="https://bocongan.gov.vn/",
                             gender={1: "male", 2: "female"}.get(row.get("gender"), ""))
                ent.add_name(full, "primary", "vi")
                for a in split_list(row.get("codeName")):
                    ent.add_name(a, "alias", "vi")
                if row.get("birthDate"):
                    d = _vn_date(str(row["birthDate"]))
                    if d:
                        ent.birth_dates.append(d)
                if clean(row.get("placeOfBirth")):
                    ent.birth_places.append(clean(row["placeOfBirth"]))
                add_countries(ent.nationalities, row.get("nationality") or "")
                add_ident(ent, "ID", str(row.get("identificationNumber") or ""))
                if clean(row.get("position")):
                    ent.extra["position"] = clean(row["position"])
                ent.remarks = clean(row.get("infoOther"))[:1500]
                ent.programs.append("VN-MPS")
                yield ent


# --------------------------------------------------------------------------
# Poland - art. 118 AML act (xlsx)
# --------------------------------------------------------------------------


def parse_pl_art118(paths: Dict[str, Path], source: ListSource) -> Iterator[Entity]:
    """Persons and entities under special restrictive measures (art. 118 AML act).

    "Pseudonim" lists aliases as a)/b)/c); "Inne informacje" holds nationality
    ("narodowość: iracka") and location. Rows with a delisting date are skipped.
    """
    seen: Set[str] = set()
    for rec in dict_rows(read_xlsx(Path(paths["main"])), ["imiona i nazwiska"]):
        name = g(rec, "imiona i nazwiska")
        if not name or g(rec, "data wykreslenia"):
            continue
        dob, pob = g(rec, "data urodzenia"), g(rec, "miejsce urodzenia")
        other = raw(rec, "inne informacje")
        is_person = bool(dob or pob or "narodowosc" in fold(other))
        m = re.match(r"^(.*?)\s*\(([^()]+)\)\s*$", name)
        ent = Entity(source=source.key, source_id=stable_id("PL", seen, g(rec, "lp"), name),
                     schema="Person" if is_person else "Organization", list_type=source.list_type,
                     url="https://www.gov.pl/web/finanse/lista-osob-i-podmiotow-wobec-ktorych-stosuje-sie-szczegolne-srodki-ograniczajace")
        ent.add_name(m.group(1) if m else name, "primary", "en")
        if m:
            ent.add_name(m.group(2), "alias", "en")
        ent.add_name(g(rec, "pisownia oryginalna"), "original")
        for a in split_list(raw(rec, "pseudonim")):
            ent.add_name(a, "alias", "en")
        add_dates(ent, dob)
        if pob:
            ent.birth_places.append(pob)
            add_countries(ent.countries, pob.rsplit(",", 1)[-1])
        add_countries(ent.nationalities, g(rec, "obywatelstwo"))
        for line in split_list(other):
            low = fold(line)
            if low.startswith("narodowosc"):
                add_countries(ent.nationalities, line.split(":", 1)[-1])
            elif low.startswith("lokalizacja"):
                add_countries(ent.countries, line.split(":", 1)[-1].rsplit(",", 1)[-1])
        add_ident(ent, "ID document", g(rec, "rodzaj i nr"))
        ent.listed_on = (norm_dates(g(rec, "data umieszczenia")) or [""])[0]
        ent.remarks = g(rec, "uzasadnienie")
        ent.programs.append("PL-AML-ART118")
        yield ent


# --------------------------------------------------------------------------
# registry
# --------------------------------------------------------------------------

SOURCES: List[ListSource] = [
    ListSource(
        key="ID_PPATK_DTTOT", name="Indonesia PPATK DTTOT (suspected terrorists and terrorist organisations)",
        jurisdiction="ID", list_type="TERRORISM",
        urls={"main": "https://ppatk.go.id/backend/assets/uploads/20260819055455.xlsx"},
        parser=parse_id_dttot, homepage="https://www.ppatk.go.id/dalam_negeri/read/1400/daftar-terduga-teroris-dan-organisasi-teroris-dttot.html",
        license="public", groups=["terrorism", "asia"], max_age_days=30,
        notes="Timestamped file name; the only href ending .xlsx on the homepage. Upload host 301-redirects."),
    ListSource(
        key="MY_MOHA_SANCTIONS_LIST", name="Malaysia MOHA Specified Entities (Act 613 s.66B)",
        jurisdiction="MY", list_type="TERRORISM",
        urls={"main": "https://www.moha.gov.my/utama/images/Bahagian%20Keselamatan%20dan%20Ketenteraman%20Awam/SENARAI_KEMENTERIAN_DALAM_NEGERI/SENARAI_KDN_2026_BI.xml"},
        parser=parse_my_moha, homepage="https://www.moha.gov.my/utama/index.php/en/component/content/article/350-list-of-ministries-of-home-affairs",
        license="public", groups=["terrorism", "asia"], max_age_days=30,
        notes="Year is embedded in the file name (SENARAI_KDN_<year>_BI.xml). Field names contain '&#10;'; matched by label."),
    ListSource(
        key="AZ_FIU_DOMESTIC_LIST", name="Azerbaijan FIU Domestic Terrorism List",
        jurisdiction="AZ", list_type="TERRORISM",
        urls={"main": "https://hms.gov.az/uploads/list/1/76f9249ad1975c2308115fa9954d3e94.xml?v=1755866308"},
        parser=parse_az_fiu, homepage="http://hms.gov.az/en/olkedaxili-siyahi", license="public",
        groups=["terrorism", "europe"], max_age_days=30,
        notes="Hashed file name; discover the 'XML' link on the homepage. UN-consolidated schema, parsed with the UN parser."),
    ListSource(
        key="UA_SFMS_BLACKLIST", name="Ukraine SFMS Terrorism Blacklist",
        jurisdiction="UA", list_type="TERRORISM",
        urls={"main": "https://fiu.gov.ua/assets/userfiles/Terror/BlackListFull.xml"},
        parser=parse_ua_sfms, homepage="https://fiu.gov.ua/en/pages/dokumenti/terror-list", license="public",
        headers={"User-Agent": "Mozilla/5.0"}, groups=["terrorism", "europe"], max_age_days=3,
        notes="Akamai returns 403 for any User-Agent containing 'VenomBot', hence the plain UA header. Daily update."),
    ListSource(
        key="NL_NATIONAL_TERRORISM_LIST", name="Netherlands National Terrorism List",
        jurisdiction="NL", list_type="TERRORISM",
        urls={"main": "https://www.government.nl/site/binaries/site-content/collections/documents/2016/01/15/national-terrorism-list/eng-terrorismelijst.ods"},
        parser=parse_nl_terror, homepage="https://www.government.nl/topics/counterterrorism-and-national-security/national-terrorism-list",
        license="CC0", groups=["terrorism", "europe", "eu"], max_age_days=30,
        notes="Fixed URL updated in place; old rijksoverheid.nl path 404s."),
    ListSource(
        key="CA_LISTED_TERRORIST_ENTITIES", name="Canada Listed Terrorist Entities (Criminal Code)",
        jurisdiction="CA", list_type="TERRORISM",
        urls={"main": "https://www.publicsafety.gc.ca/cnt/_xml/lstd-ntts-eng.xml"},
        parser=parse_ca_listed, homepage="https://www.publicsafety.gc.ca/cnt/ntnl-scrt/cntr-trrrsm/lstd-ntts/index-en.aspx",
        license="public", groups=["terrorism", "americas"], max_age_days=30,
        notes="Atom feed, ~90 entities; distinct from SEMA sanctions. Crown copyright, reproduction permitted under GC terms."),
    ListSource(
        key="VN_MPS_TERRORIST_ORGANIZATIONS", name="Vietnam MPS Terrorist Organisations and Members",
        jurisdiction="VN", list_type="COUNTER_SANCTIONS",
        urls={"orgs": "https://api-portal.bocongan.gov.vn/backend-portal/terrorist-organization",
              "members_viettan": "https://api-portal.bocongan.gov.vn/backend-portal/terrorist-member?organization_slug=to-chuc-khung-bo-viet-tan-1751427601&size=100"},
        parser=parse_vn_mps, homepage="https://bocongan.gov.vn/", license="public",
        headers={"Portal-Id": "22"}, groups=["terrorism", "asia"], max_age_days=30,
        notes="Portal-Id 22 = Vietnamese (the English portal 28 returns empty member lists). Only the Viet Tan "
              "organisation had members on 2026-10-09; other slugs return empty data. Politically contested: "
              "state designation of diaspora opposition groups. Typed COUNTER_SANCTIONS (severity LOW) so it never "
              "yields a HOLD_FOR_REVIEW on its own."),
    ListSource(
        key="PL_MF_AML_ART118_XLSX", name="Poland MF AML Art.118 Special Restrictive Measures List",
        jurisdiction="PL", list_type="TERRORISM",
        urls={"main": "https://www.gov.pl/attachment/56238b34-8a26-4431-a05a-e1d039f0defa"},
        parser=parse_pl_art118, homepage="https://www.gov.pl/web/finanse/lista-osob-i-podmiotow-wobec-ktorych-stosuje-sie-szczegolne-srodki-ograniczajace-na-podstawie-art-118-ustawy-z-dnia-1-marca-2018-r-o-przeciwdzialaniu-praniu-pieniedzy-i-finansowaniu-terroryzmu",
        license="public", groups=["terrorism", "europe", "eu"], max_age_days=60,
        notes="Attachment UUID is stable per version; the landing page links the current xlsx."),
]
