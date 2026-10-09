"""Corporate / ownership registries (context for beneficial-ownership checks).

list_type CORPORATE means INFO severity: being a PSC, a director, a trustee or a
registered company is normal and not a risk signal. The value here is
``linked_to`` and ``identifiers`` (company numbers, LEI) so an analyst can
trace who owns what once a name hits another list.

Everything is streamed. Multi-part files register part 1 only; use
``psc_part_source`` / ``basic_company_part_source`` to add more parts.
Filters (documented per parser): active records only, so dormant or dissolved
shells are not loaded.
"""

from __future__ import annotations

import csv
import io
import json
import zipfile
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, Iterator, List, Optional

from venombot.countries import to_iso2
from venombot.entities import Entity
from venombot.lists import ListSource
from venombot.lists._util import add_dates, add_identifier, clean

csv.field_size_limit(1 << 30)

# Companies House / FBI give nationality as an adjective; countries.py only knows
# country names, so map the common demonyms (extend as needed).
_DEMONYMS = {
    "british": "gb", "english": "gb", "scottish": "gb", "welsh": "gb", "northern irish": "gb", "american": "us",
    "irish": "ie", "french": "fr", "german": "de", "italian": "it", "spanish": "es", "portuguese": "pt",
    "dutch": "nl", "belgian": "be", "swiss": "ch", "austrian": "at", "swedish": "se", "norwegian": "no",
    "danish": "dk", "finnish": "fi", "polish": "pl", "czech": "cz", "romanian": "ro", "bulgarian": "bg",
    "greek": "gr", "turkish": "tr", "russian": "ru", "ukrainian": "ua", "latvian": "lv", "lithuanian": "lt",
    "estonian": "ee", "hungarian": "hu", "cypriot": "cy", "maltese": "mt", "chinese": "cn", "hong konger": "hk",
    "indian": "in", "pakistani": "pk", "bangladeshi": "bd", "japanese": "jp", "korean": "kr", "singaporean": "sg",
    "malaysian": "my", "thai": "th", "vietnamese": "vn", "indonesian": "id", "filipino": "ph", "australian": "au",
    "new zealander": "nz", "canadian": "ca", "mexican": "mx", "brazilian": "br", "argentine": "ar",
    "argentinian": "ar", "colombian": "co", "venezuelan": "ve", "chilean": "cl", "peruvian": "pe",
    "south african": "za", "nigerian": "ng", "kenyan": "ke", "ghanaian": "gh", "egyptian": "eg", "moroccan": "ma",
    "israeli": "il", "iranian": "ir", "iraqi": "iq", "saudi": "sa", "emirati": "ae", "lebanese": "lb",
    "jordanian": "jo", "syrian": "sy", "kazakh": "kz", "georgian": "ge", "armenian": "am", "albanian": "al",
    "serbian": "rs", "croatian": "hr", "slovak": "sk", "slovenian": "si", "icelandic": "is", "luxembourger": "lu",
    "dominican": "do", "jamaican": "jm", "cuban": "cu", "honduran": "hn", "guatemalan": "gt", "salvadoran": "sv",
}


def nationality_iso2(text: Optional[str]) -> Optional[str]:
    """ISO2 for a nationality adjective ("British") or a country name; None if unknown."""
    t = clean(text).lower().split(",")[0].strip()
    return _DEMONYMS.get(t) or to_iso2(t) or None


CTX = "Context only: registry presence is not wrongdoing."


def _open_member(path: Path, suffixes: tuple = ()) -> io.BufferedReader:
    """Open a file, or the first matching member if it is a zip (stream)."""
    p = Path(path)
    if zipfile.is_zipfile(p):
        z = zipfile.ZipFile(p)
        names = [n for n in z.namelist() if not n.startswith("__MACOSX") and not n.endswith("/")
                 and (not suffixes or n.lower().endswith(suffixes))]
        if not names:
            raise FileNotFoundError(f"no {suffixes} member in {p}")
        return z.open(names[0])
    return open(p, "rb")


