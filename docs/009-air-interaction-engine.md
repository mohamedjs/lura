# 009 — Lura Air v2: the interaction engine (redesign)
Status: Implemented (2026-11-20, Gemini 4 Neon xhigh, 2 rounds; reviewed by Claude)     Replaces the gesture → action layer of docs/004 and docs/006.
Builds on: docs/001, docs/003 (camera, landmarks), docs/002/005 (computer), docs/007 (HUD), docs/008 (intents).

## 1. Why a redesign

Live use (2026-11-20) was not usable: modes changed by accident, 30+ unintended workspace
switches in 4 minutes, the camera-preview window kept being the target, and the user could not
tell what a gesture would act on. Two guided recordings of Mohamed's hand (landmarks only; now
`tests/fixtures/air/*.jsonl.gz`) and the HCI literature explain why:

- **Midas touch.** Fast swipes and single poses fire from ordinary movement (session 1: 23 false
  pinches and 16 "engagements" in 2 min of normal work). Commands need a deliberate delimiter.
- **Invisible target.** "The active window" is not visible. Vogel & Balakrishnan (UIST 2005):
  relative pointing with clutching beats absolute ray pointing for freehand input → the hand
  drives the **real mouse cursor**, and the target is the window **under the cursor**. Multiple
  monitors then work like a mouse (the cursor crosses screens).
- **Marking menus** (Kurtenbach & Buxton; mid-air pinch marking menus: Lim & Kristensson 2019):
  pinch → a 4-way menu appears → lean → release. Self-revealing for novices, fast for experts,
  and hard to trigger by accident. A thumb–middle pinch was rejected by data: it reached the
  threshold in 2 of 175 frames.
- **Interaction zone** (Ultraleap): deliberate gestures are made close to the camera — palm size
  0.24–0.41 (session 2) vs. 0.11–0.14 when typing/sitting. A size gate + an explicit engage
  removed every false trigger in 2 min of normal activity (0 engagements, 1 pinch).
- **Fatigue** (Consumed Endurance, CHI 2014): short, relative movements; the cursor freezes in
  any non-open pose so the hand can rest/reposition (clutch).

Measured on the phone camera (session 2): pinch tap 8/8 (ratio 0.03–0.10, index reach
0.89–1.04); pinch+lean 7/8 pinched, displacement 0.14–0.75; pinch+hold 2/3; thumbs up/down and V
recognised; open-palm cursor jitter at rest: 1–3 px frame-to-frame (p95 ≤ 8 px at gain 1.5).

## 2. The interaction (user-facing — keep the face/captions consistent with this)

| | How | Timing / end | Face shows |
|---|---|---|---|
| Zone | the hand must be close (palm ≥ ZONE_PALM) and fully inside the frame | outside = ignored | radar dim; "Move your hand closer." after 1.5 s |
| Engage | open palm, in the zone, still | ENGAGE_HOLD 0.8 s | radar lights, welcome once |
| Disengage | no hand in the zone | DISENGAGE_AFTER 3 s | radar dims |
| Cursor | open palm moves the **real mouse cursor** (relative) | immediate; any other pose freezes it (clutch) | — |
| Command ring | pinch (thumb + index): a 4-way ring opens at once | lean ≥ LEAN 0.10 in a direction, release = run it; release in the centre = cancel; RING_TIMEOUT 3 s | the ring with 4 labels, the leaned one lit |
| Grab | pinch and keep it still in the centre | GRAB_HOLD 0.6 s → the window under the cursor follows the hand; release = drop; pushed to a screen edge = snap | a progress arc while holding, then the window title |
| Scroll | V sign, move up/down | V held SCROLL_ENTER 0.3 s; one step per SCROLL_STEP 0.03 of travel | — |
| Yes / No | thumbs up / down (zone not required) | held THUMB_HOLD 0.5 s; only meaningful when Lura asked (doc 008) | 👍 / 👎 caption |

