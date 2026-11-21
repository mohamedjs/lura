"""Checks for the macOS backend helpers, bindings, camera auth and privacy panes."""

import sys
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC))

from lura.modules.air.infrastructure import macos_camera  # noqa: E402
from lura.modules.computer.domain import Rect, is_target  # noqa: E402
from lura.modules.computer.infrastructure import macos_pure, quartz  # noqa: E402
from lura.modules.computer.infrastructure.macos import MacBackend  # noqa: E402
from lura.modules.computer.infrastructure.macos_pure import (  # noqa: E402
    FLAG_COMMAND,
    FLAG_CONTROL,
    FLAG_SHIFT,
    MacCombo,
    parse_combo,
    parse_int,
    parse_volume_settings,
    window_script,
    windows_from_cg,
)
from lura.shared import macos_privacy  # noqa: E402


def _assert_raises(fn, *args) -> None:
    try:
        fn(*args)
    except ValueError:
        return
    raise AssertionError(f"Expected ValueError from {fn.__name__}({args!r})")


def main() -> int:
    # parse_combo
    assert parse_combo("ctrl+c") == MacCombo(0x08, FLAG_COMMAND)
    assert parse_combo("alt+Left") == MacCombo(0x21, FLAG_COMMAND)
    assert parse_combo("super").special == "mission_control"
    assert parse_combo("ctrl+shift+Tab") == MacCombo(0x30, FLAG_CONTROL | FLAG_SHIFT)
    assert parse_combo("F5").keycode == 0x60
    assert parse_combo("ctrl+Page_Down") == MacCombo(0x1E, FLAG_COMMAND | FLAG_SHIFT)
    assert parse_combo("cmd+space").keycode == 0x31
    assert parse_combo("ctrl+Right") == MacCombo(0x7C, FLAG_CONTROL)
    _assert_raises(parse_combo, "foo")
    _assert_raises(parse_combo, "hyper+a")

    # volume / int parsers
    assert parse_volume_settings("50,false") == (0.5, False)
    assert parse_volume_settings("100, true") == (1.0, True)
    assert parse_volume_settings("x") is None
    assert parse_int("missing value") is None
    assert parse_int(" 42\n") == 42

    # windows_from_cg
    entries = [
        {"number": 1, "pid": 10, "owner": "Dock", "name": "", "layer": 0,
         "x": 0, "y": 0, "width": 1920, "height": 80, "onscreen": True},
        {"number": 2, "pid": 20, "owner": "Safari", "name": "Menu", "layer": 25,
         "x": 0, "y": 0, "width": 200, "height": 200, "onscreen": True},
        {"number": 3, "pid": 999, "owner": "Python", "name": "Own", "layer": 0,
         "x": 0, "y": 0, "width": 400, "height": 400, "onscreen": True},
        {"number": 4, "pid": 30, "owner": "Finder", "name": "Tiny", "layer": 0,
         "x": 10, "y": 10, "width": 30, "height": 30, "onscreen": True},
        {"number": 5, "pid": 40, "owner": "Safari", "name": "Docs", "layer": 0,
         "x": 100, "y": 50, "width": 900, "height": 700, "onscreen": True},
        {"number": 6, "pid": 50, "owner": "Notes", "name": "", "layer": 0,
         "x": 200, "y": 100, "width": 600, "height": 500, "onscreen": True},
    ]
    ws = windows_from_cg(entries, {999})
    assert len(ws) == 2
    assert [w.id for w in ws] == [5, 6]
    assert ws[0].title == "Docs" and ws[0].app == "Safari"
    assert ws[1].title == "Notes" and ws[1].app == "Notes"
    assert all(w.kind == "NORMAL" and w.desktop == 1 and is_target(w, {999}) for w in ws)

    # MacBackend.windows() returns bottom-to-top while active_window() / window() use front-to-back
    orig_wl, orig_disp = quartz.window_list, quartz.displays
    try:
        quartz.window_list = lambda: entries
        quartz.displays = lambda: [(1, 0, 0, 1440, 900, True)]
        mb = MacBackend()
        mb.own_pids = lambda: {999}
        assert mb.active_window() == 5
        assert mb.window(5) is not None and mb.window(5).title == "Docs"
        assert [w.id for w in mb.windows()] == [6, 5]
    finally:
        quartz.window_list, quartz.displays = orig_wl, orig_disp

    # window_script
    body = 'perform action "AXRaise" of w'
    script = window_script(123, Rect(10, 20, 300, 400), body)
    assert "unix id is 123" in script
    assert "cx - 10" in script and "cy - 20" in script
    assert "cw - 300" in script and "ch - 400" in script
    assert body in script

    # Non-macOS / cross-platform checks
    assert macos_privacy.open_settings("nope") is False
    if sys.platform != "darwin":
        assert quartz.available() is False
        assert macos_camera.authorization() == "authorized"
        assert macos_privacy.open_settings("camera") is False
    else:
        backend = MacBackend()
        assert backend.unsupported_reason() == ""
        assert len(backend.monitors()) > 0
        ptr = backend.pointer()
        assert isinstance(ptr, tuple) and len(ptr) == 2
        assert isinstance(ptr[0], int) and isinstance(ptr[1], int)

    assert macos_pure.SKIP_OWNERS
    print("macos OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
