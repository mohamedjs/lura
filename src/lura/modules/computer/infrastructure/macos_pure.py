"""Pure helpers and constants for the macOS desktop backend (testable on any OS)."""

from __future__ import annotations

from dataclasses import dataclass

from ..domain import Rect, Window

FLAG_SHIFT = 0x20000
FLAG_CONTROL = 0x40000
FLAG_OPTION = 0x80000
FLAG_COMMAND = 0x100000

_MOD_FLAGS: dict[str, int] = {
    "ctrl": FLAG_CONTROL,
    "control": FLAG_CONTROL,
    "shift": FLAG_SHIFT,
    "alt": FLAG_OPTION,
    "option": FLAG_OPTION,
    "opt": FLAG_OPTION,
    "super": FLAG_COMMAND,
    "cmd": FLAG_COMMAND,
    "command": FLAG_COMMAND,
    "meta": FLAG_COMMAND,
    "win": FLAG_COMMAND,
}

#: macOS virtual key codes (HIToolbox/Events.h).
KEYCODES: dict[str, int] = {
    # letters
    "a": 0x00, "s": 0x01, "d": 0x02, "f": 0x03, "h": 0x04, "g": 0x05,
    "z": 0x06, "x": 0x07, "c": 0x08, "v": 0x09, "b": 0x0B, "q": 0x0C,
    "w": 0x0D, "e": 0x0E, "r": 0x0F, "y": 0x10, "t": 0x11, "o": 0x1F,
    "u": 0x20, "i": 0x22, "p": 0x23, "l": 0x25, "j": 0x26, "k": 0x28,
    "n": 0x2D, "m": 0x2E,
    # digits
    "1": 0x12, "2": 0x13, "3": 0x14, "4": 0x15, "6": 0x16, "5": 0x17,
    "9": 0x19, "7": 0x1A, "8": 0x1C, "0": 0x1D,
    # punctuation
    "equal": 0x18, "minus": 0x1B, "bracketright": 0x1E, "bracketleft": 0x21,
    "apostrophe": 0x27, "semicolon": 0x29, "backslash": 0x2A, "comma": 0x2B,
    "slash": 0x2C, "period": 0x2F, "grave": 0x32,
    # navigation & editing
    "return": 0x24, "enter": 0x24, "tab": 0x30, "space": 0x31, "backspace": 0x33,
    "escape": 0x35, "esc": 0x35, "delete": 0x75, "home": 0x73, "end": 0x77,
    "page_up": 0x74, "prior": 0x74, "page_down": 0x79, "next": 0x79,
    "left": 0x7B, "right": 0x7C, "down": 0x7D, "up": 0x7E,
    # function keys
    "f1": 0x7A, "f2": 0x78, "f3": 0x63, "f4": 0x76, "f5": 0x60, "f6": 0x61,
    "f7": 0x62, "f8": 0x64, "f9": 0x65, "f10": 0x6D, "f11": 0x67, "f12": 0x6F,
}

_KEY_ALIASES: dict[str, str] = {
    "pageup": "page_up",
    "pagedown": "page_down",
}

#: Linux shortcuts the computer module sends, translated to what a Mac app expects.
MAC_EQUIVALENTS: dict[str, str] = {
    "ctrl+c": "cmd+c",
    "ctrl+x": "cmd+x",
    "ctrl+v": "cmd+v",
    "ctrl+a": "cmd+a",
    "ctrl+z": "cmd+z",
    "ctrl+shift+z": "cmd+shift+z",
    "ctrl+y": "cmd+shift+z",
    "ctrl+s": "cmd+s",
    "ctrl+f": "cmd+f",
    "ctrl+t": "cmd+t",
    "ctrl+w": "cmd+w",
    "ctrl+l": "cmd+l",
    "ctrl+r": "cmd+r",
    "ctrl+n": "cmd+n",
    "ctrl+q": "cmd+q",
    "alt+left": "cmd+bracketleft",
    "alt+right": "cmd+bracketright",
    "ctrl+page_up": "cmd+shift+bracketleft",
    "ctrl+page_down": "cmd+shift+bracketright",
    "alt+tab": "cmd+tab",
    "alt+f4": "cmd+w",
    "super": "mission_control",
}

