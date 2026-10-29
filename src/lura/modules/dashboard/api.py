"""The dashboard's endpoints, mapped onto the modules' application services."""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Any, Callable

from ... import __version__
from ...shared import events
from ...shared.events import EventBus
from ...shared.i18n import LANGUAGES, catalog, t
from ...shared.platform import os_name
from ..brain import BrainService
from ..connectors import ConnectorService
from ..memory import MemoryService
from ..settings import PROVIDERS, SUGGESTED_MODELS, VOICE_PROVIDERS, VOICES, SettingsService
from ..skills import SkillsService
from ..voice import VoiceService
from .server import ApiError

#: Settings that apply the moment they change, so they never ask for a restart.
#: The rest is read again at the start of every conversation.
LIVE = {"onboarded", "subtitles", "ui_language", "input_device", "output_device", "silence_ms",
        "silence_factor", "follow_up_seconds", "memory_enabled", "context_budget_tokens", "live_captions",
        "persona", "user_name", "user_gender", "reasoning_effort", "voice_name", "voice_gender", "tts_style",
        "language", "brain_provider", "models", "voice_provider", "mode", "wake_word"}

PREVIEW = {"en": "Hi, I'm {name}. This is how I sound.", "ar": "أهلاً، أنا {name}. ده صوتي."}


