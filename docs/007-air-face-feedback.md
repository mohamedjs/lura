# 007 — Lura Air Stage F1: feedback on the hologram
Status: Ready     Builds on: docs/001, docs/004-air-control.md, docs/006-air-modes.md, docs/ARCHITECTURE.md

## 1. Goal

The user must see what Air sees and is about to do, before it acts (lessons from Ultraleap's
touchless guidance: show tracking, show progress of a selection, confirm with a clear state
change, teach briefly then get out of the way). On the existing hologram — no redesign, the
current styles stay as they are — add a small **Air HUD** drawn by the renderer, plus short
**Air captions** under the face when no conversation is on:

- a radar disc at the bottom-left of the face: one dot per tracked hand (where it is in the
  frame); a pinching hand is a filled dot with a ring; the disc is bright when Air is armed,
  faint when not;
- a dwell ring around the radar that fills while the user points at a window (0 → 1 over
  POINT_DWELL) — the selection progress;
- a pulse ring after every Air action: glow colour if it worked, amber if it failed;
- a scan sweep across the radar for 0.8 s when a hand appears ("I see you");
- the mode as a small label under the radar: `AIR · WINDOW`;
- captions (idle only, 3 s): the action message ("Switched to workspace 2.", "Target: Chrome."),
  framing hints ("Raise your hand a little." when the hand sits at the frame edge), and a
  one-line welcome the first time Air is armed in a run.

## 2. Stack

Nothing new: cairo (already used by the renderer), the existing Qt/GTK backends draw captions.
Worktree venv `.venv` has pycairo; `cd src && ../.venv/bin/python -m lura.modules.overlay` runs
the overlay self-check without a display.

## 3. Structure

```
src/lura/modules/overlay/state.py          EDIT  AirView dataclass; OverlayState.air_source + air_view()
src/lura/modules/overlay/render.py         EDIT  render_surface(..., air=None); _air_hud()
src/lura/modules/overlay/captions.py       EDIT  caption_lines shows the Air message when idle
src/lura/modules/overlay/qt_backend.py     EDIT  pass air=shared.air_view() to render_surface
src/lura/modules/overlay/gtk_backend.py    EDIT  same
src/lura/modules/overlay/__init__.py       EDIT  export AirView
src/lura/modules/overlay/__main__.py       EDIT  self-check also renders every style with an AirView
src/lura/modules/air/domain.py             EDIT  framing_hint(), HINT/WELCOME constants
src/lura/modules/air/application.py        EDIT  state() adds target/dwell/hands_seen_t; hints + welcome feedback
src/lura/app.py                            EDIT  build the AirView source in cmd_run
tests/test_air_hud.py                      NEW   (in run_all)
tests/run_all.py                           EDIT  add "test_air_hud.py"
```

## 4. Rules

- `overlay` must **not** import `air` (presentation reads a plain `AirView` built by `app.py`,
  the composition point for presentation). `air` must not import `overlay`.
- The renderer stays allocation-light: the HUD is a handful of cairo calls (< 1 ms); nothing per
  frame touches the bus or does I/O. `air_view()` is called once per frame and must be cheap.
- Text drawn with cairo is ASCII only (the mode label). Messages go through `caption_lines`,
  drawn by Qt/Pango (Arabic-safe).
- Conventions as docs/002 §6. Keep the approved face/orb/bust/ring drawings untouched.

## 5. Interfaces

### 5.1 `overlay/state.py`

```python
@dataclass(frozen=True)
class AirView:
    on: bool = False                    # camera running
    armed: bool = False
    mode: str = "universal"
    hands: tuple[tuple[float, float, bool], ...] = ()   # (x, y, pinching), user's view 0..1
    dwell: float = 0.0                  # 0..1 pointing selection progress, 0 = none
    hand_age: float = 99.0              # s since a hand appeared (drives the scan sweep)
    pulse_ok: bool | None = None        # result of the last Air action, None = none yet
    pulse_age: float = 99.0             # s since that action
    message: str = ""                   # last Air message (action result / hint / welcome)
    message_age: float = 99.0
```
`OverlayState` gains: class attribute `air_source: Callable[[], AirView | None] | None = None`
(set by app.py), and `def air_view(self) -> AirView | None` — returns `air_source()` or None;
any exception → None (log once at debug).

