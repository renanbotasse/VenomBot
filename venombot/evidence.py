"""Contextual evidence about a subject that is not a list entry.

List hits are Entities with a match class. Everything else — news articles,
court cases, regulator press releases, corporate registry records, Wikidata
facts — is *context*: it can inform a reviewer but never blocks anyone, and
an allegation in an article is not a finding. Providers return Evidence.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List

# adverse_media | enforcement | court | corporate | pep_info | identity | leak
KINDS = ("adverse_media", "enforcement", "court", "corporate", "pep_info", "identity", "leak")


@dataclass
class Evidence:
    provider: str  # provider/feed key, e.g. "GDELT_DOC_API"
    kind: str
    title: str
    url: str = ""
    snippet: str = ""  # the sentence/field where the subject appears
    published: str = ""  # ISO date when known
    language: str = ""
    matched_name: str = ""
    # 0..1, how well the name matched in context; NOT a risk score
    name_score: float = 0.0
    topics: List[str] = field(default_factory=list)  # e.g. ["money_laundering"]
    extra: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


# Terms that make an article "adverse" for AML purposes. Matching one of
# these in the same sentence as the subject is a signal for review, not proof.
ADVERSE_TOPICS: Dict[str, List[str]] = {
    "money_laundering": ["money laundering", "laundering", "غسل الأموال", "غسيل الأموال", "lavagem de dinheiro", "blanchiment", "geldwäsche", "lavado de dinero"],
    "terrorism": ["terrorism", "terrorist", "terror financing", "إرهاب", "إرهابي", "terrorismo", "terrorisme"],
    "sanctions_evasion": ["sanctions evasion", "evade sanctions", "sanctions violation", "التحايل على العقوبات", "circumvent sanctions"],
    "corruption": ["corruption", "bribery", "bribe", "kickback", "embezzle", "فساد", "رشوة", "اختلاس", "corrupção", "corrupción", "korruption"],
    "fraud": ["fraud", "ponzi", "scam", "forgery", "احتيال", "تزوير", "fraude", "betrug"],
    "organised_crime": ["organised crime", "organized crime", "mafia", "cartel", "trafficking", "smuggling", "الجريمة المنظمة", "تهريب", "tráfico"],
    "tax_crime": ["tax evasion", "tax fraud", "تهرب ضريبي", "evasão fiscal"],
    "arrest_charge": ["arrested", "indicted", "charged with", "convicted", "sentenced", "pleaded guilty", "اعتقال", "اتهام", "إدانة", "حكم عليه", "preso", "condenado", "denunciado"],
    "investigation": ["under investigation", "investigated", "probe", "subpoena", "raided", "تحقيق", "investigação", "investigación"],
    "sanctioned": ["sanctioned", "designated by", "blacklisted", "asset freeze", "عقوبات", "قائمة سوداء", "sancionado"],
}


def topics_in(text: str) -> List[str]:
    """Adverse topics whose keywords appear in ``text`` (case-insensitive)."""
    low = text.lower()
    return [t for t, words in ADVERSE_TOPICS.items() if any(w.lower() in low for w in words)]