@dataclass
class DashboardApi:
    settings: SettingsService
    brain: BrainService
    voice: VoiceService
    connectors: ConnectorService
    bus: EventBus
    status: Callable[[], str]
    memory: MemoryService | None = None
    skills: SkillsService | None = None
    #: Whether a restart applies the changes made since Lura started. False
    #: for the standalone dashboard, which has no assistant to restart.
    can_restart: bool = True
    _pending: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        # Changes are applied on the next conversation anyway, but a restart
        # makes sure (wake word, devices, the face) — the dashboard offers one.
        self.bus.subscribe(events.SETTINGS_CHANGED,
                           lambda p: self._pending.extend(c for c in p.get("changed", []) if c not in LIVE))
        self.bus.subscribe(events.KEYS_CHANGED, lambda p: self._pending.append(f"key:{p.get('provider')}"))

    @property
    def restart_pending(self) -> bool:
        return self.can_restart and bool(self._pending)

    def register(self, server) -> None:
        r = server.route
        r("GET", "/api/state", lambda b, m: self.state())
        r("GET", "/api/status", lambda b, m: {"status": self.status(),
                                              "capabilities": self.settings.capabilities().to_dict(),
                                              "connectors": self.connectors.list(),
                                              "restart_pending": self.restart_pending})
        r("GET", "/api/catalog", lambda b, m: {"catalog": self.brain.full_catalog(
            self.settings.capabilities().keys)})
        r("POST", "/api/restart", lambda b, m: self.restart())
        r("PUT", "/api/settings", lambda b, m: self.update_settings(b))
        r("PUT", r"/api/keys/(\w+)", lambda b, m: self.set_key(m[1], b))
        r("DELETE", r"/api/keys/(\w+)", lambda b, m: self.remove_key(m[1]))
        r("GET", r"/api/models/(\w+)", lambda b, m: {"models": self.brain.list_models(m[1])})
        r("POST", "/api/voice/preview", lambda b, m: self.preview(b))
        r("GET", "/api/audio/devices", lambda b, m: self.voice.devices())
        r("POST", "/api/audio/test-mic", lambda b, m: self.voice.mic_test(b.get("device")))
        r("POST", "/api/audio/test-speaker", lambda b, m: self.test_speaker(b))
        r("POST", "/api/talk", lambda b, m: self._publish(events.TALK_REQUESTED))
        r("POST", "/api/stop", lambda b, m: self._publish(events.STOP_REQUESTED))
        r("GET", "/api/connectors", lambda b, m: {"connectors": self.connectors.list()})
        r("GET", "/api/chrome", lambda b, m: self.connectors.chrome_status())
        r("POST", "/api/chrome/setup", lambda b, m: {"opened": self.connectors.open_chrome_setup()})
        r("POST", "/api/connectors", lambda b, m: self.add_connector(b))
        r("PATCH", r"/api/connectors/([\w-]+)", lambda b, m: self.toggle_connector(m[1], b))
        r("DELETE", r"/api/connectors/([\w-]+)", lambda b, m: self.remove_connector(m[1]))
        r("GET", "/api/memory", lambda b, m: self._memory_state())
        r("PUT", "/api/memory", lambda b, m: self.put_memory(b))
        r("POST", "/api/memory/reset", lambda b, m: (self._mem().reset(), self._memory_state())[1])
        r("GET", "/api/history", lambda b, m: {"conversations": self._mem().conversations(b.get("q", ""))})
        r("GET", r"/api/history/(\d+)", lambda b, m: {"messages": self._mem().transcript(int(m[1]))})
        r("DELETE", r"/api/history/(\d+)", lambda b, m: self.delete_history(int(m[1])))
        r("DELETE", "/api/history", lambda b, m: (self._mem().delete_all(), {"conversations": []})[1])
        r("GET", "/api/skills", lambda b, m: {"skills": self._skills().list()})
        r("POST", "/api/skills", lambda b, m: self.add_skill(b))
        r("PATCH", r"/api/skills/([\w-]+)", lambda b, m: (self._skills().set_enabled(m[1], bool(b.get("enabled"))),
                                                          {"skills": self._skills().list()})[1])
        r("DELETE", r"/api/skills/([\w-]+)", lambda b, m: self.remove_skill(m[1]))

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
            "connectors": self.connectors.list(),
            "languages": LANGUAGES, "i18n": {lang: catalog(lang) for lang in LANGUAGES},
            "paths": {"config": str(self.settings.settings_path.parent)},
            "restart_pending": self.restart_pending, "can_restart": self.can_restart,
        }

    def restart(self) -> dict:
        if not self.can_restart:
            raise ApiError(409, "Lura is not running; start it to apply the changes.")
        self.bus.publish(events.RESTART_REQUESTED)
        return {"ok": True}

    def update_settings(self, body: dict) -> dict:
        patch = dict(body.get("patch") or {})
        lang = self.settings.current.ui_language
        notes = []
        if "wake_word" in patch:
            patch["wake_word"] = str(patch["wake_word"]).strip().lower()
            if self.voice.wake_word_known(patch["wake_word"]) is False:
                raise ApiError(400, t("lang.wake_unknown", lang, word=patch["wake_word"]))
        new_name = str(patch.get("assistant_name", "")).strip()
        old = self.settings.current
        # Renamed, and she was woken by her old name: follow the new one when
        # the wake-word model can hear it, and say so either way.
        if new_name and "wake_word" not in patch and old.wake_word in (old.assistant_name.lower(), "lura"):
            candidate = new_name.lower()
            if candidate != old.wake_word:
                if self.voice.wake_word_known(candidate):
                    patch["wake_word"] = candidate
                    notes.append(t("lang.wake_followed", lang, word=candidate))
                else:
                    notes.append(t("lang.wake_kept", lang, word=old.wake_word, name=new_name))
        changed = self.settings.update(patch)
        return {"changed": changed, "notes": notes, "settings": self.settings.current.to_dict(),
                "capabilities": self.settings.capabilities().to_dict(),
                "restart_pending": self.restart_pending}

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
                "settings": self.settings.current.to_dict(), "restart_pending": self.restart_pending}

    def remove_key(self, provider: str) -> dict:
        self.settings.remove_key(provider)
        return {"keys": self.settings.key_status(),
                "capabilities": self.settings.capabilities().to_dict(),
                "restart_pending": self.restart_pending}

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

    def _mem(self) -> MemoryService:
        if self.memory is None:
            raise ApiError(404, "memory is not available here")
        return self.memory

    def _memory_state(self) -> dict:
        mem = self._mem()
        return {"facts": mem.facts(), "summary": mem.summary(), "path": str(mem.store.root)}

    def put_memory(self, body: dict) -> dict:
        facts = body.get("facts")
        if not isinstance(facts, str) or len(facts) > 100_000:
            raise ApiError(400, "facts must be text")
        # Lura may have remembered something since the page loaded; saving
        # the stale text over it would silently lose it.
        if "base" in body and body["base"] != self._mem().facts():
            raise ApiError(409, t("mem.changed", self.settings.current.ui_language), **self._memory_state())
        self._mem().set_facts(facts)
        return self._memory_state()

    def delete_history(self, conv_id: int) -> dict:
        if not self._mem().delete(conv_id):
            raise ApiError(404, f"no conversation {conv_id}")
        return {"conversations": self._mem().conversations()}

    def _skills(self) -> SkillsService:
        if self.skills is None:
            raise ApiError(404, "skills are not available here")
        return self.skills

    def add_skill(self, body: dict) -> dict:
        name = self._skills().add(str(body.get("kind", "")), body)
        return {"added": name, "skills": self._skills().list()}

    def remove_skill(self, name: str) -> dict:
        if not self._skills().remove(name):
            raise ApiError(404, f"no skill {name}")
        return {"skills": self._skills().list()}

    def test_speaker(self, body: dict) -> dict:
        threading.Thread(target=self.voice.speaker_test, args=(body.get("device"),), daemon=True).start()
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


