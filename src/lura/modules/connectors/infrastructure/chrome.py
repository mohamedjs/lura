"""The user's own Chrome: find it, start it, and hand automation its address.

Chrome 144+ can let tools drive the window you already use — your profile,
your sign-ins, your tabs — once remote debugging is switched on in
``chrome://inspect/#remote-debugging``. While it is on, Chrome writes
``DevToolsActivePort`` (port, then the browser's WebSocket path) into its
user-data folder. A Chrome started with ``--remote-debugging-port`` and its own
``--user-data-dir`` (a launcher script, say) is found from its command line or
port 9222. Both chrome-devtools-mcp (``--wsEndpoint``) and Playwright MCP are
handed the address found here.

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


#: Chrome's usual remote-debugging port, e.g. a launcher with --remote-debugging-port=9222.
DEFAULT_PORT = 9222


def _running_chrome() -> tuple[list[Path], list[int]]:
    """Profile folders and debugging ports named on running Chrome command lines (Linux),
    for a Chrome started with its own --user-data-dir, which the usual folders miss."""
    dirs, ports = [], []
    for cmd in Path("/proc").glob("[0-9]*/cmdline") if Path("/proc").is_dir() else []:
        try:
            args = cmd.read_bytes().decode(errors="ignore").split("\0")
        except OSError:
            continue
        if not args or "chrom" not in Path(args[0]).name:
            continue
        for a in args:
            if a.startswith("--user-data-dir="):
                dirs.append(Path(a.split("=", 1)[1]))
            elif a.startswith("--remote-debugging-port=") and a.split("=", 1)[1].isdigit():
                ports.append(int(a.split("=", 1)[1]))
    return dirs, ports


def _ws_url(port: int) -> str | None:
    """The browser's WebSocket address from Chrome itself (always current)."""
    import json
    import urllib.request
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/json/version", timeout=1) as r:
            return json.loads(r.read()).get("webSocketDebuggerUrl")
    except Exception:
        return None


def endpoint() -> str | None:
    """``ws://127.0.0.1:<port>/devtools/browser/<id>`` of the running Chrome, or None
    when it is closed or remote debugging is off (a stale file is ignored).

    Looks in the usual profile folders, in any profile a running Chrome names, and
    on the usual port — so a Chrome launched with its own profile and port is found too.
    """
    extra_dirs, ports = _running_chrome()
    for folder in [*user_data_dirs(), *extra_dirs]:
        try:
            lines = [l.strip() for l in (folder / "DevToolsActivePort").read_text().splitlines() if l.strip()]
        except OSError:
            continue
        if len(lines) >= 2 and lines[0].isdigit() and _port_open(int(lines[0])):
            return _ws_url(int(lines[0])) or f"ws://127.0.0.1:{lines[0]}{lines[1]}"
    for port in dict.fromkeys([*ports, DEFAULT_PORT]):
        if _port_open(port) and (url := _ws_url(port)):
            return url
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
