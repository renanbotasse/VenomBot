"""OpenSanctions datasets (FollowTheMoney ``targets.nested.json``).

OpenSanctions republishes ~440 source datasets — sanctions, PEPs, wanted,
debarment, regulatory and crime lists from >100 jurisdictions — in one
uniform format. One parser here therefore unlocks most of the world's public
screening data, including lists whose originals are PDFs or Arabic-only HTML.

Licence: CC BY-NC 4.0 — free for non-commercial use; commercial use needs a
licence from OpenSanctions. Every source here is tagged ``CC-BY-NC`` and kept
out of the default "core" group so nobody pulls it in by accident.

The full dataset list (~440 entries) is not committed: it changes daily and
is cached locally by ``venombot lists --refresh-opensanctions`` (written to
``$VENOMBOT_DATA/opensanctions_index.json``, default ``venombot_data/``).
Until that is run, a small built-in seed (the OpenSanctions collections plus
the GCC lists) is registered so ``update`` works out of the box.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional

from venombot.countries import to_iso2
from venombot.dates import parse_dates
from venombot.entities import Entity, Identifier, Position
from venombot.lists import ListSource

INDEX_URL = "https://data.opensanctions.org/datasets/latest/index.json"

def cache_file() -> Path:
    """Local cache of the live dataset index (never committed)."""
    return Path(os.environ.get("VENOMBOT_DATA", "venombot_data")) / "opensanctions_index.json"


def _row(name, title, kind, tags, targets, country="", official=True, publisher="", frequency="daily"):
    return {"name": name, "title": title, "type": kind, "tags": tags, "targets": targets,
            "country": country, "official": official, "publisher": publisher, "frequency": frequency}


# Minimal offline seed: the aggregate collections and the GCC/MENA lists.
SEED = [
    _row("sanctions", "OpenSanctions Global Sanctions", "collection", [], 72508),
    _row("us_sanctions", "United States Sanctions", "collection", [], 27124),
    _row("eu_sanctions", "European Union Sanctions", "collection", [], 9684),
    _row("peps", "Politically Exposed Persons", "collection", [], 716929),
    _row("wanted", "Wanted Persons", "collection", [], 65203),
    _row("crime", "Crime and Organised Crime", "collection", [], 251127),
    _row("interpol_red_notices", "INTERPOL Red Notices", "source", ["list.wanted"], 6369, "", True, "Interpol"),
    _row("debarment", "Debarment and Exclusion Lists", "collection", [], 197960),
    _row("enforcement", "Enforcement Actions", "collection", [], 7885),
    _row("regulatory", "Regulatory Warnings and Actions", "collection", [], 163240),
    _row("ae_local_terrorists", "United Arab Emirates Local Terrorist List", "source", ["list.sanction"], 308, "ae", publisher="EOCN"),
    _row("qa_nctc_sanctions", "Qatar Sanction List", "source", ["list.sanction"], 852, "qa", publisher="NCTC"),
    _row("sa_pcct_terrorism_list", "Saudi Arabia Terrorism List", "source", ["list.sanction"], 335, "sa"),
    _row("jo_sanctions", "Jordan Sanctions List", "source", ["list.sanction"], 46, "jo"),
    _row("om_parliament", "Oman Majlis Ash'shura", "source", ["list.pep"], 366, "om", frequency="weekly"),
    _row("bh_nuwab", "Bahrain Council of Representatives", "source", ["list.pep"], 178, "bh", frequency="weekly"),
    _row("bh_shura_council", "Bahrain Shura Council", "source", ["list.pep"], 104, "bh", frequency="weekly"),
]
_URL = "https://data.opensanctions.org/datasets/latest/{name}/targets.nested.json"

_SCHEMA = {
    "Person": "Person",
    "Organization": "Organization",
    "Company": "Organization",
    "LegalEntity": "Organization",
    "PublicBody": "Organization",
    "Vessel": "Vessel",
    "Airplane": "Aircraft",
}

# Most severe first: an entity tagged both sanction and role.pep is screened
# as a sanctions target.
_TOPIC_ORDER = [
    ("sanction.counter", "COUNTER_SANCTIONS"),
    ("sanction.linked", "SANCTIONS_LINKED"),
    ("sanction", "SANCTIONS"),
    ("crime.terror", "TERRORISM"),
    ("wanted", "WANTED"),
    ("export.control", "EXPORT_CONTROL"),
    ("crime", "CRIME"),
    ("debarment", "DEBARMENT"),
    ("corp.disqual", "ENFORCEMENT"),
    ("reg.action", "ENFORCEMENT"),
    ("reg.warn", "ENFORCEMENT"),
    ("role.pep", "PEP"),
    ("role.rca", "PEP_RCA"),
]
_SEVERITY_RANK = [
    "SANCTIONS", "TERRORISM", "WANTED", "EXPORT_CONTROL", "CRIME", "SANCTIONS_LINKED",
    "DEBARMENT", "ENFORCEMENT", "PEP", "PEP_RCA", "COUNTER_SANCTIONS", "OTHER",
]

_TAG_TYPES = [
    ("list.sanction.counter", "COUNTER_SANCTIONS"),
    ("list.terror", "TERRORISM"),
    ("list.sanction", "SANCTIONS"),
    ("list.wanted", "WANTED"),
    ("list.export", "EXPORT_CONTROL"),
    ("list.debarment", "DEBARMENT"),
    ("list.enforcement", "ENFORCEMENT"),
    ("list.regulatory", "ENFORCEMENT"),
    ("list.crime", "CRIME"),
    ("list.risk", "CRIME"),
    ("list.pep", "PEP"),
]

_COLLECTION_TYPES = {
    "sanctions": "SANCTIONS",
    "us_sanctions": "SANCTIONS",
    "eu_sanctions": "SANCTIONS",
    "peps": "PEP",
    "wanted": "WANTED",
    "crime": "CRIME",
    "debarment": "DEBARMENT",
    "enforcement": "ENFORCEMENT",
    "regulatory": "ENFORCEMENT",
    "special_interest": "CRIME",
}

_ID_PROPS = (
    "idNumber", "passportNumber", "taxNumber", "registrationNumber", "innCode",
    "ogrnCode", "okpoCode", "leiCode", "swiftBic", "imoNumber", "mmsi",
    "vatCode", "dunsCode", "uniqueEntityId", "npiCode", "ssn", "cryptoWallets",
)


def _strings(props: Dict[str, Any], key: str) -> List[str]:
    return [v for v in props.get(key, []) if isinstance(v, str) and v.strip()]


def _nested(props: Dict[str, Any], key: str) -> List[Dict[str, Any]]:
    return [v for v in props.get(key, []) if isinstance(v, dict)]


def entity_list_type(topics: List[str], fallback: str) -> str:
    """Map FtM topics to a list type, falling back to the dataset's type."""
    found = []
    for topic in topics:
        for prefix, lt in _TOPIC_ORDER:
            if topic == prefix or topic.startswith(prefix + "."):
                found.append(lt)
                break
    if not found:
        return fallback
    return min(found, key=_SEVERITY_RANK.index)


