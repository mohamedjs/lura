"""The dashboard in its own app window, not a browser tab.

pywebview draws the page with the operating system's own web engine —
WebView2 on Windows (part of Windows 10/11), WKWebView on macOS, WebKitGTK
on Linux — so Lura's settings open as a native window with its own taskbar
entry and no address bar, without bundling a browser.

It runs in a process of its own (``lura window <url>``): pywebview, like Qt,
wants the main thread, and the daemon's main thread already belongs to the
face. If pywebview cannot start (e.g. no WebKitGTK on a Linux box), the next
best thing is a Chromium-family browser in app mode (``--app=``), which also
gives a chromeless window; only then a normal browser tab.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import sys
import webbrowser
from pathlib import Path

from ...shared import paths
from ...shared.platform import FROZEN, IS_MAC, IS_WINDOWS

log = logging.getLogger(__name__)

TITLE = "Lura"
SIZE = (1220, 880)


def show(url: str) -> None:
    """Open the window and block until it is closed. Call from a main thread."""
    try:
        import webview  # pywebview

        storage = paths.data_dir() / "webview"
        storage.mkdir(parents=True, exist_ok=True)
        webview.create_window(TITLE, url, width=SIZE[0], height=SIZE[1], min_size=(420, 560),
                              background_color="#e9ecf3", text_select=True)
        # Not private: the dashboard remembers its theme in localStorage.
        webview.start(private_mode=False, storage_path=str(storage))
        return
    except Exception as exc:
        log.info("Native window unavailable (%s); using an app-mode browser window.", exc)
    app_window_or_browser(url)


def _chromium_family() -> list[str]:
    if IS_WINDOWS:
        roots = [os.environ.get(v, "") for v in ("PROGRAMFILES(X86)", "PROGRAMFILES", "LOCALAPPDATA")]
        rel = ("Microsoft/Edge/Application/msedge.exe", "Google/Chrome/Application/chrome.exe")
        return [str(Path(r) / p) for r in roots if r for p in rel if (Path(r) / p).exists()]
    if IS_MAC:
        apps = ("Google Chrome", "Microsoft Edge", "Brave Browser", "Chromium")
        return [f"/Applications/{a}.app/Contents/MacOS/{a}" for a in apps
                if Path(f"/Applications/{a}.app/Contents/MacOS/{a}").exists()]
    names = ("google-chrome", "google-chrome-stable", "chromium", "chromium-browser", "microsoft-edge",
             "microsoft-edge-stable", "brave-browser")
    return [p for n in names if (p := shutil.which(n))]


def app_window_or_browser(url: str) -> None:
    for browser in _chromium_family():
        try:
            subprocess.Popen([browser, f"--app={url}", f"--window-size={SIZE[0]},{SIZE[1]}"],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                             **({"start_new_session": True} if not IS_WINDOWS else {}))
            return
        except OSError:
            continue
    webbrowser.open(url)


def _self_command() -> list[str]:
    if FROZEN:
        exe = Path(sys.executable)
        for name in ("LuraApp.exe", "LuraApp"):   # windowed: no console flashes up
            if (exe.parent / name).exists():
                return [str(exe.parent / name)]
        return [str(exe)]
    return [sys.executable, "-m", "lura"]


_child: subprocess.Popen | None = None


def launch(url: str) -> None:
    """Open the window from the daemon, without blocking it."""
    global _child
    if _child is not None and _child.poll() is None:
        return   # already open
    try:
        flags = 0x08000000 if IS_WINDOWS else 0   # CREATE_NO_WINDOW
        _child = subprocess.Popen([*_self_command(), "window", url], stdout=subprocess.DEVNULL,
                                  stderr=subprocess.DEVNULL, creationflags=flags)
    except OSError as exc:
        log.warning("Could not start the dashboard window (%s); opening a browser.", exc)
        app_window_or_browser(url)
