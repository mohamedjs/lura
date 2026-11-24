"""Pure hand-tracking domain: landmarks, poses, One Euro filter, gesture recognizer and stats."""

from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass, field
from typing import Any

from ..computer import Rect

# MediaPipe 21-landmark indices.
WRIST = 0
THUMB_IP = 3
THUMB_TIP = 4
INDEX_MCP = 5
INDEX_PIP = 6
INDEX_TIP = 8
MIDDLE_MCP = 9
MIDDLE_PIP = 10
MIDDLE_TIP = 12
RING_MCP = 13
RING_PIP = 14
RING_TIP = 16
PINKY_MCP = 17
PINKY_PIP = 18
PINKY_TIP = 20

# Gesture recognition thresholds (all tuned as named constants).
PINCH_ON = 0.28          # thumb–index tip distance / palm size below which a pinch starts
PINCH_OFF = 0.40         # … above which it ends (hysteresis)
#: Index wrist→tip / wrist→pip must exceed this to start a pinch. Measured on the phone camera:
#: a fist (thumb over the index) 0.58–0.72, real pinches 0.81–1.20 (1.0 rejected some of them).
PINCH_REACH = 0.80
PINCH_REACH_END = 0.70   # an active pinch ends below this (the hand closed into a fist)
RELEASE_HOLD = 0.10      # s the pinch-release condition must hold before pinch_end
EXTENDED = 1.15          # finger extended when wrist→tip > EXTENDED × wrist→pip
THUMB_OUT = 0.60         # thumb extended when thumb tip→index MCP > THUMB_OUT × palm size
POSE_STABLE = 0.15       # s a pose must hold before a "pose" event
LEAVE_GRACE = 0.45       # s without the hand before "hand_leave"
SWIPE_WINDOW = 0.5       # s of palm history examined for a swipe
SWIPE_DIST = 0.18        # normalized travel needed within the window
SWIPE_AXIS = 2.0         # main-axis travel must be ≥ SWIPE_AXIS × cross-axis travel
SWIPE_SAME_COOLDOWN = 0.35  # s after a swipe before another swipe in the same direction or other axis
RETURN_WINDOW = 0.8         # s after a swipe during which an opposite stroke is swallowed as a return
REVERSE_AFTER = 1.0         # s after a swipe when an opposite swipe is allowed without settling
SETTLE_SPEED = 0.35      # palm speed (normalized units/s) below which the palm counts as settled
SETTLE_TIME = 0.20       # s palm speed must stay below SETTLE_SPEED after a swipe before early reverse
DOUBLE_PINCH_GAP = 0.45  # s between a short pinch's end and the next pinch's start
SHORT_PINCH = 0.30       # s a pinch may last to count as the first of a double pinch
MIN_SCORE = 0.60         # hands with a lower handedness score are ignored
SPREAD_MIN_DIST = 0.05   # both-hand pinch distance must exceed this to start a spread
TRACK_JUMP = 0.18        # max palm distance to keep a track when MediaPipe flips left/right

# Control geometry & HUD constants.
SNAP_PX = 40                        # releasing a window within this many px of a screen edge snaps it
PLACE_MIN_PX = 4                    # skip a place() that moves less than this
CONTROL_HZ = 30                     # controller poll timeout rate

#: AVFoundation transport types (FourCC) and device types used to tell cameras apart.
MAC_TRANSPORT_BUILTIN = 0x626C746E      # 'bltn'
MAC_TRANSPORT_VIRTUAL = 0x76697274      # 'virt'
MAC_TYPE_BUILTIN = "AVCaptureDeviceTypeBuiltInWideAngleCamera"
MAC_TYPE_CONTINUITY = "AVCaptureDeviceTypeContinuityCamera"
#: Name hints of software cameras that show nothing unless their app runs.
VIRTUAL_CAMERA_HINTS = ("camo", "obs", "virtual", "snap camera", "mmhmm", "ndi", "elgato",
                        "manycam", "xsplit", "zoom", "teams")
#: Name hints of phone cameras (Continuity Camera and its Desk View).
PHONE_CAMERA_HINTS = ("iphone", "desk view", "ipad")

PREVIEW_FLASH_S = 0.6     # s a gesture label stays on the preview chip
PREVIEW_DOUBLE_S = 0.5    # s between two clicks shown as "DOUBLE CLICK"
GLIDE_TICK = 0.008        # s between pointer steps of a glide (~120 Hz)
GLIDE_SPAN_MIN = 0.016    # s a glide lasts at least
GLIDE_SPAN_MAX = 0.05     # s a glide lasts at most (a stalled camera never drags it out)

HINT_EDGE = 0.10        # a hand within this of the left/right frame edge, or below 1 - this, is "at the edge"
HINT_AFTER = 1.5        # s at the edge before the hint
HINT_EVERY = 10.0       # s between two hints
WELCOME = ("Air on: point one finger to move the mouse, flick it up or down to scroll, tap your thumb "
           "down like a finger gun to click, keep it down to drag, do the same with two fingers to right "
           "click, sweep the edge of your hand to slide the workspaces.")

