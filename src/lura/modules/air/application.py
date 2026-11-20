"""AirService: camera thread lifecycle, hand tracking loop, snapshots and gesture publishing."""

from __future__ import annotations

import logging
import math
import os
import queue
import threading
import time
from pathlib import Path
from typing import Any, Callable, Protocol

from ...shared import events
from ..brain import ToolSpec
from ..computer import ActionError, Rect, WindowGrip
from .domain import (
    ACTION_MIN_CONF,
    CONTROL_HZ,
    DRAG_START,
    MODES,
    POINTER_SMOOTH,
    AirState,
    Arming,
    GestureEvent,
    Hand,
    HandFrame,
    HandView,
    Recognizer,
    Stats,
    app_kind,
    drag_rect,
    edge_snap,
    hand_to_screen,
    moved,
    pick_camera,
    resolve,
    spread_rect,
)

log = logging.getLogger(__name__)

_MISS_LIMIT = 15
_RETRY_DELAY = 2.0
_IDLE_AFTER = 10.0
_IDLE_INTERVAL = 1.0 / 8.0


class Camera(Protocol):
    def open(self) -> None: ...
    def read(self) -> object | None: ...
    def close(self) -> None: ...
    @property
    def name(self) -> str: ...


class Landmarker(Protocol):
    def detect(self, frame: object, t_ms: int) -> tuple[Hand, ...]: ...
    def close(self) -> None: ...


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
        clock: Callable[[], float] = time.monotonic,
        mode: str = "universal",
    ) -> None:
        self._publish_fn = publish
        self._camera_wanted = camera
        self._camera_factory = camera_factory
        self._landmarker_factory = landmarker_factory
        self._clock = clock
        self._mode = mode

        self._lock = threading.Lock()
        self._state = AirState()
        self._stats = Stats()
        self._subscribers: list[Callable[[GestureEvent], None]] = []
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

    def set_camera(self, name: str) -> None:
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                raise RuntimeError("stop Air first")
            self._camera_wanted = name

    def subscribe(self, fn: Callable[[GestureEvent], None]) -> None:
        with self._lock:
            self._subscribers.append(fn)

    def snapshot(self) -> AirState:
        with self._lock:
            return self._state

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

            info = pick_camera(list_cameras(), self._camera_wanted)
            if info is None:
                self._set_status("error", "No camera found.", emit=True)
                return
            cam_factory = lambda info=info: OpenCVCamera(info)  # noqa: E731

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
            self._stats = Stats()
            self._last_gesture = ""
            self._last_gesture_t = 0.0
            self._cpu_last_proc = time.process_time()
            self._cpu_last_wall = time.monotonic()
            thread = threading.Thread(
                target=self._run,
                args=(cam_factory, lm_factory),
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

                    self._dispatch_events(evs)
                    loop_ms = (time.perf_counter() - loop_start) * 1000.0
                    self._update_snapshot(
                        cam_name,
                        recognizer.views(t),
                        recognizer.spread_scale(),
                        detect_ms,
                        loop_ms,
                        t,
                        len(hands),
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
            if not fatal:
                self._set_status("off", "", emit=True)


_DRAG_MODES = frozenset({"universal", "window", "workspace", "files", "media", "developer"})
_POINTER_HOLD = 0.3
_POINTER_MIN_PX = 3
_EPS = 1e-9

AIR_MODE_TOOL = ToolSpec(
    "air_mode",
    "Switch Lura Air (hand gesture control) mode, or report it. Modes: universal "
    "(gestures follow the active app), window, workspace, pointer (hand moves the mouse), "
    "files, media, developer, presentation (swipes change slides), ai (gestures go to you).",
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
    def run(self, name: str, args: dict | None = None, *, confirmed: bool = False) -> Any: ...  # ActionResult
    def grip(self, window: str | int = "active") -> WindowGrip: ...
    def place(self, grip: WindowGrip, rect: Rect) -> None: ...
    def is_maximized(self, window: str | int = "active") -> bool: ...
    def screen_bounds(self) -> Rect: ...
    def active_app(self) -> tuple[int, str, str] | None: ...
    def pointer_to(self, x: int, y: int) -> None: ...


class AirController:
    """Translates Air gesture events and hand snapshots into desktop actions on its own thread."""

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
        self._queue: queue.Queue[GestureEvent] = queue.Queue(maxsize=64)
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._subscribed = False

        self._arming = Arming()
        self._mode: str = "universal"
        self._pending: tuple[str, tuple[float, float], float] | None = None
        self._drag: tuple[str, WindowGrip, tuple[float, float]] | None = None
        self._resize: WindowGrip | None = None
        self._last_place: Rect | None = None
        self._last_action: str = ""
        self._pointer: tuple[float, float] | None = None
        self._pointer_last: tuple[int, int] | None = None
        self._pointer_down: bool = False
        self._pointer_pinch: tuple[str, tuple[float, float], float] | None = None
        self._pointer_bounds: Rect | None = None

    @property
    def mode(self) -> str:
        with self._lock:
            return self._mode

    def set_mode(self, mode: str) -> None:
        if mode not in MODES:
            raise ValueError(f"Unknown Air mode: {mode!r}")
        with self._lock:
            self._pending = None
            self._drag = None
            self._resize = None
            self._last_place = None
            self._pointer_pinch = None
            self._pointer = None
            self._pointer_last = None
            if self._pointer_down:
                self._release_pointer()
            self._mode = mode
            if mode == "pointer":
                try:
                    self._pointer_bounds = self._desktop.screen_bounds()
                except Exception:
                    log.warning("Could not read screen bounds for pointer mode", exc_info=True)
                    self._pointer_bounds = None
            else:
                self._pointer_bounds = None
        self._emit(events.AIR_MODE, {"mode": mode})
        self._feedback("mode", True, f"Air mode: {mode}.")

    def next_mode(self) -> None:
        with self._lock:
            idx = MODES.index(self._mode)
            nxt = MODES[(idx + 1) % len(MODES)]
        self.set_mode(nxt)

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

    def start(self) -> None:
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return
            if not self._subscribed:
                self._air.subscribe(self._enqueue)
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
        with self._lock:
            thread = self._thread
        if thread is not None and thread.is_alive() and thread is not threading.current_thread():
            thread.join(timeout=1.0)
        with self._lock:
            self._thread = None
            self._pending = None
            self._drag = None
            self._resize = None
            self._last_place = None
            self._pointer_pinch = None
            self._pointer = None
            self._pointer_last = None
            if self._pointer_down:
                self._release_pointer()
        while True:
            try:
                self._queue.get_nowait()
            except queue.Empty:
                break

    def _enqueue(self, ev: GestureEvent) -> None:
        try:
            self._queue.put_nowait(ev)
        except queue.Full:
            try:
                self._queue.get_nowait()
            except queue.Empty:
                pass
            try:
                self._queue.put_nowait(ev)
            except queue.Full:
                pass

    def _run(self) -> None:
        period = 1.0 / CONTROL_HZ
        while not self._stop.is_set():
            ev: GestureEvent | None = None
            try:
                ev = self._queue.get(timeout=period)
            except queue.Empty:
                pass
            if self._stop.is_set():
                break
            if ev is not None:
                try:
                    self.process(ev)
                except Exception:
                    log.exception("AirController.process failed")
            if self._stop.is_set():
                break
            try:
                self.tick(self._clock())
            except Exception:
                log.exception("AirController.tick failed")

    def state(self) -> dict:
        t = self._clock()
        with self._lock:
            return {
                "mode": self._mode,
                "armed": self._arming.armed(t),
                "dragging": self._drag is not None,
                "resizing": self._resize is not None,
                "last_action": self._last_action,
            }

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

    def _release_pointer(self) -> None:
        self._pointer_down = False
        try:
            self._desktop.run("pointer_up", {"button": "left"})
        except Exception:
            log.warning("Releasing pointer button failed", exc_info=True)

    def _active_app(self) -> tuple[int, str, str] | None:
        try:
            return self._desktop.active_app()
        except Exception:
            return None

    def _kind(self) -> str:
        info = self._active_app()
        if info is None:
            return "other"
        _, app, title = info
        try:
            overrides = self._kinds()
        except Exception:
            overrides = None
        return app_kind(app, title, overrides)

    def process(self, ev: GestureEvent) -> None:
        with self._lock:
            self._arming.on_event(ev)
            g = ev.gesture
            if not self._arming.armed(ev.t):
                self._pending = None
                self._pointer_pinch = None
                if self._pointer_down:
                    self._release_pointer()
                if g == "pinch_end" and self._drag is not None and self._drag[0] == ev.hand:
                    self._drag = None
                    self._last_place = None
                elif g == "spread_end" and self._resize is not None:
                    self._resize = None
                    self._last_place = None
                return

            mode = self._mode

            # 1. Continuous gestures: Stage D1 drag/resize or pointer mode pinch.
            if mode in _DRAG_MODES:
                if g == "pinch_start":
                    if self._drag is None and self._resize is None:
                        self._pending = (ev.hand, (ev.x, ev.y), ev.t)
                    return

                if g == "pinch_end":
                    if self._drag is not None and self._drag[0] == ev.hand:
                        _, grip, _ = self._drag
                        last = self._last_place or grip.start
                        self._drag = None
                        self._last_place = None
                        if not ev.data.get("lost"):
                            snap = edge_snap(last, grip.bounds)
                            if snap is not None:
                                r = self._desktop.run("window_snap", {"window": grip.id, "where": snap})
                                self._feedback("window_snap", r.ok, r.message)
                            else:
                                label = grip.title or f"window {grip.id}"
                                self._feedback("window_move", True, f"Moved {label}.")
                    elif self._pending is not None and self._pending[0] == ev.hand:
                        self._pending = None
                    return

                if g == "spread_start":
                    self._pending = None
                    if self._drag is not None:
                        grip = self._drag[1]
                        self._drag = None
                    else:
                        try:
                            grip = self._desktop.grip()
                        except Exception as exc:
                            self._feedback("window_resize", False, str(exc))
                            return
                    self._resize = grip
                    self._last_place = grip.start
                    return

                if g == "spread_end":
                    if self._resize is not None:
                        grip = self._resize
                        self._resize = None
                        self._last_place = None
                        label = grip.title or f"window {grip.id}"
                        self._feedback("window_resize", True, f"Resized {label}.")
                    return

            elif mode == "pointer":
                if g == "pinch_start":
                    if not self._pointer_down:
                        self._pointer_pinch = (ev.hand, (ev.x, ev.y), ev.t)
                    return

                if g == "pinch_end":
                    if self._pointer_down:
                        self._pointer_pinch = None
                        self._pointer_down = False
                        r = self._desktop.run("pointer_up", {"button": "left"})
                        self._feedback("pointer_up", r.ok, r.message)
                    elif self._pointer_pinch is not None and self._pointer_pinch[0] == ev.hand:
                        _, pos0, t0 = self._pointer_pinch
                        self._pointer_pinch = None
                        dur = ev.t - t0
                        moved_dist = math.hypot(ev.x - pos0[0], ev.y - pos0[1])
                        if not ev.data.get("lost") and dur < _POINTER_HOLD and moved_dist < DRAG_START:
                            r = self._desktop.run("pointer_click", {"button": "left"})
                            self._feedback("pointer_click", r.ok, r.message)
                    return

            if g == "double_pinch":
                self._pending = None
                self._pointer_pinch = None
                if self._drag is not None and self._drag[0] == ev.hand and self._last_place is None:
                    self._drag = None

            # 2. Discrete gestures via the pure resolver.
            if self._drag is not None or self._resize is not None or ev.confidence < ACTION_MIN_CONF:
                return

            kind = self._kind() if (mode == "universal" and g in ("swipe_left", "swipe_right")) else "other"
            res = resolve(g, ev.data.get("pose", ""), mode, kind)

            if res.kind == "mode":
                self.next_mode()
                return

            if res.kind == "intent":
                info = self._active_app()
                app, title = (info[1], info[2]) if info is not None else ("", "")
                self._emit(events.AIR_INTENT, {
                    "intent": res.intent,
                    "mode": mode,
                    "gesture": g,
                    "app": app,
                    "title": title,
                })
                self._feedback("intent", True, f"Intent {res.intent}.")
                return

            if res.kind == "action":
                if res.action == "toggle_maximize":
                    try:
                        action = "window_restore" if self._desktop.is_maximized() else "window_maximize"
                    except Exception as exc:
                        self._feedback("window_maximize", False, str(exc))
                        return
                    r = self._desktop.run(action)
                    self._feedback(action, r.ok, r.message)
                else:
                    r = self._desktop.run(res.action, dict(res.args))
                    self._feedback(res.action, r.ok, r.message)
                return

            if res.kind == "ignore" and res.reason:
                self._feedback("ignore", False, res.reason)

    def tick(self, t: float) -> None:
        with self._lock:
            if self._arming.consume_disarm(t):
                self._drag = None
                self._resize = None
                self._pending = None
                self._last_place = None
                self._pointer_pinch = None
                self._pointer = None
                self._pointer_last = None
                if self._pointer_down:
                    self._release_pointer()
                self._feedback("disarm", True, "Air control paused.")
                return

            snap = self._air.snapshot()

            if self._mode == "pointer":
                if not self._arming.armed(t) or self._drag is not None or self._resize is not None:
                    return
                if (
                    self._pointer_pinch is not None
                    and not self._pointer_down
                    and (t - self._pointer_pinch[2]) >= _POINTER_HOLD - _EPS
                ):
                    self._pointer_down = True
                    r = self._desktop.run("pointer_down", {"button": "left"})
                    self._feedback("pointer_down", r.ok, r.message)
                if snap.hands:
                    h = snap.hands[0]
                    b = self._pointer_bounds
                    if b is None:
                        try:
                            b = self._desktop.screen_bounds()
                            self._pointer_bounds = b
                        except Exception:
                            b = None
                    if b is not None and b.width > 0 and b.height > 0:
                        tx, ty = hand_to_screen(h.x, h.y, b)
                        if self._pointer is None:
                            px, py = float(tx), float(ty)
                        else:
                            px = self._pointer[0] + POINTER_SMOOTH * (tx - self._pointer[0])
                            py = self._pointer[1] + POINTER_SMOOTH * (ty - self._pointer[1])
                        self._pointer = (px, py)
                        rx, ry = round(px), round(py)
                        if (
                            self._pointer_last is None
                            or math.hypot(rx - self._pointer_last[0], ry - self._pointer_last[1])
                            >= _POINTER_MIN_PX
                        ):
                            try:
                                self._desktop.pointer_to(rx, ry)
                            except Exception:
                                log.warning("Air pointer_to failed", exc_info=True)
                            else:
                                self._pointer_last = (rx, ry)
                return

            if self._pending is not None and self._drag is None:
                side, hand0, _ = self._pending
                h = next((hv for hv in snap.hands if hv.side == side), None)
                if h is None or not h.pinching:
                    self._pending = None
                elif math.hypot(h.x - hand0[0], h.y - hand0[1]) >= DRAG_START:
                    self._pending = None
                    try:
                        grip = self._desktop.grip()
                    except ActionError as exc:
                        self._feedback("window_move", False, str(exc))
                        return
                    except Exception as exc:
                        log.warning("Air grip failed: %s", exc)
                        self._feedback("window_move", False, str(exc))
                        return
                    # Measure from where the hand is now, not where the pinch began: the hand has
                    # already travelled DRAG_START, and that would make the window jump ~180 px.
                    self._drag = (side, grip, (h.x, h.y))
                    self._last_place = None

            if self._drag is not None:
                side, grip, hand0 = self._drag
                h = next((hv for hv in snap.hands if hv.side == side), None)
                if h is not None and h.pinching:
                    rect = drag_rect(grip.start, hand0, (h.x, h.y), grip.bounds)
                    if self._last_place is None or moved(rect, self._last_place):
                        try:
                            self._desktop.place(grip, rect)
                        except Exception as exc:
                            self._drag = None
                            self._last_place = None
                            self._feedback("window_move", False, str(exc))
                            return
                        self._last_place = rect

            if self._resize is not None:
                scale = snap.spread_scale
                if scale is not None:
                    rect = spread_rect(self._resize.start, scale)
                    if self._last_place is None or moved(rect, self._last_place):
                        try:
                            self._desktop.place(self._resize, rect)
                        except Exception as exc:
                            self._resize = None
                            self._last_place = None
                            self._feedback("window_resize", False, str(exc))
                            return
                        self._last_place = rect
