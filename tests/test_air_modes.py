"""Air modes (Stage E): resolver table, app_kind, pointer helpers, controller modes, ToolProvider and computer helpers."""

from __future__ import annotations

import _setup  # noqa: F401
import sys

from lura.modules.air import (
    MODES,
    AirController,
    AirState,
    HandView,
    Resolution,
    resolve,
)
from lura.modules.air.domain import (
    KINDS,
    POINTER_MARGIN,
    app_kind,
    hand_to_screen,
)
from lura.modules.computer import ComputerService, Rect
from lura.modules.computer.domain import Monitor, Window
from lura.modules.computer.infrastructure.unsupported import UnsupportedBackend
from lura.modules.dashboard.api import LIVE
from lura.modules.settings import Settings
from lura.modules.tools import ToolService
from lura.shared import events
from test_air_control import BOUNDS, FakeAir, FakeDesktop, _ev


class ModeDesktop(FakeDesktop):
    """FakeDesktop extended with active_app(), window_at() and pointer_to()."""

    def __init__(
        self,
        *,
        maximized: bool = False,
        active: tuple[int, str, str] | None = (10, "Google-chrome", "Docs - Google Chrome"),
        windows_by_half: dict[str, tuple[int, str, str]] | None = None,
    ) -> None:
        super().__init__(maximized=maximized)
        self.active = active
        self.active_app_calls = 0
        self.pointers: list[tuple[int, int]] = []
        self.windows_by_half = windows_by_half or {}

    def active_app(self) -> tuple[int, str, str] | None:
        self.active_app_calls += 1
        return self.active

    def window_at(self, x: int, y: int) -> tuple[int, str, str] | None:
        half = "left" if x < BOUNDS.width // 2 else "right"
        return self.windows_by_half.get(half)

    def pointer_to(self, x: int, y: int) -> None:
        self.pointers.append((x, y))


class ModeAir(FakeAir):
    """FakeAir with a toggleable running flag."""

    def __init__(self, running: bool = True) -> None:
        super().__init__()
        self.running = running


def _make_controller(
    *,
    active: tuple[int, str, str] | None = (10, "Google-chrome", "Docs - Google Chrome"),
    maximized: bool = False,
    running: bool = True,
    app_kinds: dict[str, str] | None = None,
) -> tuple[AirController, ModeAir, ModeDesktop, list[tuple[str, dict]], list[float]]:
    air = ModeAir(running=running)
    desk = ModeDesktop(maximized=maximized, active=active)
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


