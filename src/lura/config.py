"""Settings, paths, and the API keys.

Two providers, two very different shapes:

* ``gemini``     — the Live API. Full-duplex native audio: your voice streams
                   up, speech streams back, the server decides when you have
                   stopped talking.
* ``openrouter`` — turn-based. Record an utterance, transcribe it, send the
                   text to any of OpenRouter's models, speak the reply. Slower,
                   but it works with every model on the service.

Keys live in a 0600 JSON file rather than the login keyring on purpose:
libsecret needs an *unlocked* keyring, and this service starts with the
graphical session — exactly when the keyring may still be locked.
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, fields
from pathlib import Path
from typing import Any

CONFIG_DIR = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "lura"
DATA_DIR = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share")) / "lura"

KEYS_FILE = CONFIG_DIR / "keys.json"
SETTINGS_FILE = CONFIG_DIR / "settings.json"
VOSK_MODEL_DIR = DATA_DIR / "vosk-model"

# The Gemini Live API fixes both of these; they are not ours to choose.
INPUT_RATE = 16_000
OUTPUT_RATE = 24_000
CHANNELS = 1

PROVIDERS = ("gemini", "openrouter")

#: Which environment variable overrides each stored key.
ENV_VARS = {
    "gemini": ("GEMINI_API_KEY", "GOOGLE_API_KEY"),
    "openrouter": ("OPENROUTER_API_KEY",),
}

WHERE_TO_GET = {
    "gemini": "https://aistudio.google.com/apikey",
    "openrouter": "https://openrouter.ai/keys",
}


@dataclass
class Settings:
    """Everything worth changing without editing code."""

    #: Which service answers you. Explicit, not guessed from which key exists —
    #: having both keys stored is normal.
    provider: str = "gemini"

    # ── models, kept per provider so switching back does not lose the other ──
    # Preview ids churn. If one is retired the error reads "model not found".
    gemini_model: str = "gemini-2.5-flash-native-audio-preview-12-2025"
    openrouter_model: str = "google/gemini-2.5-flash"
    #: Transcription for the OpenRouter path. Not local Vosk: that model is
    #: en-US only and would quietly mistranscribe every other language.
    openrouter_stt_model: str = "openai/whisper-1"
    openrouter_tts_model: str = "openai/gpt-4o-mini-tts"
    openrouter_tts_voice: str = "alloy"

    # ── voice ───────────────────────────────────────────────────────────────
    voice: str = "Puck"          # Gemini Live voice
    language: str = "en-US"
    system_instruction: str = (
        "You are a spoken assistant. Keep answers short and conversational, "
        "as if talking out loud. Do not use markdown or lists."
    )

    # ── wake word (always local, always Vosk, whichever provider answers) ────
    wake_word: str = "gemini"
    wake_cooldown: float = 1.5

    # ── end-of-turn detection, OpenRouter path only ─────────────────────────
    # Gemini Live decides this server-side. Here we own it, and a fixed
    # threshold is the classic mistake: a quiet room and a laptop fan differ by
    # more than 10x. The floor is measured at the start of each utterance and
    # these scale it.
    silence_factor: float = 2.0     # "speech" is this many times the floor
    silence_ms: int = 900           # quiet for this long ends the turn
    max_utterance_seconds: float = 30.0
    min_utterance_seconds: float = 0.4

    # ── session limits ──────────────────────────────────────────────────────
    idle_timeout: float = 180.0
    max_session_seconds: float = 600.0

    #: Mute the mic while the assistant speaks. See live.py for why.
    gate_mic_while_speaking: bool = True
    input_device: str | None = None
    output_device: str | None = None

    @property
    def model(self) -> str:
        """The model for the active provider."""
        return self.gemini_model if self.provider == "gemini" else self.openrouter_model

    @classmethod
    def load(cls) -> "Settings":
        if not SETTINGS_FILE.exists():
            return cls()
        raw = json.loads(SETTINGS_FILE.read_text())
        known = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in raw.items() if k in known})

    def save(self) -> None:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        SETTINGS_FILE.write_text(json.dumps(asdict(self), indent=2) + "\n")

    def set(self, name: str, value: str) -> Any:
        """Assign one setting from a string, coercing to the declared type."""
        known = {f.name: f for f in fields(self)}
        if name not in known:
            raise KeyError(name)

        declared = known[name].type
        text = str(declared)

        if "bool" in text:
            coerced: Any = value.strip().lower() in ("1", "true", "yes", "on")
        elif "int" in text:
            coerced = int(value)
        elif "float" in text:
            coerced = float(value)
        elif value.strip().lower() in ("none", "null", ""):
            coerced = None
        else:
            coerced = value

        if name == "provider" and coerced not in PROVIDERS:
            raise ValueError(f"provider must be one of {', '.join(PROVIDERS)}")

        setattr(self, name, coerced)
        return coerced


class MissingKeyError(RuntimeError):
    pass


def _read_keys() -> dict[str, str]:
    if not KEYS_FILE.exists():
        return {}
    try:
        data = json.loads(KEYS_FILE.read_text())
    except json.JSONDecodeError:
        return {}
    return {k: v for k, v in data.items() if isinstance(v, str)}


def load_key(provider: str) -> str:
    """The key for one provider: environment first, then the stored file."""
    for var in ENV_VARS.get(provider, ()):
        value = os.environ.get(var)
        if value:
            return value.strip()

    key = _read_keys().get(provider, "").strip()
    if key:
        return key

    raise MissingKeyError(
        f"No {provider} API key.\n"
        f"  Store one:  lura login --provider {provider}\n"
        f"  Get one at: {WHERE_TO_GET.get(provider, '')}"
    )


def save_key(provider: str, key: str) -> None:
    """Write one provider's key, 0600, leaving the other provider's alone."""
    key = key.strip()
    if not key:
        raise ValueError("Empty key.")

    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    os.chmod(CONFIG_DIR, 0o700)

    keys = _read_keys()
    keys[provider] = key

    # Created 0600 from the start rather than widened then narrowed, so the key
    # is never briefly world-readable.
    fd = os.open(KEYS_FILE, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as handle:
        json.dump(keys, handle, indent=2)
        handle.write("\n")


def forget_key(provider: str) -> bool:
    """Drop one provider's key. True if there was one."""
    keys = _read_keys()
    if provider not in keys:
        return False

    del keys[provider]
    fd = os.open(KEYS_FILE, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as handle:
        json.dump(keys, handle, indent=2)
        handle.write("\n")
    return True


def stored_providers() -> list[str]:
    return sorted(_read_keys())
