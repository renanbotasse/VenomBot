"""Offline tests for the debarment / enforcement list modules.

Fixtures in tests/fixtures/debarment_enforcement are cut from the real
downloads (firms, public designations) or synthetic records in the exact
real format (private individuals). Nothing here touches the network:
parsers that follow an index page get a fake ``parser_download``.
"""

from __future__ import annotations

import shutil
import unittest
from pathlib import Path
from unittest import mock

from venombot.entities import LIST_TYPES
from venombot.lists import _html_c, debarment_br, debarment_intl, debarment_us, enforcement_regulators

FX = Path(__file__).parent / "fixtures" / "debarment_enforcement"
MODULES = (debarment_intl, debarment_br, debarment_us, enforcement_regulators)


def by_key(module, key):
    return {s.key: s for s in module.SOURCES}[key]


def parse(module, key, files, fake=None):
    """Run a parser on fixture files; ``fake`` maps URL fragment -> fixture name."""
    src = by_key(module, key)
    paths = {name: FX / fn for name, fn in files.items()}

    def fake_download(url, dest, **kw):
        for frag, fixture in (fake or {}).items():
            if frag in url:
                shutil.copy(FX / fixture, dest)
                return Path(dest)
        raise AssertionError(f"unexpected download {url}")

    with mock.patch.object(module, "parser_download", fake_download):
        return list(src.parser(paths, src))


class RegistryTests(unittest.TestCase):
    def test_sources_are_well_formed(self):
        keys = []
        for mod in MODULES:
            for s in mod.SOURCES:
                keys.append(s.key)
                self.assertIn(s.list_type, LIST_TYPES, s.key)
                self.assertNotIn("core", s.groups, s.key)
                self.assertTrue({"debarment", "enforcement"} & set(s.groups), s.key)
                self.assertTrue(s.urls, s.key)
        self.assertEqual(len(keys), len(set(keys)))
        self.assertEqual(len(keys), 30)

    def test_brazil_group(self):
        for s in debarment_br.SOURCES:
            self.assertIn("br", s.groups)


class HelperTests(unittest.TestCase):
    def test_dates_follow_source_order(self):
        self.assertEqual(_html_c.to_iso("07/11/2006", "dmy"), "2006-11-07")
        self.assertEqual(_html_c.to_iso("07/11/2006", "mdy"), "2006-07-11")
        self.assertEqual(_html_c.to_iso("June\xa017, 2020", "mdy"), "2020-06-17")
        self.assertEqual(_html_c.to_iso("00000000"), "")
        self.assertEqual(_html_c.excel_date("43215"), "2018-04-25")

    def test_expired_flag(self):
        from venombot.entities import Entity
        e = Entity(source="x", source_id="1")
        _html_c.apply_term(e, "2000-01-01", "2001-01-01")
        self.assertTrue(e.extra["expired"])
        f = Entity(source="x", source_id="2")
        _html_c.apply_term(f, "2000-01-01", "2999-01-01")
        self.assertNotIn("expired", f.extra)


