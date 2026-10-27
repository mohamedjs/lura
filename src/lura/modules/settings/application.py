"""Reading and changing settings and keys, with the rules applied."""

from __future__ import annotations

import threading
from typing import Any

from ...shared import events
from ...shared.errors import NeedsSetup
from ...shared.events import EventBus
from .domain import PROVIDER_INFO, PROVIDERS, Capabilities, Settings, apply_patch, capabilities
from .infrastructure import JsonSettingsRepository, KeyFileStore


def mask(key: str) -> str:
    return f"{key[:4]}…{key[-4:]}" if len(key) > 12 else "••••"


class SettingsService:
    def __init__(self, repo: JsonSettingsRepository | None = None,
                 keys: KeyFileStore | None = None, bus: EventBus | None = None):
        self._repo = repo or JsonSettingsRepository()
        self._keys = keys or KeyFileStore()
        self._bus = bus
        self._lock = threading.Lock()
        self._settings = self._repo.load()

    # ── settings ──
    @property
    def current(self) -> Settings:
        """A snapshot. Conversations take one at the start and keep it."""
        with self._lock:
            return Settings(**self._settings.to_dict())

    def reload(self) -> Settings:
        with self._lock:
            self._settings = self._repo.load()
        return self.current

    def update(self, patch: dict[str, Any]) -> list[str]:
        with self._lock:
            changed = apply_patch(self._settings, patch)
            if changed:
                self._repo.save(self._settings)
        if changed and self._bus:
            self._bus.publish(events.SETTINGS_CHANGED, changed=changed)
        return changed

    def save(self) -> None:
        with self._lock:
            self._repo.save(self._settings)

    @property
    def settings_path(self):
        return self._repo.path

    # ── keys ──
    def key(self, provider: str) -> str:
        key = self._keys.get(provider)
        if not key:
            info = PROVIDER_INFO.get(provider, {})
            raise NeedsSetup(f"No {info.get('label', provider)} API key. Add one in the dashboard "
                             f"(lura dashboard) or with: lura login --provider {provider}\n"
                             f"Get one at: {info.get('key_url', '')}")
        return key

    def has_key(self, provider: str) -> bool:
        return bool(self._keys.get(provider))

    def set_key(self, provider: str, key: str) -> None:
        if provider not in PROVIDERS:
            raise ValueError(f"unknown provider {provider!r}")
        self._keys.set(provider, key)
        if self._bus:
            self._bus.publish(events.KEYS_CHANGED, provider=provider)

    def remove_key(self, provider: str) -> bool:
        removed = self._keys.remove(provider)
        if removed and self._bus:
            self._bus.publish(events.KEYS_CHANGED, provider=provider)
        return removed

    def key_status(self) -> dict[str, dict[str, Any]]:
        """Per provider: stored or not, masked, and from where. Never the key."""
        out = {}
        for provider in PROVIDERS:
            key = self._keys.get(provider)
            out[provider] = {"set": bool(key), "masked": mask(key) if key else "",
                             "source": self._keys.source(provider),
                             **{k: v for k, v in PROVIDER_INFO[provider].items() if k != "env"}}
        return out

    @property
    def keys_path(self):
        return self._keys.path

    def capabilities(self) -> Capabilities:
        return capabilities(self.current, self._keys.available())
