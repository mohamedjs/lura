"""Air: hand gestures from the webcam, recognised on this computer."""

from __future__ import annotations

from .application import AirController, AirService, Camera, Landmarker
from .domain import GESTURES, MODES, AirState, GestureEvent, HandView, pick_camera, ring_action, ring_labels
from .engine import Command, InteractionEngine
from .infrastructure.cameras import list_cameras
from .infrastructure.landmarker import air_available, download_model, model_path, model_ready
from .infrastructure.macos_camera import authorization as camera_authorization

__all__ = [
    "AirService",
    "AirController",
    "AirState",
    "Command",
    "HandView",
    "GestureEvent",
    "GESTURES",
    "InteractionEngine",
    "MODES",
    "ring_action",
    "ring_labels",
    "Camera",
    "Landmarker",
    "list_cameras",
    "pick_camera",
    "model_path",
    "model_ready",
    "download_model",
    "air_available",
    "camera_authorization",
]