def convert(data: Dict[str, Any], source: ListSource) -> Entity:
    """Convert one FtM nested target into an Entity."""
    props = data.get("properties", {})
    topics = _strings(props, "topics")
    ent = Entity(
        source=source.key,
        source_id=data["id"],
        schema=_SCHEMA.get(data.get("schema", ""), "Unknown"),
        list_type=entity_list_type(topics, source.list_type),
        topics=topics,
        datasets=list(data.get("datasets", [])),
        url=(_strings(props, "sourceUrl") or [f"https://www.opensanctions.org/entities/{data['id']}/"])[0],
    )
    caption = data.get("caption")
    ent.add_name(caption, "primary")
    for n in _strings(props, "name"):
        ent.add_name(n, "alias")
    for key, kind in (("alias", "alias"), ("previousName", "alias"), ("weakAlias", "weak"),
                      ("abbreviation", "weak")):
        for n in _strings(props, key):
            ent.add_name(n, kind)
    for d in _strings(props, "birthDate"):
        for p in parse_dates(d):
            if p not in ent.birth_dates:
                ent.birth_dates.append(p)
    ent.birth_places = _strings(props, "birthPlace")
    for key, target in (("nationality", ent.nationalities), ("citizenship", ent.nationalities),
                        ("country", ent.countries), ("jurisdiction", ent.countries),
                        ("flag", ent.countries)):
        for c in _strings(props, key):
            code = to_iso2(c) or (c.lower() if len(c) == 2 else None)
            if code and code not in target:
                target.append(code)
    for key in _ID_PROPS:
        for v in _strings(props, key):
            ent.identifiers.append(Identifier(key, v))
    ent.gender = (_strings(props, "gender") or [""])[0]
    ent.remarks = " | ".join(_strings(props, "notes"))[:2000]
    ent.programs = _strings(props, "program") + _strings(props, "programId")
    for s in _nested(props, "sanctions"):
        sp = s.get("properties", {})
        ent.programs += [p for p in _strings(sp, "program") if p not in ent.programs]
        listed = (_strings(sp, "listingDate") or _strings(sp, "startDate") or [""])[0]
        if listed and (not ent.listed_on or listed < ent.listed_on):
            ent.listed_on = listed
        for auth in _strings(sp, "authority"):
            ent.extra.setdefault("authorities", [])
            if auth not in ent.extra["authorities"]:
                ent.extra["authorities"].append(auth)
    for occ in _nested(props, "positionOccupancies"):
        op = occ.get("properties", {})
        for post in _nested(op, "post"):
            pp = post.get("properties", {})
            ent.positions.append(Position(
                title=post.get("caption", "") or (_strings(pp, "name") or [""])[0],
                country=(_strings(pp, "country") or [""])[0],
                start=(_strings(op, "startDate") or [""])[0],
                end=(_strings(op, "endDate") or [""])[0],
                status=(_strings(op, "status") or [""])[0],
            ))
    for key in ("ownershipOwner", "ownershipAsset", "familyPerson", "familyRelative",
                "associations", "associates", "directorshipDirector", "directorshipOrganization",
                "unknownLinkSubject", "unknownLinkObject"):
        for rel in _nested(props, key):
            for side in rel.get("properties", {}).values():
                for other in side:
                    if isinstance(other, dict) and other.get("id") != data["id"]:
                        cap = other.get("caption")
                        if cap and cap not in ent.linked_to:
                            ent.linked_to.append(cap)
    ent.extra["referents"] = list(data.get("referents", []))[:50]
    ent.extra["first_seen"] = data.get("first_seen", "")
    ent.extra["last_change"] = data.get("last_change", "")
    return ent


