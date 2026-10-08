"""Structured list entries — the unit every parser produces and screening reads.

Screening used to run substring search over raw snapshots, which matched a
name mentioned inside someone else's remarks and missed "SURNAME, Given"
records. Parsers now emit one Entity per listed party, with names, DOBs,
nationalities and identifiers in separate fields.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional

# What the listing means for risk. Severity is derived from this, never from
# keywords in free text.
LIST_TYPES = (
    "SANCTIONS",        # asset freeze / financial sanctions designation
    "TERRORISM",        # domestic terrorist designation lists
    "EXPORT_CONTROL",   # entity lists, denied parties
    "WANTED",           # law-enforcement wanted / red notices
    "CRIME",            # convictions, organised crime, trafficking
    "DEBARMENT",        # procurement / development-bank exclusions
    "ENFORCEMENT",      # regulatory actions, warnings, disqualifications
    "PEP",              # politically exposed persons
    "PEP_RCA",          # relatives and close associates of PEPs
    "SANCTIONS_LINKED", # owned/controlled by or linked to a designated party
    "COUNTER_SANCTIONS",# lists published by sanctioned states against others
    "CORPORATE",        # registries, ownership, leaks (context, not risk)
    "OTHER",
)

SCHEMAS = ("Person", "Organization", "Vessel", "Aircraft", "Unknown")


@dataclass
class Name:
    value: str
    # primary | alias | weak (low-quality aka) | original (native script)
    kind: str = "primary"
    lang: str = ""


@dataclass
class Identifier:
    type: str
    value: str
    country: str = ""


@dataclass
class Position:
    title: str
    country: str = ""
    start: str = ""
    end: str = ""
    status: str = ""  # current | ended | unknown


@dataclass
class Entity:
    source: str
    source_id: str
    schema: str = "Unknown"
    names: List[Name] = field(default_factory=list)
    list_type: str = "OTHER"
    topics: List[str] = field(default_factory=list)
    birth_dates: List[str] = field(default_factory=list)  # ISO partial: YYYY[-MM[-DD]]
    birth_places: List[str] = field(default_factory=list)
    nationalities: List[str] = field(default_factory=list)  # ISO2 lower-case
    countries: List[str] = field(default_factory=list)  # other linked countries, ISO2
    identifiers: List[Identifier] = field(default_factory=list)
    programs: List[str] = field(default_factory=list)
    positions: List[Position] = field(default_factory=list)
    linked_to: List[str] = field(default_factory=list)
    gender: str = ""
    listed_on: str = ""
    remarks: str = ""
    url: str = ""
    datasets: List[str] = field(default_factory=list)
    extra: Dict[str, Any] = field(default_factory=dict)

    @property
    def uid(self) -> str:
        return f"{self.source}:{self.source_id}"

    @property
    def caption(self) -> str:
        for n in self.names:
            if n.kind == "primary":
                return n.value
        return self.names[0].value if self.names else self.source_id

    @property
    def is_org(self) -> bool:
        return self.schema in ("Organization", "Vessel", "Aircraft")

    def add_name(self, value: Optional[str], kind: str = "alias", lang: str = "") -> None:
        """Add a name once; empty values and exact duplicates are ignored."""
        if not value:
            return
        value = " ".join(str(value).split())
        if not value or value in ("-0-", "-", "N/A", "n/a", "NA"):
            return
        if any(n.value == value for n in self.names):
            return
        if not self.names:
            kind = "primary"
        self.names.append(Name(value, kind, lang))

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @staticmethod
    def from_dict(data: Dict[str, Any]) -> "Entity":
        data = dict(data)
        data["names"] = [Name(**n) for n in data.get("names", [])]
        data["identifiers"] = [Identifier(**i) for i in data.get("identifiers", [])]
        data["positions"] = [Position(**p) for p in data.get("positions", [])]
        known = Entity.__dataclass_fields__
        return Entity(**{k: v for k, v in data.items() if k in known})
