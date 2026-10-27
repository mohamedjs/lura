"""Settings and keys on disk.

Keys live in a 0600 JSON file rather than the login keyring on purpose:
libsecret needs an *unlocked* keyring, and the assistant starts with the
graphical session — exactly when the keyring may still be locked.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import fields
from pathlib import Path

from ...shared import paths
from .domain import PROVIDER_INFO, PROVIDERS, Settings, migrate_legacy

log = logging.getLogger(__name__)


class JsonSettingsRepository:
    def __init__(self, path: Path | None = None):
        self.path = path or paths.config_dir() / "settings.json"

    def load(self) -> Settings:
        if not self.path.exists():
            return Settings()
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            # A hand-edited file with a typo must not stop the assistant.
            log.warning("Ignoring unreadable %s: %s", self.path, exc)
            return Settings()
        raw = migrate_legacy(raw)
        known = {f.name for f in fields(Settings)}
        settings = Settings(**{k: v for k, v in raw.items() if k in known})
        settings.models = {**Settings().models, **(settings.models or {})}
        return settings

    def save(self, settings: Settings) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(settings.to_dict(), indent=2, ensure_ascii=False) + "\n",
                       encoding="utf-8")
        os.replace(tmp, self.path)  # atomic: a crash mid-write keeps the old file


class KeyFileStore:
    """One key per provider; the environment overrides the file."""

    def __init__(self, path: Path | None = None):
        self.path = path or paths.config_dir() / "keys.json"

    def _read(self) -> dict[str, str]:
        if not self.path.exists():
            return {}
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        return {k: v.strip() for k, v in data.items() if isinstance(v, str) and v.strip()}

    def _write(self, keys: dict[str, str]) -> None:
        paths.write_private(self.path, json.dumps(keys, indent=2) + "\n")

    def get(self, provider: str) -> str | None:
        for var in PROVIDER_INFO.get(provider, {}).get("env", ()):
            if value := os.environ.get(var, "").strip():
                return value
        return self._read().get(provider)

    def source(self, provider: str) -> str | None:
        """"env", "file" or None — the dashboard shows where a key came from."""
        for var in PROVIDER_INFO.get(provider, {}).get("env", ()):
            if os.environ.get(var, "").strip():
                return "env"
        return "file" if provider in self._read() else None

    def available(self) -> frozenset[str]:
        return frozenset(p for p in PROVIDERS if self.get(p))

    def set(self, provider: str, key: str) -> None:
        key = key.strip()
        if not key:
            raise ValueError("Empty key.")
        keys = self._read()
        keys[provider] = key
        self._write(keys)

    def remove(self, provider: str) -> bool:
        keys = self._read()
        if provider not in keys:
            return False
        del keys[provider]
        self._write(keys)
        return True
