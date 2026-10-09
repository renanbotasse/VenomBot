"""National sanctions lists of EU member states and neighbours.

France, Belgium, Monaco, Lithuania, Latvia, the Czech Republic and Poland
each publish their own list. Most of it mirrors the EU/UN files, but the
national parts (French L562 freezes, Belgian terrorism decree, Czech Act 1/2023,
Polish MSWiA list, Lithuanian entry bans, Latvian ownership links) exist
nowhere else in structured form, so each is parsed in its own native layout.

Only the French, Monaco and Belgian files repeat EU/UN designations; none of
them are in the "core" group so default screening does not double-count.
"""

from __future__ import annotations

import csv
import datetime as _dt
import hashlib
import io
import json
import re
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
    fold,
    partial_iso,
    uniq,
)

_DMY_SLASH = re.compile(r"\b(\d{1,2})/(\d{1,2})/(\d{4})\b")


def _slug_id(*parts: str) -> str:
    """Stable id for lists that publish no identifier: hash of name+birth date."""
    raw = "|".join(fold(p) for p in parts if p)
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:12]


def _earliest_dmy(text: str) -> str:
    """Earliest dd/mm/yyyy date in a legal-basis string, as ISO."""
    found = []
    for d, m, y in _DMY_SLASH.findall(text or ""):
        try:
            found.append(_dt.date(int(y), int(m), int(d)).isoformat())
        except ValueError:
            continue
    return min(found) if found else ""


def _add_countries(target: List[str], text: Optional[str]) -> None:
    for c in countries_native(text):
        if c not in target:
            target.append(c)


# --------------------------------------------------------------------------
# France: DG Trésor national register of asset freezes
# --------------------------------------------------------------------------

_FR_SCHEMA = {"personne physique": "Person", "personne morale": "Organization", "navire": "Vessel"}


def parse_fr_gels(paths: Dict[str, Path], source: ListSource) -> Iterator[Entity]:
    """Parse the Registre national des gels (JSON).

    Each entry is a list of typed fields (TypeChamp/Valeur); fields repeat, so
    they are grouped by type first.
    """
    data = json.loads(read_text(paths["main"]))
    pub = (data.get("Publications") or {}).get("DatePublication", "")[:10]
    for item in (data.get("Publications") or {}).get("PublicationDetail", []):
        fields: Dict[str, list] = {}
        for det in item.get("RegistreDetail", []):
            fields.setdefault(det.get("TypeChamp", ""), []).extend(det.get("Valeur") or [])
        nature = clean(item.get("Nature")).lower()
        ent = Entity(
            source=source.key,
            source_id=str(item.get("IdRegistre", "")),
            schema=_FR_SCHEMA.get(nature, "Organization"),
            list_type=source.list_type,
            url="https://gels-avoirs.dgtresor.gouv.fr/List",
        )
        if not ent.source_id:
            continue
        surname = clean(item.get("Nom"))
        given = " ".join(clean(v.get("Prenom")) for v in fields.get("PRENOM", []) if clean(v.get("Prenom")))
        ent.add_name(" ".join(p for p in (given, surname) if p), "primary")
        for v in fields.get("ALIAS", []):
            add_name_auto(ent, v.get("Alias"), "alias")
        for v in fields.get("AUTRE_IDENTITE", []):
            if clean(v.get("NumeroCarte")):
                add_identifier(ent, clean(v.get("Commentaire")) or "Other identity document", v.get("NumeroCarte"))
        sex = clean((fields.get("SEXE") or [{}])[0].get("Sexe")).lower()
        ent.gender = {"masculin": "male", "féminin": "female", "feminin": "female"}.get(sex, "")
        for v in fields.get("DATE_DE_NAISSANCE", []):
            iso = partial_iso(v.get("Annee", ""), v.get("Mois", ""), v.get("Jour", ""))
            if iso and iso not in ent.birth_dates:
                ent.birth_dates.append(iso)
        for v in fields.get("LIEU_DE_NAISSANCE", []):
            place = ", ".join(p for p in (clean(v.get("Lieu")), clean(v.get("Pays"))) if p)
            if place and place not in ent.birth_places:
                ent.birth_places.append(place)
            _add_countries(ent.countries, v.get("Pays"))
        for v in fields.get("NATIONALITE", []):
            _add_countries(ent.nationalities, v.get("Pays"))
        for key in ("ADRESSE_PP", "ADRESSE_PM"):
            for v in fields.get(key, []):
                _add_countries(ent.countries, v.get("Pays"))
        for v in fields.get("PASSEPORT", []):
            add_identifier(ent, "Passport", v.get("NumeroPasseport"))
        for v in fields.get("IDENTIFICATION", []):
            raw = clean(v.get("Identification"))
            if raw in ("", "/"):
                continue
            label, sep, rest = raw.partition(": ")  # sometimes "Type: value" in a single cell
            if sep and len(label) < 60:
                add_identifier(ent, label, rest)
            else:
                add_identifier(ent, clean(v.get("Commentaire")) or "Identification", raw)
        for v in fields.get("NUMERO_OMI", []):
            add_identifier(ent, "IMO", v.get("NumeroOMI"))
        for v in fields.get("REFERENCE_UE", []):
            if clean(v.get("ReferenceUe")):
                ent.extra.setdefault("eu_reference", clean(v.get("ReferenceUe")))
        for v in fields.get("REFERENCE_ONU", []):
            add_identifier(ent, "UN reference number", v.get("ReferenceOnu"))
        for key, label in (("COURRIEL", "Email"), ("SITE_INTERNET", "Website"), ("TELEPHONE", "Phone")):
            for v in fields.get(key, []):
                add_identifier(ent, label, next(iter(v.values()), ""))
        titles = [clean(v.get("Titre")) for v in fields.get("TITRE", []) if clean(v.get("Titre"))]
        if titles:
            ent.extra["titles"] = titles
        labels = [clean(v.get("FondementJuridiqueLabel")) for v in fields.get("FONDEMENT_JURIDIQUE", [])]
        ent.programs = uniq(labels)
        ent.listed_on = min([d for d in (_earliest_dmy(l) for l in labels) if d] or [""])
        ent.remarks = " ".join(clean(v.get("Motifs")) for v in fields.get("MOTIFS", []) if clean(v.get("Motifs")))
        if pub:
            ent.extra["register_published"] = pub
        if ent.names:
            yield ent


