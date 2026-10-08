"""Country name -> ISO 3166-1 alpha-2 resolution (stdlib only).

Lists spell countries as "Iran (Islamic Republic of)", "Korea, North",
"Russian Federation", "UAE" or in Arabic. Screening compares ISO codes, so
every parser resolves through to_iso2().
"""

from __future__ import annotations

import re
import unicodedata
from typing import Dict, List, Optional

# code|official or common name|variants...
_TABLE = """
af|afghanistan|islamic republic of afghanistan|islamic emirate of afghanistan|أفغانستان
ax|aland islands
al|albania
dz|algeria|الجزائر
as|american samoa
ad|andorra
ao|angola
ai|anguilla
aq|antarctica
ag|antigua and barbuda|antigua
ar|argentina
am|armenia
aw|aruba
au|australia
at|austria
az|azerbaijan
bs|bahamas|the bahamas
bh|bahrain|kingdom of bahrain|البحرين
bd|bangladesh
bb|barbados
by|belarus|byelorussia
be|belgium
bz|belize
bj|benin
bm|bermuda
bt|bhutan
bo|bolivia|plurinational state of bolivia
bq|bonaire sint eustatius and saba|caribbean netherlands
ba|bosnia and herzegovina|bosnia herzegovina|bosnia
bw|botswana
bv|bouvet island
br|brazil|brasil
io|british indian ocean territory
vg|british virgin islands|virgin islands british
bn|brunei|brunei darussalam
bg|bulgaria
bf|burkina faso
bi|burundi
cv|cabo verde|cape verde
kh|cambodia
cm|cameroon
ca|canada
ky|cayman islands
cf|central african republic|car
td|chad
cl|chile
cn|china|people s republic of china|prc
cx|christmas island
cc|cocos islands|cocos keeling islands
co|colombia
km|comoros|جزر القمر
cg|congo|republic of the congo|congo brazzaville
cd|democratic republic of the congo|drc|dr congo|congo democratic republic|congo kinshasa|zaire
ck|cook islands
cr|costa rica
ci|cote d ivoire|ivory coast
hr|croatia
cu|cuba
cw|curacao
cy|cyprus
cz|czechia|czech republic
dk|denmark
dj|djibouti|جيبوتي
dm|dominica
do|dominican republic
ec|ecuador
eg|egypt|arab republic of egypt|مصر
sv|el salvador
gq|equatorial guinea
er|eritrea
ee|estonia
sz|eswatini|swaziland
et|ethiopia
fk|falkland islands
fo|faroe islands
fj|fiji
fi|finland
fr|france
gf|french guiana
pf|french polynesia
tf|french southern territories
ga|gabon
gm|gambia|the gambia
ge|georgia
de|germany|deutschland|federal republic of germany
gh|ghana
gi|gibraltar
gr|greece
gl|greenland
gd|grenada
gp|guadeloupe
gu|guam
gt|guatemala
gg|guernsey
gn|guinea
gw|guinea bissau
gy|guyana
ht|haiti
hm|heard island and mcdonald islands
va|holy see|vatican|vatican city
hn|honduras
hk|hong kong|hong kong sar|hong kong china
hu|hungary
is|iceland
in|india
id|indonesia
ir|iran|islamic republic of iran|iran islamic republic of|persia|إيران
iq|iraq|republic of iraq|العراق
ie|ireland
im|isle of man
il|israel
it|italy
jm|jamaica
jp|japan
je|jersey
jo|jordan|hashemite kingdom of jordan|الأردن
kz|kazakhstan
ke|kenya
ki|kiribati
kp|north korea|korea north|democratic people s republic of korea|dprk|korea democratic people s republic of
kr|south korea|korea south|republic of korea|korea republic of|korea
xk|kosovo
kw|kuwait|state of kuwait|الكويت
kg|kyrgyzstan|kyrgyz republic
la|laos|lao people s democratic republic|lao pdr
lv|latvia
lb|lebanon|lebanese republic|لبنان
ls|lesotho
lr|liberia
ly|libya|libyan arab jamahiriya|state of libya|ليبيا
li|liechtenstein
lt|lithuania
lu|luxembourg
mo|macao|macau
mg|madagascar
mw|malawi
my|malaysia
mv|maldives
ml|mali
mt|malta
mh|marshall islands
mq|martinique
mr|mauritania|موريتانيا
mu|mauritius
yt|mayotte
mx|mexico
fm|micronesia|federated states of micronesia
md|moldova|republic of moldova
mc|monaco
mn|mongolia
me|montenegro
ms|montserrat
ma|morocco|المغرب
mz|mozambique
mm|myanmar|burma
na|namibia
nr|nauru
np|nepal
nl|netherlands|the netherlands|holland
nc|new caledonia
nz|new zealand
ni|nicaragua
ne|niger
ng|nigeria
nu|niue
nf|norfolk island
mk|north macedonia|macedonia|former yugoslav republic of macedonia
mp|northern mariana islands
no|norway
om|oman|sultanate of oman|عمان
pk|pakistan|باكستان
pw|palau
ps|palestine|state of palestine|palestinian territory|palestinian territories|west bank|gaza|gaza strip|فلسطين
pa|panama
pg|papua new guinea
py|paraguay
pe|peru
ph|philippines
pn|pitcairn
pl|poland
pt|portugal
pr|puerto rico
qa|qatar|state of qatar|قطر
re|reunion
ro|romania
ru|russia|russian federation|روسيا
rw|rwanda
bl|saint barthelemy
sh|saint helena
kn|saint kitts and nevis|st kitts and nevis
lc|saint lucia|st lucia
mf|saint martin
pm|saint pierre and miquelon
vc|saint vincent and the grenadines|st vincent and the grenadines
ws|samoa
sm|san marino
st|sao tome and principe
sa|saudi arabia|kingdom of saudi arabia|ksa|السعودية|المملكة العربية السعودية
sn|senegal
rs|serbia
sc|seychelles
sl|sierra leone
sg|singapore
sx|sint maarten
sk|slovakia|slovak republic
si|slovenia
sb|solomon islands
so|somalia|الصومال
za|south africa
gs|south georgia and the south sandwich islands
ss|south sudan
es|spain
lk|sri lanka
sd|sudan|السودان
sr|suriname
sj|svalbard and jan mayen
se|sweden
ch|switzerland|swiss confederation
sy|syria|syrian arab republic|سوريا|سورية
tw|taiwan|chinese taipei|republic of china
tj|tajikistan
tz|tanzania|united republic of tanzania
th|thailand
tl|timor leste|east timor
tg|togo
tk|tokelau
to|tonga
tt|trinidad and tobago
tn|tunisia|تونس
tr|turkey|turkiye|republic of turkiye|تركيا
tm|turkmenistan
tc|turks and caicos islands
tv|tuvalu
ug|uganda
ua|ukraine
ae|united arab emirates|uae|u a e|emirates|الإمارات|الإمارات العربية المتحدة
gb|united kingdom|uk|great britain|britain|england|scotland|wales|northern ireland|united kingdom of great britain and northern ireland
us|united states|usa|u s a|united states of america|us|america
um|united states minor outlying islands
vi|us virgin islands|virgin islands u s
uy|uruguay
uz|uzbekistan
vu|vanuatu
ve|venezuela|bolivarian republic of venezuela
vn|vietnam|viet nam
wf|wallis and futuna
eh|western sahara
ye|yemen|republic of yemen|اليمن
zm|zambia
zw|zimbabwe
su|soviet union|ussr
yu|yugoslavia
"""

