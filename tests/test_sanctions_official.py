"""Offline parser tests for official government sanctions lists.

Fixtures in tests/fixtures/sanctions_official/ are cut from the real files
(sanctions designations of public figures and entities). Where a real list is
mostly private individuals (Belgian terrorism rows, Lithuanian entry bans) the
fixture is synthetic but keeps the exact format.
"""

import unittest
from pathlib import Path
from typing import Dict, List

from venombot.entities import LIST_TYPES, Entity
from venombot.lists import ListSource
from venombot.lists import sanctions_apac_us as apac
from venombot.lists import sanctions_eu as eu
from venombot.lists import sanctions_eu_national as nat
from venombot.lists.sanctions_common import countries_native, excel_date, is_non_latin

FIX = Path(__file__).parent / "fixtures" / "sanctions_official"


def run(module, key: str, filename: str) -> List[Entity]:
    """Parse a fixture with the parser of the source registered under ``key``."""
    pool = list(module.SOURCES) + list(getattr(module, "DISABLED_SOURCES", []))
    src = next(s for s in pool if s.key == key)
    return list(src.parser({"main": FIX / filename}, src))


def by_id(entities: List[Entity]) -> Dict[str, Entity]:
    return {e.source_id: e for e in entities}


def names(ent: Entity, kind: str = "") -> List[str]:
    return [n.value for n in ent.names if not kind or n.kind == kind]


class RegistryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.sources: List[ListSource] = eu.SOURCES + nat.SOURCES + apac.SOURCES

    def test_keys_unique_and_valid(self) -> None:
        keys = [s.key for s in self.sources]
        self.assertEqual(len(keys), len(set(keys)))
        for s in self.sources:
            self.assertIn(s.list_type, LIST_TYPES, s.key)
            self.assertIn("sanctions", s.groups, s.key)
            self.assertTrue(s.urls and all(u.startswith("https://") for u in s.urls.values()), s.key)

    def test_core_only_on_official_lists(self) -> None:
        core = {s.key for s in self.sources if "core" in s.groups}
        self.assertEqual(
            core,
            {"EU_FSF_XML_1_1", "UK_FCDO_SANCTIONS_XML", "CH_SECO", "UN_SC_CONSOLIDATED_AR", "CA_SEMA_JVCFOR",
             "AU_DFAT_CONSOLIDATED_XLSX", "NZ_MFAT_RUSSIA_REGISTER", "JP_MOF_ASSET_FREEZE_CSV", "US_CSL_DOWNLOAD"},
        )

    def test_no_overlap_with_ofac_keys(self) -> None:
        self.assertFalse({s.key for s in self.sources if s.key.startswith(("US_OFAC", "OFAC"))})

    def test_all_sources_have_fixture_coverage(self) -> None:
        self.assertEqual(len(self.sources), 21)


class HelperTests(unittest.TestCase):
    def test_excel_date(self) -> None:
        self.assertEqual(excel_date("44861"), "2022-10-27")
        self.assertEqual(excel_date("abc"), "")
        self.assertEqual(excel_date(""), "")

    def test_native_countries(self) -> None:
        self.assertEqual(countries_native("RUSSIE; ARMÉNIE"), ["ru", "am"])
        self.assertEqual(countries_native("JORDANIE (présumée)"), ["jo"])
        self.assertEqual(countries_native("Ruská federace "), ["ru"])
        self.assertEqual(countries_native("Federacja Rosyjska"), ["ru"])

    def test_non_latin(self) -> None:
        self.assertTrue(is_non_latin("Владимир"))
        self.assertTrue(is_non_latin("محمد"))
        self.assertFalse(is_non_latin("Václav Dvořák"))
        self.assertFalse(is_non_latin("Nguyễn"))


