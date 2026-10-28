"""Speech: the rules for turning a stream of text into speakable pieces.

Synthesis takes about as long as the text is long, so a reply is spoken
sentence by sentence while the brain is still writing the rest: the first
word comes out about one synthesis-of-a-sentence after the first sentence is
written, not after the whole answer and the whole synthesis.
"""

from __future__ import annotations

import re
from typing import Protocol

#: Mic in. Gemini Live fixes it; Vosk and the recorder use the same.
INPUT_RATE = 16_000
#: Speech out. Gemini TTS and Gemini Live both produce 24 kHz.
OUTPUT_RATE = 24_000
CHANNELS = 1

#: Sentence ends in Latin and Arabic script, and the newline between items.
_SENTENCE_END = re.compile(r"(?<=[.!?…۔؟])\s+|\n+")
#: Softer breaks for a sentence that runs on too long.
_CLAUSE_END = re.compile(r"(?<=[,;:،؛])\s+")
_MARKDOWN = re.compile(r"[*_`#>]+|^\s*[-•]\s+", re.MULTILINE)


class SpeechSynthesizer(Protocol):
    """Text in, 16-bit mono PCM at ``OUTPUT_RATE`` out."""

    def synthesize(self, text: str) -> bytes: ...


class Transcriber(Protocol):
    """A WAV recording in, what was said out (in the language it was said)."""

    def transcribe(self, wav: bytes) -> str: ...


def speakable(text: str) -> str:
    """Strip the markdown a model writes anyway; a voice reads ``**`` aloud."""
    return _MARKDOWN.sub("", text).strip()


class SentenceChunker:
    """Feed it streamed text; it hands back pieces ready to synthesise.

    The first piece is released as soon as one sentence is complete, so the
    voice starts early. Later pieces gather sentences up to ``target`` chars:
    fewer, longer requests sound smoother and cost fewer round trips. A
    single sentence longer than ``limit`` is cut at a comma rather than held.
    """

    def __init__(self, first: int = 1, target: int = 160, limit: int = 260):
        self._buffer = ""
        self._first = first
        self._target = target
        self._limit = limit
        self._released = 0

    def feed(self, text: str) -> list[str]:
        self._buffer += text
        out: list[str] = []
        while True:
            piece = self._take(final=False)
            if not piece:
                return out
            out.append(piece)

    def flush(self) -> list[str]:
        out = []
        while piece := self._take(final=True):
            out.append(piece)
        return out

    def _take(self, final: bool) -> str:
        buf = self._buffer
        if not buf.strip():
            return ""
        ends = [m.end() for m in _SENTENCE_END.finditer(buf)]
        want = self._first if self._released == 0 else self._target
        cut = 0
        for end in ends:
            if end > self._limit and cut:
                break
            cut = end
            if len(buf[:end].strip()) >= want:
                break
        if cut and (len(buf[:cut].strip()) >= want or final):
            return self._emit(cut)
        if len(buf) > self._limit:
            clauses = [m.end() for m in _CLAUSE_END.finditer(buf, 0, self._limit)]
            spaces = [m.start() for m in re.finditer(r"\s", buf[: self._limit])]
            return self._emit(clauses[-1] if clauses else spaces[-1] if spaces else self._limit)
        if final:
            return self._emit(len(buf))
        return ""

    def _emit(self, cut: int) -> str:
        piece, self._buffer = speakable(self._buffer[:cut]), self._buffer[cut:]
        self._released += 1
        return piece or self._take(final=False)


# ── devices ────────────────────────────────────────────────────────────────

_HW_SUFFIX = re.compile(r"\s*\(hw:\d+,\d+\)\s*$")


def _plain(name: str) -> str:
    """ALSA appends "(hw:1,0)", and the card number moves when USB devices
    are replugged or boot in another order. Compare names without it."""
    return _HW_SUFFIX.sub("", name).strip().lower()


def pick_device(devices: list[dict], wanted, kind: str, hostapi: int = 0) -> int | None:
    """A stored device name → a PortAudio index, or None for the system default.

    Names are stored, not indices: indices shift across boots and replugs.
    A digit-only value is a 1.x/2.0 setting that held an index. A device that
    is gone falls back to the default rather than failing: a dead stream in
    the wake loop would spin forever.
    """
    if wanted is None or not str(wanted).strip():
        return None
    channels = "max_input_channels" if kind == "input" else "max_output_channels"
    usable = [(i, d) for i, d in enumerate(devices) if d.get(channels, 0) > 0]
    text = str(wanted).strip()
    if text.isdigit():
        return int(text) if any(i == int(text) for i, _ in usable) else None
    # The default host API first: on Linux the same card shows under ALSA,
    # JACK and Pulse, and only one of them is the route the system uses.
    usable.sort(key=lambda item: item[1].get("hostapi", 0) != hostapi)
    for match in (lambda d: d["name"] == text, lambda d: _plain(d["name"]) == _plain(text),
                  lambda d: _plain(text) in _plain(d["name"])):
        for index, device in usable:
            if match(device):
                return index
    return None


# ── end of turn ────────────────────────────────────────────────────────────

class EndOfTurn:
    """Decides, one block of loudness at a time, when the user has finished.

    The noise floor is a low percentile of the last few seconds, measured
    continuously rather than in a calibration window: someone who starts
    talking the instant the chime ends would otherwise become the "noise"
    and never be heard. Gaps between words pull the floor down within a few
    blocks; a steady fan sets it and stays below the threshold. Time is counted in blocks, not
    the clock, so the same levels always give the same answer.
    """

    WINDOW = 50          # blocks the floor is measured over (5 s of 100 ms)
    PRE_ROLL = 3         # blocks kept before the first loud one

    def __init__(self, block_seconds: float, silence_ms: int, factor: float,
                 wait_seconds: float, max_seconds: float, min_floor: float = 0.0005):
        self._block = block_seconds
        self._silence = silence_ms / 1000
        self._factor = factor
        self._wait = wait_seconds
        self._max = max_seconds
        self._min_floor = min_floor
        self.levels: list[float] = []
        self.start: int | None = None     # block index where speech begins (with pre-roll)
        self._first_loud: int | None = None
        self._last_loud = -1
        self._quiet_blocks = 0

    @property
    def spoken_seconds(self) -> float:
        """From the first loud block to the last: a click or a door is one block."""
        if self._first_loud is None:
            return 0.0
        return (self._last_loud - self._first_loud + 1) * self._block

    def floor(self) -> float:
        ranked = sorted(self.levels[-self.WINDOW:])
        return max(ranked[len(ranked) // 10], self._min_floor) if ranked else self._min_floor

    def feed(self, level: float) -> str:
        """"wait", "done" (speech then a pause) or "nothing" (no speech in time)."""
        self.levels.append(level)
        threshold = self.floor() * self._factor
        if level > threshold:
            if self.start is None:
                self._first_loud = next(i for i, v in enumerate(self.levels) if v > threshold)
                self.start = max(0, self._first_loud - self.PRE_ROLL)
            self._last_loud = len(self.levels) - 1
            self._quiet_blocks = 0
        else:
            self._quiet_blocks += 1
        elapsed = len(self.levels) * self._block
        if self.start is not None:
            # Rounded: ten 0.1 s blocks add up to 0.9999… seconds.
            if round(self._quiet_blocks * self._block, 6) >= self._silence or elapsed >= self._max:
                return "done"
        elif elapsed >= self._wait:
            return "nothing"
        return "wait"
