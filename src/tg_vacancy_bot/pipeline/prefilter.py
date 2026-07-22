"""Быстрый префильтр сообщений до обращения к LLM."""

import re
from collections.abc import Sequence


def contains_keywords(text: str, keywords: Sequence[str]) -> bool:
    """Возвращает True, если текст содержит отдельное ключевое слово."""
    if not text:
        return False

    return any(
        re.search(rf"\b{re.escape(keyword)}\b", text, re.IGNORECASE)
        for keyword in keywords
    )

