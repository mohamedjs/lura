"""Pure Air interaction engine: hand landmarks and timestamps in, discrete Commands out."""

from __future__ import annotations

import math
from collections import deque
from collections.abc import Sequence
from dataclasses import dataclass

from .domain import (
    INDEX_PIP,
    INDEX_TIP,
    WRIST,
    Hand,
    OneEuro,
    Point,
    classify_pose,
    dist,
    extended,
    palm_center,
    palm_size,
    pinch_ratio,
)

# All values measured on session 2 (phone camera) unless stated.
ZONE_PALM = 0.20          # palm size (wrist→middle MCP, norm.) needed to count; typing is 0.11–0.14
EDGE = 0.02               # every landmark must be at least this far inside the frame
THUMB_PALM = 0.12         # thumbs up/down are held lower/farther (p10 0.13)
TRACK_PALM = 0.12         # once something is running, a hand this big keeps it going (no edge test)
ENGAGE_HOLD = 0.8         # s of still open palm in the zone to engage
STILL_STEP = 0.01         # max palm-centre move per frame that still counts as "still"
DISENGAGE_AFTER = 3.0     # s without an in-zone hand
PINCH_ON, PINCH_OFF = 0.28, 0.40
PINCH_TIGHT, PINCH_SLIP = 0.15, 0.24
PINCH_REACH, PINCH_REACH_END = 0.80, 0.70
RELEASE_HOLD = 0.10       # s the release condition must hold
REWIND = 0.10             # s of cursor movement undone at pinch start (the pinch moves the hand)
LEAN = 0.10               # normalized palm displacement that selects a ring direction
LEAN_AXIS = 1.5           # dominant axis must be ≥ this × the other
HOLD_RADIUS = 0.04        # stays "in the centre" while displacement < this
GRAB_HOLD = 0.6           # s in the centre to turn the ring into a grab
RING_TIMEOUT = 3.0        # s without a direction or grab → cancel
SCROLL_ENTER = 0.3        # s of V before scrolling
SCROLL_STEP = 0.03        # normalized vertical travel per scroll step
THUMB_HOLD = 0.5
THUMB_REPEAT = 3.0        # s: the same thumbs answer again within this counts once
HINT_AFTER, HINT_EVERY = 1.5, 10.0
CURSOR_GAIN = 1.5         # full-frame-width hand move = 1920 × CURSOR_GAIN screen px (1.5 monitors)
EURO_MIN_CUTOFF, EURO_BETA = 0.5, 0.007

_EPS = 1e-9
_HINT_MIN_PALM = 0.08
_CURSOR_HIST_WINDOW = 0.25
_SCROLL_EXIT = 0.20
_PROGRESS_STEP = 0.05
_FAR_HINT_TEXT = "Move your hand closer to the camera."


@dataclass(frozen=True)
class Command:
    kind: str     # "engage" "disengage" "cursor" "ring_open" "ring_hover" "ring_select" "ring_cancel"
                  # "grab_start" "grab_move" "grab_end" "scroll" "intent" "hint"
    t: float
    dx: float = 0.0
    dy: float = 0.0     # cursor / grab_move: screen px (relative)
    direction: str = ""                  # ring_hover / ring_select: "left" "right" "up" "down" ("" = centre)
    steps: int = 0                       # scroll: + = up, − = down
    text: str = ""                       # intent: "confirm"/"deny"; hint: the sentence
    progress: float = 0.0                # ring_hover while holding in the centre: 0..1 towards GRAB_HOLD


def _in_frame(hand: Hand) -> bool:
    return all(EDGE <= p.x <= 1.0 - EDGE and EDGE <= p.y <= 1.0 - EDGE for p in hand.points)


def _ring_direction(dx: float, dy: float) -> str:
    ax, ay = abs(dx), abs(dy)
    if ax >= LEAN - _EPS and ax >= LEAN_AXIS * ay - _EPS:
        return "right" if dx > 0.0 else "left"
    if ay >= LEAN - _EPS and ay >= LEAN_AXIS * ax - _EPS:
        return "down" if dy > 0.0 else "up"
    return ""