class IntlTests(unittest.TestCase):
    def test_adb(self):
        ents = parse(debarment_intl, "ADB_SANCTIONS", {"p00": "adb_page.json"})
        self.assertEqual(len(ents), 8)
        self.assertEqual(len({e.source_id for e in ents}), 8)
        self.assertEqual(ents[0].list_type, "DEBARMENT")
        self.assertIn("debarred_from", ents[0].extra)
        self.assertTrue(any(e.schema == "Person" for e in ents))

    def test_wb(self):
        ents = parse(debarment_intl, "WB_DEBARRED", {"main": "wb.json"})
        self.assertEqual(len(ents), 8)
        people = [e for e in ents if e.schema == "Person"]
        self.assertTrue(people)
        self.assertFalse(people[0].names[0].value.startswith("MR."))
        self.assertTrue(any(e.extra.get("permanent") for e in ents))
        self.assertTrue(any("cross-debarment" in " ".join(e.programs) for e in ents))

    def test_eib(self):
        ents = parse(debarment_intl, "EIB_EXCLUSIONS", {"main": "eib.html"})
        self.assertGreaterEqual(len(ents), 8)
        first = ents[0]
        self.assertEqual(first.names[0].value, "Ozaltin Insaat Ticaret ve Sanayi A.S")
        self.assertEqual(first.extra["debarred_from"], "2026-09-08")
        self.assertEqual(first.extra["debarred_to"], "2027-11-07")
        self.assertIn("tr", first.countries)

    def test_canada(self):
        ents = parse(debarment_intl, "CA_PSPC_INELIGIBLE_SUPPLIERS", {"main": "ca_pspc.html"})
        self.assertEqual([e.names[0].value for e in ents], ["Example Supplier One Inc.", "Example Old Supplier Ltd."])
        self.assertEqual(ents[0].extra["debarred_to"], "2030-06-17")
        self.assertTrue(ents[1].extra["expired"])

    def test_edes(self):
        ents = parse(debarment_intl, "EU_EDES", {"stub": "ca_pspc.html"}, {"edes/api": "edes.txt"})
        self.assertEqual(len(ents), 5)
        self.assertEqual(ents[0].schema, "Person")
        self.assertEqual(ents[0].names[0].value, "HAMASHA AHMED MUSTAPHA HASAN AHMAD")
        self.assertEqual(ents[0].extra["debarred_from"], "2023-12-04")
        self.assertEqual(ents[1].schema, "Organization")


class BrazilTests(unittest.TestCase):
    FAKE = {"/ceis/20261008": "ceis.zip", "/cnep/20261008": "cnep.zip", "/cepim/20261008": "cepim.zip",
            "/ceaf/20261008": "ceaf.zip"}

    def test_latest_stamp_picks_newest(self):
        html = (FX / "ceis_page.html").read_text()
        self.assertEqual(debarment_br.latest_stamp(html), "20261008")

    def _run(self, key):
        # the fake keys the zip by slug; every page fixture announces 20261008
        return parse(debarment_br, key, {"page": "ceis_page.html"}, self.FAKE)

    def test_ceis(self):
        ents = self._run("BR_CEIS")
        self.assertEqual(len(ents), 2)
        company, person = ents
        self.assertEqual(company.schema, "Organization")
        self.assertEqual(company.identifiers[0].type, "CNPJ")
        self.assertIn("CONSTRUÇÕES", company.names[0].value)  # latin-1 decoded
        self.assertEqual(company.extra["debarred_to"], "2020-02-01")
        self.assertTrue(company.extra["expired"])
        self.assertEqual(person.schema, "Person")
        self.assertEqual(person.identifiers[0].type, "CPF")
        self.assertNotIn("expired", person.extra)

    def test_cnep_fine(self):
        (e,) = self._run("BR_CNEP")
        self.assertEqual(e.extra["fine_brl"], "137260,07")
        self.assertIn("Fine (BRL): 137260,07", e.remarks)

    def test_cepim_and_ceaf(self):
        (c,) = self._run("BR_CEPIM")
        self.assertEqual(c.identifiers[0].value, "04262745000180")
        (a,) = self._run("BR_CEAF")
        self.assertEqual(a.schema, "Person")
        self.assertIn("Demissão", a.remarks)

    def test_tcu(self):
        (p,) = parse(debarment_br, "BR_TCU_INABILITADOS", {"p0": "tcu_inabilitados.json"})
        self.assertEqual(p.extra["debarred_to"], "2027-07-16")
        ents = parse(debarment_br, "BR_TCU_INIDONEOS", {"p0": "tcu_inidoneos.json"})
        self.assertEqual([e.schema for e in ents], ["Organization", "Person"])
        self.assertTrue(ents[0].extra["expired"])

    def test_bcb(self):
        ents = parse(debarment_br, "BR_BCB_INABILITADOS",
                     {"inabilitados": "bcb_inabilitados.json", "proibidos": "bcb_proibidos.json"})
        self.assertEqual(len(ents), 2)
        self.assertEqual(ents[0].list_type, "ENFORCEMENT")
        self.assertEqual(ents[0].extra["debarred_to"], "2036-01-25")


