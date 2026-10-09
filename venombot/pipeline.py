"""One-command investigation pipeline that delivers its result in parts.

``venombot investigate --name "..."`` runs every stage in order and writes each
part to disk the moment it finishes, so a reviewer can start reading the list
hits while slow third-party lookups are still running:

    00-summary.md        progress checklist, rewritten after every stage
    01-lists.*           list matches (sanctions, terror, PEP, debarment, ...)
    02-media.*           stored news / regulator-feed mentions
    03-live-<kind>.*     one part per kind of live lookup (identity, corporate,
                         court, enforcement, leak, media)
    99-dossier.*         the consolidated dossier (same format as `screen`)
    state.json           stage results; ``--resume`` skips finished stages

A failing stage never stops the run: it is recorded and the next stage starts.
"""

from __future__ import annotations

import getpass
import json
import re
import time
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from venombot import dossier
from venombot.countries import country_name
from venombot.evidence import Evidence
from venombot.normalize import ascii_fold
from venombot.screening import Cluster, Hit, Subject, cluster_hits, overall, screen
from venombot.store import EntityStore

Log = Callable[[str], None]

# Providers that return the same records as another one; skipped unless
# --all-providers is given (the first of each family is kept).
REDUNDANT_PROVIDERS = {
    "COURTLISTENER_SEARCH",       # same dockets as US_COURTLISTENER_SEARCH
    "WIKIDATA_WBSEARCHENTITIES",  # same search as WIKIDATA_ENTITY_SEARCH
    "US_SEC_EDGAR_FTS",           # same full-text search as SEC_EDGAR_FULLTEXT
}

LIVE_KINDS = ["identity", "pep_info", "corporate", "court", "enforcement", "leak", "adverse_media"]


@dataclass
class Options:
    refresh: bool = False  # update missing/stale lists first
    refresh_groups: List[str] = field(default_factory=lambda: ["core"])
    refresh_media: bool = False
    media: bool = True
    live: bool = True
    live_kinds: List[str] = field(default_factory=lambda: list(LIVE_KINDS))
    live_providers: Optional[List[str]] = None  # restrict to these keys
    media_days: Optional[int] = None
    min_score: Optional[float] = None
    formats: List[str] = field(default_factory=lambda: ["md", "json"])
    purpose: str = ""
    requested_by: str = ""
    resume: bool = False
    accept_noncommercial: bool = False
    explain: bool = True  # print where each part's information comes from
    all_providers: bool = False  # include providers that duplicate another one


@dataclass
class StageResult:
    key: str
    title: str
    status: str = "pending"  # pending | running | ok | failed | skipped
    seconds: float = 0.0
    detail: str = ""
    files: List[str] = field(default_factory=list)


def slugify(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "-", ascii_fold(name)).strip("-")[:60] or "subject"


def dedupe_evidence(items: List[Evidence]) -> List[Evidence]:
    """Merge the same article/case/record reported by several providers.

    Providers overlap (three CourtListener endpoints, several Wikidata calls),
    so the same URL or title would otherwise be shown repeatedly. The first
    item is kept and the other providers are listed in ``extra['also_in']``.
    """
    seen: Dict[str, Evidence] = {}
    out: List[Evidence] = []
    for ev in items:
        key = (ev.url or "").split("#")[0].rstrip("/") or re.sub(r"\W+", " ", ev.title.lower()).strip()
        key = re.sub(r"^https?://(www\.)?", "", key)
        if key in seen:
            first = seen[key]
            also = first.extra.setdefault("also_in", [])
            if ev.provider != first.provider and ev.provider not in also:
                also.append(ev.provider)
            if ev.topics and not first.topics:
                first.topics = list(ev.topics)
            continue
        seen[key] = ev
        out.append(ev)
    return out


