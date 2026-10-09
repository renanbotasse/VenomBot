"""EU, UK and Swiss consolidated sanctions lists, UN Arabic edition, UK sectoral bans.

These are the highest-value official lists: structured XML with original-script
names, birth data, citizenships and identity documents, so each parser keeps
those fields apart instead of leaving them in free text.
"""

from __future__ import annotations

import json
import re
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Dict, Iterator, List, Optional, Tuple

from venombot.dates import parse_dates
from venombot.entities import Entity
from venombot.lists import ListSource
from venombot.lists._util import add_dates, add_identifier, clean
from venombot.lists.sanctions_common import (
    add_country,
    add_countries_in,
    add_name_auto,
    html_text,
    is_non_latin,
    local,
    partial_iso,
    uniq,
)
from venombot.lists.un import parse_un

# --------------------------------------------------------------------------
# EU Financial Sanctions Files (FSF) 1.1
# --------------------------------------------------------------------------

EU_FSF_URL = "https://webgate.ec.europa.eu/fsd/fsf/public/files/xmlFullSanctionsList_1_1/content?token=dG9rZW4tMjAxNw"


def _eu_whole(el: ET.Element) -> str:
    whole = clean(el.get("wholeName"))
    if whole:
        return whole
    return " ".join(p for p in (clean(el.get(k)) for k in ("firstName", "middleName", "lastName")) if p)


def _eu_birthdate(el: ET.Element) -> str:
    iso = clean(el.get("birthdate"))
    if iso and parse_dates(iso):
        return parse_dates(iso)[0]
    return partial_iso(el.get("year", ""), el.get("monthOfYear", ""), el.get("dayOfMonth", ""))


def _eu_entity(el: ET.Element, source: ListSource) -> Optional[Entity]:
    kids: Dict[str, List[ET.Element]] = {}
    for c in el:
        kids.setdefault(local(c.tag), []).append(c)
    subject = kids.get("subjectType", [None])[0]
    code = (subject.get("code") if subject is not None else "") or ""
    schema = {"person": "Person", "enterprise": "Organization", "vessel": "Vessel", "aircraft": "Aircraft"}.get(
        code.lower(), "Organization" if code else "Unknown")
    logical = el.get("logicalId", "")
    ent = Entity(
        source=source.key,
        source_id=logical or el.get("euReferenceNumber", ""),
        schema=schema,
        list_type=source.list_type,
        listed_on=clean(el.get("designationDate")),
    )
    if not ent.source_id:
        return None
    eu_ref = clean(el.get("euReferenceNumber"))
    if eu_ref:
        ent.extra["eu_reference"] = eu_ref
    un_id = clean(el.get("unitedNationId"))
    if un_id:
        add_identifier(ent, "UN reference number", un_id)
    remark = " ".join(clean(r.text) for r in kids.get("remark", []) if r.text)
    details = clean(el.get("designationDetails"))
    ent.remarks = "; ".join(x for x in (remark, details) if x)

    # Strong aliases first so the primary is the best Latin-script spelling.
    aliases = sorted(
        kids.get("nameAlias", []),
        key=lambda a: (a.get("strong", "true").lower() != "true", is_non_latin(_eu_whole(a))),
    )
    for a in aliases:
        name = _eu_whole(a)
        if not name:
            continue
        weak = a.get("strong", "true").lower() != "true"
        add_name_auto(ent, name, "weak" if weak else ("primary" if not ent.names else "alias"),
                      clean(a.get("nameLanguage")).lower())
        gender = clean(a.get("gender")).upper()
        if gender in ("M", "F") and not ent.gender:
            ent.gender = "male" if gender == "M" else "female"
        func = clean(a.get("function"))
        if func and func not in ent.extra.setdefault("functions", []):
            ent.extra["functions"].append(func)

    regs = kids.get("regulation", [])
    for r in regs:
        prog = clean(r.get("programme"))
        if prog and prog not in ent.programs:
            ent.programs.append(prog)
        title = clean(r.get("numberTitle"))
        if title:
            ent.extra.setdefault("regulations", [])
            if title not in ent.extra["regulations"]:
                ent.extra["regulations"].append(title)
        if not ent.url:
            pu = r.find("{*}publicationUrl")
            if pu is not None and pu.text:
                ent.url = clean(pu.text)
    if not ent.listed_on:
        dates = sorted(d for d in (clean(r.get("entryIntoForceDate")) or clean(r.get("publicationDate")) for r in regs) if d)
        ent.listed_on = dates[0] if dates else ""

    for c in kids.get("citizenship", []):
        add_country(ent.nationalities, c.get("countryIso2Code"))
    for b in kids.get("birthdate", []):
        d = _eu_birthdate(b)
        if d and d not in ent.birth_dates:
            ent.birth_dates.append(d)
        place = ", ".join(p for p in (clean(b.get("city")), clean(b.get("place")), clean(b.get("region")),
                                      clean(b.get("countryDescription")) if b.get("countryIso2Code") not in ("", "00", None) else "") if p)
        if place and place not in ent.birth_places:
            ent.birth_places.append(place)
        add_country(ent.countries, b.get("countryIso2Code"))
    for i in kids.get("identification", []):
        number = clean(i.get("number")) or clean(i.get("latinNumber"))
        if not number:
            continue
        cc = clean(i.get("countryIso2Code")).lower()
        add_identifier(ent, clean(i.get("identificationTypeDescription")) or clean(i.get("identificationTypeCode")) or "Document",
                       number, "" if cc in ("", "00") else cc)
    for a in kids.get("address", []):
        add_country(ent.countries, a.get("countryIso2Code"))
    return ent


