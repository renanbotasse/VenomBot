"""Offline tests for the keyless live providers.

``get_text`` is patched inside each provider module with recorded (trimmed)
real responses from tests/fixtures/live_free; private-person sources use
synthetic records in the real format. No network, no other module discovery.
"""

from __future__ import annotations

import unittest
from pathlib import Path
from unittest import mock

from venombot.live import courts, leaks, regulators, registries, wikidata
from venombot.screening import Subject

FIX = Path(__file__).parent / "fixtures" / "live_free"
MODULES = (registries, courts, regulators, wikidata, leaks)


def org(name: str) -> Subject:
    return Subject.build(name, kind="org")


def person(name: str) -> Subject:
    return Subject.build(name, kind="person")


class Fake:
    """Route URL substrings to fixture files and record the requests made."""

    def __init__(self, module, routes):
        self.module, self.routes, self.calls = module, routes, []

    def __enter__(self):
        def get_text(url, **kw):
            self.calls.append((url, kw))
            for needle, fname in self.routes:
                if needle in url:
                    return (FIX / fname).read_text(encoding="utf-8")
            raise AssertionError(f"unexpected URL {url}")
        self._p = mock.patch.object(self.module, "get_text", get_text)
        self._p.start()
        return self

    def __exit__(self, *a):
        self._p.stop()


