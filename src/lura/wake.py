"""Listening for the wake word, and the small bits of audio around it.

Wake detection is Vosk running offline with a *restricted grammar*: the
recogniser is told the only things it may hear are the wake word and "[unk]".
Open-vocabulary transcription of a room all day costs far more CPU and accepts
far more rubbish; with a two-entry grammar this sits near idle and rarely
fires by accident.

Nothing here talks to the network, and none of it needs a key.
"""

from __future__ import annotations

import json
import logging
import queue
import time
from pathlib import Path

import numpy as np
import sounddevice as sd

from .config import CHANNELS, INPUT_RATE, Settings

log = logging.getLogger(__name__)

#: Vosk reads best in small chunks; this is ~0.25s.
BLOCK_FRAMES = 4000


def chime(rate: int = 24_000, device=None, up: bool = True) -> None:
    """A short two-tone beep, so a false trigger is obvious.

    Generated rather than shipped as a file: it is nine lines of numpy and
    saves carrying an asset around.
    """
    tones = (660, 990) if up else (990, 660)
    parts = []
    for freq in tones:
        t = np.linspace(0, 0.09, int(rate * 0.09), endpoint=False)
        wave = np.sin(2 * np.pi * freq * t)
        # A short fade at each end; a square-edged tone clicks.
        fade = np.minimum(1.0, np.minimum(np.arange(len(t)), len(t) - np.arange(len(t))) / 200.0)
        parts.append(wave * fade * 0.25)

    audio = (np.concatenate(parts) * 32767).astype(np.int16)
    try:
        sd.play(audio, samplerate=rate, device=device, blocking=True)
    except Exception as exc:  # a missing sink must not kill the assistant
        log.warning("Could not play chime: %s", exc)


def rms_of(pcm: bytes) -> float:
    """Loudness of an int16 buffer, 0..1. Used by --selftest."""
    if not pcm:
        return 0.0
    samples = np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768.0
    if samples.size == 0:
        return 0.0
    return float(np.sqrt(np.mean(samples**2)))


class WakeListener:
    """Blocks until the wake word is heard.

    Holds the microphone only while waiting. The Live session needs the same
    device, so this must let go before handing over — hence `listen()` opening
    and closing the stream itself rather than keeping one open for the process
    lifetime.
    """

    def __init__(self, settings: Settings, model_dir: Path):
        from vosk import KaldiRecognizer, Model, SetLogLevel

        SetLogLevel(-1)  # Vosk is extremely chatty on stderr otherwise.

        if not model_dir.exists():
            raise FileNotFoundError(
                f"Vosk model not found at {model_dir}. Re-run install.sh."
            )

        self.settings = settings
        self._model = Model(str(model_dir))
        self._word = settings.wake_word.lower().strip()
        # Map "lura" to phonetic "laura" in the English Vosk vocabulary
        vocab_word = "laura" if self._word in ("lura", "laura") else self._word
        self._accepted = {"laura", "lura"} if self._word in ("lura", "laura") else {self._word}

        # The grammar: the wake word, or "not it". Anything else the recogniser
        # might have guessed at is collapsed into [unk].
        grammar = json.dumps([vocab_word, "[unk]"])
        self._recognizer = KaldiRecognizer(self._model, INPUT_RATE, grammar)

    def _heard_wake(self, payload: str) -> bool:
        try:
            text = json.loads(payload).get("text", "")
        except json.JSONDecodeError:
            return False
        words = set(text.lower().split())
        return bool(words & self._accepted)

    def listen(self, stop) -> bool:
        """Wait for the wake word. Returns False if `stop` is set first."""
        blocks: queue.Queue[bytes] = queue.Queue()

        def on_audio(indata, frames, time_info, status):
            if status:
                log.debug("input status: %s", status)
            blocks.put(bytes(indata))

        with sd.RawInputStream(
            samplerate=INPUT_RATE,
            blocksize=BLOCK_FRAMES,
            device=self.settings.input_device,
            dtype="int16",
            channels=CHANNELS,
            callback=on_audio,
        ):
            log.info("Listening for %r…", self._word)
            while not stop.is_set():
                try:
                    block = blocks.get(timeout=0.3)
                except queue.Empty:
                    continue

                if self._recognizer.AcceptWaveform(block):
                    if self._heard_wake(self._recognizer.Result()):
                        return True
                else:
                    # Partials let us fire the moment the word lands rather
                    # than waiting for the recogniser to decide the phrase
                    # has ended — the difference between snappy and sluggish.
                    if self._heard_wake(self._recognizer.PartialResult()):
                        self._recognizer.Reset()
                        return True

        return False

    def cooldown(self) -> None:
        """Swallow the tail of the wake word so it cannot re-trigger."""
        time.sleep(self.settings.wake_cooldown)
        self._recognizer.Reset()


def record_utterance(settings: Settings) -> bytes:
    """Record until the speaker stops, and return 16kHz mono PCM.

    Only the OpenRouter path needs this. Gemini Live decides end-of-turn
    server-side from the audio stream itself.

    The noise floor is *measured*, not assumed: a quiet room and a laptop with
    a spinning fan differ by more than tenfold, and a hardcoded threshold either
    cuts people off mid-sentence or never fires at all. The first half second
    establishes the floor, and speech is anything `silence_factor` above it.
    Both that and `silence_ms` are settings, because no measurement here
    survives contact with a different room.
    """
    blocks: queue.Queue[bytes] = queue.Queue()

    def on_audio(indata, frames, time_info, status):
        if status:
            log.debug("input status: %s", status)
        blocks.put(bytes(indata))

    captured: list[bytes] = []
    floor_samples: list[float] = []
    quiet_for = 0.0
    speech_seen = False
    started = time.monotonic()
    block_seconds = BLOCK_FRAMES / INPUT_RATE

    with sd.RawInputStream(
        samplerate=INPUT_RATE,
        blocksize=BLOCK_FRAMES,
        device=settings.input_device,
        dtype="int16",
        channels=CHANNELS,
        callback=on_audio,
    ):
        while True:
            try:
                block = blocks.get(timeout=1.0)
            except queue.Empty:
                break

            elapsed = time.monotonic() - started
            if elapsed > settings.max_utterance_seconds:
                log.info("Utterance hit the %.0fs cap.", settings.max_utterance_seconds)
                break

            level = rms_of(block)

            # Calibration window: listen, do not judge.
            if len(floor_samples) < int(0.5 / block_seconds) + 1:
                floor_samples.append(level)
                captured.append(block)
                continue

            floor = max(sorted(floor_samples)[len(floor_samples) // 2], 0.0005)
            speaking = level > floor * settings.silence_factor

            captured.append(block)

            if speaking:
                speech_seen = True
                quiet_for = 0.0
                continue

            quiet_for += block_seconds
            if speech_seen and quiet_for * 1000 >= settings.silence_ms:
                break
            # Nothing said at all yet: give up rather than record the room.
            if not speech_seen and elapsed > settings.idle_timeout:
                log.info("Nothing said.")
                return b""

    audio = b"".join(captured)
    if not speech_seen or len(audio) < int(settings.min_utterance_seconds * INPUT_RATE * 2):
        return b""
    return audio


def pcm_to_wav(pcm: bytes, rate: int = INPUT_RATE) -> bytes:
    """Wrap raw PCM in a WAV container — what transcription endpoints expect."""
    import io
    import wave

    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as handle:
        handle.setnchannels(CHANNELS)
        handle.setsampwidth(2)
        handle.setframerate(rate)
        handle.writeframes(pcm)
    return buffer.getvalue()
