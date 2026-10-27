"""The built-in tools, per operating system."""

from __future__ import annotations

import getpass
import logging
import os
import platform
import shutil
import subprocess
import urllib.parse
import webbrowser
from pathlib import Path

from ....shared.platform import IS_MAC, IS_WINDOWS
from ..domain import check_command

log = logging.getLogger(__name__)

COMMAND_TIMEOUT = 15


def shell_name() -> str:
    if IS_WINDOWS:
        return "PowerShell"
    return os.environ.get("SHELL", "/bin/sh")


def run_command(command: str) -> str:
    if refusal := check_command(command):
        log.warning("Blocked run_command: %s", command)
        return refusal
    log.info("run_command: %s", command)
    argv = (["powershell", "-NoProfile", "-NonInteractive", "-Command", command] if IS_WINDOWS
            else command)
    try:
        res = subprocess.run(argv, shell=not IS_WINDOWS, capture_output=True, text=True,
                             timeout=COMMAND_TIMEOUT, cwd=str(Path.home()),
                             encoding="utf-8", errors="replace")
    except subprocess.TimeoutExpired:
        return f"Error: command timed out after {COMMAND_TIMEOUT} seconds."
    except Exception as exc:
        return f"Error executing command: {exc}"
    out, err = res.stdout.strip(), res.stderr.strip()
    # 127 is "not found". Without a nudge the model announces defeat instead
    # of trying something that would have worked.
    if res.returncode == 127 or "is not recognized" in err:
        return (f"{err or 'Command not found.'}\n[hint] That program is not installed. Try a "
                "different command or get_system_briefing. Do not tell the user you failed "
                "until you have tried an alternative.")
    if not out and not err:
        return f"(exit code {res.returncode}, no output)"
    return "\n".join(p for p in (out, f"[stderr]: {err}" if err else "") if p)[:2000]


# ── applications ────────────────────────────────────────────────────────────

def _desktop_entries() -> dict[str, Path]:
    found: dict[str, Path] = {}
    if IS_MAC:
        for root in (Path("/Applications"), Path("/System/Applications"), Path.home() / "Applications"):
            if root.exists():
                for app in root.glob("*.app"):
                    found[app.stem.lower()] = app
                for app in root.glob("*/*.app"):
                    found.setdefault(app.stem.lower(), app)
    elif IS_WINDOWS:
        roots = [Path(os.environ.get("APPDATA", "")) / "Microsoft/Windows/Start Menu/Programs",
                 Path(os.environ.get("PROGRAMDATA", "C:/ProgramData")) / "Microsoft/Windows/Start Menu/Programs"]
        for root in roots:
            if root.exists():
                for link in root.rglob("*.lnk"):
                    found.setdefault(link.stem.lower(), link)
    else:
        for root in (Path.home() / ".local/share/applications", Path("/usr/share/applications"),
                     Path("/var/lib/flatpak/exports/share/applications"),
                     Path("/var/lib/snapd/desktop/applications")):
            if root.exists():
                for entry in root.glob("*.desktop"):
                    found.setdefault(entry.stem.lower(), entry)
    return found


