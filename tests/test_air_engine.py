"""Air v2 interaction engine: ring table, pure engine phases, controller, and fixture replay."""

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
    DISENGAGE_AFTER,
    ENGAGE_HOLD,
    GRAB_HOLD,
    HINT_AFTER,
    HINT_EVERY,
    LEAN,
    RELEASE_HOLD,
    REWIND,
    RING_TIMEOUT,
    SCROLL_ENTER,
    SCROLL_STEP,
    THUMB_HOLD,
    THUMB_PALM,
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
) -> tuple[float, list[Command]]:
    out: list[Command] = []
    n = max(1, round(dur / dt))
    t = t0
    for i in range(n + 1):
        t = t0 + i * dt
        out.extend(eng.update(t, (make_hand(pose, cx=cx, cy=cy, size=size, pinch_gap=pinch_gap),)))
    return t, out


def _engage(eng: InteractionEngine, t0: float = 0.0, cx: float = 0.5, cy: float = 0.5) -> float:
    t, cmds = _step_hand(eng, t0, ENGAGE_HOLD + 0.05, "open_palm", cx=cx, cy=cy, size=0.28)
    assert any(c.kind == "engage" for c in cmds), f"failed to engage: {cmds}"
    assert eng.state["engaged"] is True and eng.state["phase"] == "pointing"
    return t


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


def check_phases_and_cursor() -> None:
    eng = InteractionEngine()
    assert eng.state == {
        "engaged": False, "zone": "none", "phase": "idle", "ring_hover": "", "grab_progress": 0.0,
    }

    # Moving palm never engages.
    t = 0.0
    for i in range(40):
        t = i * (1.0 / 30.0)
        cx = 0.3 + 0.015 * (i % 20)
        cmds = eng.update(t, (make_hand("open_palm", cx=cx, cy=0.5, size=0.28),))
        assert not any(c.kind == "engage" for c in cmds)
    assert eng.state["phase"] == "idle"

    # Still open palm in zone for ENGAGE_HOLD -> engage, phase pointing.
    t = _engage(eng, t + 0.05)

    # Moving open palm emits cursor deltas; fractional pixels accumulate.
    cursor_cmds: list[Command] = []
    for i in range(1, 20):
        t += 1.0 / 30.0
        cx = 0.5 + 0.01 * i
        cursor_cmds.extend(eng.update(t, (make_hand("open_palm", cx=cx, cy=0.5, size=0.28),)))
    total_dx = sum(c.dx for c in cursor_cmds if c.kind == "cursor")
    assert total_dx > 100.0, f"expected positive cursor movement, got {total_dx}"

    # Non-open pose freezes cursor; returning to open palm re-seeds filter with no jump.
    t += 0.05
    eng.update(t, (make_hand("fist", cx=0.69, cy=0.5, size=0.28),))
    assert eng.state["phase"] == "frozen"
    t += 0.05
    assert eng.update(t, (make_hand("fist", cx=0.30, cy=0.5, size=0.28),)) == []
    t += 0.05
    reseed_cmds = eng.update(t, (make_hand("open_palm", cx=0.30, cy=0.5, size=0.28),))
    assert eng.state["phase"] == "pointing"
    assert not any(c.kind == "cursor" for c in reseed_cmds), f"filter jumped on re-seed: {reseed_cmds}"

    # Clipped hand (touching frame edge) freezes cursor and does NOT start disengage clock.
    t += 0.05
    eng.update(t, (make_hand("open_palm", cx=0.02, cy=0.5, size=0.25),))
    assert eng.state["phase"] == "frozen"
    t += DISENGAGE_AFTER + 0.5
    cmds = eng.update(t, (make_hand("open_palm", cx=0.02, cy=0.5, size=0.25),))
    assert not any(c.kind == "disengage" for c in cmds)
    assert eng.state["engaged"] is True