Ring directions are the same everywhere: ← previous, → next, ↑ maximize/restore, ↓ **click**
(a left click at the cursor — Mohamed's choice 2026-11-20: a click must be deliberate, pinch +
lean + release, never a bare pinch). "Previous/next" depends on the window under the cursor
(§6.2). Minimizing is by voice. Modes are set by voice only (`air_mode` tool, unchanged) and
change only what the ring means.

**Zone hysteresis.** The zone (palm ≥ ZONE_PALM and fully inside the frame) is required only to
**start** something: engage, and a pinch onset. Once pointing, a ring or a grab is running, any
detected hand with palm ≥ TRACK_PALM (0.12) keeps it going even if it is clipped at the frame
edge: in pointing a clipped hand freezes the cursor and does **not** start the disengage clock;
in a ring or grab, losing the hand completely (no hand ≥ TRACK_PALM) for more than RELEASE_HOLD
gives `ring_cancel` / `grab_end` — **a direction is never selected on a lost hand**. Measured with
this rule on session 2's leans: 6/8 correct, 1 wrong (the user's own first RIGHT), 1 cancelled
when the hand left the frame — none selected by a lost hand.

## 3. Structure

```
src/lura/modules/air/engine.py              NEW   pure InteractionEngine + Command + constants (no I/O)
src/lura/modules/air/application.py         EDIT  AirService runs the engine per frame and hands Commands to
                                                   subscribers; AirController executes Commands (replaces the
                                                   swipe/drag/pointing/mode-cycling logic); state() for the HUD
src/lura/modules/air/domain.py              EDIT  ring_action()/ring_labels() replace resolve and the swipe tables
                                                   (the Recognizer itself stays unchanged — see the list below)
src/lura/modules/air/__init__.py            EDIT  export InteractionEngine, Command, ring_action, ring_labels
src/lura/modules/computer/application.py    EDIT  pointer() -> (x, y) public method (backend passthrough), for DesktopPort
src/lura/modules/overlay/state.py           EDIT  AirView: engaged, zone, ring, ring_hover, grab_progress
src/lura/modules/overlay/render.py          EDIT  _air_hud draws the ring + grab arc + zone state
src/lura/app.py                             EDIT  AirView source from the new state(); `lura air record`
tests/test_air_engine.py                    NEW   pure engine checks + replay of the two recordings (in run_all)
tests/fixtures/air/session1-free.jsonl.gz   (exists) uncued session, 2 min normal activity at the end
tests/fixtures/air/session2-cued.jsonl.gz   (exists) cued session (see §8)
tests/run_all.py                            EDIT  add "test_air_engine.py"
.gitignore                                  EDIT  add tests/fixtures/air/ (local recordings)
tests/test_air_control.py, tests/test_air_modes.py, tests/test_air_hud.py   EDIT  exactly as listed below
```
Also: `.gitignore` gets `tests/fixtures/air/` (the recordings stay local — Mohamed's decision;
never commit them).

**Exactly what is removed / kept (do not delete anything else):**
- `air/domain.py`: **remove** `Arming`, `resolve`, `Resolution`, `_GLOBAL_POSES`, `_MODE_TABLE`,
  `_UNIVERSAL_SWIPES`, `SWIPE_ACTIONS`, `drag_rect`, `spread_rect`, `hand_to_screen`, `DRAG_START`,
  `DRAG_GAIN`, `ACTION_MIN_CONF`, `FIST_HOLD`, `ARM_IDLE`, `SPREAD_MIN/MAX`, `MIN_W/MIN_H`,
  `POINTER_*`, `POINT_DWELL` and their exports. **Keep** the `Recognizer` and everything it uses
  **unchanged** (it still emits swipes etc. for `lura air watch` and logs), `MODES`, `KINDS`,
  `APP_KINDS`, `app_kind`, `edge_snap`, `SNAP_PX`, `moved`, `PLACE_MIN_PX`, `framing_hint` & HINT
  constants, `WELCOME`.
