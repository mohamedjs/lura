# 014 — Lura Air: live camera preview, the Mac's own camera, smoother pointer

Status: Implemented     Builds on: docs/003-air-hand-tracking.md, docs/009-air-interaction-engine.md,
docs/012-air-two-hands.md, docs/ARCHITECTURE.md

## 1. Goal

Mohamed (Linux, macOS, Windows) wants to *see* what Lura Air sees, pick the right camera, and
have the mouse glide instead of stepping.

1. **Live preview in the dashboard's Hands tab:** while Air is on and the Hands tab is open,
   a picture shows the camera image (mirrored, as in a mirror) with the hand drawn on it:
   21 yellow joints joined by bones, plus a status chip ("POINT", "CLICK", "DOUBLE CLICK",
   "HOLD", "TWO HANDS" …) so every gesture is visible as it happens. Frames are only
   encoded while the tab asks for them (no cost otherwise) and never leave 127.0.0.1.
2. **The Mac's built-in camera by default:** today macOS always opens AVFoundation index 0,
   which is often a virtual camera (Camo Studio, OBS) or an iPhone (Continuity Camera). List the
   real devices by name, prefer the built-in one, and never pick a virtual / iPhone camera
   unless the user names it.
3. **Camera picker** in the Hands tab (all OSes): "Automatic (built-in camera)" or a named one
   (setting `air_camera`, already live).
4. **Smoother pointer:** the controller glides the mouse from one camera frame's position to
   the next in 8 ms steps instead of jumping once per frame (~30 Hz). Every other action
   (click, press, scroll …) first puts the pointer exactly on its target, so clicks land where
   the glide was heading.

