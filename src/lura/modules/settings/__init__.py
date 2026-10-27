"""Settings & credentials: what the user chose, and which keys they gave us."""

from .application import SettingsService, mask
from .domain import (
    ANTHROPIC, DEFAULT_MODELS, GEMINI, HEARING_MODELS, LANGUAGES, MODES, OPENAI, OPENROUTER,
    PROVIDER_INFO, PROVIDERS, SUGGESTED_MODELS, TTS_MODELS, VOICE_PROVIDERS, VOICES,
    Capabilities, Settings, brain_hears_audio, gender_of, resolve_voice_provider,
)

__all__ = [
    "SettingsService", "Settings", "Capabilities", "mask", "brain_hears_audio", "gender_of",
    "resolve_voice_provider", "GEMINI", "OPENAI", "ANTHROPIC", "OPENROUTER", "PROVIDERS",
    "VOICE_PROVIDERS", "PROVIDER_INFO", "DEFAULT_MODELS", "SUGGESTED_MODELS", "TTS_MODELS",
    "HEARING_MODELS", "VOICES", "MODES", "LANGUAGES",
]
