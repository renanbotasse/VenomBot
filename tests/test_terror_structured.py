"""Offline tests for the structured domestic terrorism / asset-freeze list parsers.

Fixtures in tests/fixtures/terror_structured are cut from the real downloads
(SA, TR Art.6/3A3B/7, TN, KE, PL, NL, MY, CA, UA, ID, IQ, IL) or, where the
list names private individuals or the live file was unreachable (EG, QA, AZ,
TR wanted, VN members), synthetic records in the exact published format.
"""

from __future__ import annotations

import unittest
from pathlib import Path
from typing import Dict, List

from venombot.entities import LIST_TYPES, Entity
from venombot.lists import terror_mena as M
from venombot.lists import terror_other as O

FX = Path(__file__).parent / "fixtures" / "terror_structured"


def run(mod, key: str, **files: str) -> List[Entity]:
    src = {s.key: s for s in mod.SOURCES}[key]
    return list(src.parser({k: FX / v for k, v in files.items()}, src))


def by_name(ents: List[Entity], needle: str) -> Entity:
    for e in ents:
        if any(needle.lower() in n.value.lower() for n in e.names):
            return e
    raise AssertionError(f"no entity named like {needle!r}")


class Helpers(unittest.TestCase):
    def test_norm_dates(self) -> None:
        self.assertEqual(M.norm_dates("24535"), ["1967-03-04"])
        self.assertEqual(M.norm_dates("1958/10/09; 1959"), ["1958-10-09", "1959"])
        self.assertEqual(M.norm_dates("10-10-1998"), ["1998-10-10"])
        self.assertEqual(M.norm_dates("17.05.2014-29003"), ["2014-05-17"])  # gazette number is not a serial
        self.assertEqual(M.norm_dates("Arrêté du 9 novembre 2018"), ["2018-11-09"])
        self.assertEqual(M.norm_dates("٢٠٢٠/٣/٥"), ["2020-03-05"])
        self.assertEqual(M.norm_dates("19711119"), ["1971-11-19"])

    def test_countries_local(self) -> None:
        self.assertEqual(M.countries_local("LÜBNAN"), ["lb"])
        self.assertEqual(M.countries_local("Tunisienne/Belge"), ["tn", "be"])
        self.assertEqual(M.countries_local("Mỹ"), ["us"])  # not Malaysia
        self.assertEqual(M.countries_local("يمني"), ["ye"])
        self.assertEqual(M.countries_local("Arab Saudi"), ["sa"])

    def test_split_list(self) -> None:
        self.assertEqual(M.split_list("1.\xa0DR. X\n2. DIJE"), ["DR. X", "DIJE"])
        self.assertEqual(M.split_list("a)Isam   b)Issam M"), ["Isam", "Issam M"])
        self.assertEqual(M.split_list("أ ؛ ب"), ["أ", "ب"])


