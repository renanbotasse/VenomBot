"""Matching, parsing and screening regression tests (offline, real-format fixtures)."""

import shutil
import tempfile
import unittest
from pathlib import Path

from venombot.countries import countries_in, to_iso2
from venombot.dates import compare_dates, parse_dates
from venombot.lists.ofac import SOURCES as OFAC_SOURCES
from venombot.lists.ofac import parse_sdn
from venombot.lists.opensanctions import entity_list_type, parse_targets
from venombot.lists.un import SOURCES as UN_SOURCES
from venombot.lists.un import parse_un
from venombot.lists import ListSource
from venombot.matching import compare_names
from venombot.normalize import ascii_fold, skeleton, tokens
from venombot.screening import Subject, overall, screen
from venombot.store import EntityStore

FIX = Path(__file__).parent / "fixtures"


class NormalizeTests(unittest.TestCase):
    def test_comma_inversion(self) -> None:
        self.assertEqual(tokens("PUTIN, Vladimir Vladimirovich"), ["vladimir", "vladimirovich", "putin"])

    def test_particles_and_abd(self) -> None:
        self.assertEqual(tokens("Abd al-Rahman Al-Qaduli"), ["abdrahman", "qaduli"])
        self.assertEqual(tokens("Abdulrahman al Qaduli"), ["abdrahman", "qaduli"])

    def test_scripts(self) -> None:
        self.assertEqual(ascii_fold("Владимир Путин"), "vladimir putin")
        self.assertEqual(skeleton(tokens("الظواهري")[0]), skeleton("zawahiri"))
        self.assertEqual(skeleton("mohammed"), skeleton("muhammad"))

    def test_org_noise(self) -> None:
        self.assertEqual(tokens("ACME TRADING LLC", is_org=True), ["acme", "trading"])


class MatchingTests(unittest.TestCase):
    def test_transliteration_variants_match(self) -> None:
        self.assertGreaterEqual(compare_names("Mohammed Hussein", "Muhammad Husayn").score, 0.88)
        self.assertGreaterEqual(compare_names("Usama Bin Ladin", "Osama bin Laden").score, 0.88)

    def test_order_insensitive(self) -> None:
        self.assertGreaterEqual(compare_names("Vladimir Putin", "PUTIN, Vladimir").score, 0.99)

    def test_similar_skeleton_different_name(self) -> None:
        self.assertLess(compare_names("Vladimir Putin", "POTANIN, Vladimir Olegovich").score, 0.8)

    def test_short_tokens_not_overmatched(self) -> None:
        self.assertLess(compare_names("Kim Jong Un", "KIM, Jung Jong").score, 0.8)

    def test_single_shared_token_is_weak(self) -> None:
        self.assertLessEqual(compare_names("Vladimir Putin", "KONONOV, Vladimir P.").score, 0.6)

    def test_org_extra_words_penalised(self) -> None:
        self.assertLess(compare_names("Computing", "Rana Intelligence Computing Company", is_org=True).score, 0.8)


class DatesCountriesTests(unittest.TestCase):
    def test_parse_dates(self) -> None:
        self.assertEqual(parse_dates("DOB 07 Oct 1952; POB Leningrad"), ["1952-10-07"])
        self.assertEqual(parse_dates("circa 1960 to 1962"), ["1960", "1961", "1962"])
        self.assertIn("1951-06-19", parse_dates("1951-06-19"))

    def test_compare_dates(self) -> None:
        self.assertEqual(compare_dates("1952-10-07", ["1952-10-07"]), "exact")
        self.assertEqual(compare_dates("1952", ["1952-10-07"]), "year")
        self.assertEqual(compare_dates("1980", ["1952-10-07"]), "conflict")
        self.assertEqual(compare_dates("", ["1952"]), "unknown")

    def test_countries(self) -> None:
        self.assertEqual(to_iso2("Iran (Islamic Republic of)"), "ir")
        self.assertEqual(to_iso2("Korea, Democratic People's Republic of"), "kp")
        self.assertEqual(to_iso2("الكويت"), "kw")
        self.assertEqual(to_iso2("UAE"), "ae")
        self.assertEqual(countries_in("Iraq; Syria"), ["iq", "sy"])