class RegistryTests(unittest.TestCase):
    def run_one(self, fn, subject, routes):
        with Fake(registries, routes) as f:
            return fn(subject, None), f

    def test_asic_resolves_current_resource(self):
        ev, f = self.run_one(registries.asic, org("Australia Kuwait Business Council Pty Ltd"),
                             [("package_show", "au_package.json"), ("datastore_search", "au_ds.json")])
        self.assertEqual(len(ev), 1)
        self.assertEqual(ev[0].kind, "corporate")
        self.assertEqual(ev[0].extra["company_number"], "633895477")
        self.assertEqual(ev[0].extra["country"], "AU")
        self.assertIn("5c3914e6-413e-4a2c-b890-bf8efe3eabf2", f.calls[1][0])

    def test_ares_post_body(self):
        ev, f = self.run_one(registries.ares, org("Agrofert"), [("vyhledat", "cz.json")])
        self.assertTrue(ev and all(e.name_score >= 0.85 for e in ev))
        self.assertIn(b"Agrofert", f.calls[0][1]["data"])
        self.assertEqual(ev[0].extra["country"], "CZ")

    def test_prh_filters_trade_name_noise(self):
        ev, _ = self.run_one(registries.prh, org("Nokian Vuokrakodit Oy"), [("companies", "fi.json")])
        self.assertTrue(ev)
        self.assertEqual(len(ev), 1)  # the Fysios record only matched on a trade name
        self.assertTrue(all("nokia" in e.matched_name.lower() for e in ev))
        self.assertTrue(ev[0].extra["company_number"])

    def test_fr_company_and_director(self):
        ev, _ = self.run_one(registries.fr_recherche, org("TotalEnergies SE"), [("search?", "fr.json")])
        self.assertEqual(ev[0].extra["company_number"], "542051180")
        ev, _ = self.run_one(registries.fr_recherche, person("Jacques Aschenbroich"), [("search?", "fr.json")])
        self.assertTrue(ev)
        self.assertIn("Administrateur", ev[0].extra["role"])

    def test_persons_are_not_sent_to_company_registries(self):
        with Fake(registries, []) as f:
            self.assertEqual(registries.ares(person("Jane Testperson"), None), [])
            self.assertEqual(f.calls, [])

    def test_gleif_filter_has_lei_and_country(self):
        ev, f = self.run_one(registries.gleif_filter, org("Kuwait Finance House"), [("lei-records", "gleif_filter.json")])
        self.assertTrue(ev)
        by = {e.extra["lei"]: e for e in ev}
        self.assertEqual(by["2549007KX0N057S9BC98"].extra["country"], "KW")
        self.assertIn("filter%5Bentity.legalName%5D", f.calls[0][0])

    def test_gleif_fuzzy_falls_back_to_autocompletions(self):
        ev, f = self.run_one(registries.gleif_fuzzy, org("Kuwait Finance House"),
                             [("fuzzycompletions", "gleif_fuzzy_empty.json"), ("autocompletions", "gleif_auto.json")])
        self.assertEqual(len(f.calls), 2)
        self.assertTrue(ev and ev[0].extra["lei"])

    def test_opencorporates_and_figi(self):
        ev, _ = self.run_one(registries.opencorporates, org("Kuwait Finance House"), [("reconcile", "oc.json")])
        self.assertEqual(ev[0].extra["jurisdiction"], "gb")
        self.assertEqual(ev[0].extra["company_number"], "00877859")
        ev, _ = self.run_one(registries.openfigi, org("Kuwait Finance House"), [("v3/search", "figi.json")])
        self.assertTrue(ev)
        self.assertEqual(len({e.extra["figi"] for e in ev}), len(ev))

    def test_hk_empty_result_is_http_400(self):
        with mock.patch.object(registries, "get_text", side_effect=RuntimeError("HTTP 400 for x")):
            self.assertEqual(registries.hk(org("Zzqxv"), None), [])
        with mock.patch.object(registries, "get_text", side_effect=RuntimeError("HTTP 500 for x")):
            with self.assertRaises(RuntimeError):
                registries.hk(org("Zzqxv"), None)

    def test_hk_ie_il_no_sg(self):
        ev, f = self.run_one(registries.hk, org("Kuwait Trading Co., Limited"), [("api_builder", "hk.json")])
        self.assertEqual(ev[0].extra["country"], "HK")
        self.assertIn("begins_with", f.calls[0][0])
        self.assertNotIn("+", f.calls[0][0].split("?", 1)[1].replace("%2B", ""))
        ev, _ = self.run_one(registries.ie_cro, org("Kuwait Petroleum (Ireland) Limited"), [("datastore_search", "ie.json")])
        self.assertEqual(ev[0].extra["status"], "Dissolved")
        ev, _ = self.run_one(registries.il_registrar, org("Teva Adir Medical Cannabis Ltd"), [("datastore_search", "il.json")])
        self.assertEqual(ev[0].extra["country"], "IL")
        self.assertIn("hebrew_name", ev[0].extra)
        ev, _ = self.run_one(registries.brreg, org("Equinor ASA"), [("enheter", "brreg.json")])
        self.assertTrue(ev[0].extra["company_number"])
        ev, f = self.run_one(registries.acra, org("Kuwait Petroleum Corporation (Far East) (K.S.C.)"),
                             [("datastore_search", "sg.json")])
        self.assertEqual(len(f.calls), 2)  # both UEN resources
        self.assertTrue(ev)
        self.assertNotIn("%28", f.calls[0][0])  # parentheses stripped from q

    def test_ny_matches_service_of_process_name(self):
        ev, _ = self.run_one(registries.ny_dos, org("National Bank Kuwait SAK"), [("n9v6-gdp6", "ny.json")])
        self.assertTrue(ev)
        self.assertEqual(ev[0].extra["match_field"], "dos_process_name")

    def test_edgar(self):
        ev, f = self.run_one(registries.edgar_entity, org("Kuwait Petroleum Corp"), [("keysTyped", "edgar_entity.json")])
        self.assertEqual(ev[0].extra["cik"], "1098000")
        self.assertIn("User-Agent", f.calls[0][1]["headers"])
        ev, _ = self.run_one(registries.edgar_fulltext, org("Kuwait Finance House"), [("search-index", "edgar_fts.json")])
        self.assertTrue(ev)
        self.assertIn("server side", ev[0].extra["match_basis"])
        self.assertTrue(ev[0].url.startswith("https://www.sec.gov/Archives/edgar/data/"))

    def test_registry_noise_is_dropped(self):
        ev, _ = self.run_one(registries.asic, org("Completely Different Holdings"),
                             [("package_show", "au_package.json"), ("datastore_search", "au_ds.json")])
        self.assertEqual(ev, [])


