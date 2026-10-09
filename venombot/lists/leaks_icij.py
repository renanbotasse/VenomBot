"""ICIJ Offshore Leaks database and FinCEN Files (context, not findings).

Appearing in a leak is NOT evidence of wrongdoing: offshore structures are
lawful in most uses and the databases include service providers, nominees and
ordinary clients. Entities are emitted as CORPORATE (severity INFO) so a hit
tells an analyst "look at the ownership structure", nothing more.
Licence: ODbL (database) + CC BY-SA (contents); cite ICIJ.
"""

from __future__ import annotations

import csv
import io
import zipfile
from collections import defaultdict
from pathlib import Path
from typing import Dict, Iterator, List, Optional, Tuple

from venombot.countries import to_iso2
from venombot.entities import Entity
from venombot.lists import ListSource
from venombot.lists._util import add_identifier, clean

csv.field_size_limit(1 << 30)

LEAK_NOTE = ("Context only: appearing in an ICIJ leak is not evidence of wrongdoing "
             "(offshore structures are lawful in most uses; nominees and intermediaries are included).")
# Relationship types worth carrying into linked_to; address/same_as rows would
# link thousands of unrelated nodes through one shared address.
LINK_TYPES = {"officer_of", "intermediary_of", "underlying", "shareholder_of", "beneficiary_of",
              "director_of", "secretary_of", "protector_of", "connected_to", "similar", "same_name_as",
              "same_company_as", "probably_same_officer_as", "same_intermediary_as"}
MAX_LINKS = 25


def _member(z: zipfile.ZipFile, suffix: str) -> Optional[str]:
    """Zip member by file name, ignoring __MACOSX junk and directories."""
    for n in z.namelist():
        if n.startswith("__MACOSX") or n.endswith("/"):
            continue
        if n.lower().endswith(suffix):
            return n
    return None


def _rows(z: zipfile.ZipFile, member: str) -> Iterator[Dict[str, str]]:
    """Stream CSV rows of a zip member without loading it."""
    with z.open(member) as raw:
        yield from csv.DictReader(io.TextIOWrapper(raw, encoding="utf-8-sig", newline=""))


def _countries(codes: str) -> List[str]:
    out: List[str] = []
    for c in (codes or "").split(";"):
        iso = to_iso2(c.strip())
        if iso and iso not in out:
            out.append(iso)
    return out


def parse_offshore_leaks(paths: Dict[str, Path], source: ListSource) -> Iterator[Entity]:
    """Entities (companies/trusts) and officers; ``linked_to`` from relationships.csv.

    Memory: two passes. Pass 1 keeps node_id -> name for entities, officers and
    intermediaries (about 1.6M short strings, roughly 250 MB). Pass 2 streams
    relationships.csv keeping at most MAX_LINKS neighbours per emitted node.
    Addresses are not emitted (a shared address is a weak signal).
    Intermediaries are emitted as Organization/Person too, flagged in remarks.
    """
    with zipfile.ZipFile(paths["main"]) as z:
        files = {k: _member(z, f"nodes-{k}.csv") for k in ("entities", "officers", "intermediaries")}
        rel = _member(z, "relationships.csv")
        names: Dict[str, str] = {}
        kinds: Dict[str, str] = {}
        for kind, member in files.items():
            if not member:
                continue
            for r in _rows(z, member):
                nid = r.get("node_id", "")
                if nid and r.get("name"):
                    names[nid] = clean(r["name"])
                    kinds[nid] = kind
        links: Dict[str, List[str]] = defaultdict(list)
        if rel:
            for r in _rows(z, rel):
                if r.get("rel_type", "").strip().lower() not in LINK_TYPES:
                    continue
                a, b = r.get("node_id_start", ""), r.get("node_id_end", "")
                if a in names and b in names:
                    if len(links[a]) < MAX_LINKS and names[b] not in links[a]:
                        links[a].append(names[b])
                    if len(links[b]) < MAX_LINKS and names[a] not in links[b]:
                        links[b].append(names[a])
        for kind, member in files.items():
            if not member:
                continue
            for r in _rows(z, member):
                nid, name = r.get("node_id", ""), clean(r.get("name"))
                if not nid or not name:
                    continue
                is_entity = kind == "entities"
                ent = Entity(source=source.key, source_id=nid,
                             schema="Organization" if is_entity or kind == "intermediaries" else "Person",
                             list_type=source.list_type, linked_to=links.get(nid, []),
                             url=f"https://offshoreleaks.icij.org/nodes/{nid}")
                ent.add_name(name, "primary")
                ent.add_name(clean(r.get("original_name")), "alias")
                ent.add_name(clean(r.get("former_name")), "alias")
                ent.countries = _countries(r.get("country_codes", ""))
                leak = clean(r.get("sourceID"))
                if leak:
                    ent.datasets.append(leak)
                    ent.programs.append(leak)
                role = {"entities": "offshore entity", "officers": "officer / owner / nominee",
                        "intermediaries": "intermediary (service provider)"}[kind]
                bits = [role]
                if is_entity:
                    for fld in ("jurisdiction_description", "company_type", "status", "service_provider"):
                        if clean(r.get(fld)):
                            bits.append(f"{fld}: {clean(r[fld])}")
                    add_identifier(ent, "ICIJ internal id", r.get("internal_id"))
                    add_identifier(ent, "Registration number", r.get("ibcRUC"))
                bits.append(LEAK_NOTE)
                ent.remarks = "; ".join(bits)
                yield ent