# --------------------------------------------------------------------------
# Belgium: FPS Finance consolidated list (national terrorism decree rows only)
# --------------------------------------------------------------------------

def _be_date(text: str, birth: bool) -> str:
    """dd-mm-yy / dd-mm-yyyy / yyyy-mm-dd -> ISO; two-digit years pivot by use."""
    text = clean(text).split("\n")[0]
    m = re.match(r"^(\d{2})-(\d{2})-(\d{2,4})$", text)
    if m:
        d, mo, y = m.groups()
        if len(y) == 2:
            pivot = 12 if birth else 40
            y = ("20" if int(y) <= pivot else "19") + y
        return partial_iso(y, mo, d)
    found = parse_dates(text)
    return found[0] if found else ""


def parse_be_fps(paths: Dict[str, Path], source: ListSource) -> Iterator[Entity]:
    """Parse sanctionedFile.csv, keeping the Belgian national list (Embargos == "BE").

    The other 6,000 rows re-publish EU and UN regimes already covered by the
    EU and UN sources, so emitting them would double every hit. Remove the
    filter if a mirror of the Belgian publication date per row is wanted.
    """
    text = read_text(paths["main"])
    seen: Dict[str, int] = {}
    for row in csv.DictReader(io.StringIO(text), delimiter=";"):
        if clean(row.get("Embargos")).upper() != "BE":
            continue
        kind = clean(row.get("type")).upper()
        names = [clean(n) for n in (row.get("Wholename") or "").split("\n") if clean(n)]
        if not names:
            continue
        birth = _be_date(row.get("Birth date", ""), True)
        sid = _slug_id(names[0], birth)
        seen[sid] = seen.get(sid, 0) + 1
        ent = Entity(
            source=source.key,
            source_id=sid if seen[sid] == 1 else f"{sid}-{seen[sid]}",
            schema="Person" if kind == "P" else "Organization",
            list_type=source.list_type,
            programs=["BE national terrorism list (Royal Decree 28/12/2006)"],
            listed_on=_be_date(row.get("Publication date", ""), False),
            url=clean((row.get("Links") or "").split("\n")[0]),
            gender={"M": "male", "F": "female"}.get(clean(row.get("Gender")).upper(), ""),
        )
        for i, n in enumerate(names):
            add_name_auto(ent, n, "primary" if i == 0 else "alias")
        if birth:
            ent.birth_dates.append(birth)
        place = ", ".join(p for p in (clean(row.get("Birth place")), clean(row.get("Birth country"))) if p)
        if place:
            ent.birth_places.append(place)
        _add_countries(ent.countries, row.get("Birth country"))
        num = clean(row.get("Number"))
        if num:
            m = re.match(r"^(NRN)\s+(.*)$", num)
            add_identifier(ent, m.group(1) if m else "Number", m.group(2) if m else num)
        if clean(row.get("Function")):
            ent.extra["function"] = clean(row.get("Function"))
        ent.remarks = clean(row.get("Remark"))
        yield ent


