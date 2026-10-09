"""Regulator enforcement registers outside the US: UK, EU, Switzerland, Turkey,
Qatar and Australia.

These are ENFORCEMENT records: a regulator banned, fined, warned about or
named a party. A match is a risk finding about that party, not proof of a
crime, so remarks always carry the authority and the reason text.
"""

from __future__ import annotations

import csv
import io
import json
import re
from datetime import date
from pathlib import Path
from typing import Dict, Iterator, List
from urllib.parse import urlencode

from venombot.entities import Entity
from venombot.lists import ListSource
from venombot.lists._html_c import (
    apply_term, join_remarks, looks_like_company, page_paths, parser_download, stable_id,
    strip_tags, tidy, to_iso, unique, workdir,
)
from venombot.lists._tables import read_ods, rows_as_dicts
from venombot.lists._util import add_countries, add_identifier


# ----------------------------------------------------------------- ASIC (AU)
def _pick_asic_resource(pkg: dict) -> str:
    """URL of the current CSV resource (the file name changes every month)."""
    best = ""
    for res in (pkg.get("result") or {}).get("resources") or []:
        if (res.get("format") or "").upper() in ("CSV", "TSV") and "current" in (res.get("name") or "").lower():
            url = res.get("url") or ""
            if url > best and url.lower().endswith(".csv"):
                best = url
    if not best:
        raise RuntimeError("no CSV resource in ASIC package metadata")
    return best


def _asic_rows(index: Path, tmp: str) -> Iterator[Dict[str, str]]:
    url = _pick_asic_resource(json.loads(Path(index).read_text(encoding="utf-8")))
    raw = parser_download(url, Path(tmp) / "asic.csv").read_bytes().decode("utf-8-sig", errors="replace")
    delim = "\t" if "\t" in raw.split("\n", 1)[0] else ","  # the org file is TSV despite its name
    for row in csv.DictReader(io.StringIO(raw), delimiter=delim):
        yield {(k or "").strip(): (v or "").strip() for k, v in row.items()}


@unique
def parse_asic_persons(paths: Dict[str, Path], source: ListSource) -> Iterator[Entity]:
    """ASIC banned and disqualified persons (dates DD/MM/YYYY, 'SURNAME, GIVEN')."""
    with workdir() as tmp:
        for row in _asic_rows(paths["pkg"], tmp):
            name = tidy(row.get("BD_PER_NAME"))
            if not name:
                continue
            ent = Entity(source=source.key,
                         source_id=stable_id(name, row.get("BD_PER_DOC_NUM"), row.get("BD_PER_START_DT"), row.get("BD_PER_TYPE")),
                         schema="Person", list_type=source.list_type, url=source.homepage)
            ent.add_name(name, "primary")
            if "," in name:
                last, _, first = name.partition(",")
                ent.add_name(f"{first.strip()} {last.strip()}", "alias")
            add_countries(ent.countries, row.get("BD_PER_ADD_COUNTRY"))
            ent.programs.append(tidy(row.get("BD_PER_TYPE")) or "ASIC ban")
            apply_term(ent, to_iso(row.get("BD_PER_START_DT"), "dmy"), to_iso(row.get("BD_PER_END_DT"), "dmy"))
            comment = tidy(row.get("BD_PER_COMMENTS"))
            ent.remarks = join_remarks(
                f"Ban type: {tidy(row.get('BD_PER_TYPE'))}", f"Instrument: {tidy(row.get('BD_PER_DOC_NUM'))}",
                f"State: {tidy(row.get('BD_PER_ADD_STATE'))}", "" if comment.lower() == "no comment made" else comment,
                "Authority: Australian Securities and Investments Commission")
            yield ent


@unique
def parse_asic_orgs(paths: Dict[str, Path], source: ListSource) -> Iterator[Entity]:
    """ASIC banned and disqualified organisations."""
    with workdir() as tmp:
        for row in _asic_rows(paths["pkg"], tmp):
            name = tidy(row.get("BD_ORG_NAME"))
            if not name:
                continue
            ent = Entity(source=source.key,
                         source_id=stable_id(name, row.get("BD_ORG_ACN"), row.get("BD_ORG_START_DT"), row.get("BD_ORG_TYPE")),
                         schema="Organization", list_type=source.list_type, url=source.homepage)
            ent.add_name(name, "primary")
            add_identifier(ent, "ACN", row.get("BD_ORG_ACN"), "au")
            ent.countries.append("au")
            ent.programs.append(tidy(row.get("BD_ORG_TYPE")) or "ASIC ban")
            apply_term(ent, to_iso(row.get("BD_ORG_START_DT"), "dmy"), to_iso(row.get("BD_ORG_END_DT"), "dmy"))
            comment = tidy(row.get("BD_ORG_COMMENT"))
            ent.remarks = join_remarks(f"Ban type: {tidy(row.get('BD_ORG_TYPE'))}",
                                       "" if comment.lower() == "no comment made" else comment,
                                       "Authority: Australian Securities and Investments Commission")
            yield ent


