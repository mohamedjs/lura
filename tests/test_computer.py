"""Computer module: catalog, validation, geometry, parsers, service and ToolService integration."""

import _setup  # noqa: F401
import sys

from lura.modules.computer import (
    ACTIONS,
    CONFIRM,
    DESKTOP_TOOL,
    SAFE,
    ActionError,
    ComputerService,
)
from lura.modules.computer.domain import (
    ACTIONS_BY_NAME,
    LayoutEntry,
    Monitor,
    Rect,
    Window,
    bounds,
    catalog_text,
    clamp_visible,
    grid_rects,
    is_target,
    match_layout,
    monitor_for,
    monitor_target,
    next_window,
    parse_gdbus_int,
    parse_monitors,
    parse_pointer,
    parse_wpctl_volume,
    parse_xprop,
    parse_xwininfo,
    prop_atoms,
    prop_ints,
    prop_strings,
    resolve_desktop,
    snap_rect,
    validate_args,
    validate_keys,
    window_from_props,
    work_area,
)
from lura.modules.computer.infrastructure.unsupported import UnsupportedBackend


EXPECTED_ORDER = [
    "capabilities", "window_list", "monitors", "pointer_position",
    "window_focus", "window_move", "window_move_by", "window_resize",
    "window_maximize", "window_restore", "window_minimize", "window_close",
    "window_snap", "window_to_monitor", "window_to_workspace", "app_switch",
    "windows_arrange",
    "workspace_switch", "workspace_overview", "show_desktop",
    "layout_save", "layout_restore", "layout_list", "layout_delete",
    "pointer_move", "pointer_move_by", "pointer_click", "pointer_down",
    "pointer_up", "pointer_scroll", "pointer_drag",
    "volume_set", "volume_change", "volume_mute", "mic_mute",
    "brightness_set", "brightness_change", "media", "lock_screen",
    "clipboard_get", "clipboard_set", "copy", "cut", "paste",
    "select_all", "undo", "press_keys",
]

PROBE_KEYS = {
    "x11", "xdotool", "xprop", "xwininfo", "xrandr",
    "wpctl", "playerctl", "xclip", "loginctl", "gsd_power", "gnome",
}


