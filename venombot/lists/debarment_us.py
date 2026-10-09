"""United States debarment, exclusion and regulator-enforcement registers.

Exclusions (SAM, HHS-OIG LEIE, NY OMIG, DDTC, FDA debarment) are DEBARMENT:
the party may not receive federal contracts, reimbursements or export
authority. Regulator bans (FINRA, FDA clinical disqualification, Federal
Reserve, OCC) and public warning lists (CFTC RED list, SEC PAUSE) are
ENFORCEMENT. Terminated or reinstated actions are kept and flagged
``extra["expired"]`` because screening must still show history.
"""

from __future__ import annotations

import csv
import io
import json
import re
import zipfile
from pathlib import Path
from typing import Dict, Iterator, List

from venombot.entities import Entity
from venombot.lists import ListSource
from venombot.lists._html_c import (
    apply_term, excel_date, join_remarks, looks_like_company, page_paths, parser_download,
    person_by_structure, read_tables, stable_id, tidy, to_iso, unique, workdir,
)
from venombot.lists._tables import read_xlsx, rows_as_dicts
from venombot.lists._util import add_countries, add_dates, add_identifier

csv.field_size_limit(1 << 30)

_BROWSER_UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
               "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")


def _csv_rows(path: Path, encoding: str = "utf-8-sig") -> Iterator[Dict[str, str]]:
    with open(path, newline="", encoding=encoding, errors="replace") as fh:
        for row in csv.DictReader(fh):
            yield {(k or "").strip(): (v or "").strip() for k, v in row.items()}


# ------------------------------------------------------------------------ SAM
_SAM_BASE = "https://sam.gov/api/prod/fileextractservices/v1/api/download/Exclusions/Public%20V2/"


def newest_sam_file(listing: dict) -> str:
    """Newest extract name from the SAM file listing (YYDDD julian stamp)."""
    items = (listing.get("_embedded") or {}).get("customS3ObjectSummaryList") or []
    best, best_n = "", -1
    for it in items:
        key = it.get("displayKey") or ""
        m = re.search(r"_V2_(\d+)\.zip$", key, re.IGNORECASE)
        if m and int(m.group(1)) > best_n:
            best, best_n = key, int(m.group(1))
    if not best:
        raise RuntimeError("no SAM exclusions extract in file listing")
    return best


def parse_sam(paths: Dict[str, Path], source: ListSource) -> Iterator[Entity]:
    """SAM.gov exclusions (public V2 extract; no API key needed).

    The file name carries the julian date, so the declared URL is the file
    listing and the parser downloads the newest zip, streaming its CSV.
    """
    listing = json.loads(Path(paths["listing"]).read_text(encoding="utf-8"))
    name = newest_sam_file(listing)
    with workdir() as tmp:
        zpath = parser_download(f"{_SAM_BASE}{name}?privacy=Public", Path(tmp) / "sam.zip")
        with zipfile.ZipFile(zpath) as z:
            member = next(n for n in z.namelist() if n.lower().endswith(".csv"))
            with z.open(member) as raw:
                yield from _sam_rows(io.TextIOWrapper(raw, encoding="utf-8-sig", errors="replace", newline=""), source)


