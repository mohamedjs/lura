"""ElevenLabs: speaking (text-to-speech) and hearing (Scribe speech-to-text).

One key does both, so ElevenLabs can be the whole voice, like Gemini or
OpenRouter. Speech is asked for as raw 24 kHz PCM, Lura's own rate, and
plays while it downloads. Voices come from the user's account (the default
voices plus any they added or cloned), each labelled female or male.
"""

from __future__ import annotations

import logging
import time
import uuid
from collections.abc import Iterator

from ....shared.http import HttpError, request_json, stream_bytes
from .transcribe import HEARING_TIMEOUT
from .tts import TTS_TIMEOUT

log = logging.getLogger(__name__)

BASE = "https://api.elevenlabs.io"
STT_MODEL = "scribe_v2"
#: George, the voice in ElevenLabs' own quickstart: used only when the account's
#: voices cannot be listed. Otherwise the first voice of the chosen gender speaks.
FALLBACK_VOICE = "JBFqnCBsd6RMkjVDRZzb"


def _headers(key: str) -> dict[str, str]:
    return {"xi-api-key": key}


class ElevenLabsTTS:
    """Implements ``SpeechSynthesizer``."""

    def __init__(self, key: str, model: str, voice: str):
        self._key, self._model, self._voice = key, model, voice

    def stream(self, text: str) -> Iterator[bytes]:
        started, first, size, carry = time.monotonic(), None, 0, b""
        for chunk, _kind in stream_bytes(f"{BASE}/v1/text-to-speech/{self._voice}/stream?output_format=pcm_24000",
                                         _headers(self._key), {"text": text, "model_id": self._model},
                                         timeout=TTS_TIMEOUT):
            data = carry + chunk
            cut = len(data) - len(data) % 2     # never split a sample across reads
            carry = data[cut:]
            if cut:
                first = time.monotonic() - started if first is None else first
                size += cut
                yield data[:cut]
        if first is None:
            raise HttpError("ElevenLabs returned no audio")
        log.info("tts: elevenlabs %s streamed %d chars, first audio %.2fs, %.1fs of speech", self._model,
                 len(text), first, size / (2 * 24_000))

    def synthesize(self, text: str) -> bytes:
        return b"".join(self.stream(text))


class ElevenLabsTranscriber:
    """Implements ``Transcriber`` with Scribe; it detects Arabic or English by itself."""

    def __init__(self, key: str, model: str = STT_MODEL):
        self._key, self._model = key, model

    def transcribe(self, wav: bytes) -> str:
        boundary = uuid.uuid4().hex
        fields = {"model_id": self._model, "tag_audio_events": "false"}   # no "(coughs)" in the transcript
        body = b"".join(f'--{boundary}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode()
                        for k, v in fields.items())
        body += (f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="speech.wav"\r\n'
                 f"Content-Type: audio/wav\r\n\r\n").encode() + wav + f"\r\n--{boundary}--\r\n".encode()
        started = time.monotonic()
        data = request_json(f"{BASE}/v1/speech-to-text", _headers(self._key), timeout=HEARING_TIMEOUT,
                            raw=(body, f"multipart/form-data; boundary={boundary}"))
        text = (data.get("text") or "").strip()
        log.info("heard (%.1fs): %s", time.monotonic() - started, text)
        return text


def voices(key: str) -> list[dict]:
    """The account's voices: id, name, gender ("female"/"male"/"") and accent."""
    out = []
    for v in request_json(f"{BASE}/v2/voices?page_size=100", _headers(key), timeout=20).get("voices", []):
        labels = v.get("labels") or {}
        gender = str(labels.get("gender", "")).lower()
        out.append({"id": v["voice_id"], "name": v.get("name", v["voice_id"]),
                    "gender": gender if gender in ("female", "male") else "",
                    "accent": labels.get("accent", "")})
    return out


def check_key(key: str) -> str:
    """Raises HttpError with ElevenLabs' own words if the key is bad."""
    try:
        return f"OK — {len(voices(key))} voices available"
    except HttpError as exc:
        # A key limited to speech and transcription may not read the voice list: still a good key.
        if "permission" in str(exc).lower():
            return "OK — this key cannot list voices, so the default voice speaks"
        raise
