"""Air control (Stage D1): pure geometry, Arming, AirController, computer grip/place and settings."""

from __future__ import annotations

import _setup  # noqa: F401
import sys

from lura.modules.air import (
    AirController,
    AirService,
    AirState,
    Arming,
    GestureEvent,
    HandView,
)
from lura.modules.air.domain import (
    ARM_IDLE,
    drag_rect,
    edge_snap,
    moved,
    spread_rect,
)
from lura.modules.computer import ActionError, ActionResult, ComputerService, Rect, WindowGrip
from lura.modules.computer.domain import Monitor, Window, snap_rect, work_area
from lura.modules.settings import Settings
from lura.shared import events

BOUNDS = Rect(0, 0, 3840, 1080)


class FakeDesktop:
    """In-memory DesktopPort recording run/grip/place calls."""

    def __init__(self, *, maximized: bool = False) -> None:
        self.maximized = maximized
        self.runs: list[tuple[str, dict | None]] = []
        self.grips: list[str | int] = []
        self.places: list[tuple[WindowGrip, Rect]] = []
        self.place_error: Exception | None = None
        self.grip_error: Exception | None = None

    def run(self, name: str, args: dict | None = None, *, confirmed: bool = False) -> ActionResult:
        self.runs.append((name, args))
        return ActionResult(True, f"ran {name}")

    def grip(self, window: str | int = "active") -> WindowGrip:
        self.grips.append(window)
        if self.grip_error is not None:
            raise self.grip_error
        return WindowGrip(7, "Notes", Rect(100, 100, 800, 600), BOUNDS)

    def place(self, grip: WindowGrip, rect: Rect) -> None:
        if self.place_error is not None:
            raise self.place_error
        self.places.append((grip, rect))

    def is_maximized(self, window: str | int = "active") -> bool:
        return self.maximized

    def screen_bounds(self) -> Rect:
        return BOUNDS


class FakeAir:
    """In-memory AirService double with a settable snapshot and subscribe counter."""

    def __init__(self) -> None:
        self.subscribers: list = []
        self.state = AirState(camera="on", detail="FakeCam")

    def subscribe(self, fn) -> None:
        self.subscribers.append(fn)

    def snapshot(self) -> AirState:
        return self.state


def _ev(
    gesture: str,
    t: float,
    hand: str = "right",
    x: float = 0.5,
    y: float = 0.5,
    conf: float = 0.95,
    data: dict | None = None,
) -> GestureEvent:
    return GestureEvent(gesture, hand, conf, x, y, t, dict(data) if data else {})


def _make_controller(
    maximized: bool = False,
) -> tuple[AirController, FakeAir, FakeDesktop, list[tuple[str, dict]], list[float]]:
    air = FakeAir()
    desk = FakeDesktop(maximized=maximized)
    published: list[tuple[str, dict]] = []
    now = [0.0]

    def pub(name: str, payload: dict) -> None:
        published.append((name, payload))

    ctrl = AirController(air, desk, pub, clock=lambda: now[0])  # type: ignore[arg-type]
    return ctrl, air, desk, published, now


def check_pure_geometry() -> None:
    start = Rect(100, 100, 800, 600)
    r = drag_rect(start, (0.5, 0.5), (0.6, 0.5), BOUNDS)
    assert r == Rect(100 + 614, 100, 800, 600), f"got {r}"

    assert edge_snap(Rect(0, 100, 800, 600), BOUNDS) == "left"
    assert edge_snap(Rect(1500, 100, 800, 600), BOUNDS) is None
    assert edge_snap(Rect(3040, 100, 800, 600), BOUNDS) == "right"

    big = spread_rect(start, 2.0)
    assert big.width == 1600 and big.height == 1200
    assert big.center == start.center

    small = spread_rect(start, 0.1)
    assert small.width == 400 and small.height == 300
    assert small.center == start.center

    tiny = spread_rect(Rect(100, 100, 500, 400), 0.1)
    assert tiny.width == 320 and tiny.height == 240
    assert tiny.center == Rect(100, 100, 500, 400).center

    assert moved(Rect(0, 0, 100, 100), Rect(3, 3, 103, 103)) is False
    assert moved(Rect(0, 0, 100, 100), Rect(4, 0, 100, 100)) is True
    assert moved(Rect(0, 0, 100, 100), Rect(0, 0, 104, 100)) is True


