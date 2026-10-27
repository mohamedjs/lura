"""The one check that matters: the assistant cannot delete anything.

Run with `python3 tests/test_command_guard.py` — no pytest needed.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from lura.modules.tools import check_command  # noqa: E402

BLOCKED = [
    "rm -rf /",
    "rm -rf ~/Documents",
    "/bin/rm file.txt",
    "  RM  -f x",                      # case and padding
    "rmdir /tmp/x",
    "shred -u secrets.txt",
    "ls && rm -rf ~",                  # second command in the chain
    "ls; rm file",
    "ls || rm file",
    "cat x | xargs rm",                # via xargs
    "env rm -rf /tmp/x",               # via env
    "sudo apt remove foo",             # privilege escalation
    "su -c 'rm x'",
    "pkexec rm x",
    "dd if=/dev/zero of=/dev/sda",
    "mkfs.ext4 /dev/sda1",
    "echo hi > /dev/sda",              # clobber the disk
    "find . -name '*.log' -exec rm {} ;",
    "echo $(rm -rf /tmp/x)",           # command substitution
    "echo `rm x`",
    "nohup rm -rf /tmp/x",
    "timeout 5 rm x",
    # Windows and macOS
    "Remove-Item -Recurse C:\\Users\\me\\Documents",
    "del /s /q C:\\temp",
    "cmd /c rd /s /q C:\\x",
    "powershell -Command Remove-Item x",
    "format D:",
    "diskutil eraseDisk APFS x disk2",
    "C:\\Windows\\System32\\cmd.exe /c del x",
]

ALLOWED = [
    "ls -la",
    "ps aux",
    "sensors",
    "free -h",
    "uptime",
    "cat /proc/cpuinfo",
    "df -h",
    "chrome --version",                # contains "rm", is not rm
    "grep -r firmware /etc",           # "rm" inside a word
    "systemctl --user status lura",
    "ls /tmp && echo done",
    "journalctl -u lura -n 20 | tail",
    "echo hello > /tmp/note.txt",      # ordinary redirect
    "git status",
    "curl -s https://example.com",
    "Get-ChildItem $HOME\\Downloads",
    "Get-Process | Sort-Object CPU",
    "open -a Safari",
    "git log --format=%s -n 3",
]


def main() -> int:
    bad = []
    for cmd in BLOCKED:
        if check_command(cmd) is None:
            bad.append(f"  ALLOWED but must be blocked: {cmd!r}")
    for cmd in ALLOWED:
        refusal = check_command(cmd)
        if refusal is not None:
            bad.append(f"  BLOCKED but must be allowed: {cmd!r} -> {refusal}")

    if bad:
        print("command guard FAILED:")
        print("\n".join(bad))
        return 1
    print(f"command guard OK ({len(BLOCKED)} blocked, {len(ALLOWED)} allowed)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
