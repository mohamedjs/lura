"""Pure desktop action domain: shapes, catalog, validation, geometry and output parsers."""

from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass, field

from ..brain import ToolSpec

SAFE, CONFIRM = "safe", "confirm"

WIN_REQ = ("x11", "xprop", "xwininfo", "xrandr")


class ActionError(ValueError):
    """A validation or targeting error with a sentence the model can read."""


@dataclass(frozen=True)
class Rect:
    x: int
    y: int
    width: int
    height: int

    @property
    def center(self) -> tuple[int, int]:
        return (self.x + self.width // 2, self.y + self.height // 2)

    def intersect(self, other: Rect) -> Rect | None:
        x1 = max(self.x, other.x)
        y1 = max(self.y, other.y)
        x2 = min(self.x + self.width, other.x + other.width)
        y2 = min(self.y + self.height, other.y + other.height)
        if x2 <= x1 or y2 <= y1:
            return None
        return Rect(x1, y1, x2 - x1, y2 - y1)

    def overlap(self, other: Rect) -> int:
        inter = self.intersect(other)
        return inter.width * inter.height if inter else 0

    def translate(self, dx: int, dy: int) -> Rect:
        return Rect(self.x + dx, self.y + dy, self.width, self.height)


@dataclass(frozen=True)
class Monitor:
    index: int
    name: str
    rect: Rect
    primary: bool


@dataclass(frozen=True)
class Window:
    id: int
    title: str
    app: str
    pid: int
    desktop: int
    rect: Rect
    maximized: bool
    minimized: bool
    kind: str


@dataclass(frozen=True)
class WindowGrip:
    id: int
    title: str
    start: Rect           # frame rect at grip time (after un-maximizing)
    bounds: Rect          # union of monitors at grip time


@dataclass(frozen=True)
class Param:
    kind: str
    required: bool = False
    lo: int | None = None
    hi: int | None = None
    choices: tuple[str, ...] = ()
    default: object = None
    max_len: int = 200


@dataclass(frozen=True)
class ActionSpec:
    name: str
    group: str
    summary: str
    safety: str = SAFE
    params: dict[str, Param] = field(default_factory=dict)
    requires: tuple[str, ...] = ()


@dataclass(frozen=True)
class LayoutEntry:
    app: str
    title: str
    desktop: int
    rect: Rect
    maximized: bool


@dataclass(frozen=True)
class Layout:
    name: str
    saved_at: str
    windows: tuple[LayoutEntry, ...]


@dataclass(frozen=True)
class PresetApp:
    launch: str
    match: str
    where: str = ""
    rect: Rect | None = None
    monitor: int = 0
    workspace: int = 0


@dataclass(frozen=True)
class Preset:
    name: str
    apps: tuple[PresetApp, ...]
    saved_at: str


@dataclass
class ActionResult:
    ok: bool
    message: str
    data: dict = field(default_factory=dict)
    needs_confirmation: bool = False
    ms: float = 0.0

    def to_text(self) -> str:
        text = self.message
        if self.data:
            text = f"{text}\n{json.dumps(self.data, ensure_ascii=False, separators=(',', ':'))}"
        return text[:4000]


_W: dict[str, Param] = {
    "window": Param("target", default="active"),
    "app": Param("str", max_len=80),
}
_SNAP_CHOICES = (
    "left", "right", "top", "bottom",
    "top_left", "top_right", "bottom_left", "bottom_right",
    "center", "full",
)
_BUTTON_CHOICES = ("left", "right", "middle")
_SCROLL_CHOICES = ("up", "down", "left", "right")
_MEDIA_CHOICES = ("play_pause", "next", "previous", "stop")

ACTIONS: tuple[ActionSpec, ...] = (
    # info
    ActionSpec("capabilities", "info",
               "List available desktop actions on this computer."),
    ActionSpec("window_list", "info",
               "List open controllable windows with ids, titles, workspaces and geometry.",
               requires=WIN_REQ),
    ActionSpec("monitors", "info",
               "List connected monitors and their screen rectangles.",
               requires=("xrandr",)),
    ActionSpec("pointer_position", "info",
               "Return the mouse pointer's current x,y coordinates.",
               requires=("xdotool",)),
    # window
    ActionSpec("window_focus", "window",
               "Bring a window to the front and focus it.",
               params={**_W}, requires=WIN_REQ),
    ActionSpec("window_move", "window",
               "Move a window so its top-left is at x,y (screen pixels).",
               params={**_W,
                       "x": Param("int", required=True, lo=-10000, hi=20000),
                       "y": Param("int", required=True, lo=-10000, hi=20000)},
               requires=WIN_REQ),
    ActionSpec("window_move_by", "window",
               "Move a window by dx,dy pixels from its current position.",
               params={**_W,
                       "dx": Param("int", required=True, lo=-5000, hi=5000),
                       "dy": Param("int", required=True, lo=-5000, hi=5000)},
               requires=WIN_REQ),
    ActionSpec("window_resize", "window",
               "Resize a window to width by height pixels.",
               params={**_W,
                       "width": Param("int", required=True, lo=100, hi=10000),
                       "height": Param("int", required=True, lo=100, hi=10000)},
               requires=WIN_REQ),
    ActionSpec("window_maximize", "window",
               "Maximize a window to fill its monitor work area.",
               params={**_W}, requires=WIN_REQ),
    ActionSpec("window_restore", "window",
               "Restore a minimized or maximized window to normal size.",
               params={**_W}, requires=WIN_REQ),
    ActionSpec("window_minimize", "window",
               "Minimize a window.",
               params={**_W}, requires=WIN_REQ + ("xdotool",)),
    ActionSpec("window_close", "window",
               "Close a window.",
               safety=CONFIRM, params={**_W}, requires=WIN_REQ),
    ActionSpec("window_snap", "window",
               "Snap a window to a half, quarter, center or full monitor region.",
               params={**_W, "where": Param("choice", required=True, choices=_SNAP_CHOICES)},
               requires=WIN_REQ),
    ActionSpec("window_to_monitor", "window",
               "Move a window to another monitor (next, prev, or 1..9).",
               params={**_W, "target": Param("str", required=True, max_len=20)},
               requires=WIN_REQ),
    ActionSpec("window_to_workspace", "window",
               "Move a window to another workspace (next, prev, or 1..36).",
               params={**_W,
                       "target": Param("str", required=True, max_len=20),
                       "follow": Param("bool", default=False)},
               requires=WIN_REQ + ("xdotool",)),
    ActionSpec("app_switch", "window",
               "Switch focus to the next or previous window on this workspace.",
               params={"direction": Param("choice", choices=("next", "prev"), default="next")},
               requires=WIN_REQ),
    ActionSpec("windows_arrange", "window",
               "Tile open windows on the current workspace in a grid per monitor.",
               requires=WIN_REQ),
    # workspace
    ActionSpec("workspace_switch", "workspace",
               "Switch to another workspace (next, prev, or 1..36).",
               params={"target": Param("str", required=True, max_len=20)},
               requires=("xprop", "xdotool")),
    ActionSpec("workspace_overview", "workspace",
               "Toggle the GNOME workspace overview.",
               requires=("xdotool", "gnome")),
    ActionSpec("show_desktop", "workspace",
               "Toggle showing the desktop.",
               requires=("x11", "xprop")),
    # layout
    ActionSpec("layout_save", "layout",
               "Save the current open windows and their positions under a name.",
               params={"name": Param("str", required=True, max_len=40)},
               requires=WIN_REQ),
    ActionSpec("layout_restore", "layout",
               "Restore open windows to a previously saved layout.",
               params={"name": Param("str", required=True, max_len=40)},
               requires=WIN_REQ + ("xdotool",)),
    ActionSpec("layout_list", "layout",
               "List saved window layouts."),
    ActionSpec("layout_delete", "layout",
               "Delete a saved window layout.",
               safety=CONFIRM,
               params={"name": Param("str", required=True, max_len=40)}),
    # pointer
    ActionSpec("pointer_move", "pointer",
               "Move the mouse pointer to screen coordinates x,y.",
               params={"x": Param("int", required=True, lo=-10000, hi=20000),
                       "y": Param("int", required=True, lo=-10000, hi=20000)},
               requires=("xdotool", "xrandr")),
    ActionSpec("pointer_move_by", "pointer",
               "Move the mouse pointer by dx,dy pixels.",
               params={"dx": Param("int", required=True, lo=-5000, hi=5000),
                       "dy": Param("int", required=True, lo=-5000, hi=5000)},
               requires=("xdotool", "xrandr")),
    ActionSpec("pointer_click", "pointer",
               "Click a mouse button one to three times.",
               params={"button": Param("choice", choices=_BUTTON_CHOICES, default="left"),
                       "count": Param("int", lo=1, hi=3, default=1)},
               requires=("xdotool",)),
    ActionSpec("pointer_down", "pointer",
               "Press and hold a mouse button.",
               params={"button": Param("choice", choices=_BUTTON_CHOICES, default="left")},
               requires=("xdotool",)),
    ActionSpec("pointer_up", "pointer",
               "Release a held mouse button.",
               params={"button": Param("choice", choices=_BUTTON_CHOICES, default="left")},
               requires=("xdotool",)),
    ActionSpec("pointer_scroll", "pointer",
               "Scroll the mouse wheel in a direction.",
               params={"direction": Param("choice", required=True, choices=_SCROLL_CHOICES),
                       "amount": Param("int", lo=1, hi=20, default=3)},
               requires=("xdotool",)),
    ActionSpec("pointer_drag", "pointer",
               "Drag from the current pointer position to x,y while holding a button.",
               params={"x": Param("int", required=True, lo=-10000, hi=20000),
                       "y": Param("int", required=True, lo=-10000, hi=20000),
                       "button": Param("choice", choices=_BUTTON_CHOICES, default="left")},
               requires=("xdotool", "xrandr")),
    # system
    ActionSpec("volume_set", "system",
               "Set output audio volume to a percentage (0 to 100).",
               params={"percent": Param("int", required=True, lo=0, hi=100)},
               requires=("wpctl",)),
    ActionSpec("volume_change", "system",
               "Change output audio volume by a relative percentage (-50 to 50).",
               params={"delta": Param("int", required=True, lo=-50, hi=50)},
               requires=("wpctl",)),
    ActionSpec("volume_mute", "system",
               "Toggle output audio mute.",
               requires=("wpctl",)),
    ActionSpec("mic_mute", "system",
               "Toggle microphone mute.",
               requires=("wpctl",)),
    ActionSpec("brightness_set", "system",
               "Set screen brightness to a percentage (5 to 100).",
               params={"percent": Param("int", required=True, lo=5, hi=100)},
               requires=("gsd_power",)),
    ActionSpec("brightness_change", "system",
               "Change screen brightness by a relative percentage (-50 to 50).",
               params={"delta": Param("int", required=True, lo=-50, hi=50)},
               requires=("gsd_power",)),
    ActionSpec("media", "system",
               "Control media playback (play_pause, next, previous, stop).",
               params={"command": Param("choice", required=True, choices=_MEDIA_CHOICES)},
               requires=("playerctl",)),
    ActionSpec("lock_screen", "system",
               "Lock the current user session.",
               safety=CONFIRM,
               requires=("loginctl",)),
    # keys
    ActionSpec("clipboard_get", "keys",
               "Read text currently stored in the clipboard.",
               requires=("xclip",)),
    ActionSpec("clipboard_set", "keys",
               "Write text to the clipboard.",
               params={"text": Param("str", required=True, max_len=10000)},
               requires=("xclip",)),
    ActionSpec("copy", "keys",
               "Send Ctrl+C to copy the current selection.",
               requires=("xdotool",)),
    ActionSpec("cut", "keys",
               "Send Ctrl+X to cut the current selection.",
               requires=("xdotool",)),
    ActionSpec("paste", "keys",
               "Send Ctrl+V to paste from the clipboard.",
               requires=("xdotool",)),
    ActionSpec("select_all", "keys",
               "Send Ctrl+A to select everything in the active control.",
               requires=("xdotool",)),
    ActionSpec("undo", "keys",
               "Send Ctrl+Z to undo the last edit.",
               requires=("xdotool",)),
    ActionSpec("press_keys", "keys",
               "Send a validated keyboard shortcut to the active window.",
               params={"keys": Param("str", required=True, max_len=40)},
               requires=("xdotool",)),
    # presets
    ActionSpec("preset_list", "layout",
               "List saved workspace presets."),
    ActionSpec("preset_define", "layout",
               "Define a named workspace preset from a JSON list of apps.",
               params={"name": Param("str", required=True, max_len=40),
                       "spec": Param("str", required=True, max_len=4000)}),
    ActionSpec("preset_save", "layout",
               "Save the current open windows as a workspace preset.",
               params={"name": Param("str", required=True, max_len=40)},
               requires=WIN_REQ),
    ActionSpec("preset_apply", "layout",
               "Open and place the apps in a saved workspace preset.",
               params={"name": Param("str", required=True, max_len=40)},
               requires=WIN_REQ + ("xdotool",)),
    ActionSpec("preset_delete", "layout",
               "Delete a saved workspace preset.",
               safety=CONFIRM,
               params={"name": Param("str", required=True, max_len=40)}),
)

ACTIONS_BY_NAME: dict[str, ActionSpec] = {spec.name: spec for spec in ACTIONS}

DESKTOP_TOOL = ToolSpec(
    "desktop_action",
    "Control this computer's desktop: windows (focus, move, resize, maximize, minimize, snap, move to "
    "another monitor or workspace, arrange), workspaces, saved window layouts, mouse pointer, volume, "
    "mic mute, brightness, media playback, clipboard and keyboard shortcuts. Call action='capabilities' "
    "first if unsure what works here, and 'window_list' to see windows and their ids. Target a window "
    "with window=<id> or app=<name>; default is the active window. Actions that need the user's OK "
    "(window_close, lock_screen, layout_delete) are refused until you ask the user and call again with "
    "confirmed=true. Presets open the user's apps and place them: preset_define / preset_save, "
    "then preset_apply.",
    {
        "type": "object",
        "properties": {
            "action": {"type": "string", "enum": [a.name for a in ACTIONS],
                       "description": "Desktop action name to execute."},
            "window": {"type": "string",
                       "description": "Window id (decimal or 0x hex) or 'active'."},
            "app": {"type": "string",
                    "description": "App name or title substring to match."},
            "x": {"type": "integer", "description": "Horizontal screen coordinate in pixels."},
            "y": {"type": "integer", "description": "Vertical screen coordinate in pixels."},
            "dx": {"type": "integer", "description": "Horizontal pixel offset."},
            "dy": {"type": "integer", "description": "Vertical pixel offset."},
            "width": {"type": "integer", "description": "Target window width in pixels."},
            "height": {"type": "integer", "description": "Target window height in pixels."},
            "where": {"type": "string", "enum": list(_SNAP_CHOICES),
                      "description": "Screen region to snap the window into."},
            "target": {"type": "string",
                       "description": "Monitor or workspace target: next, prev, or 1-based number."},
            "direction": {"type": "string", "enum": ["next", "prev", "up", "down", "left", "right"],
                          "description": "Direction for app switching or pointer scrolling."},
            "button": {"type": "string", "enum": list(_BUTTON_CHOICES),
                       "description": "Mouse button: left, right or middle."},
            "count": {"type": "integer", "description": "Number of clicks (1 to 3)."},
            "amount": {"type": "integer", "description": "Scroll wheel step count (1 to 20)."},
            "percent": {"type": "integer", "description": "Target volume or brightness percentage."},
            "delta": {"type": "integer", "description": "Relative volume or brightness percentage change."},
            "follow": {"type": "boolean",
                       "description": "Switch to the workspace after moving the window."},
            "command": {"type": "string", "enum": list(_MEDIA_CHOICES),
                        "description": "Media playback command."},
            "text": {"type": "string", "description": "Text to place on the clipboard."},
            "keys": {"type": "string", "description": "Key combination such as ctrl+shift+t or F5."},
            "name": {"type": "string", "description": "Saved window layout name."},
            "spec": {"type": "string",
                     "description": "For preset_define: JSON list of apps, each "
                                    "{launch, match?, where?, monitor?, workspace?}"},
            "confirmed": {"type": "boolean",
                          "description": "True only after the user confirmed the action."},
        },
        "required": ["action"],
    },
)

LOOK_MAX_SIDE = 1568        # px: longest side sent to the brain (larger costs tokens, no gain)
LOOK_POINT_SECONDS = 20.0   # a pointed window older than this is not "pointed" any more
LOOK_JPEG_QUALITY = 85
LOOK_TOOL = ToolSpec(
    "look_at_screen",
    "See the user's screen: returns a screenshot. target 'pointed' (default) = the window the "
    "user is pointing at with their hand (Lura Air), or the window under the mouse when they are "
    "not pointing; 'active' = the focused window; 'screen' = the whole monitor under the mouse. "
    "Call it when the user asks you to look, or says 'this'/'here' about something on screen. "
    "Never read passwords, keys or other secrets aloud.",
    {
        "type": "object",
        "properties": {
            "target": {"type": "string", "enum": ["pointed", "active", "screen"]},
        },
        "required": [],
    },
)


# ── validation ──────────────────────────────────────────────────────────────

def _parse_int(name: str, val: object) -> int:
    if isinstance(val, bool):
        raise ActionError(f"'{name}' must be an integer.")
    if isinstance(val, int):
        return val
    if isinstance(val, float):
        if math.isfinite(val) and val.is_integer():
            return int(val)
        raise ActionError(f"'{name}' must be an integer.")
    if isinstance(val, str):
        s = val.strip()
        try:
            return int(s, 10)
        except ValueError:
            try:
                f = float(s)
            except ValueError:
                raise ActionError(f"'{name}' must be an integer.") from None
            if math.isfinite(f) and f.is_integer():
                return int(f)
    raise ActionError(f"'{name}' must be an integer.")


def _parse_bool(name: str, val: object) -> bool:
    if isinstance(val, bool):
        return val
    if isinstance(val, int) and val in (0, 1):
        return bool(val)
    if isinstance(val, str):
        low = val.strip().lower()
        if low in ("true", "1"):
            return True
        if low in ("false", "0"):
            return False
    raise ActionError(f"'{name}' must be true or false.")


def _parse_target(name: str, val: object) -> str | int:
    if isinstance(val, bool):
        raise ActionError(f"'{name}' must be 'active' or a window id.")
    if isinstance(val, int):
        if val > 0:
            return val
        raise ActionError(f"'{name}' must be 'active' or a positive window id.")
    if isinstance(val, str):
        s = val.strip()
        if s.lower() == "active":
            return "active"
        if s.lower().startswith("0x"):
            try:
                n = int(s, 16)
            except ValueError:
                raise ActionError(f"'{name}' must be 'active' or a window id.") from None
            if n > 0:
                return n
        elif s.isdigit():
            n = int(s, 10)
            if n > 0:
                return n
    raise ActionError(f"'{name}' must be 'active' or a positive window id.")


def validate_args(spec: ActionSpec, raw: dict) -> dict:
    out: dict[str, object] = {}
    for name, param in spec.params.items():
        if name not in raw or raw[name] is None:
            if param.required:
                raise ActionError(f"{spec.name} needs '{name}'.")
            out[name] = param.default
            continue
        val = raw[name]
        if param.kind == "int":
            n = _parse_int(name, val)
            if (param.lo is not None and n < param.lo) or (param.hi is not None and n > param.hi):
                raise ActionError(f"'{name}' must be between {param.lo} and {param.hi}.")
            out[name] = n
        elif param.kind == "bool":
            out[name] = _parse_bool(name, val)
        elif param.kind == "str":
            s = str(val).strip()
            if not s and param.required:
                raise ActionError(f"{spec.name} needs '{name}'.")
            if len(s) > param.max_len:
                raise ActionError(f"'{name}' is too long (max {param.max_len} characters).")
            out[name] = s
        elif param.kind == "choice":
            s = str(val).strip().lower()
            if s not in param.choices:
                raise ActionError(f"'{name}' must be one of: {', '.join(param.choices)}.")
            out[name] = s
        elif param.kind == "target":
            out[name] = _parse_target(name, val)
        else:
            raise ActionError(f"Unknown parameter type '{param.kind}'.")
    return out


_MODIFIERS = {
    "ctrl": "ctrl",
    "control": "ctrl",
    "alt": "alt",
    "shift": "shift",
    "super": "super",
    "win": "super",
    "meta": "super",
}
_SPECIAL_KEYS = {
    k.lower(): k
    for k in (
        "Return", "Escape", "Tab", "BackSpace", "Delete", "Home", "End",
        "Page_Up", "Page_Down", "Left", "Right", "Up", "Down", "space",
        "Insert", "plus", "minus", "comma", "period", "slash",
    )
}
_FORBIDDEN_COMBOS = (
    (frozenset({"ctrl", "alt"}), "Delete"),
    (frozenset({"ctrl", "alt"}), "BackSpace"),
    (frozenset({"super"}), "l"),
)
_FKEY_RE = re.compile(r"^[fF]([1-9]|1[0-2])$")


def validate_keys(combo: str) -> str:
    parts = [p.strip() for p in combo.split("+")]
    if not parts or len(parts) > 4 or any(not p for p in parts):
        raise ActionError(f"Not an allowed key combination: {combo}")
    mods: list[str] = []
    seen: set[str] = set()
    for raw_mod in parts[:-1]:
        mod = _MODIFIERS.get(raw_mod.lower())
        if mod is None or mod in seen:
            raise ActionError(f"Not an allowed key combination: {combo}")
        mods.append(mod)
        seen.add(mod)
    last = parts[-1]
    if len(last) == 1 and last.isascii() and last.isalnum():
        key = last.lower()
    elif m := _FKEY_RE.match(last):
        key = f"F{m.group(1)}"
    elif last.lower() in _SPECIAL_KEYS:
        key = _SPECIAL_KEYS[last.lower()]
    else:
        raise ActionError(f"Not an allowed key combination: {combo}")
    if (frozenset(mods), key) in _FORBIDDEN_COMBOS:
        raise ActionError(f"Not an allowed key combination: {combo}")
    return "+".join([*mods, key])


# ── workspace / monitor / geometry ──────────────────────────────────────────

def resolve_desktop(target: str, current: int, count: int) -> int:
    if count < 1:
        raise ActionError("There are no workspaces.")
    t = target.strip().lower()
    if t == "next":
        return min(current + 1, count - 1)
    if t == "prev":
        return max(current - 1, 0)
    if t.isdigit():
        idx = int(t) - 1
        if 0 <= idx < count:
            return idx
        raise ActionError(f"There are {count} workspaces.")
    raise ActionError(f"Workspace target must be 'next', 'prev', or 1..{count}.")


def monitor_target(target: str, current: int, count: int) -> int:
    if count < 1:
        raise ActionError("No monitors found.")
    t = target.strip().lower()
    if t == "next":
        return (current + 1) % count
    if t == "prev":
        return (current - 1) % count
    if t.isdigit():
        idx = int(t) - 1
        if 0 <= idx < count:
            return idx
        raise ActionError(f"There are {count} monitors.")
    raise ActionError(f"Monitor target must be 'next', 'prev', or 1..{count}.")


def work_area(monitor: Monitor, workarea: Rect | None) -> Rect:
    if workarea is None:
        return monitor.rect
    inter = monitor.rect.intersect(workarea)
    return inter if inter is not None else monitor.rect


def monitor_for(rect: Rect, monitors: list[Monitor]) -> Monitor:
    if not monitors:
        raise ActionError("No monitors found.")
    cx, cy = rect.center
    for m in monitors:
        if m.rect.x <= cx < m.rect.x + m.rect.width and m.rect.y <= cy < m.rect.y + m.rect.height:
            return m
    best = max(monitors, key=lambda m: rect.overlap(m.rect))
    if rect.overlap(best.rect) > 0:
        return best
    for m in monitors:
        if m.primary:
            return m
    return monitors[0]


def snap_rect(area: Rect, where: str) -> Rect:
    wl = area.width // 2
    wr = area.width - wl
    ht = area.height // 2
    hb = area.height - ht
    if where == "left":
        return Rect(area.x, area.y, wl, area.height)
    if where == "right":
        return Rect(area.x + wl, area.y, wr, area.height)
    if where == "top":
        return Rect(area.x, area.y, area.width, ht)
    if where == "bottom":
        return Rect(area.x, area.y + ht, area.width, hb)
    if where == "top_left":
        return Rect(area.x, area.y, wl, ht)
    if where == "top_right":
        return Rect(area.x + wl, area.y, wr, ht)
    if where == "bottom_left":
        return Rect(area.x, area.y + ht, wl, hb)
    if where == "bottom_right":
        return Rect(area.x + wl, area.y + ht, wr, hb)
    if where == "full":
        return area
    if where == "center":
        cw = (area.width * 60) // 100
        ch = (area.height * 70) // 100
        return Rect(area.x + (area.width - cw) // 2, area.y + (area.height - ch) // 2, cw, ch)
    raise ActionError(f"Unknown snap position: {where}.")


def grid_rects(n: int, area: Rect) -> list[Rect]:
    if n <= 0:
        return []
    if n == 1:
        return [area]
    if n == 2:
        return [snap_rect(area, "left"), snap_rect(area, "right")]
    if n == 3:
        return [snap_rect(area, "left"), snap_rect(area, "top_right"), snap_rect(area, "bottom_right")]
    cols = math.ceil(math.sqrt(n))
    rows = math.ceil(n / cols)
    rects: list[Rect] = []
    for r in range(rows):
        y0 = area.y + (r * area.height) // rows
        y1 = area.y + ((r + 1) * area.height) // rows
        k = cols if r < rows - 1 else (n - cols * (rows - 1))
        for c in range(k):
            x0 = area.x + (c * area.width) // k
            x1 = area.x + ((c + 1) * area.width) // k
            rects.append(Rect(x0, y0, x1 - x0, y1 - y0))
    return rects


def clamp_visible(rect: Rect, bounds_rect: Rect, margin: int = 64) -> Rect:
    mx = min(margin, rect.width, bounds_rect.width)
    min_x = bounds_rect.x + mx - rect.width
    max_x = bounds_rect.x + bounds_rect.width - mx
    x = max(min_x, min(max_x, rect.x))
    my = min(margin, bounds_rect.height)
    min_y = bounds_rect.y
    max_y = max(min_y, bounds_rect.y + bounds_rect.height - my)
    y = max(min_y, min(max_y, rect.y))
    return Rect(x, y, rect.width, rect.height)


def bounds(monitors: list[Monitor]) -> Rect:
    if not monitors:
        return Rect(0, 0, 0, 0)
    x1 = min(m.rect.x for m in monitors)
    y1 = min(m.rect.y for m in monitors)
    x2 = max(m.rect.x + m.rect.width for m in monitors)
    y2 = max(m.rect.y + m.rect.height for m in monitors)
    return Rect(x1, y1, x2 - x1, y2 - y1)


# ── targeting, switching, layout matching ───────────────────────────────────

_TARGET_KINDS = {"", "NORMAL", "DIALOG", "UTILITY"}


def is_target(window: Window, own_pids: set[int]) -> bool:
    if window.kind not in _TARGET_KINDS:
        return False
    if "lura" in window.app.lower():
        return False
    if window.pid and window.pid in own_pids:
        return False
    return True


def next_window(stacking: list[int], active: int | None, direction: str) -> int | None:
    if len(stacking) < 2:
        return None
    if active in stacking:
        others = [w for w in stacking if w != active]
        return others[-1] if direction == "next" else others[0]
    return stacking[-2] if direction == "next" else stacking[0]


def match_layout(
    entries: tuple[LayoutEntry, ...] | list[LayoutEntry],
    windows: list[Window],
) -> tuple[list[tuple[LayoutEntry, Window]], list[LayoutEntry]]:
    used: set[int] = set()
    matched: list[tuple[LayoutEntry, Window]] = []
    unmatched: list[LayoutEntry] = []
    for entry in entries:
        cands = [w for w in windows if w.id not in used and w.app.lower() == entry.app.lower()]
        if not cands:
            unmatched.append(entry)
            continue
        pick = next((w for w in cands if w.title == entry.title), None)
        if pick is None and entry.title:
            et = entry.title.lower()
            pick = next(
                (w for w in cands if w.title and (et in w.title.lower() or w.title.lower() in et)),
                None,
            )
        if pick is None:
            pick = cands[0]
        used.add(pick.id)
        matched.append((entry, pick))
    return matched, unmatched


_PRESET_NAME_RE = re.compile(r"^[\w \-]{1,40}$")
_REVERSE_DOMAIN_RE = re.compile(r"^(?:[A-Za-z][A-Za-z0-9_-]*\.){2,}")


def _default_match(launch: str) -> str:
    stripped = _REVERSE_DOMAIN_RE.sub("", launch)
    return (stripped or launch).lower()


def validate_preset(name: str, apps: list[dict], saved_at: str = "") -> Preset:
    if not isinstance(name, str) or not _PRESET_NAME_RE.match(name.strip()):
        raise ActionError(
            "Preset name must be 1..40 characters (letters, digits, spaces, '-' and '_')."
        )
    clean_name = name.strip()
    if not isinstance(apps, list) or not (1 <= len(apps) <= 8):
        raise ActionError("Preset must have between 1 and 8 apps.")
    parsed: list[PresetApp] = []
    for i, item in enumerate(apps):
        if not isinstance(item, dict):
            raise ActionError(f"App {i}: must be an object.")
        raw_launch = item.get("launch")
        if not isinstance(raw_launch, str) or not raw_launch.strip():
            raise ActionError(f"App {i} 'launch': must be a non-empty string.")
        launch = raw_launch.strip()
        if len(launch) > 80:
            raise ActionError(f"App {i} 'launch': is too long (max 80 characters).")

        raw_match = item.get("match")
        if raw_match is None:
            match = ""
        elif isinstance(raw_match, str):
            match = raw_match.strip()
            if len(match) > 80:
                raise ActionError(f"App {i} 'match': is too long (max 80 characters).")
        else:
            raise ActionError(f"App {i} 'match': must be a string.")
        if not match:
            match = _default_match(launch)

        raw_where = item.get("where")
        if raw_where is None:
            where = ""
        elif isinstance(raw_where, str):
            where = raw_where.strip().lower()
            if where and where not in _SNAP_CHOICES:
                raise ActionError(
                    f"App {i} 'where': must be one of {', '.join(_SNAP_CHOICES)} or empty."
                )
        else:
            raise ActionError(f"App {i} 'where': must be a string.")

        raw_rect = item.get("rect")
        if raw_rect is None:
            rect = None
        else:
            if not isinstance(raw_rect, (list, tuple)) or len(raw_rect) != 4:
                raise ActionError(
                    f"App {i} 'rect': must be a list of 4 integers [x, y, width, height]."
                )
            if any(isinstance(v, bool) or not isinstance(v, int) for v in raw_rect):
                raise ActionError(f"App {i} 'rect': must contain 4 integers.")
            rx, ry, rw, rh = raw_rect
            if rw < 100 or rh < 100:
                raise ActionError(f"App {i} 'rect': width and height must be at least 100.")
            rect = Rect(rx, ry, rw, rh)

        raw_mon = item.get("monitor")
        if raw_mon is None:
            monitor = 0
        else:
            try:
                monitor = _parse_int("monitor", raw_mon)
            except ActionError as exc:
                raise ActionError(f"App {i} 'monitor': {exc}") from None
            if not (0 <= monitor <= 9):
                raise ActionError(f"App {i} 'monitor': must be between 0 and 9.")

        raw_ws = item.get("workspace")
        if raw_ws is None:
            workspace = 0
        else:
            try:
                workspace = _parse_int("workspace", raw_ws)
            except ActionError as exc:
                raise ActionError(f"App {i} 'workspace': {exc}") from None
            if not (0 <= workspace <= 36):
                raise ActionError(f"App {i} 'workspace': must be between 0 and 36.")

        parsed.append(
            PresetApp(
                launch=launch,
                match=match,
                where=where,
                rect=rect,
                monitor=monitor,
                workspace=workspace,
            )
        )
    return Preset(name=clean_name, apps=tuple(parsed), saved_at=saved_at)


def preset_from_windows(
    name: str,
    windows: list[Window],
    monitors: list[Monitor],
    saved_at: str,
) -> Preset:
    if not isinstance(name, str) or not _PRESET_NAME_RE.match(name.strip()):
        raise ActionError(
            "Preset name must be 1..40 characters (letters, digits, spaces, '-' and '_')."
        )
    clean_name = name.strip()
    top_first = list(reversed(windows))[:8]
    if not top_first:
        raise ActionError("No open windows to save as a preset.")
    apps = tuple(
        PresetApp(
            launch=w.app,
            match=w.app,
            where="",
            rect=w.rect,
            monitor=0,
            workspace=w.desktop + 1 if w.desktop >= 0 else 0,
        )
        for w in top_first
    )
    return Preset(name=clean_name, apps=apps, saved_at=saved_at)


def find_window(app: PresetApp, windows: list[Window], used: set[int]) -> Window | None:
    needle = app.match.lower()
    if not needle:
        return None
    for w in windows:
        if w.id in used:
            continue
        if needle in w.app.lower() or needle in w.title.lower():
            return w
    return None


# ── parsers ─────────────────────────────────────────────────────────────────

_MONITOR_RE = re.compile(
    r"^\s*(\d+):\s+\+?(\*?)(\S+)\s+(\d+)/\d+x(\d+)/\d+([+-]\d+)([+-]\d+)"
)
_XPROP_LINE_RE = re.compile(r"^([A-Za-z0-9_]+)(?:\([^)]*\))?\s*(?:=|:)\s*(.*)$")
_QUOTED_RE = re.compile(r'"((?:\\.|[^"\\])*)"')
_UNESCAPE_RE = re.compile(r'\\(["\\])')
_XWININFO_X = re.compile(r"Absolute upper-left X:\s*(-?\d+)")
_XWININFO_Y = re.compile(r"Absolute upper-left Y:\s*(-?\d+)")
_XWININFO_W = re.compile(r"Width:\s*(\d+)")
_XWININFO_H = re.compile(r"Height:\s*(\d+)")
_WPCTL_RE = re.compile(r"Volume:\s*([0-9]+(?:\.[0-9]+)?)")
_GDBUS_INT_RE = re.compile(r"<\s*(?:[a-z0-9_]+\s+)?(-?\d+)\s*>")
_PTR_X_RE = re.compile(r"(?m)^X=(-?\d+)\s*$")
_PTR_Y_RE = re.compile(r"(?m)^Y=(-?\d+)\s*$")


def parse_monitors(text: str) -> list[Monitor]:
    out: list[Monitor] = []
    for line in text.splitlines():
        m = _MONITOR_RE.match(line)
        if not m:
            continue
        index = int(m.group(1))
        primary = bool(m.group(2)) or "*" in m.group(3)
        name = m.group(3).lstrip("+*")
        rect = Rect(int(m.group(6)), int(m.group(7)), int(m.group(4)), int(m.group(5)))
        out.append(Monitor(index=index, name=name, rect=rect, primary=primary))
    return out


def parse_xprop(text: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for line in text.splitlines():
        if not line or line[0].isspace():
            continue
        stripped = line.rstrip()
        if stripped.endswith("not found.") or "no such atom" in stripped.lower():
            continue
        m = _XPROP_LINE_RE.match(stripped)
        if m:
            out[m.group(1)] = m.group(2).strip()
    return out


def prop_ints(raw: str) -> list[int]:
    if not raw:
        return []
    body = raw.split("#", 1)[1] if "#" in raw else raw
    out: list[int] = []
    for part in body.split(","):
        tok = part.strip()
        if not tok:
            continue
        try:
            out.append(int(tok, 0))
        except ValueError:
            continue
    return out


def prop_strings(raw: str) -> list[str]:
    if not raw:
        return []
    return [_UNESCAPE_RE.sub(r"\1", m.group(1)) for m in _QUOTED_RE.finditer(raw)]


def prop_atoms(raw: str) -> list[str]:
    if not raw:
        return []
    return [p.strip() for p in raw.split(",") if p.strip()]


def parse_xwininfo(text: str) -> Rect | None:
    mx = _XWININFO_X.search(text)
    my = _XWININFO_Y.search(text)
    mw = _XWININFO_W.search(text)
    mh = _XWININFO_H.search(text)
    if not (mx and my and mw and mh):
        return None
    return Rect(int(mx.group(1)), int(my.group(1)), int(mw.group(1)), int(mh.group(1)))


def parse_wpctl_volume(text: str) -> tuple[float, bool] | None:
    m = _WPCTL_RE.search(text)
    if not m:
        return None
    return (float(m.group(1)), "[muted]" in text.lower())


def parse_gdbus_int(text: str) -> int | None:
    m = _GDBUS_INT_RE.search(text)
    return int(m.group(1)) if m else None


def parse_pointer(text: str) -> tuple[int, int] | None:
    mx = _PTR_X_RE.search(text)
    my = _PTR_Y_RE.search(text)
    if not (mx and my):
        return None
    return (int(mx.group(1)), int(my.group(1)))


def window_from_props(
    wid: int,
    props: dict[str, str],
    frame_inner: Rect,
    extents: tuple[int, int, int, int],
) -> Window:
    names = prop_strings(props.get("_NET_WM_NAME", "")) or prop_strings(props.get("WM_NAME", ""))
    title = names[0][:120] if names else ""
    classes = prop_strings(props.get("WM_CLASS", ""))
    app = classes[1] if len(classes) >= 2 else (classes[0] if classes else "")
    pids = prop_ints(props.get("_NET_WM_PID", ""))
    pid = pids[0] if pids else 0
    ds = prop_ints(props.get("_NET_WM_DESKTOP", ""))
    desktop = -1 if (not ds or ds[0] == 0xFFFFFFFF) else ds[0]
    states = set(prop_atoms(props.get("_NET_WM_STATE", "")))
    maximized = (
        "_NET_WM_STATE_MAXIMIZED_VERT" in states and "_NET_WM_STATE_MAXIMIZED_HORZ" in states
    )
    minimized = "_NET_WM_STATE_HIDDEN" in states
    types = prop_atoms(props.get("_NET_WM_WINDOW_TYPE", ""))
    kind = types[0].removeprefix("_NET_WM_WINDOW_TYPE_") if types else ""
    left, right, top, bottom = extents
    rect = Rect(
        frame_inner.x - left,
        frame_inner.y - top,
        frame_inner.width + left + right,
        frame_inner.height + top + bottom,
    )
    return Window(
        id=wid, title=title, app=app, pid=pid, desktop=desktop,
        rect=rect, maximized=maximized, minimized=minimized, kind=kind,
    )


def catalog_text(available: dict[str, bool] | None = None) -> str:
    lines: list[str] = []
    for spec in ACTIONS:
        head = f"{spec.name} [confirm]" if spec.safety == CONFIRM else spec.name
        if spec.params:
            ps = ", ".join(p if spec.params[p].required else f"{p}?" for p in spec.params)
            head = f"{head} ({ps})"
        unavail = (
            " (unavailable)"
            if (available is not None and not available.get(spec.name, True))
            else ""
        )
        lines.append(f"{head} — {spec.summary}{unavail}")
    return "\n".join(lines)