### 5.2 `overlay/render.py`

`render_surface(t, blender, size=OVERLAY_SIZE, amp=0.0, look=None, tools=None, air: AirView | None = None)`:
after the tool badges, `if air is not None and air.on: _air_hud(cr, size, look.palette, air, t)`.

`_air_hud(cr, size, pal, air, t)` — `u = size / OVERLAY_SIZE`; centre `cx, cy = size*0.14,
size*0.80`; radius `R = size*0.085`. In this order:
1. Disc: fill `pal.wire` alpha `0.10 if not armed else 0.18`; ring stroke `pal.glow` width
   `1.2*u`, alpha `0.35` (unarmed) / `0.90` (armed).
2. Crosshair: two faint lines (alpha 0.15) through the centre.
3. Scan sweep when `air.hand_age < 0.8`: a line from the centre at angle `2π·hand_age/0.8`,
   `pal.glow` alpha `0.8*(1 - hand_age/0.8)`, width `1.5*u`.
4. Hands: each `(x, y, pinch)` → point `(cx + (x-0.5)*1.8*R, cy + (y-0.5)*1.8*R)` clamped to
   the disc; dot radius `2.6*u` fill `pal.accent` alpha 0.95; if pinch: filled `pal.glow` and a
   ring of radius `5*u`.
5. Dwell: if `air.dwell > 0`: arc from −π/2 over `2π·min(1, dwell)` at radius `R + 4*u`, width
   `2.4*u`, `pal.accent` alpha 0.95.
6. Pulse: if `air.pulse_ok is not None and air.pulse_age < 0.6`: ring radius
   `R*(1 + air.pulse_age/0.6*0.8)`, alpha `0.9*(1 - pulse_age/0.6)`, colour `pal.glow` if ok else
   `(1.0, 0.62, 0.20)`.
7. Mode label: `"AIR · " + mode.upper()` (ASCII: use "AIR - WINDOW" if the font lacks "·"? No —
   use the plain ASCII hyphen: `f"AIR - {mode.upper()}"`), "Sans" bold, size `max(8, 9*u)`,
   centred under the disc at `cy + R + 12*u`, white alpha 0.85 (armed) / 0.45.

### 5.3 `overlay/captions.py`

`caption_lines(state)`: unchanged when there are conversation captions to show. Otherwise, if
`state.subtitles_enabled` and `view = state.air_view()` and `view.message` and
`view.message_age < AIR_MESSAGE_SECONDS (3.0)` and the face is idle/listening (not speaking or
thinking) → return `("", _tail(view.message, 120))`.

### 5.4 Backends, exports, self-check

- Both `render_surface(...)` calls pass `air=shared.air_view()`.
- `overlay/__init__.py` exports `AirView`.
- `__main__.py` self-check: render each style once with `AirView(on=True, armed=True,
  mode="window", hands=((0.3, 0.4, False), (0.7, 0.6, True)), dwell=0.5, hand_age=0.3,
  pulse_ok=False, pulse_age=0.2)` and assert some non-transparent pixels inside the HUD box
  `(0, size*0.62, size*0.32, size)`; still prints `overlay self-check OK`.

### 5.5 `air/domain.py`

```python
HINT_EDGE = 0.10        # a hand within this of the left/right frame edge, or below 1 - this, is "at the edge"
HINT_AFTER = 1.5        # s at the edge before the hint
HINT_EVERY = 10.0       # s between two hints
WELCOME = "Air on: pinch to grab, swipe to switch, V for modes, fist to stop."

def framing_hint(views: tuple[HandView, ...]) -> str | None
    # any hand with y > 1 - HINT_EDGE → "Raise your hand a little."; x < HINT_EDGE or
    # x > 1 - HINT_EDGE → "Move your hand towards the middle."; else None. (Pure; timing is the controller's.)
```