def _csv_rows(path: Path, *, delimiter: str = ",", suffixes: tuple = (), quoting: int = csv.QUOTE_MINIMAL,
              encoding: str = "utf-8-sig") -> Iterator[Dict[str, str]]:
    """Stream CSV rows as dicts with stripped header names (Companies House pads some)."""
    with _open_member(path, suffixes) as raw:
        rdr = csv.reader(io.TextIOWrapper(raw, encoding=encoding, errors="replace", newline=""),
                         delimiter=delimiter, quoting=quoting)
        header: Optional[List[str]] = None
        for row in rdr:
            if header is None:
                header = [h.strip() for h in row]
                continue
            if row:
                yield {h: (row[i] if i < len(row) else "") for i, h in enumerate(header)}


def _org(source: ListSource, sid: str, name: str, **kw) -> Optional[Entity]:
    name = clean(name)
    if not name or not sid:
        return None
    ent = Entity(source=source.key, source_id=sid, schema="Organization", list_type=source.list_type, **kw)
    ent.add_name(name, "primary")
    return ent


# --------------------------------------------------------------- UK PSC

def parse_psc(paths: Dict[str, Path], source: ListSource) -> Iterator[Entity]:
    """Persons with significant control, one JSON object per line.

    Filters: skips ceased PSCs and super-secure (protected) records. Individuals
    -> Person; corporate / legal-person PSCs -> Organization. The snapshot has no
    company names, so ``linked_to`` carries the company number (resolve names via
    UK_CH_BASIC_COMPANY_DATA).
    """
    with _open_member(paths["main"]) as raw:
        for line in io.TextIOWrapper(raw, encoding="utf-8", errors="replace"):  # closes with raw
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            d = rec.get("data") or {}
            kind = d.get("kind") or ""
            if "super-secure" in kind or d.get("ceased_on") or d.get("ceased"):
                continue
            number = clean(rec.get("company_number"))
            name = clean(d.get("name")) or clean(" ".join(
                (d.get("name_elements") or {}).get(k, "") for k in ("forename", "middle_name", "surname")))
            if not number or not name:
                continue
            self_link = ((d.get("links") or {}).get("self") or "")
            sid = f"{number}:{self_link.rsplit('/', 1)[-1] or d.get('etag', '')[:12]}"
            person = kind.startswith("individual")
            ent = Entity(source=source.key, source_id=sid, schema="Person" if person else "Organization",
                         list_type=source.list_type, linked_to=[number],
                         listed_on=clean(d.get("notified_on")),
                         url=f"https://find-and-update.company-information.service.gov.uk/company/{number}/persons-with-significant-control")
            ent.add_name(name, "primary")
            add_identifier(ent, "UK company number (controlled company)", number, "gb")
            nat = nationality_iso2(d.get("nationality"))
            if nat:
                ent.nationalities.append(nat)
            res = to_iso2(d.get("country_of_residence") or "")
            if res:
                ent.countries.append(res)
            dob = d.get("date_of_birth") or {}
            if person and dob.get("year"):
                # Companies House only publishes month and year.
                ent.birth_dates.append(f"{int(dob['year']):04d}" + (f"-{int(dob['month']):02d}" if dob.get("month") else ""))
            ident = d.get("identification") or {}
            if ident.get("registration_number"):
                add_identifier(ent, "Registration number", ident["registration_number"],
                               to_iso2(ident.get("country_registered") or "") or "")
            ent.programs = [kind]
            ent.remarks = "; ".join([f"controls company {number}"] + [c for c in d.get("natures_of_control") or []][:6] + [CTX])
            yield ent


# ---------------------------------------------------- UK basic company data

