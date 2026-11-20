"""Pure Air interaction engine: hand landmarks and timestamps in, discrete Commands out.

v3 (Mohamed's live test, 20 Nov 2026): a hand mouse, not a gesture language.

- index finger up (others folded) -> the real cursor follows the fingertip (relative, like a mouse;
  fold the finger to "lift the mouse" and move back).
- while pointing, touch thumb to index and let go quickly -> left click (twice -> double click).
  Touch and hold (DRAG_HOLD) -> mouse button down; move -> drag; let go -> button up. Dragging a
  title bar moves the window, so there is no separate "grab".
- two fingers (index + middle, V) moved up/down -> scroll, like a phone: the page follows the hand
  (hand up -> the page moves up, i.e. you read further down). SCROLL_NATURAL = False flips it.
- hand moved left/right (palm or back of the hand, any shape but the mouse ones) -> previous /
  next workspace. No stillness, no engage step.
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
DRAG_HOLD = 0.40          # s of pinch before it becomes mouse-down (shorter = click)
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
SWIPE_COOLDOWN = 0.8      # s before any next swipe
SWIPE_RETURN = 1.0        # s in which the opposite swipe (bringing the hand back) is ignored
THUMB_HOLD = 0.5
THUMB_REPEAT = 3.0        # s: the same thumbs answer again within this counts once
HINT_AFTER, HINT_EVERY = 1.5, 10.0
CURSOR_GAIN = 1.5         # full-frame-width finger move = 1920 x CURSOR_GAIN screen px
EURO_MIN_CUTOFF, EURO_BETA = 0.6, 0.01

_EPS = 1e-9
_HINT_MIN_PALM = 0.08
_CURSOR_HIST_WINDOW = 0.3
_MOUSE_POSES = ("point", "victory", "pinch")
_FAR_HINT_TEXT = "Move your hand closer to the camera."


@dataclass(frozen=True)
class Command:
    kind: str     # "engage" "disengage" "cursor" "click" "press" "release" "swipe" "scroll"
                  # "intent" "hint" (the controller still accepts the v2 ring_* / grab_* kinds)
    t: float
    dx: float = 0.0
    dy: float = 0.0     # cursor: screen px (relative)
    direction: str = ""                  # swipe: "left" | "right"
    steps: int = 0                       # scroll: + = up, - = down
    text: str = ""                       # intent: "confirm"/"deny"; hint: the sentence
    progress: float = 0.0


def _in_frame(hand: Hand) -> bool:
    return all(EDGE <= p.x <= 1.0 - EDGE and EDGE <= p.y <= 1.0 - EDGE for p in hand.points)


class InteractionEngine:
    """Pure stateful hand-mouse machine: point, pinch click/drag, V scroll, open-hand swipe, thumbs."""

    def __init__(self, screen_px_per_frame: float = 1920.0 * CURSOR_GAIN) -> None:
        self._scale_x = float(screen_px_per_frame)
        self._scale_y = float(screen_px_per_frame) * (9.0 / 16.0)
        self._fx = OneEuro(min_cutoff=EURO_MIN_CUTOFF, beta=EURO_BETA)
        self._fy = OneEuro(min_cutoff=EURO_MIN_CUTOFF, beta=EURO_BETA)
        self.reset()

    def reset(self) -> None:
        self._engaged = False
        self._phase: str = "idle"          # idle | point | pinch | drag | scroll
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
        self._acc_x = 0.0
        self._acc_y = 0.0
        self._cursor_hist: deque[tuple[float, float, float]] = deque()

        self._pinch_start: float = 0.0
        self._release_since: float | None = None
        self._lost_since: float | None = None

        self._v_since: float | None = None
        self._not_v_since: float | None = None
        self._scroll_y = 0.0
        self._scroll_acc = 0.0

        self._swipe_hist: deque[tuple[float, float, float, bool]] = deque()
        self._last_swipe: tuple[str, float] = ("", -math.inf)

        self._thumb_pose: str = ""
        self._thumb_since: float = 0.0
        self._thumb_fired: bool = False
        self._thumb_last: tuple[str, float] = ("", -math.inf)

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
        self._acc_x = 0.0
        self._acc_y = 0.0

    def _follow(self, p: Point, t: float, out: list[Command]) -> None:
        """Move the cursor by the filtered movement of ``p`` since the last frame."""
        nx = self._fx(p.x, t)
        ny = self._fy(p.y, t)
        if self._last_fx is not None and self._last_fy is not None:
            self._acc_x += (nx - self._last_fx) * self._scale_x
            self._acc_y += (ny - self._last_fy) * self._scale_y
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
                      out: list[Command]) -> bool:
        """Hand moved left/right (palm or back). Returns True when a swipe fired this frame."""
        if (self._phase in ("pinch", "drag", "scroll") or hand is None or pc is None
                or palm < SWIPE_PALM - _EPS):
            self._swipe_hist.clear()
            return False
        mouse = pose in _MOUSE_POSES
        self._swipe_hist.append((t, pc.x, pc.y, mouse))
        while self._swipe_hist and (t - self._swipe_hist[0][0]) > SWIPE_WINDOW + _EPS:
            self._swipe_hist.popleft()
        if len(self._swipe_hist) < 3 or mouse:
            return False
        mouse_frames = sum(1 for *_, m in self._swipe_hist if m)
        if mouse_frames > SWIPE_MOUSE_SHARE * len(self._swipe_hist):
            return False
        # Travel from the extreme point in the window (a slow start doesn't eat the distance).
        xs = [x for _, x, _, _ in self._swipe_hist]
        ys = [y for _, _, y, _ in self._swipe_hist]
        dx_right = pc.x - min(xs)
        dx_left = max(xs) - pc.x
        dy = max(ys) - min(ys)
        direction, travel = ("right", dx_right) if dx_right >= dx_left else ("left", dx_left)
        if travel < SWIPE_DIST - _EPS or travel < SWIPE_AXIS * dy - _EPS:
            return False
        last_dir, last_t = self._last_swipe
        if (t - last_t) < SWIPE_COOLDOWN - _EPS:
            return False
        if last_dir and direction != last_dir and (t - last_t) < SWIPE_RETURN - _EPS:
            return False
        self._last_swipe = (direction, t)
        self._swipe_hist.clear()
        self._engage(t, out)
        out.append(Command("swipe", t, direction=direction))
        return True

    # -- main ----------------------------------------------------------------

    def update(self, t: float, hands: Sequence[Hand]) -> list[Command]:
        out: list[Command] = []
        hand = max(hands, key=lambda h: h.score) if hands else None

        if hand is not None:
            palm = palm_size(hand)
            pc = palm_center(hand)
            tip = hand.points[INDEX_TIP]
            in_zone = palm >= ZONE_PALM - _EPS and _in_frame(hand)
            tracked = palm >= TRACK_PALM - _EPS
            ratio = pinch_ratio(hand)
            reach = dist(hand.points[WRIST], hand.points[INDEX_TIP]) / max(
                dist(hand.points[WRIST], hand.points[INDEX_PIP]), 1e-6)
            pinch_on = (ratio < PINCH_ON and reach > PINCH_REACH) or ratio < PINCH_TIGHT
            pinch_released = ratio > PINCH_OFF or (reach < PINCH_REACH_END and ratio > PINCH_SLIP)
            pose, _ = classify_pose(hand, self._phase in ("pinch", "drag"), self._last_ext)
            self._last_ext = extended(hand, self._last_ext)
            self._zone = "ok" if in_zone else ("far" if palm < ZONE_PALM - _EPS else "none")
        else:
            palm, pc, tip = 0.0, None, None
            in_zone = tracked = pinch_on = False
            pinch_released = True
            pose = "none"
            self._last_ext = None
            self._zone = "none"

        if tracked:
            self._last_tracked_t = t

        self._update_thumbs(t, hand, palm, pose, out)
        if self._update_swipe(t, hand, palm, pc, pose, out):
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
                self._to_idle()
                self._engaged = False
                out.append(Command("disengage", t))
            return out
        self._lost_since = None
        assert pc is not None and tip is not None

        if self._phase in ("idle", "point"):
            recently_pointing = self._phase == "point" or (t - self._last_point_t) <= POINT_GRACE + _EPS
            if pinch_on and recently_pointing:
                self._rewind(t, out)
                self._phase = "pinch"
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
                        self._seed(tip, t)
                else:
                    self._follow(tip, t, out)
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
            if pinch_released:
                if self._release_since is None:
                    self._release_since = t
                if (t - self._release_since) >= RELEASE_HOLD - _EPS:
                    out.append(Command("release" if self._phase == "drag" else "click", t))
                    self._to_idle()
                    self._last_point_t = t       # back to pointing: a second tap is a double click
                return out
            self._release_since = None

            if self._phase == "pinch":
                held = t - self._pinch_start
                if held >= DRAG_HOLD - _EPS:
                    self._engage(t, out)
                    self._phase = "drag"
                    self._progress = 0.0
                    out.append(Command("press", t))
                    self._seed(pc, t)
                    return out
                self._progress = min(1.0, held / DRAG_HOLD)
                return out

            # Drag: follow the palm centre (steadier than the pinched fingertips).
            self._follow(pc, t, out)
            return out

        if self._phase == "scroll":
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