class ParserTests(unittest.TestCase):
    def test_ofac_record(self) -> None:
        src = OFAC_SOURCES[0]
        ents = {e.source_id: e for e in parse_sdn(
            {"main": FIX / "ofac_sdn.csv", "alt": FIX / "ofac_alt.csv"}, src)}
        putin = ents["35096"]
        self.assertEqual(putin.schema, "Person")
        self.assertEqual(putin.birth_dates, ["1952-10-07"])
        self.assertIn("ru", putin.nationalities)
        self.assertIn("PUTIN, Vladimir", [n.value for n in putin.names])
        vessel = ents["37444"]
        self.assertEqual(vessel.schema, "Vessel")
        self.assertIn("PUTIN, Vladimir Vladimirovich", vessel.linked_to)

    def test_un_record(self) -> None:
        ents = list(parse_un({"main": FIX / "un_consolidated.xml"}, UN_SOURCES[0]))
        self.assertEqual(len(ents), 1)
        e = ents[0]
        self.assertIn("1951-06-19", e.birth_dates)
        self.assertTrue(any(n.kind == "original" for n in e.names))
        self.assertGreater(len(e.names), 5)

    def test_ftm_targets(self) -> None:
        src = ListSource("OS_TEST", "test", "GLOBAL", "PEP", {"main": ""}, parse_targets)
        ents = {e.source_id: e for e in parse_targets({"main": FIX / "ftm_targets.json"}, src)}
        self.assertEqual(set(ents), {"test-pep-1", "test-org-1"})
        pep = ents["test-pep-1"]
        self.assertEqual(pep.list_type, "PEP")
        self.assertEqual(pep.positions[0].title, "Member of the National Assembly of Kuwait")
        org = ents["test-org-1"]
        self.assertEqual(org.list_type, "SANCTIONS")
        self.assertEqual(org.listed_on, "2023-04-20")
        self.assertIn("TEST-PROGRAM", org.programs)

    def test_topic_precedence(self) -> None:
        self.assertEqual(entity_list_type(["role.pep", "sanction"], "PEP"), "SANCTIONS")
        self.assertEqual(entity_list_type(["sanction.linked"], "SANCTIONS"), "SANCTIONS_LINKED")
        self.assertEqual(entity_list_type([], "WANTED"), "WANTED")


class ScreeningTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.tmp = tempfile.mkdtemp()
        cls.store = EntityStore(Path(cls.tmp) / "t.db")
        cls.store.replace_source("US_OFAC_SDN", parse_sdn(
            {"main": FIX / "ofac_sdn.csv", "alt": FIX / "ofac_alt.csv"}, OFAC_SOURCES[0]))
        cls.store.replace_source("UN_SC_CONSOLIDATED", parse_un(
            {"main": FIX / "un_consolidated.xml"}, UN_SOURCES[0]))
        src = ListSource("OS_TEST", "test", "GLOBAL", "PEP", {"main": ""}, parse_targets)
        cls.store.replace_source("OS_TEST", parse_targets({"main": FIX / "ftm_targets.json"}, src))

    @classmethod
    def tearDownClass(cls) -> None:
        cls.store.close()
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def _hits(self, name, **kw):
        return screen(self.store, Subject.build(name, **kw))

    def test_named_person_found_on_own_record_only(self) -> None:
        hits = self._hits("Vladimir Putin")
        uids = [h.entity.uid for h in hits if h.match_class != "DISCOUNTED"]
        # The yacht "Linked To: PUTIN" and Potanin must not be reported as Putin.
        self.assertEqual(uids, ["US_OFAC_SDN:35096"])
        self.assertEqual(hits[0].match_class, "PROBABLE")
        self.assertEqual(hits[0].action, "HOLD_FOR_REVIEW")

    def test_dob_confirms_and_discounts(self) -> None:
        hit = self._hits("Vladimir Putin", dob="07/10/1952")[0]
        self.assertEqual(hit.match_class, "CONFIRMED")
        hit = self._hits("Vladimir Putin", dob="1980")[0]
        self.assertEqual(hit.match_class, "DISCOUNTED")
        self.assertEqual(overall([hit])["decision"], "NO_MATCH_IN_SCREENED_LISTS")

    def test_substring_inside_other_name_is_not_a_hit(self) -> None:
        self.assertEqual(self._hits("Computing", kind="org"), [])

    def test_spelling_and_script_variants(self) -> None:
        for name in ("Vladmir Puttin", "Владимир Путин", "Aiman Zawahry", "أيمن الظواهري"):
            hits = [h for h in self._hits(name) if h.match_class == "PROBABLE"]
            self.assertTrue(hits, name)

    def test_arabic_org_name(self) -> None:
        hits = self._hits("جمعية المثال الخيرية", kind="org")
        self.assertEqual(hits[0].entity.source_id, "test-org-1")

    def test_pep_needs_edd_not_hold(self) -> None:
        hit = self._hits("Jane Doe Example", dob="1970")[0]
        self.assertEqual(hit.severity, "MEDIUM")
        self.assertEqual(hit.action, "ENHANCED_DUE_DILIGENCE")

    def test_schema_filter(self) -> None:
        self.assertFalse([h for h in self._hits("Example Charity", kind="person")])


if __name__ == "__main__":
    unittest.main()