# ------------------------------------------------------------------ ESMA (EU)
@unique
def parse_esma(paths: Dict[str, Path], source: ListSource) -> Iterator[Entity]:
    """ESMA sanctions register (Solr; entity name arrives inside an HTML anchor)."""
    for path in page_paths(paths):
        for doc in (json.loads(Path(path).read_text(encoding="utf-8")).get("response") or {}).get("docs") or []:
            name = strip_tags(doc.get("sn_entityName"))
            if not name:
                continue
            lei = tidy(doc.get("sn_entityLEI"))
            ent = Entity(source=source.key, source_id=str(doc.get("id") or stable_id(name, doc.get("sn_date"))),
                         schema="Organization" if lei or looks_like_company(name) else "Unknown",
                         list_type=source.list_type, url=source.homepage)
            ent.add_name(name, "primary")
            add_identifier(ent, "LEI", lei)
            add_countries(ent.countries, doc.get("sn_countryName"))
            ent.programs.append(tidy(doc.get("sn_sanctionLegalFrameworkName")) or "ESMA register")
            ent.listed_on = to_iso(doc.get("sn_date"))
            ent.remarks = join_remarks(
                f"Framework: {tidy(doc.get('sn_sanctionLegalFrameworkName'))}", f"Nature: {tidy(doc.get('sn_natureFullName'))}",
                f"Authority: {tidy(doc.get('sn_ncaCodeFullName'))}", re.sub(r"_+", "", tidy(doc.get("sn_text")))[:300])
            yield ent


# ---------------------------------------------------------------- FINMA (CH)
_FINMA_URL = "https://www.finma.ch/en/api/search/getresult"
_FINMA_BASE = "https://www.finma.ch"


@unique
def parse_finma(paths: Dict[str, Path], source: ListSource) -> Iterator[Entity]:
    """FINMA warning list (unlicensed providers). The API is POST-only, so fetch here."""
    with workdir() as tmp:
        body = urlencode({"ds": "{1C6B8731-638C-4003-A93C-A625BF7A6800}", "Order": "1"}).encode()
        out = parser_download(_FINMA_URL, Path(tmp) / "finma.json", data=body,
                              headers={"Content-Type": "application/x-www-form-urlencoded"})
        items = json.loads(out.read_text(encoding="utf-8")).get("Items") or []
    for it in items:
        title = tidy(it.get("Title"))
        if not title:
            continue
        ent = Entity(source=source.key, source_id=str(it.get("Id") or stable_id(title)), schema="Organization",
                     list_type=source.list_type, url=_FINMA_BASE + (it.get("Link") or ""))
        parts = [p.strip() for p in title.split(" / ") if p.strip()]  # name plus its websites
        for p in parts:
            ent.add_name(p, "primary" if p == parts[0] else "alias")
        ent.countries.append("ch")
        ent.programs.append("FINMA warning")
        ent.listed_on = to_iso(it.get("Date"), "dmy")
        ent.remarks = join_remarks(f"FINMA warning: {tidy(it.get('FacetColumn'))}",
                                   "Authority: Swiss Financial Market Supervisory Authority FINMA")
        yield ent


# --------------------------------------------------------------- HMRC (UK)
_AKA = re.compile(r"\((?:formerly known as|trading as|t/a|previously)\s+([^)]+)\)", re.IGNORECASE)


@unique
def parse_hmrc(paths: Dict[str, Path], source: ListSource) -> Iterator[Entity]:
    """HMRC deliberate tax defaulters (ODS; the file URL changes each quarter)."""
    index = json.loads(Path(paths["index"]).read_text(encoding="utf-8"))
    atts = [a for a in (index.get("details") or {}).get("attachments") or []
            if (a.get("url") or "").lower().endswith(".ods")]
    if not atts:
        raise RuntimeError("no .ods attachment in the HMRC publication")
    with workdir() as tmp:
        ods = parser_download(atts[0]["url"], Path(tmp) / "pddd.ods")
        for row in rows_as_dicts(read_ods(ods)):
            name = tidy(row.get("Name"))
            if not name:
                continue
            ent = Entity(source=source.key, source_id=stable_id(name, row.get("Period of default")),
                         schema="Organization" if looks_like_company(name) else "Person",
                         list_type=source.list_type, url=source.homepage)
            ent.add_name(_AKA.sub("", name).strip(), "primary")
            for m in _AKA.finditer(name):
                ent.add_name(m.group(1), "alias")
            ent.countries.append("gb")
            ent.programs.append("HMRC deliberate tax defaulter")
            ent.remarks = join_remarks(
                f"Trade: {tidy(row.get('Business trade or occupation'))}", f"Address: {tidy(row.get('Address'))}",
                f"Period of default: {tidy(row.get('Period of default'))}",
                f"Tax/duty: {tidy(row.get('Total amount of tax/duty on which penalties are based'))}",
                f"Penalties: {tidy(row.get('Total amount of penalties charged'))}", "Authority: HM Revenue and Customs")
            yield ent