class FakeBackend:
    """In-memory Backend double recording every mutation call."""

    def __init__(self, *, probes: dict[str, bool] | None = None, fail_pointer_move: bool = False) -> None:
        self.calls: list[tuple] = []
        self._probes = probes if probes is not None else {k: True for k in PROBE_KEYS}
        self._fail_pointer_move = fail_pointer_move
        self._own = {9999}
        self._mons = [
            Monitor(0, "eDP-1", Rect(0, 0, 1920, 1080), True),
            Monitor(1, "HDMI-1-0", Rect(1920, 0, 1920, 1080), False),
        ]
        self._workarea = Rect(0, 28, 3840, 1052)
        self._wins: list[Window] = [
            Window(101, "Terminal — zsh", "Gnome-terminal", 1001, 0, Rect(100, 100, 800, 600),
                   False, False, "NORMAL"),
            Window(102, "Docs — Firefox", "firefox", 1002, 0, Rect(200, 120, 1200, 800),
                   True, False, "NORMAL"),
            Window(103, "Notes — Code", "Code", 1003, 0, Rect(300, 150, 900, 700),
                   False, False, "NORMAL"),
            Window(200, "Desktop Icons", "gjs", 2000, -1, Rect(0, 0, 1920, 1080),
                   False, False, "DESKTOP"),
            Window(201, "Lura Face", "Lura", 9999, -1, Rect(10, 10, 200, 200),
                   False, False, "NORMAL"),
        ]
        self._active: int | None = 103
        self._desktop = 0
        self._desktop_count = 5
        self._showing = False
        self._ptr = (500, 400)
        self._vol = 0.60
        self._muted = False
        self._mic_muted = False
        self._bright = 50
        self._clip = "hello"

    def unsupported_reason(self) -> str:
        return ""

    def probe(self) -> dict[str, bool]:
        return dict(self._probes)

    def own_pids(self) -> set[int]:
        return set(self._own)

    def windows(self) -> list[Window]:
        return list(self._wins)

    def active_window(self) -> int | None:
        return self._active

    def monitors(self) -> list[Monitor]:
        return list(self._mons)

    def workarea(self) -> Rect | None:
        return self._workarea

    def desktops(self) -> tuple[int, int]:
        return (self._desktop, self._desktop_count)

    def showing_desktop(self) -> bool:
        return self._showing

    def pointer(self) -> tuple[int, int]:
        return self._ptr

    def _replace(self, wid: int, **changes) -> None:
        for i, w in enumerate(self._wins):
            if w.id == wid:
                d = {**w.__dict__, **changes}
                self._wins[i] = Window(**d)
                return

    def activate(self, wid: int) -> None:
        self.calls.append(("activate", wid))
        self._active = wid
        self._replace(wid, minimized=False)

    def move_resize(self, wid: int, frame: Rect) -> None:
        self.calls.append(("move_resize", wid, frame))
        self._replace(wid, rect=frame)

    def set_maximized(self, wid: int, on: bool) -> None:
        self.calls.append(("set_maximized", wid, on))
        self._replace(wid, maximized=on)

    def minimize(self, wid: int) -> None:
        self.calls.append(("minimize", wid))
        self._replace(wid, minimized=True)

    def close(self, wid: int) -> None:
        self.calls.append(("close", wid))
        self._wins = [w for w in self._wins if w.id != wid]

    def set_window_desktop(self, wid: int, desktop: int) -> None:
        self.calls.append(("set_window_desktop", wid, desktop))
        self._replace(wid, desktop=desktop)

    def switch_desktop(self, desktop: int) -> None:
        self.calls.append(("switch_desktop", desktop))
        self._desktop = desktop

    def set_showing_desktop(self, on: bool) -> None:
        self.calls.append(("set_showing_desktop", on))
        self._showing = on

    def overview(self) -> None:
        self.calls.append(("overview",))

    def pointer_move(self, x: int, y: int) -> None:
        self.calls.append(("pointer_move", x, y))
        if self._fail_pointer_move:
            raise RuntimeError("boom")
        self._ptr = (x, y)

    def pointer_button(self, button: int, action: str, count: int = 1) -> None:
        self.calls.append(("pointer_button", button, action, count))

    def key(self, combo: str) -> None:
        self.calls.append(("key", combo))

    def volume(self) -> tuple[float, bool]:
        return (self._vol, self._muted)

    def set_volume(self, fraction: float) -> None:
        self.calls.append(("set_volume", fraction))
        self._vol = fraction

    def change_volume(self, delta_percent: int) -> None:
        self.calls.append(("change_volume", delta_percent))
        self._vol = max(0.0, min(1.0, self._vol + delta_percent / 100.0))

    def toggle_mute(self) -> None:
        self.calls.append(("toggle_mute",))
        self._muted = not self._muted

    def mic_muted(self) -> bool:
        return self._mic_muted

    def toggle_mic(self) -> None:
        self.calls.append(("toggle_mic",))
        self._mic_muted = not self._mic_muted

    def brightness(self) -> int:
        return self._bright

    def set_brightness(self, percent: int) -> None:
        self.calls.append(("set_brightness", percent))
        self._bright = percent

    def media(self, command: str) -> None:
        self.calls.append(("media", command))

    def lock(self) -> None:
        self.calls.append(("lock",))

    def clipboard_get(self) -> str:
        return self._clip

    def clipboard_set(self, text: str) -> None:
        self.calls.append(("clipboard_set", text))
        self._clip = text


def check_catalog() -> None:
    names = [a.name for a in ACTIONS]
    assert len(names) == 47
    assert len(set(names)) == len(names)
    assert names == EXPECTED_ORDER
    for spec in ACTIONS:
        assert hasattr(ComputerService, f"_do_{spec.name}"), f"missing _do_{spec.name}"
        for req in spec.requires:
            assert req in PROBE_KEYS, f"unknown probe key {req} on {spec.name}"
        assert spec.safety in (SAFE, CONFIRM)
    assert DESKTOP_TOOL.name == "desktop_action"
    assert DESKTOP_TOOL.parameters["properties"]["action"]["enum"] == names
    confirm_names = {a.name for a in ACTIONS if a.safety == CONFIRM}
    assert confirm_names == {"window_close", "layout_delete", "lock_screen"}
    txt = catalog_text({"window_close": False})
    assert "window_close [confirm]" in txt and "(unavailable)" in txt


