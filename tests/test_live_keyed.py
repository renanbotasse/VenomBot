"""Offline tests for keyed live providers, news APIs, GDELT and Interpol.

Response shapes for key-gated services follow vendor documentation; these
tests (with monkeypatched fetch) are the only verification for them. The
subjects in fixtures are synthetic (private-individual style data).
"""

from __future__ import annotations

import json
import unittest
from pathlib import Path
from unittest import mock

from venombot.live import interpol, keyed_registries as kr, media_apis as ma
from venombot.screening import Subject

FIX = Path(__file__).parent / "fixtures" / "live_keyed"


def fx(name: str) -> str:
    return (FIX / name).read_text(encoding="utf-8")


class Fake:
    """get_text stand-in: routes by URL substring and records calls."""

    def __init__(self, routes):
        self.routes, self.calls = routes, []

    def __call__(self, url, **kw):
        self.calls.append((url, kw))
        for frag, body in self.routes:
            if frag in url:
                if isinstance(body, Exception):
                    raise body
                return body
        raise AssertionError(f"unexpected url {url}")


def patched(mod, routes):
    fake = Fake(routes)
    return fake, mock.patch.object(mod, "get_text", fake)


JON = Subject.build("Jon Testerman", kind="person")
ORG = Subject.build("Testerman Holdings Limited", kind="org")


class GdeltTests(unittest.TestCase):
    def test_two_languages_and_skip_delay(self):
        fake, p = patched(ma, [("sourcelang%3Aenglish", fx("gdelt.json")), ("sourcelang%3Aarabic", fx("gdelt_ar.json"))])
        with p, mock.patch.object(ma, "_sleep") as sl:
            ev = ma.search_gdelt(JON)
        sl.assert_called_once_with(5.0)
        self.assertEqual(len(fake.calls), 2)
        self.assertEqual(len(ev), 3)
        self.assertIn("timespan=1y", fake.calls[0][0])
        titled = [e for e in ev if e.extra["name_in_title"]]
        self.assertEqual(len(titled), 1)
        self.assertIn("money_laundering", titled[0].topics)
        self.assertEqual(titled[0].published, "2026-09-24")
        self.assertIn("corruption", [t for e in ev for t in e.topics])  # Arabic bribery

    def test_empty_object_and_error_text(self):
        _, p = patched(ma, [("gdelt", "{}")])
        with p, mock.patch.object(ma, "_sleep"):
            self.assertEqual(ma.search_gdelt(JON), [])
        _, p = patched(ma, [("gdelt", "Timespan is too short.")])
        with p, mock.patch.object(ma, "_sleep"), self.assertRaises(RuntimeError):
            ma.search_gdelt(JON)


class NameFilterTests(unittest.TestCase):
    def test_window_match(self):
        self.assertGreaterEqual(ma.name_hit(JON, "Prosecutors say Jon Testerman took kickbacks.")[0], 0.85)
        self.assertLess(ma.name_hit(JON, "Jonathan Westman wins award")[0], 0.85)