# ------------------------------------------------------------------ SPK (TR)
_TR_ORG = re.compile(r"(A\.?Ş\.?|ANONİM|ANONIM|LİMİTED|LIMITED|LTD|ŞTİ|SANAYİ|TİCARET|HOLDİNG|YATIRIM|BANKASI)", re.IGNORECASE)
_TR_DUR = re.compile(r"(\d+)\s*(ay|yıl)\s*süreyle", re.IGNORECASE)
_TR_START = re.compile(r"(\d\d\.\d\d\.\d{4})\s*tarihli")


def _add_months(iso: str, months: int) -> str:
    y, m, d = int(iso[:4]), int(iso[5:7]), int(iso[8:10])
    m0 = m - 1 + months
    y, m = y + m0 // 12, m0 % 12 + 1
    return f"{y:04d}-{m:02d}-{min(d, 28):02d}"


@unique
def parse_spk(paths: Dict[str, Path], source: ListSource) -> Iterator[Entity]:
    """SPK (Capital Markets Board) trading bans on persons and companies."""
    for rec in json.loads(Path(paths["main"]).read_text(encoding="utf-8-sig")):
        name = re.sub(r"\s*\(\d+\)\s*$", "", tidy(rec.get("unvan")))
        if not name:
            continue
        ent = Entity(source=source.key, source_id=str(rec.get("id") or stable_id(name, rec.get("kurulKararNo"))),
                     schema="Organization" if _TR_ORG.search(name) else "Person", list_type=source.list_type,
                     url=source.homepage)
        ent.add_name(name, "primary")
        ent.countries.append("tr")
        add_identifier(ent, "MKK registry no (masked)", rec.get("mkkSicilNo"), "tr")
        ent.programs.append("SPK trading ban")
        text = tidy(rec.get("aciklama"))
        decided = to_iso(rec.get("kurulKararTarihi"))
        m_start, m_dur = _TR_START.search(text), _TR_DUR.search(text)
        start = to_iso(m_start.group(1), "dmy") if m_start else decided
        end = ""
        if m_dur and start:
            n = int(m_dur.group(1)) * (12 if m_dur.group(2).lower() == "yıl" else 1)
            end = _add_months(start, n)
        apply_term(ent, start, end)
        ent.listed_on = decided or ent.listed_on
        ent.topics.append("market_abuse")
        ent.remarks = join_remarks(
            f"Security: {tidy(rec.get('pay'))} ({tidy(rec.get('payKodu'))})", f"Board decision: {tidy(rec.get('kurulKararNo'))} of {decided}",
            f"Proceedings: {tidy(rec.get('yargilamaAsamasi'))}" if rec.get("yargilamaAsamasi") else "",
            text[:350], "Authority: Capital Markets Board of Turkey (SPK)")
        yield ent


# --------------------------------------------------------------- QFCRA (QA)
# Only titles that name the *sanctioned* party. Warnings about impersonators
# name the victim firm, which must never be listed as an offender.
_QFC_PATTERNS = [
    re.compile(r"settlement with\s+(.+)$", re.IGNORECASE),
    re.compile(r"\b(?:fines|penalises|penalizes|censures|bans|prohibits|imposes (?:a )?(?:fine|penalty) on|takes action against)\s+(.+)$",
               re.IGNORECASE),
    re.compile(r"enforcement action against\s+(.+)$", re.IGNORECASE),
]


@unique
def parse_qfcra(paths: Dict[str, Path], source: ListSource) -> Iterator[Entity]:
    """QFCRA press releases that announce an action against a named party."""
    for rec in json.loads(Path(paths["main"]).read_text(encoding="utf-8")):
        title = tidy((rec.get("title") or {}).get("rendered"))
        if re.search(r"impersonat|warning about|alert", title, re.IGNORECASE):
            continue
        name = ""
        for pat in _QFC_PATTERNS:
            m = pat.search(title)
            if m:
                name = re.sub(r"\s*\(.*$", "", m.group(1)).strip(" .")
                break
        if not name:
            continue
        ent = Entity(source=source.key, source_id=str(rec.get("id")), schema="Unknown", list_type=source.list_type,
                     url=rec.get("link") or source.homepage)
        ent.add_name(name, "primary")
        ent.countries.append("qa")
        ent.programs.append("QFCRA enforcement")
        ent.listed_on = to_iso((rec.get("date") or "")[:10])
        ent.remarks = join_remarks(f"QFCRA release: {title}", "Authority: Qatar Financial Centre Regulatory Authority")
        yield ent