def check_validate_args() -> None:
    move = ACTIONS_BY_NAME["window_move"]
    out = validate_args(move, {"x": "300", "y": 2.0, "window": "0x2e00004", "ignored": 123})
    assert out == {"window": 48234500, "app": None, "x": 300, "y": 2}
    assert "ignored" not in out

    click = ACTIONS_BY_NAME["pointer_click"]
    assert validate_args(click, {}) == {"button": "left", "count": 1}

    ws = ACTIONS_BY_NAME["window_to_workspace"]
    assert validate_args(ws, {"target": "2", "follow": "true"})["follow"] is True
    assert validate_args(ws, {"target": "2", "follow": 0})["follow"] is False

    for spec_name, bad in (
        ("window_move", {"x": 300}),                           # missing required y
        ("window_move", {"x": 99999, "y": 0}),                 # out of range
        ("window_move", {"x": 1.5, "y": 0}),                   # non-integral float
        ("window_snap", {"where": "diagonal"}),                # bad choice
        ("window_focus", {"window": "not-an-id"}),             # bad target
        ("window_focus", {"window": 0}),                       # non-positive id
        ("volume_set", {"percent": True}),                     # bool rejected for int
        ("window_to_workspace", {"target": "1", "follow": "maybe"}),
    ):
        try:
            validate_args(ACTIONS_BY_NAME[spec_name], bad)
        except ActionError:
            pass
        else:
            raise AssertionError(f"{spec_name} accepted {bad!r}")


def check_validate_keys() -> None:
    assert validate_keys("ctrl+shift+t") == "ctrl+shift+t"
    assert validate_keys("Super+Left") == "super+Left"
    assert validate_keys("F5") == "F5"
    assert validate_keys("control+Return") == "ctrl+Return"
    assert validate_keys("alt+tab") == "alt+Tab"
    for bad in (
        "ctrl+alt+Delete",
        "alt+ctrl+delete",
        "ctrl+alt+BackSpace",
        "super+l",
        "Super+L",
        "rm -rf",
        "ctrl+;",
        "ctrl+alt+shift+super+a",
        "ctrl+ctrl+a",
        "ctrl+control+a",
        "",
        "ctrl+",
    ):
        try:
            validate_keys(bad)
        except ActionError:
            pass
        else:
            raise AssertionError(f"validate_keys accepted {bad!r}")