def parse_basic_company(paths: Dict[str, Path], source: ListSource) -> Iterator[Entity]:
    """Companies House basic data. Filter: CompanyStatus == "Active" only."""
    for r in _csv_rows(paths["main"]):
        if clean(r.get("CompanyStatus")).lower() != "active":
            continue
        number = clean(r.get("CompanyNumber"))
        ent = _org(source, number, r.get("CompanyName", ""),
                   listed_on=clean(r.get("IncorporationDate")),
                   url=f"https://find-and-update.company-information.service.gov.uk/company/{number}")
        if not ent:
            continue
        add_identifier(ent, "UK company number", number, "gb")
        for i in range(1, 11):
            prev = clean(r.get(f"PreviousName_{i}.CompanyName"))
            if prev:
                ent.add_name(prev, "alias")
        ent.countries.append("gb")
        sic = clean(r.get("SICCode.SicText_1"))
        ent.remarks = "; ".join(x for x in (clean(r.get("CompanyCategory")), sic,
                                            clean(r.get("RegAddress.PostTown")), CTX) if x)
        yield ent


def _ch_dates() -> Dict[str, str]:
    now = datetime.now(timezone.utc)
    return {"day": (now - timedelta(days=1)).strftime("%Y-%m-%d"),
            "month": (now if now.day > 3 else now.replace(day=1) - timedelta(days=1)).strftime("%Y-%m-01")}


def psc_part_source(part: int, total: int = 33, date: Optional[str] = None) -> ListSource:
    """Build a ListSource for PSC snapshot part ``part`` (1..33).

    To load more of the register: ``SOURCES.append(psc_part_source(2))`` or add
    ``psc_part_source(n)`` for n in 2..33 in a custom module. The file name is
    dated daily (see https://download.companieshouse.gov.uk/en_pscdata.html);
    ``date`` defaults to yesterday UTC. Part 1 alone is ~72 MB zipped.
    """
    day = date or _ch_dates()["day"]
    key = "UK_CH_PSC_SNAPSHOT" if part == 1 else f"UK_CH_PSC_SNAPSHOT_P{part:02d}"
    return ListSource(
        key=key, name=f"UK Companies House PSC snapshot (part {part}/{total})", jurisdiction="GB",
        list_type="CORPORATE", urls={"main": f"https://download.companieshouse.gov.uk/psc-snapshot-{day}_{part}of{total}.zip"},
        parser=parse_psc, homepage="https://download.companieshouse.gov.uk/en_pscdata.html", license="OGL",
        groups=["corporate", "registry", "uk", "gb"], max_age_days=30, timeout=900,
        notes=("Active persons with significant control (JSON lines). Being a PSC is not wrongdoing. "
               f"Only part 1 of {total} is registered by default; add others with psc_part_source(n). "
               "File name is dated daily: if the date 404s, set the latest from en_pscdata.html."))


def basic_company_part_source(part: int, total: int = 7, month: Optional[str] = None) -> ListSource:
    """Build a ListSource for BasicCompanyData part ``part`` (1..7); ``month`` like "2026-10-01"."""
    mon = month or _ch_dates()["month"]
    key = "UK_CH_BASIC_COMPANY_DATA" if part == 1 else f"UK_CH_BASIC_COMPANY_DATA_P{part}"
    return ListSource(
        key=key, name=f"UK Companies House basic company data (part {part}/{total})", jurisdiction="GB",
        list_type="CORPORATE", urls={"main": f"https://download.companieshouse.gov.uk/BasicCompanyData-{mon}-part{part}_{total}.zip"},
        parser=parse_basic_company, homepage="https://download.companieshouse.gov.uk/en_output.html", license="OGL",
        groups=["corporate", "registry", "uk", "gb"], max_age_days=30, timeout=900,
        notes=("Active companies only (dissolved/dormant status rows skipped). Monthly file dated the 1st; "
               f"only part 1 of {total} registered, add others via basic_company_part_source(n). " + CTX))


# --------------------------------------------------------------- GLEIF RR