MODES: tuple[str, ...] = (
    "universal", "window", "workspace", "pointer", "files", "media", "developer",
    "presentation", "ai",
)
KINDS: tuple[str, ...] = ("browser", "media", "presentation", "files", "editor", "terminal", "other")

#: Lower-case substrings of WM_CLASS → app kind. First match wins; user entries (settings
#: air_app_kinds) are checked before these.
APP_KINDS: tuple[tuple[str, str], ...] = (
    ("libreoffice-impress", "presentation"), ("impress", "presentation"), ("pdfpc", "presentation"),
    ("google-chrome", "browser"), ("chromium", "browser"), ("firefox", "browser"),
    ("brave", "browser"), ("microsoft-edge", "browser"), ("vivaldi", "browser"), ("opera", "browser"),
    ("vlc", "media"), ("mpv", "media"), ("spotify", "media"), ("rhythmbox", "media"),
    ("totem", "media"), ("celluloid", "media"), ("audacious", "media"),
    ("nautilus", "files"), ("org.gnome.nautilus", "files"), ("dolphin", "files"),
    ("thunar", "files"), ("nemo", "files"), ("pcmanfm", "files"),
    ("code", "editor"), ("jetbrains", "editor"), ("gedit", "editor"), ("text-editor", "editor"),
    ("sublime", "editor"), ("zed", "editor"),
    ("gnome-terminal", "terminal"), ("terminator", "terminal"), ("kitty", "terminal"),
    ("alacritty", "terminal"), ("konsole", "terminal"), ("tilix", "terminal"), ("wezterm", "terminal"),
)

_SWIPE_OPPOSITE: dict[str, str] = {
    "swipe_left": "swipe_right",
    "swipe_right": "swipe_left",
    "swipe_up": "swipe_down",
    "swipe_down": "swipe_up",
}

_EPS = 1e-9              # float comparison tolerance for exact step boundaries
_PALM_POINTS = (WRIST, INDEX_MCP, MIDDLE_MCP, RING_MCP, PINKY_MCP)
_FINGERS = (
    ("index", INDEX_PIP, INDEX_TIP),
    ("middle", MIDDLE_PIP, MIDDLE_TIP),
    ("ring", RING_PIP, RING_TIP),
    ("pinky", PINKY_PIP, PINKY_TIP),
)

GESTURES: tuple[str, ...] = (
    "hand_enter",
    "hand_leave",
    "pose",
    "pinch_start",
    "pinch_end",
    "double_pinch",
    "swipe_left",
    "swipe_right",
    "swipe_up",
    "swipe_down",
    "spread_start",
    "spread_end",
)

POSES: tuple[str, ...] = (
    "open_palm",
    "fist",
    "point",
    "victory",
    "thumbs_up",
    "thumbs_down",
    "pinch",
    "none",
)


@dataclass(frozen=True)
class Point:
    x: float
    y: float
    z: float = 0.0


@dataclass(frozen=True)
class Hand:
    side: str                  # "left" | "right" (user's real hand; frame mirrored before detection)
    score: float               # handedness score 0..1
    points: tuple[Point, ...]  # exactly 21


@dataclass(frozen=True)
class HandFrame:
    t: float                   # seconds (monotonic)
    hands: tuple[Hand, ...]    # 0..2


@dataclass(frozen=True)
class GestureEvent:
    gesture: str
    hand: str
    confidence: float
    x: float
    y: float
    t: float
    data: dict[str, Any] = field(default_factory=dict)

    def to_payload(self, mode: str = "universal") -> dict[str, Any]:
        return {
            "source": "gesture",
            "gesture": self.gesture,
            "confidence": self.confidence,
            "hand": self.hand,
            "position": {"x": round(self.x, 3), "y": round(self.y, 3)},
            "mode": mode,
            "t": self.t,
            **self.data,
        }


@dataclass(frozen=True)
class HandView:
    side: str
    x: float
    y: float
    pose: str
    pinching: bool
    score: float
    tip_x: float = 0.0
    tip_y: float = 0.0


@dataclass(frozen=True)
class AirState:
    camera: str = "off"
    detail: str = ""
    hands: tuple[HandView, ...] = ()
    spread_scale: float | None = None
    fps: float = 0.0
    last_gesture: str = ""
    last_gesture_t: float = 0.0


@dataclass(frozen=True)
class CameraInfo:
    index: int
    name: str
    driver: str
    usable: bool


def dist(a: Point, b: Point) -> float:
    """2-D Euclidean distance in normalized image coordinates."""
    return math.hypot(a.x - b.x, a.y - b.y)


def palm_size(h: Hand) -> float:
    """Reference hand scale: wrist to middle MCP, clamped away from zero."""
    return max(dist(h.points[WRIST], h.points[MIDDLE_MCP]), 1e-6)


def palm_center(h: Hand) -> Point:
    """Mean x/y of the wrist and four finger MCPs."""
    pts = h.points
    n = float(len(_PALM_POINTS))
    return Point(
        sum(pts[i].x for i in _PALM_POINTS) / n,
        sum(pts[i].y for i in _PALM_POINTS) / n,
    )


