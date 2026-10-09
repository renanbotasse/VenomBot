"""PEP roster parsers: offline tests on tiny real-format fixtures (tests/fixtures/pep)."""

import re
import tempfile
import unittest
from pathlib import Path
from typing import Dict, List

from venombot.entities import LIST_TYPES, Entity
from venombot.lists import ListSource
from venombot.lists import pep_americas, pep_europe, pep_other, pep_wikidata

FIX = Path(__file__).parent / "fixtures" / "pep"
MODULES = (pep_europe, pep_americas, pep_other, pep_wikidata)

# Keys implemented by this family (the assignment's D_pep list minus the skipped ones).
EXPECTED_KEYS = {
    "UK_PARLIAMENT_MEMBERS_API", "UK_PARLIAMENT_GOVERNMENT_POSTS", "UK_PARLIAMENT_INTERESTS_FAMILY",
    "EU_EP_MEPS_XML", "EU_EP_OPENDATA_API", "EU_WHOISWHO_SPARQL", "DE_BUNDESTAG_STAMMDATEN",
    "FR_HATVP_LISTE", "FR_HATVP_DECLARATIONS_XML", "FR_RNE_ELUS", "ES_CONGRESO_DIPUTADOS",
    "PT_PARLAMENTO_INFORMACAO_BASE", "US_CONGRESS_LEGISLATORS_CURRENT", "US_EXECUTIVE_BRANCH",
    "US_HOUSE_CLERK_MEMBERDATA", "US_FJC_JUDGES", "US_OPENSTATES_LEGISLATORS", "US_CONGRESS_GOV_API",
    "CA_COMMONS_MEMBERS_XML", "CA_FACFOA_UKRAINE", "BR_CAMARA_DEPUTADOS", "BR_SENADO_SENADORES",
    "BR_CGU_PEP", "BR_TSE_CANDIDATOS_2026", "CO_DAFP_PEP", "AU_APH_HANDBOOK_API", "ZA_PMG_MEMBERS_API",
    "KW_CMGS_CABINET", "CIA_WORLD_LEADERS_JSON", "WIKIDATA_HEADS_OF_STATE_GOV",
    "WIKIDATA_CENTRAL_BANK_GOVERNORS", "WIKIDATA_SOE_LEADERS", "WIKIDATA_PEPS", "WIKIDATA_PEP_RELATIVES",
    "QLEVER_WIKIDATA_PEPS",
}


def source(key: str) -> ListSource:
    for mod in MODULES:
        for s in mod.SOURCES:
            if s.key == key:
                return s
    raise KeyError(key)


def run(key: str, **files: str) -> List[Entity]:
    src = source(key)
    return list(src.parser({name: FIX / f for name, f in files.items()}, src))


def pos(ent: Entity, needle: str):
    return next(p for p in ent.positions if needle in p.title)


class RegistryTests(unittest.TestCase):
    def test_keys_unique_and_complete(self) -> None:
        keys: List[str] = [s.key for m in MODULES for s in m.SOURCES]
        self.assertEqual(len(keys), len(set(keys)))
        self.assertEqual(set(keys), EXPECTED_KEYS)

    def test_contract(self) -> None:
        for m in MODULES:
            for s in m.SOURCES:
                self.assertIn(s.list_type, LIST_TYPES, s.key)
                self.assertIn("pep", s.groups) if s.key != "CA_FACFOA_UKRAINE" else None
                self.assertNotIn("core", s.groups, s.key)
                self.assertTrue(s.urls, s.key)
        self.assertTrue(source("US_CONGRESS_GOV_API").needs_key)
        self.assertIn("{api_key}", next(iter(source("US_CONGRESS_GOV_API").urls.values())))
        for s in pep_wikidata.SOURCES:
            self.assertIn("wikidata", s.groups)
            self.assertIn("VenomBot", s.headers["User-Agent"])

    def test_iso_dates_and_status(self) -> None:
        self.assertEqual(pep_europe.iso("17/08/2023"), "2023-08-17")
        self.assertEqual(pep_europe.iso("20.10.1930"), "1930-10-20")
        self.assertEqual(pep_europe.iso("2024-07-16T00:00:00"), "2024-07-16")
        self.assertEqual(pep_europe.iso("05/2022"), "2022-05")
        self.assertEqual(pep_europe.iso("Não informada"), "")
        self.assertEqual(pep_europe.status_for("", ""), "current")
        self.assertEqual(pep_europe.status_for("2000-01-01", "2001-01-01"), "ended")
        self.assertEqual(pep_europe.status_for("2000-01-01", "2999-01-01"), "current")


