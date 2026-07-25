"""Нормализация и устойчивые отпечатки текстов сообщений."""

import hashlib
import re


_ZERO_WIDTH_RE = re.compile(r"[\u200b-\u200d\u2060\ufeff]")
_TELEGRAM_LINK_RE = re.compile(
    r"(?<![\w.])(?:https?://)?t\.me/\S+",
    re.IGNORECASE,
)
_WHITESPACE_RE = re.compile(r"\s+")


def normalize_text_for_fingerprint(text: str) -> str:
    """Нормализует текст для сравнения Telegram-кросспостов."""
    normalized = text.lower().replace("ё", "е")
    normalized = _ZERO_WIDTH_RE.sub("", normalized)
    normalized = _TELEGRAM_LINK_RE.sub("", normalized)
    return _WHITESPACE_RE.sub(" ", normalized).strip()


def build_text_hash(text: str) -> str:
    """Строит SHA-256 хэш нормализованного текста."""
    normalized = normalize_text_for_fingerprint(text)
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()
