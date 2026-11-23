"""Air v3 hand mouse: ring table, pure engine phases, controller, and fixture replay."""

from __future__ import annotations

import _setup  # noqa: F401
import gzip
import json
import math
import sys
import time
from pathlib import Path

from lura.modules.air import (
    MODES,
    AirController,
    AirService,
    Command,
    InteractionEngine,
    ring_action,
    ring_labels,
)
from lura.modules.air.domain import KINDS, Hand, Point
from lura.modules.air.engine import (
    CURSOR_GAIN,
    DISENGAGE_AFTER,
    DRAG_HOLD,
    HINT_AFTER,
    HINT_EVERY,
    POINT_ENTER,
    RELEASE_HOLD,
    SCROLL_ENTER,
    SCROLL_STEP,
    SWIPE_COOLDOWN,
    SWIPE_RETURN,
    THUMB_HOLD,
    THUMB_PALM,
    TWO_COOLDOWN,
    TWO_DRAG_HOLD,
    TWO_ENTER,
    ZONE_PALM,
)
from lura.modules.computer import ActionResult, Rect, WindowGrip
from lura.shared import events
from test_air import FakeCamera, FakeLandmarker, make_hand
from test_air_control import BOUNDS, FakeAir, FakeDesktop

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "air"


def _step_hand(
    eng: InteractionEngine,
    t0: float,
    dur: float,
    pose: str,
    *,
    cx: float = 0.5,
    cy: float = 0.5,
    size: float = 0.28,
    dt: float = 1.0 / 30.0,
    pinch_gap: float | None = None,
    thumb: str | None = None,
) -> tuple[float, list[Command]]:
    out: list[Command] = []
    n = max(1, round(dur / dt))
    t = t0
    for i in range(n + 1):
        t = t0 + i * dt
        out.extend(eng.update(t, (make_hand(pose, cx=cx, cy=cy, size=size, pinch_gap=pinch_gap,
                                            thumb=thumb),)))
    return t, out


def _point(eng: InteractionEngine, t0: float = 0.0, cx: float = 0.5, cy: float = 0.5) -> float:
    t, cmds = _step_hand(eng, t0, POINT_ENTER + 0.1, "point", cx=cx, cy=cy, size=0.28)
    assert eng.state["engaged"] and eng.state["phase"] == "point", cmds
    return t


def _kinds(cmds: list[Command]) -> list[str]:
    return [c.kind for c in cmds if c.kind != "cursor"]


def check_ring_table() -> None:
    universal_lr = {
        "browser": (
            ("press_keys", {"keys": "ctrl+shift+Tab"}),
            ("press_keys", {"keys": "ctrl+Tab"}),
            ("TAB<", "TAB>", "MAX", "CLICK"),
        ),
        "media": (
            ("media", {"command": "previous"}),
            ("media", {"command": "next"}),
            ("PREV", "NEXT", "MAX", "CLICK"),
        ),
        "presentation": (
            ("press_keys", {"keys": "Left"}),
            ("press_keys", {"keys": "Right"}),
            ("SLIDE<", "SLIDE>", "MAX", "CLICK"),
        ),
        "files": (
            ("press_keys", {"keys": "alt+Left"}),
            ("press_keys", {"keys": "alt+Right"}),
            ("BACK", "FWD", "MAX", "CLICK"),
        ),
        "editor": (
            ("press_keys", {"keys": "ctrl+Page_Up"}),
            ("press_keys", {"keys": "ctrl+Page_Down"}),
            ("TAB<", "TAB>", "MAX", "CLICK"),
        ),
        "terminal": (
            ("press_keys", {"keys": "ctrl+Page_Up"}),
            ("press_keys", {"keys": "ctrl+Page_Down"}),
            ("TAB<", "TAB>", "MAX", "CLICK"),
        ),
        "other": (
            ("workspace_switch", {"target": "prev"}),
            ("workspace_switch", {"target": "next"}),
            ("DESK<", "DESK>", "MAX", "CLICK"),
        ),
    }
    assert set(universal_lr) == set(KINDS)
    for mode in ("universal", "pointer", "files", "developer"):
        for kind, (want_l, want_r, want_lbls) in universal_lr.items():
            assert ring_action("left", mode, kind) == want_l
            assert ring_action("right", mode, kind) == want_r
            assert ring_action("up", mode, kind) == ("toggle_maximize", {})
            assert ring_action("down", mode, kind) == ("click", {})
            assert ring_labels(mode, kind) == want_lbls

    mode_rows = {
        "workspace": (
            ("workspace_switch", {"target": "prev"}),
            ("workspace_switch", {"target": "next"}),
            ("workspace_overview", {}),
            ("show_desktop", {}),
            ("DESK<", "DESK>", "OVERVIEW", "DESKTOP"),
        ),
        "window": (
            ("window_snap", {"where": "left"}),
            ("window_snap", {"where": "right"}),
            ("toggle_maximize", {}),
            ("window_minimize", {}),
            ("SNAP<", "SNAP>", "MAX", "MIN"),
        ),
        "media": (
            ("media", {"command": "previous"}),
            ("media", {"command": "next"}),
            ("volume_change", {"delta": 10}),
            ("volume_change", {"delta": -10}),
            ("PREV", "NEXT", "VOL+", "VOL-"),
        ),
        "presentation": (
            ("press_keys", {"keys": "Left"}),
            ("press_keys", {"keys": "Right"}),
            ("ignore", {}),
            ("ignore", {}),
            ("SLIDE<", "SLIDE>", "", ""),
        ),
        "ai": (
            ("intent", {"intent": "talk"}),
            ("intent", {"intent": "talk"}),
            ("intent", {"intent": "talk"}),
            ("intent", {"intent": "talk"}),
            ("TALK", "TALK", "TALK", "TALK"),
        ),
    }
    for mode, (l_act, r_act, u_act, d_act, lbls) in mode_rows.items():
        for kind in ("browser", "other"):
            assert ring_action("left", mode, kind) == l_act
            assert ring_action("right", mode, kind) == r_act
            assert ring_action("up", mode, kind) == u_act
            assert ring_action("down", mode, kind) == d_act
            assert ring_labels(mode, kind) == lbls

    # Every mode in MODES returns a 4-tuple of ASCII labels.
    for mode in MODES:
        lbls = ring_labels(mode, "other")
        assert len(lbls) == 4 and all(s.isascii() for s in lbls), f"{mode}: {lbls}"
    assert ring_action("bogus", "universal", "other") == ("ignore", {})