def parse_eu_fsf(paths: Dict[str, Path], source: ListSource) -> Iterator[Entity]:
    """Stream the 26 MB FSF 1.1 XML, one Entity per <sanctionEntity>.

    The server ignores Range requests and the file is too large to hold as a
    tree, so entities are built and cleared one at a time.
    """
    for _, el in ET.iterparse(str(paths["main"]), events=("end",)):
        if local(el.tag) != "sanctionEntity":
            continue
        ent = _eu_entity(el, source)
        el.clear()
        if ent and ent.names:
            yield ent


# --------------------------------------------------------------------------
# UK FCDO Sanctions List (XML)
# --------------------------------------------------------------------------

UK_URL = "https://sanctionslist.fcdo.gov.uk/docs/UK-Sanctions-List.xml"
_UK_SCHEMA = {"individual": "Person", "entity": "Organization", "ship": "Vessel", "aircraft": "Aircraft"}


def _t(el: ET.Element, tag: str) -> str:
    return clean(el.findtext(tag))


def _uk_name(name_el: ET.Element) -> str:
    # Name1..Name5 are given/middle names and Name6 the surname, so ascending
    # order gives the natural reading order.
    return " ".join(p for p in (_t(name_el, f"Name{i}") for i in range(1, 7)) if p)


def _uk_entity(des: ET.Element, source: ListSource) -> Optional[Entity]:
    uid = _t(des, "UniqueID")
    if not uid:
        return None
    kind = _t(des, "IndividualEntityShip").lower()
    ent = Entity(
        source=source.key,
        source_id=uid,
        schema=_UK_SCHEMA.get(kind, "Organization"),
        list_type=source.list_type,
        listed_on=(parse_dates(_t(des, "DateDesignated")) or [""])[0],
        url=f"https://search-uk-sanctions-list.service.gov.uk/designations/{uid}",
    )
    ofsi = _t(des, "OFSIGroupID")
    if ofsi:
        ent.extra["ofsi_group_id"] = ofsi
    un_ref = _t(des, "UNReferenceNumber")
    if un_ref:
        add_identifier(ent, "UN reference number", un_ref)
    names = des.findall("Names/Name")
    names.sort(key=lambda n: 0 if _t(n, "NameType").lower() == "primary name" else 1)
    for n in names:
        value = _uk_name(n)
        ntype = _t(n, "NameType").lower()
        strength = _t(n, "AliasStrength").lower()
        if ntype == "primary name":
            add_name_auto(ent, value, "primary")
        else:
            add_name_auto(ent, value, "weak" if strength.startswith("low") else "alias")
    for nl in des.findall("NonLatinNames/NonLatinName"):
        add_name_auto(ent, _t(nl, "NameNonLatinScript"), "original", _t(nl, "NonLatinScriptLanguage").lower())
    regime = _t(des, "RegimeName")
    if regime:
        ent.programs.append(regime)
    source_body = _t(des, "DesignationSource")
    if source_body:
        ent.extra["designation_source"] = source_body
    imposed = _t(des, "SanctionsImposed")
    if imposed:
        ent.extra["sanctions_imposed"] = imposed
    info = _t(des, "OtherInformation")
    reasons = _t(des, "UKStatementofReasons")
    ent.remarks = " ".join(x for x in (reasons, info) if x)
    for a in des.findall("Addresses/Address"):
        add_country(ent.countries, _t(a, "AddressCountry"))
    for title in des.findall("Titles/Title"):
        if title.text:
            ent.extra.setdefault("titles", []).append(clean(title.text))

    ind = des.find("IndividualDetails/Individual")
    if ind is not None:
        for d in ind.findall("DOBs/DOB"):
            add_dates(ent, d.text)
        for nat in ind.findall("Nationalities/Nationality"):
            add_countries_in(ent.nationalities, nat.text)
        for pos in ind.findall("Positions/Position"):
            if pos.text:
                ent.extra.setdefault("positions_text", []).append(clean(pos.text))
        g = _t(ind, "Genders/Gender").lower()
        ent.gender = g if g in ("male", "female") else ""
        for loc in ind.findall("BirthDetails/Location"):
            town, country = _t(loc, "TownOfBirth"), _t(loc, "CountryOfBirth")
            place = ", ".join(p for p in (town, country) if p)
            if place:
                ent.birth_places.append(place)
            add_country(ent.countries, country)
        for p in ind.findall("PassportDetails/Passport"):
            add_identifier(ent, "Passport", _t(p, "PassportNumber"))
        for n in ind.findall("NationalIdentifierDetails/NationalIdentifier"):
            add_identifier(ent, "National ID", _t(n, "NationalIdentifierNumber"))
    org = des.find("EntityDetails/Entity")
    if org is not None:
        for n in org.findall("BusinessRegistrationNumbers/BusinessRegistrationNumber"):
            add_identifier(ent, "Registration number", n.text)
        for p in org.findall("ParentCompanies/ParentCompany"):
            if p.text:
                ent.linked_to.append(clean(p.text))
        for p in org.findall("Subsidiaries/Subsidiary"):
            if p.text:
                ent.linked_to.append(clean(p.text))
    ship = des.find("ShipDetails/Ship")
    if ship is not None:
        for imo in ship.findall("IMONumbers/IMONumber"):
            num = re.sub(r"\D", "", imo.text or "")
            add_identifier(ent, "IMO", num)
        for flag in ship.findall("CurrentBelievedFlagOfShips/CurrentBelievedFlagOfShip"):
            add_country(ent.countries, flag.text)
        for owner in ship.findall("CurrentOwnerOperators/CurrentOwnerOperator"):
            if owner.text:
                ent.linked_to.append(clean(owner.text))
    if ent.schema == "Vessel":
        # Primary names look like 'IMO 9058713 ("AVAX")'; expose the bare name too.
        for n in list(ent.names):
            for m in re.finditer(r'\(["“]([^"”]+)["”]\)', n.value):
                ent.add_name(m.group(1), "alias")
    return ent