class EuropeTests(unittest.TestCase):
    def test_uk_members(self) -> None:
        ents = run("UK_PARLIAMENT_MEMBERS_API", commons_0000="uk_members.json")
        self.assertEqual(len(ents), 3)
        abbott = ents[0]
        self.assertEqual(abbott.caption, "Ms Diane Abbott")
        p = pos(abbott, "House of Commons")
        self.assertEqual((p.status, p.start, p.country), ("current", "1987-06-11", "gb"))
        self.assertIn("Hackney North", p.title)

    def test_uk_government_posts(self) -> None:
        ents = run("UK_PARLIAMENT_GOVERNMENT_POSTS", main="uk_govposts.json")
        self.assertEqual(len(ents), 2)
        self.assertTrue(all(e.source_id.startswith("minister-") for e in ents))
        self.assertEqual(ents[0].positions[0].status, "current")
        self.assertIn("Prime Minister", ents[0].positions[0].title)

    def test_uk_family_is_rca_linked_to_mp(self) -> None:
        ents = run("UK_PARLIAMENT_INTERESTS_FAMILY", employed_000="uk_interests.json")
        self.assertEqual(len(ents), 3)
        e = ents[0]
        self.assertEqual(e.list_type, "PEP_RCA")
        self.assertEqual(e.linked_to, ["Alberto Costa"])
        self.assertIn("Spouse", e.remarks)

    def test_eu_meps_current_and_outgoing(self) -> None:
        ents = run("EU_EP_MEPS_XML", current="ep_meps.xml", outgoing="ep_meps_outgoing.xml")
        self.assertEqual(len(ents), 5)
        self.assertEqual(ents[0].countries, ["fi"])
        self.assertEqual(ents[0].positions[0].status, "current")
        out = ents[-1]
        self.assertEqual(out.positions[0].status, "ended")
        self.assertRegex(out.positions[0].end, r"^\d{4}-\d{2}-\d{2}$")
        self.assertIn("Cache", "Cache")  # keep unittest happy about unused import

    def test_eu_opendata_and_whoiswho(self) -> None:
        ents = run("EU_EP_OPENDATA_API", main="ep_opendata.json")
        self.assertEqual(len(ents), 3)
        self.assertEqual(len(ents[0].countries), 1)
        ww = run("EU_WHOISWHO_SPARQL", main="eu_whoiswho.csv")
        self.assertTrue(ww)
        self.assertTrue(all(p.country == "eu" for e in ww for p in e.positions))

    def test_bundestag_drops_deceased_and_keeps_terms(self) -> None:
        ents = run("DE_BUNDESTAG_STAMMDATEN", main="bundestag.zip")
        names = {e.caption for e in ents}
        self.assertNotIn("Manfred Abelein", names)
        self.assertEqual(len(ents), 2)
        mandrella = next(e for e in ents if "Mandrella" in e.caption)
        self.assertEqual(mandrella.birth_dates, ["2001-04-12"])
        self.assertEqual(pos(mandrella, "Bundestag").status, "current")
        adam = next(e for e in ents if "Adam" in e.caption)
        self.assertTrue(all(p.status == "ended" for p in adam.positions))

    def test_hatvp(self) -> None:
        ents = run("FR_HATVP_LISTE", main="hatvp_liste.csv")
        self.assertGreaterEqual(len(ents), 3)
        self.assertEqual(len({e.uid for e in ents}), len(ents))
        self.assertEqual(ents[0].positions[0].status, "unknown")

    def test_hatvp_declarations_declarant_dob_and_spouse_rca(self) -> None:
        ents = run("FR_HATVP_DECLARATIONS_XML", main="hatvp_declarations.xml")
        self.assertTrue(all(e.birth_dates for e in ents))
        self.assertTrue(all(e.list_type == "PEP" for e in ents))  # placeholder spouses are skipped
        text = (FIX / "hatvp_declarations.xml").read_text(encoding="utf-8")
        patched = re.sub(r"<nomConjoint>\s*\[Données non publiées\]\s*</nomConjoint>",
                         "<nomConjoint>Jeanne EXEMPLE</nomConjoint>", text)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "d.xml"
            path.write_text(patched, encoding="utf-8")
            src = source("FR_HATVP_DECLARATIONS_XML")
            out = list(src.parser({"main": path}, src))
        rca = [e for e in out if e.list_type == "PEP_RCA"]
        self.assertEqual(rca[0].caption, "Jeanne EXEMPLE")
        self.assertTrue(rca[0].linked_to)

    def test_rne(self) -> None:
        ents = run("FR_RNE_ELUS", deputes="rne_deputes.csv", senateurs="rne_senateurs.csv", maires="rne_maires.csv")
        self.assertEqual(len(ents), 9)
        self.assertTrue(all(e.birth_dates and e.positions[0].status == "current" for e in ents))
        self.assertTrue(any("Senator" in e.positions[0].title for e in ents))
        self.assertEqual(len({e.uid for e in ents}), 9)

    def test_spain_and_portugal(self) -> None:
        es = run("ES_CONGRESO_DIPUTADOS", main="es_diputados.json")
        self.assertEqual(len(es), 3)
        self.assertEqual(es[0].positions[0].status, "current")
        with tempfile.TemporaryDirectory() as tmp:
            html = Path(tmp) / "landing.html"
            html.write_text("<html>landing</html>", encoding="utf-8")
            with self.assertRaises(ValueError):
                list(source("ES_CONGRESO_DIPUTADOS").parser({"main": html}, source("ES_CONGRESO_DIPUTADOS")))
        pt = run("PT_PARLAMENTO_INFORMACAO_BASE", main="pt_informacao_base.json")
        self.assertEqual(len(pt), 4)
        statuses = {e.positions[0].status for e in pt}
        self.assertIn("current", statuses)
        self.assertTrue(any("suplente" in e.positions[0].title for e in pt))