def check_point_moves_cursor() -> None:
    eng = InteractionEngine()
    # Open palm held still no longer does anything (no engage step).
    _, cmds = _step_hand(eng, 0.0, 2.0, "open_palm", size=0.28)
    assert not cmds, cmds
    # Typing-distance hand never starts the cursor.
    eng = InteractionEngine()
    _, cmds = _step_hand(eng, 0.0, 1.0, "point", size=0.13)
    assert not any(c.kind in ("engage", "cursor") for c in cmds), cmds
    # Close pointing finger: engage after POINT_ENTER, then moving it moves the cursor right/down.
    eng = InteractionEngine()
    t = _point(eng)
    out: list[Command] = []
    for i in range(1, 31):
        out.extend(eng.update(t + i / 30.0, (make_hand("point", cx=0.5 + 0.01 * i, cy=0.5 + 0.005 * i, size=0.28),)))
    dx = sum(c.dx for c in out if c.kind == "cursor")
    dy = sum(c.dy for c in out if c.kind == "cursor")
    assert dx > 300 and dy > 50, (dx, dy)
    # Fist = lift the mouse: moving does nothing.
    t += 1.1
    _, cmds = _step_hand(eng, t, 0.3, "fist", cx=0.3, size=0.28)
    assert not any(c.kind == "cursor" for c in cmds)
    assert eng.state["phase"] == "idle"


def check_tap_click_and_hold_drag() -> None:
    eng = InteractionEngine()
    t = _point(eng)
    # Quick tap -> click (once), back to pointing.
    t, cmds = _step_hand(eng, t + 0.03, 0.15, "point", size=0.28, pinch_gap=0.0)
    t, cmds2 = _step_hand(eng, t + 0.03, 0.2, "point", size=0.28)
    assert _kinds(cmds + cmds2) == ["click"], _kinds(cmds + cmds2)
    # Hold -> press, move -> cursor, release -> release.
    t, cmds = _step_hand(eng, t + 0.03, DRAG_HOLD + 0.1, "point", size=0.28, pinch_gap=0.0)
    assert _kinds(cmds) == ["press"], _kinds(cmds)
    assert eng.state["phase"] == "drag"
    out: list[Command] = []
    for i in range(1, 16):
        out.extend(eng.update(t + i / 30.0, (make_hand("point", cx=0.5 + 0.01 * i, size=0.28, pinch_gap=0.0),)))
    assert sum(c.dx for c in out if c.kind == "cursor") > 100
    t += 0.6
    _, cmds = _step_hand(eng, t, 0.2, "point", cx=0.65, size=0.28)
    assert _kinds(cmds) == ["release"], _kinds(cmds)
    # Losing the hand mid-drag releases the button.
    t = _point(eng, t + 0.5)
    t, cmds = _step_hand(eng, t + 0.03, DRAG_HOLD + 0.1, "point", size=0.28, pinch_gap=0.0)
    assert "press" in _kinds(cmds)
    out = []
    for i in range(1, 8):
        out.extend(eng.update(t + i / 30.0, ()))
    assert _kinds(out) == ["release"], _kinds(out)
    # A pinch without pointing first (e.g. picking something up) never clicks.
    eng = InteractionEngine()
    _, cmds = _step_hand(eng, 0.0, 1.0, "pinch", size=0.28)
    assert not cmds, cmds


def check_finger_gun_click_drag_right_click() -> None:
    from lura.modules.air.engine import THUMB_ARM, THUMB_DRAG_HOLD, thumb_reach
    from lura.modules.air.engine import THUMB_ARM_OUT, THUMB_IN
    assert thumb_reach(make_hand("point", size=0.28, thumb="up")) > THUMB_ARM_OUT
    assert thumb_reach(make_hand("point", size=0.28, thumb="down")) < THUMB_IN
    # Cock the thumb, tap it down and up: one click, the cursor does not move.
    eng = InteractionEngine()
    t = _point(eng)
    t, cmds = _step_hand(eng, t + 0.03, THUMB_ARM + 0.1, "point", size=0.28, thumb="up")
    t, cmds2 = _step_hand(eng, t + 0.03, 0.1, "point", size=0.28, thumb="down")
    t, cmds3 = _step_hand(eng, t + 0.03, 0.15, "point", size=0.28, thumb="up")
    allc = cmds + cmds2 + cmds3
    assert _kinds(allc) == ["click"], _kinds(allc)
    assert sum(abs(c.dx) + abs(c.dy) for c in allc if c.kind == "cursor") == 0, allc
    # A second tap right away (no wait to re-arm) -> a second click = double click.
    t, cmds = _step_hand(eng, t + 0.03, 0.1, "point", size=0.28, thumb="down")
    t, cmds2 = _step_hand(eng, t + 0.03, 0.1, "point", size=0.28, thumb="up")
    assert _kinds(cmds + cmds2) == ["click"], _kinds(cmds + cmds2)
    # Keep the thumb down -> press; move -> cursor (drawing); thumb up -> release.
    t, _ = _step_hand(eng, t + 0.03, THUMB_ARM + 0.3, "point", size=0.28, thumb="up")
    t, cmds = _step_hand(eng, t + 0.03, THUMB_DRAG_HOLD + 0.1, "point", size=0.28, thumb="down")
    assert _kinds(cmds) == ["press"], _kinds(cmds)
    out: list[Command] = []
    for i in range(1, 16):
        out.extend(eng.update(t + i / 30.0, (make_hand("point", cx=0.5 + 0.01 * i, size=0.28, thumb="down"),)))
    assert sum(c.dx for c in out if c.kind == "cursor") > 100, out
    t += 0.6
    _, cmds = _step_hand(eng, t, 0.2, "point", cx=0.65, size=0.28, thumb="up")
    assert _kinds(cmds) == ["release"], _kinds(cmds)
    # The thumb coming down while the hand sweeps fast (a relaxed thumb drifting): no click.
    eng = InteractionEngine()
    t = _point(eng, cx=0.3)
    t, _ = _step_hand(eng, t + 0.03, THUMB_ARM + 0.1, "point", cx=0.3, size=0.28, thumb="up")
    out = []
    for i in range(1, 12):
        out += eng.update(t + i / 30.0, (make_hand("point", cx=0.3 + 0.02 * i, size=0.28,
                                                   thumb="up" if i < 6 else "down"),))
    assert not [c for c in out if c.kind in ("click", "press")], _kinds(out)
    # Switched off (settings air_thumb_click = false): the tap does nothing, the pinch still clicks.
    eng = InteractionEngine(thumb_click=False)
    t = _point(eng)
    t, cmds = _step_hand(eng, t + 0.03, THUMB_ARM + 0.1, "point", size=0.28, thumb="up")
    t, cmds2 = _step_hand(eng, t + 0.03, 0.15, "point", size=0.28, thumb="down")
    t, cmds3 = _step_hand(eng, t + 0.03, 0.15, "point", size=0.28, thumb="up")
    assert not _kinds(cmds + cmds2 + cmds3), _kinds(cmds + cmds2 + cmds3)
    t, cmds = _step_hand(eng, t + 0.03, 0.15, "point", size=0.28, pinch_gap=0.0)
    t, cmds2 = _step_hand(eng, t + 0.03, 0.2, "point", size=0.28)
    assert _kinds(cmds + cmds2) == ["click"], _kinds(cmds + cmds2)
    # Pointing with the thumb tucked the whole time (never cocked): nothing but the cursor.
    eng = InteractionEngine()
    _, cmds = _step_hand(eng, 0.0, 2.0, "point", size=0.28, thumb="down")
    assert _kinds(cmds) == ["engage"], _kinds(cmds)
    # V with the same thumb tap -> right click.
    eng = InteractionEngine()
    t, cmds = _step_hand(eng, 0.0, THUMB_ARM + 0.1, "victory", size=0.28, thumb="up")
    t, cmds2 = _step_hand(eng, t + 0.03, 0.15, "victory", size=0.28, thumb="down")
    assert _kinds(cmds2).count("right_click") == 1 and "click" not in _kinds(cmds + cmds2), cmds + cmds2
    # ... also once V scrolling is running.
    eng = InteractionEngine()
    t, _ = _step_hand(eng, 0.0, SCROLL_ENTER + THUMB_ARM + 0.2, "victory", size=0.28, thumb="up")
    assert eng.state["phase"] == "scroll"
    t, cmds = _step_hand(eng, t + 0.03, 0.15, "victory", size=0.28, thumb="down")
    assert _kinds(cmds) == ["right_click"], _kinds(cmds)


