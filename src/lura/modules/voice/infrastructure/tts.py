"""Gemini 3.8 Flash TTS, two routes: Google directly, or through OpenRouter.

Same model and the same 30 voices either way. Each route has the Lite TTS
model behind it, used only when the failure is one a different model could
survive (rate limit, out of credit, overload, timeout) — a 400 means our
request was wrong, and retrying it elsewhere just spends money to fail again.

Google's route also streams (the Interactions API): audio arrives while the
sentence is still being synthesised, so the first word plays about a second
sooner than waiting for the whole clip.
"""

from __future__ import annotations

import base64
import json
import logging
import time
from collections.abc import Iterator

from ....shared.http import HttpError, request_bytes, request_json, stream_bytes, stream_sse
from .audio import decode_audio, raw_pcm_rate, resample

log = logging.getLogger(__name__)

GEMINI_BASE = "https://generativelanguage.googleapis.com/v1beta"
OPENROUTER_BASE = "https://openrouter.ai/api/v1"

#: A stalled synthesis is dead air; past this it counts as a failure.
TTS_TIMEOUT = 25


def _lite(model: str) -> str:
    """Gemini's cheaper TTS model to fall back on; other speech models have none."""
    return model.replace("flash-tts", "flash-lite-tts") if "flash-tts" in model and "lite" not in model else ""


def is_gemini_tts(model: str) -> bool:
    return "gemini" in model and "tts" in model


def _directed(text: str, style: str) -> str:
    # Gemini TTS takes a spoken-style direction in plain words before a colon.
    return f"Say in a {style} voice: {text}" if style.strip() else text


class StreamSupport:
    """Whether Google's streaming route works for this key.

    Kept by ``VoiceService``, not per synthesizer: a new synthesizer is built
    for every reply, and a refused streaming request must not be paid again
    for every sentence.
    """

    def __init__(self):
        self.ok = True


def _stream_status(error: dict) -> int:
    """A mid-stream error event → the HTTP status the fallbacks already understand."""
    text = f"{error.get('code', '')} {error.get('message', '')}".lower()
    for words, status in ((("exhaust", "quota", "rate limit", "429"), 429),
                          (("unavailable", "overload", "503"), 503),
                          (("deadline", "timeout", "504"), 504),
                          (("invalid", "not found", "unsupported", "400", "404"), 400)):
        if any(w in text for w in words):
            return status
    return 500


class GeminiTTS:
    def __init__(self, key: str, model: str, voice: str, style: str = "", lite_fallback: bool = True,
                 streaming: StreamSupport | None = None):
        self._key, self._model, self._voice, self._style = key, model, voice, style
        #: Off when another provider stands behind this one: the Lite model
        #: shares the exhausted quota more often than not.
        self._lite_fallback = lite_fallback
        self._streaming = streaming if streaming is not None else StreamSupport()

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

    def _stream_once(self, model: str, text: str) -> Iterator[bytes]:
        body = {"model": model, "input": _directed(text, self._style),
                "response_format": {"type": "audio", "mime_type": "audio/l16", "sample_rate": 24_000},
                "generation_config": {"speech_config": [{"voice": self._voice}]},
                # These are someone's spoken replies: nothing kept server-side.
                "store": False, "stream": True}
        started, first, size = time.monotonic(), None, 0
        for _event, data in stream_sse(f"{GEMINI_BASE}/interactions", {"x-goog-api-key": self._key}, body,
                                       timeout=TTS_TIMEOUT):
            try:
                event = json.loads(data)
            except json.JSONDecodeError:
                continue   # the closing "[DONE]"
            kind = event.get("event_type")
            if kind == "error":
                error = event.get("error") or {}
                raise HttpError(f"Gemini TTS stream: {error.get('message') or error}", _stream_status(error))
            delta = event.get("delta") or {}
            if kind == "step.delta" and delta.get("type") == "audio" and delta.get("data"):
                pcm = _pcm_at(base64.b64decode(delta["data"]), f"rate={delta.get('sample_rate') or 24_000}")
                if first is None:
                    first = time.monotonic() - started
                size += len(pcm)
                yield pcm
        if first is None:
            raise HttpError("Gemini TTS stream returned no audio")
        log.info("tts: %s streamed %d chars, first audio %.2fs, %.1fs of speech", model, len(text), first,
                 size / (2 * 24_000))

    def stream(self, text: str) -> Iterator[bytes]:
        """Speech as it is synthesised. A failure before any audio falls back to
        the whole-clip route; after some audio it is raised (replaying the
        sentence from the start would say it twice)."""
        if not self._streaming.ok:
            yield self.synthesize(text)
            return
        started = False
        try:
            for pcm in self._stream_once(self._model, text):
                started = True
                yield pcm
        except HttpError as exc:
            if started:
                raise
            if exc.status in (400, 404):
                # The request shape was refused: this key or region does not
                # stream. Speak the way 2.2 did, and stop asking.
                self._streaming.ok = False
                log.warning("TTS streaming unavailable (%s); using whole clips.", str(exc)[:160])
                yield self.synthesize(text)
                return
            if not exc.retryable or not _lite(self._model) or not self._lite_fallback:
                raise
            log.warning("TTS %s failed (%s); trying %s", self._model, exc.status, _lite(self._model))
            yield self._once(_lite(self._model), text)


