"""Backend selection for the current operating system and display session."""

from __future__ import annotations

import logging
import os
from typing import TYPE_CHECKING

from ....shared.platform import IS_LINUX, IS_MAC, IS_WINDOWS
from .unsupported import UnsupportedBackend
from .x11 import X11Backend

if TYPE_CHECKING:
    from ..application import Backend

log = logging.getLogger(__name__)


def default_backend() -> Backend:
    """Pick the desktop backend for this session; never raises."""
    if IS_MAC:
        from .macos import MacBackend
        return MacBackend()
    if IS_WINDOWS:
        return UnsupportedBackend("Windows is not supported yet")
    if not IS_LINUX:
        return UnsupportedBackend("this operating system is not supported yet")
    session_type = os.environ.get("XDG_SESSION_TYPE", "").strip().lower()
    has_display = bool(os.environ.get("DISPLAY"))
    has_wayland = bool(os.environ.get("WAYLAND_DISPLAY"))
    if session_type == "wayland" or (has_wayland and not has_display):
        return UnsupportedBackend("this is a Wayland session; only X11 is supported for now")
    if not has_display:
        return UnsupportedBackend("there is no X display (DISPLAY is not set)")
    return X11Backend()
