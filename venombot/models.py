"""Core data models for AML screening."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional


@dataclass
class AMLSource:
    name: str
    url: str
    language: str  # "en" | "ar" | "mixed"
    region: str  # "US" | "MENA" | "GLOBAL"
    source_type: str  # "SANCTIONS" | "PEP" | "NEWS" | "GREY_LIST"
    update_frequency: str = "daily"


@dataclass
class Snapshot:
    source_name: str
    url: str
    language: str
    content: str
    fetched_at: str
    checksum: str
    region: str = ""
    source_type: str = ""

    @staticmethod
    def make(
        source: AMLSource,
        content: str,
        fetched_at: Optional[str] = None,
    ) -> Snapshot:
        truncated = content[:10_240]
        ts = fetched_at or datetime.now(timezone.utc).isoformat()
        checksum = hashlib.sha256(truncated.encode("utf-8")).hexdigest()
        language = source.language if source.language != "mixed" else "mixed"
        return Snapshot(
            source_name=source.name,
            url=source.url,
            language=language,
            content=truncated,
            fetched_at=ts,
            checksum=checksum,
            region=source.region,
            source_type=source.source_type,
        )


@dataclass
class TranslatedSnapshot:
    original_source_name: str
    original_checksum: str
    original_language: str
    translated_language: str
    original_content: str
    translated_content: str
    translated_at: str
    method: str = "automatic"


@dataclass
class AMLFinding:
    entity_name: str
    entity_country: str
    finding_type: str  # POTENTIAL_MATCH | NEGATIVE_NEWS | SANCTIONS_HIT
    severity: str  # CRITICAL | HIGH | MEDIUM | LOW
    source: str
    source_language: str
    source_region: str
    confidence_score: float
    evidence: str
    snapshot_checksum: str
    found_at: str
    risk_score: float = 0.0
    recommended_action: str = "REVIEW"
