"""Air modes: app_kind, ToolProvider, computer helpers, settings and event names."""

from __future__ import annotations

import _setup  # noqa: F401
import sys

from lura.modules.air import MODES, AirController
from lura.modules.air.domain import app_kind
from lura.modules.computer import ComputerService, Rect
from lura.modules.computer.domain import Monitor, Window
from lura.modules.computer.infrastructure.unsupported import UnsupportedBackend
from lura.modules.dashboard.api import LIVE
from lura.modules.settings import Settings
from lura.modules.tools import ToolService
from lura.shared import events
from test_air_control import FakeAir, FakeDesktop


class ModeAir(FakeAir):
    """FakeAir with a toggleable running flag."""

    def __init__(self, running: bool = True) -> None:
        super().__init__()
        self.running = running


def _make_controller(
    *,
    running: bool = True,
    app_kinds: dict[str, str] | None = None,
) -> tuple[AirController, ModeAir, FakeDesktop, list[tuple[str, dict]], list[float]]:
    air = ModeAir(running=running)
    desk = FakeDesktop()
    published: list[tuple[str, dict]] = []
    now = [0.0]

    def pub(name: str, payload: dict) -> None:
        published.append((name, payload))

    kinds_dict = dict(app_kinds) if app_kinds is not None else {}
    ctrl = AirController(
        air,  # type: ignore[arg-type]
        desk,  # type: ignore[arg-type]
        pub,
        clock=lambda: now[0],
        app_kinds=lambda: kinds_dict,
    )
    return ctrl, air, desk, published, now


def check_app_kind() -> None:
    assert app_kind("Google-chrome", "") == "browser"
    assert app_kind("gnome-terminal-server", "") == "terminal"
    assert app_kind("libreoffice", "deck.odp - LibreOffice Impress") == "presentation"
    assert app_kind("Gnome-text-editor", "") == "editor"
    assert app_kind("XEyes", "") == "other"
    assert app_kind("XEyes", "", {"xeyes": "media"}) == "media"
    assert app_kind("XEyes", "", {"xeyes": "not_a_kind"}) == "other"


def check_tool_provider() -> None:
    ctrl, air, _, _, _ = _make_controller(running=True)
    specs = ctrl.tool_specs()
    assert len(specs) == 1
    assert specs[0].name == "air_mode"
    assert specs[0].parameters["properties"]["mode"]["enum"] == [*MODES, "status"]
    assert ctrl.owns("air_mode") and not ctrl.owns("other")

    out = ctrl.call("air_mode", {"mode": "media"})
    assert ctrl.mode == "media"
    assert "media" in out

    status_on = ctrl.call("air_mode", {"mode": "status"})
    assert status_on == "Air is on, mode media."
    air.running = False
    status_off = ctrl.call("air_mode", {"mode": "status"})
    assert status_off == "Air is off, mode media."

    bad = ctrl.call("air_mode", {"mode": "bogus"})
    assert "Unknown mode" in bad

    ts = ToolService()
    ts.add_provider(ctrl)
    res, ended = ts.dispatch("air_mode", {"mode": "pointer"})
    assert ended is False
    assert ctrl.mode == "pointer"
    assert "pointer" in res


class FastBackend:
    """Backend double to test ComputerService.active_app(), pointer() and pointer_to()."""

    def __init__(self) -> None:
        self.window_calls: list[int] = []
        self.windows_calls = 0
        self.monitors_calls = 0
        self.pointer_moves: list[tuple[int, int]] = []
        self._pointer: tuple[int, int] = (320, 240)
        self._mons = [
            Monitor(0, "eDP-1", Rect(0, 0, 1920, 1080), True),
            Monitor(1, "HDMI-1", Rect(1920, 0, 1920, 1080), False),
        ]
        self._by_id = {
            10: Window(10, "Docs - Firefox", "firefox", 1001, 0, Rect(0, 0, 1200, 800), False, False, "NORMAL"),
            99: Window(99, "Desktop", "gjs", 2000, -1, Rect(0, 0, 1920, 1080), False, False, "DESKTOP"),
        }
        self._active: int | None = 10

    def own_pids(self) -> set[int]:
        return {9999}

    def active_window(self) -> int | None:
        return self._active

    def window(self, wid: int) -> Window | None:
        self.window_calls.append(wid)
        return self._by_id.get(wid)

    def windows(self) -> list[Window]:
        self.windows_calls += 1
        return list(self._by_id.values())

    def monitors(self) -> list[Monitor]:
        self.monitors_calls += 1
        return list(self._mons)

    def pointer(self) -> tuple[int, int]:
        return self._pointer

    def pointer_move(self, x: int, y: int) -> None:
        self._pointer = (x, y)
        self.pointer_moves.append((x, y))


def check_computer_additions() -> None:
    be = FastBackend()
    svc = ComputerService(be)  # type: ignore[arg-type]
    now = [100.0]
    svc._clock = lambda: now[0]

    # active_app uses backend.window (not windows()) and returns (id, app, title).
    assert svc.active_app() == (10, "firefox", "Docs - Firefox")
    assert be.window_calls == [10]
    assert be.windows_calls == 0

    # pointer() passes through to backend.pointer().
    assert svc.pointer() == (320, 240)

    # DESKTOP-type window -> None.
    be._active = 99
    assert svc.active_app() is None

    # pointer_to clamps into bounds and caches monitors for 5 s.
    svc.pointer_to(-50, 9999)
    assert be.pointer_moves == [(0, 1079)]
    assert svc.pointer() == (0, 1079)
    assert be.monitors_calls == 1

    now[0] = 104.0
    svc.pointer_to(5000, 500)
    assert be.pointer_moves == [(0, 1079), (3839, 500)]
    assert be.monitors_calls == 1

    now[0] = 105.5
    svc.pointer_to(100, 200)
    assert be.monitors_calls == 2

    # UnsupportedBackend.window raises NotImplementedError.
    ub = UnsupportedBackend("unsupported")
    try:
        ub.window(1)
    except NotImplementedError:
        pass
    else:
        raise AssertionError("UnsupportedBackend.window should raise NotImplementedError")


def check_settings_and_events() -> None:
    s = Settings()
    assert s.air_app_kinds == {}
    assert "air_app_kinds" in LIVE
    assert events.AIR_MODE == "air.mode"
    assert events.AIR_INTENT == "air.intent"


def main() -> int:
    for name, fn in list(globals().items()):
        if name.startswith("check_"):
            fn()
            print(f"  {name[6:]} OK")
    print("air modes OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