# --------------------------------------------------------------------------
# Monaco: Direction du Budget et du Trésor fund-freeze measures
# --------------------------------------------------------------------------

_MC_AUTHORITY = {"__ue_authority_label__": "EU", "__onu_authority_label__": "UN", "__rf_authority_label__": "national"}
_MC_SCHEMA = {"personne physique": "Person", "personne morale": "Organization", "navire": "Vessel"}


def parse_mc_freezes(paths: Dict[str, Path], source: ListSource) -> Iterator[Entity]:
    """Parse sanctions.json (one object per measure).

    The portal's WAF rejects browser-like User-Agents but accepts plain
    clients; entries in state "withdrawal" are lifted measures and skipped.
    """
    data = json.loads(read_text(paths["main"]))
    for m in data:
        if clean(m.get("state")).lower() in ("withdrawal", "withdrawn", "delete", "deleted"):
            continue
        det = m.get("mesureDetails") or {}
        ent = Entity(
            source=source.key,
            source_id=str(m.get("mesureId", "")),
            schema=_MC_SCHEMA.get(clean(m.get("nature")).lower(), "Organization"),
            list_type=source.list_type,
            url="https://geldefonds.gouv.mc/",
            remarks=clean(det.get("motifs")),
        )
        if not ent.source_id:
            continue
        name = " ".join(p for p in (clean(det.get("prenom")), clean(m.get("nom"))) if p)
        ent.add_name(name, "primary")
        for al in re.split(r"\s*;\s*|\n", det.get("alias") or ""):
            add_name_auto(ent, al, "alias")
        sex = clean(det.get("sexe")).lower()
        ent.gender = "male" if sex.startswith("m") else ("female" if sex.startswith("f") else "")
        add_dates(ent, det.get("dateNaissance"))
        pob = clean(det.get("lieuNaissance"))
        if pob:
            ent.birth_places.append(pob.replace(" : ", ", "))
            _add_countries(ent.countries, pob)
        _add_countries(ent.nationalities, det.get("nationalite"))
        _add_countries(ent.countries, det.get("adresse"))
        if clean(det.get("passeport")):
            add_identifier(ent, "Passport", det.get("passeport"))
        legal = clean(det.get("fondementJuridique"))
        ent.programs = uniq([clean(det.get("regimeSanction"))] + [p.strip() for p in legal.split(";")])
        ent.listed_on = _earliest_dmy(legal)
        auth = uniq(_MC_AUTHORITY.get(a.strip(), a.strip()) for a in clean(det.get("autoriteMesure")).split(","))
        if auth:
            ent.extra["authority"] = auth
        if clean(det.get("titre")):
            ent.extra["titles"] = [clean(det.get("titre"))]
        if ent.names:
            yield ent


# --------------------------------------------------------------------------
# Lithuania: Migration Department entry bans (Magnitsky and security grounds)
# --------------------------------------------------------------------------