def _evidence_md(title: str, items: List[Evidence], note: str = "") -> str:
    lines = [f"# {title}", ""]
    if note:
        lines += [note, ""]
    if not items:
        lines.append("_Nothing found in the sources searched._")
        return "\n".join(lines) + "\n"
    adverse = [e for e in items if e.topics]
    other = [e for e in items if not e.topics]
    for head, group in (("Adverse-topic mentions", adverse), ("Other mentions and records", other)):
        if not group:
            continue
        lines += [f"## {head} ({len(group)})", ""]
        for e in group[:60]:
            date = f"{e.published[:10]} · " if e.published else ""
            topics = f" [{', '.join(e.topics)}]" if e.topics else ""
            also = f" (also: {', '.join(e.extra['also_in'])})" if e.extra.get("also_in") else ""
            lines.append(f"- {date}**{e.provider}** · {e.kind}{topics} · name match {e.name_score:.2f}{also}")
            lines.append(f"  - {e.title[:200]}")
            if e.snippet and e.snippet != e.title:
                lines.append(f"  - “{e.snippet[:300]}”")
            if e.url:
                lines.append(f"  - {e.url}")
        if len(group) > 60:
            lines.append(f"_{len(group) - 60} more in the JSON part._")
        lines.append("")
    return "\n".join(lines) + "\n"


def _hits_md(subject: Subject, hits: List[Hit], verdict: Dict[str, Any]) -> str:
    lines = [f"# List matches — {subject.name}", "",
             f"**Decision:** `{verdict['decision']}` · **Rating:** `{verdict['rating']}` · "
             f"{len(hits)} candidate(s)", ""]
    lines += [f"- {r}" for r in verdict["reasons"]]
    live = [h for h in hits if h.match_class != "DISCOUNTED"]
    disc = [h for h in hits if h.match_class == "DISCOUNTED"]
    if live:
        lines += ["", "| Class | Severity | Conf. | List | Listed party | Matched name |", "|---|---|---|---|---|---|"]
        for h in live[:60]:
            lines.append(f"| {h.match_class} | {h.severity} | {h.confidence:.0f} | {h.entity.source} | "
                         f"{h.entity.caption} | {h.matched_name} |")
        lines += ["", "## Details", ""]
        for h in live[:30]:
            lines.append(f"**{h.entity.caption}** — {h.entity.source} `{h.entity.source_id}` — action `{h.action}`")
            lines += [f"  - {e}" for e in h.evidence]
            lines += [f"  - conflict: {c}" for c in h.conflicts]
            if h.entity.url:
                lines.append(f"  - {h.entity.url}")
            lines.append("")
    else:
        lines += ["", "_No candidate above the reporting threshold._"]
    if disc:
        lines += ["", f"## Discounted ({len(disc)})", ""]
        lines += [f"- {h.entity.caption} ({h.entity.source}): {'; '.join(h.conflicts)}" for h in disc[:30]]
    return "\n".join(lines) + "\n"


def iso_now() -> str:
    """Current UTC time as ISO-8601 with seconds (e.g. 2026-10-09T10:34:12Z)."""
    return datetime.now(timezone.utc).replace(microsecond=0).strftime("%Y-%m-%dT%H:%M:%SZ")


def flag_counts(hits: List[Hit], evidence: List[Evidence]) -> Dict[str, Any]:
    """Counts of every flag the report headlines.

    List matches are counted per *party* (one person on seven lists is one match);
    ``list_entries`` keeps the raw number of list records.
    """
    clusters = cluster_hits(hits)
    live = [c for c in clusters if c.best.match_class != "DISCOUNTED"]
    return {
        "candidates": len(clusters),
        "relevant_matches": len(live),
        "list_entries": sum(len(c.hits) for c in live),
        "authorities": len({s for c in live for s in c.sources}),
        "by_class": dict(Counter(c.best.match_class for c in clusters)),
        "by_severity": dict(Counter(c.best.severity for c in live)),
        "by_list_type": dict(Counter(h.entity.list_type for c in live for h in c.hits)),
        "by_action": dict(Counter(c.best.action for c in live)),
        "context_items": len(evidence),
        "context_with_adverse_topics": sum(1 for e in evidence if e.topics),
        "by_kind": dict(Counter(e.kind for e in evidence)),
        "by_topic": dict(Counter(tp for e in evidence for tp in e.topics)),
    }


_LIFESPAN = re.compile(r"\b(1[89]\d\d)\s*[–-]\s*((?:19|20)\d\d)\b")


