"""What can be configured, and the rules that decide what works with what.

The central rule: Lura is voice-first, and speech (both hearing and speaking)
only comes from Google's Gemini models — directly with a Gemini key, or
through OpenRouter. Any of the four providers can be the *brain* that
understands and answers. So a working setup needs one voice key (Gemini or
OpenRouter) and one brain key, which may be the same key.

Pure: no files, no network. Infrastructure persists it; application applies it.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field, fields
from typing import Any

GEMINI = "gemini"
OPENAI = "openai"
ANTHROPIC = "anthropic"
OPENROUTER = "openrouter"

PROVIDERS: tuple[str, ...] = (GEMINI, OPENAI, ANTHROPIC, OPENROUTER)
#: The providers that can hear and speak. See the module docstring.
VOICE_PROVIDERS: tuple[str, ...] = (GEMINI, OPENROUTER)

PROVIDER_INFO: dict[str, dict[str, Any]] = {
    GEMINI: {"label": "Google Gemini", "key_url": "https://aistudio.google.com/apikey",
             "env": ("GEMINI_API_KEY", "GOOGLE_API_KEY"), "voice": True, "realtime": True},
    OPENAI: {"label": "OpenAI", "key_url": "https://platform.openai.com/api-keys",
             "env": ("OPENAI_API_KEY",), "voice": False, "realtime": False},
    ANTHROPIC: {"label": "Anthropic (Claude)", "key_url": "https://console.anthropic.com/settings/keys",
                "env": ("ANTHROPIC_API_KEY",), "voice": False, "realtime": False},
    OPENROUTER: {"label": "OpenRouter", "key_url": "https://openrouter.ai/keys",
                 "env": ("OPENROUTER_API_KEY",), "voice": True, "realtime": False},
}

#: The brain each provider starts with. 3.8 Flash everywhere Google is
#: involved; it reads audio natively, so it can hear and think in one call.
DEFAULT_MODELS: dict[str, str] = {
    GEMINI: "gemini-3.8-flash",
    OPENAI: "gpt-5.5",
    ANTHROPIC: "claude-opus-5-5",
    OPENROUTER: "google/gemini-3.8-flash",
}

#: Suggestions for the model picker. Any id the provider accepts still works.
SUGGESTED_MODELS: dict[str, tuple[str, ...]] = {
    GEMINI: ("gemini-3.8-flash", "gemini-3.5-flash-lite", "gemini-3.1-pro-preview"),
    OPENAI: ("gpt-5.5", "gpt-5.4-mini", "gpt-5.4-nano", "gpt-5.5-pro"),
    ANTHROPIC: ("claude-opus-5-5", "claude-sonnet-5-5", "claude-haiku-5-5", "claude-fable-5-1"),
    OPENROUTER: ("google/gemini-3.8-flash", "anthropic/claude-sonnet-5.5", "openai/gpt-5.5",
                 "anthropic/claude-opus-5.5", "google/gemini-3.5-flash-lite"),
}

#: Speech out, per voice provider. Same model, two routes.
TTS_MODELS: dict[str, str] = {GEMINI: "gemini-3.8-flash-tts", OPENROUTER: "google/gemini-3.8-flash-tts"}
#: Speech in, for brains that cannot hear (Claude, GPT): transcribe first.
HEARING_MODELS: dict[str, str] = {GEMINI: "gemini-3.8-flash", OPENROUTER: "google/gemini-3.8-flash"}

#: The 30 prebuilt Gemini TTS voices, by how they sound. The list is what
#: OpenRouter reports as supported for gemini-3.8-flash-tts.
VOICES: dict[str, tuple[str, ...]] = {
    "female": ("Kore", "Zephyr", "Leda", "Aoede", "Callirrhoe", "Autonoe", "Despina", "Erinome",
               "Laomedeia", "Achernar", "Gacrux", "Pulcherrima", "Vindemiatrix", "Sulafat"),
    "male": ("Puck", "Charon", "Fenrir", "Orus", "Enceladus", "Iapetus", "Umbriel", "Algieba",
             "Algenib", "Rasalgethi", "Alnilam", "Schedar", "Achird", "Zubenelgenubi",
             "Sadachbia", "Sadaltager"),
}

MODES = ("pipeline", "realtime")
LANGUAGES = ("en", "ar")
EFFORTS = ("", "minimal", "low", "medium", "high")

#: Extra instructions from the user, added to the built-in prompt. Empty by
#: default: how Lura talks, hears and thinks lives in the assistant module.
DEFAULT_PERSONA = ""


def gender_of(voice: str) -> str:
    return "male" if voice in VOICES["male"] else "female"


@dataclass
class Settings:
    """Everything worth changing without editing code."""

    # ── language ──
    language: str = "en"            #: the conversation language, en or ar
    ui_language: str = "en"         #: the dashboard's language

    # ── the brain ──
    #: "pipeline": hear → think (any provider) → speak (Gemini TTS).
    #: "realtime": Gemini Live, one full-duplex audio socket; Gemini key only.
    mode: str = "pipeline"
    brain_provider: str = OPENROUTER
    models: dict[str, str] = field(default_factory=lambda: dict(DEFAULT_MODELS))
    #: How hard thinking models may think. "minimal" answers ~2 s sooner but
    #: misread follow-up questions in use; "low" is the default since 2.2.
    reasoning_effort: str = "low"
    #: OpenRouter upstream preference. Pinning lets Google's implicit prompt
    #: cache hit across turns; fallbacks stay on.
    openrouter_provider_order: list[str] = field(
        default_factory=lambda: ["google-vertex", "google-ai-studio"])

    # ── the voice ──
    voice_provider: str = "auto"    #: auto, gemini or openrouter
    voice_gender: str = "female"
    voice_name: str = "Kore"
    #: Optional delivery direction for Gemini TTS, e.g. "warm and calm".
    tts_style: str = ""
    realtime_model: str = "gemini-2.5-flash-native-audio-preview-12-2025"

    # ── who ──
    assistant_name: str = "Lura"
    user_name: str = ""
    #: "male", "female" or "" (unknown): how she addresses the user in Arabic.
    user_gender: str = ""
    persona: str = DEFAULT_PERSONA

    # ── wake word: always local Vosk, whichever provider answers ──
    wake_word: str = "lura"
    wake_cooldown: float = 1.5

    # ── end of turn (pipeline only; Gemini Live decides server-side) ──
    silence_factor: float = 2.0
    silence_ms: int = 900
    max_utterance_seconds: float = 30.0
    min_utterance_seconds: float = 0.4

    # ── session ──
    #: After Lura answers (or hears her name), how long she waits for you to
    #: start talking before she goes back to waiting for the wake word.
    follow_up_seconds: float = 8.0
    max_session_seconds: float = 3600.0
    gate_mic_while_speaking: bool = True
    input_device: str | None = None
    output_device: str | None = None

    # ── memory ──
    #: Keep every conversation (locally) and start the next one from it.
    memory_enabled: bool = True
    #: How much of the thread a request may carry. Once earlier conversations
    #: outgrow 60 % of it, their older part is folded into a summary.
    context_budget_tokens: int = 200_000

    # ── desktop ──
    #: The day's briefing (yesterday, today, suggestions — from the connected
    #: work services): "first_wake" the first time she is called each day,
    #: "boot" when the computer starts, or "off".
    daily_briefing: str = "first_wake"
    start_at_login: bool = True
    #: Look for a new release (GitHub) at start and every six hours.
    check_updates: bool = True
    overlay_enabled: bool = True
    #: What you said and what Lura answers, as subtitles under the face.
    subtitles: bool = True
    #: Write what you say while you are still saying it (one extra hearing
    #: call every ~1.5 s of speech).
    live_captions: bool = True
    dashboard_port: int = 47321
    onboarded: bool = False

    def model_for(self, provider: str) -> str:
        return self.models.get(provider) or DEFAULT_MODELS[provider]

    @property
    def brain_model(self) -> str:
        return self.model_for(self.brain_provider)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# ── validation ──────────────────────────────────────────────────────────────

_CHOICES: dict[str, tuple[str, ...]] = {
    "language": LANGUAGES, "ui_language": LANGUAGES, "mode": MODES,
    "brain_provider": PROVIDERS, "voice_provider": ("auto", *VOICE_PROVIDERS),
    "voice_gender": tuple(VOICES), "reasoning_effort": EFFORTS,
    "daily_briefing": ("first_wake", "boot", "off"),
    "context_budget_tokens": (32_000, 64_000, 128_000, 200_000),
    "user_gender": ("", "male", "female"),
}


def _coerce(default: Any, value: Any, name: str) -> Any:
    """Coerce a value (often a CLI string) to the type of the field's default."""
    if isinstance(default, bool):
        if isinstance(value, str):
            return value.strip().lower() in ("1", "true", "yes", "on")
        return bool(value)
    if isinstance(default, int) and not isinstance(default, bool):
        return int(value)
    if isinstance(default, float):
        return float(value)
    if isinstance(default, list):
        if isinstance(value, str):
            # "a,b" from the CLI; list("google-vertex") would be 13 letters.
            return [p.strip() for p in value.split(",") if p.strip()]
        return [str(v) for v in value]
    if isinstance(default, dict):
        if not isinstance(value, dict):
            raise ValueError(f"{name} must be an object")
        return {str(k): str(v) for k, v in value.items()}
    if default is None or name.endswith("_device"):
        if value is None or str(value).strip().lower() in ("", "none", "null"):
            return None
        return str(value)
    return str(value)