Click semantics stay exactly like a mouse (Mohamed's choice): tap = click, two taps = double
click, hold = mouse button held (drag/move a window). No engine thresholds change.

## 2. Stack and packages

Nothing new. `opencv-python-headless` (cv2, already in the `air` extra) draws and encodes the
preview. macOS device listing uses ctypes + the Objective-C runtime like
`air/infrastructure/macos_camera.py` does already. Plain JS/CSS in the dashboard.

## 3. Structure (every file created or changed)

```
src/lura/modules/air/domain.py                       changed: mac_camera_info, pick_camera rank, PreviewFlash, PointerGlide, constants
src/lura/modules/air/infrastructure/macos_camera.py  changed: list_devices()
src/lura/modules/air/infrastructure/cameras.py       changed: list_cameras() on macOS
src/lura/modules/air/infrastructure/preview.py       NEW: render_preview() (cv2 drawing + JPEG)
src/lura/modules/air/application.py                  changed: AirService preview, AirController glide
src/lura/modules/air/__init__.py                     changed: export nothing new except list_cameras already there (no change needed unless noted)
src/lura/modules/dashboard/server.py                 changed: Raw response type, CSP img-src blob:
src/lura/modules/dashboard/api.py                    changed: air_preview / air_cameras callables + 2 routes
src/lura/app.py                                      changed: wire air_preview / air_cameras
src/lura/modules/dashboard/static/index.html         changed: preview box + camera select in the Hands card
src/lura/modules/dashboard/static/cards.js           changed: preview loop, camera select
src/lura/modules/dashboard/static/app.css            changed: .air-preview styles
src/lura/shared/i18n/en.json, ar.json                changed: new keys
tests/test_air_preview.py                            NEW
tests/run_all.py                                     changed: add test_air_preview.py
```

`air/__init__.py` needs no change (`list_cameras` is already exported).

## 4. File responsibilities

- **air/domain.py** — pure. Adds: `mac_camera_info()` (classify a macOS device into a
  `CameraInfo`), the `"builtin"` driver rank in `pick_camera`, `PreviewFlash` (which status
  label the preview shows), `PointerGlide` (pointer interpolation math). No I/O, no cv2, no time
  calls (time is passed in).
- **air/infrastructure/macos_camera.py** — `list_devices()`: AVFoundation devices in OpenCV's
  index order. Never raises; `[]` off macOS or on any error.
- **air/infrastructure/cameras.py** — `list_cameras()` on macOS builds `CameraInfo`s from
  `list_devices()` via `mac_camera_info`. Linux/Windows unchanged.
- **air/infrastructure/preview.py** — `render_preview(frame, hands, label) -> bytes`: draws on a
  copy and returns JPEG bytes. Imports cv2/numpy only. Knows nothing about services.
- **air/application.py** — `AirService` keeps the newest frame for the preview only while it is
  wanted and serves `preview_jpeg()`; `AirController` owns a `PointerGlide` and an `air-glide`
  thread.
- **dashboard/server.py** — routes may return `Raw` (bytes + content type + status) instead of
  JSON. CSP gets `blob:` in `img-src`.
- **dashboard/api.py** — `GET /api/air/preview` (Raw JPEG or 204), `GET /api/air/cameras`.
  Must not import `lura.modules.air` (architecture rule): it gets callables.
- **app.py** — passes `air_preview=c.air.preview_jpeg` and an `air_cameras` callable.
- **static files** — UI only.

## 5. Layering rules

domain ← application ← infrastructure as today. `application.py` imports
`infrastructure.preview` lazily inside `preview_jpeg()` (same pattern as the cameras import in
`start()`), so tests without cv2 still import the module. Dashboard reaches Air only through the
callables passed in `app.py`.

## 6. Coding conventions

Match the files you edit: `from __future__ import annotations`, type hints, module-level UPPER
constants with a short comment, `log = logging.getLogger(__name__)`, docstrings one line where
the neighbours have them. JS: no framework, `$`/`el`/`t()` helpers, `"use strict"` already set,
2-space indent, no handler returning `false`. All user-facing text through i18n (en + ar,
Egyptian Arabic, feminine for Lura).

## 7. Interfaces

### 7.1 Constants (air/domain.py)

```python
#: AVFoundation transport types (FourCC) and device types used to tell cameras apart.
MAC_TRANSPORT_BUILTIN = 0x626C746E      # 'bltn'
MAC_TRANSPORT_VIRTUAL = 0x76697274      # 'virt'
MAC_TYPE_BUILTIN = "AVCaptureDeviceTypeBuiltInWideAngleCamera"
MAC_TYPE_CONTINUITY = "AVCaptureDeviceTypeContinuityCamera"
#: Name hints of software cameras that show nothing unless their app runs.
VIRTUAL_CAMERA_HINTS = ("camo", "obs", "virtual", "snap camera", "mmhmm", "ndi", "elgato",
                        "manycam", "xsplit", "zoom", "teams")
#: Name hints of phone cameras (Continuity Camera and its Desk View).
PHONE_CAMERA_HINTS = ("iphone", "desk view", "ipad")

PREVIEW_FLASH_S = 0.6     # s a gesture label stays on the preview chip
PREVIEW_DOUBLE_S = 0.5    # s between two clicks shown as "DOUBLE CLICK"
GLIDE_TICK = 0.008        # s between pointer steps of a glide (~120 Hz)
GLIDE_SPAN_MIN = 0.016    # s a glide lasts at least
GLIDE_SPAN_MAX = 0.05     # s a glide lasts at most (a stalled camera never drags it out)
```

### 7.2 `mac_camera_info(index: int, name: str, device_type: str, transport: int) -> CameraInfo`

```
low = name.lower()
if transport == MAC_TRANSPORT_VIRTUAL or any(h in low for h in VIRTUAL_CAMERA_HINTS):
    driver, usable = "virtual", False
elif device_type == MAC_TYPE_CONTINUITY or any(h in low for h in PHONE_CAMERA_HINTS):
    driver, usable = "continuity", False
elif transport == MAC_TRANSPORT_BUILTIN or device_type == MAC_TYPE_BUILTIN:
    driver, usable = "builtin", True
else:
    driver, usable = "usb", True
return CameraInfo(index=index, name=name or f"Camera {index}", driver=driver, usable=usable)
```
(Virtual is checked first: a virtual camera may claim the built-in device type.)

### 7.3 `pick_camera` changes (air/domain.py)

- In the `named` list, also include non-usable cameras whose driver is `"continuity"`:
  `driver in ("v4l2loopback", "virtual", "continuity")`.
- Ranking of the automatic pick: `key=lambda c: (0 if c.driver in ("uvcvideo", "builtin") else 1, c.index)`.
- Update the docstring: on macOS a virtual (Camo, OBS) or iPhone camera is never picked by itself.

### 7.4 `PreviewFlash` (air/domain.py)

```python
_FLASH_LABELS = {"click": "CLICK", "right_click": "RIGHT CLICK", "press": "HOLD",
                 "release": "RELEASE", "scroll": "SCROLL", "swipe": "SWIPE",
                 "two_start": "TWO HANDS", "engage": "AIR ON", "disengage": "AIR OFF"}

class PreviewFlash:
    """Which gesture label the camera preview shows: the newest action for a moment, else the phase."""
    def __init__(self) -> None:
        self._label = ""
        self._t = -math.inf
        self._last_click_t = -math.inf
    def note(self, kind: str, t: float) -> None:
        if kind not in _FLASH_LABELS: return
        label = _FLASH_LABELS[kind]
        if kind == "click":
            if t - self._last_click_t <= PREVIEW_DOUBLE_S: label = "DOUBLE CLICK"
            self._last_click_t = t
        self._label, self._t = label, t
    def label(self, t: float, phase: str) -> str:
        if self._label and t - self._t <= PREVIEW_FLASH_S: return self._label
        return (phase or "").replace("_", " ").upper()
```
(`math` is already imported in domain.py; import it if not.)

### 7.5 `PointerGlide` (air/domain.py)

```python
class PointerGlide:
    """Linear glide of the mouse pointer from where it is drawn to the newest target."""
    def __init__(self) -> None:
        self._from: tuple[float, float] | None = None
        self._to: tuple[float, float] | None = None
        self._t0 = 0.0
        self._span = GLIDE_SPAN_MIN
    def retarget(self, start: tuple[float, float], target: tuple[float, float], now: float, span: float) -> None:
        # start = the position the pointer is drawn at now; if a glide is running, at(now) wins.
        cur = self.at(now)
        self._from = cur if cur is not None else start
        self._to = target
        self._t0 = now
        self._span = max(GLIDE_SPAN_MIN, min(GLIDE_SPAN_MAX, span))
    def at(self, now: float) -> tuple[float, float] | None:
        # None when idle. Else the interpolated point; at/after the end it is exactly the target.
        if self._to is None or self._from is None: return None
        f = (now - self._t0) / self._span
        if f >= 1.0: return self._to
        f = max(0.0, f)
        return (self._from[0] + (self._to[0] - self._from[0]) * f,
                self._from[1] + (self._to[1] - self._from[1]) * f)
    def done(self, now: float) -> bool:
        return self._to is None or (now - self._t0) >= self._span
    def finish(self) -> tuple[float, float] | None:
        # The target (or None if idle); the glide becomes idle.
        to = self._to
        self._from = self._to = None
        return to
```

### 7.6 `list_devices() -> list[tuple[str, str, int]]` (macos_camera.py)

Returns `(localized name, device type string or "", transport type int or 0)` for
`[AVCaptureDevice devicesWithMediaType:AVMediaTypeVideo]` followed by
`devicesWithMediaType:AVMediaTypeMuxed` — **exactly OpenCV's order** (checked in OpenCV 4.10,
4.12 and 5.x `cap_avfoundation_mac.mm`: `[[AVCaptureDevice devicesWithMediaType: AVMediaTypeVideo]
arrayByAddingObjectsFromArray:[AVCaptureDevice devicesWithMediaType:AVMediaTypeMuxed]]`), so the
list position is the `cv2.VideoCapture` index.

