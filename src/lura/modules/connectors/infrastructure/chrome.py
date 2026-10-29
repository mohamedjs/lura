"""The user's own Chrome: find it, start it, and hand automation its address.

Chrome 144+ can let tools drive the window you already use — your profile,
your sign-ins, your tabs — once remote debugging is switched on in
``chrome://inspect/#remote-debugging``. While it is on, Chrome writes
``DevToolsActivePort`` (port, then the browser's WebSocket path) into its
user-data folder. chrome-devtools-mcp reads that file itself
(``--autoConnect``); Playwright MCP is handed the address it holds.

Without this, both tools start a Chrome of their own on an empty profile:
the "guest" window that opens next to yours.
"""

from __future__ import annotations

import logging
import os
import shutil
import socket
import subprocess
import time
from pathlib import Path

from ....shared.platform import IS_MAC, IS_WINDOWS

log = logging.getLogger(__name__)

ENABLE_HINT = ("Open Chrome, go to chrome://inspect/#remote-debugging, switch on remote debugging "
               "and allow the connection; then ask again.")


def user_data_dirs() -> list[Path]:
    """Where Chrome (then Chromium) keeps the user's profile on this OS."""
    home = Path.home()
    if IS_MAC:
        base = home / "Library/Application Support"
        return [base / "Google/Chrome", base / "Chromium"]
    if IS_WINDOWS:
        local = Path(os.environ.get("LOCALAPPDATA", home / "AppData/Local"))
        return [local / "Google/Chrome/User Data", local / "Chromium/User Data"]
    config = Path(os.environ.get("XDG_CONFIG_HOME", home / ".config"))
    return [config / "google-chrome", config / "chromium"]


def _port_open(port: int) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.5):
            return True
    except OSError:
        return False


def endpoint() -> str | None:
    """``ws://127.0.0.1:<port>/devtools/browser/<id>`` of the running Chrome, or None
    when it is closed or remote debugging is off (a stale file is ignored)."""
    for folder in user_data_dirs():
        try:
            lines = [l.strip() for l in (folder / "DevToolsActivePort").read_text().splitlines() if l.strip()]
        except OSError:
            continue
        if len(lines) >= 2 and lines[0].isdigit() and _port_open(int(lines[0])):
            return f"ws://127.0.0.1:{lines[0]}{lines[1]}"
    return None


def launch(url: str = "") -> bool:
    """Open the user's Chrome the normal way (their profile), optionally on a
    page. False if Chrome is not installed."""
    extra = [url] if url else []
    try:
        if IS_MAC:
            subprocess.Popen(["open", "-a", "Google Chrome", *extra])
            return True
        if IS_WINDOWS:
            subprocess.Popen(["cmd", "/c", "start", "", "chrome", *extra], creationflags=0x08000000)
            return True
        for name in ("google-chrome", "google-chrome-stable", "chromium", "chromium-browser"):
            if path := shutil.which(name):
                subprocess.Popen([path, *extra], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                 start_new_session=True)
                return True
    except OSError as exc:
        log.warning("Could not start Chrome: %s", exc)
    return False


def ensure_running(timeout: float = 15.0) -> str | None:
    """The endpoint, starting Chrome first if it is closed. None if remote
    debugging stays off (only the user can switch it on)."""
    if url := endpoint():
        return url
    if not launch():
        return None
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        time.sleep(0.5)
        if url := endpoint():
            return url
    return None