def parse_gleif_rr(paths: Dict[str, Path], source: ListSource) -> Iterator[Entity]:
    """Level-2 "who owns whom": one entity per child LEI, ``linked_to`` = parent LEIs.

    Relationship records carry no names (those are in the 480 MB lei2 file, not
    loaded), so the LEI is the primary name; use it as an identifier join key.
    Only ACTIVE relationships are kept.
    """
    parents: Dict[str, List[str]] = defaultdict(list)
    kinds: Dict[str, set] = defaultdict(set)
    for r in _csv_rows(paths["main"], suffixes=(".csv",)):
        status = clean(r.get("Relationship.RelationshipStatus")).upper()
        if status and status != "ACTIVE":
            continue
        child, parent = clean(r.get("Relationship.StartNode.NodeID")), clean(r.get("Relationship.EndNode.NodeID"))
        if not child or not parent:
            continue
        if parent not in parents[child]:
            parents[child].append(parent)
        kinds[child].add(clean(r.get("Relationship.RelationshipType")))
    for lei, plist in parents.items():
        ent = Entity(source=source.key, source_id=lei, schema="Organization", list_type=source.list_type,
                     linked_to=plist, url=f"https://search.gleif.org/#/record/{lei}")
        ent.add_name(lei, "primary")
        add_identifier(ent, "LEI", lei)
        ent.remarks = "; ".join(sorted(kinds[lei]) + ["parents: " + ", ".join(plist), CTX])
        yield ent


# ----------------------------------------------------------- UK charities

def _tsv(path: Path) -> Iterator[Dict[str, str]]:
    # QUOTE_NONE: stray quotes in free text would otherwise swallow rows.
    return _csv_rows(path, delimiter="\t", quoting=csv.QUOTE_NONE, suffixes=(".txt",))


def parse_charity(paths: Dict[str, Path], source: ListSource) -> Iterator[Entity]:
    """Registered charities (status "Registered") plus trustees linked by organisation_number.

    Trustees are People (or Organizations when ``individual_or_organisation`` says so)
    with ``linked_to`` = charity name. Both are context for NPO / terrorist-financing checks.
    """
    charities: Dict[str, str] = {}
    for r in _tsv(paths["charity"]):
        if clean(r.get("charity_registration_status")).lower() != "registered":
            continue
        org, num = clean(r.get("organisation_number")), clean(r.get("registered_charity_number"))
        name = clean(r.get("charity_name")).strip('"')
        if not org or not name or clean(r.get("linked_charity_number")) not in ("", "0"):
            continue
        charities[org] = name
        ent = _org(source, f"C{num or org}", name,
                   listed_on=clean(r.get("date_of_registration")),
                   url=f"https://register-of-charities.charitycommission.gov.uk/charity-search/-/charity-details/{org}")
        if not ent:
            continue
        add_identifier(ent, "UK charity number", num, "gb")
        add_identifier(ent, "UK company number", r.get("charity_company_registration_number"), "gb")
        ent.countries.append("gb")
        ent.remarks = "; ".join(x for x in (clean(r.get("charity_type")), CTX) if x)
        yield ent
    if "trustees" not in paths:
        return
    for r in _tsv(paths["trustees"]):
        org = clean(r.get("organisation_number"))
        tname = clean(r.get("trustee_name")).strip('"')
        if org not in charities or not tname:
            continue
        is_org = clean(r.get("individual_or_organisation")).upper().startswith("O")
        ent = Entity(source=source.key, source_id=f"T{org}:{clean(r.get('trustee_id'))}",
                     schema="Organization" if is_org else "Person", list_type=source.list_type,
                     linked_to=[charities[org]], listed_on=clean(r.get("trustee_date_of_appointment")),
                     remarks=f"Trustee of {charities[org]}" +
                             ("; chair" if clean(r.get("trustee_is_chair")).upper().startswith(("T", "Y")) else "") +
                             f". {CTX}")
        ent.add_name(tname, "primary")
        yield ent


# --------------------------------------------------------- Estonia BO

