"""AML source catalog — core + extended MENA/GCC-relevant feeds."""

from __future__ import annotations

from typing import List

from venombot.models import AMLSource

# Cap huge downloads so crawl stays practical (content also truncated on save).
_MB = 1_000_000
_CAP = 8 * _MB


def core_sources() -> List[AMLSource]:
    return [
        AMLSource(
            "OFAC_SDN",
            "https://sanctionslistservice.ofac.treas.gov/api/download/sdn.csv",
            "en",
            "US",
            "SANCTIONS",
            max_bytes=_CAP,
        ),
        AMLSource(
            "OFAC_ALT",
            "https://sanctionslistservice.ofac.treas.gov/api/download/alt.csv",
            "en",
            "US",
            "SANCTIONS",
            max_bytes=_CAP,
        ),
        AMLSource(
            "CBK_SANCTIONS",
            "https://www.cbk.gov.kw/en",
            "mixed",
            "MENA",
            "SANCTIONS",
        ),
        AMLSource(
            "UN_SANCTIONS",
            "https://scsanctions.un.org/resources/xml/en/consolidated.xml",
            "en",
            "GLOBAL",
            "SANCTIONS",
            max_bytes=_CAP,
        ),
        AMLSource(
            "UN_OPENSANCTIONS",
            "https://data.opensanctions.org/datasets/latest/un_sc_sanctions/targets.simple.csv",
            "en",
            "GLOBAL",
            "SANCTIONS",
            max_bytes=_CAP,
        ),
        AMLSource(
            "FATF_GREY_LIST",
            "https://www.fatf-gafi.org/en/countries/black-and-grey-lists.html",
            "en",
            "GLOBAL",
            "GREY_LIST",
        ),
        AMLSource(
            "BBC_ARABIC_NEWS",
            "https://feeds.bbci.co.uk/arabic/rss.xml",
            "ar",
            "MENA",
            "NEWS",
        ),
        AMLSource(
            "BBC_MENA_ENGLISH",
            "https://feeds.bbci.co.uk/news/world/middle_east/rss.xml",
            "en",
            "MENA",
            "NEWS",
        ),
        AMLSource(
            "ALJAZEERA_ENGLISH",
            "https://www.aljazeera.com/xml/rss/all.xml",
            "en",
            "MENA",
            "NEWS",
        ),
    ]


def sanctions_sources() -> List[AMLSource]:
    """Additional sanctions lists (EU, UK, OFAC non-SDN, CA, AU, CH, UAE, KW, OS)."""
    return [
        AMLSource(
            "EU_SANCTIONS",
            # Public demo token used by EU FSF portal downloads; may rotate.
            "https://webgate.ec.europa.eu/fsd/fsf/public/files/xmlFullSanctionsList_1_1/content?token=dG9rZW4tMjAxNw",
            "en",
            "EU",
            "SANCTIONS",
            max_bytes=_CAP,
        ),
        AMLSource(
            "UK_SANCTIONS",
            "https://sanctionslist.fcdo.gov.uk/docs/UK-Sanctions-List.csv",
            "en",
            "UK",
            "SANCTIONS",
            max_bytes=_CAP,
        ),
        AMLSource(
            "OFAC_NON_SDN",
            "https://sanctionslistservice.ofac.treas.gov/api/download/cons_prim.csv",
            "en",
            "US",
            "SANCTIONS",
            max_bytes=_CAP,
        ),
        AMLSource(
            "US_CSL",
            # Trade.gov CSL API requires a subscription key; landing page for now.
            "https://www.trade.gov/consolidated-screening-list",
            "en",
            "US",
            "SANCTIONS",
        ),
        AMLSource(
            "CA_SEMA",
            "https://www.international.gc.ca/world-monde/assets/office_docs/international_relations-relations_internationales/sanctions/sema-lmes.xml",
            "en",
            "CA",
            "SANCTIONS",
            max_bytes=_CAP,
        ),
        AMLSource(
            "AU_DFAT",
            "https://www.dfat.gov.au/international-relations/security/sanctions/consolidated-list",
            "en",
            "AU",
            "SANCTIONS",
        ),
        AMLSource(
            "CH_SECO",
            "https://www.sesam.search.admin.ch/sesam-search-web/pages/downloadXmlGesamtliste.xhtml?lang=en&action=downloadXmlGesamtlisteAction",
            "en",
            "CH",
            "SANCTIONS",
            max_bytes=_CAP,
        ),
        AMLSource(
            "UAE_TERROR_LIST",
            "https://www.uaeiec.gov.ae",
            "mixed",
            "MENA",
            "SANCTIONS",
        ),
        AMLSource(
            "KUWAIT_MOFA",
            "https://www.mofa.gov.kw",
            "mixed",
            "MENA",
            "SANCTIONS",
        ),
        AMLSource(
            "OPENSANCTIONS_DEFAULT",
            "https://data.opensanctions.org/datasets/latest/default/targets.simple.csv",
            "en",
            "GLOBAL",
            "SANCTIONS",
            # Full file is hundreds of MB; store a capped slice for offline search.
            max_bytes=_CAP,
        ),
    ]