def pinch_point(h: Hand) -> Point:
    """Midpoint of thumb tip and index tip."""
    a, b = h.points[THUMB_TIP], h.points[INDEX_TIP]
    return Point((a.x + b.x) / 2.0, (a.y + b.y) / 2.0)


def pinch_ratio(h: Hand) -> float:
    """Thumb-to-index tip distance divided by palm size."""
    return dist(h.points[THUMB_TIP], h.points[INDEX_TIP]) / palm_size(h)


def extended(h: Hand, prev: dict[str, bool] | None = None) -> dict[str, bool]:
    """Which fingers are extended, keyed by finger name (with hysteresis when prev is given)."""
    pts = h.points
    wrist = pts[WRIST]
    out: dict[str, bool] = {
        "thumb": dist(pts[THUMB_TIP], pts[INDEX_MCP]) > THUMB_OUT * palm_size(h),
    }
    for name, pip_idx, tip_idx in _FINGERS:
        d_tip = dist(wrist, pts[tip_idx])
        d_pip = dist(wrist, pts[pip_idx])
        if prev is not None and prev.get(name):
            out[name] = not (d_tip < (EXTENDED - 0.08) * d_pip)
        else:
            out[name] = d_tip > EXTENDED * d_pip
    return out


def classify_pose(
    h: Hand,
    pinching: bool,
    prev: dict[str, bool] | None = None,
) -> tuple[str, float]:
    """Classify the static hand pose and return (pose, clarity in 0..1)."""
    if pinching:
        return ("pinch", 1.0)
    ext = extended(h, prev)
    fingers = sum(ext[name] for name, _, _ in _FINGERS)
    if fingers == 4:
        pose = "open_palm"
    elif ext["index"] and not (ext["middle"] or ext["ring"] or ext["pinky"]):
        pose = "point"
    elif ext["index"] and ext["middle"] and not (ext["ring"] or ext["pinky"]):
        pose = "victory"
    elif fingers == 0:
        palm = palm_size(h)
        thumb_y = h.points[THUMB_TIP].y
        idx_y = h.points[INDEX_MCP].y
        if ext["thumb"] and thumb_y < idx_y - 0.3 * palm:
            pose = "thumbs_up"
        elif ext["thumb"] and thumb_y > idx_y + 0.6 * palm:
            pose = "thumbs_down"
        else:
            pose = "fist"
    else:
        pose = "none"

    if pose in ("open_palm", "fist"):
        pts = h.points
        wrist = pts[WRIST]
        clear = 0
        for _, pip_idx, tip_idx in _FINGERS:
            ratio = dist(wrist, pts[tip_idx]) / max(dist(wrist, pts[pip_idx]), 1e-6)
            if pose == "open_palm" and ratio >= EXTENDED + 0.05:
                clear += 1
            elif pose == "fist" and ratio <= EXTENDED - 0.05:
                clear += 1
        return (pose, clear / 4.0)
    return (pose, 1.0)


class OneEuro:
    """1-D One Euro low-pass filter for jitter-free hand positions."""

    def __init__(self, min_cutoff: float = 1.2, beta: float = 0.02, d_cutoff: float = 1.0) -> None:
        self._min_cutoff = min_cutoff
        self._beta = beta
        self._d_cutoff = d_cutoff
        self._t: float | None = None
        self._x: float = 0.0
        self._dx: float = 0.0

    def reset(self) -> None:
        self._t = None
        self._x = 0.0
        self._dx = 0.0

    @staticmethod
    def _alpha(cutoff: float, dt: float) -> float:
        return 1.0 / (1.0 + 1.0 / (2.0 * math.pi * cutoff * dt))

    def __call__(self, value: float, t: float) -> float:
        if self._t is None:
            self._t = t
            self._x = value
            self._dx = 0.0
            return value
        dt = t - self._t
        if dt <= 0.0:
            return self._x
        dx = (value - self._x) / dt
        a_d = self._alpha(self._d_cutoff, dt)
        dx_hat = a_d * dx + (1.0 - a_d) * self._dx
        cutoff = self._min_cutoff + self._beta * abs(dx_hat)
        a = self._alpha(cutoff, dt)
        x_hat = a * value + (1.0 - a) * self._x
        self._t = t
        self._x = x_hat
        self._dx = dx_hat
        return x_hat


class _Track:
    """Per-hand temporal state owned by Recognizer."""

    def __init__(self, side: str, t: float, score: float) -> None:
        self.side = side
        self.last_seen = t
        self.score = score
        self.pinching = False
        self.pinch_start = 0.0
        self.release_since: float | None = None
        self.pinch_was_double = False
        self.last_pinch_end: float | None = None
        self.last_pinch_dur = 0.0
        self.stable_pose = "none"
        self.candidate_pose = ""
        self.candidate_since = t
        self.last_open_palm = -math.inf
        self.last_ext: dict[str, bool] | None = None
        self.palm_x = 0.0
        self.palm_y = 0.0
        self.pinch_x = 0.0
        self.pinch_y = 0.0
        self.tip_x = 0.0
        self.tip_y = 0.0
        self.palm_history: deque[tuple[float, float, float]] = deque()
        self.settled = True
        self.settle_since: float | None = None
        self.palm_fx = OneEuro()
        self.palm_fy = OneEuro()
        self.pinch_fx = OneEuro()
        self.pinch_fy = OneEuro()


