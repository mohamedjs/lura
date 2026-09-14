"""Machine tools and system context for Gemini Live."""

from __future__ import annotations

import getpass
import logging
import os
import platform
import shutil
import subprocess
from pathlib import Path

from google.genai import types

log = logging.getLogger(__name__)


def get_machine_context() -> str:
    """Return concise system context so the model understands the host."""
    user = getpass.getuser()
    host = platform.node()
    os_info = f"{platform.system()} {platform.release()}"
    desktop = os.environ.get("XDG_CURRENT_DESKTOP", "unknown")
    shell = os.environ.get("SHELL", "/bin/bash")
    home = str(Path.home())
    cwd = os.getcwd()

    local_bin = Path.home() / ".local/bin"
    local_scripts: list[str] = []
    if local_bin.exists():
        try:
            local_scripts = [
                f.name
                for f in local_bin.iterdir()
                if f.is_file() and os.access(f, os.X_OK)
            ][:30]
        except Exception:
            pass

    scripts_str = ", ".join(sorted(local_scripts)) if local_scripts else "none"

    return (
        f"\n[Host Machine Context]\n"
        f"- OS: {os_info} (Desktop: {desktop})\n"
        f"- Current User: {user}\n"
        f"- Hostname: {host}\n"
        f"- Shell: {shell}\n"
        f"- Home Directory: {home}\n"
        f"- Working Directory: {cwd}\n"
        f"- User scripts in ~/.local/bin: {scripts_str}\n"
        f"- Common search paths for applications: {local_bin}, /usr/local/bin, /usr/bin\n"
        f"You have tools: `run_command` (run shell commands), `list_applications` (list running or installed apps), "
        f"`open_application` (launch apps), and `end_session` (dismiss/exit).\n"
        f"IMPORTANT: Call the tool immediately when asked — never narrate or explain that you are about to run a command."
    )


def run_command(command: str) -> str:
    """Execute a shell command with a timeout and return stdout and stderr."""
    log.info("Executing tool run_command: %s", command)
    try:
        res = subprocess.run(
            command,
            shell=True,
            capture_output=True,
            text=True,
            timeout=15,
            cwd=os.getcwd(),
            env=os.environ.copy(),
        )
        out = res.stdout.strip()
        err = res.stderr.strip()
        if not out and not err:
            return f"(Command executed with exit code {res.returncode}, no output)"
        parts = []
        if out:
            parts.append(out)
        if err:
            parts.append(f"[stderr]: {err}")
        return "\n".join(parts)[:2000]
    except subprocess.TimeoutExpired:
        return "Error: command timed out after 15 seconds."
    except Exception as exc:
        return f"Error executing command: {exc}"


