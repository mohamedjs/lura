# 013 — Lura Air + Voice: point at a window, "Lura, look at this"
Status: Implemented (2026-11-24, Gemini 4 Neon high, 1 round; reviewed by Claude)     Builds on: docs/008-air-intelligence.md (air note, thumbs), docs/009 (controller),
docs/012-air-two-hands.md (implemented first, same files in `air/application.py`), docs/ARCHITECTURE.md

## 1. Goal

Mohamed (2026-11-24): "I talk to Lura and point with my finger at the open window… I tell her
'look' and she looks with me and talks with me. The user should feel they control the machine with
voice and hands and everything together."

1. **Pointing reaches the conversation again.** Since gestures v4 the controller never publishes
   the `point` intent, so the doc-008 note ("the user is pointing at …") is dead. The controller now
   remembers the window under the Air cursor, and the assistant reads it directly (no bus event).
2. **She can see.** A new tool `look_at_screen` takes a screenshot of the window the user points at
   (or the active window / the whole monitor) and hands the image to the brain, which talks about
   it. Screenshots are taken **only when the brain calls the tool** (the user asked her to look or
   said "this/here"); never in the background.
3. **She looks along.** While the Air cursor moves, her face/bust turns towards it.
4. A dashboard switch turns looking off (`look_enabled`), and the Hands tab explains the two-hand
   pinch (doc 012) and "point + look".

## 2. Stack

No new package. Linux capture via ctypes `XGetImage` (libX11 is already loaded by
`computer/infrastructure/xlib.py`), numpy (core dependency), JPEG through `cv2` when it is
importable (the Air extra and the .deb have it), else a stdlib `zlib` PNG writer. macOS:
`/usr/sbin/screencapture` and `/usr/bin/sips` (both ship with macOS). Windows: the desktop backend
is unsupported there already; the tool says so.

## 3. Structure

```
src/lura/modules/brain/domain.py                      EDIT  Message.image/image_mime; ToolReply
src/lura/modules/brain/__init__.py                    EDIT  export ToolReply
src/lura/modules/brain/infrastructure/openai_compat.py EDIT  tool images → a user message with image_url parts
src/lura/modules/brain/infrastructure/anthropic_chat.py EDIT  tool images → image block inside tool_result
src/lura/modules/tools/application.py                 EDIT  ToolProvider.call may return ToolReply; dispatch_reply()
src/lura/modules/computer/domain.py                   EDIT  LOOK_TOOL spec, LOOK_* constants
src/lura/modules/computer/application.py              EDIT  Backend.capture; ComputerService.configure_look/look; tools
src/lura/modules/computer/infrastructure/capture.py   NEW   grab_x11, downscale, encode, png, grab_mac
src/lura/modules/computer/infrastructure/x11.py       EDIT  X11Backend.capture
src/lura/modules/computer/infrastructure/macos.py     EDIT  MacBackend.capture
src/lura/modules/computer/infrastructure/quartz.py    EDIT  screen_capture_allowed(request)
src/lura/modules/computer/infrastructure/unsupported.py EDIT  capture raises ActionError
src/lura/modules/air/application.py                   EDIT  AirController.pointed(); state() cursor + cursor_age
src/lura/modules/assistant/domain.py                  EDIT  air_note text, prompt paragraph, BLIND_NOTE, image in fit_history/message_tokens
src/lura/modules/assistant/application.py             EDIT  air_pointed injection; tool images; blind latch
src/lura/modules/settings/domain.py                   EDIT  look_enabled
src/lura/modules/dashboard/api.py                     EDIT  look_enabled editable + LIVE
src/lura/modules/dashboard/static/index.html          EDIT  switch + two guide lines in the Hands tab
src/lura/modules/dashboard/static/cards.js            EDIT  bindCheck("#look-enabled", "look_enabled")
src/lura/shared/i18n/en.json, ar.json                 EDIT  air.look, air.g.two, air.g.look
src/lura/modules/overlay/state.py                     EDIT  AirView.gaze; OverlayState face centres
src/lura/modules/overlay/render.py                    EDIT  _motion follows gaze; styles take gaze
src/lura/modules/overlay/gtk_backend.py               EDIT  publish face centres
src/lura/modules/overlay/qt_backend.py                EDIT  publish face centres
src/lura/app.py                                       EDIT  air_view() computes a smoothed gaze
src/lura/bootstrap.py                                 EDIT  wiring (§8.10)
tests/test_air_look.py                                NEW   (prints "air look OK")
tests/run_all.py                                      EDIT  add "test_air_look.py"
```
No other files.

