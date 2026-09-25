"""Multilingual AML web crawler orchestration."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from venombot.fetch import DEFAULT_TIMEOUT, DEFAULT_USER_AGENT, fetch_url, strip_html
from venombot.models import AMLFinding, AMLSource, Snapshot, TranslatedSnapshot
from venombot.scoring import extract_evidence, severity_and_score
from venombot.sources import SAMPLE_CONTENT, AMLSourcesRegistry
from venombot.storage import SnapshotDatabase
from venombot.translator import SimpleTranslator


class MultilingualWebCrawler:
    USER_AGENT = DEFAULT_USER_AGENT
    TIMEOUT = DEFAULT_TIMEOUT

    def __init__(self, db: SnapshotDatabase, use_sample_fallback: bool = True) -> None:
        self.db = db
        self.use_sample_fallback = use_sample_fallback
        self.sources = AMLSourcesRegistry.get_sources()
        self.snapshots: List[Snapshot] = []
        self.translated: List[TranslatedSnapshot] = []
        self.findings: List[AMLFinding] = []

    def crawl_source(self, source: AMLSource) -> Snapshot:
        print(f"  Crawling {source.name} ({source.language}/{source.region})...")
        content = fetch_url(source.url, user_agent=self.USER_AGENT, timeout=self.TIMEOUT)
        if content is None or len(content.strip()) < 50:
            if self.use_sample_fallback:
                content = SAMPLE_CONTENT.get(source.name, f"Demo content for {source.name}")
                print(f"  [info] using sample content for {source.name}")
            else:
                content = f"EMPTY_FETCH:{source.name}"

        content = strip_html(content)

        if source.language == "mixed":
            detected = SimpleTranslator.detect_language(content)
            source_for_snap = AMLSource(
                name=source.name,
                url=source.url,
                language=detected,
                region=source.region,
                source_type=source.source_type,
                update_frequency=source.update_frequency,
            )
        else:
            source_for_snap = source

        snapshot = Snapshot.make(source_for_snap, content)
        path = self.db.save_snapshot(snapshot)
        print(f"  [ok] saved {path.name} (sha256={snapshot.checksum[:12]}…)")
        self.snapshots.append(snapshot)
        return snapshot

    def crawl_all_sources(self) -> List[Snapshot]:
        print(f"Crawling {len(self.sources)} AML sources…")
        self.snapshots = []
        for source in self.sources:
            self.crawl_source(source)
        return self.snapshots

    def translate_snapshots(
        self,
        snapshots: Optional[List[Snapshot]] = None,
    ) -> List[TranslatedSnapshot]:
        snaps = snapshots if snapshots is not None else self.snapshots
        print("Translating Arabic snapshots…")
        self.translated = []
        for snap in snaps:
            detected = SimpleTranslator.detect_language(snap.content)
            if snap.language not in ("ar", "mixed") and detected not in ("ar", "mixed"):
                continue
            translated_text = SimpleTranslator.translate_arabic_to_english(snap.content)
            item = TranslatedSnapshot(
                original_source_name=snap.source_name,
                original_checksum=snap.checksum,
                original_language=snap.language,
                translated_language="en",
                original_content=snap.content,
                translated_content=translated_text,
                translated_at=datetime.now(timezone.utc).isoformat(),
                method="automatic",
            )
            path = self.db.save_translated(item)
            print(f"  [ok] translated {snap.source_name} → {path.name}")
            self.translated.append(item)
        return self.translated

    def search_entity(
        self,
        entity_name: str,
        entity_country: str = "",
        region: Optional[str] = None,
        source_type: Optional[str] = None,
        language: Optional[str] = None,
    ) -> List[AMLFinding]:
        findings: List[AMLFinding] = []
        now = datetime.now(timezone.utc).isoformat()
        name = entity_name.strip()
        if not name:
            return findings

        snapshots = self.db.load_all_snapshots()
        translated = self.db.load_all_translated()

        for snap in snapshots:
            if region and snap.region != region:
                continue
            if source_type and snap.source_type != source_type:
                continue
            if language and snap.language != language:
                continue
            if name.lower() not in snap.content.lower():
                continue
            sev, conf, ftype, risk, action = severity_and_score(
                snap.source_type, snap.language, name, snap.content
            )
            findings.append(
                AMLFinding(
                    entity_name=name,
                    entity_country=entity_country,
                    finding_type=ftype,
                    severity=sev,
                    source=snap.source_name,
                    source_language=snap.language,
                    source_region=snap.region,
                    confidence_score=conf,
                    evidence=extract_evidence(snap.content, name),
                    snapshot_checksum=snap.checksum,
                    found_at=now,
                    risk_score=risk,
                    recommended_action=action,
                )
            )

        for tr in translated:
            if language and language not in ("en", "ar→en"):
                continue
            if name.lower() not in tr.translated_content.lower():
                continue
            if any(
                f.snapshot_checksum == tr.original_checksum and f.entity_name == name
                for f in findings
            ):
                continue
            orig = next((s for s in snapshots if s.checksum == tr.original_checksum), None)
            stype = orig.source_type if orig else "NEWS"
            sregion = orig.region if orig else "MENA"
            sev, conf, ftype, risk, action = severity_and_score(
                stype, "ar→en", name, tr.translated_content
            )
            findings.append(
                AMLFinding(
                    entity_name=name,
                    entity_country=entity_country,
                    finding_type=ftype,
                    severity=sev,
                    source=tr.original_source_name,
                    source_language="ar→en",
                    source_region=sregion,
                    confidence_score=conf,
                    evidence=extract_evidence(tr.translated_content, name),
                    snapshot_checksum=tr.original_checksum,
                    found_at=now,
                    risk_score=risk,
                    recommended_action=action,
                )
            )

        self.findings.extend(findings)
        return findings

    def search_batch_entities(
        self,
        entities: List[Dict[str, str]],
        **filters: Any,
    ) -> Dict[str, List[AMLFinding]]:
        results: Dict[str, List[AMLFinding]] = {}
        for entity in entities:
            name = entity.get("name", "")
            country = entity.get("country", "")
            results[name] = self.search_entity(name, country, **filters)
        return results
