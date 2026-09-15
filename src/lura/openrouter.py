"""The OpenRouter path: hear, think, speak — with tools and the hologram.

Turn-based rather than full-duplex like Gemini Live: record an utterance, send
it, speak the reply. You cannot interrupt mid-answer. What you get for it is
every model on OpenRouter, and a bill measured in fractions of a cent.

Two decisions worth knowing about:

*Audio goes straight into the chat model.* There is a ``/audio/transcriptions``
endpoint, but the default model reads audio natively, so transcribing first
would be a second network round trip that can only lose information. The model
hears the recording.

*Speech comes back from a free model, with a paid one behind it.* Measured, not
guessed: `hexgrad/kokoro-82m` is the cheapest voice on the service and it
cannot say a word of Arabic — it reads the letter names out loud. The models
below were each made to say an Arabic sentence and transcribed back to check
the words survived.
"""

from __future__ import annotations

import base64
import json
import logging
import re
import subprocess
import time
import urllib.error
import urllib.request
from typing import Any

from .config import OUTPUT_RATE, Settings

log = logging.getLogger(__name__)

BASE = "https://openrouter.ai/api/v1"
TIMEOUT = 120

# OpenRouter asks callers to identify themselves; it is also what makes the
# app show up in your usage dashboard rather than as anonymous traffic.
HEADERS = {
    "HTTP-Referer": "https://github.com/local/lura",
    "X-Title": "lura",
}

#: A tool-calling model can ask for tools forever. It gets this many rounds,
#: then we speak whatever it has said so far.
MAX_TOOL_ROUNDS = 5

#: Synthesis takes about as long as the text is long: the boot briefing is ~450
#: characters and took twelve seconds to come back as one blob, twelve seconds
#: of silence after login before Lura says anything. Sentences are sent
#: separately and the next one is fetched while the current one plays, which
#: puts the first word about two seconds after the model finishes.
SPEECH_CHUNK_CHARS = 140

#: Sentence ends in Latin and Arabic script, plus the newline that separates
#: the briefing's bullet points.
_SENTENCE_END = re.compile(r"(?<=[.!?…۔؟]|\n)\s+")


def split_for_speech(text: str, limit: int = SPEECH_CHUNK_CHARS) -> list[str]:
    """Break a reply into speakable pieces on sentence boundaries.

    Never mid-word: a chunk that ends in the middle of a sentence is audible as
    a stumble. Sentences longer than the limit are left whole rather than cut.
    """
    chunks: list[str] = []
    current = ""
    for sentence in _SENTENCE_END.split(text.strip()):
        sentence = sentence.strip()
        if not sentence:
            continue
        if current and len(current) + len(sentence) + 1 > limit:
            chunks.append(current)
            current = sentence
        else:
            current = f"{current} {sentence}".strip()
    if current:
        chunks.append(current)
    return chunks


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
        raise OpenRouterError(f"HTTP {exc.code} from {path}: {detail}", exc.code) from None
    except urllib.error.URLError as exc:
        raise OpenRouterError(f"Cannot reach OpenRouter: {exc.reason}") from None


def _status_of(exc: OpenRouterError) -> int | None:
    """The HTTP status carried on an error, if it came from one."""
    return exc.args[1] if len(exc.args) > 1 and isinstance(exc.args[1], int) else None


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

    With hundreds of models a typo is the likeliest failure, and it must not
    read as a broken app.
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


# ── speech out ──────────────────────────────────────────────────────────────

def speak(settings: Settings, key: str, text: str) -> bytes:
    """Text to speech. Returns MP3 bytes.

    The configured model first, then ``openrouter_tts_fallback_model`` — but
    only when the failure is one a different model could survive. A 400 means
    the request was wrong, and retrying it elsewhere just spends money to fail
    the same way.
    """
    def _try(model: str) -> bytes:
        payload = {"model": model, "input": text, "response_format": "mp3"}
        if settings.openrouter_tts_voice:
            payload["voice"] = settings.openrouter_tts_voice
        return _request("/audio/speech", key, payload, raw=True)

    try:
        return _try(settings.openrouter_tts_model)
    except OpenRouterError as exc:
        status = _status_of(exc)
        fallback = settings.openrouter_tts_fallback_model
        # 429 rate limited, 402 out of credit, 5xx provider trouble: all worth
        # one try elsewhere. Anything else is our bug, not the model's.
        if not fallback or status not in (402, 408, 429, 500, 502, 503, 504):
            raise
        log.warning("TTS %s failed (%s); falling back to %s",
                    settings.openrouter_tts_model, status, fallback)
        return _try(fallback)