def check_disengage_closes_open_phases() -> None:
    # Disengage while ring is open emits ring_cancel then disengage.
    eng = InteractionEngine()
    t = _engage(eng, 0.0)
    t += 0.05
    cmds = eng.update(t, (make_hand("pinch", cx=0.5, cy=0.5, size=0.28),))
    assert [c.kind for c in cmds] == ["ring_open"]
    assert eng.state["phase"] == "ring"
    # Jump straight past DISENGAGE_AFTER with no hand -> closes ring and disengages.
    t += DISENGAGE_AFTER + 0.1
    cmds = eng.update(t, ())
    assert [c.kind for c in cmds] == ["ring_cancel", "disengage"]
    assert eng.state["engaged"] is False and eng.state["phase"] == "idle"

    # Disengage while grab is active emits grab_end then disengage.
    t = _engage(eng, t + 0.1)
    t += 0.05
    eng.update(t, (make_hand("pinch", cx=0.5, cy=0.5, size=0.28),))
    t, cmds = _step_hand(eng, t + 0.05, GRAB_HOLD + 0.05, "pinch", cx=0.5, cy=0.5, size=0.28)
    assert any(c.kind == "grab_start" for c in cmds)
    assert eng.state["phase"] == "grab"
    t += DISENGAGE_AFTER + 0.1
    cmds = eng.update(t, ())
    assert [c.kind for c in cmds] == ["grab_end", "disengage"]
    assert eng.state["phase"] == "idle"


def check_rewind_and_ring_axis() -> None:
    # REWIND undoes cursor movement emitted in the last 0.10 s before pinch onset.
    eng = InteractionEngine()
    t = _engage(eng, 0.0, cx=0.4, cy=0.5)
    emitted: list[tuple[float, float, float]] = []
    for i in range(1, 16):
        t += 0.02
        cx = 0.4 + 0.01 * i
        for c in eng.update(t, (make_hand("open_palm", cx=cx, cy=0.5, size=0.28),)):
            if c.kind == "cursor":
                emitted.append((c.t, c.dx, c.dy))
    t += 0.02
    pinch_t = t
    want_dx = -sum(dx for ct, dx, _ in emitted if pinch_t - ct <= REWIND + 1e-9)
    want_dy = -sum(dy for ct, _, dy in emitted if pinch_t - ct <= REWIND + 1e-9)
    assert want_dx < 0.0
    cmds = eng.update(t, (make_hand("pinch", cx=0.55, cy=0.5, size=0.28),))
    assert [c.kind for c in cmds] == ["cursor", "ring_open"]
    assert abs(cmds[0].dx - want_dx) < 1e-6 and abs(cmds[0].dy - want_dy) < 1e-6

    # All four directions + diagonal rejected + release in centre cancels + lost hand cancels.
    cases = [
        ((-LEAN - 0.04, 0.0), "left"),
        ((LEAN + 0.04, 0.0), "right"),
        ((0.0, -LEAN - 0.04), "up"),
        ((0.0, LEAN + 0.04), "down"),
        ((LEAN + 0.04, LEAN + 0.04), ""),   # diagonal rejected by LEAN_AXIS
        ((0.0, 0.0), ""),                   # release in centre cancels
    ]
    for (dx, dy), want in cases:
        e = InteractionEngine()
        tt = _engage(e, 0.0, cx=0.5, cy=0.5)
        tt += 0.05
        e.update(tt, (make_hand("pinch", cx=0.5, cy=0.5, size=0.28),))
        tt += 0.05
        hover_cmds = e.update(tt, (make_hand("pinch", cx=0.5 + dx, cy=0.5 + dy, size=0.28),))
        if want:
            assert any(c.kind == "ring_hover" and c.direction == want for c in hover_cmds), hover_cmds
        # Hold release >= RELEASE_HOLD
        tt, rel_cmds = _step_hand(
            e, tt + 0.03, RELEASE_HOLD + 0.05, "open_palm", cx=0.5 + dx, cy=0.5 + dy, size=0.28,
        )
        if want:
            sels = [c for c in rel_cmds if c.kind == "ring_select"]
            assert len(sels) == 1 and sels[0].direction == want, rel_cmds
        else:
            assert any(c.kind == "ring_cancel" for c in rel_cmds), rel_cmds
            assert not any(c.kind == "ring_select" for c in rel_cmds), rel_cmds

    # Lost hand while leaning NEVER selects a direction (cancels after RELEASE_HOLD).
    e_lost = InteractionEngine()
    tt = _engage(e_lost, 0.0, cx=0.5, cy=0.5)
    tt += 0.05
    e_lost.update(tt, (make_hand("pinch", cx=0.5, cy=0.5, size=0.28),))
    tt += 0.05
    e_lost.update(tt, (make_hand("pinch", cx=0.66, cy=0.5, size=0.28),))
    assert e_lost.state["ring_hover"] == "right"
    tt += 0.05
    assert e_lost.update(tt, ()) == []
    tt += RELEASE_HOLD + 0.02
    lost_cmds = e_lost.update(tt, ())
    assert [c.kind for c in lost_cmds] == ["ring_cancel"]
    assert e_lost.state["phase"] == "frozen"


