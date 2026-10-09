"""Law-enforcement "most wanted" lists (FBI, Europol, HK ICAC, HHS-OIG, ICE, USSS, SAPS).

These are public appeals by police agencies. An entry means a warrant or
public appeal exists, not a conviction, so remarks keep the stated allegation
and the parsers never infer guilt.

NOTE on verification: the HTML layouts below were written from the discovery
notes (field classes / link patterns observed 2026-10-08) and tested against
synthetic fixtures in the same format. They have not been re-checked against
the live pages, so each parser is defensive and yields nothing (rather than
garbage) when a layout changes.
"""

from __future__ import annotations

import html as htmlmod
import json
import re
from pathlib import Path
from typing import Dict, Iterator, List, Optional, Tuple

from venombot.entities import Entity
from venombot.lists import ListSource
from venombot.lists._util import add_countries, add_dates, clean, read_text
from venombot.lists.corporate_registries import nationality_iso2

_TAG = re.compile(r"<[^>]+>")


def _text(fragment: str) -> str:
    """Strip tags and entities from an HTML fragment."""
    return clean(htmlmod.unescape(_TAG.sub(" ", fragment)))


def _flip(name: str) -> str:
    """"SURNAME, Given" -> "Given SURNAME" so token matching sees natural order."""
    if "," in name:
        sur, _, given = name.partition(",")
        if sur.strip() and given.strip():
            return f"{given.strip()} {sur.strip()}"
    return name


def _person(source: ListSource, sid: str, name: str, url: str = "", remarks: str = "") -> Entity:
    ent = Entity(source=source.key, source_id=sid, schema="Person", list_type=source.list_type,
                 url=url, remarks=remarks)
    ent.add_name(_flip(name), "primary")
    return ent


def _field(block: str, cls: str) -> str:
    """Text of the first element whose class list contains ``cls``."""
    m = re.search(r'<div class="[^"]*\b' + re.escape(cls) + r'\b[^"]*">(.*?)</div>', block, re.S)
    return _text(m.group(1)) if m else ""


# --------------------------------------------------------------------- FBI

FBI_PAGES = 30  # 1,262 items / 50 per page = 26 pages today; spare pages return no items


def parse_fbi(paths: Dict[str, Path], source: ListSource) -> Iterator[Entity]:
    """FBI Wanted API pages. Only person posters (Main / default or ten-most-wanted).

    Kidnapping victims, missing persons and "seeking information" posters are
    not wanted subjects and are skipped.
    """
    seen = set()
    for name in sorted(paths):
        try:
            data = json.loads(Path(paths[name]).read_text(encoding="utf-8"))
        except (ValueError, OSError):
            continue
        for it in data.get("items") or []:
            uid = it.get("uid") or ""
            if not uid or uid in seen or it.get("person_classification") != "Main":
                continue
            if it.get("poster_classification") not in ("default", "ten"):
                continue
            seen.add(uid)
            title = clean(it.get("title"))
            if not title:
                continue
            ent = _person(source, uid, title, it.get("url") or "")
            ent.schema = "Person"
            for al in it.get("aliases") or []:
                ent.add_name(clean(al), "alias")
            for dob in it.get("dates_of_birth_used") or []:
                add_dates(ent, dob)
            nat = nationality_iso2(it.get("nationality"))
            if nat:
                ent.nationalities.append(nat)
            if it.get("place_of_birth"):
                ent.birth_places.append(clean(it["place_of_birth"]))
            sex = clean(it.get("sex")).lower()
            ent.gender = {"male": "m", "female": "f"}.get(sex, "")
            ent.programs = [clean(s) for s in (it.get("subjects") or []) if clean(s)]
            if it.get("poster_classification") == "ten":
                ent.programs.append("Ten Most Wanted")
            ent.listed_on = clean(it.get("publication"))[:10]
            ent.remarks = clean(_text(it.get("caution") or "") or it.get("description"))[:600]
            if it.get("reward_text"):
                ent.extra["reward"] = clean(it["reward_text"])
            yield ent


# ----------------------------------------------------------------- Europol

