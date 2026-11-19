"""Fallback backend for platforms or display sessions without desktop control yet."""

from __future__ import annotations

import logging

from ..domain import Monitor, Rect, Window

log = logging.getLogger(__name__)


class UnsupportedBackend:
    """Reports why desktop control is unavailable and refuses backend operations."""

    def __init__(self, reason: str) -> None:
        self._reason = reason

    def unsupported_reason(self) -> str:
        return self._reason

    def probe(self) -> dict[str, bool]:
        return {}

    def own_pids(self) -> set[int]:
        raise NotImplementedError(self._reason)

    def windows(self) -> list[Window]:
        raise NotImplementedError(self._reason)

    def active_window(self) -> int | None:
        raise NotImplementedError(self._reason)

    def monitors(self) -> list[Monitor]:
        raise NotImplementedError(self._reason)

    def workarea(self) -> Rect | None:
        raise NotImplementedError(self._reason)

    def desktops(self) -> tuple[int, int]:
        raise NotImplementedError(self._reason)

    def showing_desktop(self) -> bool:
        raise NotImplementedError(self._reason)

    def pointer(self) -> tuple[int, int]:
        raise NotImplementedError(self._reason)

    def activate(self, wid: int) -> None:
        raise NotImplementedError(self._reason)

    def move_resize(self, wid: int, frame: Rect) -> None:
        raise NotImplementedError(self._reason)

    def set_maximized(self, wid: int, on: bool) -> None:
        raise NotImplementedError(self._reason)

    def minimize(self, wid: int) -> None:
        raise NotImplementedError(self._reason)

    def close(self, wid: int) -> None:
        raise NotImplementedError(self._reason)

    def set_window_desktop(self, wid: int, desktop: int) -> None:
        raise NotImplementedError(self._reason)

    def switch_desktop(self, desktop: int) -> None:
        raise NotImplementedError(self._reason)

    def set_showing_desktop(self, on: bool) -> None:
        raise NotImplementedError(self._reason)

    def overview(self) -> None:
        raise NotImplementedError(self._reason)

    def pointer_move(self, x: int, y: int) -> None:
        raise NotImplementedError(self._reason)

    def pointer_button(self, button: int, action: str, count: int = 1) -> None:
        raise NotImplementedError(self._reason)

    def key(self, combo: str) -> None:
        raise NotImplementedError(self._reason)

    def volume(self) -> tuple[float, bool]:
        raise NotImplementedError(self._reason)

    def set_volume(self, fraction: float) -> None:
        raise NotImplementedError(self._reason)

    def change_volume(self, delta_percent: int) -> None:
        raise NotImplementedError(self._reason)

    def toggle_mute(self) -> None:
        raise NotImplementedError(self._reason)

    def mic_muted(self) -> bool:
        raise NotImplementedError(self._reason)

    def toggle_mic(self) -> None:
        raise NotImplementedError(self._reason)

    def brightness(self) -> int:
        raise NotImplementedError(self._reason)

    def set_brightness(self, percent: int) -> None:
        raise NotImplementedError(self._reason)

    def media(self, command: str) -> None:
        raise NotImplementedError(self._reason)

    def lock(self) -> None:
        raise NotImplementedError(self._reason)

    def clipboard_get(self) -> str:
        raise NotImplementedError(self._reason)

    def clipboard_set(self, text: str) -> None:
        raise NotImplementedError(self._reason)