## 4. File responsibilities

- `brain/domain.py` — data only. `Message` gains `image: bytes | None = None` and
  `image_mime: str = ""` (set only on `role == "tool"` messages in this doc). New:
  ```python
  @dataclass(frozen=True)
  class ToolReply:
      """A tool result with an optional picture for the brain."""
      text: str
      image: bytes | None = None
      image_mime: str = ""
  ```
- adapters — only the wire format; no capture, no policy.
- `tools/application.py` — runs tools; does not know what an image is beyond passing it on.
- `computer/infrastructure/capture.py` — pixels and encoding only; no window logic, no logging of
  image content. Imports: `ctypes`, `numpy`, `zlib`, `struct`, `tempfile`, `os`, `..domain.Rect`,
  `.xlib._load`, `.runner.run`. Never imports `cv2` at module level (inside `encode` only).
- `ComputerService` — decides **what** to capture (pointed / active / screen) and builds the
  ToolReply.
- `AirController` — remembers what the Air cursor is over; nothing about screenshots.
- `assistant` — still imports nothing from `air` or `computer`; it gets `air_pointed` as a callable.
- `overlay` — draws only; imports nothing from `air`.

## 5. Layering rules

`tests/test_architecture.py` must still pass: modules talk through their package `__init__`, only
`bootstrap.py` wires modules together. `computer` may import `ToolSpec`/`ToolReply` from `..brain`
(it already imports `ToolSpec` from there — check and follow the same import line).

## 6. Conventions

As doc 012 §6. English log lines; `log.info("look_at_screen %s %dx%d %d KB", what, w, h, kb)` —
never log image bytes or base64. Tool texts are plain English sentences for the brain.

## 7. Interfaces

### 7.1 Constants (`computer/domain.py`)

```python
LOOK_MAX_SIDE = 1568        # px: longest side sent to the brain (larger costs tokens, no gain)
LOOK_POINT_SECONDS = 20.0   # a pointed window older than this is not "pointed" any more
LOOK_JPEG_QUALITY = 85
LOOK_TOOL = ToolSpec(
    name="look_at_screen",
    description=(
        "See the user's screen: returns a screenshot. target 'pointed' (default) = the window the "
        "user is pointing at with their hand (Lura Air), or the window under the mouse when they are "
        "not pointing; 'active' = the focused window; 'screen' = the whole monitor under the mouse. "
        "Call it when the user asks you to look, or says 'this'/'here' about something on screen. "
        "Never read passwords, keys or other secrets aloud."),
    parameters={"type": "object", "properties": {"target": {
        "type": "string", "enum": ["pointed", "active", "screen"]}}, "required": []},
)
```
Use the real `ToolSpec` constructor shape of this repo (read `DESKTOP_TOOL` and copy its form).

### 7.2 `Backend.capture`

Add to the `Backend` Protocol in `computer/application.py`, under `# read`:
`def capture(self, rect: Rect) -> tuple[bytes, str]: ...` — the encoded image (≤ LOOK_MAX_SIDE)
and its MIME type; raises `ActionError` with a user-readable reason.

### 7.3 `AirController.pointed`

```python
def pointed(self) -> tuple[int, str, str, float] | None:
    """(window id, app, title, seconds since the Air cursor was last over it), or None."""
```

### 7.4 `ToolService.dispatch_reply`

```python
def dispatch_reply(self, name: str, args: dict | None,
                   session: ToolSession | None = None) -> tuple[ToolReply, bool]:
```
`dispatch()` keeps its signature and becomes a wrapper:
`reply, ends = self.dispatch_reply(...)`; returns `(reply.text + IMAGE_UNSUPPORTED if reply.image
else reply.text, ends)` where `IMAGE_UNSUPPORTED = " (The picture cannot be shown in this mode.)"`
(used by realtime mode). Every existing branch of `dispatch` moves into `dispatch_reply` and wraps
its text in `ToolReply(text)`; a provider's `call()` result is used as is when it is a `ToolReply`,
else wrapped. The `ToolProvider.call` Protocol return type becomes `str | ToolReply`.

## 8. Functions

### 8.1 `capture.py`

