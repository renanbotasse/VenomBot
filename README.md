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

### One command: `investigate`

`investigate` runs everything in order — list matches, stored media, then live lookups by kind — and writes each part as soon as it finishes, so you can read the list hits while slow lookups are still running.

```bash
python3 -m venombot investigate --name "Nome Completo" --dob 1970-05-14 --country BR --type person \
    --purpose "KYC onboarding" --requested-by "seu-nome" --refresh --refresh-media
```

Output in `reports/<name>/`:

| File | Content |
|---|---|
| `<name>_<ISO-time>.md` | **The report.** Header with requester, subject and the search time (ISO-8601 UTC). A summary on top: decision, rating, and counts per flag — matches by class (CONFIRMED/PROBABLE/POSSIBLE), by severity and list type, context items by kind and by adverse topic, lookups answered/failed/skipped — then gaps, list matches, adverse and other context, coverage, review block |
| `00-summary.md` | Progress checklist, rewritten after every stage |
| `01-lists.*`, `02-media.*`, `03-live-<kind>.*` | The parts, written as each stage ends (kinds: identity, corporate, court, enforcement, leak, adverse_media) |
| `<name>_<ISO-time>.json/.html` | Same report as data / printable page (`--format md json html`) |
| `state.json` | Stage results; `--resume` skips finished stages |

While it runs, the terminal explains where each part's information comes from: the loaded lists grouped by issuing authority (with entity counts and data date), which registered lists are **not** loaded, and for live parts which external services receive the subject's name (`--quiet` hides this).

Options: `--refresh` (update missing/stale lists first; `--refresh-groups core pep …`), `--refresh-media`, `--no-media`, `--no-live`, `--kinds court corporate`, `--providers KEY …`, `--csv file` (batch), `--resume`. A failing stage or provider never stops the run; it appears under "Gaps in this search".

### Coverage

VenomBot registers **139 direct list sources**, 16 built-in OpenSanctions datasets (~440 after `lists --refresh-opensanctions`), **57 live search providers** and **42 news/regulator feeds**. The full, generated table with licences, key requirements and known gaps is in [docs/SOURCES.md](docs/SOURCES.md).

| Family | Examples |
|---|---|
| Sanctions (official) | OFAC SDN/Consolidated, UN (EN+AR), EU FSF, UK FCDO, Switzerland SECO, Canada SEMA, Australia DFAT, NZ, Japan, France, Belgium, Monaco, Baltics, Czechia, Poland, US CSL/BIS, Taiwan |
| Terrorism / domestic lists | Saudi Arabia, Iraq, Israel NBCTF, Tunisia, Turkey MASAK, Egypt, Qatar, Kenya, Indonesia, Malaysia, Ukraine, Netherlands, India MHA, Australia, Singapore, Thailand, Nigeria, US State FTO |
| Debarment / enforcement | World Bank, ADB, EIB, EU EDES, Brazil CEIS/CNEP/CEPIM/CEAF/TCU, US SAM/LEIE/DDTC/FINRA/OCC/Fed, ASIC, ESMA, FINMA |
| PEP | UK/EU/US/CA/BR/DE/FR parliaments and governments, CIA World Leaders, Wikidata (GCC, Levant, N. Africa, heads of state, central banks, SOEs) |
| Wanted / corporate / leaks | FBI, Europol, ICIJ Offshore Leaks, FinCEN Files, GLEIF, UK PSC and Companies House |

```bash
python3 -m venombot lists                         # everything registered + load status
python3 -m venombot lists --group mena            # filter by group, list type or jurisdiction
python3 -m venombot update                        # "core": official sanctions lists (~60k entities, ~1 min)
python3 -m venombot update --group pep terrorism debarment
python3 -m venombot update --source SA_PCCT_NATIONAL_TERRORISM_LIST WB_DEBARRED
python3 -m venombot update --group opensanctions --accept-noncommercial
python3 -m venombot lists --refresh-opensanctions # fetch the full OpenSanctions dataset index (cached locally)
```