- `tests/test_air.py`: unchanged.
- `tests/test_air_control.py`: **delete** `check_arming`, `check_swipes_and_arming_gate`,
  `check_drag_snap_lost_and_tap`, `check_double_pinch`, `check_spread`, `check_fist_during_drag`;
  **rewrite** `check_pure_geometry` (only `edge_snap` and `moved`), `check_place_error_recovery`
  (a failing `place` during a grab → feedback False, grab cleared, controller keeps working),
  `check_queue_and_lifecycle` (Command queue, coalescing, start/stop/start); **keep**
  `check_computer_fast_path`, `check_settings_defaults`.
- `tests/test_air_modes.py`: **delete** `check_resolve_table`, `check_hand_to_screen`,
  `check_controller_modes_and_intents`, `check_pointer_mode`, `check_point_to_focus`; **keep**
  `check_app_kind`, `check_tool_provider`, `check_computer_additions`, `check_settings_and_events`.
- `tests/test_air_hud.py`: **keep** `check_render_hud`, `check_captions_and_air_view`,
  `check_framing_hint`; **rewrite** `check_controller_hints_welcome_and_pointing` as
  `check_controller_engage_hints` (engage → welcome once; hint commands → feedback).
- The ring table gets its own check in `tests/test_air_engine.py` (`check_ring_table`: every cell).

## 4. Rules

- `engine.py` is pure: input frames (landmarks + time), output Commands. No cv2, no desktop, no
  clock. It must replay a recording deterministically.
- The engine runs on the camera thread (cheap, < 0.2 ms/frame); Commands go to the controller's
  queue; **desktop I/O happens only on the `air-control` thread**. The bus still gets only
  discrete events (`AIR_ACTION`, `AIR_INTENT`, `AIR_STATUS`, `AIR_MODE`).
- A false pinch must never click UI: the ring and grab act on windows/workspaces/tabs only;
  there is no pinch-click in this design (pointer mode is removed from Air; the cursor itself is
  the pointer).
- Conventions as docs/002 §6. Keep `Recognizer` (snapshot views, HUD dots), but the controller no
  longer consumes its swipe/pose events for actions.

## 5. `engine.py`

```python
# All values measured on session 2 (phone camera) unless stated.
ZONE_PALM = 0.20          # palm size (wrist→middle MCP, normalized) needed to count; typing hands are 0.11–0.14
EDGE = 0.02               # every landmark must be at least this far inside the frame
THUMB_PALM = 0.12         # thumbs up/down are held lower/farther (p10 0.13)
TRACK_PALM = 0.12         # once something is running, a hand this big keeps it going (no edge test)
ENGAGE_HOLD = 0.8         # s of still open palm in the zone to engage
STILL_STEP = 0.01         # max palm-centre move per frame that still counts as "still"
DISENGAGE_AFTER = 3.0     # s without an in-zone hand
PINCH_ON, PINCH_OFF = 0.28, 0.40
PINCH_REACH, PINCH_REACH_END = 0.80, 0.70
RELEASE_HOLD = 0.10       # s the release condition must hold
REWIND = 0.10             # s of cursor movement undone at pinch start (the pinch moves the hand)
LEAN = 0.10               # normalized palm displacement that selects a ring direction
LEAN_AXIS = 1.5           # dominant axis must be ≥ this × the other
HOLD_RADIUS = 0.04        # stays "in the centre" while displacement < this
GRAB_HOLD = 0.6           # s in the centre to turn the ring into a grab
RING_TIMEOUT = 3.0        # s without a direction or grab → cancel
SCROLL_ENTER = 0.3        # s of V before scrolling
SCROLL_STEP = 0.03        # normalized vertical travel per scroll step
THUMB_HOLD = 0.5
HINT_AFTER, HINT_EVERY = 1.5, 10.0
CURSOR_GAIN = 1.5         # a full-frame-width hand move = 1920 × CURSOR_GAIN screen px (one monitor and a half)
EURO_MIN_CUTOFF, EURO_BETA = 0.5, 0.007

@dataclass(frozen=True)
class Command:
    kind: str     # "engage" "disengage" "cursor" "ring_open" "ring_hover" "ring_select" "ring_cancel"
                  # "grab_start" "grab_move" "grab_end" "scroll" "intent" "hint"
    t: float
    dx: float = 0.0; dy: float = 0.0     # cursor / grab_move: screen px (relative)
    direction: str = ""                  # ring_hover / ring_select: "left" "right" "up" "down" ("" = centre)
    steps: int = 0                       # scroll: + = up, − = down
    text: str = ""                       # intent: "confirm"/"deny"; hint: the sentence
    progress: float = 0.0                # ring_hover while holding in the centre: 0..1 towards GRAB_HOLD

class InteractionEngine:
    def __init__(self, screen_px_per_frame: float = 2880.0) -> None
        # screen px for a full-frame-width hand move (= 1920 × CURSOR_GAIN by default); y uses the
        # same scale × 9/16 (frames are 16:9) so movement is isotropic.
    def update(self, t: float, hands: Sequence[Hand]) -> list[Command]
    @property
    def state(self) -> dict   # {"engaged", "zone": "ok"|"far"|"none", "phase": "idle"|"pointing"|
                              #  "frozen"|"ring"|"grab"|"scroll", "ring_hover", "grab_progress"}
    def reset(self) -> None
```
Per frame (`update`): pick the hand with the highest score (one-hand interaction); compute
features with the existing pure helpers in `domain.py` (palm size, palm centre, pinch ratio,
index reach, `classify_pose` with the finger-hysteresis state kept in the engine).

