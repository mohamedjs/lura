"""The dashboard's endpoints, mapped onto the modules' application services."""

from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import Any, Callable

from ... import __version__
from ...shared import events
from ...shared.events import EventBus
from ...shared.i18n import LANGUAGES, catalog, t
from ...shared.platform import os_name
from ..billing import BillingService
from ..brain import BrainService
from ..connectors import ConnectorService
from ..settings import PROVIDERS, SUGGESTED_MODELS, VOICE_PROVIDERS, VOICES, SettingsService
from ..voice import VoiceService
from .server import ApiError

PREVIEW = {"en": "Hi, I'm {name}. This is how I sound.", "ar": "أهلاً، أنا {name}. ده صوتي."}


@dataclass
class DashboardApi:
    settings: SettingsService
    brain: BrainService
    voice: VoiceService
    connectors: ConnectorService
    billing: BillingService
    bus: EventBus
    status: Callable[[], str]

    def register(self, server) -> None:
        r = server.route
        r("GET", "/api/state", lambda b, m: self.state())
        r("GET", "/api/status", lambda b, m: {"status": self.status(),
                                              "capabilities": self.settings.capabilities().to_dict(),
                                              "connectors": self.connectors.list(),
                                              "plan": self.billing.plan()})
        r("PUT", "/api/settings", lambda b, m: self.update_settings(b))
        r("PUT", r"/api/keys/(\w+)", lambda b, m: self.set_key(m[1], b))
        r("DELETE", r"/api/keys/(\w+)", lambda b, m: self.remove_key(m[1]))
        r("GET", r"/api/models/(\w+)", lambda b, m: {"models": self.brain.list_models(m[1])})
        r("POST", "/api/voice/preview", lambda b, m: self.preview(b))
        r("POST", "/api/talk", lambda b, m: self._publish(events.TALK_REQUESTED))
        r("POST", "/api/stop", lambda b, m: self._publish(events.STOP_REQUESTED))
        r("GET", "/api/connectors", lambda b, m: {"connectors": self.connectors.list()})
        r("POST", "/api/connectors", lambda b, m: self.add_connector(b))
        r("PATCH", r"/api/connectors/([\w-]+)", lambda b, m: self.toggle_connector(m[1], b))
        r("DELETE", r"/api/connectors/([\w-]+)", lambda b, m: self.remove_connector(m[1]))
        r("POST", "/api/billing/activate", lambda b, m: self.billing.activate(str(b.get("key", ""))))
        r("POST", "/api/billing/deactivate", lambda b, m: self.billing.deactivate())
        r("POST", "/api/billing/refresh", lambda b, m: (self.billing.revalidate(force=True), self.billing.plan())[1])

    def _publish(self, name: str) -> dict:
        self.bus.publish(name)
        return {"ok": True}

    def state(self) -> dict[str, Any]:
        s = self.settings.current
        return {
            "version": __version__, "os": os_name(), "status": self.status(),
            "settings": s.to_dict(), "keys": self.settings.key_status(),
            "capabilities": self.settings.capabilities().to_dict(),
            "providers": list(PROVIDERS), "voice_providers": list(VOICE_PROVIDERS),
            "suggested_models": {k: list(v) for k, v in SUGGESTED_MODELS.items()},
            "voices": {k: list(v) for k, v in VOICES.items()},
            "plan": self.billing.plan(), "connectors": self.connectors.list(),
            "languages": LANGUAGES, "i18n": {lang: catalog(lang) for lang in LANGUAGES},
            "paths": {"config": str(self.settings.settings_path.parent)},
        }

    def update_settings(self, body: dict) -> dict:
        changed = self.settings.update(body.get("patch") or {})
        return {"changed": changed, "settings": self.settings.current.to_dict(),
                "capabilities": self.settings.capabilities().to_dict()}

    def set_key(self, provider: str, body: dict) -> dict:
        if provider not in PROVIDERS:
            raise ApiError(404, f"unknown provider {provider}")
        key = str(body.get("key", "")).strip()
        if not key:
            raise ApiError(400, t("keys.empty", self.settings.current.ui_language))
        # Test before saving: a key that does not work should never be stored
        # and then fail mysteriously mid-conversation.
        message = self.brain.check_key(provider, key)
        self.settings.set_key(provider, key)
        # The first key decides the brain if the user has not picked one.
        caps = self.settings.capabilities()
        if caps.brain_provider is None and not self.settings.has_key(self.settings.current.brain_provider):
            self.settings.update({"brain_provider": provider})
        return {"message": message, "keys": self.settings.key_status(),
                "capabilities": self.settings.capabilities().to_dict(),
                "settings": self.settings.current.to_dict()}

    def remove_key(self, provider: str) -> dict:
        self.settings.remove_key(provider)
        return {"keys": self.settings.key_status(),
                "capabilities": self.settings.capabilities().to_dict()}

    def preview(self, body: dict) -> dict:
        s = self.settings.current
        voice = str(body.get("voice") or s.voice_name)
        if voice not in VOICES["female"] + VOICES["male"]:
            raise ApiError(400, f"unknown voice {voice}")
        text = PREVIEW.get(s.language, PREVIEW["en"]).format(name=s.assistant_name)
        # Spoken in the background: synthesis takes a second or two and the
        # browser should not sit on a pending request meanwhile.
        threading.Thread(target=self.voice.preview, args=(s, voice, text), daemon=True).start()
        return {"ok": True}

    def add_connector(self, body: dict) -> dict:
        kind = body.get("kind")
        if kind == "json":
            added = self.connectors.import_json(str(body.get("json", "")))
            return {"added": added, "connectors": self.connectors.list()}
        name = str(body.get("name", "")).strip().lower()
        if kind == "url":
            self.connectors.add_url(name, str(body.get("url", "")), str(body.get("token", "")))
        elif kind == "command":
            env = body.get("env") or {}
            if not isinstance(env, dict):
                raise ApiError(400, "env must be an object")
            self.connectors.add_command(name, str(body.get("command", "")),
                                        {str(k): str(v) for k, v in env.items()})
        else:
            raise ApiError(400, "kind must be url, command or json")
        return {"added": [name], "connectors": self.connectors.list()}

    def toggle_connector(self, name: str, body: dict) -> dict:
        self.connectors.set_enabled(name, bool(body.get("enabled")))
        return {"connectors": self.connectors.list()}

    def remove_connector(self, name: str) -> dict:
        if not self.connectors.remove(name):
            raise ApiError(404, f"no connector {name}")
        return {"connectors": self.connectors.list()}