def check_geometry() -> None:
    m0 = Monitor(0, "eDP-1", Rect(0, 0, 1920, 1080), True)
    m1 = Monitor(1, "HDMI-1-0", Rect(1920, 0, 1920, 1080), False)
    wa = Rect(0, 28, 3840, 1052)
    assert work_area(m0, wa) == Rect(0, 28, 1920, 1052)
    assert work_area(m1, wa) == Rect(1920, 28, 1920, 1052)
    assert work_area(m0, None) == m0.rect
    assert bounds([m0, m1]) == Rect(0, 0, 3840, 1080)

    odd = Rect(0, 28, 1921, 1053)
    left, right = snap_rect(odd, "left"), snap_rect(odd, "right")
    assert left.x == 0 and left.width + right.width == odd.width and right.x == left.width
    top, bot = snap_rect(odd, "top"), snap_rect(odd, "bottom")
    assert top.y == 28 and top.height + bot.height == odd.height and bot.y == top.y + top.height
    for where in ("left", "right", "top", "bottom", "top_left", "top_right",
                  "bottom_left", "bottom_right", "center", "full"):
        r = snap_rect(Rect(0, 28, 1920, 1052), where)
        assert r.width > 0 and r.height > 0
    assert snap_rect(Rect(0, 28, 1920, 1052), "full") == Rect(0, 28, 1920, 1052)

    area = Rect(0, 28, 1920, 1052)
    assert grid_rects(0, area) == []
    assert grid_rects(1, area) == [area]
    assert len(grid_rects(2, area)) == 2
    g3 = grid_rects(3, area)
    assert g3 == [snap_rect(area, "left"), snap_rect(area, "top_right"), snap_rect(area, "bottom_right")]
    g4 = grid_rects(4, area)
    assert len(g4) == 4 and sum(r.width * r.height for r in g4) == area.width * area.height
    g5 = grid_rects(5, area)
    assert len(g5) == 5 and sum(r.width * r.height for r in g5) == area.width * area.height
    assert g5[3].width + g5[4].width == area.width

    assert monitor_for(Rect(100, 100, 400, 300), [m0, m1]) == m0
    assert monitor_for(Rect(2000, 100, 400, 300), [m0, m1]) == m1
    assert monitor_for(Rect(-5000, -5000, 100, 100), [m0, m1]) == m0  # primary fallback

    clamped = clamp_visible(Rect(9000, -500, 400, 300), Rect(0, 0, 3840, 1080))
    assert clamped.y == 0 and clamped.x == 3840 - 64
    clamped_left = clamp_visible(Rect(-9000, 5000, 400, 300), Rect(0, 0, 3840, 1080))
    assert clamped_left.x + clamped_left.width == 64 and clamped_left.y == 1080 - 64

    assert resolve_desktop("next", 0, 5) == 1
    assert resolve_desktop("next", 4, 5) == 4
    assert resolve_desktop("prev", 0, 5) == 0
    assert resolve_desktop("3", 0, 5) == 2
    try:
        resolve_desktop("6", 0, 5)
    except ActionError as exc:
        assert "5" in str(exc)
    else:
        raise AssertionError("out of range desktop accepted")

    assert monitor_target("next", 1, 2) == 0
    assert monitor_target("prev", 0, 2) == 1
    assert monitor_target("2", 0, 2) == 1


def check_parsers() -> None:
    xr = (
        "Monitors: 2\n"
        " 0: +*eDP-1 1920/344x1080/193+0+0  eDP-1\n"
        " 1: +HDMI-1-0 1920/527x1080/296+1920+0  HDMI-1-0\n"
    )
    mons = parse_monitors(xr)
    assert len(mons) == 2
    assert mons[0] == Monitor(0, "eDP-1", Rect(0, 0, 1920, 1080), True)
    assert mons[1] == Monitor(1, "HDMI-1-0", Rect(1920, 0, 1920, 1080), False)
    assert parse_monitors("garbage") == []

    xwi = (
        "xwininfo: Window id: 0x2e00004 \"xeyes\"\n"
        "  Absolute upper-left X:  120\n"
        "  Absolute upper-left Y:  84\n"
        "  Width: 300\n"
        "  Height: 200\n"
    )
    assert parse_xwininfo(xwi) == Rect(120, 84, 300, 200)
    assert parse_xwininfo("nothing here") is None

    xp = (
        'WM_CLASS(STRING) = "xeyes", "XEyes"\n'
        '_NET_WM_NAME(UTF8_STRING) = "Say \\"hi\\" \\\\ test"\n'
        "_NET_WM_STATE(ATOM) = _NET_WM_STATE_MAXIMIZED_VERT, _NET_WM_STATE_MAXIMIZED_HORZ\n"
        "_NET_FRAME_EXTENTS(CARDINAL) = 10, 10, 32, 10\n"
        "_NET_WM_WINDOW_TYPE:  not found.\n"
        "WM_STATE(WM_STATE):\n"
        "                window state: Normal\n"
        "_NET_CLIENT_LIST_STACKING(WINDOW): window id # 0x600001, 0x2e00004\n"
    )
    props = parse_xprop(xp)
    assert prop_strings(props["WM_CLASS"]) == ["xeyes", "XEyes"]
    assert prop_strings(props["_NET_WM_NAME"]) == ['Say "hi" \\ test']
    assert prop_atoms(props["_NET_WM_STATE"]) == [
        "_NET_WM_STATE_MAXIMIZED_VERT", "_NET_WM_STATE_MAXIMIZED_HORZ",
    ]
    assert prop_ints(props["_NET_FRAME_EXTENTS"]) == [10, 10, 32, 10]
    assert "_NET_WM_WINDOW_TYPE" not in props
    assert prop_ints(props["_NET_CLIENT_LIST_STACKING"]) == [0x600001, 0x2E00004]

    empty_state = parse_xprop("_NET_WM_STATE(ATOM) = \n")
    assert prop_atoms(empty_state["_NET_WM_STATE"]) == []

    win = window_from_props(0x2E00004, props, Rect(120, 84, 300, 200), (10, 10, 32, 10))
    assert win.title == 'Say "hi" \\ test' and win.app == "XEyes"
    assert win.maximized and not win.minimized and win.kind == "" and win.desktop == -1
    assert win.rect == Rect(110, 52, 320, 242)

    assert parse_wpctl_volume("Volume: 0.61\n") == (0.61, False)
    assert parse_wpctl_volume("Volume: 0.13 [MUTED]\n") == (0.13, True)
    assert parse_wpctl_volume("bad") is None
    assert parse_gdbus_int("(<25>,)\n") == 25
    assert parse_gdbus_int("nope") is None
    assert parse_pointer("X=640\nY=480\nSCREEN=0\n") == (640, 480)
    assert parse_pointer("bad") is None


