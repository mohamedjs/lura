# 003 — Lura Air Stage C: camera, hand tracking, gesture recognition (`modules/air`)
Status: Implemented (2026-11-20, Gemini 4 Neon, 2 rounds; reviewed, fixed and tested live by Claude, see §12)     Builds on: docs/001-lura-air.md, docs/002-air-desktop-actions.md, docs/ARCHITECTURE.md

## 1. Goal

Turn the webcam into a stream of **discrete gesture events** (hand appears, pinch, swipe, thumbs
up, …) plus a **continuously updated hand state** (where the hand is, is it pinching), all
computed locally. Nothing acts on the desktop yet (that is Stage D); this stage delivers the
recognition engine, its camera lifecycle, numbers (fps, latency, CPU, memory), and a terminal
tool to watch it work. Camera frames never leave the process and are never written to disk.

## 2. Stack and packages

- `mediapipe==1.2.3` (wheel `py3-none-manylinux_2_28_x86_64`, 43 MB). It ships **only the Tasks
  API**: `mediapipe.tasks.python.vision.HandLandmarker` (no `mp.solutions.hands`). Verified
  signatures:
  - `HandLandmarkerOptions(base_options=BaseOptions(model_asset_path=str), running_mode=RunningMode.VIDEO,
    num_hands=2, min_hand_detection_confidence=…, min_hand_presence_confidence=…, min_tracking_confidence=…)`
  - `HandLandmarker.create_from_options(opts)`, `.detect_for_video(mp.Image, timestamp_ms: int)`, `.close()`
  - result fields: `hand_landmarks` (list per hand of 21 landmarks with `.x .y .z`, normalized 0..1),
    `handedness` (list per hand of categories with `.category_name` "Left"/"Right" and `.score`).
  - `mp.Image(image_format=mp.ImageFormat.SRGB, data=<HxWx3 uint8 RGB numpy array>)`.
- `opencv-python-headless>=4.10` (installed 5.1.0.95) for capture and colour conversion.
- Model file `hand_landmarker.task` (7,819,105 bytes) from
  `https://storage.googleapis.com/mediapipe-models/hand_landmarker/hand_landmarker/float16/latest/hand_landmarker.task`,
  stored at `data_dir()/air/hand_landmarker.task`. Not shipped in the repo.
- Both packages are an **optional extra** `air` in `pyproject.toml`. `lura.modules.air` must
  import and its pure parts must work **without** cv2/mediapipe installed (lazy imports in
  infrastructure only).