def check_grab_and_ring_timeout() -> None:
    # Hold pinch in centre -> ring_hover progress >= 0.05 steps -> grab_start -> grab_move -> grab_end.
    eng = InteractionEngine()
    t = _engage(eng, 0.0)
    t += 0.05
    eng.update(t, (make_hand("pinch", cx=0.5, cy=0.5, size=0.28),))
    t, cmds = _step_hand(eng, t + 0.03, GRAB_HOLD + 0.05, "pinch", cx=0.5, cy=0.5, size=0.28)
    hovers = [c for c in cmds if c.kind == "ring_hover"]
    assert len(hovers) >= 5 and all(c.direction == "" for c in hovers)
    assert hovers[-1].progress > hovers[0].progress
    assert any(c.kind == "grab_start" for c in cmds)
    assert eng.state["phase"] == "grab"

    t += 0.05
    mv = eng.update(t, (make_hand("pinch", cx=0.55, cy=0.52, size=0.28),))
    assert any(c.kind == "grab_move" and c.dx > 0.0 and c.dy > 0.0 for c in mv), mv

    t, rel = _step_hand(eng, t + 0.03, RELEASE_HOLD + 0.05, "fist", cx=0.55, cy=0.52, size=0.28)
    assert any(c.kind == "grab_end" for c in rel)
    assert eng.state["phase"] == "frozen"

    # Tight pinch (ratio < PINCH_TIGHT = 0.15) starts even with curled index (reach < PINCH_REACH),
    # stays held while ratio <= PINCH_SLIP (0.24), and releases when curled & ratio > PINCH_SLIP.
    eng3 = InteractionEngine()
    t = _engage(eng3, 0.0, cx=0.5, cy=0.5)
    t += 0.05
    # Curled fist with ratio 0.10 (< PINCH_TIGHT) -> opens ring.
    cmds = eng3.update(t, (make_hand("fist", cx=0.5, cy=0.5, size=0.28, pinch_gap=0.10),))
    assert [c.kind for c in cmds] == ["ring_open"], cmds
    # Curled fist with ratio 0.20 (<= PINCH_SLIP) held > RELEASE_HOLD -> stays in ring / enters grab.
    t, cmds = _step_hand(
        eng3, t + 0.03, GRAB_HOLD + 0.05, "fist", cx=0.5, cy=0.5, size=0.28, pinch_gap=0.20,
    )
    assert any(c.kind == "grab_start" for c in cmds), cmds
    # Curled fist with ratio 0.28 (> PINCH_SLIP) held >= RELEASE_HOLD -> releases grab.
    t, cmds = _step_hand(
        eng3, t + 0.03, RELEASE_HOLD + 0.05, "fist", cx=0.5, cy=0.5, size=0.28, pinch_gap=0.28,
    )
    assert any(c.kind == "grab_end" for c in cmds), cmds

    # RING_TIMEOUT: stay in deadzone (between HOLD_RADIUS and LEAN) for RING_TIMEOUT -> cancel + ignore pinch.
    eng2 = InteractionEngine()
    t = _engage(eng2, 0.0, cx=0.5, cy=0.5)
    t += 0.05
    eng2.update(t, (make_hand("pinch", cx=0.5, cy=0.5, size=0.28),))
    t, cmds = _step_hand(eng2, t + 0.05, RING_TIMEOUT + 0.1, "pinch", cx=0.56, cy=0.5, size=0.28)
    assert any(c.kind == "ring_cancel" for c in cmds), cmds
    assert eng2.state["phase"] == "frozen"
    # Keeping the same pinch held must NOT re-open the ring.
    t += 0.05
    assert eng2.update(t, (make_hand("pinch", cx=0.56, cy=0.5, size=0.28),)) == []
    # Releasing and pinching again opens a fresh ring.
    t += 0.05
    eng2.update(t, (make_hand("open_palm", cx=0.56, cy=0.5, size=0.28),))
    t += 0.05
    reopen = eng2.update(t, (make_hand("pinch", cx=0.56, cy=0.5, size=0.28),))
    assert any(c.kind == "ring_open" for c in reopen)