def check_resolve_table() -> None:
    def act(name: str, **args) -> Resolution:
        return Resolution("action", name, args)

    def ign(gesture: str, mode: str) -> Resolution:
        return Resolution("ignore", reason=f"{gesture} does nothing in {mode} mode.")

    # Every cell of the §5.1 table (using kind="other" for universal default).
    expected: dict[str, dict[str, Resolution]] = {
        "universal": {
            "swipe_left": act("workspace_switch", target="prev"),
            "swipe_right": act("workspace_switch", target="next"),
            "swipe_up": act("workspace_overview"),
            "swipe_down": act("show_desktop"),
            "double_pinch": act("toggle_maximize"),
        },
        "window": {
            "swipe_left": act("window_snap", where="left"),
            "swipe_right": act("window_snap", where="right"),
            "swipe_up": act("window_maximize"),
            "swipe_down": act("window_minimize"),
            "double_pinch": act("toggle_maximize"),
        },
        "workspace": {
            "swipe_left": act("workspace_switch", target="prev"),
            "swipe_right": act("workspace_switch", target="next"),
            "swipe_up": act("workspace_overview"),
            "swipe_down": act("show_desktop"),
            "double_pinch": ign("double_pinch", "workspace"),
        },
        "pointer": {
            "swipe_left": ign("swipe_left", "pointer"),
            "swipe_right": ign("swipe_right", "pointer"),
            "swipe_up": act("pointer_scroll", direction="up", amount=5),
            "swipe_down": act("pointer_scroll", direction="down", amount=5),
            "double_pinch": act("pointer_click", button="left", count=2),
        },
        "files": {
            "swipe_left": act("press_keys", keys="alt+Left"),
            "swipe_right": act("press_keys", keys="alt+Right"),
            "swipe_up": act("press_keys", keys="alt+Up"),
            "swipe_down": ign("swipe_down", "files"),
            "double_pinch": act("press_keys", keys="Return"),
        },
        "media": {
            "swipe_left": act("media", command="previous"),
            "swipe_right": act("media", command="next"),
            "swipe_up": act("volume_change", delta=10),
            "swipe_down": act("volume_change", delta=-10),
            "double_pinch": act("media", command="play_pause"),
        },
        "developer": {
            "swipe_left": act("press_keys", keys="ctrl+Page_Up"),
            "swipe_right": act("press_keys", keys="ctrl+Page_Down"),
            "swipe_up": act("workspace_switch", target="prev"),
            "swipe_down": act("workspace_switch", target="next"),
            "double_pinch": act("toggle_maximize"),
        },
        "presentation": {
            "swipe_left": act("press_keys", keys="Left"),
            "swipe_right": act("press_keys", keys="Right"),
            "swipe_up": ign("swipe_up", "presentation"),
            "swipe_down": ign("swipe_down", "presentation"),
            "double_pinch": ign("double_pinch", "presentation"),
        },
    }

    for mode, row in expected.items():
        for gesture, want in row.items():
            got = resolve(gesture, "", mode, "other")
            assert got == want, f"{mode}/{gesture}: got {got}, want {want}"

    # Universal swipes for each kind.
    universal_kinds = {
        "browser": (act("press_keys", keys="ctrl+shift+Tab"), act("press_keys", keys="ctrl+Tab")),
        "media": (act("media", command="previous"), act("media", command="next")),
        "presentation": (act("press_keys", keys="Left"), act("press_keys", keys="Right")),
        "files": (act("press_keys", keys="alt+Left"), act("press_keys", keys="alt+Right")),
        "editor": (act("press_keys", keys="ctrl+Page_Up"), act("press_keys", keys="ctrl+Page_Down")),
        "terminal": (act("press_keys", keys="ctrl+Page_Up"), act("press_keys", keys="ctrl+Page_Down")),
        "other": (act("workspace_switch", target="prev"), act("workspace_switch", target="next")),
    }
    assert set(universal_kinds) == set(KINDS)
    for kind, (want_l, want_r) in universal_kinds.items():
        assert resolve("swipe_left", "", "universal", kind) == want_l
        assert resolve("swipe_right", "", "universal", kind) == want_r

    # Globals in every mode (including ai).
    for mode in MODES:
        assert resolve("pose", "victory", mode, "other") == Resolution("mode")
        assert resolve("pose", "thumbs_up", mode, "other") == Resolution("intent", intent="confirm")
        assert resolve("pose", "thumbs_down", mode, "other") == Resolution("intent", intent="deny")
        assert resolve("pose", "point", mode, "other") == Resolution("intent", intent="point")
        for silent_pose in ("open_palm", "fist", "pinch", "none"):
            assert resolve("pose", silent_pose, mode, "other") == Resolution("ignore")
        for silent_g in ("hand_enter", "hand_leave", "pinch_start", "pinch_end", "spread_start", "spread_end"):
            assert resolve(silent_g, "", mode, "other") == Resolution("ignore")

    # AI mode discrete gestures -> intent talk.
    for g in ("swipe_left", "swipe_right", "swipe_up", "swipe_down", "double_pinch"):
        assert resolve(g, "", "ai", "browser") == Resolution("intent", intent="talk", args={"gesture": g})

    # Unknown combo -> ignore with reason text.
    unk = resolve("unknown_gesture", "", "window", "other")
    assert unk == Resolution("ignore", reason="unknown_gesture does nothing in window mode.")


