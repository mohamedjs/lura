"""Machine tools and system context for Gemini Live."""

from __future__ import annotations

import functools
import getpass
import logging
import os
import platform
import shlex
import shutil
import subprocess
from pathlib import Path

from google.genai import types

log = logging.getLogger(__name__)


@functools.lru_cache(maxsize=1)
def get_machine_context() -> str:
    """Return concise system context so the model understands the host.

    Cached for the life of the process for two reasons: it shells out and walks
    a directory, and it is the head of every request's prompt. Google caches a
    repeated prefix implicitly, so a string that varies between turns quietly
    costs seven times as much as one that does not.
    """
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

    # Which GitHub account this machine pushes as. Without it the model guesses
    # a username from the user's name and searches for a person who is not them.
    # Asked of the package's own checkout, not the working directory: the
    # systemd service runs from $HOME, where there is no git remote and the
    # answer would silently be blank — frozen that way by the cache above.
    repo = Path(__file__).resolve().parents[2]
    github = ""
    try:
        remote = subprocess.run(["git", "remote", "get-url", "origin"], cwd=repo,
                                capture_output=True, text=True, timeout=5)
        if remote.returncode == 0 and (url := remote.stdout.strip()):
            owner = url.rstrip("/").removesuffix(".git").replace(":", "/").split("/")[-2]
            github = f"- GitHub account: {owner}\n"
    except Exception:
        pass

    return (
        f"\n[Host Machine Context]\n"
        f"- OS: {os_info} (Desktop: {desktop})\n"
        f"- Current User: {user}\n"
        f"- Hostname: {host}\n"
        f"- Shell: {shell}\n"
        f"- Home Directory: {home}\n"
        f"- Working Directory: {cwd}\n"
        f"- User scripts in ~/.local/bin: {scripts_str}\n"
        f"{github}"
        f"- Common search paths for applications: {local_bin}, /usr/local/bin, /usr/bin\n"
        f"You have tools: `run_command` (run shell commands), `get_system_briefing` (weather, CPU "
        f"temperature, RAM, load, internet, latest commit), `list_applications` (list running or "
        f"installed apps), `open_application` (launch apps), and `end_session` (dismiss/exit).\n"
        # `sensors` is not installed here and the model reaches for it first,
        # then gives up rather than trying the files that do exist.
        f"- CPU temperature: use `get_system_briefing`, or read /sys/class/hwmon/hwmon*/temp*_input "
        f"(millidegrees). `sensors` is NOT installed on this machine.\n"
        f"- Deleting or overwriting data is blocked in code. Inspect freely; do not attempt removals.\n"
        f"IMPORTANT: Call the tool immediately when asked — never narrate or explain that you are about to run a command.\n"
        f"IMPORTANT: Reply in the SAME LANGUAGE the user spoke. Egyptian Arabic in, Egyptian Arabic out."
    )


# ── What the assistant is not allowed to run ────────────────────────────────
#
# Enforced here, in the one function both providers call, rather than in a
# system prompt: a prompt is a request, and a model that ignores it deletes
# your files. Matching is on the resolved command word of every command in the
# string, so `/bin/rm` is caught and `chrome` is not.

#: Commands that destroy data, and the privilege escalators that would let a
#: blocked command back in under another name.
BLOCKED_COMMANDS: frozenset[str] = frozenset({
    "rm", "rmdir", "unlink", "shred", "srm", "wipe", "wipefs",
    "dd", "mkfs", "fdisk", "sfdisk", "cfdisk", "parted", "mkswap", "blkdiscard",
    "sudo", "su", "doas", "pkexec",
})

#: Writing to a block device is `rm` for the whole disk.
BLOCKED_REDIRECT_PREFIXES = ("/dev/sd", "/dev/nvme", "/dev/hd", "/dev/mmcblk", "/dev/vd")

#: Shell tokens that start a new command, so every segment gets checked, not
#: just the first: `ls && rm -rf ~` is two commands in one string.
_SEPARATORS = {";", "&&", "||", "|", "&", "\n"}

_REFUSAL = (
    "I am not allowed to run {cmd} — deleting or overwriting files is blocked. "
    "Ask me to inspect instead, or run that one yourself."
)


def check_command(command: str) -> str | None:
    """Return a refusal to speak, or None if the command may run.

    Split out from :func:`run_command` so it is testable on its own.
    """
    # Command substitution hides a whole command inside a word. Rather than
    # parse it, refuse: the assistant has no reason to need it.
    if "$(" in command or "`" in command or "${" in command:
        return _REFUSAL.format(cmd="commands that build themselves from other commands")

    try:
        # punctuation_chars makes the lexer emit ; && || | & as their own
        # tokens instead of gluing them onto the neighbouring word.
        lexer = shlex.shlex(command, punctuation_chars=True)
        lexer.whitespace_split = True
        tokens = list(lexer)
    except ValueError:
        return "That command is not quoted correctly, so I will not guess at it."

    expect_command = True
    after_redirect = False
    for token in tokens:
        # The word right after a `>` is the file being written, not a command.
        if after_redirect:
            after_redirect = False
            if token.strip("\"'").startswith(BLOCKED_REDIRECT_PREFIXES):
                return _REFUSAL.format(cmd="writes straight to a disk device")
            continue

        if token in _SEPARATORS or set(token) <= {";", "&", "|"}:
            expect_command = True
            continue

        if token.startswith(">"):
            target = token.lstrip("><&").strip("\"'")
            if target.startswith(BLOCKED_REDIRECT_PREFIXES):
                return _REFUSAL.format(cmd="writes straight to a disk device")
            after_redirect = not target
            continue

        if expect_command:
            # `rm`, `/bin/rm`, `env rm`, `xargs rm` all resolve to the name.
            name = os.path.basename(token).lower()
            if name in BLOCKED_COMMANDS or name.startswith("mkfs."):
                return _REFUSAL.format(cmd=f"`{name}`")
            # These take another command as their argument, so keep checking.
            if name not in ("env", "xargs", "nice", "nohup", "time", "timeout", "command"):
                expect_command = False
        else:
            # A bare `-exec rm` inside find, and friends.
            if os.path.basename(token).lower() in BLOCKED_COMMANDS:
                return _REFUSAL.format(cmd=f"`{os.path.basename(token).lower()}`")

    return None


