"""Pick the brain for the current settings, and answer questions about models."""

from __future__ import annotations

from ...shared.http import HttpError
from ..settings import ANTHROPIC, SUGGESTED_MODELS, Settings
from .domain import ChatModel


class BrainService:
    def __init__(self, key_for):
        #: ``key_for(provider) -> str`` raising NeedsSetup. Injected so this
        #: module does not depend on how keys are stored.
        self._key_for = key_for

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

    def check_key(self, provider: str, key: str) -> str:
        """Raises HttpError with the provider's own words if the key is bad."""
        if provider == ANTHROPIC:
            from .infrastructure.anthropic_chat import list_models
            return f"OK — {len(list_models(key))} models available"
        from .infrastructure.openai_compat import check_key
        return check_key(provider, key)
