"""Microphone and speaker, through PortAudio (sounddevice).

``sounddevice`` is imported inside each function: the dashboard and the
CLI's config commands must work on a machine whose audio stack is broken,
which is exactly when someone opens them.
"""

from __future__ import annotations

import io
import logging
import queue
import shutil
import subprocess
import time
import wave

import numpy as np

from ..domain import CHANNELS, INPUT_RATE, OUTPUT_RATE

log = logging.getLogger(__name__)

BLOCK_FRAMES = 4000   # ~0.25 s at 16 kHz; what Vosk reads best


def rms_of(pcm: bytes) -> float:
    """Loudness of an int16 buffer, 0..1."""
    if len(pcm) < 2:
        return 0.0
    samples = np.frombuffer(pcm[: len(pcm) - len(pcm) % 2], dtype=np.int16).astype(np.float32)
    return float(np.sqrt(np.mean(samples * samples))) / 32768.0 if samples.size else 0.0


def chime(rate: int = OUTPUT_RATE, device=None, up: bool = True) -> None:
    """A short two-tone beep, so a false trigger is obvious. Generated, not shipped."""
    import sounddevice as sd

    parts = []
    for freq in ((660, 990) if up else (990, 660)):
        t = np.linspace(0, 0.09, int(rate * 0.09), endpoint=False)
        fade = np.minimum(1.0, np.minimum(np.arange(len(t)), len(t) - np.arange(len(t))) / 200.0)
        parts.append(np.sin(2 * np.pi * freq * t) * fade * 0.25)
    try:
        sd.play((np.concatenate(parts) * 32767).astype(np.int16), samplerate=rate,
                device=device, blocking=True)
    except Exception as exc:  # a missing sink must not kill the assistant
        log.warning("Could not play chime: %s", exc)


class Speaker:
    """One output stream for a whole reply.

    Opened once and written in ~50 ms slices: the face's mouth tracks the
    speech, and opening the device per sentence clicks on PipeWire and clips
    the tail of each sentence as the buffer is torn down mid-drain.
    """

    def __init__(self, device=None, rate: int = OUTPUT_RATE, on_level=None):
        import sounddevice as sd

        self._rate = rate
        self._on_level = on_level
        self._stream = sd.RawOutputStream(samplerate=rate, blocksize=int(rate * 0.05),
                                          device=device, dtype="int16", channels=CHANNELS)
        self._stream.start()

    def write(self, pcm: bytes, cancelled=lambda: False) -> None:
        block = int(self._rate * 0.05) * 2
        for offset in range(0, len(pcm), block):
            if cancelled():
                return
            part = pcm[offset:offset + block]
            if self._on_level:
                self._on_level(min(1.0, rms_of(part) * 4.0))
            self._stream.write(part)

    def close(self) -> None:
        try:
            self._stream.stop()
            self._stream.close()
        finally:
            if self._on_level:
                self._on_level(0.0)  # or the mouth freezes half-open