def _sam_rows(handle, source: ListSource) -> Iterator[Entity]:
    seen = set()
    for row in csv.DictReader(handle):
        cls = tidy(row.get("Classification"))
        if cls == "Individual":
            parts = [row.get(k, "") for k in ("First", "Middle", "Last", "Suffix")]
            full = " ".join(p for p in parts if p.strip())
            schema = "Person"
        else:
            full = row.get("Name", "")
            schema = {"Vessel": "Vessel"}.get(cls, "Organization")
        full = tidy(full)
        sid = tidy(row.get("SAM Number")) or stable_id(full, row.get("Active Date"), row.get("Excluding Agency"))
        if not full or sid in seen:
            continue
        seen.add(sid)
        ent = Entity(source=source.key, source_id=sid, schema=schema, list_type=source.list_type,
                     url=source.homepage)
        ent.add_name(full, "primary")
        if schema == "Person" and row.get("Last"):
            ent.add_name(f"{tidy(row.get('Last'))}, {tidy(row.get('First'))} {tidy(row.get('Middle'))}".strip(), "alias")
        add_countries(ent.countries, row.get("Country"))
        add_identifier(ent, "Unique Entity ID", row.get("Unique Entity ID"), "us")
        add_identifier(ent, "CAGE", row.get("CAGE"), "us")
        add_identifier(ent, "NPI", row.get("NPI"), "us")
        ent.programs.append(tidy(row.get("Exclusion Program")) or "SAM exclusion")
        end = to_iso(row.get("Termination Date"))
        apply_term(ent, to_iso(row.get("Active Date")), end)
        if tidy(row.get("Record Status")).lower() == "inactive":
            ent.extra["expired"] = True
        addr = ", ".join(p for p in (tidy(row.get(k)) for k in ("Address 1", "City", "State / Province", "Country")) if p)
        ent.remarks = join_remarks(
            f"Exclusion type: {tidy(row.get('Exclusion Type'))}", f"Program: {tidy(row.get('Exclusion Program'))}",
            f"Excluding agency: {tidy(row.get('Excluding Agency'))}", f"Basis (CT): {tidy(row.get('CT Code'))}"
            if row.get("CT Code") else "", tidy(row.get("Additional Comments"))[:300],
            f"Address: {addr}" if addr else "")
        yield ent


# ----------------------------------------------------------------------- LEIE
_LEIE_TYPES = {
    "1128a1": "conviction of program-related crimes", "1128a2": "conviction relating to patient abuse/neglect",
    "1128a3": "felony conviction relating to health care fraud", "1128a4": "felony conviction relating to controlled substances",
    "1128b1": "misdemeanor conviction relating to health care fraud", "1128b2": "conviction relating to obstruction of an investigation",
    "1128b3": "misdemeanor conviction relating to controlled substances", "1128b4": "license revocation or suspension",
    "1128b5": "exclusion or suspension under a federal or state health care program",
    "1128b6": "claims for excessive charges or unnecessary services", "1128b7": "fraud, kickbacks and other prohibited activities",
    "1128b8": "entities controlled by a sanctioned individual", "1128b14": "default on health education loan or scholarship",
    "1128b15": "individuals controlling a sanctioned entity", "1156": "failure to meet statutory obligations of practitioners",
}


@unique
def parse_leie(paths: Dict[str, Path], source: ListSource) -> Iterator[Entity]:
    """HHS-OIG List of Excluded Individuals/Entities (monthly UPDATED.csv)."""
    for row in _csv_rows(paths["main"]):
        bus, last, first, mid = (tidy(row.get(k)) for k in ("BUSNAME", "LASTNAME", "FIRSTNAME", "MIDNAME"))
        person = bool(last or first) and not bus
        if not (bus or last):
            continue
        sid = stable_id(bus, last, first, mid, row.get("DOB"), row.get("EXCLDATE"), row.get("EXCLTYPE"), row.get("NPI"))
        ent = Entity(source=source.key, source_id=sid, schema="Person" if person else "Organization",
                     list_type=source.list_type, url=source.homepage)
        if person:
            ent.add_name(" ".join(p for p in (first, mid, last) if p), "primary")
            ent.add_name(f"{last}, {first} {mid}".strip(), "alias")
            add_dates(ent, "-".join([row.get("DOB", "")[:4], row.get("DOB", "")[4:6], row.get("DOB", "")[6:8]])
                      if len(row.get("DOB", "")) == 8 else "")
        else:
            ent.add_name(bus, "primary")
        npi = tidy(row.get("NPI"))
        if npi and set(npi) != {"0"}:
            add_identifier(ent, "NPI", npi, "us")
        add_identifier(ent, "UPIN", row.get("UPIN"), "us")
        ent.countries.append("us")
        code = tidy(row.get("EXCLTYPE"))
        ent.programs.append(f"LEIE {code}")
        rein = to_iso(row.get("REINDATE"))
        apply_term(ent, to_iso(row.get("EXCLDATE")), rein)
        if rein:
            ent.extra["reinstated"] = True
        addr = ", ".join(p for p in (tidy(row.get(k)) for k in ("ADDRESS", "CITY", "STATE", "ZIP")) if p)
        ent.remarks = join_remarks(
            f"Exclusion type: {code} ({_LEIE_TYPES.get(code, 'see SSA section')})",
            f"Provider type: {tidy(row.get('GENERAL'))} / {tidy(row.get('SPECIALTY'))}".strip(" /"),
            f"Address: {addr}" if addr else "", "Authority: HHS Office of Inspector General")
        yield ent


