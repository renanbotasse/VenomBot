# VenomBot

Multilingual AML/KYC web crawler focused on MENA/GCC and global sanctions, PEP, corporate, risk and adverse-media sources. Captures snapshots, translates Arabic content for unified search, and produces structured findings reports.

**Requirements:** Python 3.9+ — standard library only (no third-party packages).

## Install

```bash
pip install -e .
```

Or run without installing:

```bash
python3 -m venombot --help
```

## Usage

### 1. Crawl sources

```bash
python3 -m venombot crawl --db aml_snapshots -o aml-report
```

Fetches **39 sources**, stores snapshots with SHA-256 checksums, and translates Arabic content to English. Huge lists (OFAC, EU, UK, OpenSanctions, SECO, etc.) are capped at ~8 MB per source so crawls stay practical.

If a live fetch fails, the crawler falls back to sample content. Use `--no-sample` to disable that.

### 2. Search entities

```bash
python3 -m venombot search \
  --entities "Hasan J. Zainal" "ZAINAL, Akram" \
  --db aml_snapshots \
  -o aml-report
```

Outputs:

- `aml-findings.json`
- `aml-findings.md`

### 3. Regenerate reports

```bash
python3 -m venombot report -o aml-report
```

## Data sources (URLs)

### Core

| Name | URL |
|---|---|
| OFAC SDN | https://sanctionslistservice.ofac.treas.gov/api/download/sdn.csv |
| OFAC aliases | https://sanctionslistservice.ofac.treas.gov/api/download/alt.csv |
| CBK Kuwait | https://www.cbk.gov.kw/en |
| UN sanctions | https://scsanctions.un.org/resources/xml/en/consolidated.xml |
| OpenSanctions UN | https://data.opensanctions.org/datasets/latest/un_sc_sanctions/targets.simple.csv |
| FATF grey list | https://www.fatf-gafi.org/en/countries/black-and-grey-lists.html |
| BBC Arabic RSS | https://feeds.bbci.co.uk/arabic/rss.xml |
| BBC Middle East RSS | https://feeds.bbci.co.uk/news/world/middle_east/rss.xml |
| Al Jazeera RSS | https://www.aljazeera.com/xml/rss/all.xml |

### Sanctions (additional)

| Name | URL |
|---|---|
| EU consolidated | https://webgate.ec.europa.eu/fsd/fsf/public/files/xmlFullSanctionsList_1_1/content?token=dG9rZW4tMjAxNw |
| UK Sanctions List (FCDO) | https://sanctionslist.fcdo.gov.uk/docs/UK-Sanctions-List.csv |
| OFAC Non-SDN | https://sanctionslistservice.ofac.treas.gov/api/download/cons_prim.csv |
| US CSL (Trade.gov) | https://www.trade.gov/consolidated-screening-list |
| Canada SEMA | https://www.international.gc.ca/world-monde/assets/office_docs/international_relations-relations_internationales/sanctions/sema-lmes.xml |
| Australia DFAT | https://www.dfat.gov.au/international-relations/security/sanctions/consolidated-list |
| Switzerland SECO | https://www.sesam.search.admin.ch/sesam-search-web/pages/downloadXmlGesamtliste.xhtml?lang=en&action=downloadXmlGesamtlisteAction |
| UAE local list | https://www.uaeiec.gov.ae |
| Kuwait MOFA | https://www.mofa.gov.kw |
| OpenSanctions default | https://data.opensanctions.org/datasets/latest/default/targets.simple.csv |

### PEPs / wanted

| Name | URL |
|---|---|
| OpenSanctions PEPs | https://data.opensanctions.org/datasets/latest/peps/targets.simple.csv |
| Wikidata SPARQL (GCC) | https://query.wikidata.org |
| CIA World Leaders | https://www.cia.gov/resources/world-leaders/foreign-governments/ |
| Interpol Red Notices | https://ws-public.interpol.int/notices/v1/red |
| FBI Wanted | https://api.fbi.gov/wanted/v1/list |
| Europol Most Wanted | https://eumostwanted.eu |
| World Bank debarred | https://www.worldbank.org/en/projects-operations/procurement/debarred-firms |

### Corporate / ownership

| Name | URL |
|---|---|
| ICIJ Offshore Leaks | https://offshoreleaks.icij.org |
| OCCRP Aleph | https://aleph.occrp.org |
| OpenCorporates | https://opencorporates.com |
| GLEIF LEI API | https://api.gleif.org/api/v1/lei-records |
| UK Companies House | https://developer.company-information.service.gov.uk |

### Country risk / regulators

| Name | URL |
|---|---|
| Kuwait FIU | https://www.kwfiu.gov.kw |
| MENAFATF | https://www.menafatf.org |
| Basel AML Index | https://index.baselgovernance.org |
| Transparency CPI (CSV mirror) | https://raw.githubusercontent.com/datasets/corruption-perceptions-index/master/data/cpi.csv |
| FinCEN advisories | https://www.fincen.gov/resources/advisoriesbulletinsfact-sheets |

### Adverse media

| Name | URL |
|---|---|
| GDELT DOC API | https://api.gdeltproject.org/api/v2/doc/doc |
| KUNA (Kuwait) | https://www.kuna.net.kw |
| Google News RSS | https://news.google.com/rss/search?q=... |

### Notes

- Validate download endpoints before production use — they change often.
- OpenSanctions free datasets are typically **non-commercial**; get a commercial license if needed.
- ICIJ, OpenCorporates, Companies House, Trade.gov CSL and some APIs have their own terms / keys.
- Kuwait/UAE portals often lack stable machine feeds (HTML / Cloudflare).

## Package layout

```text
venombot/
  catalog.py      # Source URL registry (core + extended)
  samples.py      # Offline fallback content
  sources.py      # Registry facade
  models.py       # Dataclasses
  translator.py   # Arabic → English AML terms
  fetch.py        # HTTP fetch + cleanup
  scoring.py      # Severity / confidence
  storage.py      # JSON snapshot DB
  crawler.py      # Crawl / translate / search
  reporter.py     # JSON + Markdown reports
  cli.py          # CLI
```

## License

MIT (project code). Upstream data sources keep their own licenses.
