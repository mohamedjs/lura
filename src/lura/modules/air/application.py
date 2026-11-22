"""AirService: camera thread lifecycle, hand tracking loop, snapshots, Commands and AirController."""

from __future__ import annotations

import logging
import os
import threading
import time
from collections import deque
from pathlib import Path
from typing import Any, Callable, Protocol

from ...shared import events
from ..brain import ToolSpec
from ..computer import Rect, WindowGrip
from .domain import (
    MODES,
    WELCOME,
    AirState,
    CameraInfo,
    GestureEvent,
    Hand,
    HandFrame,
    HandView,
    Recognizer,
    Stats,
    app_kind,
    edge_snap,
    camera_candidates,
    moved,
    ring_action,
    ring_labels,
)
from .engine import Command, InteractionEngine

log = logging.getLogger(__name__)

_MISS_LIMIT = 15
_RETRY_DELAY = 2.0
_IDLE_AFTER = 10.0
_IDLE_INTERVAL = 1.0 / 8.0
_QUEUE_MAX = 64
_POINTER_RESYNC_S = 1.0
_BOUNDS_CACHE_S = 5.0


class Camera(Protocol):
    def open(self) -> None: ...
    def read(self) -> object | None: ...
    def close(self) -> None: ...
    @property
    def name(self) -> str: ...


class Landmarker(Protocol):
    def detect(self, frame: object, t_ms: int) -> tuple[Hand, ...]: ...
    def close(self) -> None: ...


class CameraChain:
    """Camera factory over an ordered list of cameras; ``failed()`` moves to the next one."""

    def __init__(self, chain: list[CameraInfo], make: Callable[[CameraInfo], Camera]) -> None:
        self._chain = list(chain)
        self._make = make
        self._pos = 0

    @property
    def current(self) -> CameraInfo:
        return self._chain[self._pos]

    def __call__(self) -> Camera:
        return self._make(self.current)

    def failed(self) -> bool:
        """Switch to the next camera; True when there is another one to try right away."""
        if len(self._chain) < 2:
            return False
        before = self.current
        self._pos = (self._pos + 1) % len(self._chain)
        log.warning("Air camera %s not working; trying %s", before.name, self.current.name)
        return True


def _next_camera(factory: object) -> bool:
    fn = getattr(factory, "failed", None)
    return bool(fn()) if callable(fn) else False


def _rss_mb() -> float:
    """Resident set size in MB on Linux; 0.0 elsewhere."""
    try:
        fields = Path("/proc/self/statm").read_text(encoding="ascii").split()
        pages = int(fields[1])
        return round((pages * os.sysconf("SC_PAGE_SIZE")) / (1024 * 1024), 1)
    except (OSError, ValueError, IndexError, AttributeError):
        return 0.0