Phases (exactly one at a time):
1. **idle** (not engaged): an in-zone, open-palm hand whose centre moved < STILL_STEP per frame
   for ENGAGE_HOLD → `engage`, phase pointing. Hand present but palm < ZONE_PALM (and ≥ 0.08)
   for HINT_AFTER, at most every HINT_EVERY → `hint "Move your hand closer to the camera."`.
2. **Disengage** from any phase: no in-zone hand for DISENGAGE_AFTER → close what is open
   (ring → `ring_cancel`, grab → `grab_end`), then `disengage`, phase idle.
3. **pointing**: open palm → 1€-filtered palm centre (EURO params) → `cursor` with the delta
   (fractional pixels accumulated; emit when |accum| ≥ 1 px). Keep a 0.25 s history of emitted
   cursor deltas for REWIND. Another pose → phase frozen (no cursor). V held SCROLL_ENTER →
   scroll. Pinch onset (PINCH_ON, reach > PINCH_REACH, in zone) → emit `cursor` with minus the
   deltas emitted in the last REWIND s, then `ring_open`, phase ring (anchor = palm centre now).
4. **frozen**: open palm again → pointing (re-seed the filter: no jump); pinch → as in 3.
5. **ring**: disp = palm − anchor. Direction = the dominant axis if its |value| ≥ LEAN and
   ≥ LEAN_AXIS × the other (x+ = right, y+ = down). Emit `ring_hover` when the direction changes,
   and while |disp| < HOLD_RADIUS emit `ring_hover` (direction "") with `progress` =
   held/GRAB_HOLD every frame the value changes by ≥ 0.05. Held in the centre ≥ GRAB_HOLD →
   `grab_start`, phase grab. Release (debounced RELEASE_HOLD) → `ring_select(direction)` if a
   direction is lit, else `ring_cancel`; → frozen. RING_TIMEOUT without a direction or grab →
   `ring_cancel`, and ignore this pinch until it is released.
6. **grab**: every frame `grab_move` with the filtered palm delta in screen px (same scale as the
   cursor). Release → `grab_end` → frozen.
7. **scroll**: V pose; vertical palm travel accumulates; each SCROLL_STEP → `scroll` ±1 (hand up
   = +1 = scroll up). Pose not V for 0.2 s → frozen.
8. **Thumbs** (any phase except ring/grab, zone not required, palm ≥ THUMB_PALM): a thumbs_up /
   thumbs_down pose held THUMB_HOLD → `intent confirm|deny`, once per hold.