def _match_swipe(history: list[tuple[float, float, float]], min_dist: float) -> str | None:
    """Test a palm-position history for a single-axis swipe exceeding min_dist."""
    if len(history) < 2:
        return None
    _, x1, y1 = history[-1]
    for _, x0, y0 in reversed(history[:-1]):
        dx = x1 - x0
        dy = y1 - y0
        if abs(dx) >= min_dist - _EPS and abs(dx) >= SWIPE_AXIS * abs(dy) - _EPS:
            return "swipe_right" if dx > 0 else "swipe_left"
        if abs(dy) >= min_dist - _EPS and abs(dy) >= SWIPE_AXIS * abs(dx) - _EPS:
            return "swipe_down" if dy > 0 else "swipe_up"
    return None


class Recognizer:
    """Stateful, single-threaded gesture recognizer over HandFrames."""

    def __init__(self) -> None:
        self._tracks: dict[str, _Track] = {}
        self._last_swipe: float = -math.inf
        self._last_swipe_dir: str = ""
        self._spread_d0: float | None = None
        self._spread_d: float | None = None
        self._spread_pos: tuple[float, float] = (0.5, 0.5)
        self._spread_conf: float = 0.0

    def reset(self) -> None:
        self._tracks.clear()
        self._last_swipe = -math.inf
        self._last_swipe_dir = ""
        self._spread_d0 = None
        self._spread_d = None
        self._spread_pos = (0.5, 0.5)
        self._spread_conf = 0.0

    def _record_swipe(self, swipe: str, t: float) -> None:
        self._last_swipe = t
        self._last_swipe_dir = swipe
        for track in self._tracks.values():
            track.settled = False
            track.settle_since = None

    def _swipe_decision(self, swipe: str, tr: _Track, t: float) -> str:
        """Direction-aware swipe gate: 'emit', 'swallow' (return stroke), or 'block'."""
        dt = t - self._last_swipe
        if _SWIPE_OPPOSITE.get(self._last_swipe_dir) == swipe:
            if dt < RETURN_WINDOW - _EPS:
                return "swallow"
            if dt >= REVERSE_AFTER - _EPS or tr.settled:
                return "emit"
            return "block"
        if dt >= SWIPE_SAME_COOLDOWN - _EPS:
            return "emit"
        return "block"

    def update(self, frame: HandFrame) -> list[GestureEvent]:
        t = frame.t
        events: list[GestureEvent] = []

        # 1. Keep hands with score >= MIN_SCORE; deduplicate same side by highest score.
        present: dict[str, Hand] = {}
        for hand in frame.hands:
            if hand.score < MIN_SCORE:
                continue
            existing = present.get(hand.side)
            if existing is None or hand.score > existing.score:
                present[hand.side] = hand

        # R2-2: if MediaPipe flipped a lone hand's Left/Right label while turning, keep its track.
        for side, hand in list(present.items()):
            if side in self._tracks or side not in ("left", "right"):
                continue
            other = "right" if side == "left" else "left"
            tr_other = self._tracks.get(other)
            if tr_other is None or other in present:
                continue
            if (t - tr_other.last_seen) >= LEAVE_GRACE - _EPS:
                continue
            pc = palm_center(hand)
            if math.hypot(pc.x - tr_other.palm_x, pc.y - tr_other.palm_y) <= TRACK_JUMP + _EPS:
                del present[side]
                present[other] = Hand(other, hand.score, hand.points)

        # 2–5. Process each present hand.
        for side, hand in present.items():
            pc = palm_center(hand)
            pp = pinch_point(hand)
            tip = hand.points[INDEX_TIP]
            tr = self._tracks.get(side)
            if tr is None:
                tr = _Track(side, t, hand.score)
                self._tracks[side] = tr
                tr.palm_x = tr.palm_fx(pc.x, t)
                tr.palm_y = tr.palm_fy(pc.y, t)
                tr.pinch_x = tr.pinch_fx(pp.x, t)
                tr.pinch_y = tr.pinch_fy(pp.y, t)
                tr.tip_x = tip.x
                tr.tip_y = tip.y
                events.append(GestureEvent(
                    "hand_enter", side, round(hand.score, 2), tr.palm_x, tr.palm_y, t,
                ))
            else:
                tr.last_seen = t
                tr.score = hand.score
                tr.palm_x = tr.palm_fx(pc.x, t)
                tr.palm_y = tr.palm_fy(pc.y, t)
                tr.pinch_x = tr.pinch_fx(pp.x, t)
                tr.pinch_y = tr.pinch_fy(pp.y, t)
                tr.tip_x = tip.x
                tr.tip_y = tip.y

            # 3. Pinch hysteresis (with index reach guard, release debounce) and double-pinch detection.
            ratio = pinch_ratio(hand)
            wrist_tip = dist(hand.points[WRIST], hand.points[INDEX_TIP])
            wrist_pip = dist(hand.points[WRIST], hand.points[INDEX_PIP])
            if not tr.pinching and ratio < PINCH_ON and wrist_tip > PINCH_REACH * wrist_pip:
                tr.pinching = True
                tr.pinch_start = t
                tr.release_since = None
                tr.palm_history.clear()
                events.append(GestureEvent(
                    "pinch_start", side, round(hand.score, 2), tr.pinch_x, tr.pinch_y, t,
                ))
                if (
                    tr.last_pinch_end is not None
                    and (t - tr.last_pinch_end) <= DOUBLE_PINCH_GAP + _EPS
                    and tr.last_pinch_dur <= SHORT_PINCH + _EPS
                ):
                    events.append(GestureEvent(
                        "double_pinch", side, round(hand.score, 2), tr.pinch_x, tr.pinch_y, t, {},
                    ))
                    tr.pinch_was_double = True
                    tr.last_pinch_end = None
                    tr.last_pinch_dur = 0.0
                else:
                    tr.pinch_was_double = False
            elif tr.pinching:
                if ratio > PINCH_OFF or wrist_tip < PINCH_REACH_END * wrist_pip:
                    if tr.release_since is None:
                        tr.release_since = t
                    elif (t - tr.release_since) >= RELEASE_HOLD - _EPS:
                        tr.pinching = False
                        tr.release_since = None
                        dur = max(0.0, t - tr.pinch_start)
                        events.append(GestureEvent(
                            "pinch_end", side, round(hand.score, 2), tr.pinch_x, tr.pinch_y, t,
                            {"duration": round(dur, 2)},
                        ))
                        if tr.pinch_was_double:
                            tr.last_pinch_end = None
                            tr.last_pinch_dur = 0.0
                        else:
                            tr.last_pinch_end = t
                            tr.last_pinch_dur = dur
                        tr.pinch_was_double = False
                else:
                    tr.release_since = None

            # 4. Pose stability filter (with finger extension hysteresis).
            pose, clarity = classify_pose(hand, tr.pinching, tr.last_ext)
            tr.last_ext = extended(hand, tr.last_ext)
            if pose == "open_palm":
                tr.last_open_palm = t
            if pose != tr.candidate_pose:
                tr.candidate_pose = pose
                tr.candidate_since = t
            elif (
                (t - tr.candidate_since) >= POSE_STABLE - _EPS
                and tr.candidate_pose != tr.stable_pose
            ):
                tr.stable_pose = tr.candidate_pose
                if tr.stable_pose not in ("pinch", "none"):
                    events.append(GestureEvent(
                        "pose", side, round(hand.score * clarity, 2), tr.palm_x, tr.palm_y, t,
                        {"pose": tr.stable_pose},
                    ))

            # 5. Swipe detection using raw palm centre (open_palm now or within last 0.5 s).
            recent_open = (t - tr.last_open_palm) <= 0.5 + _EPS
            if not tr.pinching and (tr.stable_pose == "open_palm" or recent_open):
                if not tr.settled and tr.palm_history:
                    t0, x0, y0 = tr.palm_history[-1]
                    dt = t - t0
                    if dt > 0.0:
                        speed = math.hypot(pc.x - x0, pc.y - y0) / dt
                        if speed < SETTLE_SPEED:
                            if tr.settle_since is None:
                                tr.settle_since = t0
                            if (t - tr.settle_since) >= SETTLE_TIME - _EPS:
                                tr.settled = True
                                tr.settle_since = None
                        else:
                            tr.settle_since = None
                tr.palm_history.append((t, pc.x, pc.y))
                cutoff = t - SWIPE_WINDOW - _EPS
                while tr.palm_history and tr.palm_history[0][0] < cutoff:
                    tr.palm_history.popleft()
                swipe = _match_swipe(list(tr.palm_history), SWIPE_DIST)
                if swipe is not None:
                    decision = self._swipe_decision(swipe, tr, t)
                    if decision == "emit":
                        self._record_swipe(swipe, t)
                        tr.palm_history.clear()
                        tr.palm_history.append((t, pc.x, pc.y))
                        events.append(GestureEvent(
                            swipe, side, round(hand.score, 2), tr.palm_x, tr.palm_y, t,
                        ))
                    elif decision == "swallow":
                        tr.palm_history.clear()
                        tr.palm_history.append((t, pc.x, pc.y))
            else:
                tr.palm_history.clear()
                tr.settle_since = None

        # 6. Tracks missing past LEAVE_GRACE: release pinch or check exit swipe, then leave.
        for side in list(self._tracks):
            if side in present:
                continue
            tr = self._tracks[side]
            if (t - tr.last_seen) >= LEAVE_GRACE - _EPS:
                if tr.pinching:
                    dur = max(0.0, t - tr.pinch_start)
                    events.append(GestureEvent(
                        "pinch_end", side, round(tr.score, 2), tr.pinch_x, tr.pinch_y, t,
                        {"duration": round(dur, 2), "lost": True},
                    ))
                    tr.pinching = False
                    tr.release_since = None
                    tr.pinch_was_double = False
                elif tr.palm_history:
                    cutoff = tr.last_seen - SWIPE_WINDOW - _EPS
                    recent = [entry for entry in tr.palm_history if entry[0] >= cutoff]
                    swipe = _match_swipe(recent, 0.7 * SWIPE_DIST)
                    if swipe is not None and self._swipe_decision(swipe, tr, tr.last_seen) == "emit":
                        self._record_swipe(swipe, t)
                        events.append(GestureEvent(
                            swipe, side, round(tr.score, 2), tr.palm_x, tr.palm_y, t,
                        ))
                events.append(GestureEvent(
                    "hand_leave", side, round(tr.score, 2), tr.palm_x, tr.palm_y, t,
                ))
                del self._tracks[side]

        # 7. Two-hand spread (both hands pinching).
        left = self._tracks.get("left")
        right = self._tracks.get("right")
        both_pinching = (
            left is not None and right is not None and left.pinching and right.pinching
        )
        if both_pinching:
            assert left is not None and right is not None
            lp = Point(left.pinch_x, left.pinch_y)
            rp = Point(right.pinch_x, right.pinch_y)
            d = dist(lp, rp)
            mx = (lp.x + rp.x) / 2.0
            my = (lp.y + rp.y) / 2.0
            conf = round(min(left.score, right.score), 2)
            if self._spread_d0 is None:
                if d > SPREAD_MIN_DIST and left.release_since is None and right.release_since is None:
                    self._spread_d0 = d
                    self._spread_d = d
                    self._spread_pos = (mx, my)
                    self._spread_conf = conf
                    events.append(GestureEvent("spread_start", "both", conf, mx, my, t))
            elif left.release_since is None and right.release_since is None:
                self._spread_d = d
                self._spread_pos = (mx, my)
                self._spread_conf = conf
        elif self._spread_d0 is not None:
            last_d = self._spread_d if self._spread_d is not None else self._spread_d0
            scale = round(last_d / self._spread_d0, 2)
            mx, my = self._spread_pos
            events.append(GestureEvent(
                "spread_end", "both", self._spread_conf, mx, my, t, {"scale": scale},
            ))
            self._spread_d0 = None
            self._spread_d = None

        return events

    def views(self, t: float) -> tuple[HandView, ...]:
        """Current tracked hands as immutable views."""
        out: list[HandView] = []
        for side in sorted(self._tracks):
            tr = self._tracks[side]
            if (t - tr.last_seen) >= LEAVE_GRACE - _EPS:
                continue
            x, y = (tr.pinch_x, tr.pinch_y) if tr.pinching else (tr.palm_x, tr.palm_y)
            pose = "pinch" if tr.pinching else tr.stable_pose
            out.append(HandView(
                side=tr.side, x=x, y=y, pose=pose, pinching=tr.pinching, score=tr.score,
                tip_x=tr.tip_x, tip_y=tr.tip_y,
            ))
        return tuple(out)

    def spread_scale(self) -> float | None:
        if self._spread_d0 is None or self._spread_d is None:
            return None
        return self._spread_d / self._spread_d0


