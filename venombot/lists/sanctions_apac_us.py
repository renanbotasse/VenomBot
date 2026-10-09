"""Canada, Australia, New Zealand, Japan, Taiwan, South Africa, China and the US CSL.

Official asset-freeze lists outside the EU/UK/CH group plus two oddities:
China's counter-sanctions list (published by a sanctioning state against
others, hence COUNTER_SANCTIONS) and the non-Treasury parts of the US
Consolidated Screening List (export-control lists). Treasury/OFAC rows of the
CSL are deliberately skipped: ``ofac.py`` already parses them from OFAC's own
files with more fidelity.
"""

from __future__ import annotations

import csv
import datetime as _dt
import io
import re
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Dict, Iterator, List, Optional

from venombot.dates import parse_dates
from venombot.entities import Entity
from venombot.lists import ListSource
from venombot.lists._tables import read_xlsx, rows_as_dicts
from venombot.lists._util import add_dates, add_identifier, clean, read_text
from venombot.lists.sanctions_common import (
    add_name_auto,
    countries_native,
    excel_date,
    is_non_latin,
    local,
    partial_iso,
    uniq,
)


def _add_countries(target: List[str], text: Optional[str]) -> None:
    for c in countries_native(text):
        if c not in target:
            target.append(c)


def _today() -> str:
    return _dt.date.today().isoformat()


# --------------------------------------------------------------------------
# Canada: Special Economic Measures Act + Magnitsky (JVCFOR) consolidated
# --------------------------------------------------------------------------

_CA_ALIAS_SPLIT = re.compile(r"[;,\n]+")
_CA_LANG_PREFIX = re.compile(r"^\s*(?:Belarusian|Russian|Ukrainian|Persian|Arabic|Chinese|Burmese)\s*:\s*", re.I)


def parse_ca_sema(paths: Dict[str, Path], source: ListSource) -> Iterator[Entity]:
    """Parse sema-lmes.xml (<record> per listed party, bilingual tags).

    The regime is in ``Country-Pays`` (the 80 Magnitsky-law JVCFOR designations
    live in the same file). Quirk: for some Iranian records the
    ``ShipIMONumber`` tag holds the person's Persian-script name, so a
    non-numeric value is treated as an original-script name.
    """
    for _, el in ET.iterparse(str(paths["main"]), events=("end",)):
        if el.tag != "record":
            continue
        f = {local(c.tag).split("-")[0]: clean(c.text) for c in el}
        el.clear()
        regime = f.get("Country", "")
        regime_en = clean(regime.split("/")[0])
        item = f.get("Item", "")
        if not regime or not item:
            continue
        imo = f.get("ShipIMONumber", "")
        is_ship = imo.isdigit()
        entity_name = f.get("EntityOrShip", "")
        person = bool(f.get("LastName") or f.get("GivenName")) and not entity_name
        ent = Entity(
            source=source.key,
            source_id=re.sub(r"\W+", "-", f"{regime_en}-{f.get('Schedule', '')}-{item}").strip("-").lower(),
            schema="Vessel" if is_ship else ("Person" if person else "Organization"),
            list_type=source.list_type,
            listed_on=(parse_dates(f.get("DateOfListing", "")) or [""])[0],
            programs=[regime_en],
            url="https://www.international.gc.ca/world-monde/international_relations-relations_internationales/sanctions/consolidated-consolide.aspx?lang=eng",
        )
        if person:
            ent.add_name(" ".join(p for p in (f.get("GivenName"), f.get("LastName")) if p), "primary")
        else:
            ent.add_name(entity_name, "primary")
        for alias in _CA_ALIAS_SPLIT.split(f.get("Aliases", "")):
            add_name_auto(ent, _CA_LANG_PREFIX.sub("", alias), "alias")
        if imo and not is_ship:
            add_name_auto(ent, imo, "original")
        if is_ship:
            add_identifier(ent, "IMO", imo)
        elif f.get("DateOfBirthOrShipBuildDate"):
            add_dates(ent, f["DateOfBirthOrShipBuildDate"])
        if f.get("TitleOrShipType"):
            ent.extra["ship_type" if is_ship else "title"] = f["TitleOrShipType"]
        if f.get("Schedule"):
            ent.extra["schedule"] = f["Schedule"]
        if ent.names:
            yield ent


# --------------------------------------------------------------------------
# Australia: DFAT consolidated list (xlsx; one row per name, grouped by ref)
# --------------------------------------------------------------------------

