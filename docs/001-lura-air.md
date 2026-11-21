# 001 — Lura Air: gesture interaction (overview, Stage A report, roadmap)
Status: Ready (Stage A done)     Builds on: docs/ARCHITECTURE.md

Lura Air lets the user control the computer and talk to Lura with hand gestures seen by the
webcam. It has three pillars:

- **Air Control:** control the computer (windows, workspaces, pointer, system, clipboard).
- **Air Workspace:** move and arrange windows by hand, and use workspace presets.
- **Air Intelligence:** gestures combined with voice, the brain, skills and MCP.

This doc holds the Stage A report (what the machine and the code really offer) and the module
map and rules that every later stage builds on. Each stage gets its own numbered design doc
(`002` = Stage B, …).

## 1. Stage A — environment report (measured on Mohamed's machine, 2026-11-20)

| Item | Value |
|---|---|
| OS | Ubuntu 24.04.5 LTS, kernel 6.8, 12 CPUs, 23 GiB RAM |
| Session | **X11** (`XDG_SESSION_TYPE=x11`, `DISPLAY=:0`, no `WAYLAND_DISPLAY`) |
| Desktop | GNOME Shell 46.0 (Mutter), Ubuntu flavour |
| Extensions | dash-to-dock, tiling-assistant, ding (desktop icons), x11gestures, CoverflowAltTab, just-perfection, … |
| Monitors | 2: `eDP-1` 1920×1080+0+0 (primary), `HDMI-1-0` 1920×1080+1920+0 |
| Workspaces | dynamic (`org.gnome.mutter dynamic-workspaces true`), 5 at probe time |
| `_NET_WORKAREA` | `0,28,3840,1052` — **one rect spanning both screens** (28 px top bar), not per monitor |
| Cameras | `/dev/video0` "Integrated Camera" (real webcam), `/dev/video9` "Mobile Webcam" (**v4l2loopback**, not a camera) |
| Python | 3.12.3 (`.venv`, editable install); **no OpenCV, no MediaPipe** installed |

### Capability matrix

"Verified" = run for real on this machine. Window actions were run only on a throwaway
`xeyes` window spawned for the test; system settings were restored right after.

