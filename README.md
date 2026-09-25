# VenomBot

Multilingual AML/KYC web crawler for MENA and US sources. Captures original snapshots (Arabic and English), translates Arabic content for unified search, and produces structured findings reports.

**Requirements:** Python 3.9+ — standard library only (no third-party packages).

## Install

```bash
pip install -e .
```

Or run directly without installing:

```bash
python -m venombot --help
```

## Usage

### 1. Crawl sources

Fetches 9 AML sources, stores snapshots with SHA-256 checksums, and translates Arabic content to English.

```bash
python -m venombot crawl --db aml_snapshots -o aml-report
```

Sources include OFAC SDN, Central Bank of Kuwait, MENAFATF, PEP lists, UN sanctions, FATF grey list, BBC Arabic, Reuters MENA, and Al Jazeera.

If a live fetch fails (blocked URL, network error), the crawler falls back to sample content so you can still demo the pipeline. Use `--no-sample` to disable that.

### 2. Search entities

```bash
python -m venombot search \
  --entities "Acme Trading Co" "Al-Noor Finance" "Dubai Holdings" \
  --db aml_snapshots \
  -o aml-report
```

Outputs:

- `aml-findings.json` — machine-readable findings
- `aml-findings.md` — human-readable report

### 3. Regenerate reports

```bash
python -m venombot report -o aml-report
```

## Package layout

```text
venombot/
  models.py       # AMLSource, Snapshot, Finding dataclasses
  translator.py   # Arabic → English AML term mapping
  sources.py      # Source registry + sample fallback content
  fetch.py        # HTTP fetch and HTML cleanup
  scoring.py      # Severity, confidence, evidence
  storage.py      # JSON snapshot database
  crawler.py      # Crawl, translate, search orchestration
  reporter.py     # JSON + Markdown reports
  cli.py          # argparse entrypoint
```

## Library usage

```python
from pathlib import Path
from venombot import MultilingualWebCrawler, SnapshotDatabase, AMLReporter

db = SnapshotDatabase(Path("aml_snapshots"))
crawler = MultilingualWebCrawler(db)
crawler.crawl_all_sources()
crawler.translate_snapshots()

findings = crawler.search_entity("Acme Trading Co", entity_country="UAE")
AMLReporter(findings).write(Path("aml-report"))
```

## License

MIT
