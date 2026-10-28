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

from ..domain import CHANNELS, INPUT_RATE, OUTPUT_RATE, EndOfTurn, pick_device

log = logging.getLogger(__name__)

BLOCK_SECONDS = 0.1   # end-of-turn resolution; Vosk reads any block size
#: After Lura stops talking, the room still rings for a moment; listening
#: right away hears her own tail as the user's turn.
REVERB_SECONDS = 0.15


# ── devices ─────────────────────────────────────────────────────────────────
# ponytail: PortAudio enumerates devices once per process, so a device
# plugged in after Lura started shows up after a restart.

def _devices(sd) -> tuple[list[dict], int]:
    return [dict(d) for d in sd.query_devices()], int(sd.default.hostapi)


def resolve(device, kind: str):
    """The stored name → an index PortAudio takes (None = system default)."""
    if device is None or not str(device).strip():
        return None
    import sounddevice as sd

    devices, hostapi = _devices(sd)
    index = pick_device(devices, device, kind, hostapi)
    if index is None:
        log.warning("%s device %r is not connected; using the system default.", kind.title(), device)
    return index


def list_devices() -> dict[str, list[dict]]:
    """Inputs and outputs of the default host API, for the dashboard picker."""
    import sounddevice as sd

    devices, hostapi = _devices(sd)
    defaults = list(sd.default.device)
    out: dict[str, list[dict]] = {"inputs": [], "outputs": []}
    for index, d in enumerate(devices):
        if d.get("hostapi") != hostapi:
            continue
        for kind, key, slot in (("inputs", "max_input_channels", 0), ("outputs", "max_output_channels", 1)):
            if d.get(key, 0) > 0:
                out[kind].append({"name": d["name"], "index": index, "channels": d[key],
                                  "default_samplerate": d.get("default_samplerate"),
                                  "is_default": index == defaults[slot]})
    return out


def _format(sd, index, kind: str, rate: int) -> tuple[int, int]:
    """A (rate, channels) the device accepts, closest to what we want.

    Pulse/PipeWire "default" resamples anything; a raw ALSA hw device takes
    only its own rates and sometimes refuses mono.
    """
    check = sd.check_input_settings if kind == "input" else sd.check_output_settings
    info = sd.query_devices(index if index is not None else sd.default.device[0 if kind == "input" else 1])
    native = int(info["default_samplerate"])
    most = int(info["max_input_channels" if kind == "input" else "max_output_channels"]) or 1
    for candidate in ((rate, 1), (native, 1), (rate, min(2, most)), (native, min(2, most))):
        try:
            check(device=index, samplerate=candidate[0], channels=candidate[1], dtype="int16")
            return candidate
        except Exception:
            continue
    return native, most


def _open(sd, device, kind: str, rate: int, factory):
    """Open ``factory(index, rate, channels)`` on the named device, or on the
    default if it is busy or refuses every format: an exception here inside
    the wake loop would retry every two seconds forever."""
    index = resolve(device, kind)
    try:
        fmt = _format(sd, index, kind, rate)
        return factory(index, *fmt), fmt
    except Exception as exc:
        if index is None:
            raise
        log.warning("%s device %r would not open (%s); using the system default.", kind.title(), device, exc)
        fmt = _format(sd, None, kind, rate)
        return factory(None, *fmt), fmt


def _to_mono(pcm: bytes, channels: int) -> bytes:
    if channels == 1:
        return pcm
    return np.frombuffer(pcm, np.int16).reshape(-1, channels).mean(1).astype(np.int16).tobytes()


class MicStream:
    """The microphone as 16 kHz mono int16 blocks, whatever the device wants.

    Every input in Lura goes through here (the wake word, the recorder,
    Gemini Live), so a device that refuses 16 kHz works everywhere or nowhere.
    With ``on_block``, blocks are handed over from the audio thread instead
    of queued for ``read``.
    """

    def __init__(self, device=None, block_seconds: float = BLOCK_SECONDS, on_block=None):
        import sounddevice as sd

        self._blocks: queue.Queue[bytes] = queue.Queue()
        self._on_block = on_block or self._blocks.put
        self._stream, (self.rate, self.channels) = _open(
            sd, device, "input", INPUT_RATE,
            lambda index, rate, channels: sd.RawInputStream(
                samplerate=rate, blocksize=int(rate * block_seconds), device=index, dtype="int16",
                channels=channels, callback=self._callback))
        if self.rate != INPUT_RATE:
            log.info("Microphone runs at %d Hz; resampling to %d.", self.rate, INPUT_RATE)

    def _callback(self, indata, frames, time_info, status):
        self._on_block(resample(_to_mono(bytes(indata), self.channels), self.rate, INPUT_RATE))

    def read(self, timeout: float) -> bytes | None:
        try:
            return self._blocks.get(timeout=timeout)
        except queue.Empty:
            return None

    def __enter__(self) -> "MicStream":
        self._stream.start()
        return self

    def __exit__(self, *exc) -> None:
        self._stream.stop()
        self._stream.close()


