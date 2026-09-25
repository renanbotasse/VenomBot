"""Arabic → English translation for AML-relevant terms."""

from __future__ import annotations

import re
from typing import Dict


class SimpleTranslator:
    ARABIC_TRANSLATIONS: Dict[str, str] = {
        "إرهاب": "terrorism",
        "إرهابي": "terrorist",
        "غسل أموال": "money laundering",
        "غسيل أموال": "money laundering",
        "احتيال": "fraud",
        "فساد": "corruption",
        "رشوة": "bribery",
        "عقوبات": "sanctions",
        "مدرج": "listed",
        "قائمة": "list",
        "تحقيق": "investigation",
        "اعتقال": "arrest",
        "تجميد": "freeze",
        "أصول": "assets",
        "بنك": "bank",
        "مركزي": "central",
        "كويت": "kuwait",
        "سعودية": "saudi",
        "الإمارات": "uae",
        "خطر": "risk",
        "عالي": "high",
        "ممنوع": "prohibited",
        "محظور": "banned",
        "تمويل": "financing",
        "جرائم": "crimes",
        "مالية": "financial",
        "تهريب": "smuggling",
        "تهرب ضريبي": "tax evasion",
        "شخص معرض سياسيا": "politically exposed person",
        "عقوبة": "penalty",
        "مخالفة": "violation",
        "تبيض": "laundering",
        "أموال": "money",
        "شركة": "company",
        "تجارة": "trading",
        "استثمار": "investment",
        "تمويل الإرهاب": "terrorism financing",
        "قائمة سوداء": "blacklist",
        "قائمة رمادية": "grey list",
        "عقوبات دولية": "international sanctions",
        "مجلس الأمن": "security council",
        "الأمم المتحدة": "united nations",
        "مشتبه": "suspect",
        "متهم": "accused",
        "محكوم": "convicted",
        "سجن": "prison",
        "مصادرة": "confiscation",
        "حظر": "embargo",
        "قيود": "restrictions",
        "امتثال": "compliance",
        "غسل": "laundering",
        "أحمد": "Ahmed",
        "الصباح": "Al-Sabah",
        "محمد": "Mohammed",
        "علي": "Ali",
        "حسن": "Hassan",
    }

    ARABIC_RANGE = re.compile(r"[\u0600-\u06FF]")

    @classmethod
    def detect_language(cls, text: str) -> str:
        if not text:
            return "en"
        arabic_chars = len(cls.ARABIC_RANGE.findall(text))
        ratio = arabic_chars / max(len(text), 1)
        if ratio > 0.15:
            return "ar"
        if arabic_chars > 0:
            return "mixed"
        return "en"

    @classmethod
    def translate_arabic_to_english(cls, text: str) -> str:
        result = text
        for ar, en in sorted(cls.ARABIC_TRANSLATIONS.items(), key=lambda x: -len(x[0])):
            result = result.replace(ar, en)
        return result