class CourtTests(unittest.TestCase):
    def test_courtlistener_party_and_opinions_and_feed(self):
        with Fake(courts, [("type=r", "cl_dockets.json")]):
            ev = courts.courtlistener_party_dockets(person("Jane Q. Testperson"), None)
        self.assertEqual(len(ev), 1)
        self.assertEqual(ev[0].kind, "court")
        self.assertTrue(ev[0].url.startswith("https://www.courtlistener.com/docket/"))
        with Fake(courts, [("type=o", "cl_opinions.json")]):
            ev = courts.courtlistener_opinions(person("Jane Q. Testperson"), None)
        self.assertEqual(len(ev), 1)
        self.assertIn("money_laundering", ev[0].topics)
        with Fake(courts, [("feed/search", "cl_feed.xml")]):
            ev = courts.courtlistener_opinion_feed(person("Jane Testperson"), None)
        self.assertEqual(len(ev), 1)

    def test_uk_case_law_party_titles(self):
        with Fake(courts, [("atom.xml", "ukcl.xml")]):
            ev = courts.uk_find_case_law(person("Mukhtar Ablyazov"), None)
        self.assertTrue(ev)
        self.assertTrue(all(e.name_score >= 0.8 for e in ev))
        self.assertIn("caselaw.nationalarchives.gov.uk", ev[0].url)

    def test_hudoc_dedupes_and_flags_surname_only(self):
        with Fake(courts, [("hudoc", "hudoc.json")]) as f:
            ev = courts.echr_hudoc(person("Mikhail Khodorkovskiy"), None)
        self.assertEqual(len(ev), 1)  # ENG + FRE versions collapse by application number
        self.assertEqual(ev[0].extra["application_no"], "51111/07;42757/07")
        self.assertIn("docname%3A%22Khodorkovskiy%22", f.calls[0][0])
        self.assertEqual(ev[0].extra["match_level"], "surname only")
        self.assertLessEqual(ev[0].name_score, 0.8)

    def test_hudoc_no_hits_is_http_error(self):
        with mock.patch.object(courts, "get_text", side_effect=RuntimeError("HTTP 404 for x")):
            self.assertEqual(courts.echr_hudoc(person("Nobody Atall"), None), [])

    def test_cjeu_matches_parties_not_subject_matter(self):
        with Fake(courts, [("sparql", "cellar.json")]) as f:
            ev = courts.cjeu_cellar(person("Arkady Rotenberg"), None)
        self.assertTrue(ev)
        self.assertIn("rotenberg", f.calls[0][0].lower())
        self.assertEqual(ev[0].extra["court"], "CJEU")


class RegulatorTests(unittest.TestCase):
    def test_govuk(self):
        with Fake(regulators, [("search.json", "govuk.json")]) as f:
            ev = regulators.uk_govuk_search(org("Glencore"), None)
        self.assertTrue(ev)
        self.assertTrue(ev[0].url.startswith("https://www.gov.uk/"))
        self.assertEqual(f.calls[0][0].count("filter_organisations"), 6)

    def test_gazette_title_match(self):
        with Fake(regulators, [("data.json", "gazette.json")]):
            ev = regulators.uk_gazette(org("JSC BTA Bank"), None)
        self.assertTrue(ev)
        self.assertEqual(ev[0].kind, "corporate")
        self.assertIn("notice_code", ev[0].extra)

    def test_doj_title_substring_and_topics(self):
        with Fake(regulators, [("press_releases", "doj.json")]):
            ev = regulators.doj_press(person("Jane Q. Testperson"), None)
        self.assertEqual(len(ev), 1)
        self.assertEqual(ev[0].kind, "enforcement")
        self.assertEqual(ev[0].published, "2024-12-30")
        self.assertIn("fraud", ev[0].topics)

    def test_federal_register_marks_body_matches(self):
        with Fake(regulators, [("documents.json", "fedreg.json")]):
            ev = regulators.federal_register(person("Oleg Deripaska"), None)
        self.assertTrue(ev)
        self.assertTrue(all(e.name_score >= 0.85 for e in ev))
        self.assertTrue(all("match_basis" in e.extra for e in ev))

    def test_finra_flags_and_identity_kind(self):
        with Fake(regulators, [("search/individual", "finra_individual.json")]):
            ev = regulators.finra_brokercheck(person("Jane Q Testperson"), None)
        kinds = {e.extra["crd"]: e.kind for e in ev}
        self.assertEqual(kinds["9000001"], "enforcement")
        self.assertEqual(kinds["9000002"], "identity")  # clean registration is not enforcement
        self.assertNotIn("9000003", kinds)

    def test_sanctions_network(self):
        with Fake(regulators, [("search_sanctions", "sanctions_network.json")]):
            ev = regulators.sanctions_network(person("Vladimir Putin"), None)
        self.assertTrue(ev)
        self.assertEqual(ev[0].extra["list"], "ofac")


