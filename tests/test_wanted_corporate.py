"""Wanted-list and corporate/leaks parsers (offline).

Wanted-person fixtures are synthetic (private individuals) in the layouts
described by the discovery notes; CA/IRS/GLEIF/FinCEN fixtures are real rows
of public entities; ICIJ/Companies House/Estonia/charity fixtures are
synthetic rows in the real column layout.
"""

import tempfile
import unittest
import zipfile
from pathlib import Path

from venombot.lists import corporate_registries as cr
from venombot.lists import leaks_icij as li
from venombot.lists import wanted as w

FIX = Path(__file__).parent / "fixtures" / "wanted_corporate"


def src(mod, key):
    return next(s for s in mod.SOURCES if s.key == key)


def run(mod, key, **paths):
    s = src(mod, key)
    return list(s.parser({k: FIX / v for k, v in paths.items()}, s))


class WantedTests(unittest.TestCase):
    def test_fbi_keeps_only_wanted_persons(self):
        ents = run(w, "FBI_WANTED", p01="fbi_p01.json")
        self.assertEqual(len(ents), 1)
        e = ents[0]
        self.assertEqual(e.list_type, "WANTED")
        self.assertIn("Johnny Test", [n.value for n in e.names])
        self.assertEqual(e.nationalities, ["br"])
        self.assertTrue(e.birth_dates)

    def test_fbi_pages_registered(self):
        self.assertEqual(len(src(w, "FBI_WANTED").urls), w.FBI_PAGES)

    def test_europol_skips_arrested_and_flips_name(self):
        ents = run(w, "EUROPOL_WANTED", main="europol.html")
        self.assertEqual([e.source_id for e in ents], ["9001"])
        self.assertEqual(ents[0].caption, "Alfa TESTSON")
        self.assertIn("se", ents[0].countries)

    def test_icac(self):
        ents = run(w, "HK_ICAC_WANTED", main="icac.html")
        self.assertEqual(sorted(e.source_id for e in ents), ["901", "902"])

    def test_hhs(self):
        ents = run(w, "US_HHS_OIG_FUGITIVES", main="hhs.html")
        self.assertEqual([e.caption for e in ents], ["Jane Q. Testperson", "Sample Person Two"])

    def test_ice_full_name_from_alt(self):
        ents = run(w, "US_ICE_MOST_WANTED", main="ice.html")
        self.assertEqual([e.caption for e in ents], ["Alfa Bravo Testcarias"])
        self.assertIn("Narcotics", ents[0].programs)

    def test_usss_ignores_contact(self):
        ents = run(w, "US_SECRET_SERVICE_WANTED", main="usss.html")
        self.assertEqual(len(ents), 2)

    def test_saps(self):
        ents = run(w, "ZA_SAPS_WANTED", main="saps.html")
        self.assertEqual(ents[0].caption, "Alfonso Testhuis")
        self.assertEqual(ents[0].source_id, "90001")