def check_pointer_acceleration() -> None:
    from lura.modules.air.engine import GAIN_FAST, GAIN_SLOW, accel_gain
    assert accel_gain(0.0) == GAIN_SLOW and accel_gain(5.0) == GAIN_FAST
    assert GAIN_SLOW < accel_gain(0.4) < GAIN_FAST

    def travel(step: float, frames: int) -> float:
        eng = InteractionEngine()
        t = _point(eng, cx=0.3)
        out: list[Command] = []
        for i in range(1, frames + 31):                 # then held still while the filter settles
            out += eng.update(t + i / 30.0, (make_hand("point", cx=0.3 + step * min(i, frames), size=0.28),))
        return sum(c.dx for c in out if c.kind == "cursor")
    # The same 0.3 of the frame: slowly (3 s) moves the cursor much less than quickly (0.3 s).
    slow, fast = travel(0.0033, 90), travel(0.033, 9)
    assert fast > 1.5 * slow, (slow, fast)


def check_flick_glide() -> None:
    eng = InteractionEngine()
    t = _point(eng, cx=0.5, cy=0.6)
    out: list[Command] = []
    for i in range(1, 5):
        t += 1 / 30.0
        out += eng.update(t, (make_hand("point", cx=0.5, cy=0.6 - 0.0375 * i, size=0.28),))
    flick = [c for c in out if c.kind == "scroll" and c.text != "glide"]
    assert len(flick) == 1, out
    glide: list[Command] = []
    for _ in range(45):
        t += 1 / 30.0
        glide += eng.update(t, (make_hand("point", cx=0.5, cy=0.6, size=0.28),))
    steps = [c.steps for c in glide if c.kind == "scroll"]
    assert steps and all(s < 0 for s in steps), glide             # keeps going the same way ...
    assert abs(sum(steps)) >= abs(flick[0].steps) // 2, (steps, flick)
    assert max(c.t for c in glide if c.kind == "scroll") < t - 0.3, glide  # ... and comes to rest
    # A fist stops the glide at once.
    eng = InteractionEngine()
    t = _point(eng, cx=0.5, cy=0.6)
    for i in range(1, 5):
        t += 1 / 30.0
        eng.update(t, (make_hand("point", cx=0.5, cy=0.6 - 0.0375 * i, size=0.28),))
    out = []
    for _ in range(20):
        t += 1 / 30.0
        out += eng.update(t, (make_hand("fist", cx=0.5, cy=0.6, size=0.28),))
    assert not [c for c in out if c.kind == "scroll"], out


def _sweep(eng: InteractionEngine, t0: float, pose: str, x0: float, step: float, n: int = 12) -> list[Command]:
    out: list[Command] = []
    for i in range(n):
        out.extend(eng.update(t0 + i / 30.0, (make_hand(pose, cx=x0 + step * i, size=0.26),)))
    return out


