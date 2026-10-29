"""Checking for a new Lura and installing it from inside the app."""

from __future__ import annotations

import logging
import threading
import time
from typing import Any, Callable

from .domain import (CHECK_EVERY, RELEASES_URL, SELF_INSTALL, checksum_for, checksum_url, is_newer,
                     pick_asset, trusted_url)

log = logging.getLogger(__name__)


class UpdateService:
    """``state``: idle → checking → idle | downloading → installing → (Lura exits)
    or error. ``on_done(how)`` is called once the new version is ready to
    take over: how = "exit" (its installer restarts Lura) or "restart"."""

    def __init__(self, current: str, enabled: Callable[[], bool] = lambda: True,
                 on_done: Callable[[str], None] = lambda how: None):
        self._current = current
        self._enabled = enabled
        self._on_done = on_done
        self._lock = threading.Lock()
        self._release: dict | None = None
        self._checked = 0.0
        self.state, self.error, self.progress = "idle", "", 0.0

    # ── what the dashboard shows ──
    def status(self) -> dict[str, Any]:
        from .infrastructure import arch, install_kind

        kind = install_kind()
        release = self._release or {}
        latest = (release.get("tag_name") or "").lstrip("v")
        available = bool(latest) and is_newer(latest, self._current)
        asset = pick_asset(release, kind, arch()) if available else None
        return {"current": self._current, "latest": latest, "available": available,
                "can_install": available and kind in SELF_INSTALL and asset is not None,
                "kind": kind, "notes_url": release.get("html_url") or RELEASES_URL,
                "size": asset["size"] if asset else 0, "checked": self._checked,
                "state": self.state, "progress": round(self.progress, 3), "error": self.error}

    def check(self, force: bool = False) -> dict[str, Any]:
        """Ask GitHub, at most every CHECK_EVERY seconds unless ``force``."""
        from .infrastructure import latest_release

        if not force and (not self._enabled() or time.time() - self._checked < CHECK_EVERY):
            return self.status()
        with self._lock:
            if self.state in ("downloading", "installing"):
                return self.status()
            self.state, self.error = "checking", ""
        try:
            self._release = latest_release()
            self._checked = time.time()
            self.state = "idle"
            if (s := self.status())["available"]:
                log.info("Lura %s is available (this is %s).", s["latest"], self._current)
        except Exception as exc:
            self.state, self.error = "idle", f"Could not reach GitHub: {exc}"
            log.info("Update check failed: %s", exc)
        return self.status()

    def watch(self, stop: threading.Event, first_delay: float = 60.0) -> None:
        """Background: check a minute after start, then every CHECK_EVERY."""
        if stop.wait(first_delay):
            return
        while True:
            self.check()
            if stop.wait(CHECK_EVERY):
                return

    # ── installing ──
    def install(self) -> dict[str, Any]:
        """Start downloading and installing the latest release (in the background)."""
        with self._lock:
            if self.state in ("downloading", "installing"):
                return self.status()
            status = self.status()
            if not status["can_install"]:
                raise RuntimeError("No update this copy of Lura can install by itself."
                                   if status["available"] else "Lura is up to date.")
            self.state, self.error, self.progress = "downloading", "", 0.0
        threading.Thread(target=self._install, daemon=True, name="update").start()
        return self.status()

    def _install(self) -> None:
        from . import infrastructure as infra

        try:
            kind = infra.install_kind()
            asset = pick_asset(self._release or {}, kind, infra.arch())
            sums = checksum_url(self._release or {})
            if not asset or not sums or not trusted_url(asset["url"]) or not trusted_url(sums):
                raise RuntimeError("The release is missing its file or its checksums.")
            expected = checksum_for(infra.fetch_text(sums), asset["name"])
            if not expected:
                raise RuntimeError(f"SHA256SUMS.txt does not list {asset['name']}.")

            def progress(done: int, total: int) -> None:
                self.progress = done / total if total else 0.0

            file = infra.download(asset["url"], progress)
            if infra.sha256(file) != expected:
                file.unlink(missing_ok=True)
                raise RuntimeError("The download does not match its checksum; nothing was installed.")
            self.state, self.progress = "installing", 1.0
            log.info("Installing Lura %s from %s", (self._release or {}).get("tag_name"), file.name)
            how = infra.install(kind, file)
        except Exception as exc:
            log.warning("Update failed: %s", exc)
            self.state, self.error = "error", str(exc)
            return
        self._on_done(how)
