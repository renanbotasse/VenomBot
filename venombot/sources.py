"""AML source registry and offline sample content."""

from __future__ import annotations

from typing import Dict, List

from venombot.models import AMLSource


class AMLSourcesRegistry:
    @staticmethod
    def get_sources() -> List[AMLSource]:
        return [
            AMLSource(
                name="OFAC_SDN",
                url="https://www.treasury.gov/ofac/downloads/sdnlist.txt",
                language="en",
                region="US",
                source_type="SANCTIONS",
                update_frequency="daily",
            ),
            AMLSource(
                name="CBK_SANCTIONS",
                url="https://www.cbk.gov.kw/en/supervision/anti-money-laundering",
                language="mixed",
                region="MENA",
                source_type="SANCTIONS",
                update_frequency="weekly",
            ),
            AMLSource(
                name="ARAB_FATF",
                url="https://www.menafatf.org/",
                language="ar",
                region="MENA",
                source_type="GREY_LIST",
                update_frequency="monthly",
            ),
            AMLSource(
                name="PEP_ARABIA",
                url="https://www.cbk.gov.kw/en/supervision/anti-money-laundering",
                language="mixed",
                region="MENA",
                source_type="PEP",
                update_frequency="weekly",
            ),
            AMLSource(
                name="UN_SANCTIONS",
                url="https://scsanctions.un.org/resources/xml/en/consolidated.xml",
                language="en",
                region="GLOBAL",
                source_type="SANCTIONS",
                update_frequency="daily",
            ),
            AMLSource(
                name="FATF_GREY_LIST",
                url="https://www.fatf-gafi.org/en/countries/black-and-grey-lists.html",
                language="en",
                region="GLOBAL",
                source_type="GREY_LIST",
                update_frequency="monthly",
            ),
            AMLSource(
                name="BBC_ARABIC_NEWS",
                url="https://www.bbc.com/arabic",
                language="ar",
                region="MENA",
                source_type="NEWS",
                update_frequency="daily",
            ),
            AMLSource(
                name="REUTERS_MENA",
                url="https://www.reuters.com/world/middle-east/",
                language="en",
                region="MENA",
                source_type="NEWS",
                update_frequency="daily",
            ),
            AMLSource(
                name="ALJAZEERA_ENGLISH",
                url="https://www.aljazeera.com/middle-east/",
                language="en",
                region="MENA",
                source_type="NEWS",
                update_frequency="daily",
            ),
        ]


# Used when live fetch fails (offline / blocked demo mode)
SAMPLE_CONTENT: Dict[str, str] = {
    "OFAC_SDN": (
        "OFAC Specially Designated Nationals List\n"
        "AHMED AL-SABAH - Specially Designated National - Kuwait\n"
        "ACME TRADING CO - Entity - UAE - money laundering concerns\n"
        "AL-NOOR FINANCE - Entity - KSA - sanctions evasion\n"
        "Sample SDN entries for AML screening demonstration.\n"
    ),
    "CBK_SANCTIONS": (
        "Central Bank of Kuwait - Anti-Money Laundering\n"
        "قائمة العقوبات - بنك الكويت المركزي\n"
        "أحمد الصباح مدرج على قائمة غسل أموال\n"
        "Dubai Holdings under investigation for fraud\n"
        "CBK sanctions and PEP monitoring list (demo).\n"
    ),
    "ARAB_FATF": (
        "MENAFATF - Arab Financial Action Task Force\n"
        "قائمة رمادية - دول عالية الخطر\n"
        "تمويل الإرهاب وغسل أموال - مراقبة إقليمية\n"
        "High-risk jurisdictions under enhanced monitoring.\n"
    ),
    "PEP_ARABIA": (
        "Politically Exposed Persons - MENA region\n"
        "شخص معرض سياسيا - أحمد الصباح - الكويت\n"
        "PEP screening database for Arabia region (demo).\n"
    ),
    "UN_SANCTIONS": (
        "<?xml version='1.0'?><CONSOLIDATED_LIST>"
        "<INDIVIDUAL><NAME>AHMED AL-SABAH</NAME><COUNTRY>Kuwait</COUNTRY></INDIVIDUAL>"
        "<ENTITY><NAME>ACME TRADING CO</NAME><COUNTRY>UAE</COUNTRY></ENTITY>"
        "</CONSOLIDATED_LIST>\n"
        "UN Security Council Consolidated Sanctions List (demo excerpt).\n"
    ),
    "FATF_GREY_LIST": (
        "FATF Jurisdictions under Increased Monitoring (Grey List)\n"
        "Countries subject to enhanced due diligence.\n"
        "Money laundering and terrorism financing risks.\n"
    ),
    "BBC_ARABIC_NEWS": (
        "بي بي سي عربي - أخبار الشرق الأوسط\n"
        "تحقيق في احتيال وفساد يتعلق بشركة تجارة\n"
        "أحمد الصباح مدرج على قائمة عقوبات دولية\n"
        "اعتقال مشتبه في تمويل الإرهاب وغسل أموال\n"
    ),
    "REUTERS_MENA": (
        "Reuters Middle East - Compliance Watch\n"
        "Acme Trading Co faces fraud investigation in UAE\n"
        "Al-Noor Finance linked to money laundering probe\n"
        "Dubai Holdings under regulatory review.\n"
    ),
    "ALJAZEERA_ENGLISH": (
        "Al Jazeera Middle East\n"
        "Sanctions update: entities listed for terrorism financing\n"
        "Regional banks tighten AML compliance after FATF review\n"
        "Investigation into corruption and bribery allegations.\n"
    ),
}
