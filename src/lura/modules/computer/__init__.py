"""Computer: the desktop actions Lura (and Lura Air) can take, validated, per OS."""

from __future__ import annotations

from .application import Backend, ComputerService
from .domain import ACTIONS, CONFIRM, DESKTOP_TOOL, SAFE, ActionError, ActionResult, Rect, WindowGrip
from .infrastructure import default_backend


def accessibility_trusted(prompt: bool = False) -> bool:
    """Whether macOS Accessibility permission is granted (always True off macOS)."""
    from ...shared.platform import IS_MAC
    if not IS_MAC:
        return True
    from .infrastructure import quartz
    return quartz.ax_trusted(prompt)


__all__ = [
    "ComputerService",
    "Backend",
    "default_backend",
    "accessibility_trusted",
    "ActionResult",
    "ActionError",
    "ACTIONS",
    "DESKTOP_TOOL",
    "SAFE",
    "CONFIRM",
    "Rect",
    "WindowGrip",
]
