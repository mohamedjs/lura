# 005 — Lura Air Stage D2: workspace presets
Status: Implemented (2026-11-20, Gemini 4 Neon; reviewed, fixed and tested live by Claude, see §11)
Builds on: docs/002-air-desktop-actions.md, docs/004-air-control.md

## 1. Goal

A **preset** is a named set of the user's own apps with where each one goes ("developer":
editor left half, terminal right half on monitor 2, browser on workspace 2…). Applying it opens
the apps that are not running, waits for their windows, and places every window. Nothing is
hard-coded: there are no built-in presets; the user (or Lura, by voice) defines them, or saves
the current desktop as one. Exposed as `desktop_action` actions, so voice works now
("Lura, open my research workspace"); a gesture for presets comes with modes (Stage E).

## 2. Stack

Nothing new. Launching reuses the existing `open_application` built-in (tools module): it is
**injected** into `ComputerService` as a callable, so `computer` never imports `tools` and the
.desktop lookup is not duplicated.

## 3. Structure

```
src/lura/modules/computer/domain.py                 EDIT  PresetApp, Preset, validate_preset, match rules, 5 catalog entries, "spec" tool property
src/lura/modules/computer/application.py            EDIT  launcher injection, _do_preset_* handlers
src/lura/modules/computer/infrastructure/presets.py NEW   presets.json load/save
src/lura/bootstrap.py                               EDIT  pass the launcher
src/lura/app.py                                     EDIT  cmd_desktop passes a launcher
tests/test_computer.py                              EDIT  preset checks
```
No other file is created or changed.

## 4. Responsibilities and rules

- `domain.py` pure (validation, matching, entry building). `application.py` orchestrates
  (find / launch / wait / place). `presets.py` = file I/O only, same pattern as `layouts.py`
  (atomic write, corrupt file → `{}` + warning).
- `ComputerService(backend, layouts_store=None, presets_store=None, launcher=None)`;
  `launcher: Callable[[str], str] | None` — takes an app name, returns a sentence. None →
  `preset_apply` still places running apps and reports the others as "not running".
- `computer` still imports nothing from `tools`, `air`, `assistant`.
- Conventions as docs/002 §6.

## 5. Interfaces (`domain.py`)

```python
@dataclass(frozen=True)
class PresetApp:
    launch: str            # what to open, given to the launcher: an app name ("terminal", "org.gnome.Calculator")
    match: str             # case-insensitive substring of Window.app or Window.title that identifies its window
    where: str = ""        # a window_snap position ("left", "top_right", …, "full") or "" = keep size
    rect: Rect | None = None   # exact frame rect; wins over `where`
    monitor: int = 0       # 1-based; 0 = the monitor the window is on
    workspace: int = 0     # 1-based; 0 = leave it where it is

@dataclass(frozen=True)
class Preset:
    name: str
    apps: tuple[PresetApp, ...]
    saved_at: str
```
`presets.json`: `{"version": 1, "presets": {"<name>": {"saved_at": "...", "apps": [{"launch": ..,
"match": .., "where": .., "rect": [x,y,w,h] | null, "monitor": 0, "workspace": 0}]}}}`.

- `validate_preset(name: str, apps: list[dict]) -> Preset` — name `^[\w \-]{1,40}$`; 1–8 apps;
  each: `launch` str 1–80 (required), `match` str 0–80 (empty → `launch` with a leading
  reverse-domain prefix removed and lower-cased: "org.gnome.Calculator" → "calculator"), `where` in
  the window_snap choices or "", `rect` list of 4 ints with width/height ≥ 100 or None, `monitor`
  0–9, `workspace` 0–36. Unknown keys ignored. Errors → `ActionError` naming the app index and field.
- `preset_from_windows(name, windows, monitors, saved_at) -> Preset` — one PresetApp per window
  (max 8, top of stack first): launch = match = `window.app`, rect = `window.rect`,
  workspace = desktop+1 (or 0 for −1), monitor 0.
- `find_window(app: PresetApp, windows, used: set[int]) -> Window | None` — first window not in
  `used` whose app or title contains `match` (case-insensitive).

### Catalog (append after `press_keys`, in this order)

| name | group | safety | params | requires |
|---|---|---|---|---|
| preset_list | layout | safe | — | () |
| preset_define | layout | safe | name str req max 40, spec str req max 4000 (JSON list of app objects) | () |
| preset_save | layout | safe | name str req max 40 | WIN_REQ |
| preset_apply | layout | safe | name str req max 40 | WIN_REQ + ("xdotool",) |
| preset_delete | layout | **confirm** | name str req max 40 | () |