- Dev environment: the worktree has its own venv with both installed:
  `/var/www/html/old/lura-air/.venv/bin/python`. **Do not install anything else. Do not download
  the model** (the reviewer does it after the user's OK). Do not open the camera.

Measured on this machine (Stage A): `/dev/video0` "Integrated Camera" is the real webcam
(MJPG 640×360 @ ~28 fps, first frame after 0.5 s); `/dev/video1` is its metadata node;
`/dev/video9` "Mobile Webcam" is a **v4l2loopback** device, not a camera.

## 3. Structure

```
src/lura/modules/air/__init__.py                    NEW  public API
src/lura/modules/air/domain.py                      NEW  pure: landmarks, poses, filters, recognizer, events, stats
src/lura/modules/air/application.py                 NEW  AirService (camera thread, lifecycle, state, publishing), ports
src/lura/modules/air/infrastructure/__init__.py     NEW  empty docstring module
src/lura/modules/air/infrastructure/cameras.py      NEW  find cameras (Linux sysfs), OpenCV capture
src/lura/modules/air/infrastructure/landmarker.py   NEW  MediaPipe HandLandmarker wrapper, model path + download
src/lura/shared/events.py                           EDIT add AIR_GESTURE, AIR_STATUS
src/lura/app.py                                     EDIT `lura air` subcommand (cameras, setup, watch, bench)
pyproject.toml                                      EDIT optional extra `air`
tests/test_air.py                                   NEW  pure + fake-camera checks (in run_all)
tests/live_air.py                                   NEW  real camera benchmark (NOT in run_all)
tests/run_all.py                                    EDIT add "test_air.py" to CHECKS
docs/ARCHITECTURE.md                                EDIT one line for air/ in the module tree (see §9 task 8)
```
No other file is created or changed. `bootstrap.py` is **not** changed in this stage (the
daemon does not start the camera yet).

## 4. File responsibilities

**`air/__init__.py`** — docstring `"""Air: hand gestures from the webcam, recognised on this computer."""`.
Exports exactly: `AirService`, `AirState`, `HandView`, `GestureEvent`, `GESTURES`, `Camera`,
`Landmarker`, `list_cameras`, `pick_camera`, `model_path`, `model_ready`, `download_model`,
`air_available`. Imports from its own submodules only.

**`domain.py`** — pure (no cv2, mediapipe, numpy, threads, clocks, files). All times are passed in
as float seconds. Exports the shapes of §7, the constants of §7.1, `classify_pose`, `pinch_ratio`,
`palm_center`, `OneEuro`, `Recognizer`, `Stats`, `CameraInfo`, `pick_camera`.

**`application.py`** — `Camera` and `Landmarker` Protocols (ports), `AirService`. Owns the camera
thread. May import `.domain`, `..` nothing else from other modules, and `...shared.events` (for the
event names) + stdlib. Must not import cv2/mediapipe.

**`infrastructure/cameras.py`** — `list_cameras() -> list[CameraInfo]` (Linux: sysfs; other OS:
`[CameraInfo(index=0, …)]`), `OpenCVCamera` implementing `Camera`. Imports cv2 inside methods.

**`infrastructure/landmarker.py`** — `MediaPipeLandmarker` implementing `Landmarker`, plus
`model_path()`, `model_ready()`, `download_model()`, `air_available()`. Imports mediapipe/numpy/cv2
inside functions.

**`shared/events.py`** — two names (§7.4).

**`app.py`** — `air` subparser and `cmd_air` (§8.5).

**`tests/test_air.py`** — §10. **`tests/live_air.py`** — §8.6.

## 5. Layering rules

- `air` imports only `shared` (events, paths, platform). It must **not** import `computer`,
  `assistant`, `tools`, `overlay`, `settings`, `brain` in this stage.
- `AirService` receives `publish: Callable[[str, dict], None] | None` (bus.publish adapter),
  camera/landmarker factories, and a clock — it never builds infrastructure itself unless the
  factory arguments are omitted (then it uses `infrastructure` defaults lazily, inside `start()`).
- **The event bus is synchronous**: publish only discrete events (§7.3), never per-frame data.
  Per-frame state is read with `AirService.snapshot()` (thread-safe, cheap).
- Camera work runs on one daemon thread named `air-camera`. Nothing runs on the main or audio
  threads. `stop()` must return within 2 s.

## 6. Coding conventions

Same as docs/002 §6: `from __future__ import annotations`, short plain docstrings, full type
hints, frozen dataclasses for value types, `log = logging.getLogger(__name__)`, line length ≤ 110,
no print() outside `app.py`/tests, comments only for non-obvious reasons. Thresholds are named
constants in `domain.py` with a one-line comment each (they will be tuned).

## 7. Interfaces (`domain.py`)

Landmark indices (MediaPipe hand): `WRIST=0, THUMB_TIP=4, INDEX_MCP=5, INDEX_PIP=6, INDEX_TIP=8,
MIDDLE_MCP=9, MIDDLE_PIP=10, MIDDLE_TIP=12, RING_MCP=13, RING_PIP=14, RING_TIP=16, PINKY_MCP=17,
PINKY_PIP=18, PINKY_TIP=20, THUMB_IP=3`.

```python
@dataclass(frozen=True)
class Point:
    x: float; y: float; z: float = 0.0          # normalized image coords, 0..1, y down

@dataclass(frozen=True)
class Hand:
    side: str                  # "left" | "right" (the user's real hand; the frame is mirrored first)
    score: float               # handedness score 0..1
    points: tuple[Point, ...]  # exactly 21

@dataclass(frozen=True)
class HandFrame:
    t: float                   # seconds (monotonic)
    hands: tuple[Hand, ...]    # 0..2

@dataclass(frozen=True)
class GestureEvent:
    gesture: str               # one of GESTURES
    hand: str                  # "left" | "right" | "both"
    confidence: float          # 0..1, rounded to 2 decimals
    x: float; y: float         # smoothed position (user's view, 0..1), see §8.1
    t: float
    data: dict = field(default_factory=dict)    # extra: {"pose": "...", "scale": 1.4, ...}
    def to_payload(self, mode: str = "universal") -> dict:
        # {"source": "gesture", "gesture": g, "confidence": c, "hand": h,
        #  "position": {"x": round(x, 3), "y": round(y, 3)}, "mode": mode, "t": t, **data}

@dataclass(frozen=True)
class HandView:
    side: str; x: float; y: float; pose: str; pinching: bool; score: float

@dataclass(frozen=True)
class AirState:
    camera: str = "off"        # "off" | "starting" | "on" | "lost" | "error"
    detail: str = ""           # e.g. the error text or the camera name
    hands: tuple[HandView, ...] = ()
    spread_scale: float | None = None      # while both hands pinch: distance / distance at start
    fps: float = 0.0
    last_gesture: str = ""
    last_gesture_t: float = 0.0

@dataclass(frozen=True)
class CameraInfo:
    index: int                 # OpenCV / V4L2 index (N in /dev/videoN)
    name: str                  # sysfs name
    driver: str                # e.g. "uvcvideo", "v4l2loopback", "" when unknown
    usable: bool               # capture node of a real camera
```

`GESTURES = ("hand_enter", "hand_leave", "pose", "pinch_start", "pinch_end", "double_pinch",
"swipe_left", "swipe_right", "swipe_up", "swipe_down", "spread_start", "spread_end")`.
Pose names: `"open_palm", "fist", "point", "victory", "thumbs_up", "thumbs_down", "pinch", "none"`.

### 7.1 Constants (domain.py, exact values)

```python
PINCH_ON = 0.28          # thumb–index tip distance / palm size below which a pinch starts
PINCH_OFF = 0.40         # … above which it ends (hysteresis)
EXTENDED = 1.15          # finger extended when wrist→tip > EXTENDED × wrist→pip
THUMB_OUT = 0.60         # thumb extended when thumb tip→index MCP > THUMB_OUT × palm size
POSE_STABLE = 0.15       # s a pose must hold before a "pose" event
LEAVE_GRACE = 0.25       # s without the hand before "hand_leave"
SWIPE_WINDOW = 0.35      # s of palm history examined for a swipe
SWIPE_DIST = 0.22        # normalized travel needed within the window
SWIPE_AXIS = 2.0         # main-axis travel must be ≥ SWIPE_AXIS × cross-axis travel
SWIPE_COOLDOWN = 0.70    # s after a swipe before any other swipe
DOUBLE_PINCH_GAP = 0.45  # s between a short pinch's end and the next pinch's start
SHORT_PINCH = 0.30       # s a pinch may last to count as the first of a double pinch
MIN_SCORE = 0.60         # hands with a lower handedness score are ignored
SPREAD_MIN_DIST = 0.05   # both-hand pinch distance must exceed this to start a spread
```

### 7.2 Ports (`application.py`)

```python
class Camera(Protocol):
    def open(self) -> None: ...            # raises RuntimeError(sentence) if it cannot
    def read(self) -> "object | None": ... # an RGB HxWx3 uint8 frame, already mirrored; None = no frame
    def close(self) -> None: ...
    @property
    def name(self) -> str: ...

class Landmarker(Protocol):
    def detect(self, frame: object, t_ms: int) -> tuple[Hand, ...]: ...
    def close(self) -> None: ...
```

### 7.3 Which events go on the bus

Published (as `events.AIR_GESTURE` with `GestureEvent.to_payload()`): every `GestureEvent`.
Published as `events.AIR_STATUS` with `{"camera": state, "detail": str}`: only when the camera
state changes. Never published: per-frame positions, pinch movement, spread scale — those are in
`snapshot()`.

### 7.4 `shared/events.py`

Append after `EXIT_REQUESTED`, with comments in the file's style:
```python
#: Lura Air recognised a gesture: the payload is GestureEvent.to_payload() (discrete events only).
AIR_GESTURE = "air.gesture"
#: The Air camera changed state: {"camera": "on" | "lost" | "off" | "error", "detail": str}.
AIR_STATUS = "air.status"
```

## 8. Functions

### 8.1 `domain.py`

- `dist(a, b) -> float` (2-D, x/y only). `palm_size(h) = dist(WRIST, MIDDLE_MCP)`, never below 1e-6.
- `palm_center(h) -> Point` — mean x/y of WRIST, INDEX_MCP, MIDDLE_MCP, RING_MCP, PINKY_MCP.
- `pinch_point(h) -> Point` — midpoint of THUMB_TIP and INDEX_TIP.
- `pinch_ratio(h) -> float` — `dist(THUMB_TIP, INDEX_TIP) / palm_size(h)`.
- `extended(h) -> dict[str, bool]` — keys thumb, index, middle, ring, pinky. Finger f (not thumb):
  `dist(WRIST, tip) > EXTENDED * dist(WRIST, pip)`. Thumb: `dist(THUMB_TIP, INDEX_MCP) > THUMB_OUT * palm_size`.
- `classify_pose(h, pinching: bool) -> tuple[str, float]` — returns (pose, clarity 0..1):
  1. `pinching` → ("pinch", 1.0).
  2. fingers = count of extended among index, middle, ring, pinky.
  3. fingers == 4 → "open_palm". index only → "point". index+middle only → "victory".
  4. fingers == 0: thumb extended and `THUMB_TIP.y < INDEX_MCP.y - 0.3*palm` → "thumbs_up";
     thumb extended and `THUMB_TIP.y > INDEX_MCP.y + 0.6*palm` → "thumbs_down"; else "fist".
  5. otherwise "none".
  Clarity: for open_palm/fist the fraction of the four fingers whose `wrist→tip / wrist→pip`
  is beyond 1.15 (or below it for fist) by ≥ 0.05; for other poses 1.0. (Used only to scale
  the event confidence: `confidence = round(hand.score * clarity, 2)`.)
- `class OneEuro` — 1-D One Euro filter: `__init__(min_cutoff=1.2, beta=0.02, d_cutoff=1.0)`,
  `__call__(value: float, t: float) -> float`, `reset()`. First call returns the value. Standard
  formula: `alpha(cutoff, dt) = 1 / (1 + 1/(2π·cutoff·dt))`; dt ≤ 0 → return the previous output.
- `class Recognizer` — stateful, pure, single-threaded. `update(frame: HandFrame) -> list[GestureEvent]`
  and `views(t) -> tuple[HandView, ...]`, `spread_scale() -> float | None`, `reset()`.
  Per side ("left"/"right") it keeps a `_Track`: last seen t, pinching flag, pinch start t,
  last pinch end t and duration, current stable pose + candidate pose + candidate since, palm
  history deque of (t, x, y) (smoothed), two `OneEuro` per tracked point (palm x/y, pinch x/y),
  last swipe t is **global** (shared by both hands).
  `update` steps, in this order:
  1. Keep hands with `score >= MIN_SCORE`; if two have the same side keep the higher score.
  2. For each present hand: if no track → create it and emit `hand_enter` at the palm centre.
     Smooth palm centre and pinch point with its filters.
  3. Pinch hysteresis: not pinching and `pinch_ratio < PINCH_ON` → pinching; emit `pinch_start`
     at the pinch point; if `t - last_pinch_end <= DOUBLE_PINCH_GAP` and the last pinch lasted
     `<= SHORT_PINCH` → also emit `double_pinch` (data `{}`) and clear the last-pinch record.
     Pinching and ratio `> PINCH_OFF` → not pinching; emit `pinch_end` with data
     `{"duration": round(d, 2)}`; record end t and duration.
  4. Pose: `classify_pose`. If it differs from the candidate → candidate = it, since = t. If the
     candidate has held `>= POSE_STABLE` and differs from the stable pose → stable = candidate and,
     unless it is "pinch" or "none", emit `pose` with data `{"pose": name}`.
  5. Swipe (only when not pinching and the stable pose is "open_palm"): append (t, x, y) of the
     smoothed palm centre; drop entries older than `SWIPE_WINDOW`. If `t - last_swipe >=
     SWIPE_COOLDOWN`: dx = x_now − x_oldest, dy likewise; `abs(dx) >= SWIPE_DIST and abs(dx) >=
     SWIPE_AXIS*abs(dy)` → `swipe_right` if dx > 0 else `swipe_left`; same for dy → `swipe_down`
     if dy > 0 else `swipe_up`. On a swipe: last_swipe = t, clear this hand's history.
     (x grows to the user's right because frames are mirrored before detection.)
  6. Tracks not present in this frame: if `t - last_seen >= LEAVE_GRACE` → if pinching emit
     `pinch_end` (data `{"duration": …, "lost": True}`) first, then `hand_leave`, delete the track.
     (A drag must never stay "held" after the hand is gone.)
  7. Two-hand spread: both tracks pinching and `dist(pinch points) > SPREAD_MIN_DIST` and no
     spread active → start (store d0), emit `spread_start` hand "both" at the midpoint. Active and
     either not pinching or missing → emit `spread_end` with data `{"scale": round(d/d0, 2)}`
     (last known d), end. While active `spread_scale()` = d/d0.
  Event x/y: the smoothed point the event is about (palm centre, or pinch point for pinch
  events). Events are returned in the order generated.
- `class Stats` — rolling numbers over the last 120 frames: `add(detect_ms: float, loop_ms: float,
  t: float, hands: int)`; `summary() -> dict` with `frames`, `fps` (frames / time span),
  `detect_ms_avg`, `detect_ms_p95`, `loop_ms_avg`, `hand_frames` (frames with ≥1 hand). Round to 1 decimal.
- `pick_camera(cams: list[CameraInfo], wanted: str = "") -> CameraInfo | None` — usable cameras
  only; `wanted` non-empty → first whose name contains it (case-insensitive) or whose
  `str(index) == wanted`; else prefer driver "uvcvideo", then the lowest index.

### 8.2 `AirService` (`application.py`)

```python
class AirService:
    def __init__(self, publish: Callable[[str, dict], None] | None = None, *, camera: str = "",
                 camera_factory: Callable[[], Camera] | None = None,
                 landmarker_factory: Callable[[], Landmarker] | None = None,
                 clock: Callable[[], float] = time.monotonic, mode: str = "universal") -> None
    def start(self) -> None          # idempotent; spawns the air-camera thread
    def stop(self) -> None           # idempotent; signals, joins ≤ 2 s, closes camera + landmarker
    def snapshot(self) -> AirState   # thread-safe copy
    def stats(self) -> dict          # Stats.summary() + {"cpu_percent", "rss_mb"} (see below)
    def subscribe(self, fn: Callable[[GestureEvent], None]) -> None   # local listeners (CLI, tests)
    @property
    def running(self) -> bool
```
Defaults when factories are omitted (resolved inside `start()`, lazily imported from
`.infrastructure`): camera = `OpenCVCamera(pick_camera(list_cameras(), camera))` (no usable
camera → state "error", detail "No camera found.", thread not started); landmarker =
`MediaPipeLandmarker(model_path())` (missing model or packages → state "error" with the reason
from `air_available()`, not started).

Thread loop (`_run`), all exceptions caught and logged — the thread never dies silently:
1. state "starting"; `camera.open()`; failure → state "lost", retry every 2 s until stopped.
2. state "on" (detail = camera name), publish AIR_STATUS.
3. Loop while not stopping:
   - `frame = camera.read()`; None → count misses; 15 in a row → state "lost" (publish), close the
     camera, go to 1 after 2 s. A frame resets the miss counter.
   - `t = clock()`; `hands = landmarker.detect(frame, int(t * 1000))` timed as detect_ms;
     timestamps passed to detect must be strictly increasing (bump by 1 ms if not).
   - `events = recognizer.update(HandFrame(t, hands))`; for each: publish `AIR_GESTURE` with
     `ev.to_payload(self._mode)`, call local subscribers (each in try/except), record last gesture.
   - Update the snapshot (hands from `recognizer.views(t)`, spread scale, fps from stats).
   - `stats.add(detect_ms, loop_ms, t, len(hands))`.
   - Idle throttle: no hand seen for 10 s → sleep so the loop runs at most 8 fps until a hand
     appears again (saves CPU when nobody gestures). With a hand, no sleep (camera paces it).
4. On exit: close camera and landmarker, state "off", publish AIR_STATUS.
`publish` exceptions are caught and logged. `stats()` CPU: process CPU time delta
(`time.process_time()`) over wall time delta since the previous `stats()` call (or since start),
× 100, rounded; RSS from `/proc/self/statm` (Linux; 0.0 elsewhere) in MB.

### 8.3 `infrastructure/cameras.py`

- `list_cameras() -> list[CameraInfo]` — Linux: for each `/sys/class/video4linux/video*`
  (sorted by N): name = `name` file stripped; `index` file (missing → 0); driver = basename of the
  `device/driver` symlink target (missing → ""); `usable = index_file == 0 and driver != "v4l2loopback"
  and "loopback" not in name.lower()`. The sysfs root is a parameter `root: Path =
  Path("/sys/class/video4linux")` for tests. Non-Linux: `[CameraInfo(0, "Default camera", "", True)]`.
- `OpenCVCamera(info: CameraInfo, width=640, height=360, fps=30)`: `open()` → `cv2.VideoCapture(
  index, cv2.CAP_V4L2 if Linux else cv2.CAP_ANY)`, set FOURCC MJPG, width, height, fps,
  `CAP_PROP_BUFFERSIZE` 1; not opened → `RuntimeError(f"Cannot open camera {name}.")`.
  `read()` → `ok, frame = cap.read()`; not ok → None; else `cv2.flip(frame, 1)` then
  `cv2.cvtColor(…, cv2.COLOR_BGR2RGB)`. `close()` releases (idempotent). `cv2.setNumThreads(1)`
  once at open (keeps CPU free for voice).

### 8.4 `infrastructure/landmarker.py`

- `MODEL_URL` (the URL in §2), `MODEL_SIZE = 7_819_105`, `MODEL_SHA256: str | None = None`
  (the reviewer fills it in after downloading).
- `model_path() -> Path` = `data_dir() / "air" / "hand_landmarker.task"`.
- `model_ready() -> bool` — exists and size == MODEL_SIZE.
- `air_available() -> str` — "" when cv2 and mediapipe import and `model_ready()`; else a sentence:
  `"Install the air extra: pip install 'lura[air]'."` or `"Run 'lura air setup' to download the hand model."`.
- `download_model(progress: Callable[[int, int], None] | None = None) -> Path` — download `MODEL_URL`
  with `urllib.request` to `<path>.part` in 64 KB chunks, check size == MODEL_SIZE and (if set)
  sha256, then `os.replace` into place; on any failure delete the .part and raise
  `RuntimeError(sentence)`. Returns the path. Creates the folder.
- `MediaPipeLandmarker(path: Path, num_hands=2)`: builds `HandLandmarker` with RunningMode.VIDEO,
  `min_hand_detection_confidence=0.6`, `min_hand_presence_confidence=0.5`,
  `min_tracking_confidence=0.5`. `detect(frame, t_ms)` → `mp.Image(SRGB, data=frame)` →
  `detect_for_video` → `tuple[Hand]`: side = `handedness[i][0].category_name.lower()`, score =
  its `.score`, points = 21 `Point(lm.x, lm.y, lm.z)`. Because the frame is already mirrored,
  MediaPipe's label is the user's real hand. `close()` idempotent.

### 8.5 `app.py` — `lura air`

Subparser `air` (help `"Lura Air: cameras, setup, watch, bench"`), positional `action` choices
`cameras setup watch bench` (default `watch`), `--camera` (str, default ""), `--seconds` (float,
default 0 = until Ctrl+C for watch, 15 for bench). `cmd_air(args)`:
- `cameras`: print one line per `list_cameras()` entry: `/dev/video{index}  {name}  [{driver}]`
  plus `  ← used` on the one `pick_camera` picks and `  (not a camera)` when not usable.
- `setup`: if `model_ready()` print "Hand model already installed: <path>"; else download with a
  one-line progress (`\r… 42%`) and print the path. Return 1 with the message on failure.
- `watch`: refuse (print `air_available()`, return 1) when not available. Start `AirService(None,
  camera=args.camera)`, subscribe a printer: `HH:MM:SS.mmm  gesture  hand  conf  x,y  data`, and
  every 2 s print a status line from `snapshot()` (camera, hands with pose/pinch, fps). Stop on
  Ctrl+C or after `--seconds`. Always `stop()` in `finally`.
- `bench`: same start, run `--seconds` (default 15), then print `json.dumps(stats(), indent=2)`.
Add `"air": cmd_air` to `handlers`.

### 8.6 `tests/live_air.py` (not in run_all)

`import _setup`; skip (print `skipped: <why>`, exit 0) when `air_available()` is not "" or no
usable camera. Run `AirService` for 12 s (or `sys.argv[1]` seconds) with a subscriber that prints
events; print `stats()` at the end and the count of each gesture seen. Exit 0. (Real gestures are
performed by the reviewer in front of the camera.)

## 9. Tasks

1. `domain.py` (shapes, constants, geometry, poses, OneEuro, Recognizer, Stats, pick_camera).
2. `shared/events.py` names.
3. `infrastructure/cameras.py`, `infrastructure/landmarker.py`, `infrastructure/__init__.py`.
4. `application.py` (ports, AirService).
5. `__init__.py` exports.
6. `app.py` `lura air`.
7. `pyproject.toml`: under `[project.optional-dependencies]` add
   `air = ["mediapipe>=1.2.3", "opencv-python-headless>=4.10"]` after `window`, and add both to `all`.
8. `docs/ARCHITECTURE.md`: under modules, after the `assistant/` line, add
   `    ├── air/          Lura Air: webcam hand tracking (MediaPipe), gesture recognizer, gesture events`
   (this one line is the only allowed edit under docs/).
9. `tests/test_air.py` + add to CHECKS after `test_computer.py`; `tests/live_air.py`.

## 10. Acceptance checks

`tests/test_air.py` (pattern of `tests/test_computer.py`; prints `air OK`). Build hands
synthetically with a helper `make_hand(pose, cx=0.5, cy=0.5, size=0.2, side="right", score=0.95)`
that places the 21 points for: open_palm (fingers straight up), fist (tips folded near the MCPs),
point, victory, thumbs_up, thumbs_down, pinch (open palm with thumb tip at index tip). Checks:
- `classify_pose` returns each pose for its synthetic hand, at two sizes and two positions;
  `extended` and `pinch_ratio` sanity.
- Pinch hysteresis: ratio 0.30 → not started; 0.25 → `pinch_start`; 0.35 → still pinching;
  0.45 → `pinch_end` with duration.
- `double_pinch`: two short pinches 0.3 s apart → emitted once; a long first pinch (0.6 s) → not.
- Pose stability: pose changes held < 0.15 s emit nothing; held ≥ 0.15 s emit one `pose`.
- Swipes: open palm moving +0.3 in x over 0.25 s at 30 fps → exactly one `swipe_right`; −0.3 →
  `swipe_left`; vertical → `swipe_up`/`swipe_down`; diagonal (dx = dy) → none; slow (0.3 over
  1.0 s) → none; a second fast swipe 0.3 s after the first → none (cooldown), after 0.8 s → yes;
  a pinching hand moving fast → no swipe.
- Leave: hand missing 0.2 s → nothing; 0.3 s → `hand_leave`; while pinching → `pinch_end`
  (lost=True) before `hand_leave`.
- Spread: both hands pinching 0.2 apart → `spread_start`; moving to 0.3 apart →
  `spread_scale()` ≈ 1.5; one releases → `spread_end` with scale 1.5.
- Low score hands ignored; duplicate side keeps the higher score.
- `OneEuro`: a constant signal with ±0.01 noise has lower variance after filtering; first value passthrough.
- `to_payload` has exactly the keys of docs/001 §3 (+ `t` and data keys), position rounded.
- `Stats.summary` fps/p95 on known inputs.
- `list_cameras` on a fake sysfs tree in a temp dir mirroring this machine (video0 uvcvideo index 0,
  video1 uvcvideo index 1, video9 "Mobile Webcam" v4l2loopback index 0) → only video0 usable;
  `pick_camera` picks video0; `wanted="9"` → None; `wanted="integrated"` → video0.
- `AirService` with a `FakeCamera` (frames = Python objects; `None` after N reads to simulate a
  disconnect) and `FakeLandmarker` (returns scripted hands per call) and a fake clock: start →
  state "on" + AIR_STATUS published; scripted swipe → one AIR_GESTURE payload with the §3 keys;
  15 `None`s → state "lost" published, then reopened; `stop()` returns < 2 s and state "off";
  publish raising does not kill the thread; `start()` twice → one thread.
- Import hygiene: after `import lura.modules.air` in a fresh subprocess, `"cv2" not in sys.modules
  and "mediapipe" not in sys.modules`.

Commands (from the worktree root):
```bash
.venv/bin/python -m compileall -q src/
.venv/bin/python tests/test_air.py            # → air OK
.venv/bin/python tests/run_all.py             # → All checks passed.
.venv/bin/python -m lura.app air cameras      # lists video0 (← used), video1 and video9 (not a camera)
.venv/bin/python -c "import lura; print(lura.__file__)"   # → /var/www/html/old/lura-air/src/lura/__init__.py
```
**Do not run** `lura air setup|watch|bench`, `tests/live_air.py`, or anything that opens the camera
or downloads. The reviewer does those.

## 11. Out of scope

- Any desktop action, mode, resolver, overlay/hologram change, dashboard, settings fields,
  bootstrap wiring, daemon start of the camera (Stages D–F).
- Packaging (.deb/pyinstaller), README/USAGE/AGENTS/CLAUDE.md, i18n.
- Changes to `modules/computer` or any other module not listed in §3.
- Installing packages, downloading the model, opening the camera, committing, editing `docs/`
  other than the one ARCHITECTURE.md line.

## 12. Decisions

- 2026-11-20: HandLandmarker + our own pure classifiers instead of MediaPipe's GestureRecognizer:
  our gestures (pinch, swipes, double pinch, two-hand spread) are not in its canned set, the pure
  rules are unit-testable with synthetic landmarks, and it is one model (7.8 MB) instead of two.
- 2026-11-20: frames are mirrored before detection, so x grows to the user's right and
  MediaPipe's Left/Right label is the user's real hand.
- Review round 1 (Claude, after the first build). Changed by Claude, keep as is:
  `infrastructure/cameras.py` reads frames on an `air-grab` thread (capture overlaps detection);
  `MODEL_SHA256` pinned. Accepted deviation: the sysfs `virtual/` fallback for v4l2loopback.
- Live test with Mohamed (120 s, night light, camera at ~8.3 fps, detect ~35 ms, CPU ~30 %,
  RSS ~190 MB): open_palm, fist, point, thumbs_up, thumbs_down, pinch, double_pinch recognised;
  **no swipe in ~12 attempts**; spread never started (one hand kept leaving/entering at the
  frame edge); a fist with the thumb over the index read as a **15 s pinch**; a triple pinch gave
  two double_pinch; victory flickered with point. Fixes (round 2), all in `domain.py` + tests:
  1. `PINCH_REACH = 1.0`: a pinch only starts when `dist(WRIST, INDEX_TIP) > PINCH_REACH *
     dist(WRIST, INDEX_PIP)` (index not folded into a fist); an active pinch also ends when that
     reach drops below `0.85 * dist(WRIST, INDEX_PIP)` (the hand closed into a fist).
  2. `LEAVE_GRACE = 0.45` (was 0.25): at 8 fps one or two missed frames dropped the hand.
  3. Swipes: `SWIPE_WINDOW = 0.5`, `SWIPE_DIST = 0.18`; palm history uses the **raw** palm centre
     (not the One Euro output, which lags fast motion); history is kept while not pinching and
     (stable pose is open_palm, or the raw pose was open_palm within the last 0.5 s — motion blur
     turns frames into "none"). **Exit swipe:** when a track is removed for hand_leave and the
     cooldown allows, test its history (entries within SWIPE_WINDOW of its last_seen) with
     `0.7 * SWIPE_DIST` and the same axis rule; if it passes, emit the swipe (at the last palm
     position) before `hand_leave` — a fast swipe often leaves the frame.
  4. A pinch that produced a `double_pinch` never counts as the first pinch of another one.
  5. Finger hysteresis: `extended()` takes an optional previous result; a finger that was
     extended stays extended until `wrist→tip < (EXTENDED - 0.08) * wrist→pip`. The Recognizer
     keeps the previous result per track and passes it to `classify_pose`.
- Round 2 implemented by Neon (domain.py + tests only). Claude additionally: `OpenCVCamera`
  scales frames wider than 640 px down (a phone via v4l2loopback sends 1080p); `pick_camera`
  selects a named loopback camera (`--camera mobile`) but never picks one by itself.
- Live test 2 (phone camera "Mobile Webcam" via Webcamo, ~35 fps, detect ~18 ms, CPU ~97 % of
  one core — mostly scaling 1080p frames): 12 swipes in all four directions, open_palm, fist,
  point, pinch, double_pinch, victory recognised; no false long pinch.
- Live test 3 (per-frame probe): thumbs_up and thumbs_down held steadily for 6–8 s each; fist
  with the thumb over the index has index reach 0.58–0.72 while real pinches measured 0.81–1.20,
  so `PINCH_REACH` lowered 1.0 → **0.80** and the end threshold is now `PINCH_REACH_END = 0.70`.
  Spread not verified live: the second hand was mostly outside the frame (wrist y > 1.0).
- Open for later stages: CPU cost of 1080p sources (ask Webcamo for 640×360, or resize cheaper);
  spread and two-hand gestures need a framing check (e.g. a "move your hands up" hint on the face).