SKIP_OWNERS: frozenset[str] = frozenset({
    "Window Server",
    "Dock",
    "Control Center",
    "Notification Center",
    "Spotlight",
    "SystemUIServer",
    "WindowManager",
    "Screenshot",
})


@dataclass(frozen=True)
class MacCombo:
    keycode: int | None
    flags: int
    special: str = ""


def parse_combo(combo: str) -> MacCombo:
    c = combo.strip().lower()
    if not c:
        raise ValueError("Empty key combo.")
    c = MAC_EQUIVALENTS.get(c, c)
    if c == "mission_control":
        return MacCombo(None, 0, "mission_control")
    parts = [p.strip() for p in c.split("+")]
    if not parts or any(not p for p in parts):
        raise ValueError(f"Unknown key: {combo}")
    flags = 0
    for m in parts[:-1]:
        flag = _MOD_FLAGS.get(m)
        if flag is None:
            raise ValueError(f"Unknown modifier: {m}")
        flags |= flag
    last = parts[-1]
    if last in _MOD_FLAGS:
        raise ValueError(f"Unknown key: {last}")
    code = KEYCODES.get(last)
    if code is None:
        alias = _KEY_ALIASES.get(last)
        if alias is not None:
            code = KEYCODES.get(alias)
    if code is None:
        raise ValueError(f"Unknown key: {last}")
    return MacCombo(code, flags)


def parse_volume_settings(text: str) -> tuple[float, bool] | None:
    parts = text.split(",")
    if len(parts) != 2:
        return None
    try:
        level = int(parts[0].strip())
    except ValueError:
        return None
    muted_str = parts[1].strip().lower()
    if muted_str == "true":
        muted = True
    elif muted_str == "false":
        muted = False
    else:
        return None
    frac = max(0.0, min(1.0, level / 100.0))
    return (frac, muted)


def parse_int(text: str) -> int | None:
    s = text.strip()
    if not s or s.lower() == "missing value":
        return None
    try:
        return int(s)
    except ValueError:
        return None


def windows_from_cg(entries: list[dict], own_pids: set[int]) -> list[Window]:
    out: list[Window] = []
    for entry in entries:
        if entry.get("layer", 0) != 0:
            continue
        pid = int(entry.get("pid", 0))
        if pid in own_pids:
            continue
        owner = str(entry.get("owner", ""))
        if owner in SKIP_OWNERS:
            continue
        width = int(entry.get("width", 0))
        height = int(entry.get("height", 0))
        if width < 40 or height < 40:
            continue
        name = str(entry.get("name", ""))
        out.append(Window(
            id=int(entry.get("number", 0)),
            title=name or owner,
            app=owner,
            pid=pid,
            desktop=1,
            rect=Rect(int(entry.get("x", 0)), int(entry.get("y", 0)), width, height),
            maximized=False,
            minimized=False,
            kind="NORMAL",
        ))
    return out


def window_script(pid: int, rect: Rect, body: str) -> str:
    return (
        'tell application "System Events"\n'
        f'  set p to first application process whose unix id is {pid}\n'
        '  set w to missing value\n'
        '  repeat with cand in windows of p\n'
        '    set {cx, cy} to position of cand\n'
        '    set {cw, ch} to size of cand\n'
        f'    set dx to cx - {rect.x}\n'
        f'    set dy to cy - {rect.y}\n'
        f'    set dw to cw - {rect.width}\n'
        f'    set dh to ch - {rect.height}\n'
        '    if dx < 7 and dx > -7 and dy < 7 and dy > -7 '
        'and dw < 7 and dw > -7 and dh < 7 and dh > -7 then\n'
        '      set w to cand\n'
        '      exit repeat\n'
        '    end if\n'
        '  end repeat\n'
        '  if w is missing value then set w to window 1 of p\n'
        f'  {body}\n'
        'end tell'
    )
