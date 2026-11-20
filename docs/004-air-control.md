# 004 — Lura Air Stage D1: gestures control windows and workspaces
Status: Implemented (2026-11-20, Gemini 4 Neon, 3 rounds; reviewed and tested live by Claude, see §11)     Builds on: docs/001-lura-air.md, docs/002-air-desktop-actions.md, docs/003-air-hand-tracking.md

## 1. Goal

Make the recognised gestures (Stage C, `modules/air`) act on the desktop (Stage B,
`modules/computer`), safely and smoothly:

| Gesture (while armed) | Action |
|---|---|
| open palm held | **arm** Air control (nothing acts until then); stays armed 8 s after the last hand/gesture |
| fist | **disarm**; a window being dragged/resized is dropped where it is |
| pinch + move | grab the **active** window and move it with the hand |
| release pinch near the left/right screen edge | snap the window to that half |
| double pinch | maximize the active window, or restore it if it is maximized |
| both hands pinch, spread / close | resize the active window around its centre |
| swipe left / right | previous / next workspace |
| swipe up | workspace overview |
| swipe down | show desktop (toggle) |

Off by default (`air_enabled`). When on, the daemon runs the camera and this controller.
Context-aware mapping (modes, browser tabs, slides…) is Stage E; face feedback is Stage F —
this stage only publishes an `AIR_ACTION` event each time it acts.

## 2. Stack and packages

Nothing new. Uses the worktree venv `/var/www/html/old/lura-air/.venv` (mediapipe + opencv are
installed there; the hand model is already in `~/.local/share/lura/air/`). Do not install
anything, do not download, **do not open the camera, do not move real windows**.

## 3. Structure

```
src/lura/modules/computer/domain.py        EDIT  WindowGrip dataclass
src/lura/modules/computer/application.py   EDIT  grip(), place(), is_maximized(), screen_bounds()
src/lura/modules/computer/__init__.py      EDIT  export Rect, WindowGrip
src/lura/modules/air/domain.py             EDIT  control constants, drag/spread/edge geometry, Arming
src/lura/modules/air/application.py        EDIT  AirService.set_camera(); AirController + DesktopPort
src/lura/modules/air/__init__.py           EDIT  export AirController, Arming
src/lura/modules/settings/domain.py        EDIT  air_enabled, air_camera fields
src/lura/modules/dashboard/api.py          EDIT  add "air_enabled", "air_camera" to LIVE
src/lura/shared/events.py                  EDIT  AIR_ACTION
src/lura/bootstrap.py                      EDIT  build AirService + AirController, Container fields
src/lura/app.py                            EDIT  start/stop Air in cmd_run; `lura air control`
tests/test_air_control.py                  NEW   pure + fake-desktop controller checks (in run_all)
tests/run_all.py                           EDIT  add "test_air_control.py" to CHECKS
```
No other file is created or changed.

## 4. File responsibilities

