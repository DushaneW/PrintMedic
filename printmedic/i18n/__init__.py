"""Tiny key-based translation layer (English, Polish). Missing keys fall back to English, then the key."""

from __future__ import annotations

from typing import Any

from printmedic.i18n.en import STRINGS as EN
from printmedic.i18n.pl import STRINGS as PL

LANGUAGES = {"en": EN, "pl": PL}
_current = "en"


class _Safe(dict[str, Any]):
    def __missing__(self, key: str) -> str:
        return "{" + key + "}"


def set_language(lang: str) -> None:
    global _current
    _current = lang if lang in LANGUAGES else "en"


def language() -> str:
    return _current


def tr(key: str, /, **kwargs: Any) -> str:
    text = LANGUAGES[_current].get(key) or EN.get(key)
    if text is None:
        # dynamic keys like "cond.media-jam" fall back to a readable form of their last segment
        text = key.split(".", 1)[-1].replace("_", " ").replace("-", " ") if "." in key else key
    if kwargs:
        try:
            return text.format_map(_Safe(kwargs))
        except (ValueError, IndexError):
            return text
    return text