## 6. Controller (`application.py`)

### 6.1 AirService
- New `subscribe_commands(fn: Callable[[Command], None])`. In `_run`, after the recognizer:
  `cmds = self._engine.update(t, hands)` (engine created in `start()`; constructor kwarg
  `engine_factory=InteractionEngine`); each Command is passed to command subscribers
  (try/except each). `snapshot()` unchanged; add `engine_state()` returning a copy of
  `engine.state` (under the snapshot lock).
- The old `AIR_GESTURE` events stay (other subscribers, logs), unchanged.

### 6.2 AirController (rewrite of the action path)
Drop: Arming, swipe handling, V mode cycling, point-to-select, D1 pinch-move drag, spread,
pointer mode. Keep: thread + queue, `_feedback`, `AIR_MODE`, `set_mode`/`mode` (voice tool),
ToolProvider (`air_mode`), `AIR_INTENT` publishing.
- `start()` subscribes `_enqueue` to `subscribe_commands`. Queue: consecutive `cursor` /
  `grab_move` Commands are **coalesced** (summed) when the thread is behind.
- Cursor: on `engage`, read the real pointer once (`desktop.pointer()` → add to DesktopPort);
  keep `(cx, cy)` locally; `cursor` → `cx += dx; cy += dy`, clamp to `screen_bounds()` (all
  monitors — the cursor crosses screens like a mouse), `desktop.pointer_to(round(cx), round(cy))`.
  Re-read the real pointer if the last Air move is older than 1 s (the user used the mouse).
- `ring_open`: target = `desktop.window_at(cx, cy)` (may be None); remember it; feedback
  "ring" with the four labels for the target's kind (`ring_labels`, §6.3).
- `ring_select(direction)`: run `ring_action(direction, mode, kind)` (below). Window
  actions act on the target window (focus it first with `window_focus`); key actions focus the
  target then press keys; workspace actions need no target. No target and a window action →
  feedback ok False "No window under the cursor.".
- `grab_start`: target = window under the cursor; none → feedback False; else `window_focus`,
  `grip(target id)`; `grab_move` → accumulate, `place(grip, start.translate(ax, ay))` when moved
  ≥ PLACE_MIN_PX; `grab_end` → `edge_snap(last rect, bounds)` → `window_snap` or "Moved …".
- `scroll(steps)` → `desktop.run("pointer_scroll", {"direction": "up"|"down", "amount": |steps|})`.
- `intent` → publish `AIR_INTENT {"intent": text, "mode", "gesture": "thumbs", "app", "title"}`
  (app/title of the window under the cursor) — doc 008 consumes it.
- `engage` / `disengage` / `hint` → feedback ("Air on." once with WELCOME on the first engage;
  "Air off."; the hint text).
- `state()` → `{"mode", "engaged", "zone", "phase", "ring_hover", "grab_progress", "ring_labels"
  (4-tuple or None), "target"}` from `air.engine_state()` + the controller's ring labels/target.

### 6.3 `ring_action` and `ring_labels` (`domain.py`)
`ring_action(direction, mode, kind) -> tuple[str, dict]` — kind from `app_kind()` of the window
under the cursor. Labels (`ring_labels(mode, kind) -> tuple[str, str, str, str]` for
left/right/up/down, short ASCII for the HUD):

| mode / kind | ← | → | ↑ | ↓ |
|---|---|---|---|---|
| universal + browser | K(ctrl+shift+Tab) "TAB<" | K(ctrl+Tab) "TAB>" | toggle_maximize "MAX" | click "CLICK" |
| universal + media | media previous "PREV" | media next "NEXT" | toggle_maximize | click |
| universal + presentation | K(Left) "SLIDE<" | K(Right) "SLIDE>" | toggle_maximize | click |
| universal + files | K(alt+Left) "BACK" | K(alt+Right) "FWD" | toggle_maximize | click |
| universal + editor/terminal | K(ctrl+Page_Up) "TAB<" | K(ctrl+Page_Down) "TAB>" | toggle_maximize | click |
| universal + other / no window | workspace prev "DESK<" | workspace next "DESK>" | toggle_maximize | click |