def parse_lt_bans(paths: Dict[str, Path], source: ListSource) -> Iterator[Entity]:
    """Parse the migracija.lt entry-ban list (all pages fit in one request).

    Entries carry no id, so the id is a hash of name+birth date. Expired bans
    (uzdraustaIki in the past) are dropped. Only the Magnitsky-style ground
    (Aliens Law art. 133(4)) counts as SANCTIONS; other grounds are plain
    entry bans and are typed ENFORCEMENT so severity is not overstated.
    """
    data = json.loads(read_text(paths["main"]))
    today = _dt.date.today().isoformat()
    for r in data.get("list", []):
        until = clean(r.get("uzdraustaIki"))[:10]
        if until and until < today:
            continue
        first, last = clean(r.get("vardas")), clean(r.get("pavarde"))
        name = " ".join(p for p in (first, last) if p)
        if not name:
            continue
        dob = clean(r.get("gimimoData"))[:10]
        reason = clean(r.get("priezastis"))
        magnitsky = "133 str. 4" in reason
        ent = Entity(
            source=source.key,
            source_id=_slug_id(name, dob),
            schema="Person",
            list_type="SANCTIONS" if magnitsky else "ENFORCEMENT",
            programs=["LT-MAGNITSKY-ENTRY-BAN" if magnitsky else "LT-ENTRY-BAN"],
            remarks=reason,
            url="https://www.migracija.lt/",
            gender={"V": "male", "M": "female"}.get(clean(r.get("lytis")).upper(), ""),
        )
        ent.add_name(name, "primary")
        add_dates(ent, dob)
        for c in r.get("pilietybes") or []:
            entry = ((c.get("valstybe") or {}).get("pilietybeClaEntry")) or {}
            code = clean((entry.get("properties") or {}).get("kodas2s")).lower()
            if code and code not in ent.nationalities:
                ent.nationalities.append(code)
        if until:
            ent.extra["ban_until"] = until
        yield ent


# --------------------------------------------------------------------------
# Latvia: Register of Enterprises sanctions-risk links
# --------------------------------------------------------------------------

def parse_lv_sanctions_risk(paths: Dict[str, Path], source: ListSource) -> Iterator[Entity]:
    """Parse sanctions.csv: Latvian persons/companies linked to listed parties.

    Typed SANCTIONS_LINKED because a row means "this party is tied to an
    OFAC/EU/UN designee (as owner, board member, ...)", not that it is itself
    listed. The same register id can repeat per list, so rows are merged.
    """
    merged: Dict[str, Entity] = {}
    text = read_text(paths["main"])
    for row in csv.DictReader(io.StringIO(text), delimiter=";"):
        rid = clean(row.get("id"))
        name = clean(row.get("name")) or clean(row.get("legal_entity_name"))
        if not rid or not name:
            continue
        person = clean(row.get("entity_type")) == "NATURAL_PERSON"
        ent = merged.get(rid)
        if ent is None:
            ent = Entity(
                source=source.key,
                source_id=rid,
                schema="Person" if person else "Organization",
                list_type=source.list_type,
                listed_on=clean(row.get("entry_date")) or clean(row.get("registered_on")),
                url="https://data.gov.lv/dati/lv/dataset/sankciju-riska-subjekti",
            )
            ent.add_name(name, "primary")
            add_dates(ent, row.get("birth_date"))
            nat = clean(row.get("nationality"))
            if nat:
                _add_countries(ent.nationalities, nat)
            reg = clean(row.get("legal_entity_registration_number"))
            if reg:
                add_identifier(ent, "Registration number", reg, "lv")
            ent.countries.append(clean(row.get("legal_entity_country")).lower() or "lv")
            merged[rid] = ent
        subject = clean(row.get("sanctions_subject_name"))
        if subject and subject not in ent.linked_to:
            ent.linked_to.append(subject)
        prog = clean(row.get("program"))
        label = f"{clean(row.get('list_text'))} {prog}".strip()
        if label and label not in ent.programs:
            ent.programs.append(label)
        role = clean(row.get("position_text_en")) or clean(row.get("position"))
        link = "; ".join(p for p in (role, subject and f"of {subject}", clean(row.get("legal_base"))) if p)
        if link:
            ent.remarks = (ent.remarks + " | " if ent.remarks else "") + link
    yield from merged.values()