class UsTests(unittest.TestCase):
    def test_sam(self):
        ents = parse(debarment_us, "US_SAM_EXCLUSIONS", {"listing": "sam_listing.json"}, {"V2_26281.ZIP": "sam.zip"})
        self.assertEqual(len(ents), 2)
        self.assertEqual(ents[0].schema, "Person")
        self.assertEqual(ents[0].names[0].value, "Casey Q Example")
        self.assertEqual(ents[1].identifiers[0].value, "UEI12345")
        self.assertTrue(ents[1].extra["expired"])

    def test_sam_picks_newest(self):
        import json
        listing = json.loads((FX / "sam_listing.json").read_text())
        self.assertTrue(debarment_us.newest_sam_file(listing).endswith("26281.ZIP"))

    def test_leie_dedupes_and_reinstated(self):
        ents = parse(debarment_us, "US_HHS_OIG_LEIE", {"main": "leie.csv"})
        self.assertEqual(len(ents), 2)
        org, person = ents
        self.assertEqual(org.schema, "Organization")
        self.assertEqual(person.birth_dates, ["1970-02-15"])
        self.assertTrue(person.extra["reinstated"] and person.extra["expired"])
        self.assertEqual(person.identifiers[0].value, "1234567890")

    def test_omig(self):
        ents = parse(debarment_us, "US_NY_OMIG_EXCLUSIONS", {"main": "omig.xlsx"})
        self.assertEqual([e.schema for e in ents], ["Organization", "Person"])
        self.assertEqual(ents[1].extra["debarred_from"], "2009-02-03")

    def test_ddtc(self):
        ents = parse(debarment_us, "US_DDTC_DEBARRED",
                     {"statutory": "ddtc_statutory.xlsx", "administrative": "ddtc_administrative.xlsx"})
        self.assertEqual(len(ents), 3)
        self.assertEqual(ents[0].extra["debarred_from"], "2002-03-05")
        self.assertEqual(ents[1].birth_dates, ["1975-11"])
        self.assertEqual([n.value for n in ents[2].names][:2], ["Poe, Edgar", "Example Optics, S.A."])

    def test_fda(self):
        ents = parse(debarment_us, "US_FDA_DEBARMENT", {"main": "fda_debarment.html"})
        firm, person = ents
        self.assertEqual(firm.extra["debarred_to"], "2015-01-15")
        self.assertTrue(firm.extra["expired"])
        self.assertTrue(person.extra["permanent"])
        self.assertEqual(person.names[0].value, "Jane Q. Example")

    def test_fda_clinical_keeps_cleared(self):
        ents = parse(debarment_us, "US_FDA_CLINICAL_DISQUALIFIED", {"main": "fda_clinical.xls"})
        self.assertEqual(len(ents), 2)
        self.assertNotIn("expired", ents[0].extra)
        self.assertTrue(ents[1].extra["expired"])
        self.assertEqual(ents[0].list_type, "ENFORCEMENT")

    def test_finra_skips_letter_rows(self):
        (e,) = parse(debarment_us, "US_FINRA_BARRED", {"main": "finra.html"})
        self.assertEqual(e.identifiers[0].value, "1000001")

    def test_cftc_and_sec(self):
        ents = parse(debarment_us, "US_CFTC_RED_LIST", {"p00": "cftc_page.html"})
        self.assertEqual(len(ents), 10)
        self.assertEqual(ents[0].listed_on, "2017-04-25")
        ents = parse(debarment_us, "US_SEC_PAUSE", {"p00": "sec_page.html"})
        self.assertGreaterEqual(len(ents), 3)
        self.assertIn("Category:", ents[0].remarks)

    def test_sec_needs_contact_env(self):
        s = by_key(debarment_us, "US_SEC_PAUSE")
        self.assertTrue(s.needs_key)
        self.assertIn("{api_key}", s.headers["User-Agent"])

    def test_occ_and_fed(self):
        occ = parse(debarment_us, "US_OCC_ENFORCEMENT", {"main": "occ.json"})
        self.assertTrue(occ[0].extra["expired"])
        self.assertEqual(occ[1].schema, "Person")
        self.assertEqual(occ[1].linked_to, ["Sample National Bank"])
        self.assertNotIn("expired", occ[1].extra)
        fed = parse(debarment_us, "US_FED_ENFORCEMENT", {"main": "fed.csv"})
        self.assertEqual([e.schema for e in fed], ["Organization", "Person"])
        self.assertTrue(fed[1].url.startswith("https://www.federalreserve.gov/"))