`click` = a controller pseudo-action: `desktop.run("pointer_click", {"button": "left"})` at the
current cursor (the cursor was rewound at pinch start, so it clicks where the user aimed).
`toggle_maximize` with no window under the cursor → feedback False "No window under the cursor."
| workspace | workspace prev | workspace next | workspace_overview "OVERVIEW" | show_desktop "DESKTOP" |
| window | window_snap left "SNAP<" | window_snap right "SNAP>" | toggle_maximize | window_minimize |
| media | media previous | media next | volume_change +10 "VOL+" | volume_change −10 "VOL−" |
| presentation | K(Left) | K(Right) | ignore | ignore |
| ai | intent "talk" (any direction) |

Modes `pointer`, `files`, `developer` from Stage E map to universal (keep the names accepted by
`air_mode` so voice commands do not fail).

## 7. HUD (`overlay`)
`AirView` gains `engaged: bool`, `zone: str`, `ring: tuple[str, str, str, str] | None`,
`ring_hover: str`, `grab_progress: float`. `_air_hud`: radar bright only when engaged; zone
"far" → dashed ring; when `ring` is set, draw four labels around the radar at 0°/90°/180°/270°
(cairo, ASCII, size 9·u), the hovered one in `pal.accent` with a filled wedge, and the grab
progress as the existing dwell arc. `app.py` builds these from `c.air_control.state()`.

## 8. Tests (`tests/test_air_engine.py`, prints `air engine OK`)

Fixture format: gzip JSON lines `{"t", "label", "cue"?, "hands": [{"side", "score", "pts": 21×[x,y,z]}]}`;
a helper turns `pts` into `Hand` and feeds `InteractionEngine.update(t, hands)` for every line.
**Replay acceptance (the core of this stage):**
- session2, label `normal` (2 min of typing/mouse/drinking): **zero** `engage`, `ring_select`,
  `grab_start`, `scroll`, `intent` commands that start inside that label (feed the whole file;
  count by the label of the frame where the command is emitted).
- session1, label `normal` (hand visible ~31% of frames — the stronger false-trigger test):
  **zero** `engage`, and ≤ 1 `ring_select`/`grab_start` in total.
- The fixtures are local only (git-ignored). When a fixture file is missing, the replay checks
  print `replay skipped: tests/fixtures/air/<name> not found (local recordings, not in git)` and
  the pure checks still run; the script still ends with `air engine OK`.
- session2 `near_still`: an `engage` within the label.
- session2 `pinch_tap`: ≥ 7 `ring_open` and no `ring_select` (taps cancel).
- session2 `pinch_lean`: for each cue window, the `ring_select` direction; ≥ 6 of 8 equal the
  cue's direction (cue texts contain LEFT/RIGHT/UP/DOWN) and none is the opposite direction
  except the known user mistake in the first RIGHT cue (tolerate one mismatch).
- session2 `pinch_hold`: ≥ 2 `grab_start`, each followed by `grab_end`.
- session2 `thumbs`: ≥ 1 `intent confirm` in the THUMBS UP window and ≥ 1 `intent deny` in DOWN.
- session2 `v_scroll`: ≥ 3 `scroll` commands.
- session2 `near_move`: cursor deltas sum to a path ≥ 300 px; while `near_still` after engage,
  the cursor moves ≤ 40 px in total over 5 s (stability).
