"""Subject due-diligence dossier: JSON (canonical), Markdown and HTML.

The dossier is written for the analyst who must sign off: it states what
was screened (and what was not), why each candidate was or was not judged to
be the subject, and where every fact came from. "No match" is always
qualified by coverage — an absent result is not a clearance.
"""

from __future__ import annotations

import html
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from venombot import __version__
from venombot.countries import country_name
from venombot.lists import all_list_sources
from venombot.screening import SCORING, Hit, Subject, overall
from venombot.store import EntityStore

DISCLAIMER = (
    "Automated screening result for compliance review. A listed match is a potential "
    "match until confirmed by a qualified reviewer against identifying documents; "
    "allegations in adverse sources are unproven unless a conviction is cited. "
    "Absence of a match only covers the lists named in the coverage section, as of "
    "the dates shown. Do not use this report as the sole basis for a decision about "
    "an individual (GDPR Art. 22 / LGPD Art. 20)."
)


def _age_days(iso: str) -> Optional[float]:
    if not iso:
        return None
    try:
        ts = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    except ValueError:
        return None
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - ts).total_seconds() / 86400


def coverage(store: EntityStore) -> Dict[str, Any]:
    """What was actually searchable at screening time."""
    registry = all_list_sources()
    rows = []
    for st in store.status():
        src = registry.get(st.key)
        served_at = st.source_updated or (st.fetched_at if st.status == "ok" else "")
        age = _age_days(served_at)
        max_age = src.max_age_days if src else 7
        rows.append({
            "key": st.key,
            "name": st.name,
            "jurisdiction": st.jurisdiction,
            "list_type": st.list_type,
            "license": st.license,
            "status": st.status,
            "entities": st.entity_count,
            "data_as_of": served_at,
            "age_days": round(age, 1) if age is not None else None,
            "stale": bool(age is not None and age > max_age),
            "last_attempt": st.fetched_at,
            "error": st.error,
            "sha256": st.sha256,
            "url": st.url,
        })
    searchable = [r for r in rows if r["entities"] > 0]
    return {
        "lists_searchable": len(searchable),
        "entities_searchable": sum(r["entities"] for r in searchable),
        "lists_failed_last_update": [r["key"] for r in rows if r["status"] == "failed"],
        "lists_stale": [r["key"] for r in searchable if r["stale"]],
        "lists_registered_not_loaded": sorted(set(registry) - {r["key"] for r in rows}),
        "rows": rows,
    }


def build(store: EntityStore, subject: Subject, hits: List[Hit], *, purpose: str = "",
          requested_by: str = "") -> Dict[str, Any]:
    """Assemble the canonical dossier dictionary."""
    cov = coverage(store)
    verdict = overall(hits)
    if verdict["decision"] == "NO_MATCH_IN_SCREENED_LISTS" and (cov["lists_stale"] or cov["lists_failed_last_update"]):
        verdict["reasons"].append(
            "coverage incomplete: some lists are stale or failed to update — see coverage")
    by_class: Dict[str, int] = {}
    for h in hits:
        by_class[h.match_class] = by_class.get(h.match_class, 0) + 1
    return {
        "report_type": "venombot.subject_screening",
        "generated_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "tool_version": __version__,
        "scoring": SCORING,
        "purpose": purpose,
        "requested_by": requested_by,
        "subject": {
            "name": subject.name,
            "aliases": subject.aliases,
            "dob": subject.dob,
            "countries": subject.countries,
            "type": subject.kind,
            "identifiers_provided": len(subject.identifiers),
        },
        "verdict": verdict,
        "summary": {"candidates": len(hits), "by_class": by_class},
        "hits": [h.to_dict() for h in hits],
        "coverage": cov,
        "review": {"reviewer": "", "disposition": "", "notes": "", "reviewed_at": ""},
        "disclaimer": DISCLAIMER,
    }


