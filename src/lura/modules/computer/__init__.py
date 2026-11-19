"""Computer: the desktop actions Lura (and Lura Air) can take, validated, per OS."""

from __future__ import annotations

from .application import Backend, ComputerService
from .domain import ACTIONS, CONFIRM, DESKTOP_TOOL, SAFE, ActionError, ActionResult
from .infrastructure import default_backend

__all__ = [
    "ComputerService",
    "Backend",
    "default_backend",
    "ActionResult",
    "ActionError",
    "ACTIONS",
    "DESKTOP_TOOL",
    "SAFE",
    "CONFIRM",
]
