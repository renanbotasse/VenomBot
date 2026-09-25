"""Scoring unit tests."""

import unittest

from venombot.scoring import extract_evidence, severity_and_score


class ScoringTests(unittest.TestCase):
    def test_sanctions_hit_is_critical(self) -> None:
        sev, conf, ftype, risk, action = severity_and_score(
            "SANCTIONS",
            "en",
            "Acme Trading Co",
            "Acme Trading Co listed on SDN for sanctions evasion",
        )
        self.assertEqual(sev, "CRITICAL")
        self.assertEqual(ftype, "SANCTIONS_HIT")
        self.assertEqual(action, "BLOCK")
        self.assertGreaterEqual(conf, 90.0)
        self.assertGreaterEqual(risk, 90.0)

    def test_extract_evidence_window(self) -> None:
        text = "prefix " + ("x" * 50) + " Target Name " + ("y" * 50) + " suffix"
        evidence = extract_evidence(text, "Target Name", window=20)
        self.assertIn("Target Name", evidence)
        self.assertLess(len(evidence), len(text))


if __name__ == "__main__":
    unittest.main()
