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
