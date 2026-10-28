"""Pick the brain for the current settings, and answer questions about models."""

from __future__ import annotations

import logging
import threading
import time

from ...shared.http import HttpError
from ..settings import ANTHROPIC, PROVIDERS, SUGGESTED_MODELS, Settings
from .domain import ChatModel, Done, Message, brain_candidate

log = logging.getLogger(__name__)

#: Model lists change rarely; the picker should open instantly.
CATALOG_TTL = 600


class BrainService:
    def __init__(self, key_for):
        #: ``key_for(provider) -> str`` raising NeedsSetup. Injected so this
        #: module does not depend on how keys are stored.
        self._key_for = key_for
        self._catalog: dict[str, tuple[float, list[dict]]] = {}
        self._lock = threading.Lock()

    def model(self, settings: Settings, provider: str | None = None,
              model: str | None = None) -> ChatModel:
        provider = provider or settings.brain_provider
        model = model or settings.model_for(provider)
        key = self._key_for(provider)
        if provider == ANTHROPIC:
            from .infrastructure.anthropic_chat import AnthropicChat
            return AnthropicChat(key, model, reasoning_effort=settings.reasoning_effort or "low")
        from .infrastructure.openai_compat import OpenAICompatChat
        return OpenAICompatChat(provider, key, model, reasoning_effort=settings.reasoning_effort,
                                provider_order=settings.openrouter_provider_order)

    def complete(self, settings: Settings, prompt: str, system: str = "") -> str:
        """One plain answer, no tools: summarising the memory, for instance."""
        text = ""
        for event in self.model(settings).stream(system, [Message("user", prompt)], []):
            if isinstance(event, Done):
                text = event.message.text
        return text

    def list_models(self, provider: str) -> list[str]:
        """Live list from the provider, or the suggestions if it can't be had."""
        try:
            if provider == ANTHROPIC:
                from .infrastructure.anthropic_chat import list_models
                return list_models(self._key_for(provider))
            from .infrastructure.openai_compat import list_models
            key = None if provider == "openrouter" else self._key_for(provider)
            return list_models(provider, key)
        except (HttpError, Exception):
            return list(SUGGESTED_MODELS.get(provider, ()))

    def catalog(self, provider: str) -> list[dict]:
        """Models this provider offers that can be the brain (chat + tools).

        Falls back to the suggested ids if the provider cannot be reached, so
        the picker is never empty for a provider that has a key.
        """
        with self._lock:
            hit = self._catalog.get(provider)
            if hit and time.monotonic() - hit[0] < CATALOG_TTL:
                return hit[1]
        try:
            if provider == ANTHROPIC:
                from .infrastructure.anthropic_chat import list_model_records
                records = list_model_records(self._key_for(provider))
            else:
                from .infrastructure.openai_compat import list_model_records
                records = list_model_records(provider, None if provider == "openrouter"
                                             else self._key_for(provider))
            entries = [e for r in records if (e := brain_candidate(provider, r))]
            entries.sort(key=lambda e: e["id"])
        except Exception as exc:
            log.info("Model list for %s unavailable (%s); using suggestions.", provider, exc)
            entries = [{"id": m, "name": m, "hears": provider == "gemini" or m.startswith("google/gemini")}
                       for m in SUGGESTED_MODELS.get(provider, ())]
        with self._lock:
            self._catalog[provider] = (time.monotonic(), entries)
        return entries

    def full_catalog(self, providers_with_keys) -> dict[str, list[dict]]:
        """The picker's data: every brain-capable model of every keyed provider."""
        out: dict[str, list[dict]] = {}
        threads = []
        for provider in PROVIDERS:
            if provider in providers_with_keys:
                t = threading.Thread(target=lambda p=provider: out.__setitem__(p, self.catalog(p)))
                t.start()
                threads.append(t)
        for t in threads:
            t.join(timeout=35)
        return {p: out.get(p, []) for p in PROVIDERS if p in providers_with_keys}

    def check_key(self, provider: str, key: str) -> str:
        """Raises HttpError with the provider's own words if the key is bad."""
        if provider == ANTHROPIC:
            from .infrastructure.anthropic_chat import list_models
            return f"OK — {len(list_models(key))} models available"
        from .infrastructure.openai_compat import check_key
        return check_key(provider, key)
