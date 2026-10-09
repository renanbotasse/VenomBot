# VenomBot

Multilingual AML/KYC screening for people and organisations. VenomBot downloads official sanctions, PEP, wanted and debarment lists, parses them into structured records, matches a subject against them with a spelling- and script-tolerant name matcher (Latin, Arabic, Cyrillic), and writes an explainable due-diligence dossier.

**Requirements:** Python 3.9+ — standard library only (no third-party packages).

> VenomBot supports a compliance review; it never makes the decision. The strongest outcome is `HOLD_FOR_REVIEW`, and a listed match is a *potential* match until a qualified reviewer confirms it. "No match" only covers the lists that were loaded, as stated in each report's coverage section.

## Install

```bash
pip install -e .
```

Or run without installing: `python3 -m venombot --help`

## Quick start

```bash
# 1. Load the official lists (OFAC SDN + Consolidated, UN Security Council)
python3 -m venombot update

# 2. Screen a subject and write a dossier
python3 -m venombot screen --name "Jeffrey Epstein" --dob 1953-01-20 --country US \
    --format json md html -o reports

# 3. Batch screening from CSV (columns: name,dob,country,type,id,aliases)
python3 -m venombot screen --csv clients.csv -o reports --fail-on-hit
```

### Add more coverage

VenomBot registers three official feeds plus the OpenSanctions datasets (16 built in; **~440** after `lists --refresh-opensanctions`) (sanctions, terrorism, PEP, wanted, debarment, enforcement and crime lists from 100+ jurisdictions, including the UAE, Qatar and Saudi Arabia local lists).

```bash
python3 -m venombot lists --refresh-opensanctions               # once: register all ~440 datasets
python3 -m venombot lists                       # everything registered + load status
python3 -m venombot lists --group os-mena       # filter by group, type or jurisdiction
python3 -m venombot update --group os-sanctions os-pep --accept-noncommercial
python3 -m venombot update --source OS_AE_LOCAL_TERRORISTS OS_QA_NCTC_SANCTIONS --accept-noncommercial
python3 -m venombot update --group all --accept-noncommercial   # large: several GB
python3 -m venombot lists --refresh-opensanctions               # fetch the full OpenSanctions dataset index (cached locally)
```

Groups: `core`, `sanctions`, `un`, `us`, `mena`, `opensanctions`, `os-sanctions`, `os-pep`, `os-wanted`, `os-debarment`, `os-enforcement`, `os-crime`, `os-mena`, `os-official`, `os-collections`, `all`.

> **Licence:** OpenSanctions data is CC BY-NC 4.0 (non-commercial). Those sources are excluded unless you pass `--accept-noncommercial`; commercial use needs a licence from OpenSanctions.

Failed updates never wipe data: the last good version keeps being served and is flagged in the report's coverage section.

### `screen` options

| Option | Meaning |
|---|---|
| `--name "Full Name"` | Subject (quote it) |
| `--alias A B` | Other known names, screened too |
| `--dob` | Any format; year-only is accepted |
| `--country` | Nationality / country (`KW`, `Kuwait`, `الكويت`) |
| `--type person\|org\|any` | Restrict candidate types |
| `--id` | Passport / ID / registration numbers (strong evidence) |
| `--csv file` | Batch input |
| `--source KEY …` | Restrict to specific lists |
| `--format json md html` | Dossier formats (default json, md) |
| `--purpose`, `--requested-by` | Recorded in the report for the audit trail |
| `--fail-on-hit` | Exit code 3 when a subject rates HIGH/CRITICAL (CI use) |

## How matching and scoring work

