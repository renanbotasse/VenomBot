"""PEP and relatives/associates from Wikidata (WDQS and QLever SPARQL families).

Wikidata is CC0 and broad but uneven: end dates are often missing, so an open
term can be stale. Rows are therefore tagged with the position class and
deceased people are dropped in the query, not trusted blindly. One request per
ListSource URL: WDQS has a 60 s timeout, so country batches are separate URLs.

Not registered: WIKIDATA_POSITION_CLASSES. It is a position->class lookup
(141k rows), not a roster of people; the class tiers are embedded in the
queries here instead (``?class`` column / per-family queries).
"""

from __future__ import annotations

import csv
import io
import json
import re
from pathlib import Path
from typing import Dict, Iterator, List, Optional
from urllib.parse import quote

from venombot.entities import Entity
from venombot.lists import ListSource
from venombot.lists._util import add_dates, add_identifier, clean, read_text
from venombot.lists.pep_europe import iso, iso2, mk_person, position

# Wikimedia policy: descriptive UA with contact; browsers UAs are throttled harder.
WD_UA = "VenomBot/1.0 (https://github.com/renanbotasse/VenomBot; renan.botasse@revenya.com) python-urllib"
WDQS = "https://query.wikidata.org/sparql?query="
QLEVER = "https://qlever.dev/api/wikidata?query="
WD_HEADERS = {"User-Agent": WD_UA, "Accept": "text/csv"}

_QID = re.compile(r"Q\d+$")

# Position-class QIDs (verified in discovery) -> tier shown in the position title.
CLASS_TIER = {
    "Q48352": ("head of state", "national"), "Q2285706": ("head of government", "national"),
    "Q83307": ("minister", "national"), "Q26204040": ("deputy minister", "national"),
    "Q15686806": ("senator", "national"), "Q486839": ("member of parliament", "national"),
    "Q107363151": ("central bank governor", "national"), "Q117826617": ("supreme court judge", "national"),
    "Q16533": ("judge", "national"), "Q121998": ("ambassador", "national"),
    "Q189290": ("military officer", "national"), "Q30185": ("mayor", "local"),
}

PREFIXES = (
    "PREFIX wd: <http://www.wikidata.org/entity/> PREFIX wdt: <http://www.wikidata.org/prop/direct/> "
    "PREFIX p: <http://www.wikidata.org/prop/> PREFIX ps: <http://www.wikidata.org/prop/statement/> "
    "PREFIX pq: <http://www.wikidata.org/prop/qualifier/> PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#> "
)


def _url(base: str, query: str) -> str:
    return base + quote(" ".join(query.split()), safe="")


# --------------------------------------------------------------------------
# result reading (CSV preferred; SPARQL-JSON accepted)
# --------------------------------------------------------------------------
def _rows(path: Path) -> Iterator[Dict[str, str]]:
    """Yield result rows as plain dicts from a CSV or SPARQL-JSON response."""
    text = read_text(path)
    if text.lstrip().startswith("{"):
        data = json.loads(text)
        if "results" not in data:
            raise ValueError("Wikidata response is not a result set: " + text[:200])
        for b in data["results"]["bindings"]:
            yield {k: v.get("value", "") for k, v in b.items()}
        return
    head = text.lstrip()[:200]
    if not head or head.startswith("<") or "," not in head.split("\n", 1)[0]:
        raise ValueError("Wikidata response is not CSV (rate limit or timeout?): " + head[:120])
    yield from csv.DictReader(io.StringIO(text))


def _qid(uri: str) -> str:
    return (uri or "").rsplit("/", 1)[-1]


def _label(row: Dict[str, str], *keys: str) -> str:
    """First usable label (an unlabelled item comes back as its bare QID)."""
    for k in keys:
        v = clean(row.get(k))
        if v and not _QID.match(v):
            return v
    return ""


def _dob(ent: Entity, raw: Optional[str]) -> None:
    """Add a birth date; Jan-1 dates are year-only because Wikidata precision is not exported."""
    d = iso(clean(raw)[:10]) if raw else ""
    if not d:
        return
    if d.endswith("-01-01"):
        d = d[:4]
    if d not in ent.birth_dates:
        ent.birth_dates.append(d)


def _cc(row: Dict[str, str]) -> str:
    return iso2(_label(row, "countryLabel", "cn", "stateLabel"))


def _tier(row: Dict[str, str]) -> str:
    cls = _qid(row.get("class", ""))
    t = CLASS_TIER.get(cls)
    return t[0] if t else ""


