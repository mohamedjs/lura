"""Air control: pure geometry, place error recovery, Command queue/lifecycle, computer fast path, settings."""

from __future__ import annotations

import _setup  # noqa: F401
import sys
import time

from lura.modules.air import AirController, AirService, AirState, Command
from lura.modules.air.domain import edge_snap, moved
from lura.modules.computer import ActionError, ActionResult, ComputerService, Rect, WindowGrip
from lura.modules.computer.domain import Monitor, Window, snap_rect, work_area
from lura.modules.settings import Settings
from lura.shared import events

BOUNDS = Rect(0, 0, 3840, 1080)


class FakeDesktop:
    """In-memory DesktopPort recording run/grip/place/pointer calls."""

    def __init__(
        self,
        *,
        maximized: bool = False,
        pointer_pos: tuple[int, int] = (500, 400),
        target: tuple[int, str, str] | None = (7, "code", "Notes"),
    ) -> None:
        self.maximized = maximized
        self.pointer_pos = pointer_pos
        self.target = target
        self.runs: list[tuple[str, dict | None]] = []
        self.grips: list[str | int] = []
        self.places: list[tuple[WindowGrip, Rect]] = []
        self.pointers: list[tuple[int, int]] = []
        self.pointer_reads = 0
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

    def screen_bounds(self, monitor: str = "all") -> Rect:
        return BOUNDS

    def active_app(self) -> tuple[int, str, str] | None:
        return self.target

    def window_at(self, x: int, y: int) -> tuple[int, str, str] | None:
        return self.target

    def pointer(self) -> tuple[int, int]:
        self.pointer_reads += 1
        return self.pointer_pos

    def pointer_to(self, x: int, y: int) -> None:
        self.pointer_pos = (x, y)
        self.pointers.append((x, y))


class FakeAir:
    """In-memory AirService double with settable snapshot, engine state and command subscribers."""

    def __init__(self) -> None:
        self.subscribers: list = []
        self.cmd_subscribers: list = []
        self.state = AirState(camera="on", detail="FakeCam")
        self.eng_state: dict = {
            "engaged": False,
            "zone": "none",
            "phase": "idle",
            "ring_hover": "",
            "grab_progress": 0.0,
        }

    def subscribe(self, fn) -> None:
        self.subscribers.append(fn)

    def subscribe_commands(self, fn) -> None:
        self.cmd_subscribers.append(fn)

    def snapshot(self) -> AirState:
        return self.state

    def engine_state(self) -> dict:
        return dict(self.eng_state)


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
    assert edge_snap(Rect(0, 100, 800, 600), BOUNDS) == "left"
    assert edge_snap(Rect(1500, 100, 800, 600), BOUNDS) is None
    assert edge_snap(Rect(3040, 100, 800, 600), BOUNDS) == "right"

    assert moved(Rect(0, 0, 100, 100), Rect(3, 3, 103, 103)) is False
    assert moved(Rect(0, 0, 100, 100), Rect(4, 0, 100, 100)) is True
    assert moved(Rect(0, 0, 100, 100), Rect(0, 0, 104, 100)) is True


def check_place_error_recovery() -> None:
    ctrl, _, desk, pub, _ = _make_controller()
    ctrl.process(Command("engage", 0.0))
    desk.place_error = RuntimeError("X11 disconnected")

    ctrl.process(Command("grab_start", 1.0))
    assert ctrl._grab is not None
    ctrl.process(Command("grab_move", 1.05, dx=20.0, dy=0.0))
    assert ctrl._grab is None
    assert pub[-1][1]["ok"] is False
    assert set(pub[-1][1].keys()) == {"action", "ok", "message"}

    # Controller keeps working afterwards.
    desk.place_error = None
    desk.runs.clear()
    desk.target = None
    ctrl.process(Command("ring_open", 2.0))
    ctrl.process(Command("ring_select", 2.2, direction="right"))
    assert desk.runs == [("workspace_switch", {"target": "next"})]


def check_queue_and_lifecycle() -> None:
    ctrl, air, desk, _, _ = _make_controller()
    # Non-coalescing commands are bounded at 64.
    for i in range(100):
        ctrl._enqueue(Command("hint", float(i), text=f"h{i}"))
    assert len(ctrl._queue) <= 64
    ctrl._queue.clear()

    # Consecutive cursor commands coalesce into one summed command.
    for i in range(25):
        ctrl._enqueue(Command("cursor", float(i) * 0.01, dx=2.0, dy=-1.0))
    assert len(ctrl._queue) == 1
    merged = ctrl._queue[0]
    assert merged.kind == "cursor" and merged.dx == 50.0 and merged.dy == -25.0

    # start -> stop -> start subscribes to AirService commands once.
    ctrl.start()
    deadline = time.monotonic() + 2.0
    while not desk.pointers and time.monotonic() < deadline:
        time.sleep(0.01)
    assert len(desk.pointers) == 1
    assert desk.pointers[0] == (550, 375)
    ctrl.stop()
    ctrl.start()
    ctrl.stop()
    assert len(air.cmd_subscribers) == 1

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
