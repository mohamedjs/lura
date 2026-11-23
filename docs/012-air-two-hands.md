# 012 — Lura Air: two hands resize and move a window
Status: Implemented (2026-11-24, Gemini 4 Neon high, 1 round; reviewed by Claude)     Builds on: docs/009-air-interaction-engine.md (engine, controller, HUD), the v4 engine
header in `src/lura/modules/air/engine.py`, docs/ARCHITECTURE.md

## 1. Goal

Mohamed (2026-11-24): "I pinch thumb and index on my right hand and my left hand, and keep making
the window bigger and smaller, like a zoom in / zoom out with both hands, and hold it."

- **Two-hand pinch = take the window with both hands.** Pinch (thumb tip + index tip) with **both**
  hands → the window under the Air cursor is taken.
  - Hands apart → the window grows; hands together → it shrinks (around its own centre, like
    pinch-zoom on a phone photo).
  - Both hands moving together → the window moves with them.
  - Let go with either hand → done. Spread it to (almost) the whole screen → it is maximized.
- The one-hand mouse of v4 (point, finger-gun click, pinch click/drag, flick, V scroll, blade
  swipe, thumbs) must behave **exactly** as before when only one hand is up. A two-hand pinch must
  never leave a stray click or a stuck mouse button.

"Bigger / smaller" means the window's size, not Ctrl+/− content zoom (decided, §11).

## 2. Stack

Nothing new. MediaPipe already tracks 2 hands (`landmarker.py`, `num_hands=2`); `Hand.side` and
`Hand.score` exist.

## 3. Structure

```
src/lura/modules/air/engine.py        EDIT  two-hand phase "two": constants, pinch_point(), state, Commands
src/lura/modules/air/application.py   EDIT  AirController handles two_start / two_move / two_end; queue coalescing
src/lura/modules/overlay/state.py     EDIT  AirView gains `phase: str = ""` (the engine phase)
src/lura/modules/overlay/render.py    EDIT  _air_hud draws the "two" phase (label + scale)
src/lura/app.py                       EDIT  air_view() passes phase=st["phase"]
tests/test_air_engine.py              EDIT  add check_two_hands_engine, check_two_hands_controller (called from main)
```
No other files. No new files.

## 4. File responsibilities

- `engine.py` — stays pure (no I/O, no clock, deterministic replay). Adds the two-hand phase.
- `application.py` — `AirController` executes the new Commands on the `air-control` thread against
  `DesktopPort` (`grip`, `place`, `run`, `window_at`, `screen_bounds`, `active_app`). No new port
  methods.
- `state.py` — `AirView.phase: str = ""` after `grab_progress`, comment `# engine phase: idle point pinch drag scroll two`.
- `app.py` — in `air_view()`, add `phase=st["phase"]` to the `AirView(...)` call. Nothing else.
- `render.py` — HUD only; reads `AirView.phase` and `AirView.grab_progress`.

## 5. Layering rules

As docs/009 §4: engine pure, desktop I/O only on the controller thread, the bus gets only discrete
events (`AIR_ACTION` feedback as today). `overlay` imports nothing from `air`.

## 6. Conventions

Match the file you edit: module constants in CAPS with a trailing comment saying what they measure
and why; `_EPS` comparisons like the existing code; `log.info` for a finished user action,
`log.warning(..., exc_info=True)` for a failed desktop call; feedback strings in English, short,
ending with a full stop. Type hints everywhere, `from __future__ import annotations` stays.

## 7. Interfaces

### 7.1 New constants (`engine.py`, next to the others)

```python
TWO_PALM = 0.14           # both hands at least this big to start/keep a two-hand pinch (two hands
                          # held apart sit a little farther from the camera than one)
TWO_ENTER = 0.12          # s both hands must be pinching before the window is taken
TWO_DRAG_HOLD = 1.2       # s: while a second hand is up, a one-hand pinch waits this long before
                          # mouse-down (instead of DRAG_HOLD), so the second pinch can still arrive
TWO_DEADZONE = 0.04       # |scale - 1| below this = 1.0 (moving both hands must not resize)
TWO_SCALE_MIN, TWO_SCALE_MAX = 0.25, 4.0
TWO_COOLDOWN = 0.6        # s after a two-hand pinch ends in which no one-hand pinch/click starts
TWO_EURO_MIN_CUTOFF, TWO_EURO_BETA = 1.0, 0.02   # filter on the distance between the two pinches
```