# --------------------------------------------------------------------------
# parsers
# --------------------------------------------------------------------------
def parse_positions(paths: Dict[str, Path], source: ListSource) -> Iterator[Entity]:
    """Person x position rows -> one entity per person with all their positions."""
    people: Dict[str, Entity] = {}
    for key in sorted(paths):
        for row in _rows(paths[key]):
            name = _label(row, "personLabel", "name")
            qid = _qid(row.get("person", ""))
            if not qid or not name or clean(row.get("dod")):
                continue
            ent = people.get(qid)
            if ent is None:
                ent = mk_person(source, qid, name, url=f"https://www.wikidata.org/wiki/{qid}")
                add_identifier(ent, "Wikidata", qid)
                people[qid] = ent
            _dob(ent, row.get("dob"))
            nat = iso2(_label(row, "citizenshipLabel"))
            if nat and nat not in ent.nationalities:
                ent.nationalities.append(nat)
            cc = _cc(row)
            if cc and cc not in ent.countries:
                ent.countries.append(cc)
            title = _label(row, "positionLabel", "posName", "roleLabel")
            if not title and row.get("role"):
                title = _ROLE.get(row["role"], row["role"].replace("_", " "))
            org = _label(row, "orgLabel")
            if org:
                title = f"{title or 'Leader'}, {org}"
            state = _label(row, "stateLabel")
            if state and row.get("role"):
                title = f"{row['role'].replace('_', ' ').capitalize()} of {state}"
            if not title:
                continue
            tier = _tier(row)
            if tier and tier not in title.lower():
                title = f"{title} [{tier}]"
            p = position(title, cc, clean(row.get("start"))[:10], clean(row.get("end"))[:10])
            if not any(x.title == p.title and x.start == p.start for x in ent.positions):
                ent.positions.append(p)
            if tier:
                ent.extra.setdefault("tiers", [])
                if tier not in ent.extra["tiers"]:
                    ent.extra["tiers"].append(tier)
    yield from people.values()


_ROLE = {"ceo": "Chief executive", "chair": "Chairperson", "director_manager": "Director/manager"}
_REL_LABEL = {"spouse": "Spouse", "child": "Child", "father": "Father", "mother": "Mother",
              "sibling": "Sibling", "relative": "Relative"}


def parse_relatives(paths: Dict[str, Path], source: ListSource) -> Iterator[Entity]:
    """PEP -> relative pairs (P26/P40/P22/P25/P3373/P1038): one RCA entity per relative."""
    people: Dict[str, Entity] = {}
    for key in sorted(paths):
        for row in _rows(paths[key]):
            rel_q, pep_q = _qid(row.get("rel", "")), _qid(row.get("pep", ""))
            rel_name, pep_name = _label(row, "relLabel"), _label(row, "pepLabel")
            if not rel_q or not rel_name or not pep_name or rel_q == pep_q:
                continue
            ent = people.get(rel_q)
            if ent is None:
                ent = mk_person(source, rel_q, rel_name, list_type="PEP_RCA", url=f"https://www.wikidata.org/wiki/{rel_q}")
                add_identifier(ent, "Wikidata", rel_q)
                people[rel_q] = ent
            _dob(ent, row.get("relDob"))
            rel = _REL_LABEL.get(clean(row.get("relProp")), clean(row.get("relProp")).capitalize())
            if pep_name not in ent.linked_to:
                ent.linked_to.append(pep_name)
            note = f"{rel} of PEP {pep_name}"
            if note not in ent.remarks:
                ent.remarks = f"{ent.remarks}; {note}".strip("; ")
    yield from people.values()


# --------------------------------------------------------------------------
# queries
# --------------------------------------------------------------------------
_PEPS_Q = """
SELECT ?person ?personLabel ?dob ?dod ?citizenshipLabel ?position ?positionLabel ?country ?countryLabel ?start ?end WHERE {
  VALUES ?country { %s }
  ?position wdt:P17|wdt:P1001 ?country .
  ?person p:P39 ?st . ?st ps:P39 ?position . ?person wdt:P31 wd:Q5 .
  FILTER NOT EXISTS { ?st wikibase:rank wikibase:DeprecatedRank }
  OPTIONAL { ?st pq:P580 ?start } OPTIONAL { ?st pq:P582 ?end }
  OPTIONAL { ?person wdt:P569 ?dob } OPTIONAL { ?person wdt:P570 ?dod }
  OPTIONAL { ?person wdt:P27 ?citizenship }
  SERVICE wikibase:label { bd:serviceParam wikibase:language "en,ar,fr,es,ru". }
}"""

_REL_Q = """
SELECT DISTINCT ?pep ?pepLabel ?relProp ?rel ?relLabel ?relDob WHERE {
  VALUES ?country { %s }
  VALUES (?p ?relProp) { (wdt:P26 "spouse") (wdt:P40 "child") (wdt:P22 "father") (wdt:P25 "mother") (wdt:P3373 "sibling") (wdt:P1038 "relative") }
  ?position wdt:P17|wdt:P1001 ?country .
  ?pep wdt:P39 ?position ; wdt:P31 wd:Q5 .
  FILTER NOT EXISTS { ?pep wdt:P570 [] }
  ?pep ?p ?rel .
  FILTER NOT EXISTS { ?rel wdt:P570 [] }
  OPTIONAL { ?rel wdt:P569 ?relDob }
  SERVICE wikibase:label { bd:serviceParam wikibase:language "en,ar". }
}"""