1. **Parsed records, not text search.** Each list is parsed into an `Entity` (names with kind, DOB, nationality, identifiers, programmes, positions, links). A name that merely appears inside someone else's remarks is not a hit.
2. **Name normalisation.** `SURNAME, Given` inversion, diacritics, Arabic/Cyrillic/Greek transliteration, particles (al, bin, ibn), `Abd al-Rahman` variants, legal-form noise for organisations.
3. **Fuzzy matching.** Order-insensitive token alignment with Jaro-Winkler and consonant-skeleton tolerance (Mohammed/Muhammad, Usama/Osama, أيمن/Ayman). Candidates come from an indexed SQLite store, so searches stay fast on millions of records.
4. **Two separate axes.**
   - *Match class:* `CONFIRMED` (name + exact DOB or identifier), `PROBABLE`, `POSSIBLE`, `DISCOUNTED` (DOB conflict). Very common names without DOB never exceed `POSSIBLE`.
   - *Severity:* from the list type, never from keywords — sanctions/terrorism `CRITICAL`; wanted/crime `HIGH`; PEP/debarment/enforcement `MEDIUM`.
5. **Action.** `HOLD_FOR_REVIEW`, `ESCALATE`, `ENHANCED_DUE_DILIGENCE`, `REVIEW`, `NO_ACTION` or `NO_MATCH_IN_SCREENED_LISTS`. Thresholds and weights live in `venombot/screening.py` (`SCORING`, versioned and stamped into every report).

## The dossier

Each screening writes `screening-<name>.json` (canonical), `.md` and `.html` with:

1. Subject and inputs (and a warning when no DOB was given)
2. Verdict with reasons
3. Candidate matches with the evidence behind each score
4. Discounted candidates and why
5. **Coverage** — every list searched, entity counts, data age, stale or failed lists
6. Reviewer sign-off block
7. Disclaimer (no sole automated decisions — GDPR Art. 22 / LGPD Art. 20)

Reports contain personal data: `venombot_data/` and `reports/` are git-ignored. Keep retention short and record the `--purpose`.

## Legacy page crawler

The original snapshot crawler is kept for news and context pages (BBC, Al Jazeera, FATF, regulator sites):

```bash
python3 -m venombot crawl --db aml_snapshots        # --demo stores sample content on fetch failure
python3 -m venombot search --entities "Some Name" --db aml_snapshots
```

It does plain-text search over raw pages, so use it for adverse-media context only; sanctions/PEP screening should go through `screen`. Demo content is no longer stored by default, so a failed fetch can never masquerade as real data.

## Tests

```bash
python3 -m unittest discover -s tests
```

Tests are offline and use real-format fixtures (OFAC and UN records are public domain; the OpenSanctions fixture is synthetic).

## Legacy catalog: page and feed URLs

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

### Notes (legacy catalog)

- Validate download endpoints before production use — they change often.
- OpenSanctions free datasets are typically **non-commercial**; get a commercial license if needed.
- ICIJ, OpenCorporates, Companies House, Trade.gov CSL and some APIs have their own terms / keys.
- Kuwait/UAE portals often lack stable machine feeds (HTML / Cloudflare).

## Package layout

```text
venombot/
  # structured screening
  entities.py     # Entity model (names, DOB, ids, programmes, positions)
  normalize.py    # Transliteration, particles, blocking keys
  matching.py     # Fuzzy name matcher
  dates.py        # Partial-ISO dates, DOB comparison
  countries.py    # Country names -> ISO2 (incl. Arabic)
  store.py        # SQLite entity store + index + source status
  update.py       # Download -> parse -> store pipeline
  screening.py    # Subject screening, match class, severity, actions
  dossier.py      # JSON / Markdown / HTML dossier + coverage
  lists/          # One module per list family; auto-registered
    ofac.py  un.py  opensanctions.py  _util.py
  fetch.py        # Streaming downloads (no truncation, retries, atomic)
  cli.py          # update | lists | screen | crawl | search | report
  # legacy page crawler
  catalog.py  samples.py  sources.py  models.py  translator.py
  scoring.py  storage.py  crawler.py  reporter.py
```

To add a jurisdiction, create a module in `venombot/lists/` that defines `SOURCES` (a list of `ListSource` with a parser); it is discovered automatically.

## License

MIT (project code). Upstream data sources keep their own licenses.