class InteractionEngine:
    """Pure stateful interaction machine: one-hand zone, engage, cursor, ring, grab, scroll, thumbs."""

    def __init__(self, screen_px_per_frame: float = 2880.0) -> None:
        self._scale_x = float(screen_px_per_frame)
        self._scale_y = float(screen_px_per_frame) * (9.0 / 16.0)
        self._fx = OneEuro(min_cutoff=EURO_MIN_CUTOFF, beta=EURO_BETA)
        self._fy = OneEuro(min_cutoff=EURO_MIN_CUTOFF, beta=EURO_BETA)
        self.reset()

    def reset(self) -> None:
        self._phase: str = "idle"
        self._zone: str = "none"
        self._ring_hover: str = ""
        self._grab_progress: float = 0.0

        self._last_ext: dict[str, bool] | None = None
        self._still_since: float | None = None
        self._prev_pc: Point | None = None
        self._far_since: float | None = None
        self._last_hint_t: float = -math.inf

        self._last_active_t: float = -math.inf
        self._fx.reset()
        self._fy.reset()
        self._last_fx: float | None = None
        self._last_fy: float | None = None
        self._acc_x: float = 0.0
        self._acc_y: float = 0.0
        self._cursor_hist: deque[tuple[float, float, float]] = deque()

        self._anchor: Point | None = None
        self._ring_start: float = 0.0
        self._center_since: float | None = None
        self._last_emitted_progress: float = 0.0
        self._release_since: float | None = None
        self._lost_since: float | None = None
        self._ignore_pinch: bool = False

        self._v_since: float | None = None
        self._not_v_since: float | None = None
        self._scroll_y: float = 0.0
        self._scroll_acc: float = 0.0

        self._thumb_pose: str = ""
        self._thumb_since: float = 0.0
        self._thumb_fired: bool = False
        self._thumb_last: tuple[str, float] = ("", -1e9)

    @property
    def state(self) -> dict:
        return {
            "engaged": self._phase != "idle",
            "zone": self._zone,
            "phase": self._phase,
            "ring_hover": self._ring_hover,
            "grab_progress": self._grab_progress,
        }

    def _seed_filter(self, pc: Point, t: float) -> None:
        self._fx.reset()
        self._fy.reset()
        self._last_fx = self._fx(pc.x, t)
        self._last_fy = self._fy(pc.y, t)
        self._acc_x = 0.0
        self._acc_y = 0.0

    def _enter_ring(self, t: float, pc: Point, out: list[Command]) -> None:
        rw_x = sum(dx for ct, dx, _ in self._cursor_hist if (t - ct) <= REWIND + _EPS)
        rw_y = sum(dy for ct, _, dy in self._cursor_hist if (t - ct) <= REWIND + _EPS)
        if rw_x != 0.0 or rw_y != 0.0:
            out.append(Command("cursor", t, dx=-rw_x, dy=-rw_y))
        self._cursor_hist.clear()
        self._acc_x = 0.0
        self._acc_y = 0.0

        self._phase = "ring"
        self._anchor = pc
        self._ring_start = t
        self._ring_hover = ""
        self._grab_progress = 0.0
        self._last_emitted_progress = 0.0
        self._center_since = t
        self._release_since = None
        self._lost_since = None
        self._v_since = None
        out.append(Command("ring_open", t))

    def _update_thumbs(
        self,
        t: float,
        hand: Hand | None,
        palm: float,
        pose: str,
        out: list[Command],
    ) -> None:
        if self._phase in ("ring", "grab"):
            self._thumb_pose = ""
            self._thumb_fired = False
            return
        if hand is not None and palm >= THUMB_PALM - _EPS and pose in ("thumbs_up", "thumbs_down"):
            if pose != self._thumb_pose:
                self._thumb_pose = pose
                self._thumb_since = t
                self._thumb_fired = False
            elif not self._thumb_fired and (t - self._thumb_since) >= THUMB_HOLD - _EPS:
                self._thumb_fired = True
                intent = "confirm" if pose == "thumbs_up" else "deny"
                # One held thumbs up flickers through "fist" and re-arms; replay of session 2 gave
                # three confirms for one hold. The same answer twice within THUMB_REPEAT is one.
                last_intent, last_t = self._thumb_last
                if intent != last_intent or (t - last_t) >= THUMB_REPEAT - _EPS:
                    self._thumb_last = (intent, t)
                    out.append(Command("intent", t, text=intent))
        else:
            self._thumb_pose = ""
            self._thumb_fired = False

    def update(self, t: float, hands: Sequence[Hand]) -> list[Command]:
        out: list[Command] = []
        hand = max(hands, key=lambda h: h.score) if hands else None

        if hand is not None:
            palm = palm_size(hand)
            pc = palm_center(hand)
            in_edge = _in_frame(hand)
            in_zone = palm >= ZONE_PALM - _EPS and in_edge
            tracked = palm >= TRACK_PALM - _EPS
            ratio = pinch_ratio(hand)
            wrist_tip = dist(hand.points[WRIST], hand.points[INDEX_TIP])
            wrist_pip = dist(hand.points[WRIST], hand.points[INDEX_PIP])
            pinch_on = (
                (ratio < PINCH_ON and wrist_tip > PINCH_REACH * wrist_pip)
                or ratio < PINCH_TIGHT
            )
            pinch_released = (
                ratio > PINCH_OFF
                or (wrist_tip < PINCH_REACH_END * wrist_pip and ratio > PINCH_SLIP)
            )
            pose, _ = classify_pose(
                hand,
                self._phase in ("ring", "grab") or self._ignore_pinch,
                self._last_ext,
            )
            self._last_ext = extended(hand, self._last_ext)
            if in_zone:
                self._zone = "ok"
            elif palm < ZONE_PALM - _EPS:
                self._zone = "far"
            else:
                self._zone = "none"
        else:
            palm = 0.0
            pc = None
            in_zone = False
            tracked = False
            pinch_on = False
            pinch_released = True
            pose = "none"
            self._last_ext = None
            self._zone = "none"

        if self._ignore_pinch and (hand is None or pinch_released):
            self._ignore_pinch = False

        # Thumbs work in any phase except ring/grab (zone not required, palm >= THUMB_PALM).
        self._update_thumbs(t, hand, palm, pose, out)

        if self._phase == "idle":
            if in_zone and pose == "open_palm":
                assert pc is not None
                self._far_since = None
                if (
                    self._prev_pc is not None
                    and math.hypot(pc.x - self._prev_pc.x, pc.y - self._prev_pc.y) < STILL_STEP
                ):
                    if self._still_since is None:
                        self._still_since = t
                else:
                    self._still_since = t
                self._prev_pc = pc
                if (t - self._still_since) >= ENGAGE_HOLD - _EPS:
                    self._phase = "pointing"
                    self._last_active_t = t
                    self._still_since = None
                    self._prev_pc = None
                    self._cursor_hist.clear()
                    self._seed_filter(pc, t)
                    out.append(Command("engage", t))
            else:
                self._still_since = None
                self._prev_pc = None
                if hand is not None and _HINT_MIN_PALM - _EPS <= palm < ZONE_PALM - _EPS:
                    if self._far_since is None:
                        self._far_since = t
                    elif (
                        (t - self._far_since) >= HINT_AFTER - _EPS
                        and (t - self._last_hint_t) >= HINT_EVERY - _EPS
                    ):
                        self._last_hint_t = t
                        self._far_since = t
                        out.append(Command("hint", t, text=_FAR_HINT_TEXT))
                else:
                    self._far_since = None
            return out

        # Engaged: zone hysteresis keeps the session alive while palm >= TRACK_PALM.
        if tracked:
            self._last_active_t = t
        elif (t - self._last_active_t) >= DISENGAGE_AFTER - _EPS:
            if self._phase == "ring":
                self._ring_hover = ""
                self._grab_progress = 0.0
                out.append(Command("ring_cancel", t))
            elif self._phase == "grab":
                out.append(Command("grab_end", t))
            out.append(Command("disengage", t))
            self._phase = "idle"
            self._still_since = None
            self._prev_pc = None
            self._far_since = None
            self._anchor = None
            self._v_since = None
            self._not_v_since = None
            self._release_since = None
            self._lost_since = None
            self._cursor_hist.clear()
            self._acc_x = 0.0
            self._acc_y = 0.0
            return out

        if self._phase in ("pointing", "frozen"):
            if in_zone and pinch_on and not self._ignore_pinch:
                assert pc is not None
                self._enter_ring(t, pc, out)
                return out

            if tracked and pose == "victory":
                assert pc is not None
                if self._v_since is None:
                    self._v_since = t
                if (t - self._v_since) >= SCROLL_ENTER - _EPS:
                    self._phase = "scroll"
                    self._scroll_y = pc.y
                    self._scroll_acc = 0.0
                    self._not_v_since = None
                    self._v_since = None
                    return out
                if self._phase == "pointing":
                    self._phase = "frozen"
                return out
            self._v_since = None

            if self._phase == "pointing":
                if not in_zone or pose != "open_palm":
                    self._phase = "frozen"
                    return out
                assert pc is not None
                nx = self._fx(pc.x, t)
                ny = self._fy(pc.y, t)
                if self._last_fx is not None and self._last_fy is not None:
                    self._acc_x += (nx - self._last_fx) * self._scale_x
                    self._acc_y += (ny - self._last_fy) * self._scale_y
                    if abs(self._acc_x) >= 1.0 or abs(self._acc_y) >= 1.0:
                        ex = float(math.trunc(self._acc_x))
                        ey = float(math.trunc(self._acc_y))
                        self._acc_x -= ex
                        self._acc_y -= ey
                        self._cursor_hist.append((t, ex, ey))
                        while (
                            self._cursor_hist
                            and (t - self._cursor_hist[0][0]) > _CURSOR_HIST_WINDOW + _EPS
                        ):
                            self._cursor_hist.popleft()
                        out.append(Command("cursor", t, dx=ex, dy=ey))
                self._last_fx = nx
                self._last_fy = ny
            else:
                if in_zone and pose == "open_palm":
                    assert pc is not None
                    self._phase = "pointing"
                    self._seed_filter(pc, t)
            return out

        if self._phase == "ring":
            if not tracked:
                if self._lost_since is None:
                    self._lost_since = t
                if (t - self._lost_since) >= RELEASE_HOLD - _EPS:
                    self._ring_hover = ""
                    self._grab_progress = 0.0
                    self._anchor = None
                    self._lost_since = None
                    self._release_since = None
                    self._phase = "frozen"
                    out.append(Command("ring_cancel", t))
                return out
            self._lost_since = None

            if pinch_released:
                if self._release_since is None:
                    self._release_since = t
                if (t - self._release_since) >= RELEASE_HOLD - _EPS:
                    chosen = self._ring_hover
                    self._ring_hover = ""
                    self._grab_progress = 0.0
                    self._anchor = None
                    self._release_since = None
                    self._phase = "frozen"
                    if chosen:
                        out.append(Command("ring_select", t, direction=chosen))
                    else:
                        out.append(Command("ring_cancel", t))
                    return out
            else:
                self._release_since = None

            assert pc is not None and self._anchor is not None
            dx = pc.x - self._anchor.x
            dy = pc.y - self._anchor.y
            disp = math.hypot(dx, dy)
            new_dir = _ring_direction(dx, dy)

            if disp < HOLD_RADIUS - _EPS:
                if self._center_since is None:
                    self._center_since = t
                held = t - self._center_since
                if held >= GRAB_HOLD - _EPS:
                    self._ring_hover = ""
                    self._grab_progress = 0.0
                    self._anchor = None
                    self._center_since = None
                    self._release_since = None
                    self._lost_since = None
                    self._phase = "grab"
                    self._seed_filter(pc, t)
                    out.append(Command("grab_start", t))
                    return out
                prog = min(1.0, max(0.0, held / GRAB_HOLD))
                self._grab_progress = prog
                if (
                    new_dir != self._ring_hover
                    or abs(prog - self._last_emitted_progress) >= _PROGRESS_STEP - _EPS
                ):
                    self._ring_hover = new_dir
                    self._last_emitted_progress = prog
                    out.append(Command("ring_hover", t, direction="", progress=prog))
            else:
                self._center_since = None
                had_progress = self._grab_progress > 0.0
                self._grab_progress = 0.0
                if new_dir != self._ring_hover or had_progress:
                    self._ring_hover = new_dir
                    self._last_emitted_progress = 0.0
                    out.append(Command("ring_hover", t, direction=new_dir, progress=0.0))

            if not self._ring_hover and (t - self._ring_start) >= RING_TIMEOUT - _EPS:
                self._ring_hover = ""
                self._grab_progress = 0.0
                self._anchor = None
                self._center_since = None
                self._release_since = None
                self._ignore_pinch = True
                self._phase = "frozen"
                out.append(Command("ring_cancel", t))
            return out

        if self._phase == "grab":
            if not tracked:
                if self._lost_since is None:
                    self._lost_since = t
                if (t - self._lost_since) >= RELEASE_HOLD - _EPS:
                    self._lost_since = None
                    self._release_since = None
                    self._phase = "frozen"
                    out.append(Command("grab_end", t))
                return out
            self._lost_since = None

            if pinch_released:
                if self._release_since is None:
                    self._release_since = t
                if (t - self._release_since) >= RELEASE_HOLD - _EPS:
                    self._release_since = None
                    self._phase = "frozen"
                    out.append(Command("grab_end", t))
                    return out
            else:
                self._release_since = None

            assert pc is not None
            nx = self._fx(pc.x, t)
            ny = self._fy(pc.y, t)
            dx = (nx - self._last_fx) * self._scale_x if self._last_fx is not None else 0.0
            dy = (ny - self._last_fy) * self._scale_y if self._last_fy is not None else 0.0
            self._last_fx = nx
            self._last_fy = ny
            out.append(Command("grab_move", t, dx=dx, dy=dy))
            return out

        if self._phase == "scroll":
            if tracked and pose == "victory":
                assert pc is not None
                self._not_v_since = None
                self._scroll_acc += pc.y - self._scroll_y
                self._scroll_y = pc.y
                while self._scroll_acc <= -SCROLL_STEP + _EPS:
                    self._scroll_acc += SCROLL_STEP
                    out.append(Command("scroll", t, steps=1))
                while self._scroll_acc >= SCROLL_STEP - _EPS:
                    self._scroll_acc -= SCROLL_STEP
                    out.append(Command("scroll", t, steps=-1))
            else:
                if self._not_v_since is None:
                    self._not_v_since = t
                if (t - self._not_v_since) >= _SCROLL_EXIT - _EPS:
                    self._not_v_since = None
                    self._phase = "frozen"
            return out

        return out
