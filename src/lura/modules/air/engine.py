"""Pure Air interaction engine: hand landmarks and timestamps in, discrete Commands out.

v4 (Nov 2026): a hand mouse, not a gesture language.

- index finger up (others folded) -> the real cursor follows the index knuckle (INDEX_MCP), not the
  fingertip, so clicking does not shake it (relative, like a mouse; fold the finger to "lift the
  mouse" and move back). Pointer acceleration: slow hand = small precise moves, fast = far.
- a quick flick of the pointing finger up or down -> scroll, like flicking a phone: the page follows
  the finger (up -> the page goes up, you read further down) and keeps gliding, slowing down by
  itself (a faster flick glides further, flicks in a row add up). A fist stops the glide. The cursor
  movement of the flick is undone and the cursor stays still while the finger comes back.
- "finger gun": pointing with the thumb up, bring the thumb down onto the side of the middle finger
  and up again -> left click (twice -> double click). Keep it down (THUMB_DRAG_HOLD) -> mouse button
  down; move -> drag / draw; thumb up -> button up. The index finger never moves for a click.
- the v3 pinch (thumb tip to index tip) still works the same way: quick -> click, hold DRAG_HOLD ->
  drag. Dragging a title bar moves the window, so there is no separate "grab".
- V (index + middle up) with the same thumb tap -> right click.
- two fingers (index + middle, V) moved up/down -> scroll, like a phone: the page follows the hand
  (hand up -> the page moves up, i.e. you read further down). SCROLL_NATURAL = False flips it.
- the edge of the hand (a "blade": hand turned sideways, fingers straight) swept left/right ->
  the workspaces slide with it, like moving screens by hand: sweep right -> the previous one comes
  in from the left, sweep left -> the next one. A flat palm or a fist moving sideways does nothing.
- thumbs up / down -> confirm / deny Lura's question.

Starting anything needs the hand close to the camera (ZONE_PALM) so typing hands (palm 0.11-0.14
in the recordings) never count.
"""

from __future__ import annotations

import math
from collections import deque
from collections.abc import Sequence
from dataclasses import dataclass