Groups include `core`, `sanctions`, `terrorism`, `pep`, `debarment`, `enforcement`, `wanted`, `corporate`, `leaks`, `mena`, `gcc`, `eu`, `us`, `uk`, `wikidata`, `html` (fragile page scrapers), `opensanctions` and `all`.

> **Licence:** OpenSanctions data is CC BY-NC 4.0 (non-commercial). Those sources are excluded unless you pass `--accept-noncommercial`; commercial use needs a licence from OpenSanctions. Check each source's licence in `docs/SOURCES.md` before commercial use.

Failed updates never wipe data: the last good version keeps being served and is flagged in the report's coverage section.

### Context: news, courts, registries

List hits and context are kept apart. Context is evidence for a reviewer, never a match.

```bash
python3 -m venombot media update                  # RSS/Atom: regulators, police, Arabic and English news
python3 -m venombot media update --group enforcement
python3 -m venombot screen --name "Jane Doe" --media            # search the stored articles (local)
python3 -m venombot screen --name "Jane Doe" --live             # query third-party search APIs
python3 -m venombot screen --name "Jane Doe" --live-group court corporate
```

`--media` searches locally at sentence level (the name and an adverse topic such as money laundering, fraud or sanctions evasion in the same sentence). `--live` **sends the subject's name to third-party services**; it is opt-in and every query is recorded in the dossier. Providers that need a key are skipped until the variable is set, for example `COMPANIES_HOUSE_API_KEY`, `NEWSAPI_KEY`, `GUARDIAN_API_KEY`, `OPENCORPORATES_API_TOKEN` (see each provider's `api_key_env` in `docs/SOURCES.md`). Set `VENOMBOT_CONTACT` to a contact string: SEC and Wikimedia require one in the User-Agent.

### Secrets and environment variables

**None are required** for the list screening, the feeds, or the keyless live lookups (courts, SEC, DOJ, Wikidata, registries, ICIJ). Keys only switch on extra providers: a provider whose variable is not set is skipped and shown as `skipped` in the report. VenomBot reads plain environment variables (it does not load `.env` files; `.env*` is git-ignored if you use a tool such as direnv). Never put keys in the code or in commits.

```bash
export VENOMBOT_CONTACT="you@example.com"
export NEWSAPI_KEY="..."
```