def _ent_lines(e: Dict[str, Any]) -> List[str]:
    lines = []
    names = [n["value"] for n in e["names"]]
    if len(names) > 1:
        lines.append(f"  - Names/aliases: {'; '.join(names[:12])}{' …' if len(names) > 12 else ''}")
    if e["birth_dates"]:
        lines.append(f"  - Listed DOB: {', '.join(e['birth_dates'][:6])}")
    if e["birth_places"]:
        lines.append(f"  - Place of birth: {'; '.join(e['birth_places'][:3])}")
    if e["nationalities"]:
        lines.append(f"  - Nationality: {', '.join(country_name(c) for c in e['nationalities'])}")
    if e["countries"]:
        lines.append(f"  - Linked countries: {', '.join(country_name(c) for c in e['countries'][:8])}")
    if e["programs"]:
        lines.append(f"  - Programmes: {', '.join(e['programs'][:8])}")
    if e["listed_on"]:
        lines.append(f"  - Listed on: {e['listed_on']}")
    for p in e["positions"][:6]:
        span = " – ".join(x for x in (p["start"], p["end"]) if x)
        lines.append(f"  - Position: {p['title']} {('(' + span + ')') if span else ''} {p['status']}".rstrip())
    if e["identifiers"]:
        ids = [f"{i['type']} {i['value']}" + (f" ({i['country'].upper()})" if i["country"] else "") for i in e["identifiers"][:6]]
        lines.append(f"  - Identifiers: {'; '.join(ids)}")
    if e["linked_to"]:
        lines.append(f"  - Linked to: {'; '.join(e['linked_to'][:8])}")
    if e["remarks"]:
        lines.append(f"  - Remarks: {e['remarks'][:400]}")
    if e["url"]:
        lines.append(f"  - Source record: {e['url']}")
    return lines


def to_markdown(d: Dict[str, Any]) -> str:
    """Render the dossier as Markdown."""
    s, v, cov = d["subject"], d["verdict"], d["coverage"]
    out = [
        f"# Screening dossier — {s['name']}",
        "",
        f"**Decision:** `{v['decision']}` · **Rating:** `{v['rating']}` · "
        f"**Lists searched:** {cov['lists_searchable']} ({cov['entities_searchable']:,} entities)",
        "",
        f"Generated {d['generated_at']} · VenomBot {d['tool_version']} · scoring {d['scoring']['version']}",
    ]
    if d["purpose"] or d["requested_by"]:
        out.append(f"Purpose: {d['purpose'] or '—'} · Requested by: {d['requested_by'] or '—'}")
    out += ["", "## 1. Subject", ""]
    out.append(f"- Name: {s['name']}")
    if s["aliases"]:
        out.append(f"- Aliases screened: {', '.join(s['aliases'])}")
    out.append(f"- DOB: {s['dob'] or 'not provided'}")
    out.append(f"- Countries: {', '.join(country_name(c) for c in s['countries']) or 'not provided'}")
    out.append(f"- Type: {s['type']}")
    if not s["dob"]:
        out.append("- _No DOB provided: matches cannot be confirmed or discounted on date of birth._")
    out += ["", "## 2. Verdict", ""]
    out += [f"- {r}" for r in v["reasons"]]

    live = [h for h in d["hits"] if h["match_class"] != "DISCOUNTED"]
    disc = [h for h in d["hits"] if h["match_class"] == "DISCOUNTED"]
    out += ["", "## 3. Candidate matches", ""]
    if not live:
        out.append("_No candidate above the reporting threshold._")
    else:
        out += ["| # | Class | Severity | Conf. | List | Listed party | Matched name |",
                "|---|---|---|---|---|---|---|"]
        for i, h in enumerate(live, 1):
            out.append(f"| {i} | {h['match_class']} | {h['severity']} | {h['confidence']:.0f} | "
                       f"{h['source']} ({h['list_type']}) | {h['caption']} | {h['matched_name']} |")
        out += ["", "### Match details", ""]
        for i, h in enumerate(live[:40], 1):
            out.append(f"**{i}. {h['caption']}** — {h['source']} `{h['source_id']}` — action `{h['action']}`")
            out += [f"  - Evidence: {e}" for e in h["evidence"]]
            out += [f"  - Conflict: {c}" for c in h["conflicts"]]
            out += _ent_lines(h["entity"])
            out.append("")
        if len(live) > 40:
            out.append(f"_{len(live) - 40} further candidates in the JSON report._")
    if disc:
        out += ["", "## 4. Discounted candidates", ""]
        for h in disc[:30]:
            out.append(f"- {h['caption']} ({h['source']}): {'; '.join(h['conflicts'])}")
    out += ["", "## 5. Coverage", ""]
    out.append(f"- Searchable lists: {cov['lists_searchable']} · entities: {cov['entities_searchable']:,}")
    if cov["lists_failed_last_update"]:
        out.append(f"- Failed last update (older data may be served): {', '.join(cov['lists_failed_last_update'])}")
    if cov["lists_stale"]:
        out.append(f"- Stale: {', '.join(cov['lists_stale'])}")
    out.append(f"- Registered but not loaded: {len(cov['lists_registered_not_loaded'])} "
               "(run `venombot update --group …` to add them)")
    out += ["", "| List | Type | Jurisdiction | Entities | Data as of | Status |", "|---|---|---|---|---|---|"]
    for r in sorted(cov["rows"], key=lambda r: (r["status"] != "ok", r["key"])):
        flag = " ⚠ stale" if r["stale"] else ""
        out.append(f"| {r['key']} | {r['list_type']} | {r['jurisdiction']} | {r['entities']:,} | "
                   f"{(r['data_as_of'] or '—')[:10]} | {r['status']}{flag} |")
    out += ["", "## 6. Review", "", "- Reviewer: ____________  - Disposition: ____________  - Date: ________", "",
            "## 7. Disclaimer", "", d["disclaimer"], ""]
    return "\n".join(out)