def apply_patch(settings: Settings, patch: dict[str, Any]) -> list[str]:
    """Validate and apply changes in place. Returns the names that changed.

    All or nothing: every value is checked before any is assigned.
    """
    defaults = Settings()
    known = {f.name for f in fields(Settings)}
    staged: dict[str, Any] = {}
    for name, value in patch.items():
        if name not in known:
            raise KeyError(name)
        coerced = _coerce(getattr(defaults, name), value, name)
        if name in _CHOICES and coerced not in _CHOICES[name]:
            raise ValueError(f"{name} must be one of: {', '.join(c or '(empty)' for c in _CHOICES[name])}")
        if name == "models":
            unknown = set(coerced) - set(PROVIDERS)
            if unknown:
                raise ValueError(f"unknown provider in models: {', '.join(sorted(unknown))}")
            coerced = {**settings.models, **{k: v for k, v in coerced.items() if v}}
        if name == "voice_name" and coerced not in VOICES["female"] + VOICES["male"]:
            raise ValueError(f"unknown voice {coerced!r}")
        staged[name] = coerced

    # A voice must match its gender; changing the gender alone picks the
    # first voice of that gender rather than leaving a mismatch.
    if "voice_gender" in staged and "voice_name" not in staged:
        if gender_of(settings.voice_name) != staged["voice_gender"]:
            staged["voice_name"] = VOICES[staged["voice_gender"]][0]
    if "voice_name" in staged:
        staged["voice_gender"] = gender_of(staged["voice_name"])

    changed = [n for n, v in staged.items() if getattr(settings, n) != v]
    for name, value in staged.items():
        setattr(settings, name, value)
    return changed


