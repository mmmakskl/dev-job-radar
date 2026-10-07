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

_GENERAL_ROLE = re.compile(
    r"\b(?:developer|engineer|разработчик\w*|инженер\w*|qa|tester|"
    r"devops|sre|analyst|аналитик\w*|designer|дизайнер\w*|"
    r"recruiter|рекрутер\w*|hr|manager|менеджер\w*|marketer|маркетолог\w*|"
    r"редактор\w*|писатель|копирайтер\w*|креатор\w*|геймдизайнер\w*|"
    r"администратор\w*|архитектор\w*|художник\w*|таргетоолог\w*|"
    r"sales|support|поддержк\w*|seo|smm|pr|copywriter|creator|scientist|"
    r"architect|artist|writer|account)\b",
    re.IGNORECASE,
)
_COURSE_AD = re.compile(
    r"\b(?:курс\w*|обучени\w*|вебинар\w*|bootcamp|course|webinar)\b", re.I
)


def universal_prefilter(text: str) -> bool:
    """Cheap, broad gate for hiring posts across roles in the role catalog."""
    if not text or candidate_profile_reasons(text):
        return False
    # Training benefits are common in real vacancies. Reject course mentions
    # only without explicit recruitment; ambiguous hiring goes to the classifier.
    explicit_hiring = bool(
        re.search(
            r"\b(?:ищем|нанимаем|требуется|вакансия|hiring|vacancy|join our team)\b",
            text,
            re.I,
        )
    )
    course_promotion = re.search(
        r"^\s*(?:курс\w*|вебинар\w*|bootcamp|course|webinar)\b|"
        r"\b(?:записывай\w*|запиш\w*|регистрируй\w*|enroll|sign up)\b",
        text,
        re.I,
    )
    if _COURSE_AD.search(text) and course_promotion and not explicit_hiring:
        return False
    hiring = any(re.search(pattern, text, re.I) for pattern in _HIRING_SIGNAL_PATTERNS)
    # An explicit role title can be ambiguous; pass it to the classifier.
    return hiring or bool(_GENERAL_ROLE.search(text))


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