def _iter_json_array(stream: io.TextIOBase, chunk: int = 1 << 20) -> Iterator[dict]:
    """Yield elements of a huge top-level JSON array without loading it all."""
    dec = json.JSONDecoder()
    buf, pos, started = "", 0, False
    while True:
        data = stream.read(chunk)
        buf = buf[pos:] + data
        pos = 0
        if not started:
            i = buf.find("[")
            if i < 0:
                if not data:
                    return
                continue
            pos, started = i + 1, True
        while True:
            while pos < len(buf) and buf[pos] in " \r\n\t,":
                pos += 1
            if pos >= len(buf) or buf[pos] == "]":
                break
            try:
                obj, pos = dec.raw_decode(buf, pos)
            except ValueError:
                break  # element continues in next chunk
            yield obj
        if not data:
            return


def parse_estonia_bo(paths: Dict[str, Path], source: ListSource) -> Iterator[Entity]:
    """Beneficial owners per company. Filter: skips owners with an end date (lopp_kpv)."""
    with _open_member(paths["main"], (".json",)) as raw:
        for comp in _iter_json_array(io.TextIOWrapper(raw, encoding="utf-8", errors="replace")):
            code, cname = clean(str(comp.get("ariregistri_kood") or "")), clean(comp.get("nimi"))
            for bo in comp.get("kasusaajad") or []:
                if bo.get("lopp_kpv"):
                    continue
                name = clean(f"{bo.get('eesnimi') or ''} {bo.get('nimi') or ''}")
                if not name:
                    continue
                ent = Entity(source=source.key, source_id=f"{code}:{bo.get('kirje_id')}", schema="Person",
                             list_type=source.list_type, linked_to=[x for x in (cname, code) if x],
                             listed_on=clean(bo.get("algus_kpv")),
                             url=f"https://ariregister.rik.ee/eng/company/{code}")
                ent.add_name(name, "primary")
                if bo.get("synniaeg"):
                    add_dates(ent, str(bo["synniaeg"]))
                add_identifier(ent, "EE company registry code", code, "ee")
                add_identifier(ent, "Personal code", bo.get("isikukood"), "ee")
                for k in ("aadress_riik", "valis_kood_riik"):
                    iso = to_iso2(clean(bo.get(k)))
                    if iso and iso not in ent.countries:
                        ent.countries.append(iso)
                ent.remarks = "; ".join(x for x in (f"beneficial owner of {cname}",
                                                    clean(bo.get("kontrolli_teostamise_viis_tekstina")), CTX) if x)
                yield ent


# ----------------------------------------------------- CA / US registries

def parse_ca_federal(paths: Dict[str, Path], source: ListSource) -> Iterator[Entity]:
    """Active CBCA corporations (no director names in the open data)."""
    for r in _csv_rows(paths["main"]):
        if clean(r.get("Status")).lower() not in ("active", ""):
            continue
        num = clean(r.get("Corporation number"))
        ent = _org(source, num, r.get("Corporate name - form 1", ""),
                   url=f"https://ised-isde.canada.ca/cc/lgcy/fdrlCrpDtls.html?corpId={num}")
        if not ent:
            continue
        ent.add_name(clean(r.get("Corporate name - form 2")), "alias")
        add_identifier(ent, "CA corporation number", num, "ca")
        add_identifier(ent, "CA business number", r.get("Business number (BN)"), "ca")
        ent.countries.append("ca")
        ent.remarks = "; ".join(x for x in (clean(r.get("Governing legislation")), clean(r.get("City/town")),
                                            clean(r.get("Province/territory")), CTX) if x)
        yield ent