class NewsTests(unittest.TestCase):
    def provider(self, key):
        return next(p for p in ma.PROVIDERS if p.key == key)

    def test_newsapi_filters_and_languages(self):
        fake, p = patched(ma, [("newsapi.org", fx("newsapi.json"))])
        with p, mock.patch.object(ma, "_sleep"):
            ev = self.provider("NEWSAPI_ORG").search(JON, "k")
        self.assertEqual(len(fake.calls), 2)  # en + ar
        self.assertEqual(fake.calls[0][1]["headers"], {"X-Api-Key": "k"})
        self.assertEqual(len(ev), 1)  # same URL from both calls deduped, other article dropped
        self.assertEqual(ev[0].kind, "adverse_media")
        self.assertIn("corruption", ev[0].topics)
        self.assertGreaterEqual(ev[0].name_score, 0.85)

    def test_error_payload_raises(self):
        _, p = patched(ma, [("newsapi.org", fx("newsapi_error.json"))])
        with p, self.assertRaises(RuntimeError):
            self.provider("NEWSAPI_ORG").search(JON, "bad")

    def test_missing_key_raises(self):
        with self.assertRaises(RuntimeError):
            self.provider("GNEWS_IO").search(JON, None)

    def test_all_news_shapes(self):
        art = {"title": "Jon Testerman faces fraud trial", "description": "x", "url": "https://e.com/g"}
        payloads = {
            "GNEWS_IO": {"articles": [dict(art, publishedAt="2026-09-01T00:00:00Z", source={"name": "E"})]},
            "NEWSDATA_IO": {"status": "success", "results": [{"title": art["title"], "link": art["url"], "pubDate": "2026-09-01 00:00:00"}]},
            "MEDIASTACK": {"data": [dict(art, published_at="2026-09-01T00:00:00+00:00", language="en")]},
            "CURRENTS_API": {"status": "ok", "news": [dict(art, published="2026-09-01 00:00:00 +0000")]},
            "THENEWSAPI": {"data": [dict(art, published_at="2026-09-01T00:00:00Z")]},
            "WORLD_NEWS_API": {"news": [{"title": art["title"], "text": "fraud", "url": art["url"], "publish_date": "2026-09-01 00:00:00"}]},
            "GUARDIAN_OPEN_PLATFORM": {"response": {"status": "ok", "results": [{"webTitle": art["title"], "webUrl": art["url"], "webPublicationDate": "2026-09-01T00:00:00Z", "fields": {"trailText": "fraud"}}]}},
            "NYT_ARTICLE_SEARCH": {"response": {"docs": [{"headline": {"main": art["title"]}, "web_url": art["url"], "pub_date": "2026-09-01T00:00:00+0000", "abstract": "fraud"}]}},
            "EVENT_REGISTRY_API": {"articles": {"results": [{"title": art["title"], "body": "fraud", "url": art["url"], "dateTime": "2026-09-01T00:00:00Z", "lang": "eng"}]}},
            "MEDIA_CLOUD_SEARCH": {"stories": [{"title": art["title"], "url": art["url"], "publish_date": "2026-09-01", "language": "en"}]},
        }
        for key, payload in payloads.items():
            with self.subTest(key):
                _, p = patched(ma, [("", json.dumps(payload))])
                with p, mock.patch.object(ma, "_sleep"):
                    ev = self.provider(key).search(JON, "k")
                self.assertEqual(len(ev), 1)
                self.assertEqual(ev[0].published, "2026-09-01")
                self.assertIn("fraud", ev[0].topics)

    def test_registered(self):
        keys = {p.key for p in ma.PROVIDERS}
        self.assertEqual(len(keys), 12)
        gd = self.provider("GDELT_DOC_API")
        self.assertTrue(gd.available)
        self.assertIn("free", gd.groups)
        self.assertTrue(all(p.api_key_env and "news" in p.groups for p in ma.PROVIDERS if p.key != "GDELT_DOC_API"))