def to_html(d: Dict[str, Any]) -> str:
    """Self-contained printable HTML rendering of the Markdown dossier."""
    md = to_markdown(d)
    body: List[str] = []
    in_table = False
    for line in md.splitlines():
        esc = html.escape(line)
        if line.startswith("|"):
            cells = [c.strip() for c in line.strip("|").split("|")]
            if all(set(c) <= {"-"} for c in cells):
                continue
            tag = "th" if not in_table else "td"
            if not in_table:
                body.append("<table>")
                in_table = True
            body.append("<tr>" + "".join(f"<{tag}>{html.escape(c)}</{tag}>" for c in cells) + "</tr>")
            continue
        if in_table:
            body.append("</table>")
            in_table = False
        if line.startswith("# "):
            body.append(f"<h1>{html.escape(line[2:])}</h1>")
        elif line.startswith("## "):
            body.append(f"<h2>{html.escape(line[3:])}</h2>")
        elif line.startswith("### "):
            body.append(f"<h3>{html.escape(line[4:])}</h3>")
        elif line.startswith("  - "):
            body.append(f"<div class='sub'>{esc[4:]}</div>")
        elif line.startswith("- "):
            body.append(f"<div class='li'>{esc[2:]}</div>")
        elif line.strip():
            body.append(f"<p>{esc}</p>")
    if in_table:
        body.append("</table>")
    rating = d["verdict"]["rating"]
    return f"""<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Screening dossier — {html.escape(d['subject']['name'])}</title>
<style>
:root{{--fg:#1d2330;--muted:#5b6475;--line:#d9dde5;--bg:#fff;--accent:#8a1c1c}}
@media (prefers-color-scheme:dark){{:root{{--fg:#e6e8ee;--muted:#9aa3b5;--line:#343a46;--bg:#14171d;--accent:#ff8a80}}}}
body{{font:14px/1.5 system-ui,sans-serif;color:var(--fg);background:var(--bg);max-width:1100px;margin:24px auto;padding:0 16px}}
h1{{font-size:22px;border-bottom:3px solid var(--accent);padding-bottom:6px}} h2{{font-size:17px;margin-top:28px}}
table{{border-collapse:collapse;width:100%;font-size:12.5px;margin:8px 0;display:block;overflow-x:auto}}
td,th{{border:1px solid var(--line);padding:4px 6px;text-align:left;vertical-align:top}}
.li{{margin:2px 0 2px 12px}} .sub{{margin:1px 0 1px 28px;color:var(--muted)}}
.badge{{display:inline-block;padding:2px 8px;border-radius:4px;border:1px solid var(--accent);color:var(--accent);font-weight:600}}
@media print{{body{{margin:0}} h2{{page-break-after:avoid}}}}
</style></head><body>
<div class="badge">Rating: {html.escape(rating)}</div>
{''.join(body)}
</body></html>"""


def write(d: Dict[str, Any], prefix: Path, formats: List[str]) -> List[Path]:
    """Write the dossier in the requested formats (json, md, html)."""
    prefix = Path(prefix)
    prefix.parent.mkdir(parents=True, exist_ok=True)
    out = []
    if "json" in formats:
        p = prefix.with_suffix(".json")
        p.write_text(json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8")
        out.append(p)
    if "md" in formats:
        p = prefix.with_suffix(".md")
        p.write_text(to_markdown(d), encoding="utf-8")
        out.append(p)
    if "html" in formats:
        p = prefix.with_suffix(".html")
        p.write_text(to_html(d), encoding="utf-8")
        out.append(p)
    return out
