"""Brazilian federal debarment and sanction registers.

CEIS, CNEP, CEPIM and CEAF come from the CGU Portal da Transparencia (zipped
latin-1 CSV, ';' delimited, Portuguese headers). TCU publishes the persons
barred from public office (inabilitados) and companies declared unfit to
contract (inidoneos); the Central Bank publishes managers it has barred.

CPF / CNPJ numbers are public in these registers and are stored as
identifiers; they must never be written to logs.
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
from venombot.lists._html_c import apply_term, join_remarks, parser_download, tidy, to_iso, workdir
from venombot.lists._util import add_identifier

csv.field_size_limit(1 << 30)

_PORTAL = "https://portaldatransparencia.gov.br/download-de-dados/"
_PUSH = re.compile(r'arquivos\.push\(\{\s*"ano"\s*:\s*"(\d{4})"\s*,\s*"mes"\s*:\s*"(\d{1,2})"\s*,\s*"dia"\s*:\s*"(\d{1,2})"')
_BROWSER_UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
               "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")


def latest_stamp(page_html: str) -> str:
    """Newest YYYYMMDD announced by a portal download page.

    The data file URL embeds the publication date and a date that was never
    published answers 403, so the page is the only reliable index.
    """
    stamps = [f"{y}{int(m):02d}{int(d):02d}" for y, m, d in _PUSH.findall(page_html)]
    if not stamps:
        raise RuntimeError("no dated archives found on the Portal da Transparencia page")
    return max(stamps)


def _zip_rows(zip_path: Path) -> Iterator[Dict[str, str]]:
    """Stream the single CSV inside a portal zip as dicts (latin-1, ';')."""
    with zipfile.ZipFile(zip_path) as z:
        member = next(n for n in z.namelist() if n.lower().endswith(".csv"))
        with z.open(member) as raw:
            reader = csv.DictReader(io.TextIOWrapper(raw, encoding="latin-1", newline=""), delimiter=";")
            for row in reader:
                yield {(k or "").strip(): (v or "").strip() for k, v in row.items()}


def _fetch_zip(page_file: Path, name: str, tmp: str) -> Path:
    stamp = latest_stamp(Path(page_file).read_text(encoding="utf-8", errors="replace"))
    return parser_download(f"{_PORTAL}{name}/{stamp}", Path(tmp) / f"{name}.zip")


def _person_or_org(kind: str, tax_id: str) -> str:
    kind = kind.upper()
    if kind == "J":
        return "Organization"
    if kind == "F":
        return "Person"
    digits = re.sub(r"\D", "", tax_id)
    return "Organization" if len(digits) == 14 else "Person" if digits else "Unknown"


def _tax_id(ent: Entity, tax_id: str) -> None:
    digits = re.sub(r"\D", "", tax_id)
    kind = "CNPJ" if len(digits) == 14 else "CPF"
    add_identifier(ent, kind, tax_id, "br")


def _sanction_entity(row: Dict[str, str], source: ListSource, cadastro: str) -> Entity:
    """CEIS / CNEP / CEAF rows share the same leading columns."""
    tax = row.get("CPF OU CNPJ DO SANCIONADO", "")
    ent = Entity(source=source.key, source_id=f"{cadastro}-{row.get('CÓDIGO DA SANÇÃO', '')}",
                 schema=_person_or_org(row.get("TIPO DE PESSOA", ""), tax), list_type=source.list_type,
                 url=source.homepage)
    ent.add_name(row.get("NOME DO SANCIONADO"), "primary")
    for col in ("NOME INFORMADO PELO ÓRGÃO SANCIONADOR", "RAZÃO SOCIAL - CADASTRO RECEITA",
                "NOME FANTASIA - CADASTRO RECEITA"):
        ent.add_name(row.get(col), "alias")
    _tax_id(ent, tax)
    ent.countries.append("br")
    ent.programs.append(cadastro)
    apply_term(ent, to_iso(row.get("DATA INÍCIO SANÇÃO"), "dmy"), to_iso(row.get("DATA FINAL SANÇÃO"), "dmy"))
    return ent


def _legal(text: str) -> str:
    return tidy(text)[:300]


def parse_ceis(paths: Dict[str, Path], source: ListSource) -> Iterator[Entity]:
    """CEIS: companies/persons barred from federal procurement."""
    with workdir() as tmp:
        for row in _zip_rows(_fetch_zip(paths["page"], "ceis", tmp)):
            if not row.get("NOME DO SANCIONADO"):
                continue
            ent = _sanction_entity(row, source, "CEIS")
            ent.remarks = join_remarks(
                f"Sanction: {row.get('CATEGORIA DA SANÇÃO', '')}",
                f"Authority: {row.get('ÓRGÃO SANCIONADOR', '')} ({row.get('ESFERA ÓRGÃO SANCIONADOR', '')}/"
                f"{row.get('UF ÓRGÃO SANCIONADOR', '')})", f"Case: {row.get('NÚMERO DO PROCESSO', '')}",
                f"Legal basis: {_legal(row.get('FUNDAMENTAÇÃO LEGAL', ''))}", row.get("OBSERVAÇÕES", ""))
            yield ent


def parse_cnep(paths: Dict[str, Path], source: ListSource) -> Iterator[Entity]:
    """CNEP: penalties under the Anti-Corruption Law (12.846), incl. fines."""
    with workdir() as tmp:
        for row in _zip_rows(_fetch_zip(paths["page"], "cnep", tmp)):
            if not row.get("NOME DO SANCIONADO"):
                continue
            ent = _sanction_entity(row, source, "CNEP")
            fine = row.get("VALOR DA MULTA", "")
            ent.remarks = join_remarks(
                f"Sanction: {row.get('CATEGORIA DA SANÇÃO', '')}", f"Fine (BRL): {fine}" if fine else "",
                f"Authority: {row.get('ÓRGÃO SANCIONADOR', '')}", f"Case: {row.get('NÚMERO DO PROCESSO', '')}",
                f"Legal basis: {_legal(row.get('FUNDAMENTAÇÃO LEGAL', ''))}", row.get("OBSERVAÇÕES", ""))
            if fine:
                ent.extra["fine_brl"] = fine
            yield ent


def parse_ceaf(paths: Dict[str, Path], source: ListSource) -> Iterator[Entity]:
    """CEAF: federal civil servants expelled from the service (CPF masked)."""
    with workdir() as tmp:
        for row in _zip_rows(_fetch_zip(paths["page"], "ceaf", tmp)):
            if not row.get("NOME DO SANCIONADO"):
                continue
            ent = _sanction_entity(row, source, "CEAF")
            ent.remarks = join_remarks(
                f"Sanction: {row.get('CATEGORIA DA SANÇÃO', '')}", f"Former post: {row.get('CARGO EFETIVO', '')}",
                f"Unit: {row.get('ÓRGÃO DE LOTAÇÃO', '')}", f"Authority: {row.get('ÓRGÃO SANCIONADOR', '')}",
                f"Case: {row.get('NÚMERO DO PROCESSO', '')}", f"Legal basis: {_legal(row.get('FUNDAMENTAÇÃO LEGAL', ''))}")
            yield ent


def parse_cepim(paths: Dict[str, Path], source: ListSource) -> Iterator[Entity]:
    """CEPIM: non-profits barred from federal transfers (CNPJ only)."""
    with workdir() as tmp:
        for row in _zip_rows(_fetch_zip(paths["page"], "cepim", tmp)):
            name = row.get("NOME ENTIDADE", "")
            if not name:
                continue
            ent = Entity(source=source.key, source_id=f"{row.get('CNPJ ENTIDADE', '')}-{row.get('NÚMERO CONVÊNIO', '')}",
                         schema="Organization", list_type=source.list_type, url=source.homepage)
            ent.add_name(name, "primary")
            _tax_id(ent, row.get("CNPJ ENTIDADE", ""))
            ent.countries.append("br")
            ent.programs.append("CEPIM")
            ent.remarks = join_remarks(f"Reason: {row.get('MOTIVO DO IMPEDIMENTO', '')}",
                                       f"Agreement: {row.get('NÚMERO CONVÊNIO', '')}",
                                       f"Grantor: {row.get('ÓRGÃO CONCEDENTE', '')}")
            yield ent


# ------------------------------------------------------------------------ TCU
def _tcu_items(paths: Dict[str, Path]) -> Iterator[dict]:
    for key in sorted(paths):
        yield from json.loads(Path(paths[key]).read_text(encoding="utf-8")).get("items") or []


def _tcu_entity(it: dict, source: ListSource, tax: str, schema: str) -> Entity:
    ent = Entity(source=source.key, source_id=f"{re.sub(r'[^0-9A-Za-z]', '', tax)}-{it.get('processo', '')}",
                 schema=schema, list_type=source.list_type, url=source.homepage)
    ent.add_name(it.get("nome"), "primary")
    add_identifier(ent, "CNPJ" if schema == "Organization" else "CPF", tax, "br")
    ent.countries.append("br")
    ent.programs.append(source.key)
    apply_term(ent, to_iso(it.get("data_transito_julgado")), to_iso(it.get("data_final")))
    ent.remarks = join_remarks(
        f"Case: {it.get('processo', '')}", f"Ruling: {it.get('deliberacao', '')}",
        f"Ruling date: {to_iso(it.get('data_acordao'))}" if it.get("data_acordao") else "",
        f"Location: {it.get('municipio') or ''} {it.get('uf') or ''}".strip(),
        "Authority: Tribunal de Contas da Uniao")
    return ent


def parse_tcu_inabilitados(paths: Dict[str, Path], source: ListSource) -> Iterator[Entity]:
    """TCU: persons barred from public office / positions of trust."""
    seen = set()
    for it in _tcu_items(paths):
        if not it.get("nome"):
            continue
        ent = _tcu_entity(it, source, it.get("cpf") or "", "Person")
        if ent.source_id not in seen:
            seen.add(ent.source_id)
            yield ent


def parse_tcu_inidoneos(paths: Dict[str, Path], source: ListSource) -> Iterator[Entity]:
    """TCU: parties declared unfit to contract with the public administration."""
    seen = set()
    for it in _tcu_items(paths):
        tax = it.get("cpf_cnpj") or ""
        if not it.get("nome"):
            continue
        schema = "Organization" if len(re.sub(r"\D", "", tax)) == 14 else "Person"
        ent = _tcu_entity(it, source, tax, schema)
        if ent.source_id not in seen:
            seen.add(ent.source_id)
            yield ent


# ------------------------------------------------------------------------ BCB
def parse_bcb(paths: Dict[str, Path], source: ListSource) -> Iterator[Entity]:
    """BCB OData: persons barred from managing financial institutions."""
    for key in sorted(paths):
        for rec in json.loads(Path(paths[key]).read_text(encoding="utf-8")).get("value") or []:
            name = tidy(rec.get("Nome"))
            if not name:
                continue
            tax = rec.get("CPF") or rec.get("CPF_CNPJ") or ""
            digits = re.sub(r"\D", "", tax)
            ent = Entity(source=source.key, source_id=f"{key}-{rec.get('PAS', '')}-{name[:20]}",
                         schema="Organization" if len(digits) == 14 else "Person", list_type=source.list_type,
                         url=source.homepage)
            ent.add_name(name, "primary")
            add_identifier(ent, "CPF/CNPJ (masked)", tax, "br")
            ent.countries.append("br")
            ent.programs.append(tidy(rec.get("Penalidade")) or "BCB penalty")
            apply_term(ent, to_iso(rec.get("Inicio_do_cumprimento")), to_iso(rec.get("Prazo_final_penalidade")))
            ent.remarks = join_remarks(f"Penalty: {tidy(rec.get('Penalidade'))}",
                                       f"Term (years): {rec.get('Prazo_em_anos')}", f"PAS: {rec.get('PAS')}",
                                       "Authority: Banco Central do Brasil")
            yield ent


def _portal_source(key: str, name: str, slug: str, parser, note: str) -> ListSource:
    return ListSource(
        key=key, name=name, jurisdiction="BR", list_type="DEBARMENT",
        urls={"page": f"{_PORTAL}{slug}"}, parser=parser, homepage=f"{_PORTAL}{slug}",
        license="public", groups=["debarment", "br", "latam"], max_age_days=3,
        notes=note + " The declared URL is the index page; the parser reads the newest YYYYMMDD from it and "
                     "downloads the dated zip itself (a date that was never published answers 403).")


_TCU_HEADERS = {"User-Agent": _BROWSER_UA}  # the TCU WAF rejects non-browser agents

SOURCES: List[ListSource] = [
    _portal_source("BR_CEIS", "Brazil CEIS - companies and persons barred from federal procurement", "ceis",
                   parse_ceis, "Large (about 34 MB CSV); streamed."),
    _portal_source("BR_CNEP", "Brazil CNEP - Anti-Corruption Law penalties", "cnep", parse_cnep, "Includes fines."),
    _portal_source("BR_CEPIM", "Brazil CEPIM - NGOs barred from federal transfers", "cepim", parse_cepim,
                   "Refreshed less often than CEIS."),
    _portal_source("BR_CEAF", "Brazil CEAF - expelled federal civil servants", "ceaf", parse_ceaf,
                   "CPF is masked by the publisher."),
    ListSource(
        key="BR_TCU_INABILITADOS", name="Brazil TCU - persons barred from public office", jurisdiction="BR",
        list_type="DEBARMENT",
        urls={f"p{i}": f"https://contas.tcu.gov.br/ords/condenacao/consulta/inabilitados?offset={i * 500}&limit=500"
              for i in range(6)},
        parser=parse_tcu_inabilitados, homepage="https://contas.tcu.gov.br/ords/condenacao/consulta/inabilitados",
        license="public", groups=["debarment", "br", "latam"], headers=_TCU_HEADERS, max_age_days=14,
        notes="ORDS pages of 500 (hasMore flag); six fixed pages cover 3,000 records (about 1,400 at 2026-10). "
              "A browser User-Agent is needed to pass the TCU firewall."),
    ListSource(
        key="BR_TCU_INIDONEOS", name="Brazil TCU - parties declared unfit to contract", jurisdiction="BR",
        list_type="DEBARMENT",
        urls={"p0": "https://contas.tcu.gov.br/ords/condenacao/consulta/inidoneos?offset=0&limit=500"},
        parser=parse_tcu_inidoneos, homepage="https://contas.tcu.gov.br/ords/condenacao/consulta/inidoneos",
        license="public", groups=["debarment", "br", "latam"], headers=_TCU_HEADERS, max_age_days=14,
        notes="About 100 records; one page of 500. Browser User-Agent required."),
    ListSource(
        key="BR_BCB_INABILITADOS", name="Banco Central do Brasil - barred / prohibited managers",
        jurisdiction="BR", list_type="ENFORCEMENT",
        urls={"inabilitados": "https://olinda.bcb.gov.br/olinda/servico/Gepad_QuadrosGeraisInternet/versao/v1/"
                              "odata/QuadroGeralInabilitados?$format=json",
              "proibidos": "https://olinda.bcb.gov.br/olinda/servico/Gepad_QuadrosGeraisInternet/versao/v1/"
                           "odata/QuadroGeralProibidos?$format=json"},
        parser=parse_bcb, homepage="https://www.bcb.gov.br/estabilidadefinanceira/quadrosgerais",
        license="public", groups=["enforcement", "br", "latam"], max_age_days=14,
        notes="OData sets QuadroGeralInabilitados and QuadroGeralProibidos; CPF is masked by the publisher."),
]