Implementation (ctypes; reuse `_objc()`'s libraries — refactor `_objc()` so it also returns
`objc` and `avf`, or add a second `@functools.lru_cache` helper `_runtime()` returning
`(objc, avf)` used by both; keep `authorization()` behaviour identical):

- Never call `objc_msgSend` variadic; build one `CFUNCTYPE` per signature from
  `ctypes.cast(objc.objc_msgSend, ctypes.c_void_p).value`:
  - `id f(id, SEL)` → `CFUNCTYPE(c_void_p, c_void_p, c_void_p)`
  - `id f(id, SEL, id)` → `CFUNCTYPE(c_void_p, c_void_p, c_void_p, c_void_p)`
  - `NSUInteger f(id, SEL)` → `CFUNCTYPE(c_ulong, c_void_p, c_void_p)`
  - `id f(id, SEL, NSUInteger)` → `CFUNCTYPE(c_void_p, c_void_p, c_void_p, c_ulong)`
  - `char* f(id, SEL)` → `CFUNCTYPE(c_char_p, c_void_p, c_void_p)`
  - `int32 f(id, SEL)` → `CFUNCTYPE(c_int32, c_void_p, c_void_p)`
  - `BOOL f(id, SEL, SEL)` → `CFUNCTYPE(c_bool, c_void_p, c_void_p, c_void_p)` (respondsToSelector:)