def check_arming() -> None:
    arm = Arming()
    assert not arm.armed(0.0)
    arm.on_event(_ev("swipe_left", 1.0))
    assert not arm.armed(1.0)

    arm.on_event(_ev("pose", 2.0, data={"pose": "open_palm"}))
    assert arm.armed(2.0)
    assert arm.armed(2.0 + ARM_IDLE - 0.01)
    assert not arm.armed(2.0 + ARM_IDLE)

    # Events extend while armed.
    arm.on_event(_ev("pose", 10.0, data={"pose": "open_palm"}))
    arm.on_event(_ev("hand_enter", 15.0))
    assert arm.armed(15.0 + ARM_IDLE - 0.1)
    assert not arm.armed(15.0 + ARM_IDLE)

    # Fist disarms after FIST_HOLD (0.5 s).
    arm.on_event(_ev("pose", 30.0, data={"pose": "open_palm"}))
    assert arm.armed(31.0)
    arm.on_event(_ev("pose", 32.0, data={"pose": "fist"}))
    assert arm.armed(32.0)
    assert arm.armed(32.49)
    assert not arm.armed(32.5)

    # Fist cancelled by a pinch_start of the same hand within 0.5 s -> stays armed.
    arm.on_event(_ev("pose", 40.0, data={"pose": "open_palm"}))
    arm.on_event(_ev("pose", 41.0, hand="right", data={"pose": "fist"}))
    arm.on_event(_ev("pinch_start", 41.3, hand="right"))
    assert arm.armed(41.6)


def check_swipes_and_arming_gate() -> None:
    ctrl, _, desk, pub, _ = _make_controller()
    # Not armed: swipe ignored.
    ctrl.process(_ev("swipe_left", 1.0))
    assert desk.runs == []

    # Arm with open palm, then all four swipes map to expected calls.
    ctrl.process(_ev("pose", 2.0, data={"pose": "open_palm"}))
    for i, g in enumerate(("swipe_left", "swipe_right", "swipe_up", "swipe_down"), start=1):
        ctrl.process(_ev(g, 2.0 + i * 0.5))
    assert desk.runs == [
        ("workspace_switch", {"target": "prev"}),
        ("workspace_switch", {"target": "next"}),
        ("workspace_overview", {}),
        ("show_desktop", {}),
    ]

    # Low confidence (0.6) -> ignored.
    desk.runs.clear()
    ctrl.process(_ev("swipe_left", 5.0, conf=0.60))
    assert desk.runs == []

    # Check AIR_ACTION payload keys.
    assert pub
    for name, payload in pub:
        assert name == events.AIR_ACTION
        assert set(payload.keys()) == {"action", "ok", "message"}


