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
        f"You have tools to execute commands (`run_command`), launch applications (`open_application`), "
        f"and end the session (`end_session`). Use them when asked."
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


GEMINI_TOOLS = [
    types.Tool(
        function_declarations=[
            types.FunctionDeclaration(
                name="run_command",
                description="Execute a bash shell command on the local machine and return stdout and stderr. Use for running ls, checking directories, inspecting files, searching, checking system info, etc.",
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