class EnforcementTests(unittest.TestCase):
    ASIC = {"bd_per": "asic_persons.csv", "bd_org": "asic_orgs.tsv"}

    def test_asic_persons_dmy(self):
        ents = parse(enforcement_regulators, "AU_ASIC_BANNED_PERSONS", {"pkg": "asic_pkg.json"}, self.ASIC)
        self.assertEqual(len(ents), 2)
        self.assertEqual(ents[0].extra["debarred_from"], "1994-03-29")
        self.assertTrue(ents[0].extra["expired"])
        self.assertEqual(ents[1].extra["debarred_from"], "2030-11-07")  # DD/MM, not MM/DD
        self.assertNotIn("expired", ents[1].extra)
        self.assertIn("sam example", ents[0].names[1].value.lower())

    def test_asic_orgs_tab_delimited(self):
        (o,) = parse(enforcement_regulators, "AU_ASIC_BANNED_ORGS", {"pkg": "asic_pkg_org.json"}, self.ASIC)
        self.assertEqual(o.identifiers[0].value, "081402379")
        self.assertTrue(o.extra["expired"])

    def test_esma_strips_anchor(self):
        ents = parse(enforcement_regulators, "EU_ESMA_SANCTIONS", {"p0": "esma.json"})
        self.assertEqual(ents[0].names[0].value, "EXAMPLE BROKER, a.s.")
        self.assertEqual(ents[0].identifiers[0].type, "LEI")
        self.assertEqual(ents[0].listed_on, "2013-08-27")
        self.assertNotIn("____", ents[0].remarks)

    def test_finma_post(self):
        ents = parse(enforcement_regulators, "CH_FINMA_WARNINGS", {"stub": "ca_pspc.html"}, {"getresult": "finma.json"})
        self.assertEqual(len(ents), 4)
        self.assertEqual(ents[0].names[0].value, "10CryptoMarket")
        self.assertEqual(ents[0].listed_on, "2020-05-14")
        self.assertEqual(len(ents[3].names), 3)  # name + websites

    def test_hmrc(self):
        ents = parse(enforcement_regulators, "UK_HMRC_TAX_DEFAULTERS", {"index": "hmrc_index.json"}, {".ods": "hmrc.ods"})
        self.assertEqual([e.schema for e in ents], ["Organization", "Person"])
        self.assertEqual([n.value for n in ents[0].names], ["Example Payroll Limited", "Sample Digital Limited"])

    def test_spk(self):
        ents = parse(enforcement_regulators, "TR_SPK_TRADING_BANS", {"main": "spk.json"})
        self.assertEqual(ents[0].names[0].value, "ORNEK KISI")  # " (1)" disambiguator removed
        self.assertEqual(ents[0].extra["debarred_from"], "2026-08-21")
        self.assertEqual(ents[0].extra["debarred_to"], "2027-02-21")
        self.assertEqual(ents[1].schema, "Organization")
        self.assertTrue(ents[1].extra["expired"])

    def test_qfcra_skips_impersonation_warnings(self):
        ents = parse(enforcement_regulators, "QA_QFCRA_ENFORCEMENT", {"main": "qfcra.json"})
        names = " ".join(e.names[0].value for e in ents).lower()
        self.assertIn("experts credit solutions", names)
        self.assertNotIn("al rayan", names)


if __name__ == "__main__":
    unittest.main()