### 7.2 New pure helper (`engine.py`)

```python
def pinch_point(hand: Hand) -> Point:
    """Midpoint of the thumb tip and the index tip (where the fingers meet)."""
```
`Point` is the existing namedtuple/dataclass from `domain.py`; build it the same way the code
already does (check `domain.Point`'s fields: x, y, z).

### 7.3 Commands (existing `Command` dataclass, no new fields)

| kind | fields | meaning |
|---|---|---|
| `two_start` | `t` | both hands pinched for TWO_ENTER: take the window |
| `two_move` | `t`, `dx`, `dy` (screen px, relative, since the last two_move), `progress` = absolute scale vs. the start (1.0 = same size, deadzone applied, clamped) | resize/move |
| `two_end` | `t` | either hand let go or was lost |

Update the comment on `Command.kind` to list them.

### 7.4 `state` property

`phase` may now be `"two"`; `grab_progress` holds the current scale while in "two" (so the HUD can
show it). All other keys unchanged.

## 8. Functions

### 8.1 Engine (`InteractionEngine`)

New state in `reset()`:
`self._two_since: float | None`, `self._two_d0: float` (filtered start distance),
`self._two_mid: tuple[float, float] | None` (last midpoint), `self._two_scale: float = 1.0`,
`self._two_release_since: float | None`, `self._two_block_until: float = -math.inf`,
`self._two_filter = OneEuro(min_cutoff=TWO_EURO_MIN_CUTOFF, beta=TWO_EURO_BETA)` (create once in
`__init__` like `_fx`, `reset()` it in `reset()`).

Pinch test per hand — factor the existing expressions out of `update()` into a private method,
used by both paths, so the one-hand behaviour is byte-for-byte the same:

```python
def _pinch_flags(self, hand: Hand) -> tuple[bool, bool, float]:
    """(pinch_on, pinch_released, ratio) with the exact rules already in update()."""
```

`update(t, hands)` — add at the very top, before the existing single-hand code:

1. `pair` = the two hands with the highest `score` whose `palm_size >= TWO_PALM - _EPS`
   (None if fewer than two). Distance/midpoint use `pinch_point()` of each, with x multiplied by
   `ASPECT` for the distance (isotropic, like `blade_ratio`).
2. **In phase "two"** (`self._phase == "two"`):
   - `pair is None` or either hand of the pair has `pinch_released` → start/keep
     `_two_release_since`; once it has held `RELEASE_HOLD` → emit `two_end`, `self._to_idle()`,
     `self._two_block_until = t + TWO_COOLDOWN`, `self._last_point_t = -math.inf` (no "recently
     pointing" carry-over), clear `_two_*`; return `out`.
   - else `_two_release_since = None`; `d = self._two_filter(raw_distance, t)`;
     `scale = d / max(self._two_d0, 1e-6)`; if `abs(scale - 1) < TWO_DEADZONE` → `scale = 1.0`;
     clamp to `[TWO_SCALE_MIN, TWO_SCALE_MAX]`; midpoint delta since `_two_mid` in screen px:
     `dx = (mx - px) * self._scale_x`, `dy = (my - py) * self._scale_y` (no acceleration);
     store; `self._two_scale = scale`; `self._progress = scale`; emit
     `Command("two_move", t, dx=dx, dy=dy, progress=scale)` when `|dx| >= 0.5 or |dy| >= 0.5`
     or the scale changed by ≥ 0.01 since the last emitted one; return `out`.
     (Thumbs, glide, swipe, flick and everything below are skipped while in "two".)
3. **Entering** — when `self._phase in ("idle", "point", "pinch", "drag")`, `pair` is not None,
   both hands are `_in_frame`, both `pinch_on`, and `t >= self._two_block_until`:
   - start/keep `_two_since`; when it has held `TWO_ENTER`:
     - if `self._phase == "drag"` → emit `Command("release", t)` first (a mouse-down was sent);
     - `self._stop_glide()`; `self._engage(t, out)`; `self._phase = "two"`;
       `self._two_filter.reset()`; `self._two_d0 = self._two_filter(raw_distance, t)`;
       `self._two_mid = midpoint`; `self._two_scale = 1.0`; `self._progress = 1.0`;
       emit `Command("two_start", t)`; return `out`.
     - A phase "pinch" that is converted emits **no** click (nothing is emitted for it).
   - while waiting (`_two_since` set, not yet TWO_ENTER), fall through to the single-hand code
     unchanged.
   - otherwise `_two_since = None`.
4. **Single-hand code changes (only these):**
   - In the `("pinch", "drag")` branch, the hold before `press`: `hold = THUMB_DRAG_HOLD if
     self._press_src == "thumb" else DRAG_HOLD`; when another tracked hand (any hand other than the
     one picked, `palm_size >= TWO_PALM`) is visible this frame, use `max(hold, TWO_DRAG_HOLD)`.
   - A one-hand pinch/gun-tap onset (the `if (pinch_on or gun_tap) and recently_pointing ...`
     branch) and the V right-click require `t >= self._two_block_until`.
   - In the pinch branch, while `_two_since` is not None (the second pinch is arriving), a release
     does **not** emit `click` yet: the release clock keeps running and the click is emitted only
     if, when RELEASE_HOLD has passed, `_two_since` is None again. (If both pinches complete,
     step 3 has already moved to "two" and no click is ever sent.)
5. `disengage` after `DISENGAGE_AFTER` without a tracked hand: if phase is "two", emit `two_end`
   before `disengage`.

### 8.2 Controller (`AirController`)

New state: `self._two: WindowGrip | None`, `self._two_dx = 0.0`, `self._two_dy = 0.0`,
`self._two_scale = 1.0`, `self._two_last: Rect | None`. Cleared in `_clear_active()` (no desktop
call needed — the window just stays where it is).

Module constants in `application.py`:
```python
_TWO_MIN_W, _TWO_MIN_H = 400, 300      # px: smallest window a two-hand pinch makes
_TWO_MAXIMIZE = 0.95                    # final rect ≥ this share of the monitor in both directions → maximize
```

`process(cmd)`:
- `two_start`: cancel any ring/grab state (`self._ring_labels = self._ring_target = None`;
  if `self._grab` is set treat as `grab_end` without snapping: just clear it). Target =
  `_window_at(cursor)`; if None → `self._desktop.active_app()`; if still None → feedback
  `("window_resize", False, "No window under the cursor.")` and return. Then
  `run("window_focus", {"window": wid})`; `grip = self._desktop.grip(wid)` (exceptions → feedback
  False with the message, return); store; feedback `("window_resize", True, f"Holding {title or app}.")`.
- `two_move`: ignore if `self._two is None`. `self._two_dx += cmd.dx; self._two_dy += cmd.dy;
  self._two_scale = cmd.progress or 1.0`. Compute:
  ```
  s = self._two.start; b = self._two.bounds
  w = max(_TWO_MIN_W, min(b.width,  round(s.width  * scale)))
  h = max(_TWO_MIN_H, min(b.height, round(s.height * scale)))
  cx, cy = s.center; cx += round(dx_total); cy += round(dy_total)
  rect = Rect(cx - w // 2, cy - h // 2, w, h)
  ```
  `place(grip, rect)` when `moved(rect, last or start)` (same helper as grab). A failing `place`
  → clear `_two`, feedback False with the message (like grab_move).
- `two_end`: ignore if None. `last = self._two_last or start`; clear state. Monitor = the monitor
  the rect's centre is on — use `self._desktop.screen_bounds("active")` after focusing (it is the
  monitor of the active window, which is this one). If `last.width >= _TWO_MAXIMIZE * mon.width and
  last.height >= _TWO_MAXIMIZE * mon.height` → `run("window_maximize", {"window": grip.id})`,
  feedback with its result. Else feedback `("window_resize", True, f"Resized {label} to
  {last.width}×{last.height}.")` and `log.info` the same.
- `_enqueue` coalescing: consecutive `two_move` commands are merged like `cursor`/`grab_move`
  (sum `dx`/`dy`, keep the **newest** `progress` and `t`). Read the existing coalescing code and
  extend it; do not restructure it.
- `state()`: unchanged keys; `phase` comes from the engine (now may be "two").

### 8.3 HUD (`render.py`, `_air_hud`)

When `air.phase == "two"`: draw the label `f"x{air.grab_progress:.1f}"` (e.g. `x1.4`) and two small
filled dots left and right of the radar (the two hands), in `pal.accent`, using the same font and
sizing conventions the ring labels use (`9 * u`). Nothing else changes.

## 9. Tasks

1. `engine.py`: constants, `pinch_point`, `_pinch_flags` refactor (no behaviour change — run the
   existing tests right after this step), two-hand phase, single-hand changes §8.1-4/5.
2. `application.py`: controller handling + coalescing.
3. `render.py`: HUD for "two".
4. Tests (§10).

## 10. Acceptance checks

Add to `tests/test_air_engine.py` (reuse its existing synthetic-hand helpers; read them first) and
call both from `main()`:

`check_two_hands_engine`:
- Two in-frame hands (palm 0.22, sides "Left"/"Right"), both pinching from the first frame, held
  0.3 s → exactly one `two_start`, **no** `click`, `press`, `right_click`.
- Then move the pinch points apart so the distance grows 1.5× over 0.5 s → `two_move` commands;
  the last one's `progress` within 1.35–1.65; moving them together to 0.7× → last `progress`
  0.6–0.8.
- Moving both hands right by 0.1 frame width with the same distance → summed `dx` ≈
  0.1 × 1920 × CURSOR_GAIN (±15 %), `progress` exactly 1.0 (deadzone).
- Opening one hand's pinch (ratio 0.6) for > RELEASE_HOLD → one `two_end`; then that hand
  re-pinching within TWO_COOLDOWN produces no `click`.
- Right hand pinches (one-hand pinch phase), the left joins 0.3 s later → `two_start`, no `click`,
  no `press`.
- One hand only, pointing then pinch-tap → `click` exactly as before (regression); one hand held
  pinched > DRAG_HOLD → `press` (no second hand visible).
- A one-hand pinch held 0.9 s while a second, open (not pinching) hand is visible → no `press`
  yet (TWO_DRAG_HOLD); held 1.3 s → `press`.
- A hand lost (only one hand) during "two" → `two_end` after RELEASE_HOLD.

`check_two_hands_controller` (FakeAir/FakeDesktop patterns already in the file; extend FakeDesktop
only inside the test file):
- `two_start` with a window under the cursor → `window_focus` + `grip` on it.
- `two_move(progress=1.5)` on a 800×600 window at (100,100) → `place` with 1200×900 centred on the
  same centre; `progress=0.2` → clamped to 400×300 minimum.
- `dx=50, dy=-20` → the centre moves by (50, −20).
- `two_end` with a final rect ≥ 95 % of a 1920×1080 monitor → `run("window_maximize", …)`.
- 30 queued `two_move` commands coalesce into one with summed dx/dy and the newest progress.
- No window under the cursor and no active app → feedback ok False.

```bash
.venv/bin/python -m compileall -q src/
.venv/bin/python tests/test_air_engine.py                         # → air engine OK (replays: 0 false triggers, as before)
cd src && ../.venv/bin/python -m lura.modules.overlay && cd ..   # → overlay self-check OK
.venv/bin/python tests/run_all.py                                 # → All checks passed.
```
The existing replay checks in `check_replay_recordings` must still pass with unchanged thresholds
(if the fixtures are missing they print "replay skipped"; that is fine).

## 11. Out of scope

Rotating windows, content zoom (Ctrl +/−), two-hand gestures other than the pinch, new settings,
dashboard text, the assistant, recordings. No live runs (camera / `lura run`), no packages, no
commits, no edits under `docs/`.

## 12. Decisions

- 2026-11-24 (Claude): "اكبر في الويندو واصغر في الويندو" = resize the window itself; both hands
  moving together also move it ("وامسكها"), so one gesture covers hold + move + resize.
- 2026-11-24: there are no two-hand recordings; the constants are first guesses to tune live with
  Mohamed (`lura air record` can capture one session).
- Implemented by Gemini 4 Neon in one round. Accepted deviations: `_two_mid` / the compared scale
  advance only when a `two_move` is emitted (no lost sub-pixel drift); disengage is handled inside
  the "two" phase too. The replay fixtures were not on this machine, so the zero-false-trigger
  replays were skipped; not verified live yet.