class MenaLists(unittest.TestCase):
    def test_sa_pcct_merges_arabic(self) -> None:
        ents = run(M, "SA_PCCT_NATIONAL_TERRORISM_LIST", main="sa_en.xlsx", ar="sa_ar.xlsx")
        schemas = {e.schema for e in ents}
        self.assertEqual(schemas, {"Person", "Organization", "Vessel"})
        self.assertGreater(len(ents), 300)
        harb = by_name(ents, "KHALIL YUSIF HARB")
        self.assertEqual(harb.names[0].kind, "primary")
        self.assertIn("HARB, KHALIL YUSIF", [n.value for n in harb.names])  # source form kept
        original = [n.value for n in harb.names if n.kind == "original"]
        self.assertIn("خليل يوسف حرب", original)
        self.assertEqual(harb.birth_dates, ["1958-10-09"])
        self.assertEqual(harb.listed_on, "2015-05-27")
        self.assertEqual(harb.list_type, "TERRORISM")
        houthi = by_name(ents, "ANSAR ALLAH AL-HOUTHI")
        self.assertEqual(houthi.schema, "Organization")
        self.assertTrue(any(n.value == "أنصار الله الحوثي" for n in houthi.names))
        vessel = [e for e in ents if e.schema == "Vessel"][0]
        self.assertEqual(vessel.identifiers[0].type, "IMO")
        self.assertEqual(len({e.source_id for e in ents}), len(ents))

    def test_eg_mlcu_arabic_columns(self) -> None:
        ents = run(M, "EG_MLCU_DOMESTIC_TERRORIST_LIST", main="eg.xlsx")
        self.assertEqual(len(ents), 5)
        first = ents[0]
        self.assertEqual(first.names[0].value, "محمد أحمد علي حسن")
        self.assertEqual(first.identifiers[0].value, "**********0655")
        self.assertEqual(first.nationalities, ["eg"])
        self.assertEqual(first.listed_on, "2018-04-26")
        second = ents[1]
        self.assertIn("أبو الاختبار", [n.value for n in second.names])
        self.assertEqual(second.listed_on, "2020-03-05")  # Arabic-Indic digits
        third = ents[2]  # masked ID drifted into the "other name" column
        self.assertEqual(len(third.names), 1)
        self.assertEqual(third.identifiers[0].value, "**********0123")
        self.assertEqual(third.listed_on, "2020-04-06")  # Excel serial 43927
        self.assertEqual([e.schema for e in ents], ["Person"] * 3 + ["Organization"] * 2)

    def test_qa_local_vs_un_rows(self) -> None:
        ents = run(M, "QA_NCTC_UNIFIED_SANCTIONS", main="qa.json")
        local, entity, un = ents
        self.assertEqual(local.list_type, "TERRORISM")
        self.assertEqual(un.list_type, "SANCTIONS")
        self.assertEqual(entity.schema, "Organization")
        self.assertEqual(local.birth_dates, ["1963-07-15", "1971"])
        self.assertEqual(local.nationalities, ["ye"])
        self.assertEqual([n.value for n in local.names if n.kind == "original"], ["اختبار شخص مثال النموذج"])

    def test_iq_aml(self) -> None:
        ents = run(M, "IQ_AML_LOCAL_SANCTIONS", main="iq.json")
        self.assertEqual(len(ents), 7)  # the isExcluded row is dropped
        self.assertNotIn("excluded-0001", [e.source_id for e in ents])
        kunya = [e for e in ents if len(e.names) > 1][0]
        self.assertTrue(any("المكنى" in n.value for n in kunya.names if n.kind == "primary"))
        first = ents[0]
        self.assertEqual(first.birth_dates, ["1993"])
        self.assertIn("mother_name", first.extra)
        self.assertEqual(sum(1 for e in ents if e.schema == "Organization"), 1)

    def test_iq_rejects_multipage(self) -> None:
        import json
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "x.json"
            p.write_text(json.dumps({"data": {"data": [], "rowCount": 10, "pageCount": 2}}), encoding="utf-8")
            src = {s.key: s for s in M.SOURCES}["IQ_AML_LOCAL_SANCTIONS"]
            with self.assertRaises(ValueError):
                list(src.parser({"main": p}, src))

    def test_il_operatives_active_only(self) -> None:
        ents = run(M, "IL_NBCTF_DESIGNATED_OPERATIVES", op0="il_operative.json")
        self.assertEqual(len(ents), 3)  # Cancelled and non-designated rows are dropped
        for e in ents:
            self.assertEqual(e.schema, "Person")
            self.assertTrue(e.birth_dates)
            self.assertTrue(e.listed_on)
            self.assertTrue(e.url.startswith("https://matal.mod.gov.il"))

    def test_il_organizations_and_wallets(self) -> None:
        orgs = run(M, "IL_NBCTF_DESIGNATED_ORGANIZATIONS", org0="il_organization.json")
        self.assertEqual(len(orgs), 2)
        self.assertTrue(all(o.schema == "Organization" for o in orgs))
        self.assertTrue(any(len(o.names) > 1 for o in orgs))
        wallets = run(M, "IL_NBCTF_SEIZED_CRYPTO_WALLETS", w0="il_wallet.json")
        self.assertEqual(len(wallets), 4)
        self.assertTrue(all(w.identifiers and w.identifiers[0].type.startswith("Crypto wallet") for w in wallets))
        self.assertTrue(wallets[0].linked_to)

    def test_il_api_error_raises(self) -> None:
        import json
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "x.json"
            p.write_text(json.dumps({"type": "Error", "detail": "Filter option not found"}), encoding="utf-8")
            src = {s.key: s for s in M.SOURCES}["IL_NBCTF_SEIZED_CRYPTO_WALLETS"]
            with self.assertRaises(ValueError):
                list(src.parser({"w0": p}, src))

    def test_tn_cnlct(self) -> None:
        ents = run(M, "TN_CNLCT_NATIONAL_TERRORIST_LIST", main="tn.xlsx")
        self.assertGreater(len(ents), 150)
        first = ents[0]
        self.assertEqual(first.birth_dates, ["1972-04-16"])
        self.assertEqual(first.birth_places, ["Tunis"])
        self.assertEqual(first.nationalities, ["tn"])
        self.assertEqual(first.listed_on, "2018-11-09")
        self.assertEqual(first.identifiers[0].value, "05226***")
        self.assertEqual(len({e.source_id for e in ents}), len(ents))

    def test_tr_art7_csv(self) -> None:
        ents = run(M, "TR_MASAK_DOMESTIC_FREEZE_ART7", main="tr_art7.csv")
        self.assertEqual(len(ents), 10)
        first = ents[0]
        self.assertEqual(first.names[0].value, "ABDULKADİR BAŞARAN")  # cp1254 decoded, İ kept
        self.assertEqual(first.birth_dates, ["1982-07-19"])
        self.assertEqual(first.identifiers[0].type, "TCKN")
        self.assertEqual(first.programs, ["FETÖ/PDY"])
        self.assertEqual(first.listed_on, "2021-04-06")
        self.assertEqual(first.list_type, "TERRORISM")

    def test_tr_art6_and_3ab(self) -> None:
        art6 = run(M, "TR_MASAK_FOREIGN_REQUEST_FREEZE_ART6", main="tr_art6.xlsx")
        self.assertEqual(len(art6), 112)
        amhaz = by_name(art6, "ISSAM MOHAMED AMHAZ")
        self.assertEqual(amhaz.birth_dates, ["1967-03-04"])  # Excel serial 24535
        self.assertEqual(amhaz.nationalities, ["lb"])
        self.assertEqual(amhaz.identifiers[0].value, "RL0000199")
        self.assertEqual({n.value for n in amhaz.names}, {"ISSAM MOHAMED AMHAZ", "Isam AMHAZ", "Issam Mohamed AMHAZ"})
        self.assertEqual(amhaz.listed_on, "2015-03-20")
        self.assertEqual(amhaz.list_type, "SANCTIONS")
        a3 = run(M, "TR_MASAK_PROLIFERATION_FREEZE_3A3B", main="tr_3ab.xlsx")
        self.assertEqual(len(a3), 276)
        yun = by_name(a3, "YUN HO-JIN")
        self.assertEqual(yun.nationalities, ["kp"])
        self.assertEqual(yun.birth_dates, ["1944-10-13"])
        self.assertTrue(any(e.schema == "Organization" for e in a3))

    def test_tr_wanted_is_wanted_type(self) -> None:
        ents = run(M, "TR_TERROR_WANTED", main="tr_wanted.json")
        self.assertEqual(len(ents), 3)  # Sil=true row skipped
        self.assertTrue(all(e.list_type == "WANTED" for e in ents))
        self.assertEqual(ents[1].countries, ["tj"])
        self.assertEqual(len({e.source_id for e in ents}), 3)

    def test_ke_frc(self) -> None:
        ents = run(M, "KE_FRC_DOMESTIC_TERRORISM_LIST", main="ke.xlsx")
        first = ents[0]
        self.assertEqual(first.source_id, "KEi.001")
        self.assertEqual(first.birth_dates, ["1990-01-13"])
        self.assertEqual(first.nationalities, ["ke"])
        self.assertIn("DIJE", [n.value for n in first.names])
        self.assertEqual(first.listed_on, "2026-02-04")
        self.assertEqual(first.gender, "female")