class RegistryTests(unittest.TestCase):
    def test_companies_house(self):
        fake, p = patched(kr, [("/search/companies", fx("ch_companies.json")), ("/search/officers", fx("ch_officers.json"))])
        with p:
            ev = kr.search_ch(Subject.build("Testerman Holdings Limited"), "KEY")
        self.assertTrue(fake.calls[0][1]["headers"]["Authorization"].startswith("Basic "))
        self.assertEqual([e.title for e in ev], ["TESTERMAN HOLDINGS LIMITED"])
        self.assertEqual(ev[0].kind, "corporate")
        self.assertIn("01234567", ev[0].url)

    def test_companies_house_person(self):
        _, p = patched(kr, [("/search/officers", fx("ch_officers.json"))])
        with p:
            ev = kr.search_ch(JON, "KEY")
        self.assertEqual(len(ev), 1)
        self.assertTrue(ev[0].url.endswith("/officers/abc123"))

    def test_ch_auth_error_raises(self):
        _, p = patched(kr, [("api.company", '{"error":"Empty Authorization header","type":"ch:service"}')])
        with p, self.assertRaises(RuntimeError):
            kr.search_ch_disqualified(JON, "KEY")

    def test_disqualified(self):
        _, p = patched(kr, [("disqualified-officers", fx("ch_disq.json"))])
        with p:
            ev = kr.search_ch_disqualified(JON, "KEY")
        self.assertEqual(ev[0].kind, "enforcement")
        self.assertIn("natural/xyz789", ev[0].url)

    def test_opencorporates(self):
        _, p = patched(kr, [("companies/search", fx("oc_companies.json"))])
        with p:
            ev = kr.search_opencorporates(ORG, "TOK")
        self.assertEqual(len(ev), 1)
        self.assertEqual(ev[0].extra["jurisdiction_code"], "gb")
        _, p = patched(kr, [("companies/search", fx("oc_error.json"))])
        with p, self.assertRaises(RuntimeError):
            kr.search_opencorporates(ORG, "bad")

    def test_transparencia(self):
        fake, p = patched(kr, [("/ceis", fx("pt_ceis.json")), ("/cnep", "[]")])
        with p:
            ev = kr.search_pt_sanctions(Subject.build("Testerman Comercio Ltda", kind="org"), "K")
        self.assertEqual(fake.calls[0][1]["headers"]["chave-api-dados"], "K")
        self.assertEqual(len(ev), 1)
        self.assertEqual(ev[0].extra["register"], "CEIS")
        # by tax id: name need not match
        fake, p = patched(kr, [("/ceis", fx("pt_ceis.json")), ("/cnep", "[]")])
        with p:
            ev = kr.search_pt_sanctions(Subject.build("Other Name", identifiers=["00.000.000/0001-00"]), "K")
        self.assertIn("codigoSancionado=00000000000100", fake.calls[0][0])
        self.assertEqual(len(ev), 1)
        _, p = patched(kr, [("/ceis", fx("pt_error.json"))])
        with p, self.assertRaises(RuntimeError):
            kr.search_pt_sanctions(JON, "K")

    def test_transparencia_pep(self):
        _, p = patched(kr, [("/peps", fx("pt_pep.json"))])
        with p:
            ev = kr.search_pt_pep(JON, "K")
        self.assertEqual(ev[0].kind, "pep_info")
        self.assertEqual(ev[0].extra["function"], "MINISTRO DE ESTADO")

    def test_fca_needs_both_env(self):
        with mock.patch.dict("os.environ", {}, clear=True), self.assertRaises(RuntimeError):
            kr.search_fca(JON, "K")
        fake, p = patched(kr, [("Search", fx("fca.json"))])
        with p, mock.patch.dict("os.environ", {"FCA_REGISTER_EMAIL": "a@b.c"}):
            ev = kr.search_fca(JON, "K")
        h = fake.calls[0][1]["headers"]
        self.assertEqual((h["X-Auth-Email"], h["X-Auth-Key"]), ("a@b.c", "K"))
        self.assertEqual(ev[0].extra["status"], "Prohibited")

    def test_govinfo_party_level_only(self):
        fake, p = patched(kr, [("api.govinfo.gov/search", fx("govinfo.json"))])
        with p:
            ev = kr.search_govinfo(JON, "K")
        self.assertIn("data", fake.calls[0][1])
        self.assertEqual(len(ev), 1)
        self.assertEqual(ev[0].kind, "court")

    def test_aleph(self):
        fake, p = patched(kr, [("aleph.occrp.org", fx("aleph.json"))])
        with p:
            ev = kr.search_aleph(JON, "K")
        self.assertEqual(fake.calls[0][1]["headers"]["Authorization"], "ApiKey K")
        self.assertEqual(ev[0].kind, "leak")
        self.assertEqual(ev[0].extra["dataset"], "Test leak")

    def test_csl_alt_name(self):
        fake, p = patched(kr, [("consolidated_screening_list", fx("csl.json"))])
        with p:
            ev = kr.search_csl(Subject.build("Testerman Trading LLC", kind="org"), "K")
        self.assertEqual(fake.calls[0][1]["headers"], {"subscription-key": "K"})
        self.assertEqual(len(ev), 1)
        self.assertIn("sanctioned", ev[0].topics)

    def test_non_json_raises(self):
        _, p = patched(kr, [("", "<html>blocked</html>")])
        with p, self.assertRaises(RuntimeError):
            kr.search_csl(ORG, "K")

    def test_registered(self):
        keys = [p.key for p in kr.PROVIDERS]
        self.assertEqual(len(keys), len(set(keys)))
        self.assertEqual(len(keys), 9)
        self.assertTrue(all(p.api_key_env for p in kr.PROVIDERS))


class InterpolTests(unittest.TestCase):
    def test_notice(self):
        fake, p = patched(interpol, [("ws-public.interpol.int", fx("interpol.json"))])
        with p:
            ev = interpol.search_interpol(JON)
        self.assertIn("forename=Jon", fake.calls[0][0])
        self.assertEqual(ev[0].extra["nationalities"], ["GB"])
        self.assertEqual(ev[0].kind, "enforcement")

    def test_403_propagates(self):
        _, p = patched(interpol, [("", RuntimeError("HTTP 403 for x"))])
        with p, self.assertRaises(RuntimeError):
            interpol.search_interpol(JON)

    def test_org_skipped_and_single_name_rejected(self):
        self.assertEqual(interpol.search_interpol(ORG), [])
        with self.assertRaises(RuntimeError):
            interpol.search_interpol(Subject.build("Madonna", kind="person"))

    def test_provider(self):
        (p,) = interpol.PROVIDERS
        self.assertTrue(p.available)


if __name__ == "__main__":
    unittest.main()