def parse_uk_fcdo(paths: Dict[str, Path], source: ListSource) -> Iterator[Entity]:
    """Stream <Designation> records; one designation = one UK Unique ID."""
    for _, el in ET.iterparse(str(paths["main"]), events=("end",)):
        if el.tag != "Designation":
            continue
        ent = _uk_entity(el, source)
        el.clear()
        if ent and ent.names:
            yield ent


# --------------------------------------------------------------------------
# Switzerland SECO (SESAM) whole list
# --------------------------------------------------------------------------

CH_URL = ("https://www.sesam.search.admin.ch/sesam-search-web/pages/downloadXmlGesamtliste.xhtml"
          "?lang=en&action=downloadXmlGesamtlisteAction")

# Rank of name parts so Western reading order is "given father family".
_CH_PART_RANK = {
    "title": 0, "given-name": 1, "further-given-name": 2, "father-name": 3, "grand-father-name": 4,
    "whole-name": 5, "family-name": 6, "maiden-name": 7, "other": 8, "suffix": 9,
}
_CH_KIND = {"individual": "Person", "entity": "Organization", "object": "Vessel"}


def _ch_place(place: Optional[ET.Element]) -> Tuple[str, str]:
    if place is None:
        return "", ""
    parts = [clean(place.findtext(t)) for t in ("location", "area")]
    country = place.find("country")
    cname = clean(country.text) if country is not None else ""
    cc = clean(country.get("iso-code")).lower() if country is not None else ""
    return ", ".join(p for p in parts + [cname] if p), cc