def check_drag_snap_lost_and_tap() -> None:
    ctrl, air, desk, pub, _ = _make_controller()
    ctrl.process(_ev("pose", 0.0, data={"pose": "open_palm"}))

    # Pinch and move 0.02 (< DRAG_START 0.03) -> no grip.
    ctrl.process(_ev("pinch_start", 1.0, x=0.5, y=0.5))
    air.state = AirState(
        camera="on",
        hands=(HandView("right", 0.52, 0.5, "pinch", True, 0.95),),
    )
    ctrl.tick(1.05)
    assert desk.grips == []
    assert desk.places == []

    # Move 0.05 (>= DRAG_START) -> one grip, one place.
    air.state = AirState(
        camera="on",
        hands=(HandView("right", 0.55, 0.5, "pinch", True, 0.95),),
    )
    ctrl.tick(1.10)
    assert len(desk.grips) == 1
    assert len(desk.places) == 1

    # Tiny further move (< 4 px: 0.0002 * 3840 * 1.6 = 1.2 px) -> no extra place.
    air.state = AirState(
        camera="on",
        hands=(HandView("right", 0.5502, 0.5, "pinch", True, 0.95),),
    )
    ctrl.tick(1.15)
    assert len(desk.places) == 1

    # Release at x=0.5 -> no snap, feedback window_move.
    ctrl.process(_ev("pinch_end", 1.2, x=0.5, y=0.5))
    assert desk.runs == []
    assert pub[-1][1]["action"] == "window_move"
    assert pub[-1][1]["ok"] is True
    assert "Notes" in pub[-1][1]["message"]
    assert not ctrl.state()["dragging"]

    # New drag pushing window to the right screen edge -> window_snap right.
    ctrl.process(_ev("pinch_start", 2.0, x=0.2, y=0.5))
    air.state = AirState(
        camera="on",
        hands=(HandView("right", 0.25, 0.5, "pinch", True, 0.95),),
    )
    ctrl.tick(2.05)  # grips at x=0.25
    air.state = AirState(
        camera="on",
        hands=(HandView("right", 0.75, 0.5, "pinch", True, 0.95),),
    )
    ctrl.tick(2.08)  # moves window +3072 px -> right edge >= 3840
    ctrl.process(_ev("pinch_end", 2.1, x=0.75, y=0.5))
    assert desk.runs == [("window_snap", {"window": 7, "where": "right"})]
    assert pub[-1][1]["action"] == "window_snap"

    # Lost pinch after pushing window to right edge -> no snap, drag cleared.
    desk.runs.clear()
    pub.clear()
    ctrl.process(_ev("pinch_start", 3.0, x=0.2, y=0.5))
    air.state = AirState(
        camera="on",
        hands=(HandView("right", 0.25, 0.5, "pinch", True, 0.95),),
    )
    ctrl.tick(3.05)
    air.state = AirState(
        camera="on",
        hands=(HandView("right", 0.75, 0.5, "pinch", True, 0.95),),
    )
    ctrl.tick(3.08)
    assert ctrl.state()["dragging"] is True
    ctrl.process(_ev("pinch_end", 3.1, x=0.75, y=0.5, data={"lost": True}))
    assert desk.runs == []
    assert pub == []
    assert ctrl.state()["dragging"] is False

    # Quick pinch without movement (tap) -> no grip at all.
    desk.grips.clear()
    ctrl.process(_ev("pinch_start", 4.0, x=0.5, y=0.5))
    air.state = AirState(
        camera="on",
        hands=(HandView("right", 0.5, 0.5, "pinch", True, 0.95),),
    )
    ctrl.tick(4.05)
    ctrl.process(_ev("pinch_end", 4.1, x=0.5, y=0.5))
    assert desk.grips == []


def check_double_pinch() -> None:
    # Maximized -> window_restore, no grip.
    ctrl, _, desk, pub, _ = _make_controller(maximized=True)
    ctrl.process(_ev("pose", 0.0, data={"pose": "open_palm"}))
    ctrl.process(_ev("pinch_start", 1.0))
    ctrl.process(_ev("double_pinch", 1.0))
    assert desk.grips == []
    assert desk.runs == [("window_restore", None)]
    assert pub[-1][1]["action"] == "window_restore"

    # Not maximized -> window_maximize, no grip.
    desk.maximized = False
    desk.runs.clear()
    ctrl.process(_ev("pinch_start", 2.0))
    ctrl.process(_ev("double_pinch", 2.0))
    assert desk.grips == []
    assert desk.runs == [("window_maximize", None)]
    assert pub[-1][1]["action"] == "window_maximize"


