"""AML findings report generation (JSON + Markdown)."""

from __future__ import annotations

import json
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Tuple

from venombot.models import AMLFinding


class AMLReporter:
    def __init__(self, findings: List[AMLFinding]) -> None:
        self.findings = findings

    def _summary(self) -> Dict[str, Any]:
        by_severity: Dict[str, int] = {}
        by_type: Dict[str, int] = {}
        for finding in self.findings:
            by_severity[finding.severity] = by_severity.get(finding.severity, 0) + 1
            by_type[finding.finding_type] = by_type.get(finding.finding_type, 0) + 1
        return {
            "scan_date": datetime.now(timezone.utc).isoformat(),
            "total_findings": len(self.findings),
            "findings_by_severity": by_severity,
            "findings_by_type": by_type,
        }

    def generate_json_report(self) -> Dict[str, Any]:
        summary = self._summary()
        return {
            **summary,
            "findings": [asdict(f) for f in self.findings],
        }

    def generate_markdown_report(self) -> str:
        summary = self._summary()
        lines = [
            "# AML Findings Report",
            "",
            f"**Scan date:** {summary['scan_date']}",
            f"**Total findings:** {summary['total_findings']}",
            "",
            "## Summary by Severity",
            "",
        ]
        for sev, count in sorted(summary["findings_by_severity"].items()):
            lines.append(f"- **{sev}:** {count}")
        lines.extend(["", "## Findings", ""])

        order = {"CRITICAL": 0, "HIGH": 1, "MEDIUM": 2, "LOW": 3}
        for f in sorted(self.findings, key=lambda x: order.get(x.severity, 9)):
            lines.extend(
                [
                    f"### {f.entity_name} — {f.severity}",
                    "",
                    f"- **Country:** {f.entity_country or 'N/A'}",
                    f"- **Type:** {f.finding_type}",
                    f"- **Source:** {f.source} ({f.source_language}, {f.source_region})",
                    f"- **Confidence:** {f.confidence_score}",
                    f"- **Risk score:** {f.risk_score}",
                    f"- **Action:** {f.recommended_action}",
                    f"- **Evidence:** {f.evidence[:300]}",
                    "",
                ]
            )
        if not self.findings:
            lines.append("_No findings._")
        return "\n".join(lines) + "\n"

    def write(self, output_prefix: Path) -> Tuple[Path, Path]:
        base = str(output_prefix)
        if base == "aml-report":
            json_path = Path("aml-findings.json")
            md_path = Path("aml-findings.md")
        else:
            json_path = Path(f"{base}-findings.json")
            md_path = Path(f"{base}-findings.md")

        json_path.parent.mkdir(parents=True, exist_ok=True)
        json_path.write_text(
            json.dumps(self.generate_json_report(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        md_path.write_text(self.generate_markdown_report(), encoding="utf-8")
        return json_path, md_path