class CorporateTests(unittest.TestCase):
    def test_psc_filters_and_links(self):
        ents = run(cr, "UK_CH_PSC_SNAPSHOT", main="psc.jsonl")
        self.assertEqual(len(ents), 2)
        person = ents[0]
        self.assertEqual(person.schema, "Person")
        self.assertEqual(person.linked_to, ["00000001"])
        self.assertEqual(person.birth_dates, ["1975-03"])
        self.assertEqual(person.nationalities, ["gb"])
        self.assertEqual(ents[1].schema, "Organization")
        self.assertEqual(person.list_type, "CORPORATE")
        self.assertIn("not wrongdoing", person.remarks)

    def test_part_helpers(self):
        p2 = cr.psc_part_source(2, date="2026-10-08")
        self.assertTrue(p2.urls["main"].endswith("psc-snapshot-2026-10-08_2of33.zip"))
        self.assertNotEqual(p2.key, "UK_CH_PSC_SNAPSHOT")
        b = cr.basic_company_part_source(3, month="2026-10-01")
        self.assertIn("part3_7", b.urls["main"])

    def test_basic_company_active_only(self):
        ents = run(cr, "UK_CH_BASIC_COMPANY_DATA", main="basic_company.csv")
        self.assertEqual(len(ents), 1)
        self.assertEqual(ents[0].identifiers[0].value, "00000001")
        self.assertIn("OLD EXAMPLE LTD", [n.value for n in ents[0].names])

    def test_gleif(self):
        ents = run(cr, "GLEIF_GOLDEN_COPY_RR", main="gleif_rr.csv")
        self.assertTrue(ents)
        self.assertTrue(all(e.linked_to for e in ents))
        self.assertEqual(ents[0].identifiers[0].type, "LEI")

    def test_charity_and_trustees(self):
        ents = run(cr, "UK_CHARITY_COMMISSION_EXTRACT", charity="charity.txt", trustees="charity_trustee.txt")
        self.assertEqual([e.caption for e in ents], ["EXAMPLE RELIEF FUND", "Test Trustee"])
        self.assertEqual(ents[1].linked_to, ["EXAMPLE RELIEF FUND"])

    def test_estonia_streaming_decoder(self):
        ents = run(cr, "EE_ARIREGISTER_BENEFICIAL_OWNERS", main="ee_bo.json")
        self.assertEqual([e.caption for e in ents], ["Test Kasutaja", "Sample Beneficiary"])
        self.assertEqual(ents[0].birth_dates, ["1980-03-12"])
        self.assertIn("lv", ents[0].countries)

    def test_json_array_small_chunks(self):
        import io
        data = '[{"a": 1}, {"b": "x,y"} ,\n{"c": [1,2]}]'
        self.assertEqual(len(list(cr._iter_json_array(io.StringIO(data), chunk=3))), 3)

    def test_ca_and_irs(self):
        ca = run(cr, "CA_FEDERAL_CORPORATIONS", main="ca_cbca.csv")
        self.assertEqual(ca[0].caption, "MINDANGLER CAPITAL INC.")
        self.assertEqual(ca[0].identifiers[0].value, "8660115")
        irs = run(cr, "US_IRS_EO_BMF", main="irs_eo1.csv")
        self.assertEqual(irs[0].identifiers[0].value, "000019818")


class LeaksTests(unittest.TestCase):
    def _zip(self, tmp, members):
        p = Path(tmp) / "x.zip"
        with zipfile.ZipFile(p, "w") as z:
            for arc, fx in members.items():
                z.write(FIX / fx, arc)
            z.writestr("__MACOSX/._junk.csv", "x")
        return p

    def test_offshore_leaks_links(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = self._zip(tmp, {"nodes-entities.csv": "icij_entities.csv", "nodes-officers.csv": "icij_officers.csv",
                                "nodes-intermediaries.csv": "icij_intermediaries.csv",
                                "relationships.csv": "icij_relationships.csv"})
            s = src(li, "ICIJ_OFFSHORE_LEAKS_CSV")
            ents = {e.source_id: e for e in s.parser({"main": p}, s)}
        self.assertEqual(set(ents), {"10000001", "12000001", "11000001"})
        self.assertEqual(ents["12000001"].linked_to, ["SAMPLE OFFSHORE HOLDINGS LTD."])
        self.assertEqual(sorted(ents["10000001"].linked_to), ["SAMPLE FIDUCIARY SERVICES", "TEST NOMINEE EXAMPLE"])
        self.assertEqual(ents["12000001"].countries, ["kr"])
        self.assertEqual(ents["12000001"].list_type, "CORPORATE")
        self.assertIn("not evidence of wrongdoing", ents["10000001"].remarks)

    def test_fincen(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = self._zip(tmp, {"download_bank_connections.csv": "fincen_bank_connections.csv",
                                "download_transactions_map.csv": "fincen_transactions_map.csv"})
            s = src(li, "ICIJ_FINCEN_FILES")
            ents = list(s.parser({"main": p}, s))
        names = {e.caption for e in ents}
        self.assertIn("Standard Chartered Plc", names)
        self.assertIn("Habib Metropolitan Bank Limited", names)
        std = next(e for e in ents if e.caption == "Standard Chartered Plc")
        self.assertIn("Habib Metropolitan Bank Limited", std.linked_to)


if __name__ == "__main__":
    unittest.main()