def check_app_kind() -> None:
    assert app_kind("Google-chrome", "") == "browser"
    assert app_kind("gnome-terminal-server", "") == "terminal"
    assert app_kind("libreoffice", "deck.odp - LibreOffice Impress") == "presentation"
    assert app_kind("Gnome-text-editor", "") == "editor"
    assert app_kind("XEyes", "") == "other"
    assert app_kind("XEyes", "", {"xeyes": "media"}) == "media"
    assert app_kind("XEyes", "", {"xeyes": "not_a_kind"}) == "other"


def check_hand_to_screen() -> None:
    assert hand_to_screen(POINTER_MARGIN, POINTER_MARGIN, BOUNDS) == (0, 0)
    assert hand_to_screen(0.0, 0.0, BOUNDS) == (0, 0)
    assert hand_to_screen(1.0 - POINTER_MARGIN, 1.0 - POINTER_MARGIN, BOUNDS) == (3839, 1079)
    assert hand_to_screen(1.0, 1.0, BOUNDS) == (3839, 1079)
    cx, cy = hand_to_screen(0.5, 0.5, BOUNDS)
    assert abs(cx - 1920) <= 1 and abs(cy - 540) <= 1


def check_controller_modes_and_intents() -> None:
    ctrl, air, desk, pub, _ = _make_controller(active=(10, "Google-chrome", "Docs - Chrome"))
    assert ctrl.mode == "universal"
    assert ctrl.state()["mode"] == "universal"

    # Arm with open palm.
    ctrl.process(_ev("pose", 0.0, data={"pose": "open_palm"}))

    # Universal + browser + swipe_right -> press_keys ctrl+Tab.
    ctrl.process(_ev("swipe_right", 1.0))
    assert desk.runs == [("press_keys", {"keys": "ctrl+Tab"})]

    # Universal + other app + swipe_right -> workspace_switch next.
    desk.runs.clear()
    desk.active = (12, "XEyes", "xeyes")
    ctrl.process(_ev("swipe_right", 2.0))
    assert desk.runs == [("workspace_switch", {"target": "next"})]

    # Media mode swipe_up -> volume_change +10.
    desk.runs.clear()
    ctrl.set_mode("media")
    ctrl.process(_ev("swipe_up", 3.0))
    assert desk.runs == [("volume_change", {"delta": 10})]

    # Presentation mode: pinch + move -> no grip.
    desk.grips.clear()
    ctrl.set_mode("presentation")
    ctrl.process(_ev("pinch_start", 4.0, x=0.5, y=0.5))
    air.state = AirState(
        camera="on",
        hands=(HandView("right", 0.7, 0.5, "pinch", True, 0.95),),
    )
    ctrl.tick(4.05)
    assert desk.grips == []

    # Ignore with reason -> AIR_ACTION ok False, no desktop call.
    desk.runs.clear()
    pub.clear()
    ctrl.process(_ev("swipe_up", 4.5))
    assert desk.runs == []
    assert pub == [
        (events.AIR_ACTION, {
            "action": "ignore",
            "ok": False,
            "message": "swipe_up does nothing in presentation mode.",
        }),
    ]

    # Window mode swipe_left -> window_snap left.
    desk.runs.clear()
    ctrl.set_mode("window")
    ctrl.process(_ev("swipe_left", 5.0))
    assert desk.runs == [("window_snap", {"where": "left"})]

    # Victory pose cycles modes: back to universal first, then victory -> window + AIR_MODE.
    ctrl.set_mode("universal")
    pub.clear()
    ctrl.process(_ev("pose", 6.0, data={"pose": "victory"}))
    assert ctrl.mode == "window"
    mode_events = [p for n, p in pub if n == events.AIR_MODE]
    assert mode_events == [{"mode": "window"}]

    # 8 more victories (9 total from universal) -> back to universal.
    for i in range(8):
        ctrl.process(_ev("pose", 6.5 + i * 0.2, data={"pose": "victory"}))
    assert ctrl.mode == "universal"

    # Thumbs up -> AIR_INTENT confirm with app/title, no desktop call.
    desk.runs.clear()
    desk.active = (15, "Code", "main.py - VS Code")
    pub.clear()
    ctrl.process(_ev("pose", 9.0, data={"pose": "thumbs_up"}))
    assert desk.runs == []
    intents = [p for n, p in pub if n == events.AIR_INTENT]
    assert intents == [{
        "intent": "confirm",
        "mode": "universal",
        "gesture": "pose",
        "app": "Code",
        "title": "main.py - VS Code",
    }]

    # AI mode swipe -> AIR_INTENT talk, no desktop call.
    ctrl.set_mode("ai")
    desk.runs.clear()
    pub.clear()
    ctrl.process(_ev("swipe_left", 10.0))
    assert desk.runs == []
    intents = [p for n, p in pub if n == events.AIR_INTENT]
    assert intents == [{
        "intent": "talk",
        "mode": "ai",
        "gesture": "swipe_left",
        "app": "Code",
        "title": "main.py - VS Code",
    }]