- Wrap the whole listing in `objc_autoreleasePoolPush()` / `objc_autoreleasePoolPop(pool)`
  (`restype c_void_p`, `argtypes [c_void_p]`), pop in `finally`.
- For each media type symbol (`AVMediaTypeVideo`, `AVMediaTypeMuxed`, read with
  `ctypes.c_void_p.in_dll(avf, …).value`; skip a symbol that is missing): `arr =
  devicesWithMediaType:(type)`; `n = count`; for i in range(n): `dev = objectAtIndex:i`;
  name = `UTF8String` of `localizedName` (decode utf-8, errors="replace"; "" if NULL);
  device type = `UTF8String` of `deviceType` only if `respondsToSelector:@selector(deviceType)`
  else ""; transport = `transportType` if `respondsToSelector:@selector(transportType)` else 0.
- Any exception → log at debug, return `[]`. Off macOS → `[]`.

### 7.7 `list_cameras()` on macOS (cameras.py)

Replace the non-Linux branch:
```python
if not IS_LINUX and root == _DEFAULT_SYSFS:
    if IS_MAC:
        devices = list_mac_devices()   # import list_devices as list_mac_devices from .macos_camera
        if devices:
            return [mac_camera_info(i, name, kind, transport)
                    for i, (name, kind, transport) in enumerate(devices)]
    return [CameraInfo(index=0, name="Default camera", driver="", usable=True)]
```
Windows keeps the single default camera (out of scope).

### 7.8 `render_preview(frame, hands, label: str) -> bytes` (infrastructure/preview.py)

```python
HAND_BONES = ((0, 1), (1, 2), (2, 3), (3, 4), (0, 5), (5, 6), (6, 7), (7, 8), (5, 9), (9, 10),
              (10, 11), (11, 12), (9, 13), (13, 14), (14, 15), (15, 16), (13, 17), (0, 17),
              (17, 18), (18, 19), (19, 20))
FINGERTIPS = (4, 8, 12, 16, 20)
JOINT_RGB = (255, 210, 0)        # yellow joints
BONE_RGB = (255, 240, 170)       # pale yellow bones
OUTLINE_RGB = (30, 30, 30)       # dark edge so it reads on any background
PREVIEW_JPEG_QUALITY = 75
```
Steps: `img = np.ascontiguousarray(frame).copy()` (RGB uint8, already mirrored — the landmarks
were detected on this same mirrored frame, so `x*w, y*h` maps straight). For each hand, pixel
points `p = (round(pt.x * w), round(pt.y * h))`. Bones: `cv2.line` OUTLINE thickness 5, then
BONE thickness 2, `lineType=cv2.LINE_AA`. Joints: `cv2.circle` OUTLINE radius r+1 filled, then
JOINT radius r filled (r = 6 for FINGERTIPS, 4 otherwise), LINE_AA. Label chip if `label`:
`cv2.getTextSize(label, FONT_HERSHEY_SIMPLEX, 0.6, 2)`, a filled dark rectangle blended at
0.55 alpha (draw on a copy of the region, `cv2.addWeighted`) at top-left (10, 10) with 8 px padding,
then white text. Encode: `cv2.imencode(".jpg", cv2.cvtColor(img, cv2.COLOR_RGB2BGR),
[cv2.IMWRITE_JPEG_QUALITY, PREVIEW_JPEG_QUALITY])`; return `buf.tobytes()`; raise
`RuntimeError("preview encode failed")` if not ok.