def parse_irs_eo(paths: Dict[str, Path], source: ListSource) -> Iterator[Entity]:
    """IRS EO BMF. Filter: STATUS "01" (unconditional exemption) only; EIN as identifier."""
    for r in _csv_rows(paths["main"], encoding="latin-1"):
        if clean(r.get("STATUS")) not in ("01", "1", ""):
            continue
        ein = clean(r.get("EIN"))
        ent = _org(source, ein, r.get("NAME", ""), url="https://apps.irs.gov/app/eos/")
        if not ent:
            continue
        ent.add_name(clean(r.get("SORT_NAME")), "alias")
        add_identifier(ent, "EIN", ein, "us")
        ent.countries.append("us")
        ent.remarks = "; ".join(x for x in (f"IRC 501(c) subsection {clean(r.get('SUBSECTION'))}" if clean(r.get("SUBSECTION")) else "",
                                            clean(r.get("NTEE_CD")), clean(r.get("CITY")), clean(r.get("STATE")), CTX) if x)
        yield ent


def _s(key: str, name: str, jur: str, url: str, parser, home: str, lic: str, groups: List[str], notes: str,
       urls: Optional[Dict[str, str]] = None, age: int = 30) -> ListSource:
    return ListSource(key=key, name=name, jurisdiction=jur, list_type="CORPORATE", urls=urls or {"main": url},
                      parser=parser, homepage=home, license=lic, groups=["corporate", "registry"] + groups,
                      max_age_days=age, notes=notes + " " + CTX, timeout=900)


SOURCES: List[ListSource] = [
    psc_part_source(1),
    basic_company_part_source(1),
    _s("GLEIF_GOLDEN_COPY_RR", "GLEIF Golden Copy level-2 relationship records", "GLOBAL",
       "https://goldencopy.gleif.org/api/v2/golden-copies/publishes/rr/latest.csv", parse_gleif_rr,
       "https://www.gleif.org/en/lei-data/gleif-golden-copy", "CC0", ["global"],
       "302 redirect to dated zip. Active consolidation relationships (child -> parent LEIs); no names "
       "(names are in the 480 MB lei2 file, not loaded).", age=7),
    _s("UK_CHARITY_COMMISSION_EXTRACT", "Charity Commission (E&W) register extract: charities + trustees", "GB", "",
       parse_charity, "https://register-of-charities.charitycommission.gov.uk/register/full-register-download", "OGL",
       ["uk", "gb"], "Registered charities and their trustees (individual / organisation).",
       urls={"charity": "https://ccewuksprdoneregsadata1.blob.core.windows.net/data/txt/publicextract.charity.zip",
             "trustees": "https://ccewuksprdoneregsadata1.blob.core.windows.net/data/txt/publicextract.charity_trustee.zip"}),
    _s("EE_ARIREGISTER_BENEFICIAL_OWNERS", "Estonia e-Business Register beneficial owners", "EE",
       "https://avaandmed.ariregister.rik.ee/sites/default/files/avaandmed/ettevotja_rekvisiidid__kasusaajad.json.zip",
       parse_estonia_bo, "https://avaandmed.ariregister.rik.ee/en/open-data", "terms", ["eu", "ee"],
       "Current beneficial owners (JSON array, streamed). Licence: confirm on the open-data page."),
    _s("CA_FEDERAL_CORPORATIONS", "Canada federal corporations (active CBCA)", "CA",
       "https://d4bf66bykfyaf.cloudfront.net/corporations-active-cbca-en.csv", parse_ca_federal,
       "https://ised-isde.canada.ca/cc/lgcy/fdrlCrpSrch.html", "OGL", ["ca"],
       "Open Government Licence - Canada. Only the active CBCA file (104 MB); non-CBCA is a sibling file. No director names."),
    _s("US_IRS_EO_BMF", "IRS Exempt Organizations Business Master File (region 1)", "US",
       "https://www.irs.gov/pub/irs-soi/eo1.csv", parse_irs_eo, "https://www.irs.gov/charities-non-profits/exempt-organizations-business-master-file-extract-eo-bmf",
       "public", ["us"], "Region 1 only; eo2.csv..eo4.csv cover other regions (add via copies of this source)."),
]