class OutputStream:
    """A speaker stream fed 24 kHz mono, resampled if the device wants otherwise."""

    def __init__(self, device=None, rate: int = OUTPUT_RATE, block_seconds: float = 0.05):
        import sounddevice as sd

        self._src = rate
        self._stream, (self.rate, self.channels) = _open(
            sd, device, "output", rate,
            lambda index, out_rate, channels: sd.RawOutputStream(
                samplerate=out_rate, blocksize=int(out_rate * block_seconds), device=index, dtype="int16",
                channels=channels))
        self._stream.start()

    def write(self, pcm: bytes) -> None:
        pcm = resample(pcm, self._src, self.rate)
        if self.channels > 1:
            pcm = np.repeat(np.frombuffer(pcm, np.int16), self.channels).tobytes()
        self._stream.write(pcm)

    def close(self) -> None:
        self._stream.stop()
        self._stream.close()


def mic_test(device=None, seconds: float = 3.0) -> dict:
    """Listen for a few seconds and report levels — the dashboard's Test button.

    ponytail: if the wake listener holds a raw hw device, this one reports
    "busy"; the error is passed through verbatim so that is obvious.
    """
    try:
        levels = []
        with MicStream(device) as mic:
            for _ in range(int(seconds / BLOCK_SECONDS)):
                block = mic.read(timeout=1.0)
                if block is None:
                    break
                levels.append(rms_of(block))
            rate = mic.rate
    except Exception as exc:
        return {"ok": False, "error": str(exc)}
    if not levels:
        return {"ok": False, "error": "no audio arrived from the microphone"}
    ranked = sorted(levels)
    peak, floor = ranked[-1], ranked[len(ranked) // 10]
    return {"ok": peak >= 0.003, "peak": round(peak, 4), "floor": round(floor, 4), "rate": rate,
            "levels": [round(v, 4) for v in levels]}


def rms_of(pcm: bytes) -> float:
    """Loudness of an int16 buffer, 0..1."""
    if len(pcm) < 2:
        return 0.0
    samples = np.frombuffer(pcm[: len(pcm) - len(pcm) % 2], dtype=np.int16).astype(np.float32)
    return float(np.sqrt(np.mean(samples * samples))) / 32768.0 if samples.size else 0.0


def chime(rate: int = OUTPUT_RATE, device=None, up: bool = True) -> None:
    """A short two-tone beep, so a false trigger is obvious. Generated, not shipped."""
    parts = []
    for freq in ((660, 990) if up else (990, 660)):
        t = np.linspace(0, 0.09, int(rate * 0.09), endpoint=False)
        fade = np.minimum(1.0, np.minimum(np.arange(len(t)), len(t) - np.arange(len(t))) / 200.0)
        parts.append(np.sin(2 * np.pi * freq * t) * fade * 0.25)
    try:
        out = OutputStream(device, rate)
        try:
            out.write((np.concatenate(parts) * 32767).astype(np.int16).tobytes())
        finally:
            out.close()
    except Exception as exc:  # a missing sink must not kill the assistant
        log.warning("Could not play chime: %s", exc)


class Speaker:
    """One output stream for a whole reply.

    Opened once and written in ~50 ms slices: the face's mouth tracks the
    speech, and opening the device per sentence clicks on PipeWire and clips
    the tail of each sentence as the buffer is torn down mid-drain.
    """

    def __init__(self, device=None, rate: int = OUTPUT_RATE, on_level=None):
        self._rate = rate
        self._on_level = on_level
        self._stream = OutputStream(device, rate)

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
            self._stream.close()
        finally:
            if self._on_level:
                self._on_level(0.0)  # or the mouth freezes half-open


def record_utterance(settings, should_stop=lambda: False) -> bytes:
    """Record until the speaker stops; return 16 kHz mono PCM, or b"" for nothing.

    ``follow_up_seconds`` is how long to wait for the user to start; then
    ``silence_ms`` of quiet ends the turn. The noise floor is measured as it
    goes (see ``EndOfTurn``): a quiet room and a laptop fan differ by more
    than tenfold. Leading silence is trimmed to a short pre-roll, so seconds
    of nothing are not sent to the brain as audio.
    """
    turn = EndOfTurn(BLOCK_SECONDS, settings.silence_ms, settings.silence_factor,
                     settings.follow_up_seconds, settings.max_utterance_seconds)
    captured: list[bytes] = []
    verdict = "wait"
    time.sleep(REVERB_SECONDS)
    with MicStream(settings.input_device) as mic:
        while verdict == "wait" and not should_stop():
            block = mic.read(timeout=1.0)
            if block is None:
                log.warning("The microphone stopped sending audio.")
                break
            captured.append(block)
            verdict = turn.feed(rms_of(block))
    # One block of slack: 100 ms blocks quantise a short "no" down to 0.3 s.
    if turn.start is None or turn.spoken_seconds < settings.min_utterance_seconds - BLOCK_SECONDS:
        if turn.start is not None:
            log.info("Ignored %.1fs of sound: too short to be speech.", turn.spoken_seconds)
        return b""
    return b"".join(captured[turn.start:])


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