def check_pointer_mode() -> None:
    ctrl, air, desk, pub, _ = _make_controller()
    ctrl.process(_ev("pose", 0.0, data={"pose": "open_palm"}))
    ctrl.set_mode("pointer")
    desk.runs.clear()

    # Tick moves pointer_to towards the hand.
    air.state = AirState(
        camera="on",
        hands=(HandView("right", 0.5, 0.5, "open_palm", False, 0.95),),
    )
    ctrl.tick(0.1)
    assert len(desk.pointers) == 1
    assert abs(desk.pointers[0][0] - 1920) <= 1 and abs(desk.pointers[0][1] - 540) <= 1

    air.state = AirState(
        camera="on",
        hands=(HandView("right", 0.7, 0.5, "open_palm", False, 0.95),),
    )
    ctrl.tick(0.2)
    assert len(desk.pointers) == 2
    assert desk.pointers[1][0] > desk.pointers[0][0]

    # Short pinch -> pointer_click left.
    ctrl.process(_ev("pinch_start", 1.0, x=0.5, y=0.5))
    ctrl.process(_ev("pinch_end", 1.15, x=0.5, y=0.5))
    assert desk.runs == [("pointer_click", {"button": "left"})]

    # Long pinch -> pointer_down on tick (>= 0.3 s), then pointer_up on release.
    desk.runs.clear()
    ctrl.process(_ev("pinch_start", 2.0, x=0.5, y=0.5))
    ctrl.tick(2.1)
    assert desk.runs == []
    ctrl.tick(2.35)
    assert desk.runs == [("pointer_down", {"button": "left"})]
    ctrl.process(_ev("pinch_end", 2.5, x=0.55, y=0.5))
    assert desk.runs == [
        ("pointer_down", {"button": "left"}),
        ("pointer_up", {"button": "left"}),
    ]

    # set_mode releases a held button.
    desk.runs.clear()
    ctrl.process(_ev("pinch_start", 3.0, x=0.5, y=0.5))
    ctrl.tick(3.35)
    assert desk.runs == [("pointer_down", {"button": "left"})]
    ctrl.set_mode("universal")
    assert desk.runs == [
        ("pointer_down", {"button": "left"}),
        ("pointer_up", {"button": "left"}),
    ]