class Stats:
    """Rolling performance metrics over the last 120 frames."""

    def __init__(self, window: int = 120) -> None:
        self._frames = 0
        self._hand_frames = 0
        self._detect: deque[float] = deque(maxlen=window)
        self._loop: deque[float] = deque(maxlen=window)
        self._times: deque[float] = deque(maxlen=window)

    def add(self, detect_ms: float, loop_ms: float, t: float, hands: int) -> None:
        self._frames += 1
        if hands >= 1:
            self._hand_frames += 1
        self._detect.append(detect_ms)
        self._loop.append(loop_ms)
        self._times.append(t)

    def summary(self) -> dict[str, float | int]:
        n = len(self._detect)
        if n == 0:
            return {
                "frames": 0,
                "fps": 0.0,
                "detect_ms_avg": 0.0,
                "detect_ms_p95": 0.0,
                "loop_ms_avg": 0.0,
                "hand_frames": 0,
            }
        span = self._times[-1] - self._times[0]
        fps = ((n - 1) / span) if (n >= 2 and span > 0.0) else 0.0
        ordered = sorted(self._detect)
        p95_idx = min(n - 1, max(0, math.ceil(0.95 * n) - 1))
        return {
            "frames": self._frames,
            "fps": round(fps, 1),
            "detect_ms_avg": round(sum(self._detect) / n, 1),
            "detect_ms_p95": round(ordered[p95_idx], 1),
            "loop_ms_avg": round(sum(self._loop) / n, 1),
            "hand_frames": self._hand_frames,
        }


