"""Open a macOS System Settings Privacy & Security pane."""

from __future__ import annotations

import logging
import subprocess
import sys

log = logging.getLogger(__name__)

PANES: dict[str, str] = {
    "camera": "Privacy_Camera",
    "accessibility": "Privacy_Accessibility",
    "microphone": "Privacy_Microphone",
}


def open_settings(pane: str) -> bool:
    if pane not in PANES or sys.platform != "darwin":
        return False
    url = f"x-apple.systempreferences:com.apple.preference.security?{PANES[pane]}"
    try:
        proc = subprocess.run(["open", url], capture_output=True, timeout=10)
        return proc.returncode == 0
    except Exception:
        return False
