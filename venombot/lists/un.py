"""UN Security Council Consolidated List (XML)."""

from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Dict, Iterator, Optional

from venombot.entities import Entity
from venombot.lists import ListSource
from venombot.lists._util import add_countries, add_dates, add_identifier, clean


def _text(el: Optional[ET.Element], tag: str) -> str:
    if el is None:
        return ""
    child = el.find(tag)
    return clean(child.text) if child is not None and child.text else ""


def _values(el: ET.Element, tag: str) -> list:
    return [clean(v.text) for v in el.findall(f"{tag}/VALUE") if v.text and clean(v.text)]


def parse_un(paths: Dict[str, Path], source: ListSource) -> Iterator[Entity]:
    """Parse INDIVIDUAL and ENTITY records from consolidated.xml."""
    for _, el in ET.iterparse(str(paths["main"]), events=("end",)):
        if el.tag not in ("INDIVIDUAL", "ENTITY"):
            continue
        is_person = el.tag == "INDIVIDUAL"
        ref = _text(el, "REFERENCE_NUMBER") or _text(el, "DATAID")
        ent = Entity(
            source=source.key,
            source_id=ref,
            schema="Person" if is_person else "Organization",
            list_type=source.list_type,
            listed_on=_text(el, "LISTED_ON"),
            remarks=_text(el, "COMMENTS1"),
            gender=_text(el, "GENDER").lower(),
            url="https://main.un.org/securitycouncil/en/content/un-sc-consolidated-list",
        )
        parts = [_text(el, t) for t in ("FIRST_NAME", "SECOND_NAME", "THIRD_NAME", "FOURTH_NAME")]
        ent.add_name(" ".join(p for p in parts if p), "primary")
        ent.add_name(_text(el, "NAME_ORIGINAL_SCRIPT"), "original")
        alias_tag = "INDIVIDUAL_ALIAS" if is_person else "ENTITY_ALIAS"
        for al in el.findall(alias_tag):
            quality = _text(al, "QUALITY").lower()
            ent.add_name(_text(al, "ALIAS_NAME"), "weak" if quality in ("low", "a.k.a. low") else "alias")
        program = _text(el, "UN_LIST_TYPE")
        if program:
            ent.programs.append(f"UN-{program}")
        for title in _values(el, "TITLE") + _values(el, "DESIGNATION"):
            ent.extra.setdefault("titles", []).append(title)
        for nat in _values(el, "NATIONALITY"):
            add_countries(ent.nationalities, nat)
        for dob in el.findall("INDIVIDUAL_DATE_OF_BIRTH"):
            for tag in ("DATE", "YEAR"):
                add_dates(ent, _text(dob, tag))
            frm, to = _text(dob, "FROM_YEAR"), _text(dob, "TO_YEAR")
            if frm and to:
                add_dates(ent, f"{frm} to {to}")
        for pob in el.findall("INDIVIDUAL_PLACE_OF_BIRTH"):
            place = ", ".join(p for p in (_text(pob, "CITY"), _text(pob, "STATE_PROVINCE"), _text(pob, "COUNTRY")) if p)
            if place:
                ent.birth_places.append(place)
        for doc in el.findall("INDIVIDUAL_DOCUMENT"):
            country = _text(doc, "ISSUING_COUNTRY") or _text(doc, "COUNTRY_OF_ISSUE")
            codes = []
            add_countries(codes, country)
            add_identifier(ent, _text(doc, "TYPE_OF_DOCUMENT") or "Document", _text(doc, "NUMBER"), codes[0] if codes else "")
        addr_tag = "INDIVIDUAL_ADDRESS" if is_person else "ENTITY_ADDRESS"
        for addr in el.findall(addr_tag):
            add_countries(ent.countries, _text(addr, "COUNTRY"))
        el.clear()
        if ent.names:
            yield ent


SOURCES = [
    ListSource(
        key="UN_SC_CONSOLIDATED",
        name="UN Security Council Consolidated List",
        jurisdiction="UN",
        list_type="SANCTIONS",
        urls={"main": "https://scsanctions.un.org/resources/xml/en/consolidated.xml"},
        parser=parse_un,
        homepage="https://main.un.org/securitycouncil/en/content/un-sc-consolidated-list",
        license="public",
        groups=["core", "sanctions", "un", "mena"],
        max_age_days=7,
    ),
]
