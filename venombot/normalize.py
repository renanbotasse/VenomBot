"""Name normalisation, transliteration and blocking keys.

Sanctions lists write the same person in many ways: "PUTIN, Vladimir
Vladimirovich", "Владимир Путин", "Muhammad" vs "Mohammed", "Abd al-Rahman"
vs "Abdulrahman". Everything that compares names goes through this module so
that lists, aliases and the screened subject are reduced to the same form.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Dict, List, Set

# Arabic script is written without short vowels, so a consonant-only
# transliteration lines up naturally with the consonant skeleton of Latin
# spellings ("محمد" -> "mhmd" == skeleton("Mohammed")).
_ARABIC: Dict[str, str] = {
    "ا": "a", "أ": "a", "إ": "i", "آ": "a", "ٱ": "a", "ء": "", "ؤ": "u", "ئ": "i",
    "ب": "b", "ت": "t", "ث": "th", "ج": "j", "ح": "h", "خ": "kh", "د": "d",
    "ذ": "dh", "ر": "r", "ز": "z", "س": "s", "ش": "sh", "ص": "s", "ض": "d",
    "ط": "t", "ظ": "z", "ع": "", "غ": "gh", "ف": "f", "ق": "q", "ك": "k",
    "ل": "l", "م": "m", "ن": "n", "ه": "h", "ة": "a", "و": "u", "ي": "i",
    "ى": "a", "پ": "p", "چ": "ch", "ژ": "zh", "گ": "g", "ک": "k", "ی": "i",
    "ـ": "",
}
_ARABIC_DIACRITICS = re.compile(r"[ً-ٰٟۖ-ۭ]")

_CYRILLIC: Dict[str, str] = {
    "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ё": "e",
    "ж": "zh", "з": "z", "и": "i", "й": "i", "к": "k", "л": "l", "м": "m",
    "н": "n", "о": "o", "п": "p", "р": "r", "с": "s", "т": "t", "у": "u",
    "ф": "f", "х": "kh", "ц": "ts", "ч": "ch", "ш": "sh", "щ": "shch",
    "ъ": "", "ы": "y", "ь": "", "э": "e", "ю": "yu", "я": "ya",
    "є": "ye", "і": "i", "ї": "yi", "ґ": "g", "ў": "u",
}

_GREEK: Dict[str, str] = {
    "α": "a", "β": "v", "γ": "g", "δ": "d", "ε": "e", "ζ": "z", "η": "i",
    "θ": "th", "ι": "i", "κ": "k", "λ": "l", "μ": "m", "ν": "n", "ξ": "x",
    "ο": "o", "π": "p", "ρ": "r", "σ": "s", "ς": "s", "τ": "t", "υ": "y",
    "φ": "f", "χ": "ch", "ψ": "ps", "ω": "o",
}

# Characters NFKD does not decompose to ASCII.
_LATIN_EXTRA: Dict[str, str] = {
    "ß": "ss", "æ": "ae", "œ": "oe", "ø": "o", "đ": "d", "ð": "d", "þ": "th",
    "ł": "l", "ı": "i", "ʼ": "", "ʻ": "", "’": "", "‘": "", "`": "", "'": "",
}

# Particles that carry no identifying weight and are written inconsistently
# ("Al-Sabah", "AL SABAH", "Alsabah"). Dropped from tokens, never from display.
PARTICLES: Set[str] = {
    "al", "el", "ul", "bin", "ben", "ibn", "bint", "binti", "bte", "bt",
    "van", "von", "der", "den", "de", "da", "di", "du", "del", "della", "la",
    "le", "dos", "das", "do", "y", "e", "mr", "mrs", "ms", "dr", "sheikh",
    "shaikh", "sayyid", "haji", "hajji",
}

# Legal-form noise for organisations; removing it lets "ACME TRADING LLC"
# match "Acme Trading".
ORG_NOISE: Set[str] = {
    "llc", "ltd", "limited", "inc", "incorporated", "corp", "corporation",
    "co", "company", "plc", "gmbh", "ag", "sa", "sarl", "srl", "spa", "bv",
    "nv", "oy", "ab", "as", "jsc", "ojsc", "pjsc", "cjsc", "ooo", "zao", "oao",
    "pao", "llp", "lp", "fze", "fzco", "fzc", "fz", "wll", "est", "establishment",
    "the", "group", "holding", "holdings", "and",
}

_TOKEN_SPLIT = re.compile(r"[^a-z0-9]+")
# "Abd al-Rahman", "Abdel Rahman", "Abdul Rahman", "Abdulrahman" -> "abdrahman"
_ABD = re.compile(r"\babd(?:ul|el|al|ol|u|e|i)?\s+(?:(?:al|el|ul)\s+)?([a-z])")
_ABD_GLUED = re.compile(r"\babd(?:ul|el|al|ol)([a-z]{3,})")


def _transliterate_char(ch: str) -> str:
    low = ch.lower()
    for table in (_CYRILLIC, _GREEK, _LATIN_EXTRA):
        if low in table:
            return table[low]
    return ch


def _arabic_word(word: str) -> str:
    # The definite article is glued in Arabic ("الصباح"); split it off so it
    # behaves like the Latin "Al-" particle.
    if word.startswith("ال") and len(word) > 3:
        word = word[2:]
    out = []
    for i, ch in enumerate(word):
        if ch == "و" and i == 0:
            out.append("w")
        elif ch == "ي" and i == 0:
            out.append("y")
        else:
            out.append(_ARABIC.get(ch, ch))
    return "".join(out)


def ascii_fold(text: str) -> str:
    """Return an ASCII, lower-case rendering of any supported script.

    @param text arbitrary name in Latin, Arabic, Cyrillic or Greek script
    @return lower-case ASCII approximation (unknown scripts are dropped)
    """
    if not text:
        return ""
    text = _ARABIC_DIACRITICS.sub("", text)
    words = []
    for word in text.split():
        if any("؀" <= c <= "ۿ" for c in word):
            word = _arabic_word(word)
        words.append(word)
    text = " ".join(words)
    text = "".join(_transliterate_char(c) for c in text)
    text = unicodedata.normalize("NFKD", text)
    text = "".join(c for c in text if not unicodedata.combining(c))
    return text.encode("ascii", "ignore").decode("ascii").lower()


def reorder_comma_name(name: str) -> str:
    """Turn list-style "SURNAME, Given Names" into "Given Names SURNAME".

    Only a single comma is treated as an inversion; anything else (company
    names with commas) is left alone.
    """
    if name.count(",") != 1:
        return name
    last, first = (part.strip() for part in name.split(","))
    if not last or not first or len(first.split()) > 6:
        return name
    return f"{first} {last}"


def tokens(name: str, *, is_org: bool = False) -> List[str]:
    """Normalised identifying tokens of a name, in original order.

    @param name display name in any supported script/order
    @param is_org drop legal-form noise words (LLC, JSC, ...)
    @return list of lower-case ASCII tokens without particles
    """
    text = ascii_fold(reorder_comma_name(name))
    text = re.sub(r"[-_./]", " ", text)
    text = _ABD.sub(lambda m: "abd" + m.group(1), text)
    text = _ABD_GLUED.sub(lambda m: "abd" + m.group(1), text)
    out = []
    for tok in _TOKEN_SPLIT.split(text):
        if not tok or tok in PARTICLES:
            continue
        if is_org and tok in ORG_NOISE:
            continue
        out.append(tok)
    return out


def normalize(name: str, *, is_org: bool = False) -> str:
    """Single-string normal form used for exact comparison and display keys."""
    return " ".join(tokens(name, is_org=is_org))


_SKELETON_SUBS = (
    ("ph", "f"), ("ck", "k"), ("kh", "h"), ("gh", "g"), ("dh", "d"),
    ("th", "t"), ("sch", "sh"), ("tch", "ch"), ("dj", "j"), ("dzh", "j"),
    ("ou", "u"), ("q", "k"), ("c", "k"), ("x", "ks"),
)

_ARABIC_CHARS = re.compile(r"[؀-ۿ]")


def has_arabic(text: str) -> bool:
    """True when a name contains Arabic script (consonantal transliteration)."""
    return bool(_ARABIC_CHARS.search(text or ""))


def skeleton(token: str) -> str:
    """Consonant skeleton of a token for spelling-tolerant blocking.

    Collapses transliteration variance: Mohammed/Muhammad/Mohamad -> "mhmd",
    Hussein/Husayn -> "hsn", Yousef/Yusuf/يوسف -> "sf", Zawahiri/الظواهري -> "zhr".
    "w" is treated as a semi-vowel because Arabic و is written "w", "u" or "o".
    """
    t = token
    for a, b in _SKELETON_SUBS:
        t = t.replace(a, b)
    t = re.sub(r"[aeiouyw]", "", t)
    t = re.sub(r"(.)\1+", r"\1", t)
    return t


def blocking_keys(name: str, *, is_org: bool = False) -> Set[str]:
    """Index keys for candidate retrieval: exact tokens plus skeletons.

    Skeleton keys are prefixed with "~" so they never collide with tokens.
    """
    keys: Set[str] = set()
    for tok in tokens(name, is_org=is_org):
        if len(tok) >= 2:
            keys.add(tok)
        sk = skeleton(tok)
        if len(sk) >= 2:
            keys.add("~" + sk)
    return keys