class OtherLists(unittest.TestCase):
    def test_id_dttot(self) -> None:
        ents = run(O, "ID_PPATK_DTTOT", main="id_dttot.xlsx")
        first = ents[0]
        self.assertEqual(first.source_id, "ILQ-308")
        self.assertEqual(first.names[0].value, "HAMIDAH NABAGALA")
        self.assertIn("NABAGGALA", [n.value for n in first.names])
        self.assertEqual(first.birth_dates, ["1996-03-09"])
        self.assertEqual(first.identifiers[0].value, "A00044599")
        self.assertEqual(first.nationalities, ["ug"])
        self.assertGreaterEqual(sum(1 for e in ents if e.schema == "Organization"), 3)

    def test_my_moha(self) -> None:
        ents = run(O, "MY_MOHA_SANCTIONS_LIST", main="my.xml")
        self.assertEqual(len(ents), 79)
        first = ents[0]
        self.assertEqual(first.source_id, "KDN.I.08-2014")
        self.assertEqual(first.birth_dates, ["1961-12-09"])
        self.assertEqual(first.listed_on, "2014-11-12")
        self.assertTrue(any(i.type == "NRIC" for i in first.identifiers))
        self.assertTrue(any(e.schema == "Organization" and len(e.names) > 3 for e in ents))

    def test_az_reuses_un_schema(self) -> None:
        ents = run(O, "AZ_FIU_DOMESTIC_LIST", main="az.xml")
        self.assertEqual([e.source_id for e in ents], ["I001", "E001"])  # ids no longer collide
        self.assertEqual(ents[0].birth_dates, ["1980-01-02"])
        self.assertEqual(ents[0].names[1].kind, "original")

    def test_ua_sfms(self) -> None:
        ents = run(O, "UA_SFMS_BLACKLIST", main="ua.xml")
        self.assertEqual(len(ents), 5)
        person = ents[0]
        entity = [e for e in ents if e.schema == "Organization"][0]  # type-entry 1
        self.assertEqual(person.schema, "Person")  # type-entry 2
        self.assertTrue(entity.names)
        self.assertEqual(person.birth_dates, ["1971-11-19"])
        self.assertEqual(person.names[0].value, "MOHAMMAD HAMDI MOHAMMAD SADIQ AL-AHDAL")
        self.assertEqual(person.nationalities, ["ye"])
        domestic = [e for e in ents if any(n.lang == "uk" for n in e.names)][0]
        self.assertTrue(any(n.kind == "original" for n in domestic.names))

    def test_nl_ods(self) -> None:
        ents = run(O, "NL_NATIONAL_TERRORISM_LIST", main="nl.ods")
        self.assertEqual(len(ents), 117)
        first = ents[0]
        self.assertEqual(first.names[0].value, "Kawthar Abdellaoui")
        self.assertEqual(first.birth_dates, ["1998-10-10"])
        self.assertEqual(first.listed_on, "2016-12-07")
        self.assertEqual(sum(1 for e in ents if e.schema == "Organization"), 3)

    def test_ca_atom(self) -> None:
        ents = run(O, "CA_LISTED_TERRORIST_ENTITIES", main="ca.xml")
        self.assertEqual(len(ents), 4)
        aab = by_name(ents, "Abdallah Azzam Brigades")
        self.assertIn("AAB", [n.value for n in aab.names])
        self.assertEqual(aab.schema, "Organization")
        self.assertTrue(aab.remarks)

    def test_vn_orgs_and_members(self) -> None:
        ents = run(O, "VN_MPS_TERRORIST_ORGANIZATIONS", orgs="vn_orgs.json", members_viettan="vn_members.json")
        persons = [e for e in ents if e.schema == "Person"]
        self.assertEqual(len(persons), 1)
        self.assertEqual(persons[0].birth_dates, ["1970-01-15"])  # +7h: UTC evening is the next ICT day
        self.assertEqual(persons[0].nationalities, ["us"])
        self.assertGreaterEqual(sum(1 for e in ents if e.schema == "Organization"), 5)

    def test_pl_art118(self) -> None:
        ents = run(O, "PL_MF_AML_ART118_XLSX", main="pl.xlsx")
        self.assertEqual(len(ents), 2)
        first = ents[0]
        self.assertEqual(first.birth_dates, ["1991"])
        self.assertEqual(first.nationalities, ["iq"])
        self.assertEqual(first.listed_on, "2023-09-26")
        self.assertIn("Abu Khadijah", [n.value for n in first.names])


