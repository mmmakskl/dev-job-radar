"""Нормализация и устойчивые отпечатки текстов сообщений."""

import hashlib
import re
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit


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


_GROUP_TEXT_RE = re.compile(r"[^\w]+", re.UNICODE)
_TRACKING_QUERY_KEYS = {'fbclid', 'gclid', 'yclid', 'mc_cid', 'mc_eid'}


def normalize_group_text(value: str | None) -> str | None:
    """Normalizes known company/title/contact values without fuzzy matching."""
    if not value:
        return None
    normalized = value.casefold().replace('ё', 'е').strip()
    normalized = _GROUP_TEXT_RE.sub(' ', normalized)
    normalized = _WHITESPACE_RE.sub(' ', normalized).strip()
    return normalized or None


def normalize_application_url(value: str | None) -> str | None:
    """Removes harmless URL presentation/tracking differences for exact matching."""
    if not value:
        return None
    candidate = value.strip()
    if not candidate:
        return None
    parsed = urlsplit(candidate if '://' in candidate else f'https://{candidate}')
    if not parsed.netloc:
        return None
    query = sorted(
        (key, item)
        for key, item in parse_qsl(parsed.query, keep_blank_values=True)
        if not key.casefold().startswith('utm_')
        and key.casefold() not in _TRACKING_QUERY_KEYS
    )
    path = parsed.path.rstrip('/') or '/'
    return urlunsplit(
        (
            parsed.scheme.casefold(),
            parsed.netloc.casefold(),
            path,
            urlencode(query, doseq=True),
            '',
        )
    )