class OpenRouterTTS:
    def __init__(self, key: str, model: str, voice: str, style: str = "", lite_fallback: bool = True):
        self._key, self._model, self._voice, self._style = key, model, voice, style
        #: Off when another provider stands behind this one: the Lite model
        #: shares the exhausted quota more often than not.
        self._lite_fallback = lite_fallback
        #: Raw PCM needs no decoder; MP3 needs ffmpeg, which Windows lacks.
        #: Ask for PCM and fall back to MP3 once if the route refuses it.
        self._format = "pcm"

    def _request(self, model: str, text: str) -> tuple[str, dict, dict]:
        payload = {"model": model, "input": _directed(text, self._style), "response_format": self._format}
        if self._voice:                     # else the model's own default voice
            payload["voice"] = self._voice
        headers = {"Authorization": f"Bearer {self._key}",
                   "HTTP-Referer": "https://github.com/mohamedjs/lura", "X-Title": "Lura"}
        return f"{OPENROUTER_BASE}/audio/speech", headers, payload

    def _once(self, model: str, text: str) -> bytes:
        url, headers, payload = self._request(model, text)
        started = time.monotonic()
        try:
            audio, kind = request_bytes(url, headers, payload, timeout=TTS_TIMEOUT)
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

    def _stream_once(self, model: str, text: str) -> Iterator[bytes]:
        """The body is raw audio bytes; raw PCM plays as it downloads, WAV or
        MP3 is decoded once complete."""
        url, headers, payload = self._request(model, text)
        started, first, size = time.monotonic(), None, 0
        rate, whole, carry, kind = None, [], b"", ""
        for chunk, kind in stream_bytes(url, headers, payload, timeout=TTS_TIMEOUT):
            if not whole and rate is None and first is None:
                rate = raw_pcm_rate(kind, chunk)
            if rate is None:
                whole.append(chunk)
                continue
            data = carry + chunk
            cut = len(data) - len(data) % 2     # never split a sample across reads
            carry = data[cut:]
            if cut:
                pcm = resample(data[:cut], rate)
                first = time.monotonic() - started if first is None else first
                size += len(pcm)
                yield pcm
        if whole:
            pcm = decode_audio(b"".join(whole), kind or "audio/mpeg")
            if pcm:
                first = time.monotonic() - started
                size += len(pcm)
                yield pcm
        if first is None:
            raise HttpError("OpenRouter TTS returned no audio")
        log.info("tts: %s streamed %d chars, first audio %.2fs, %.1fs of speech", model, len(text), first,
                 size / (2 * 24_000))

    def stream(self, text: str) -> Iterator[bytes]:
        if self._format != "pcm":
            yield self.synthesize(text)   # MP3 cannot be played until it is whole
            return
        started = False
        try:
            for pcm in self._stream_once(self._model, text):
                started = True
                yield pcm
        except HttpError as exc:
            if started:
                raise
            if exc.status == 400 and "format" in str(exc).lower():
                self._format = "mp3"
                yield self.synthesize(text)
                return
            if not exc.retryable or not _lite(self._model) or not self._lite_fallback:
                raise
            log.warning("TTS %s failed (%s); trying %s", self._model, exc.status, _lite(self._model))
            yield self._once(_lite(self._model), text)


def speech_models() -> list[dict]:
    """OpenRouter's text-to-speech models and the voices each one has (public, no key)."""
    data = request_json(f"{OPENROUTER_BASE}/models?output_modalities=speech", timeout=20).get("data", [])
    return [{"id": m["id"], "name": m.get("name", m["id"]), "voices": list(m.get("supported_voices") or []),
             "free": m["id"].endswith(":free") or str((m.get("pricing") or {}).get("prompt")) == "0"}
            for m in data if "speech" in ((m.get("architecture") or {}).get("output_modalities") or [])]


def _pcm_at(raw: bytes, mime: str) -> bytes:
    rate = 24_000
    for part in mime.replace(";", " ").split():
        if part.startswith("rate="):
            rate = int(part[5:])
    return resample(raw, rate)