| Variable | Unlocks | Where to get it |
|---|---|---|
| `VENOMBOT_CONTACT` | **Recommended.** Contact string put in the User-Agent: the 4 SEC feeds and the SEC / Wikimedia lookups answer 403 without it | any email or URL of yours |
| `COMPANIES_HOUSE_API_KEY` | UK Companies House: company and officer search, disqualified directors | developer.company-information.service.gov.uk  |
| `FCA_REGISTER_KEY` + `FCA_REGISTER_EMAIL` | UK FCA Register (firms and individuals) | register.fca.org.uk/Developer |
| `OPENCORPORATES_API_TOKEN` | OpenCorporates API (companies, officers) | opencorporates.com/info/our-data (API access; plans and limits change) |
| `ALEPH_API_KEY` | OCCRP Aleph (investigative documents and entities) | aleph.occrp.org (account) |
| `PORTAL_TRANSPARENCIA_API_KEY` | Brazil Portal da Transparência: sanctions (CEIS) and PEP lookups by name, CPF/CNPJ | portaldatransparencia.gov.br/api-de-dados  |
| `TRADE_GOV_API_KEY` | US Consolidated Screening List search API | developer.trade.gov  |
| `GOVINFO_API_KEY` | US GovInfo court documents | api.govinfo.gov (api.data.gov key) |
| `CONGRESS_API_KEY` | `US_CONGRESS_GOV_API` PEP list | api.congress.gov/sign-up  |
| `SEC_CONTACT_EMAIL` | `US_SEC_PAUSE` list (SEC rejects requests without a contact) | your email |
| `NEWSAPI_KEY` | NewsAPI.org news search | newsapi.org (check the free tier's terms) |
| `GUARDIAN_API_KEY` | The Guardian Open Platform | open-platform.theguardian.com  |
| `NYT_API_KEY` | New York Times Article Search | developer.nytimes.com  |
| `NEWSDATA_API_KEY`, `GNEWS_API_KEY`, `MEDIASTACK_ACCESS_KEY`, `CURRENTS_API_KEY`, `THENEWSAPI_TOKEN`, `WORLD_NEWS_API_KEY`, `EVENT_REGISTRY_API_KEY`, `MEDIA_CLOUD_API_KEY` | Other news-search APIs (many languages, including Arabic) | each provider's site (check each tier's terms) |
| `OPENFIGI_API_KEY`, `COURTLISTENER_TOKEN` | **Optional.** Raise the anonymous rate limits of OpenFIGI and CourtListener (without them some calls return HTTP 429) | openfigi.com/api/overview, wiki.free.law/c/courtlistener/help/api |
| `VENOMBOT_CA_BUNDLE` | Path to a CA bundle for sites with a private root CA (e.g. Russia's Rosfinmonitoring list). TLS verification is never disabled | your OS / the publisher |
| `VENOMBOT_DATA` | Where the OpenSanctions index cache lives (default `venombot_data/`) | — |
| `VENOMBOT_DEBUG` | Print Python tracebacks for failed list updates | — |

Without keys, **no news-API provider runs** — adverse media then comes only from the stored feeds (`media update`) and GDELT (keyless but rate-limited, often HTTP 429). `docs/SOURCES.md` shows which provider needs which variable. Terms and free-tier limits change: check each provider's current terms, and note that some free tiers forbid commercial use.

### Interpol and GDELT: known limits

Two sources are less reliable than the rest, and for both a **date of birth** matters:

- **Interpol red notices.** Interpol's public API answers HTTP 403 to automated clients on many networks (bot protection); VenomBot does not try to evade that. Use the daily notice list instead, which is loaded like any other list and is then matched with name **and** date of birth / nationality:

  ```bash
  python3 -m venombot update --source OS_INTERPOL_RED_NOTICES --accept-noncommercial   # ~6,400 notices
  python3 -m venombot investigate --name "Nome Completo" --dob 1970-05-14 --country XX
  ```

  Without a date of birth a name-only match can be a namesake (class `POSSIBLE`); with one, the same notice becomes `CONFIRMED` (date agrees) or `DISCOUNTED` (date conflicts). The OpenSanctions mirror is CC BY-NC (non-commercial).
- **GDELT news search.** Free but limited to about one request per 5 seconds per IP and often answers HTTP 429 (the report then lists `GDELT_DOC_API` as failed under "Gaps in this search"; run again in a few minutes). It searches by name only — a date of birth cannot narrow news articles — so its results are context and may concern namesakes. For more reliable adverse media, set a news-API key (see the table above) and/or run `venombot media update`.

### Sensitive and contested lists

Some state lists include opposition figures (Russia's Rosfinmonitoring list, Vietnam's terrorist organisations). They are typed `COUNTER_SANCTIONS`, so they rate LOW and can never produce a `HOLD_FOR_REVIEW` on their own. Several publishers block non-browser User-Agents; those sources set an explicit `headers` override, visible in the source definition.

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
    ofac.py  un.py  opensanctions.py  sanctions_*.py  terror_*.py  debarment_*.py
    enforcement_regulators.py  pep_*.py  wanted.py  leaks_icij.py  corporate_registries.py
    _util.py  _tables.py (xlsx/ods)  _html*.py
  live/           # Per-subject search APIs (registries, courts, news); opt-in
  feeds/          # RSS/Atom ingest, article store, sentence-level search
  evidence.py     # Context evidence + adverse-topic taxonomy
  fetch.py        # Streaming downloads (no truncation, retries, atomic)
  pipeline.py     # investigate: staged run, parts, timestamped report
  cli.py          # update | lists | screen | investigate | media | crawl | search | report
  # legacy page crawler
  catalog.py  samples.py  sources.py  models.py  translator.py
  scoring.py  storage.py  crawler.py  reporter.py
```

To add a jurisdiction, create a module in `venombot/lists/` that defines `SOURCES` (a list of `ListSource` with a parser); it is discovered automatically.

## License

MIT (project code). Upstream data sources keep their own licenses.