def check_spread() -> None:
    ctrl, air, desk, pub, _ = _make_controller()
    ctrl.process(_ev("pose", 0.0, data={"pose": "open_palm"}))
    ctrl.process(_ev("spread_start", 1.0, hand="both"))
    assert len(desk.grips) == 1
    assert ctrl.state()["resizing"] is True

    air.state = AirState(camera="on", spread_scale=1.5)
    ctrl.tick(1.05)
    assert len(desk.places) == 1
    grip, rect = desk.places[0]
    assert rect.width == 1200 and rect.height == 900
    assert rect.center == grip.start.center

    ctrl.process(_ev("spread_end", 1.2, hand="both", data={"scale": 1.5}))
    assert ctrl.state()["resizing"] is False
    assert pub[-1][1]["action"] == "window_resize"
    assert pub[-1][1]["ok"] is True


def check_fist_during_drag() -> None:
    ctrl, air, desk, pub, now = _make_controller()
    ctrl.process(_ev("pose", 0.0, data={"pose": "open_palm"}))
    ctrl.process(_ev("pinch_start", 1.0, x=0.5, y=0.5))
    air.state = AirState(
        camera="on",
        hands=(HandView("right", 0.6, 0.5, "pinch", True, 0.95),),
    )
    ctrl.tick(1.05)
    assert ctrl.state()["dragging"] is True
    places_before = len(desk.places)
    assert [p["action"] for _, p in pub] == ["welcome"]
    pub.clear()

    # Fist followed within 0.3 s by a pinch_start -> stays armed, drag continues.
    now[0] = 1.1
    ctrl.process(_ev("pose", 1.1, data={"pose": "fist"}))
    assert ctrl.state()["dragging"] is True
    assert ctrl.state()["armed"] is True
    assert pub == []

    now[0] = 1.4
    ctrl.process(_ev("pinch_start", 1.4, x=0.6, y=0.5))
    now[0] = 1.7
    air.state = AirState(
        camera="on",
        hands=(HandView("right", 0.65, 0.5, "pinch", True, 0.95),),
    )
    ctrl.tick(1.7)
    assert ctrl.state()["armed"] is True
    assert ctrl.state()["dragging"] is True
    assert len(desk.places) == places_before + 1
    assert pub == []

    # Fist held (no other event) -> disarmed after 0.5 s via tick, one disarm feedback.
    places_before = len(desk.places)
    now[0] = 2.0
    ctrl.process(_ev("pose", 2.0, data={"pose": "fist"}))
    now[0] = 2.3
    ctrl.tick(2.3)
    assert ctrl.state()["armed"] is True
    assert ctrl.state()["dragging"] is True
    assert pub == []

    now[0] = 2.55
    ctrl.tick(2.55)
    assert ctrl.state()["dragging"] is False
    assert ctrl.state()["armed"] is False
    assert [p["action"] for _, p in pub] == ["disarm"]

    # Further ticks & hand movement cause no more places and no duplicate disarm feedback.
    now[0] = 2.7
    air.state = AirState(
        camera="on",
        hands=(HandView("right", 0.8, 0.5, "pinch", True, 0.95),),
    )
    ctrl.tick(2.7)
    assert len(desk.places) == places_before
    assert [p["action"] for _, p in pub] == ["disarm"]


def check_place_error_recovery() -> None:
    ctrl, air, desk, pub, _ = _make_controller()
    ctrl.process(_ev("pose", 0.0, data={"pose": "open_palm"}))
    desk.place_error = RuntimeError("X11 disconnected")
    ctrl.process(_ev("pinch_start", 1.0, x=0.5, y=0.5))
    air.state = AirState(
        camera="on",
        hands=(HandView("right", 0.6, 0.5, "pinch", True, 0.95),),
    )
    ctrl.tick(1.05)
    assert ctrl.state()["dragging"] is False
    assert pub[-1][1]["ok"] is False
    assert set(pub[-1][1].keys()) == {"action", "ok", "message"}

    # Controller keeps working afterwards.
    desk.place_error = None
    ctrl.process(_ev("swipe_right", 2.0))
    assert desk.runs == [("workspace_switch", {"target": "next"})]