def deceased_hint(subject: Subject, hits: List[Hit], evidence: List[Evidence]) -> Optional[str]:
    """A death year seen in identity evidence, only when it plausibly is the listed person.

    Sanctions listings outlive people, and a reviewer should know. The life span
    ("1951–2022") must start in the subject's birth year or in a listed party's
    birth year, so a namesake is not reported as dead.
    """
    births = set()
    if subject.dob:
        births.add(subject.dob[:4])
    for h in hits:
        if h.match_class != "DISCOUNTED":
            births |= {d[:4] for d in h.entity.birth_dates}
    if not births:
        return None
    for e in evidence:
        if e.kind not in ("identity", "pep_info") or e.name_score < 0.95:
            continue
        m = _LIFESPAN.search(e.snippet or "") or _LIFESPAN.search(e.title or "")
        if m and m.group(1) in births:
            return f"{m.group(1)}–{m.group(2)} per {e.provider} ({e.url or e.title})"
    return None


def is_weak(e: Evidence) -> bool:
    """Name matched by the server but no text shown: cannot be checked, so not listed."""
    basis = str(e.extra.get("match_basis") or "").lower()
    return (not e.snippet or e.snippet == e.title) and "server" in basis


def _fmt_counts(d: Dict[str, int], order: Optional[List[str]] = None) -> str:
    if not d:
        return "none"
    keys = [k for k in (order or []) if k in d] + sorted(k for k in d if k not in (order or []))
    return ", ".join(f"{d[k]} {k}" for k in keys)