_AU_SCHEMA = {"individual": "Person", "entity": "Organization", "vessel": "Vessel"}
_AU_PASSPORT = re.compile(r"Passport no:\s*(.*?)(?=\s+National identification no:|\s+Address:|\s+Listed on:|$)", re.I)


def parse_au_dfat(paths: Dict[str, Path], source: ListSource) -> Iterator[Entity]:
    """Parse the single sheet, merging "2", "2a", "2b" rows into one entity.

    The base reference carries the primary name; suffix rows are aliases or
    original-script names ("Name Type") that repeat the party's other columns.
    """
    groups: Dict[str, List[Dict[str, str]]] = {}
    for row in rows_as_dicts(read_xlsx(paths["main"])):
        m = re.match(r"^(\d+)([a-z]*)$", clean(row.get("Reference")))
        if m and clean(row.get("Name of Individual or Entity")):
            groups.setdefault(m.group(1), []).append(row)
    for base, rows in groups.items():
        primary = next((r for r in rows if clean(r.get("Name Type")).lower() == "primary name"), rows[0])
        ent = Entity(
            source=source.key,
            source_id=base,
            schema=_AU_SCHEMA.get(clean(primary.get("Type")).lower(), "Organization"),
            list_type=source.list_type,
            url="https://www.dfat.gov.au/international-relations/security/sanctions/consolidated-list",
        )
        ent.add_name(clean(primary.get("Name of Individual or Entity")), "primary")
        _au_primary_fields(ent, primary)
        for row in rows:
            if row is primary:
                continue
            name = clean(row.get("Name of Individual or Entity"))
            ntype = clean(row.get("Name Type")).lower()
            if ntype == "original script":
                add_name_auto(ent, name, "original")
            else:
                weak = clean(row.get("Alias Strength")).lower() == "weak"
                add_name_auto(ent, name, "weak" if weak else "alias")
        yield ent


def _au_primary_fields(ent: Entity, row: Dict[str, str]) -> None:
    """Fill party-level fields from the primary-name row."""
    add_dates(ent, row.get("Date of Birth"))
    pob = clean(row.get("Place of Birth"))
    if pob:
        ent.birth_places.append(pob)
        _add_countries(ent.countries, pob)
    _add_countries(ent.nationalities, row.get("Citizenship"))
    _add_countries(ent.countries, row.get("Address"))
    info = clean(row.get("Additional Information"))
    listing = clean(row.get("Listing Information"))
    ent.remarks = info
    ent.listed_on = (parse_dates(listing) or parse_dates(info) or [""])[0] if listing else ""
    m = _AU_PASSPORT.search(info)
    if m:
        for num in re.findall(r"number\s+([A-Z0-9]{5,})", m.group(1)):
            add_identifier(ent, "Passport", num)
    imo = clean(row.get("IMO Number"))
    if imo:
        add_identifier(ent, "IMO", imo)
    prog = clean(row.get("Committees"))
    instrument = clean(row.get("Instrument of Designation"))
    ent.programs = uniq([prog, instrument])
    ent.extra["measures"] = [k for k in ("Targeted Financial Sanction", "Travel Ban", "Arms Embargo", "Maritime Restriction")
                             if clean(row.get(k)) == "1"]
    cd = excel_date(row.get("Control Date"))
    if cd:
        ent.extra["control_date"] = cd


# --------------------------------------------------------------------------
# New Zealand: MFAT Russia Sanctions Register (xlsx, several sheets)
# --------------------------------------------------------------------------

def _nz_register_rows(path: Path) -> Iterator[Dict[str, str]]:
    """Yield data rows of the register sheet; a legend block precedes the header."""
    header: Optional[List[str]] = None
    for row in read_xlsx(path, "Russia Sanctions Register"):
        if header is None:
            if row and row[0] == "Type" and "Unique Identifier" in row:
                header = row
            continue
        if not any(row):
            continue
        yield {h: (row[i] if i < len(row) else "") for i, h in enumerate(header) if h}