class AirService:
    """Runs local webcam hand tracking on a dedicated daemon thread."""

    def __init__(
        self,
        publish: Callable[[str, dict], None] | None = None,
        *,
        camera: str = "",
        camera_factory: Callable[[], Camera] | None = None,
        landmarker_factory: Callable[[], Landmarker] | None = None,
        engine_factory: Callable[[], InteractionEngine] = InteractionEngine,
        clock: Callable[[], float] = time.monotonic,
        mode: str = "universal",
        thumb_click: bool = True,
    ) -> None:
        self._publish_fn = publish
        self._thumb_click = thumb_click
        self._camera_wanted = camera
        self._camera_factory = camera_factory
        self._landmarker_factory = landmarker_factory
        self._engine_factory = engine_factory
        self._clock = clock
        self._mode = mode

        self._lock = threading.Lock()
        self._state = AirState()
        self._engine = engine_factory()
        self._engine.thumb_click = thumb_click
        self._engine_state: dict = dict(self._engine.state)
        self._stats = Stats()
        self._subscribers: list[Callable[[GestureEvent], None]] = []
        self._cmd_subscribers: list[Callable[[Command], None]] = []
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._published_status: str | None = None
        self._last_gesture = ""
        self._last_gesture_t = 0.0
        self._cpu_last_proc = time.process_time()
        self._cpu_last_wall = time.monotonic()

    @property
    def running(self) -> bool:
        with self._lock:
            return self._thread is not None and self._thread.is_alive()

    def set_thumb_click(self, on: bool) -> None:
        """Finger-gun thumb clicks on/off, live (settings ``air_thumb_click``)."""
        with self._lock:
            self._thumb_click = bool(on)
            self._engine.thumb_click = bool(on)

    def set_camera(self, name: str) -> None:
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                raise RuntimeError("stop Air first")
            self._camera_wanted = name

    def subscribe(self, fn: Callable[[GestureEvent], None]) -> None:
        with self._lock:
            self._subscribers.append(fn)

    def subscribe_commands(self, fn: Callable[[Command], None]) -> None:
        with self._lock:
            self._cmd_subscribers.append(fn)

    def snapshot(self) -> AirState:
        with self._lock:
            return self._state

    def engine_state(self) -> dict:
        with self._lock:
            return dict(self._engine_state)

    def stats(self) -> dict:
        with self._lock:
            summary = self._stats.summary()
            now_proc = time.process_time()
            now_wall = time.monotonic()
            dt = now_wall - self._cpu_last_wall
            cpu = round(((now_proc - self._cpu_last_proc) / dt) * 100.0, 1) if dt > 0.0 else 0.0
            self._cpu_last_proc = now_proc
            self._cpu_last_wall = now_wall
        return {**summary, "cpu_percent": cpu, "rss_mb": _rss_mb()}

    def start(self) -> None:
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return
            cam_factory = self._camera_factory
            lm_factory = self._landmarker_factory

        if cam_factory is None:
            from .infrastructure.cameras import OpenCVCamera, list_cameras

            chain = camera_candidates(list_cameras(), self._camera_wanted)
            if not chain:
                self._set_status("error", "No camera found.", emit=True)
                return
            cam_factory = CameraChain(chain, OpenCVCamera)

        if lm_factory is None:
            from .infrastructure.landmarker import MediaPipeLandmarker, air_available, model_path

            reason = air_available()
            if reason:
                self._set_status("error", reason, emit=True)
                return
            lm_factory = lambda: MediaPipeLandmarker(model_path())  # noqa: E731

        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return
            self._stop.clear()
            self._engine = self._engine_factory()
            self._engine.thumb_click = self._thumb_click
            self._engine_state = dict(self._engine.state)
            self._stats = Stats()
            self._last_gesture = ""
            self._last_gesture_t = 0.0
            self._cpu_last_proc = time.process_time()
            self._cpu_last_wall = time.monotonic()
            thread = threading.Thread(
                target=self._run,
                args=(cam_factory, lm_factory, self._engine),
                daemon=True,
                name="air-camera",
            )
            self._thread = thread
            thread.start()

    def stop(self) -> None:
        self._stop.set()
        with self._lock:
            thread = self._thread
        if thread is not None and thread.is_alive() and thread is not threading.current_thread():
            thread.join(timeout=2.0)
        with self._lock:
            self._thread = None

    def _emit(self, name: str, payload: dict) -> None:
        if self._publish_fn is None:
            return
        try:
            self._publish_fn(name, payload)
        except Exception:
            log.exception("Air event publish failed for %s", name)

    def _set_status(self, camera: str, detail: str = "", *, emit: bool) -> None:
        should_publish = False
        with self._lock:
            if camera != "on":
                self._state = AirState(
                    camera=camera,
                    detail=detail,
                    hands=(),
                    spread_scale=None,
                    fps=0.0,
                    last_gesture=self._last_gesture,
                    last_gesture_t=self._last_gesture_t,
                )
            else:
                self._state = AirState(
                    camera=camera,
                    detail=detail,
                    hands=self._state.hands,
                    spread_scale=self._state.spread_scale,
                    fps=self._state.fps,
                    last_gesture=self._last_gesture,
                    last_gesture_t=self._last_gesture_t,
                )
            if emit and self._published_status != camera:
                self._published_status = camera
                should_publish = True
        if should_publish:
            self._emit(events.AIR_STATUS, {"camera": camera, "detail": detail})

    def _update_snapshot(
        self,
        cam_name: str,
        hands: tuple[HandView, ...],
        spread_scale: float | None,
        detect_ms: float,
        loop_ms: float,
        t: float,
        raw_hands: int,
        eng_state: dict | None = None,
    ) -> None:
        with self._lock:
            self._stats.add(detect_ms, loop_ms, t, raw_hands)
            fps = float(self._stats.summary()["fps"])
            self._state = AirState(
                camera="on",
                detail=cam_name,
                hands=hands,
                spread_scale=spread_scale,
                fps=fps,
                last_gesture=self._last_gesture,
                last_gesture_t=self._last_gesture_t,
            )
            if eng_state is not None:
                self._engine_state = eng_state

    def _dispatch_events(self, gesture_events: list[GestureEvent]) -> None:
        if not gesture_events:
            return
        with self._lock:
            subs = list(self._subscribers)
        for ev in gesture_events:
            with self._lock:
                self._last_gesture = ev.gesture
                self._last_gesture_t = ev.t
            self._emit(events.AIR_GESTURE, ev.to_payload(self._mode))
            for fn in subs:
                try:
                    fn(ev)
                except Exception:
                    log.exception("Air gesture subscriber failed")

    def _dispatch_commands(self, cmds: list[Command]) -> None:
        if not cmds:
            return
        with self._lock:
            subs = list(self._cmd_subscribers)
        for cmd in cmds:
            for fn in subs:
                try:
                    fn(cmd)
                except Exception:
                    log.exception("Air command subscriber failed")

    @staticmethod
    def _safe_close(obj: object | None) -> None:
        if obj is None:
            return
        try:
            obj.close()  # type: ignore[attr-defined]
        except Exception:
            log.exception("Closing %s failed", type(obj).__name__)

    def _run(
        self,
        cam_factory: Callable[[], Camera],
        lm_factory: Callable[[], Landmarker],
        engine: InteractionEngine,
    ) -> None:
        recognizer = Recognizer()
        cam: Camera | None = None
        landmarker: Landmarker | None = None
        last_ts_ms: int | None = None
        last_hand_t: float | None = None
        fatal = False

        try:
            try:
                landmarker = lm_factory()
            except Exception as exc:
                log.exception("Could not initialize hand landmarker")
                self._set_status("error", str(exc), emit=True)
                fatal = True
                return

            while not self._stop.is_set():
                self._set_status("starting", "", emit=False)
                try:
                    cam = cam_factory()
                    cam.open()
                except Exception as exc:
                    log.warning("Camera open failed: %s", exc)
                    self._safe_close(cam)
                    cam = None
                    self._set_status("lost", str(exc), emit=True)
                    if not _next_camera(cam_factory):
                        self._stop.wait(_RETRY_DELAY)
                    continue

                cam_name = cam.name
                self._set_status("on", cam_name, emit=True)
                misses = 0

                while not self._stop.is_set():
                    loop_start = time.perf_counter()
                    try:
                        frame = cam.read()
                    except Exception:
                        log.exception("Camera read failed")
                        frame = None

                    if frame is None:
                        misses += 1
                        if misses >= _MISS_LIMIT:
                            self._set_status("lost", f"{cam_name} disconnected.", emit=True)
                            self._safe_close(cam)
                            cam = None
                            if not _next_camera(cam_factory):
                                self._stop.wait(_RETRY_DELAY)
                            break
                        continue
                    misses = 0

                    t = self._clock()
                    ts_ms = int(t * 1000)
                    if last_ts_ms is not None and ts_ms <= last_ts_ms:
                        ts_ms = last_ts_ms + 1
                    last_ts_ms = ts_ms

                    det_start = time.perf_counter()
                    try:
                        hands = landmarker.detect(frame, ts_ms)
                    except Exception:
                        log.exception("Hand detection failed")
                        self._stop.wait(0.05)
                        continue
                    detect_ms = (time.perf_counter() - det_start) * 1000.0

                    if hands or last_hand_t is None:
                        last_hand_t = t

                    try:
                        evs = recognizer.update(HandFrame(t, hands))
                    except Exception:
                        log.exception("Gesture recognizer update failed")
                        continue

                    try:
                        cmds = engine.update(t, hands)
                        eng_state: dict | None = dict(engine.state)
                    except Exception:
                        log.exception("InteractionEngine update failed")
                        cmds = []
                        eng_state = None

                    self._dispatch_events(evs)
                    self._dispatch_commands(cmds)
                    loop_ms = (time.perf_counter() - loop_start) * 1000.0
                    self._update_snapshot(
                        cam_name,
                        recognizer.views(t),
                        recognizer.spread_scale(),
                        detect_ms,
                        loop_ms,
                        t,
                        len(hands),
                        eng_state,
                    )

                    # Idle throttle: slow to at most 8 fps after 10 s without any hand.
                    if not hands and (t - last_hand_t) >= _IDLE_AFTER:
                        remaining = _IDLE_INTERVAL - (time.perf_counter() - loop_start)
                        if remaining > 0.0:
                            self._stop.wait(remaining)
        except Exception:
            log.exception("Air camera thread crashed unexpectedly")
        finally:
            self._safe_close(cam)
            self._safe_close(landmarker)
            engine.reset()
            with self._lock:
                self._engine_state = dict(engine.state)
            if not fatal:
                self._set_status("off", "", emit=True)


