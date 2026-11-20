"""Air: hand gestures from the webcam, recognised on this computer."""

from __future__ import annotations

from .application import AirService, Camera, Landmarker
from .domain import GESTURES, AirState, GestureEvent, HandView, pick_camera
from .infrastructure.cameras import list_cameras
from .infrastructure.landmarker import air_available, download_model, model_path, model_ready

__all__ = [
    "AirService",
    "AirState",
    "HandView",
    "GestureEvent",
    "GESTURES",
    "Camera",
    "Landmarker",
    "list_cameras",
    "pick_camera",
    "model_path",
    "model_ready",
    "download_model",
    "air_available",
]
