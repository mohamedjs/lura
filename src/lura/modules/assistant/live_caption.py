"""What the user is saying, written under the face while they still speak."""

from __future__ import annotations

import logging
import threading
import time

from ...shared.presence import Presence

log = logging.getLogger(__name__)


class LiveCaption:
    """What the user is saying, written under the face while they still speak.

    Display only: every ~1.5 s of new speech the audio so far is transcribed in
    the background and shown with a trailing "…". The final transcript never
    comes from here. Each refresh is one hearing call, so they are spaced out
    and capped per turn.
    """

    GAP = 1.5
    MAX_CALLS = 8
    MIN_BLOCKS = 10        # a second of speech before the first try

    def __init__(self, transcriber, to_wav, presence: Presence):
        self._transcriber = transcriber   # callable returning a Transcriber, made on first use
        self._to_wav = to_wav
        self._presence = presence
        self._busy = threading.Event()
        self._last = 0.0
        self._calls = 0
        self._closed = False

    def __call__(self, blocks: list[bytes], start: int) -> None:
        now = time.monotonic()
        if (self._closed or self._busy.is_set() or self._calls >= self.MAX_CALLS
                or now - self._last < self.GAP or len(blocks) - start < self.MIN_BLOCKS):
            return
        self._busy.set()
        self._last, self._calls = now, self._calls + 1
        pcm = b"".join(blocks[start:])
        threading.Thread(target=self._run, args=(pcm,), daemon=True, name="live-caption").start()

    def _run(self, pcm: bytes) -> None:
        try:
            text = self._transcriber().transcribe(self._to_wav(pcm))
            if text and not self._closed:
                self._presence.set_caption("you", f"{text} …")
        except Exception as exc:
            log.debug("Live caption failed: %s", exc)
        finally:
            self._busy.clear()

    def close(self) -> None:
        self._closed = True
