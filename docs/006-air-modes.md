# 006 — Lura Air Stage E: modes and the context-aware resolver
Status: Implemented (2026-11-20, Gemini 4 Neon; reviewed and partly tested live by Claude, see §12)     Builds on: docs/001-lura-air.md, docs/002, docs/004-air-control.md, docs/005

## 1. Goal

The same gesture does the right thing for where the user is. A **mode** (9 of them) says how
gestures are read; in the default **universal** mode the **active app's kind** (browser, media
player, presentation, file manager, editor, terminal) decides. Mode changes by gesture (a held
victory sign cycles), by voice (Lura's new `air_mode` tool), or from the terminal. Gestures that
need Lura's judgement (point, thumbs up/down, everything in **ai** mode) are not acted on here:
they become `AIR_INTENT` events for the assistant (wired in Stage F). Nothing is guessed: a
gesture with no meaning in the current mode/app is ignored with a reason (`AIR_ACTION ok=False`).

## 2. Stack

Nothing new. Worktree venv `.venv`. Do not install, download, open the camera or move windows.

## 3. Structure

```
src/lura/modules/air/domain.py             EDIT  MODES, APP_KINDS, app_kind(), Resolution, resolve(), hand_to_screen(), POINTER constants
src/lura/modules/air/application.py        EDIT  AirController: mode state, resolver use, pointer mode, intents, air_mode ToolProvider
src/lura/modules/air/__init__.py           EDIT  export MODES, Resolution, resolve
src/lura/modules/computer/application.py   EDIT  active_app(), pointer_to()
src/lura/modules/settings/domain.py        EDIT  air_app_kinds field
src/lura/modules/dashboard/api.py          EDIT  "air_app_kinds" in LIVE
src/lura/shared/events.py                  EDIT  AIR_MODE, AIR_INTENT
src/lura/bootstrap.py                      EDIT  tools.add_provider(air_control); app kinds from settings
src/lura/app.py                            EDIT  `lura air control --mode <mode>`
tests/test_air_modes.py                    NEW   resolver table, app kinds, controller with modes (in run_all)
tests/run_all.py                           EDIT  add "test_air_modes.py"
```

## 4. Rules

- The resolver is **pure** (`air/domain.py`): (gesture, pose, mode, kind) → `Resolution`. All
  mapping lives in one table there, so it can be read and changed in one place.
- The controller does I/O: asks `computer` for the active app (once per discrete gesture), runs
  the resolution, publishes events. Pointer moves use a fast path (no log line, no scan).
- `air` still imports only `computer` and `shared`; `computer` gains two small methods.
- Conventions as docs/002 §6.

## 5. Interfaces (`air/domain.py`)

```python
MODES = ("universal", "window", "workspace", "pointer", "files", "media", "developer",
         "presentation", "ai")
KINDS = ("browser", "media", "presentation", "files", "editor", "terminal", "other")

#: Lower-case substrings of WM_CLASS → app kind. First match wins; user entries (settings
#: air_app_kinds) are checked before these.
APP_KINDS: tuple[tuple[str, str], ...] = (
    ("libreoffice-impress", "presentation"), ("impress", "presentation"), ("pdfpc", "presentation"),
    ("google-chrome", "browser"), ("chromium", "browser"), ("firefox", "browser"),
    ("brave", "browser"), ("microsoft-edge", "browser"), ("vivaldi", "browser"), ("opera", "browser"),
    ("vlc", "media"), ("mpv", "media"), ("spotify", "media"), ("rhythmbox", "media"),
    ("totem", "media"), ("celluloid", "media"), ("audacious", "media"),
    ("nautilus", "files"), ("org.gnome.nautilus", "files"), ("dolphin", "files"),
    ("thunar", "files"), ("nemo", "files"), ("pcmanfm", "files"),
    ("code", "editor"), ("jetbrains", "editor"), ("gedit", "editor"), ("text-editor", "editor"),
    ("sublime", "editor"), ("zed", "editor"),
    ("gnome-terminal", "terminal"), ("terminator", "terminal"), ("kitty", "terminal"),
    ("alacritty", "terminal"), ("konsole", "terminal"), ("tilix", "terminal"), ("wezterm", "terminal"),
)

def app_kind(app: str, title: str, overrides: dict[str, str] | None = None) -> str
    # app = WM_CLASS class; checks overrides (key substring of app.lower() → value, value must be
    # in KINDS) then APP_KINDS; LibreOffice: app "libreoffice" or "soffice" with "impress" in
    # title.lower() → "presentation". No match → "other".

@dataclass(frozen=True)
class Resolution:
    kind: str                  # "action" | "intent" | "ignore" | "mode"
    action: str = ""           # computer action name (kind "action")
    args: dict = field(default_factory=dict)
    intent: str = ""           # kind "intent": "point" | "confirm" | "deny" | "talk"
    reason: str = ""           # kind "ignore": a short sentence

def resolve(gesture: str, pose: str, mode: str, kind: str) -> Resolution
```
`gesture` is a GestureEvent name; `pose` is `data["pose"]` for "pose" events, else "".
Continuous gestures (pinch drag, spread) are **not** resolved here — the controller keeps the
Stage D1 behaviour for them in every mode except `pointer`, `presentation` and `ai` (see §6).

### 5.1 The table (`resolve`)

Global, before the mode table, in every mode:
- pose `victory` → `Resolution("mode")` (the controller cycles the mode).
- pose `thumbs_up` → intent "confirm"; pose `thumbs_down` → intent "deny"; pose `point` → intent "point".
- in mode `ai`: every other gesture → intent "talk" with `args={"gesture": gesture}`, except
  `pinch_start/pinch_end/hand_enter/hand_leave/pose`(other poses) → ignore "".
- `pose` events not listed (open_palm, fist, pinch, none) and hand_enter/hand_leave,
  pinch_start/pinch_end, spread_start/spread_end → `ignore` with reason "" (no feedback).

Swipes and double pinch (S_L, S_R, S_U, S_D = swipe_left/right/up/down; DP = double_pinch).
`K(x)` = `press_keys` with `{"keys": x}`; `WS(t)` = workspace_switch target t:

| mode | S_L | S_R | S_U | S_D | DP |
|---|---|---|---|---|---|
| universal | by kind ↓ | by kind ↓ | workspace_overview | show_desktop | maximize/restore |
| window | window_snap left | window_snap right | window_maximize | window_minimize | maximize/restore |
| workspace | WS prev | WS next | workspace_overview | show_desktop | ignore |
| pointer | ignore | ignore | pointer_scroll up 5 | pointer_scroll down 5 | pointer_click left ×2 |
| files | K(alt+Left) | K(alt+Right) | K(alt+Up) | ignore | K(Return) |
| media | media previous | media next | volume_change +10 | volume_change -10 | media play_pause |
| developer | K(ctrl+Page_Up) | K(ctrl+Page_Down) | WS prev | WS next | maximize/restore |
| presentation | K(Left) | K(Right) | ignore | ignore | ignore |

Universal by kind (S_L / S_R): browser → K(ctrl+shift+Tab) / K(ctrl+Tab); media → media
previous / next; presentation → K(Left) / K(Right); files → K(alt+Left) / K(alt+Right);
editor, terminal → K(ctrl+Page_Up) / K(ctrl+Page_Down); other → WS prev / WS next.
"maximize/restore" = `Resolution("action", "toggle_maximize")` (a controller pseudo-action:
`is_maximized()` → window_restore else window_maximize, as in Stage D1).
Ignore reasons: `f"{gesture} does nothing in {mode} mode."`.

### 5.2 Pointer helpers

```python
POINTER_MARGIN = 0.15   # the central 70 % of the frame maps to the whole screen
POINTER_SMOOTH = 0.35   # exponential smoothing of the pointer target per tick (0..1, higher = snappier)
def hand_to_screen(x: float, y: float, bounds: Rect) -> tuple[int, int]
    # u = clamp((x - M) / (1 - 2M), 0, 1), same for v; return (bounds.x + round(u*(w-1)), bounds.y + round(v*(h-1)))
```

## 6. Controller (`air/application.py`)

New state: `_mode = "universal"`, `_kinds: Callable[[], dict[str, str]]` (constructor kwarg
`app_kinds=lambda: {}`), `_pointer: tuple[float, float] | None` (smoothed target),
`_pointer_down: bool`.

- `mode` property; `set_mode(mode: str) -> None` (unknown → `ValueError`; ends any drag/resize,
  releases a held pointer button; publishes `AIR_MODE {"mode": m}`; feedback "mode" ok
  `f"Air mode: {m}."`); `next_mode()` cycles `MODES` in order (after `ai` comes `universal`).
- `process(ev)`, after the arming/fist logic of Stage D1 (unchanged):
  1. Continuous gestures keep D1 behaviour (pinch_start pending, tick grip/drag, pinch_end
     drop/snap, spread start/end) **only** in modes universal, window, workspace, files, media,
     developer. In `pointer` mode: `pinch_start` → if no button down: remember t; `pinch_end` →
     if the pinch lasted < 0.3 s and the hand moved < DRAG_START → `pointer_click left`; else if
     `_pointer_down` → `pointer_up`; in `tick`, a pinch held ≥ 0.3 s → `pointer_down` (drag).
     In `presentation` and `ai`: no window drag/resize.
  2. For any other event: `kind = self._kind()` only for swipes in universal mode (one
     `desktop.active_app()` call); `r = resolve(ev.gesture, ev.data.get("pose", ""), mode, kind)`.
     - `mode` → `next_mode()`.
     - `intent` → publish `AIR_INTENT {"intent": r.intent, "mode": mode, "gesture": ev.gesture,
       "app": app, "title": title}` (app/title from `active_app()`, "" if none); feedback
       "intent" ok `f"Intent {r.intent}."`.
     - `action` → `toggle_maximize` handled as in D1; else `desktop.run(r.action, r.args)`; feedback.
     - `ignore` → if `r.reason`: feedback ok False with the reason (no desktop call).
  Discrete actions keep the `ACTION_MIN_CONF` and "not while dragging" rules of D1.
- `tick(t)` in `pointer` mode (armed, no drag): the first hand with a view → target =
  `hand_to_screen(h.x, h.y, bounds)`; smoothed `p = p + POINTER_SMOOTH * (target - p)`;
  `desktop.pointer_to(round(px), round(py))` when it moved ≥ 3 px. `bounds` from
  `desktop.screen_bounds()` cached at `set_mode("pointer")`.
- `state()` adds `"mode"`.
- ToolProvider (so the brain can switch modes by voice):
  `tool_specs()` → one `ToolSpec("air_mode", "Switch Lura Air (hand gesture control) mode, or
  report it. Modes: universal (gestures follow the active app), window, workspace, pointer (hand
  moves the mouse), files, media, developer, presentation (swipes change slides), ai (gestures go
  to you).", {"type": "object", "properties": {"mode": {"type": "string", "enum": [*MODES,
  "status"], "description": "The mode, or status to report it."}}, "required": ["mode"]})`;
  `owns(name)` → `name == "air_mode"`; `call(name, args)` → "status" → `f"Air is {on/off}, mode
  {mode}."` (on = the AirService is running); else `set_mode` → `f"Air mode is now {mode}."`;
  unknown → `"Unknown mode …"`. Import `ToolSpec` from `..brain` (package).

## 7. computer additions (`application.py`)

- `active_app() -> tuple[int, str, str] | None` — (id, app, title) of the active window if it is
  a target window; reads only that window (`backend.active_window()` + the existing per-window
  read — add `Backend.window(wid) -> Window | None` to the Protocol and implement it in
  `X11Backend` by factoring the per-window part of `windows()`; `UnsupportedBackend` raises).
  `is_target` rules apply; non-target → None.
- `pointer_to(x: int, y: int) -> None` — clamp into `bounds(monitors)` (monitors cached for 5 s on
  the service) and `backend.pointer_move`; no log line.

## 8. Settings, events, wiring

- `settings/domain.py` after `air_camera`:
  ```python
  #: Lura Air: your own app kinds, WM_CLASS part → browser, media, presentation, files, editor,
  #: terminal or other, e.g. {"obsidian": "editor"}. Checked before the built-in list.
  air_app_kinds: dict[str, str] = field(default_factory=dict)
  ```
  and `"air_app_kinds"` in dashboard `LIVE`.
- `shared/events.py`: `AIR_MODE = "air.mode"` (`{"mode"}`), `AIR_INTENT = "air.intent"`
  (`{"intent", "mode", "gesture", "app", "title"}`), each with a `#:` comment.
- `bootstrap.py`: `AirController(..., app_kinds=lambda: settings.current.air_app_kinds)` and
  `tools.add_provider(air_control)` (after it is built).
- `app.py`: `lura air control` gets `--mode` (choices MODES, default universal) → `ctrl.set_mode`
  after start; the printer also prints AIR_MODE / AIR_INTENT payloads (the publish callback
  receives them too).

## 9. Tasks

1. air/domain: MODES, KINDS, APP_KINDS, app_kind, Resolution, resolve, pointer helpers.
2. computer: Backend.window, X11/Unsupported, active_app, pointer_to.
3. air/application: modes, resolver use, pointer mode, intents, ToolProvider.
4. settings, LIVE, events, bootstrap, app.py.
5. tests + CHECKS.

## 10. Acceptance checks

`tests/test_air_modes.py` (prints `air modes OK`):
- `resolve`: every cell of the §5.1 table (loop over a literal expected table in the test);
  universal swipes for each kind; globals (victory → mode, thumbs → confirm/deny, point → intent)
  in every mode; ai mode swipe → intent talk; unknown combos → ignore with the reason text.
- `app_kind`: "Google-chrome" → browser, "gnome-terminal-server" → terminal, "libreoffice" +
  title "deck.odp - LibreOffice Impress" → presentation, "Gnome-text-editor" → editor,
  "XEyes" → other, override {"xeyes": "media"} → media, an override with a bad kind is ignored.
- `hand_to_screen`: margins map to the edges, centre to the centre (bounds 0,0,3840,1080).
- Controller (FakeAir/FakeDesktop from test_air_control.py, extend FakeDesktop with
  `active_app()` and `pointer_to`): armed universal + browser app + swipe_right → `press_keys
  ctrl+Tab`; same with app "other" → `workspace_switch next`; media mode swipe_up →
  `volume_change +10`; presentation mode: pinch + move → no grip; window mode swipe_left →
  `window_snap left`; victory pose → mode becomes "window", AIR_MODE published; 9 victories →
  back to universal; thumbs_up → AIR_INTENT confirm with app/title, no desktop call; ai mode
  swipe → AIR_INTENT talk; pointer mode: tick moves pointer_to towards the hand, short pinch →
  pointer_click, long pinch → pointer_down then pointer_up on release; set_mode releases a held
  button; ignore with reason → AIR_ACTION ok False, no desktop call.
- ToolProvider: `tool_specs()` has `air_mode` with the enum; `call("air_mode", {"mode": "media"})`
  sets the mode; `{"mode": "status"}` reports it; ToolService integration (`ToolService();
  add_provider(ctrl); dispatch("air_mode", {"mode": "pointer"})`).
- computer: `active_app` returns the active target window via `backend.window`, None for a
  DESKTOP-type window; `pointer_to` clamps and does not call `monitors()` twice within 5 s.

```bash
.venv/bin/python -m compileall -q src/
.venv/bin/python tests/test_air_modes.py     # → air modes OK
.venv/bin/python tests/run_all.py            # → All checks passed.
```
Do not run `lura air …`, `lura run`, `tests/live_*.py`. No packages, no commits, no docs/ edits.

## 11. Out of scope

Overlay mode indicator, captions and the assistant consuming AIR_INTENT (Stage F); dashboard
UI for modes/app kinds (Stage F); global keyboard shortcuts (needs an X key grab — not in this
stage; the terminal flag and voice cover switching); presets by gesture.

## 12. Decisions

- 2026-11-20: thumbs up/down and point are intents for Lura, not desktop actions: confirming a
  pending request is the assistant's job (Stage F wires it), so the gesture never confirms
  something by itself.
- 2026-11-20: a held victory sign cycles modes — the one pose no mode uses for anything else.
- 2026-11-20: universal mode reads the active app's WM_CLASS, configurable via `air_app_kinds`;
  presentation apps inside a browser (Google Slides) count as browser — the user switches to
  presentation mode for those.
- Implemented by Gemini 4 Neon in one round (no deviations); the X11 `window()` refactor kept the
  GTK frame-extent fix. Claude added: `computer._target("active")` falls back to the front
  window on the current workspace (a D1 live test had 13 of 31 window gestures fail with "no
  active window" after workspace switches / show desktop).
- Live test (phone camera, universal mode, 4 min): browser swipes → ctrl+Tab / ctrl+shift+Tab
  (9×), VLC focused → media previous/next (4×), point → AIR_INTENT with app/title, snap, resize,
  overview OK. Victory was not recognised in that run (the user's V had the middle finger at
  ~1.09–1.14 < EXTENDED, read as point); a per-frame probe with a clearly open V (middle
  1.28–1.37) gave 3/3 victory events from the Recognizer. Not yet verified live: mode cycling
  end to end, pointer / window / files / media / developer / presentation / ai modes.