_HOS_Q = """
SELECT ?state ?stateLabel ?role ?person ?personLabel ?dob ?start WHERE {
  ?state wdt:P31 wd:Q3624078 . FILTER NOT EXISTS { ?state wdt:P576 [] }
  VALUES (?p ?ps ?role) { (p:P35 ps:P35 "head_of_state") (p:P6 ps:P6 "head_of_government") }
  ?state ?p ?st . ?st ?ps ?person .
  FILTER NOT EXISTS { ?st pq:P582 [] }
  OPTIONAL { ?st pq:P580 ?start } OPTIONAL { ?person wdt:P569 ?dob }
  SERVICE wikibase:label { bd:serviceParam wikibase:language "en". }
}"""

_CB_Q = """
SELECT ?person ?personLabel ?dob ?dod ?position ?positionLabel ?country ?countryLabel ?start ?end WHERE {
  { ?position wdt:P279+ wd:Q107363151 } UNION { ?position wdt:P31 wd:Q107363151 }
  ?person p:P39 ?st . ?st ps:P39 ?position .
  OPTIONAL { ?position wdt:P17 ?country }
  OPTIONAL { ?st pq:P580 ?start } OPTIONAL { ?st pq:P582 ?end }
  OPTIONAL { ?person wdt:P569 ?dob } OPTIONAL { ?person wdt:P570 ?dod }
  SERVICE wikibase:label { bd:serviceParam wikibase:language "en". }
}"""

_SOE_Q = """
SELECT ?org ?orgLabel ?country ?countryLabel ?role ?person ?personLabel ?dob ?dod ?start ?end WHERE {
  ?org wdt:P31/wdt:P279* wd:Q270791 .
  VALUES (?p ?ps ?role) { (p:P169 ps:P169 "ceo") (p:P488 ps:P488 "chair") (p:P1037 ps:P1037 "director_manager") }
  ?org ?p ?st . ?st ?ps ?person . ?person wdt:P31 wd:Q5 .
  OPTIONAL { ?org wdt:P17 ?country }
  OPTIONAL { ?st pq:P580 ?start } OPTIONAL { ?st pq:P582 ?end }
  OPTIONAL { ?person wdt:P569 ?dob } OPTIONAL { ?person wdt:P570 ?dod }
  SERVICE wikibase:label { bd:serviceParam wikibase:language "en". }
}"""

# QLever has no label service and needs prefixes; only current, living holders.
_QL_Q = PREFIXES + """
SELECT ?person ?personLabel ?dob ?position ?positionLabel ?country ?countryLabel ?class ?start WHERE {
  VALUES ?class { %s }
  ?position wdt:P279* ?class .
  FILTER NOT EXISTS { ?position wdt:P279* wd:Q30185 }
  ?person wdt:P31 wd:Q5 ; p:P39 ?st . ?st ps:P39 ?position .
  FILTER NOT EXISTS { ?st pq:P582 [] }
  FILTER NOT EXISTS { ?person wdt:P570 [] }
  OPTIONAL { ?position wdt:P17 ?country . ?country rdfs:label ?countryLabel FILTER(LANG(?countryLabel) = "en") }
  OPTIONAL { ?st pq:P580 ?start }
  OPTIONAL { ?person wdt:P569 ?dob }
  OPTIONAL { ?person rdfs:label ?personLabel FILTER(LANG(?personLabel) = "en") }
  OPTIONAL { ?position rdfs:label ?positionLabel FILTER(LANG(?positionLabel) = "en") }
} LIMIT 120000"""


def _vals(qids: str) -> str:
    return " ".join(f"wd:{q}" for q in qids.split())


# Country batches (QIDs): sized to stay well under WDQS's 60 s limit.
REGIONS = {
    "gcc": "Q817 Q851 Q878 Q846 Q398 Q842",
    "levant_iraq_iran_yemen": "Q858 Q822 Q810 Q796 Q794 Q805",
    "north_africa": "Q79 Q262 Q1028 Q948 Q1016 Q1049",
    "russia": "Q159",
    "cis_caucasus": "Q212 Q184 Q232 Q813 Q863 Q874 Q265 Q230 Q399 Q227 Q217",
    "south_asia_turkey": "Q843 Q889 Q902 Q43",
    "china": "Q148",
    "latam_south": "Q155 Q414 Q717 Q739 Q298 Q419",
    "iberia": "Q45 Q29",
}
REL_REGIONS = {"gcc": REGIONS["gcc"], "levant_iraq_iran_yemen": REGIONS["levant_iraq_iran_yemen"],
               "north_africa": REGIONS["north_africa"]}

