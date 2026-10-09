"""Command-line interface for VenomBot."""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from pathlib import Path
from typing import List, Optional

from venombot.crawler import MultilingualWebCrawler
from venombot.models import AMLFinding
from venombot.reporter import AMLReporter
from venombot.storage import SnapshotDatabase

DEFAULT_DB = "venombot_data/venombot.db"


# --- structured lists -------------------------------------------------------

def cmd_update(args: argparse.Namespace) -> int:
    from venombot.lists import select_sources
    from venombot.store import EntityStore
    from venombot.update import update_sources

    try:
        sources = select_sources(args.source, args.group)
    except KeyError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    nc = [s for s in sources if s.license == "CC-BY-NC"]
    if nc and not args.accept_noncommercial:
        print(
            f"{len(nc)} selected source(s) are licensed CC BY-NC (OpenSanctions): free for "
            "non-commercial use only. Re-run with --accept-noncommercial to confirm your use "
            "is non-commercial or that you hold a commercial licence.",
            file=sys.stderr,
        )
        sources = [s for s in sources if s.license != "CC-BY-NC"]
        if not sources:
            return 2
    store = EntityStore(Path(args.db))
    results = update_sources(store, sources, workers=args.workers,
                             keep_raw=Path(args.keep_raw) if args.keep_raw else None)
    failed = [r for r in results if r.status == "failed"]
    return 1 if failed and len(failed) == len(results) else 0


def cmd_lists(args: argparse.Namespace) -> int:
    from venombot.lists import all_list_sources, select_sources
    from venombot.store import EntityStore

    if args.refresh_opensanctions:
        from venombot.lists.opensanctions import refresh_index

        n = refresh_index()
        print(f"OpenSanctions dataset index refreshed: {n} datasets")
        return 0
    status = {}
    if Path(args.db).exists():
        status = {s.key: s for s in EntityStore(Path(args.db)).status()}
    sources = select_sources(None, args.group or ["all"])
    if args.loaded:
        sources = [s for s in sources if s.key in status and status[s.key].entity_count]
    if args.json:
        rows = []
        for s in sources:
            st = status.get(s.key)
            rows.append({"key": s.key, "name": s.name, "jurisdiction": s.jurisdiction,
                         "list_type": s.list_type, "groups": s.groups, "license": s.license,
                         "needs_key": s.api_key_env, "status": st.status if st else "never",
                         "entities": st.entity_count if st else 0,
                         "data_as_of": (st.source_updated if st else "")})
        print(json.dumps(rows, ensure_ascii=False, indent=2))
        return 0
    print(f"{'KEY':40} {'TYPE':17} {'JUR':7} {'LICENSE':9} {'STATUS':8} {'ENTITIES':>9}  NAME")
    for s in sources:
        st = status.get(s.key)
        print(f"{s.key[:40]:40} {s.list_type:17} {s.jurisdiction[:7]:7} {s.license[:9]:9} "
              f"{(st.status if st else 'never'):8} {(st.entity_count if st else 0):>9}  {s.name[:70]}")
    reg = all_list_sources()
    groups = sorted({g for s in reg.values() for g in s.groups})
    print(f"\n{len(sources)} shown / {len(reg)} registered. Groups: {', '.join(groups)}")
    return 0


def _subjects_from_args(args: argparse.Namespace):
    from venombot.screening import Subject

    if args.csv:
        subjects = []
        with open(args.csv, newline="", encoding="utf-8-sig") as fh:
            for row in csv.DictReader(fh):
                row = {k.strip().lower(): (v or "").strip() for k, v in row.items() if k}
                if not row.get("name"):
                    continue
                subjects.append(Subject.build(
                    row["name"], dob=row.get("dob", ""), country=row.get("country", ""),
                    kind=row.get("type", "") or args.type,
                    identifiers=[i for i in re.split(r"[|;]", row.get("id", "")) if i],
                    aliases=[a for a in re.split(r"[|;]", row.get("aliases", "")) if a],
                ))
        return subjects
    return [Subject.build(args.name, dob=args.dob or "", country=args.country or "",
                          kind=args.type, identifiers=args.id or [], aliases=args.alias or [])]


def cmd_screen(args: argparse.Namespace) -> int:
    from venombot import dossier
    from venombot.normalize import ascii_fold
    from venombot.screening import overall, screen
    from venombot.store import EntityStore

    if not args.name and not args.csv:
        print("error: give --name or --csv", file=sys.stderr)
        return 2
    if not Path(args.db).exists():
        print(f"No list database at {args.db}. Run 'venombot update' first.", file=sys.stderr)
        return 1
    store = EntityStore(Path(args.db))
    if store.count() == 0:
        print("List database is empty. Run 'venombot update' first.", file=sys.stderr)
        return 1
    subjects = _subjects_from_args(args)
    formats = args.format or ["json", "md"]
    out_dir = Path(args.output)
    worst = 0
    rank = {"NONE": 0, "LOW": 1, "MEDIUM": 2, "HIGH": 3, "CRITICAL": 4}
    for subject in subjects:
        hits = screen(store, subject, min_name_score=args.min_score, sources=args.source)
        verdict = overall(hits)
        worst = max(worst, rank.get(verdict["rating"], 0))
        print(f"\n{subject.name}: {verdict['decision']} (rating {verdict['rating']}, {len(hits)} candidate(s))")
        for h in hits[: args.show]:
            print(f"  [{h.match_class:10}] {h.severity:8} {h.confidence:5.1f}  {h.entity.source:28} "
                  f"{h.entity.caption[:60]}  <- '{h.matched_name[:40]}'")
        if len(hits) > args.show:
            print(f"  … {len(hits) - args.show} more in the report")
        doc = dossier.build(store, subject, hits, purpose=args.purpose or "",
                            requested_by=args.requested_by or "")
        slug = re.sub(r"[^A-Za-z0-9]+", "-", ascii_fold(subject.name)).strip("-")[:60] or "subject"
        paths = dossier.write(doc, out_dir / f"screening-{slug}", formats)
        print("  report: " + ", ".join(str(p) for p in paths))
    return 3 if args.fail_on_hit and worst >= rank["HIGH"] else 0