def parse_europol(paths: Dict[str, Path], source: ListSource) -> Iterator[Entity]:
    """eumostwanted.eu homepage: Drupal ``views-row`` blocks."""
    page = read_text(paths["main"])
    for block in page.split('<div class="views-row">')[1:]:
        title = _field(block, "views-field-title")
        nid = _field(block, "views-field-nid")
        state = _field(block, "field-state-of-case")
        status = _field(block, "views-field-status")
        if not title or not nid:
            continue
        if "arrested" in (state + " " + status).lower() or title.lower() == "arrested":
            continue  # captured: no longer wanted
        path = _field(block, "views-field-view-node")
        ent = _person(source, nid, title, "https://eumostwanted.eu" + path if path.startswith("/") else path)
        add_countries(ent.countries, _field(block, "field-country-code") or _field(block, "field-enfast-country"))
        crime = _field(block, "field-crime")
        if crime:
            ent.programs.append(crime)
        parts = [crime, state, _field(block, "field-years-sentenced"),
                 _field(block, "field-propable-locations")]
        ent.remarks = "; ".join(p for p in parts if p)
        yield ent


# ---------------------------------------------------------------- HK ICAC

def parse_icac(paths: Dict[str, Path], source: ListSource) -> Iterator[Entity]:
    """ICAC index: anchors to ``wanted/index_id_<n>.html`` with "SURNAME Given" text.

    The index page carries the name only; offence and warrant date live on the
    detail pages, which we do not crawl (one request per person, ToS-neutral
    but unnecessary for name screening).
    """
    page = read_text(paths["main"])
    seen = set()
    for m in re.finditer(r'<a[^>]+href="([^"]*index_id_(\d+)\.html)"[^>]*>(.*?)</a>', page, re.S):
        href, ident, label = m.group(1), m.group(2), _text(m.group(3))
        if ident in seen or not label or label.lower().startswith(("more", "details", "read")):
            continue
        seen.add(ident)
        url = href if href.startswith("http") else "https://www.icac.org.hk/en/rc/wanted/" + href.split("/")[-1]
        ent = _person(source, ident, label, url, "Wanted by the Hong Kong ICAC (corruption-related offences)")
        ent.countries.append("hk")
        yield ent


# --------------------------------------------------------------- HHS-OIG

def parse_hhs_oig(paths: Dict[str, Path], source: ListSource) -> Iterator[Entity]:
    """Fugitive links ``/fraud/fugitives/<slug>/``; name from link text, else slug."""
    page = read_text(paths["main"])
    seen = set()
    for m in re.finditer(r'<a[^>]+href="(?:https://oig\.hhs\.gov)?/fraud/fugitives/([a-z0-9][a-z0-9-]*)/?"[^>]*>(.*?)</a>',
                         page, re.S | re.I):
        slug, label = m.group(1), _text(m.group(2))
        if slug in seen:
            continue
        seen.add(slug)
        if not label or label.lower() in ("read more", "view profile", "more", "details"):
            label = slug.replace("-", " ").title()
        ent = _person(source, slug, label, f"https://oig.hhs.gov/fraud/fugitives/{slug}/",
                      "HHS-OIG Most Wanted Health Care Fraud Fugitive")
        ent.countries.append("us")
        yield ent


# -------------------------------------------------------------------- ICE

def parse_ice(paths: Dict[str, Path], source: ListSource) -> Iterator[Entity]:
    """USWDS cards; the heading is truncated with an ellipsis so the image alt wins."""
    page = read_text(paths["main"])
    for card in re.split(r'<li[^>]+class="[^"]*usa-card[^"]*"', page)[1:]:
        link = re.search(r'href="(https://www\.ice\.gov/most-wanted/([a-z0-9-]+))"', card)
        if not link or link.group(2) == "captured":
            continue
        alt = re.search(r'<img[^>]+alt="([^"]+)"', card)
        head = re.search(r'usa-card__heading[^>]*>(.*?)</h2>', card, re.S)
        name = _text(alt.group(1)) if alt else ""
        if not name:
            name = _text(head.group(1)).rstrip("…").strip() if head else ""
        if not name:
            continue
        tag = re.search(r'usa-tag[^>]*>\s*Wanted for:\s*(.*?)</span>', card, re.S)
        ent = _person(source, link.group(2), name, link.group(1))
        if tag:
            ent.remarks = "Wanted for: " + _text(tag.group(1))
            ent.programs = [p.strip() for p in _text(tag.group(1)).split(";") if p.strip()]
        yield ent


# ------------------------------------------------------------ Secret Service

def parse_usss(paths: Dict[str, Path], source: ListSource) -> Iterator[Entity]:
    """Names in h2/h3 inside ``wanted-card`` blocks (Western order, SURNAME capitalised)."""
    page = read_text(paths["main"])
    seen = set()
    for card in re.split(r'<div[^>]+class="[^"]*wanted-card[^"]*"', page)[1:]:
        m = re.search(r"<h[23][^>]*>(.*?)</h[23]>", card, re.S)
        if not m:
            continue
        name = _text(m.group(1))
        if not name or name.lower() in ("contact us",) or name in seen:
            continue
        seen.add(name)
        ent = _person(source, re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-"), name,
                      "https://www.secretservice.gov/investigations/mostwanted")
        ent.remarks = "US Secret Service Most Wanted (cyber / financial crime)"
        yield ent