```python
def grab_x11(rect: Rect) -> np.ndarray      # (h, w, 3) uint8 RGB of the root window in rect
def downscale(rgb: np.ndarray, max_side: int) -> np.ndarray
def png(rgb: np.ndarray) -> bytes
def encode(rgb: np.ndarray, quality: int) -> tuple[bytes, str]
def grab_mac(rect: Rect, max_side: int, quality: int) -> tuple[bytes, str]
```

`grab_x11`:
- Structures (exact):
  ```python
  class _Funcs(ctypes.Structure):
      _fields_ = [(n, ctypes.c_void_p) for n in
                  ("create_image", "destroy_image", "get_pixel", "put_pixel", "sub_image", "add_pixel")]
  class _XImage(ctypes.Structure):
      _fields_ = [("width", ctypes.c_int), ("height", ctypes.c_int), ("xoffset", ctypes.c_int),
                  ("format", ctypes.c_int), ("data", ctypes.c_void_p), ("byte_order", ctypes.c_int),
                  ("bitmap_unit", ctypes.c_int), ("bitmap_bit_order", ctypes.c_int),
                  ("bitmap_pad", ctypes.c_int), ("depth", ctypes.c_int),
                  ("bytes_per_line", ctypes.c_int), ("bits_per_pixel", ctypes.c_int),
                  ("red_mask", ctypes.c_ulong), ("green_mask", ctypes.c_ulong),
                  ("blue_mask", ctypes.c_ulong), ("obdata", ctypes.c_void_p), ("f", _Funcs)]
  ```
- `lib = xlib._load()`; set (once, module-level flag) argtypes/restype for `XGetImage`
  (`c_void_p, c_ulong, c_int, c_int, c_uint, c_uint, c_ulong, c_int` → `POINTER(_XImage)`),
  `XDefaultScreen`, `XDisplayWidth`, `XDisplayHeight`, `XSetErrorHandler`.
- Install an X error handler **once** (keep the `CFUNCTYPE(c_int, c_void_p, c_void_p)` object in a
  module global so it is not garbage-collected); it records that an error happened and returns 0.
  Without it a BadMatch from XGetImage terminates the whole daemon.
- Open the display with `lib.XOpenDisplay(None)` (close in `finally`), root =
  `XDefaultRootWindow`. Clip `rect` to `(0, 0, XDisplayWidth, XDisplayHeight)`; empty → raise
  `ActionError("The window is off screen.")` (import `ActionError` from `..domain`; if it lives
  elsewhere, import it from where `x11.py` gets it).
- `img = XGetImage(dpy, root, x, y, w, h, 0xFFFFFFFF_FFFFFFFF & ctypes.c_ulong(-1).value, 2)` (ZPixmap
  = 2). NULL or the error flag set → `ActionError("Could not read the screen.")`.
- Require `bits_per_pixel == 32`, else `ActionError("Unsupported screen format.")`.
- `buf = ctypes.string_at(img.contents.data, bpl * h)`; `arr = np.frombuffer(buf, np.uint8)
  .reshape(h, bpl)[:, : w * 4].reshape(h, w, 4)`; RGB = `arr[..., 2::-1].copy()` (BGRX → RGB,
  assuming LSBFirst `byte_order == 0`; if `byte_order == 1` use `arr[..., 1:4]`).
- Free with `ctypes.CFUNCTYPE(ctypes.c_int, ctypes.c_void_p)(img.contents.f.destroy_image)(img)`.

`downscale`: `k = ceil(max(h, w) / max_side)`; `k <= 1` → unchanged; else crop to multiples of `k`
and box-average: `rgb[:h2, :w2].reshape(h2//k, k, w2//k, k, 3).mean(axis=(1, 3)).astype(np.uint8)`.

`png`: 8-bit RGB, filter byte 0 per row, `zlib.compress(raw, 6)`, chunks IHDR/IDAT/IEND with
`zlib.crc32`; standard signature `b"\x89PNG\r\n\x1a\n"`.

`encode`: try `import cv2` → `ok, buf = cv2.imencode(".jpg", rgb[..., ::-1], [cv2.IMWRITE_JPEG_QUALITY,
quality])` → `(buf.tobytes(), "image/jpeg")`; on ImportError or `not ok` → `(png(rgb), "image/png")`.