def check_swipe_workspace() -> None:
    from lura.modules.air.engine import blade_ratio
    assert blade_ratio(make_hand("blade", size=0.26)) < 0.38 < blade_ratio(make_hand("open_palm", size=0.26))
    # The hand's edge swept right (0.3 of the frame in 0.4 s): the workspaces follow the hand, so the
    # previous one comes in -> the controller's "left" (prev).
    eng = InteractionEngine()
    out = _sweep(eng, 0.0, "blade", 0.35, 0.025)
    assert [c.direction for c in out if c.kind == "swipe"] == ["left"], out
    t = 11 / 30.0
    # Bringing the hand back right after is ignored.
    out = _sweep(eng, t + 0.05, "blade", 0.63, -0.025)
    assert not [c for c in out if c.kind == "swipe"], out
    # After SWIPE_RETURN the other direction works: sweep left -> next ("right").
    t += SWIPE_RETURN + SWIPE_COOLDOWN
    _sweep(eng, t - 0.4, "blade", 0.65, 0.0)                    # hand still for a moment
    out = _sweep(eng, t, "blade", 0.65, -0.025)
    assert [c.direction for c in out if c.kind == "swipe"] == ["right"], out
    # Back-and-forth waving inside SWIPE_RETURN gives one switch, not one per wave.
    eng = InteractionEngine()
    out, t = [], 0.0
    for k in range(6):
        out += _sweep(eng, t, "blade", 0.35 if k % 2 == 0 else 0.63, 0.025 if k % 2 == 0 else -0.025)
        t += 0.45
    assert len([c for c in out if c.kind == "swipe"]) == 1, [c for c in out if c.kind == "swipe"]
    # Two deliberate sweeps with a short pause between them -> two switches.
    eng = InteractionEngine()
    out = _sweep(eng, 0.0, "blade", 0.35, 0.025)                 # sweep right
    out += _sweep(eng, 0.45, "blade", 0.625, -0.025)             # bring the hand back (ignored)
    out += _sweep(eng, 0.85, "blade", 0.35, 0.0)                 # still for 0.4 s
    out += _sweep(eng, 1.30, "blade", 0.35, 0.025)               # sweep right again
    assert [c.direction for c in out if c.kind == "swipe"] == ["left", "left"], out
    # A slow drift is not a swipe.
    eng = InteractionEngine()
    out = _sweep(eng, 0.0, "blade", 0.3, 0.004, n=60)
    assert not [c for c in out if c.kind == "swipe"], out
    # A flat palm, a fist or the pointing finger moving sideways: nothing.
    for pose in ("open_palm", "fist", "point"):
        eng = InteractionEngine()
        out = _sweep(eng, 0.0, pose, 0.35, 0.025)
        assert not [c for c in out if c.kind == "swipe"], (pose, out)


def check_flick_scroll() -> None:
    from lura.modules.air.engine import FLICK_FREEZE, FLICK_RETURN
    eng = InteractionEngine()
    t = _point(eng, cx=0.5, cy=0.6)
    # Quick flick up (0.15 of the frame in ~0.13 s): one scroll, page follows the finger -> wheel down.
    out: list[Command] = []
    for i in range(1, 5):
        t += 1 / 30.0
        out += eng.update(t, (make_hand("point", cx=0.5, cy=0.6 - 0.0375 * i, size=0.28),))
    scrolls = [c for c in out if c.kind == "scroll" and c.text != "glide"]
    assert len(scrolls) == 1 and scrolls[0].steps <= -3, out
    net_y = sum(c.dy for c in out if c.kind == "cursor")
    assert abs(net_y) <= 12, (net_y, out)                 # the flick's cursor movement was undone
    # The finger coming back down (fast, inside FLICK_RETURN): no cursor, no opposite scroll; the
    # page keeps gliding the same way.
    out = []
    for i in range(1, 5):
        t += 1 / 30.0
        out += eng.update(t, (make_hand("point", cx=0.5, cy=0.45 + 0.0375 * i, size=0.28),))
    assert not [c for c in out if c.kind == "cursor"], out
    assert all(c.text == "glide" and c.steps < 0 for c in out if c.kind == "scroll"), out
    # A pinch right after a flick does not click.
    out = []
    for i in range(6):
        t += 1 / 30.0
        out += eng.update(t, (make_hand("point", cx=0.5, cy=0.6, size=0.28, pinch_gap=0.02),))
    assert not [c for c in out if c.kind in ("click", "press")], out
    for _ in range(30):                                   # let the glide die out
        t += 1 / 30.0
        eng.update(t, (make_hand("point", cx=0.5, cy=0.6, size=0.28),))
    # After the freeze, a second flick up scrolls again.
    t += max(FLICK_FREEZE, FLICK_RETURN)
    for _ in range(4):
        t += 1 / 30.0
        eng.update(t, (make_hand("point", cx=0.5, cy=0.6, size=0.28),))
    out = []
    for i in range(1, 5):
        t += 1 / 30.0
        out += eng.update(t, (make_hand("point", cx=0.5, cy=0.6 - 0.0375 * i, size=0.28),))
    assert [c.steps < 0 for c in out if c.kind == "scroll" and c.text != "glide"] == [True], out
    # A flick down -> wheel up (after FLICK_RETURN).
    t += FLICK_RETURN + 0.1
    for _ in range(4):
        t += 1 / 30.0
        eng.update(t, (make_hand("point", cx=0.5, cy=0.45, size=0.28),))
    out = []
    for i in range(1, 5):
        t += 1 / 30.0
        out += eng.update(t, (make_hand("point", cx=0.5, cy=0.45 + 0.0375 * i, size=0.28),))
    assert [c.steps > 0 for c in out if c.kind == "scroll" and c.text != "glide"] == [True], out
    # Slow vertical movement is the mouse, not a scroll.
    eng = InteractionEngine()
    t = _point(eng, cx=0.5, cy=0.7)
    out = []
    for i in range(1, 31):
        t += 1 / 30.0
        out += eng.update(t, (make_hand("point", cx=0.5, cy=0.7 - 0.006 * i, size=0.28),))
    assert not [c for c in out if c.kind == "scroll"], out
    assert sum(c.dy for c in out if c.kind == "cursor") < -100, out    # slow = precise (acceleration)