# --------------------------------------------------------------------- SAPS

def parse_saps(paths: Dict[str, Path], source: ListSource) -> Iterator[Entity]:
    """Rows ``Surname | Given names | Crime`` linking to detail.php?bid=<id>."""
    page = read_text(paths["main"])
    seen = set()
    for m in re.finditer(r'<a[^>]+href="detail\.php\?bid=(\d+)"[^>]*>(.*?)</a>', page, re.S):
        bid, label = m.group(1), _text(m.group(2))
        if bid in seen:
            continue
        parts = [p.strip() for p in label.split("|")]
        if len(parts) < 2 or not parts[0]:
            continue
        seen.add(bid)
        ent = _person(source, bid, f"{parts[1]} {parts[0]}".strip(),
                      f"https://www.saps.gov.za/crimestop/wanted/detail.php?bid={bid}")
        if len(parts) > 2 and parts[2]:
            ent.programs.append(parts[2])
            ent.remarks = f"Wanted for: {parts[2]}"
        ent.countries.append("za")
        yield ent


def _src(key: str, name: str, jur: str, urls: Dict[str, str], parser, home: str, lic: str,
         groups: List[str], notes: str = "", headers: Optional[Dict[str, str]] = None) -> ListSource:
    return ListSource(key=key, name=name, jurisdiction=jur, list_type="WANTED", urls=urls,
                      parser=parser, homepage=home, license=lic, groups=["wanted"] + groups,
                      max_age_days=7, notes=notes, headers=headers or {})


SOURCES: List[ListSource] = [
    _src("FBI_WANTED", "FBI Wanted (persons)", "US",
         {f"p{n:02d}": f"https://api.fbi.gov/wanted/v1/list?pageSize=50&page={n}" for n in range(1, FBI_PAGES + 1)},
         parse_fbi, "https://www.fbi.gov/wanted", "public", ["us"],
         "pageSize max 50; pages beyond the last return empty items. Keeps person_classification=Main with "
         "poster_classification default/ten only. A wanted poster is an allegation, not a conviction."),
    _src("EUROPOL_WANTED", "Europe's Most Wanted Fugitives (ENFAST)", "EU",
         {"main": "https://eumostwanted.eu/"}, parse_europol, "https://eumostwanted.eu/",
         "terms", ["eu", "europe"], "Arrested fugitives are skipped. Public appeal; attribute Europol/ENFAST."),
    _src("HK_ICAC_WANTED", "Hong Kong ICAC wanted persons", "HK",
         {"main": "https://www.icac.org.hk/en/rc/wanted/index.html"}, parse_icac,
         "https://www.icac.org.hk/en/rc/wanted/index.html", "terms", ["asia", "hk"],
         "Names only from the index page (detail pages hold offence/warrant date; not crawled)."),
    _src("US_HHS_OIG_FUGITIVES", "HHS-OIG most wanted health care fraud fugitives", "US",
         {"main": "https://oig.hhs.gov/fraud/fugitives/"}, parse_hhs_oig,
         "https://oig.hhs.gov/fraud/fugitives/", "public", ["us"],
         "Name from link text/slug only. oig.hhs.gov DNS was flaky in discovery."),
    _src("US_ICE_MOST_WANTED", "ICE most wanted fugitives", "US",
         {"main": "https://www.ice.gov/most-wanted"}, parse_ice, "https://www.ice.gov/most-wanted",
         "public", ["us"], "Full name taken from image alt (card heading is truncated)."),
    _src("US_SECRET_SERVICE_WANTED", "US Secret Service most wanted", "US",
         {"main": "https://www.secretservice.gov/investigations/mostwanted"}, parse_usss,
         "https://www.secretservice.gov/investigations/mostwanted", "public", ["us"],
         "Mostly cyber / financial-crime fugitives."),
    _src("ZA_SAPS_WANTED", "South African Police Service wanted persons", "ZA",
         {"main": "https://www.saps.gov.za/crimestop/wanted/list.php"}, parse_saps,
         "https://www.saps.gov.za/crimestop/wanted/list.php", "terms", ["africa", "za"],
         "Single page, ~600 rows: surname | given names | crime. Public appeal, no explicit licence."),
]
