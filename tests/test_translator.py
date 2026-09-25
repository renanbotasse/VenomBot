"""Translator unit tests."""

import unittest

from venombot.translator import SimpleTranslator


class TranslatorTests(unittest.TestCase):
    def test_detect_arabic(self) -> None:
        self.assertEqual(SimpleTranslator.detect_language("غسل أموال واحتيال"), "ar")

    def test_detect_english(self) -> None:
        self.assertEqual(SimpleTranslator.detect_language("money laundering fraud"), "en")

    def test_translate_key_terms(self) -> None:
        text = SimpleTranslator.translate_arabic_to_english("قضية غسل أموال")
        self.assertIn("money laundering", text)


if __name__ == "__main__":
    unittest.main()
