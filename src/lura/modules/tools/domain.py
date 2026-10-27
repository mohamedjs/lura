"""The built-in tools, and what the assistant is never allowed to run.

The guard is enforced here, in the one function every brain and the realtime
mode go through, rather than in a prompt: a prompt is a request, and a model
that ignores it deletes your files. Matching is on the resolved command word
of every command in the string, so ``/bin/rm`` is caught and ``chrome`` is not.
"""

from __future__ import annotations

import os
import shlex

from ..brain import ToolSpec

END_SESSION = "end_session"
#: MCP tools are not declared up front: a hundred schemas is ~12k tokens on
#: every request, which measured 2.7 s of added latency per turn even cached.
#: The model asks for them by description when it needs them.
FIND_TOOLS = "find_tools"


def _spec(name: str, description: str, properties: dict | None = None,
          required: list[str] | None = None) -> ToolSpec:
    return ToolSpec(name, description, {"type": "object", "properties": properties or {},
                                        "required": required or []})


BUILTIN_TOOLS: tuple[ToolSpec, ...] = (
    _spec("run_command",
          "Run a shell command on this computer and return its output. Use it to inspect the "
          "system: CPU, memory, disk, processes, files, logs. Commands that delete or overwrite "
          "data are blocked and will be refused.",
          {"command": {"type": "string", "description": "The command, e.g. 'df -h' or 'ls ~/Downloads'."}},
          ["command"]),
    _spec("get_system_briefing",
          "Weather, CPU temperature, memory, CPU load and internet status, in one call.", {}),
    _spec("list_applications",
          "List applications. running_only=true for open apps, false for installed ones.",
          {"running_only": {"type": "boolean", "description": "True for running apps, false for installed."}}),
    _spec("open_application", "Launch a desktop application by name.",
          {"app_name": {"type": "string", "description": "Application name, e.g. 'chrome', 'spotify', 'terminal'."}},
          ["app_name"]),
    _spec("open_url", "Open a web page, video or search in the user's default browser.",
          {"url": {"type": "string", "description": "A full URL, or words to search the web for."}},
          ["url"]),
    _spec("find_files",
          "Find files or folders on this computer by name. Searches the home folder by default.",
          {"query": {"type": "string", "description": "Part of the file name, e.g. 'invoice' or '.pdf'."},
           "folder": {"type": "string", "description": "Optional folder to search in, e.g. '~/Documents'."}},
          ["query"]),
    _spec(FIND_TOOLS,
          "Search for an extra tool when none of yours can do the job — the browser (Chrome "
          "DevTools: tabs, pages, clicks), fetching live web pages and news, files, GitHub, and "
          "anything else connected. Describe what you need; matching tools become callable next step.",
          {"query": {"type": "string", "description": "What you need, e.g. 'open a new chrome tab'."}},
          ["query"]),
    _spec(END_SESSION,
          "End the conversation. Call it when the user says goodbye, stop, exit, or is done.", {}),
)

# ── the guard ───────────────────────────────────────────────────────────────

#: Commands that destroy data, on every OS we ship to, and the privilege
#: escalators that would let a blocked command back in under another name.
BLOCKED_COMMANDS: frozenset[str] = frozenset({
    # POSIX
    "rm", "rmdir", "unlink", "shred", "srm", "wipe", "wipefs", "dd", "mkfs", "fdisk", "sfdisk",
    "cfdisk", "parted", "mkswap", "blkdiscard", "diskutil", "newfs",
    "sudo", "su", "doas", "pkexec",
    # Windows (cmd and PowerShell)
    "del", "erase", "rd", "format", "format-volume", "diskpart", "clear-disk", "cipher",
    "remove-item", "ri", "remove-itemproperty", "clear-content", "runas",
})

#: Writing to a block device is `rm` for the whole disk.
BLOCKED_REDIRECT_PREFIXES = ("/dev/sd", "/dev/nvme", "/dev/hd", "/dev/mmcblk", "/dev/vd", "/dev/disk",
                             "\\\\.\\physicaldrive")

_SEPARATORS = {";", "&&", "||", "|", "&", "\n"}
#: These take another command as their argument, so keep checking after them.
_WRAPPERS = {"env", "xargs", "nice", "nohup", "time", "timeout", "command", "cmd", "/c",
             "powershell", "pwsh", "-command", "-c", "start-process"}

_REFUSAL = ("I am not allowed to run {cmd} — deleting or overwriting files is blocked. "
            "Ask me to inspect instead, or run that one yourself.")


def _name(token: str) -> str:
    name = os.path.basename(token.replace("\\", "/")).lower()
    return name[:-4] if name.endswith(".exe") else name


def check_command(command: str) -> str | None:
    """Return a refusal to speak, or None if the command may run."""
    # Command substitution hides a whole command inside a word. Refuse rather
    # than parse; the wording tells the model to retry with something simpler.
    if "$(" in command or "`" in command or "${" in command:
        return ("I cannot run commands containing $( ), ${ } or backticks. "
                "Run a simpler command without them, or use get_system_briefing.")
    try:
        lexer = shlex.shlex(command, punctuation_chars=True)
        lexer.whitespace_split = True
        tokens = list(lexer)
    except ValueError:
        return "That command is not quoted correctly, so I will not guess at it."

    expect_command = True
    after_redirect = False
    for token in tokens:
        if after_redirect:   # the word after `>` is the file being written
            after_redirect = False
            if token.strip("\"'").lower().startswith(BLOCKED_REDIRECT_PREFIXES):
                return _REFUSAL.format(cmd="writes straight to a disk device")
            continue
        if token in _SEPARATORS or set(token) <= {";", "&", "|"}:
            expect_command = True
            continue
        if token.startswith(">"):
            target = token.lstrip("><&").strip("\"'")
            if target.lower().startswith(BLOCKED_REDIRECT_PREFIXES):
                return _REFUSAL.format(cmd="writes straight to a disk device")
            after_redirect = not target
            continue
        name = _name(token)
        if expect_command:
            if name in BLOCKED_COMMANDS or name.startswith("mkfs."):
                return _REFUSAL.format(cmd=f"`{name}`")
            if name not in _WRAPPERS:
                expect_command = False
        elif name in BLOCKED_COMMANDS:   # `find -exec rm`, and friends
            return _REFUSAL.format(cmd=f"`{name}`")
    return None
