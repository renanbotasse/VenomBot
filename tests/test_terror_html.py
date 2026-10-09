"""Offline tests for the HTML-only terrorism/designation list parsers.

Fixtures in tests/fixtures/terror_html are cut from the real pages (observed
2026-10-08); the Russian one is synthetic because that list names private
individuals.
"""

from __future__ import annotations

import unittest
from pathlib import Path
from unittest import mock

from venombot.lists import _html
from venombot.lists import terror_html as T

FX = Path(__file__).parent / "fixtures" / "terror_html"
BY_KEY = {s.key: s for s in T.SOURCES}


def run(key, **files):
    src = BY_KEY[key]
    return list(src.parser({k: FX / v for k, v in files.items()}, src))


def run_text(key, tmp_text, name="main"):
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "page.html"
        p.write_text(tmp_text, encoding="utf-8")
        src = BY_KEY[key]
        return list(src.parser({name: p}, src))


class HtmlHelpers(unittest.TestCase):
    def test_table_rows_and_breaks(self):
        html = "<table><tr><th>A</th></tr><tr><td><p>x</p><p>y z</p></td></tr></table>"
        self.assertEqual(_html.table_rows(html)[0], [["A"], ["x y z"]])
        self.assertIn(_html.BREAK, _html.table_rows(html, keep_breaks=True)[0][1][0])

    def test_text_lines_skips_script(self):
        lines = _html.text_lines("<script>var a=1</script><li>One</li><li>Two</li>")
        self.assertEqual([t for _, t in lines], ["One", "Two"])

    def test_links(self):
        self.assertEqual(_html.links('<a href="/x">Go <b>now</b></a>'), [("/x", "Go now")])


class Registry(unittest.TestCase):
    def test_keys_groups_and_headers(self):
        self.assertEqual(len(T.SOURCES), 13)
        for s in T.SOURCES:
            self.assertIn("html", s.groups)
            self.assertNotIn("core", s.groups)
            self.assertNotIn("VenomBot", s.headers["User-Agent"])
        self.assertEqual(BY_KEY["EE_MFA_NATIONAL_SANCTIONS"].list_type, "SANCTIONS")
        self.assertEqual(BY_KEY["US_STATE_CUBA_RESTRICTED_LIST"].list_type, "SANCTIONS")
        self.assertIn("state-list", BY_KEY["RU_ROSFINMONITORING_TERRORIST_EXTREMIST_LIST"].groups)


