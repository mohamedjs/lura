"""X11 desktop backend using EWMH ClientMessages, xprop, xwininfo, xrandr and xdotool."""

from __future__ import annotations

import logging
import os
import shutil
import time

from ..domain import (
    Monitor,
    Rect,
    Window,
    parse_monitors,
    parse_pointer,
    parse_xprop,
    parse_xwininfo,
    prop_ints,
    window_from_props,
)
from . import xlib
from .controls import LinuxControls
from .runner import run

log = logging.getLogger(__name__)

_CLI_PROBES = ("xdotool", "xprop", "xwininfo", "xrandr", "wpctl", "playerctl", "xclip", "loginctl")


class X11Backend:
    """X11 implementation of the desktop backend port."""

    def __init__(self) -> None:
        self._controls = LinuxControls()
        self._probe_cache: dict[str, bool] | None = None
        #: (desktop, monotonic time) of the last switch we asked for, see desktops().
        self._pending_desktop: tuple[int, float] | None = None

    def unsupported_reason(self) -> str:
        return ""

    def _checked(self, argv: list[str], timeout: float = 3.0) -> str:
        res = run(argv, timeout=timeout)
        if not res.ok:
            detail = (res.err or res.out).strip()[:200]
            raise RuntimeError(f"{argv[0]} failed: {detail}")
        return res.out

    def _overlay_key(self) -> str:
        res = run(["gsettings", "get", "org.gnome.mutter", "overlay-key"])
        if not res.ok:
            return ""
        return res.out.strip().strip("'\"")

    def probe(self) -> dict[str, bool]:
        if self._probe_cache is not None:
            return dict(self._probe_cache)
        caps: dict[str, bool] = {"x11": xlib.available()}
        for tool in _CLI_PROBES:
            caps[tool] = bool(shutil.which(tool))
        gsd_ok = False
        if shutil.which("gdbus"):
            try:
                self._controls.brightness()
                gsd_ok = True
            except Exception:
                gsd_ok = False
        caps["gsd_power"] = gsd_ok
        is_gnome = "GNOME" in os.environ.get("XDG_CURRENT_DESKTOP", "").upper()
        caps["gnome"] = bool(is_gnome and shutil.which("gsettings") and self._overlay_key())
        self._probe_cache = caps
        return dict(caps)

    def own_pids(self) -> set[int]:
        return {os.getpid(), os.getppid()}

    # ── read ────────────────────────────────────────────────────────────────

    def windows(self) -> list[Window]:
        out = self._checked(["xprop", "-root", "_NET_CLIENT_LIST_STACKING"])
        ids = prop_ints(parse_xprop(out).get("_NET_CLIENT_LIST_STACKING", ""))
        result: list[Window] = []
        for wid in ids:
            rp = run([
                "xprop", "-id", str(wid),
                "WM_CLASS", "_NET_WM_NAME", "WM_NAME", "_NET_WM_DESKTOP",
                "_NET_WM_STATE", "_NET_WM_WINDOW_TYPE", "_NET_WM_PID", "_NET_FRAME_EXTENTS",
            ])
            ri = run(["xwininfo", "-id", str(wid)])
            if not rp.ok or not ri.ok:
                continue
            props = parse_xprop(rp.out)
            inner = parse_xwininfo(ri.out)
            if inner is None:
                continue
            ext = prop_ints(props.get("_NET_FRAME_EXTENTS", ""))
            extents = (ext[0], ext[1], ext[2], ext[3]) if len(ext) >= 4 else (0, 0, 0, 0)
            result.append(window_from_props(wid, props, inner, extents))
        return result

    def active_window(self) -> int | None:
        out = self._checked(["xprop", "-root", "_NET_ACTIVE_WINDOW"])
        ids = prop_ints(parse_xprop(out).get("_NET_ACTIVE_WINDOW", ""))
        return ids[0] if ids and ids[0] > 0 else None

    def monitors(self) -> list[Monitor]:
        return parse_monitors(self._checked(["xrandr", "--listmonitors"]))

    def workarea(self) -> Rect | None:
        out = self._checked(["xprop", "-root", "_NET_WORKAREA"])
        vals = prop_ints(parse_xprop(out).get("_NET_WORKAREA", ""))
        return Rect(vals[0], vals[1], vals[2], vals[3]) if len(vals) >= 4 else None

    def desktops(self) -> tuple[int, int]:
        out = self._checked(["xprop", "-root", "_NET_CURRENT_DESKTOP", "_NET_NUMBER_OF_DESKTOPS"])
        props = parse_xprop(out)
        cur = prop_ints(props.get("_NET_CURRENT_DESKTOP", ""))
        cnt = prop_ints(props.get("_NET_NUMBER_OF_DESKTOPS", ""))
        current, count = (cur[0] if cur else 0, cnt[0] if cnt else 1)
        # Mutter updates _NET_CURRENT_DESKTOP a moment after a switch (measured: a read right
        # after `set_desktop` still shows the old one), so "switch back" looked like "already
        # there" and two quick swipes moved one step. Trust our own request for up to 1 s.
        if self._pending_desktop:
            wanted, at = self._pending_desktop
            if current == wanted or time.monotonic() - at > 1.0 or wanted >= count:
                self._pending_desktop = None
            else:
                current = wanted
        return (current, count)

    def showing_desktop(self) -> bool:
        out = self._checked(["xprop", "-root", "_NET_SHOWING_DESKTOP"])
        vals = prop_ints(parse_xprop(out).get("_NET_SHOWING_DESKTOP", ""))
        return bool(vals and vals[0] == 1)

    def pointer(self) -> tuple[int, int]:
        out = self._checked(["xdotool", "getmouselocation", "--shell"])
        pos = parse_pointer(out)
        if pos is None:
            raise RuntimeError(f"Could not parse pointer position: {out.strip()[:120]}")
        return pos

    # ── windows & workspaces ────────────────────────────────────────────────

    def activate(self, wid: int) -> None:
        xlib.activate(wid)

    def move_resize(self, wid: int, frame: Rect) -> None:
        # xdotool windowmove lands offset by the frame on Mutter (measured), hence EWMH.
        res = run(["xprop", "-id", str(wid), "_NET_FRAME_EXTENTS"])
        ext = prop_ints(parse_xprop(res.out).get("_NET_FRAME_EXTENTS", "")) if res.ok else []
        left, right, top, bottom = (ext[0], ext[1], ext[2], ext[3]) if len(ext) >= 4 else (0, 0, 0, 0)
        xlib.move_resize(
            wid,
            frame.x,
            frame.y,
            max(1, frame.width - left - right),
            max(1, frame.height - top - bottom),
        )

    def set_maximized(self, wid: int, on: bool) -> None:
        xlib.set_maximized(wid, on)

    def minimize(self, wid: int) -> None:
        self._checked(["xdotool", "windowminimize", str(wid)])

    def close(self, wid: int) -> None:
        xlib.close(wid)

    def set_window_desktop(self, wid: int, desktop: int) -> None:
        self._checked(["xdotool", "set_desktop_for_window", str(wid), str(desktop)])

    def switch_desktop(self, desktop: int) -> None:
        self._checked(["xdotool", "set_desktop", str(desktop)])
        self._pending_desktop = (desktop, time.monotonic())

    def set_showing_desktop(self, on: bool) -> None:
        xlib.showing_desktop(on)

    def overview(self) -> None:
        key = self._overlay_key()
        if not key:
            raise RuntimeError("GNOME overlay key is not set.")
        if key == "Super_L":
            key = "super"
        self._checked(["xdotool", "key", key])

    # ── input ───────────────────────────────────────────────────────────────

    def pointer_move(self, x: int, y: int) -> None:
        self._checked(["xdotool", "mousemove", str(x), str(y)])

    def pointer_button(self, button: int, action: str, count: int = 1) -> None:
        if action == "click":
            self._checked(["xdotool", "click", "--repeat", str(count), "--delay", "60", str(button)])
        elif action == "down":
            self._checked(["xdotool", "mousedown", str(button)])
        elif action == "up":
            self._checked(["xdotool", "mouseup", str(button)])
        else:
            raise RuntimeError(f"Unknown pointer button action: {action}")

    def key(self, combo: str) -> None:
        self._checked(["xdotool", "key", "--clearmodifiers", combo])

    # ── system ──────────────────────────────────────────────────────────────

    def volume(self) -> tuple[float, bool]:
        return self._controls.volume()

    def set_volume(self, fraction: float) -> None:
        self._controls.set_volume(fraction)

    def change_volume(self, delta_percent: int) -> None:
        self._controls.change_volume(delta_percent)

    def toggle_mute(self) -> None:
        self._controls.toggle_mute()

    def mic_muted(self) -> bool:
        return self._controls.mic_muted()

    def toggle_mic(self) -> None:
        self._controls.toggle_mic()

    def brightness(self) -> int:
        return self._controls.brightness()

    def set_brightness(self, percent: int) -> None:
        self._controls.set_brightness(percent)

    def media(self, command: str) -> None:
        self._controls.media(command)

    def lock(self) -> None:
        self._controls.lock()

    def clipboard_get(self) -> str:
        return self._controls.clipboard_get()

    def clipboard_set(self, text: str) -> None:
        self._controls.clipboard_set(text)