# ----------------------------------------------------------------------- OMIG
@unique
def parse_omig(paths: Dict[str, Path], source: ListSource) -> Iterator[Entity]:
    """New York OMIG Medicaid exclusions (xlsx; person vs entity by name shape)."""
    for i, row in enumerate(rows_as_dicts(read_xlsx(paths["main"]))):
        name = tidy(row.get("provider_name"))
        if not name:
            continue
        ent = Entity(source=source.key, source_id=stable_id(name, row.get("npi_num"), row.get("exclusion_effective_date")),
                     schema=person_by_structure(name), list_type=source.list_type, url=source.homepage)
        ent.add_name(name, "primary")
        if ent.schema == "Person" and "," in name:
            last, _, first = name.partition(",")
            ent.add_name(f"{first.strip()} {last.strip()}", "alias")
        add_identifier(ent, "NPI", row.get("npi_num"), "us")
        add_identifier(ent, "License", row.get("license_num"), "us")
        ent.countries.append("us")
        ent.programs.append("NY Medicaid exclusion")
        apply_term(ent, to_iso(row.get("exclusion_effective_date"), "mdy"), "")
        ent.remarks = join_remarks(f"Provider type: {tidy(row.get('provider_type'))}",
                                   "Authority: NY Office of the Medicaid Inspector General")
        yield ent


# ----------------------------------------------------------------------- DDTC
@unique
def parse_ddtc(paths: Dict[str, Path], source: ListSource) -> Iterator[Entity]:
    """DDTC ITAR debarred parties: statutory sheet (with DOB) + administrative sheet."""
    for key in ("statutory", "administrative"):
        if key not in paths:
            continue
        for row in rows_as_dicts(read_xlsx(paths[key])):
            raw = tidy(row.get("Party Name") or row.get("Name"))
            if not raw:
                continue
            parts = [p.strip() for p in raw.split(";") if p.strip()]
            ent = Entity(source=source.key, source_id=f"{key[:4]}-{stable_id(raw, row.get('Federal Register Notice'))}",
                         schema=person_by_structure(parts[0]), list_type=source.list_type, url=source.homepage)
            for p in parts:
                ent.add_name(p, "primary" if p == parts[0] else "alias")
            if ent.schema == "Person" and "," in parts[0]:
                last, _, first = parts[0].partition(",")
                ent.add_name(f"{first.strip()} {last.strip()}", "alias")
            add_dates(ent, row.get("Date Of Birth"))
            when = excel_date(row.get("Notice Date") or row.get("Date"))
            ent.programs.append(f"ITAR debarment ({key})")
            apply_term(ent, when, "")
            ent.countries.append("us")
            ent.remarks = join_remarks(
                f"Federal Register: {tidy(row.get('Federal Register Notice'))}",
                f"Correction: {tidy(row.get('Corrected Notice'))}" if row.get("Corrected Notice") else "",
                f"Register: {'statutory' if key == 'statutory' else 'administrative'} debarment",
                "Authority: State Department DDTC")
            yield ent


# ------------------------------------------------------------------------ FDA
_TERM_YEARS = re.compile(r"(\d+)\s*year", re.IGNORECASE)


