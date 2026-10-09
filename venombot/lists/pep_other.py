"""PEP rosters for Australia, South Africa, Kuwait and the CIA World Leaders list.

EveryPolitician is deliberately not registered: its data stopped in 2019 and the
index file contains no people (only pointers to per-legislature Popolo files).
"""

from __future__ import annotations

import html
import re
from pathlib import Path
from typing import Dict, Iterator, List

from venombot.entities import Entity
from venombot.lists import ListSource
from venombot.lists._util import add_dates, add_identifier, clean, read_text
from venombot.lists.pep_europe import BROWSER_UA, iso2, load_json, mk_person, position


# --------------------------------------------------------------------------
# Australia
# --------------------------------------------------------------------------
def parse_au_aph(paths: Dict[str, Path], source: ListSource) -> Iterator[Entity]:
    """APH Handbook individuals (current and former federal parliamentarians, with DOB).

    The OData endpoint caps $top at 100, so the roster arrives as several pages.
    """
    seen = set()
    for key in sorted(paths):
        for v in load_json(paths[key]).get("value", []):
            pid = clean(v.get("PHID"))
            if not pid or pid in seen or clean(v.get("DateOfDeath")):
                continue
            seen.add(pid)
            given, fam = clean(v.get("PreferredName")) or clean(v.get("GivenName")), clean(v.get("FamilyName"))
            ent = mk_person(source, pid, f"{given} {fam}".strip(),
                            url=f"https://www.aph.gov.au/Senators_and_Members/Parliamentarian?MPID={pid}")
            ent.add_name(f"{clean(v.get('GivenName'))} {clean(v.get('MiddleNames'))} {fam}", "alias")
            ent.add_name(clean(v.get("DisplayName")), "alias")
            add_dates(ent, v.get("DateOfBirth"))
            place = ", ".join(x for x in (clean(v.get("PlaceOfBirth")), clean(v.get("StateOfBirth")), clean(v.get("CountryOfBirth"))) if x)
            if place:
                ent.birth_places.append(place)
            ent.gender = clean(v.get("Gender")).lower()
            ent.nationalities.append("au")
            ent.countries.append("au")
            roles = v.get("MPorSenator") or []
            current = clean(v.get("InCurrentParliament")).lower() == "true"
            if "Senator" in roles:
                title = f"Senator for {clean(v.get('SenateState')) or clean(v.get('State'))}"
            else:
                title = f"Member of the House of Representatives, {clean(v.get('Electorate'))}"
            ent.positions.append(position(title, "au", v.get("ServiceHistory_Start"), "" if current else v.get("ServiceHistory_End"),
                                          "current" if current else "ended"))
            if "Senator" in roles and "Member" in roles:
                ent.positions.append(position("Member of the House of Representatives", "au", "", "", "ended" if not current else "unknown"))
            ent.extra["party"] = clean(v.get("Party"))
            yield ent


# --------------------------------------------------------------------------
# South Africa
# --------------------------------------------------------------------------
_ZA_TITLES = re.compile(r"^(?:(?:Mr|Ms|Mrs|Miss|Dr|Prof|Adv|Rev|Chief|Inkosi|Hon|Judge|Justice)\.?\s+)+", re.I)


def parse_za_pmg(paths: Dict[str, Path], source: ListSource) -> Iterator[Entity]:
    """PMG members (all houses). Names are "Surname, Title I"; the People's
    Assembly slug in pa_link gives the full given name for better matching."""
    seen = set()
    for key in sorted(paths):
        for m in load_json(paths[key]).get("results", []):
            mid = m.get("id")
            name = clean(m.get("name"))
            if not mid or not name or mid in seen:
                continue
            seen.add(mid)
            surname, _, rest = name.partition(",")
            ent = mk_person(source, str(mid), f"{_ZA_TITLES.sub('', rest.strip())} {surname}".strip(),
                            url=clean(m.get("pa_link")) or clean(m.get("url")))
            ent.add_name(name, "alias")
            slug = clean(m.get("pa_link")).rstrip("/").rsplit("/", 1)[-1]
            if slug and "-" in slug and not slug.isdigit():
                ent.add_name(" ".join(w.capitalize() for w in slug.split("-")), "alias")
            ent.countries.append("za")
            ent.nationalities.append("za")
            house = m.get("house") or {}
            current = bool(m.get("current"))
            where = clean(house.get("name"))
            sphere = clean(house.get("sphere"))
            ent.positions.append(position(f"Member, {where}" if where else "Member of Parliament", "za",
                                          m.get("start_date"), "", "current" if current else "ended"))
            ent.extra.update({"party": clean((m.get("party") or {}).get("name")),
                              "level": "national" if sphere == "national" else "regional" if sphere else ""})
            yield ent