def check_scroll_thumbs_and_hints() -> None:
    # Scroll: V held SCROLL_ENTER -> scroll +1 up / -1 down per SCROLL_STEP; non-V 0.2 s -> frozen.
    eng = InteractionEngine()
    t = _engage(eng, 0.0)
    t, cmds = _step_hand(eng, t + 0.05, SCROLL_ENTER + 0.05, "victory", cx=0.5, cy=0.5, size=0.28)
    assert eng.state["phase"] == "scroll"
    scrolls: list[Command] = []
    # Move hand up by 2.5 * SCROLL_STEP -> 2 up (+1) scrolls.
    for i in range(1, 11):
        t += 0.03
        cy = 0.5 - (2.5 * SCROLL_STEP) * (i / 10.0)
        scrolls.extend(eng.update(t, (make_hand("victory", cx=0.5, cy=cy, size=0.28),)))
    assert [c.steps for c in scrolls if c.kind == "scroll"] == [1, 1]
    # Move hand down by 2.5 * SCROLL_STEP -> 2 down (-1) scrolls.
    scrolls.clear()
    y_start = 0.5 - 2.5 * SCROLL_STEP
    for i in range(1, 11):
        t += 0.03
        cy = y_start + (2.5 * SCROLL_STEP) * (i / 10.0)
        scrolls.extend(eng.update(t, (make_hand("victory", cx=0.5, cy=cy, size=0.28),)))
    assert [c.steps for c in scrolls if c.kind == "scroll"] == [-1, -1]
    # Drop V pose for 0.2 s -> frozen.
    t, _ = _step_hand(eng, t + 0.03, 0.25, "fist", cx=0.5, cy=0.5, size=0.28)
    assert eng.state["phase"] == "frozen"

    # Thumbs: works even while idle and outside zone (palm >= THUMB_PALM), fires once per hold.
    eng_t = InteractionEngine()
    t, cmds = _step_hand(eng_t, 0.0, THUMB_HOLD + 0.3, "thumbs_up", cx=0.5, cy=0.6, size=THUMB_PALM + 0.02)
    intents = [c for c in cmds if c.kind == "intent"]
    assert len(intents) == 1 and intents[0].text == "confirm"
    # Release and show thumbs_down -> deny once.
    t += 0.05
    eng_t.update(t, (make_hand("fist", cx=0.5, cy=0.6, size=0.15),))
    t, cmds = _step_hand(eng_t, t + 0.05, THUMB_HOLD + 0.3, "thumbs_down", cx=0.5, cy=0.6, size=0.15)
    intents = [c for c in cmds if c.kind == "intent"]
    assert len(intents) == 1 and intents[0].text == "deny"

    # Far-hand hint cadence: fires after HINT_AFTER (1.5 s), not again until HINT_EVERY (10.0 s).
    eng_h = InteractionEngine()
    t, cmds1 = _step_hand(eng_h, 0.0, HINT_AFTER + 0.1, "open_palm", cx=0.5, cy=0.5, size=ZONE_PALM - 0.05)
    hints1 = [c for c in cmds1 if c.kind == "hint"]
    assert len(hints1) == 1 and hints1[0].text == "Move your hand closer to the camera."
    assert eng_h.state["zone"] == "far"
    t, cmds2 = _step_hand(eng_h, t + 0.1, 5.0, "open_palm", cx=0.5, cy=0.5, size=0.15, dt=0.2)
    assert not any(c.kind == "hint" for c in cmds2)
    t, cmds3 = _step_hand(eng_h, t + 0.1, HINT_EVERY - 4.0, "open_palm", cx=0.5, cy=0.5, size=0.15, dt=0.2)
    assert len([c for c in cmds3 if c.kind == "hint"]) == 1


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


