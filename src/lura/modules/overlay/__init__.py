"""The overlay: Lura's hologram face, floating top-right on every monitor.

Presentation only. It implements :class:`lura.shared.presence.Presence`
through :class:`OverlayState` and knows nothing about the assistant.
"""

from __future__ import annotations

import logging
import os
import threading

from ...shared.platform import FROZEN, IS_LINUX
from .state import OverlayState, State, StateBlender

log = logging.getLogger(__name__)

__all__ = ["Overlay", "OverlayState", "State", "StateBlender"]


class Overlay:
    """Pick a backend and own the UI loop.

    ``run`` blocks on the calling (main) thread until ``stop`` is set — Qt
    requires the main thread; GTK runs in its own thread and ``run`` just waits.
    """

    def __init__(self, enabled: bool = True, actions: dict | None = None, labels: dict | None = None):
        self.state = OverlayState()
        self._enabled = enabled
        self._actions = actions or {}
        self._labels = labels or {}
        self.backend = self._pick()

    def _pick(self) -> str:
        wanted = os.environ.get("LURA_OVERLAY", "").lower()
        from . import gtk_backend, qt_backend

        if wanted == "none":
            return "none"
        if wanted != "qt" and IS_LINUX and not FROZEN and gtk_backend.available():
            return "gtk"
        if qt_backend.available():
            return "qt"
        if IS_LINUX and gtk_backend.available():
            return "gtk"
        log.info("No overlay backend (install PySide6 and pycairo) — running headless.")
        return "none"

    def run(self, stop: threading.Event) -> None:
        if self.backend == "qt":
            from . import qt_backend
            try:
                qt_backend.run(self.state, stop, self._actions, self._labels, self._enabled)
                return
            except Exception:
                log.warning("Qt overlay failed — running headless.", exc_info=True)
        elif self.backend == "gtk" and self._enabled:
            from . import gtk_backend
            gtk_backend.start(self.state, self._actions.get("open"))
        while not stop.wait(0.5):
            pass