def parse_nz_register(paths: Dict[str, Path], source: ListSource) -> Iterator[Entity]:
    """Parse the Register and Ships sheets of Russia-Sanctions-Register.xlsx.

    The file is 21 MB only because a trade-measures sheet is huge; read_xlsx
    streams just the sheets named here. "Asset" rows describe classes of
    property (e.g. "Russian government owned ships"), not parties, and are skipped.
    """
    for row in _nz_register_rows(paths["main"]):
        rtype = clean(row.get("Type"))
        uid = clean(row.get("Unique Identifier"))
        if rtype not in ("Individual", "Entity", "Bank") or not uid:
            continue
        if clean(row.get("Sanction Status")).lower() not in ("", "sanctioned"):
            continue
        person = rtype == "Individual"
        ent = Entity(
            source=source.key,
            source_id=uid,
            schema="Person" if person else "Organization",
            list_type=source.list_type,
            listed_on=excel_date(row.get("Date of Sanction")),
            programs=["NZ Russia Sanctions Act 2022"],
            remarks=clean(row.get("General Rationale for Sanction")),
            url="https://www.mfat.govt.nz/en/countries-and-regions/europe/ukraine/russian-invasion-of-ukraine/sanctions/russia-sanctions-register",
        )
        if person:
            full = " ".join(p for p in (clean(row.get("First name")), clean(row.get("Middle name(s)")),
                                        clean(row.get("Last name"))) if p)
            ent.add_name(full, "primary")
            first_last = " ".join(p for p in (clean(row.get("First name")), clean(row.get("Last name"))) if p)
            if first_last != full:
                ent.add_name(first_last, "alias")
        else:
            ent.add_name(clean(row.get("First name")) or clean(row.get("Name of Asset")), "primary")
        for alias in re.split(r"\s*;\s*|\n", row.get("Alias/Alternate Spellings", "")):
            add_name_auto(ent, alias, "alias")
        dob = excel_date(row.get("DOB"))
        if dob:
            ent.birth_dates.append(dob)
        elif clean(row.get("DOB")):
            add_dates(ent, row.get("DOB"))
        pob = clean(row.get("Place of Birth"))
        if pob:
            ent.birth_places.append(pob)
            _add_countries(ent.countries, pob)
        for col in ("Citizenship", "Citizenship 2", "Citizenship 3"):
            _add_countries(ent.nationalities, row.get(col))
        _add_countries(ent.countries, row.get("Address"))
        for num in re.split(r"\s*;\s*", row.get("Passport Number", "")):
            add_identifier(ent, "Passport", num)
        if clean(row.get("Title")):
            ent.extra["title"] = clean(row.get("Title"))
        if clean(row.get("Associates/Relatives")):
            ent.linked_to.append(clean(row.get("Associates/Relatives")))
        if ent.names:
            yield ent
    for row in rows_as_dicts(read_xlsx(paths["main"], "Ships")):
        uid = clean(row.get("Unique Identifier"))
        name = clean(row.get("Name of Ship as of Date of Sanction"))
        if not uid or not name or clean(row.get("Record Deleted Flag")).lower() in ("yes", "y", "true", "1"):
            continue
        ent = Entity(
            source=source.key,
            source_id=uid,
            schema="Vessel",
            list_type=source.list_type,
            listed_on=excel_date(row.get("Date of Sanction")),
            programs=["NZ Russia Sanctions Act 2022"],
            url="https://www.mfat.govt.nz/en/countries-and-regions/europe/ukraine/russian-invasion-of-ukraine/sanctions/russia-sanctions-register",
        )
        ent.add_name(name, "primary")
        for alias in re.split(r"\s*;\s*|\n", row.get("Alias/Alternate Names", "")):
            ent.add_name(clean(alias), "alias")
        add_identifier(ent, "IMO", row.get("IMO Number"))
        yield ent


# --------------------------------------------------------------------------
# Japan: MOF asset-freeze list and vessel IMO list
# --------------------------------------------------------------------------

_JP_YMD = re.compile(r"(\d{4})/(\d{1,2})/(\d{1,2})")
_JP_YEAR = re.compile(r"(\d{4})年")
_JP_NOTICE_DATE = re.compile(r"(\d{4})\.(\d{1,2})\.(\d{1,2})")
_JP_ORIGINAL = re.compile(r"\(original script:\s*([^)]+)\)")
_JP_DOC = re.compile(r"\b([A-Z]{0,3}\d{5,}[A-Z0-9]*)\b")


def _jp_dates(text: str) -> List[str]:
    out = []
    for y, m, d in _JP_YMD.findall(text or ""):
        out.append(partial_iso(y, m, d))
    consumed = _JP_YMD.sub(" ", text or "")
    for y in _JP_YEAR.findall(consumed):
        out.append(partial_iso(y))
    return uniq(out)


def _split(cell: str) -> List[str]:
    return [clean(x) for x in re.split(r"\s*;\s*", cell or "") if clean(x)]