def pep_wanted_sources() -> List[AMLSource]:
    return [
        AMLSource(
            "OPENSANCTIONS_PEPS",
            "https://data.opensanctions.org/datasets/latest/peps/targets.simple.csv",
            "en",
            "GLOBAL",
            "PEP",
            max_bytes=_CAP,
        ),
        AMLSource(
            "WIKIDATA_PEPS",
            (
                "https://query.wikidata.org/sparql?format=json&query="
                "SELECT%20%3FitemLabel%20%3FcountryLabel%20WHERE%20%7B"
                "%20%3Fitem%20wdt%3AP39%20%3Fposition%20."
                "%20%3Fitem%20wdt%3AP27%20%3Fcountry%20."
                "%20VALUES%20%3Fcountry%20%7B%20wd%3AQ817%20wd%3AQ878%20wd%3AQ851%20wd%3AQ398%20wd%3AQ858%20%7D"
                "%20SERVICE%20wikibase%3Alabel%20%7B%20bd%3AserviceParam%20wikibase%3Alanguage%20%22en%22.%20%7D"
                "%20%7D%20LIMIT%20200"
            ),
            "en",
            "MENA",
            "PEP",
        ),
        AMLSource(
            "CIA_WORLD_LEADERS",
            "https://www.cia.gov/resources/world-leaders/foreign-governments/",
            "en",
            "GLOBAL",
            "PEP",
        ),
        AMLSource(
            "INTERPOL_RED",
            "https://ws-public.interpol.int/notices/v1/red?resultPerPage=20",
            "en",
            "GLOBAL",
            "WANTED",
        ),
        AMLSource(
            "FBI_WANTED",
            "https://api.fbi.gov/wanted/v1/list?pageSize=20",
            "en",
            "US",
            "WANTED",
        ),
        AMLSource(
            "EUROPOL_WANTED",
            "https://eumostwanted.eu",
            "en",
            "EU",
            "WANTED",
        ),
        AMLSource(
            "WB_DEBARRED",
            "https://www.worldbank.org/en/projects-operations/procurement/debarred-firms",
            "en",
            "GLOBAL",
            "SANCTIONS",
        ),
    ]


def corporate_sources() -> List[AMLSource]:
    return [
        AMLSource(
            "ICIJ_OFFSHORE",
            "https://offshoreleaks.icij.org",
            "en",
            "GLOBAL",
            "CORPORATE",
        ),
        AMLSource(
            "OCCRP_ALEPH",
            "https://aleph.occrp.org",
            "en",
            "GLOBAL",
            "CORPORATE",
        ),
        AMLSource(
            "OPENCORPORATES",
            "https://opencorporates.com",
            "en",
            "GLOBAL",
            "CORPORATE",
        ),
        AMLSource(
            "GLEIF_LEI",
            "https://api.gleif.org/api/v1/lei-records?page[size]=25",
            "en",
            "GLOBAL",
            "CORPORATE",
        ),
        AMLSource(
            "UK_COMPANIES_HOUSE",
            "https://developer.company-information.service.gov.uk",
            "en",
            "UK",
            "CORPORATE",
        ),
    ]


def risk_sources() -> List[AMLSource]:
    return [
        AMLSource(
            "KWFIU",
            "https://www.kwfiu.gov.kw",
            "mixed",
            "MENA",
            "RISK",
        ),
        AMLSource(
            "MENAFATF",
            "https://www.menafatf.org",
            "mixed",
            "MENA",
            "GREY_LIST",
        ),
        AMLSource(
            "BASEL_AML_INDEX",
            "https://index.baselgovernance.org",
            "en",
            "GLOBAL",
            "RISK",
        ),
        AMLSource(
            "TI_CPI",
            "https://raw.githubusercontent.com/datasets/corruption-perceptions-index/master/data/cpi.csv",
            "en",
            "GLOBAL",
            "RISK",
        ),
        AMLSource(
            "FINCEN_ADVISORIES",
            "https://www.fincen.gov/resources/advisoriesbulletinsfact-sheets",
            "en",
            "US",
            "RISK",
        ),
    ]


def media_sources() -> List[AMLSource]:
    return [
        AMLSource(
            "GDELT_DOC",
            "https://api.gdeltproject.org/api/v2/doc/doc?query=%22money%20laundering%22%20OR%20sanctions%20Kuwait&mode=ArtList&format=json&maxrecords=25",
            "en",
            "GLOBAL",
            "NEWS",
        ),
        AMLSource(
            "KUNA_NEWS",
            "https://www.kuna.net.kw",
            "mixed",
            "MENA",
            "NEWS",
        ),
        AMLSource(
            "GOOGLE_NEWS_AML",
            "https://news.google.com/rss/search?q=Kuwait+OR+GCC+(fraud+OR+sanctions+OR+%22money+laundering%22)&hl=en-US&gl=US&ceid=US:en",
            "en",
            "MENA",
            "NEWS",
        ),
    ]


def all_sources() -> List[AMLSource]:
    return (
        core_sources()
        + sanctions_sources()
        + pep_wanted_sources()
        + corporate_sources()
        + risk_sources()
        + media_sources()
    )