AIR_MODE_TOOL = ToolSpec(
    "air_mode",
    "Switch Lura Air (hand gesture control) mode, or report it. Modes: universal "
    "(command ring follows the window under the cursor), window, workspace, pointer, "
    "files, media, developer, presentation (ring changes slides), ai (ring starts talking).",
    {
        "type": "object",
        "properties": {
            "mode": {
                "type": "string",
                "enum": [*MODES, "status"],
                "description": "The mode, or status to report it.",
            },
        },
        "required": ["mode"],
    },
)


class DesktopPort(Protocol):
    def run(self, name: str, args: dict | None = None, *, confirmed: bool = False) -> Any: ...
    def grip(self, window: str | int = "active") -> WindowGrip: ...
    def place(self, grip: WindowGrip, rect: Rect) -> None: ...
    def is_maximized(self, window: str | int = "active") -> bool: ...
    def screen_bounds(self, monitor: str = "all") -> Rect: ...
    def active_app(self) -> tuple[int, str, str] | None: ...
    def window_at(self, x: int, y: int) -> tuple[int, str, str] | None: ...
    def pointer(self) -> tuple[int, int]: ...
    def pointer_to(self, x: int, y: int) -> None: ...


_DEFAULT_ENGINE_STATE: dict[str, Any] = {
    "engaged": False,
    "zone": "none",
    "phase": "idle",
    "ring_hover": "",
    "grab_progress": 0.0,
}