class EuFsfTests(unittest.TestCase):
    def setUp(self) -> None:
        self.ents = by_id(run(eu, "EU_FSF_XML_1_1", "eu_fsf.xml"))

    def test_count_and_fields(self) -> None:
        self.assertEqual(len(self.ents), 4)
        saddam = self.ents["13"]
        self.assertEqual(saddam.schema, "Person")
        self.assertEqual(saddam.caption, "Saddam Hussein Al-Tikriti")
        self.assertIn("1937-04-28", saddam.birth_dates)
        self.assertEqual(saddam.nationalities, ["iq"])
        self.assertEqual(saddam.programs, ["IRQ"])
        self.assertEqual(saddam.listed_on, "2003-07-07")
        self.assertEqual(saddam.remarks, "UNSC RESOLUTION 1483")
        self.assertEqual(saddam.gender, "male")
        self.assertEqual(saddam.extra["eu_reference"], "EU.27.28")

    def test_original_script_and_identifiers(self) -> None:
        minin = self.ents["124966"]
        self.assertIn("Алексей Валерьевич МИНИН", names(minin, "original"))
        self.assertEqual(minin.names[0].kind, "primary")
        self.assertTrue(any(i.type.lower().startswith("national pas") and i.value == "120017582" for i in minin.identifiers))
        self.assertEqual(minin.programs, ["CYB"])

    def test_enterprise_and_un_reference(self) -> None:
        org = self.ents["117789"]
        self.assertEqual(org.schema, "Organization")
        self.assertTrue(any(i.value == "1157746088170" for i in org.identifiers))
        dorda = self.ents["6106"]
        self.assertTrue(any(i.type == "UN reference number" and i.value == "LYi.006" for i in dorda.identifiers))
        self.assertEqual(dorda.listed_on, "2011-02-26")


class UkFcdoTests(unittest.TestCase):
    def setUp(self) -> None:
        self.ents = by_id(run(eu, "UK_FCDO_SANCTIONS_XML", "uk_fcdo.xml"))

    def test_entity_with_non_latin_name(self) -> None:
        sber = self.ents["RUS0256"]
        self.assertEqual(sber.schema, "Organization")
        self.assertEqual(sber.caption, "PJSC Sberbank (Public Joint-Stock Company Sberbank)")
        self.assertIn("ПАО Сбербанк", names(sber, "original"))
        self.assertIn("Sberbank", names(sber, "alias"))
        self.assertEqual(sber.listed_on, "2022-03-01")
        self.assertTrue(any(i.value == "1027700132195" for i in sber.identifiers))

    def test_individual(self) -> None:
        p = self.ents["AFG0014"]
        self.assertEqual(p.schema, "Person")
        self.assertEqual(p.nationalities, ["af"])
        self.assertTrue(p.birth_dates)
        self.assertTrue(any(i.type == "Passport" for i in p.identifiers))
        self.assertTrue(any(i.type == "UN reference number" for i in p.identifiers))
        self.assertEqual(p.extra["ofsi_group_id"].isdigit(), True)

    def test_ship(self) -> None:
        ship = self.ents["LIB0079"]
        self.assertEqual(ship.schema, "Vessel")
        self.assertIn("AVAX", names(ship))
        self.assertTrue(any(i.type == "IMO" and i.value == "9058713" for i in ship.identifiers))
        self.assertEqual(ship.countries, ["cm"])


class SwissTests(unittest.TestCase):
    def setUp(self) -> None:
        self.ents = by_id(run(eu, "CH_SECO", "ch_seco.xml"))

    def test_delisted_target_skipped(self) -> None:
        self.assertEqual(len(self.ents), 3)
        self.assertEqual({e.schema for e in self.ents.values()}, {"Person", "Organization", "Vessel"})

    def test_person_details_resolved_from_late_place_table(self) -> None:
        p = self.ents["85502"]
        self.assertEqual(p.birth_dates, ["1965-04-23"])
        self.assertIn("by", p.nationalities)
        self.assertTrue(p.birth_places)
        self.assertIn("ru", p.countries)
        self.assertTrue(any(i.type == "Passport" for i in p.identifiers))
        self.assertEqual(p.programs, ["Belarus"])
        self.assertEqual(p.listed_on, "2024-12-24")

    def test_spelling_variants_and_vessel(self) -> None:
        org = self.ents["44348"]
        self.assertTrue(any(is_non_latin(n.value) for n in org.names))
        ship = self.ents["34461"]
        self.assertTrue(any(i.type == "IMO" and i.value == "8405270" for i in ship.identifiers))
        self.assertEqual(ship.programs, ["North Korea"])


class UnArabicTests(unittest.TestCase):
    def test_arabic_names(self) -> None:
        ents = by_id(run(eu, "UN_SC_CONSOLIDATED_AR", "un_ar.xml"))
        self.assertEqual(set(ents), {"IQi.025", "CDi.002", "CDe.001"})
        aziz = ents["IQi.025"]
        self.assertEqual(aziz.names[0].lang, "ar")
        self.assertTrue(is_non_latin(aziz.caption))
        self.assertEqual(aziz.extra["english_edition"], "UN_SC_CONSOLIDATED")
        self.assertEqual(aziz.gender, "")
        self.assertEqual(ents["CDe.001"].schema, "Organization")


