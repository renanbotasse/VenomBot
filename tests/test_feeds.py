"""Offline tests for venombot.feeds (parser, store, search, registry)."""

from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from venombot.feeds import FeedSource
from venombot.feeds.parse import ParsedItem, normalise_date, parse_feed, strip_html
from venombot.feeds.search import search_articles
from venombot.feeds.store import ArticleStore
from venombot.screening import Subject

FIX = Path(__file__).parent / "fixtures" / "feeds"
KINDS = {"syn": "enforcement", "other": "adverse_media"}


def _iso(days_ago: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days_ago)).strftime("%Y-%m-%dT%H:%M:%SZ")


def item(guid: str, title: str, summary: str = "", days: int = 1) -> ParsedItem:
    return ParsedItem(guid=guid, title=title, url="https://example.org/" + guid,
                      published=_iso(days), summary=summary, language="en")


class ParseTests(unittest.TestCase):
    def test_rss(self):
        f = parse_feed((FIX / "nca_rss.xml").read_bytes())
        self.assertEqual(f.format, "rss")
        self.assertEqual(len(f.items), 3)
        for it in f.items:
            self.assertTrue(it.title and it.url and it.guid)
            self.assertRegex(it.published, r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ$")
            self.assertNotIn("<", it.summary)

    def test_atom(self):
        f = parse_feed((FIX / "sfo_atom.xml").read_bytes())
        self.assertEqual(f.format, "atom")
        self.assertEqual(len(f.items), 3)
        self.assertTrue(f.items[0].url.startswith("http"))
        self.assertTrue(f.items[0].published.endswith("Z"))

    def test_arabic(self):
        f = parse_feed((FIX / "dw_arabic.xml").read_bytes(), "ar")
        self.assertEqual(len(f.items), 3)
        self.assertTrue(any("\u0600" <= ch <= "\u06ff" for ch in f.items[0].title))

    def test_malformed_falls_back(self):
        f = parse_feed((FIX / "malformed_rss.xml").read_bytes())
        self.assertEqual(len(f.items), 3)
        self.assertEqual(f.items[0].title, "Jane Doe Example charged with fraud")
        self.assertIn("fraud on Tuesday & released", f.items[0].summary)
        self.assertEqual(f.items[1].published, "2026-10-06T07:00:00Z")
        self.assertEqual(f.items[1].guid, "https://example.org/a2")  # guid falls back to link

    def test_regex_fallback_on_broken_xml(self):
        raw = b"<rss><channel><item><title>A &amp; B <unclosed</title><link>http://x/1</link></item></channel>"
        f = parse_feed(raw)
        self.assertEqual(f.format, "regex")
        self.assertEqual(len(f.items), 1)

    def test_dates_and_html(self):
        self.assertEqual(normalise_date("Thu, 08 Oct 2026 22:08:53 +0100"), "2026-10-08T21:08:53Z")
        self.assertEqual(normalise_date("2026-10-08T10:00:00+02:00"), "2026-10-08T08:00:00Z")
        self.assertEqual(normalise_date("Thursday, October 8, 2026 - 13:00"), "2026-10-08T13:00:00Z")
        self.assertEqual(normalise_date("garbage"), "")
        self.assertEqual(strip_html("<p>a&nbsp;b</p><p>c</p>"), "a b c")


class StoreSearchTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = ArticleStore(Path(self.tmp.name) / "v.db")

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def test_dedup(self):
        it = item("g1", "Regulator fines Example Bank for money laundering failures")
        self.assertEqual(self.store.add_items("syn", [it, it]), 1)
        self.assertEqual(self.store.add_items("syn", [it]), 0)
        # same normalised title from another feed is a syndicated copy
        dup = item("zz", "Regulator fines Example Bank for money laundering failures!")
        self.assertEqual(self.store.add_items("other", [dup]), 0)
        self.assertEqual(self.store.count(), 1)

    def test_sentence_level_match(self):
        self.store.add_items("syn", [
            item("m", "Court news", "Jane Doe Example was charged with fraud on Tuesday."),
            item("n", "Budget", "Jane opened the session. Later Doe presented the Example budget."),
            item("o", "Other", "Nothing relevant here."),
        ])
        subj = Subject.build("Jane Example Doe", kind="person")
        res = search_articles(self.store, subj, feed_kinds=KINDS)
        self.assertEqual([e.url for e in res], ["https://example.org/m"])
        e = res[0]
        self.assertEqual(e.kind, "enforcement")
        self.assertIn("fraud", e.topics)
        self.assertGreaterEqual(e.name_score, 0.85)
        self.assertIn("charged with fraud", e.snippet)

    def test_single_token_org_and_ranking(self):
        self.store.add_items("other", [
            item("a", "Acme Holdings expands", "Acme Holdings opened an office.", days=1),
            item("b", "Acme Holdings probe", "Acme Holdings is under investigation for bribery.", days=10),
        ])
        res = search_articles(self.store, Subject.build("Acme Holdings", kind="org"), feed_kinds=KINDS)
        self.assertEqual([e.url[-1] for e in res], ["b", "a"])  # adverse first
        res1 = search_articles(self.store, Subject.build("Acme", kind="org"), feed_kinds=KINDS, max_results=1)
        self.assertEqual(len(res1), 1)

    def test_days_filter_and_aliases(self):
        self.store.add_items("syn", [item("old", "T", "Jane Doe Example was charged with fraud.", days=100)])
        subj = Subject.build("Jane Example Doe", kind="person")
        self.assertEqual(len(search_articles(self.store, subj, days=30, feed_kinds=KINDS)), 0)
        self.assertEqual(len(search_articles(self.store, subj, days=200, feed_kinds=KINDS)), 1)
        al = Subject.build("Unknown Person", aliases=["Jane Doe Example"], kind="person")
        self.assertEqual(len(search_articles(self.store, al, feed_kinds=KINDS)), 1)

    def test_arabic_subject(self):
        self.store.add_items("other", [ParsedItem("ar1", "عنوان", "https://e/ar1", _iso(1),
                                                  "ألقي القبض على أحمد محمود علي بتهمة غسل الأموال.", "ar")])
        res = search_articles(self.store, Subject.build("أحمد محمود علي", kind="person"), feed_kinds=KINDS)
        self.assertEqual(len(res), 1)
        self.assertIn("money_laundering", res[0].topics)

    def test_purge_and_status(self):
        self.store.add_items("syn", [item("new", "New thing here today", days=1),
                                     item("old", "Ancient thing from way back", "Jane Doe Example", days=500)])
        self.assertEqual(self.store.purge(days=365), 1)
        self.assertEqual(self.store.count(), 1)
        self.assertEqual(len(search_articles(self.store, Subject.build("Jane Doe Example"), feed_kinds=KINDS)), 0)
        self.store.set_status("syn", True, count=1, etag='"x"', last_modified="Thu, 01 Jan 2026 00:00:00 GMT")
        self.store.set_status("syn", False, "HTTP 500")
        st = self.store.get_status("syn")
        self.assertEqual((st["ok"], st["etag"]), (0, '"x"'))  # validators survive a failure

    def test_shares_db_with_entity_store(self):
        from venombot.store import EntityStore
        p = Path(self.tmp.name) / "shared.db"
        a = ArticleStore(p)
        a.add_items("syn", [item("x", "Some headline long enough")])
        a.close()
        es = EntityStore(p)
        es.close()
        a = ArticleStore(p)
        self.assertEqual(a.count(), 1)
        a.close()

    def test_registry(self):
        from venombot.feeds import all_feeds, select_feeds
        reg = all_feeds()
        self.assertEqual(len(reg), 42)
        self.assertNotIn("GDELT_GKG_15MIN", reg)
        self.assertTrue(all(isinstance(f, FeedSource) for f in reg.values()))
        self.assertTrue(select_feeds(groups=["enforcement"]))
        self.assertEqual(len(select_feeds(keys=["ICIJ_RSS"])), 1)


if __name__ == "__main__":
    unittest.main()