def _ch_names(ident: ET.Element, ent: Entity) -> None:
    for name in ident.findall("name"):
        ntype = name.get("name-type", "")
        parts = sorted(name.findall("name-part"), key=lambda p: (_CH_PART_RANK.get(p.get("name-part-type", ""), 8),
                                                                int(p.get("order", "0") or 0)))
        base = " ".join(clean(p.findtext("value")) for p in parts if clean(p.findtext("value")))
        lang = clean(name.get("lang")).lower()
        kind = "primary" if ntype == "primary-name" else ("weak" if name.get("quality", "good") not in ("good", "") else "alias")
        add_name_auto(ent, base, kind, lang)
        # Each part may carry spelling variants per (language, script); rebuild
        # one full name per variant key so Cyrillic/Arabic spellings are matchable.
        keys: Dict[Tuple[str, str], bool] = {}
        for p in parts:
            for sv in p.findall("spelling-variant"):
                keys[(sv.get("lang", ""), sv.get("script", ""))] = True
        for lang_k, script in keys:
            tokens = []
            partial = False
            for p in parts:
                pick = next((clean(sv.text) for sv in p.findall("spelling-variant")
                             if (sv.get("lang", ""), sv.get("script", "")) == (lang_k, script) and clean(sv.text)), None)
                if pick is None:
                    partial = True
                    pick = clean(p.findtext("value"))
                tokens.append(pick)
            variant = " ".join(t for t in tokens if t)
            # A variant covering only some parts mixes scripts in one name; keep it
            # (token matching still benefits) but mark it low-confidence.
            add_name_auto(ent, variant, "weak" if partial else "alias", lang_k.lower())


def _ch_target(target: ET.Element, source: ListSource, places: Dict[str, Tuple[str, str]],
               sets: Dict[str, Dict[str, str]]) -> Optional[Entity]:
    body = next((c for c in target if c.tag in _CH_KIND), None)
    if body is None:
        return None
    mods = target.findall("modification")
    if mods:
        latest = max(mods, key=lambda m: m.get("effective-date") or m.get("enactment-date") or "")
        if latest.get("modification-type") == "de-listed":
            return None  # the file keeps full history; delisted parties are not active designations
    ent = Entity(source=source.key, source_id=target.get("ssid", ""), schema=_CH_KIND[body.tag],
                 list_type=source.list_type)
    if not ent.source_id:
        return None
    listed = [m.get("effective-date") or m.get("enactment-date") for m in mods if m.get("modification-type") == "listed"]
    listed = sorted(d for d in listed if d)
    ent.listed_on = listed[0] if listed else ""
    sid = clean(target.findtext("sanctions-set-id"))
    meta = sets.get(sid, {})
    if meta.get("program"):
        ent.programs.append(meta["program"])
    if meta.get("measures"):
        ent.extra["measures"] = meta["measures"]
    if meta.get("origin"):
        ent.extra["origin"] = meta["origin"]
    if body.tag == "individual" and body.get("sex"):
        ent.gender = body.get("sex", "").lower()
    for ident in body.findall("identity"):
        _ch_names(ident, ent)
        for d in ident.findall("day-month-year"):
            iso = partial_iso(d.get("year", ""), d.get("month", ""), d.get("day", ""))
            if iso and iso not in ent.birth_dates:
                ent.birth_dates.append(iso)
        for y in ident.findall("year"):
            iso = partial_iso(y.text or "")
            if iso and iso not in ent.birth_dates:
                ent.birth_dates.append(iso)
        for nat in ident.findall("nationality"):
            c = nat.find("country")
            if c is not None and c.get("iso-code"):
                add_country(ent.nationalities, c.get("iso-code"))
        for pob in ident.findall("place-of-birth"):
            text, cc = places.get(pob.get("place-id", ""), ("", ""))
            if text and text not in ent.birth_places:
                ent.birth_places.append(text)
            add_country(ent.countries, cc)
        for doc in ident.findall("identification-document"):
            issuer = doc.find("issuer")
            cc = clean(issuer.get("code")).lower() if issuer is not None else ""
            add_identifier(ent, doc.get("document-type", "document").replace("-", " ").title(),
                           clean(doc.findtext("number")), cc)
        for addr in ident.findall("address"):
            add_country(ent.countries, places.get(addr.get("place-id", ""), ("", ""))[1])
    just = [clean(j.text) for j in body.findall("justification") if clean(j.text)]
    other = [clean(o.text) for o in body.findall("other-information") if clean(o.text)]
    ent.remarks = " ".join(uniq(just + other))
    for o in other:
        m = re.search(r"IMO(?: Number)?:?\s*(\d{7})", o)
        if m:
            add_identifier(ent, "IMO", m.group(1))
    rel = [r.get("target-id", "") for r in body.findall("relation") if r.get("target-id")]
    if rel:
        ent.extra["related_ssids"] = rel
    return ent


