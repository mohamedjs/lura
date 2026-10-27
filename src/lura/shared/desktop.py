"""Starting Lura at login, per operating system.

* Linux: the systemd user unit when one is installed (the .deb and
  install.sh ship it: restart on failure, wait for the audio session), else
  an XDG autostart entry, which every desktop honours.
* macOS: a LaunchAgent.
* Windows: the per-user Run key.
"""

from __future__ import annotations

import logging
import shutil
import subprocess
import sys
from pathlib import Path
from xml.sax.saxutils import escape

from .platform import FROZEN, IS_MAC, IS_WINDOWS

log = logging.getLogger(__name__)

LABEL = "com.lura.assistant"


def launch_command() -> list[str]:
    """How to start the daemon from a login item."""
    if FROZEN:
        # The windowed sibling, so no console window opens at login.
        exe = Path(sys.executable)
        for name in ("LuraApp.exe", "LuraApp"):
            if (exe.parent / name).exists():
                return [str(exe.parent / name), "run"]
        return [str(exe), "run"]
    if exe := shutil.which("lura"):
        return [exe, "run"]
    return [sys.executable, "-m", "lura", "run"]


def _systemd_unit() -> bool:
    if not shutil.which("systemctl"):
        return False
    try:
        return subprocess.run(["systemctl", "--user", "cat", "lura.service"],
                              capture_output=True, timeout=5).returncode == 0
    except Exception:
        return False


def _xdg_entry() -> Path:
    return Path.home() / ".config/autostart/lura.desktop"


def _launch_agent() -> Path:
    return Path.home() / "Library/LaunchAgents" / f"{LABEL}.plist"


def set_autostart(enabled: bool) -> str:
    """Turn the login item on or off. Returns how it was done."""
    cmd = launch_command()
    if IS_WINDOWS:
        import winreg  # type: ignore[import-not-found]

        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Run",
                            0, winreg.KEY_SET_VALUE) as key:
            if enabled:
                winreg.SetValueEx(key, "Lura", 0, winreg.REG_SZ, subprocess.list2cmdline(cmd))
            else:
                try:
                    winreg.DeleteValue(key, "Lura")
                except FileNotFoundError:
                    pass
        return "registry"
    if IS_MAC:
        plist = _launch_agent()
        if enabled:
            plist.parent.mkdir(parents=True, exist_ok=True)
            args = "".join(f"<string>{escape(a)}</string>" for a in cmd)
            plist.write_text(
                '<?xml version="1.0" encoding="UTF-8"?>\n'
                '<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">\n'
                f'<plist version="1.0"><dict><key>Label</key><string>{LABEL}</string>'
                f"<key>ProgramArguments</key><array>{args}</array>"
                "<key>RunAtLoad</key><true/><key>ProcessType</key><string>Interactive</string>"
                "</dict></plist>\n")
        elif plist.exists():
            plist.unlink()
        return "launchagent"
    if _systemd_unit():
        subprocess.run(["systemctl", "--user", "enable" if enabled else "disable", "lura.service"],
                       capture_output=True, timeout=10)
        return "systemd"
    entry = _xdg_entry()
    if enabled:
        entry.parent.mkdir(parents=True, exist_ok=True)
        entry.write_text("[Desktop Entry]\nType=Application\nName=Lura\n"
                         f"Exec={subprocess.list2cmdline(cmd)}\nX-GNOME-Autostart-enabled=true\n"
                         "NoDisplay=true\n")
    elif entry.exists():
        entry.unlink()
    return "xdg-autostart"


def apply_autostart(enabled: bool) -> None:
    try:
        how = set_autostart(enabled)
        log.info("Start at login %s (%s).", "on" if enabled else "off", how)
    except Exception as exc:
        log.warning("Could not change start-at-login: %s", exc)
