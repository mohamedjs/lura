"""What the user is saying, written under the face while they still speak."""

from __future__ import annotations

import logging
import threading
import time

from ...shared.presence import Presence

log = logging.getLogger(__name__)


class LiveCaption:
    """What the user is saying, written under the face while they still speak.

    Display only: the audio so far is transcribed in the background and shown
    with a trailing "…". Each refresh is one hearing call that sends the whole
    turn again, so on a provider billed by the second (ElevenLabs) eight of
    them cost four times the turn itself and ran the account out of quota
    mid-conversation. Hence few calls, further apart each time, and none after
    one fails. ``last`` is what the newest one read: the conversation falls
    back on it when the final transcript comes back empty.
    """

    GAP = 1.5              # before the second call; each later one waits GAP_STEP longer
    GAP_STEP = 1.0
    MAX_CALLS = 4
    MIN_BLOCKS = 20        # two seconds of speech first: a one-second clip is read as any language

    def __init__(self, transcriber, to_wav, presence: Presence):
        self._transcriber = transcriber   # callable returning a Transcriber, made on first use
        self._to_wav = to_wav
        self._presence = presence
        self._busy = threading.Event()
        self._last = 0.0
        self._calls = 0
        self._closed = False
        self.last = ""

    def __call__(self, blocks: list[bytes], start: int) -> None:
        now = time.monotonic()
        if (self._closed or self._busy.is_set() or self._calls >= self.MAX_CALLS
                or now - self._last < self.GAP + self.GAP_STEP * (self._calls - 1) or len(blocks) - start < self.MIN_BLOCKS):
            return
        self._busy.set()
        self._last, self._calls = now, self._calls + 1
        pcm = b"".join(blocks[start:])
        threading.Thread(target=self._run, args=(pcm,), daemon=True, name="live-caption").start()

    def _run(self, pcm: bytes) -> None:
        try:
            text = self._transcriber().transcribe(self._to_wav(pcm))
            if text:
                self.last = text
                if not self._closed:
                    self._presence.set_caption("you", f"{text} …")
        except Exception as exc:
            # Out of quota or offline: the final transcript needs what is left, not more of these.
            self._calls = self.MAX_CALLS
            log.info("Live captions off for this turn: %s", str(exc)[:120])
        finally:
            self._busy.clear()

    def close(self) -> None:
        self._closed = True