# --------------------------------------------------------------------------
# Czech Republic: national sanctions list (Act 1/2023)
# --------------------------------------------------------------------------

def _cz_variants(cell: str) -> List[str]:
    return [clean(v) for v in (cell or "").split("/") if clean(v)]


def parse_cz_national(paths: Dict[str, Path], source: ListSource) -> Iterator[Entity]:
    """Parse the Czech Foreign Ministry national list CSV.

    Surname and given-name cells hold slash-separated variants (Czech, English,
    Cyrillic) that line up by index; entries marked "zrušen" (cancelled) are
    skipped. Rows are positional because the header contains stray spaces.
    """
    text = read_text(paths["main"])
    latest: Dict[str, Entity] = {}
    for i, row in enumerate(csv.reader(io.StringIO(text))):
        if i == 0 or not row or len(row) < 8 or not any(row):
            continue
        status = clean(row[2]).lower()
        if status.startswith("zru"):
            continue
        surnames, givens = _cz_variants(row[0]), _cz_variants(row[1])
        if not surnames:
            continue
        dob = clean(row[4])
        person = bool(givens)
        ent = Entity(
            source=source.key,
            source_id=_slug_id(surnames[0], givens[0] if givens else "", dob),
            schema="Person" if person else "Organization",
            list_type=source.list_type,
            listed_on=(parse_dates(row[6]) or [""])[0],
            programs=["CZ national sanctions (Act 69/2006 as amended by Act 1/2023)"],
            url="https://mzv.gov.cz/jnp/cz/zahranicni_vztahy/sankce/",
        )
        for n in range(len(surnames)):
            given = givens[n] if n < len(givens) else (givens[0] if givens else "")
            add_name_auto(ent, " ".join(p for p in (given, surnames[n]) if p), "primary" if n == 0 else "alias")
        add_dates(ent, dob)
        _add_countries(ent.nationalities if person else ent.countries, row[5])
        legal = clean(row[7])
        if legal:
            ent.extra["legal_act"] = legal
        reason = clean(row[9]) if len(row) > 9 else ""
        ent.remarks = reason
        if len(row) > 8 and clean(row[8]):
            ent.extra["eu_provision"] = clean(row[8])
        # An amended entry ("změněn") repeats the party; the newest listing wins.
        prev = latest.get(ent.source_id)
        if prev is None or ent.listed_on >= prev.listed_on:
            latest[ent.source_id] = ent
    yield from latest.values()


# --------------------------------------------------------------------------
# Poland: MSWiA national sanctions list (xlsx: osoby, podmioty)
# --------------------------------------------------------------------------

_PL_MONTHS = {
    "stycznia": 1, "lutego": 2, "marca": 3, "kwietnia": 4, "maja": 5, "czerwca": 6, "lipca": 7,
    "sierpnia": 8, "września": 9, "wrzesnia": 9, "października": 10, "pazdziernika": 10,
    "listopada": 11, "grudnia": 12,
}
_PL_DATE = re.compile(r"(\d{1,2})\s+([a-ząćęłńóśźż]+)\s+(\d{4})", re.I)
_PL_IDS = re.compile(r"\b(KRS|NIP|REGON|PESEL)\s*:?\s*([0-9][0-9 -]{5,})", re.I)


def pl_dates(text: str) -> List[str]:
    """Polish-language dates ("5 października 1973") plus numeric forms."""
    out = []
    for d, mon, y in _PL_DATE.findall(text or ""):
        m = _PL_MONTHS.get(mon.lower())
        if m:
            out.append(partial_iso(y, str(m), d))
    for d in parse_dates(_PL_DATE.sub(" ", text or "")):
        if d not in out:
            out.append(d)
    return [d for d in out if d]


def _pl_names(raw: str) -> List[str]:
    """Split "SURNAME Given (ALT NAME)" / multi-line cells into name variants."""
    names: List[str] = []
    for line in (raw or "").split("\n"):
        line = clean(line)
        if not line:
            continue
        line = re.sub(r"^\(?\s*(poprzednio|dawniej|d\.)\s*:?\s*", "", line, flags=re.I)
        parts = re.split(r"[()]", line)
        for p in parts:
            p = clean(re.sub(r"^(poprzednio|dawniej|inaczej)\s*:?\s*", "", p, flags=re.I))
            if p:
                names.append(p)
    return uniq(names)


