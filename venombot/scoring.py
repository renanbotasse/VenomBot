"""Severity, confidence, and evidence extraction for AML findings."""

from __future__ import annotations

from typing import Tuple


def severity_and_score(
    source_type: str,
    language_label: str,
    entity_name: str,
    text: str,
) -> Tuple[str, float, str, float, str]:
    """Return severity, confidence, finding_type, risk_score, recommended_action."""
    lower = text.lower()
    critical_kw = ("terrorism", "terrorist", "sanction", "sdn", "عقوبات", "إرهاب")
    high_kw = (
        "money laundering",
        "fraud",
        "corruption",
        "arrest",
        "investigation",
        "غسل",
        "احتيال",
    )

    is_critical = any(k.lower() in lower for k in critical_kw) or source_type == "SANCTIONS"
    is_high = any(k.lower() in lower for k in high_kw) or source_type in ("PEP", "NEWS")

    if source_type == "SANCTIONS" and entity_name.lower() in lower:
        severity = "CRITICAL"
        finding_type = "SANCTIONS_HIT"
        confidence = 95.0 if language_label == "en" else 85.0
    elif is_critical:
        severity = "HIGH"
        finding_type = "POTENTIAL_MATCH"
        confidence = 85.0 if language_label in ("en", "ar→en") else 75.0
    elif is_high:
        severity = "HIGH" if source_type == "NEWS" else "MEDIUM"
        finding_type = "NEGATIVE_NEWS" if source_type == "NEWS" else "POTENTIAL_MATCH"
        confidence = 70.0 if language_label == "ar→en" else 80.0
    else:
        severity = "MEDIUM"
        finding_type = "POTENTIAL_MATCH"
        confidence = 60.0 if language_label == "ar→en" else 70.0

    risk_map = {"CRITICAL": 95.0, "HIGH": 75.0, "MEDIUM": 45.0, "LOW": 20.0}
    action_map = {
        "CRITICAL": "BLOCK",
        "HIGH": "REVIEW",
        "MEDIUM": "REVIEW",
        "LOW": "APPROVE",
    }
    return severity, confidence, finding_type, risk_map[severity], action_map[severity]


def extract_evidence(text: str, entity_name: str, window: int = 120) -> str:
    idx = text.lower().find(entity_name.lower())
    if idx < 0:
        return text[:200]
    start = max(0, idx - window)
    end = min(len(text), idx + len(entity_name) + window)
    return text[start:end].replace("\n", " ").strip()
