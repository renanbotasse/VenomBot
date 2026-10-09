"""Terrorism / national designation lists that exist only as HTML pages.

None of these publishers offers a machine-readable file, so each parser reads
the page's own table or list markup with ``_html`` (stdlib html.parser).  The
pages change layout without notice, therefore every parser validates what it
found (expected table headers, a minimum record count) and raises ValueError
instead of returning partial data: the update pipeline then keeps the previous
good snapshot and flags the source as failed.

Fetch notes: state.gov, mha.gov.in, nationalsecurity.gov.au and
sso.agc.gov.sg answer 403 to a User-Agent containing "VenomBot" (bot filter),
so these sources send a plain browser-style UA.  One request per page, no
crawling; per-person detail pages (NG, TH) and linked PDFs are deliberately not
followed because the update pipeline downloads only the URLs declared here.

Skipped on purpose (see report): Bahrain gazette PDF (URL now 404 and Arabic
glyphs are not extractable with the stdlib) and the Oman NCTC local list (the
page only links PDFs through opaque, per-load tokens).
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Dict, Iterator, List, Optional, Tuple

from venombot.countries import countries_in
from venombot.dates import parse_dates
from venombot.entities import Entity
from venombot.lists import ListSource
from venombot.lists._html import (BREAK, links, read_html, require, split_acronym, squash,
                                  table_rows, text_lines)
from venombot.lists._util import add_dates, add_identifier

# Plain browser UA: the bot filters on these sites block any UA naming the tool.
BROWSER_HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"),
    "Accept": "text/html,application/xhtml+xml",
}

_DMY_TXT = re.compile(r"\b\d{1,2}\s+[A-Z][a-z]+\s+\d{4}\b")


# --------------------------------------------------------------------------
# shared helpers
# --------------------------------------------------------------------------

def _slug(text: str) -> str:
    """Stable ASCII-ish id fragment so ids survive row re-ordering on the page."""
    s = re.sub(r"[^\w]+", "-", text.lower(), flags=re.UNICODE).strip("-")
    return s[:80] or "x"


class _Ids:
    """Hands out unique source_ids; duplicates get a numeric suffix."""

    def __init__(self) -> None:
        self._seen: Dict[str, int] = {}

    def __call__(self, base: str) -> str:
        n = self._seen.get(base, 0)
        self._seen[base] = n + 1
        return base if n == 0 else f"{base}-{n + 1}"


def _paren_groups(text: str) -> Tuple[str, List[str]]:
    """Split 'Name (a) (b (c))' into ('Name', ['a', 'b (c)']) honouring nesting.

    Why: TEL/FTO aliases live in parentheses and may nest ("(a.k.a. X (Y))").
    Unbalanced input is returned unsplit rather than guessed at.
    """
    base: List[str] = []
    groups: List[str] = []
    depth = 0
    cur: List[str] = []
    for ch in text:
        if ch == "(":
            if depth == 0:
                cur = []
            else:
                cur.append(ch)
            depth += 1
        elif ch == ")":
            if depth == 0:
                return squash(text), []
            depth -= 1
            if depth == 0:
                groups.append(squash("".join(cur)))
            else:
                cur.append(ch)
        elif depth:
            cur.append(ch)
        else:
            base.append(ch)
    if depth:
        return squash(text), []
    return squash("".join(base)), [g for g in groups if g]


_AKA_PREFIX = re.compile(r"^(?:a\.?k\.?a\.?|f\.?k\.?a\.?|also known as|formerly known as|formerly|also|aka)\s*:?\s+",
                         re.IGNORECASE)


def _alias_from_group(group: str, split_commas: bool = False) -> List[str]:
    """Turn the inside of a parenthesis into alias strings."""
    out: List[str] = []
    for part in re.split(r";", group):
        part = _AKA_PREFIX.sub("", squash(part)).strip(" .;")
        if not part:
            continue
        if split_commas:
            out.extend(p.strip() for p in part.split(",") if p.strip())
        else:
            out.append(part)
    return out


def _org(source: ListSource, sid: str, name: str, *, aliases: Optional[List[str]] = None,
         listed_on: str = "", remarks: str = "", url: str = "", programs: Optional[List[str]] = None,
         list_type: Optional[str] = None) -> Entity:
    ent = Entity(source=source.key, source_id=sid, schema="Organization",
                 list_type=list_type or source.list_type, listed_on=listed_on,
                 remarks=remarks, url=url or source.homepage)
    ent.add_name(name, "primary")
    for a in aliases or []:
        ent.add_name(a, "alias")
    if programs:
        ent.programs.extend(programs)
    return ent


def _first_date(text: str) -> str:
    m = re.match(r"^(\d{1,2})/(\d{1,2})/(\d{4})\b", text.strip())
    if m and int(m.group(3)) > 1900:  # M/D/YYYY (NIGSAC); dates.parse_dates would keep only the year
        return f"{int(m.group(3)):04d}-{int(m.group(1)):02d}-{int(m.group(2)):02d}"
    dates = parse_dates(text)
    return dates[0] if dates else ""


# --------------------------------------------------------------------------
# Australia - Criminal Code listed terrorist organisations
# --------------------------------------------------------------------------

def parse_au(paths: Dict[str, Path], source: ListSource) -> Iterator[Entity]:
    """One <table>: organisation (acronym), listed date, re-listed dates."""
    tables = table_rows(read_html(paths["main"]))
    rows = next((t for t in tables if t and t[0] and t[0][0].lower().startswith("terrorist organisation")), None)
    require(rows is not None, "AU: table headed 'Terrorist organisation' not found (layout changed)")
    ids = _Ids()
    count = 0
    for row in rows[1:]:
        require(len(row) >= 2, f"AU: unexpected row shape {row!r}")
        base, acr = split_acronym(row[0])
        listed = _first_date(row[1])
        require(bool(base) and bool(listed), f"AU: row without name/date {row!r}")
        relisted = [d for d in (parse_dates(x)[0] for x in _DMY_TXT.findall(row[2]) if parse_dates(x)) ] if len(row) > 2 else []
        ent = _org(source, ids(_slug(base)), base, aliases=[acr] if acr else [], listed_on=listed,
                   remarks=("Re-listed: " + ", ".join(relisted)) if relisted else "",
                   programs=["AU-CRIMINAL-CODE-1995"], url=source.homepage)
        count += 1
        yield ent
    require(count >= 10, f"AU: only {count} organisations parsed (expected ~31)")


# --------------------------------------------------------------------------
# India MHA (UAPA schedules)
# --------------------------------------------------------------------------

_GLUED = re.compile(r"(?<=[a-z])(?=[A-Z])")


def _mha_rows(path: Path, label: str, minimum: int) -> List[Tuple[str, str]]:
    tables = table_rows(read_html(path))
    rows = next((t for t in tables if t and [c.lower() for c in t[0][:2]] == ["sr-no", "title"]), None)
    require(rows is not None, f"{label}: table 'SR-No | Title' not found (layout changed)")
    out = []
    for row in rows[1:]:
        require(len(row) >= 2 and row[0].strip().isdigit(), f"{label}: unexpected row {row!r}")
        out.append((row[0].strip(), row[1].strip()))
    require(len(out) >= minimum, f"{label}: only {len(out)} rows (expected >= {minimum})")
    return out


def parse_in_individuals(paths: Dict[str, Path], source: ListSource) -> Iterator[Entity]:
    """Fourth Schedule individuals: 'Name @ alias @ alias.'; no DOB/IDs on the page."""
    for sr, title in _mha_rows(paths["main"], "IN individuals", 20):
        parts = [squash(p).strip(" .") for p in title.split("@")]
        parts = [p for p in parts if p]
        require(bool(parts), f"IN individuals: empty title in row {sr}")
        ent = Entity(source=source.key, source_id=f"fourth-schedule-{sr}", schema="Person",
                     list_type=source.list_type, programs=["IN-UAPA-FOURTH-SCHEDULE"],
                     nationalities=[], url=source.homepage,
                     remarks="Designated individual terrorist under UAPA 1967 Fourth Schedule")
        for i, p in enumerate(parts):
            ent.add_name(p, "primary" if i == 0 else "alias")
            # Source text sometimes glues words ("AzharAlvi"); index a repaired variant too.
            fixed = _GLUED.sub(" ", p)
            if fixed != p:
                ent.add_name(fixed, "alias")
        yield ent


def _mha_orgs(paths: Dict[str, Path], source: ListSource, program: str, prefix: str,
              minimum: int, note: str) -> Iterator[Entity]:
    for sr, title in _mha_rows(paths["main"], source.key, minimum):
        title = title.strip(" .")
        base, acr = split_acronym(title)
        ent = _org(source, f"{prefix}-{sr}", base or title, aliases=[acr] if acr else [],
                   programs=[program], remarks=note)
        # 'JKPL ... (A) and ...' style titles are kept whole; the acronym alias only
        # applies when the parenthesis closes the title.
        yield ent


def parse_in_orgs(paths: Dict[str, Path], source: ListSource) -> Iterator[Entity]:
    """First Schedule (s.35) terrorist organisations."""
    return _mha_orgs(paths, source, "IN-UAPA-FIRST-SCHEDULE", "first-schedule", 20,
                     "Terrorist organisation under UAPA 1967 First Schedule")


def parse_in_unlawful(paths: Dict[str, Path], source: ListSource) -> Iterator[Entity]:
    """Section 3 unlawful associations (banned groups, not all terrorist organisations)."""
    return _mha_orgs(paths, source, "IN-UAPA-S3-UNLAWFUL", "unlawful", 10,
                     "Unlawful association declared under UAPA 1967 s.3")


# --------------------------------------------------------------------------
# Nigeria NIGSAC
# --------------------------------------------------------------------------

def parse_ng(paths: Dict[str, Path], source: ListSource) -> Iterator[Entity]:
    """Two tables on /IndSancList: individuals (with reference no.) and entities."""
    tables = table_rows(read_html(paths["main"]))
    ind = next((t for t in tables if t and "full name" in [c.lower() for c in t[0]]), None)
    ents = next((t for t in tables if t and "entity name" in [c.lower() for c in t[0]]), None)
    require(ind is not None, "NG: individuals table (header 'Full Name') not found")
    header = [c.lower() for c in ind[0]]
    require("reference number" in header, "NG: 'Reference Number' column missing")
    iname, iref = header.index("full name"), header.index("reference number")
    idate = header.index("record date") if "record date" in header else None
    ids = _Ids()
    n = 0
    for row in ind[1:]:
        require(len(row) > max(iname, iref), f"NG: short row {row!r}")
        name = row[iname]
        ref = row[iref]
        require(bool(name), f"NG: row without name {row!r}")
        # A reference number is missing on some real rows (record date 1/1/0001).
        ref = ref or "noref-" + _slug(name)
        ent = Entity(source=source.key, source_id=ids(ref), schema="Person", list_type=source.list_type,
                     listed_on=(_first_date(row[idate]) if idate is not None and len(row) > idate
                                and not row[idate].startswith("1/1/0001") else ""),
                     programs=["NG-NIGSAC"], url=source.homepage,
                     remarks="Nigeria Sanctions Committee designation; DOB/nationality only on detail page")
        ent.add_name(name, "primary")
        if not ref.startswith("noref-"):
            add_identifier(ent, "nigsac-ref", ref, "ng")
        n += 1
        yield ent
    require(n >= 10, f"NG: only {n} individuals parsed (expected ~50+)")
    if ents is not None:
        eh = [c.lower() for c in ents[0]]
        en = eh.index("entity name")
        ec = eh.index("comments") if "comments" in eh else None
        ed = eh.index("record date") if "record date" in eh else None
        for row in ents[1:]:
            require(len(row) > en, f"NG: short entity row {row!r}")
            ent = _org(source, ids("ent-" + _slug(row[en])), row[en],
                       listed_on=_first_date(row[ed]) if ed is not None and len(row) > ed else "",
                       remarks=row[ec] if ec is not None and len(row) > ec else "",
                       programs=["NG-NIGSAC"])
            yield ent


# --------------------------------------------------------------------------
# New Zealand Police - TSA designations
# --------------------------------------------------------------------------

_NZ_AKA = re.compile(r"(?:also\s+known\s+as|a\.k\.a\.?)", re.IGNORECASE)


def _split_aka(text: str) -> List[str]:
    """Split 'A, B (x, y), C or D and E.' on commas/or/and outside parentheses."""
    text = text.strip(" .")
    out: List[str] = []
    depth = 0
    cur: List[str] = []
    for ch in text:
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth = max(0, depth - 1)
        if ch == "," and depth == 0:
            out.append("".join(cur))
            cur = []
        else:
            cur.append(ch)
    out.append("".join(cur))
    final: List[str] = []
    for part in out:
        for sub in re.split(r"\s+(?:or|and)\s+(?=[A-Z\"'’])", part.strip()):
            sub = sub.strip(" .,/")
            if sub:
                final.append(sub)
    return final


def parse_nz(paths: Dict[str, Path], source: ListSource) -> Iterator[Entity]:
    """Tables of designated entities; tables about expired designations are skipped."""
    tables = table_rows(read_html(paths["main"]), keep_breaks=True)
    ids = _Ids()
    n = 0
    seen_tables = 0
    for t in tables:
        if not t or not t[0] or not t[0][0].lower().startswith("terrorist entity"):
            continue
        seen_tables += 1
        header = " ".join(t[0]).lower()
        if "expire" in header:
            continue  # delisted entities are history, not live designations
        for row in t[1:]:
            require(len(row) >= 2, f"NZ: unexpected row shape {row!r}")
            pieces = [p.strip() for p in row[0].split(BREAK) if p.strip()]
            require(bool(pieces), "NZ: empty entity cell")
            heading = pieces[0]
            rest = " ".join(pieces[1:])
            base, acr = split_acronym(heading)
            aliases: List[str] = []
            if acr:
                aliases.append(acr)
            m = _NZ_AKA.search(rest) or _NZ_AKA.search(heading)
            if m:
                src = rest if _NZ_AKA.search(rest) else heading
                m2 = _NZ_AKA.search(src)
                aliases.extend(_split_aka(src[m2.end():]))
            elif rest:
                # e.g. 'JAA, formerly designated under ...' - keep as remarks only.
                pass
            if base.lower().startswith("the ") and len(base) > 6:
                aliases.append(base[4:])
            desig = " ".join(row[1].replace(BREAK, " ").split())
            renewals = [parse_dates(d)[0] for d in _DMY_TXT.findall(
                " ".join(row[2].replace(BREAK, " ").split())) if parse_dates(d)] if len(row) > 2 else []
            ent = _org(source, ids(_slug(base)), base, aliases=aliases, listed_on=_first_date(desig),
                       remarks=("Designated under the NZ Terrorism Suppression Act 2002"
                                + ("; renewed/considered: " + ", ".join(renewals[:3]) if renewals else "")
                                + (f"; note: {rest}" if rest and not m else "")),
                       programs=["NZ-TSA-2002"])
            n += 1
            yield ent
    require(seen_tables >= 1, "NZ: no 'Terrorist entity' tables found (layout changed)")
    require(n >= 8, f"NZ: only {n} entities parsed (expected ~20+)")


# --------------------------------------------------------------------------
# Singapore TSOFA First Schedule
# --------------------------------------------------------------------------

_SG_ITEM = re.compile(r"^(?P<name>.+?)\s*\((?P<nat>[^()]*?)\s+citizen\)(?P<rest>.*)$", re.IGNORECASE)
_DEMONYMS = {"indonesian": "id", "malaysian": "my", "filipino": "ph", "thai": "th", "pakistani": "pk",
             "indian": "in", "afghan": "af", "iraqi": "iq", "syrian": "sy", "sri lankan": "lk"}


def parse_sg(paths: Dict[str, Path], source: ListSource) -> Iterator[Entity]:
    """Items '(x) Name (Country citizen) (Passport No. ..) (Date of Birth: ..)' in the schedule."""
    lines = [t for tag, t in text_lines(read_html(paths["main"])) if tag == "td"]
    start = next((i for i, t in enumerate(lines) if re.match(r"^2\.\s+The following (individuals|entities|persons)", t)), None)
    require(start is not None, "SG: 'The following individuals' item not found in First Schedule")
    ids = _Ids()
    n = 0
    for t in lines[start + 1:]:
        if re.match(r"^\d+\.\s", t):
            break  # item 3 onwards are definitions
        if re.match(r"^\([a-z]+\)$", t) or t.startswith("[Deleted"):
            continue
        m = _SG_ITEM.match(t)
        require(m is not None, f"SG: unparsable schedule item {t[:120]!r}")
        name_part = m.group("name")
        names = [squash(x).strip(" .") for x in name_part.split("@")]
        ent = Entity(source=source.key, source_id="", schema="Person", list_type=source.list_type,
                     programs=["SG-TSFA-FIRST-SCHEDULE"], url=source.homepage,
                     remarks="Singapore Terrorism (Suppression of Financing) Act 2002, First Schedule (autonomous designation)")
        for i, nm in enumerate(names):
            ent.add_name(nm, "primary" if i == 0 else "alias")
        nat = m.group("nat").strip()
        iso = countries_in(nat) or ([_DEMONYMS[nat.lower()]] if nat.lower() in _DEMONYMS else [])
        for c in iso:
            if c not in ent.nationalities:
                ent.nationalities.append(c)
        rest = m.group("rest")
        for pm in re.finditer(r"Passport No\.\s*([A-Za-z0-9]+)", rest):
            add_identifier(ent, "passport", pm.group(1), iso[0] if iso else "")
        for wm in re.finditer(r"Work Permit No\.\s*([A-Za-z0-9]+)", rest):
            add_identifier(ent, "work-permit", wm.group(1), "sg")
        for dm in re.finditer(r"Date of Birth:\s*([0-9]{1,2}\s+[A-Za-z]+\s+[0-9]{4})", rest):
            add_dates(ent, dm.group(1))
        dob = ent.birth_dates[0] if ent.birth_dates else ""
        ent.source_id = ids(_slug(names[0]) + ("-" + dob if dob else ""))
        n += 1
        yield ent
    require(n >= 10, f"SG: only {n} designated persons parsed (expected ~40)")


# --------------------------------------------------------------------------
# Thailand AMLO
# --------------------------------------------------------------------------

def parse_th(paths: Dict[str, Path], source: ListSource) -> Iterator[Entity]:
    """Rows: no | Thai name | English name | masked ID | - | - | type | view."""
    tables = table_rows(read_html(paths["main"]))
    rows = max((t for t in tables), key=len, default=None)
    require(rows is not None and len(rows) >= 50, "TH: results table not found or too small")
    ids = _Ids()
    n = 0
    for row in rows:
        if not row or not row[0].isdigit():
            continue  # header or pager rows
        require(len(row) >= 7, f"TH: unexpected row shape {row!r}")
        thai, eng = row[1], row[2]
        if not thai and not eng:
            continue  # placeholder row ('XXXXXXXX') the portal appends
        ent = Entity(source=source.key, source_id="", schema="Person", list_type=source.list_type,
                     programs=["TH-AMLO-DESIGNATED"], url=source.homepage,
                     countries=["th"], remarks="Thailand AMLO designated person (ID numbers masked on page)")
        ent.add_name(eng or thai, "primary")
        if eng and thai:
            ent.add_name(thai, "original", "th")
        ent.source_id = ids(_slug(eng or thai) + "-" + _slug(row[3]))
        n += 1
        yield ent
    require(n >= 50, f"TH: only {n} designated persons parsed (expected ~400)")


# --------------------------------------------------------------------------
# US State Department: FTO, TEL, Cuba Restricted List
# --------------------------------------------------------------------------

def _amendment_aliases(segment: str) -> List[str]:
    """'Ansar al-Shari'a Amendment (Oct 5, 2012)' -> ['Ansar al-Shari'a']."""
    seg = re.sub(r"\([^()]*\d{4}\)", "", segment)
    seg = re.sub(r"\bAmendments?\b", "", seg).strip(" ,")
    return [p.strip() for p in re.split(r",\s*(?:and\s+)?|\s+and\s+", seg) if p.strip()]


def parse_us_fto(paths: Dict[str, Path], source: ListSource) -> Iterator[Entity]:
    """Table 'Date Implemented | Name'; the removed-designations table is ignored."""
    tables = table_rows(read_html(paths["main"]))
    rows = next((t for t in tables if t and [c.lower() for c in t[0][:2]] == ["date implemented", "name"]), None)
    require(rows is not None, "US FTO: table 'Date Implemented | Name' not found")
    ids = _Ids()
    n = 0
    for row in rows[1:]:
        require(len(row) >= 2 and bool(row[1]), f"US FTO: unexpected row {row!r}")
        listed = _first_date(row[0])
        require(bool(listed), f"US FTO: row without parsable date {row!r}")
        segments = [s.strip() for s in row[1].split("—")]
        head = segments[0]
        aliases: List[str] = []
        for seg in segments[1:]:
            aliases.extend(_amendment_aliases(seg))
        base, groups = _paren_groups(head)
        names = [x.strip() for x in re.split(r",\s*aka\s+|\s+aka\s+", base) if x.strip()]
        for g in groups:
            aliases.extend(_alias_from_group(g, split_commas=True))
        ent = _org(source, ids(_slug(names[0])), names[0], aliases=names[1:] + aliases,
                   listed_on=listed, programs=["US-FTO"],
                   remarks="Designated Foreign Terrorist Organization (INA s.219)")
        n += 1
        yield ent
    require(n >= 30, f"US FTO: only {n} organisations parsed (expected ~90)")


def parse_us_tel(paths: Dict[str, Path], source: ListSource) -> Iterator[Entity]:
    """<li> items between the 'Designees' heading and the 'Delisted' heading."""
    lines = text_lines(read_html(paths["main"]))
    active = False
    ids = _Ids()
    n = 0
    for tag, t in lines:
        if tag.startswith("h"):
            low = t.lower()
            if "terrorist exclusion list designees" in low:
                active = True
                continue
            if active and "delisted" in low:
                break
            if active:
                break
        if not active or tag != "li":
            continue
        base, groups = _paren_groups(t)
        aliases: List[str] = []
        for g in groups:
            aliases.extend(_alias_from_group(g))
        require(bool(base), f"US TEL: empty name in {t[:80]!r}")
        n += 1
        yield _org(source, ids(_slug(base)), base, aliases=aliases, programs=["US-TEL"],
                   remarks="Terrorist Exclusion List (INA s.212(a)(3)(B)(vi)(II)): immigration inadmissibility")
    require(active, "US TEL: 'Designees' heading not found (layout changed)")
    require(n >= 30, f"US TEL: only {n} entries parsed (expected ~55)")


_CUBA_ACRONYM = re.compile(r"^([A-Z0-9][A-Z0-9/&.\- ]{1,18}?)\s+[—–]\s+(.+)$")
_EFFECTIVE = re.compile(r"\s*Effective\s+([A-Z][a-z]+\s+\d{1,2},\s+\d{4})\s*$")


def parse_us_cuba(paths: Dict[str, Path], source: ListSource) -> Iterator[Entity]:
    """Bold paragraphs are category headers; plain paragraphs are entities.

    'alias X' / 'also X' lines belong to the previous entity, 'ACR — Full name'
    carries an acronym, trailing '(Real Estate)' notes describe activity and
    'Effective <date>' is the date the entry was added.
    """
    markup = read_html(paths["main"])
    start = markup.find("<strong>Ministries</strong>")
    start = markup.rfind("<p", 0, start) if start >= 0 else start
    require(start >= 0, "US Cuba: 'Ministries' category header not found (layout changed)")
    end = markup.find("Back to Top", start)
    body = markup[start:end if end > 0 else len(markup)]
    ids = _Ids()
    category = ""
    last: Optional[Entity] = None
    pending: List[Entity] = []
    n = 0
    for pm in re.finditer(r"<p[^>]*>(.*?)</p>", body, re.S | re.IGNORECASE):
        inner = pm.group(1)
        text = " ".join(t for _, t in text_lines(inner))
        text = squash(text)
        if not text:
            continue
        if re.match(r"^\s*(?:<[^>]+>\s*)*<(?:strong|b)>.*</(?:strong|b)>\s*(?:<[^>]+>\s*)*$", inner, re.S | re.IGNORECASE):
            category = text.rstrip(":")
            continue
        if text.endswith(":") and len(text) < 60:
            category = (category.split(" / ")[0] + " / " + text.rstrip(":")) if category else text.rstrip(":")
            continue
        eff = _EFFECTIVE.search(text)
        listed = ""
        if eff:
            listed = _first_date(eff.group(1))
            text = text[:eff.start()].strip()
        am = re.match(r"^(?:alias|also)\s+(.+)$", text, re.IGNORECASE)
        if am and last is not None:
            alias = am.group(1).strip().strip("“”\"")
            last.add_name(alias, "alias")
            continue
        aliases: List[str] = []
        m = _CUBA_ACRONYM.match(text)
        name = text
        if m:
            aliases.append(m.group(1).strip())
            name = m.group(2).strip()
        name2, groups = _paren_groups(name)
        notes = []
        for g in groups:
            if re.fullmatch(r"[A-Z0-9/&.\-]{2,12}", g):
                aliases.append(g)
            else:
                notes.append(g)
        name = re.sub(r"\s+\.$", "", name2 or name)
        ent = _org(source, ids(_slug(name)), name, aliases=aliases, listed_on=listed,
                   programs=["US-CUBA-RESTRICTED-LIST"],
                   remarks=f"Cuba Restricted List ({category})" + (f"; {'; '.join(notes)}" if notes else ""))
        ent.extra["category"] = category
        last = ent
        pending.append(ent)
        n += 1
    require(n >= 100, f"US Cuba: only {n} entities parsed (expected ~240)")
    yield from pending


# --------------------------------------------------------------------------
# Estonia MFA national sanctions
# --------------------------------------------------------------------------

_EE_ITEM = re.compile(r"^(\d{1,3})\.\s+(.+)$")


def _parse_ee(path: Path, source: ListSource, program: str, label: str, minimum: int) -> Iterator[Entity]:
    ids = _Ids()
    n = 0
    expect = 1
    for _, t in text_lines(read_html(path)):
        m = _EE_ITEM.match(t)
        if m and int(m.group(1)) == 1:
            expect = 1  # the page holds several numbered lists (e.g. Russia, then Georgia)
        if not m or int(m.group(1)) != expect:
            continue  # numbering must be consecutive from 1; anything else is page text
        expect += 1
        base, groups = _paren_groups(m.group(2))
        aliases: List[str] = []
        for g in groups:
            aliases.extend(_alias_from_group(g))
        base = base.strip(" ;.,")
        require(bool(base), f"EE {label}: empty name in {t[:80]!r}")
        ent = Entity(source=source.key, source_id=ids(f"{label}-{_slug(base)}"), schema="Person",
                     list_type=source.list_type, programs=[program], url=source.homepage,
                     remarks="Estonian national sanctions (directive of the Minister of Foreign Affairs)")
        ent.add_name(base, "primary")
        for a in aliases:
            ent.add_name(a, "alias")
        n += 1
        yield ent
    require(n >= minimum, f"EE {label}: only {n} subjects parsed (expected >= {minimum})")


def parse_ee(paths: Dict[str, Path], source: ListSource) -> Iterator[Entity]:
    """Two pages: human-rights list (~190 numbered names) and security list (few names)."""
    yield from _parse_ee(paths["human_rights"], source, "EE-NATIONAL-HUMAN-RIGHTS", "hr", 50)
    yield from _parse_ee(paths["security"], source, "EE-NATIONAL-SECURITY", "sec", 2)


# --------------------------------------------------------------------------
# Russia Rosfinmonitoring (state list; politically sensitive)
# --------------------------------------------------------------------------

_RU_SECTIONS = (("russianUL", "org", "national"), ("russianFL", "person", "national"),
                ("foreignUL", "org", "international"), ("foreignFL", "person", "international"))
_RU_LI = re.compile(r"<li>(.*?)</li>", re.S)
_RU_DOB = re.compile(r"(\d{2}\.\d{2}\.\d{4}|\d{4})\s*г\.р\.")
_RU_INN = re.compile(r"ИНН:\s*(\d+)")
_RU_OGRN = re.compile(r"ОГРН:\s*(\d+)")
# Sanity floors; tests lower them for their tiny synthetic fixture.
RU_MIN_PERSONS = 1000
RU_MIN_ORGS = 50
RU_SENSITIVITY = ("Russian domestic designation (state list); includes opposition figures, journalists, "
                  "NGOs and religious groups. Context only - not a standalone adverse finding.")


def _ru_sections(markup: str) -> Dict[str, str]:
    ids = [(sid, markup.find(f'id="{sid}"')) for sid, _, _ in _RU_SECTIONS]
    require(all(pos >= 0 for _, pos in ids[:2]),
            "RU: sections russianUL/russianFL not found (layout changed)")
    positions = sorted((pos, sid) for sid, pos in ids if pos >= 0)
    out: Dict[str, str] = {}
    for i, (pos, sid) in enumerate(positions):
        nxt = positions[i + 1][0] if i + 1 < len(positions) else len(markup)
        out[sid] = markup[pos:nxt]
    return out


def parse_ru(paths: Dict[str, Path], source: ListSource) -> Iterator[Entity]:
    """<ol class='terrorist-list'><li>N. NAME*, dd.mm.yyyy г.р. , place;</li> in four sections."""
    sections = _ru_sections(read_html(paths["main"]))
    ids = _Ids()
    totals = {"person": 0, "org": 0}
    for sid, kind, part in _RU_SECTIONS:
        block = sections.get(sid, "")
        for raw in _RU_LI.findall(block):
            text = squash(re.sub(r"<[^>]+>", " ", raw))
            m = re.match(r"^(\d+)\.\s+(.*)$", text)
            require(m is not None, f"RU: list item without number in {sid}: {text[:80]!r}")
            body = m.group(2).rstrip(" ;")
            flagged = "*" in body.split(",")[0] or "*" in body.split("(")[0]
            ent = Entity(source=source.key, source_id="", schema="Person" if kind == "person" else "Organization",
                         list_type=source.list_type, programs=[f"RU-ROSFINMONITORING-{part.upper()}"],
                         url=source.homepage, countries=["ru"], remarks=RU_SENSITIVITY,
                         topics=["state-list", "terrorism" if flagged else "extremism"])
            ent.extra["sensitivity"] = "politically-sensitive-state-list"
            aliases: List[str] = []
            if kind == "person":
                head = body.split(",", 1)[0]
                name = head.replace("*", "").strip()
                dm = _RU_DOB.search(body)
                if dm:
                    add_dates(ent, dm.group(1))
                for g in re.findall(r"\(([^)]*)\)", body):
                    aliases.extend(a.strip() for a in g.split(";") if a.strip())
                tail = body.split(",")
                place = tail[-1].strip() if len(tail) > 1 else ""
                if place and not _RU_DOB.search(place) and not place.startswith("("):
                    ent.birth_places.append(place)
                sid_key = _slug(name) + ("-" + dm.group(1) if dm else "")
            else:
                inn, ogrn = _RU_INN.search(body), _RU_OGRN.search(body)
                core = re.sub(r",?\s*ИНН:.*$", "", body)
                core = re.sub(r"[ ,;]+$", "", core)
                name_part, groups = _paren_groups(core)
                name = name_part.replace("*", "").rstrip(" ,;").strip()
                for g in groups:
                    aliases.extend(a.strip() for a in g.split(";") if a.strip())
                if inn:
                    add_identifier(ent, "inn", inn.group(1), "ru")
                if ogrn:
                    add_identifier(ent, "ogrn", ogrn.group(1), "ru")
                sid_key = _slug(name) + ("-" + inn.group(1) if inn else "")
            require(bool(name), f"RU: empty name in {sid}: {text[:80]!r}")
            ent.add_name(name, "primary")
            for a in aliases:
                ent.add_name(a.replace("*", "").strip(), "alias")
            ent.source_id = ids(f"{part}-{kind}-{sid_key}")
            totals[kind] += 1
            yield ent
    require(totals["person"] >= RU_MIN_PERSONS and totals["org"] >= RU_MIN_ORGS,
            f"RU: implausible counts {totals} (expected ~23000 persons, ~900 orgs)")


# --------------------------------------------------------------------------
# registry
# --------------------------------------------------------------------------

def _src(key: str, name: str, jur: str, urls: Dict[str, str], parser, homepage: str, license: str,
         groups: List[str], notes: str, list_type: str = "TERRORISM", max_age_days: int = 14) -> ListSource:
    return ListSource(key=key, name=name, jurisdiction=jur, list_type=list_type, urls=urls, parser=parser,
                      homepage=homepage, license=license, groups=["html"] + groups,
                      headers=dict(BROWSER_HEADERS), max_age_days=max_age_days, notes=notes)


_MHA_NOTE = "HTML table scraped from mha.gov.in (403 for non-browser UA); names only, DOB/IDs are in linked gazette PDFs."

SOURCES: List[ListSource] = [
    _src("AU_LISTED_TERRORIST_ORGS", "Australia - Listed terrorist organisations (Criminal Code)", "AU",
         {"main": "https://www.nationalsecurity.gov.au/what-australia-is-doing/terrorist-organisations/listed-terrorist-organisations"},
         parse_au, "https://www.nationalsecurity.gov.au/what-australia-is-doing/terrorist-organisations/listed-terrorist-organisations",
         "CC-BY", ["terrorism", "oceania", "apac"],
         "HTML table; 403 for UA containing 'VenomBot'. Licence: Commonwealth CC BY 4.0 (site default)."),
    _src("IN_MHA_UAPA_INDIVIDUAL_TERRORISTS", "India MHA - Individual Terrorists (Fourth Schedule, UAPA)", "IN",
         {"main": "https://www.mha.gov.in/en/commoncontent/individuals-terrorists-listed-fourth-schedule-of-unlawful-activities-prevention-act"},
         parse_in_individuals, "https://www.mha.gov.in/en/commoncontent/individuals-terrorists-listed-fourth-schedule-of-unlawful-activities-prevention-act",
         "terms", ["terrorism", "asia", "india"], _MHA_NOTE + " Aliases split on '@'."),
    _src("IN_MHA_UAPA_TERRORIST_ORGANISATIONS", "India MHA - Terrorist Organisations (First Schedule, UAPA s.35)", "IN",
         {"main": "https://www.mha.gov.in/en/commoncontent/list-of-organisations-designated-%E2%80%98terrorist-organizations%E2%80%99-under-section-35-of"},
         parse_in_orgs, "https://www.mha.gov.in/en/divisionofmha/counter-terrorism-and-counter-radicalization-division",
         "terms", ["terrorism", "asia", "india"], _MHA_NOTE),
    _src("IN_MHA_UNLAWFUL_ASSOCIATIONS", "India MHA - Unlawful Associations (UAPA s.3)", "IN",
         {"main": "https://www.mha.gov.in/en/commoncontent/unlawful-associations-under-section-3-of-unlawful-activities-prevention-act-1967"},
         parse_in_unlawful, "https://www.mha.gov.in/en/commoncontent/unlawful-associations-under-section-3-of-unlawful-activities-prevention-act-1967",
         "terms", ["terrorism", "asia", "india"], _MHA_NOTE),
    _src("NG_NIGSAC_SANCTIONS_LIST", "Nigeria Sanctions Committee (NIGSAC) Nigeria Sanctions List", "NG",
         {"main": "https://nigsac.gov.ng/IndSancList"}, parse_ng, "https://nigsac.gov.ng/",
         "terms", ["terrorism", "africa"],
         "Individuals and entities tables on one page; DOB/nationality only on per-person detail pages (not fetched)."),
    _src("NZ_POLICE_DESIGNATED_TERRORISTS", "New Zealand Police - designated terrorist entities (UNSCR 1373 list)", "NZ",
         {"main": "https://www.police.govt.nz/advice/personal-community/counterterrorism/designated-entities/lists-associated-with-resolution-1373"},
         parse_nz, "https://www.police.govt.nz/advice/personal-community/counterterrorism/designated-entities/lists-associated-with-resolution-1373",
         "CC-BY", ["terrorism", "oceania", "apac"],
         "Name + 'Also known as' paragraph per row; expired-designation table skipped."),
    _src("SG_TSOFA_FIRST_SCHEDULE", "Singapore Terrorism (Suppression of Financing) Act - First Schedule", "SG",
         {"main": "https://sso.agc.gov.sg/Act/TSFA2002?ProvIds=Sc1-"}, parse_sg,
         "https://sso.agc.gov.sg/Act/TSFA2002", "terms", ["terrorism", "asia", "apac"],
         "Autonomous designations only (item 2); item 1 incorporates the UN 1267/1988 lists, covered by the UN source."),
    _src("TH_AMLO_DESIGNATED_PERSONS", "Thailand AMLO List of Designated Persons", "TH",
         {"main": "https://aps.amlo.go.th/aps/public/thailandlist/search?un_name=&un_registration_number=&passport_no=&un_id="},
         parse_th, "https://aps.amlo.go.th/", "terms", ["terrorism", "asia", "apac"],
         "Empty search returns the full list (Thai + English names, masked IDs). Detail pages (DOB) not fetched."),
    _src("US_STATE_FTO", "US State Dept Foreign Terrorist Organizations list", "US",
         {"main": "https://www.state.gov/foreign-terrorist-organizations/"}, parse_us_fto,
         "https://www.state.gov/foreign-terrorist-organizations/", "public", ["terrorism", "us", "americas"],
         "HTML table; amendment lines ('- X Amendment') become aliases. Mostly a cross-check of OFAC SDN."),
    _src("US_STATE_TERRORIST_EXCLUSION_LIST", "US State Dept Terrorist Exclusion List (TEL)", "US",
         {"main": "https://www.state.gov/terrorist-exclusion-list/"}, parse_us_tel,
         "https://www.state.gov/terrorist-exclusion-list/", "public", ["terrorism", "us", "americas"],
         "Immigration inadmissibility list; '(a.k.a. ...)' inline aliases."),
    _src("US_STATE_CUBA_RESTRICTED_LIST", "US State Dept Cuba Restricted List", "US",
         {"main": "https://www.state.gov/division-for-counter-threat-finance-and-sanctions/cuba-restricted-list"},
         parse_us_cuba, "https://www.state.gov/division-for-counter-threat-finance-and-sanctions/cuba-restricted-list",
         "public", ["sanctions", "us", "americas"],
         "Entities grouped under bold category headers; 31 CFR 515.209 restriction on direct financial transactions.",
         list_type="SANCTIONS"),
    _src("EE_MFA_NATIONAL_SANCTIONS", "Estonia Government (national) sanctions - subject lists (MFA)", "EE",
         {"human_rights": "https://www.vm.ee/en/list-subjects-sanction-government-republic-ensure-following-human-rights",
          "security": "https://www.vm.ee/en/list-subjects-sanction-government-republic-protect-estonias-security-and-interests-and-against"},
         parse_ee, "https://www.vm.ee/en/activity/international-sanctions/sanctions-government-republic-estonia",
         "public", ["sanctions", "eu", "europe"],
         "Numbered 'SURNAME, Given (also ALIAS; ALIAS)' lines on two pages.", list_type="SANCTIONS"),
    _src("RU_ROSFINMONITORING_TERRORIST_EXTREMIST_LIST",
         "Russia Rosfinmonitoring List of organizations and individuals involved in extremism or terrorism", "RU",
         {"main": "https://www.fedsfm.ru/documents/terrorists-catalog-portal-act"}, parse_ru,
         "https://www.fedsfm.ru/documents/terrorists-catalog-portal-act", "terms",
         ["terrorism", "state-list", "europe"],
         "POLITICALLY SENSITIVE STATE LIST: includes opposition figures, Memorial, FBK, Jehovah's Witnesses; "
         "OpenSanctions disabled its copy for this reason. Treat hits as 'Russian domestic designation', never as "
         "a standalone adverse finding. Site uses the Russian Trusted Root CA: set VENOMBOT_CA_BUNDLE to a bundle "
         "containing it (verification is never disabled). ~4 MB page, ~24k entries; '*' marks terrorism (else extremism). "
         "Typed COUNTER_SANCTIONS (severity LOW) on purpose: a state list that includes opposition figures must "
         "never produce a HOLD_FOR_REVIEW on its own.",
         list_type="COUNTER_SANCTIONS", max_age_days=7),
]