@unique
def parse_fda_debarment(paths: Dict[str, Path], source: ListSource) -> Iterator[Entity]:
    """FDA debarment list: firms table then persons table (term text with markers)."""
    for table in read_tables(paths["main"]):
        if not table or not table[0]:
            continue
        head = [c.text.lower() for c in table[0]]
        firms = head[0].startswith("name of firm")
        persons = head[0].startswith("last name")
        if not (firms or persons):
            continue
        for row in table[1:]:
            if len(row) < 4 or not row[0].text or row[0].text.lower().startswith("none as of"):
                continue
            if persons:
                last, first = row[0].text, row[1].text
                eff, term, fr = row[2].text, row[3].text, row[-1]
                name = f"{first} {last}".strip()
            else:
                last = first = ""
                name = row[0].text
                eff, term, fr = row[1].text, row[2].text, row[-1]
            ent = Entity(source=source.key, source_id=stable_id(name, eff, term), schema="Person" if persons else "Organization",
                         list_type=source.list_type, url=fr.href or source.homepage)
            ent.add_name(name, "primary")
            if persons:
                ent.add_name(f"{last}, {first}", "alias")
            start = to_iso(eff, "mdy")
            years = _TERM_YEARS.search(term)
            end = ""
            if years and start:
                end = f"{int(start[:4]) + int(years.group(1))}{start[4:]}"
            permanent = "permanent" in term.lower()
            apply_term(ent, start, end, permanent=permanent)
            ent.countries.append("us")
            ent.programs.append("FDA debarment")
            clean_term = re.sub(r"[\^%*#]", "", term).strip()
            ent.remarks = join_remarks(f"Term: {clean_term}",
                                       f"Federal Register: {fr.text}" if fr.text else "",
                                       "Authority: FDA (drug product applications debarment)")
            yield ent


@unique
def parse_fda_clinical(paths: Dict[str, Path], source: ListSource) -> Iterator[Entity]:
    """FDA clinical investigators disqualification proceedings (HTML served as .xls)."""
    for table in read_tables(paths["main"]):
        if not table or not table[0] or table[0][0].text.lower() != "name":
            continue
        for row in table[1:]:
            if len(row) < 6 or not row[0].text:
                continue
            name = row[0].text
            status = row[4].text
            ent = Entity(source=source.key, source_id=stable_id(name, row[2].text, row[3].text, row[5].text),
                         schema="Person", list_type=source.list_type, url=source.homepage)
            ent.add_name(name, "primary")
            m = re.match(r"^(.*?),\s*(.+)$", name)
            if m:
                ent.add_name(f"{m.group(2)} {m.group(1)}", "alias")
            ent.countries.append("us")
            ent.programs.append("FDA clinical investigator disqualification")
            apply_term(ent, to_iso(row[5].text, "mdy"), "")
            if re.search(r"not disqualified|reinstated|removed|adequate assurances", status, re.IGNORECASE):
                ent.extra["expired"] = True  # cleared: kept as history
            ent.remarks = join_remarks(f"Status: {status}", f"Center: {row[1].text}",
                                       f"Location: {row[2].text}, {row[3].text}", "Authority: FDA")
            yield ent


# ---------------------------------------------------------------------- FINRA
@unique
def parse_finra(paths: Dict[str, Path], source: ListSource) -> Iterator[Entity]:
    """FINRA individuals barred (CRD number + name; letter header rows skipped)."""
    for table in read_tables(paths["main"]):
        if not table or [c.text.lower() for c in table[0][:2]] != ["crd", "individual name"]:
            continue
        for row in table[1:]:
            if len(row) < 2 or not row[0].text.isdigit() or not row[1].text:
                continue
            ent = Entity(source=source.key, source_id=row[0].text, schema="Person", list_type=source.list_type,
                         url=row[1].href or source.homepage)
            ent.add_name(row[1].text, "primary")
            add_identifier(ent, "CRD", row[0].text, "us")
            ent.countries.append("us")
            ent.programs.append("FINRA bar")
            ent.remarks = "Barred from association with FINRA member firms | Authority: FINRA"
            yield ent


# ----------------------------------------------------------------------- CFTC
def parse_cftc(paths: Dict[str, Path], source: ListSource) -> Iterator[Entity]:
    """CFTC RED list (unregistered foreign forex entities), paged HTML."""
    seen = set()
    for path in page_paths(paths):
        for table in read_tables(path):
            for row in table:
                if len(row) < 2 or not re.fullmatch(r"\d\d/\d\d/\d{4}", row[0].text) or not row[1].text:
                    continue
                sid = stable_id(row[1].text, row[0].text)
                if sid in seen:
                    continue
                seen.add(sid)
                ent = Entity(source=source.key, source_id=sid, schema="Organization", list_type=source.list_type,
                             url=("https://www.cftc.gov" + row[1].href) if row[1].href.startswith("/") else source.homepage)
                ent.add_name(row[1].text, "primary")
                ent.listed_on = to_iso(row[0].text, "mdy")
                ent.programs.append("CFTC RED list")
                ent.remarks = "Entity on the CFTC Registration Deficient (RED) list: not registered to solicit US customers | Authority: CFTC"
                yield ent