`grab_mac`: `tempfile.mkstemp(suffix=".jpg")`; `run(["/usr/sbin/screencapture", "-x", "-t", "jpg",
f"-R{x},{y},{w},{h}", path])` (use `runner.run` exactly as `macos.py` uses it, timeout 10 s); if
`max(w, h) > max_side`: `run(["/usr/bin/sips", "-Z", str(max_side), path])`; read the bytes; delete
the file in `finally`; empty file → `ActionError("Could not read the screen.")`.

### 8.2 Backends

- `X11Backend.capture(rect)` → `rgb = capture.grab_x11(rect)`; `return capture.encode(
  capture.downscale(rgb, LOOK_MAX_SIDE), LOOK_JPEG_QUALITY)`.
- `quartz.screen_capture_allowed(request: bool) -> bool` — `CGPreflightScreenCaptureAccess()`
  from CoreGraphics (restype `c_bool`); when False and `request`, call
  `CGRequestScreenCaptureAccess()` once (it shows the system prompt). Missing symbols → True (old
  macOS has no such gate). Load through the same `_lib()` pattern `quartz.py` uses.
- `MacBackend.capture(rect)` → if `not quartz.screen_capture_allowed(True)`: raise
  `ActionError("Lura needs Screen Recording permission: System Settings → Privacy & Security → "
  "Screen Recording, then restart Lura.")`; else `capture.grab_mac(rect, LOOK_MAX_SIDE,
  LOOK_JPEG_QUALITY)`.
- `UnsupportedBackend.capture` → `raise ActionError(f"Looking at the screen is not available: {reason}")`
  using the backend's existing reason text.

### 8.3 `ComputerService`

- `__init__`: `self._pointed: Callable[[], tuple[int, str, str, float] | None] = lambda: None`,
  `self._look_enabled: Callable[[], bool] = lambda: True`.
- `configure_look(self, pointed=None, enabled=None) -> None` — replace whichever is given.
- `tool_specs()` → `[DESKTOP_TOOL] + ([LOOK_TOOL] if self._look_enabled() else [])`.
- `owns(name)` → also `LOOK_TOOL.name` (only when enabled).
- `call(name, arguments)` → `LOOK_TOOL.name` → `self.look(str((arguments or {}).get("target") or
  "pointed"))`; anything else as today.
- `look(self, target: str = "pointed") -> ToolReply` (under `self._lock` for the window lookups,
  **not** while capturing):
  1. `target` not in ("pointed", "active", "screen") → "pointed".
  2. "screen": monitor = `monitor_for(Rect(px, py, 1, 1), monitors)` with the real pointer
     (`self._backend.pointer()`); rect = that monitor's rect; label "the screen".
  3. "pointed": `p = self._pointed()`; if `p` and `p[3] <= LOOK_POINT_SECONDS` → window `p[0]`;
     else the window under the real pointer (`self.window_at(*self._backend.pointer())`); else
     fall through to "active".
  4. "active": `self._backend.active_window()` (must pass `is_target` like `active_app()`).
  5. Window chosen: `w = self._backend.window(wid)`; None or `w.minimized` → `ToolReply("Could not
     look: that window is not on screen.")`. rect = `w.rect` clipped to `bounds(monitors)`.
     label `f'the window "{w.title[:80]}" ({w.app})'`.
  6. Capture: `data, mime = self._backend.capture(rect)`; `ActionError`/any exception →
     `ToolReply(f"Could not look: {exc}")` (log warning).
  7. `return ToolReply(f"Here is a screenshot of {label} ({rect.width}×{rect.height} px). "
     f"Use what you see to answer; do not describe it unless asked.", data, mime)`.

### 8.4 Adapters

- `openai_compat._payload`: replace `*map(self._wire, history)` with a loop. After appending the
  wire form of a `tool` message, if the next history item is not a `tool` message and any tool
  message in this consecutive run has an `image`, append one
  `{"role": "user", "content": [{"type": "text", "text": "(The picture from the tool result above.)"},
  *[{"type": "image_url", "image_url": {"url": f"data:{m.image_mime};base64,{b64}"}} for each]]}`.
  (Chat completions cannot carry an image in a `tool` message; the user message must come after
  the whole run so every `tool_call_id` still directly follows its assistant turn.)
