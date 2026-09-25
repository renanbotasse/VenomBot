"""Command-line interface for VenomBot."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import List, Optional

from venombot.crawler import MultilingualWebCrawler
from venombot.models import AMLFinding
from venombot.reporter import AMLReporter
from venombot.storage import SnapshotDatabase


def cmd_crawl(args: argparse.Namespace) -> int:
    db = SnapshotDatabase(Path(args.db))
    crawler = MultilingualWebCrawler(db, use_sample_fallback=not args.no_sample)
    crawler.crawl_all_sources()
    crawler.translate_snapshots()
    print(f"\nDone. Snapshots in: {db.root.resolve()}")
    print(f"  Original: {len(crawler.snapshots)}")
    print(f"  Translated: {len(crawler.translated)}")
    return 0


def cmd_search(args: argparse.Namespace) -> int:
    db = SnapshotDatabase(Path(args.db))
    if not list(db.root.glob("*.json")):
        print(f"No snapshots in {db.root}. Run 'crawl' first.", file=sys.stderr)
        return 1

    crawler = MultilingualWebCrawler(db)
    entities = [{"name": name, "country": ""} for name in args.entities]
    results = crawler.search_batch_entities(entities)

    all_findings: List[AMLFinding] = []
    for name, findings in results.items():
        print(f"\n{name}: {len(findings)} finding(s)")
        for finding in findings:
            print(
                f"  - [{finding.severity}] {finding.source} "
                f"({finding.source_language}) conf={finding.confidence_score}"
            )
        all_findings.extend(findings)

    reporter = AMLReporter(all_findings)
    json_path, md_path = reporter.write(Path(args.output))
    print(f"\nReports written:\n  {json_path}\n  {md_path}")
    return 0


def cmd_report(args: argparse.Namespace) -> int:
    findings_path = Path("aml-findings.json")
    findings: List[AMLFinding] = []
    if findings_path.exists():
        data = json.loads(findings_path.read_text(encoding="utf-8"))
        fields = AMLFinding.__dataclass_fields__
        for item in data.get("findings", []):
            # Migrate legacy partner_* keys if present
            if "entity_name" not in item and "partner_name" in item:
                item["entity_name"] = item.pop("partner_name")
            if "entity_country" not in item and "partner_country" in item:
                item["entity_country"] = item.pop("partner_country")
            findings.append(AMLFinding(**{k: item[k] for k in fields if k in item}))

    reporter = AMLReporter(findings)
    json_path, md_path = reporter.write(Path(args.output))
    print(f"Reports written:\n  {json_path}\n  {md_path}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="venombot",
        description="VenomBot — multilingual AML/KYC web crawler (MENA + US)",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_crawl = sub.add_parser("crawl", help="Crawl all sources and create snapshots")
    p_crawl.add_argument("--db", default="aml_snapshots", help="Snapshot database directory")
    p_crawl.add_argument("-o", "--output", default="aml-report", help="Report prefix")
    p_crawl.add_argument(
        "--no-sample",
        action="store_true",
        help="Do not fall back to sample content when fetch fails",
    )
    p_crawl.set_defaults(func=cmd_crawl)

    p_search = sub.add_parser("search", help="Search entities across snapshots")
    p_search.add_argument(
        "--entities",
        "--partners",
        dest="entities",
        nargs="+",
        required=True,
        help="Entity names to screen",
    )
    p_search.add_argument("--db", default="aml_snapshots", help="Snapshot database directory")
    p_search.add_argument("-o", "--output", default="aml-report", help="Output prefix")
    p_search.set_defaults(func=cmd_search)

    p_report = sub.add_parser("report", help="Regenerate reports from findings")
    p_report.add_argument("-o", "--output", default="aml-report", help="Output prefix")
    p_report.set_defaults(func=cmd_report)

    return parser


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)