_QL_GROUPS = {
    "top": "wd:Q48352 wd:Q2285706 wd:Q26204040 wd:Q107363151 wd:Q117826617 wd:Q189290",
    "ministers": "wd:Q83307",
    "senators_ambassadors": "wd:Q15686806 wd:Q121998",
}

_GROUPS = ["pep", "wikidata", "global"]

SOURCES: List[ListSource] = [
    ListSource(
        key="WIKIDATA_HEADS_OF_STATE_GOV", name="Wikidata - current heads of state and government", jurisdiction="GLOBAL",
        list_type="PEP", urls={"main": _url(WDQS, _HOS_Q)}, parser=parse_positions,
        homepage="https://www.wikidata.org/", license="CC0", groups=_GROUPS, headers=WD_HEADERS, max_age_days=14, timeout=120,
        notes="P35/P6 statements without an end date on current sovereign states (~450 rows). Stale where editors never closed a term "
              "(Kuwait example): cross-check with CIA_WORLD_LEADERS_JSON.",
    ),
    ListSource(
        key="WIKIDATA_CENTRAL_BANK_GOVERNORS", name="Wikidata - central bank governors (current and former)", jurisdiction="GLOBAL",
        list_type="PEP", urls={"main": _url(WDQS, _CB_Q)}, parser=parse_positions,
        homepage="https://www.wikidata.org/", license="CC0", groups=_GROUPS, headers=WD_HEADERS, max_age_days=30, timeout=120,
        notes="Positions that are instances/subclasses of Q107363151 (~1,150 rows). Q1553195 is 'party leader', not a governor class.",
    ),
    ListSource(
        key="WIKIDATA_SOE_LEADERS", name="Wikidata - state-owned enterprise CEOs, chairs and directors", jurisdiction="GLOBAL",
        list_type="PEP", urls={"main": _url(WDQS, _SOE_Q)}, parser=parse_positions,
        homepage="https://www.wikidata.org/", license="CC0", groups=_GROUPS, headers=WD_HEADERS, max_age_days=30, timeout=120,
        notes="Organisations typed Q270791 with P169/P488/P1037 holders (~440 rows); thin coverage (Aramco/ADNOC/KPC are not typed so). "
              "Directors of SOEs are PEPs under FATF R.12 (senior management).",
    ),
    ListSource(
        key="WIKIDATA_PEPS", name="Wikidata - position holders by country batch (with dates and DOB)", jurisdiction="GLOBAL",
        list_type="PEP", urls={name: _url(WDQS, _PEPS_Q % _vals(q)) for name, q in REGIONS.items()}, parser=parse_positions,
        homepage="https://www.wikidata.org/", license="CC0", groups=_GROUPS, headers=WD_HEADERS, max_age_days=30, timeout=180,
        notes="One WDQS request per country batch (60 s server limit; space requests out, HTTP 429 means retry later). Rows include foreign "
              "ambassadors TO the country (nationality shows their own country). Deceased holders are dropped. For global coverage of "
              "current office-holders see QLEVER_WIKIDATA_PEPS.",
    ),
    ListSource(
        key="WIKIDATA_PEP_RELATIVES", name="Wikidata - relatives of PEPs (spouse, child, parent, sibling)", jurisdiction="GLOBAL",
        list_type="PEP_RCA", urls={name: _url(WDQS, _REL_Q % _vals(q)) for name, q in REL_REGIONS.items()}, parser=parse_relatives,
        homepage="https://www.wikidata.org/", license="CC0", groups=_GROUPS, headers=WD_HEADERS, max_age_days=30, timeout=180,
        notes="Living PEPs and living relatives only; GCC/MENA batches (ruling families are well modelled). Each relative links to the PEP by name.",
    ),
    ListSource(
        key="QLEVER_WIKIDATA_PEPS", name="QLever Wikidata - current office holders worldwide (by position class)",
        jurisdiction="GLOBAL", list_type="PEP",
        urls={name: _url(QLEVER, _QL_Q % q) for name, q in _QL_GROUPS.items()}, parser=parse_positions,
        homepage="https://qlever.dev/wikidata", license="CC0", groups=_GROUPS,
        headers={"User-Agent": WD_UA, "Accept": "text/csv"}, max_age_days=14, timeout=300,
        notes="Current (no end date), living holders of head of state/government, minister, deputy minister, senator, ambassador, central "
              "bank governor, supreme-court judge and military-officer positions (mayors excluded). Parliament members (~217k) are left out "
              "on purpose: official rosters cover them. The ?class column becomes a tier tag in the position title. QLever is a free "
              "academic service: fair use, few requests.",
    ),
]