- `anthropic_chat._messages`: for a tool message with an image, the block's `"content"` becomes
  `[{"type": "text", "text": message.text or "(picture)"}, {"type": "image", "source":
  {"type": "base64", "media_type": message.image_mime, "data": b64}}]`; without an image unchanged.
  The existing cache-breakpoint code (`last["content"][-1]`) must still work (it targets the last
  tool_result block — leave it).

### 8.5 `AirController` (doc 012 must already be in the file)

- Constants: `_POINT_EVERY = 0.25  # s between window lookups while the Air cursor moves`.
- State: `self._pointed: tuple[int, str, str, float] | None = None` (wid, app, title, wall time from
  `self._clock()`), `self._pointed_lock = threading.Lock()`, `self._point_check_t = -math.inf`
  (command time), `self._cursor_wall = -math.inf`.
- `cursor` branch, after `pointer_to`: `self._cursor_wall = self._clock()`; if `cmd.t -
  self._point_check_t >= _POINT_EVERY`: `self._point_check_t = cmd.t`; `hit = self._window_at(...)`;
  if hit: `with self._pointed_lock: self._pointed = (*hit, self._clock())`.
- `grab_start` and `two_start`: after choosing the target, set `_pointed` the same way.
- `pointed()`: under `_pointed_lock` only (never `self._lock` — it is held during desktop I/O):
  `None` if unset, else `(wid, app, title, self._clock() - t)`.
- `state()`: add `"cursor": (round(cx), round(cy)) if self._cursor else None` and
  `"cursor_age": self._clock() - self._cursor_wall`.

### 8.6 `assistant/domain.py`

- `air_note(app, title)`: same prefix and target text; the ending becomes
  `'. "This"/"here" means that window; call look_at_screen to see it.]'`.
- The Air paragraph in `system_prompt` (byte-stable, ≤ 80 words, keep `f"{name} Air"`): gestures
  exist; the note explains "this/here"; **when the user asks you to look, or talks about what is on
  their screen, call look_at_screen — it shows you the window they point at; then answer from what
  you see**; a turn that is only "Yes."/"No." may be a thumbs up/down; both hands pinching resize
  windows (so "I made it bigger" makes sense).
- `BLIND_NOTE = " (The picture could not be sent: this model does not take images. Tell the user to
  choose a model that can see images.)"`
- `LOOK_IMAGE_TOKENS = 1500`; `message_tokens` adds it when `message.image`.
- `fit_history`: only the newest message with an `image` keeps it; every older one becomes
  `dataclasses.replace(m, image=None, image_mime="", text=m.text + " (The picture is no longer
  attached.)")`. Do this in the same loop that builds `view`; keep the audio logic as is.

### 8.7 `assistant/application.py`

- `Assistant.__init__(..., air_pointed=lambda: None)`; store; `self._air_used = -math.inf`.
- `_take_air_point()`: first `p = self._air_pointed()` (try/except → None). If `p` and
  `p[3] < domain.AIR_NOTE_SECONDS`: `stamp = time.monotonic() - p[3]`; if `abs(stamp -
  self._air_used) > 0.05` → `self._air_used = stamp`; return `(p[1], p[2])`. Otherwise fall back to
  the existing bus-based `_air_point` logic unchanged.
- `PipelineConversation`: `self._blind = False`.
  - `_run_tools`: use `self._tools.dispatch_reply(...)`. If `reply.image and not self._blind` →
    `Message("tool", reply.text[:6000], ..., image=reply.image, image_mime=reply.image_mime)`; if
    blind → text `reply.text[:6000] + domain.BLIND_NOTE`, no image.
  - `_stream`: in `except HttpError as exc`, **before** the existing handling: if `exc.status in
    (400, 404, 415, 422)`, `not self._blind`, and any message in `self._history` has an image →
    `self._blind = True`; for each such message set `m.image = None; m.image_mime = ""; m.text +=
    domain.BLIND_NOTE`; `player.cancel()`; `log.warning("Brain refused the picture (%s); "
    "continuing without images.", exc)`; `return self._stream(with_tools)` (one retry; the latch
    stops a loop).

### 8.8 Settings & dashboard

- `settings/domain.py`, after `air_thumb_click`: `#: Lura may take a screenshot of the window you
  point at when you ask her to look (look_at_screen); it goes to your brain provider.` `look_enabled:
  bool = True`.
- `dashboard/api.py`: add `"look_enabled"` to `LIVE` and to the editable list next to
  `"air_thumb_click"`.