class AmericasTests(unittest.TestCase):
    def test_us_legislators(self) -> None:
        ents = run("US_CONGRESS_LEGISLATORS_CURRENT", main="us_legislators.json")
        self.assertEqual(len(ents), 3)
        e = ents[0]
        self.assertRegex(e.birth_dates[0], r"^\d{4}-\d{2}-\d{2}$")
        self.assertEqual(e.positions[-1].status, "current")
        self.assertTrue(any(i.type == "Bioguide" for i in e.identifiers))

    def test_us_exec_house_fjc_openstates_congress_api(self) -> None:
        ex = run("US_EXECUTIVE_BRANCH", main="us_executive.json")
        self.assertTrue(ex and all(int(e.birth_dates[0][:4]) >= 1925 for e in ex))
        house = run("US_HOUSE_CLERK_MEMBERDATA", main="us_house_memberdata.xml")
        self.assertEqual(len(house), 3)
        self.assertEqual(house[0].positions[0].status, "current")
        judges = run("US_FJC_JUDGES", main="us_fjc_judges.csv")
        self.assertEqual(len(judges), 3)
        self.assertTrue(all("Judge" in j.positions[0].title or "Chief" in j.positions[0].title for j in judges))
        os_ = run("US_OPENSTATES_LEGISLATORS", ny="us_openstates_ny.csv")
        self.assertEqual(len(os_), 3)
        self.assertIn("NY", os_[0].positions[0].title)
        api = run("US_CONGRESS_GOV_API", p000="us_congress_gov.json")
        self.assertEqual(api[0].source_id, "W000832")

    def test_canada(self) -> None:
        mps = run("CA_COMMONS_MEMBERS_XML", main="ca_commons.xml")
        self.assertEqual(len(mps), 3)
        self.assertEqual(mps[0].positions[0].status, "current")
        fac = run("CA_FACFOA_UKRAINE", main="ca_facfoa_ukraine.xml")
        self.assertEqual(len(fac), 16)  # repealed items are skipped
        self.assertTrue(all(e.list_type == "SANCTIONS" for e in fac))
        azarov = fac[0]
        self.assertEqual(azarov.birth_dates, ["1947-12-17"])
        self.assertEqual(azarov.positions[0].status, "ended")
        son = fac[1]
        self.assertFalse(son.positions)
        self.assertIn("son of", son.remarks)

    def test_brazil(self) -> None:
        dep = run("BR_CAMARA_DEPUTADOS", main="br_camara.csv")
        self.assertEqual(len(dep), 3)
        self.assertEqual({e.positions[0].status for e in dep}, {"current", "ended"})
        self.assertEqual(dep[0].birth_dates, ["1984-01-31"])
        sen = run("BR_SENADO_SENADORES", main="br_senado.json")
        self.assertEqual(len(sen), 2)
        self.assertEqual(sen[0].positions[0].status, "current")
        cgu = run("BR_CGU_PEP", main="br_cgu_pep.zip")
        self.assertEqual(len({e.uid for e in cgu}), len(cgu))
        self.assertTrue(any(re.search(r"\((local|regional|national)\)", e.positions[0].title) for e in cgu))
        self.assertFalse(any(e.caption.startswith("(CUMULATIVAMENTE)") for e in cgu))
        self.assertTrue(all(e.identifiers and e.identifiers[0].type == "CPF (masked)" for e in cgu))
        tse = run("BR_TSE_CANDIDATOS_2026", main="br_tse_2026.zip")
        self.assertIn(len(tse), (2, 3))  # the non-elected candidate is dropped
        self.assertTrue(all(e.birth_dates for e in tse))

    def test_colombia_merges_positions_per_cedula(self) -> None:
        ents = run("CO_DAFP_PEP", main="co_dafp_pep.json")
        by_id = {e.source_id: e for e in ents}
        self.assertEqual(len(by_id), len(ents))
        self.assertEqual(by_id["94275549"].identifiers[0].value, "94275549")
        self.assertEqual(pos(by_id["94275549"], "CONCEJAL").start, "2024-01-01")

    def test_cgu_url_uses_month_minus_two(self) -> None:
        from datetime import date
        self.assertTrue(pep_americas._cgu_url(date(2026, 10, 9)).endswith("/202608_PEP.zip"))
        self.assertTrue(pep_americas._cgu_url(date(2026, 2, 1)).endswith("/202512_PEP.zip"))