def check_air_service_commands() -> None:
    # AirService feeds InteractionEngine and dispatches Commands + updates engine_state().
    script = [(make_hand("open_palm", cx=0.5, cy=0.5, size=0.28),) for _ in range(32)]
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


def check_replay_session1() -> None:
    path = FIXTURES / "session1-free.jsonl.gz"
    if not path.is_file():
        print(
            "replay skipped: tests/fixtures/air/session1-free.jsonl.gz "
            "not found (local recordings, not in git)"
        )
        return
    emitted = _replay(path)
    normal = [c for lbl, _, c in emitted if lbl == "normal"]
    engages = [c for c in normal if c.kind == "engage"]
    triggers = [c for c in normal if c.kind in ("ring_select", "grab_start")]
    assert len(engages) == 0, f"session1 normal engages={len(engages)}: {engages}"
    assert len(triggers) <= 1, f"session1 normal ring_select/grab_start={len(triggers)}: {triggers}"


def _cue_direction(cue: str) -> str:
    up = cue.upper()
    for d in ("LEFT", "RIGHT", "UP", "DOWN"):
        if d in up:
            return d.lower()
    return ""


def check_replay_session2() -> None:
    path = FIXTURES / "session2-cued.jsonl.gz"
    if not path.is_file():
        print(
            "replay skipped: tests/fixtures/air/session2-cued.jsonl.gz "
            "not found (local recordings, not in git)"
        )
        return
    emitted = _replay(path)

    # 1. normal: ignore commands before the first disengage inside normal; zero of every kind after it.
    normal_cmds = [(cue, c) for lbl, cue, c in emitted if lbl == "normal"]
    disengage_idx = next((i for i, (_, c) in enumerate(normal_cmds) if c.kind == "disengage"), None)
    assert disengage_idx is not None, f"session2 normal: no disengage emitted: {normal_cmds}"
    after_disengage = normal_cmds[disengage_idx + 1 :]
    assert not after_disengage, (
        f"session2 normal commands after disengage ({len(after_disengage)}): {after_disengage}"
    )

    # 2. near_still: engage within the label, and cursor moves <= 40 px over 5 s after engage
    still_cmds = [c for lbl, _, c in emitted if lbl == "near_still"]
    still_engages = [c for c in still_cmds if c.kind == "engage"]
    assert still_engages, "session2 near_still: no engage"
    eng_t = still_engages[0].t
    still_move = sum(
        math.hypot(c.dx, c.dy) for c in still_cmds
        if c.kind == "cursor" and eng_t <= c.t <= eng_t + 5.0
    )
    assert still_move <= 40.0, f"session2 near_still cursor drift={still_move:.1f} px"

    # 3. near_move: cursor path >= 300 px
    move_path = sum(
        math.hypot(c.dx, c.dy) for lbl, _, c in emitted
        if lbl == "near_move" and c.kind == "cursor"
    )
    assert move_path >= 300.0, f"session2 near_move cursor path={move_path:.1f} px"

    # 4. pinch_tap: >= 5 ring_open and no ring_select
    tap_cmds = [c for lbl, _, c in emitted if lbl == "pinch_tap"]
    tap_opens = [c for c in tap_cmds if c.kind == "ring_open"]
    tap_selects = [c for c in tap_cmds if c.kind == "ring_select"]
    assert len(tap_opens) >= 5, f"session2 pinch_tap ring_open={len(tap_opens)}"
    assert len(tap_selects) == 0, f"session2 pinch_tap ring_select={tap_selects}"

    # 5. pinch_lean: >= 6 of 8 equal cue direction; tolerate at most one mismatch (first RIGHT)
    opposite = {"left": "right", "right": "left", "up": "down", "down": "up"}
    open_cue = ""
    lean_matches = 0
    lean_mismatches: list[tuple[str, str]] = []
    for lbl, cue, c in emitted:
        if lbl != "pinch_lean":
            continue
        if c.kind == "ring_open":
            open_cue = cue
        elif c.kind == "ring_select":
            want = _cue_direction(open_cue or cue)
            if c.direction == want:
                lean_matches += 1
            else:
                lean_mismatches.append((want, c.direction))
    assert lean_matches >= 6, f"session2 pinch_lean matches={lean_matches}, mismatches={lean_mismatches}"
    assert len(lean_mismatches) <= 1, f"session2 pinch_lean mismatches={lean_mismatches}"
    for want, got in lean_mismatches:
        if got == opposite.get(want):
            assert want == "right", f"unexpected opposite mismatch: want={want}, got={got}"

    # 6. pinch_hold: >= 2 grab_start, each followed by grab_end (even if release crosses into next label)
    grab_start_idxs = [
        i for i, (lbl, _, c) in enumerate(emitted)
        if lbl == "pinch_hold" and c.kind == "grab_start"
    ]
    assert len(grab_start_idxs) >= 2, f"session2 pinch_hold grab_start={len(grab_start_idxs)}"
    for idx in grab_start_idxs:
        nxt = next(
            (c.kind for _, _, c in emitted[idx + 1 :] if c.kind in ("grab_start", "grab_end")),
            None,
        )
        assert nxt == "grab_end", f"session2 pinch_hold unclosed grab at {emitted[idx][2]} (next={nxt})"

    # 7. thumbs: >= 1 confirm in THUMBS UP and >= 1 deny in THUMBS DOWN
    up_confirms = [
        c for lbl, cue, c in emitted
        if lbl == "thumbs" and "THUMBS UP" in cue.upper() and c.kind == "intent" and c.text == "confirm"
    ]
    down_denies = [
        c for lbl, cue, c in emitted
        if lbl == "thumbs" and "THUMBS DOWN" in cue.upper() and c.kind == "intent" and c.text == "deny"
    ]
    assert len(up_confirms) >= 1, f"session2 thumbs confirm={len(up_confirms)}"
    assert len(down_denies) >= 1, f"session2 thumbs deny={len(down_denies)}"

    # 8. v_scroll: >= 3 scroll commands
    scrolls = [c for lbl, _, c in emitted if lbl == "v_scroll" and c.kind == "scroll"]
    assert len(scrolls) >= 3, f"session2 v_scroll scrolls={len(scrolls)}"


def main() -> int:
    for name, fn in list(globals().items()):
        if name.startswith("check_"):
            fn()
            print(f"  {name[6:]} OK")
    print("air engine OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