- `index.html`: after the `air.g.thumbs` `<li>` add `<li data-i18n="air.g.two"></li>` and
  `<li data-i18n="air.g.look"></li>`; after the `air-thumb-click` switch add
  `<label class="switch"><input type="checkbox" id="look-enabled"><span></span><b data-i18n="air.look"></b></label>`.
- `cards.js` in `renderAir()`: `bindCheck("#look-enabled", "look_enabled");`.
- i18n (exact):
  - en `air.look`: "Lura can look at the window you point at (a screenshot goes to your AI provider only when she looks)"
  - ar `air.look`: "لورا تقدر تبص على الشباك اللي بتشاور عليه (صورة الشاشة بتروح لمزوّد الذكاء الاصطناعي بس لما تبص)"
  - en `air.g.two`: "Pinch with both hands: you hold the window. Hands apart = bigger, together = smaller, move both = move it. Let go with one hand to drop it."
  - ar `air.g.two`: "اقفل الإبهام والسبابة بإيديك الاتنين: إنت ماسك الشباك. بعّد إيديك يكبر، قرّبهم يصغر، حرّكهم سوا يتحرك. سيب بإيد واحدة ويفضل مكانه."
  - en `air.g.look`: "Point at a window and say “Lura, look at this”: she sees it and talks with you about it."
  - ar `air.g.look`: "شاور على شباك وقول «لورا، بصي على ده»: هتشوفه وتتكلم معاك عنه."

### 8.9 Gaze (overlay + app.py)

- `AirView.gaze: tuple[float, float] | None = None` — direction from her face to the Air cursor,
  each axis −1..1 (x right, y down).
- `OverlayState`: `set_face_centers(centers: list[tuple[int, int]])` and `face_centers() ->
  list[tuple[int, int]]` (thread-safe, same lock style as the other setters).
- `gtk_backend._frames.tick` and the Qt backend's frame tick: every 15th frame publish the global
  screen centre of the drawn face of every overlay window (window position + the face centre inside
  it — read how each backend positions the face, including any offset for captions).
- `render.py`: `_motion(t, b, amp, gaze=None)`; with gaze `(gx, gy)`:
  `w = min(1.0, math.hypot(gx, gy) * 3.0)`; `yaw = (1 - w) * idle_yaw + w * (GAZE_SIGN_X * GAZE_YAW *
  gx + 0.06 * math.sin(t * 0.45))`; `pitch = (1 - w) * idle_pitch + w * (-0.06 + GAZE_SIGN_Y *
  GAZE_PITCH * gy)`; constants `GAZE_YAW = 0.6`, `GAZE_PITCH = 0.2`, and `GAZE_SIGN_X`/`GAZE_SIGN_Y`
  (±1.0) chosen so that the nose tip moves **towards** the cursor on screen — determine them with
  the check in §10 (project the nose vertex with `_rot` + the projector) and set them as literals.
  The four style functions get a keyword `gaze=None`; face and bust pass it to `_motion`; orb and
  ring ignore it. `render_surface` passes `gaze=air.gaze if air is not None and air.on else None`.
  If the bust picks frames from `yaw`, make sure it clamps to its rendered range.
- `app.py air_view()`: keep `gaze_state = [None]` outside. Target: if `st["engaged"]`,
  `st.get("cursor")` and `st.get("cursor_age", 99) < 3.0` and `overlay.state.face_centers()`:
  nearest centre `(fx, fy)` → `((cx - fx) / 800, (cy - fy) / 600)` each clamped to −1..1; else None.
  Smoothing per call: with a target, `g = target if g is None else g + 0.2 * (target - g)` per axis;
  without, `g *= 0.85` per axis and None once both |axes| < 0.02. Pass `gaze=g`.

### 8.10 `bootstrap.py`

After `air_control` is created:
```python
computer.configure_look(pointed=air_control.pointed, enabled=lambda: settings.current.look_enabled)
```
and `Assistant(..., air_pointed=air_control.pointed)`.

## 9. Tasks

1. brain: Message fields, ToolReply, export; adapters.
2. tools: dispatch_reply + wrapper.
3. computer: constants/spec, capture.py, backends, ComputerService look + tool.
4. air: pointed tracking + state keys.
5. assistant: domain + application.
6. settings, dashboard, i18n.
7. overlay gaze + app.py + bootstrap.
8. tests.

## 10. Acceptance checks

