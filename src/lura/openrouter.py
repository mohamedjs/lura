"""The OpenRouter path: transcribe, ask any model, speak the reply.

Unlike Gemini Live this is turn-based, not full-duplex — three HTTP calls per
exchange rather than one streaming socket. Slower, and you cannot interrupt
mid-answer. What you get for it is every model on OpenRouter.

Transcription goes to OpenRouter rather than the local Vosk model on purpose:
Vosk is here for the wake word, where a two-word grammar makes it accurate and
free, but `vosk-model-small-en-us-0.15` is en-US only. Sending Arabic through
it would return confident nonsense that the model would then answer.
"""

from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request
from typing import Any

from .config import Settings

log = logging.getLogger(__name__)

BASE = "https://openrouter.ai/api/v1"
TIMEOUT = 120

# OpenRouter asks callers to identify themselves; it is also what makes the
# app show up in your usage dashboard rather than as anonymous traffic.
HEADERS = {
    "HTTP-Referer": "https://github.com/local/lura",
    "X-Title": "lura",
}


class OpenRouterError(RuntimeError):
    """An API error worth showing the user verbatim."""


def _request(path: str, key: str, payload: dict | None = None, raw: bool = False):
    url = f"{BASE}{path}"
    headers = {"Authorization": f"Bearer {key}", **HEADERS}

    data = None
    if payload is not None:
        data = json.dumps(payload).encode()
        headers["Content-Type"] = "application/json"

    request = urllib.request.Request(url, data=data, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
            body = response.read()
            return body if raw else json.loads(body)
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode(errors="replace")[:500]
        # Verbatim: "model not found" and "out of credit" need different fixes,
        # and only the server knows which one this is.
        raise OpenRouterError(f"HTTP {exc.code} from {path}: {detail}") from None
    except urllib.error.URLError as exc:
        raise OpenRouterError(f"Cannot reach OpenRouter: {exc.reason}") from None


def check_key(key: str) -> dict[str, Any]:
    """Key metadata: label, usage, limit. Distinguishes invalid from spent."""
    return _request("/key", key).get("data", {})


def list_models(key: str | None = None) -> list[dict]:
    """Every model. Needs no key, so `models` works before you have one."""
    request = urllib.request.Request(f"{BASE}/models", headers=HEADERS)
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.loads(response.read()).get("data", [])


def model_exists(model_id: str) -> tuple[bool, list[str]]:
    """Is this model real? If not, what looks close?

    With 445 models a typo is the likeliest failure, and it must not read as a
    broken app.
    """
    try:
        ids = [m["id"] for m in list_models()]
    except Exception:
        return True, []  # offline: do not block on a check we cannot make

    if model_id in ids:
        return True, []

    needle = model_id.lower().split("/")[-1]
    near = [i for i in ids if needle[:8] and needle[:8] in i.lower()][:8]
    return False, near


def transcribe(settings: Settings, key: str, wav: bytes) -> str:
    """Speech to text."""
    import base64

    # OpenRouter wants input_audio {data, format}; "file"/"filename" is rejected
    # with HTTP 400 invalid_union (found by running it, 2026-10).
    payload = {
        "model": settings.openrouter_stt_model,
        "input_audio": {"data": base64.b64encode(wav).decode(), "format": "wav"},
    }
    if settings.language:
        payload["language"] = settings.language.split("-")[0]

    result = _request("/audio/transcriptions", key, payload)
    return (result.get("text") or "").strip()


def chat(settings: Settings, key: str, history: list[dict]) -> str:
    """Ask the chosen model. `history` is OpenAI-shaped messages."""
    payload = {
        "model": settings.openrouter_model,
        "messages": [
            {"role": "system", "content": settings.system_instruction},
            *history,
        ],
    }
    result = _request("/chat/completions", key, payload)

    choices = result.get("choices") or []
    if not choices:
        raise OpenRouterError(f"No reply in response: {json.dumps(result)[:300]}")
    return (choices[0].get("message", {}).get("content") or "").strip()


def speak(settings: Settings, key: str, text: str) -> bytes:
    """Text to speech. Returns WAV bytes."""
    payload = {
        "model": settings.openrouter_tts_model,
        "input": text,
        "voice": settings.openrouter_tts_voice,
        "response_format": "wav",
    }
    return _request("/audio/speech", key, payload, raw=True)


def play_wav(data: bytes, device=None) -> None:
    """Play a WAV blob. Its own sample rate, not the Live API's 24kHz."""
    import io
    import wave

    import numpy as np
    import sounddevice as sd

    with wave.open(io.BytesIO(data), "rb") as handle:
        rate = handle.getframerate()
        channels = handle.getnchannels()
        frames = handle.readframes(handle.getnframes())

    audio = np.frombuffer(frames, dtype=np.int16)
    if channels > 1:
        audio = audio.reshape(-1, channels)

    sd.play(audio, samplerate=rate, device=device, blocking=True)


class Conversation:
    """One wake-to-idle exchange, turn by turn."""

    def __init__(self, settings: Settings, key: str):
        self.settings = settings
        self.key = key
        self.history: list[dict] = []

    def run(self) -> None:
        from .wake import pcm_to_wav, record_utterance

        while True:
            pcm = record_utterance(self.settings)
            if not pcm:
                log.info("Nothing said — closing.")
                return

            said = transcribe(self.settings, self.key, pcm_to_wav(pcm))
            if not said:
                log.info("Could not make that out — closing.")
                return

            log.info("you: %s", said)
            self.history.append({"role": "user", "content": said})

            reply = chat(self.settings, self.key, self.history)
            log.info("%s: %s", self.settings.openrouter_model, reply)
            self.history.append({"role": "assistant", "content": reply})

            if reply:
                play_wav(speak(self.settings, self.key, reply), self.settings.output_device)