def check_scroll_thumbs_disengage_hint() -> None:
    eng = InteractionEngine()
    t, cmds = _step_hand(eng, 0.0, SCROLL_ENTER + 0.1, "victory", size=0.28)
    assert eng.state["phase"] == "scroll"
    out: list[Command] = []
    for i in range(1, 16):
        out.extend(eng.update(t + i / 30.0, (make_hand("victory", cy=0.5 - 0.01 * i, size=0.28),)))
    # Phone-style (SCROLL_NATURAL): hand up -> the page moves up -> wheel down (negative steps).
    downs = -sum(c.steps for c in out if c.kind == "scroll")
    assert downs >= int(0.15 / SCROLL_STEP) - 1 and all(c.steps < 0 for c in out if c.kind == "scroll"), out
    # Hand back down -> wheel up.
    t2 = t + 16 / 30.0
    out = []
    for i in range(1, 16):
        out.extend(eng.update(t2 + i / 30.0, (make_hand("victory", cy=0.35 + 0.01 * i, size=0.28),)))
    assert out and all(c.steps > 0 for c in out if c.kind == "scroll"), out
    t = t2 + 16 / 30.0
    # Disengage after DISENGAGE_AFTER without a hand.
    t += 0.6
    out = []
    for i in range(int((DISENGAGE_AFTER + 0.2) * 30)):
        out.extend(eng.update(t + i / 30.0, ()))
    assert _kinds(out) == ["disengage"], _kinds(out)

    eng_t = InteractionEngine()
    t, cmds = _step_hand(eng_t, 0.0, THUMB_HOLD + 0.3, "thumbs_up", cy=0.6, size=THUMB_PALM + 0.02)
    assert [c.text for c in cmds if c.kind == "intent"] == ["confirm"]
    t, cmds = _step_hand(eng_t, t + 0.05, THUMB_HOLD + 0.3, "thumbs_down", cy=0.6, size=0.15)
    assert [c.text for c in cmds if c.kind == "intent"] == ["deny"]

    eng_h = InteractionEngine()
    _, cmds = _step_hand(eng_h, 0.0, HINT_AFTER + 0.1, "point", size=ZONE_PALM - 0.05)
    hints = [c for c in cmds if c.kind == "hint"]
    assert len(hints) == 1 and hints[0].text == "Move your hand closer to the camera."
    _, cmds = _step_hand(eng_h, HINT_AFTER + 0.2, HINT_EVERY - 3.0, "point", size=0.15, dt=0.2)
    assert not any(c.kind == "hint" for c in cmds)


def check_controller_cursor_and_coalescing() -> None:
    air = FakeAir()
    desk = FakeDesktop(pointer_pos=(1900, 500))
    now = [0.0]
    ctrl = AirController(air, desk, clock=lambda: now[0])  # type: ignore[arg-type]

    # Engage reads pointer once; cursor accumulates across two monitors and clamps to BOUNDS (3840x1080).
    ctrl.process(Command("engage", 0.0))
    assert desk.pointer_reads == 1
    ctrl.process(Command("cursor", 0.1, dx=500.0, dy=-600.0))
    assert desk.pointers[-1] == (2400, 0)  # crossed onto 2nd monitor, y clamped to 0
    ctrl.process(Command("cursor", 0.2, dx=5000.0, dy=5000.0))
    assert desk.pointers[-1] == (3839, 1079)

    # Gap > 1.0 s re-reads real pointer (user moved the mouse).
    desk.pointer_pos = (120, 220)
    ctrl.process(Command("cursor", 2.0, dx=30.0, dy=-20.0))
    assert desk.pointer_reads == 2
    assert desk.pointers[-1] == (150, 200)

    # Coalescing of 50 queued cursor commands into ONE pointer move.
    desk.pointers.clear()
    for i in range(50):
        ctrl._enqueue(Command("cursor", 2.01 + i * 0.001, dx=2.0, dy=1.0))
    ctrl.start()
    deadline = time.monotonic() + 2.0
    while not desk.pointers and time.monotonic() < deadline:
        time.sleep(0.01)
    ctrl.stop()
    assert desk.pointers == [(250, 250)], f"expected single coalesced move, got {desk.pointers}"


def check_controller_ring_grab_scroll_intent() -> None:
    air = FakeAir()
    desk = FakeDesktop(pointer_pos=(400, 300), target=(42, "google-chrome", "Docs - Chrome"))
    pub: list[tuple[str, dict]] = []
    ctrl = AirController(air, desk, lambda n, p: pub.append((n, p)))  # type: ignore[arg-type]

    ctrl.process(Command("engage", 0.0))

    # Universal + browser -> ring_open shows browser labels, ring_select right focuses target then ctrl+Tab.
    ctrl.process(Command("ring_open", 0.1))
    assert ctrl.state()["ring_labels"] == ("TAB<", "TAB>", "MAX", "CLICK")
    ctrl.process(Command("ring_select", 0.2, direction="right"))
    assert desk.runs == [
        ("window_focus", {"window": 42}),
        ("press_keys", {"keys": "ctrl+Tab"}),
    ]
    assert ctrl.state()["ring_labels"] is None

    # Universal + down -> click at current cursor.
    desk.runs.clear()
    ctrl.process(Command("ring_open", 0.3))
    ctrl.process(Command("ring_select", 0.4, direction="down"))
    assert desk.runs == [("pointer_click", {"button": "left"})]

    # Universal + up -> toggle_maximize on target (focused first); no target -> ok False.
    desk.runs.clear()
    desk.maximized = True
    ctrl.process(Command("ring_open", 0.5))
    ctrl.process(Command("ring_select", 0.6, direction="up"))
    assert desk.runs == [("window_focus", {"window": 42}), ("window_restore", {"window": 42})]

    desk.runs.clear()
    desk.target = None
    ctrl.process(Command("ring_open", 0.7))
    ctrl.process(Command("ring_select", 0.8, direction="up"))
    assert desk.runs == []
    assert pub[-1][1]["ok"] is False and "No window" in pub[-1][1]["message"]

    # Universal + no window + right -> workspace_switch next.
    desk.runs.clear()
    ctrl.process(Command("ring_open", 0.9))
    ctrl.process(Command("ring_select", 1.0, direction="right"))
    assert desk.runs == [("workspace_switch", {"target": "next"})]

    # Window mode + left / down; presentation + up (ignore); ai + left (AIR_INTENT talk).
    desk.target = (42, "google-chrome", "Docs - Chrome")
    ctrl.set_mode("window")
    desk.runs.clear()
    ctrl.process(Command("ring_open", 1.1))
    ctrl.process(Command("ring_select", 1.2, direction="left"))
    assert desk.runs == [("window_focus", {"window": 42}), ("window_snap", {"where": "left", "window": 42})]

    ctrl.set_mode("presentation")
    desk.runs.clear()
    ctrl.process(Command("ring_open", 1.3))
    ctrl.process(Command("ring_select", 1.4, direction="up"))
    assert desk.runs == [] and pub[-1][1]["action"] == "ignore" and pub[-1][1]["ok"] is False

    ctrl.set_mode("ai")
    pub.clear()
    ctrl.process(Command("ring_open", 1.5))
    ctrl.process(Command("ring_select", 1.6, direction="left"))
    intents = [p for n, p in pub if n == events.AIR_INTENT]
    assert intents == [{
        "intent": "talk",
        "mode": "ai",
        "gesture": "ring_left",
        "app": "google-chrome",
        "title": "Docs - Chrome",
    }]

    # Grab moves the window under the cursor and snaps at the right edge.
    ctrl.set_mode("universal")
    desk.runs.clear()
    desk.places.clear()
    desk.grips.clear()
    ctrl.process(Command("grab_start", 2.0))
    assert desk.runs == [("window_focus", {"window": 42})]
    assert desk.grips == [42]
    assert ctrl.state()["target"] == "Docs - Chrome"
    ctrl.process(Command("grab_move", 2.1, dx=2.0, dy=0.0))   # < PLACE_MIN_PX
    assert desk.places == []
    ctrl.process(Command("grab_move", 2.2, dx=3000.0, dy=0.0))  # reaches right edge of 3840
    assert len(desk.places) == 1
    ctrl.process(Command("grab_end", 2.3))
    assert desk.runs[-1] == ("window_snap", {"window": 7, "where": "right"})

    # Scroll up and down.
    desk.runs.clear()
    ctrl.process(Command("scroll", 3.0, steps=2))
    ctrl.process(Command("scroll", 3.1, steps=-1))
    assert desk.runs == [
        ("pointer_scroll", {"direction": "up", "amount": 2}),
        ("pointer_scroll", {"direction": "down", "amount": 1}),
    ]

    # Thumbs intent carries app/title of window under cursor.
    pub.clear()
    ctrl.process(Command("intent", 4.0, text="confirm"))
    intents = [p for n, p in pub if n == events.AIR_INTENT]
    assert intents == [{
        "intent": "confirm", "mode": "universal", "gesture": "thumbs",
        "app": "google-chrome", "title": "Docs - Chrome",
    }]