def render_report(subject: Subject, hits: List[Hit], evidence: List[Evidence], lookups: List[Any],
                  stages: List[StageResult], cov: Dict[str, Any], requested_by: str, purpose: str,
                  started: str, finished: str) -> str:
    """Single consolidated Markdown report with the headline summary on top."""
    verdict = overall(hits)
    weak = [e for e in evidence if is_weak(e)]
    evidence = [e for e in evidence if not is_weak(e)]
    c = flag_counts(hits, evidence)
    clusters = cluster_hits(hits)
    death = deceased_hint(subject, hits, evidence)
    ok = [l for l in lookups if l.ok]
    failed = [l for l in lookups if not l.ok and not l.skipped]
    skipped = [l for l in lookups if l.skipped]
    lines = [
        f"# Investigation report — {subject.name}",
        "",
        f"- **Requested by:** {requested_by}",
        f"- **Subject:** {subject.name}"
        + (f" · DOB {subject.dob}" if subject.dob else " · DOB not provided")
        + (f" · {', '.join(country_name(c) for c in subject.countries)}" if subject.countries else ""),
        f"- **Search started:** {started} (UTC)",
        f"- **Search finished:** {finished} (UTC)",
    ]
    if purpose:
        lines.append(f"- **Purpose:** {purpose}")
    lines += ["", "## Summary", "",
              f"**Decision: `{verdict['decision']}` · Rating: `{verdict['rating']}`**", ""]
    headline = (f"Found **{c['relevant_matches']}** relevant person/entity match(es) on "
                f"**{c['list_entries']}** list entr{'y' if c['list_entries'] == 1 else 'ies'} from "
                f"**{c['authorities']}** list(s) ({c['candidates'] - c['relevant_matches']} discounted) and "
                f"**{c['context_items']}** context item(s), **{c['context_with_adverse_topics']}** of them with an "
                f"adverse topic.")
    lines += [headline, "", "| Flag | Count |", "|---|---|",
              f"| Matches (persons/entities) — by class | {_fmt_counts(c['by_class'], ['CONFIRMED', 'PROBABLE', 'POSSIBLE', 'DISCOUNTED'])} |",
              f"| Matches — by severity | {_fmt_counts(c['by_severity'], ['CRITICAL', 'HIGH', 'MEDIUM', 'LOW', 'INFO'])} |",
              f"| List entries — by list type | {_fmt_counts(c['by_list_type'])} |",
              f"| Recommended actions | {_fmt_counts(c['by_action'])} |",
              f"| Context — by kind | {_fmt_counts(c['by_kind'])} |",
              f"| Context — adverse topics | {_fmt_counts(c['by_topic'])} |",
              f"| Live lookups | {len(ok)} answered, {len(failed)} failed, {len(skipped)} skipped |", ""]
    live_clusters = [cl for cl in clusters if cl.best.match_class != "DISCOUNTED"]
    for cl in live_clusters[:10]:
        lines.append(f"- {cl.best.match_class} {cl.best.entity.list_type} match: **{cl.label}** — listed by "
                     f"{len(cl.sources)} list(s): {', '.join(cl.sources)}")
    if len(live_clusters) > 10:
        lines.append(f"- … {len(live_clusters) - 10} more matches below")
    if death:
        lines.append(f"- **Possible death recorded:** {death}. Listings can outlive the person; check whether "
                     "the designation still applies and whether this is the same individual.")
    problems = [s for s in stages if s.status in ("failed", "skipped", "partial")]
    if problems or failed:
        lines += ["", "**Gaps in this search:**"]
        lines += [f"- {s.title}: {s.status} — {s.detail}" for s in problems]
        lines += [f"- {l.provider}: failed — {l.error[:120]}" for l in failed]
    if cov["lists_stale"] or cov["lists_failed_last_update"]:
        lines.append(f"- Lists stale: {', '.join(cov['lists_stale']) or '—'}; "
                     f"failed last update: {', '.join(cov['lists_failed_last_update']) or '—'}")

    lines += ["", "## 1. List matches", ""]
    if not live_clusters:
        lines += ["_No candidate above the reporting threshold in the lists searched._", ""]
    else:
        lines += ["One row per person/entity; the same party listed by several authorities is grouped.", "",
                  "| Class | Severity | Conf. | Listed party | Lists | Action |", "|---|---|---|---|---|---|"]
        for cl in live_clusters[:60]:
            b = cl.best
            lines.append(f"| {b.match_class} | {b.severity} | {b.confidence:.0f} | {cl.label} | "
                         f"{len(cl.sources)}: {', '.join(cl.sources)} | {b.action} |")
        lines.append("")
        for cl in live_clusters[:30]:
            b = cl.best
            lines.append(f"**{cl.label}** — {len(cl.hits)} list entr{'y' if len(cl.hits) == 1 else 'ies'}")
            lines += [f"  - best evidence ({b.entity.source}): {e}" for e in b.evidence]
            lines += [f"  - conflict: {x}" for x in b.conflicts]
            for h in sorted(cl.hits, key=lambda h: h.entity.source):
                names = "; ".join(n.value for n in h.entity.names[:4])
                bits = [f"{h.entity.source} `{h.entity.source_id}`", names]
                if h.entity.birth_dates:
                    bits.append("DOB " + ", ".join(h.entity.birth_dates[:2]))
                if h.entity.url:
                    bits.append(h.entity.url)
                lines.append("  - " + " · ".join(b_ for b_ in bits if b_))
            lines.append("")
    disc = [cl for cl in clusters if cl.best.match_class == "DISCOUNTED"]
    if disc:
        lines += [f"Discounted candidates ({len(disc)}): " + "; ".join(
            f"{cl.label} ({', '.join(cl.sources)})" for cl in disc[:10]), ""]

    adverse = [e for e in evidence if e.topics]
    other = [e for e in evidence if not e.topics]
    for n, (head, group) in enumerate((("Adverse-topic mentions", adverse),
                                       ("Other mentions and records", other)), start=2):
        lines += [f"## {n}. {head} ({len(group)})", ""]
        if not group:
            lines += ["_None._", ""]
            continue
        for e in group[:60]:
            date = f"{e.published[:10]} · " if e.published else ""
            topics = f" [{', '.join(e.topics)}]" if e.topics else ""
            also = f" (also: {', '.join(e.extra['also_in'])})" if e.extra.get("also_in") else ""
            lines.append(f"- {date}**{e.provider}** · {e.kind}{topics} · name match {e.name_score:.2f}{also}")
            lines.append(f"  - {e.title[:200]}")
            if e.snippet and e.snippet != e.title:
                lines.append(f"  - “{e.snippet[:300]}”")
            if e.url:
                lines.append(f"  - {e.url}")
        if len(group) > 60:
            lines.append(f"_{len(group) - 60} more in the JSON parts._")
        lines.append("")

    if weak:
        lines += [f"_{len(weak)} weak match(es) omitted: the service matched the name but returned no text to "
                  "verify (kept in the JSON parts)._", ""]
    lines += ["## 4. Coverage", "",
              f"- Lists searched: {cov['lists_searchable']} ({cov['entities_searchable']:,} entities)",
              f"- Live providers queried (the subject name was sent to each): "
              + (", ".join(f"{l.provider}={'skip' if l.skipped else ('fail' if not l.ok else l.count)}"
                           for l in lookups) or "none"), "",
              "## 5. Parts", ""]
    for s in stages:
        lines.append(f"- {s.title}: {s.status} {('— ' + s.detail) if s.detail else ''} "
                     + ", ".join(f"`{Path(f).name}`" for f in s.files))
    lines += ["", "## 6. Review", "", "- Reviewer: ____________  - Disposition: ____________  - Date: ________", "",
              "## 7. Disclaimer", "", dossier.DISCLAIMER, ""]
    return "\n".join(lines)