SOURCES: List[ListSource] = [
    ListSource(
        key="AU_ASIC_BANNED_PERSONS", name="ASIC banned and disqualified persons", jurisdiction="AU",
        list_type="ENFORCEMENT",
        urls={"pkg": "https://data.gov.au/data/api/3/action/package_show?id=asic-banned-disqualified-per"},
        parser=parse_asic_persons, homepage="https://data.gov.au/data/dataset/asic-banned-disqualified-per",
        license="CC-BY", groups=["enforcement", "au", "apac"], max_age_days=35,
        notes="Monthly CSV (bd_per_YYYYMM.csv): the declared URL is the CKAN package metadata and the parser "
              "downloads the current CSV. Expired bans are kept with expired=True."),
    ListSource(
        key="AU_ASIC_BANNED_ORGS", name="ASIC banned and disqualified organisations", jurisdiction="AU",
        list_type="ENFORCEMENT",
        urls={"pkg": "https://data.gov.au/data/api/3/action/package_show?id=asic-banned-disqualified-org"},
        parser=parse_asic_orgs, homepage="https://data.gov.au/data/dataset/asic-banned-disqualified-org",
        license="CC-BY", groups=["enforcement", "au", "apac"], max_age_days=35,
        notes="The 'CSV' is tab-delimited; the parser detects the delimiter."),
    ListSource(
        key="EU_ESMA_SANCTIONS", name="ESMA interim sanctions register", jurisdiction="EU", list_type="ENFORCEMENT",
        urls={f"p{i}": f"https://registers.esma.europa.eu/solr/esma_registers_sanctions/select?q=*:*&rows=1000&start={i * 1000}&wt=json"
              for i in range(3)},
        parser=parse_esma, homepage="https://registers.esma.europa.eu/publication/searchRegister?core=esma_registers_sanctions",
        license="terms", groups=["enforcement", "eu"], max_age_days=30, timeout=240,
        notes="Solr, 1,000 rows per page; three fixed pages cover 3,000 (1,325 at 2026-10)."),
    ListSource(
        key="CH_FINMA_WARNINGS", name="FINMA warning list (unauthorised providers)", jurisdiction="CH",
        list_type="ENFORCEMENT", urls={"stub": "https://www.finma.ch/en/finma-public/warnungen/warning-list/"},
        parser=parse_finma, homepage="https://www.finma.ch/en/finma-public/warnungen/warning-list/",
        license="terms", groups=["enforcement", "ch", "eu"], max_age_days=14, timeout=240,
        notes="The search API is POST-only, so the parser calls it directly; the declared URL is a stub."),
    ListSource(
        key="UK_HMRC_TAX_DEFAULTERS", name="HMRC deliberate tax defaulters", jurisdiction="GB", list_type="ENFORCEMENT",
        urls={"index": "https://www.gov.uk/api/content/government/publications/publishing-details-of-deliberate-tax-defaulters-pddd"},
        parser=parse_hmrc,
        homepage="https://www.gov.uk/government/publications/publishing-details-of-deliberate-tax-defaulters-pddd",
        license="OGL", groups=["enforcement", "uk"], max_age_days=100,
        notes="Quarterly ODS whose URL changes: the parser reads the newest attachment from the content API."),
    ListSource(
        key="TR_SPK_TRADING_BANS", name="Turkey SPK trading bans", jurisdiction="TR", list_type="ENFORCEMENT",
        urls={"main": "https://idariyaptirimlar.spk.gov.tr/api/IslemYasagi"}, parser=parse_spk,
        homepage="https://idariyaptirimlar.spk.gov.tr/", license="terms",
        groups=["enforcement", "tr", "mena"], max_age_days=14,
        notes="Temporary and permanent trading bans for market manipulation / insider dealing. Ban end dates are "
              "estimated from the duration stated in the decision text."),
    ListSource(
        key="QA_QFCRA_ENFORCEMENT", name="QFCRA enforcement releases", jurisdiction="QA", list_type="ENFORCEMENT",
        urls={"main": "https://www.qfcra.com/wp-json/wp/v2/e_press_releases?per_page=100&_fields=id,date,title,link"},
        parser=parse_qfcra, homepage="https://www.qfcra.com/en-us/enforcement/", license="terms",
        groups=["enforcement", "qa", "gcc", "mena"], max_age_days=30,
        notes="Press-release feed: only titles that name the sanctioned party are kept (settlements, fines, bans); "
              "fraud warnings about impersonated firms are deliberately skipped. Few records (about 20)."),
]