# ------------------------------------------------------------------------ SEC
def parse_sec_pause(paths: Dict[str, Path], source: ListSource) -> Iterator[Entity]:
    """SEC PAUSE list: unregistered soliciting entities, impersonators, fictitious regulators."""
    seen = set()
    for path in page_paths(paths):
        for table in read_tables(path):
            if not table or [c.text.lower() for c in table[0][:2]] != ["name", "category"]:
                continue
            for row in table[1:]:
                if len(row) < 2 or not row[0].text:
                    continue
                sid = (row[0].href.rsplit("/", 1)[-1] if row[0].href else "") or stable_id(row[0].text)
                if sid in seen:
                    continue
                seen.add(sid)
                ent = Entity(source=source.key, source_id=sid, schema="Organization", list_type=source.list_type,
                             url=("https://www.sec.gov" + row[0].href) if row[0].href.startswith("/") else source.homepage)
                ent.add_name(row[0].text, "primary")
                ent.programs.append("SEC PAUSE")
                ent.topics.append("fraud")
                ent.remarks = join_remarks(f"Category: {row[1].text}", "Authority: US SEC (public alert)")
                yield ent


# ------------------------------------------------------------------ OCC / Fed
def _finish_action(ent: Entity, start: str, end: str, source: ListSource) -> None:
    """Actions are history once terminated; keep them with expired=True."""
    apply_term(ent, start, end)
    ent.countries.append("us")


@unique
def parse_occ(paths: Dict[str, Path], source: ListSource) -> Iterator[Entity]:
    """OCC enforcement actions export (JSON array; individuals and institutions)."""
    data = json.loads(Path(paths["main"]).read_text(encoding="utf-8-sig"))
    for i, rec in enumerate(data):
        indiv = tidy(rec.get("Individual"))
        org = tidy(rec.get("Company")) or tidy(rec.get("Institution"))
        name = indiv or org
        if not name:
            continue
        sid = stable_id(name, rec.get("DocketNumber"), rec.get("StartDate"), rec.get("TypeCode"), indiv and rec.get("Institution"))
        ent = Entity(source=source.key, source_id=sid, schema="Person" if indiv else "Organization",
                     list_type=source.list_type, url=source.homepage)
        ent.add_name(name, "primary")
        if indiv and tidy(rec.get("Institution")):
            ent.linked_to.append(tidy(rec.get("Institution")))
        ent.programs.append(f"OCC {tidy(rec.get('TypeCode'))}")
        _finish_action(ent, to_iso(rec.get("StartDate"), "mdy"), to_iso(rec.get("TerminationDate"), "mdy"), source)
        amount = tidy(rec.get("Amount"))
        ent.remarks = join_remarks(
            f"Action: {tidy(rec.get('TypeDescription'))}", f"Docket: {tidy(rec.get('DocketNumber'))}",
            f"Institution: {tidy(rec.get('Institution'))}" if indiv else "",
            f"Amount: {amount}" if amount and amount not in ("0.00", "0") else "",
            f"Location: {tidy(rec.get('Location'))}" if rec.get("Location") else "",
            "Authority: Office of the Comptroller of the Currency")
        yield ent


@unique
def parse_fed(paths: Dict[str, Path], source: ListSource) -> Iterator[Entity]:
    """Federal Reserve enforcement actions CSV (Individual or Banking Organization)."""
    for row in _csv_rows(paths["main"]):
        indiv = tidy(row.get("Individual"))
        org = tidy(row.get("Banking Organization"))
        name = indiv or org
        if not name:
            continue
        ent = Entity(source=source.key, source_id=stable_id(name, row.get("Effective Date"), row.get("Action"), row.get("URL")),
                     schema="Person" if indiv else "Organization", list_type=source.list_type,
                     url=("https://www.federalreserve.gov" + row["URL"]) if row.get("URL", "").startswith("/") else source.homepage)
        ent.add_name(name, "primary")
        if indiv and tidy(row.get("Individual Affiliation")):
            ent.linked_to.append(tidy(row.get("Individual Affiliation")))
        ent.programs.append(f"Fed {tidy(row.get('Action'))}")
        _finish_action(ent, to_iso(row.get("Effective Date")), to_iso(row.get("Termination Date")), source)
        ent.remarks = join_remarks(f"Action: {tidy(row.get('Action'))}",
                                   f"Affiliation: {tidy(row.get('Individual Affiliation'))}" if indiv else "",
                                   tidy(row.get("Note")), "Authority: Federal Reserve Board")
        yield ent