def parse_fincen(paths: Dict[str, Path], source: ListSource) -> Iterator[Entity]:
    """Banks named in FinCEN Files SARs (bank-level only; no individuals).

    One Organization per bank slug; ``linked_to`` lists correspondent banks
    seen with it in the same SAR. Being named in a SAR is not a finding: SARs
    are filed on suspicion and unproven.
    """
    banks: Dict[str, Entity] = {}

    def bank(slug: str, name: str, iso: str) -> Optional[Entity]:
        slug = clean(slug)
        if not slug or not clean(name):
            return None
        ent = banks.get(slug)
        if ent is None:
            ent = Entity(source=source.key, source_id=slug, schema="Organization", list_type=source.list_type,
                         remarks="Bank named in FinCEN Files suspicious activity reports. " + LEAK_NOTE,
                         url="https://www.icij.org/investigations/fincen-files/")
            ent.add_name(clean(name), "primary")
            banks[slug] = ent
        c = to_iso2(iso)
        if c and c not in ent.countries:
            ent.countries.append(c)
        return ent

    def link(a: Entity, b: Entity) -> None:
        if a is b:
            return
        for x, y in ((a, b), (b, a)):
            if y.caption not in x.linked_to and len(x.linked_to) < MAX_LINKS:
                x.linked_to.append(y.caption)

    with zipfile.ZipFile(paths["main"]) as z:
        conn = _member(z, "bank_connections.csv")
        trans = _member(z, "transactions_map.csv")
        if conn:
            for r in _rows(z, conn):
                a = bank(r.get("filer_org_name_id", ""), r.get("filer_org_name", ""), "")
                b = bank(r.get("entity_b_id", ""), r.get("entity_b", ""), r.get("entity_b_iso_code", ""))
                if a and b:
                    link(a, b)
        if trans:
            for r in _rows(z, trans):
                a = bank(r.get("filer_org_name_id", ""), r.get("filer_org_name", ""), "")
                o = bank(r.get("originator_bank_id", ""), r.get("originator_bank", ""), r.get("originator_iso", ""))
                b = bank(r.get("beneficiary_bank_id", ""), r.get("beneficiary_bank", ""), r.get("beneficiary_iso", ""))
                for other in (o, b):
                    if a and other:
                        link(a, other)
    return iter(banks.values())


SOURCES: List[ListSource] = [
    ListSource(
        key="ICIJ_OFFSHORE_LEAKS_CSV", name="ICIJ Offshore Leaks Database (full CSV export)", jurisdiction="GLOBAL",
        list_type="CORPORATE", urls={"main": "https://offshoreleaks-data.icij.org/offshoreleaks/csv/full-oldb.LATEST.zip"},
        parser=parse_offshore_leaks, homepage="https://offshoreleaks.icij.org/", license="terms",
        groups=["corporate", "leaks", "global"], max_age_days=30, timeout=900,
        notes="ODbL + CC BY-SA, cite ICIJ. 72 MB zip, ~1.6M nodes; streamed, two passes. " + LEAK_NOTE),
    ListSource(
        key="ICIJ_FINCEN_FILES", name="ICIJ FinCEN Files (SAR bank connections)", jurisdiction="GLOBAL",
        list_type="CORPORATE", urls={"main": "https://media.icij.org/uploads/2020/09/download_data_fincen_files.zip"},
        parser=parse_fincen, homepage="https://www.icij.org/investigations/fincen-files/", license="terms",
        groups=["corporate", "leaks", "global"], max_age_days=365,
        notes="Static 2020 dataset, banks only; attribution to ICIJ requested. " + LEAK_NOTE),
]