class Parsers(unittest.TestCase):
    def test_au(self):
        ents = run("AU_LISTED_TERRORIST_ORGS", main="au.html")
        self.assertEqual(len(ents), 15)
        asg = ents[0]
        self.assertEqual(asg.caption, "Abu Sayyaf Group")
        self.assertIn("ASG", [n.value for n in asg.names])
        self.assertEqual(asg.listed_on, "2002-11-14")
        self.assertEqual(asg.schema, "Organization")

    def test_in_individuals_aliases_and_glued_names(self):
        ents = run("IN_MHA_UAPA_INDIVIDUAL_TERRORISTS", main="in_individuals.html")
        self.assertEqual(len(ents), 25)
        azhar = ents[0]
        names = [n.value for n in azhar.names]
        self.assertEqual(names[0], "Maulana Masood Azhar")
        self.assertIn("Maulana Mohammad Masood Azhar Alvi", names)  # repaired glued name
        self.assertEqual(azhar.schema, "Person")
        self.assertFalse(any(n.endswith(".") for n in names))

    def test_in_orgs_and_unlawful(self):
        orgs = run("IN_MHA_UAPA_TERRORIST_ORGANISATIONS", main="in_orgs.html")
        self.assertEqual(orgs[0].caption, "Babbar Khalsa International")
        unl = run("IN_MHA_UNLAWFUL_ASSOCIATIONS", main="in_unlawful.html")
        simi = unl[0]
        self.assertEqual(simi.caption, "Student’s Islamic Movement of India")
        self.assertIn("SIMI", [n.value for n in simi.names])
        self.assertEqual(len({e.source_id for e in unl}), len(unl))

    def test_ng_individuals_and_entities(self):
        ents = run("NG_NIGSAC_SANCTIONS_LIST", main="ng.html")
        persons = [e for e in ents if e.schema == "Person"]
        orgs = [e for e in ents if e.schema == "Organization"]
        self.assertTrue(persons and orgs)
        self.assertEqual(persons[0].source_id, "NLISWi.24")
        self.assertEqual(persons[0].listed_on, "2026-06-18")  # M/D/YYYY handled
        self.assertEqual(persons[0].identifiers[0].type, "nigsac-ref")

    def test_ng_missing_reference_and_placeholder_date(self):
        html = ("<table><tr><th>S/N</th><th>Full Name</th><th>Reference Number</th><th>Record Date</th><th></th></tr>"
                + "".join(f"<tr><td>{i}</td><td>NAME {i}</td><td>NLISWi.{i}</td><td>6/18/2026 1:00:00 PM</td><td>Details</td></tr>"
                          for i in range(12))
                + "<tr><td>13</td><td>NO REF PERSON</td><td></td><td>1/1/0001 12:00:00 AM</td><td>Details</td></tr></table>")
        ents = run_text("NG_NIGSAC_SANCTIONS_LIST", html)
        last = ents[-1]
        self.assertTrue(last.source_id.startswith("noref-"))
        self.assertEqual(last.listed_on, "")
        self.assertEqual(last.identifiers, [])

    def test_nz_aliases_and_expired_table_skipped(self):
        ents = run("NZ_POLICE_DESIGNATED_TERRORISTS", main="nz.html")
        by = {e.caption: e for e in ents}
        aamb = by["Al-Aqsa Martyrs’ Brigades"]
        vals = [n.value for n in aamb.names]
        self.assertIn("AAMB", vals)
        self.assertIn("Al-Aqsa Intifada Martyrs’ Group", vals)
        self.assertEqual(aamb.listed_on, "2010-12-15")
        self.assertFalse([e for e in ents if "Euskadi" in e.caption])  # expired designations excluded
        self.assertFalse([e for e in ents if "Shining Path" in e.caption])

    def test_sg_items(self):
        ents = run("SG_TSOFA_FIRST_SCHEDULE", main="sg.html")
        self.assertGreaterEqual(len(ents), 30)
        names = {e.caption: e for e in ents}
        self.assertNotIn("", names)
        mizan = names["Rahman Mizanur"]
        self.assertEqual(mizan.birth_dates, ["1985-03-15"])
        self.assertEqual(mizan.nationalities, ["bd"])
        self.assertEqual(mizan.identifiers[0].value, "BH0187173")
        anin = next(e for e in ents if e.caption.startswith("Anindia Afiyantari"))
        self.assertEqual(anin.nationalities, ["id"])
        self.assertIn("Anin Dia Afiyan Tari", [n.value for n in anin.names])
        self.assertEqual(len({e.source_id for e in ents}), len(ents))

    def test_th(self):
        ents = run("TH_AMLO_DESIGNATED_PERSONS", main="th.html")
        self.assertEqual(len(ents), 70)
        first = ents[0]
        self.assertEqual(first.caption, "AMRAN MING")
        self.assertEqual([n.kind for n in first.names], ["primary", "original"])
        self.assertEqual(first.names[1].lang, "th")
        self.assertEqual(first.identifiers, [])  # masked IDs are not stored

    def test_us_fto(self):
        ents = run("US_STATE_FTO", main="fto.html")
        by = {e.caption: e for e in ents}
        jc = by["Juarez Cartel"]
        self.assertIn("La Linea", [n.value for n in jc.names])
        self.assertEqual(jc.listed_on, "2026-07-16")
        self.assertEqual(jc.list_type, "TERRORISM")

    def test_us_fto_amendments_become_aliases(self):
        import re
        rows = "".join(f"<tr><td>May 1, 2020</td><td>Group {i} (G{i})</td></tr>" for i in range(35))
        rows += ("<tr><td>March 27, 2002</td><td>al-Shabaab — al-Hijra Amendment (August 1, 2018)</td></tr>"
                 "<tr><td>May 16, 2001</td><td>New Irish Republican Army (formerly Real IRA) — New IRA Amendment (June 30, 2023)</td></tr>")
        html = "<table><tr><th>Date Implemented</th><th>Name</th></tr>" + rows + "</table>"
        ents = {e.caption: e for e in run_text("US_STATE_FTO", html)}
        self.assertIn("al-Hijra", [n.value for n in ents["al-Shabaab"].names])
        irish = [n.value for n in ents["New Irish Republican Army"].names]
        self.assertIn("Real IRA", irish)
        self.assertIn("New IRA", irish)
        self.assertEqual(ents["Group 1"].names[1].value, "G1")

    def test_us_tel(self):
        ents = run("US_STATE_TERRORIST_EXCLUSION_LIST", main="tel.html")
        self.assertEqual(len(ents), 45)  # criteria <li> items before the heading are excluded
        wafa = next(e for e in ents if e.caption.startswith("Al-Wafa"))
        self.assertIn("Wafa Humanitarian Organization", [n.value for n in wafa.names])
        abb = next(e for e in ents if e.caption.startswith("Alex Boncayao"))
        self.assertIn("ABB", [n.value for n in abb.names])

    def test_us_cuba(self):
        ents = run("US_STATE_CUBA_RESTRICTED_LIST", main="cuba.html")
        by = {e.caption: e for e in ents}
        minfar = by["Ministerio de las Fuerzas Armadas Revolucionarias"]
        self.assertIn("MINFAR", [n.value for n in minfar.names])
        self.assertIn("Ministries", minfar.remarks)
        grand = by["Grand Aston La Habana"]
        self.assertEqual(grand.listed_on, "2025-07-14")
        iber = by["Iberostar Selection La Habana"]
        self.assertIn("Torre K", [n.value for n in iber.names])  # 'also' line attaches to previous entry
        self.assertTrue(all(e.list_type == "SANCTIONS" for e in ents))
        self.assertNotIn("Hotels in Pinar del Rio Province", by)  # category header, not an entity

    def test_ee(self):
        ents = run("EE_MFA_NATIONAL_SANCTIONS", human_rights="ee_hr.html", security="ee_sec.html")
        by = {e.caption: e for e in ents}
        al = by["ALAUDINOV, Apti Kharonovich"]
        self.assertIn("ALAUDINOV, Apty", [n.value for n in al.names])
        self.assertIn("Gulbakhor ISMAILOVA", by)
        self.assertEqual(len({e.source_id for e in ents}), len(ents))
        # numbering restarts for the second list on the page: both lists are read
        self.assertIn("SALIA, Paata", by)

    def test_ru_synthetic(self):
        with mock.patch.object(T, "RU_MIN_PERSONS", 1), mock.patch.object(T, "RU_MIN_ORGS", 1):
            ents = run("RU_ROSFINMONITORING_TERRORIST_EXTREMIST_LIST", main="ru.html")
        self.assertEqual(len(ents), 8)
        orgs = [e for e in ents if e.schema == "Organization"]
        persons = [e for e in ents if e.schema == "Person"]
        self.assertEqual((len(orgs), len(persons)), (4, 4))
        inn = next(e for e in orgs if e.identifiers)
        self.assertEqual({i.type for i in inn.identifiers}, {"inn", "ogrn"})
        self.assertIn("МЕЖДУНАРОДНАЯ ПРИМЕР АССОЦИАЦИЯ", [n.value for n in orgs[1].names])
        ivan = persons[0]
        self.assertEqual(ivan.birth_dates, ["1990-06-08"])
        self.assertEqual(ivan.caption, "ИВАНОВ ИВАН ИВАНОВИЧ")  # '*' flag stripped
        self.assertIn("terrorism", ivan.topics)
        self.assertIn("extremism", persons[1].topics)
        self.assertIn("СИДАРОВ СИДОР СИДОРОВИЧ", [n.value for n in persons[2].names])
        for e in ents:
            self.assertIn("state-list", e.topics)
            self.assertIn("Context only", e.remarks)

    def test_ru_real_size_floor_fails_loudly(self):
        with self.assertRaises(ValueError):
            run("RU_ROSFINMONITORING_TERRORIST_EXTREMIST_LIST", main="ru.html")


class FailLoudly(unittest.TestCase):
    """Layout changes must raise, never return empty/partial data."""

    GARBAGE = "<html><body><p>Access denied</p><table><tr><td>x</td></tr></table></body></html>"

    def test_every_parser_rejects_garbage(self):
        for src in T.SOURCES:
            names = list(src.urls)
            import tempfile
            with tempfile.TemporaryDirectory() as d:
                paths = {}
                for n in names:
                    p = Path(d) / f"{n}.html"
                    p.write_text(self.GARBAGE, encoding="utf-8")
                    paths[n] = p
                with self.subTest(src.key):
                    with self.assertRaises(ValueError):
                        list(src.parser(paths, src))

    def test_too_few_rows_rejected(self):
        html = "<table><tr><th>Terrorist organisation</th><th>Listed</th></tr><tr><td>A Group</td><td>1 May 2005</td></tr></table>"
        with self.assertRaises(ValueError):
            run_text("AU_LISTED_TERRORIST_ORGS", html)


if __name__ == "__main__":
    unittest.main()