def check_targets_and_layouts() -> None:
    r = Rect(0, 0, 200, 200)
    assert not is_target(Window(1, "d", "gjs", 10, 0, r, False, False, "DESKTOP"), {99})
    assert not is_target(Window(2, "d", "dock", 10, 0, r, False, False, "DOCK"), {99})
    assert not is_target(Window(3, "f", "Lura", 10, 0, r, False, False, "NORMAL"), {99})
    assert not is_target(Window(4, "f", "App", 99, 0, r, False, False, "NORMAL"), {99})
    assert is_target(Window(5, "t", "XEyes", 10, 0, r, False, False, "NORMAL"), {99})
    assert is_target(Window(6, "t", "XEyes", 10, 0, r, False, False, ""), {99})

    assert next_window([1], 1, "next") is None
    assert next_window([1, 2, 3], 3, "next") == 2
    assert next_window([1, 2, 3], 3, "prev") == 1

    w1 = Window(1, "Alpha", "Code", 10, 0, r, False, False, "NORMAL")
    w2 = Window(2, "Beta", "Code", 11, 0, r, False, False, "NORMAL")
    entries = (
        LayoutEntry("code", "Beta", 0, r, False),
        LayoutEntry("code", "Alpha", 0, r, False),
        LayoutEntry("code", "Gamma", 0, r, False),
        LayoutEntry("Slack", "General", 0, r, False),
    )
    matched, unmatched = match_layout(entries, [w1, w2])
    assert [(e.title, w.id) for e, w in matched] == [("Beta", 2), ("Alpha", 1)]
    assert [e.title for e in unmatched] == ["Gamma", "General"]