def open_application(app_name: str) -> str:
    """Search for and launch a desktop application or binary in the background."""
    log.info("Executing tool open_application: %s", app_name)
    app = app_name.strip()

    # 1. Check direct binary in PATH or ~/.local/bin
    resolved = shutil.which(app)
    if not resolved:
        local_candidate = Path.home() / ".local/bin" / app
        if local_candidate.exists() and os.access(local_candidate, os.X_OK):
            resolved = str(local_candidate)

    if resolved:
        try:
            subprocess.Popen(
                [resolved],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
            return f"Launched binary {app!r} ({resolved}) in background."
        except Exception as exc:
            return f"Failed to launch binary {resolved}: {exc}"

    # 2. Try gtk-launch or desktop files
    try:
        res = subprocess.run(
            ["gtk-launch", app],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
            timeout=5,
        )
        if res.returncode == 0:
            return f"Launched application {app!r} via gtk-launch."
    except Exception:
        pass

    desktop_dirs = [
        Path.home() / ".local/share/applications",
        Path("/usr/share/applications"),
    ]
    for d in desktop_dirs:
        if not d.exists():
            continue
        for entry in d.glob("*.desktop"):
            if app.lower() in entry.stem.lower():
                try:
                    subprocess.run(["gtk-launch", entry.stem], timeout=5)
                    return f"Launched application {entry.stem!r}."
                except Exception:
                    pass

    return (
        f"Could not find application or binary named {app!r}. "
        f"Searched PATH, ~/.local/bin, and desktop applications."
    )


def list_applications(running_only: bool = True) -> str:
    """List running user desktop applications or all installed applications."""
    log.info("Executing tool list_applications: running_only=%s", running_only)
    desktop_dirs = [
        Path.home() / ".local/share/applications",
        Path("/usr/share/applications"),
    ]
    installed: dict[str, str] = {}
    for d in desktop_dirs:
        if d.exists():
            for f in d.glob("*.desktop"):
                installed[f.stem.lower()] = f.stem

    if not running_only:
        names = sorted(list(set(installed.values())))[:50]
        return "Installed desktop applications:\n" + ", ".join(names)

    # Running user processes
    try:
        user = getpass.getuser()
        res = subprocess.run(
            ["ps", "-u", user, "-o", "comm="],
            capture_output=True,
            text=True,
            timeout=5,
        )
        procs = set(res.stdout.splitlines())
    except Exception as exc:
        return f"Error checking processes: {exc}"

    skip_terms = {
        "daemon", "portal", "proxy", "service", "helper", "pam", "sh",
        "bash", "zsh", "cat", "sort", "ps", "grep", "gsd-", "gvfs",
        "ibus", "at-spi", "pipewire", "wireplumber", "indicator", "dconf",
    }
    active = []
    for p in procs:
        p_clean = p.strip().lower()
        if not p_clean or any(k in p_clean for k in skip_terms):
            continue
        if p_clean in installed or any(p_clean in inst for inst in installed):
            active.append(installed.get(p_clean, p.strip()))

    active = sorted(list(set(active)))
    if not active:
        return "No graphical user applications currently detected running."
    return "Running applications:\n" + ", ".join(active)


def get_system_briefing() -> str:
    """Return a full system briefing: weather, CPU temp, RAM, load, Internet, and GitHub commit."""
    log.info("Executing tool get_system_briefing")
    from .briefing import gather_briefing
    data = gather_briefing()
    return (
        f"Weather: {data['weather']}\n"
        f"CPU Temperature: {data['cpu_temp']}\n"
        f"RAM: {data['ram']}\n"
        f"CPU Status: {data['cpu_load']}\n"
        f"Internet: {data['net']}\n"
        f"Latest GitHub Commit: {data['github']}"
    )


GEMINI_TOOLS = [
    types.Tool(
        function_declarations=[
            types.FunctionDeclaration(
                name="run_command",
                description="Execute a bash shell command on the local machine and return stdout and stderr. Use for running ls, inspecting files, checking system info, etc.",
                parameters=types.Schema(
                    type=types.Type.OBJECT,
                    properties={
                        "command": types.Schema(
                            type=types.Type.STRING,
                            description="The bash command to run, e.g. 'ls -la', 'ps aux', 'cat file.txt'.",
                        )
                    },
                    required=["command"],
                ),
            ),
            types.FunctionDeclaration(
                name="get_system_briefing",
                description="Get full system briefing: weather, CPU temperature, RAM usage, CPU load, internet status, and latest GitHub commit.",
                parameters=types.Schema(
                    type=types.Type.OBJECT,
                    properties={},
                ),
            ),
            types.FunctionDeclaration(
                name="list_applications",
                description="List applications on the system. Use running_only=True to list currently open/running apps, or running_only=False to list installed applications.",
                parameters=types.Schema(
                    type=types.Type.OBJECT,
                    properties={
                        "running_only": types.Schema(
                            type=types.Type.BOOLEAN,
                            description="True for running applications, False for installed applications.",
                        )
                    },
                ),
            ),
            types.FunctionDeclaration(
                name="open_application",
                description="Search for and launch a desktop application, GUI software, or command-line program in the background. Searches PATH, ~/.local/bin, and desktop applications.",
                parameters=types.Schema(
                    type=types.Type.OBJECT,
                    properties={
                        "app_name": types.Schema(
                            type=types.Type.STRING,
                            description="The name of the application or binary to open (e.g. 'claude', 'obs', 'google-chrome', 'antigravity-ide', 'terminal').",
                        )
                    },
                    required=["app_name"],
                ),
            ),
            types.FunctionDeclaration(
                name="end_session",
                description="End the current conversation session. Call this when the user says goodbye, bye, stop listening, exit, dismiss, or indicates they are done talking.",
                parameters=types.Schema(
                    type=types.Type.OBJECT,
                    properties={},
                ),
            ),
        ]
    )
]