def parse_jp_mof(paths: Dict[str, Path], source: ListSource) -> Iterator[Entity]:
    """Parse the MOF asset-freeze CSV (Japanese and English columns by position).

    Columns are addressed by position because header text is Japanese. English
    columns drive matching; Japanese names are kept as aliases, and the
    "(original script: ...)" note inside その他の情報 becomes an original-script name.
    """
    text = read_text(paths["main"])
    for i, row in enumerate(csv.reader(io.StringIO(text))):
        if i == 0 or len(row) < 31:
            continue
        row = row + [""] * (32 - len(row))
        cat, num, notice, _, kind = (clean(c) for c in row[:5])
        if not num:
            continue
        person = kind == "個人"
        ent = Entity(
            source=source.key,
            source_id=num,
            schema="Person" if person else "Organization",
            list_type=source.list_type,
            programs=[f"JP-asset-freeze-{cat}"],
            url="https://www.mof.go.jp/policy/international_policy/gaitame_kawase/gaitame/economic_sanctions/list.html",
        )
        m = _JP_NOTICE_DATE.search(notice)
        if m:
            ent.listed_on = partial_iso(*m.groups())
        add_name_auto(ent, row[6], "primary")
        add_name_auto(ent, row[5], "alias", "ja")
        for col, kindname, lang in ((7, "alias", "ja"), (8, "alias", ""), (9, "alias", "ja"), (10, "alias", ""),
                                    (11, "weak", "ja"), (12, "weak", "")):
            for n in _split(row[col]):
                add_name_auto(ent, n, kindname, lang)
        other = clean(row[30])
        for m2 in _JP_ORIGINAL.finditer(other):
            add_name_auto(ent, m2.group(1), "original")
        ent.remarks = clean(_JP_ORIGINAL.sub("", other))
        for d in _jp_dates(row[17]):
            if d not in ent.birth_dates:
                ent.birth_dates.append(d)
        if clean(row[19]):
            ent.birth_places.append(clean(row[19]))
        _add_countries(ent.countries, row[19])
        _add_countries(ent.nationalities, row[21])
        _add_countries(ent.countries, row[26])
        for doc in _JP_DOC.findall(row[22]):
            add_identifier(ent, "Passport", doc)
        for doc in _JP_DOC.findall(row[23]):
            add_identifier(ent, "ID number", doc)
        for title in _split(row[14]) + _split(row[16]):
            ent.extra.setdefault("titles", []).append(title)
        un = clean(row[28])
        if un:
            add_identifier(ent, "UN reference number", un)
        if ent.names:
            yield ent


def parse_jp_vessels(paths: Dict[str, Path], source: ListSource) -> Iterator[Entity]:
    """Parse the IMO-only vessel list (import/export bans).

    The file has no names, so each vessel is named "IMO <number>" to stay
    findable by a typed IMO string; the IMO is also stored as an identifier.
    """
    text = read_text(paths["main"])
    for i, row in enumerate(csv.reader(io.StringIO(text))):
        if i == 0 or len(row) < 5 or not clean(row[4]).isdigit():
            continue
        imo = clean(row[4])
        ent = Entity(
            source=source.key,
            source_id=imo,
            schema="Vessel",
            list_type=source.list_type,
            programs=[f"JP-vessel-ban-{clean(row[0])}"],
            url="https://www.mof.go.jp/policy/international_policy/gaitame_kawase/gaitame/economic_sanctions/list.html",
        )
        m = _JP_NOTICE_DATE.search(clean(row[2]))
        if m:
            ent.listed_on = partial_iso(*m.groups())
        ent.add_name(f"IMO {imo}", "primary")
        add_identifier(ent, "IMO", imo)
        yield ent


# --------------------------------------------------------------------------
# Taiwan: SHTC entity list
# --------------------------------------------------------------------------

def parse_tw_shtc(paths: Dict[str, Path], source: ListSource) -> Iterator[Entity]:
    """Parse SHTCEntityList.csv (item, name, alias, country code, address, ID number).

    Names written "SURNAME, Given" are individuals; everything else is left as
    Unknown because the file has no type column.
    """
    text = read_text(paths["main"])
    for i, row in enumerate(csv.reader(io.StringIO(text))):
        if i == 0 or len(row) < 6 or not clean(row[1]):
            continue
        name = clean(row[1])
        ent = Entity(
            source=source.key,
            source_id=clean(row[0]) or str(i),
            schema="Person" if "," in name else "Unknown",
            list_type=source.list_type,
            programs=["TW-SHTC-entity-list"],
            url="https://www.trade.gov.tw/english/Pages/List.aspx?nodeid=298",
        )
        ent.add_name(name, "primary")
        for alias in _split(row[2]):
            add_name_auto(ent, alias, "alias")
        cc = clean(row[3]).lower()
        if cc:
            ent.countries.append(cc)
        for num in _split(row[5]):
            add_identifier(ent, "ID number", num)
        if clean(row[4]):
            ent.extra["address"] = clean(row[4])
        yield ent