def check_point_to_focus() -> None:
    win_a = (101, "firefox", "Docs - Firefox")
    win_b = (202, "code", "main.py - Code")
    ctrl, air, desk, pub, _ = _make_controller()
    desk.windows_by_half = {"left": win_a, "right": win_b}
    ctrl.process(_ev("pose", 0.0, data={"pose": "open_palm"}))

    # Point held over window A (left half, tip_x=0.25) for 0.5 s ->
    # window_focus A + one 'target' feedback + one AIR_INTENT point.
    pub.clear()
    desk.runs.clear()
    air.state = AirState(
        camera="on",
        hands=(HandView("right", 0.3, 0.5, "point", False, 0.95, tip_x=0.25, tip_y=0.5),),
    )
    ctrl.process(_ev("pose", 1.0, data={"pose": "point"}))
    for t in (1.0, 1.1, 1.2, 1.3, 1.4, 1.5):
        ctrl.tick(t)
    assert desk.runs == [("window_focus", {"window": 101})]
    targets = [p for n, p in pub if n == events.AIR_ACTION and p["action"] == "target"]
    assert targets == [{"action": "target", "ok": True, "message": "Target: Docs - Firefox."}]
    intents = [p for n, p in pub if n == events.AIR_INTENT]
    assert intents == [{
        "intent": "point",
        "mode": "universal",
        "gesture": "pose",
        "app": "firefox",
        "title": "Docs - Firefox",
    }]

    # Finger moves to window B (right half, tip_x=0.75) and dwells -> focuses B.
    pub.clear()
    desk.runs.clear()
    air.state = AirState(
        camera="on",
        hands=(HandView("right", 0.7, 0.5, "point", False, 0.95, tip_x=0.75, tip_y=0.5),),
    )
    for t in (1.6, 1.8, 2.0, 2.2):
        ctrl.tick(t)
    assert desk.runs == [("window_focus", {"window": 202})]
    targets = [p for n, p in pub if n == events.AIR_ACTION and p["action"] == "target"]
    assert targets == [{"action": "target", "ok": True, "message": "Target: main.py - Code."}]
    intents = [p for n, p in pub if n == events.AIR_INTENT]
    assert intents == [{
        "intent": "point",
        "mode": "universal",
        "gesture": "pose",
        "app": "code",
        "title": "main.py - Code",
    }]

    # End point pose, then point for only 0.2 s -> nothing.
    ctrl.process(_ev("pose", 2.5, data={"pose": "open_palm"}))
    pub.clear()
    desk.runs.clear()
    air.state = AirState(
        camera="on",
        hands=(HandView("right", 0.3, 0.5, "point", False, 0.95, tip_x=0.25, tip_y=0.5),),
    )
    ctrl.process(_ev("pose", 3.0, data={"pose": "point"}))
    ctrl.tick(3.0)
    ctrl.tick(3.2)
    ctrl.process(_ev("pose", 3.25, data={"pose": "open_palm"}))
    ctrl.tick(3.5)
    assert desk.runs == []
    assert pub == []

    # AI mode -> no window_focus (only immediate intent).
    ctrl.set_mode("ai")
    pub.clear()
    desk.runs.clear()
    air.state = AirState(
        camera="on",
        hands=(HandView("right", 0.3, 0.5, "point", False, 0.95, tip_x=0.25, tip_y=0.5),),
    )
    ctrl.process(_ev("pose", 4.0, data={"pose": "point"}))
    for t in (4.0, 4.2, 4.4, 4.6):
        ctrl.tick(t)
    assert desk.runs == []
    intents = [p for n, p in pub if n == events.AIR_INTENT]
    assert len(intents) == 1 and intents[0]["intent"] == "point" and intents[0]["mode"] == "ai"


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
    """Backend double to test ComputerService.active_app() and pointer_to()."""

    def __init__(self) -> None:
        self.window_calls: list[int] = []
        self.windows_calls = 0
        self.monitors_calls = 0
        self.pointer_moves: list[tuple[int, int]] = []
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

    def pointer_move(self, x: int, y: int) -> None:
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

    # DESKTOP-type window -> None.
    be._active = 99
    assert svc.active_app() is None

    # pointer_to clamps into bounds and caches monitors for 5 s.
    svc.pointer_to(-50, 9999)
    assert be.pointer_moves == [(0, 1079)]
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