- **computer/** — gains a *fast path* for continuous moves: `grip()` resolves and validates a
  window once (the normal full window scan, ~200 ms); `place()` then sends a single EWMH
  move/resize (~1 ms) with no scan. Nothing in computer knows about gestures.
- **air/domain.py** — pure geometry and the arming rules; no time source (times passed in).
- **air/application.py** — `AirController`: turns Air events + snapshots into computer calls on
  its own thread (`air-control`), never on the camera thread. Depends on the `DesktopPort`
  protocol (implemented by `ComputerService`) — it imports `..computer` only for `Rect`.
- **settings/domain.py** — two fields with docstring comments in the file's style.
- **bootstrap.py** — builds both objects (cheap; no camera opened). **app.py** — starts/stops them.

## 5. Layering rules

- `air` may import the `computer` **package** (Rect, WindowGrip). `computer` must never import
  `air`. Neither imports `assistant`, `overlay`, `dashboard`.
- Desktop calls happen only on the `air-control` thread. The camera thread only enqueues.
- The bus gets only discrete `AIR_ACTION` events. Per-frame data stays in snapshots.

## 6. Coding conventions

As docs/002 §6 and docs/003 §6. Constants in `air/domain.py` with a one-line comment each.

## 7. Interfaces

### 7.1 computer

```python
# computer/domain.py
@dataclass(frozen=True)
class WindowGrip:
    id: int
    title: str
    start: Rect           # frame rect at grip time (after un-maximizing, see below)
    bounds: Rect          # union of monitors at grip time
```
`ComputerService` (application.py), all under `self._lock`:
- `grip(window: str | int = "active") -> WindowGrip` — `validate_args`-style target parsing
  (reuse `_target({"window": window, "app": None})`); raises `ActionError` like `_target`.
  If the window is maximized: `set_maximized(False)` and immediately `move_resize` it to
  `snap_rect(work_area(monitor, workarea), "center")` — that rect is `start` (a maximized window
  cannot be dragged, and its restored size is unknown). Logs `desktop grip <id>`.
- `place(grip: WindowGrip, rect: Rect) -> None` — `clamp_visible(rect, grip.bounds)` then
  `backend.move_resize(grip.id, rect)`. No scan, no validation beyond the grip. Exceptions
  propagate (the controller catches them).
- `is_maximized(window: str | int = "active") -> bool` — via `_target`.
- `screen_bounds() -> Rect` — `bounds(backend.monitors())`.
Export `Rect` and `WindowGrip` from `computer/__init__.py` (add to `__all__`).

### 7.2 air/domain.py additions (exact values)

```python
ARM_IDLE = 8.0          # s armed after the last hand or gesture
ACTION_MIN_CONF = 0.75  # discrete gestures below this confidence are ignored
DRAG_START = 0.03       # normalized hand travel while pinching before a window is grabbed
DRAG_GAIN = 1.6         # screen pixels moved per pixel of hand travel (hand travel × bounds size × gain)
EDGE_SNAP = 0.08        # releasing within this of the left/right frame edge snaps the window
PLACE_MIN_PX = 4        # skip a place() that moves less than this
CONTROL_HZ = 30         # how often an active drag/resize is updated
SPREAD_MIN, SPREAD_MAX = 0.5, 2.5   # resize scale clamp
MIN_W, MIN_H = 320, 240             # smallest size a spread can make a window

SWIPE_ACTIONS: dict[str, tuple[str, dict]] = {
    "swipe_left": ("workspace_switch", {"target": "prev"}),
    "swipe_right": ("workspace_switch", {"target": "next"}),
    "swipe_up": ("workspace_overview", {}),
    "swipe_down": ("show_desktop", {}),
}
```
Rects in air are `computer.Rect`.

- `drag_rect(start: Rect, hand0: tuple[float, float], hand: tuple[float, float], bounds: Rect) -> Rect`
  — `dx = round((hand[0]-hand0[0]) * bounds.width * DRAG_GAIN)`, same for y with height;
  `start.translate(dx, dy)`.
- `edge_snap(x: float) -> str | None` — `"left"` if `x <= EDGE_SNAP`, `"right"` if
  `x >= 1 - EDGE_SNAP`, else None.
- `spread_rect(start: Rect, scale: float) -> Rect` — clamp scale to [SPREAD_MIN, SPREAD_MAX];
  width/height = max(MIN_W/MIN_H, round(start.w/h * scale)); same centre as `start`.
- `moved(a: Rect, b: Rect) -> bool` — True when |Δx| or |Δy| or |Δw| or |Δh| ≥ PLACE_MIN_PX.
- `class Arming` — `__init__()`, `on_event(ev: GestureEvent) -> None`, `armed(t: float) -> bool`,
  `disarm() -> None`. Rules: a `pose` event with `data["pose"] == "open_palm"` arms
  (`until = ev.t + ARM_IDLE`); while armed every event extends `until = max(until, ev.t + ARM_IDLE)`;
  a `pose` "fist" disarms; `armed(t)` is `t < until`.

### 7.3 air/application.py additions

```python
class DesktopPort(Protocol):
    def run(self, name: str, args: dict | None = None, *, confirmed: bool = False) -> Any: ...  # ActionResult
    def grip(self, window: str | int = "active") -> WindowGrip: ...
    def place(self, grip: WindowGrip, rect: Rect) -> None: ...
    def is_maximized(self, window: str | int = "active") -> bool: ...
    def screen_bounds(self) -> Rect: ...

class AirController:
    def __init__(self, air: AirService, desktop: DesktopPort,
                 publish: Callable[[str, dict], None] | None = None,
                 clock: Callable[[], float] = time.monotonic) -> None
    def start(self) -> None        # air.subscribe(self._enqueue) once; start "air-control" thread
    def stop(self) -> None         # stop thread (join ≤ 1 s); drops any grip in place
    def process(self, ev: GestureEvent) -> None   # one event (thread calls it; tests call it directly)
    def tick(self, t: float) -> None              # continuous drag/resize step (thread calls ~CONTROL_HZ)
    def state(self) -> dict        # {"armed": bool, "dragging": bool, "resizing": bool, "last_action": str}
```
`AirService.set_camera(name: str) -> None` — only stores the name; it takes effect at the next
`start()` (raise `RuntimeError("stop Air first")` if running).

Thread: `queue.Queue(maxsize=64)`; `_enqueue` uses `put_nowait` and on `Full` drops the oldest
then puts (never blocks the camera thread). Loop: `get(timeout=1/CONTROL_HZ)` → `process(ev)`
if any; then `tick(clock())`. Every exception in process/tick is caught and logged; a failing
desktop call never kills the thread.

`process(ev)` (state lives on the controller: `_arming`, `_pending` = (side, hand0, t) or None,
`_drag` = (side, grip, hand0) or None, `_resize` = grip or None, `_last_place` Rect or None):
1. `_arming.on_event(ev)`. If ev is pose "fist": drop drag/resize (`_drag=_resize=_pending=None`),
   `_feedback("disarm", True, "Air control paused.")`; return.
2. If not `_arming.armed(ev.t)`: return (also clear `_pending`).
3. `pinch_start`: if no drag/resize → `_pending = (ev.hand, (ev.x, ev.y), ev.t)`.
4. `double_pinch`: clear `_pending` (and a drag of the same hand that has not moved);
   `if desktop.is_maximized(): run("window_restore") else run("window_maximize")`;
   feedback with the action name.
5. `pinch_end` for the drag hand: if `_drag` and not `ev.data.get("lost")` →
   `snap = edge_snap(ev.x)`; if snap → `run("window_snap", {"window": grip.id, "where": snap})`,
   feedback `window_snap`; else feedback `window_move` ("Moved <title>."). Clear `_drag`,
   `_last_place`. If lost → just clear (the window stays where it is). `pinch_end` for the
   pending hand → clear `_pending` (a tap, nothing happens).
6. `spread_start`: end any `_drag` without snapping; `_resize = _drag grip or desktop.grip()`;
   remember `_last_place = grip.start`. `spread_end`: clear `_resize`, feedback `window_resize`.
7. Swipes: only when no drag/resize and `ev.confidence >= ACTION_MIN_CONF` →
   `name, args = SWIPE_ACTIONS[g]`; `r = desktop.run(name, args)`; feedback (name, r.ok, r.message).
8. Everything else: ignored.

`tick(t)`: `snap = air.snapshot()`.
- If `_pending` and not `_drag`: find the `HandView` of that side; if it is pinching and moved
  ≥ DRAG_START from hand0 (Euclidean, normalized) → `grip = desktop.grip()` (ActionError →
  feedback failure, clear `_pending`), `_drag = (side, grip, hand0)`, clear `_pending`.
  If the hand is gone or not pinching → clear `_pending`.
- If `_drag`: hand of that side, pinching → `rect = drag_rect(grip.start, hand0, (h.x, h.y),
  grip.bounds)`; if `_last_place` is None or `moved(rect, _last_place)` → `desktop.place(grip,
  rect)`, `_last_place = rect`. A place() exception → feedback failure, clear `_drag`.
- If `_resize`: `scale = snap.spread_scale`; if not None → `rect = spread_rect(grip.start,
  scale)`; place when moved (same rule).
`_feedback(action, ok, message)`: log info, `publish(events.AIR_ACTION, {"action": action,
"ok": ok, "message": message})` (exceptions caught), set `last_action`.

### 7.4 settings, events, wiring

- `settings/domain.py` after `overlay_scale`:
  ```python
  #: Lura Air: control windows and workspaces with hand gestures seen by the webcam.
  air_enabled: bool = False
  #: Which camera Lura Air uses: part of its name or its number; empty = the built-in webcam.
  air_camera: str = ""
  ```
- `shared/events.py`: `#: Lura Air acted on the desktop: {"action", "ok", "message"}.` `AIR_ACTION = "air.action"`.
- `dashboard/api.py`: add `"air_enabled", "air_camera"` to the `LIVE` set (they apply without restart).
- `bootstrap.py`: `air = AirService(lambda name, payload: bus.publish(name, **payload),
  camera=settings.current.air_camera)`; `air_control = AirController(air, computer,
  lambda name, payload: bus.publish(name, **payload))`. `Container` gains `air: AirService` and
  `air_control: AirController` as its last two fields (after `computer`).
- `app.py cmd_run`: a local `def apply_air():` — stops both if running; if
  `c.settings.current.air_enabled`: `c.air.set_camera(c.settings.current.air_camera)`,
  `c.air.start()`, `c.air_control.start()`. Call it once before the assistant thread starts, and
  from the existing `on_settings` handler when `"air_enabled"` or `"air_camera"` is in
  `payload.get("changed", ())`. On shutdown (where `c.connectors.stop_all()` is called) stop both.
  Missing extra/model → `AirService` already reports state "error"; also log it once.
- `app.py`: `lura air control` (add `control` to the `air` action choices): build
  `ComputerService(default_backend())`, `AirService(None, camera=args.camera)`, `AirController`
  with a publish that prints `HH:MM:SS  action  ok  message`; print gestures too (reuse the watch
  printer); run until Ctrl+C / `--seconds`; stop both in `finally`. Refuse with
  `air_available()` when not available (like watch).

`AirController.start()` must tolerate `AirService` not running (it just receives no events).
`stop()` then `start()` again must work (fresh thread, same subscription — subscribe only once).

## 8. Tasks

1. computer: WindowGrip, grip/place/is_maximized/screen_bounds, exports.
2. air/domain: constants, drag_rect, edge_snap, spread_rect, moved, Arming.
3. air/application: set_camera, DesktopPort, AirController (process/tick/thread/feedback).
4. events, settings, dashboard LIVE, bootstrap, app.py (cmd_run wiring + `air control`).
5. tests/test_air_control.py + CHECKS.

## 9. Acceptance checks

`tests/test_air_control.py` (pattern of test_air.py; prints `air control OK`), using a
`FakeDesktop` (records `run`/`grip`/`place` calls, configurable `is_maximized`, bounds
`Rect(0,0,3840,1080)`, grip returns `WindowGrip(7, "Notes", Rect(100,100,800,600), bounds)`)
and a `FakeAir` (`subscribe`, `snapshot()` returning a settable `AirState`):
- pure: `drag_rect` (hand +0.1 x → +614 px), `edge_snap` (0.05/0.5/0.95), `spread_rect` (scale
  2.0 keeps centre; 0.1 clamps to 0.5 and MIN sizes), `moved`.
- Arming: nothing acts before open palm; open palm arms; idle 8 s disarms; events extend; fist disarms.
- Not armed: swipe → no `run`.
- Armed swipes → the four mapped `run` calls; low confidence (0.6) → none.
- Drag: pinch_start, tick with the hand moved 0.02 → no grip; moved 0.05 → one grip, one place;
  further ticks with tiny moves (< 4 px) → no extra place; pinch_end at x=0.5 → no snap, feedback
  window_move; a new drag released at x=0.95 → `run("window_snap", where="right")`.
- Lost pinch (`data.lost`) → no snap, drag cleared.
- Quick pinch without movement → no grip at all (a tap does nothing).
- Double pinch: maximized → `window_restore`; not maximized → `window_maximize`; no grip made.
- Spread: spread_start → grip; tick with spread_scale 1.5 → place with 1.5× size around centre; spread_end → feedback window_resize.
- Fist during a drag → drag cleared, disarmed, no more places.
- A `place` that raises → feedback ok False, drag cleared, controller keeps working.
- Queue: 100 events enqueued without the thread running → no exception, queue holds ≤ 64.
- `AirController.start(); stop(); start(); stop()` works; subscribe called once.
- `AIR_ACTION` payloads have exactly keys action, ok, message.
- computer: with the Stage B `FakeBackend` pattern (copy a minimal one into this test), `grip` on
  a maximized window un-maximizes and centres it; `place` sends exactly one `move_resize` and no
  `windows()` call; `grip` on a non-target window raises `ActionError`.
- Settings: `Settings()` has `air_enabled False`, `air_camera ""`.

Commands:
```bash
.venv/bin/python -m compileall -q src/
.venv/bin/python tests/test_air_control.py    # → air control OK
.venv/bin/python tests/run_all.py             # → All checks passed.
```
**Do not run** `lura air control|watch|bench`, `lura run`, `tests/live_*.py`, or anything that
opens the camera or moves windows. The reviewer does those.

## 10. Out of scope

Modes, context resolver, per-app gestures, confirmations by thumbs up (Stage E); overlay /
dashboard UI (Stage F); workspace presets (doc 005); `windows_arrange` by gesture (Stage E,
window mode); README/USAGE/AGENTS; packaging. No new packages, no commits, no edits under `docs/`.

## 11. Decisions

- 2026-11-20: grab the **active** window (the mission's "pinch to grab an active window"),
  not the one under the hand: mapping hand → screen point is not reliable enough yet.
- 2026-11-20: the window is grabbed only after the pinching hand moves `DRAG_START`, so a quick
  pinch or a double pinch never grabs (and never un-maximizes) anything.
- 2026-11-20: Air acts only while armed (open palm), so drinking coffee or typing does not
  move windows; a fist is the stop gesture.
- Review round 1 (Claude): the drag origin is the hand position **at grip time**, not at
  pinch start (otherwise the window jumped DRAG_START × 3840 × 1.6 ≈ 180 px). Done in
  application.py, keep it.
- Live test 1 with Mohamed (phone camera, gnome-calculator as target, 150 s): 12 drags, resize,
  maximize/restore, disarm by fist, swipes (workspace switch, overview) all acted correctly. Two
  defects, fixed in round 2:
  1. **Pinch flicker:** 87 pinches; 27 releases lasted < 0.1 s (1–3 frames above PINCH_OFF)
     before the pinch came back, which produced 23 unintended `double_pinch` (the window toggled
     maximize repeatedly). Fix in `air/domain.py` Recognizer: `RELEASE_HOLD = 0.10` — an active
     pinch ends only after the release condition (ratio > PINCH_OFF, or reach < PINCH_REACH_END)
     has held continuously for RELEASE_HOLD; the pinch_end event's time/position are those of
     the frame where it ends; a frame back under PINCH_OFF in between cancels the pending release.
     Hand loss (hand_leave) still ends the pinch at once (lost=True). Duration = end − start.
  2. **Edge snap unreachable:** release x ranged 0.16–0.66 (phone framing), never within
     EDGE_SNAP=0.08 of the frame edge. Fix in `air/domain.py` + controller: replace
     `edge_snap(x)` with `edge_snap(rect: Rect, bounds: Rect) -> str | None` — "left" when
     `rect.x <= bounds.x + SNAP_PX`, "right" when `rect.x + rect.width >= bounds.x + bounds.width
     - SNAP_PX`, else None; `SNAP_PX = 40`. The controller passes the last placed rect
     (`_last_place`, or `grip.start` if nothing was placed) — the window, not the hand, must be
     pushed against the screen edge (clamp_visible lets it go there). Remove `EDGE_SNAP`.
- Round 2 done by Neon (accepted deviation: spread distance frozen while a hand is releasing).
- Live test 2 (150 s): pinch flicker gone (27 pinches, 0 releases < 0.1 s, 2 double_pinch).
  Two new defects, fixed in round 3:
  3. **Return-stroke swipes:** 29 swipes alternating right/left (workspace 1↔2, 3↔4): the hand
     coming back after a swipe was read as the opposite swipe once the 0.7 s cooldown ended.
     Fix in the Recognizer (`air/domain.py`): after a swipe, (a) a swipe in the **opposite**
     direction is blocked for `SWIPE_REVERSE_BLOCK = 1.5` s; (b) no swipe at all until the palm
     has **settled**: its speed (from the raw palm history, normalized units/s) stays below
     `SETTLE_SPEED = 0.35` for `SETTLE_TIME = 0.2` s, or the hand left and re-entered. Keep the
     global SWIPE_COOLDOWN as well.
  4. **Accidental disarm:** 11 disarms; a hand closing from a pinch or turning passes through
     "fist" for > POSE_STABLE. Fix in `Arming` + controller: a fist pose only disarms if no other
     pose/pinch event for that hand arrives within `FIST_HOLD = 0.5` s after it; implement as
     `Arming.on_event` storing `pending_disarm = (hand, t + FIST_HOLD)`, cleared by any later
     `pose` (non-fist) or `pinch_start` of that hand; `Arming.armed(t)` applies it once
     `t >= deadline`. The controller drops a drag/resize only when the disarm actually applies
     (check in `tick`), and emits the "disarm" feedback then (once).
- Round 3 done by Neon. Live test 3 (150 s): window_snap works (calculator → right half);
  swipes ~2 s apart in clean runs (1→2→3→4→3→2→1), no return-stroke bounce; 2 disarms (was 11);
  drags and maximize fine. Not seen live: snap left. Note: after a workspace switch the active
  window was the user's Chrome, which the gestures then moved — acting on the active window is
  the design (Stage E's resolver/feedback should make the target visible).