from .domain import (
    INDEX_MCP,
    INDEX_PIP,
    INDEX_TIP,
    MIDDLE_MCP,
    PINKY_MCP,
    THUMB_TIP,
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

ZONE_PALM = 0.20          # palm size (wrist->middle MCP, norm.) needed to start anything
EDGE = 0.02               # every landmark at least this far inside the frame to start
THUMB_PALM = 0.12         # thumbs up/down are held lower/farther
TRACK_PALM = 0.12         # once running, a hand this big keeps it going (no edge test)
DISENGAGE_AFTER = 3.0     # s without a tracked hand -> Air off (HUD only; nothing needs re-arming)
POINT_ENTER = 0.15        # s of index-only pose before the cursor follows
POINT_GRACE = 0.6         # s after pointing in which a pinch still counts (the thumb moves the pose)
PINCH_ON, PINCH_OFF = 0.28, 0.40
PINCH_TIGHT, PINCH_SLIP = 0.15, 0.24
PINCH_REACH, PINCH_REACH_END = 0.80, 0.70
RELEASE_HOLD = 0.08       # s the release must hold (one flickering frame is not a release)
DRAG_HOLD = 0.70          # s of pinch before it becomes mouse-down (shorter = click); 0.4 dragged by mistake
REWIND = 0.12             # s of cursor movement undone at pinch start (pinching moves the finger)
SCROLL_ENTER = 0.35       # s of V before scrolling
SCROLL_EXIT = 0.25        # s without V to stop scrolling
SCROLL_STEP = 0.035       # normalized vertical travel per wheel step
SCROLL_NATURAL = True     # like a phone: the page follows the hand (hand up -> page moves up = wheel down)
SWIPE_WINDOW = 0.50       # s the hand has to cover SWIPE_DIST in
SWIPE_DIST = 0.16         # normalized horizontal travel of the palm centre
SWIPE_AXIS = 2.0          # horizontal must be >= this x vertical
SWIPE_PALM = 0.16         # a fast-moving hand blurs smaller; allow a bit less than ZONE_PALM
SWIPE_MOUSE_SHARE = 0.4   # more than this share of point/V frames = using the mouse, not a swipe
SWIPE_COOLDOWN = 1.0      # s before any next swipe
SWIPE_RETURN = 1.6        # s in which the opposite swipe (bringing the hand back) is ignored
SWIPE_SETTLE = 0.25       # s the hand must be slow after a swipe before the next one (no wave bursts)
SWIPE_SETTLE_SPEED = 0.5  # normalized palm units / s that count as "slow"
SWIPE_NATURAL = True      # the workspaces follow the hand: sweep right -> previous, left -> next
BLADE_WIDTH = 0.38        # knuckle width / palm length (aspect-corrected) below this = hand edge-on
                          # (recordings: a hand facing the camera is 0.55-0.75)
BLADE_SHARE = 0.6         # share of the swipe window's frames that must show the blade
ASPECT = 16.0 / 9.0       # camera frame width / height (x and y are normalized separately)
FLICK_WINDOW = 0.20       # s the fingertip has to cover FLICK_DIST in
FLICK_DIST = 0.12         # normalized vertical fingertip travel for a flick (a quick mouse move up is less)
FLICK_AXIS = 2.0          # vertical must be >= this x horizontal
FLICK_STEP = 0.02         # wheel steps per this much travel
FLICK_MIN_STEPS, FLICK_MAX_STEPS = 3, 15
FLICK_FREEZE = 0.45       # s the cursor stays still after a flick (the finger coming back)
FLICK_RETURN = 0.8        # s in which an opposite flick (the finger coming back) is ignored
FLICK_PINCH_BLOCK = 0.5   # s after a flick in which a pinch does not count (the finger curls)
GLIDE_PER_STEP = 2.5      # glide speed (wheel steps / s) added per step of a flick
GLIDE_TAU = 0.35          # s: glide speed decays as exp(-t / GLIDE_TAU) (total glide ~ n x 0.9)
GLIDE_STOP = 1.0          # wheel steps / s below which the glide ends
THUMB_ARM_OUT = 0.65      # thumb tip -> index MCP / palm above this = thumb up ("gun" cocked)
THUMB_ARM = 0.20          # s the thumb must be up while pointing before a tap counts
THUMB_ARM_GRACE = 0.6     # s after the thumb was up in which bringing it down still counts
THUMB_IN = 0.50           # thumb tip -> index MCP / palm below this = thumb down (pressed)
THUMB_UP_OUT = 0.60       # ... above this again = thumb lifted (hysteresis)
THUMB_DRAG_HOLD = 0.35    # s the thumb stays down before it becomes mouse-down (shorter = click)
THUMB_STILL = 0.15        # knuckle speed (normalized units / s) above which a thumb tap is ignored:
                          # a real tap happens with the hand still, a relaxed thumb drifts while moving
THUMB_HOLD = 0.5
THUMB_REPEAT = 3.0        # s: the same thumbs answer again within this counts once
HINT_AFTER, HINT_EVERY = 1.5, 10.0
CURSOR_GAIN = 1.5         # full-frame-width finger move = 1920 x CURSOR_GAIN screen px
ACCEL_SLOW, ACCEL_FAST = 0.10, 0.80   # hand speed (normalized units / s) of the two gain ends
GAIN_SLOW, GAIN_FAST = 0.6, 1.6       # gain multiplier at / below ACCEL_SLOW and at / above ACCEL_FAST
EURO_MIN_CUTOFF, EURO_BETA = 0.6, 0.01
TWO_PALM = 0.14           # both hands at least this big to start/keep a two-hand pinch (two hands
                          # held apart sit a little farther from the camera than one)
TWO_ENTER = 0.12          # s both hands must be pinching before the window is taken
TWO_DRAG_HOLD = 1.2       # s: while a second hand is up, a one-hand pinch waits this long before
                          # mouse-down (instead of DRAG_HOLD), so the second pinch can still arrive
TWO_DEADZONE = 0.04       # |scale - 1| below this = 1.0 (moving both hands must not resize)
TWO_SCALE_MIN, TWO_SCALE_MAX = 0.25, 4.0
TWO_COOLDOWN = 0.6        # s after a two-hand pinch ends in which no one-hand pinch/click starts
TWO_EURO_MIN_CUTOFF, TWO_EURO_BETA = 1.0, 0.02   # filter on the distance between the two pinches

_EPS = 1e-9
_HINT_MIN_PALM = 0.08
_CURSOR_HIST_WINDOW = 0.3
_MOUSE_POSES = ("point", "victory", "pinch")
_FAR_HINT_TEXT = "Move your hand closer to the camera."


@dataclass(frozen=True)
class Command:
    kind: str     # "engage" "disengage" "cursor" "click" "right_click" "press" "release" "swipe" "scroll"
                  # "intent" "hint" "two_start" "two_move" "two_end"
                  # (the controller still accepts the v2 ring_* / grab_* kinds)
    t: float
    dx: float = 0.0
    dy: float = 0.0     # cursor / two_move: screen px (relative)
    direction: str = ""                  # swipe: "left" | "right"
    steps: int = 0                       # scroll: + = up, - = down
    text: str = ""                       # intent: "confirm"/"deny"; hint: the sentence; scroll: "glide"
    progress: float = 0.0


def pinch_point(hand: Hand) -> Point:
    """Midpoint of the thumb tip and the index tip (where the fingers meet)."""
    a, b = hand.points[THUMB_TIP], hand.points[INDEX_TIP]
    return Point((a.x + b.x) * 0.5, (a.y + b.y) * 0.5, (a.z + b.z) * 0.5)


def _two_geom(pair: tuple[Hand, Hand]) -> tuple[float, tuple[float, float]]:
    p0, p1 = pinch_point(pair[0]), pinch_point(pair[1])
    d = math.hypot((p0.x - p1.x) * ASPECT, p0.y - p1.y)
    return d, ((p0.x + p1.x) * 0.5, (p0.y + p1.y) * 0.5)


def blade_ratio(hand: Hand) -> float:
    """Knuckle width / palm length with x corrected for the frame's aspect; small = hand edge-on."""
    def d(a: int, b: int) -> float:
        pa, pb = hand.points[a], hand.points[b]
        return math.hypot((pa.x - pb.x) * ASPECT, pa.y - pb.y)
    return d(INDEX_MCP, PINKY_MCP) / max(d(WRIST, MIDDLE_MCP), 1e-6)


def accel_gain(speed: float) -> float:
    """Pointer acceleration: the gain multiplier for a hand moving at ``speed`` (normalized units/s)."""
    if speed <= ACCEL_SLOW:
        return GAIN_SLOW
    if speed >= ACCEL_FAST:
        return GAIN_FAST
    return GAIN_SLOW + (speed - ACCEL_SLOW) / (ACCEL_FAST - ACCEL_SLOW) * (GAIN_FAST - GAIN_SLOW)


def thumb_reach(hand: Hand) -> float:
    """Thumb tip to index knuckle / palm size: large = thumb up, small = thumb down on the fingers."""
    return dist(hand.points[THUMB_TIP], hand.points[INDEX_MCP]) / palm_size(hand)


def _in_frame(hand: Hand) -> bool:
    return all(EDGE <= p.x <= 1.0 - EDGE and EDGE <= p.y <= 1.0 - EDGE for p in hand.points)


class InteractionEngine:
    """Pure stateful hand-mouse machine: point, pinch click/drag, V scroll, open-hand swipe, thumbs."""

    def __init__(self, screen_px_per_frame: float = 1920.0 * CURSOR_GAIN, *, thumb_click: bool = True) -> None:
        #: Finger-gun clicks (thumb tap) on; off = only the v3 pinch clicks (settings air_thumb_click).
        self.thumb_click = thumb_click
        self._scale_x = float(screen_px_per_frame)
        self._scale_y = float(screen_px_per_frame) * (9.0 / 16.0)
        self._fx = OneEuro(min_cutoff=EURO_MIN_CUTOFF, beta=EURO_BETA)
        self._fy = OneEuro(min_cutoff=EURO_MIN_CUTOFF, beta=EURO_BETA)
        self._two_filter = OneEuro(min_cutoff=TWO_EURO_MIN_CUTOFF, beta=TWO_EURO_BETA)
        self.reset()

    def reset(self) -> None:
        self._engaged = False
        self._phase: str = "idle"          # idle | point | pinch | drag | scroll | two
        self._zone: str = "none"
        self._progress: float = 0.0

        self._last_ext: dict[str, bool] | None = None
        self._last_tracked_t: float = -math.inf
        self._far_since: float | None = None
        self._last_hint_t: float = -math.inf

        self._point_since: float | None = None
        self._last_point_t: float = -math.inf
        self._fx.reset()
        self._fy.reset()
        self._last_fx: float | None = None
        self._last_fy: float | None = None
        self._last_ft: float = 0.0
        self._acc_x = 0.0
        self._acc_y = 0.0
        self._cursor_hist: deque[tuple[float, float, float]] = deque()

        self._pinch_start: float = 0.0
        self._press_src: str = "pinch"     # what started the pinch phase: "pinch" | "thumb"
        self._thumb_out_since: float | None = None
        self._thumb_armed_t: float = -math.inf
        self._thumb_down: bool = False
        self._thumb_tap_t: float = -math.inf
        self._still_prev: tuple[float, float, float] | None = None
        self._still_speed: float = 0.0
        self._glide_v: float = 0.0         # wheel steps / s, sign = direction
        self._glide_acc: float = 0.0
        self._glide_t: float = 0.0
        self._release_since: float | None = None
        self._lost_since: float | None = None

        self._v_since: float | None = None
        self._not_v_since: float | None = None
        self._scroll_y = 0.0
        self._scroll_acc = 0.0

        self._swipe_hist: deque[tuple[float, float, float, bool, bool]] = deque()
        self._tip_hist: deque[tuple[float, float, float, bool]] = deque()
        self._flick_until: float = -math.inf
        self._last_flick: tuple[str, float] = ("", -math.inf)
        self._last_swipe: tuple[str, float] = ("", -math.inf)
        self._swipe_armed = True
        self._slow_since: float | None = None
        self._prev_pc: tuple[float, float, float] | None = None

        self._thumb_pose: str = ""
        self._thumb_since: float = 0.0
        self._thumb_fired: bool = False
        self._thumb_last: tuple[str, float] = ("", -math.inf)

        self._two_since: float | None = None
        self._two_d0: float = 0.0
        self._two_mid: tuple[float, float] | None = None
        self._two_scale: float = 1.0
        self._two_emitted_scale: float = 1.0
        self._two_release_since: float | None = None
        self._two_block_until: float = -math.inf
        self._two_filter.reset()

    def _clear_two(self) -> None:
        self._two_since = None
        self._two_d0 = 0.0
        self._two_mid = None
        self._two_scale = 1.0
        self._two_emitted_scale = 1.0
        self._two_release_since = None
        self._two_filter.reset()

    @property
    def state(self) -> dict:
        return {
            "engaged": self._engaged,
            "zone": self._zone,
            "phase": self._phase,
            "ring_hover": "",
            "grab_progress": self._progress,
        }

    # -- helpers -------------------------------------------------------------

    def _engage(self, t: float, out: list[Command]) -> None:
        if not self._engaged:
            self._engaged = True
            out.append(Command("engage", t))

    def _seed(self, p: Point, t: float) -> None:
        self._fx.reset()
        self._fy.reset()
        self._last_fx = self._fx(p.x, t)
        self._last_fy = self._fy(p.y, t)
        self._last_ft = t
        self._acc_x = 0.0
        self._acc_y = 0.0

    def _follow(self, p: Point, t: float, out: list[Command]) -> None:
        """Move the cursor by the filtered movement of ``p`` since the last frame."""
        nx = self._fx(p.x, t)
        ny = self._fy(p.y, t)
        if self._last_fx is not None and self._last_fy is not None:
            dt = t - self._last_ft
            speed = math.hypot(nx - self._last_fx, ny - self._last_fy) / dt if dt > _EPS else 0.0
            gain = accel_gain(speed)
            self._acc_x += (nx - self._last_fx) * self._scale_x * gain
            self._acc_y += (ny - self._last_fy) * self._scale_y * gain
            if abs(self._acc_x) >= 1.0 or abs(self._acc_y) >= 1.0:
                ex = float(math.trunc(self._acc_x))
                ey = float(math.trunc(self._acc_y))
                self._acc_x -= ex
                self._acc_y -= ey
                self._cursor_hist.append((t, ex, ey))
                while self._cursor_hist and (t - self._cursor_hist[0][0]) > _CURSOR_HIST_WINDOW + _EPS:
                    self._cursor_hist.popleft()
                out.append(Command("cursor", t, dx=ex, dy=ey))
        self._last_fx = nx
        self._last_fy = ny
        self._last_ft = t

    def _rewind(self, t: float, out: list[Command]) -> None:
        rx = sum(dx for ct, dx, _ in self._cursor_hist if (t - ct) <= REWIND + _EPS)
        ry = sum(dy for ct, _, dy in self._cursor_hist if (t - ct) <= REWIND + _EPS)
        if rx or ry:
            out.append(Command("cursor", t, dx=-rx, dy=-ry))
        self._cursor_hist.clear()

    def _to_idle(self) -> None:
        self._phase = "idle"
        self._point_since = None
        self._release_since = None
        self._lost_since = None
        self._v_since = None
        self._not_v_since = None
        self._progress = 0.0
        self._last_fx = None
        self._last_fy = None

    def _stop_glide(self) -> None:
        self._glide_v = 0.0
        self._glide_acc = 0.0

    def _update_glide(self, t: float, pose: str, out: list[Command]) -> None:
        """Phone-like momentum after a flick: keep scrolling, slowing down; a fist stops it."""
        if self._glide_v == 0.0:
            return
        if pose == "fist" or self._phase in ("pinch", "drag", "scroll"):
            self._stop_glide()
            return
        dt = max(0.0, t - self._glide_t)
        self._glide_t = t
        self._glide_acc += self._glide_v * dt
        self._glide_v *= math.exp(-dt / GLIDE_TAU)
        n = math.trunc(self._glide_acc)
        if n:
            self._glide_acc -= n
            out.append(Command("scroll", t, steps=n, text="glide"))
        if abs(self._glide_v) < GLIDE_STOP:
            self._stop_glide()

    def _update_thumb_tap(self, t: float, hand: Hand | None, pose: str, ratio: float) -> bool:
        """Finger-gun trigger. True on the frame the cocked thumb comes down onto the fingers."""
        if hand is None or not self.thumb_click:
            self._thumb_out_since = None
            self._thumb_down = False
            self._still_prev = None
            return False
        # Knuckle speed, smoothed over a few frames (MediaPipe jitters frame to frame).
        k = hand.points[INDEX_MCP]
        if self._still_prev is not None and t > self._still_prev[0]:
            v = math.hypot(k.x - self._still_prev[1], k.y - self._still_prev[2]) / (t - self._still_prev[0])
            self._still_speed = 0.5 * self._still_speed + 0.5 * v
        self._still_prev = (t, k.x, k.y)
        reach = thumb_reach(hand)
        gun = pose in ("point", "victory")
        # Cocked: thumb up and away from the index tip (a v3 pinch is not a cocked thumb).
        if gun and reach > THUMB_ARM_OUT and ratio > PINCH_OFF:
            if self._thumb_out_since is None:
                self._thumb_out_since = t
            # Right after a tap the thumb re-arms at once, so two taps make a double click.
            wait = 0.0 if (t - self._thumb_tap_t) <= THUMB_ARM_GRACE + _EPS else THUMB_ARM
            if (t - self._thumb_out_since) >= wait - _EPS:
                self._thumb_armed_t = t
        else:
            self._thumb_out_since = None
        if self._thumb_down:
            if reach > THUMB_UP_OUT:
                self._thumb_down = False
            return False
        if (gun and reach < THUMB_IN and (t - self._thumb_armed_t) <= THUMB_ARM_GRACE + _EPS
                and self._still_speed <= THUMB_STILL + _EPS):
            self._thumb_down = True
            self._thumb_armed_t = -math.inf
            self._thumb_tap_t = t
            return True
        return False

    def _update_thumbs(self, t: float, hand: Hand | None, palm: float, pose: str, out: list[Command]) -> None:
        if self._phase in ("pinch", "drag"):
            self._thumb_pose, self._thumb_fired = "", False
            return
        if hand is not None and palm >= THUMB_PALM - _EPS and pose in ("thumbs_up", "thumbs_down"):
            if pose != self._thumb_pose:
                self._thumb_pose, self._thumb_since, self._thumb_fired = pose, t, False
            elif not self._thumb_fired and (t - self._thumb_since) >= THUMB_HOLD - _EPS:
                self._thumb_fired = True
                intent = "confirm" if pose == "thumbs_up" else "deny"
                # A held thumbs up flickers through "fist" and re-arms: the same answer within
                # THUMB_REPEAT counts once.
                last_intent, last_t = self._thumb_last
                if intent != last_intent or (t - last_t) >= THUMB_REPEAT - _EPS:
                    self._thumb_last = (intent, t)
                    out.append(Command("intent", t, text=intent))
        else:
            self._thumb_pose, self._thumb_fired = "", False

    def _update_swipe(self, t: float, hand: Hand | None, palm: float, pc: Point | None, pose: str,
                      blade: bool, out: list[Command]) -> bool:
        """Hand edge swept left/right. Returns True when a swipe fired this frame."""
        if (self._phase in ("pinch", "drag", "scroll") or hand is None or pc is None
                or palm < SWIPE_PALM - _EPS):
            self._swipe_hist.clear()
            self._prev_pc = None
            if hand is None:
                self._swipe_armed = True      # hand out of view and back = a fresh start
            return False
        mouse = pose in _MOUSE_POSES
        # Re-arm only after the hand has been slow for SWIPE_SETTLE: waving back and forth is one swipe.
        if self._prev_pc is not None and t > self._prev_pc[0]:
            speed = math.hypot(pc.x - self._prev_pc[1], pc.y - self._prev_pc[2]) / (t - self._prev_pc[0])
            if speed < SWIPE_SETTLE_SPEED:
                if self._slow_since is None:
                    self._slow_since = t
                if (t - self._slow_since) >= SWIPE_SETTLE - _EPS:
                    self._swipe_armed = True
            else:
                self._slow_since = None
        self._prev_pc = (t, pc.x, pc.y)
        self._swipe_hist.append((t, pc.x, pc.y, mouse, blade))
        while self._swipe_hist and (t - self._swipe_hist[0][0]) > SWIPE_WINDOW + _EPS:
            self._swipe_hist.popleft()
        if len(self._swipe_hist) < 3 or mouse:
            return False
        mouse_frames = sum(1 for *_, m, _b in self._swipe_hist if m)
        if mouse_frames > SWIPE_MOUSE_SHARE * len(self._swipe_hist):
            return False
        blade_frames = sum(1 for *_, b in self._swipe_hist if b)
        if blade_frames < BLADE_SHARE * len(self._swipe_hist) - _EPS:
            return False
        # Travel from the extreme point in the window (a slow start doesn't eat the distance).
        xs = [h[1] for h in self._swipe_hist]
        ys = [h[2] for h in self._swipe_hist]
        dx_right = pc.x - min(xs)
        dx_left = max(xs) - pc.x
        dy = max(ys) - min(ys)
        direction, travel = ("right", dx_right) if dx_right >= dx_left else ("left", dx_left)
        if travel < SWIPE_DIST - _EPS or travel < SWIPE_AXIS * dy - _EPS:
            return False
        last_dir, last_t = self._last_swipe
        if not self._swipe_armed or (t - last_t) < SWIPE_COOLDOWN - _EPS:
            return False
        if last_dir and direction != last_dir and (t - last_t) < SWIPE_RETURN - _EPS:
            return False
        self._last_swipe = (direction, t)
        self._swipe_armed = False
        self._slow_since = None
        self._swipe_hist.clear()
        self._engage(t, out)
        # The controller maps "right" -> next workspace. Natural: the workspaces follow the hand, so a
        # sweep to the right brings in the previous one.
        target = {"right": "left", "left": "right"}[direction] if SWIPE_NATURAL else direction
        out.append(Command("swipe", t, direction=target))
        return True

    def _update_flick(self, t: float, tip: Point | None, recently_pointing: bool, ratio: float,
                      pose: str, out: list[Command]) -> bool:
        """Quick vertical flick of the pointing finger -> scroll. True when one fired this frame."""
        if tip is None or not recently_pointing or self._phase not in ("idle", "point"):
            self._tip_hist.clear()
            return False
        if ratio < PINCH_OFF - _EPS:
            # Thumb near the fingertip: a pinch on its way, never a flick (the recordings' pinches
            # moved the hand up and down).
            self._tip_hist.clear()
            return False
        if pose != "point":
            # Only a clear pointing finger counts: a curled finger straightening out of a fist or a
            # pinch jumps the fingertip up and is not a flick.
            return False
        self._tip_hist.append((t, tip.x, tip.y, True))
        while self._tip_hist and (t - self._tip_hist[0][0]) > FLICK_WINDOW + _EPS:
            self._tip_hist.popleft()
        if len(self._tip_hist) < 2 or t < self._flick_until:
            return False
        # Mostly a clear pointing finger (a fast finger blurs, so not every frame).
        if sum(1 for *_, pt in self._tip_hist if pt) < 0.6 * len(self._tip_hist) - _EPS:
            return False
        ys = [h[2] for h in self._tip_hist]
        xs = [h[1] for h in self._tip_hist]
        up, down = max(ys) - tip.y, tip.y - min(ys)
        direction, travel = ("up", up) if up >= down else ("down", down)
        if travel < FLICK_DIST - _EPS or travel < FLICK_AXIS * (max(xs) - min(xs)) - _EPS:
            return False
        last_dir, last_t = self._last_flick
        if last_dir and direction != last_dir and (t - last_t) < FLICK_RETURN - _EPS:
            return False
        # Undo what the flick moved the cursor, then scroll.
        rx = sum(dx for ct, dx, _ in self._cursor_hist if (t - ct) <= FLICK_WINDOW + 0.05 + _EPS)
        ry = sum(dy for ct, _, dy in self._cursor_hist if (t - ct) <= FLICK_WINDOW + 0.05 + _EPS)
        if rx or ry:
            out.append(Command("cursor", t, dx=-rx, dy=-ry))
        self._cursor_hist.clear()
        n = max(FLICK_MIN_STEPS, min(FLICK_MAX_STEPS, round(travel / FLICK_STEP)))
        up_steps = -n if SCROLL_NATURAL else n          # finger up -> page up -> wheel down
        steps = up_steps if direction == "up" else -up_steps
        out.append(Command("scroll", t, steps=steps))
        # Momentum: a flick the same way adds to the glide, the other way replaces it.
        v = steps * GLIDE_PER_STEP
        if self._glide_v and (self._glide_v > 0) == (v > 0):
            v += self._glide_v
        self._glide_v, self._glide_t = v, t
        self._engage(t, out)
        self._last_flick = (direction, t)
        self._flick_until = t + FLICK_FREEZE
        self._tip_hist.clear()
        self._last_fx = None
        self._last_fy = None
        return True

    def _pinch_flags(self, hand: Hand) -> tuple[bool, bool, float]:
        """(pinch_on, pinch_released, ratio) with the exact rules already in update()."""
        ratio = pinch_ratio(hand)
        reach = dist(hand.points[WRIST], hand.points[INDEX_TIP]) / max(
            dist(hand.points[WRIST], hand.points[INDEX_PIP]), 1e-6)
        pinch_on = (ratio < PINCH_ON and reach > PINCH_REACH) or ratio < PINCH_TIGHT
        pinch_released = ratio > PINCH_OFF or (reach < PINCH_REACH_END and ratio > PINCH_SLIP)
        return pinch_on, pinch_released, ratio

    # -- main ----------------------------------------------------------------

    def update(self, t: float, hands: Sequence[Hand]) -> list[Command]:
        out: list[Command] = []

        big = [h for h in hands if palm_size(h) >= TWO_PALM - _EPS]
        pair: tuple[Hand, Hand] | None = None
        if len(big) >= 2:
            big.sort(key=lambda h: h.score, reverse=True)
            pair = (big[0], big[1])

        if self._phase == "two":
            any_tracked = any(palm_size(h) >= TRACK_PALM - _EPS for h in hands)
            if any_tracked:
                self._last_tracked_t = t
            if pair is not None:
                self._zone = "ok"
                _, p0_rel, _ = self._pinch_flags(pair[0])
                _, p1_rel, _ = self._pinch_flags(pair[1])
                releasing = p0_rel or p1_rel
            else:
                self._zone = "none"
                releasing = True
            if releasing:
                if (
                    not any_tracked
                    and self._engaged
                    and (t - self._last_tracked_t) >= DISENGAGE_AFTER - _EPS
                ):
                    out.append(Command("two_end", t))
                    self._clear_two()
                    self._to_idle()
                    self._two_block_until = t + TWO_COOLDOWN
                    self._last_point_t = -math.inf
                    self._engaged = False
                    out.append(Command("disengage", t))
                    return out
                if self._two_release_since is None:
                    self._two_release_since = t
                if (t - self._two_release_since) >= RELEASE_HOLD - _EPS:
                    out.append(Command("two_end", t))
                    self._clear_two()
                    self._to_idle()
                    self._two_block_until = t + TWO_COOLDOWN
                    self._last_point_t = -math.inf
                return out
            self._two_release_since = None
            assert pair is not None and self._two_mid is not None
            raw_d, mid = _two_geom(pair)
            d = self._two_filter(raw_d, t)
            scale = d / max(self._two_d0, 1e-6)
            if abs(scale - 1.0) < TWO_DEADZONE:
                scale = 1.0
            scale = max(TWO_SCALE_MIN, min(TWO_SCALE_MAX, scale))
            dx = (mid[0] - self._two_mid[0]) * self._scale_x
            dy = (mid[1] - self._two_mid[1]) * self._scale_y
            self._two_scale = scale
            self._progress = scale
            if (
                abs(dx) >= 0.5
                or abs(dy) >= 0.5
                or abs(scale - self._two_emitted_scale) >= 0.01 - _EPS
            ):
                self._two_mid = mid
                self._two_emitted_scale = scale
                out.append(Command("two_move", t, dx=dx, dy=dy, progress=scale))
            return out

        if (
            self._phase in ("idle", "point", "pinch", "drag")
            and pair is not None
            and _in_frame(pair[0])
            and _in_frame(pair[1])
            and self._pinch_flags(pair[0])[0]
            and self._pinch_flags(pair[1])[0]
            and t >= self._two_block_until - _EPS
        ):
            if self._two_since is None:
                self._two_since = t
            if (t - self._two_since) >= TWO_ENTER - _EPS:
                if self._phase == "drag":
                    out.append(Command("release", t))
                self._stop_glide()
                self._engage(t, out)
                self._to_idle()
                self._phase = "two"
                self._zone = "ok"
                self._last_tracked_t = t
                self._two_since = None
                self._two_release_since = None
                self._two_filter.reset()
                raw_d, mid = _two_geom(pair)
                self._two_d0 = self._two_filter(raw_d, t)
                self._two_mid = mid
                self._two_scale = 1.0
                self._two_emitted_scale = 1.0
                self._progress = 1.0
                out.append(Command("two_start", t))
                return out
        else:
            self._two_since = None

        hand = max(hands, key=lambda h: h.score) if hands else None

        if hand is not None:
            palm = palm_size(hand)
            pc = palm_center(hand)
            tip = hand.points[INDEX_TIP]
            in_zone = palm >= ZONE_PALM - _EPS and _in_frame(hand)
            tracked = palm >= TRACK_PALM - _EPS
            pinch_on, pinch_released, ratio = self._pinch_flags(hand)
            pose, _ = classify_pose(hand, self._phase in ("pinch", "drag"), self._last_ext)
            ext = extended(hand, self._last_ext)
            self._last_ext = ext
            blade = blade_ratio(hand) < BLADE_WIDTH - _EPS and sum(1 for v in ext.values() if v) >= 3
            self._zone = "ok" if in_zone else ("far" if palm < ZONE_PALM - _EPS else "none")
        else:
            palm, pc, tip = 0.0, None, None
            in_zone = tracked = pinch_on = False
            pinch_released = True
            pose = "none"
            blade = False
            ratio = 1.0
            self._last_ext = None
            self._zone = "none"

        if tracked:
            self._last_tracked_t = t

        thumb_tap = self._update_thumb_tap(t, hand, pose, ratio)
        if pinch_on:
            self._thumb_armed_t = -math.inf      # a v3 pinch is never the start of a thumb tap
        self._update_glide(t, pose, out)
        self._update_thumbs(t, hand, palm, pose, out)
        if self._update_swipe(t, hand, palm, pc, pose, blade, out):
            if self._phase == "point":
                self._to_idle()
            return out

        # Far-hand hint: a gesture pose shown too far from the camera.
        if (self._phase == "idle" and hand is not None and pose in ("point", "open_palm", "victory")
                and _HINT_MIN_PALM - _EPS <= palm < ZONE_PALM - _EPS):
            if self._far_since is None:
                self._far_since = t
            elif ((t - self._far_since) >= HINT_AFTER - _EPS
                  and (t - self._last_hint_t) >= HINT_EVERY - _EPS):
                self._last_hint_t = t
                self._far_since = t
                out.append(Command("hint", t, text=_FAR_HINT_TEXT))
        else:
            self._far_since = None

        # Lost hand: end what is running; after DISENGAGE_AFTER switch the HUD off.
        if not tracked:
            if self._phase in ("pinch", "drag"):
                if self._lost_since is None:
                    self._lost_since = t
                if (t - self._lost_since) >= RELEASE_HOLD - _EPS:
                    if self._phase == "drag":
                        out.append(Command("release", t))
                    self._to_idle()
            elif self._phase != "idle":
                self._to_idle()
            if self._engaged and (t - self._last_tracked_t) >= DISENGAGE_AFTER - _EPS:
                if self._phase == "drag":
                    out.append(Command("release", t))
                elif self._phase == "two":
                    out.append(Command("two_end", t))
                    self._clear_two()
                self._to_idle()
                self._engaged = False
                out.append(Command("disengage", t))
            return out
        self._lost_since = None
        assert pc is not None and tip is not None and hand is not None
        knuckle = hand.points[INDEX_MCP]          # steadier than the tip: clicking moves the tip

        if self._phase in ("idle", "point"):
            recently_pointing = self._phase == "point" or (t - self._last_point_t) <= POINT_GRACE + _EPS
            if self._update_flick(t, tip, recently_pointing and pose != "pinch" and not pinch_on, ratio, pose, out):
                return out
            flicked_lately = (t - self._last_flick[1]) < FLICK_PINCH_BLOCK - _EPS
            if thumb_tap and pose == "victory" and in_zone and t >= self._two_block_until - _EPS:
                self._engage(t, out)
                out.append(Command("right_click", t))
                return out
            gun_tap = thumb_tap and pose == "point" and recently_pointing
            if (
                (pinch_on or gun_tap)
                and recently_pointing
                and not flicked_lately
                and t >= self._two_block_until - _EPS
            ):
                self._stop_glide()
                self._rewind(t, out)
                self._phase = "pinch"
                self._press_src = "thumb" if gun_tap and not pinch_on else "pinch"
                self._pinch_start = t
                self._release_since = None
                self._progress = 0.0
                return out

            if pose == "victory" and (in_zone or self._phase == "point"):
                if self._v_since is None:
                    self._v_since = t
                if (t - self._v_since) >= SCROLL_ENTER - _EPS:
                    self._engage(t, out)
                    self._phase = "scroll"
                    self._scroll_y = pc.y
                    self._scroll_acc = 0.0
                    self._not_v_since = None
                    self._v_since = None
                return out
            self._v_since = None

            if pose == "point" and (in_zone or self._phase == "point"):
                if self._phase == "idle":
                    if self._point_since is None:
                        self._point_since = t
                    if (t - self._point_since) >= POINT_ENTER - _EPS:
                        self._engage(t, out)
                        self._phase = "point"
                        self._cursor_hist.clear()
                        self._seed(knuckle, t)
                elif t < self._flick_until:
                    pass                          # the finger coming back after a flick: cursor still
                elif self._last_fx is None:
                    self._seed(knuckle, t)        # first frame after a flick
                else:
                    self._follow(knuckle, t, out)
                self._last_point_t = t
                return out

            # Any other pose lifts the "mouse": the cursor stops, the hand can move back.
            self._point_since = None
            if self._phase == "point":
                self._phase = "idle"
                self._last_fx = None
                self._last_fy = None
            return out

        if self._phase in ("pinch", "drag"):
            released = (not self._thumb_down) if self._press_src == "thumb" else pinch_released
            if released:
                if self._release_since is None:
                    self._release_since = t
                if (t - self._release_since) >= RELEASE_HOLD - _EPS:
                    if self._phase == "pinch" and self._two_since is not None:
                        return out
                    out.append(Command("release" if self._phase == "drag" else "click", t))
                    self._to_idle()
                    self._last_point_t = t       # back to pointing: a second tap is a double click
                return out
            self._release_since = None

            if self._phase == "pinch":
                held = t - self._pinch_start
                hold = THUMB_DRAG_HOLD if self._press_src == "thumb" else DRAG_HOLD
                if any(h is not hand and palm_size(h) >= TWO_PALM - _EPS for h in hands):
                    hold = max(hold, TWO_DRAG_HOLD)
                if held >= hold - _EPS:
                    self._engage(t, out)
                    self._phase = "drag"
                    self._progress = 0.0
                    out.append(Command("press", t))
                    self._seed(pc, t)
                    return out
                self._progress = min(1.0, held / hold)
                return out

            # Drag: follow the palm centre (steadier than the pinched fingertips).
            self._follow(pc, t, out)
            return out

        if self._phase == "scroll":
            if thumb_tap and pose == "victory" and t >= self._two_block_until - _EPS:
                out.append(Command("right_click", t))
                return out
            if pose == "victory":
                self._not_v_since = None
                self._scroll_acc += pc.y - self._scroll_y
                self._scroll_y = pc.y
                up = -1 if SCROLL_NATURAL else 1      # wheel step for a hand moving up
                while self._scroll_acc <= -SCROLL_STEP + _EPS:
                    self._scroll_acc += SCROLL_STEP
                    out.append(Command("scroll", t, steps=up))
                while self._scroll_acc >= SCROLL_STEP - _EPS:
                    self._scroll_acc -= SCROLL_STEP
                    out.append(Command("scroll", t, steps=-up))
            else:
                if self._not_v_since is None:
                    self._not_v_since = t
                if (t - self._not_v_since) >= SCROLL_EXIT - _EPS:
                    self._to_idle()
            return out

        return out