def check_controller_click_drag_swipe() -> None:
    air = FakeAir()
    desk = FakeDesktop(pointer_pos=(400, 300))
    ctrl = AirController(air, desk)  # type: ignore[arg-type]
    ctrl.process(Command("engage", 0.0))
    desk.runs.clear()
    ctrl.process(Command("click", 0.1))
    ctrl.process(Command("press", 0.2))
    ctrl.process(Command("release", 0.3))
    ctrl.process(Command("release", 0.35))          # no second button-up
    ctrl.process(Command("right_click", 0.37))
    ctrl.process(Command("swipe", 0.4, direction="right"))
    ctrl.process(Command("swipe", 0.5, direction="left"))
    assert desk.runs == [
        ("pointer_click", {"button": "left", "count": 1}),
        ("pointer_down", {"button": "left"}),
        ("pointer_up", {"button": "left"}),
        ("pointer_click", {"button": "right", "count": 1}),
        ("workspace_switch", {"target": "next"}),
        ("workspace_switch", {"target": "prev"}),
    ], desk.runs
    # Disengage while the button is held lets it go.
    desk.runs.clear()
    ctrl.process(Command("press", 1.0))
    ctrl.process(Command("disengage", 1.1))
    assert desk.runs == [("pointer_down", {"button": "left"}), ("pointer_up", {"button": "left"})], desk.runs


def check_camera_fallback() -> None:
    from lura.modules.air.application import CameraChain
    from lura.modules.air.domain import CameraInfo, camera_candidates

    cams = [
        CameraInfo(0, "Integrated Camera", "uvcvideo", True),
        CameraInfo(9, "Mobile Webcam", "v4l2loopback", False),
    ]
    assert [c.index for c in camera_candidates(cams, "mobile")] == [9, 0]
    assert [c.index for c in camera_candidates(cams, "")] == [0]
    assert [c.index for c in camera_candidates(cams[1:], "nothing")] == []
    assert [c.index for c in camera_candidates(cams, "nothing")] == [0]   # asked-for one gone -> internal

    chain = CameraChain(camera_candidates(cams, "mobile"), lambda info: info.index)  # type: ignore[arg-type,return-value]
    assert chain() == 9
    assert chain.failed() is True and chain() == 0
    single = CameraChain([cams[0]], lambda info: info.index)  # type: ignore[arg-type,return-value]
    assert single.failed() is False

    # AirService: a camera that never delivers frames is replaced by the next one.
    class DeadCamera(FakeCamera):
        name = "Mobile Webcam"

        def read(self):  # type: ignore[override]
            return None

    good = FakeCamera()
    made: list[str] = []

    class Chain:
        def __init__(self) -> None:
            self.pos = 0

        def __call__(self):
            made.append("dead" if self.pos == 0 else "good")
            return DeadCamera() if self.pos == 0 else good

        def failed(self) -> bool:
            self.pos = 1
            return True

    script = [(make_hand("point", size=0.28),) for _ in range(40)]
    svc = AirService(None, camera_factory=Chain(), landmarker_factory=lambda: FakeLandmarker(script))
    svc.start()
    deadline = time.monotonic() + 3.0
    while "good" not in made and time.monotonic() < deadline:
        time.sleep(0.01)
    svc.stop()
    assert made[:2] == ["dead", "good"], made


def check_air_service_thumb_click_setting() -> None:
    svc = AirService(None, thumb_click=False)
    assert svc._engine.thumb_click is False
    svc.set_thumb_click(True)
    assert svc._engine.thumb_click is True


def check_air_service_commands() -> None:
    # AirService feeds InteractionEngine and dispatches Commands + updates engine_state().
    script = [(make_hand("point", cx=0.5, cy=0.5, size=0.28),) for _ in range(32)]
    clock_t = [0.0]

    def fake_clock() -> float:
        t = clock_t[0]
        clock_t[0] += 1.0 / 30.0
        return t

    cmds: list[Command] = []
    cam = FakeCamera()
    lm = FakeLandmarker(script)
    svc = AirService(
        None,
        camera_factory=lambda: cam,
        landmarker_factory=lambda: lm,
        clock=fake_clock,
    )
    svc.subscribe_commands(cmds.append)
    svc.start()
    deadline = time.monotonic() + 3.0
    while not any(c.kind == "engage" for c in cmds) and time.monotonic() < deadline:
        time.sleep(0.01)
    engaged_seen = any(c.kind == "engage" for c in cmds)
    svc.stop()
    assert engaged_seen, f"AirService never emitted engage command: {cmds}"