class Investigation:
    """Runs the stages for one subject and persists each part as it completes."""

    def __init__(self, store: EntityStore, subject: Subject, out_dir: Path, opts: Options,
                 log: Log = print, db_path: Optional[Path] = None) -> None:
        self.store, self.subject, self.opts, self.log = store, subject, opts, log
        self.db_path = db_path
        self.dir = Path(out_dir) / slugify(subject.name)
        self.dir.mkdir(parents=True, exist_ok=True)
        self.started = iso_now()
        self.report_path: Optional[Path] = None
        self.stages: List[StageResult] = []
        self.hits: List[Hit] = []
        self.evidence: List[Evidence] = []
        self.media_evidence: List[Evidence] = []
        self.lookups: List[Any] = []
        self.media_searched = False
        self._state_path = self.dir / "state.json"
        self._prior: Dict[str, Any] = {}
        if opts.resume and self._state_path.exists():
            try:
                self._prior = json.loads(self._state_path.read_text(encoding="utf-8"))
            except ValueError:
                self._prior = {}

    # -- bookkeeping ---------------------------------------------------------

    def _write(self, name: str, text: str) -> str:
        path = self.dir / name
        path.write_text(text, encoding="utf-8")
        return str(path)

    def _save_state(self) -> None:
        data = {
            "subject": {"name": self.subject.name, "dob": self.subject.dob, "countries": self.subject.countries,
                        "kind": self.subject.kind},
            "updated": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
            "stages": [s.__dict__ for s in self.stages],
            "evidence": [e.to_dict() for e in self.evidence],
            "media": [e.to_dict() for e in self.media_evidence],
            "lookups": [l.__dict__ for l in self.lookups],
            "media_searched": self.media_searched,
        }
        self._state_path.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")

    def _write_summary(self) -> None:
        lines = [f"# Investigation — {self.subject.name}", "",
                 f"_Updated {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}_", ""]
        if self.hits or any(s.key == "lists" and s.status == "ok" for s in self.stages):
            v = overall(self.hits)
            lines += [f"**List decision:** `{v['decision']}` · rating `{v['rating']}`", ""]
        icon = {"ok": "✅", "partial": "⚠️", "failed": "❌", "skipped": "⏭", "running": "⏳", "pending": "·"}
        lines += ["| Part | Status | Time | Detail |", "|---|---|---|---|"]
        for s in self.stages:
            files = ", ".join(Path(f).name for f in s.files)
            lines.append(f"| {s.title} | {icon.get(s.status, '')} {s.status} | {s.seconds:.0f}s | "
                         f"{(s.detail + ' ' + files).strip()} |")
        lines += ["", "Context items (articles, court records, registry entries) are for a reviewer; "
                  "they are never list matches and allegations are unproven.", ""]
        self._write("00-summary.md", "\n".join(lines))

    def _run_stage(self, key: str, title: str, fn: Callable[[StageResult], None]) -> StageResult:
        st = StageResult(key, title)
        self.stages.append(st)
        prior = next((p for p in self._prior.get("stages", []) if p["key"] == key and p["status"] == "ok"), None)
        if prior and self.opts.resume:
            st.status, st.detail, st.files = "ok", f"resumed ({prior['detail']})", prior.get("files", [])
            self.log(f"[{key}] resumed from previous run")
            self._restore(key)
            return st
        st.status = "running"
        self._write_summary()
        self.log(f"[{key}] {title} …")
        t0 = time.monotonic()
        try:
            fn(st)
            if st.status == "running":
                st.status = "ok"
        except Exception as exc:  # noqa: BLE001 - one failed stage must not stop the rest
            st.status, st.detail = "failed", f"{type(exc).__name__}: {exc}"[:300]
        st.seconds = time.monotonic() - t0
        self.log(f"[{key}] {st.status}{(' — ' + st.detail) if st.detail else ''} ({st.seconds:.0f}s)")
        self._write_summary()
        self._save_state()
        return st

    def _restore(self, key: str) -> None:
        """Reload data of a resumed stage so later parts and the dossier include it."""
        if key == "media":
            self.media_evidence = [Evidence(**e) for e in self._prior.get("media", [])]
            self.media_searched = bool(self._prior.get("media_searched"))
        elif key.startswith("live:"):
            kind = key.split(":", 1)[1]
            self.evidence += [Evidence(**e) for e in self._prior.get("evidence", []) if e["kind"] == kind
                              or (kind == "identity" and e["kind"] in ("identity", "pep_info"))]
            if not self.lookups:
                from venombot.live import LookupRecord

                self.lookups = [LookupRecord(**l) for l in self._prior.get("lookups", [])]

    # -- terminal explanations -----------------------------------------------

    def _say(self, lines: List[str]) -> None:
        if self.opts.explain:
            for line in lines:
                self.log("      " + line)

    def _explain_lists(self) -> List[str]:
        """Which lists are searched, grouped by issuing jurisdiction, and what is missing."""
        from venombot.lists import all_list_sources

        rows = [s for s in self.store.status() if s.entity_count > 0]
        total = sum(s.entity_count for s in rows)
        by_jur: Dict[str, list] = {}
        for s in rows:
            by_jur.setdefault(s.jurisdiction or "?", []).append(s)
        lines = [f"Searching {len(rows)} loaded list(s), {total:,} entities, by issuing authority:"]
        for jur in sorted(by_jur):
            label = {"UN": "United Nations", "EU": "European Union", "GLOBAL": "Global"}.get(jur, country_name(jur.lower()) or jur)
            for s in sorted(by_jur[jur], key=lambda x: x.key):
                when = (s.source_updated or s.fetched_at or "")[:10]
                lines.append(f"{label[:16]:16} {s.list_type.lower():10} {s.entity_count:>7,}  {s.name[:58]}"
                             f"  (data {when})")
        reg = all_list_sources()
        loaded = {s.key for s in rows}
        missing: Dict[str, int] = {}
        for k, src in reg.items():
            if k not in loaded:
                missing[src.list_type.lower()] = missing.get(src.list_type.lower(), 0) + 1
        keep = ["sanctions", "terrorism", "pep", "debarment", "enforcement", "wanted", "crime"]
        gaps = [f"{missing[k]} {k}" for k in keep if k in missing]
        if gaps:
            lines.append("NOT searched (registered but not loaded): " + ", ".join(gaps)
                         + "  → venombot update --group pep terrorism debarment enforcement wanted")
        return lines

    def _explain_media(self, n_articles: int) -> List[str]:
        from venombot.feeds import all_feeds

        feeds = all_feeds()
        kinds = Counter(f.kind for f in feeds.values())
        return [f"Searching {n_articles:,} stored articles from up to {len(feeds)} feeds "
                f"({', '.join(f'{v} {k}' for k, v in sorted(kinds.items()))}); matching is sentence-level, "
                "not full-text."]

    def _explain_live(self, kind: str, providers: List[Any]) -> List[str]:
        what = {"identity": "identity and PEP facts (who the name could be)",
                "corporate": "company registries, LEI and ownership records",
                "court": "court opinions and case law",
                "enforcement": "regulator actions and government press releases",
                "leak": "leaked-document databases (context only)",
                "adverse_media": "news (negative-news search)"}.get(kind, kind)
        lines = [f"Contacting {len(providers)} external service(s) for {what}.",
                 "The subject's NAME is sent to each of them:"]
        for p in providers:
            lines.append(f"{p.jurisdiction[:6]:6} {p.key[:34]:34} {(p.notes or p.name)[:60]}")
        return lines

    # -- stages --------------------------------------------------------------

    def _stage_refresh(self, st: StageResult) -> None:
        from venombot.lists import select_sources
        from venombot.update import update_sources

        wanted = {s.key: s for s in select_sources(None, self.opts.refresh_groups)}
        loaded = {s.key: s for s in self.store.status()}
        todo = []
        for key, src in wanted.items():
            cur = loaded.get(key)
            if src.license == "CC-BY-NC" and not self.opts.accept_noncommercial:
                continue
            if cur is None or cur.status != "ok" or cur.entity_count == 0:
                todo.append(src)
                continue
            age = dossier._age_days(cur.source_updated or cur.fetched_at)
            if age is None or age > src.max_age_days:
                todo.append(src)
        if not todo:
            st.status, st.detail = "skipped", "all requested lists are fresh"
            return
        results = update_sources(self.store, todo, log=lambda m: None)
        ok = sum(1 for r in results if r.status == "ok")
        st.detail = f"{ok}/{len(results)} lists updated"

    def _stage_lists(self, st: StageResult) -> None:
        self._say(self._explain_lists())
        self.hits = screen(self.store, self.subject, min_name_score=self.opts.min_score)
        verdict = overall(self.hits)
        files = [self._write("01-lists.md", _hits_md(self.subject, self.hits, verdict))]
        if "json" in self.opts.formats:
            files.append(self._write("01-lists.json", json.dumps(
                {"verdict": verdict, "hits": [h.to_dict() for h in self.hits]}, ensure_ascii=False, indent=1)))
        st.files = files
        st.detail = f"{verdict['decision']}, {len(self.hits)} candidate(s)"

    def _stage_media_refresh(self, st: StageResult) -> None:
        from venombot.feeds import select_feeds
        from venombot.feeds.store import ArticleStore
        from venombot.feeds.update import update_feeds

        astore = ArticleStore(self.db_path or Path("venombot_data/venombot.db"))
        result = update_feeds(astore, select_feeds(), workers=4, log=lambda m: None)
        bad = [k for k, v in result.items() if v.get("status") == "failed"]
        st.detail = f"{len(result) - len(bad)}/{len(result)} feeds ok"

    def _stage_media(self, st: StageResult) -> None:
        from venombot.feeds.search import search_articles
        from venombot.feeds.store import ArticleStore

        astore = ArticleStore(self.db_path or Path("venombot_data/venombot.db"))
        if astore.count() == 0:
            st.status, st.detail = "skipped", "no articles stored; run `venombot media update` or use --refresh-media"
            self._write("02-media.md", _evidence_md("Media and regulator feeds", [],
                                                    "_No articles are stored yet, so nothing was searched._"))
            return
        self._say(self._explain_media(astore.count()))
        self.media_searched = True
        self.media_evidence = dedupe_evidence(search_articles(astore, self.subject, days=self.opts.media_days))
        st.files = [self._write("02-media.md", _evidence_md(
            "Media and regulator feeds", self.media_evidence,
            f"Searched {astore.count():,} stored articles at sentence level."))]
        if "json" in self.opts.formats:
            st.files.append(self._write("02-media.json", json.dumps(
                [e.to_dict() for e in self.media_evidence], ensure_ascii=False, indent=1)))
        adverse = sum(1 for e in self.media_evidence if e.topics)
        st.detail = f"{len(self.media_evidence)} item(s), {adverse} with adverse topics"

    def _stage_live(self, kind: str, st: StageResult) -> None:
        from venombot.live import lookup, select_providers

        kinds = {kind, "pep_info"} if kind == "identity" else {kind}
        providers = [p for p in select_providers() if p.kind in kinds]
        if self.opts.live_providers:
            providers = [p for p in providers if p.key in self.opts.live_providers]
        elif not self.opts.all_providers:
            providers = [p for p in providers if p.key not in REDUNDANT_PROVIDERS]
        if not providers:
            st.status, st.detail = "skipped", "no available providers (keys not set?)"
            return
        self._say(self._explain_live(kind, providers))
        found, records = lookup(self.subject, providers)
        found = dedupe_evidence(found)
        self.evidence += found
        self.lookups += records
        failed = [r.provider for r in records if not r.ok and not r.skipped]
        self._say([f"Result: {r.provider}: " + ("skipped (" + r.skipped + ")" if r.skipped else
                   ("FAILED " + r.error[:70] if not r.ok else f"{r.count} result(s)"))
                   for r in records if r.skipped or not r.ok or r.count])
        note = (f"Queried {len(records)} provider(s) (the subject name was sent to each): "
                + ", ".join(f"{r.provider}={'skip' if r.skipped else ('fail' if not r.ok else r.count)}"
                            for r in records))
        name = f"03-live-{kind}"
        st.files = [self._write(f"{name}.md", _evidence_md(f"Live lookups — {kind}", found, note))]
        if "json" in self.opts.formats:
            st.files.append(self._write(f"{name}.json", json.dumps(
                {"lookups": [r.__dict__ for r in records], "evidence": [e.to_dict() for e in found]},
                ensure_ascii=False, indent=1)))
        answered = sum(1 for r in records if r.ok)
        st.detail = f"{len(found)} item(s) from {answered}/{len(records)} providers"
        if records and answered == 0:
            st.status = "failed"
            st.detail += " — every provider failed or was skipped, so nothing was actually searched"
        elif failed:
            st.status = "partial"
            st.detail += f" — failed: {', '.join(failed)}"

    def _stage_dossier(self, st: StageResult) -> None:
        evidence = dedupe_evidence(self.media_evidence + self.evidence)
        requested_by = self.opts.requested_by or getpass.getuser()
        finished = iso_now()
        st.status = "ok"  # this report is the stage's output; do not list it as "running" inside itself
        cov = dossier.coverage(self.store)
        report = render_report(self.subject, self.hits, evidence, self.lookups, self.stages, cov,
                               requested_by, self.opts.purpose, self.started, finished)
        # File name = subject + ISO timestamp (colons are not portable in file names).
        stamp = self.started.replace(":", "-")
        main = self.dir / f"{slugify(self.subject.name)}_{stamp}.md"
        files = [self._write(main.name, report)]
        extra = [f for f in ("json", "html") if f in self.opts.formats]
        if extra:
            doc = dossier.build(self.store, self.subject, self.hits, purpose=self.opts.purpose,
                                requested_by=requested_by, evidence=evidence, lookups=self.lookups,
                                media_searched=self.media_searched)
            doc["pipeline"] = {"stages": [s.__dict__ for s in self.stages], "started": self.started,
                               "finished": finished, "flags": flag_counts(self.hits, evidence)}
            files += [str(x) for x in dossier.write(doc, self.dir / f"{slugify(self.subject.name)}_{stamp}", extra)]
        st.files = files
        self.report_path = main
        st.detail = main.name

    # -- driver --------------------------------------------------------------

    def run(self) -> List[StageResult]:
        if self.opts.refresh:
            self._run_stage("refresh", "Update lists", self._stage_refresh)
        self._run_stage("lists", "1 · List matches", self._stage_lists)
        if self.opts.media:
            if self.opts.refresh_media:
                self._run_stage("media-refresh", "Update news feeds", self._stage_media_refresh)
            self._run_stage("media", "2 · Stored media and regulator feeds", self._stage_media)
        if self.opts.live:
            for kind in self.opts.live_kinds:
                if kind == "pep_info":
                    continue  # folded into the identity part
                self._run_stage(f"live:{kind}", f"3 · Live lookups — {kind}",
                                lambda st, k=kind: self._stage_live(k, st))
        self._run_stage("dossier", "4 · Consolidated report", self._stage_dossier)
        return self.stages