def check_queue_and_lifecycle() -> None:
    ctrl, air, _, _, _ = _make_controller()
    for i in range(100):
        ctrl._enqueue(_ev("hand_enter", float(i)))
    assert ctrl._queue.qsize() <= 64

    ctrl.start()
    ctrl.stop()
    ctrl.start()
    ctrl.stop()
    assert len(air.subscribers) == 1

    # AirService.set_camera works when stopped.
    svc = AirService(None)
    svc.set_camera("mobile")
    assert svc._camera_wanted == "mobile"


class MinimalBackend:
    """Minimal Backend double for testing ComputerService.grip/place/is_maximized/screen_bounds."""

    def __init__(self) -> None:
        self.calls: list[tuple] = []
        self.windows_calls = 0
        self._mons = [
            Monitor(0, "eDP-1", Rect(0, 0, 1920, 1080), True),
            Monitor(1, "HDMI-1", Rect(1920, 0, 1920, 1080), False),
        ]
        self._workarea = Rect(0, 28, 3840, 1052)
        self._wins = [
            Window(10, "Browser", "firefox", 1001, 0, Rect(0, 28, 1920, 1052), True, False, "NORMAL"),
            Window(11, "Notes", "code", 1002, 0, Rect(200, 200, 800, 600), False, False, "NORMAL"),
            Window(99, "Desktop", "gjs", 2000, -1, Rect(0, 0, 1920, 1080), False, False, "DESKTOP"),
        ]
        self._active: int | None = 10

    def own_pids(self) -> set[int]:
        return {9999}

    def windows(self) -> list[Window]:
        self.windows_calls += 1
        return list(self._wins)

    def active_window(self) -> int | None:
        return self._active

    def monitors(self) -> list[Monitor]:
        return list(self._mons)

    def workarea(self) -> Rect | None:
        return self._workarea

    def set_maximized(self, wid: int, on: bool) -> None:
        self.calls.append(("set_maximized", wid, on))

    def move_resize(self, wid: int, frame: Rect) -> None:
        self.calls.append(("move_resize", wid, frame))


def check_computer_fast_path() -> None:
    be = MinimalBackend()
    svc = ComputerService(be)  # type: ignore[arg-type]

    assert svc.screen_bounds() == Rect(0, 0, 3840, 1080)
    assert svc.is_maximized() is True
    assert svc.is_maximized(11) is False

    # grip on a maximized window un-maximizes and centres it on its monitor work area.
    grip = svc.grip()
    expected_center = snap_rect(work_area(be._mons[0], be._workarea), "center")
    assert grip == WindowGrip(10, "Browser", expected_center, Rect(0, 0, 3840, 1080))
    assert be.calls == [
        ("set_maximized", 10, False),
        ("move_resize", 10, expected_center),
    ]

    # place sends exactly one move_resize and makes no windows() call.
    be.calls.clear()
    scans_before = be.windows_calls
    svc.place(grip, Rect(400, 200, 800, 600))
    assert be.windows_calls == scans_before
    assert be.calls == [("move_resize", 10, Rect(400, 200, 800, 600))]

    # grip on a non-target window raises ActionError.
    try:
        svc.grip(99)
    except ActionError:
        pass
    else:
        raise AssertionError("grip on non-target window should raise ActionError")


def check_settings_defaults() -> None:
    s = Settings()
    assert s.air_enabled is False
    assert s.air_camera == ""


def main() -> int:
    for name, fn in list(globals().items()):
        if name.startswith("check_"):
            fn()
            print(f"  {name[6:]} OK")
    print("air control OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