| Capability | Tool / API | Result |
|---|---|---|
| Move window | `xdotool windowmove` | **Works, but lands offset by the frame** (asked 400,300 → 428,380). Use `_NET_MOVERESIZE_WINDOW` instead. |
| Move + resize window | EWMH `_NET_MOVERESIZE_WINDOW` (ctypes ClientMessage) | **Verified.** Frame-exact; Mutter fits it into the work area (left half of eDP-1 → 960×1020). |
| Resize window | `xdotool windowsize` | Verified |
| Move to other monitor | `_NET_MOVERESIZE_WINDOW` with x ≥ 1920 | Verified |
| Maximize / restore | EWMH `_NET_WM_STATE` add/remove `MAXIMIZED_VERT+HORZ` | **Verified.** `xdotool` 3.20160805 has no `windowstate`; GNOME `maximize` keybinding is **empty**, `toggle-maximized` is `Alt+F10` (user-configurable) — keybindings are not reliable. |
| Minimize | `xdotool windowminimize` | Verified (`_NET_WM_STATE_HIDDEN`, Iconic) |
| Restore from minimized / focus | EWMH `_NET_ACTIVE_WINDOW` (source 2) | Verified |
| Close | EWMH `_NET_CLOSE_WINDOW` | Verified (window gone) |
| Window → workspace | `xdotool set_desktop_for_window` | Verified (1 → 0) |
| Switch workspace | `xdotool set_desktop N` | Verified and restored |
| Show desktop | EWMH `_NET_SHOWING_DESKTOP` 1/0 | Verified |
| Overview | `xdotool key super` (`org.gnome.mutter overlay-key = 'Super_L'`; `toggle-overview` keybinding is empty) | Verified (opened, Escape closed) |
| Tile left/right | GNOME `toggle-tiled-left` is **empty**; tiling-assistant is active | Use computed geometry (monitor rect ∩ work area, halves/quarters) via `_NET_MOVERESIZE_WINDOW`; unmaximize first. |
| Window list / info | `xprop -root _NET_CLIENT_LIST`, `xprop -id` (`WM_CLASS`, `_NET_WM_NAME`, `_NET_WM_DESKTOP`, `_NET_WM_STATE`, `_NET_WM_WINDOW_TYPE`, `_NET_WM_PID`, `_NET_FRAME_EXTENTS`), `xwininfo -id` | Present; `ding` shows as `gjs`/`_NET_WM_WINDOW_TYPE_DESKTOP` (must never be a target) |
| Pointer move/click/scroll/drag | `xdotool mousemove / click / mousedown / mouseup` (buttons 4/5/6/7 scroll) | Present (not exercised on the user's screen) |
| Keys to active window | `xdotool key` | Verified (`super`, `Escape`) |
| Volume | `wpctl set-volume -l 1.0 @DEFAULT_AUDIO_SINK@ 5%-` / `get-volume` | Verified and restored (0.61 → 0.56 → 0.61) |
| Mic mute | `wpctl set-mute @DEFAULT_AUDIO_SOURCE@ toggle` | Verified and restored. **Muting also deafens Lura's wake word.** |
| Brightness | D-Bus `org.gnome.SettingsDaemon.Power.Screen.Brightness` (Get/Set, 0–100) | Verified and restored (25 → 35 → 25). `brightnessctl` not installed. |
| Media | `playerctl` (MPRIS) | Present, one player (`chromium…`, Playing). **Not exercised** (would pause the user's media). |
| Clipboard text | `xclip -selection clipboard` | Verified and restored (text only; non-text targets are lost on restore). |
| Lock screen | `loginctl lock-session` | Present. **Not exercised** (needs Mohamed's OK). |
| Camera controls | `v4l2-ctl` | Present; Stage C. |
| `wmctrl`, `ydotool`, `hyprctl`, `swaymsg`, `brightnessctl`, `xsel`, `wl-copy` | — | **Not installed.** Not needed on this X11/GNOME setup. |
| AT-SPI | `pyatspi` | **Not importable.** Not used in Stage B; screen context comes from the window list + existing screenshot tools. |
| Wayland | — | Not running here. Every action reports "unsupported on Wayland" instead of failing (future: GNOME Shell extension). |

## 2. What Lura already has (from the code)

- **Event bus** `shared/events.py`: synchronous, in-process, handlers run on the publisher's
  thread. **Rule for Air:** publish only discrete events (a gesture recognised, a mode change),
  never per-frame data. Per-frame hand data stays inside the air module and reaches the face
  through a thread-safe state object, like `OverlayState.amplitude`.
- **Tools** `modules/tools`: `ToolService.dispatch` is the single path every brain and the
  realtime mode use. Other modules add tools with the `ToolProvider` protocol
  (`tool_specs / owns / call`), as memory and skills do; bootstrap wires them.
  `run_command` is guarded by `check_command`. Existing built-ins: `open_application`,
  `open_url`, `find_files`, `list_applications` — **not duplicated** by Air.
- **Orchestration** `modules/assistant`: wake loop + `PipelineConversation`; started by
  `TALK_REQUESTED`, interrupted by `INTERRUPT_REQUESTED`. Air does not add a second conversation
  system: context-aware gestures become events the assistant handles.
- **Presence / overlay** `shared/presence.py`, `modules/overlay/state.py`: `OverlayState` holds
  state, captions, amplitude, tool badges (`set_tool`), read every frame by the renderer.
  Air extends this (hand detected, mode, gesture pulse) — no redesign.
- **X11 via ctypes** `overlay/workspaces.py` already sends EWMH ClientMessages with ctypes
  (no python-xlib dependency). Air uses the same technique in its own module (modules may not
  import each other's internals).

## 3. Module map (decided)

```
presentation   app.py (CLI)  ·  dashboard  ·  overlay (face feedback)
                      │
application    assistant  ◄── events ──  air   (camera, hands, gestures, modes, resolver)   [Stage C–F]
                      │                    │
capabilities   tools ◄─ ToolProvider ── computer  (desktop actions, layouts, presets)        [Stage B, D]
                      │                    │
kernel         settings · shared (events, paths, platform, presence)
```

- **`modules/computer/`** (Stage B): every desktop action, one validated catalog, per-OS
  backends, capability probe, saved layouts. Knows nothing about gestures or the brain.
  Exposed to the brain as **one** tool (`desktop_action`) through `ToolProvider`.
  (Named `computer` because `shared/desktop.py` already exists for autostart.)
- **`modules/air/`** (Stage C–F): `camera` (lifecycle, by device name, never sends frames
  anywhere), `hands` (MediaPipe landmarks), `gestures` (pure classifiers: pinch, swipe,
  open palm, fist, thumbs up, point, two-hand spread; smoothing, debounce, cooldown),
  `modes` (the 9 Air modes), `resolver` (gesture + mode + context → an action name from
  `computer`, or an event for the assistant, or "ambiguous → ask"). Depends on `computer`
  and `settings` packages only; talks to `assistant` only through the bus.
- Gesture event shape (Stage E), published as `events.GESTURE`:
  `{"source": "gesture", "gesture": "pinch", "confidence": 0.98, "hand": "right",
  "position": {"x": 0.52, "y": 0.38}, "mode": "window_control"}`.

## 4. Rules for every stage

1. No arbitrary shell from gestures or from `desktop_action`: argv lists, `shell=False`,
   validated/clamped arguments, window ids only from our own window list.
2. Every action has a safety level: `safe` runs directly; `confirm` (close window, lock,
   delete a layout, …) needs an explicit confirmation (brain: `confirmed=true` after asking;
   gesture: thumbs-up while the request is pending, Stage E).
3. Never target Lura's own windows or desktop/dock windows (`_NET_WM_WINDOW_TYPE` DESKTOP,
   DOCK, …; `ding`).
4. Imports cleanly on macOS, Windows and Wayland; unsupported actions say so.
5. Camera frames never leave the process; camera work runs off the main and audio threads.
6. Pure logic in `domain.py` with tests in `tests/run_all.py`; live-desktop checks in a
   separate `tests/live_*.py` that is **not** in `CHECKS` (CI has no display).
7. New dependencies (OpenCV, MediaPipe) need Mohamed's approval, as an optional extra
   (`pip install lura[air]`), never a hard dependency.

## 5. Roadmap

| Stage | Doc | Scope | Needs |
|---|---|---|---|
| A | this doc | environment + architecture report | done |
| B | `002-air-desktop-actions.md` | `modules/computer`: catalog, X11 backend, system controls, layouts, `desktop_action` tool, `lura desktop` CLI | nothing new |
| C | `003` | camera + MediaPipe hands + gesture classifiers, latency/CPU numbers | **approval to install opencv + mediapipe** (`[air]` extra); check the Python 3.12 wheel, `HandLandmarker` Tasks API vs legacy `solutions.hands`, model file, size added to the .deb |
| D | `004` | Air Control + Air Workspace: pinch-grab-move, double pinch maximize, swipe workspaces, snap, arrange, presets (user-defined app lists + positions) | C |
| E | `005` | modes, mode indicator, context-aware resolver, confirmations by gesture | D |
| F | `006` | bus/assistant integration ("explain this" + point), face feedback (scan, pulse, mode text), dashboard Air tab | E |
| G | `007` | performance pass, docs (README, USAGE, AGENTS) | F |

## 6. Decisions
- 2026-11-20: X11 actions via EWMH ClientMessages (ctypes) + `xdotool`; GNOME keybindings
  are not used for window state (they are empty or user-configured on this machine).
- 2026-11-20: Stage B executor: Gemini 4 Neon (`gemini-4-neon-high`) via agy, at Mohamed's request.