_LEIE_HOME = "https://oig.hhs.gov/exclusions/exclusions_list.asp"

SOURCES: List[ListSource] = [
    ListSource(
        key="US_SAM_EXCLUSIONS", name="US SAM.gov exclusions (federal debarment)", jurisdiction="US",
        list_type="DEBARMENT",
        urls={"listing": "https://sam.gov/api/prod/fileextractservices/v1/api/listfiles?domain=Exclusions/Public%20V2&random=1"},
        parser=parse_sam, homepage="https://sam.gov/exclusions", license="public",
        groups=["debarment", "us"], max_age_days=3, timeout=300,
        notes="No API key needed for the public V2 extract. The declared URL is the file listing; the parser "
              "downloads the newest julian-dated zip (about 12 MB, 78 MB CSV, streamed)."),
    ListSource(
        key="US_HHS_OIG_LEIE", name="HHS-OIG List of Excluded Individuals/Entities", jurisdiction="US",
        list_type="DEBARMENT", urls={"main": "https://oig.hhs.gov/exclusions/downloadables/UPDATED.csv"},
        parser=parse_leie, homepage=_LEIE_HOME, license="public", groups=["debarment", "us"], max_age_days=35,
        timeout=300, notes="About 16 MB; the server ignores Range. DNS for oig.hhs.gov was flaky: retries apply."),
    ListSource(
        key="US_NY_OMIG_EXCLUSIONS", name="NY OMIG Medicaid exclusions", jurisdiction="US-NY", list_type="DEBARMENT",
        urls={"main": "https://apps.omig.ny.gov/exclusions/exporttoexcel.aspx"}, parser=parse_omig,
        homepage="https://omig.ny.gov/medicaid-fraud/medicaid-terminations-and-exclusions", license="public",
        groups=["debarment", "us"], max_age_days=14,
        notes="Person vs entity is inferred from the name shape (no type column)."),
    ListSource(
        key="US_DDTC_DEBARRED", name="State Dept DDTC ITAR debarred parties", jurisdiction="US", list_type="DEBARMENT",
        urls={"statutory": "https://www.pmddtc.state.gov/sys_attachment.do?sys_id=27c46b251baf29102b6ca932f54bcb20",
              "administrative": "https://www.pmddtc.state.gov/sys_attachment.do?sys_id=9f8bbc2f1b8f29d0c6c3866ae54bcbdb"},
        parser=parse_ddtc,
        homepage="https://www.pmddtc.state.gov/ddtc_public/ddtc_public?id=ddtc_kb_article_page&sys_id=c22d1833dbb8d300d0a370131f9619f0",
        license="public", groups=["debarment", "us", "export"], max_age_days=30,
        notes="ServiceNow attachment ids are stable until DDTC replaces the file; if a download 404s, re-read "
              "the sys_ids from the DDTC debarred-parties page. Overlaps the CSL ITAR list but adds DOB."),
    ListSource(
        key="US_FDA_DEBARMENT", name="FDA debarment list (drug product applications)", jurisdiction="US",
        list_type="DEBARMENT",
        urls={"main": "https://www.fda.gov/inspections-compliance-enforcement-and-criminal-investigations/"
                      "compliance-actions-and-activities/fda-debarment-list-drug-product-applications"},
        parser=parse_fda_debarment,
        homepage="https://www.fda.gov/inspections-compliance-enforcement-and-criminal-investigations/"
                 "compliance-actions-and-activities/fda-debarment-list-drug-product-applications",
        license="public", groups=["debarment", "us"], max_age_days=30),
    ListSource(
        key="US_FDA_CLINICAL_DISQUALIFIED", name="FDA clinical investigators disqualification proceedings",
        jurisdiction="US", list_type="ENFORCEMENT",
        urls={"main": "https://www.accessdata.fda.gov/scripts/SDA/sdExportData.cfm?"
                      "sd=clinicalinvestigatorsdisqualificationproceedings&exportType=msexcel"},
        parser=parse_fda_clinical,
        homepage="https://www.accessdata.fda.gov/scripts/SDA/sdNavigation.cfm?sd=clinicalinvestigatorsdisqualificationproceedings",
        license="public", groups=["enforcement", "us"], max_age_days=30,
        notes="Served as an HTML table with an .xls name. Cleared/reinstated investigators are kept with expired=True."),
    ListSource(
        key="US_FINRA_BARRED", name="FINRA individuals barred", jurisdiction="US", list_type="ENFORCEMENT",
        urls={"main": "https://www.finra.org/rules-guidance/enforcement/individuals-barred-finra"},
        parser=parse_finra, homepage="https://www.finra.org/rules-guidance/enforcement/individuals-barred-finra",
        license="terms", groups=["enforcement", "us"], max_age_days=14, timeout=240,
        notes="Single 1.6 MB page; CRD number gives the BrokerCheck id."),
    ListSource(
        key="US_CFTC_RED_LIST", name="CFTC Registration Deficient (RED) list", jurisdiction="US", list_type="ENFORCEMENT",
        urls={f"p{i:02d}": f"https://www.cftc.gov/LearnAndProtect/Resources/Check/redlist.htm?page={i}" for i in range(36)},
        parser=parse_cftc, homepage="https://www.cftc.gov/LearnAndProtect/Resources/Check/redlist.htm",
        license="public", groups=["enforcement", "us"], max_age_days=30, headers={"User-Agent": _BROWSER_UA},
        notes="Drupal view of 10 rows per page (29 pages at 2026-10); 36 fixed pages leave headroom, extra pages are empty."),
    ListSource(
        key="US_SEC_PAUSE", name="SEC PAUSE - unregistered soliciting entities and impersonators", jurisdiction="US",
        list_type="ENFORCEMENT",
        urls={f"p{i:02d}": f"https://www.sec.gov/enforcement-litigation/public-alerts-unregistered-soliciting-entities?page={i}"
              for i in range(20)},
        parser=parse_sec_pause,
        homepage="https://www.sec.gov/enforcement-litigation/public-alerts-unregistered-soliciting-entities",
        license="public", groups=["enforcement", "us"], max_age_days=30,
        api_key_env="SEC_CONTACT_EMAIL", headers={"User-Agent": "VenomBot/1.0 {api_key}"},
        notes="sec.gov answers 403 unless the User-Agent carries a contact address (fair-access policy). Set "
              "SEC_CONTACT_EMAIL to your own contact; it is only sent to sec.gov. About 100 rows per page, 17 pages."),
    ListSource(
        key="US_OCC_ENFORCEMENT", name="OCC enforcement actions", jurisdiction="US", list_type="ENFORCEMENT",
        urls={"main": "https://apps.occ.gov/EASearch/Search/ExportToJSON?Search=&StartDateMinimum=&StartDateMaximum="
                      "&TerminationDateMinimum=&TerminationDateMaximum=&ShowIndividualActionsOnly=false"
                      "&ShowInstitutionActionsOnly=false&ShowTerminatedActionsOnly=false&ShowActiveOnly=false&Category="
                      "&Sort=BankName&AutoCompleteSelection=&CurrentPageIndex=0&ItemsPerPage=10&view=Table&IsAdvanced=true"},
        parser=parse_occ, homepage="https://apps.occ.gov/EASearch", license="public", groups=["enforcement", "us"],
        max_age_days=14, timeout=300, notes="The export returns the whole dataset (3.4 MB) regardless of ItemsPerPage."),
    ListSource(
        key="US_FED_ENFORCEMENT", name="Federal Reserve enforcement actions", jurisdiction="US", list_type="ENFORCEMENT",
        urls={"main": "https://www.federalreserve.gov/supervisionreg/files/enforcementactions.csv"},
        parser=parse_fed, homepage="https://www.federalreserve.gov/supervisionreg/enforcementactions.htm",
        license="public", groups=["enforcement", "us"], max_age_days=14),
]