class UkHmtTests(unittest.TestCase):
    def test_rows(self) -> None:
        ents = by_id(run(eu, "UK_HMT_RUSSIA_INVESTMENT_BANS", "uk_hmt.json"))
        self.assertEqual(len(ents), 4)
        oboronprom = ents["13120"]
        self.assertEqual(oboronprom.caption, "OPK OBORONPROM")
        self.assertNotIn("fcdo_unique_id", oboronprom.extra)
        self.assertEqual(ents["13119"].extra["fcdo_unique_id"], "RUS2383")
        self.assertEqual(oboronprom.listed_on, "2026-02-24")


class FranceTests(unittest.TestCase):
    def test_register(self) -> None:
        ents = by_id(run(nat, "FR_DGTRESOR_GELS_AVOIRS_JSON", "fr_gels.json"))
        self.assertEqual(len(ents), 3)
        p = ents["4240"]
        self.assertEqual(p.caption, "Grigory Vladimirovich SHILKIN")
        self.assertIn("Григорий Владимирович ШИЛКИН", names(p, "original"))
        self.assertEqual(p.birth_dates, ["1976-10-20"])
        self.assertEqual(p.listed_on, "2022-02-25")
        org = ents["7805"]
        self.assertEqual(org.schema, "Organization")
        self.assertEqual(org.countries, ["ru"])
        self.assertTrue(any(i.value == "1023202741781" for i in org.identifiers))
        ship = ents["2218"]
        self.assertEqual(ship.schema, "Vessel")
        self.assertTrue(any(i.type == "IMO" for i in ship.identifiers))


class BelgiumTests(unittest.TestCase):
    def test_only_national_rows(self) -> None:
        ents = run(nat, "BE_FPS_FINANCE_CONSOLIDATED_CSV", "be_fps.csv")
        self.assertEqual(len(ents), 2)  # the TAQA (EU) row is skipped
        a, b = ents
        self.assertEqual(a.birth_dates, ["1987-02-28"])  # two-digit birth year pivot
        self.assertEqual(a.listed_on, "2026-06-08")
        self.assertEqual(b.birth_dates, ["1972-12-14"])
        self.assertEqual(b.listed_on, "2017-10-12")
        self.assertEqual(b.countries, ["be"])
        self.assertEqual(a.identifiers[0].type, "NRN")
        self.assertNotEqual(a.source_id, b.source_id)


class MonacoTests(unittest.TestCase):
    def test_withdrawn_skipped(self) -> None:
        ents = by_id(run(nat, "MC_FUND_FREEZES_JSON", "mc_freezes.json"))
        self.assertEqual(set(ents), {"99473", "107056"})
        org = ents["99473"]
        self.assertIn("JANNAT OSHIKLARI", names(org))
        self.assertEqual(org.listed_on, "2022-03-07")
        p = ents["107056"]
        self.assertEqual(p.caption, "Olga Alekseevna BELYAVTSEVA")
        self.assertEqual(p.birth_dates, ["1969-10-25"])
        self.assertEqual(p.nationalities, ["ru"])


class LithuaniaTests(unittest.TestCase):
    def test_expired_dropped_and_grounds_split(self) -> None:
        ents = run(nat, "LT_MIGRACIJA_MAGNITSKY_JSON", "lt_bans.json")
        self.assertEqual(len(ents), 2)
        magn, sec = ents
        self.assertEqual(magn.list_type, "SANCTIONS")
        self.assertEqual(sec.list_type, "ENFORCEMENT")
        self.assertEqual(magn.nationalities, ["ru"])
        self.assertEqual(magn.gender, "male")
        self.assertEqual(magn.birth_dates, ["1990-06-01"])


class LatviaTests(unittest.TestCase):
    def test_links(self) -> None:
        ents = by_id(run(nat, "LV_UR_SANCTIONS_RISK", "lv_sanctions_risk.csv"))
        self.assertEqual(set(ents), {"1", "2", "43", "51"})
        for e in ents.values():
            self.assertEqual(e.list_type, "SANCTIONS_LINKED")
        self.assertIn("L.V.K.", ents["1"].linked_to[0])
        self.assertEqual(ents["43"].identifiers[0].value, "40103651284")
        self.assertEqual(ents["43"].schema, "Organization")


class CzechTests(unittest.TestCase):
    def test_cancelled_skipped_and_variants_aligned(self) -> None:
        ents = run(nat, "CZ_MZV_NATIONAL_SANCTIONS_CSV", "cz_national.csv")
        self.assertEqual(len(ents), 2)
        p = next(e for e in ents if e.schema == "Person")
        self.assertEqual(p.birth_dates, ["1946-11-20"])
        self.assertEqual(p.nationalities, ["ru"])
        self.assertIn("Владимир Михайлович ГУНДЯЕВ", names(p, "original"))
        self.assertEqual(p.listed_on, "2023-04-26")