def check_service() -> None:
    fb = FakeBackend()
    svc = ComputerService(fb)

    # Confirm flow for window_close
    res = svc.run("window_close", {"window": 101})
    assert not res.ok and res.needs_confirmation and "Terminal" in res.message
    assert ("close", 101) not in fb.calls
    res = svc.run("window_close", {"window": 101}, confirmed=True)
    assert res.ok and ("close", 101) in fb.calls

    # Unsupported backend
    usvc = ComputerService(UnsupportedBackend("Wayland session"))
    assert not usvc.run("window_list").ok
    assert "Wayland session" in usvc.run("window_list").message
    assert usvc.run("layout_list").ok
    caps = usvc.capabilities()
    assert not caps["window_list"]["available"] and caps["layout_list"]["available"]

    # Missing probe key
    fb_no_xclip = FakeBackend(probes={**{k: True for k in PROBE_KEYS}, "xclip": False})
    r_missing = ComputerService(fb_no_xclip).run("clipboard_get")
    assert not r_missing.ok and "xclip" in r_missing.message

    # Ambiguous app
    fb2 = FakeBackend()
    fb2._wins.append(Window(104, "Other — Code", "Code", 1004, 0, Rect(0, 0, 500, 500),
                            False, False, "NORMAL"))
    fb2._active = 101
    svc2 = ComputerService(fb2)
    amb = svc2.run("window_focus", {"app": "Code"})
    assert not amb.ok and "Several windows match" in amb.message
    assert not fb2.calls

    # window_snap left on maximized window -> unmaximize first then move_resize
    fb3 = FakeBackend()
    svc3 = ComputerService(fb3)
    sn = svc3.run("window_snap", {"window": 102, "where": "left"})
    assert sn.ok
    assert fb3.calls == [
        ("set_maximized", 102, False),
        ("move_resize", 102, Rect(0, 28, 960, 1052)),
    ]

    # window_to_monitor next translates by 1920
    fb3.calls.clear()
    mv = svc3.run("window_to_monitor", {"window": 101, "target": "next"})
    assert mv.ok
    assert ("move_resize", 101, Rect(2020, 100, 800, 600)) in fb3.calls

    # windows_arrange with 3 windows on monitor 1
    fb4 = FakeBackend()
    svc4 = ComputerService(fb4)
    arr = svc4.run("windows_arrange")
    assert arr.ok and "3 windows on 1 monitors" in arr.message
    mr_calls = [c for c in fb4.calls if c[0] == "move_resize"]
    assert len(mr_calls) == 3
    assert mr_calls[0] == ("move_resize", 103, Rect(0, 28, 960, 1052))

    # Layout save -> move -> restore round trip via temp config dir, missing app reported
    fb5 = FakeBackend()
    svc5 = ComputerService(fb5)
    assert svc5.run("layout_save", {"name": "work"}).ok
    assert svc5.run("window_move", {"window": 101, "x": 600, "y": 400}).ok
    fb5._wins = [w for w in fb5._wins if w.id != 103]   # simulate Code closed
    rest = svc5.run("layout_restore", {"name": "work"})
    assert rest.ok and "Restored 2 windows." in rest.message and "Code" in rest.message
    w101 = next(w for w in fb5.windows() if w.id == 101)
    assert w101.rect == Rect(100, 100, 800, 600)
    assert any(row["name"] == "work" for row in svc5.run("layout_list").data["layouts"])
    assert svc5.run("layout_delete", {"name": "work"}).needs_confirmation
    assert svc5.run("layout_delete", {"name": "work"}, confirmed=True).ok

    # workspace_switch next at last workspace stays
    fb5._desktop = 4
    fb5.calls.clear()
    ws_stay = svc5.run("workspace_switch", {"target": "next"})
    assert ws_stay.ok and "Already on workspace 5" in ws_stay.message
    assert not fb5.calls

    # app_switch
    fb6 = FakeBackend()
    svc6 = ComputerService(fb6)
    sw = svc6.run("app_switch", {"direction": "next"})
    assert sw.ok and ("activate", 102) in fb6.calls

    # pointer_drag releases button even when move raises
    fb_drag = FakeBackend(fail_pointer_move=True)
    dr = ComputerService(fb_drag).run("pointer_drag", {"x": 800, "y": 500})
    assert not dr.ok
    assert ("pointer_button", 1, "down", 1) in fb_drag.calls
    assert ("pointer_button", 1, "up", 1) in fb_drag.calls

    # mic_mute message mentions wake word when muted
    fb7 = FakeBackend()
    mm = ComputerService(fb7).run("mic_mute")
    assert mm.ok and "wake word" in mm.message


def check_tool_service() -> None:
    from lura.modules.tools import ToolService

    fb = FakeBackend()
    svc = ComputerService(fb)
    ts = ToolService()
    ts.add_provider(svc)
    assert svc.owns("desktop_action") and not svc.owns("other")
    assert len(svc.tool_specs()) == 1 and svc.tool_specs()[0].name == "desktop_action"
    out, ended = ts.dispatch("desktop_action", {"action": "window_list"})
    assert not ended and "Terminal — zsh" in out


def main() -> int:
    for name, fn in list(globals().items()):
        if name.startswith("check_"):
            fn()
            print(f"  {name[6:]} OK")
    print("computer OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
