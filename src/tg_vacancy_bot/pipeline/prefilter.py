"""Быстрый префильтр сообщений до обращения к LLM."""

import re
from collections.abc import Sequence

_DIRECT_RESUME_PATTERNS = [
    (r"(?<!\w)#\s*резюме\b", "#резюме"),
    (r"\bopen\s+to\s+work\b", "open to work"),
    (r"\blooking\s+for\s+(?:a\s+)?(?:job|work)\b", "looking for work"),
    (r"\bищ[уе]?\s+работ[уы]\b", "ищу работу"),
    (r"\bв\s+поиске\s+работ[уы]\b", "в поиске работы"),
    (r"\bрассматриваю\s+предложения\b", "рассматриваю предложения"),
    (r"\bготов(?:а)?\s+приступить\b", "готов приступить"),
]

_RESUME_SIGNAL_PATTERNS = [
    (r"\bрезюме\b", "резюме"),
    (r"\bcv\b", "cv"),
    (r"\bкандидат(?:ка|ы|ов|ам|ами|е)?\b", "кандидат"),
    (r"\bопыт\s+работы\b", "опыт работы"),
    (r"\bжелаем(?:ая|ый|ое)\s+зарплат[аы]\b", "желаемая зарплата"),
    (r"\bожидаем(?:ая|ый|ое)\s+зарплат[аы]\b", "ожидаемая зарплата"),
    (r"\bзарплатные\s+ожидания\b", "зарплатные ожидания"),
    (r"\b(?:мой|моя|мои)\s+(?:стек|опыт|контакт)", "мой стек/опыт/контакт"),
    (r"\bобо\s+мне\b", "обо мне"),
]

_HIRING_SIGNAL_PATTERNS = [
    r"\bваканси[яиюе]\b",
    r"\bищем\b",
    r"\bтребуется\b",
    r"\bнанимаем\b",
    r"\bприглашаем\b",
    r"\bмы\s+ищем\b",
    r"\bобязанности\b",
    r"\bтребования\b",
    r"\bусловия\b",
    r"\bотправ(?:ить|ляйте)\s+резюме\b",
    r"\bsend\s+(?:your\s+)?cv\b",
]


def contains_keywords(text: str, keywords: Sequence[str]) -> bool:
    """Возвращает True, если текст содержит отдельное ключевое слово."""
    if not text:
        return False

    return any(
        re.search(rf"\b{re.escape(keyword)}\b", text, re.IGNORECASE)
        for keyword in keywords
    )


def candidate_profile_reasons(text: str) -> list[str]:
    """Возвращает признаки того, что сообщение похоже на резюме кандидата."""
    if not text:
        return []

    direct_matches = [
        label
        for pattern, label in _DIRECT_RESUME_PATTERNS
        if re.search(pattern, text, re.IGNORECASE)
    ]
    if direct_matches:
        return direct_matches

    resume_matches = [
        label
        for pattern, label in _RESUME_SIGNAL_PATTERNS
        if re.search(pattern, text, re.IGNORECASE)
    ]
    if len(resume_matches) < 2:
        return []

    has_hiring_signal = any(
        re.search(pattern, text, re.IGNORECASE) for pattern in _HIRING_SIGNAL_PATTERNS
    )
    if has_hiring_signal:
        return []

    return resume_matches


def looks_like_candidate_profile(text: str) -> bool:
    """True для резюме/профилей кандидатов, которые не нужно анализировать как вакансии."""
    return bool(candidate_profile_reasons(text))
