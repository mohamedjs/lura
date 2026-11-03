"""Barge-in: the microphone stays open while she speaks (rules: ``domain.BargeIn``).

Opened when a reply starts, so the pause while the brain thinks measures the
room. Once she speaks, each block is judged against what she was playing at
that moment. When the user takes the floor, the open microphone and the last
few seconds it heard go to the recorder, which carries on from there — no
syllable is lost to reopening the device.
"""

from __future__ import annotations

import collections
import logging
import queue
import threading
import time

from ..domain import OUTPUT_RATE, BargeIn
from .audio import BLOCK_SECONDS, MicStream, speech_level

log = logging.getLogger(__name__)

#: Which of her sound a mic block can contain: device latency is in the
#: timestamps already, but drivers report it loosely (PipeWire said 0.3 s for an
#: echo measured at 0.1 s), and the room rings on after she stops.
ECHO_BEFORE, ECHO_AFTER = 0.5, 0.2


class Carry:
    """The user's first words, and the microphone still open on them."""

    def __init__(self, mic: MicStream, blocks: queue.Queue, heard: list[bytes]):
        self._mic, self._blocks = mic, blocks
        self.heard = heard        # the room before they spoke, then their first words

    def read(self, timeout: float) -> bytes | None:
        try:
            return self._blocks.get(timeout=timeout)[0]
        except queue.Empty:
            return None

    def close(self) -> None:
        self._mic.__exit__(None, None, None)


class BargeInListener:
    RECENT = 30               # blocks kept (3 s): enough room noise for the recorder's floor

    def __init__(self, settings):
        self._factor = settings.silence_factor
        self._blocks: queue.Queue = queue.Queue()          # (block, when it reached the mic)
        self._recent: collections.deque = collections.deque(maxlen=self.RECENT)
        self._room: list[float] = []
        self._her: collections.deque = collections.deque(maxlen=400)   # (plays_at, level)
        self._lock = threading.Lock()
        self._player = None
        self._done = threading.Event()
        #: Set once the mic is closed or handed over: the recorder must not open
        #: the device while this still holds it (a raw hw: device would refuse).
        self.released = threading.Event()
        self.carry: Carry | None = None
        try:
            self._mic = MicStream(settings.input_device, on_block=self._arrived)
            self._mic.__enter__()     # started only once _arrived can read self._mic
        except Exception as exc:   # a busy device costs barge-in for one reply, never the reply
            log.info("Barge-in unavailable for this reply: the microphone would not open (%s).", exc)
            self.released.set()
            return
        threading.Thread(target=self._run, daemon=True, name="barge-in").start()

    def _arrived(self, block: bytes) -> None:
        # The audio thread: stamp it now, with the block's middle and the input latency.
        self._blocks.put((block, time.monotonic() - self._mic.latency - BLOCK_SECONDS / 2))

    # Called from the playback thread.
    def start(self, player) -> None:
        self._player = player

    def played(self, pcm: bytes, plays_at: float) -> None:
        with self._lock:
            self._her.append((plays_at, speech_level(pcm, OUTPUT_RATE)))

    def stop(self) -> None:
        """Her reply is over. The thread closes the mic within 0.1 s — before
        the recorder opens it again — unless the user's words are in it."""
        self._done.set()

    def _her_level(self, heard_at: float) -> float:
        with self._lock:
            return max((level for t, level in self._her if heard_at - ECHO_BEFORE <= t <= heard_at + ECHO_AFTER),
                       default=0.0)

    def _run(self) -> None:
        try:
            self._listen()
        except Exception as exc:
            log.warning("Barge-in stopped: %s", exc)
        finally:
            if self.carry is None:
                self._mic.__exit__(None, None, None)
            self.released.set()

    def _listen(self) -> None:
        detector = None
        while not self._done.is_set():
            try:
                block, heard_at = self._blocks.get(timeout=0.1)
            except queue.Empty:
                continue
            level = speech_level(block)
            self._recent.append(block)
            if self._player is None:          # still thinking: this is the room
                self._room.append(level)
                continue
            if detector is None:
                ranked = sorted(self._room[-50:])
                detector = BargeIn(ranked[len(ranked) // 10] if ranked else 0.0, self._factor)
            state = detector.feed(level, self._her_level(heard_at))
            if state == "off":
                log.info("Barge-in off for this reply: the mic hears her (speakers). Tap the face to interrupt.")
                return
            if state == "barge":
                log.info("Barge-in: the user is speaking.")
                self.carry = Carry(self._mic, self._blocks, list(self._recent))
                self._player.interrupt()
                return
