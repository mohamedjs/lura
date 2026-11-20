"""What the face shows, shared between the assistant's threads and the UI thread."""

from __future__ import annotations

from dataclasses import dataclass
import enum
import logging
import threading
import time
from typing import Callable

from .looks import Look

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class AirView:
    on: bool = False                    # camera running
    armed: bool = False
    engaged: bool = False
    zone: str = "none"                  # "ok" | "far" | "none"
    mode: str = "universal"
    hands: tuple[tuple[float, float, bool], ...] = ()   # (x, y, pinching), user's view 0..1
    dwell: float = 0.0                  # 0..1 pointing selection progress, 0 = none
    ring: tuple[str, str, str, str] | None = None       # (left, right, up, down) labels when ring open
    ring_hover: str = ""                # "left" | "right" | "up" | "down" | ""
    grab_progress: float = 0.0          # 0..1 hold-in-centre progress towards grab
    hand_age: float = 99.0              # s since a hand appeared (drives the scan sweep)
    pulse_ok: bool | None = None        # result of the last Air action, None = none yet
    pulse_age: float = 99.0             # s since that action
    message: str = ""                   # last Air message (action result / hint / welcome)
    message_age: float = 99.0


class State(enum.Enum):
    LISTENING = "listening"    # Stable, gentle idle pulse/glow
    THINKING = "thinking"      # Rotational or data-processing glow
    SPEAKING = "speaking"      # Audio-reactive wave/pulse animation
    # Backward compatibility aliases
    IDLE = "idle"
    CONNECTING = "connecting"


#: How long a used connector's badge stays next to the face.
TOOL_LINGER = 5.0


class OverlayState:
    """Thread-safe state container."""

    def __init__(self):
        self._state = State.LISTENING
        self._captions: dict[str, str] = {}
        self._caption_ts = 0.0
        self._amp = 0.0
        self._amp_ts = 0.0
        self._tools: dict[str, float] = {}
        self._air_err_logged = False
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

    def set_caption(self, who: str, text: str) -> None:
        with self._lock:
            self._captions[who] = text.strip()
            self._caption_ts = time.monotonic()

    def set_tool(self, name: str) -> None:
        """A connector (MCP server) is being used; the face shows it for a few seconds."""
        with self._lock:
            self._tools[name] = time.monotonic()

    def tools(self, linger: float = TOOL_LINGER) -> list[tuple[str, float]]:
        """(connector, seconds since last used) for the ones used lately, oldest first."""
        now = time.monotonic()
        with self._lock:
            for name in [n for n, ts in self._tools.items() if now - ts > linger]:
                del self._tools[name]
            return sorted(((n, now - ts) for n, ts in self._tools.items()), key=lambda x: -x[1])

    def captions(self) -> tuple[str, str, float]:
        """(what you said, what Lura is saying, seconds since either changed)."""
        with self._lock:
            return (self._captions.get("you", ""), self._captions.get("lura", ""),
                    time.monotonic() - self._caption_ts)

    #: Subtitles under the face, switched live from the dashboard or the tray.
    subtitles_enabled: bool = True
    #: Style, colours and size, switched live from the dashboard or the tray.
    look: Look = Look()
    #: Air HUD source callback, wired by app.py.
    air_source: Callable[[], AirView | None] | None = None

    def air_view(self) -> AirView | None:
        src = self.air_source
        if src is None:
            return None
        try:
            return src()
        except Exception:
            if not self._air_err_logged:
                self._air_err_logged = True
                log.debug("air_source failed", exc_info=True)
            return None


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