def _pl_given_first(name: str) -> str:
    """"ALAUDINOV Apti Aronovich" -> "Apti Aronovich ALAUDINOV" (list puts surname first)."""
    toks = name.split()
    lead = []
    while toks and toks[0].isupper() and len(toks[0]) > 1:
        lead.append(toks.pop(0))
    if lead and toks:
        return " ".join(toks + lead)
    return ""


def parse_pl_mswia(paths: Dict[str, Path], source: ListSource) -> Iterator[Entity]:
    """Parse both sheets; rows with a removal date (col 6) are delisted and skipped."""
    for sheet, schema in (("osoby", "Person"), ("podmioty", "Organization")):
        for row in rows_as_dicts(read_xlsx(paths["main"], sheet)):
            vals = list(row.values())
            if len(vals) < 5:
                continue
            raw_name, details, reason, measures, added = (vals + [""] * 6)[:5]
            removed = (vals + [""] * 6)[5]
            if clean(removed):
                continue
            names = _pl_names(raw_name)
            if not names:
                continue
            ent = Entity(
                source=source.key,
                source_id=_slug_id(sheet, names[0]),
                schema=schema,
                list_type=source.list_type,
                listed_on=excel_date(added),
                programs=["PL national sanctions list (Act of 13 April 2022)"],
                remarks=clean(reason)[:1500],
                url="https://www.gov.pl/web/mswia/lista-osob-i-podmiotow-objetych-sankcjami",
            )
            for i, n in enumerate(names):
                add_name_auto(ent, n, "primary" if i == 0 else "alias")
                if schema == "Person":
                    alt = _pl_given_first(n)
                    if alt:
                        ent.add_name(alt, "alias")
            for d in pl_dates(details):
                if d not in ent.birth_dates and schema == "Person":
                    ent.birth_dates.append(d)
            for kind, value in _PL_IDS.findall(details):
                add_identifier(ent, kind.upper(), re.sub(r"\s+", "", value).strip("-"), "pl" if kind.upper() != "PESEL" else "pl")
            _add_countries(ent.countries, details)
            if "KRS" in details.upper() or "NIP" in details.upper():
                if "pl" not in ent.countries:
                    ent.countries.append("pl")
            if clean(measures):
                ent.extra["measures"] = clean(measures)[:600]
            yield ent


# --------------------------------------------------------------------------

_EU_GROUPS = ["sanctions", "eu"]

