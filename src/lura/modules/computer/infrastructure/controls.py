"""Linux system controls: volume, mic, brightness, media, lock screen and clipboard."""

from __future__ import annotations

import logging

from ..domain import parse_gdbus_int, parse_wpctl_volume
from .runner import run

log = logging.getLogger(__name__)

_GSD_DEST = "org.gnome.SettingsDaemon.Power"
_GSD_PATH = "/org/gnome/SettingsDaemon/Power"
_GSD_SCREEN = "org.gnome.SettingsDaemon.Power.Screen"
_MEDIA_MAP = {
    "play_pause": "play-pause",
    "next": "next",
    "previous": "previous",
    "stop": "stop",
}


class LinuxControls:
    """System-level controls implemented via standard Linux CLI tools."""

    def _checked(self, argv: list[str], timeout: float = 3.0, input_text: str | None = None) -> str:
        res = run(argv, timeout=timeout, input_text=input_text)
        if not res.ok:
            detail = (res.err or res.out).strip()[:200]
            raise RuntimeError(f"{argv[0]} failed: {detail}")
        return res.out

    def volume(self) -> tuple[float, bool]:
        out = self._checked(["wpctl", "get-volume", "@DEFAULT_AUDIO_SINK@"])
        parsed = parse_wpctl_volume(out)
        if parsed is None:
            raise RuntimeError(f"Could not parse volume from wpctl: {out.strip()[:120]}")
        return parsed

    def set_volume(self, fraction: float) -> None:
        self._checked(["wpctl", "set-volume", "-l", "1.0", "@DEFAULT_AUDIO_SINK@", f"{fraction:.2f}"])

    def change_volume(self, delta_percent: int) -> None:
        sign = "+" if delta_percent >= 0 else "-"
        self._checked(["wpctl", "set-volume", "-l", "1.0", "@DEFAULT_AUDIO_SINK@",
                       f"{abs(delta_percent)}%{sign}"])

    def toggle_mute(self) -> None:
        self._checked(["wpctl", "set-mute", "@DEFAULT_AUDIO_SINK@", "toggle"])

    def mic_muted(self) -> bool:
        out = self._checked(["wpctl", "get-volume", "@DEFAULT_AUDIO_SOURCE@"])
        parsed = parse_wpctl_volume(out)
        if parsed is None:
            raise RuntimeError(f"Could not parse mic state from wpctl: {out.strip()[:120]}")
        return parsed[1]

    def toggle_mic(self) -> None:
        self._checked(["wpctl", "set-mute", "@DEFAULT_AUDIO_SOURCE@", "toggle"])

    def brightness(self) -> int:
        out = self._checked([
            "gdbus", "call", "--session",
            "--dest", _GSD_DEST,
            "--object-path", _GSD_PATH,
            "--method", "org.freedesktop.DBus.Properties.Get",
            _GSD_SCREEN, "Brightness",
        ])
        val = parse_gdbus_int(out)
        if val is None:
            raise RuntimeError(f"Could not parse brightness from gdbus: {out.strip()[:120]}")
        return val

    def set_brightness(self, percent: int) -> None:
        self._checked([
            "gdbus", "call", "--session",
            "--dest", _GSD_DEST,
            "--object-path", _GSD_PATH,
            "--method", "org.freedesktop.DBus.Properties.Set",
            _GSD_SCREEN, "Brightness", f"<int32 {percent}>",
        ])

    def media(self, command: str) -> None:
        listing = run(["playerctl", "-l"])
        if not listing.ok or not listing.out.strip():
            raise RuntimeError("No media player is running.")
        self._checked(["playerctl", _MEDIA_MAP[command]])

    def lock(self) -> None:
        self._checked(["loginctl", "lock-session"])

    def clipboard_get(self) -> str:
        res = run(["xclip", "-o", "-selection", "clipboard"])
        if res.ok:
            return res.out
        if not res.out:
            return ""
        detail = (res.err or res.out).strip()[:200]
        raise RuntimeError(f"xclip failed: {detail}")

    def clipboard_set(self, text: str) -> None:
        # xclip forks a child that keeps serving the selection; with captured output that child
        # holds the pipe open and the call hangs until the timeout (measured), so output is discarded.
        res = run(["xclip", "-selection", "clipboard"], timeout=2.0, input_text=text, capture=False)
        if not res.ok:
            raise RuntimeError(f"xclip failed: exit {res.code} {res.err}".strip())
