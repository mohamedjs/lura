# 002 — Lura Air Stage B: universal desktop actions (`modules/computer`)
Status: Implemented (2026-11-20, Gemini 4 Neon; reviewed and fixed by Claude, see §12)     Builds on: docs/001-lura-air.md, docs/ARCHITECTURE.md

## 1. Goal

One validated catalog of desktop actions (windows, workspaces, saved layouts, pointer,
system controls, clipboard, keys) behind a per-OS backend. Lura's brain uses it through one
tool, `desktop_action`; Mohamed can run any action from the terminal (`lura desktop …`);
the gesture stages (C–F) will call the same service. Nothing here knows about gestures,
cameras or the brain. Only X11 is implemented; macOS, Windows and Wayland report
"unsupported" instead of failing.

Read docs/001-lura-air.md §1 first: it lists which tool does what on this machine and why
(e.g. `xdotool windowmove` is offset by the frame, so moves use EWMH).

## 2. Stack and packages

- Python ≥3.10 standard library only: `subprocess`, `ctypes`, `json`, `re`, `shutil`,
  `threading`, `dataclasses`, `time`, `os`, `logging`.
- System programs (already installed; probe for them, never install anything):
  `xdotool`, `xprop`, `xwininfo`, `xrandr`, `wpctl`, `gdbus`, `playerctl`, `xclip`,
  `loginctl`, `gsettings`, and `libX11.so.6` through ctypes.
- **Do not install any package** (no pip, no apt). Do not add dependencies to `pyproject.toml`.

## 3. Structure

```
src/lura/modules/computer/__init__.py                  NEW  public API
src/lura/modules/computer/domain.py                    NEW  pure: shapes, catalog, validation, geometry, parsers
src/lura/modules/computer/application.py               NEW  ComputerService, Backend port, ToolProvider
src/lura/modules/computer/infrastructure/__init__.py   NEW  default_backend()
src/lura/modules/computer/infrastructure/runner.py     NEW  run argv with timeout
src/lura/modules/computer/infrastructure/xlib.py       NEW  ctypes EWMH ClientMessages
src/lura/modules/computer/infrastructure/x11.py        NEW  X11Backend (windows, workspaces, pointer, keys)
src/lura/modules/computer/infrastructure/controls.py   NEW  volume, mic, brightness, media, lock, clipboard (Linux)
src/lura/modules/computer/infrastructure/unsupported.py NEW UnsupportedBackend
src/lura/modules/computer/infrastructure/layouts.py    NEW  layouts.json load/save
src/lura/bootstrap.py                                   EDIT build ComputerService, add as tool provider, Container field
src/lura/app.py                                         EDIT `lura desktop` subcommand
tests/test_computer.py                                  NEW  pure + fake-backend checks (in run_all)
tests/live_desktop.py                                   NEW  real X11 checks on a throwaway xeyes window (NOT in run_all)
tests/run_all.py                                        EDIT add "test_computer.py" to CHECKS
docs/ARCHITECTURE.md                                    EDIT one line for computer/ in the module tree
```

No other file is created or changed.

## 4. File responsibilities

**`computer/__init__.py`** — exports exactly: `ComputerService`, `Backend`, `default_backend`,
`ActionResult`, `ActionError`, `ACTIONS`, `DESKTOP_TOOL`, `SAFE`, `CONFIRM`. Imports from its
own `domain`, `application`, `infrastructure` only. Docstring one line:
`"""Computer: the desktop actions Lura (and Lura Air) can take, validated, per OS."""`

**`domain.py`** — pure: no subprocess, no files, no ctypes, no env reads. Exports the data
shapes (§7), `ACTIONS`, `ACTIONS_BY_NAME`, `DESKTOP_TOOL`, `validate_args`, `validate_keys`,
geometry helpers, parsers, `is_target`, `match_layout`, `next_window`, `resolve_desktop`,
`catalog_text`. May import only `..brain` (for `ToolSpec`) and the standard library.

**`application.py`** — `Backend` Protocol (the port), `ComputerService` (the use cases: one
`_do_<action>` method per catalog entry), and the `ToolProvider` methods. May import
`.domain`, `.infrastructure.layouts` and the stdlib. Must not import subprocess or ctypes.

**`infrastructure/__init__.py`** — `default_backend() -> Backend`: picks `X11Backend` or
`UnsupportedBackend` (§8). Imports nothing heavy at module import time (ctypes lib load
happens lazily inside `xlib`).

**`infrastructure/runner.py`** — `run(argv, timeout=3.0, input_text=None) -> Run`, the only
place that starts processes in this module. Never `shell=True`.

