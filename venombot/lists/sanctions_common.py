"""Helpers shared by the official sanctions-list parsers (sanctions_*.py).

Kept in one private-purpose module so each parser file stays about its own
list format. This module declares no SOURCES, so registry discovery skips it.
"""

from __future__ import annotations

import datetime as _dt
import gzip
import html
import re
import unicodedata
from pathlib import Path
from typing import Iterable, List, Optional

from venombot.countries import countries_in, to_iso2
from venombot.entities import Entity

_TAG = re.compile(r"<[^>]+>")


def local(tag: str) -> str:
    """Strip an XML namespace: '{ns}name' -> 'name'.

    Official XML exports differ in whether they use a default namespace; parsers
    that match on local names survive an unannounced namespace change.
    """
    return tag.rsplit("}", 1)[-1] if "}" in tag else tag


def is_non_latin(text: str) -> bool:
    """True when the string contains letters outside Latin scripts.

    Used to label native-script names ("original") without trusting each
    list's own language tags, which are often blank.
    """
    for ch in text:
        o = ord(ch)
        if o > 0x24F and not (0x1E00 <= o <= 0x1EFF) and ch.isalpha():
            return True
    return False


def add_name_auto(ent: Entity, value: Optional[str], kind: str = "alias", lang: str = "") -> None:
    """Add a name, relabelling plain aliases in native script as "original"."""
    if not value:
        return
    if kind == "alias" and is_non_latin(value):
        kind = "original"
    ent.add_name(value, kind, lang)


def excel_date(value: Optional[str]) -> str:
    """Excel serial number (as text) -> ISO date; "" if not a plausible serial.

    Government xlsx files mix real dates and serials in one column, so the
    caller can pass anything and gets "" back for non-serials.
    """
    if not value:
        return ""
    try:
        n = float(str(value).strip())
    except ValueError:
        return ""
    if not 1 < n < 80000:
        return ""
    return (_dt.date(1899, 12, 30) + _dt.timedelta(days=int(n))).isoformat()


def iso2(value: Optional[str]) -> str:
    """Country name or code -> lower-case ISO2, "" when unknown."""
    if not value:
        return ""
    code = to_iso2(value.strip())
    return code or ""


def add_country(target: List[str], value: Optional[str]) -> None:
    """Append the ISO2 of a single country name/code once."""
    code = iso2(value)
    if code and code != "00" and code not in target:
        target.append(code)


def add_countries_in(target: List[str], text: Optional[str]) -> None:
    """Append every country mentioned in free text once."""
    for c in countries_in(text):
        if c not in target:
            target.append(c)


def html_text(fragment: str) -> str:
    """Strip tags and unescape entities from a small HTML fragment."""
    return " ".join(html.unescape(_TAG.sub(" ", fragment)).split())


def open_maybe_gzip(path: Path):
    """Open a file for binary reading, transparently un-gzipping.

    The downloader already decodes Content-Encoding gzip, but some endpoints
    (eCFR, data.gov) serve gzip bodies without the header.
    """
    with open(path, "rb") as fh:
        magic = fh.read(2)
    return gzip.open(path, "rb") if magic == b"\x1f\x8b" else open(path, "rb")


def uniq(items: Iterable[str]) -> List[str]:
    """De-duplicate preserving order, dropping empties."""
    out: List[str] = []
    for i in items:
        if i and i not in out:
            out.append(i)
    return out


def partial_iso(year: str = "", month: str = "", day: str = "") -> str:
    """Build YYYY[-MM[-DD]] from possibly-blank/zero components."""
    try:
        y = int(year)
    except (TypeError, ValueError):
        return ""
    if y < 1000:
        return ""
    try:
        m = int(month)
    except (TypeError, ValueError):
        m = 0
    if not 1 <= m <= 12:
        return f"{y:04d}"
    try:
        d = int(day)
    except (TypeError, ValueError):
        d = 0
    if not 1 <= d <= 31:
        return f"{y:04d}-{m:02d}"
    return f"{y:04d}-{m:02d}-{d:02d}"


# --------------------------------------------------------------------------
# Country names in the languages of national lists (FR, CS, PL, ES).
# venombot.countries is English-only; these lists name countries natively.
# Keys are accent-folded lower-case.
# --------------------------------------------------------------------------

