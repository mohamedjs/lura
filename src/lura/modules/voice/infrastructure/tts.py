"""Gemini 3.8 Flash TTS, two routes: Google directly, or through OpenRouter.

Same model and the same 30 voices either way. Each route has the Lite TTS
model behind it, used only when the failure is one a different model could
survive (rate limit, out of credit, overload, timeout) — a 400 means our
request was wrong, and retrying it elsewhere just spends money to fail again.
"""

from __future__ import annotations

import base64
import logging
import time

from ....shared.http import HttpError, request_bytes, request_json
from .audio import decode_audio, resample

log = logging.getLogger(__name__)

GEMINI_BASE = "https://generativelanguage.googleapis.com/v1beta"
OPENROUTER_BASE = "https://openrouter.ai/api/v1"

#: A stalled synthesis is dead air; past this it counts as a failure.
TTS_TIMEOUT = 25


def _lite(model: str) -> str:
    return model.replace("flash-tts", "flash-lite-tts") if "lite" not in model else ""


def _directed(text: str, style: str) -> str:
    # Gemini TTS takes a spoken-style direction in plain words before a colon.
    return f"Say in a {style} voice: {text}" if style.strip() else text


class GeminiTTS:
    def __init__(self, key: str, model: str, voice: str, style: str = "", lite_fallback: bool = True):
        self._key, self._model, self._voice, self._style = key, model, voice, style
        #: Off when another provider stands behind this one: the Lite model
        #: shares the exhausted quota more often than not.
        self._lite_fallback = lite_fallback

    def _once(self, model: str, text: str) -> bytes:
        payload = {
            "contents": [{"role": "user", "parts": [{"text": _directed(text, self._style)}]}],
            "generationConfig": {
                "responseModalities": ["AUDIO"],
                "speechConfig": {"voiceConfig": {"prebuiltVoiceConfig": {"voiceName": self._voice}}},
            },
        }
        started = time.monotonic()
        data = request_json(f"{GEMINI_BASE}/models/{model}:generateContent",
                            {"x-goog-api-key": self._key}, payload, timeout=TTS_TIMEOUT)
        pcm = b""
        for candidate in data.get("candidates") or []:
            for part in (candidate.get("content") or {}).get("parts") or []:
                inline = part.get("inlineData") or part.get("inline_data")
                if inline and inline.get("data"):
                    raw = base64.b64decode(inline["data"])
                    mime = inline.get("mimeType") or inline.get("mime_type") or ""
                    pcm += decode_audio(raw, mime) if "wav" in mime else _pcm_at(raw, mime)
        if not pcm:
            reason = (data.get("promptFeedback") or {}).get("blockReason") or "no audio in reply"
            raise HttpError(f"Gemini TTS returned no audio ({reason})")
        log.info("tts: %s said %d chars in %.1fs", model, len(text), time.monotonic() - started)
        return pcm

    def synthesize(self, text: str) -> bytes:
        try:
            return self._once(self._model, text)
        except HttpError as exc:
            if not exc.retryable or not _lite(self._model) or not self._lite_fallback:
                raise
            log.warning("TTS %s failed (%s); trying %s", self._model, exc.status, _lite(self._model))
            return self._once(_lite(self._model), text)


class OpenRouterTTS:
    def __init__(self, key: str, model: str, voice: str, style: str = "", lite_fallback: bool = True):
        self._key, self._model, self._voice, self._style = key, model, voice, style
        #: Off when another provider stands behind this one: the Lite model
        #: shares the exhausted quota more often than not.
        self._lite_fallback = lite_fallback
        #: Raw PCM needs no decoder; MP3 needs ffmpeg, which Windows lacks.
        #: Ask for PCM and fall back to MP3 once if the route refuses it.
        self._format = "pcm"

    def _once(self, model: str, text: str) -> bytes:
        payload = {"model": model, "input": _directed(text, self._style), "voice": self._voice,
                   "response_format": self._format}
        headers = {"Authorization": f"Bearer {self._key}",
                   "HTTP-Referer": "https://github.com/mohamedjs/lura", "X-Title": "Lura"}
        started = time.monotonic()
        try:
            audio, kind = request_bytes(f"{OPENROUTER_BASE}/audio/speech", headers, payload,
                                        timeout=TTS_TIMEOUT)
        except HttpError as exc:
            if exc.status == 400 and self._format == "pcm" and "format" in str(exc).lower():
                self._format = "mp3"
                return self._once(model, text)
            raise
        log.info("tts: %s said %d chars in %.1fs", model, len(text), time.monotonic() - started)
        return decode_audio(audio, kind or ("audio/pcm" if self._format == "pcm" else "audio/mpeg"))

    def synthesize(self, text: str) -> bytes:
        try:
            return self._once(self._model, text)
        except HttpError as exc:
            if not exc.retryable or not _lite(self._model) or not self._lite_fallback:
                raise
            log.warning("TTS %s failed (%s); trying %s", self._model, exc.status, _lite(self._model))
            return self._once(_lite(self._model), text)


def _pcm_at(raw: bytes, mime: str) -> bytes:
    rate = 24_000
    for part in mime.replace(";", " ").split():
        if part.startswith("rate="):
            rate = int(part[5:])
    return resample(raw, rate)