def _ch_children(path: Path) -> Iterator[ET.Element]:
    """Yield each direct child of the root once complete (cleared by the caller)."""
    depth = 0
    for event, el in ET.iterparse(str(path), events=("start", "end")):
        if event == "start":
            depth += 1
            continue
        depth -= 1
        if depth == 1:
            yield el


def parse_ch_seco(paths: Dict[str, Path], source: ListSource) -> Iterator[Entity]:
    """Parse the SESAM whole-list XML in two streaming passes.

    The file puts ``<place>`` lookups at the very end, after the targets that
    reference them, so places and programme sets are collected first. Only
    top-level ``<target>`` elements are parties (copies of targets are nested in
    modification history), and de-listed parties are kept in the file, so the
    latest modification type is checked.
    """
    places: Dict[str, Tuple[str, str]] = {}
    sets: Dict[str, Dict[str, str]] = {}
    for el in _ch_children(paths["main"]):
        if el.tag == "place":
            places[el.get("ssid", "")] = _ch_place(el)
        elif el.tag == "sanctions-program":
            key = next((clean(k.text) for k in el.findall("program-key") if k.get("lang") == "eng"), "")
            origin = clean(el.findtext("origin"))
            for st in el.findall("sanctions-set"):
                if st.get("lang") == "eng":
                    sets[st.get("ssid", "")] = {"program": key, "measures": clean(st.text), "origin": origin}
        el.clear()
    for el in _ch_children(paths["main"]):
        if el.tag != "target":
            el.clear()
            continue
        ent = _ch_target(el, source, places, sets)
        el.clear()
        if ent and ent.names:
            yield ent


# --------------------------------------------------------------------------
# UN Security Council consolidated list, Arabic edition
# --------------------------------------------------------------------------

def parse_un_ar(paths: Dict[str, Path], source: ListSource) -> Iterator[Entity]:
    """Reuse the UN XML parser on the Arabic edition.

    The schema is identical; names and comments come back in Arabic script,
    which is what makes this edition worth a separate entry for Arabic-name
    matching. ``source_id`` is the UN reference number, shared with the English
    entity, recorded in ``extra`` so reports can link the two.
    """
    for ent in parse_un(paths, source):
        for n in ent.names:
            if is_non_latin(n.value):
                n.lang = "ar"
        ent.gender = ""  # Arabic gender words are not normalised
        ent.extra["english_edition"] = "UN_SC_CONSOLIDATED"
        yield ent


# --------------------------------------------------------------------------
# UK Russia investment / financial restrictions (GOV.UK content API)
# --------------------------------------------------------------------------

_ROW = re.compile(r"<tr[^>]*>(.*?)</tr>", re.S | re.I)
_CELL = re.compile(r"<t[dh][^>]*>(.*?)</t[dh]>", re.S | re.I)


def parse_uk_hmt_bans(paths: Dict[str, Path], source: ListSource) -> Iterator[Entity]:
    """Parse the table inside ``details.body`` of the GOV.UK content JSON.

    These are Schedule 2 capital-market restrictions, so some entities are not
    asset-frozen and appear nowhere in the FCDO list.
    """
    data = json.loads(Path(paths["main"]).read_text(encoding="utf-8"))
    body = (data.get("details") or {}).get("body", "")
    updated = (data.get("public_updated_at") or "")[:10]
    for row in _ROW.findall(body):
        cells = [html_text(c) for c in _CELL.findall(row)]
        if len(cells) < 3 or cells[0].lower().startswith("organisation"):
            continue
        name, other, group_id = cells[0], cells[1], cells[2]
        uk_id = cells[3] if len(cells) > 3 and cells[3] not in ("-", "") else ""
        if not name:
            continue
        ent = Entity(
            source=source.key,
            source_id=group_id if group_id.isdigit() else re.sub(r"\W+", "-", name.lower()),
            schema="Organization",
            list_type=source.list_type,
            programs=["UK-RUSSIA-SCHEDULE-2"],
            listed_on=updated,
            url=(data.get("base_path") and "https://www.gov.uk" + data["base_path"]) or "",
            remarks="Subject to UK Russia financial and investment restrictions (capital markets / loans)."
                    + (" Also on the UK Sanctions List." if uk_id else ""),
        )
        ent.add_name(name, "primary")
        if group_id.isdigit():
            add_identifier(ent, "OFSI Group ID", group_id)
        if uk_id:
            add_identifier(ent, "UK Sanctions List Unique ID", uk_id)
            ent.extra["fcdo_unique_id"] = uk_id
        ent.countries.append("ru")
        yield ent


