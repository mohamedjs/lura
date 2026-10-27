"""Translations, Arabic and English.

One JSON catalog per language, shared by the dashboard (served to the browser
as-is) and by the backend for the few strings it says or prints itself. A key
missing from Arabic falls back to English, and a key missing everywhere comes
back as itself, so a gap reads as a visible key rather than a crash.
"""

from __future__ import annotations

import functools
import json
from pathlib import Path

LANGUAGES: dict[str, dict[str, str]] = {
    "en": {"name": "English", "dir": "ltr", "speech": "English"},
    "ar": {"name": "العربية", "dir": "rtl", "speech": "Egyptian Arabic"},
}
DEFAULT = "en"


@functools.lru_cache(maxsize=None)
def catalog(lang: str) -> dict[str, str]:
    path = Path(__file__).with_name(f"{lang}.json")
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def t(key: str, lang: str = DEFAULT, **values: object) -> str:
    text = catalog(lang).get(key) or catalog(DEFAULT).get(key) or key
    return text.format(**values) if values else text


def normalise(lang: str | None) -> str:
    lang = (lang or "").lower()[:2]
    return lang if lang in LANGUAGES else DEFAULT
