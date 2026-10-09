"""INTERPOL Red Notices public API, queried per name only.

INTERPOL's terms forbid bulk redistribution, so each result keeps minimal
facts (name, notice URL, nationalities, charge summary) and one name query
is made per subject. The endpoint sits behind Akamai bot protection and often
answers 403 to non-browser clients; that is raised as an error (the framework
records it) rather than reported as "no notices". A red notice is a request
to locate a person, not a conviction, so this is Evidence of kind "enforcement".
"""

from __future__ import annotations

import json
from typing import List, Optional
from urllib.parse import quote, urlencode

from venombot.evidence import Evidence
from venombot.fetch import get_text
from venombot.live import LiveProvider
from venombot.live.media_apis import _dedupe_cap, make_evidence
from venombot.screening import Subject

_BASE = "https://ws-public.interpol.int/notices/v1/red"


def search_interpol(subject: Subject, key: Optional[str] = None) -> List[Evidence]:
    """Red notices whose forename+surname match the subject.

    The API wants surname and forename separately; the last word is used as
    the surname and the rest as forename, then the fuzzy filter checks the
    full name in either order. Organisations are skipped (notices are persons).
    """
    if subject.kind == "org":
        return []
    parts = subject.name.split()
    if len(parts) < 2:
        raise RuntimeError("INTERPOL_RED needs at least a forename and a surname")
    params = {"name": parts[-1], "forename": " ".join(parts[:-1]), "resultPerPage": 20, "page": 1}
    raw = get_text(_BASE + "?" + urlencode(params, quote_via=quote), headers={"Accept": "application/json"})
    try:
        data = json.loads(raw)
    except ValueError as exc:
        raise RuntimeError(f"INTERPOL_RED: non-JSON response (bot protection?): {raw[:100]}") from exc
    out: List[Evidence] = []
    for n in (data.get("_embedded") or {}).get("notices", []) or []:
        full = f"{n.get('forename', '')} {n.get('name', '')}".strip()
        link = ((n.get("_links") or {}).get("self") or {}).get("href", "")
        eid = str(n.get("entity_id", ""))
        url = link or ("https://www.interpol.int/en/How-we-work/Notices/Red-Notices/View-Red-Notices#"
                       + eid.replace("/", "-"))
        ev = make_evidence(
            subject, "INTERPOL_RED", "enforcement", full, text="INTERPOL red notice", url=url, language="en",
            extra={"entity_id": eid, "nationalities": n.get("nationalities") or [],
                   "date_of_birth": n.get("date_of_birth", ""),
                   "charges": [str(c) for c in (n.get("charges") or [])][:3]},
        )
        if ev:
            out.append(ev)
    return _dedupe_cap(out)


PROVIDERS: List[LiveProvider] = [
    LiveProvider(
        key="INTERPOL_RED", name="INTERPOL Red Notices (public API)", kind="enforcement", jurisdiction="UN",
        search=search_interpol, url=_BASE, license="INTERPOL website terms: public extracts only, no bulk redistribution",
        groups=["enforcement", "wanted", "free"], min_interval=3.0,
        notes="Keyless but Akamai often answers 403 (observed 2026-10-09 from the dev network); a 403 is raised "
              "as an error. One name query per subject. Fallback: the OpenSanctions INTERPOL mirror list.",
    )
]