`tests/test_air_look.py` (add to `tests/run_all.py`; reuse fakes/patterns from
`tests/test_air_assistant.py`, `tests/test_air_engine.py`, `tests/test_air_modes.py` — read them
first; prints `air look OK`):
- `png()` of a 3×2 image starts with the PNG signature and its IHDR says 3×2; if cv2 imports,
  `cv2.imdecode` gives back the same pixels. `downscale` of 3000×2000 → long side ≤ 1568.
- `encode()` returns `image/jpeg` when cv2 is present, else `image/png`.
- `ComputerService.look` with a fake backend (implement only what `look` and the methods it calls
  need; `capture` records the rect and returns `(b"img", "image/png")`):
  pointed fresh (age 2) → captures that window's rect; age 30 → the window under the pointer; no
  pointer window → the active window; "screen" → the monitor under the pointer; minimized →
  "Could not look"; capture raising `ActionError("x")` → text starts with "Could not look", no image.
- `tool_specs()` contains `look_at_screen` only while `enabled()` is true.
- `ToolService.dispatch_reply` passes a provider's ToolReply through; `dispatch()` returns its text
  plus IMAGE_UNSUPPORTED.
- OpenAI payload: [user, assistant(tool_call A), tool A with image] → the message after the tool is a
  user message with one `image_url` data URL of the right MIME; two tool results with images in one
  run → one user message after both, with two images; no image → no extra message.
- Anthropic `_messages` (construct the adapter with `cls.__new__(cls)` and set only the attributes
  `_messages` reads, so the SDK is not needed): a tool result with an image has a content list with
  an `image` block.
- `fit_history`: two tool messages with images → only the newest keeps its image; the older text ends
  with "(The picture is no longer attached.)"; `message_tokens` counts LOOK_IMAGE_TOKENS.
- Blind latch: a fake brain that raises `HttpError("no images", 400)` while any history message has
  an image and otherwise answers → the conversation answers, the retried request has no image, the
  tool message text ends with BLIND_NOTE, and a second look in the same conversation is sent as text.
- `AirController.pointed()`: cursor commands over a FakeDesktop window → `pointed()[0]` is that
  window and age < 1; 10 cursor commands within 0.1 s → `window_at` called once.
- Assistant with `air_pointed` returning a fresh point → the next user turn starts with the air note
  (containing "look_at_screen"); the same point is not used twice; age 16 → no note.
- Gaze: with `gaze=(1, 0)` the projected nose tip is to the right of `gaze=(-1, 0)`'s; with `(0, 1)`
  it is lower than with `(0, -1)`; `render_surface(..., air=AirView(on=True, gaze=(0.5, 0.2)))`
  runs for every style.
- Settings default `look_enabled is True`.

```bash
.venv/bin/python -m compileall -q src/
.venv/bin/python tests/test_air_look.py                           # → air look OK
.venv/bin/python tests/test_air_assistant.py                      # → air assistant OK
cd src && ../.venv/bin/python -m lura.modules.overlay && cd ..   # → overlay self-check OK
.venv/bin/python tests/run_all.py                                 # → All checks passed.
```

## 11. Out of scope

Screenshots attached automatically without the tool, OCR, video, Wayland/Windows capture, the
dashboard's macOS permission row for Screen Recording (the tool's error text says what to do),
realtime-mode images. No live runs, no packages, no commits, no edits under `docs/`.

## 12. Decisions

- 2026-11-24 (Claude): looking is a tool the brain calls, not an image on every pointed turn —
  explicit (privacy) and cheap (no image tokens on turns that do not need them).
- 2026-11-24: the pointed window is read straight from the controller through an injected callable
  (no bus event), because the window id is needed for the screenshot and nothing else consumes it.
- 2026-11-24: a model that refuses images is detected by its 400/404/415/422 on a request with an
  image; the conversation continues without pictures and Lura says the model cannot see.
- Implemented by Gemini 4 Neon in one round. It also updated `tests/test_air_assistant.py` (the new
  air-note text) and `tests/test_computer.py` (two tool specs) — accepted. Claude changed
  `capture.downscale` to an exact cv2 INTER_AREA resize when cv2 is present: the integer box factor
  halved a 1920 px screen to 960 px. Checked live on X11: active window and full screen capture
  (1568×882, ~80 KB JPEG). Not verified: macOS capture, a real brain receiving the image.