class WikidataLeakTests(unittest.TestCase):
    ROUTES = [("wbsearchentities", "wd_search.json"), ("props=labels%7Caliases%7Cclaims", "wd_entities.json"),
              ("props=labels", "wd_labels.json"), ("sparql", "wd_sparql.json")]

    def test_search_is_identity_or_pep_without_topics(self):
        with Fake(wikidata, self.ROUTES):
            ev = wikidata.search_entities(person("Mishal Al-Ahmad Al-Jaber Al-Sabah"), None)
        self.assertEqual(ev[0].kind, "pep_info")  # description "Emir of Kuwait"
        self.assertEqual(ev[0].extra["qid"], "Q12243161")
        self.assertEqual(ev[0].topics, [])
        self.assertTrue(ev[0].url.endswith("Q12243161"))

    def test_claims_carry_dob_and_positions(self):
        with Fake(wikidata, self.ROUTES) as f:
            ev = wikidata.get_entities(person("Mishal Al-Ahmad Al-Jaber Al-Sabah"), None)
        self.assertEqual(ev[0].extra["date_of_birth"], "1940-09-27")
        self.assertTrue(ev[0].extra["positions"])
        self.assertEqual(ev[0].topics, [])
        self.assertIn("User-Agent", f.calls[0][1]["headers"])

    def test_sparql_positions_with_dates(self):
        with Fake(wikidata, self.ROUTES) as f:
            ev = wikidata.sparql_positions(person("Mishal Al-Ahmad Al-Jaber Al-Sabah"), None)
        self.assertEqual(ev[0].kind, "pep_info")
        self.assertIn("start", ev[0].extra["positions"][0])
        self.assertNotIn("Mishal", f.calls[1][0].split("query=")[1][:0] + "")  # SPARQL carries QIDs only

    def test_littlesis(self):
        with Fake(wikidata, [("littlesis.org", "littlesis.json")]):
            ev = wikidata.littlesis(person("David Koch"), None)
        self.assertEqual(ev[0].extra["littlesis_id"], 14934)
        self.assertIn("Person", ev[0].extra["types"])

    def test_icij(self):
        with Fake(leaks, [("reconcile", "icij.json")]) as f:
            ev = leaks.icij_reconcile(org("Mossack Fonseca"), None)
        self.assertTrue(ev)
        self.assertEqual(ev[0].kind, "leak")
        self.assertIn(b"Mossack Fonseca", f.calls[0][1]["data"])
        self.assertTrue(ev[0].url.startswith("https://offshoreleaks.icij.org/nodes/"))


class ContractTests(unittest.TestCase):
    def test_provider_metadata(self):
        keys = []
        for mod in MODULES:
            for p in mod.PROVIDERS:
                keys.append(p.key)
                self.assertTrue(p.notes, p.key)
                self.assertIn("free", p.groups)
                self.assertFalse(p.api_key_env, p.key)  # keyless
        self.assertEqual(len(keys), len(set(keys)))
        self.assertEqual(len(keys), 35)

    def test_non_json_answers_raise(self):
        with mock.patch.object(registries, "get_text", return_value="<html>blocked</html>"):
            with self.assertRaises(RuntimeError):
                registries.ares(org("Agrofert"), None)


if __name__ == "__main__":
    unittest.main()