# --------------------------------------------------------------------------
# South Africa: FIC targeted financial sanctions list (UN mirror)
# --------------------------------------------------------------------------

def parse_za_fic(paths: Dict[str, Path], source: ListSource) -> Iterator[Entity]:
    """Parse the ADO.NET DataSet export (<Table> individuals, <Table1> entities).

    The endpoint answers GET with an HTML page and only returns XML to an
    empty-body POST, which the generic downloader cannot send; this parser
    raises if it is handed that HTML page instead of silently yielding nothing.
    """
    raw = read_text(paths["main"])
    if "<NewDataSet" not in raw:
        raise ValueError("ZA FIC file is not the DataSet XML (the endpoint requires HTTP POST)")
    root = ET.fromstring(raw.lstrip("﻿"))
    for rec in root:
        f: Dict[str, List[str]] = {}
        for c in rec:
            f.setdefault(local(c.tag), []).append(clean(c.text))
        ref = (f.get("ReferenceNumber") or [""])[0]
        person = "IndividualID" in f
        sid = (f.get("IndividualID") or f.get("EntityID") or [""])[0]
        name = (f.get("FullName") or f.get("FirstName") or [""])[0]
        if not sid or not name:
            continue
        ent = Entity(
            source=source.key,
            source_id=ref or sid,
            schema="Person" if person else "Organization",
            list_type=source.list_type,
            remarks=(f.get("Comments") or [""])[0],
            programs=["UN-SC-via-ZA-FIC"],
            url="https://tfs.fic.gov.za/",
        )
        ent.listed_on = (parse_dates((f.get("ListedOn") or [""])[0]) or [""])[0]
        ent.add_name(name, "primary")
        for a in f.get("IndividualAlias", []) + f.get("EntityAlias", []):
            # One cell can hold "Good, X, Low, Y" or "a.k.a., X, a.k.a., Y": split before each label.
            for chunk in re.split(r"(?:^|,\s*)(?=(?:good|low|a\.k\.a\.),)", a, flags=re.I):
                m = re.match(r"^(good|low|a\.k\.a\.),\s*(.*)$", chunk.strip(), re.I)
                label, value = (m.group(1).lower(), m.group(2)) if m else ("", chunk)
                add_name_auto(ent, value.strip(" ,"), "weak" if label == "low" else "alias")
        for d in f.get("IndividualDateOfBirth", []):
            add_dates(ent, d)
        for nat in f.get("Nationality", []):
            for part in nat.split(","):
                _add_countries(ent.nationalities, part)
        for p in f.get("IndividualPlaceOfBirth", []):
            ent.birth_places.append(p)
        for doc in f.get("IndividualDocument", []):
            toks = [t.strip() for t in doc.split(",")]
            for k in range(0, len(toks) - 1, 2):
                add_identifier(ent, toks[k], toks[k + 1])
        for a in f.get("IndividualAddress", []) + f.get("EntityAddress", []):
            _add_countries(ent.countries, a)
        if f.get("Designation"):
            ent.extra["designation"] = f["Designation"][0]
        yield ent


# --------------------------------------------------------------------------
# China: counter-sanctions / unreliable entity / export-control targets
# --------------------------------------------------------------------------

_CN_DATE = re.compile(r"^(\d{1,2})\.(\d{1,2})\.(\d{2,4})$")


def _cn_date(text: str) -> str:
    m = _CN_DATE.match(clean(text))
    if not m:
        return ""
    d, mo, y = m.groups()
    if len(y) == 2:
        y = "20" + y
    return partial_iso(y, mo, d)


