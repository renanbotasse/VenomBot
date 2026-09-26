"""Offline sample fallback content when live fetch fails."""

from __future__ import annotations

from typing import Dict

SAMPLE_CONTENT: Dict[str, str] = {
    "OFAC_SDN": '36,"AEROCARIBBEAN AIRLINES",-0- ,"CUBA"\n8414,"ZAINAL, Akram","individual","IRAQ2"\n',
    "OFAC_ALT": '36,12,"aka","AERO-CARIBBEAN",-0-\n',
    "CBK_SANCTIONS": "Central Bank of Kuwait AML page (demo snapshot).\nقائمة العقوبات\n",
    "UN_SANCTIONS": (
        '<?xml version="1.0"?><CONSOLIDATED_LIST>'
        "<INDIVIDUAL><FIRST_NAME>AHMED</FIRST_NAME><SECOND_NAME>AL-SABAH</SECOND_NAME></INDIVIDUAL>"
        "</CONSOLIDATED_LIST>\n"
    ),
    "UN_OPENSANCTIONS": 'id,schema,name\nx,Person,"AHMED AL-SABAH"\n',
    "FATF_GREY_LIST": "FATF Jurisdictions under Increased Monitoring (Grey List)\n",
    "BBC_ARABIC_NEWS": "بي بي سي عربي - أخبار الشرق الأوسط\n",
    "BBC_MENA_ENGLISH": "BBC Middle East news feed (demo)\n",
    "ALJAZEERA_ENGLISH": "Al Jazeera Middle East sanctions update (demo)\n",
    "EU_SANCTIONS": (
        '<?xml version="1.0"?><export><sanctionEntity>'
        "<nameAlias wholeName=\"DEMO ENTITY EU\"/></sanctionEntity></export>\n"
    ),
    "UK_SANCTIONS": "Last Updated,Unique ID,Name 1\n01/01/2026,UKDEMO,DEMO SANCTIONS ENTITY\n",
    "OFAC_NON_SDN": '9640,"ABU TEIR, Mohammed","individual","NS-PLC"\n',
    "US_CSL": "US Consolidated Screening List landing page (API key required for bulk).\n",
    "CA_SEMA": '<?xml version="1.0"?><data-set><record><Country>Canada</Country></record></data-set>\n',
    "AU_DFAT": "Australia DFAT consolidated sanctions list page (demo).\n",
    "CH_SECO": '<?xml version="1.0"?><swiss-sanctions-list></swiss-sanctions-list>\n',
    "UAE_TERROR_LIST": "UAE local terrorist list portal (demo).\n",
    "KUWAIT_MOFA": "Kuwait MOFA sanctions / UNSC implementation committee (demo).\n",
    "OPENSANCTIONS_DEFAULT": (
        "id,schema,name\n"
        'os1,Person,"DEMO OPEN SANCTIONS"\n'
        "# OpenSanctions free data is typically non-commercial; check license before production use.\n"
    ),
    "OPENSANCTIONS_PEPS": 'id,schema,name\npep1,Person,"DEMO PEP"\n',
    "WIKIDATA_PEPS": '{"results":{"bindings":[{"itemLabel":{"value":"Demo GCC Official"}}]}}\n',
    "CIA_WORLD_LEADERS": "CIA World Leaders directory (demo).\n",
    "INTERPOL_RED": '{"total":0,"_embedded":{"notices":[]}}\n',
    "FBI_WANTED": '{"total":0,"items":[]}\n',
    "EUROPOL_WANTED": "Europol EU Most Wanted portal (demo).\n",
    "WB_DEBARRED": "World Bank debarred firms and individuals (demo).\n",
    "ICIJ_OFFSHORE": "ICIJ Offshore Leaks Database portal (demo). Check ICIJ terms of use.\n",
    "OCCRP_ALEPH": "OCCRP Aleph investigative data platform (API key may be required).\n",
    "OPENCORPORATES": "OpenCorporates company data (API key may be required).\n",
    "GLEIF_LEI": '{"data":[{"attributes":{"entity":{"legalName":{"name":"DEMO LEI ENTITY"}}}}]}\n',
    "UK_COMPANIES_HOUSE": "UK Companies House developer hub (API key required).\n",
    "KWFIU": "Kuwait Financial Intelligence Unit portal (demo).\n",
    "MENAFATF": "MENAFATF regional AML/CFT body (demo).\n",
    "BASEL_AML_INDEX": "Basel AML Index country risk scores (demo).\n",
    "TI_CPI": '[{"country":"Kuwait","iso3":"KWT","score":46}]\n',
    "FINCEN_ADVISORIES": "FinCEN advisories and bulletins (demo).\n",
    "GDELT_DOC": '{"articles":[{"title":"Demo sanctions article","url":"https://example.com"}]}\n',
    "KUNA_NEWS": "KUNA Kuwait News Agency (demo).\n",
    "GOOGLE_NEWS_AML": (
        '<?xml version="1.0"?><rss><channel><title>Google News AML</title>'
        "<item><title>Demo fraud investigation</title></item></channel></rss>\n"
    ),
}
