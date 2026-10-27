"""What the face shows, shared between the assistant's threads and the UI thread."""

from __future__ import annotations

import enum
import threading
import time


class State(enum.Enum):
    LISTENING = "listening"    # Stable, gentle idle pulse/glow
    THINKING = "thinking"      # Rotational or data-processing glow
    SPEAKING = "speaking"      # Audio-reactive wave/pulse animation
    # Backward compatibility aliases
    IDLE = "idle"
    CONNECTING = "connecting"


class OverlayState:
    """Thread-safe state container."""

    def __init__(self):
        self._state = State.LISTENING
        self._transcript = ""
        self._amp = 0.0
        self._amp_ts = 0.0
        self._lock = threading.Lock()

    @property
    def state(self) -> State:
        with self._lock:
            return self._state

    @state.setter
    def state(self, val: State | str):
        if isinstance(val, str):
            val = State(val)
        with self._lock:
            self._state = val

    @property
    def amplitude(self) -> float:
        """Voice loudness 0..1, decaying to silence between audio chunks.

        Chunks arrive far slower than the 60fps render loop, so the value is
        decayed against wall-clock time rather than per read.
        """
        with self._lock:
            if self._amp <= 0.0:
                return 0.0
            age = time.monotonic() - self._amp_ts
            return self._amp * max(0.0, 1.0 - age / 0.22)

    @amplitude.setter
    def amplitude(self, val: float):
        with self._lock:
            self._amp = max(0.0, min(1.0, val))
            self._amp_ts = time.monotonic()

    # ── the Presence protocol (lura.shared.presence) ──
    def set_state(self, state: str) -> None:
        self.state = State(state)

    def set_amplitude(self, level: float) -> None:
        self.amplitude = level

    def set_transcript(self, text: str) -> None:
        self.transcript = text

    @property
    def transcript(self) -> str:
        with self._lock:
            return self._transcript

    @transcript.setter
    def transcript(self, val: str):
        with self._lock:
            self._transcript = val


class StateBlender:
    """Smooth continuous interpolation between visual states."""

    def __init__(self):
        self.wl = 1.0  # listening / idle weight
        self.wt = 0.0  # thinking weight
        self.ws = 0.0  # speaking weight

    def update(self, target_st: State, dt: float = 0.04):
        if target_st in (State.IDLE, State.LISTENING):
            tl, tt, ts = 1.0, 0.0, 0.0
        elif target_st in (State.CONNECTING, State.THINKING):
            tl, tt, ts = 0.0, 1.0, 0.0
        else:  # SPEAKING
            tl, tt, ts = 0.0, 0.0, 1.0

        k = min(1.0, dt * 5.0)
        self.wl += (tl - self.wl) * k
        self.wt += (tt - self.wt) * k
        self.ws += (ts - self.ws) * k

        tot = self.wl + self.wt + self.ws
        if tot > 0.001:
            self.wl /= tot
            self.wt /= tot
            self.ws /= tot