def parse_cn_counter(paths: Dict[str, Path], source: ListSource) -> Iterator[Entity]:
    """Parse the community-curated CSV of MFA/MOFCOM/TAO measures.

    Everything here is typed COUNTER_SANCTIONS: these are measures imposed by
    China against foreign firms and people, relevant for China-facing
    compliance rather than AML risk. Entries with an end date in the past are
    skipped; suspended measures are kept with their status in ``extra``.
    """
    text = read_text(paths["main"])
    today = _today()
    merged: Dict[str, Entity] = {}
    for i, row in enumerate(csv.DictReader(io.StringIO(text))):
        name = clean(row.get("Name"))
        if not name:
            continue
        end = _cn_date(row.get("End date", ""))
        if end and end < today:
            continue
        kind = clean(row.get("Type"))
        ent = Entity(
            source=source.key,
            source_id=clean(row.get("QID")) or f"cn-{i}",
            schema="Person" if kind == "Person" else "Organization",
            list_type=source.list_type,
            listed_on=_cn_date(row.get("Date", "")),
            url=clean(row.get("Source URL")),
            programs=uniq([clean(row.get("List")), clean(row.get("Body")), clean(row.get("Topics"))]),
            remarks=clean(row.get("Summary")),
        )
        ent.add_name(name, "primary")
        add_name_auto(ent, row.get("Chinese name"), "original", "zh")
        for alias in re.split(r"\s*;\s*|\n", row.get("Alias", "")):
            add_name_auto(ent, alias, "alias")
        cc = clean(row.get("Country")).lower()
        if cc:
            (ent.nationalities if kind == "Person" else ent.countries).append(cc)
        if clean(row.get("Address")):
            ent.extra["address"] = clean(row.get("Address"))
        status = clean(row.get("Current status (source)"))
        if status:
            ent.extra["current_status"] = status
        if clean(row.get("Notice ID")):
            ent.extra["notice_id"] = clean(row.get("Notice ID"))
        if clean(row.get("QID")):
            add_identifier(ent, "Wikidata", row.get("QID"))
        # The same party appears once per announcement; merge so each has one record.
        prev = merged.get(ent.source_id)
        if prev is None:
            merged[ent.source_id] = ent
        else:
            prev.programs = uniq(prev.programs + ent.programs)
            for n in ent.names:
                prev.add_name(n.value, n.kind, n.lang)
            prev.listed_on = min(d for d in (prev.listed_on, ent.listed_on) if d) if (prev.listed_on or ent.listed_on) else ""
    yield from merged.values()


# --------------------------------------------------------------------------
# US Consolidated Screening List: non-Treasury (export-control) lists
# --------------------------------------------------------------------------

# CSL "source" prefix -> (list_type, programme label). Treasury lists are
# absent on purpose (see module docstring).
_CSL_LISTS = {
    "Entity List (EL)": ("EXPORT_CONTROL", "BIS Entity List"),
    "Unverified List (UVL)": ("EXPORT_CONTROL", "BIS Unverified List"),
    "Military End User (MEU) List": ("EXPORT_CONTROL", "BIS Military End-User List"),
    "Denied Persons List (DPL)": ("EXPORT_CONTROL", "BIS Denied Persons List"),
    "ITAR Debarred (DTC)": ("EXPORT_CONTROL", "State Dept ITAR Debarred"),
    "Nonproliferation Sanctions (ACN)": ("SANCTIONS", "State Dept Nonproliferation Sanctions"),
}
_CSL_SCHEMA = {"individual": "Person", "entity": "Organization", "vessel": "Vessel", "aircraft": "Aircraft"}


