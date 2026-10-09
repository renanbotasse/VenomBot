"""International / multilateral debarment registers.

ADB, World Bank, EIB, EU EDES and Canada's PSPC ineligibility list. These are
public exclusion registers: the listed parties (firms and individuals) were
sanctioned by an institution for fraud, corruption or similar practices, and
cross-debarment between the banks means one party often appears in several.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, Iterator, List

from venombot.entities import Entity
from venombot.lists import ListSource
from venombot.lists._html_c import (
    apply_term, epoch_ms_date, entity_countries, join_remarks, parser_download,
    read_tables, tidy, to_iso, workdir,
)
from venombot.lists._util import add_countries, add_identifier, clean

_PERMANENT_WORDS = ("further notice", "indefinite", "permanent", "2999")


# ------------------------------------------------------------------------ ADB
def parse_adb(paths: Dict[str, Path], source: ListSource) -> Iterator[Entity]:
    """ADB published sanctions (JSON:API pages of 100).

    The API caps ``size`` at 100, so the source lists a fixed set of offsets;
    pages past the end return an empty ``data`` array and are harmless.
    """
    seen = set()
    for key in sorted(paths):
        data = json.loads(Path(paths[key]).read_text(encoding="utf-8")).get("data") or []
        for rec in data:
            a = rec.get("attributes") or {}
            sid = rec.get("id") or ""
            name = tidy(a.get("name"))
            if not name or sid in seen:
                continue
            seen.add(sid)
            is_person = (a.get("entityType") or "").lower().startswith("indiv")
            ent = Entity(source=source.key, source_id=sid, schema="Person" if is_person else "Organization",
                         list_type=source.list_type, url=source.homepage)
            ent.add_name(name, "primary")
            other = tidy(a.get("otherName"))
            if other.lower().startswith(("registration", "reg.", "company no")):
                add_identifier(ent, "Registration number", other.split("No.", 1)[-1].strip(" .:"))
            else:
                for alias in other.split(";"):
                    ent.add_name(alias, "alias")
            add_countries(ent.nationalities, a.get("nationality"))
            entity_countries(ent, a.get("address"), a.get("nationality"))
            ent.programs.append(tidy(a.get("sanctionType")) or "ADB sanction")
            end_raw = tidy(a.get("lapseDateOfSanction"))
            end = to_iso(end_raw)
            apply_term(ent, to_iso(a.get("effectiveDateOfSanction")), end,
                       permanent=not end and any(w in end_raw.lower() for w in _PERMANENT_WORDS))
            ent.remarks = join_remarks(
                f"Sanction: {tidy(a.get('sanctionType'))}", f"Grounds: {tidy(a.get('grounds'))}",
                "Address: " + tidy(a.get("address")) if a.get("address") else "",
                "Authority: Asian Development Bank")
            yield ent


# ------------------------------------------------------------------------- WB
def parse_wb(paths: Dict[str, Path], source: ListSource) -> Iterator[Entity]:
    """World Bank debarred firms and individuals (one JSON document)."""
    body = json.loads(Path(paths["main"]).read_text(encoding="utf-8"))
    for rec in (body.get("response") or {}).get("ZPROCSUPP") or []:
        name = tidy(rec.get("SUPP_NAME"))
        if not name:
            continue
        code = rec.get("SUPP_TYPE_CODE")
        schema = "Person" if code == "I" else "Organization" if code in ("F", "C") else "Unknown"
        ent = Entity(source=source.key, source_id=str(rec.get("SUPP_ID")), schema=schema,
                     list_type=source.list_type, url=source.homepage)
        for pre in ("MR. ", "MS. ", "MRS. ", "DR. "):
            if schema == "Person" and name.upper().startswith(pre):
                ent.add_name(name[len(pre):], "primary")
                ent.add_name(name, "alias")
                break
        else:
            ent.add_name(name, "primary")
        add_countries(ent.countries, rec.get("LAND1"))
        add_countries(ent.countries, rec.get("COUNTRY_NAME"))
        if schema == "Person":
            ent.nationalities = list(ent.countries)
        to_raw = tidy(rec.get("DEBAR_TO_DATE"))
        apply_term(ent, to_iso(rec.get("DEBAR_FROM_DATE")),
                   "" if to_raw.startswith("2999") else to_iso(to_raw),
                   permanent=to_raw.startswith("2999"))
        cross = rec.get("INELIG_FLG") == "X"
        ent.programs.append("World Bank cross-debarment (X)" if cross else "World Bank debarment")
        addr = ", ".join(p for p in (tidy(rec.get("SUPP_ADDR")), tidy(rec.get("SUPP_CITY")),
                                     tidy(rec.get("COUNTRY_NAME"))) if p)
        ent.remarks = join_remarks(
            f"Status: {tidy(rec.get('SUPP_ELIG_STAT'))} ({tidy(rec.get('INELIGIBLY_STATUS'))})",
            f"Grounds: {tidy(rec.get('DEBAR_REASON'))}",
            f"Footnote: {tidy(rec.get('ADD_SUPP_INFO'))}" if rec.get("ADD_SUPP_INFO") else "",
            f"Address: {addr}" if addr else "", "Authority: World Bank Group")
        yield ent


# ------------------------------------------------------------------------ EIB
def parse_eib(paths: Dict[str, Path], source: ListSource) -> Iterator[Entity]:
    """EIB exclusion decisions (single HTML table; dates D.M.YYYY)."""
    for table in read_tables(paths["main"]):
        if not table or [c.text.lower() for c in table[0][:2]] != ["name", "country of incorporation"]:
            continue
        for i, row in enumerate(table[1:]):
            if len(row) < 5 or not row[0].text:
                continue
            name = row[0].text
            if name.lower().endswith("public statement"):
                name = name[: -len("public statement")].strip()
            ent = Entity(source=source.key, source_id=f"{i}-{name[:40]}", schema="Organization",
                         list_type=source.list_type, url=row[0].href or source.homepage)
            base, _, paren = name.partition("(")
            ent.add_name(base.strip() if paren else name, "primary")
            if paren:  # "(CCCC)" acronym or local-script name
                ent.add_name(paren.rstrip(") ").strip(), "alias")
                ent.add_name(name, "alias")
            add_countries(ent.countries, row[1].text)
            apply_term(ent, to_iso(row[3].text, "dmy"), to_iso(row[4].text, "dmy"))
            ent.programs.append("EIB exclusion")
            ent.remarks = join_remarks(
                f"Decision: {row[2].text}", f"Proceedings: {row[5].text}" if len(row) > 5 else "",
                "Authority: European Investment Bank")
            yield ent


# ------------------------------------------------------------------- Canada
def parse_ca_pspc(paths: Dict[str, Path], source: ListSource) -> Iterator[Entity]:
    """Canada PSPC ineligible / suspended suppliers (dates 'June 17, 2020')."""
    for table in read_tables(paths["main"]):
        if not table or not table[0] or not table[0][0].text.lower().startswith("supplier"):
            continue
        for i, row in enumerate(table[1:]):
            if len(row) < 3 or not row[0].text:
                continue
            ent = Entity(source=source.key, source_id=f"{i}-{row[0].text[:40]}", schema="Organization",
                         list_type=source.list_type, url=source.homepage)
            ent.add_name(row[0].text, "primary")
            ent.countries.append("ca")
            apply_term(ent, to_iso(row[3].text, "mdy") if len(row) > 3 else "",
                       to_iso(row[4].text, "mdy") if len(row) > 4 else "")
            ent.programs.append(f"PSPC {row[2].text or 'ineligible'}")
            ent.remarks = join_remarks(f"Status: {row[2].text}", f"Address: {row[1].text}",
                                       "Authority: Public Services and Procurement Canada (Integrity Regime)")
            yield ent


# ----------------------------------------------------------------------- EDES
def parse_edes(paths: Dict[str, Path], source: ListSource) -> Iterator[Entity]:
    """EU EDES published exclusion cases.

    The API only answers POST, so the parser performs it itself; the declared
    URL (the public EDES page) is only a cheap reachability stub.
    """
    page = 0
    with workdir() as tmp:
        while True:
            body = json.dumps({"ecOpCountryCode": None, "ecOpName": None, "page": page, "take": 100}).encode()
            out = parser_download(
                "https://ec.europa.eu/edes/api/cases/paginatedList", Path(tmp) / f"edes{page}.json",
                headers={"Content-Type": "application/json;charset=UTF-8", "Accept": "application/json"},
                data=body)
            text = out.read_text(encoding="utf-8")
            if text.startswith(")]}'"):  # anti-JSON-hijacking prefix
                text = text.split("\n", 1)[1]
            doc = json.loads(text)
            for i, rec in enumerate(doc.get("content") or []):
                raw = tidy(rec.get("ecOpName"))
                if not raw:
                    continue
                parts = [p.strip() for p in raw.split("*") if p.strip()]
                ent = Entity(source=source.key, source_id=f"{page}-{i}-{parts[0][:30] if parts else raw[:30]}",
                             schema="Person" if len(raw.split("*")) == 2 and parts[1:] else "Organization",
                             list_type=source.list_type, url=source.homepage)
                ent.add_name(" ".join(parts), "primary")
                if len(parts) == 2:
                    ent.add_name(f"{parts[1]} {parts[0]}", "alias")
                add_countries(ent.countries, rec.get("ecOpCountryCode"))
                apply_term(ent, epoch_ms_date(rec.get("from")), epoch_ms_date(rec.get("to")))
                ent.programs.append(f"EDES {tidy(rec.get('typeLabel') or rec.get('type'))}")
                fine = rec.get("fpAmount")
                ent.remarks = join_remarks(
                    f"Grounds: {tidy(rec.get('grounds'))}", tidy(rec.get("comments")),
                    f"Financial penalty: {fine} {rec.get('fpCurrencyCode') or ''}".strip() if fine else "",
                    f"Address: {tidy(rec.get('ecOpAddress'))}" if rec.get("ecOpAddress") else "",
                    "Authority: European Commission (EDES)")
                yield ent
            if doc.get("last", True) or not doc.get("content"):
                break
            page += 1


SOURCES: List[ListSource] = [
    ListSource(
        key="ADB_SANCTIONS", name="Asian Development Bank published sanctions", jurisdiction="ADB",
        list_type="DEBARMENT",
        urls={f"p{i:02d}": f"https://apim.adb.org/sanctions/lists/v1/published-list?size=100&offset={i * 100}"
              for i in range(18)},
        parser=parse_adb, homepage="https://www.adb.org/who-we-are/integrity/sanctions",
        license="terms", groups=["debarment", "mdb", "asia"], max_age_days=14,
        notes="size is capped at 100: 18 fixed pages cover 1,800 records (1,558 at 2026-10); raise the range if "
              "meta.totalItems approaches it."),
    ListSource(
        key="WB_DEBARRED", name="World Bank debarred and cross-debarred firms and individuals",
        jurisdiction="WB", list_type="DEBARMENT",
        urls={"main": "https://apigwext.worldbank.org/dvsvc/v1.0/json/APPLICATION/ADOBE_EXPRNCE_MGR/FIRM/SANCTIONED_FIRM"},
        parser=parse_wb, homepage="https://projects.worldbank.org/en/projects-operations/procurement/debarred-firms",
        license="terms", groups=["debarment", "mdb", "global"], max_age_days=7,
        headers={"apikey": "z9duUaFUiEUYSHs97CU38fcZO7ipOPvm"},
        notes="The apikey is the public client key embedded in the worldbank.org debarred-firms page script (not a "
              "private credential); on HTTP 401 re-read it from that page. Dates 2999-12-31 mean permanent."),
    ListSource(
        key="EIB_EXCLUSIONS", name="EIB exclusion decisions", jurisdiction="EU", list_type="DEBARMENT",
        urls={"main": "https://www.eib.org/en/about/accountability/anti-fraud/exclusion/index.htm"},
        parser=parse_eib, homepage="https://www.eib.org/en/about/accountability/anti-fraud/exclusion/index.htm",
        license="terms", groups=["debarment", "mdb", "eu"], max_age_days=14,
        notes="Single HTML table of about ten decisions."),
    ListSource(
        key="CA_PSPC_INELIGIBLE_SUPPLIERS", name="Canada PSPC ineligible and suspended suppliers",
        jurisdiction="CA", list_type="DEBARMENT",
        urls={"main": "https://www.canada.ca/en/public-services-procurement/services/standards-oversight/"
                      "supplier-integrity-compliance/ineligible-suspended-suppliers.html"},
        parser=parse_ca_pspc,
        homepage="https://www.canada.ca/en/public-services-procurement/services/standards-oversight/"
                 "supplier-integrity-compliance/ineligible-suspended-suppliers.html",
        license="OGL", groups=["debarment", "americas"], max_age_days=14,
        notes="Parser written from the discovery sample; canada.ca did not answer from the build network "
              "(HTTP/2 stream reset), so it was not verified on live bytes."),
    ListSource(
        key="EU_EDES", name="EU Early Detection and Exclusion System (published cases)", jurisdiction="EU",
        list_type="DEBARMENT", urls={"stub": "https://ec.europa.eu/edes/"},
        parser=parse_edes, homepage="https://ec.europa.eu/edes/",
        license="terms", groups=["debarment", "eu"], max_age_days=14,
        notes="The API only answers POST, so the parser calls it directly; the declared URL is a stub. "
              "Few cases are public (5 at 2026-10)."),
]
