"""Which operating system we are on, asked once."""

from __future__ import annotations

import sys

IS_WINDOWS = sys.platform.startswith("win")
IS_MAC = sys.platform == "darwin"
IS_LINUX = sys.platform.startswith("linux")

#: True inside a PyInstaller build. Resources live next to the executable
#: there, not next to the source files.
FROZEN = bool(getattr(sys, "frozen", False))


def os_name() -> str:
    if IS_WINDOWS:
        return "windows"
    if IS_MAC:
        return "macos"
    return "linux"
