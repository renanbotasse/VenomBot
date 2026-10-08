"""US Treasury OFAC — SDN and Consolidated (non-SDN) lists.

The legacy CSV files are header-less and split one party across sdn.csv
(primary name + remarks), alt.csv (aliases) and add.csv (addresses), joined
on ent_num. DOB, nationality and IDs live inside the free-text remarks.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Dict, Iterable, Iterator

from venombot.entities import Entity
from venombot.lists import ListSource
from venombot.lists._util import (
    add_countries,
    add_dates,
    add_identifier,
    clean,
    iter_csv,
    split_value_country,
)

_BASE = "https://sanctionslistservice.ofac.treas.gov/api/download/"

SDN_FIELDS = [
    "ent_num", "name", "type", "program", "title", "call_sign", "vess_type",
    "tonnage", "grt", "vess_flag", "vess_owner", "remarks",
]
ALT_FIELDS = ["ent_num", "alt_num", "alt_type", "alt_name", "alt_remarks"]
ADD_FIELDS = ["ent_num", "add_num", "address", "city", "country", "add_remarks"]

_SCHEMA = {"individual": "Person", "vessel": "Vessel", "aircraft": "Aircraft"}

# Remark segments carrying identifiers, e.g. "Passport A1234567 (Iran)".
_ID_SEGMENT = re.compile(
    r"^(?P<type>(?:[A-Z][\w./-]*\s){0,5}?(?:Passport|National ID No\.|Tax ID No\.|"
    r"Registration (?:ID|Number)|Identification Number|Cedula No\.|NIT #|RFC|CURP|"
    r"SSN|Driver's License No\.|IMO|MMSI|SWIFT/BIC|Business Registration Number|"
    r"Company Number|Personal ID Card|Residency Number|Travel Document Number|"
    r"Digital Currency Address - \w+|Email Address|Website|Phone Number|D-U-N-S Number|"
    r"Legal Entity Number|Commercial Registry Number|Public Security and Immigration No\.))"
    r"\s+(?P<value>.+)$"
)
_AKA = re.compile(r"a\.k\.a\.,?\s+'([^']+)'")


def _parse_remarks(ent: Entity, remarks: str) -> None:
    for seg in (s.strip(" .") for s in remarks.split(";")):
        if not seg:
            continue
        low = seg.lower()
        if low.startswith("dob "):
            add_dates(ent, seg[4:])
        elif low.startswith("pob "):
            ent.birth_places.append(seg[4:].strip())
        elif low.startswith("nationality "):
            add_countries(ent.nationalities, seg[12:])
        elif low.startswith("citizen "):
            add_countries(ent.nationalities, seg[8:])
        elif low.startswith("gender "):
            ent.gender = seg[7:].strip().lower()
        elif low.startswith("linked to: "):
            ent.linked_to.append(seg[11:].strip())
        elif low.startswith(("organization established date", "date of registration")):
            continue
        else:
            for alias in _AKA.findall(seg):
                ent.add_name(alias, "weak")
            m = _ID_SEGMENT.match(seg)
            if m:
                value, country = split_value_country(m.group("value"))
                add_identifier(ent, m.group("type"), value, country)


def _parse(paths: Dict[str, Path], source: ListSource, prefix: str) -> Iterator[Entity]:
    aliases: Dict[str, list] = {}
    if f"{prefix}alt" in paths:
        for row in iter_csv(paths[f"{prefix}alt"], fieldnames=ALT_FIELDS):
            name = clean(row.get("alt_name"))
            if name:
                kind = "weak" if clean(row.get("alt_type")).lower() == "weak aka" else "alias"
                aliases.setdefault(clean(row["ent_num"]), []).append((name, kind))
    addresses: Dict[str, list] = {}
    if f"{prefix}add" in paths:
        for row in iter_csv(paths[f"{prefix}add"], fieldnames=ADD_FIELDS):
            addresses.setdefault(clean(row["ent_num"]), []).append(clean(row.get("country")))

    for row in iter_csv(paths[f"{prefix}main"], fieldnames=SDN_FIELDS):
        ent_num = clean(row.get("ent_num"))
        name = clean(row.get("name"))
        if not ent_num or not name or not ent_num.isdigit():
            continue
        ent = Entity(
            source=source.key,
            source_id=ent_num,
            schema=_SCHEMA.get(clean(row.get("type")).lower(), "Organization"),
            list_type=source.list_type,
            url=f"https://sanctionssearch.ofac.treas.gov/Details.aspx?id={ent_num}",
        )
        ent.add_name(name, "primary")
        for alias, kind in aliases.get(ent_num, []):
            ent.add_name(alias, kind)
        ent.programs = [p.strip() for p in re.split(r"\]\s*\[|;", clean(row.get("program")).strip("[]")) if p.strip()]
        title = clean(row.get("title"))
        if title:
            ent.extra["title"] = title
        remarks = clean(row.get("remarks"))
        ent.remarks = remarks
        _parse_remarks(ent, remarks)
        for country in addresses.get(ent_num, []):
            add_countries(ent.countries, country)
        flag = clean(row.get("vess_flag"))
        if flag:
            add_countries(ent.countries, flag)
        yield ent


def parse_sdn(paths: Dict[str, Path], source: ListSource) -> Iterable[Entity]:
    """Parse sdn.csv + alt.csv + add.csv."""
    return _parse(paths, source, "")


SOURCES = [
    ListSource(
        key="US_OFAC_SDN",
        name="OFAC Specially Designated Nationals (SDN) List",
        jurisdiction="US",
        list_type="SANCTIONS",
        urls={"main": _BASE + "sdn.csv", "alt": _BASE + "alt.csv", "add": _BASE + "add.csv"},
        parser=parse_sdn,
        homepage="https://ofac.treasury.gov/specially-designated-nationals-and-blocked-persons-list-sdn-human-readable-lists",
        license="public",
        groups=["core", "sanctions", "us"],
        max_age_days=3,
    ),
    ListSource(
        key="US_OFAC_CONS",
        name="OFAC Consolidated (non-SDN) Sanctions List",
        jurisdiction="US",
        list_type="SANCTIONS",
        urls={
            "main": _BASE + "cons_prim.csv",
            "alt": _BASE + "cons_alt.csv",
            "add": _BASE + "cons_add.csv",
        },
        parser=parse_sdn,
        homepage="https://ofac.treasury.gov/consolidated-sanctions-list-non-sdn-lists",
        license="public",
        groups=["core", "sanctions", "us"],
        max_age_days=7,
    ),
]