# ── what works with the keys you have ───────────────────────────────────────

@dataclass(frozen=True)
class Capabilities:
    """What the stored keys allow. ``problems`` are i18n keys, worst first."""

    keys: frozenset[str]
    brain_provider: str | None
    voice_provider: str | None
    realtime: bool
    problems: tuple[str, ...]

    @property
    def can_talk(self) -> bool:
        return not self.problems

    def to_dict(self) -> dict[str, Any]:
        return {"keys": sorted(self.keys), "brain_provider": self.brain_provider,
                "voice_provider": self.voice_provider, "realtime": self.realtime,
                "can_talk": self.can_talk, "problems": list(self.problems)}


def resolve_voice_provider(settings: Settings, keys: frozenset[str] | set[str]) -> str | None:
    if settings.voice_provider in VOICE_PROVIDERS:
        return settings.voice_provider if settings.voice_provider in keys else None
    for provider in VOICE_PROVIDERS:   # auto: direct Gemini first, it is one hop shorter
        if provider in keys:
            return provider
    return None


def capabilities(settings: Settings, keys: frozenset[str] | set[str]) -> Capabilities:
    keys = frozenset(keys)
    problems: list[str] = []
    if settings.mode == "realtime":
        if GEMINI not in keys:
            problems.append("problem.realtime_needs_gemini")
        return Capabilities(keys, GEMINI if GEMINI in keys else None,
                            GEMINI if GEMINI in keys else None, GEMINI in keys, tuple(problems))

    voice = resolve_voice_provider(settings, keys)
    if voice is None:
        problems.append("problem.no_voice_key" if settings.voice_provider == "auto"
                        else "problem.voice_provider_key_missing")
    brain = settings.brain_provider if settings.brain_provider in keys else None
    if brain is None:
        problems.append("problem.no_brain_key")
    return Capabilities(keys, brain, voice, GEMINI in keys, tuple(problems))


def brain_hears_audio(provider: str, model: str) -> bool:
    """Can this brain take the recording itself, skipping transcription?

    Gemini models read audio natively, and so do OpenAI's gpt-audio models
    on OpenRouter. Claude and the GPT-5 family do not — they get a transcript.
    """
    if provider == GEMINI:
        return True
    if provider == OPENROUTER:
        return model.startswith(("google/gemini", "openai/gpt-audio"))
    return False


def migrate_legacy(raw: dict[str, Any]) -> dict[str, Any]:
    """Settings files from Lura 1.x had one ``provider`` and flat model names."""
    if "daily_briefing" not in raw and raw.get("briefing_on_boot") is False:
        raw = {**raw, "daily_briefing": "off"}   # 2.2 and older: the boot briefing was switched off
    if "provider" not in raw and "openrouter_model" not in raw:
        return raw
    out = {k: v for k, v in raw.items() if k in {f.name for f in fields(Settings)}}
    if raw.get("provider") == GEMINI:
        out["mode"] = "realtime"
        out["brain_provider"] = GEMINI
    else:
        out["mode"] = "pipeline"
        out["brain_provider"] = OPENROUTER
    models = dict(DEFAULT_MODELS)
    if raw.get("openrouter_model"):
        models[OPENROUTER] = raw["openrouter_model"]
    out["models"] = models
    if raw.get("gemini_model"):
        out["realtime_model"] = raw["gemini_model"]
    if raw.get("voice") in VOICES["female"] + VOICES["male"]:
        out["voice_name"] = raw["voice"]
        out["voice_gender"] = gender_of(raw["voice"])
    reply = str(raw.get("reply_language", "")).lower()
    out["language"] = "ar" if reply.startswith("arab") else "en"
    out["onboarded"] = True
    return out