# --------------------------------------------------------------------------
# Kuwait
# --------------------------------------------------------------------------
_AR_TITLE = re.compile(r"^(?:نائب|وزير|رئيس مجلس)")
_URL_LINE = re.compile(r"^(?:https?://)?(?:www\.)?[\w.-]+\.[a-z]{2,}(?:/\S*)?$", re.I)


def parse_kw_cabinet(paths: Dict[str, Path], source: ListSource) -> Iterator[Entity]:
    """Kuwait cabinet page: "name / title / ministry URLs" repeated per minister.

    The page is Arabic-only and, at last check, still shows the May 2024
    formation (amendments are listed below it), so status is "unknown".
    """
    raw = read_text(paths["main"])
    raw = re.sub(r"<script.*?</script>|<style.*?</style>", "", raw, flags=re.S)
    text = html.unescape(re.sub(r"<[^>]+>", "\n", raw))
    lines = [ln.strip() for ln in re.split(r"\s*\n\s*", text) if ln.strip()]
    try:
        start = next(i for i, ln in enumerate(lines) if ln.startswith("بالتشكيل الوزاري"))
    except StopIteration as exc:
        raise ValueError("Kuwait cabinet page layout changed (formation header not found)") from exc
    stop = next((i for i, ln in enumerate(lines) if ln.startswith("التعديلات والاستقالات")), len(lines))
    block = [ln for ln in lines[start + 1:stop] if not _URL_LINE.match(ln)]
    # Skip the decree date lines until the first person (Prime Minister).
    i = 0
    while i < len(block) and not _AR_TITLE.match(block[i + 1] if i + 1 < len(block) else ""):
        i += 1
    n = 0
    while i + 1 < len(block):
        name, title = block[i], block[i + 1]
        if not _AR_TITLE.match(title) or _AR_TITLE.match(name):
            i += 1
            continue
        n += 1
        name = re.sub(r"^(?:سمو الشيخ|الشيخ|معالي)\s+", "", name)
        ent = mk_person(source, f"cmgs-{n}", name, url=source.homepage)
        ent.names[0].kind, ent.names[0].lang = "original", "ar"
        ent.countries.append("kw")
        ent.nationalities.append("kw")
        ent.positions.append(position(title, "kw", "2024-05-12", "", "unknown"))
        ent.extra["level"] = "national"
        yield ent
        i += 2


# --------------------------------------------------------------------------
# CIA World Leaders
# --------------------------------------------------------------------------
_CIA_ISO = {"Congo, Democratic Republic of the": "cd", "Congo, Republic of the": "cg"}
_ACTING = re.compile(r"\s*\((acting|interim)\)\s*$", re.I)