_ISO3_TO_2 = {
    "afg": "af", "alb": "al", "dza": "dz", "ago": "ao", "arg": "ar", "arm": "am",
    "aus": "au", "aut": "at", "aze": "az", "bhr": "bh", "bgd": "bd", "blr": "by",
    "bel": "be", "bih": "ba", "bra": "br", "bgr": "bg", "khm": "kh", "cmr": "cm",
    "can": "ca", "caf": "cf", "tcd": "td", "chl": "cl", "chn": "cn", "col": "co",
    "cod": "cd", "cog": "cg", "hrv": "hr", "cub": "cu", "cyp": "cy", "cze": "cz",
    "dnk": "dk", "dji": "dj", "egy": "eg", "eri": "er", "est": "ee", "eth": "et",
    "fin": "fi", "fra": "fr", "geo": "ge", "deu": "de", "gha": "gh", "grc": "gr",
    "hkg": "hk", "hun": "hu", "ind": "in", "idn": "id", "irn": "ir", "irq": "iq",
    "irl": "ie", "isr": "il", "ita": "it", "jpn": "jp", "jor": "jo", "kaz": "kz",
    "ken": "ke", "prk": "kp", "kor": "kr", "kwt": "kw", "kgz": "kg", "lao": "la",
    "lva": "lv", "lbn": "lb", "lby": "ly", "ltu": "lt", "lux": "lu", "mys": "my",
    "mli": "ml", "mlt": "mt", "mrt": "mr", "mex": "mx", "mda": "md", "mng": "mn",
    "mne": "me", "mar": "ma", "moz": "mz", "mmr": "mm", "nld": "nl", "nzl": "nz",
    "nic": "ni", "ner": "ne", "nga": "ng", "mkd": "mk", "nor": "no", "omn": "om",
    "pak": "pk", "pse": "ps", "pan": "pa", "per": "pe", "phl": "ph", "pol": "pl",
    "prt": "pt", "qat": "qa", "rou": "ro", "rus": "ru", "rwa": "rw", "sau": "sa",
    "sen": "sn", "srb": "rs", "sgp": "sg", "svk": "sk", "svn": "si", "som": "so",
    "zaf": "za", "ssd": "ss", "esp": "es", "lka": "lk", "sdn": "sd", "swe": "se",
    "che": "ch", "syr": "sy", "twn": "tw", "tjk": "tj", "tza": "tz", "tha": "th",
    "tun": "tn", "tur": "tr", "tkm": "tm", "uga": "ug", "ukr": "ua", "are": "ae",
    "gbr": "gb", "usa": "us", "ury": "uy", "uzb": "uz", "ven": "ve", "vnm": "vn",
    "yem": "ye", "zmb": "zm", "zwe": "zw", "blz": "bz", "vgb": "vg", "cym": "ky",
    "pry": "py", "bol": "bo", "ecu": "ec", "gtm": "gt", "hnd": "hn", "slv": "sv",
    "cri": "cr", "dom": "do", "hti": "ht", "jam": "jm", "tto": "tt", "isl": "is",
    "lie": "li", "mco": "mc", "xkx": "xk", "brn": "bn", "mdv": "mv",
}