### 7.9 AirService preview (application.py)

```python
_PREVIEW_HOLD = 2.0   # s the camera keeps frames for the preview after the dashboard last asked
```
New fields in `__init__`: `self._preview_lock = threading.Lock()`, `self._preview_until = -math.inf`
(monotonic), `self._preview_src: tuple[int, object, tuple[Hand, ...], str] | None = None`,
`self._preview_seq = 0`, `self._preview_cache: tuple[int, bytes] | None = None`,
`self._flash = PreviewFlash()`.

`preview_jpeg(self) -> bytes | None`:
```
with self._preview_lock:
    self._preview_until = time.monotonic() + _PREVIEW_HOLD
    src, cache = self._preview_src, self._preview_cache
if src is None: return None
seq, frame, hands, label = src
if cache is not None and cache[0] == seq: return cache[1]
from .infrastructure.preview import render_preview
try: data = render_preview(frame, hands, label)
except Exception: log.debug("Air preview failed", exc_info=True); return None
with self._preview_lock: self._preview_cache = (seq, data)
return data
```
In `_run`, right after `cmds = engine.update(...)`/`eng_state` and before dispatch:
```
for c in cmds: self._flash.note(c.kind, t)
if time.monotonic() < self._preview_until:      # read without the lock: a float, races are harmless
    phase = str((eng_state or {}).get("phase", "")) if hands else ""
    with self._preview_lock:
        self._preview_seq += 1
        self._preview_src = (self._preview_seq, frame, hands, self._flash.label(t, phase))
```
(Store references only; `frame` is a new array from every `read()`.) When no hand is visible the
chip shows nothing unless an action label is still flashing. In the `finally` of `_run`, set
`self._preview_src = None` and `self._preview_cache = None` under `_preview_lock`. While the
preview is wanted, skip the idle throttle (`if not hands and ... and time.monotonic() >= self._preview_until`)
so the picture stays live.

Use whatever the engine state calls its phase key (`engine.state["phase"]` — already used in
app.py as `st["phase"]`).

### 7.10 AirController glide (application.py)

New fields: `self._glide = PointerGlide()`, `self._drawn: tuple[float, float] | None = None`
(last position sent to `pointer_to`), `self._last_cursor_t = -math.inf` (cmd.t of the last
cursor command), `self._glide_wake = threading.Event()`, `self._glide_thread = None`.

`cursor` branch, replacing the direct `pointer_to` call (target computation unchanged):
```
span = cmd.t - self._last_cursor_t
self._last_cursor_t = cmd.t
start = self._drawn if self._drawn is not None else prev   # prev = self._cursor before this command
self._glide.retarget(start, (cx, cy), self._clock(), span)
self._glide_wake.set()
```
(If no glide thread is running — tests that call `process()` directly — the target is still
reached: every other command flushes, and `_glide_step()` can be called by hand.)

`_glide_step(self) -> bool` (under `self._lock`): `now = self._clock()`; `pos = self._glide.at(now)`;
if None return False; `pt = (round(pos[0]), round(pos[1]))`; if `self._drawn` rounded differs,
call `self._desktop.pointer_to(*pt)` (log warning on exception, like today) and set
`self._drawn = (float(pt[0]), float(pt[1]))`; if `self._glide.done(now)`: `self._glide.finish()`;
return False; else return True.

`_glide_flush(self)` (under `self._lock`): `to = self._glide.finish()`; if to is not None and
rounded `to` != rounded `self._drawn`: `pointer_to(round(to[0]), round(to[1]))`, set `_drawn`.

At the top of `process()`, inside `with self._lock:`, for every `k != "cursor"`: call
`self._glide_flush()` first. On `disengage` and in `stop()`: flush, then `self._drawn = None`.
When the cursor is resynced from the real pointer (`_read_pointer()` in the cursor branch or
`engage`), also set `self._drawn = self._cursor`.