def mac_camera_info(index: int, name: str, device_type: str, transport: int) -> CameraInfo:
    """Classify a macOS AVFoundation camera device into a CameraInfo."""
    low = name.lower()
    if transport == MAC_TRANSPORT_VIRTUAL or any(h in low for h in VIRTUAL_CAMERA_HINTS):
        driver, usable = "virtual", False
    elif device_type == MAC_TYPE_CONTINUITY or any(h in low for h in PHONE_CAMERA_HINTS):
        driver, usable = "continuity", False
    elif transport == MAC_TRANSPORT_BUILTIN or device_type == MAC_TYPE_BUILTIN:
        driver, usable = "builtin", True
    else:
        driver, usable = "usb", True
    return CameraInfo(index=index, name=name or f"Camera {index}", driver=driver, usable=usable)


def pick_camera(cams: list[CameraInfo], wanted: str = "") -> CameraInfo | None:
    """Choose the best usable camera, optionally matching a name substring or index.

    A virtual camera (a phone through v4l2loopback on Linux, or Camo, OBS or an iPhone on macOS)
    is never picked by itself — it shows nothing unless its app is running — but naming it
    (``wanted``) selects it. Metadata nodes never are.
    """
    usable = [c for c in cams if c.usable]
    needle = wanted.strip()
    if needle:
        low = needle.lower()
        named = usable + [
            c for c in cams if not c.usable and c.driver in ("v4l2loopback", "virtual", "continuity")
        ]
        for cam in named:
            if low in cam.name.lower() or str(cam.index) == needle:
                return cam
        return None
    if not usable:
        return None
    return min(usable, key=lambda c: (0 if c.driver in ("uvcvideo", "builtin") else 1, c.index))


