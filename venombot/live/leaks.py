"""ICIJ Offshore Leaks reconciliation (kind ``leak``).

Appearing in the Offshore Leaks database is not wrongdoing: it records
offshore structures, intermediaries and officers from leaked documents. The
evidence says which investigation and node type, nothing more.
"""

from __future__ import annotations

import json
from typing import List, Optional

from venombot.evidence import Evidence
from venombot.fetch import get_text
from venombot.live import LiveProvider
from venombot.live._freecommon import THRESHOLD, best_name, cap, make, parse_json
from venombot.screening import Subject

BASE = "https://offshoreleaks.icij.org"


def icij_reconcile(subject: Subject, key: Optional[str]) -> List[Evidence]:
    body = json.dumps({"queries": {"q0": {"query": subject.name, "limit": 15}}}).encode()
    data = parse_json(get_text(f"{BASE}/api/v1/reconcile", data=body, headers={"Content-Type": "application/json"}))
    out = []
    for r in ((data.get("q0") or {}).get("result") or []):
        name = r.get("name", "")
        matched, score = best_name(subject, [name])
        if score < THRESHOLD:
            continue
        types = [t.get("name") for t in r.get("types") or []]
        desc = r.get("description") or ""
        out.append(make("ICIJ_OFFSHORE_LEAKS_RECONCILE", "leak", f"Offshore Leaks: {name}",
                        url=f"{BASE}/nodes/{r.get('id')}", snippet=desc or f"{name} ({', '.join(types)})",
                        matched=matched, score=score, topics=False,
                        extra={"node_id": r.get("id"), "node_type": types, "reconcile_score": r.get("score"),
                               "description": desc}))
    return cap(out, 15)


PROVIDERS: List[LiveProvider] = [
    LiveProvider(
        key="ICIJ_OFFSHORE_LEAKS_RECONCILE", name="ICIJ Offshore Leaks (reconcile)", kind="leak",
        jurisdiction="INT", search=icij_reconcile, url=f"{BASE}/api/v1/reconcile",
        license="ODbL + CC BY-SA (cite ICIJ)", groups=["leak", "free"], min_interval=2.0,
        notes="Sends the subject name only; presence in a leak is context, not evidence of wrongdoing."),
]