def _replay(path: Path) -> list[tuple[str, str, Command]]:
    eng = InteractionEngine()
    out: list[tuple[str, str, Command]] = []
    with gzip.open(path, "rt", encoding="utf-8") as handle:
        for line in handle:
            row = json.loads(line)
            hands = tuple(
                Hand(h["side"], float(h["score"]), tuple(Point(p[0], p[1], p[2]) for p in h["pts"]))
                for h in row["hands"]
            )
            lbl = str(row.get("label", ""))
            cue = str(row.get("cue", ""))
            for cmd in eng.update(float(row["t"]), hands):
                out.append((lbl, cue, cmd))
    return out


def check_replay_recordings() -> None:
    """Recorded sessions (open palm / pinch / normal typing, no pointing): v3 must stay quiet."""
    for name in ("session1-free.jsonl.gz", "session2-cued.jsonl.gz"):
        path = FIXTURES / name
        if not path.is_file():
            print(f"replay skipped: tests/fixtures/air/{name} not found (local recordings, not in git)")
            continue
        emitted = _replay(path)
        normal = [c for lbl, _, c in emitted if lbl == "normal"]
        bad = [c for c in normal if c.kind in ("click", "press", "swipe")]
        assert not bad, f"{name} normal activity triggered {bad}"
        if name.startswith("session2"):
            # Open hand held still or moved slowly: no swipe, no click.
            calm = [c for lbl, _, c in emitted if lbl in ("near_still", "sit_normal", "ready")
                    and c.kind in ("click", "press", "swipe", "cursor")]
            assert not calm, calm
            thumbs = [c.text for lbl, _, c in emitted if lbl == "thumbs" and c.kind == "intent"]
            assert "confirm" in thumbs and "deny" in thumbs, thumbs
            scrolls = [c for lbl, _, c in emitted if lbl == "v_scroll" and c.kind == "scroll"]
            assert len(scrolls) >= 3, len(scrolls)


def check_two_hands_engine() -> None:
    dt = 1.0 / 30.0

    def pair_hands(
        lx: float,
        rx: float,
        cy: float = 0.55,
        *,
        l_pose: str = "pinch",
        r_pose: str = "pinch",
        l_gap: float | None = None,
        r_gap: float | None = None,
        size: float = 0.22,
    ) -> tuple[Hand, Hand]:
        return (
            make_hand(l_pose, cx=lx, cy=cy, size=size, side="Left", pinch_gap=l_gap),
            make_hand(r_pose, cx=rx, cy=cy, size=size, side="Right", pinch_gap=r_gap),
        )

    # 1. Two in-frame hands pinching from frame 0, held 0.3 s -> one two_start, no click/press/right_click.
    eng = InteractionEngine()
    t = 0.0
    out: list[Command] = []
    for i in range(10):
        t = i * dt
        out.extend(eng.update(t, pair_hands(0.40, 0.60)))
    assert _kinds(out).count("two_start") == 1, out
    assert not any(c.kind in ("click", "press", "right_click") for c in out), out
    assert eng.state["phase"] == "two"

    # 2. Spread apart to 1.5x over 0.5 s -> progress in 1.35..1.65; shrink to 0.7x -> 0.6..0.8.
    out = []
    for i in range(1, 16):
        t += dt
        frac = min(1.0, i / 10.0)
        half = 0.10 + 0.05 * frac
        out.extend(eng.update(t, pair_hands(0.50 - half, 0.50 + half)))
    moves = [c for c in out if c.kind == "two_move"]
    assert moves and 1.35 <= moves[-1].progress <= 1.65, moves

    out = []
    for i in range(1, 21):
        t += dt
        frac = min(1.0, i / 10.0)
        half = 0.15 - 0.08 * frac
        out.extend(eng.update(t, pair_hands(0.50 - half, 0.50 + half)))
    moves = [c for c in out if c.kind == "two_move"]
    assert moves and 0.60 <= moves[-1].progress <= 0.80, moves

    # 3. Move both hands right by 0.1 frame width with same distance -> summed dx ~ 0.1 * 1920 * CURSOR_GAIN, progress == 1.0.
    eng_m = InteractionEngine()
    t_m = 0.0
    for i in range(8):
        t_m = i * dt
        eng_m.update(t_m, pair_hands(0.35, 0.55))
    assert eng_m.state["phase"] == "two"
    out = []
    for i in range(1, 16):
        t_m += dt
        shift = 0.1 * (i / 15.0)
        out.extend(eng_m.update(t_m, pair_hands(0.35 + shift, 0.55 + shift)))
    moves = [c for c in out if c.kind == "two_move"]
    expected_dx = 0.1 * 1920.0 * CURSOR_GAIN
    got_dx = sum(c.dx for c in moves)
    assert abs(got_dx - expected_dx) <= 0.15 * expected_dx, (got_dx, expected_dx)
    assert moves and all(c.progress == 1.0 for c in moves), moves

    # 4. Open one hand's pinch (ratio 0.6) for > RELEASE_HOLD -> one two_end; re-pinching within TWO_COOLDOWN -> no click.
    out = []
    for _ in range(5):
        t_m += dt
        out.extend(eng_m.update(t_m, pair_hands(0.45, 0.65, r_gap=0.6)))
    assert _kinds(out) == ["two_end"], out
    end_t = out[0].t
    re_out: list[Command] = []
    while t_m < end_t + TWO_COOLDOWN - 0.1:
        t_m += dt
        phase_t = t_m - end_t
        if phase_t < 0.2:
            h = make_hand("point", cx=0.65, cy=0.55, size=0.22, side="Right")
        elif phase_t < 0.3:
            h = make_hand("point", cx=0.65, cy=0.55, size=0.22, side="Right", pinch_gap=0.0)
        else:
            h = make_hand("point", cx=0.65, cy=0.55, size=0.22, side="Right")
        re_out.extend(eng_m.update(t_m, (h,)))
    assert not any(c.kind == "click" for c in re_out), re_out

    # 5. Right hand pinches (one-hand pinch phase), left joins 0.3 s later -> two_start, no click, no press.
    eng_j = InteractionEngine()
    t_j = _point(eng_j, cx=0.60, cy=0.55)
    out = []
    for _ in range(9):
        t_j += dt
        out.extend(eng_j.update(t_j, (make_hand("point", cx=0.60, cy=0.55, size=0.22, side="Right", pinch_gap=0.0),)))
    assert eng_j.state["phase"] == "pinch"
    for _ in range(8):
        t_j += dt
        out.extend(eng_j.update(t_j, pair_hands(0.40, 0.60, r_pose="point", r_gap=0.0)))
    assert _kinds(out) == ["two_start"], out
    assert eng_j.state["phase"] == "two"

    # 6. Regression: one hand only, point then pinch-tap -> click; hold > DRAG_HOLD -> press.
    eng_r = InteractionEngine()
    t_r = _point(eng_r)
    t_r, c1 = _step_hand(eng_r, t_r + dt, 0.15, "point", size=0.28, pinch_gap=0.0)
    t_r, c2 = _step_hand(eng_r, t_r + dt, 0.20, "point", size=0.28)
    assert _kinds(c1 + c2) == ["click"], _kinds(c1 + c2)
    t_r, c3 = _step_hand(eng_r, t_r + dt, DRAG_HOLD + 0.1, "point", size=0.28, pinch_gap=0.0)
    assert _kinds(c3) == ["press"], _kinds(c3)

    # 7. One-hand pinch held 0.9 s while a second open hand is visible -> no press yet; held 1.3 s -> press.
    eng_w = InteractionEngine()
    t_w = _point(eng_w, cx=0.60, cy=0.55)
    t_start = t_w + dt
    out_90: list[Command] = []
    for i in range(int(0.9 / dt)):
        t_w = t_start + i * dt
        out_90.extend(eng_w.update(t_w, (
            make_hand("point", cx=0.60, cy=0.55, size=0.28, side="Right", pinch_gap=0.0),
            make_hand("open_palm", cx=0.30, cy=0.55, size=0.22, side="Left"),
        )))
    assert "press" not in _kinds(out_90), out_90
    assert eng_w.state["phase"] == "pinch"
    out_130: list[Command] = []
    while (t_w - t_start) < max(TWO_DRAG_HOLD + 0.1, 1.3):
        t_w += dt
        out_130.extend(eng_w.update(t_w, (
            make_hand("point", cx=0.60, cy=0.55, size=0.28, side="Right", pinch_gap=0.0),
            make_hand("open_palm", cx=0.30, cy=0.55, size=0.22, side="Left"),
        )))
    assert _kinds(out_130) == ["press"], out_130

    # 8. Hand lost (only one hand) during "two" -> two_end after RELEASE_HOLD.
    eng_l = InteractionEngine()
    t_l = 0.0
    for i in range(int((TWO_ENTER + 0.1) / dt) + 1):
        t_l = i * dt
        eng_l.update(t_l, pair_hands(0.40, 0.60))
    assert eng_l.state["phase"] == "two"
    out = []
    for _ in range(int((RELEASE_HOLD + 0.08) / dt) + 1):
        t_l += dt
        out.extend(eng_l.update(t_l, (make_hand("pinch", cx=0.60, cy=0.55, size=0.22, side="Right"),)))
    assert _kinds(out) == ["two_end"], out
    assert eng_l.state["phase"] == "idle"