class PolandTests(unittest.TestCase):
    def test_sheets(self) -> None:
        ents = run(nat, "PL_MSWIA_SANCTIONS_XLSX", "pl_mswia.xlsx")
        self.assertEqual(len(ents), 4)  # one delisted person was dropped
        alaudinov = next(e for e in ents if e.caption.startswith("ALAUDINOV"))
        self.assertEqual(alaudinov.birth_dates, ["1973-10-05"])  # Polish month name
        self.assertIn("Apti Aronovich ALAUDINOV", names(alaudinov))
        self.assertEqual(alaudinov.listed_on, "2022-10-27")
        nick = next(e for e in ents if e.caption.startswith("NIECZAJEW"))
        self.assertIn("NECHAYEV Alexy Gennadyevich", names(nick))
        corp = next(e for e in ents if e.schema == "Organization" and e.identifiers)
        self.assertEqual({i.type for i in corp.identifiers}, {"KRS", "NIP"})
        self.assertEqual(corp.countries, ["pl"])

    def test_polish_dates(self) -> None:
        self.assertEqual(nat.pl_dates("urodzony 5 października 1973 r."), ["1973-10-05"])
        self.assertEqual(nat.pl_dates("urodzony 26.05.1974 r."), ["1974-05-26"])


class CanadaTests(unittest.TestCase):
    def test_records(self) -> None:
        ents = by_id(run(apac, "CA_SEMA_JVCFOR", "ca_sema.xml"))
        self.assertEqual(len(ents), 5)
        person = ents["belarus-1-part-1-99"]
        self.assertEqual(person.schema, "Person")
        self.assertTrue(names(person, "original"))
        self.assertFalse(any(n.startswith("Belarusian:") for n in names(person)))
        ship = ents["russia-1-1-1"]
        self.assertEqual(ship.schema, "Vessel")
        self.assertEqual(ship.identifiers[0].value, "7612448")
        self.assertEqual(ship.birth_dates, [])  # build year is not a birth date
        quirk = ents["iran-1-part-2-116"]
        self.assertIn("مجتبی خامنه‌ای", names(quirk, "original"))
        magn = next(e for k, e in ents.items() if k.startswith("justice-for-victims"))
        self.assertEqual(magn.birth_dates, ["1964-03"])


class AustraliaTests(unittest.TestCase):
    def test_grouped_rows(self) -> None:
        ents = by_id(run(apac, "AU_DFAT_CONSOLIDATED_XLSX", "au_dfat.xlsx"))
        self.assertEqual(set(ents), {"2", "3", "155", "8227"})
        akhund = ents["2"]
        self.assertEqual(akhund.caption, "MOHAMMAD HASSAN AKHUND")
        self.assertIn("محمد حسن أخوند", names(akhund, "original"))
        self.assertEqual(akhund.nationalities, ["af"])
        self.assertEqual(akhund.listed_on, "2001-01-25")
        self.assertTrue(any(i.value == "P04581926" for i in akhund.identifiers))
        self.assertIn("Targeted Financial Sanction", akhund.extra["measures"])
        jan = ents["3"]
        self.assertIn("A. Kabir", names(jan))
        self.assertEqual(ents["8227"].schema, "Vessel")


class NewZealandTests(unittest.TestCase):
    def test_register_and_ships(self) -> None:
        ents = by_id(run(apac, "NZ_MFAT_RUSSIA_REGISTER", "nz_mfat.xlsx"))
        self.assertEqual(set(ents), {"IND-1", "IND-56", "ENT-1", "BAN-16", "SHP-1", "SHP-2"})  # asset row skipped
        putin = ents["IND-1"]
        self.assertEqual(putin.caption, "Vladimir Vladimirovich Putin")
        self.assertEqual(putin.birth_dates, ["1952-10-07"])  # Excel serial
        self.assertEqual(putin.listed_on, "2022-03-18")
        self.assertEqual(ents["IND-56"].nationalities, ["ru", "fi"])
        self.assertEqual(ents["SHP-1"].identifiers[0].value, "9288693")
        self.assertIn("Mirador", names(ents["SHP-1"]))