# --- legacy snapshot crawler ------------------------------------------------

def cmd_crawl(args: argparse.Namespace) -> int:
    db = SnapshotDatabase(Path(args.db))
    crawler = MultilingualWebCrawler(db, use_sample_fallback=args.demo)
    crawler.crawl_all_sources()
    crawler.translate_snapshots()
    print(f"\nDone. Snapshots in: {db.root.resolve()}")
    print(f"  Original: {len(crawler.snapshots)}")
    print(f"  Translated: {len(crawler.translated)}")
    return 0


def cmd_search(args: argparse.Namespace) -> int:
    print(
        "note: 'search' scans raw page snapshots (news, context pages). For sanctions/PEP "
        "screening use 'venombot screen --name ...', which matches parsed list records.",
        file=sys.stderr,
    )
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
        description="VenomBot — multilingual AML/KYC screening (sanctions, PEP, wanted, debarment)",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_up = sub.add_parser("update", help="Download and index structured lists")
    p_up.add_argument("--source", nargs="+", help="Source keys (see 'lists')")
    p_up.add_argument("--group", nargs="+",
                      help="Groups: core, sanctions, pep, wanted, mena, opensanctions, os-pep, all, …")
    p_up.add_argument("--db", default=DEFAULT_DB, help="List database path")
    p_up.add_argument("--workers", type=int, default=4, help="Parallel downloads")
    p_up.add_argument("--keep-raw", help="Archive raw downloads in this directory (evidence)")
    p_up.add_argument("--accept-noncommercial", action="store_true",
                      help="Allow CC BY-NC sources (OpenSanctions) — non-commercial use only")
    p_up.set_defaults(func=cmd_update)

    p_ls = sub.add_parser("lists", help="Show registered list sources and their status")
    p_ls.add_argument("--group", nargs="+", help="Filter by group/type/jurisdiction")
    p_ls.add_argument("--loaded", action="store_true", help="Only lists with data")
    p_ls.add_argument("--json", action="store_true", help="JSON output")
    p_ls.add_argument("--db", default=DEFAULT_DB)
    p_ls.add_argument("--refresh-opensanctions", action="store_true",
                      help="Re-download the OpenSanctions dataset index")
    p_ls.set_defaults(func=cmd_lists)

    p_sc = sub.add_parser("screen", help="Screen a person or organisation and write a dossier")
    p_sc.add_argument("--name", help='Full name, quoted: --name "Jeffrey Epstein"')
    p_sc.add_argument("--alias", nargs="+", help="Other known names to screen too")
    p_sc.add_argument("--dob", help="Date of birth (any format; year-only accepted)")
    p_sc.add_argument("--country", help="Nationality/country, e.g. 'KW' or 'Kuwait'")
    p_sc.add_argument("--type", choices=["person", "org", "any"], default="any")
    p_sc.add_argument("--id", nargs="+", help="Passport/ID/registration numbers")
    p_sc.add_argument("--csv", help="Batch file with columns name,dob,country,type,id,aliases")
    p_sc.add_argument("--source", nargs="+", help="Restrict to these list keys")
    p_sc.add_argument("--min-score", type=float, help="Name-score floor (default 0.80)")
    p_sc.add_argument("--db", default=DEFAULT_DB)
    p_sc.add_argument("-o", "--output", default="reports", help="Output directory")
    p_sc.add_argument("--format", nargs="+", choices=["json", "md", "html"])
    p_sc.add_argument("--purpose", help="Why this screening is performed (recorded in report)")
    p_sc.add_argument("--requested-by", help="Requester (recorded in report)")
    p_sc.add_argument("--show", type=int, default=10, help="Candidates to print")
    p_sc.add_argument("--fail-on-hit", action="store_true",
                      help="Exit 3 when any subject rates HIGH or CRITICAL (for pipelines)")
    p_sc.set_defaults(func=cmd_screen)

    p_crawl = sub.add_parser("crawl", help="(legacy) Snapshot news/context pages")
    p_crawl.add_argument("--db", default="aml_snapshots", help="Snapshot database directory")
    p_crawl.add_argument("-o", "--output", default="aml-report", help="Report prefix")
    p_crawl.add_argument("--demo", action="store_true",
                         help="Store bundled demo content when a fetch fails (never for real use)")
    p_crawl.add_argument("--no-sample", action="store_true", help=argparse.SUPPRESS)
    p_crawl.set_defaults(func=cmd_crawl)

    p_search = sub.add_parser("search", help="(legacy) Text search over page snapshots")
    p_search.add_argument("--entities", "--partners", dest="entities", nargs="+", required=True)
    p_search.add_argument("--db", default="aml_snapshots", help="Snapshot database directory")
    p_search.add_argument("-o", "--output", default="aml-report", help="Output prefix")
    p_search.set_defaults(func=cmd_search)

    p_report = sub.add_parser("report", help="(legacy) Regenerate snapshot-search reports")
    p_report.add_argument("-o", "--output", default="aml-report", help="Output prefix")
    p_report.set_defaults(func=cmd_report)

    return parser


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)