def mp3_to_pcm(data: bytes, rate: int = OUTPUT_RATE) -> bytes:
    """Decode MP3 to 16-bit mono PCM with ffmpeg.

    ffmpeg rather than a decoder library: it is already on the machine, and a
    voice assistant that cannot speak because a wheel failed to build is worse
    than one that shells out.
    """
    proc = subprocess.run(
        ["ffmpeg", "-nostdin", "-loglevel", "error", "-i", "pipe:0",
         "-f", "s16le", "-acodec", "pcm_s16le", "-ar", str(rate), "-ac", "1", "pipe:1"],
        input=data, capture_output=True, timeout=60,
    )
    if proc.returncode != 0 or not proc.stdout:
        # Empty output with a zero exit code is ffmpeg's way of saying the
        # input was not audio. Silence here must be explained, not swallowed.
        log.warning("ffmpeg could not decode the speech (%s): %s",
                    proc.returncode, proc.stderr.decode(errors="replace")[:300])
        return b""
    return proc.stdout


def write_pcm(stream, pcm: bytes, overlay=None, rate: int = OUTPUT_RATE) -> None:
    """Write 16-bit mono PCM to an open stream, driving the hologram's mouth.

    Written in ~50ms slices rather than one blocking call so the face tracks
    the speech instead of jumping once per reply. The stream is passed in and
    not opened here: a reply is spoken sentence by sentence, and opening the
    output device per sentence clicks on PipeWire and clips the tail of each
    one as the buffer is torn down mid-drain.
    """
    import numpy as np

    if not pcm:
        return

    block = int(rate * 0.05) * 2  # 50ms of int16
    for offset in range(0, len(pcm), block):
        part = pcm[offset:offset + block]
        if overlay is not None:
            samples = np.frombuffer(part[:len(part) - len(part) % 2], dtype=np.int16)
            if samples.size:
                rms = float(np.sqrt(np.mean(samples.astype(np.float32) ** 2))) / 32768.0
                overlay.amplitude = min(1.0, rms * 4.0)
        stream.write(part)


def open_output(device=None, rate: int = OUTPUT_RATE):
    """One output stream for a whole spoken reply."""
    import sounddevice as sd

    stream = sd.RawOutputStream(samplerate=rate, blocksize=int(rate * 0.05),
                                device=device, dtype="int16", channels=1)
    stream.start()
    return stream


# ── the model ───────────────────────────────────────────────────────────────

def chat(settings: Settings, key: str, history: list[dict], tools: list[dict] | None = None) -> dict:
    """One call. Returns the assistant message, which may carry tool calls."""
    from .tools import get_machine_context

    payload: dict[str, Any] = {
        "model": settings.openrouter_model,
        "messages": [
            {"role": "system", "content": settings.system_instruction + get_machine_context()},
            *history,
        ],
    }
    if tools:
        payload["tools"] = tools

    result = _request("/chat/completions", key, payload)
    choices = result.get("choices") or []
    if not choices:
        raise OpenRouterError(f"No reply in response: {json.dumps(result)[:300]}")
    return choices[0].get("message") or {}


