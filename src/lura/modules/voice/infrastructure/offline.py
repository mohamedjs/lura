"""Offline speech router: tiny-voca-arabic for Arabic script, Piper otherwise.

The offline voice is two engines: tiny-voca-arabic reads sentences containing
Arabic script (far more natural than Piper's Arabic voice) and falls back to
Piper on any error or when not installed; English sentences always go to Piper.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator

from . import arabic_voice, piper
from .piper import _ARABIC, PiperTTS

log = logging.getLogger(__name__)


class OfflineTTS:
    """Implements ``SpeechSynthesizer`` by routing each sentence to tiny-voca or Piper."""

    def __init__(self, arabic=None, piper_tts=None):
        self._arabic = arabic or arabic_voice.TinyVocaArabicTTS()
        self._piper = piper_tts or PiperTTS()

    def synthesize(self, text: str) -> bytes:
        if _ARABIC.search(text) and arabic_voice.ready():
            try:
                return self._arabic.synthesize(text)
            except Exception as exc:
                log.warning("Offline Arabic voice failed (%s); using Piper.", str(exc)[:120])
        return self._piper.synthesize(text)

    def stream(self, text: str) -> Iterator[bytes]:
        pcm = self.synthesize(text)
        if pcm:
            yield pcm


def ready() -> bool:
    return piper.ready() or arabic_voice.ready()


def ensure() -> bool:
    p = piper.ensure()
    a = arabic_voice.ensure()
    return p or a
