"""MediaPipe HandLandmarker wrapper and model asset management."""

from __future__ import annotations

import hashlib
import logging
import os
import urllib.request
from pathlib import Path
from typing import Any, Callable

from ....shared.paths import data_dir
from ..domain import Hand, Point

log = logging.getLogger(__name__)

MODEL_URL = (
    "https://storage.googleapis.com/mediapipe-models/hand_landmarker/"
    "hand_landmarker/float16/latest/hand_landmarker.task"
)
MODEL_SIZE = 7_819_105
#: Of the file downloaded on 2026-11-20 (Last-Modified 2023-04-26); a changed upstream file is refused.
MODEL_SHA256: str | None = "fbc2a30080c3c557093b5ddfc334698132eb341044ccee322ccf8bcf3607cde1"

_CHUNK_BYTES = 64 * 1024


def model_path() -> Path:
    """Location of the locally cached MediaPipe hand landmarker task file."""
    return data_dir() / "air" / "hand_landmarker.task"


def model_ready() -> bool:
    """True when the hand landmarker model file exists with the expected size."""
    path = model_path()
    try:
        return path.is_file() and path.stat().st_size == MODEL_SIZE
    except OSError:
        return False


def air_available() -> str:
    """Empty string when packages and model are ready; otherwise a user-facing reason."""
    try:
        import cv2  # noqa: F401
        import mediapipe  # noqa: F401
    except Exception:
        return "Install the air extra: pip install 'lura[air]'."
    if not model_ready():
        return "Run 'lura air setup' to download the hand model."
    return ""


def download_model(progress: Callable[[int, int], None] | None = None) -> Path:
    """Download the hand landmarker model atomically with size and optional SHA-256 checks."""
    dest = model_path()
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_suffix(dest.suffix + ".part")
    digest = hashlib.sha256()
    written = 0
    try:
        with urllib.request.urlopen(MODEL_URL, timeout=60) as resp, part.open("wb") as out:
            header_len = resp.headers.get("Content-Length")
            total = int(header_len) if header_len and header_len.isdigit() else MODEL_SIZE
            while True:
                chunk = resp.read(_CHUNK_BYTES)
                if not chunk:
                    break
                out.write(chunk)
                digest.update(chunk)
                written += len(chunk)
                if progress is not None:
                    progress(written, total)
        if written != MODEL_SIZE:
            raise RuntimeError(
                f"Downloaded hand model has unexpected size ({written} bytes, expected {MODEL_SIZE})."
            )
        if MODEL_SHA256 is not None and digest.hexdigest().lower() != MODEL_SHA256.lower():
            raise RuntimeError("Downloaded hand model checksum mismatch.")
        os.replace(part, dest)
        return dest
    except Exception as exc:
        try:
            part.unlink(missing_ok=True)
        except OSError:
            pass
        if isinstance(exc, RuntimeError):
            raise
        raise RuntimeError(f"Could not download hand model: {exc}") from exc


class MediaPipeLandmarker:
    """MediaPipe Tasks HandLandmarker in VIDEO running mode."""

    def __init__(self, path: Path, num_hands: int = 2) -> None:
        import mediapipe as mp
        from mediapipe.tasks.python import BaseOptions
        from mediapipe.tasks.python.vision import (
            HandLandmarker,
            HandLandmarkerOptions,
            RunningMode,
        )

        opts = HandLandmarkerOptions(
            base_options=BaseOptions(model_asset_path=str(path)),
            running_mode=RunningMode.VIDEO,
            num_hands=num_hands,
            min_hand_detection_confidence=0.6,
            min_hand_presence_confidence=0.5,
            min_tracking_confidence=0.5,
        )
        self._mp: Any = mp
        self._landmarker: Any = HandLandmarker.create_from_options(opts)

    def detect(self, frame: object, t_ms: int) -> tuple[Hand, ...]:
        if self._landmarker is None:
            return ()
        mp_image = self._mp.Image(image_format=self._mp.ImageFormat.SRGB, data=frame)
        result = self._landmarker.detect_for_video(mp_image, t_ms)
        hands: list[Hand] = []
        for landmarks, categories in zip(result.hand_landmarks, result.handedness):
            if not categories or len(landmarks) != 21:
                continue
            top = categories[0]
            pts = tuple(Point(float(lm.x), float(lm.y), float(lm.z)) for lm in landmarks)
            hands.append(Hand(side=top.category_name.lower(), score=float(top.score), points=pts))
        return tuple(hands)

    def close(self) -> None:
        lm = self._landmarker
        self._landmarker = None
        if lm is not None:
            lm.close()