def camera_candidates(cams: list[CameraInfo], wanted: str = "") -> list[CameraInfo]:
    """Cameras to try in order: the one asked for, then the best built-in one as a fallback.

    A phone camera (v4l2loopback) that is switched off, unplugged or not delivering frames is
    replaced by the laptop's internal camera instead of leaving Air without a camera.
    """
    out: list[CameraInfo] = []
    for cam in (pick_camera(cams, wanted) if wanted.strip() else None, pick_camera(cams, "")):
        if cam is not None and all(c.index != cam.index for c in out):
            out.append(cam)
    return out


# ── control geometry & command ring ─────────────────────────────────────────

def edge_snap(rect: Rect, bounds: Rect) -> str | None:
    """Return 'left' or 'right' when ``rect`` touches within SNAP_PX of a screen edge."""
    if rect.x <= bounds.x + SNAP_PX:
        return "left"
    if rect.x + rect.width >= bounds.x + bounds.width - SNAP_PX:
        return "right"
    return None


def moved(a: Rect, b: Rect) -> bool:
    """True when any edge coordinate or size differs by at least PLACE_MIN_PX."""
    return (
        abs(a.x - b.x) >= PLACE_MIN_PX
        or abs(a.y - b.y) >= PLACE_MIN_PX
        or abs(a.width - b.width) >= PLACE_MIN_PX
        or abs(a.height - b.height) >= PLACE_MIN_PX
    )


def app_kind(app: str, title: str, overrides: dict[str, str] | None = None) -> str:
    """Classify a window's WM_CLASS + title into one of ``KINDS``."""
    low = (app or "").lower()
    if overrides:
        for needle, kind in overrides.items():
            key = needle.strip().lower()
            if key and key in low and kind in KINDS:
                return kind
    if ("libreoffice" in low or "soffice" in low) and "impress" in (title or "").lower():
        return "presentation"
    for needle, kind in APP_KINDS:
        if needle in low:
            return kind
    return "other"


_RING_DIRS: tuple[str, str, str, str] = ("left", "right", "up", "down")
_UNIVERSAL_ALIASES = frozenset({"pointer", "files", "developer"})

_UNIVERSAL_RING_LR: dict[str, tuple[tuple[str, dict, str], tuple[str, dict, str]]] = {
    "browser": (
        ("press_keys", {"keys": "ctrl+shift+Tab"}, "TAB<"),
        ("press_keys", {"keys": "ctrl+Tab"}, "TAB>"),
    ),
    "media": (
        ("media", {"command": "previous"}, "PREV"),
        ("media", {"command": "next"}, "NEXT"),
    ),
    "presentation": (
        ("press_keys", {"keys": "Left"}, "SLIDE<"),
        ("press_keys", {"keys": "Right"}, "SLIDE>"),
    ),
    "files": (
        ("press_keys", {"keys": "alt+Left"}, "BACK"),
        ("press_keys", {"keys": "alt+Right"}, "FWD"),
    ),
    "editor": (
        ("press_keys", {"keys": "ctrl+Page_Up"}, "TAB<"),
        ("press_keys", {"keys": "ctrl+Page_Down"}, "TAB>"),
    ),
    "terminal": (
        ("press_keys", {"keys": "ctrl+Page_Up"}, "TAB<"),
        ("press_keys", {"keys": "ctrl+Page_Down"}, "TAB>"),
    ),
    "other": (
        ("workspace_switch", {"target": "prev"}, "DESK<"),
        ("workspace_switch", {"target": "next"}, "DESK>"),
    ),
}

