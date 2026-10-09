"""Domestic terrorist / asset-freeze designation lists: MENA, GCC, Turkey, Kenya.

These lists are published by the governments themselves (xlsx, csv, json) and
carry names in native script, Latin transliteration, or both. Parsers keep the
native-script string untouched (kind "original") because
``venombot.normalize`` is what strips tatweel/diacritics at match time; mutating
the stored name here would hide the source's own spelling from reviewers.

Shared helpers (date/country normalisation for non-English cells, table
access by folded header) live here and are imported by ``terror_other``.

UAE (AE_UAEIEC_LOCAL_TERRORIST_LIST) is deliberately NOT registered: the portal
serves a true BIFF8 ``.xls`` which ``_tables`` cannot read, and the
OpenSanctions mirror OS_AE_LOCAL_TERRORISTS already covers it.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import re
import unicodedata
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Dict, Iterable, Iterator, List, Optional, Sequence, Set

from venombot.countries import countries_in
from venombot.dates import parse_dates
from venombot.entities import Entity, Identifier, Position
from venombot.lists import ListSource
from venombot.lists._tables import read_xlsx, xlsx_sheet_names
from venombot.lists._util import clean, read_text

# --------------------------------------------------------------------------
# shared helpers
# --------------------------------------------------------------------------

_ARABIC_INDIC = str.maketrans("٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹", "01234567890123456789")


def fold(text: str) -> str:
    """Lower-case, strip diacritics, map Turkish dotless i: a lookup key only.

    Never used on stored names; it exists so header and country lookups
    survive "İRAN"/"Iran", "Nationalité"/"nationalite" and Arabic hamza forms.
    """
    text = unicodedata.normalize("NFKD", str(text or "")).replace("ı", "i").replace("İ", "i")
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    return " ".join(text.lower().replace("ـ", "").split())


def ascii_digits(text: str) -> str:
    """Arabic-Indic / Persian digits -> ASCII so dates and ids can be parsed."""
    return str(text or "").translate(_ARABIC_INDIC)


_FR_MONTHS = {
    "janvier": "January", "fevrier": "February", "février": "February", "mars": "March",
    "avril": "April", "mai": "May", "juin": "June", "juillet": "July", "aout": "August",
    "août": "August", "septembre": "September", "octobre": "October", "novembre": "November",
    "decembre": "December", "décembre": "December",
}
_FR_RE = re.compile(r"\b(" + "|".join(_FR_MONTHS) + r")\b", re.IGNORECASE)
_YMD_SLASH = re.compile(r"\b(1[89]\d\d|20\d\d)[/.](\d{1,2})[/.](\d{1,2})\b")
_DMY_DASH = re.compile(r"\b(\d{1,2})-(\d{1,2})-(1[89]\d\d|20\d\d)\b")
_COMPACT = re.compile(r"(?<!\d)((?:1[89]|20)\d\d)(\d\d)(\d\d)(?!\d)")
_DMY_DOT = re.compile(r"(?<!\d)(\d{1,2})\.(\d{1,2})\.(1[89]\d\d|20\d\d)(?!\d)")
_SERIAL = re.compile(r"(?<![\d.\-/])(\d{5})(?![\d.\-/])")


def _excel_serial(match: "re.Match[str]") -> str:
    # Excel 1900 system: serial 1 = 1900-01-01 with the Lotus leap-year bug,
    # so day 0 is 1899-12-30. Only 5-digit values (1927..2173) are treated as
    # dates; a bare 4-digit number is a birth year.
    n = int(match.group(1))
    if not 10000 <= n <= 80000:
        return match.group(0)
    return (date(1899, 12, 30) + timedelta(days=n)).isoformat()


def norm_dates(text: Optional[str]) -> List[str]:
    """Parse list-specific date shapes into partial ISO strings.

    Handles what ``parse_dates`` does not: Excel serials (24535), YYYY/MM/DD,
    D-M-YYYY, YYYYMMDD, French month names and Arabic-Indic digits.
    @param text raw cell text, possibly several dates
    @return de-duplicated ISO dates, most precise first
    """
    if not text:
        return []
    t = ascii_digits(str(text))
    t = _FR_RE.sub(lambda m: _FR_MONTHS[m.group(1).lower()], t)
    t = _DMY_DOT.sub(lambda m: f"{m.group(3)}-{int(m.group(2)):02d}-{int(m.group(1)):02d}", t)
    t = _YMD_SLASH.sub(lambda m: f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}", t)
    t = _DMY_DASH.sub(lambda m: f"{m.group(3)}-{int(m.group(2)):02d}-{int(m.group(1)):02d}", t)
    t = _COMPACT.sub(lambda m: f"{m.group(1)}-{m.group(2)}-{m.group(3)}", t)
    t = _SERIAL.sub(_excel_serial, t)
    return parse_dates(t)


def add_dates(ent: Entity, text: Optional[str]) -> None:
    """Append every date in ``text`` to ``ent.birth_dates`` once."""
    for d in norm_dates(text):
        if d not in ent.birth_dates:
            ent.birth_dates.append(d)


def _build_country_map() -> Dict[str, str]:
    raw = {
        # Turkish
        "lubnan": "lb", "suriye": "sy", "irak": "iq", "iirak": "iq", "filistin": "ps", "rusya": "ru",
        "yunanistan": "gr", "turkiye": "tr", "turkiye cumhuriyeti": "tr", "turkiye cumhuriyeti vatandasi": "tr",
        "azerbaycan": "az", "fas": "ma", "libya": "ly", "ingiltere": "gb", "tirinidad ve tobago": "tt",
        "tunus": "tn", "kenya": "ke", "ozbekistan": "uz", "tacikistan": "tj", "afganistan": "af",
        "kore demokratik halk cumhuriyeti": "kp", "iran": "ir", "cin": "cn", "marshall adalari": "mh",
        "panama": "pa", "singapur": "sg", "misir": "eg", "suudi arabistan": "sa", "urdun": "jo",
        "kuveyt": "kw", "katar": "qa", "bahreyn": "bh", "umman": "om", "birlesik arap emirlikleri": "ae",
        "somali": "so", "cezayir": "dz", "almanya": "de", "fransa": "fr", "hollanda": "nl", "belcika": "be",
        "abd": "us", "amerika birlesik devletleri": "us", "ukrayna": "ua", "gurcistan": "ge",
        "ermenistan": "am", "kirgizistan": "kg", "turkmenistan": "tm", "kazakistan": "kz",
        "hindistan": "in", "endonezya": "id", "malezya": "my", "filipinler": "ph", "nijerya": "ng",
        "etiyopya": "et", "gurcistan cumhuriyeti": "ge", "bulgaristan": "bg", "romanya": "ro",
        "italya": "it", "ispanya": "es", "isvec": "se", "avusturya": "at", "isvicre": "ch",
        "tayland": "th", "pakistan": "pk", "bangladesh": "bd", "cad": "td", "nijer": "ne",
        # Indonesian
        "aljazair": "dz", "mesir": "eg", "arab saudi": "sa", "rusia": "ru", "inggris": "gb", "yaman": "ye",
        "maroko": "ma", "perancis": "fr", "filipina": "ph", "bosnia herzegovina": "ba", "yordania": "jo",
        "jerman": "de", "suriah": "sy", "turki": "tr", "uni emirat arab": "ae", "amerika serikat": "us",
        "republik demokratik kongo": "cd", "republik demokratik kongo (rdk)": "cd", "cina": "cn",
        "kamboja": "kh", "palestina": "ps", "libanon": "lb", "singapura": "sg", "belanda": "nl",
        "australia": "au", "afrika selatan": "za", "tunisia": "tn", "sudan": "sd", "somalia": "so",
        "aljazair ": "dz", "myanmar": "mm", "thailand": "th", "malaysia": "my", "indonesia": "id",
        # French
        "tunisie": "tn", "tunisienne": "tn", "marocaine": "ma", "maroc": "ma", "italienne": "it",
        "italie": "it", "belge": "be", "belgique": "be", "irlandaise": "ie", "irlande": "ie",
        "francaise": "fr", "france": "fr", "algerienne": "dz", "algerie": "dz", "libyenne": "ly",
        "libye": "ly", "liban": "lb", "syrienne": "sy", "syrie": "sy", "irakienne": "iq", "egyptienne": "eg",
        "turque": "tr", "turquie": "tr", "allemande": "de", "allemagne": "de", "britannique": "gb",
        "canadienne": "ca", "americaine": "us", "mauritanienne": "mr", "soudanaise": "sd",
        # Polish
        "iracka": "iq", "republika iraku": "iq", "irak ": "iq", "nigeryjska": "ng",
        "federalna republika nigerii": "ng", "syryjska": "sy", "libijska": "ly", "afganska": "af",
        "jemenska": "ye", "somalijska": "so", "republika mali": "ml", "malijska": "ml",
        "republika jemenu": "ye", "syryjska republika arabska": "sy", "libia": "ly", "libanska": "lb",
        "republika somalii": "so", "afganistanu": "af", "islamska republika afganistanu": "af",
        "demokratyczna republika konga": "cd", "republika nigru": "ne", "burkina faso": "bf",
        "tunezyjska": "tn", "algierska": "dz", "egipska": "eg", "saudyjska": "sa", "turecka": "tr",
        # English demonyms and local variants used in these files
        "kenyan": "ke", "tanzanian": "tz", "ugandan": "ug", "afghan": "af", "tunisian": "tn",
        "somali": "so", "kingdom of saudi arabia": "sa",
        # Vietnamese
        "my": "us", "uc": "au", "viet nam": "vn", "phap": "fr", "duc": "de", "anh": "gb",
        "canada": "ca", "na uy": "no", "nhat ban": "jp", "han quoc": "kr", "thai lan": "th",
        # Arabic (country names and demonyms)
        "اليمن": "ye", "يمني": "ye", "لبنان": "lb", "لبناني": "lb", "سوريا": "sy", "سوري": "sy",
        "العراق": "iq", "عراقي": "iq", "مصر": "eg", "مصري": "eg", "السعودية": "sa", "سعودي": "sa",
        "ليبيا": "ly", "ليبي": "ly", "تونس": "tn", "تونسي": "tn", "السودان": "sd", "سوداني": "sd",
        "فلسطين": "ps", "فلسطيني": "ps", "الكويت": "kw", "كويتي": "kw", "البحرين": "bh", "بحريني": "bh",
        "قطر": "qa", "قطري": "qa", "الأردن": "jo", "أردني": "jo", "الجزائر": "dz", "جزائري": "dz",
        "المغرب": "ma", "مغربي": "ma", "الصومال": "so", "صومالي": "so", "أفغانستان": "af", "أفغاني": "af",
        "باكستان": "pk", "باكستاني": "pk", "إيران": "ir", "إيراني": "ir", "تركيا": "tr", "تركي": "tr",
        "روسيا": "ru", "روسي": "ru", "الإمارات": "ae", "إماراتي": "ae", "عمان": "om", "عماني": "om",
        "موريتانيا": "mr", "موريتاني": "mr", "الصين": "cn", "بريطانيا": "gb", "بريطاني": "gb",
        "الهند": "in", "هندي": "in", "اندونيسيا": "id", "إندونيسيا": "id", "إندونيسي": "id",
        "الفلبين": "ph", "نيجيريا": "ng", "نيجيري": "ng", "مالي": "ml", "تشاد": "td", "أمريكي": "us",
        "الولايات المتحدة": "us", "ألماني": "de", "فرنسي": "fr", "كندي": "ca", "أسترالي": "au",
    }
    return {fold(k): v for k, v in raw.items()}


_COUNTRY_MAP = _build_country_map()
_COUNTRY_SPLIT = re.compile(r"[;,/\n|]|\s-\s|\b[a-d]\)\s*|\(|\)")


def countries_local(text: Optional[str]) -> List[str]:
    """ISO2 codes in a cell that may use Turkish/Indonesian/French/Arabic names.

    The local map is tried first on each fragment so that Vietnamese "Mỹ"
    (United States) is not read as the Latin code "my" (Malaysia). Falls back
    to ``countries_in`` for English names.
    @param text cell text such as "LÜBNAN", "Tunisienne/Belge", "Mỹ"
    @return ISO2 codes, de-duplicated, order preserved
    """
    out: List[str] = []
    if not text:
        return out
    for frag in _COUNTRY_SPLIT.split(str(text)):
        frag = frag.strip(" .-•*\xa0")
        if len(frag) < 2:
            continue
        code = _COUNTRY_MAP.get(fold(frag))
        found = [code] if code else (countries_in(frag) if len(frag) > 2 else [])
        for c in found:
            if c and c not in out:
                out.append(c)
    return out


def add_countries(target: List[str], text: Optional[str]) -> None:
    """Append ISO2 codes found in ``text`` (local names included) once each."""
    for c in countries_local(text):
        if c not in target:
            target.append(c)


def add_ident(ent: Entity, id_type: str, value: Optional[str], country: str = "") -> None:
    """Add an identifier once; placeholders ("Unknown", "n/a") are dropped."""
    value = clean(value)
    if not value or fold(value) in ("unknown", "n/a", "yok"):
        return
    if any(i.value == value and i.type == id_type for i in ent.identifiers):
        return
    ent.identifiers.append(Identifier(id_type, value, country))


def split_list(text: Optional[str], extra_seps: str = "") -> List[str]:
    """Split alias cells on ``;``, newlines, ``|`` and numbered/lettered markers.

    "1. A 2. B", "a) A b) B", "A; B" and "A | B" all become ["A", "B"].
    """
    if not text:
        return []
    t = str(text).replace("\xa0", " ").replace("؛", ";")
    t = re.sub(r"(?<![A-Za-z0-9])(?:\d{1,2}\.\s+|\d{1,2}\)\s*|[a-z]\)\s*)", "\n", t)
    parts = re.split(r"[;\n|" + re.escape(extra_seps) + r"]", t)
    out: List[str] = []
    for p in parts:
        p = clean(p.strip(" ,"))
        if p and p not in out:
            out.append(p)
    return out


def reorder_comma_name(name: str) -> str:
    """"HARB, KHALIL YUSIF" -> "KHALIL YUSIF HARB" (one comma only).

    Lists written "SURNAME, Given" are matched against "Given Surname" queries;
    both forms are stored so screening works either way.
    """
    if name.count(",") == 1:
        sur, given = [p.strip() for p in name.split(",")]
        if sur and given:
            return f"{given} {sur}"
    return name


def stable_id(prefix: str, seen: Set[str], *parts: Any) -> str:
    """Deterministic id for lists without a key column; collisions get a counter."""
    digest = hashlib.sha1("|".join(str(p) for p in parts).encode("utf-8")).hexdigest()[:12]
    base = f"{prefix}-{digest}"
    sid, n = base, 1
    while sid in seen:
        n += 1
        sid = f"{base}-{n}"
    seen.add(sid)
    return sid


def dict_rows(rows: Iterable[List[str]], header_has: Sequence[str]) -> Iterator[Dict[str, str]]:
    """Yield rows as dicts keyed by *folded* header text.

    The header row is the first row whose folded cells contain every token in
    ``header_has`` (title banners above it are skipped). Duplicate headers keep
    the first non-empty value; fully empty rows are dropped.
    """
    header: Optional[List[str]] = None
    for row in rows:
        if header is None:
            folded = [fold(c) for c in row]
            if all(any(tok in c for c in folded) for tok in header_has):
                header = folded
            continue
        if not any(c.strip() for c in row):
            continue
        rec: Dict[str, str] = {}
        for i, key in enumerate(header):
            if not key:
                continue
            val = row[i] if i < len(row) else ""
            if val and not rec.get(key):
                rec[key] = val
            else:
                rec.setdefault(key, "")
        yield rec


def g(rec: Dict[str, str], *subs: str, exclude: Sequence[str] = ()) -> str:
    """First non-empty value whose folded header contains one of ``subs``."""
    for sub in subs:
        s = fold(sub)
        for key, val in rec.items():
            if s in key and not any(fold(x) in key for x in exclude) and clean(val):
                return clean(val)
    return ""


def raw(rec: Dict[str, str], *subs: str) -> str:
    """Like ``g`` but keeps newlines (for multi-line alias cells)."""
    for sub in subs:
        s = fold(sub)
        for key, val in rec.items():
            if s in key and val and val.strip():
                return val
    return ""


def _sheet_rows(path: Path, wanted: str) -> Optional[List[List[str]]]:
    """Rows of the sheet whose stripped, folded name equals ``wanted``."""
    for name in xlsx_sheet_names(path):
        if fold(name.strip()) == fold(wanted):
            return list(read_xlsx(path, name))
    return None


def _is_org_by_structure(*person_markers: str) -> bool:
    return not any(clean(m) for m in person_markers)


# --------------------------------------------------------------------------
# Saudi Arabia - Permanent Committee for Combating Terrorism (PCCT)
# --------------------------------------------------------------------------


def parse_sa_pcct(paths: Dict[str, Path], source: ListSource) -> Iterator[Entity]:
    """Merge the English and Arabic PCCT workbooks (Individuals, Entities, Vessels).

    The two files share the "No." column per sheet, so the Arabic row is joined
    by number. The server sends no Content-Type; the file is detected by its zip
    magic, not by header.
    """
    en_path, ar_path = paths["main"], paths.get("ar")

    def ar_index(sheet: str, header_has: str) -> Dict[str, Dict[str, str]]:
        if not ar_path or not Path(ar_path).exists():
            return {}
        rows = _sheet_rows(Path(ar_path), sheet)
        out: Dict[str, Dict[str, str]] = {}
        for rec in dict_rows(rows or [], [header_has]):
            num = clean(rec.get("الرقم", ""))
            if num:
                out[num] = rec
        return out

    ar_ind = ar_index("أفراد", "الاسم")
    ar_ent = ar_index("كيانات", "اسم الكيان")
    ar_ves = ar_index("قطع بحرية", "اسم السفينة")

    def finish(ent: Entity, rec: Dict[str, str], ar: Dict[str, str], date_key: str) -> None:
        ent.listed_on = (norm_dates(g(rec, date_key)) or [""])[0]
        link = g(rec, "link of terrorism")
        if link:
            ent.programs.append(link)
            ent.linked_to.append(link)
        info = g(rec, "additional info")
        if info:
            ent.remarks = info
        ent.url = "https://pctc.pss.gov.sa/portal/pcct/slist/"

    # --- individuals
    rows = _sheet_rows(Path(en_path), "Individuals") or []
    for rec in dict_rows(rows, ["full name"]):
        num = g(rec, "no.")
        name = g(rec, "full name")
        if not name:
            continue
        ent = Entity(source=source.key, source_id=f"I{num}", schema="Person", list_type=source.list_type,
                     gender=g(rec, "gender").lower())
        ent.add_name(reorder_comma_name(name), "primary", "en")
        if "," in name:
            ent.add_name(name, "alias", "en")
        for a in split_list(raw(rec, "aliases")):
            ent.add_name(reorder_comma_name(a), "alias", "en")
        ar = ar_ind.get(num, {})
        ent.add_name(g(ar, "الاسم", exclude=["اخرى", "أخرى"]), "original", "ar")
        for a in split_list(raw(ar, "اخرى", "أخرى")):
            ent.add_name(a, "original", "ar")
        add_dates(ent, g(rec, "dob"))
        pob = g(rec, "pob")
        if pob:
            ent.birth_places.append(pob)
            add_countries(ent.countries, g(rec, "cob"))
        add_countries(ent.nationalities, g(rec, "nationality"))
        doc_type, doc_no = g(rec, "document type") or "Document", g(rec, "document no")
        for n in split_list(doc_no):
            add_ident(ent, doc_type, n, (countries_local(g(rec, "issuing country")) or [""])[0])
        add_countries(ent.countries, g(rec, "address"))
        finish(ent, rec, ar, "designated date")
        yield ent

    # --- entities
    rows = _sheet_rows(Path(en_path), "Entities") or []
    for rec in dict_rows(rows, ["entities name"]):
        num, name = g(rec, "no."), g(rec, "entities name")
        if not name:
            continue
        ent = Entity(source=source.key, source_id=f"E{num}", schema="Organization", list_type=source.list_type)
        ent.add_name(name, "primary", "en")
        for a in split_list(raw(rec, "aliases")):
            ent.add_name(a, "alias", "en")
        ar = ar_ent.get(num, {})
        ent.add_name(g(ar, "اسم الكيان"), "original", "ar")
        for a in split_list(raw(ar, "اسماء اخرى", "اسماء أخرى")):
            ent.add_name(a, "original", "ar")
        add_ident(ent, "Registration number", g(rec, "registration number"))
        add_countries(ent.countries, g(rec, "address"))
        owner = g(rec, "owner name")
        if owner:
            ent.linked_to.append(owner)
        finish(ent, rec, ar, "designated date")
        yield ent

    # --- vessels
    rows = _sheet_rows(Path(en_path), "Vessels") or []
    for rec in dict_rows(rows, ["vessel name"]):
        num, name = g(rec, "no."), g(rec, "vessel name")
        if not name:
            continue
        ent = Entity(source=source.key, source_id=f"V{num}", schema="Vessel", list_type=source.list_type)
        ent.add_name(name, "primary", "en")
        ar = ar_ves.get(num, {})
        ent.add_name(g(ar, "اسم السفينة"), "original", "ar")
        imo = g(rec, "imo")
        add_ident(ent, "IMO", re.sub(r"(?i)^imo\s*", "", imo))
        add_countries(ent.countries, g(rec, "vessel flag"))
        owner = g(rec, "owner name")
        if owner:
            ent.linked_to.append(owner)
        finish(ent, rec, ar, "designated date")
        yield ent


# --------------------------------------------------------------------------
# Egypt - Money Laundering Combating Unit (Arabic-only xlsx)
# --------------------------------------------------------------------------

_MASKED_ID = re.compile(r"^[\*\d]{6,}$")


def parse_eg_mlcu(paths: Dict[str, Path], source: ListSource) -> Iterator[Entity]:
    """Terrorists, terrorist entities and legal persons (3 Arabic sheets).

    Columns drift between releases: the "other name" column sometimes holds the
    masked national ID, so cells of the name-ish/ID-ish columns are classified
    by shape (all digits/asterisks = ID) instead of trusted by header.
    """
    path = Path(paths["main"])
    seen: Set[str] = set()
    for idx, sheet in enumerate(xlsx_sheet_names(path)):
        rows = list(read_xlsx(path, sheet))
        recs = list(_eg_records(rows))
        is_person_sheet = any("الرقم القومي" in k or "جواز" in k for r in recs[:1] for k in r)
        for rec in recs:
            name_key = next((k for k in rec if k.startswith("اسم") and "اخر" not in k and "آخر" not in k), None) \
                or next((k for k in rec if k == "الاسم"), None)
            name = clean(ascii_digits(rec.get(name_key or "", "")))
            if not name or _MASKED_ID.match(name):
                continue
            case = g(rec, "رقم القضية", "قضية")
            ent = Entity(source=source.key,
                         source_id=stable_id(f"S{idx}", seen, name, case, g(rec, "الرقم القومي")),
                         schema="Person" if is_person_sheet else "Organization",
                         list_type=source.list_type,
                         url="https://mlcu.org.eg/ar/3125/")
            ent.add_name(name, "primary", "ar")
            other = ""
            for k, v in rec.items():
                if ("اخر" in k or "آخر" in k) and v:
                    other = v
            ids: List[str] = []
            for cell in (other, g(rec, "الرقم القومي"), g(rec, "جواز")):
                cell = clean(ascii_digits(cell))
                if cell and _MASKED_ID.match(cell):
                    ids.append(cell)
                elif cell and cell == other:
                    for a in split_list(cell):
                        ent.add_name(a, "alias", "ar")
            for i in ids:
                add_ident(ent, "National ID (masked)", i, "eg")
            passport = clean(ascii_digits(g(rec, "جواز")))
            if passport and not _MASKED_ID.match(passport):
                add_ident(ent, "Passport", passport)
            add_countries(ent.nationalities, g(rec, "الجنسية"))
            if case:
                ent.programs.append(case)
            decision = g(rec, "رقم قرار")
            if decision:
                ent.remarks = decision
            pub = g(rec, "تاريخ النشر")
            ent.listed_on = (norm_dates(pub) or [""])[0]
            yield ent


def _eg_records(rows: List[List[str]]) -> Iterator[Dict[str, str]]:
    header: Optional[List[str]] = None
    for row in rows:
        cells = [c.strip() for c in row]
        if header is None:
            if sum(1 for c in cells if c) >= 2 and any("اسم" in c for c in cells):
                header = [" ".join(c.split()) for c in cells]
            continue
        if not any(cells):
            continue
        rec: Dict[str, str] = {}
        for i, h in enumerate(header):
            if h and i < len(row) and row[i].strip() and not rec.get(h):
                rec[h] = row[i].strip()
        yield rec


# --------------------------------------------------------------------------
# Qatar - NCTC unified list (JSON; local QLD rows + UN rows)
# --------------------------------------------------------------------------


def _dob_format(text: str) -> List[str]:
    """"EXACT_15/7/1963;1971___" -> ["1963-07-15", "1971"]."""
    out: List[str] = []
    for part in (text or "").split(";"):
        part = part.split("_", 1)[-1] if part.startswith(("EXACT", "APPROX", "BETWEEN")) else part
        part = part.strip("_ ")
        for d in norm_dates(part):
            if d not in out:
                out.append(d)
    return out


def parse_qa_nctc(paths: Dict[str, Path], source: ListSource) -> Iterator[Entity]:
    """Qatar unified sanctions list: local QLD rows are TERRORISM, UN rows SANCTIONS.

    The portal serves JSON as text/html; the body is parsed regardless. EN/AR
    name parts are split into first..fourth plus full names; the full forms are
    preferred and the parts are only a fallback.
    """
    data = json.loads(read_text(Path(paths["main"])))
    for item in data.get("content", []):
        ref = str(item.get("referenceNumber") or item.get("dataId") or "")
        is_person = str(item.get("typ", "1")) == "1"
        local = ref.upper().startswith("QLD") or str(item.get("moiListType")) == "1"
        ent = Entity(source=source.key, source_id=ref, schema="Person" if is_person else "Organization",
                     list_type="TERRORISM" if local else "SANCTIONS",
                     listed_on=(norm_dates(str(item.get("listedOn") or "")) or [""])[0],
                     url="https://portal.moi.gov.qa/wps/portal/NCTC/sanctionlist")
        en_full = clean(item.get("fullNameEn") or item.get("fullNameEN")) or clean(
            " ".join(str(item.get(k) or "") for k in ("firstNameEN", "secondNameEN", "thirdNameEN", "fourthNameEN")))
        ar_full = clean(item.get("fullNameAr") or item.get("fullNameAR")) or clean(
            " ".join(str(item.get(k) or "") for k in ("firstNameAR", "secondNameAR", "thirdNameAR", "fourthNameAR")))
        ent.add_name(en_full, "primary", "en")
        ent.add_name(ar_full, "original", "ar")
        for key, val in item.items():
            if "alias" in key.lower() or "aka" in key.lower():
                vals = val if isinstance(val, list) else [val]
                for v in vals:
                    for a in split_list(str(v or "")):
                        ent.add_name(a, "alias", "ar" if re.search("[؀-ۿ]", a) else "en")
        if not ent.names:
            continue
        for d in _dob_format(str(item.get("dobFormat") or "")):
            if d not in ent.birth_dates:
                ent.birth_dates.append(d)
        add_countries(ent.nationalities, str(item.get("nationality") or ""))
        add_ident(ent, "Passport", str(item.get("passportNo") or ""))
        add_ident(ent, "QID", str(item.get("qid") or ""), "qa")
        ent.programs.append("QA-QLD" if local else "UN")
        yield ent


# --------------------------------------------------------------------------
# Iraq - Anti-Money-Laundering Office, local sanctions list (JSON)
# --------------------------------------------------------------------------

_KUNYA = re.compile(r"\s+(?:المكنى|المكناة|الملقب|المعروف ب)\s*")


def parse_iq_aml(paths: Dict[str, Path], source: ListSource) -> Iterator[Entity]:
    """Iraqi freezing-committee designations (6k+ rows, almost all individuals).

    The whole list must arrive in one page: ordering is unstable across pages,
    so a multi-page answer would silently drop or duplicate names. Excluded
    (delisted) rows are skipped. Mother's name is kept because it is the
    strongest disambiguator for Iraqi four-part names.
    """
    body = json.loads(read_text(Path(paths["main"])))
    block = body["data"]
    if int(block.get("pageCount", 1)) != 1:
        raise ValueError("Iraq AML list returned several pages; request a larger pageSize")
    for item in block["data"]:
        if item.get("isExcluded"):
            continue
        name = clean(item.get("name"))
        if not name:
            continue
        is_person = int(item.get("type", 0)) == 0
        ent = Entity(source=source.key, source_id=str(item.get("id")), schema="Person" if is_person else "Organization",
                     list_type=source.list_type, url="https://aml.iq/",
                     listed_on=str(item.get("decisionYear") or ""))
        ent.add_name(name, "primary", "ar")
        parts = _KUNYA.split(name)
        if len(parts) > 1:
            for p in parts:
                ent.add_name(p, "alias", "ar")
        for a in split_list(item.get("alias")):
            ent.add_name(a, "alias", "ar")
        year = item.get("birthYear")
        if year and str(year).isdigit() and 1900 <= int(year) <= 2100:
            ent.birth_dates.append(str(int(year)))
        mother = clean(item.get("motherName"))
        if mother:
            ent.extra["mother_name"] = mother
            ent.remarks = f"Mother: {mother}"
        decision = clean(item.get("decisionNumber"))
        if decision:
            ent.programs.append(f"IQ-DECISION-{decision}")
        ent.countries.append("iq")
        yield ent


# --------------------------------------------------------------------------
# Israel - NBCTF (Umbraco delivery API v2)
# --------------------------------------------------------------------------

_IL_BASE = "https://matal.mod.gov.il"


def _il_items(paths: Dict[str, Path], prefix: str) -> Iterator[Dict[str, Any]]:
    for name in sorted(paths):
        if not name.startswith(prefix):
            continue
        body = json.loads(read_text(Path(paths[name])))
        if isinstance(body, dict) and body.get("type") == "Error":
            raise ValueError(f"NBCTF API error for {name}: {body.get('detail')}")
        for item in body.get("items", []):
            yield item


def _blocks(value: Any) -> List[Dict[str, Any]]:
    """Umbraco block lists: [{"elementType":..,"properties":{..}}] -> properties."""
    out: List[Dict[str, Any]] = []
    for b in value or []:
        if isinstance(b, dict):
            out.append(b.get("properties", b))
    return out


def _strs(value: Any) -> List[str]:
    out: List[str] = []
    for v in value or []:
        if isinstance(v, str):
            out.append(clean(v))
        elif isinstance(v, dict):
            p = v.get("properties", v)
            out.extend(clean(str(x)) for x in p.values() if isinstance(x, str))
    return [o for o in out if o]


def _il_url(item: Dict[str, Any]) -> str:
    path = ((item.get("cultures") or {}).get("en") or {}).get("path") or (item.get("route") or {}).get("path") or ""
    return f"{_IL_BASE}{path}" if path else _IL_BASE


def parse_il_operatives(paths: Dict[str, Path], source: ListSource) -> Iterator[Entity]:
    """Designated terror operatives (individuals); only status "Active" is listed.

    The request carries ``Accept-Language: en`` so ``fullName`` is the Latin
    transliteration; without it the API returns Hebrew-only names that cannot
    be matched against Latin queries. Non-designated records (~1,600) are kept
    out: they exist only as targets of seizure orders.
    """
    for item in _il_items(paths, "op"):
        p = item.get("properties", {})
        if p.get("status") != "Active" or not p.get("isDesignated"):
            continue
        name = clean(p.get("fullName") or item.get("name"))
        if not name:
            continue
        ent = Entity(source=source.key, source_id=str(item.get("id")), schema="Person", list_type=source.list_type,
                     listed_on=(norm_dates(str(p.get("temporaryDesignationDate") or "")) or [""])[0],
                     url=_il_url(item), gender=clean(p.get("gender")).lower())
        ent.add_name(name, "primary", "en")
        for a in _strs(p.get("additionalNames")):
            ent.add_name(a, "alias", "ar" if re.search("[؀-ۿ]", a) else ("he" if re.search("[א-ת]", a) else "en"))
        for b in _blocks(p.get("datesOfBirth")):
            add_dates(ent, str(b.get("date") or ""))
        for pl in _strs(p.get("placesOfBirth")):
            ent.birth_places.append(pl)
        for b in _blocks(p.get("passportDetails")):
            add_countries(ent.nationalities, str(b.get("nationality") or ""))
            add_ident(ent, "Passport", str(b.get("passportNumber") or ""), (countries_local(str(b.get("country") or "")) or [""])[0])
        for b in _blocks(p.get("identificationDocuments")):
            add_ident(ent, str(b.get("documentType") or "ID") if fold(str(b.get("documentType") or "")) != "unknown" else "ID",
                      str(b.get("identificationNumber") or ""), (countries_local(str(b.get("country") or "")) or [""])[0])
        for b in _blocks(p.get("bankAccounts")):
            add_ident(ent, "Bank account", " ".join(str(v) for v in b.values() if isinstance(v, (str, int)) and v))
        for ph in _strs(p.get("phoneNumbers")):
            ent.extra.setdefault("phones", []).append(ph)
        for em in _strs(p.get("emailAddresses")):
            ent.extra.setdefault("emails", []).append(em)
        for rel in p.get("relatedOrganizations") or []:
            if isinstance(rel, dict) and rel.get("name"):
                ent.linked_to.append(clean(rel["name"]))
        foreign = clean(p.get("foreignDesignator"))
        if foreign:
            ent.programs.append(foreign)
        ent.remarks = clean(re.sub(r"<[^>]+>", " ", str(p.get("comments") or "")))
        ent.extra["local_designator"] = clean(p.get("localDesignator"))
        yield ent


def parse_il_organizations(paths: Dict[str, Path], source: ListSource) -> Iterator[Entity]:
    """Designated terror organizations and financial platforms (status Active)."""
    for item in _il_items(paths, "org"):
        p = item.get("properties", {})
        if p.get("status") != "Active":
            continue
        name = clean(p.get("organizationName") or item.get("name"))
        if not name:
            continue
        ent = Entity(source=source.key, source_id=str(item.get("id")), schema="Organization", list_type=source.list_type,
                     listed_on=(norm_dates(str(p.get("temporaryDesignationDate") or "")) or [""])[0],
                     url=_il_url(item))
        ent.add_name(name, "primary", "en")
        for a in _strs(p.get("alternativeNames")):
            ent.add_name(a, "alias")
        add_ident(ent, "Corporation ID", str(p.get("corporationID") or ""))
        for b in _blocks(p.get("addresses")):
            add_countries(ent.countries, str(b.get("country") or ""))
        for b in _blocks(p.get("bankAccounts")):
            add_ident(ent, "Bank account", " ".join(str(v) for v in b.values() if isinstance(v, (str, int)) and v))
        for w in _strs(p.get("websites")):
            ent.extra.setdefault("websites", []).append(w)
        for rel in p.get("linkedOrganizations") or []:
            if isinstance(rel, dict) and rel.get("name"):
                ent.linked_to.append(clean(rel["name"]))
        foreign = clean(p.get("foreignDesignator"))
        if foreign:
            ent.programs.append(foreign)
        number = clean(p.get("designationNumber"))
        if number:
            ent.extra["designation_number"] = number
        yield ent


def parse_il_wallets(paths: Dict[str, Path], source: ListSource) -> Iterator[Entity]:
    """Administrative seizure orders on crypto wallets / exchange accounts.

    One entity per wallet so address screening can hit it; the designated
    operatives/organizations it belongs to are carried in ``linked_to``.
    """
    for item in _il_items(paths, "w"):
        p = item.get("properties", {})
        address = clean(p.get("walletAddress") or item.get("name"))
        if not address:
            continue
        ent = Entity(source=source.key, source_id=str(item.get("id") or address), schema="Unknown",
                     list_type=source.list_type, url=_IL_BASE)
        ent.add_name(address, "primary")
        coin = clean(p.get("coinType"))
        add_ident(ent, "Crypto wallet" + (f" ({coin})" if coin else ""), address)
        for key in ("designatedOperatives", "designatedOrganizations", "nonDesignatedOperatives", "nonDesignatedOrganizations"):
            for rel in p.get(key) or []:
                if isinstance(rel, dict) and rel.get("name"):
                    ent.linked_to.append(clean(rel["name"]))
        ent.remarks = f"Seizure order on {coin or 'crypto'} account/wallet"
        yield ent


# --------------------------------------------------------------------------
# Tunisia - CNLCT national list (French xlsx)
# --------------------------------------------------------------------------


def parse_tn_cnlct(paths: Dict[str, Path], source: ListSource) -> Iterator[Entity]:
    """National terrorism list. "Prénom" holds the whole patronymic chain.

    "Date et Lieu de naissance" is one cell ("16/04/1972 à Tunis"); "Divers"
    holds masked CIN numbers. Rows without a surname and without birth data are
    organisations.
    """
    rows = list(read_xlsx(Path(paths["main"])))
    seen: Set[str] = set()
    for rec in dict_rows(rows, ["prenom", "nom"]):
        first, last = g(rec, "prenom"), g(rec, "nom", exclude=["prenom"])
        if not (first or last):
            continue
        dob_cell = g(rec, "date et lieu")
        divers = raw(rec, "divers")
        is_org = _is_org_by_structure(last, dob_cell)
        full = clean(f"{first} {last}")
        ent = Entity(source=source.key,
                     source_id=stable_id("TN", seen, full, dob_cell),
                     schema="Organization" if is_org else "Person", list_type=source.list_type,
                     url="http://www.cnlct.tn/fr/?page_id=1684")
        ent.add_name(full, "primary", "fr")
        if first and last:
            ent.add_name(first, "alias", "fr") if len(first.split()) > 2 else None
        add_dates(ent, dob_cell)
        m = re.search(r"\bà\s*(.+)$", dob_cell)
        if m:
            ent.birth_places.append(clean(m.group(1)))
        add_countries(ent.nationalities, g(rec, "nationalite"))
        addr = g(rec, "adresse")
        if addr:
            ent.extra["address"] = addr
            add_countries(ent.countries, addr.rsplit(",", 1)[-1])
        for cin in re.findall(r"CIN\s*n?°?\s*:?\s*([\d\*]+)", divers):
            add_ident(ent, "CIN (masked)", cin, "tn")
        decision = g(rec, "decision")
        ent.listed_on = (norm_dates(decision) or [""])[0]
        why = g(rec, "justification")
        last_mod = g(rec, "derniere modification")
        ent.remarks = clean(" ".join(x for x in (why, decision, last_mod) if x))
        ent.programs.append("TN-CNLCT")
        yield ent


# --------------------------------------------------------------------------
# Turkey - MASAK (Mali Suçları Araştırma Kurulu) lists, TR national police wanted
# --------------------------------------------------------------------------


def _tr_ids(ent: Entity, cell: str) -> None:
    """Split the mixed "TCKN-VKN-PASAPORT" cell into typed identifiers."""
    cell = " ".join(cell.split())
    if not cell:
        return
    consumed = False
    for m in re.finditer(r"(?i)\b(?:PN|Passport|Pasaport)\s*[:\-]?\s*([A-Z0-9]{5,})", cell):
        add_ident(ent, "Passport", m.group(1))
        consumed = True
    for m in re.finditer(r"\b(\d{11})\b", cell):
        add_ident(ent, "TCKN", m.group(1), "tr")
        consumed = True
    for m in re.finditer(r"\b(\d{10})\b(?!\d)", cell):
        add_ident(ent, "VKN", m.group(1), "tr")
        consumed = True
    if not consumed:
        add_ident(ent, "ID", cell)


def parse_tr_masak_art7(paths: Dict[str, Path], source: ListSource) -> Iterator[Entity]:
    """MASAK Art.7 domestic freeze list (semicolon CSV, cp1254 Turkish).

    The "TCKN/VKN/GKN PASAPORT NO" header contains quoted newlines, which the
    csv module handles. Includes politically contested organisation labels
    (FETÖ/PDY) exactly as published; they are kept in programs, not judged.
    """
    text = read_text_cp1254(Path(paths["main"]))
    reader = csv.reader(io.StringIO(text), delimiter=";")
    seen: Set[str] = set()
    for rec in dict_rows(reader, ["sira no", "adi soyadi"]):
        name = g(rec, "adi soyadi")
        if not name:
            continue
        mother, father, birthplace = g(rec, "anne adi"), g(rec, "baba adi"), g(rec, "dogum yeri")
        ids = g(rec, "tckn", "pasaport")
        dob_cell = g(rec, "dogum tarihi")
        is_person = bool(mother or father or birthplace or re.search(r"\b\d{11}\b", ids))
        ent = Entity(source=source.key, source_id=stable_id("TR7", seen, g(rec, "sira no"), name, ids),
                     schema="Person" if is_person else "Organization", list_type=source.list_type,
                     url="https://en.hmb.gov.tr/fcib-tf-current-list")
        ent.add_name(name, "primary", "tr")
        for a in split_list(raw(rec, "diger isimleri")):
            ent.add_name(a, "alias", "tr")
        _tr_ids(ent, ids)
        add_countries(ent.nationalities, g(rec, "uyrugu", exclude=["diger"]))
        add_countries(ent.nationalities, g(rec, "diger uyruk"))
        add_dates(ent, dob_cell)
        if birthplace:
            ent.birth_places.append(birthplace)
        org = g(rec, "orgutu")
        if org:
            ent.programs.append(org)
        decision = g(rec, "karar tarih")
        ent.listed_on = (norm_dates(decision) or [""])[0]
        extra = [f"Mother: {mother}" if mother else "", f"Father: {father}" if father else "",
                 f"Decision: {decision}" if decision else ""]
        ent.remarks = "; ".join(x for x in extra if x)
        yield ent


def read_text_cp1254(path: Path) -> str:
    """Decode Turkish CSVs: UTF-8 when valid, else Windows-1254 (never latin-1 first)."""
    data = Path(path).read_bytes()
    for enc in ("utf-8-sig", "cp1254"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    return data.decode("cp1254", errors="replace")


def _tr_xlsx_rows(path: Path, header_has: Sequence[str]) -> Iterator[Dict[str, str]]:
    return dict_rows(read_xlsx(path), header_has)


def parse_tr_masak_art6(paths: Dict[str, Path], source: ListSource) -> Iterator[Entity]:
    """MASAK Art.6 list: freezes at foreign states' request (Hezbollah, etc.).

    Dates arrive either as Excel serials or "a)1970 b)1971" year lists; both
    are handled by ``norm_dates``.
    """
    seen: Set[str] = set()
    for rec in _tr_xlsx_rows(Path(paths["main"]), ["sira no", "adi soyadi"]):
        name = g(rec, "adi soyadi")
        if not name:
            continue
        mother, father = g(rec, "anne adi"), g(rec, "baba adi")
        dob, pob = g(rec, "dogum tarihi"), g(rec, "dogum yeri")
        is_person = bool(mother or father or dob or pob)
        ent = Entity(source=source.key, source_id=stable_id("TR6", seen, g(rec, "sira no"), name),
                     schema="Person" if is_person else "Organization", list_type=source.list_type,
                     url="https://en.hmb.gov.tr/portal/v2/pages?slug=6madde_ing")
        ent.add_name(name, "primary", "tr")
        for a in split_list(raw(rec, "diger isimleri")):
            ent.add_name(a, "alias", "tr")
        _tr_ids(ent, g(rec, "tckn", "pasaport"))
        add_countries(ent.nationalities, g(rec, "uyrugu", exclude=["diger"]))
        add_countries(ent.nationalities, g(rec, "diger uyruk"))
        add_dates(ent, dob)
        if pob:
            ent.birth_places.append(pob)
            add_countries(ent.countries, pob.rsplit(",", 1)[-1])
        addr = raw(rec, "adres")
        for seg in split_list(addr):
            add_countries(ent.countries, seg.rsplit(",", 1)[-1])
        gazette = g(rec, "resmi gazete")
        ent.listed_on = (norm_dates(gazette) or [""])[0]
        ent.programs.append(g(rec, "yaptirim turu") or "TR-MASAK-ART6")
        ent.remarks = f"Official Gazette: {gazette}" if gazette else ""
        yield ent


def parse_tr_masak_3ab(paths: Dict[str, Path], source: ListSource) -> Iterator[Entity]:
    """MASAK Law 7262 Art.3A/3B proliferation-financing list (UN DPRK/Iran).

    "KURULUŞ YAPISI" says whether the row is a legal entity; dates are Excel
    serials.
    """
    seen: Set[str] = set()
    for rec in _tr_xlsx_rows(Path(paths["main"]), ["sira no", "adi soyadi"]):
        name = g(rec, "adi soyadi")
        if not name:
            continue
        structure = g(rec, "kurulus yapisi")
        is_org = "tuzel" in fold(structure) or "kurulus" in fold(structure)
        ent = Entity(source=source.key, source_id=stable_id("TR3", seen, g(rec, "sira no"), name),
                     schema="Organization" if is_org else "Person", list_type=source.list_type,
                     url="https://en.hmb.gov.tr/portal/v2/pages?slug=3a3b")
        ent.add_name(name, "primary", "tr")
        ent.add_name(g(rec, "eski adi"), "alias", "tr")
        for a in split_list(raw(rec, "diger isimleri")):
            ent.add_name(a, "alias", "tr")
        _tr_ids(ent, g(rec, "pasaport"))
        add_countries(ent.nationalities, g(rec, "uyrugu"))
        add_dates(ent, g(rec, "dogum tarihi"))
        pob = g(rec, "dogum yeri")
        if pob:
            ent.birth_places.append(pob)
        for seg in split_list(raw(rec, "adres")):
            add_countries(ent.countries, seg.rsplit(",", 1)[-1])
        ent.listed_on = (norm_dates(g(rec, "listeye alinma")) or [""])[0]
        role = g(rec, "gorevi")
        other = g(rec, "diger bilgiler")
        if role:
            ent.positions.append(Position(title=role, status="unknown"))
        ent.remarks = other
        org = g(rec, "orgutu")
        ent.programs.append(org or "TR-MASAK-3A3B")
        yield ent


def parse_tr_wanted(paths: Dict[str, Path], source: ListSource) -> Iterator[Entity]:
    """Interior Ministry "terrorists wanted" notice (red/orange/yellow/grey).

    Rows have no unique key (``ID`` is 0 everywhere), so ids are hashes of
    category, name, birth year/place and organisation. Foreign birthplaces
    ("TACİKİSTAN") are also recorded as a linked country.
    """
    data = json.loads(read_text(Path(paths["main"])))
    seen: Set[str] = set()
    for category, people in data.items():
        if not isinstance(people, list):
            continue
        for p in people:
            if p.get("Sil"):
                continue
            first, last = clean(p.get("Adi")), clean(p.get("Soyadi"))
            if not (first or last):
                continue
            place = clean(p.get("DogumYeri"))
            org = clean(p.get("TOrgutAdi"))
            ent = Entity(source=source.key,
                         source_id=stable_id("TRW", seen, category, first, last, p.get("DogumTarihi"), place, org),
                         schema="Person", list_type=source.list_type, url="https://terorarananlar.pol.tr/")
            ent.add_name(f"{first} {last}".strip(), "primary", "tr")
            year = p.get("DogumTarihi")
            if year and str(year).isdigit():
                ent.birth_dates.append(str(year))
            if place:
                ent.birth_places.append(place)
                add_countries(ent.countries, place)
            if org:
                ent.programs.append(org)
            ent.extra["category"] = category
            ent.remarks = f"Wanted category: {category}"
            yield ent


# --------------------------------------------------------------------------
# Kenya - Financial Reporting Centre domestic list
# --------------------------------------------------------------------------


def parse_ke_frc(paths: Dict[str, Path], source: ListSource) -> Iterator[Entity]:
    """Kenya domestic terrorism list: numbered aliases, serial-number dates, "n/a"."""
    for rec in dict_rows(read_xlsx(Path(paths["main"])), ["reference", "full name"]):
        ref, name = g(rec, "reference"), g(rec, "full name")
        if not (ref and name):
            continue
        is_person = fold(g(rec, "category")).startswith("indiv")
        ent = Entity(source=source.key, source_id=ref, schema="Person" if is_person else "Organization",
                     list_type=source.list_type, gender=g(rec, "gender").lower(),
                     url="https://www.frc.go.ke/?page_id=193")
        ent.add_name(name, "primary", "en")
        for a in split_list(raw(rec, "aliases")):
            ent.add_name(a, "alias", "en")
        add_ident(ent, "National ID", g(rec, "id number"), "ke")
        add_ident(ent, "Passport", g(rec, "passport number"))
        add_dates(ent, g(rec, "date of birth", exclude=["alternative"]))
        add_dates(ent, g(rec, "alternative date"))
        pob = g(rec, "place of birth")
        if pob:
            ent.birth_places.append(pob)
        add_countries(ent.nationalities, g(rec, "nationality 1"))
        add_countries(ent.nationalities, g(rec, "nationality 2"))
        add_countries(ent.countries, g(rec, "physical address"))
        occupation = g(rec, "occupation")
        if occupation:
            ent.extra["occupation"] = occupation
        phone = g(rec, "telephone")
        if phone:
            ent.extra["phone"] = phone
        ent.listed_on = (norm_dates(g(rec, "date of designation")) or [""])[0]
        ent.remarks = g(rec, "narrative")
        ent.programs.append("KE-DOMESTIC")
        yield ent


# --------------------------------------------------------------------------
# registry
# --------------------------------------------------------------------------

_MENA = ["terrorism", "mena"]

SOURCES: List[ListSource] = [
    ListSource(
        key="SA_PCCT_NATIONAL_TERRORISM_LIST", name="Saudi Arabia PCCT National Terrorism List",
        jurisdiction="SA", list_type="TERRORISM",
        urls={"main": "https://pctc.pss.gov.sa/portal/pcct/slist/js/slistEN.xlsx",
              "ar": "https://pctc.pss.gov.sa/portal/pcct/slist/js/slistAR.xlsx"},
        parser=parse_sa_pcct, homepage="https://pctc.pss.gov.sa/portal/pcct/slist/", license="public",
        groups=_MENA + ["gcc", "core-mena"], max_age_days=14,
        notes="Static URLs, no Content-Type header (detect by zip magic). EN and AR twins joined by row No.; "
              "Individuals/Entities/Vessels sheets. Sheet name 'Entities ' has a trailing space."),
    ListSource(
        key="EG_MLCU_DOMESTIC_TERRORIST_LIST", name="Egypt MLCU Domestic Terrorist List",
        jurisdiction="EG", list_type="TERRORISM",
        urls={"main": "https://mlcu.org.eg/upload/uploadeditor/files/%d8%a7%d9%84%d9%82%d9%88%d8%a7%d8%a6%d9%85%20%d8%a7%d9%84%d9%85%d8%ad%d9%84%d9%8a%d8%a9/"
                      "%d9%82%d8%a7%d8%a6%d9%85%d8%a9%20%d8%a5%d8%af%d8%b1%d8%a7%d8%ac%20%d8%a7%d9%84%d9%83%d9%8a%d8%a7%d9%86%d8%a7%d8%aa%20%d8%a7%d9%84%d8%a5%d8%b1%d9%87%d8%a7%d8%a8%d9%8a%d8%a9%20"
                      "%d9%88%d8%a7%d9%84%d8%a5%d8%b1%d9%87%d8%a7%d8%a8%d9%8a%d9%8a%d9%86%2001%20%d8%a3%d9%83%d8%aa%d9%88%d8%a8%d8%b1%202026%20.xlsx"},
        parser=parse_eg_mlcu, homepage="https://mlcu.org.eg/ar/3125/", license="public",
        groups=_MENA + ["core-mena"], max_age_days=30,
        notes="File name embeds the issue date and changes per update; discover from the list page "
              "(a.LinkStyle.AutoDownload; page needs a browser UA, the F5 WAF rejects bot UAs). Arabic-only; "
              "national IDs are masked; ~4,000 individuals incl. mass Muslim Brotherhood listings. "
              "Mirror: OpenSanctions eg_terrorists."),
    ListSource(
        key="QA_NCTC_UNIFIED_SANCTIONS", name="Qatar NCTC Unified Sanctions List (local QLD + UN)",
        jurisdiction="QA", list_type="SANCTIONS",
        urls={"main": "https://portal.moi.gov.qa/wps/portal/NCTC/sanctionlist/unifiedsanctionlist/!ut/p/z1/jY_BDoIwAEM_aXUbIMdByLa4iZgRcBeyk1mi6MH4_RL16qS3Jq9tSjwZiZ_DM57DI97mcFn8yeeTLimnilPT1lygY5V2lllImZHhDWTKNJLvsJeFq9C1VLn8qCiwIX5NHj8ksC6fAHy6fiD-MyGsBuUwbVMvDaVyqmbA9lB8gdTFfyP3a9-PiPoFJNS7hg!!/dz/d5/L3dDZyEvUUZRSS9ZTlEh/p0/IZ7_I9242H42LOC4A0Q3BITM3M0G85=CZ6_I9242H42LOC4A0Q3BITM3M0GG5=NJgetSanctionList=/?lang=en&name=&qid=&passport=&listType="},
        parser=parse_qa_nctc, homepage="https://portal.moi.gov.qa/wps/portal/NCTC/sanctionlist", license="public",
        groups=_MENA + ["gcc", "core-mena"], max_age_days=7,
        notes="Long WebSphere portal URL must be used verbatim. JSON served as text/html. Entity list_type is set "
              "per row: local QLD rows TERRORISM, UN-derived rows SANCTIONS. Parser written from the discovery "
              "sample; the portal's WAF rejected the build host so the live file could not be re-parsed."),
    ListSource(
        key="IQ_AML_LOCAL_SANCTIONS", name="Iraq AML Office Local Sanctions List",
        jurisdiction="IQ", list_type="TERRORISM",
        urls={"main": "https://api.aml.iq/api/localsanctionslist/getall/1?pageSize=10000"},
        parser=parse_iq_aml, homepage="https://aml.iq/", license="public",
        groups=_MENA + ["core-mena"], max_age_days=7,
        notes="pageSize must cover rowCount (6,280) in one response; parser raises if pageCount != 1. "
              "Arabic names with kunya inside the string; birth year only; isExcluded rows skipped."),
    ListSource(
        key="IL_NBCTF_DESIGNATED_OPERATIVES", name="Israel NBCTF Designated Terror Operatives",
        jurisdiction="IL", list_type="TERRORISM",
        urls={f"op{n}": f"https://matal.mod.gov.il/umbraco/delivery/api/v2/content?filter=contentType:operative&skip={n * 1000}&take=1000"
              for n in range(4)},
        parser=parse_il_operatives, homepage="https://nbctf.mod.gov.il/en", license="public",
        headers={"Accept-Language": "en"},
        groups=_MENA + ["core-mena"], max_age_days=7,
        notes="Pages of 1000 (skip 0..3000; the last may be empty). Accept-Language: en returns Latin names; "
              "default is Hebrew-only. Only Active+designated rows are emitted (~430 of 2,073 records)."),
    ListSource(
        key="IL_NBCTF_DESIGNATED_ORGANIZATIONS", name="Israel NBCTF Designated Organizations",
        jurisdiction="IL", list_type="TERRORISM",
        urls={f"org{n}": f"https://matal.mod.gov.il/umbraco/delivery/api/v2/content?filter=contentType:organization&skip={n * 1000}&take=1000"
              for n in range(2)},
        parser=parse_il_organizations, homepage="https://nbctf.mod.gov.il/en", license="public",
        headers={"Accept-Language": "en"},
        groups=_MENA + ["core-mena"], max_age_days=7,
        notes="Status Active only (~400 of 495); includes crypto exchanges and payment platforms."),
    ListSource(
        key="IL_NBCTF_SEIZED_CRYPTO_WALLETS", name="Israel NBCTF Seized Crypto Wallets",
        jurisdiction="IL", list_type="SANCTIONS",
        urls={f"w{n}": f"https://matal.mod.gov.il/umbraco/delivery/api/v2/content?filter=contentType:cryptocurrencyWallet&skip={n * 1000}&take=1000"
              for n in range(4)},
        parser=parse_il_wallets, homepage="https://nbctf.mod.gov.il/en", license="public",
        groups=_MENA + ["core-mena", "crypto"], max_age_days=7,
        notes="No Accept-Language header here: the wallet content type 400s with one. Wallet/account ids are "
              "emitted as identifiers; designated owners in linked_to."),
    ListSource(
        key="TN_CNLCT_NATIONAL_TERRORIST_LIST", name="Tunisia CNLCT National Terrorist List",
        jurisdiction="TN", list_type="TERRORISM",
        urls={"main": "http://www.cnlct.tn/fr/wp-content/uploads/2026/09/16__09_2026fr.xlsx"},
        parser=parse_tn_cnlct, homepage="http://www.cnlct.tn/fr/?page_id=1684", license="public",
        groups=_MENA + ["africa"], max_age_days=30,
        notes="File name changes per update; discover first .xlsx (not MAJ_*) on the homepage entry-content. "
              "Plain http."),
    ListSource(
        key="TR_MASAK_DOMESTIC_FREEZE_ART7", name="Turkey MASAK Domestic Freeze List (Law 6415 Art.7)",
        jurisdiction="TR", list_type="TERRORISM",
        urls={"main": "https://ms.hmb.gov.tr/uploads/sites/2/2026/10/C-IC-DONDURMA-KARARI-ILE-MALVARLIKLARI-DONDURULANLAR-6415-SAYILI-KANUN-7.-MADDE-01.10-951d1ad07e573d1e.csv"},
        parser=parse_tr_masak_art7, homepage="https://en.hmb.gov.tr/fcib-tf-current-list", license="public",
        groups=_MENA + ["europe", "core-mena"], max_age_days=14,
        notes="Hashed file name changes each update; discover via https://en.hmb.gov.tr/portal/v2/pages?slug=7madde_ing. "
              "cp1254 semicolon CSV. Includes politically contested FETÖ/PDY listings and Turkish national IDs."),
    ListSource(
        key="TR_MASAK_FOREIGN_REQUEST_FREEZE_ART6", name="Turkey MASAK Foreign-Request Freeze List (Art.6)",
        jurisdiction="TR", list_type="SANCTIONS",
        urls={"main": "https://ms.hmb.gov.tr/uploads/sites/2/2026/01/B-YABANCI-ULKE-TALEPLERINE-ISTINADEN-MALVARLIKLARI-DONDURULANLAR-6415-SAYILI-KANUN-6.-MADDE-972ff13d63fcaf1d.xlsx"},
        parser=parse_tr_masak_art6, homepage="https://en.hmb.gov.tr/portal/v2/pages?slug=6madde_ing", license="public",
        groups=_MENA + ["europe", "core-mena"], max_age_days=30,
        notes="Hashed file name; discover via the WordPress REST page slug 6madde_ing."),
    ListSource(
        key="TR_MASAK_PROLIFERATION_FREEZE_3A3B", name="Turkey MASAK Proliferation Freeze List (Law 7262 Art.3A/3B)",
        jurisdiction="TR", list_type="SANCTIONS",
        urls={"main": "https://ms.hmb.gov.tr/uploads/sites/12/2026/07/D-7262-SAYILI-KANUN-3.A-VE-3.B-MADDELERI-EXCEL-29.07.2026-7065dd0684b9962c.xlsx"},
        parser=parse_tr_masak_3ab, homepage="https://en.hmb.gov.tr/portal/v2/pages?slug=3a3b", license="public",
        groups=_MENA + ["europe", "core-mena"], max_age_days=30,
        notes="Hashed file name; discover via slug 3a3b. Mostly UN DPRK/Iran entries; dates are Excel serials."),
    ListSource(
        key="TR_TERROR_WANTED", name="Turkey Interior Ministry Terrorists Wanted",
        jurisdiction="TR", list_type="WANTED",
        urls={"main": "https://terorarananlar.pol.tr/ISAYWebPart/TArananlar/GetTerorleArananlarList"},
        parser=parse_tr_wanted, homepage="https://terorarananlar.pol.tr/", license="public",
        headers={"Content-Type": "application/json"}, post_data=b"",
        groups=_MENA + ["europe", "wanted", "core-mena"], max_age_days=14,
        notes="The endpoint answers POST with an empty body (ListSource.post_data)."),
    ListSource(
        key="KE_FRC_DOMESTIC_TERRORISM_LIST", name="Kenya FRC Domestic Terrorism List",
        jurisdiction="KE", list_type="TERRORISM",
        urls={"main": "https://www.frc.go.ke/wp-content/uploads/2026/02/Domestic-List_Kenya.xlsx"},
        parser=parse_ke_frc, homepage="https://www.frc.go.ke/?page_id=193", license="public",
        groups=["terrorism", "africa"], max_age_days=30,
        notes="Upload path is dated; discover the href containing 'Domestic-List' on the TFS page."),
]