SOURCES = [
    ListSource(
        key="EU_FSF_XML_1_1",
        name="EU Financial Sanctions Files (consolidated, XML 1.1)",
        jurisdiction="EU",
        list_type="SANCTIONS",
        urls={"main": EU_FSF_URL},
        parser=parse_eu_fsf,
        homepage="https://data.europa.eu/data/datasets/consolidated-list-of-persons-groups-and-entities-subject-to-eu-financial-sanctions",
        license="terms",
        groups=["core", "sanctions", "eu"],
        max_age_days=3,
        timeout=300,
        notes="Public token 'token-2017' in URL; server ignores Range so the full 26 MB is downloaded. "
              "EU reuse policy (Decision 2011/833/EU): reuse with attribution. Sibling file "
              "csvFullSanctionsList_1_1 carries the same data. Check .../fsf/public/rss for the current token.",
    ),
    ListSource(
        key="UK_FCDO_SANCTIONS_XML",
        name="UK Sanctions List (FCDO, XML)",
        jurisdiction="GB",
        list_type="SANCTIONS",
        urls={"main": UK_URL},
        parser=parse_uk_fcdo,
        homepage="https://www.gov.uk/government/publications/the-uk-sanctions-list",
        license="OGL",
        groups=["core", "sanctions", "uk"],
        max_age_days=3,
        timeout=300,
        notes="Replaces the 50 MB CSV (one row per name, repeats statements of reasons). One <Designation> per "
              "UniqueID with grouped names, non-Latin names, DOBs, passports, IMO numbers.",
    ),
    ListSource(
        key="CH_SECO",
        name="Swiss SECO Sanctions List (SESAM whole list)",
        jurisdiction="CH",
        list_type="SANCTIONS",
        urls={"main": CH_URL},
        parser=parse_ch_seco,
        homepage="https://www.seco.admin.ch/seco/en/home/Aussenwirtschaftspolitik_Wirtschaftliche_Zusammenarbeit/Wirtschaftsbeziehungen/exportkontrollen-und-sanktionen/sanktionen-embargos/sanktionsmassnahmen.html",
        license="terms",
        groups=["core", "sanctions", "ch"],
        max_age_days=3,
        timeout=300,
        notes="42 MB, chunked (no Range). The file includes history and de-listed targets; the parser skips "
              "targets whose latest modification is 'de-listed'. Free reuse with source attribution "
              "(opendata.swiss). Liechtenstein applies this list.",
    ),
    ListSource(
        key="UN_SC_CONSOLIDATED_AR",
        name="UN Security Council Consolidated List (Arabic edition)",
        jurisdiction="UN",
        list_type="SANCTIONS",
        urls={"main": "https://scsanctions.un.org/resources/xml/ar/consolidated.xml"},
        parser=parse_un_ar,
        homepage="https://main.un.org/securitycouncil/ar/content/un-sc-consolidated-list",
        license="public",
        groups=["core", "sanctions", "un", "mena"],
        max_age_days=7,
        notes="Same schema as the English file; names and comments in Arabic script. source_id is the UN "
              "reference number (same as UN_SC_CONSOLIDATED).",
    ),
    ListSource(
        key="UK_HMT_RUSSIA_INVESTMENT_BANS",
        name="UK Russia financial and investment restrictions (Schedule 2)",
        jurisdiction="GB",
        list_type="SANCTIONS",
        urls={"main": "https://www.gov.uk/api/content/guidance/russia-list-of-persons-named-in-relation-to-financial-and-investment-restrictions"},
        parser=parse_uk_hmt_bans,
        homepage="https://www.gov.uk/government/publications/russia-list-of-persons-named-in-relation-to-financial-and-investment-restrictions",
        license="OGL",
        groups=["sanctions", "uk"],
        max_age_days=14,
        notes="Sectoral restrictions, not asset freezes: ROSNEFT and OPK OBORONPROM are not on the FCDO list.",
    ),
]