def _launch(argv: list[str]) -> None:
    kwargs = {"stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL}
    if IS_WINDOWS:
        kwargs["creationflags"] = 0x00000008 | 0x00000200   # DETACHED | NEW_PROCESS_GROUP
    else:
        kwargs["start_new_session"] = True
    subprocess.Popen(argv, **kwargs)


def open_application(app_name: str) -> str:
    app = app_name.strip()
    if not app:
        return "Which application?"
    needle = app.lower()
    entries = _desktop_entries()
    match = entries.get(needle) or next((p for k, p in entries.items() if needle in k), None)
    try:
        if match is not None:
            if IS_MAC:
                _launch(["open", str(match)])
            elif IS_WINDOWS:
                os.startfile(str(match))  # type: ignore[attr-defined]
            else:
                _launch(["gtk-launch", match.stem] if shutil.which("gtk-launch")
                        else ["xdg-open", str(match)])
            return f"Opened {match.stem}."
        if IS_MAC:
            res = subprocess.run(["open", "-a", app], capture_output=True, timeout=5)
            if res.returncode == 0:
                return f"Opened {app}."
        resolved = shutil.which(app) or (str(Path.home() / ".local/bin" / app)
                                         if (Path.home() / ".local/bin" / app).exists() else None)
        if resolved:
            _launch([resolved])
            return f"Launched {app} ({resolved})."
    except Exception as exc:
        return f"Failed to open {app}: {exc}"
    return f"Could not find an application called {app!r}. Try list_applications to see what is installed."


def list_applications(running_only: bool = True) -> str:
    entries = _desktop_entries()
    if not running_only:
        return "Installed applications:\n" + ", ".join(sorted({p.stem for p in entries.values()})[:80])
    try:
        if IS_WINDOWS:
            out = subprocess.run(["tasklist", "/fo", "csv", "/nh"], capture_output=True, text=True,
                                 timeout=8).stdout
            procs = {line.split(",")[0].strip('"').removesuffix(".exe").lower() for line in out.splitlines() if line}
        else:
            out = subprocess.run(["ps", "-u", getpass.getuser(), "-o", "comm="], capture_output=True,
                                 text=True, timeout=5).stdout
            procs = {os.path.basename(line.strip()).lower() for line in out.splitlines() if line.strip()}
    except Exception as exc:
        return f"Error checking processes: {exc}"
    running = sorted({entries[p].stem for p in procs if p in entries} |
                     {p.stem for k, p in entries.items() if any(k == q or (len(q) > 3 and q in k) for q in procs)})
    return ("Running applications:\n" + ", ".join(running[:60])) if running else \
        "No desktop applications detected running."


def open_url(target: str) -> str:
    target = target.strip()
    if not target:
        return "Which page?"
    if " " in target or "." not in target:
        url = "https://www.google.com/search?q=" + urllib.parse.quote_plus(target)
    else:
        url = target if "://" in target else f"https://{target}"
    if not webbrowser.open(url):
        return f"Could not open a browser for {url}."
    return f"Opened {url} in the browser."


_SKIP_DIRS = {".git", "node_modules", ".cache", "__pycache__", ".venv", "venv", "Library",
              "AppData", ".local", ".npm", ".cargo", ".rustup", "snap"}


def find_files(query: str, folder: str = "", limit: int = 25) -> str:
    needle = query.strip().lower()
    if not needle:
        return "What should I look for?"
    root = Path(os.path.expanduser(folder or "~")).resolve()
    if not root.exists():
        return f"There is no folder {root}."
    hits: list[str] = []
    seen = 0
    for current, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if d not in _SKIP_DIRS and not d.startswith(".")]
        for name in dirs + files:
            if needle in name.lower():
                hits.append(str(Path(current) / name))
        seen += 1
        if len(hits) >= limit or seen > 20000:   # a home folder can be huge
            break
    if not hits:
        return f"Nothing named like {query!r} under {root}."
    return f"Found under {root}:\n" + "\n".join(hits[:limit])


# ── machine context for the system prompt ───────────────────────────────────

def machine_context() -> str:
    """Facts about this computer, stable for the life of the process.

    Stable on purpose: it heads every request, and providers cache a repeated
    prefix; a string that varies between turns costs several times as much.
    """
    home = Path.home()
    if IS_WINDOWS:
        os_hint = ("Commands run in PowerShell. Use Get-ChildItem, Get-Process, Get-CimInstance. "
                   "Apps are in the Start Menu.")
    elif IS_MAC:
        os_hint = ("Commands run in the user's shell. Use `open -a` for apps, `sysctl`, `vm_stat`, "
                   "`pmset -g batt`, `system_profiler`.")
    else:
        os_hint = ("For CPU temperature read /sys/class/hwmon/hwmon*/temp*_input (millidegrees) or use "
                   "get_system_briefing; `sensors` may not be installed.")
    return (
        "\n[This computer]\n"
        f"- OS: {platform.system()} {platform.release()} ({platform.machine()})"
        f"{' desktop ' + os.environ['XDG_CURRENT_DESKTOP'] if os.environ.get('XDG_CURRENT_DESKTOP') else ''}\n"
        f"- User: {getpass.getuser()} on {platform.node()}\n"
        f"- Home folder: {home}\n"
        f"- Shell: {shell_name()}\n"
        f"- {os_hint}\n"
        "- Deleting or overwriting data is blocked in code. Inspect freely; never attempt removals.\n"
        "Call a tool immediately when one is needed; never narrate that you are about to.\n"
    )