def run_command(command: str) -> str:
    """Execute a shell command with a timeout and return stdout and stderr."""
    refusal = check_command(command)
    if refusal:
        log.warning("Blocked run_command: %s", command)
        return refusal

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

        # 127 is "not found". Without a nudge the model announces defeat to the
        # user instead of trying the tool that would have answered them.
        if res.returncode == 127:
            return (
                f"{err or 'Command not found.'}\n"
                "[hint] That program is not installed. Try a different command, or "
                "`get_system_briefing` for CPU temperature, RAM, load and internet. "
                "Do not tell the user you failed until you have tried an alternative."
            )

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


# ── The same tools, in OpenAI shape, for the OpenRouter path ────────────────
#
# Declared from the Gemini list rather than retyped: two hand-maintained copies
# of the same five tools drift, and the drift shows up as a model calling a
# tool that the other provider does not have.

def _openai_tool(name: str, description: str, properties: dict, required: list[str] | None = None) -> dict:
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {
                "type": "object",
                "properties": properties,
                "required": required or [],
            },
        },
    }


OPENAI_TOOLS: list[dict] = [
    _openai_tool(
        "run_command",
        "Run a bash command on this machine and return stdout and stderr. Use it to "
        "inspect the system: CPU, temperature, memory, disk, processes, files, logs. "
        "Commands that delete or overwrite data are blocked and will be refused.",
        {"command": {"type": "string", "description": "The bash command, e.g. 'sensors', 'free -h', 'ls -la'."}},
        ["command"],
    ),
    _openai_tool(
        "get_system_briefing",
        "Full system briefing: weather, CPU temperature, RAM, CPU load, internet status, "
        "and the latest GitHub commit.",
        {},
    ),
    _openai_tool(
        "list_applications",
        "List applications. running_only=true for currently open apps, false for installed ones.",
        {"running_only": {"type": "boolean", "description": "True for running apps, False for installed."}},
    ),
    _openai_tool(
        "open_application",
        "Launch a desktop application or binary in the background.",
        {"app_name": {"type": "string", "description": "Application or binary name, e.g. 'chrome', 'obs'."}},
    ),
    _openai_tool(
        "find_tools",
        "Search for an extra tool when none of your own can do the job — GitHub, "
        "the browser, the database, and anything else connected to this machine. "
        "Call it with a plain description of what you need, and the matching tools "
        "become available for you to call on your next step.",
        {"query": {"type": "string",
                   "description": "What you need to do, e.g. 'list my github pull requests'."}},
        ["query"],
    ),
    _openai_tool(
        "end_session",
        "End the conversation. Call this when the user says goodbye, bye, stop, exit, or is done talking.",
        {},
    ),
]

#: Name of the tool that ends a conversation, so callers do not hardcode it.
END_SESSION = "end_session"

#: Name of the tool that pulls MCP tools in on demand. They are not declared
#: up front: a hundred of them is ~12k tokens of schema on every request, which
#: measured 2.7 seconds of added latency per turn whether or not the prompt was
#: cached. Caching makes them cheap; it does not make them fast.
FIND_TOOLS = "find_tools"


def dispatch(name: str, args: dict | None, mcp_manager=None) -> str:
    """Run one tool by name and return what to hand back to the model.

    Local tools first, then MCP. Never raises: a tool that blows up must come
    back as text the model can talk about, not as a dead conversation.
    """
    args = args or {}
    try:
        if name == "run_command":
            return run_command(str(args.get("command", "")))
        if name == "get_system_briefing":
            return get_system_briefing()
        if name == "list_applications":
            return list_applications(bool(args.get("running_only", True)))
        if name == "open_application":
            return open_application(str(args.get("app_name", "")))
        if name == END_SESSION:
            return "Session ending now. Say a brief friendly goodbye."
        if name == FIND_TOOLS:
            # The caller attaches the matches; this only reports them.
            if mcp_manager is None:
                return "No extra tools are connected right now."
            found = mcp_manager.search_openai_tools(str(args.get("query", "")))
            if not found:
                return "No extra tool matches that. Try run_command instead."
            listing = "\n".join(
                f"- {t['function']['name']}: {t['function'].get('description', '')[:120]}"
                for t in found
            )
            return f"These tools are now available to call:\n{listing}"
        if mcp_manager is not None:
            return mcp_manager.call_tool(name, args)
        return f"Error: unknown tool {name}."
    except Exception as exc:
        log.warning("Tool %s failed: %s", name, exc)
        return f"Error running {name}: {exc}"