class JapanTests(unittest.TestCase):
    def test_asset_freeze(self) -> None:
        ents = by_id(run(apac, "JP_MOF_ASSET_FREEZE_CSV", "jp_asset_freeze.csv"))
        self.assertEqual(len(ents), 3)
        akhund = ents["002-000002"]
        self.assertEqual(akhund.caption, "MOHAMMAD HASSAN AKHUND")
        self.assertIn("محمد حسن آخوند", names(akhund, "original"))
        self.assertTrue(any(n.lang == "ja" for n in akhund.names))
        self.assertIn("1955", akhund.birth_dates)
        self.assertEqual(akhund.listed_on, "2001-09-22")
        self.assertTrue(any(i.value == "TAi.002" for i in akhund.identifiers))
        self.assertEqual(ents["002-000166"].schema, "Organization")
        putin = ents["029-000001"]
        self.assertEqual(putin.birth_dates, ["1952-10-07"])

    def test_vessels(self) -> None:
        ents = run(apac, "JP_MOF_EXPORT_BAN_VESSELS", "jp_vessels.csv")
        self.assertEqual([e.source_id for e in ents], ["9179842", "8517839", "9339337"])
        self.assertEqual(ents[0].caption, "IMO 9179842")
        self.assertEqual(ents[0].listed_on, "2026-10-02")


class TaiwanTests(unittest.TestCase):
    def test_entries(self) -> None:
        ents = by_id(run(apac, "TW_SHTC_ENTITY_LIST", "tw_shtc.csv"))
        self.assertEqual(len(ents), 4)
        self.assertEqual(ents["1"].list_type, "EXPORT_CONTROL")
        self.assertEqual(ents["1"].schema, "Unknown")
        self.assertEqual(ents["8"].schema, "Person")
        self.assertEqual(ents["8"].countries, ["af"])
        self.assertIn("PKK", names(ents["3"]))
        self.assertEqual(len(ents["620"].identifiers), 2)


class SouthAfricaTests(unittest.TestCase):
    def test_dataset(self) -> None:
        ents = by_id(run(apac, "ZA_FIC_TFS_LIST", "za_fic.xml"))
        self.assertEqual(set(ents), {"QDi.430", "YEi.010", "QDe.144"})
        p = ents["QDi.430"]
        self.assertEqual(p.birth_dates, ["1967-07-04"])
        self.assertEqual(p.nationalities, ["tt"])
        self.assertEqual({i.value for i in p.identifiers}, {"19670704052", "420985453", "TB162181"})
        self.assertEqual([n.kind for n in ents["YEi.010"].names[1:]], ["weak"] * 3)
        self.assertEqual(ents["QDe.144"].schema, "Organization")

    def test_html_page_is_rejected(self) -> None:
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            f = Path(tmp) / "page.html"
            f.write_text("<html><body>download page</body></html>", encoding="utf-8")
            src = apac.DISABLED_SOURCES[0]
            with self.assertRaises(ValueError):
                list(src.parser({"main": f}, src))

    def test_za_registered_with_post(self) -> None:
        za = {s.key: s for s in apac.SOURCES}["ZA_FIC_TFS_LIST"]
        self.assertEqual(za.post_data, b"")


class ChinaTests(unittest.TestCase):
    def test_counter_sanctions(self) -> None:
        ents = run(apac, "CN_COUNTER_SANCTIONS_RESEARCH", "cn_counter.csv")
        self.assertEqual(len(ents), 3)  # expired entry dropped, duplicate QID merged
        for e in ents:
            self.assertEqual(e.list_type, "COUNTER_SANCTIONS")
        rubio = next(e for e in ents if e.source_id == "Q324546")
        self.assertIn("鲁比欧", names(rubio, "original"))
        self.assertEqual(rubio.nationalities, ["us"])
        self.assertEqual(rubio.listed_on, "2020-07-13")
        self.assertTrue(any(i.type == "Wikidata" for i in rubio.identifiers))


class UsCslTests(unittest.TestCase):
    def test_non_treasury_only(self) -> None:
        ents = run(apac, "US_CSL_DOWNLOAD", "us_csl.csv")
        # Entity List x2, ITAR x1, ACN x1; the SDN row and the expired DPL row are skipped.
        self.assertEqual(len(ents), 4)
        progs = [e.programs[0] for e in ents]
        self.assertEqual(progs.count("BIS Entity List"), 2)
        self.assertIn("State Dept ITAR Debarred", progs)
        acn = next(e for e in ents if e.programs[0].startswith("State Dept Nonprolif"))
        self.assertEqual(acn.list_type, "SANCTIONS")
        el = next(e for e in ents if e.programs[0] == "BIS Entity List" and e.countries)
        self.assertEqual(el.list_type, "EXPORT_CONTROL")
        self.assertTrue(el.listed_on)
        self.assertNotIn("AEROCARIBBEAN AIRLINES", [e.caption for e in ents])


if __name__ == "__main__":
    unittest.main()