_LOCAL_COUNTRIES = {
    # French
    "russie": "ru", "federation de russie": "ru", "bielorussie": "by", "belarus": "by", "syrie": "sy",
    "republique arabe syrienne": "sy", "irak": "iq", "coree du nord": "kp", "rpdc": "kp",
    "republique populaire democratique de coree": "kp", "republique populaire democratique de coree (rpdc)": "kp",
    "tunisie": "tn", "birmanie": "mm", "birmanie/myanmar": "mm", "republique democratique du congo": "cd",
    "rdc": "cd", "congo": "cg", "moldavie": "md", "libye": "ly", "indonesie": "id", "algerie": "dz",
    "chine": "cn", "soudan": "sd", "soudan du sud": "ss", "royaume-uni": "gb", "egypte": "eg",
    "arabie saoudite": "sa", "somalie": "so", "emirats arabes unis": "ae", "republique centrafricaine": "cf",
    "turquie": "tr", "allemagne": "de", "ouganda": "ug", "maroc": "ma", "jordanie": "jo",
    "etats-unis": "us", "etats-unis d'amerique": "us", "liban": "lb", "koweit": "kw", "ouzbekistan": "uz",
    "malaisie": "my", "territoires palestiniens": "ps", "etat de palestine": "ps", "georgie": "ge",
    "lettonie": "lv", "azerbaidjan": "az", "mauritanie": "mr", "armenie": "am", "kirghizistan": "kg",
    "kirghizstan": "kg", "italie": "it", "suisse": "ch", "inde": "in", "tanzanie": "tz", "colombie": "co",
    "belgique": "be", "trinidad et tobago": "tt", "tadjikistan": "tj", "erythree": "er",
    "bosnie-herzegovine": "ba", "australie": "au", "republique tcheque": "cz", "tchad": "td",
    "lituanie": "lt", "ethiopie": "et", "serbie": "rs", "norvege": "no", "singapour": "sg", "cambodge": "kh",
    "estonie": "ee", "roumanie": "ro", "finlande": "fi", "pays-bas": "nl", "cameroun": "cm", "suede": "se",
    "cisjordanie": "ps", "hongrie": "hu", "maurice": "mu", "iles caimanes": "ky", "saint-kitts-et-nevis": "kn",
    "saint-christophe-et-nieves": "kn", "chypre": "cy", "slovenie": "si", "bahrein": "bh",
    "iles marshall": "mh", "comores": "km", "albanie": "al", "espagne": "es", "guinee": "gn",
    "iles vierges britanniques": "vg", "japon": "jp", "rwanda": "rw", "israel": "il", "israelienne": "il",
    "montenegro": "me", "honduras": "hn", "france": "fr", "ukraine": "ua", "kenya": "ke", "pakistan": "pk",
    "afghanistan": "af", "iran": "ir", "yemen": "ye", "venezuela": "ve", "nicaragua": "ni", "haiti": "ht",
    "cuba": "cu", "mali": "ml", "niger": "ne", "guinee-bissau": "gw", "burundi": "bi", "zimbabwe": "zw",
    "bulgarie": "bg", "pologne": "pl", "autriche": "at", "danemark": "dk", "irlande": "ie", "portugal": "pt",
    "grece": "gr", "slovaquie": "sk", "croatie": "hr", "luxembourg": "lu", "malte": "mt", "macedoine du nord": "mk",
    "kazakhstan": "kz", "bresil": "br", "mexique": "mx", "canada": "ca", "liban ": "lb", "angola": "ao",
    "nigeria": "ng", "ghana": "gh", "senegal": "sn", "gabon": "ga", "mozambique": "mz", "madagascar": "mg",
    "qatar": "qa", "oman": "om", "turkmenistan": "tm", "mongolie": "mn", "vietnam": "vn", "thailande": "th",
    "philippines": "ph", "bangladesh": "bd", "sri lanka": "lk", "nepal": "np", "afrique du sud": "za",
    # Czech
    "ruska federace": "ru", "belorusko": "by", "syrie ": "sy", "cina": "cn", "ukrajina": "ua",
    "iran ": "ir", "lotyssko": "lv", "litva": "lt", "estonsko": "ee", "polsko": "pl",
    # Polish
    "rosja": "ru", "federacja rosyjska": "ru", "bialorus": "by", "syria": "sy", "chiny": "cn",
    "lotwa": "lv", "litwa": "lt", "estonia": "ee", "niemcy": "de", "wielka brytania": "gb",
    "stany zjednoczone": "us", "turcja": "tr", "gruzja": "ge", "armenia": "am", "uzbekistan": "uz",
    "kirgistan": "kg", "tadzykistan": "tj", "kazachstan": "kz", "mołdawia": "md", "moldawia": "md",
    # Spanish/other
    "corea del norte": "kp", "rusia": "ru", "bielorrusia": "by", "siria": "sy",
    # English variants not in venombot.countries
    "democratic peoples republic of korea": "kp", "democratic people's republic of korea": "kp",
    "columbia": "co", "russian federation": "ru", "republic of serbia": "rs",
}


def fold(text: str) -> str:
    """Lower-case and strip accents so 'BIÉLORUSSIE' == 'bielorussie'."""
    nfkd = unicodedata.normalize("NFKD", text)
    return "".join(c for c in nfkd if not unicodedata.combining(c)).lower().strip()


_SPLIT = re.compile(r"[;/,()\r\n]+|\bet/ou\b|\bprétendument\b|\bprésumée\b|\baujourd'hui\b|:", re.I)


def countries_native(text: Optional[str]) -> List[str]:
    """ISO2 codes for every country named in a French/Czech/Polish/English field.

    Splits multi-country cells ("RUSSIE; ARMÉNIE") and tolerates annotations
    such as "(présumée)". Unknown pieces fall back to the English resolver.
    """
    if not text:
        return []
    out: List[str] = []
    for piece in _SPLIT.split(str(text)):
        key = fold(piece)
        key = re.sub(r"^(presumee|pretendument|aujourd'hui)\s*", "", key).strip(" .)-")
        if not key:
            continue
        code = _LOCAL_COUNTRIES.get(key)
        if code is None:
            code = to_iso2(piece.strip()) or ""
        if code and code not in out:
            out.append(code)
    return out
