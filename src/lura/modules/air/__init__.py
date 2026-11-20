"""Air: hand gestures from the webcam, recognised on this computer."""

from __future__ import annotations

from .application import AirController, AirService, Camera, Landmarker
from .domain import GESTURES, MODES, AirState, Arming, GestureEvent, HandView, Resolution, pick_camera, resolve
from .infrastructure.cameras import list_cameras
from .infrastructure.landmarker import air_available, download_model, model_path, model_ready

__all__ = [
    "AirService",
    "AirController",
    "AirState",
    "Arming",
    "HandView",
    "GestureEvent",
    "GESTURES",
    "MODES",
    "Resolution",
    "resolve",
    "Camera",
    "Landmarker",
    "list_cameras",
    "pick_camera",
    "model_path",
    "model_ready",
    "download_model",
    "air_available",
]