**`infrastructure/xlib.py`** — opens the X display with ctypes and sends EWMH ClientMessages.
Copy the structure/argtypes pattern from `src/lura/modules/overlay/workspaces.py` (read it;
do not import it — modules may not import each other's internals).

**`infrastructure/x11.py`** — `X11Backend`: reads window/monitor/desktop state through
`xprop`, `xwininfo`, `xrandr`, `xdotool` and parses with `domain` parsers; writes through
`xlib` and `xdotool`. Delegates system controls to `controls.LinuxControls`.

**`infrastructure/controls.py`** — `LinuxControls`: volume/mic (`wpctl`), brightness (gsd-power
over `gdbus`), media (`playerctl`), lock (`loginctl lock-session`), clipboard text (`xclip`).

**`infrastructure/unsupported.py`** — `UnsupportedBackend(reason)`: `unsupported_reason()` returns
the reason, `probe()` returns `{}`; every other method raises `NotImplementedError`.

**`infrastructure/layouts.py`** — `load() -> dict[str, Layout]`, `save(layouts)`: JSON at
`config_dir()/layouts.json`, atomic write (tmp file + `os.replace`). Import `config_dir` from
`....shared.paths`.

**`bootstrap.py`** — build `ComputerService(default_backend())`, `tools.add_provider(computer)`,
add `computer: ComputerService` as the **last** field of `Container`, pass it in `build`.

**`app.py`** — `desktop` subparser + `cmd_desktop` (§8.6). Imports `from .modules.computer import
ComputerService, default_backend` inside `cmd_desktop`.

**`tests/test_computer.py`** — all checks of §10 using a `FakeBackend`; no display, no processes.

**`tests/live_desktop.py`** — real checks (§8.7); exits 0 with `skipped: <why>` when there is no
X11 session or no `xeyes`.

## 5. Layering rules

- domain ← application ← infrastructure; `__init__` composes. `application.py` receives a
  `Backend` (it never builds one); `default_backend()` lives in infrastructure.
- `computer` imports only `brain` (package, for `ToolSpec`) and `shared`. It must not import
  `tools`, `assistant`, `overlay`, `dashboard`, `voice`, `settings`.
- `ComputerService` satisfies `tools.ToolProvider` structurally (no import of `tools`).
- All side effects (processes, X11, files) are in `infrastructure/`.
- `tests/test_architecture.py` must still pass.

## 6. Coding conventions (match the repo)

- `from __future__ import annotations` at the top of every new module; module docstring of one
  or two plain sentences, like the existing ones.
- Type hints on every function; `X | None`, `list[...]`, `dict[...]`. Frozen dataclasses for data.
- Names: snake_case functions, PascalCase classes, UPPER_CASE constants, private helpers `_x`.
- `log = logging.getLogger(__name__)` per module. `log.info("desktop_action %s %s", name, safe_args)`
  per run where `safe_args` replaces `text` with `f"<{len(text)} chars>"`; `log.warning` on failure.
  Never log clipboard contents.
- Errors: domain raises `ActionError(ValueError)` with a sentence the model can read.
  `ComputerService.run` never raises: every failure becomes `ActionResult(ok=False, message=...)`.
- Messages are short English sentences addressed to the model (the brain translates for the user).
- Comments only where the reason is not obvious (e.g. why EWMH and not `xdotool windowmove`).
- Line length ≤ 110. No print() outside `app.py` and tests.

## 7. Interfaces (`domain.py`)

```python
SAFE, CONFIRM = "safe", "confirm"

class ActionError(ValueError): ...

@dataclass(frozen=True)
class Rect:
    x: int; y: int; width: int; height: int
    @property
    def center(self) -> tuple[int, int]
    def intersect(self, other: "Rect") -> "Rect | None"     # None when they do not overlap
    def overlap(self, other: "Rect") -> int                  # area of the intersection, 0 if none
    def translate(self, dx: int, dy: int) -> "Rect"

@dataclass(frozen=True)
class Monitor:
    index: int          # 0-based, order of `xrandr --listmonitors`
    name: str
    rect: Rect
    primary: bool

@dataclass(frozen=True)
class Window:
    id: int
    title: str
    app: str            # WM_CLASS class part (second string), e.g. "XEyes", "Google-chrome"
    pid: int            # 0 when unknown
    desktop: int        # 0-based; -1 = on all desktops (0xFFFFFFFF)
    rect: Rect          # outer frame rect, absolute screen pixels
    maximized: bool     # both MAXIMIZED_VERT and MAXIMIZED_HORZ
    minimized: bool     # _NET_WM_STATE_HIDDEN
    kind: str           # _NET_WM_WINDOW_TYPE without prefix: "NORMAL", "DIALOG", "DESKTOP", … ; "" when absent

@dataclass(frozen=True)
class Param:
    kind: str                     # "int" | "bool" | "str" | "choice" | "target"
    required: bool = False
    lo: int | None = None         # int: inclusive bounds
    hi: int | None = None
    choices: tuple[str, ...] = () # choice
    default: object = None
    max_len: int = 200            # str

@dataclass(frozen=True)
class ActionSpec:
    name: str
    group: str                    # "info" "window" "workspace" "layout" "pointer" "system" "keys"
    summary: str                  # one line, used by catalog_text()
    safety: str = SAFE
    params: dict[str, Param] = field(default_factory=dict)
    requires: tuple[str, ...] = ()   # probe keys, §8.2

@dataclass(frozen=True)
class LayoutEntry:
    app: str; title: str; desktop: int; rect: Rect; maximized: bool

@dataclass(frozen=True)
class Layout:
    name: str; saved_at: str; windows: tuple[LayoutEntry, ...]

@dataclass
class ActionResult:
    ok: bool
    message: str
    data: dict = field(default_factory=dict)
    needs_confirmation: bool = False
    ms: float = 0.0
    def to_text(self) -> str:
        # message, then (if data) a newline and json.dumps(data, ensure_ascii=False, separators=(",", ":"))
        # truncated to 4000 chars total.
```

### 7.1 Shared parameters

Window targeting (used by every action whose params include `window`):
`window: Param("target", default="active")` — `"active"` or a numeric window id (int or digit
string; hex `0x…` accepted and converted), and `app: Param("str", max_len=80)` — optional text
matched case-insensitively against `Window.app` and `Window.title`. If `app` is given it wins over
`window`.

### 7.2 The catalog (`ACTIONS`, in this order)

`W` = `{"window": target, "app": str}` as in 7.1. Requires keys are from §8.2.
`WIN_REQ = ("x11", "xprop", "xwininfo", "xrandr")`.

| name | group | safety | params (beyond W where noted) | requires |
|---|---|---|---|---|
| capabilities | info | safe | — | () |
| window_list | info | safe | — | WIN_REQ |
| monitors | info | safe | — | ("xrandr",) |
| pointer_position | info | safe | — | ("xdotool",) |
| window_focus | window | safe | W | WIN_REQ |
| window_move | window | safe | W + x int req −10000..20000, y int req −10000..20000 | WIN_REQ |
| window_move_by | window | safe | W + dx int req −5000..5000, dy int req −5000..5000 | WIN_REQ |
| window_resize | window | safe | W + width int req 100..10000, height int req 100..10000 | WIN_REQ |
| window_maximize | window | safe | W | WIN_REQ |
| window_restore | window | safe | W | WIN_REQ |
| window_minimize | window | safe | W | WIN_REQ + ("xdotool",) |
| window_close | window | **confirm** | W | WIN_REQ |
| window_snap | window | safe | W + where choice req (left, right, top, bottom, top_left, top_right, bottom_left, bottom_right, center, full) | WIN_REQ |
| window_to_monitor | window | safe | W + target str req ("next", "prev", or "1".."9") | WIN_REQ |
| window_to_workspace | window | safe | W + target str req ("next", "prev", or "1".."36"), follow bool default False | WIN_REQ + ("xdotool",) |
| app_switch | window | safe | direction choice (next, prev) default next | WIN_REQ |
| windows_arrange | window | safe | — | WIN_REQ |
| workspace_switch | workspace | safe | target str req ("next", "prev", or "1".."36") | ("xprop", "xdotool") |
| workspace_overview | workspace | safe | — | ("xdotool", "gnome") |
| show_desktop | workspace | safe | — | ("x11", "xprop") |
| layout_save | layout | safe | name str req max_len 40 | WIN_REQ |
| layout_restore | layout | safe | name str req max_len 40 | WIN_REQ + ("xdotool",) |
| layout_list | layout | safe | — | () |
| layout_delete | layout | **confirm** | name str req max_len 40 | () |
| pointer_move | pointer | safe | x int req −10000..20000, y int req −10000..20000 | ("xdotool", "xrandr") |
| pointer_move_by | pointer | safe | dx int req −5000..5000, dy int req −5000..5000 | ("xdotool", "xrandr") |
| pointer_click | pointer | safe | button choice (left, right, middle) default left, count int 1..3 default 1 | ("xdotool",) |
| pointer_down | pointer | safe | button choice default left | ("xdotool",) |
| pointer_up | pointer | safe | button choice default left | ("xdotool",) |
| pointer_scroll | pointer | safe | direction choice req (up, down, left, right), amount int 1..20 default 3 | ("xdotool",) |
| pointer_drag | pointer | safe | x int req, y int req (same bounds as pointer_move), button choice default left | ("xdotool", "xrandr") |
| volume_set | system | safe | percent int req 0..100 | ("wpctl",) |
| volume_change | system | safe | delta int req −50..50 | ("wpctl",) |
| volume_mute | system | safe | — | ("wpctl",) |
| mic_mute | system | safe | — | ("wpctl",) |
| brightness_set | system | safe | percent int req 5..100 | ("gsd_power",) |
| brightness_change | system | safe | delta int req −50..50 | ("gsd_power",) |
| media | system | safe | command choice req (play_pause, next, previous, stop) | ("playerctl",) |
| lock_screen | system | **confirm** | — | ("loginctl",) |
| clipboard_get | keys | safe | — | ("xclip",) |
| clipboard_set | keys | safe | text str req max_len 10000 | ("xclip",) |
| copy | keys | safe | — | ("xdotool",) |
| cut | keys | safe | — | ("xdotool",) |
| paste | keys | safe | — | ("xdotool",) |
| select_all | keys | safe | — | ("xdotool",) |
| undo | keys | safe | — | ("xdotool",) |
| press_keys | keys | safe | keys str req max_len 40 | ("xdotool",) |

Summaries: one short line each, written by you in the style "Move a window so its top-left is at
x,y (screen pixels)." Booleans in "bool" params accept True/False, "true"/"false", 1/0.

### 7.3 `DESKTOP_TOOL` (one `ToolSpec`)

```python
DESKTOP_TOOL = ToolSpec("desktop_action", <description>, {"type": "object", "properties": {...}, "required": ["action"]})
```
Description (exactly):
`"Control this computer's desktop: windows (focus, move, resize, maximize, minimize, snap, move to
another monitor or workspace, arrange), workspaces, saved window layouts, mouse pointer, volume,
mic mute, brightness, media playback, clipboard and keyboard shortcuts. Call action='capabilities'
first if unsure what works here, and 'window_list' to see windows and their ids. Target a window
with window=<id> or app=<name>; default is the active window. Actions that need the user's OK
(window_close, lock_screen, layout_delete) are refused until you ask the user and call again with
confirmed=true."`

Properties (flat, so every provider's schema subset accepts it): `action` (string, `enum` = the
catalog names in order), and optional `window` (string), `app`, `x`, `y`, `dx`, `dy`, `width`,
`height` (integers), `where`, `target`, `direction`, `button` (strings), `count`, `amount`,
`percent`, `delta` (integers), `follow` (boolean), `command`, `text`, `keys`, `name` (strings),
`confirmed` (boolean). Each with a description of ≤ 12 words. `where`, `direction`, `button`,
`command` carry `enum` of their union of choices.

### 7.4 `layouts.json`

```json
{"version": 1, "layouts": {"<name>": {"saved_at": "2026-11-20T21:04:05",
  "windows": [{"app": "XEyes", "title": "xeyes", "desktop": 0,
               "rect": [x, y, w, h], "maximized": false}]}}}
```
Unknown/corrupt file → `load()` returns `{}` and logs a warning (do not crash, do not overwrite
until the next save).

## 8. Functions

### 8.1 `domain.py`

- `validate_args(spec, raw: dict) -> dict` — for each param in `spec.params`: missing → default
  (or `ActionError(f"{spec.name} needs '{p}'.")` if required). `int`: accept int, float with
  integral value, or numeric string; outside lo..hi → `ActionError(f"'{p}' must be between {lo}
  and {hi}.")`. `bool`: as §7.2. `str`: `str(value).strip()`, empty and required → error, longer
  than max_len → error. `choice`: lower-case; not in choices → error listing them. `target`:
  "active" or a positive int (decimal or `0x` hex) → returns `"active"` or the int; else error.
  Keys not in `spec.params` are ignored. Returns a new dict containing every declared param.
- `validate_keys(combo: str) -> str` — split on `+`, strip, 1–4 tokens. Modifiers (case-insensitive)
  `ctrl|control → ctrl`, `alt`, `shift`, `super|win|meta → super`, all before the last token,
  no duplicates. Last token: one ASCII letter/digit (lower-cased), `F1`–`F12`, or one of
  `Return Escape Tab BackSpace Delete Home End Page_Up Page_Down Left Right Up Down space Insert
  plus minus comma period slash` (match case-insensitively, return the canonical spelling).
  Refuse `ctrl+alt+Delete`, `ctrl+alt+BackSpace`, and `super+l` (lock has its own confirm action).
  Returns the normalized combo joined with `+`. Anything else → `ActionError("Not an allowed key
  combination: ...")`.
- `resolve_desktop(target: str, current: int, count: int) -> int` — "next" → min(current+1,
  count-1); "prev" → max(current-1, 0); digits → int-1 and must be < count, else
  `ActionError(f"There are {count} workspaces.")`.
- `monitor_target(target: str, current: int, count: int) -> int` — "next"/"prev" wrap around;
  digits 1-based, must exist.
- `work_area(monitor: Monitor, workarea: Rect | None) -> Rect` — `monitor.rect.intersect(workarea)`
  or `monitor.rect` when that is None. (GNOME reports one workarea for all screens:
  `0,28,3840,1052` with monitors `0,0,1920,1080` and `1920,0,1920,1080` gives `0,28,1920,1052`
  and `1920,28,1920,1052`.)
- `monitor_for(rect: Rect, monitors: list[Monitor]) -> Monitor` — the monitor containing the
  rect's centre; else the one with the largest overlap; else the primary; else the first.
- `snap_rect(area: Rect, where: str) -> Rect` — halves (`left`, `right`, `top`, `bottom`),
  quarters (`top_left` …), `full` = area, `center` = 60 % × 70 % of area centred. Integer
  division; the right/bottom piece takes the remainder pixel so the two halves cover the area.
- `grid_rects(n: int, area: Rect) -> list[Rect]` — n=0 → []; 1 → [area]; 2 → left/right halves;
  3 → left half, top_right quarter-height… precisely: left half, right-top, right-bottom;
  n ≥ 4 → `cols = ceil(sqrt(n))`, `rows = ceil(n / cols)`, row-major cells; the last row's cells
  share the full width equally.
- `clamp_visible(rect: Rect, bounds: Rect, margin: int = 64) -> Rect` — translate so that at least
  `margin` px of the rect stays inside `bounds` horizontally and the top edge stays inside
  `bounds` vertically (a title bar must never be pushed off-screen).
- `bounds(monitors) -> Rect` — union of monitor rects.
- `is_target(window: Window, own_pids: set[int]) -> bool` — False when `kind` not in
  `{"", "NORMAL", "DIALOG", "UTILITY"}`, when `"lura" in app.lower()`, or when `pid in own_pids`.
- `next_window(stacking: list[int], active: int | None, direction: str) -> int | None` —
  `stacking` = candidate ids bottom→top. "next" → the one just below the top (Alt+Tab); "prev" →
  the bottom-most. Fewer than 2 candidates → None.
- `match_layout(entries, windows) -> tuple[list[tuple[LayoutEntry, Window]], list[LayoutEntry]]` —
  for each entry in order, pick an unused window with the same `app` (case-insensitive), preferring
  an exact title match, then a title containing the entry title or vice versa, then the first;
  unmatched entries go in the second list. A window is used at most once.
- Parsers (raise nothing; return empty/None on unparseable input):
  - `parse_monitors(text) -> list[Monitor]` for `xrandr --listmonitors`; line regex
    `^\s*(\d+):\s+\+?(\*?)(\S+)\s+(\d+)/\d+x(\d+)/\d+([+-]\d+)([+-]\d+)`; the name is group 3
    without a leading `+`/`*`.
  - `parse_xprop(text) -> dict[str, str]` — `NAME(TYPE) = value` or `NAME: value` lines →
    {NAME: raw value}; lines ending in `not found.` / `no such atom` are skipped. Continuation
    lines (start with whitespace, e.g. `WM_STATE`) are ignored.
  - `prop_ints(raw) -> list[int]` (comma list, decimal or `0x` hex, also after `window id # `),
    `prop_strings(raw) -> list[str]` (quoted, comma-separated, `\"` and `\\` unescaped),
    `prop_atoms(raw) -> list[str]` (comma-separated names).
  - `parse_xwininfo(text) -> Rect | None` from `Absolute upper-left X:`, `…Y:`, `Width:`, `Height:`.
  - `parse_wpctl_volume(text) -> tuple[float, bool] | None` for `Volume: 0.61` / `Volume: 0.13 [MUTED]`.
  - `parse_gdbus_int(text) -> int | None` for `(<25>,)`.
  - `parse_pointer(text) -> tuple[int, int] | None` for `xdotool getmouselocation --shell` (`X=…`, `Y=…`).
- `window_from_props(wid, props: dict, frame_inner: Rect, extents: tuple[int,int,int,int]) -> Window`
  — builds a Window: title from `_NET_WM_NAME` else `WM_NAME` (first string, ≤120 chars); app =
  second WM_CLASS string else first else ""; desktop from `_NET_WM_DESKTOP` (0xFFFFFFFF → −1,
  missing → −1); state atoms → maximized/minimized; kind = first `_NET_WM_WINDOW_TYPE` atom without
  `_NET_WM_WINDOW_TYPE_`; frame rect = inner rect grown by extents (left, right, top, bottom):
  `Rect(x-l, y-t, w+l+r, h+t+b)`.
- `catalog_text(available: dict[str, bool] | None = None) -> str` — one line per action:
  `name [confirm] (param, param?) — summary` (`?` = optional), plus ` (unavailable)` when
  `available` says so.

### 8.2 Probe keys and `Backend` (`application.py`)

Probe keys: `x11` (libX11 loads and `XOpenDisplay` succeeds), `xdotool`, `xprop`, `xwininfo`,
`xrandr`, `wpctl`, `playerctl`, `xclip`, `loginctl` (each: `shutil.which`), `gsd_power` (`gdbus`
exists and reading Brightness returns an int), `gnome` (`"GNOME" in XDG_CURRENT_DESKTOP` and
`gsettings get org.gnome.mutter overlay-key` is non-empty).

```python
class Backend(Protocol):
    def unsupported_reason(self) -> str: ...          # "" when usable
    def probe(self) -> dict[str, bool]: ...
    def own_pids(self) -> set[int]: ...
    # read
    def windows(self) -> list[Window]: ...             # in _NET_CLIENT_LIST_STACKING order, bottom→top
    def active_window(self) -> int | None: ...
    def monitors(self) -> list[Monitor]: ...
    def workarea(self) -> Rect | None: ...
    def desktops(self) -> tuple[int, int]: ...         # (current 0-based, count)
    def showing_desktop(self) -> bool: ...
    def pointer(self) -> tuple[int, int]: ...
    # windows
    def activate(self, wid: int) -> None: ...
    def move_resize(self, wid: int, frame: Rect) -> None: ...
    def set_maximized(self, wid: int, on: bool) -> None: ...
    def minimize(self, wid: int) -> None: ...
    def close(self, wid: int) -> None: ...
    def set_window_desktop(self, wid: int, desktop: int) -> None: ...
    def switch_desktop(self, desktop: int) -> None: ...
    def set_showing_desktop(self, on: bool) -> None: ...
    def overview(self) -> None: ...
    # input
    def pointer_move(self, x: int, y: int) -> None: ...
    def pointer_button(self, button: int, action: str, count: int = 1) -> None: ...  # action: click|down|up
    def key(self, combo: str) -> None: ...
    # system
    def volume(self) -> tuple[float, bool]: ...
    def set_volume(self, fraction: float) -> None: ...
    def change_volume(self, delta_percent: int) -> None: ...
    def toggle_mute(self) -> None: ...
    def mic_muted(self) -> bool: ...
    def toggle_mic(self) -> None: ...
    def brightness(self) -> int: ...
    def set_brightness(self, percent: int) -> None: ...
    def media(self, command: str) -> None: ...          # play_pause|next|previous|stop
    def lock(self) -> None: ...
    def clipboard_get(self) -> str: ...
    def clipboard_set(self, text: str) -> None: ...
```
Backend methods raise `RuntimeError(sentence)` when a program fails.

### 8.3 `ComputerService` (`application.py`)

```python
class ComputerService:
    def __init__(self, backend: Backend, layouts_store=None)   # layouts_store defaults to infrastructure.layouts module (load/save)
    def run(self, name: str, args: dict | None = None, *, confirmed: bool = False) -> ActionResult
    def capabilities(self, refresh: bool = False) -> dict[str, dict]   # {action: {"available": bool, "why": str}}
    # ToolProvider
    def tool_specs(self) -> list[ToolSpec]      # [DESKTOP_TOOL]
    def owns(self, name: str) -> bool           # name == "desktop_action"
    def call(self, name: str, arguments: dict) -> str
```

`run` steps, in order:
1. `spec = ACTIONS_BY_NAME.get(name)`; unknown → fail `f"Unknown action '{name}'. Call action='capabilities' for the list."`.
2. `args = validate_args(spec, args or {})`; `ActionError` → fail with its message.
3. If `spec.requires` is non-empty: `reason = backend.unsupported_reason()` → if set, fail
   `f"Desktop control is not available here: {reason}."`; then missing probe keys → fail
   `f"{name} needs {', '.join(missing)}, which is not available on this computer."`.
4. `spec.safety == CONFIRM and not confirmed` → `ActionResult(False, f"{name} needs the user's OK
   first: {describe}. Ask them, then call again with confirmed=true.", needs_confirmation=True)`,
   where `describe` names the target (window title / layout name / "lock the screen"). Resolve the
   window first for window_close so the description has its title; nothing is executed.
5. Under `self._lock` (`threading.RLock`): `getattr(self, f"_do_{name}")(args)` → ActionResult.
   Time it with `time.perf_counter()` into `result.ms` (rounded to 0.1).
6. Any exception from 5 → `ActionResult(False, f"{name} failed: {exc}")` and `log.warning`.
Log `desktop_action <name> <safe_args>` at info before step 5.

The probe is computed once lazily (`self._probe`); `capabilities(refresh=True)` recomputes.
`capabilities()` for an unsupported backend marks every action with requires as unavailable with
the reason; actions without requires (capabilities, layout_list, layout_delete) stay available.

Window resolution `_target(args) -> Window` (private):
- `ws = [w for w in backend.windows() if is_target(w, backend.own_pids())]`.
- `app` given → matches where `app.lower()` in `w.app.lower()` or in `w.title.lower()`; exactly
  one → it; several on the current desktop and exactly one is active → active; otherwise
  `ActionError("Several windows match 'x': <title (id)>, … Say which one (window=<id>).")`;
  none → `ActionError("No window matches 'x'.")`.
- `window == "active"` → `backend.active_window()`; None or not in `ws` →
  `ActionError("There is no active window I can control.")`.
- numeric id not in `ws` → `ActionError("Window <id> is not one I can control.")`.

Handlers (each returns ActionResult; `ok=True` messages are short, past tense):
- `capabilities` → message `"Desktop actions on this computer:"`, data `{"backend": reason or "x11",
  "actions": catalog_text(available)}`.
- `window_list` → data `{"windows": [...]}` each `{"id", "title"[:80], "app", "workspace"
  (desktop+1, or "all"), "monitor" (monitor_for index+1), "x","y","width","height",
  "maximized","minimized","active"}` for target windows, top of the stack first.
- `monitors` → data list of `{"monitor": i+1, "name", "x","y","width","height","primary"}`.
- `pointer_position` → data `{"x","y"}`.
- `window_focus` → `activate`.
- `window_move` → unmaximize if maximized, `move_resize(Rect(x, y, w.width, w.height))` after
  `clamp_visible` against `bounds(monitors)`.
- `window_move_by` → same with `w.rect.translate(dx, dy)`.
- `window_resize` → unmaximize if needed; keep x,y; clamp size to the window's monitor work area.
- `window_maximize` → `set_maximized(True)`; `window_restore` → if minimized `activate`, if
  maximized `set_maximized(False)`; message says which.
- `window_minimize` → `minimize`. `window_close` → `close`.
- `window_snap` → area = `work_area(monitor_for(w.rect), workarea())`; `full` → `set_maximized(True)`;
  else unmaximize then `move_resize(snap_rect(area, where))`.
- `window_to_monitor` → src/dst monitors via `monitor_target`; offset = dst.rect.xy − src.rect.xy;
  remember maximized; unmaximize; `move_resize(clamp_visible(w.rect.translate(...), dst work area))`;
  re-maximize if it was.
- `window_to_workspace` → `resolve_desktop` from the window's desktop (−1 → current);
  `set_window_desktop`; `follow` → `switch_desktop` + `activate`.
- `app_switch` → candidates = non-minimized target windows on the current desktop (or −1), in
  stacking order; `next_window` → `activate`; None → fail `"There is no other window to switch to."`.
- `windows_arrange` → non-minimized target windows on the current desktop grouped by
  `monitor_for`; per monitor `grid_rects(len, work_area)` in stacking order top→bottom; unmaximize
  each then `move_resize`. Message `"Arranged N windows on M monitors."`.
- `workspace_switch` → `resolve_desktop(target, current, count)`; same as current → ok
  `"Already on workspace k."`; else `switch_desktop`.
- `workspace_overview` → `overview()`. `show_desktop` → toggle `set_showing_desktop(not showing_desktop())`.
- `layout_save` → name validated with `^[\w \-]{1,40}$` (else ActionError); entries from
  non-minimized target windows (all desktops); store via `layouts_store`; overwriting an existing
  name is allowed; message `"Saved layout 'x' with N windows."`.
- `layout_restore` → unknown name → fail listing names. `match_layout`; for each pair: set desktop
  (if entry.desktop ≥ 0 and differs), unmaximize, `move_resize(entry.rect)`, maximize if
  `entry.maximized`. Message `"Restored N windows."` + `" Not open: app1, app2."` when unmatched.
- `layout_list` → data `{"layouts": [{"name","windows","saved_at"}]}`. `layout_delete` → remove + save.
- `pointer_move` → clamp x,y into `bounds(monitors)` (x in [b.x, b.x+b.width-1]), `pointer_move`.
- `pointer_move_by` → current `pointer()` + delta, clamp, move.
- `pointer_click` → button map left=1, middle=2, right=3; `pointer_button(b, "click", count)`.
- `pointer_down` / `pointer_up` → `pointer_button(b, "down"|"up")`.
- `pointer_scroll` → up=4, down=5, left=6, right=7; `pointer_button(b, "click", amount)`.
- `pointer_drag` → start = `pointer()`; `pointer_button(b,"down")`; 12 evenly spaced
  `pointer_move` steps to the clamped target with `time.sleep(0.01)` between; `pointer_button(b,"up")`
  (in a `finally`, so the button is never left held).
- `volume_set` → `set_volume(percent/100)`; `volume_change` → `change_volume(delta)`; both return
  data `{"volume": round(fraction*100), "muted": bool}` read back with `volume()`.
- `volume_mute` → `toggle_mute`, message "Sound muted."/"Sound unmuted." from `volume()`.
- `mic_mute` → `toggle_mic`; when now muted the message adds
  `" While it is muted Lura cannot hear the wake word."`.
- `brightness_set` → `set_brightness(percent)`; `brightness_change` → clamp `brightness()+delta`
  to 5..100; data `{"brightness": int}` read back.
- `media` → `media(command)`. `lock_screen` → `lock()`.
- `clipboard_get` → data `{"text": text[:4000]}`, message notes truncation if longer.
- `clipboard_set` → `clipboard_set(text)`, message `"Copied N characters to the clipboard."`.
- `copy`/`cut`/`paste`/`select_all`/`undo` → `key("ctrl+c" | "ctrl+x" | "ctrl+v" | "ctrl+a" | "ctrl+z")`.
- `press_keys` → `key(validate_keys(keys))`.

`call(name, arguments)`: pop `action` and `confirmed` (bool-coerced), `run(action, rest,
confirmed=confirmed)`, return `result.to_text()`.

### 8.4 Infrastructure

`runner.py`:
```python
@dataclass(frozen=True)
class Run:
    ok: bool; code: int; out: str; err: str
def run(argv: list[str], timeout: float = 3.0, input_text: str | None = None) -> Run
```
`subprocess.run(argv, capture_output=True, text=True, timeout=timeout, input=input_text,
encoding="utf-8", errors="replace", shell=False)`. `FileNotFoundError` → `Run(False, 127, "",
"not installed")`; `TimeoutExpired` → `Run(False, -1, "", f"timed out after {timeout}s")`.
`ok = code == 0`. Assert `argv` is a list of str.

`xlib.py`:
- `class X11Display` context manager: `__enter__` loads libX11 (`ctypes.util.find_library("X11")
  or "libX11.so.6"`, cached at module level), `XOpenDisplay(None)`; None → `RuntimeError("Cannot
  open the X display.")`. `__exit__` → `XCloseDisplay`.
- `atom(name) -> int` (`XInternAtom(d, name, False)`), `root` attribute.
- `send(window: int, message: str, *data: int)` → ClientMessage (type 33, format 32,
  send_event 1) to the root with mask `(1 << 19) | (1 << 20)`, then `XFlush`. Data values are
  masked into C long range like `workspaces.py` does.
- `available() -> bool` → library loads and a display opens and closes.
- Module functions, each opening a display for one call:
  `activate(wid)` → `_NET_ACTIVE_WINDOW (2, 0, 0)`;
  `set_maximized(wid, on)` → `_NET_WM_STATE (1 if on else 0, atom(MAXIMIZED_VERT), atom(MAXIMIZED_HORZ), 2)`;
  `close(wid)` → `_NET_CLOSE_WINDOW (0, 2)`;
  `move_resize(wid, x, y, w, h)` → `_NET_MOVERESIZE_WINDOW (1 | (0xF << 8) | (2 << 12), x, y, w, h)`
  (gravity NorthWest = x,y is the frame's outer top-left; flags x,y,w,h; source = pager);
  `showing_desktop(on)` → to root `_NET_SHOWING_DESKTOP (1 or 0)`.
  These exact messages were verified on GNOME 46 (docs/001 §1).

`x11.py` `X11Backend`:
- `unsupported_reason()` → "" (selection already happened in `default_backend`).
- `probe()` → §8.2, cached on the instance.
- `own_pids()` → `{os.getpid(), os.getppid()}`.
- `windows()` → ids from `xprop -root _NET_CLIENT_LIST_STACKING` (`prop_ints`); for each id
  `xprop -id <id> WM_CLASS _NET_WM_NAME WM_NAME _NET_WM_DESKTOP _NET_WM_STATE _NET_WM_WINDOW_TYPE
  _NET_WM_PID _NET_FRAME_EXTENTS` and `xwininfo -id <id>`; skip ids whose calls fail (window
  closed meanwhile); `window_from_props`. Extents missing → (0,0,0,0).
- `active_window()` → `xprop -root _NET_ACTIVE_WINDOW` → int or None (0 → None).
- `monitors()` → `xrandr --listmonitors`. `workarea()` → first 4 ints of `xprop -root _NET_WORKAREA`.
- `desktops()` → `_NET_CURRENT_DESKTOP`, `_NET_NUMBER_OF_DESKTOPS` from one `xprop -root` call.
- `showing_desktop()` → `_NET_SHOWING_DESKTOP` == 1.
- `move_resize(wid, frame)` → read the window's extents (l, r, t, b) and send
  `xlib.move_resize(wid, frame.x, frame.y, max(1, frame.width-l-r), max(1, frame.height-t-b))`.
  Comment: `xdotool windowmove` lands offset by the frame on Mutter (measured), hence EWMH.
- `activate`, `set_maximized`, `close`, `set_showing_desktop` → `xlib`.
- `minimize` → `xdotool windowminimize <id>`; `set_window_desktop` → `xdotool set_desktop_for_window
  <id> <n>`; `switch_desktop` → `xdotool set_desktop <n>`.
- `overview()` → overlay key from `gsettings get org.gnome.mutter overlay-key` (strip quotes,
  `Super_L` → `super`), `xdotool key <key>`.
- `pointer()` → `xdotool getmouselocation --shell`; `pointer_move` → `xdotool mousemove <x> <y>`;
  `pointer_button(b, "click", n)` → `xdotool click --repeat <n> --delay 60 <b>`; down/up →
  `xdotool mousedown|mouseup <b>`.
- `key(combo)` → `xdotool key --clearmodifiers <combo>`.
- System methods delegate to `LinuxControls()` held by the backend.
- Any failed `Run` → `RuntimeError(f"{argv[0]} failed: {err or out}")` (≤200 chars).

`controls.py` `LinuxControls`:
- `volume()` → `wpctl get-volume @DEFAULT_AUDIO_SINK@` → `parse_wpctl_volume`.
- `set_volume(f)` → `wpctl set-volume -l 1.0 @DEFAULT_AUDIO_SINK@ <f:.2f>`;
  `change_volume(d)` → `… <abs(d)>%+` or `%-`; `toggle_mute()` → `wpctl set-mute @DEFAULT_AUDIO_SINK@ toggle`.
- `mic_muted()`/`toggle_mic()` → same with `@DEFAULT_AUDIO_SOURCE@`.
- `brightness()` → `gdbus call --session --dest org.gnome.SettingsDaemon.Power --object-path
  /org/gnome/SettingsDaemon/Power --method org.freedesktop.DBus.Properties.Get
  org.gnome.SettingsDaemon.Power.Screen Brightness` → `parse_gdbus_int`;
  `set_brightness(p)` → same with `…Properties.Set org.gnome.SettingsDaemon.Power.Screen Brightness "<int32 p>"`
  (the variant is one argv element: `f"<int32 {p}>"`).
- `media(c)` → `playerctl -l` empty → `RuntimeError("No media player is running.")`; else
  `playerctl play-pause|next|previous|stop`.
- `lock()` → `loginctl lock-session`.
- `clipboard_get()` → `xclip -o -selection clipboard` (empty clipboard → ""; a non-zero exit with
  empty output counts as "").
- `clipboard_set(t)` → `xclip -selection clipboard` with `input_text=t`, timeout 2 s. (xclip forks
  and keeps serving the selection; `subprocess.run` returns because xclip's parent exits.)

`unsupported.py` — as §4. `infrastructure/__init__.default_backend()`:
- not Linux → `UnsupportedBackend("macOS is not supported yet")` / `("Windows is not supported yet")`
  (use `shared.platform.IS_MAC/IS_WINDOWS`).
- `XDG_SESSION_TYPE == "wayland"` or (`WAYLAND_DISPLAY` set and no `DISPLAY`) →
  `UnsupportedBackend("this is a Wayland session; only X11 is supported for now")`.
- no `DISPLAY` → `UnsupportedBackend("there is no X display (DISPLAY is not set)")`.
- else `X11Backend()`. Never raises.

`layouts.py` — `load()`/`save()` as §4/§7.4; `Rect` serialised as `[x, y, w, h]`.

### 8.5 `bootstrap.py`

```python
from .modules.computer import ComputerService, default_backend
...
computer = ComputerService(default_backend())
tools.add_provider(memory); tools.add_provider(skills); tools.add_provider(computer)
...
return Container(bus, settings, connectors, tools, brain, voice, memory, skills, updates, assistant, computer)
```
`Container` gets `computer: ComputerService` as its last field.

### 8.6 `app.py` — `lura desktop`

```
lura desktop                       → capabilities
lura desktop window_list
lura desktop window_snap where=left app=terminal
lura desktop window_close window=0x2e00004 --yes
```
Subparser `desktop` (help `"desktop actions: list, run one (key=value args)"`): positional
`action` (nargs="?", default "capabilities"), positional `params` (nargs="*", each `key=value`;
a token without `=` → print `bad argument: <tok> (use key=value)` and return 2), flag `--yes`.
`cmd_desktop(args)`: build `ComputerService(default_backend())`, `run(action, params,
confirmed=args.yes)`, print `result.message`, then `json.dumps(result.data, indent=2,
ensure_ascii=False)` if data, then `({result.ms} ms)`. Return 0 if ok, 1 otherwise. For
`capabilities` print `data["actions"]` as plain text (not JSON-escaped). Add `"desktop":
cmd_desktop` to `handlers`. Values stay strings; `validate_args` converts them.

### 8.7 `tests/live_desktop.py` (not in run_all)

`import _setup` first. Skip (print `skipped: <why>`, exit 0) unless `default_backend()` is an
X11 backend whose probe has x11, xdotool, xprop, xwininfo, xrandr and `shutil.which("xeyes")`.
Then:
1. `p = subprocess.Popen(["xeyes", "-geometry", "300x200+100+100"])`; find its window with
   `xdotool search --sync --pid <p.pid>` (timeout 5 s); everything below passes `window=<id>`.
2. Through `ComputerService.run`, each printed as `name: ok/FAIL message (ms)`, and the frame rect
   read back from `window_list` asserted within ±48 px where a position/size is stated:
   window_move (400, 300) · window_move_by (50, 40) · window_resize (500, 350) · window_snap
   left, right, top_right on the window's monitor (expected = snap_rect of its work area, check
   x and width only) · window_maximize (maximized true) · window_restore (false) ·
   window_to_monitor next then prev (monitor index changes, skipped with one monitor) ·
   window_to_workspace next then prev (workspace changes, follow false) · window_minimize
   (minimized true) · window_restore (false) · layout_save "live-test" · window_move (200, 200)
   · layout_restore "live-test" (back within ±48 px) · layout_delete confirmed · window_close
   without confirm (needs_confirmation true, window still exists) · window_close confirmed
   (window gone within 2 s).
3. System, each restored in a `finally`: volume_change −5 then volume_set back to the original
   percent; brightness_change +10 (only if gsd_power) then brightness_set back; clipboard: save
   `clipboard_get` text, clipboard_set "lura-live-test", read back equal, set back the saved text;
   workspace_switch next then back to the original number; pointer_position then pointer_move to
   the same position.
4. Never run: lock_screen, media, mic_mute, pointer clicks/drags, press_keys, copy/paste keys,
   workspace_overview, show_desktop, app_switch, windows_arrange (they act on the user's real
   windows/session).
5. `finally`: `p.kill()`. Print a summary `live desktop: N passed, M failed` and exit 1 if any failed.

## 9. Tasks

1. `domain.py`: shapes, Param/ActionSpec, catalog, DESKTOP_TOOL, validation, geometry, parsers,
   `is_target`, `next_window`, `match_layout`, `catalog_text`.
2. `infrastructure/runner.py`, `xlib.py`, `unsupported.py`, `layouts.py`, `controls.py`, `x11.py`,
   `infrastructure/__init__.py`.
3. `application.py`: Backend, ComputerService (every `_do_<name>`), ToolProvider methods.
4. `__init__.py` exports.
5. `bootstrap.py`, `app.py` (`lura desktop`).
6. `tests/test_computer.py` + add to `tests/run_all.py` CHECKS (after `test_updates.py`).
7. `tests/live_desktop.py`.
8. `docs/ARCHITECTURE.md`: add under modules, after `tools/`:
   `    ├── computer/     desktop actions (windows, workspaces, layouts, pointer, system, clipboard), X11 backend, desktop_action tool`

## 10. Acceptance checks

`tests/test_computer.py` (pattern of `tests/test_skills.py`: `import _setup`, `check_*` functions,
`main()` printing `  <name> OK` per check and finally `computer OK`). It must cover:
- catalog: names unique and in §7.2 order; every name has `ComputerService._do_<name>`; every
  requires key is a §8.2 probe key; `DESKTOP_TOOL` enum equals the names; the three confirm actions.
- `validate_args`: "300" → 300, 2.0 → 2, out of range / missing required / bad choice / bad target
  raise `ActionError`; defaults filled; `0x2e00004` → 48234500.
- `validate_keys`: `ctrl+shift+t` ok, `Super+Left` → `super+Left`, `F5` ok; refused: `ctrl+alt+Delete`,
  `super+l`, `rm -rf`, `ctrl+;`, five tokens, `ctrl+ctrl+a`.
- geometry with the real numbers: work_area → `Rect(0,28,1920,1052)` and `Rect(1920,28,1920,1052)`;
  `snap_rect` all ten `where`s (halves cover the area exactly, odd widths); `grid_rects` 0–5;
  `monitor_for`; `clamp_visible` keeps a far-off rect reachable; `resolve_desktop`, `monitor_target`.
- parsers on these literal samples: `xrandr --listmonitors` (docs/001 §1 values), `xwininfo`,
  `xprop` for WM_CLASS / _NET_WM_NAME with an escaped quote / _NET_WM_STATE empty and set /
  _NET_FRAME_EXTENTS / `_NET_WM_WINDOW_TYPE:  not found.` / `window id # 0x600001, 0x2e00004`,
  wpctl with and without `[MUTED]`, gdbus `(<25>,)`, getmouselocation; garbage → empty/None.
- `is_target`: DESKTOP and DOCK types refused, app "Lura" refused, own pid refused, NORMAL and "" ok.
- `next_window`, `match_layout` (exact title wins, duplicates used once, unmatched listed).
- service with `FakeBackend` (in-memory windows/monitors, records calls): confirm flow for
  window_close (no `close` call without confirmed; then called); unsupported backend → window
  actions fail with the reason, layout_list still works, capabilities marks them unavailable;
  missing probe key → refusal naming it; ambiguous `app` → fail, no call; `window_snap left` →
  `set_maximized(False)` then `move_resize(Rect(0,28,960,1052))` for a maximized window;
  `window_to_monitor next` translates by 1920; `windows_arrange` with 3 windows on monitor 1;
  layout save → move → restore round trip via the temp config dir, missing app reported;
  `workspace_switch next` at the last workspace stays; `app_switch`; `pointer_drag` releases the
  button even when a move raises; mic_mute message mentions the wake word.
- ToolService integration: `ToolService().add_provider(svc)`; `dispatch("desktop_action",
  {"action": "window_list"})` returns text containing the fake window's title; `owns`;
  `tool_specs()` has exactly one spec.

Commands (run from the repo root, the worktree):

```bash
PYTHONPATH=src /var/www/html/old/lura/.venv/bin/python -m compileall -q src/
PYTHONPATH=src /var/www/html/old/lura/.venv/bin/python tests/test_computer.py      # → computer OK
PYTHONPATH=src /var/www/html/old/lura/.venv/bin/python tests/run_all.py           # → All checks passed.
PYTHONPATH=src /var/www/html/old/lura/.venv/bin/python -c "import lura.modules.computer as c; print(len(c.ACTIONS))"   # → 47
DISPLAY= PYTHONPATH=src /var/www/html/old/lura/.venv/bin/python -m lura.app desktop window_list; echo $?   # message names "no X display", exit 1
```
(The `.venv` belongs to the main checkout; `PYTHONPATH=src` makes it import this worktree's code.)

**Do not run** `tests/live_desktop.py`, `lura desktop` with a real `DISPLAY`, `xdotool`, or
anything else that moves, closes or types into windows or changes volume/brightness/clipboard on
this machine. The reviewer runs the live checks.

## 11. Out of scope

- Gestures, camera, MediaPipe, modes, the overlay, the dashboard, settings fields (Stages C–F).
- Workspace presets that launch applications (Stage D), file moving / file-manager automation,
  AT-SPI, camera controls, Wayland/macOS/Windows implementations.
- Any change to `tools/domain.py` (`BUILTIN_TOOLS`, `check_command`) or to `run_command`.
- README / USAGE / AGENTS / CLAUDE.md, i18n files, pyproject, packaging.
- Do not install packages. Do not commit. Do not edit any file under `docs/` other than the one
  line in `docs/ARCHITECTURE.md`.

## 12. Decisions

- Review fixes (Claude, after the live run):
  - `runner.run(..., capture=False)` + `clipboard_set` uses it. §8.4's claim that `subprocess.run`
    returns once xclip's parent exits was wrong: the forked child keeps the captured pipe open and
    every `clipboard_set` hung until the 2 s timeout.
  - `X11Backend.desktops()` reports the desktop it just switched to for up to 1 s: Mutter updates
    `_NET_CURRENT_DESKTOP` after a delay, so switching straight back read as "already there" and
    quick repeated swipes would have been lost.
  - `tests/live_desktop.py` finds the new xeyes window by class (xeyes sets no `_NET_WM_PID`).
  - Neon skipped the ARCHITECTURE.md line (its prompt forbids editing `docs/`); added by Claude.
- Accepted deviation: `infrastructure/__init__.py` imports `Backend` under `TYPE_CHECKING`
  (avoids a cycle with `application.py`).
- Live result (this machine): 25/25 checks pass; window actions 90–260 ms (mostly the `xprop`/
  `xwininfo` window scan), system actions 8–30 ms. `layout_restore` touches every open window;
  measured save→restore drift on 9 real windows: 0 px. Stage D (pinch-drag) needs a faster
  per-frame move than a full window scan.
- `DESKTOP_TOOL` is ~3.6k chars on every turn; revisit (e.g. behind `find_tools`) if latency shows.
