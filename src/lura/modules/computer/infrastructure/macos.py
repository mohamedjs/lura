"""macOS desktop backend using CoreGraphics (quartz), System Events AppleScript and CLI tools."""

from __future__ import annotations

import logging
import os
import time
from dataclasses import replace

from ..domain import (
    LOOK_JPEG_QUALITY,
    LOOK_MAX_SIDE,
    ActionError,
    Monitor,
    Rect,
    Window,
    monitor_for,
    work_area,
)
from . import capture, quartz
from .macos_pure import (
    MacCombo,
    parse_combo,
    parse_int,
    parse_volume_settings,
    window_script,
    windows_from_cg,
)
from .runner import run

log = logging.getLogger(__name__)

_MENU_BAR = 25
_KEY_F11 = 0x67
_BTN_TO_CG = {1: 0, 3: 1, 2: 2}
_SCROLL_DELTA = {4: (1, 0), 5: (-1, 0), 6: (0, 1), 7: (0, -1)}


class MacBackend:
    """macOS implementation of the desktop backend port."""

    name = "macos"

    def __init__(self) -> None:
        self._probe_cache: dict[str, bool] | None = None
        self._held: int | None = None
        self._showing = False
        self._mic_level = 75

    def _osa(self, script: str, timeout: float = 4.0) -> str:
        res = run(["osascript", "-e", script], timeout=timeout)
        if not res.ok:
            detail = (res.err or res.out).strip()[:200]
            raise RuntimeError(f"osascript failed: {detail}")
        return res.out

    def _cg_windows(self) -> list[Window]:
        return windows_from_cg(quartz.window_list(), self.own_pids())

    def _ax_body(self, wid: int, body: str) -> None:
        w = self.window(wid)
        if w is None:
            raise RuntimeError(f"No window {wid}.")
        self._osa(window_script(w.pid, w.rect, body))

    def unsupported_reason(self) -> str:
        return "" if quartz.available() else "macOS CoreGraphics could not be loaded"

    def probe(self) -> dict[str, bool]:
        if self._probe_cache is not None:
            return dict(self._probe_cache)
        ax = quartz.ax_trusted(False)
        # The computer domain's requires tokens are named after Linux tools; on a Mac they
        # mean the same capability: xdotool = input events, xprop = window control — both
        # need Accessibility.
        caps = {
            "x11": True,
            "xprop": ax,
            "xwininfo": True,
            "xrandr": True,
            "xdotool": ax,
            "wpctl": True,
            "gsd_power": False,
            "playerctl": False,
            "loginctl": True,
            "xclip": True,
            "gnome": True,
        }
        if ax:
            self._probe_cache = caps
        return dict(caps)

    def request_permissions(self) -> dict:
        trusted = quartz.ax_trusted(True)
        self._probe_cache = None
        return {"accessibility": trusted}

    def own_pids(self) -> set[int]:
        return {os.getpid(), os.getppid()}

    # ── read ────────────────────────────────────────────────────────────────

    def window(self, wid: int) -> Window | None:
        return next((w for w in self._cg_windows() if w.id == wid), None)

    def windows(self) -> list[Window]:
        # CG returns front-to-back; Backend.windows() is bottom-to-top like X11 stacking.
        ws = list(reversed(self._cg_windows()))
        mons = self.monitors()
        wa = self.workarea()
        if not mons:
            return ws
        out: list[Window] = []
        for w in ws:
            area = work_area(monitor_for(w.rect, mons), wa)
            is_max = (
                abs(w.rect.x - area.x) <= 8
                and abs(w.rect.y - area.y) <= 8
                and abs(w.rect.width - area.width) <= 8
                and abs(w.rect.height - area.height) <= 8
            )
            out.append(replace(w, maximized=is_max) if is_max else w)
        return out

    def active_window(self) -> int | None:
        ws = self._cg_windows()
        return ws[0].id if ws else None

    def monitors(self) -> list[Monitor]:
        return [
            Monitor(index=i, name=f"Display {i + 1}", rect=Rect(x, y, w, h), primary=is_main)
            for i, (_did, x, y, w, h, is_main) in enumerate(quartz.displays())
        ]

    def workarea(self) -> Rect | None:
        ds = quartz.displays()
        if not ds:
            return None
        _did, x, y, w, h, _is_main = ds[0]
        return Rect(x, y + _MENU_BAR, w, max(0, h - _MENU_BAR))

    def desktops(self) -> tuple[int, int]:
        # macOS has no public API for Spaces, so Lura only moves relative to the current one —
        # desktops() reports the current Space as the middle of three so "next"/"prev" resolve to ±1.
        return (1, 3)

    def showing_desktop(self) -> bool:
        return self._showing

    def pointer(self) -> tuple[int, int]:
        return quartz.cursor()

    def capture(self, rect: Rect) -> tuple[bytes, str]:
        if not quartz.screen_capture_allowed(True):
            raise ActionError(
                "Lura needs Screen Recording permission: System Settings → Privacy & Security → "
                "Screen Recording, then restart Lura."
            )
        return capture.grab_mac(rect, LOOK_MAX_SIDE, LOOK_JPEG_QUALITY)

    # ── windows ─────────────────────────────────────────────────────────────

    def activate(self, wid: int) -> None:
        self._ax_body(wid, 'set frontmost of p to true\n  perform action "AXRaise" of w')

    def move_resize(self, wid: int, frame: Rect) -> None:
        body = (
            f"set position of w to {{{frame.x}, {frame.y}}}\n"
            f"  set size of w to {{{frame.width}, {frame.height}}}"
        )
        self._ax_body(wid, body)

    def set_maximized(self, wid: int, on: bool) -> None:
        if not on:
            return   # macOS has no un-maximize state to restore
        w = self.window(wid)
        if w is None:
            raise RuntimeError(f"No window {wid}.")
        mons = self.monitors()
        if not mons:
            raise RuntimeError("No displays found.")
        mon = monitor_for(w.rect, mons)
        area = work_area(mon, self.workarea()) if mon.primary else mon.rect
        self.move_resize(wid, area)

    def minimize(self, wid: int) -> None:
        self._ax_body(wid, 'set value of attribute "AXMinimized" of w to true')

    def close(self, wid: int) -> None:
        self._ax_body(wid, 'click (first button of w whose subrole is "AXCloseButton")')

    def set_window_desktop(self, wid: int, desktop: int) -> None:
        raise NotImplementedError("macOS has no way for apps to move a window to another Space.")

    def switch_desktop(self, desktop: int) -> None:
        steps = desktop - 1
        if steps == 0:
            return
        # Mission Control's default Space shortcuts; a synthetic CGEvent Control+arrow
        # may not switch Spaces, System Events does.
        code = 124 if steps > 0 else 123
        for i in range(abs(steps)):
            self._osa(f'tell application "System Events" to key code {code} using control down')
            if i + 1 < abs(steps):
                time.sleep(0.35)

    def set_showing_desktop(self, on: bool) -> None:
        if on != self._showing:
            quartz.post_key(_KEY_F11, True, 0)
            quartz.post_key(_KEY_F11, False, 0)
            self._showing = on

    def overview(self) -> None:
        res = run(["open", "-a", "Mission Control"])
        if not res.ok:
            detail = (res.err or res.out).strip()[:200]
            raise RuntimeError(f"Mission Control failed: {detail}")

    # ── input ───────────────────────────────────────────────────────────────

    def pointer_move(self, x: int, y: int) -> None:
        btn = _BTN_TO_CG[self._held or 1]
        quartz.post_mouse("drag" if self._held else "move", btn, x, y)

    def pointer_button(self, button: int, action: str, count: int = 1) -> None:
        if button in _SCROLL_DELTA:
            if action != "click":
                return
            dy, dx = _SCROLL_DELTA[button]
            for i in range(count):
                quartz.post_scroll(dy, dx)
                if i + 1 < count:
                    time.sleep(0.015)
            return
        btn = _BTN_TO_CG.get(button)
        if btn is None:
            raise RuntimeError(f"Unknown pointer button: {button}")
        x, y = quartz.cursor()
        if action == "click":
            for i in range(1, count + 1):
                quartz.post_mouse("down", btn, x, y, i)
                quartz.post_mouse("up", btn, x, y, i)
                if i < count:
                    time.sleep(0.06)
        elif action == "down":
            quartz.post_mouse("down", btn, x, y, 1)
            self._held = button
        elif action == "up":
            quartz.post_mouse("up", btn, x, y, 1)
            self._held = None
        else:
            raise RuntimeError(f"Unknown pointer button action: {action}")

    def _post_combo(self, mc: MacCombo) -> None:
        assert mc.keycode is not None
        quartz.post_key(mc.keycode, True, mc.flags)
        quartz.post_key(mc.keycode, False, mc.flags)

    def key(self, combo: str) -> None:
        try:
            mc = parse_combo(combo)
        except ValueError as exc:
            raise RuntimeError(str(exc)) from None
        if mc.special == "mission_control":
            self.overview()
        else:
            self._post_combo(mc)

    # ── system ──────────────────────────────────────────────────────────────

    def volume(self) -> tuple[float, bool]:
        out = self._osa(
            'set s to get volume settings\n'
            'return (output volume of s as text) & "," & (output muted of s as text)'
        )
        parsed = parse_volume_settings(out)
        if parsed is None:
            raise RuntimeError(f"Could not parse volume settings: {out.strip()[:100]}")
        return parsed

    def set_volume(self, fraction: float) -> None:
        pct = max(0, min(100, round(fraction * 100)))
        self._osa(f"set volume output volume {pct}")

    def change_volume(self, delta_percent: int) -> None:
        frac, _ = self.volume()
        pct = max(0, min(100, round(frac * 100) + delta_percent))
        self.set_volume(pct / 100.0)

    def toggle_mute(self) -> None:
        self._osa("set volume output muted not (output muted of (get volume settings))")

    def mic_muted(self) -> bool:
        return parse_int(self._osa("input volume of (get volume settings)")) == 0

    def toggle_mic(self) -> None:
        level = parse_int(self._osa("input volume of (get volume settings)"))
        if level is None:
            raise RuntimeError("Could not read microphone volume.")
        if level > 0:
            self._mic_level = level
            self._osa("set volume input volume 0")
        else:
            self._osa(f"set volume input volume {self._mic_level}")

    def brightness(self) -> int:
        raise NotImplementedError("Brightness control is not available on macOS yet.")

    def set_brightness(self, percent: int) -> None:
        raise NotImplementedError("Brightness control is not available on macOS yet.")

    def media(self, command: str) -> None:
        raise NotImplementedError("Media keys are not available on macOS yet.")

    def lock(self) -> None:
        res = run(["pmset", "displaysleepnow"])
        if not res.ok:
            detail = (res.err or res.out).strip()[:200]
            raise RuntimeError(f"pmset failed: {detail}")

    def clipboard_get(self) -> str:
        res = run(["pbpaste"])
        if not res.ok:
            detail = (res.err or res.out).strip()[:200]
            raise RuntimeError(f"pbpaste failed: {detail}")
        return res.out

    def clipboard_set(self, text: str) -> None:
        res = run(["pbcopy"], input_text=text)
        if not res.ok:
            detail = (res.err or res.out).strip()[:200]
            raise RuntimeError(f"pbcopy failed: {detail}")