Thread `air-glide` (started in `start()` next to `air-control`, daemon, joined in `stop()`):
```
while not self._stop.is_set():
    self._glide_wake.wait(0.2)
    self._glide_wake.clear()
    while not self._stop.is_set():
        with self._lock:
            busy = self._glide_step()
        if not busy: break
        self._stop.wait(GLIDE_TICK)
```
`stop()` sets `_glide_wake` too so the thread exits promptly.

### 7.11 Dashboard server `Raw` (server.py)

```python
@dataclass(frozen=True)
class Raw:
    """A non-JSON route result (e.g. a JPEG frame)."""
    body: bytes
    kind: str
    status: int = 200
```
In `_dispatch`: `result = handler(body, match)`; `if isinstance(result, Raw): return
self._send(result.status, result.body, result.kind)` else `self._json(200, result)`. Token check
is unchanged (Raw routes need the token too). CSP: `img-src 'self' data: blob:`.

### 7.12 API (api.py)

New fields on `DashboardApi`: `air_preview: Callable[[], bytes | None] | None = None`,
`air_cameras: Callable[[], list[dict]] | None = None`. Routes:
- `GET /api/air/preview` → `Raw(data, "image/jpeg")` when `air_preview` returns bytes, else
  `Raw(b"", "image/jpeg", 204)`.
- `GET /api/air/cameras` → `{"cameras": air_cameras() or [], "wanted": settings.current.air_camera}`;
  `[]` when the callable is None or raises (log debug).

### 7.13 app.py

```python
def air_cameras() -> list[dict]:
    from .modules.air import list_cameras
    return [{"index": cam.index, "name": cam.name, "driver": cam.driver, "usable": cam.usable}
            for cam in list_cameras() if cam.usable or cam.driver in ("v4l2loopback", "virtual", "continuity")]
```
(Linux metadata nodes — `usable False` with a real driver — are left out.) Pass
`air_preview=c.air.preview_jpeg, air_cameras=air_cameras` to `DashboardApi(...)`.

### 7.14 Hands tab UI

index.html, inside `#air-card` right after `<div id="air-perms" hidden></div>`:
```html
<div class="air-preview" id="air-preview" hidden>
  <img id="air-preview-img" alt="" hidden>
  <p class="air-preview-wait" id="air-preview-wait" data-i18n="air.preview.wait"></p>
</div>
<label class="field" id="air-camera-field" hidden><span data-i18n="air.camera"></span>
  <select id="air-camera"></select>
</label>
```
The `alt` is set from i18n in JS: `img.alt = t("air.preview.alt")`.

cards.js:
- In `renderAir()`: `$("#air-preview").hidden = !on; $("#air-camera-field").hidden = !on;`, then
  `if (on) { airPreviewLoop(); loadAirCameras(); }`.
- `showTab(name)`: after toggling panels add `if (name === "air") airPreviewLoop();`.
- `document.addEventListener("visibilitychange", () => { if (!document.hidden) airPreviewLoop(); });`
  registered once at top level of cards.js.
- Preview loop (top level, after `loadAir`):
```js
let airPreviewRunning = false;
let airPreviewUrl = null;
const airPreviewWanted = () => !!(S && S.settings.air_enabled) && tab === "air" && !document.hidden;
async function airPreviewLoop() {
  if (airPreviewRunning) return;
  airPreviewRunning = true;
  const img = $("#air-preview-img"), wait = $("#air-preview-wait");
  img.alt = t("air.preview.alt");
  while (airPreviewWanted()) {
    const started = performance.now();
    let shown = false;
    try {
      const res = await fetch("/api/air/preview", { headers: { "X-Lura-Token": TOKEN }, cache: "no-store" });
      if (res.status === 200) {
        const url = URL.createObjectURL(await res.blob());
        img.src = url;
        if (airPreviewUrl) URL.revokeObjectURL(airPreviewUrl);
        airPreviewUrl = url;
        shown = true;
      }
    } catch (_) { /* daemon restarting: keep waiting */ }
    img.hidden = !shown;
    wait.hidden = shown;
    await new Promise((r) => setTimeout(r, Math.max(10, 66 - (performance.now() - started))));
  }
  airPreviewRunning = false;
}
```
- Camera select:
```js
let airCamerasLoaded = false;
async function loadAirCameras() {
  if (airCamerasLoaded) return;
  airCamerasLoaded = true;
  let data = { cameras: [], wanted: "" };
  try { data = await api("GET", "/api/air/cameras"); } catch (_) { airCamerasLoaded = false; return; }
  const sel = $("#air-camera");
  sel.replaceChildren(el("option", { value: "", textContent: t("air.camera.auto") }),
    ...data.cameras.map((c) => el("option", { value: c.name,
      textContent: c.usable ? c.name : `${c.name} · ${t(c.driver === "continuity" ? "air.camera.phone" : "air.camera.virtual")}` })));
  sel.value = data.cameras.some((c) => c.name === data.wanted) ? data.wanted : "";
  sel.onchange = () => save({ air_camera: sel.value });
}
```
  (Use the existing `el()` helper's signature as other code in cards.js does; if `el` takes
  children differently, follow it.)