def _key(text: str) -> str:
    text = unicodedata.normalize("NFKD", text)
    text = "".join(c for c in text if not unicodedata.combining(c)).lower()
    text = re.sub(r"[\(\)\[\],.'’`]", " ", text)
    text = re.sub(r"[^\w؀-ۿ]+", " ", text)
    return " ".join(text.split())


_INDEX: Dict[str, str] = {}
_NAMES: Dict[str, str] = {}
for _line in _TABLE.strip().splitlines():
    _parts = _line.split("|")
    _code = _parts[0]
    _NAMES[_code] = _parts[1].title()
    for _p in _parts[1:]:
        _INDEX[_key(_p)] = _code
    _INDEX[_code] = _code


def to_iso2(value: Optional[str]) -> Optional[str]:
    """Resolve a country name or code to lower-case ISO2.

    @param value "Iran (Islamic Republic of)", "IRN", "ir", "الكويت", ...
    @return ISO2 code or None when unknown
    """
    if not value:
        return None
    raw = str(value).strip()
    k = _key(raw)
    if not k:
        return None
    if k in _INDEX:
        return _INDEX[k]
    if len(k) == 3 and k in _ISO3_TO_2:
        return _ISO3_TO_2[k]
    # "Iran (Islamic Republic of)" -> "iran"; "Korea, Democratic People's..."
    head = _key(re.split(r"[(\[,;/]", raw)[0])
    if head in _INDEX and head not in ("korea", "congo"):
        return _INDEX[head]
    if k.startswith("the ") and k[4:] in _INDEX:
        return _INDEX[k[4:]]
    return None


def countries_in(text: Optional[str]) -> List[str]:
    """Resolve a delimited list ("Iraq; Syria", "IR, IQ") to ISO2 codes."""
    if not text:
        return []
    out: List[str] = []
    # Only an exact whole-string hit counts as one country; "Korea, North"
    # must not be split while "Iraq; Syria" must be.
    whole = _key(str(text)) in _INDEX
    parts = [text] if whole else re.split(r"[;|/]|,(?![^()]*\))", str(text))
    for part in parts:
        code = to_iso2(part)
        if code and code not in out:
            out.append(code)
    return out


def country_name(code: str) -> str:
    """Display name for an ISO2 code (falls back to the code)."""
    return _NAMES.get((code or "").lower(), (code or "").upper())