def record_utterance(settings, should_stop=lambda: False) -> bytes:
    """Record until the speaker stops; return 16 kHz mono PCM, or b"" for nothing.

    The noise floor is *measured* over the first half second, never assumed:
    a quiet room and a laptop fan differ by more than tenfold, and a fixed
    threshold either cuts people off or never ends. ``silence_factor`` and
    ``silence_ms`` stay settings because no measurement survives every room.
    """
    import sounddevice as sd

    blocks: queue.Queue[bytes] = queue.Queue()

    def on_audio(indata, frames, time_info, status):
        blocks.put(bytes(indata))

    captured: list[bytes] = []
    floor: list[float] = []
    quiet_for = 0.0
    speech_seen = False
    started = time.monotonic()
    block_seconds = BLOCK_FRAMES / INPUT_RATE
    calibration = int(0.5 / block_seconds) + 1

    with sd.RawInputStream(samplerate=INPUT_RATE, blocksize=BLOCK_FRAMES,
                           device=settings.input_device, dtype="int16",
                           channels=CHANNELS, callback=on_audio):
        while not should_stop():
            try:
                block = blocks.get(timeout=1.0)
            except queue.Empty:
                break
            elapsed = time.monotonic() - started
            if elapsed > settings.max_utterance_seconds:
                log.info("Utterance hit the %.0fs cap.", settings.max_utterance_seconds)
                break
            level = rms_of(block)
            captured.append(block)
            if len(floor) < calibration:   # calibration: listen, do not judge
                floor.append(level)
                continue
            noise = max(sorted(floor)[len(floor) // 2], 0.0005)
            if level > noise * settings.silence_factor:
                speech_seen, quiet_for = True, 0.0
                continue
            quiet_for += block_seconds
            if speech_seen and quiet_for * 1000 >= settings.silence_ms:
                break
            if not speech_seen and elapsed > settings.idle_timeout:
                return b""

    audio = b"".join(captured)
    if not speech_seen or len(audio) < int(settings.min_utterance_seconds * INPUT_RATE * 2):
        return b""
    return audio


def pcm_to_wav(pcm: bytes, rate: int = INPUT_RATE) -> bytes:
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as handle:
        handle.setnchannels(CHANNELS)
        handle.setsampwidth(2)
        handle.setframerate(rate)
        handle.writeframes(pcm)
    return buffer.getvalue()


def resample(pcm: bytes, src: int, dst: int = OUTPUT_RATE) -> bytes:
    """Linear resampling — plenty for speech, and no dependency."""
    if src == dst or not pcm:
        return pcm
    samples = np.frombuffer(pcm[: len(pcm) - len(pcm) % 2], dtype=np.int16).astype(np.float32)
    n = int(len(samples) * dst / src)
    out = np.interp(np.linspace(0, len(samples) - 1, n), np.arange(len(samples)), samples)
    return out.astype(np.int16).tobytes()


def decode_audio(data: bytes, content_type: str = "") -> bytes:
    """Whatever a TTS endpoint returned → 16-bit mono PCM at 24 kHz.

    WAV and raw PCM are handled here; anything compressed (MP3) goes through
    ffmpeg when it exists. Returning b"" makes the caller say *why* it is
    silent instead of playing noise.
    """
    if data[:4] == b"RIFF":
        with wave.open(io.BytesIO(data)) as handle:
            pcm = handle.readframes(handle.getnframes())
            if handle.getnchannels() == 2:
                pcm = np.frombuffer(pcm, np.int16).reshape(-1, 2).mean(1).astype(np.int16).tobytes()
            return resample(pcm, handle.getframerate())
    kind = content_type.lower()
    if "pcm" in kind or "l16" in kind or not kind or kind == "application/octet-stream":
        if not _looks_compressed(data):
            rate = _rate_from(kind) or OUTPUT_RATE
            return resample(data, rate)
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        log.warning("Speech arrived as %s and ffmpeg is not installed to decode it.", kind or "unknown")
        return b""
    proc = subprocess.run([ffmpeg, "-nostdin", "-loglevel", "error", "-i", "pipe:0", "-f", "s16le",
                           "-acodec", "pcm_s16le", "-ar", str(OUTPUT_RATE), "-ac", "1", "pipe:1"],
                          input=data, capture_output=True, timeout=60)
    if proc.returncode != 0 or not proc.stdout:
        log.warning("ffmpeg could not decode the speech: %s", proc.stderr.decode(errors="replace")[:300])
        return b""
    return proc.stdout


def _looks_compressed(data: bytes) -> bool:
    return data[:3] == b"ID3" or data[:2] in (b"\xff\xfb", b"\xff\xf3", b"\xff\xf2") or data[:4] == b"OggS"


def _rate_from(content_type: str) -> int | None:
    for part in content_type.replace(";", " ").split():
        if part.startswith("rate="):
            try:
                return int(part[5:])
            except ValueError:
                return None
    return None