class AirController:
    """Executes InteractionEngine Commands against the desktop on its own thread."""

    def __init__(
        self,
        air: AirService,
        desktop: DesktopPort,
        publish: Callable[[str, dict], None] | None = None,
        clock: Callable[[], float] = time.monotonic,
        app_kinds: Callable[[], dict[str, str]] = lambda: {},
    ) -> None:
        self._air = air
        self._desktop = desktop
        self._publish_fn = publish
        self._clock = clock
        self._kinds = app_kinds

        self._lock = threading.RLock()
        self._q_cond = threading.Condition()
        self._queue: deque[Command] = deque()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._subscribed = False

        self._mode: str = "universal"
        self._welcomed: bool = False
        self._last_action: str = ""

        self._cursor: tuple[float, float] | None = None
        self._last_move_t: float = -1e9
        self._bounds_cache: tuple[float, Rect] | None = None

        self._ring_target: tuple[int, str, str] | None = None
        self._ring_labels: tuple[str, str, str, str] | None = None

        self._grab: WindowGrip | None = None
        self._grab_dx: float = 0.0
        self._grab_dy: float = 0.0
        self._last_place: Rect | None = None
        self._grab_target: str = ""
        self._pressed: bool = False

    @property
    def mode(self) -> str:
        with self._lock:
            return self._mode

    def _clear_active(self) -> None:
        if self._pressed:
            self._pressed = False
            try:
                self._desktop.run("pointer_up", {"button": "left"})
            except Exception:
                log.warning("Air pointer_up failed", exc_info=True)
        self._ring_target = None
        self._ring_labels = None
        self._grab = None
        self._grab_dx = 0.0
        self._grab_dy = 0.0
        self._last_place = None
        self._grab_target = ""

    def set_mode(self, mode: str) -> None:
        if mode not in MODES:
            raise ValueError(f"Unknown Air mode: {mode!r}")
        with self._lock:
            self._clear_active()
            self._mode = mode
        self._emit(events.AIR_MODE, {"mode": mode})
        self._feedback("mode", True, f"Air mode: {mode}.")

    # ── ToolProvider ────────────────────────────────────────────────────────

    def tool_specs(self) -> list[ToolSpec]:
        return [AIR_MODE_TOOL]

    def owns(self, name: str) -> bool:
        return name == AIR_MODE_TOOL.name

    def call(self, name: str, arguments: dict) -> str:
        mode = str((arguments or {}).get("mode", "")).strip().lower()
        if mode == "status":
            running = "on" if getattr(self._air, "running", False) else "off"
            return f"Air is {running}, mode {self.mode}."
        if mode not in MODES:
            return f"Unknown mode '{mode}'. Choose one of: {', '.join(MODES)}, status."
        self.set_mode(mode)
        return f"Air mode is now {self.mode}."

    # ── lifecycle & command queue ───────────────────────────────────────────

    def start(self) -> None:
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return
            if not self._subscribed:
                self._air.subscribe_commands(self._enqueue)
                self._subscribed = True
            self._stop.clear()
            thread = threading.Thread(
                target=self._run,
                daemon=True,
                name="air-control",
            )
            self._thread = thread
            thread.start()

    def stop(self) -> None:
        self._stop.set()
        with self._q_cond:
            self._q_cond.notify_all()
        with self._lock:
            thread = self._thread
        if thread is not None and thread.is_alive() and thread is not threading.current_thread():
            thread.join(timeout=1.0)
        with self._lock:
            self._thread = None
            self._clear_active()
            self._cursor = None
        with self._q_cond:
            self._queue.clear()

    def _enqueue(self, cmd: Command) -> None:
        with self._q_cond:
            if (
                cmd.kind in ("cursor", "grab_move")
                and self._queue
                and self._queue[-1].kind == cmd.kind
            ):
                prev = self._queue[-1]
                self._queue[-1] = Command(
                    cmd.kind,
                    cmd.t,
                    dx=prev.dx + cmd.dx,
                    dy=prev.dy + cmd.dy,
                )
            elif (
                cmd.kind == "scroll"
                and self._queue
                and self._queue[-1].kind == "scroll"
                and (self._queue[-1].steps > 0) == (cmd.steps > 0)
            ):
                # A flick's glide sends a few steps every frame; one wheel call for what is waiting.
                prev = self._queue[-1]
                self._queue[-1] = Command("scroll", cmd.t, steps=prev.steps + cmd.steps, text=prev.text)
            else:
                if len(self._queue) >= _QUEUE_MAX:
                    self._queue.popleft()
                self._queue.append(cmd)
            self._q_cond.notify()

    def _run(self) -> None:
        while not self._stop.is_set():
            with self._q_cond:
                while not self._queue and not self._stop.is_set():
                    self._q_cond.wait(timeout=0.1)
                if self._stop.is_set():
                    break
                cmd = self._queue.popleft()
            try:
                self.process(cmd)
            except Exception:
                log.exception("AirController.process failed for %s", cmd.kind)

    def state(self) -> dict:
        fn = getattr(self._air, "engine_state", None)
        est = fn() if callable(fn) else dict(_DEFAULT_ENGINE_STATE)
        with self._lock:
            if self._grab_target:
                target = self._grab_target
            elif self._ring_target is not None:
                target = self._ring_target[2] or self._ring_target[1]
            else:
                target = ""
            return {
                "mode": self._mode,
                "engaged": bool(est.get("engaged", False)),
                "zone": str(est.get("zone", "none")),
                "phase": str(est.get("phase", "idle")),
                "ring_hover": str(est.get("ring_hover", "")),
                "grab_progress": float(est.get("grab_progress", 0.0)),
                "ring_labels": self._ring_labels,
                "target": target,
            }

    # ── desktop & event helpers ─────────────────────────────────────────────

    def _emit(self, name: str, payload: dict) -> None:
        if self._publish_fn is None:
            return
        try:
            self._publish_fn(name, payload)
        except Exception:
            log.exception("Air event publish failed for %s", name)

    def _feedback(self, action: str, ok: bool, message: str) -> None:
        self._last_action = action
        log.info("air action %s ok=%s: %s", action, ok, message)
        self._emit(events.AIR_ACTION, {
            "action": action,
            "ok": ok,
            "message": message,
        })

    def _read_pointer(self) -> tuple[float, float]:
        try:
            px, py = self._desktop.pointer()
            return (float(px), float(py))
        except Exception:
            log.warning("Could not read pointer position", exc_info=True)
            return (0.0, 0.0)

    def _ensure_cursor(self) -> tuple[float, float]:
        if self._cursor is None:
            self._cursor = self._read_pointer()
        return self._cursor

    def _screen_bounds(self) -> Rect | None:
        now = self._clock()
        if self._bounds_cache is not None and (now - self._bounds_cache[0]) < _BOUNDS_CACHE_S:
            return self._bounds_cache[1]
        try:
            b = self._desktop.screen_bounds()
        except Exception:
            return self._bounds_cache[1] if self._bounds_cache is not None else None
        self._bounds_cache = (now, b)
        return b

    def _window_at(self, x: int, y: int) -> tuple[int, str, str] | None:
        try:
            return self._desktop.window_at(x, y)
        except Exception:
            return None

    def _kind_for(self, target: tuple[int, str, str] | None) -> str:
        if target is None:
            return "other"
        _, app, title = target
        try:
            overrides = self._kinds()
        except Exception:
            overrides = None
        return app_kind(app, title, overrides)

    # ── Command execution ───────────────────────────────────────────────────

    def process(self, cmd: Command) -> None:
        with self._lock:
            k = cmd.kind

            if k == "engage":
                self._cursor = self._read_pointer()
                self._last_move_t = cmd.t
                self._bounds_cache = None
                self._screen_bounds()
                if not self._welcomed:
                    self._welcomed = True
                    self._feedback("welcome", True, WELCOME)
                else:
                    self._feedback("engage", True, "Air on.")
                return

            if k == "disengage":
                self._clear_active()
                self._cursor = None
                self._feedback("disengage", True, "Air off.")
                return

            if k == "hint":
                self._feedback("hint", True, cmd.text)
                return

            if k == "cursor":
                if self._cursor is None or (cmd.t - self._last_move_t) > _POINTER_RESYNC_S:
                    self._cursor = self._read_pointer()
                cx = self._cursor[0] + cmd.dx
                cy = self._cursor[1] + cmd.dy
                b = self._screen_bounds()
                if b is not None and b.width > 0 and b.height > 0:
                    cx = max(float(b.x), min(float(b.x + b.width - 1), cx))
                    cy = max(float(b.y), min(float(b.y + b.height - 1), cy))
                self._cursor = (cx, cy)
                self._last_move_t = cmd.t
                try:
                    self._desktop.pointer_to(round(cx), round(cy))
                except Exception:
                    log.warning("Air pointer_to failed", exc_info=True)
                return

            if k == "ring_open":
                cx, cy = self._ensure_cursor()
                target = self._window_at(round(cx), round(cy))
                self._ring_target = target
                kind = self._kind_for(target)
                labels = ring_labels(self._mode, kind)
                self._ring_labels = labels
                self._feedback("ring", True, " / ".join(lbl for lbl in labels if lbl))
                return

            if k == "ring_hover":
                return

            if k == "ring_cancel":
                self._ring_labels = None
                self._ring_target = None
                return

            if k == "ring_select":
                target = self._ring_target
                self._ring_labels = None
                self._ring_target = None
                self._run_ring_action(cmd.direction, target)
                return

            if k == "grab_start":
                self._ring_labels = None
                self._ring_target = None
                cx, cy = self._ensure_cursor()
                target = self._window_at(round(cx), round(cy))
                if target is None:
                    self._feedback("window_move", False, "No window under the cursor.")
                    return
                wid, app, title = target
                self._desktop.run("window_focus", {"window": wid})
                try:
                    grip = self._desktop.grip(wid)
                except Exception as exc:
                    self._feedback("window_move", False, str(exc))
                    return
                self._grab = grip
                self._grab_dx = 0.0
                self._grab_dy = 0.0
                self._last_place = None
                self._grab_target = title or app
                return

            if k == "grab_move":
                if self._grab is None:
                    return
                self._grab_dx += cmd.dx
                self._grab_dy += cmd.dy
                rect = self._grab.start.translate(round(self._grab_dx), round(self._grab_dy))
                ref = self._last_place if self._last_place is not None else self._grab.start
                if moved(rect, ref):
                    try:
                        self._desktop.place(self._grab, rect)
                    except Exception as exc:
                        self._grab = None
                        self._grab_dx = 0.0
                        self._grab_dy = 0.0
                        self._last_place = None
                        self._grab_target = ""
                        self._feedback("window_move", False, str(exc))
                        return
                    self._last_place = rect
                return

            if k == "grab_end":
                if self._grab is None:
                    return
                grip = self._grab
                last = self._last_place or grip.start
                self._grab = None
                self._grab_dx = 0.0
                self._grab_dy = 0.0
                self._last_place = None
                self._grab_target = ""
                snap = edge_snap(last, grip.bounds)
                if snap is not None:
                    r = self._desktop.run("window_snap", {"window": grip.id, "where": snap})
                    self._feedback("window_snap", r.ok, r.message)
                else:
                    label = grip.title or f"window {grip.id}"
                    self._feedback("window_move", True, f"Moved {label}.")
                return

            if k == "click":
                r = self._desktop.run("pointer_click", {"button": "left", "count": 1})
                self._feedback("pointer_click", r.ok, r.message)
                return

            if k == "right_click":
                r = self._desktop.run("pointer_click", {"button": "right", "count": 1})
                self._feedback("pointer_click", r.ok, r.message)
                return

            if k == "press":
                r = self._desktop.run("pointer_down", {"button": "left"})
                self._pressed = r.ok
                self._feedback("pointer_down", r.ok, r.message)
                return

            if k == "release":
                if not self._pressed:
                    return
                self._pressed = False
                r = self._desktop.run("pointer_up", {"button": "left"})
                self._feedback("pointer_up", r.ok, r.message)
                return

            if k == "swipe":
                target = "next" if cmd.direction == "right" else "prev"
                r = self._desktop.run("workspace_switch", {"target": target})
                self._feedback("workspace_switch", r.ok, r.message)
                return

            if k == "scroll":
                if cmd.steps == 0:
                    return
                direction = "up" if cmd.steps > 0 else "down"
                r = self._desktop.run("pointer_scroll", {"direction": direction, "amount": abs(cmd.steps)})
                self._feedback("pointer_scroll", r.ok, r.message)
                return

            if k == "intent":
                cx, cy = self._ensure_cursor()
                hit = self._window_at(round(cx), round(cy))
                app, title = (hit[1], hit[2]) if hit is not None else ("", "")
                self._emit(events.AIR_INTENT, {
                    "intent": cmd.text,
                    "mode": self._mode,
                    "gesture": "thumbs",
                    "app": app,
                    "title": title,
                })
                self._feedback("intent", True, f"Intent {cmd.text}.")

    def _run_ring_action(self, direction: str, target: tuple[int, str, str] | None) -> None:
        kind = self._kind_for(target)
        action, args = ring_action(direction, self._mode, kind)

        if action == "ignore":
            self._feedback("ignore", False, f"{direction} does nothing in {self._mode} mode.")
            return

        if action == "intent":
            intent = str(args.get("intent", "talk"))
            app, title = (target[1], target[2]) if target is not None else ("", "")
            self._emit(events.AIR_INTENT, {
                "intent": intent,
                "mode": self._mode,
                "gesture": f"ring_{direction}",
                "app": app,
                "title": title,
            })
            self._feedback("intent", True, f"Intent {intent}.")
            return

        if action == "click":
            r = self._desktop.run("pointer_click", {"button": "left"})
            self._feedback("pointer_click", r.ok, r.message)
            return

        if action == "toggle_maximize":
            if target is None:
                self._feedback("window_maximize", False, "No window under the cursor.")
                return
            wid = target[0]
            self._desktop.run("window_focus", {"window": wid})
            try:
                act = "window_restore" if self._desktop.is_maximized(wid) else "window_maximize"
            except Exception as exc:
                self._feedback("window_maximize", False, str(exc))
                return
            r = self._desktop.run(act, {"window": wid})
            self._feedback(act, r.ok, r.message)
            return

        if action in ("window_snap", "window_minimize"):
            if target is None:
                self._feedback(action, False, "No window under the cursor.")
                return
            wid = target[0]
            self._desktop.run("window_focus", {"window": wid})
            r = self._desktop.run(action, {**args, "window": wid})
            self._feedback(action, r.ok, r.message)
            return

        if action == "press_keys":
            if target is not None:
                self._desktop.run("window_focus", {"window": target[0]})
            r = self._desktop.run("press_keys", dict(args))
            self._feedback("press_keys", r.ok, r.message)
            return

        r = self._desktop.run(action, dict(args))
        self._feedback(action, r.ok, r.message)