def check_two_hands_controller() -> None:
    class TwoDesktop(FakeDesktop):
        def __init__(self, **kwargs) -> None:
            super().__init__(**kwargs)
            self.active_monitor = Rect(0, 0, 1920, 1080)
            self.active_target: tuple[int, str, str] | None = self.target

        def screen_bounds(self, monitor: str = "all") -> Rect:
            return self.active_monitor if monitor == "active" else BOUNDS

        def active_app(self) -> tuple[int, str, str] | None:
            return self.active_target

    air = FakeAir()
    desk = TwoDesktop(pointer_pos=(400, 300), target=(42, "code", "Notes"))
    pub: list[tuple[str, dict]] = []
    ctrl = AirController(air, desk, lambda n, p: pub.append((n, p)))  # type: ignore[arg-type]
    ctrl.process(Command("engage", 0.0))

    # two_start with window under cursor -> window_focus + grip.
    desk.runs.clear()
    desk.grips.clear()
    ctrl.process(Command("two_start", 1.0))
    assert desk.runs == [("window_focus", {"window": 42})], desk.runs
    assert desk.grips == [42], desk.grips

    # two_move(progress=1.5) on 800x600 at (100,100) -> 1200x900 centred on (500,400);
    # progress=0.2 -> clamped to 400x300 minimum.
    ctrl.process(Command("two_move", 1.1, dx=0.0, dy=0.0, progress=1.5))
    assert len(desk.places) == 1
    rect = desk.places[-1][1]
    assert (rect.width, rect.height) == (1200, 900) and rect.center == (500, 400), rect

    ctrl.process(Command("two_move", 1.2, dx=0.0, dy=0.0, progress=0.2))
    rect = desk.places[-1][1]
    assert (rect.width, rect.height) == (400, 300) and rect.center == (500, 400), rect

    # dx=50, dy=-20 -> centre moves by (50, -20).
    ctrl.process(Command("two_move", 1.3, dx=50.0, dy=-20.0, progress=1.0))
    rect = desk.places[-1][1]
    assert (rect.width, rect.height) == (800, 600) and rect.center == (550, 380), rect

    # two_end with final rect >= 95% of 1920x1080 monitor -> window_maximize.
    desk.runs.clear()
    ctrl.process(Command("two_move", 1.4, dx=0.0, dy=0.0, progress=2.4))
    ctrl.process(Command("two_end", 1.5))
    assert desk.runs == [("window_maximize", {"window": 7})], desk.runs

    # 30 queued two_move commands coalesce into one with summed dx/dy and newest progress.
    ctrl._queue.clear()
    for i in range(30):
        ctrl._enqueue(Command("two_move", 2.0 + i * 0.01, dx=2.0, dy=-1.0, progress=1.0 + i * 0.02))
    assert len(ctrl._queue) == 1, ctrl._queue
    merged = ctrl._queue[0]
    assert merged.kind == "two_move"
    assert merged.dx == 60.0 and merged.dy == -30.0
    assert abs(merged.progress - (1.0 + 29 * 0.02)) < 1e-9
    ctrl._queue.clear()

    # No window under cursor and no active app -> feedback ok False.
    desk.target = None
    desk.active_target = None
    pub.clear()
    ctrl.process(Command("two_start", 3.0))
    assert pub and pub[-1][1]["ok"] is False, pub


def main() -> int:
    for name, fn in list(globals().items()):
        if name.startswith("check_"):
            fn()
            print(f"  {name[6:]} OK")
    print("air engine OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