### 5.6 `air/application.py`

- `state()` adds: `"target": str` (title of the window selected by pointing, else ""),
  `"dwell": float` (0..1: `(t - candidate_since)/POINT_DWELL` while a candidate exists, else 0),
  `"hand_since": float | None` (time the current hands appeared: the `t` of the latest
  `hand_enter` while no hand was present before; None when no hand).
- Hints: in `tick`, armed or not, `hint = framing_hint(snap.hands)`; when the same hint has held
  for `HINT_AFTER` and no hint was given in the last `HINT_EVERY` s → `_feedback("hint", True, hint)`.
- Welcome: the first time per controller lifetime that arming switches from off to on →
  `_feedback("welcome", True, WELCOME)`.

### 5.7 `app.py` (cmd_run) — the AirView source

Keep the last `AIR_ACTION` payload and its time from a bus subscription (`events.AIR_ACTION`),
then:
```python
def air_view():
    snap = c.air.snapshot()
    if snap.camera != "on":
        return None
    st = c.air_control.state()
    now = time.monotonic()
    hs = st.get("hand_since")
    return AirView(on=True, armed=st["armed"], mode=st["mode"],
                   hands=tuple((h.x, h.y, h.pinching) for h in snap.hands),
                   dwell=st.get("dwell", 0.0), hand_age=(now - hs) if hs is not None else 99.0,
                   pulse_ok=last["ok"], pulse_age=now - last["t"],
                   message=last["message"], message_age=now - last["t"])
overlay.state.air_source = air_view
```
(`last` initialised to `{"ok": None, "message": "", "t": -1e9}`; the subscriber stores
`ok`, `message`, `t=time.monotonic()`.) The controller's clock is `time.monotonic` (same base).

## 6. Tasks

1. overlay: AirView, air_source/air_view, `_air_hud`, render_surface param, captions, both
   backends, export, self-check.
2. air: framing_hint + constants; state() fields; hint + welcome feedback.
3. app.py source.
4. tests.

## 7. Acceptance checks

`tests/test_air_hud.py` (prints `air hud OK`; skip the render checks with a printed note if
`import cairo` fails):
- `render_surface` for each of face/orb/ring/bust with `air=None` equals (pixel-for-pixel) a
  render without the argument; with the AirView of §5.4 it has non-transparent pixels in the HUD
  box; `air.on=False` draws nothing extra.
- `caption_lines`: idle + Air message aged 1 s → `("", message)`; aged 4 s → None; conversation
  captions present → those win; speaking state → no Air message.
- `OverlayState.air_view()` with a source that raises → None.
- `framing_hint`: y 0.95 → raise; x 0.03 / 0.97 → middle; centre → None.
- Controller (FakeAir/FakeDesktop from test_air_control.py): a hand at y 0.95 for 1.6 s via
  ticks → one "hint" feedback; again 5 s later → none; after 10 s → one more. First arm → one
  "welcome"; disarm + re-arm → no second welcome. While pointing with a candidate 0.2 s →
  `state()["dwell"]` ≈ 0.5; after selection `state()["target"]` = the title.

```bash
.venv/bin/python -m compileall -q src/
cd src && ../.venv/bin/python -m lura.modules.overlay && cd ..   # → overlay self-check OK
.venv/bin/python tests/test_air_hud.py                            # → air hud OK
.venv/bin/python tests/run_all.py                                 # → All checks passed.
```
Do not run `lura run`, `lura air …`, or anything that opens windows or the camera. No packages,
no commits, no docs/ edits.

## 8. Out of scope

Assistant integration (thumbs = answers, point + voice context) — doc 008. Dashboard Air tab,
i18n of Air messages (English for now), sounds.

## 9. Decisions

- 2026-11-20: feedback lives on the existing hologram (a small radar HUD + captions), not in a
  separate window: Lura is the one interface; the face shows what Air sees and does.
- 2026-11-20: no camera image is shown anywhere (frames never leave the Air thread); the radar
  shows hand positions only.