If a replay threshold fails, report the measured numbers in `notes` (do not loosen the
acceptance or change constants on your own — the reviewer tunes constants).
**Pure checks:** synthetic hands for each phase transition; disengage closes an open ring/grab;
REWIND undoes the last 0.1 s of cursor; ring direction axis rule; RING_TIMEOUT; thumbs need
THUMB_HOLD and fire once; hint cadence. Controller (FakeAir with `subscribe_commands`, FakeDesktop
with `pointer`, `pointer_to`, `window_at`, `grip`, `place`, `run`): cursor accumulation and
clamping across two monitors; ring_select per table row (browser → ctrl+Tab on the target after
`window_focus`; no window → workspace next); grab moves the window under the cursor and snaps at
the edge; scroll; intent payload; coalescing of 50 queued cursor commands into one pointer move.

```bash
.venv/bin/python -m compileall -q src/
cd src && ../.venv/bin/python -m lura.modules.overlay && cd ..   # → overlay self-check OK
.venv/bin/python tests/test_air_engine.py                         # → air engine OK
.venv/bin/python tests/run_all.py                                 # → All checks passed.
```
No live runs (camera, `lura run`, `lura air …` other than `--help`), no packages, no commits,
no docs/ edits, no new fixtures.

## 9. Tasks
1. `engine.py` (+ exports). 2. ring_action/ring_labels in domain. 3. AirService command path.
4. AirController rewrite of the action path. 5. computer `pointer()` passthrough + DesktopPort.
6. HUD + AirView + app.py source. 7. `lura air record [--seconds N] OUT.jsonl` — the recorder
(landmarks only, same format, prints that no images are saved). 8. Tests: new engine tests,
remove/rewrite tests of removed behaviour, keep everything else green.

## 10. Out of scope
Head-pose monitor selection (needs a second model + approval), Wayland, dashboard UI, sounds,
spoken feedback, any change to the assistant (doc 008 stays as is).

## 11. Decisions
- 2026-11-20: relative cursor + target under the cursor + one pinch (ring or grab) + a size
  zone + explicit engage, from the two recordings and the literature in §1 — replaces swipes,
  V-cycling, spread and point-to-select, which produced the unusable live session.
- 2026-11-20: the recordings are landmarks only (no images), stored as test fixtures with
  Mohamed's knowledge; every threshold is checked by replaying them.
- 2026-11-20, answers to Neon's round-1 questions (decided by Claude from the recordings):
  1. **Tight pinch.** Held pinches are made with all fingers curled (session 2 `pinch_hold`:
     thumb–index ratio p10/p50/p90 0.08/0.11/0.19, index reach 0.63–0.69), while fists in normal
     activity keep the thumb away from the index tip (ratio 0.16/0.33/0.58). New rule in
     `engine.py`: onset when `ratio < PINCH_ON (0.28) and reach > PINCH_REACH (0.80)` **or**
     `ratio < PINCH_TIGHT (0.15)` (any reach). Release (debounced RELEASE_HOLD) when
     `ratio > PINCH_OFF (0.40)` **or** (`reach < PINCH_REACH_END (0.70) and ratio > PINCH_SLIP (0.24)`)
     — the thumb slid off into a fist. Index reach alone never ends a pinch.
  2. `pinch_tap` acceptance becomes ≥ 5 `ring_open` and 0 `ring_select` (the user held several
     taps 1.5–3.5 s across cues; only 5 separate pinches exist in the fixture; held ones may grab).
  3. Disengage keeps the §2 TRACK_PALM hysteresis. The session-2 `normal` check ignores commands
     emitted before the first `disengage` inside the `normal` label (the hand coming down from
     `v_scroll`) and requires **zero** of every command kind after it. Session 1 `normal`: zero
     `engage` from the start of the label (unchanged).
- Round 2 result (replayed by Claude, all pass): session 1 normal 0 engage, 1 ring_select (a
  release carried over at the label start); session 2 normal 0 commands after the first
  disengage; still-hand cursor drift 9 px / 5 s; leans 6/8 correct (1 user mistake, 1 cancelled on
  a lost hand); pinch_hold 3 grabs; thumbs ✓; V scroll 11 steps. Claude added `THUMB_REPEAT = 3.0`:
  the same thumbs answer within 3 s counts once (one held 👍 gave 3 confirms on replay; now 1).
- Not verified live yet.