class Registry(unittest.TestCase):
    def test_keys_types_and_groups(self) -> None:
        sources = M.SOURCES + O.SOURCES
        keys = [s.key for s in sources]
        self.assertEqual(len(keys), len(set(keys)))
        self.assertEqual(len(keys), 21)  # 22 assigned minus the legacy-xls UAE list
        self.assertNotIn("AE_UAEIEC_LOCAL_TERRORIST_LIST", keys)
        for s in sources:
            self.assertIn(s.list_type, LIST_TYPES, s.key)
            self.assertIn("terrorism", s.groups, s.key)
            self.assertNotIn("core", s.groups, s.key)
        core_mena = {s.key for s in sources if "core-mena" in s.groups}
        for key in ("SA_PCCT_NATIONAL_TERRORISM_LIST", "QA_NCTC_UNIFIED_SANCTIONS", "EG_MLCU_DOMESTIC_TERRORIST_LIST",
                    "IQ_AML_LOCAL_SANCTIONS", "IL_NBCTF_DESIGNATED_OPERATIVES", "TR_MASAK_DOMESTIC_FREEZE_ART7"):
            self.assertIn(key, core_mena)
        gcc = {s.key for s in sources if "gcc" in s.groups}
        self.assertEqual(gcc, {"SA_PCCT_NATIONAL_TERRORISM_LIST", "QA_NCTC_UNIFIED_SANCTIONS"})
        self.assertEqual({s.key: s.list_type for s in sources}["TR_TERROR_WANTED"], "WANTED")


if __name__ == "__main__":
    unittest.main()