`DESKTOP_TOOL`: add property `spec` (string, "For preset_define: JSON list of apps, each
{launch, match?, where?, monitor?, workspace?}") — the enum grows automatically. Add one sentence
to the description: `"Presets open the user's apps and place them: preset_define / preset_save,
then preset_apply."` (keep the rest exactly).

## 6. Handlers (`application.py`)

- `preset_list` → data `{"presets": [{"name", "apps": [launch…], "saved_at"}]}`.
- `preset_define` → `json.loads(spec)` (error → ActionError "spec is not valid JSON: …"),
  must be a list → `validate_preset` → store (overwrite allowed) → "Saved preset 'x' with N apps."
- `preset_save` → `preset_from_windows` over non-minimized target windows → store.
- `preset_delete` → like layout_delete.
- `preset_apply(name)`:
  1. Unknown name → fail listing names.
  2. Under the lock: `windows = self._targets()`; for each app `find_window` (marking used).
  3. For apps without a window: if no launcher → note "not running"; else call
     `launcher(app.launch)` **outside the lock** and remember the ids that existed before.
  4. Wait (outside the lock) up to 10 s, polling `self._targets()` every 0.4 s, until every
     launched app has a *new* window (`find_window` over windows whose id was not there before).
     Apps still missing after 10 s → note "did not open".
  5. Under the lock, for each (app, window): workspace (if > 0 and differs) via
     `set_window_desktop`; if `rect` → unmaximize, `move_resize(clamp_visible(rect, bounds))`;
     elif `where == "full"` → maximize; elif `where` → monitor = `monitors[app.monitor-1]` if
     `monitor` > 0 (and exists) else `monitor_for(window.rect)`; unmaximize;
     `move_resize(snap_rect(work_area(monitor, workarea), where))`; else, if `monitor` > 0,
     move to that monitor like `window_to_monitor`.
  6. Message: `"Applied preset 'x': N placed (M opened)."` + `" Not running: a, b."` /
     `" Did not open: c."` when any. ok = at least one window placed.
  `time.sleep` and the clock are `self._sleep` / `self._clock` (defaults `time.sleep`,
  `time.monotonic`) so tests run instantly.

## 7. Wiring

- `bootstrap.py`: `computer = ComputerService(default_backend(), launcher=lambda name:
  tools.dispatch("open_application", {"app_name": name})[0])` (tools is built before computer).
- `app.py cmd_desktop`: build `ToolService()` and pass the same launcher lambda.

## 8. Tasks

1. domain: dataclasses, validate_preset, preset_from_windows, find_window, catalog + tool property.
2. infrastructure/presets.py.
3. application: constructor args, five handlers, clock/sleep hooks.
4. bootstrap + app.py.
5. tests.

## 9. Acceptance checks

In `tests/test_computer.py` (new `check_preset_*` functions, FakeBackend extended with a
`spawn(app)` the fake launcher calls to add a window after N polls):
- validate_preset: good spec; 9 apps; bad where; rect too small; missing launch; derived match
  ("org.gnome.Calculator" → "calculator").
- define → list → apply with all apps running: correct move_resize per `where` (incl. a
  monitor 2 target) and set_window_desktop for workspace; no launcher call.
- apply with one app not running: launcher called once with its `launch`; the window appears on
  the 3rd poll → placed; message says "(1 opened)".
- app that never appears → "Did not open: …", the others still placed.
- no launcher → "Not running: …".
- preset_save from 3 fake windows → apply after moving them restores rects.
- preset_delete needs confirmation; unknown preset lists names; invalid JSON spec → clear error.
- catalog: 52 actions, the 5 new ones at the end, `spec` in DESKTOP_TOOL properties.

```bash
.venv/bin/python -m compileall -q src/
.venv/bin/python tests/test_computer.py   # → computer OK
.venv/bin/python tests/run_all.py         # → All checks passed.
```
Do not run `lura desktop` with a real display, `tests/live_*.py`, or anything that opens apps
or moves windows. No packages, no commits, no edits under `docs/`.

## 10. Out of scope

A gesture to pick a preset (Stage E), dashboard UI for presets (Stage F), built-in preset
examples, app launching outside the injected launcher.

## 11. Decisions

- 2026-11-20: launching goes through the existing `open_application` built-in via an injected
  callable — one .desktop lookup in the codebase, and `computer` keeps not depending on `tools`.
- 2026-11-20: no shipped presets: the user's apps differ; a preset is defined by voice/CLI JSON
  or saved from the current desktop.
- Implemented by Gemini 4 Neon in one round (no deviations). Live test (Claude): preset
  "calculator left, Text Editor right, monitor 1" opened both apps and placed them; the calculator
  was exact, Text Editor (GTK4, client-side shadow) looked ~60 px too small on each side. Fixed in
  `computer/infrastructure/x11.py`: frame extents now also read `_GTK_FRAME_EXTENTS` (as negative
  extents) for both the window list and `move_resize`. After the fix: exact right half when
  already open, on re-apply (no drift) and when freshly opened. This also corrects window_snap,
  layouts and Air drags for every GTK4/libadwaita app.