app.css (next to `.air-list`):
```css
.air-preview { position: relative; margin: .8em 0; border-radius: var(--radius); overflow: hidden;
  background: #111; min-height: 140px; display: grid; place-items: center; }
.air-preview img { display: block; width: 100%; height: auto; }
.air-preview-wait { color: #ccc; margin: 0; padding: 1em; text-align: center; }
```

### 7.15 i18n keys (en / ar)

| key | en | ar |
|---|---|---|
| `air.preview.wait` | Waiting for the camera… | مستنية الكاميرا… |
| `air.preview.alt` | What Lura's camera sees, with your hand's joints marked | اللي كاميرا لورا شايفاه، ومفاصل إيدك متعلّم عليها |
| `air.camera` | Camera | الكاميرا |
| `air.camera.auto` | Automatic (the computer's own camera) | تلقائي (كاميرا الجهاز نفسه) |
| `air.camera.virtual` | virtual camera | كاميرا افتراضية |
| `air.camera.phone` | phone | موبايل |

Change `air.privacy` to — en: "Everything stays on this computer: frames are read by a local
model and never saved or sent. The preview above is shown only here." ar: keep the existing
sentence and append « والصورة اللي فوق بتظهر هنا بس.»

## 8. Functions — edge cases

- `preview_jpeg()` with Air off → None → 204. First request after opening the tab may be 204
  (frames start being kept only once asked): the loop just asks again.
- Two dashboards polling: both get the cached bytes for the same seq; encoding happens once per frame.
- A frame with no hands: plain mirrored image, chip only while a flash lasts.
- `list_devices()` on a Mac with no cameras → `[]` → `[CameraInfo(0, "Default camera", …)]` fallback.
- A user-named camera that disappears: `camera_candidates` already falls back to the automatic pick.
- Glide: a cursor command arriving mid-glide starts from the interpolated point (no jump back).

## 9. Tasks

1. domain.py: constants, `mac_camera_info`, `pick_camera` changes, `PreviewFlash`, `PointerGlide`.
2. macos_camera.py `list_devices`; cameras.py macOS branch.
3. infrastructure/preview.py.
4. application.py: AirService preview + idle-throttle tweak; AirController glide + thread.
5. server.py `Raw` + CSP; api.py fields/routes; app.py wiring.
6. index.html, cards.js, app.css, en.json, ar.json.
7. tests/test_air_preview.py (+ run_all.py entry).

## 10. Acceptance checks

`tests/test_air_preview.py` (style of tests/test_air_look.py: plain functions, `_setup` import,
prints `air preview OK` at the end), covering:
1. `mac_camera_info`: ("FaceTime HD Camera", MAC_TYPE_BUILTIN, BUILTIN) → builtin/usable;
   ("Camo", MAC_TYPE_BUILTIN, VIRTUAL) → virtual/not usable; ("Camo Studio", "", 0) → virtual;
   ("Mohamed's iPhone Camera", MAC_TYPE_CONTINUITY, 0) → continuity/not usable; ("Logitech BRIO",
   "AVCaptureDeviceTypeExternal", 0x75736220) → usb/usable.
2. `pick_camera([camo@0, iphone@1, facetime@2])` → facetime; `pick_camera(..., "camo")` → camo;
   `pick_camera(..., "iphone")` → iphone; with a usb@0 and builtin@1 → builtin.
   `camera_candidates([...], "camo")` → [camo, facetime].
3. `PreviewFlash`: note("click", 1.0) → label(1.1, "point") == "CLICK"; note("click", 1.3) →
   "DOUBLE CLICK"; label(2.5, "point") == "POINT"; note("cursor", …) changes nothing;
   label(5, "") == "".
4. `PointerGlide`: retarget((0,0),(100,0), now=0, span=0.04) → at(0.02) == (50,0), at(0.04) ==
   (100,0), done(0.04) True; retarget mid-way ((…),(100,100), now=0.02, span=0.04) starts at (50,0);
   span clamps to GLIDE_SPAN_MIN/MAX; finish() returns the target then at() is None.
5. If cv2 imports: `render_preview(np.zeros((360,640,3),uint8), (hand,), "CLICK")` with a fake Hand
   whose 21 points sit around (0.5, 0.5) → bytes starting `b"\xff\xd8"`; decode with
   `cv2.imdecode` and check the pixel at the index tip is yellowish (R>180, G>150, B<120).
   Without cv2, print a skip line.
6. AirService: a fresh `AirService(lambda n, p: None)` → `preview_jpeg()` is None; set
   `svc._preview_src = (1, frame, (), "")` → returns JPEG (cv2 only), a second call returns the
   identical bytes object (cache); `svc._preview_until` > `time.monotonic()` after the call.
7. AirController glide with a fake desktop recording `pointer_to` (reuse the fake from
   tests/test_air_control.py or tests/test_air_assistant.py; do not start threads) and a
   controllable clock: engage, then a `cursor` command dx=100 → no `pointer_to` yet for the target;
   advance the clock 10 ms, `_glide_step()` → a point strictly between start and target; then a
   `click` command → the last `pointer_to` before the click is exactly the target.
8. Dashboard (spin up `DashboardServer` the way tests/test_dashboard.py does, with
   `air_preview` returning `b"\xff\xd8fake"` and then None, `air_cameras` returning one camera):
   `GET /api/air/preview` with the token → 200, `Content-Type: image/jpeg`, body equal; with the
   callable returning None → 204; without the token → 401. `GET /api/air/cameras` → the list and
   `wanted`. The CSP header contains `blob:`. Use `urllib.request` / `http.client` directly for the
   raw response.

Commands (all must pass):
```bash
.venv/bin/python -m compileall -q src/
.venv/bin/python tests/test_air_preview.py         # → air preview OK
.venv/bin/python tests/test_air_engine.py          # → air engine OK (unchanged)
.venv/bin/python tests/test_air_control.py
.venv/bin/python tests/test_dashboard.py
.venv/bin/python tests/test_architecture.py
.venv/bin/python tests/run_all.py                  # → All checks passed.
cd src && ../.venv/bin/python -m lura.modules.overlay   # → overlay self-check OK
```

## 11. Out of scope

Do not change engine.py thresholds or gestures, the overlay, Windows camera names, settings
fields (no new setting), README/CLAUDE.md, version numbers, packaging. Do not touch `docs/`.
Do not commit.

## 12. Decisions

- Mohamed chose: clicks stay mouse-like (tap = click, two taps = double click, hold = drag).
- Preview is pulled as single JPEGs (no MJPEG stream): the server is a plain
  `ThreadingHTTPServer` with JSON routes, a fetch per frame needs the token header anyway, and it
  stops by itself when the tab closes.
- (Executor question, glide vs existing tests) **Option B.** `AirController.__init__` gets
  `glide: bool = False` (keyword, after `app_kinds`). With `glide=False` the `cursor` branch calls
  `pointer_to` directly exactly as before (and sets `_drawn`), `start()` starts no `air-glide`
  thread, and flushes are no-ops — so tests/test_air_engine.py and tests/test_air_control.py stay
  unchanged and pass. With `glide=True` everything in §7.10 applies. `src/lura/bootstrap.py`
  (added to §3) passes `glide=True` to the `AirController(...)` it builds. test_air_preview.py
  check 7 constructs the controller with `glide=True`.