SOURCES = [
    ListSource(
        key="FR_DGTRESOR_GELS_AVOIRS_JSON",
        name="France DG Trésor - Registre national des gels",
        jurisdiction="FR",
        list_type="SANCTIONS",
        urls={"main": "https://gels-avoirs.dgtresor.gouv.fr/ApiPublic/api/v1/publication/derniere-publication-fichier-json"},
        parser=parse_fr_gels,
        homepage="https://gels-avoirs.dgtresor.gouv.fr/",
        license="terms",
        groups=_EU_GROUPS + ["fr"],
        max_age_days=3,
        timeout=300,
        notes="Etalab-style open publication (verify). Includes EU, UN and French national (CMF L562) freezes, "
              "so it overlaps EU_FSF_XML_1_1 and UN_SC_CONSOLIDATED; kept out of 'core'. Freshness check: "
              ".../publication/derniere-publication-date.",
    ),
    ListSource(
        key="BE_FPS_FINANCE_CONSOLIDATED_CSV",
        name="Belgium FPS Finance - national terrorism list (consolidated list rows 'BE')",
        jurisdiction="BE",
        list_type="SANCTIONS",
        urls={"main": "https://sifi.minfin.fgov.be/public/api/consolidated-list"},
        parser=parse_be_fps,
        homepage="https://finance.belgium.be/en/treasury/financial-sanctions",
        license="terms",
        groups=_EU_GROUPS + ["be"],
        max_age_days=7,
        notes="Only Embargos=BE rows (Royal Decree 28/12/2006) are emitted; the other ~6,200 rows duplicate EU/UN. "
              "No explicit licence found (Belgian federal public information).",
    ),
    ListSource(
        key="MC_FUND_FREEZES_JSON",
        name="Monaco - fund freeze measures (Direction du Budget et du Trésor)",
        jurisdiction="MC",
        list_type="SANCTIONS",
        urls={"main": "https://geldefonds.gouv.mc/directdownload/sanctions.json"},
        parser=parse_mc_freezes,
        homepage="https://geldefonds.gouv.mc/",
        license="terms",
        groups=_EU_GROUPS + ["mc"],
        max_age_days=3,
        timeout=300,
        notes="11 MB JSON. Rejects browser User-Agents (WAF 'Request Rejected'); a plain client UA works. "
              "Mirrors EU/UN plus Monaco national measures; no explicit open licence.",
    ),
    ListSource(
        key="LT_MIGRACIJA_MAGNITSKY_JSON",
        name="Lithuania - Migration Department entry bans (Magnitsky grounds)",
        jurisdiction="LT",
        list_type="SANCTIONS",
        urls={"main": "https://www.migracija.lt/external/nam/search?pageNo=0&pageSize=500&language=lt"},
        parser=parse_lt_bans,
        homepage="https://www.migracija.lt/",
        license="terms",
        groups=_EU_GROUPS + ["lt", "baltics"],
        max_age_days=14,
        notes="pageSize=500 returns all (numFound ~285); raise it or paginate if numFound grows. Non-Magnitsky "
              "grounds are typed ENFORCEMENT per entity.",
    ),
    ListSource(
        key="LV_UR_SANCTIONS_RISK",
        name="Latvia - Register of Enterprises sanctions-risk links",
        jurisdiction="LV",
        list_type="SANCTIONS_LINKED",
        urls={"main": "https://data.gov.lv/dati/dataset/526d69b8-4b81-49a9-94a4-9acf3d601f69/resource/fa10a73f-0a10-4ce7-b0f8-f9652d572a10/download/sanctions.csv"},
        parser=parse_lv_sanctions_risk,
        homepage="https://data.gov.lv/dati/lv/dataset/sankciju-riska-subjekti",
        license="CC0",
        groups=_EU_GROUPS + ["lv", "baltics"],
        max_age_days=30,
        notes="Latvian persons/companies linked (beneficial owner, board member, ...) to OFAC/EU/UN designees.",
    ),
    ListSource(
        key="CZ_MZV_NATIONAL_SANCTIONS_CSV",
        name="Czech Republic - national sanctions list (Foreign Ministry)",
        jurisdiction="CZ",
        list_type="SANCTIONS",
        urls={"main": "https://mzv.gov.cz/file/6248997/Vnitrostatni_sankcni_seznam_2026_07_23.csv"},
        parser=parse_cz_national,
        homepage="https://mzv.gov.cz/jnp/cz/o_ministerstvu/otevrena_data/index_5.html",
        license="public",
        groups=_EU_GROUPS + ["cz"],
        max_age_days=30,
        notes="URL embeds a file id and date and changes on each republication: re-resolve from the open-data page "
              "with href regex '/file/\\d+/Vnitrostatni_sankcni_seznam_[0-9_]+\\.csv'. Small file (~25 rows).",
    ),
    ListSource(
        key="PL_MSWIA_SANCTIONS_XLSX",
        name="Poland - MSWiA national sanctions list (xlsx)",
        jurisdiction="PL",
        list_type="SANCTIONS",
        urls={"main": "https://www.gov.pl/attachment/efe8ad28-0dd8-454b-b0ab-ea58033aa902"},
        parser=parse_pl_mswia,
        homepage="https://www.gov.pl/web/mswia/lista-osob-i-podmiotow-objetych-sankcjami",
        license="public",
        groups=_EU_GROUPS + ["pl"],
        max_age_days=14,
        notes="The attachment UUID changes whenever MSWiA republishes (file tabela_lista_sankcyjna_<date>.xlsx): "
              "re-resolve from the landing page; the HTML table there can be newer than the xlsx. "
              "Rows with a removal date are skipped.",
    ),
]