def parse_us_csl(paths: Dict[str, Path], source: ListSource) -> Iterator[Entity]:
    """Stream consolidated.csv, keeping BIS and State Department lists only.

    The Treasury rows (SDN, SSI, CAPTA, CMIC, NS-MBS, PLC) are OFAC data that
    ``ofac.py`` parses from the OFAC files; emitting them here would double
    every OFAC hit. Denied-person orders whose end date has passed are dropped.
    """
    csv_field_limit = 1 << 30
    csv.field_size_limit(csv_field_limit)
    today = _today()
    with open(paths["main"], "r", encoding="utf-8-sig", newline="") as fh:
        for row in csv.DictReader(fh):
            src = clean(row.get("source"))
            meta = next((v for k, v in _CSL_LISTS.items() if src.startswith(k)), None)
            if meta is None:
                continue
            end = clean(row.get("end_date"))[:10]
            if end and end < today:
                continue
            name = clean(row.get("name"))
            sid = clean(row.get("_id"))
            if not name or not sid:
                continue
            ltype, label = meta
            ent = Entity(
                source=source.key,
                source_id=sid,
                schema=_CSL_SCHEMA.get(clean(row.get("type")).lower(), "Unknown"),
                list_type=ltype,
                listed_on=clean(row.get("start_date"))[:10],
                programs=uniq([label] + [p.strip() for p in clean(row.get("programs")).split(";")]),
                remarks=clean(row.get("remarks")),
                url=clean(row.get("source_list_url")),
            )
            ent.add_name(name, "primary")
            for alias in re.split(r"\s*;\s*", row.get("alt_names", "") or ""):
                add_name_auto(ent, alias, "alias")
            add_dates(ent, (row.get("dates_of_birth") or "").replace(";", ","))
            for p in re.split(r"\s*;\s*", row.get("places_of_birth", "") or ""):
                if clean(p):
                    ent.birth_places.append(clean(p))
            for n in re.split(r"\s*;\s*", row.get("nationalities", "") or "") + re.split(r"\s*;\s*", row.get("citizenships", "") or ""):
                _add_countries(ent.nationalities, n)
            for addr in re.split(r"\s*;\s+", row.get("addresses", "") or ""):
                m = re.search(r",\s*([A-Z]{2})\s*$", addr.strip())
                if m and m.group(1).lower() not in ent.countries:
                    ent.countries.append(m.group(1).lower())
            for ident in re.split(r"\s*;\s*", row.get("ids", "") or ""):
                parts = [x.strip() for x in ident.split(",")]
                if len(parts) >= 2:
                    add_identifier(ent, parts[0], parts[1], parts[2].lower() if len(parts) > 2 and len(parts[2]) == 2 else "")
            for key in ("federal_register_notice", "license_requirement", "license_policy"):
                if clean(row.get(key)):
                    ent.extra[key] = clean(row.get(key))
            yield ent


# --------------------------------------------------------------------------

_JP_PAGE = "https://www.mof.go.jp/policy/international_policy/gaitame_kawase/gaitame/economic_sanctions/"