def parse_targets(paths: Dict[str, Path], source: ListSource) -> Iterator[Entity]:
    """Stream ``targets.nested.json`` (one JSON object per line)."""
    with open(paths["main"], "r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            data = json.loads(line)
            if data.get("schema") not in _SCHEMA:
                continue
            yield convert(data, source)


def _dataset_type(ds: Dict[str, Any]) -> str:
    if ds["type"] == "collection":
        return _COLLECTION_TYPES.get(ds["name"], "OTHER")
    for tag, lt in _TAG_TYPES:
        if tag in ds.get("tags", []):
            return lt
    return "OTHER"


_GROUP_BY_TYPE = {
    "SANCTIONS": "sanctions", "TERRORISM": "sanctions", "COUNTER_SANCTIONS": "counter",
    "WANTED": "wanted", "EXPORT_CONTROL": "sanctions", "DEBARMENT": "debarment",
    "ENFORCEMENT": "enforcement", "CRIME": "crime", "PEP": "pep", "OTHER": "other",
}
_MENA = {"ae", "sa", "qa", "kw", "bh", "om", "jo", "eg", "lb", "iq", "sy", "ye", "ps",
         "il", "ir", "tr", "ma", "dz", "tn", "ly", "sd", "mr"}


def load_sources() -> List[ListSource]:
    """Build one ListSource per dataset in the stored index snapshot."""
    rows = SEED
    cached = cache_file()
    if cached.exists():
        try:
            rows = json.loads(cached.read_text(encoding="utf-8"))["datasets"] or SEED
        except (ValueError, KeyError, OSError):
            rows = SEED
    out: List[ListSource] = []
    for ds in rows:
        lt = _dataset_type(ds)
        kind = _GROUP_BY_TYPE.get(lt, "other")
        groups = ["opensanctions", f"os-{kind}"]
        if ds["type"] == "collection":
            groups = ["os-collections", f"os-collection-{ds['name']}"]
        if ds.get("country") in _MENA:
            groups.append("os-mena")
        if ds.get("official") and ds["type"] == "source":
            groups.append("os-official")
        out.append(ListSource(
            key=("OSC_" if ds["type"] == "collection" else "OS_") + ds["name"].upper(),
            name=f"{ds['title']} (via OpenSanctions)",
            jurisdiction=(ds.get("country") or "GLOBAL").upper(),
            list_type=lt,
            urls={"main": _URL.format(name=ds["name"])},
            parser=parse_targets,
            homepage=f"https://www.opensanctions.org/datasets/{ds['name']}/",
            license="CC-BY-NC",
            groups=groups,
            max_age_days=3 if ds.get("frequency") == "daily" else 14,
            notes=f"{ds.get('targets', 0)} targets; publisher {ds.get('publisher', '')}",
            timeout=600,
        ))
    return out


def refresh_index(dest: Optional[Path] = None) -> int:
    """Download the live OpenSanctions index and cache the dataset list.

    @return number of datasets written
    """
    dest = dest or cache_file()
    dest.parent.mkdir(parents=True, exist_ok=True)
    import tempfile

    from venombot.fetch import download

    with tempfile.TemporaryDirectory() as tmp:
        res = download(INDEX_URL, Path(tmp) / "index.json", timeout=120)
        if not res.ok or res.path is None:
            raise RuntimeError(f"index download failed: {res.error}")
        index = json.loads(res.path.read_text(encoding="utf-8"))
    rows = []
    for x in index["datasets"]:
        if x.get("deprecated") or x.get("disabled") or x.get("hidden"):
            continue
        if x["type"] not in ("source", "collection"):
            continue
        if x["type"] == "collection" and x["name"] in ("default", "enrichers", "maritime", "securities"):
            continue
        if not any(r["name"] == "targets.nested.json" for r in x.get("resources", [])):
            continue
        p = x.get("publisher") or {}
        rows.append(_row(
            x["name"], x["title"], x["type"], x.get("tags", []), x.get("target_count", 0),
            p.get("country") or "", bool(p.get("official")), p.get("acronym") or p.get("name") or "",
            (x.get("coverage") or {}).get("frequency", ""),
        ))
    rows.sort(key=lambda r: r["name"])
    # One dataset per line keeps the cache readable and diff-friendly.
    lines = ",\n".join(json.dumps(r, ensure_ascii=False) for r in rows)
    dest.write_text(f'{{"generated_from": "{INDEX_URL}", "generated_at": '
                    f'{json.dumps(index.get("run_time"))}, "datasets": [\n{lines}\n]}}\n',
                    encoding="utf-8")
    return len(rows)


SOURCES = load_sources()
