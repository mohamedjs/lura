"""Camera discovery (Linux sysfs) and OpenCV capture adapter."""

from __future__ import annotations

import logging
import os
import threading
import time
from pathlib import Path
from typing import Any

from ....shared.platform import IS_LINUX, IS_MAC
from ..domain import CameraInfo
from .macos_camera import authorization as camera_authorization

log = logging.getLogger(__name__)

_DEFAULT_SYSFS = Path("/sys/class/video4linux")


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace").strip()
    except OSError:
        return ""


def _read_driver(entry: Path) -> str:
    link = entry / "device" / "driver"
    try:
        if link.is_symlink():
            return Path(os.readlink(link)).name
        if link.exists():
            return link.resolve().name
    except OSError:
        pass
    # v4l2loopback nodes live under /sys/devices/virtual/video4linux with no device/ symlink.
    try:
        resolved = entry.resolve()
        if "virtual" in resolved.parts:
            return "v4l2loopback" if (entry / "max_openers").exists() else "virtual"
    except OSError:
        pass
    return ""


def list_cameras(root: Path = _DEFAULT_SYSFS) -> list[CameraInfo]:
    """List video capture devices; on Linux reads sysfs to skip metadata and loopback nodes."""
    if not IS_LINUX and root == _DEFAULT_SYSFS:
        return [CameraInfo(index=0, name="Default camera", driver="", usable=True)]
    if not root.exists():
        return []
    entries: list[tuple[int, Path]] = []
    try:
        for child in root.iterdir():
            if child.name.startswith("video") and child.name[5:].isdigit():
                entries.append((int(child.name[5:]), child))
    except OSError:
        return []
    entries.sort(key=lambda pair: pair[0])

    cams: list[CameraInfo] = []
    for index, entry in entries:
        name = _read_text(entry / "name") or f"video{index}"
        idx_raw = _read_text(entry / "index")
        try:
            dev_index = int(idx_raw) if idx_raw else 0
        except ValueError:
            dev_index = 0
        driver = _read_driver(entry)
        usable = (
            dev_index == 0
            and driver not in ("v4l2loopback", "virtual")
            and "loopback" not in name.lower()
        )
        cams.append(CameraInfo(index=index, name=name, driver=driver, usable=usable))
    return cams


#: How long read() waits for a new frame before reporting a miss.
_FRAME_WAIT = 0.2


class OpenCVCamera:
    """OpenCV webcam capture producing mirrored RGB frames.

    A grabber thread reads the camera continuously and keeps only the newest frame, so capture
    overlaps hand detection instead of adding to it (a serial read waits up to a frame period).
    """

    def __init__(self, info: CameraInfo, width: int = 640, height: int = 360, fps: int = 30) -> None:
        self._info = info
        self._width = width
        self._height = height
        self._fps = fps
        self._cap: Any = None
        self._cond = threading.Condition()
        self._frame: Any = None
        self._seq = 0
        self._taken = 0
        self._stop = threading.Event()
        self._grabber: threading.Thread | None = None

    @property
    def name(self) -> str:
        return self._info.name

    def open(self) -> None:
        import cv2

        cv2.setNumThreads(1)
        status = ""
        if IS_MAC:
            status = camera_authorization()
            if status in ("denied", "restricted"):
                raise RuntimeError("Camera access is off for Lura. Turn it on in System Settings → "
                                   "Privacy & Security → Camera, then switch Lura Air off and on.")
        backend = cv2.CAP_V4L2 if IS_LINUX else (cv2.CAP_AVFOUNDATION if IS_MAC else cv2.CAP_ANY)
        cap = cv2.VideoCapture(self._info.index, backend)
        if IS_MAC and (not cap or not cap.isOpened()) and status == "not_determined":
            # OpenCV asks macOS for the camera and fails this first open; wait for the user's answer.
            if cap is not None:
                cap.release()
            for _ in range(120):                 # up to 60 s
                time.sleep(0.5)
                if camera_authorization() != "not_determined":
                    break
            if camera_authorization() == "authorized":
                cap = cv2.VideoCapture(self._info.index, backend)
        if not cap or not cap.isOpened():
            if cap is not None:
                cap.release()
            raise RuntimeError(f"Cannot open camera {self._info.name}.")
        if not IS_MAC:
            cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, self._width)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self._height)
        cap.set(cv2.CAP_PROP_FPS, self._fps)
        cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        self._cap = cap
        self._stop.clear()
        self._frame, self._seq, self._taken = None, 0, 0
        self._grabber = threading.Thread(target=self._grab, args=(cap,), name="air-grab", daemon=True)
        self._grabber.start()

    def _grab(self, cap: Any) -> None:
        while not self._stop.is_set():
            try:
                ok, frame = cap.read()
            except Exception as exc:
                log.debug("Camera grab failed: %s", exc)
                ok, frame = False, None
            if not ok or frame is None:
                self._stop.wait(0.05)   # unplugged: read() times out and the service counts misses
                continue
            with self._cond:
                self._frame, self._seq = frame, self._seq + 1
                self._cond.notify_all()

    def read(self) -> object | None:
        if self._cap is None:
            return None
        import cv2

        with self._cond:
            if not self._cond.wait_for(lambda: self._seq != self._taken or self._stop.is_set(),
                                       timeout=_FRAME_WAIT) or self._frame is None:
                return None
            frame, self._taken = self._frame, self._seq
        # Virtual cameras (a phone through v4l2loopback) ignore the requested size and send
        # 1080p; detection on that is several times slower for no gain, so scale it down here.
        if frame.shape[1] > self._width:
            height = round(frame.shape[0] * self._width / frame.shape[1])
            frame = cv2.resize(frame, (self._width, height), interpolation=cv2.INTER_AREA)
        mirrored = cv2.flip(frame, 1)
        return cv2.cvtColor(mirrored, cv2.COLOR_BGR2RGB)

    def close(self) -> None:
        self._stop.set()
        with self._cond:
            self._cond.notify_all()
        if self._grabber is not None:
            self._grabber.join(timeout=1.0)
            self._grabber = None
        cap = self._cap
        self._cap = None
        if cap is not None:
            cap.release()