SOURCES = [
    ListSource(
        key="CA_SEMA_JVCFOR",
        name="Canada - SEMA and Justice for Victims of Corrupt Foreign Officials (Magnitsky) consolidated list",
        jurisdiction="CA",
        list_type="SANCTIONS",
        urls={"main": "https://www.international.gc.ca/world-monde/assets/office_docs/international_relations-relations_internationales/sanctions/sema-lmes.xml"},
        parser=parse_ca_sema,
        homepage="https://www.international.gc.ca/world-monde/international_relations-relations_internationales/sanctions/consolidated-consolide.aspx?lang=eng",
        license="OGL",
        groups=["core", "sanctions", "ca"],
        max_age_days=7,
        timeout=240,
        notes="Open Government Licence - Canada. Same file as the older CA_SEMA entry; JVCFOR (Magnitsky) rows "
              "are included, no separate file needed.",
    ),
    ListSource(
        key="AU_DFAT_CONSOLIDATED_XLSX",
        name="Australia - DFAT Consolidated Sanctions List (xlsx)",
        jurisdiction="AU",
        list_type="SANCTIONS",
        urls={"main": "https://www.dfat.gov.au/sites/default/files/Australian_Sanctions_Consolidated_List.xlsx"},
        parser=parse_au_dfat,
        homepage="https://www.dfat.gov.au/international-relations/security/sanctions/consolidated-list",
        license="CC-BY",
        groups=["core", "sanctions", "au"],
        max_age_days=7,
        notes="CC BY 4.0 per DFAT site default (verify). Rows '2', '2a', '2b' are merged per base reference.",
    ),
    ListSource(
        key="NZ_MFAT_RUSSIA_REGISTER",
        name="New Zealand - MFAT Russia Sanctions Register",
        jurisdiction="NZ",
        list_type="SANCTIONS",
        urls={"main": "https://www.mfat.govt.nz/assets/Countries-and-Regions/Europe/Ukraine/Russia-Sanctions-Register.xlsx"},
        parser=parse_nz_register,
        homepage="https://www.mfat.govt.nz/en/countries-and-regions/europe/ukraine/russian-invasion-of-ukraine/sanctions/russia-sanctions-register",
        license="CC-BY",
        groups=["core", "sanctions", "nz"],
        max_age_days=14,
        timeout=300,
        notes="21.6 MB download (a trade-measures sheet is huge); only the register and ships sheets are parsed. "
              "NZ Crown copyright, CC BY 4.0 (verify).",
    ),
    ListSource(
        key="JP_MOF_ASSET_FREEZE_CSV",
        name="Japan - MOF asset-freeze target list",
        jurisdiction="JP",
        list_type="SANCTIONS",
        urls={"main": _JP_PAGE + "shisantouketsu20261009.csv"},
        parser=parse_jp_mof,
        homepage=_JP_PAGE + "list.html",
        license="CC-BY",
        groups=["sanctions", "jp"],
        max_age_days=7,
        notes="The file name embeds the update date and changes on every update: resolve the current "
              "href './shisantouketsu(\\d{8})\\.csv' from list.html and re-point this URL. Government of "
              "Japan Standard Terms of Use 2.0 (CC BY 4.0 compatible).",
    ),
    ListSource(
        key="JP_MOF_EXPORT_BAN_VESSELS",
        name="Japan - MOF import/export ban vessels (IMO list)",
        jurisdiction="JP",
        list_type="SANCTIONS",
        urls={"main": _JP_PAGE + "ekimukisei_IMOnumber_20261002.csv"},
        parser=parse_jp_vessels,
        homepage=_JP_PAGE + "list.html",
        license="terms",
        groups=["sanctions", "jp", "vessels"],
        max_age_days=14,
        notes="IMO numbers only. Dated file name changes on each update (same discovery as the asset-freeze CSV).",
    ),
    ListSource(
        key="TW_SHTC_ENTITY_LIST",
        name="Taiwan - SHTC entity list (strategic high-tech commodities)",
        jurisdiction="TW",
        list_type="EXPORT_CONTROL",
        urls={"main": "https://publicinfo.trade.gov.tw/icp/Download.action?file=ZgPKOWuRLCD%2B8S4%2FZhAeuw%3D%3D"},
        parser=parse_tw_shtc,
        homepage="https://www.trade.gov.tw/english/Pages/List.aspx?nodeid=298",
        license="terms",
        groups=["sanctions", "tw", "export_control"],
        max_age_days=30,
        notes="Aggregates UN/US/EU/Japan terror and proliferation entities adopted by Taiwan; no explicit licence.",
    ),
    ListSource(
        key="CN_COUNTER_SANCTIONS_RESEARCH",
        name="China - counter-sanctions / unreliable entity / export-control targets (OpenSanctions-curated CSV)",
        jurisdiction="CN",
        list_type="COUNTER_SANCTIONS",
        urls={"main": "https://raw.githubusercontent.com/opensanctions/opensanctions/refs/heads/main/datasets/cn/sanctions/sanctions.csv"},
        parser=parse_cn_counter,
        homepage="https://github.com/opensanctions/opensanctions/tree/main/datasets/cn/sanctions",
        license="CC-BY-NC",
        groups=["sanctions", "cn", "counter"],
        max_age_days=30,
        notes="Hand-curated from MFA/MOFCOM/TAO announcements (no official consolidated file exists); data CC BY-NC 4.0.",
    ),
    ListSource(
        key="US_CSL_DOWNLOAD",
        name="US Consolidated Screening List - BIS and State Department lists",
        jurisdiction="US",
        list_type="EXPORT_CONTROL",
        urls={"main": "https://data.trade.gov/downloadable_consolidated_screening_list/v1/consolidated.csv"},
        parser=parse_us_csl,
        homepage="https://www.trade.gov/consolidated-screening-list",
        license="public",
        groups=["core", "sanctions", "us", "export_control"],
        max_age_days=3,
        timeout=300,
        notes="16.8 MB; HEAD returns 404, GET works. Emits Entity List, UVL, MEU, Denied Persons (unexpired), "
              "ITAR Debarred and ACN rows only; Treasury rows are handled by ofac.py.",
    ),
]

# Parsers implemented and tested but not registered: the generic downloader
# cannot issue the required HTTP POST.
DISABLED_SOURCES = [
    ListSource(
        key="ZA_FIC_TFS_LIST",
        name="South Africa - FIC targeted financial sanctions list (UN mirror)",
        jurisdiction="ZA",
        list_type="SANCTIONS",
        urls={"main": "https://tfs.fic.gov.za/Pages/TFSListDownload?fileType=xml"},
        parser=parse_za_fic, post_data=b"",
        homepage="https://tfs.fic.gov.za/",
        license="terms",
        groups=["sanctions", "za"],
        max_age_days=7,
        notes="Answers only an empty-body HTTP POST (ListSource.post_data); GET returns an HTML page. "
              "Mirrors the UN list.",
    ),
]
# POST support exists now (ListSource.post_data), so the formerly disabled source is live.
SOURCES.extend(DISABLED_SOURCES)