_MODE_RING: dict[str, tuple[tuple[str, dict, str], ...]] = {
    "workspace": (
        ("workspace_switch", {"target": "prev"}, "DESK<"),
        ("workspace_switch", {"target": "next"}, "DESK>"),
        ("workspace_overview", {}, "OVERVIEW"),
        ("show_desktop", {}, "DESKTOP"),
    ),
    "window": (
        ("window_snap", {"where": "left"}, "SNAP<"),
        ("window_snap", {"where": "right"}, "SNAP>"),
        ("toggle_maximize", {}, "MAX"),
        ("window_minimize", {}, "MIN"),
    ),
    "media": (
        ("media", {"command": "previous"}, "PREV"),
        ("media", {"command": "next"}, "NEXT"),
        ("volume_change", {"delta": 10}, "VOL+"),
        ("volume_change", {"delta": -10}, "VOL-"),
    ),
    "presentation": (
        ("press_keys", {"keys": "Left"}, "SLIDE<"),
        ("press_keys", {"keys": "Right"}, "SLIDE>"),
        ("ignore", {}, ""),
        ("ignore", {}, ""),
    ),
    "ai": (
        ("intent", {"intent": "talk"}, "TALK"),
        ("intent", {"intent": "talk"}, "TALK"),
        ("intent", {"intent": "talk"}, "TALK"),
        ("intent", {"intent": "talk"}, "TALK"),
    ),
}


def _ring_entries(mode: str, kind: str) -> tuple[tuple[str, dict, str], ...]:
    norm = "universal" if mode in _UNIVERSAL_ALIASES else mode
    if norm in _MODE_RING:
        return _MODE_RING[norm]
    left, right = _UNIVERSAL_RING_LR.get(kind, _UNIVERSAL_RING_LR["other"])
    return (
        left,
        right,
        ("toggle_maximize", {}, "MAX"),
        ("click", {}, "CLICK"),
    )


def ring_action(direction: str, mode: str, kind: str) -> tuple[str, dict]:
    """Return ``(action, args)`` for a command-ring direction in the given mode and app kind."""
    if direction not in _RING_DIRS:
        return ("ignore", {})
    action, args, _ = _ring_entries(mode, kind)[_RING_DIRS.index(direction)]
    return (action, dict(args))


def ring_labels(mode: str, kind: str) -> tuple[str, str, str, str]:
    """Return short ASCII ``(left, right, up, down)`` ring labels for the HUD."""
    entries = _ring_entries(mode, kind)
    return (entries[0][2], entries[1][2], entries[2][2], entries[3][2])


def framing_hint(views: tuple[HandView, ...]) -> str | None:
    """Return a framing hint when a tracked hand sits near the bottom or side edge."""
    if any(h.y > 1.0 - HINT_EDGE for h in views):
        return "Raise your hand a little."
    if any(h.x < HINT_EDGE or h.x > 1.0 - HINT_EDGE for h in views):
        return "Move your hand towards the middle."
    return None


# ── preview flash & pointer glide ───────────────────────────────────────────

_FLASH_LABELS: dict[str, str] = {
    "click": "CLICK",
    "right_click": "RIGHT CLICK",
    "press": "HOLD",
    "release": "RELEASE",
    "scroll": "SCROLL",
    "swipe": "SWIPE",
    "two_start": "TWO HANDS",
    "engage": "AIR ON",
    "disengage": "AIR OFF",
}


class PreviewFlash:
    """Which gesture label the camera preview shows: the newest action for a moment, else the phase."""

    def __init__(self) -> None:
        self._label = ""
        self._t = -math.inf
        self._last_click_t = -math.inf

    def note(self, kind: str, t: float) -> None:
        if kind not in _FLASH_LABELS:
            return
        label = _FLASH_LABELS[kind]
        if kind == "click":
            if t - self._last_click_t <= PREVIEW_DOUBLE_S:
                label = "DOUBLE CLICK"
            self._last_click_t = t
        self._label, self._t = label, t

    def label(self, t: float, phase: str) -> str:
        if self._label and t - self._t <= PREVIEW_FLASH_S:
            return self._label
        return (phase or "").replace("_", " ").upper()


class PointerGlide:
    """Linear glide of the mouse pointer from where it is drawn to the newest target."""

    def __init__(self) -> None:
        self._from: tuple[float, float] | None = None
        self._to: tuple[float, float] | None = None
        self._t0 = 0.0
        self._span = GLIDE_SPAN_MIN

    def retarget(self, start: tuple[float, float], target: tuple[float, float], now: float, span: float) -> None:
        cur = self.at(now)
        self._from = cur if cur is not None else start
        self._to = target
        self._t0 = now
        self._span = max(GLIDE_SPAN_MIN, min(GLIDE_SPAN_MAX, span))

    def at(self, now: float) -> tuple[float, float] | None:
        if self._to is None or self._from is None:
            return None
        f = (now - self._t0) / self._span
        if f >= 1.0:
            return self._to
        f = max(0.0, f)
        return (
            self._from[0] + (self._to[0] - self._from[0]) * f,
            self._from[1] + (self._to[1] - self._from[1]) * f,
        )

    def done(self, now: float) -> bool:
        return self._to is None or (now - self._t0) >= self._span

    def finish(self) -> tuple[float, float] | None:
        to = self._to
        self._from = self._to = None
        return to