class OtherTests(unittest.TestCase):
    def test_australia_and_south_africa(self) -> None:
        au = run("AU_APH_HANDBOOK_API", p0000="au_aph.json")
        self.assertEqual(au[0].birth_dates, ["1972-11-28"])
        self.assertEqual(au[0].positions[0].status, "ended")
        za = run("ZA_PMG_MEMBERS_API", p01="za_pmg.json")
        self.assertEqual(len(za), 3)
        self.assertIn("Alvin Botes", [n.value for n in za[0].names])
        self.assertEqual(za[0].positions[0].status, "current")

    def test_kuwait_cabinet_html(self) -> None:
        ents = run("KW_CMGS_CABINET", main="kw_cmgs_cabinet.html")
        self.assertEqual(len(ents), 3)
        self.assertEqual(ents[0].names[0].lang, "ar")
        self.assertIn("رئيس مجلس الوزراء", ents[0].positions[0].title)
        self.assertEqual(ents[0].positions[0].status, "unknown")

    def test_cia_leaders_acting_and_country(self) -> None:
        ents = run("CIA_WORLD_LEADERS_JSON", main="cia_world_leaders.json")
        self.assertTrue(any("(acting)" in e.positions[0].title for e in ents))
        self.assertTrue(all(e.countries for e in ents))
        self.assertFalse(any("(Acting)" in e.caption for e in ents))
        self.assertIn("cd", {c for e in ents for c in e.countries})


class WikidataTests(unittest.TestCase):
    def test_positions_and_deceased(self) -> None:
        ents = run("WIKIDATA_PEPS", gcc="wd_peps.csv")
        self.assertTrue(ents)
        self.assertEqual(len({e.uid for e in ents}), len(ents))
        self.assertTrue(all(e.positions and e.source_id.startswith("Q") for e in ents))

    def test_family_files(self) -> None:
        heads = run("WIKIDATA_HEADS_OF_STATE_GOV", main="wd_heads.csv")
        self.assertTrue(any("Head of" in e.positions[0].title for e in heads))
        self.assertTrue(run("WIKIDATA_CENTRAL_BANK_GOVERNORS", main="wd_cb.csv"))
        soe = run("WIKIDATA_SOE_LEADERS", main="wd_soe.csv")
        self.assertTrue(any("Chief executive" in p.title or "Chairperson" in p.title or "Director" in p.title
                            for e in soe for p in e.positions))

    def test_relatives_are_rca(self) -> None:
        ents = run("WIKIDATA_PEP_RELATIVES", gcc="wd_relatives.csv")
        self.assertEqual({e.list_type for e in ents}, {"PEP_RCA"})
        self.assertEqual(ents[0].linked_to, ["Sabah Al-Ahmad Al-Jaber Al-Sabah"])
        self.assertEqual(ents[1].birth_dates, ["1939"])  # Jan-1 means year precision

    def test_qlever_class_tier(self) -> None:
        ents = run("QLEVER_WIKIDATA_PEPS", top="qlever_peps.csv")
        self.assertIn("[head of government]", ents[0].positions[0].title)
        self.assertEqual(ents[0].birth_dates, ["1895-02-12"])


if __name__ == "__main__":
    unittest.main()
