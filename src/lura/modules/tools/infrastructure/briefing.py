"""The once-per-boot briefing: weather, temperature, memory, load, internet.

Every probe is best-effort and quick; a missing reading is left out rather
than invented. (The 1.x briefing filled gaps with made-up numbers — a
"55 degrees" that was never measured is worse than saying nothing.)
"""

from __future__ import annotations

import ctypes
import json
import logging
import os
import socket
import subprocess
import time
import urllib.request
from pathlib import Path

from ....shared import paths
from ....shared.platform import IS_LINUX, IS_MAC, IS_WINDOWS

log = logging.getLogger(__name__)


def weather(lang: str) -> str:
    try:
        url = f"https://wttr.in/?format=%C+%t&lang={'ar' if lang == 'ar' else 'en'}"
        with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "curl"}),
                                    timeout=4) as response:
            text = response.read().decode("utf-8", errors="replace").strip()
            return "" if "unknown" in text.lower() or len(text) > 80 else text
    except Exception as exc:
        log.debug("weather: %s", exc)
        return ""


def cpu_temperature() -> str:
    if not IS_LINUX:
        return ""
    for probe in sorted(Path("/sys/class/hwmon").glob("hwmon*/temp*_input")):
        try:
            name = (probe.parent / "name").read_text().strip() if (probe.parent / "name").exists() else ""
            if any(k in name for k in ("coretemp", "k10temp", "cpu", "zenpower")):
                return f"{int(probe.read_text()) / 1000:.0f}°C"
        except (OSError, ValueError):
            continue
    for zone in Path("/sys/class/thermal").glob("thermal_zone*/temp"):
        try:
            return f"{int(zone.read_text()) / 1000:.0f}°C"
        except (OSError, ValueError):
            continue
    return ""


def memory() -> str:
    try:
        if IS_LINUX:
            info = dict(line.split(":", 1) for line in Path("/proc/meminfo").read_text().splitlines())
            total = int(info["MemTotal"].split()[0]) / 1048576
            free = int(info["MemAvailable"].split()[0]) / 1048576
            return f"{total - free:.1f} of {total:.1f} GB used"
        if IS_WINDOWS:
            class Status(ctypes.Structure):
                _fields_ = [("length", ctypes.c_ulong), ("load", ctypes.c_ulong),
                            ("total", ctypes.c_ulonglong), ("avail", ctypes.c_ulonglong),
                            ("tp", ctypes.c_ulonglong), ("ap", ctypes.c_ulonglong),
                            ("tv", ctypes.c_ulonglong), ("av", ctypes.c_ulonglong),
                            ("ae", ctypes.c_ulonglong)]
            st = Status()
            st.length = ctypes.sizeof(Status)
            ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(st))  # type: ignore[attr-defined]
            return f"{(st.total - st.avail) / 2**30:.1f} of {st.total / 2**30:.1f} GB used"
        if IS_MAC:
            total = int(subprocess.run(["sysctl", "-n", "hw.memsize"], capture_output=True,
                                       text=True, timeout=3).stdout) / 2**30
            return f"{total:.0f} GB installed"
    except Exception as exc:
        log.debug("memory: %s", exc)
    return ""


def cpu_load() -> str:
    try:
        one, five, _ = os.getloadavg()
        return f"load {one:.2f} over 1 min, {five:.2f} over 5 min on {os.cpu_count()} cores"
    except (AttributeError, OSError):
        return f"{os.cpu_count()} cores"


def internet() -> str:
    started = time.monotonic()
    try:
        with socket.create_connection(("1.1.1.1", 443), timeout=2):
            return f"online, {int((time.monotonic() - started) * 1000)} ms to the internet"
    except OSError:
        return "offline"


def gather(lang: str) -> dict[str, str]:
    data = {"weather": weather(lang), "cpu_temperature": cpu_temperature(), "memory": memory(),
            "cpu": cpu_load(), "internet": internet()}
    return {k: v for k, v in data.items() if v}


def as_text(data: dict[str, str]) -> str:
    return "\n".join(f"{k.replace('_', ' ').title()}: {v}" for k, v in data.items()) or "No readings."


# ── once per boot / per day ───────────────────────────────────────────────────────────

def _flag() -> Path:
    return paths.data_dir() / "last_briefing"


def boot_time() -> float:
    try:
        if IS_LINUX:
            return time.time() - float(Path("/proc/uptime").read_text().split()[0])
        if IS_WINDOWS:
            ms = ctypes.windll.kernel32.GetTickCount64  # type: ignore[attr-defined]
            ms.restype = ctypes.c_ulonglong
            return time.time() - ms() / 1000
        if IS_MAC:
            out = subprocess.run(["sysctl", "-n", "kern.boottime"], capture_output=True, text=True,
                                 timeout=3).stdout   # "{ sec = 1700000000, usec = 0 } ..."
            return float(out.split("sec =")[1].split(",")[0])
    except Exception:
        pass
    return time.time() - 3600


def due(per: str = "boot") -> bool:
    """Not given since the computer booted (``"boot"``) or since midnight (``"day"``)."""
    try:
        last = float(_flag().read_text().strip())
    except (OSError, ValueError):
        return True
    if per == "day":
        return time.localtime(last)[:3] != time.localtime()[:3]
    return last < boot_time()


def mark_done() -> None:
    try:
        _flag().parent.mkdir(parents=True, exist_ok=True)
        _flag().write_text(str(time.time()))
    except OSError as exc:
        log.warning("Could not write briefing flag: %s", exc)


def github_line(call_tool) -> str:
    """The latest commit, if a connector named ``github`` is connected."""
    try:
        me = json.loads(call_tool("mcp_github_get_me", {}))
        login = me.get("login")
        if not login:
            return ""
        repos = json.loads(call_tool("mcp_github_search_repositories",
                                     {"query": f"user:{login} sort:updated", "perPage": 1}))
        item = (repos.get("items") or [None])[0]
        if not item:
            return ""
        commits = json.loads(call_tool("mcp_github_list_commits",
                                       {"owner": login, "repo": item["name"], "perPage": 1}))
        commit = commits[0]["commit"] if isinstance(commits, list) and commits else {}
        return f"{item['name']}: \"{commit.get('message', '').strip()[:140]}\"" if commit else ""
    except Exception as exc:
        log.debug("github briefing: %s", exc)
        return ""
