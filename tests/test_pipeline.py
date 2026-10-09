"""Investigation pipeline: parts, timestamped report, summary counts, resume."""

import re
import shutil
import tempfile
import unittest
from pathlib import Path

from venombot.evidence import Evidence
from venombot.lists.ofac import SOURCES as OFAC_SOURCES
from venombot.lists.ofac import parse_sdn
from venombot.pipeline import Investigation, Options, dedupe_evidence, flag_counts, slugify
from venombot.screening import Subject, screen
from venombot.store import EntityStore

FIX = Path(__file__).parent / "fixtures"


def _ev(provider: str, url: str, title: str = "t", topics=None, kind: str = "court") -> Evidence:
    return Evidence(provider=provider, kind=kind, title=title, url=url, name_score=1.0, topics=topics or [])


class DedupeTests(unittest.TestCase):
    def test_same_url_merged_and_topics_kept(self) -> None:
        out = dedupe_evidence([_ev("A", "https://x.org/case/1/"), _ev("B", "http://www.x.org/case/1", topics=["fraud"])])
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0].extra["also_in"], ["B"])
        self.assertEqual(out[0].topics, ["fraud"])

    def test_distinct_items_kept(self) -> None:
        self.assertEqual(len(dedupe_evidence([_ev("A", "https://x.org/1"), _ev("A", "https://x.org/2")])), 2)

    def test_title_used_when_no_url(self) -> None:
        self.assertEqual(len(dedupe_evidence([_ev("A", "", "Same Title"), _ev("B", "", "same  title")])), 1)


class FlagTests(unittest.TestCase):
    def test_counts(self) -> None:
        ev = [_ev("A", "u1", topics=["fraud", "corruption"]), _ev("B", "u2", kind="corporate")]
        c = flag_counts([], ev)
        self.assertEqual(c["context_items"], 2)
        self.assertEqual(c["context_with_adverse_topics"], 1)
        self.assertEqual(c["by_topic"], {"fraud": 1, "corruption": 1})
        self.assertEqual(c["by_kind"], {"court": 1, "corporate": 1})


class InvestigationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.tmp = tempfile.mkdtemp()
        cls.store = EntityStore(Path(cls.tmp) / "t.db")
        cls.store.replace_source("US_OFAC_SDN", parse_sdn(
            {"main": FIX / "ofac_sdn.csv", "alt": FIX / "ofac_alt.csv"}, OFAC_SOURCES[0]))

    @classmethod
    def tearDownClass(cls) -> None:
        cls.store.close()
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def _run(self, out: Path, **kw) -> Investigation:
        subject = Subject.build("Vladimir Putin", dob="1952-10-07", country="RU")
        opts = Options(media=False, live=False, requested_by="Tester", purpose="unit test", **kw)
        inv = Investigation(self.store, subject, out, opts, log=lambda m: None, db_path=Path(self.tmp) / "t.db")
        inv.run()
        return inv

    def test_report_named_with_subject_and_iso_time(self) -> None:
        out = Path(self.tmp) / "o1"
        inv = self._run(out)
        self.assertRegex(inv.report_path.name, r"^vladimir-putin_\d{4}-\d{2}-\d{2}T\d{2}-\d{2}-\d{2}Z\.md$")
        text = inv.report_path.read_text(encoding="utf-8")
        self.assertIn("**Requested by:** Tester", text)
        self.assertRegex(text, r"\*\*Search started:\*\* \d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z")

    def test_summary_is_on_top_with_counts(self) -> None:
        text = self._run(Path(self.tmp) / "o2").report_path.read_text(encoding="utf-8")
        self.assertLess(text.index("## Summary"), text.index("## 1. List matches"))
        self.assertIn("1 CONFIRMED", text)
        self.assertIn("1 CRITICAL", text)
        self.assertIn("HOLD_FOR_REVIEW", text)
        self.assertRegex(text, r"Found \*\*1\*\* relevant list match")

    def test_parts_written_and_skipped_stages_reported(self) -> None:
        inv = self._run(Path(self.tmp) / "o3", formats=["md", "json"])
        names = {p.name for p in inv.dir.iterdir()}
        self.assertTrue({"00-summary.md", "01-lists.md", "01-lists.json", "state.json"} <= names)
        self.assertFalse(any(n.startswith("02-media") for n in names))

    def test_resume_skips_finished_stage(self) -> None:
        out = Path(self.tmp) / "o4"
        self._run(out)
        logs = []
        subject = Subject.build("Vladimir Putin", dob="1952-10-07", country="RU")
        inv = Investigation(self.store, subject, out, Options(media=False, live=False, resume=True),
                            log=logs.append, db_path=Path(self.tmp) / "t.db")
        inv.run()
        self.assertTrue(any("resumed" in m for m in logs))

    def test_slug(self) -> None:
        self.assertEqual(slugify("José  Müller-Ñandú"), "jose-muller-nandu")
        self.assertEqual(slugify("أيمن"), "aimn")


if __name__ == "__main__":
    unittest.main()