def parse_cia_leaders(paths: Dict[str, Path], source: ListSource) -> Iterator[Entity]:
    """CIA World Leaders (Gatsby static-query JSON): names per country, no titles.

    Names come as "GIVEN SURNAME-in-caps"; the chief-of-state / cabinet
    distinction needs per-country files, so positions are generic.
    """
    seen_all = set()
    for node in load_json(paths["main"])["data"]["leaders"]["nodes"]:
        country_name = clean(node.get("country"))
        cc = iso2(country_name) or _CIA_ISO.get(country_name, "")
        seen = set()
        for bucket in ("leaders", "leaders_2", "leaders_3"):
            for person in node.get(bucket) or []:
                raw = clean(person.get("name"))
                if not raw:
                    continue
                acting = bool(_ACTING.search(raw))
                name = _ACTING.sub("", raw)
                sid = f"{country_name}-{name}".replace(" ", "-")
                if name in seen or sid in seen_all:
                    continue
                seen.add(name)
                seen_all.add(sid)
                ent = mk_person(source, sid, name,
                                url="https://www.cia.gov/resources/world-leaders/foreign-governments/")
                if cc:
                    ent.countries.append(cc)
                title = "Government leader / cabinet member" + (" (acting)" if acting else "")
                ent.positions.append(position(f"{title}, {country_name}", cc, "", "", "current"))
                ent.extra["level"] = "national"
                yield ent


# --------------------------------------------------------------------------
# registry
# --------------------------------------------------------------------------
_AU = "https://handbookapi.aph.gov.au/api/individuals?%24top=100&%24skip={s}"
_ZA = "https://api.pmg.org.za/member/?page={p}"

SOURCES: List[ListSource] = [
    ListSource(
        key="AU_APH_HANDBOOK_API", name="Australia Parliamentary Handbook - federal parliamentarians", jurisdiction="AU",
        list_type="PEP", urls={f"p{s:04d}": _AU.format(s=s) for s in range(0, 2000, 100)},
        parser=parse_au_aph, homepage="https://www.aph.gov.au/Senators_and_Members", license="terms",
        groups=["pep", "other", "au", "oceania"], max_age_days=30,
        notes="OData caps $top at 100 (about 1,880 individuals, current and former, with DOB). APH terms: CC BY-NC-ND; verify commercial use.",
    ),
    ListSource(
        key="ZA_PMG_MEMBERS_API", name="South Africa PMG - members of Parliament and legislatures", jurisdiction="ZA",
        list_type="PEP", urls={f"p{p:02d}": _ZA.format(p=p) for p in range(1, 21)},
        parser=parse_za_pmg, homepage="https://pmg.org.za/members/", license="terms",
        groups=["pep", "other", "za", "africa"], max_age_days=30,
        notes="938 members over 19 pages of 50 (page 20 is a spare). Names are abbreviated; the People's Assembly slug supplies a full-name alias. "
              "Overlaps OpenSanctions za_pmg_legislators.",
    ),
    ListSource(
        key="KW_CMGS_CABINET", name="Kuwait Council of Ministers - current formation (Arabic)", jurisdiction="KW",
        list_type="PEP", urls={"main": "https://www.cmgs.gov.kw/CurrentMinisterialFormation.aspx"},
        parser=parse_kw_cabinet, homepage="https://www.cmgs.gov.kw/", license="terms",
        groups=["pep", "other", "kw", "gcc", "mena"], max_age_days=30,
        notes="Server-rendered Arabic HTML parsed as name/title pairs (about 20 ministers). The page showed the May 2024 formation with "
              "later amendments listed separately, so positions are marked status 'unknown'. Low-volume fetch only.",
    ),
    ListSource(
        key="CIA_WORLD_LEADERS_JSON", name="CIA World Leaders (chiefs of state and cabinet members)", jurisdiction="GLOBAL",
        list_type="PEP",
        urls={"main": "https://www.cia.gov/resources/world-leaders/page-data/sq/d/3338022342.json"},
        parser=parse_cia_leaders, homepage="https://www.cia.gov/resources/world-leaders/", license="public",
        groups=["pep", "other", "global"], headers={"User-Agent": BROWSER_UA}, max_age_days=14,
        notes="The static-query hash 3338022342 is a Gatsby build artifact: re-discover it from "
              "/resources/world-leaders/page-data/foreign-governments/page-data.json (staticQueryHashes) when it 404s. Names only; "
              "per-country files carry titles but need 199 more requests. Data can be stale (Kuwait entry dated 2024).",
    ),
]