class Conversation:
    """One wake-to-idle exchange, turn by turn."""

    def __init__(self, settings: Settings, key: str, overlay_state=None,
                 mcp_manager=None, initial_prompt: str | None = None):
        self.settings = settings
        self.key = key
        self.history: list[dict] = []
        self._overlay = overlay_state
        self._mcp = mcp_manager
        self._initial_prompt = initial_prompt
        self._ending = False
        self._stopped = False

    def stop(self) -> None:
        """Called from the signal handler on SIGTERM/SIGINT. Must be safe to
        call from any thread, and must not raise — it runs during shutdown."""
        self._stopped = True
        self._ending = True

    # ── hologram ────────────────────────────────────────────────────────────
    def _state(self, name: str) -> None:
        if self._overlay is not None:
            from .overlay import State
            self._overlay.state = State(name)

    def _transcript(self, text: str) -> None:
        if self._overlay is not None:
            self._overlay.transcript = text

    # ── tools ───────────────────────────────────────────────────────────────
    def _tools(self) -> list[dict]:
        from .tools import OPENAI_TOOLS

        tools = list(OPENAI_TOOLS)
        if self._mcp is not None:
            try:
                tools.extend(self._mcp.get_openai_tools())
            except Exception as exc:
                log.warning("Could not list MCP tools: %s", exc)
        return tools

    def _run_tool_calls(self, calls: list[dict]) -> None:
        """Answer every tool call the model made, in order."""
        from .tools import END_SESSION, dispatch

        for call in calls:
            fn = call.get("function") or {}
            name = fn.get("name", "")
            try:
                args = json.loads(fn.get("arguments") or "{}")
            except json.JSONDecodeError:
                args = {}

            log.info("tool %s %s", name, args)
            result = dispatch(name, args, self._mcp)
            if name == END_SESSION:
                self._ending = True

            # tool_call_id must come back exactly, or the next call is a 400.
            self.history.append({
                "role": "tool",
                "tool_call_id": call.get("id", ""),
                "content": result[:4000],
            })

    # ── one turn ────────────────────────────────────────────────────────────
    def _answer(self) -> str:
        """Call the model, run any tools it asks for, return what to say."""
        self._state("thinking")
        tools = self._tools()

        for _ in range(MAX_TOOL_ROUNDS):
            message = chat(self.settings, self.key, self.history, tools)
            self.history.append(message)

            calls = message.get("tool_calls") or []
            if not calls:
                return (message.get("content") or "").strip()

            self._run_tool_calls(calls)
            if self._ending:
                # Let it say goodbye, but do not let it start another errand.
                # The nudge is a user turn, not tool output: an instruction
                # inside a tool result gets ignored and the model signs off
                # with an empty message, so the assistant ends in silence.
                self.history.append({"role": "user", "content":
                    "Say a short goodbye, one sentence, in the language I was speaking."})
                message = chat(self.settings, self.key, self.history, None)
                self.history.append(message)
                return (message.get("content") or "").strip()

        # No canned sentence here: it would be spoken in English to whoever
        # asked, in whatever language they asked in. The closing chime says it.
        log.warning("Model kept asking for tools; giving up on this turn.")
        return ""

    def _synth(self, text: str) -> bytes:
        try:
            return mp3_to_pcm(speak(self.settings, self.key, text))
        except OpenRouterError as exc:
            log.warning("Could not speak %r: %s", text[:40], exc)
            return b""

    def _say(self, text: str) -> None:
        """Speak a reply, fetching each sentence while the last one plays."""
        from concurrent.futures import ThreadPoolExecutor

        if not text:
            return
        self._transcript(f"Lura: {text}")
        self._state("speaking")

        chunks = split_for_speech(text)
        stream = open_output(self.settings.output_device)
        try:
            with ThreadPoolExecutor(max_workers=1) as pool:
                pending = pool.submit(self._synth, chunks[0])
                for index, chunk in enumerate(chunks):
                    pcm = pending.result()
                    if index + 1 < len(chunks):
                        pending = pool.submit(self._synth, chunks[index + 1])
                    if self._stopped:
                        break
                    write_pcm(stream, pcm, self._overlay)
        except Exception as exc:
            log.warning("Audio playback warning: %s", exc)
        finally:
            stream.stop()
            stream.close()
            if self._overlay is not None:
                self._overlay.amplitude = 0.0  # or the mouth freezes half-open

    def run(self) -> None:
        """Talk until the user stops, says goodbye, or the session times out.

        Returns rather than raises on every ordinary ending: the caller loops
        back to the wake word, and an exception there is logged as a failure
        and leaves the assistant looking alive but deaf.
        """
        from .wake import record_utterance

        started = time.monotonic()

        # The startup briefing arrives as text with nothing recorded yet.
        if self._initial_prompt:
            self.history.append({"role": "user", "content": self._initial_prompt})
            try:
                self._say(self._answer())
            except OpenRouterError as exc:
                log.warning("Briefing failed: %s", exc)
            return

        while not self._ending and not self._stopped:
            if time.monotonic() - started > self.settings.max_session_seconds:
                log.info("Session hit its time limit.")
                return

            self._state("listening")
            pcm = record_utterance(self.settings)
            if not pcm:
                log.info("Nothing said — closing.")
                return

            # The model hears the recording; there is no transcription step.
            wav = _pcm_to_wav(pcm)
            self.history.append({"role": "user", "content": [
                {"type": "input_audio",
                 "input_audio": {"data": base64.b64encode(wav).decode(), "format": "wav"}},
            ]})

            try:
                reply = self._answer()
            except OpenRouterError as exc:
                # Silent for the same reason: a canned English apology to an
                # Arabic speaker is worse than the chime that follows.
                log.warning("OpenRouter failed: %s", exc)
                return

            log.info("lura: %s", reply)
            self._say(reply)

        self._state("listening")


def _pcm_to_wav(pcm: bytes) -> bytes:
    from .wake import pcm_to_wav

    return pcm_to_wav(pcm)
