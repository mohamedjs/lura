"""Settings, paths, and the API keys.

Two providers, two very different shapes:

* ``gemini``     — the Live API. Full-duplex native audio: your voice streams
                   up, speech streams back, the server decides when you have
                   stopped talking.
* ``openrouter`` — turn-based, and the default. Record an utterance, hand the
                   audio itself to a model that reads it, run whatever tools it
                   asks for, speak the reply. Slower and not interruptible, but
                   it works with every model on the service and costs a
                   fraction of a cent an exchange.

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
    provider: str = "openrouter"

    # ── models, kept per provider so switching back does not lose the other ──
    # Preview ids churn. If one is retired the error reads "model not found".
    gemini_model: str = "gemini-2.5-flash-native-audio-preview-12-2025"
    #: Reads audio natively *and* calls tools, so one request covers hearing,
    #: thinking and deciding — no separate transcription step, and no local
    #: Vosk (that model is en-US only and would mistranscribe Arabic).
    #: `gemini-3.1-flash-lite` is the fast, cheap option — 2.5s and $0.0005 a
    #: turn against 6-11s and $0.0025 here — but it is the weaker instruction
    #: follower: asked in English it still answered in Arabic. Chosen for
    #: answer quality over speed.
    openrouter_model: str = "google/gemini-3.8-flash"
    #: English only, and free — but "free" on OpenRouter means 50 requests a
    #: day without credits, and the limit is shared across every :free model
    #: on the account. When it runs out this 429s and the fallback answers.
    openrouter_tts_model: str = "deepgram/flux-tts:free"
    openrouter_tts_voice: str = "flux-alexis-en"
    #: Same vendor, paid, English: it sounds like the primary rather than like
    #: a different assistant when the free tier is spent. fish-audio/s1 is the
    #: one to come back to if Arabic speech is ever wanted again — deepgram
    #: does not speak it.
    openrouter_tts_fallback_model: str = "deepgram/aura-2"
    #: Voice names are vendor-specific; the fallback rejects the primary's.
    openrouter_tts_fallback_voice: str = "aura-2-thalia-en"
    #: Which upstream OpenRouter should prefer, most wanted first.
    #:
    #: Google caches a repeated prompt prefix implicitly, but only on the
    #: machine that saw it: left to route freely, consecutive turns land on
    #: different upstreams and none of them hit. Ordinary turns are now small
    #: enough not to care, but a turn where `find_tools` attaches MCP schemas
    #: is not, and that is where this earns its keep. Fallbacks stay on, so a
    #: pinned upstream that is down costs money rather than silence.
    openrouter_provider_order: tuple[str, ...] = ("google-vertex", "google-ai-studio")
    #: How hard the model may think before answering. The thinking models spend
    #: it on deciding which tool to call, which for "what is my CPU
    #: temperature" is not a decision worth 367 tokens: minimal cut a
    #: gemini-3.8-flash turn from 9.3s and $0.0028 to 7.4s and $0.0014 with the
    #: same answer. Harmless on models that do not think. Empty to leave it
    #: to the model; not every endpoint lets it be switched off entirely.
    openrouter_reasoning_effort: str = "minimal"

    # ── voice ───────────────────────────────────────────────────────────────
    voice: str = "Puck"          # Gemini Live voice
    language: str = "en-US"
    #: Force every spoken answer into one language. Empty mirrors whoever is
    #: speaking, which is right for Gemini Live. It is wrong when the voice
    #: only speaks English: the model answers the Arabic it heard in Arabic,
    #: and an English synthesiser reads that as noise.
    reply_language: str = "English"
    system_instruction: str = (
        "You are Lura, a spoken assistant. Keep answers short and "
        "conversational, as if talking out loud. Do not use markdown or lists. "
        # The language rule is repeated at the end of the machine context too:
        # buried in the middle of a long system message it gets ignored, and
        # an Arabic question comes back answered in English.
        "Always reply in the same language the user spoke to you in."
    )

    # ── wake word (always local, always Vosk, whichever provider answers) ────
    wake_word: str = "lura"
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
    max_session_seconds: float = 3600.0

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
