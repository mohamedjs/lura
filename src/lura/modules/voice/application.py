"""Hearing and speaking, wired for the current settings."""

from __future__ import annotations

import logging
import queue
import threading
import time

from ...shared.http import HttpError
from ...shared.presence import NullPresence, Presence
from ..settings import GEMINI, HEARING_MODELS, OPENROUTER, TTS_MODELS, Settings, resolve_voice_provider
from .domain import SentenceChunker, SpeechSynthesizer, Transcriber, retry_after

log = logging.getLogger(__name__)

_DONE = object()


class SpeechPlayer:
    """Speaks streamed text as it arrives.

    Three stages, so nothing waits on anything it does not need: the caller
    feeds text; a synthesis thread turns finished sentences into audio; a
    playback thread plays them in order. While sentence one plays, sentence
    two is already being synthesised and sentence three still being written.
    """

    def __init__(self, synth: SpeechSynthesizer, device=None, presence: Presence | None = None):
        self._synth = synth
        self._device = device
        self._presence = presence or NullPresence()
        self._chunker = SentenceChunker()
        self._text: queue.Queue = queue.Queue()
        self._audio: queue.Queue = queue.Queue(maxsize=4)
        self._cancelled = threading.Event()
        self.spoken: list[str] = []
        self._threads = [threading.Thread(target=self._synth_loop, daemon=True, name="tts"),
                         threading.Thread(target=self._play_loop, daemon=True, name="playback")]
        for thread in self._threads:
            thread.start()

    def feed(self, text: str) -> None:
        for piece in self._chunker.feed(text):
            self._text.put(piece)

    def say(self, text: str) -> None:
        self.feed(text)
        self.finish()

    def finish(self) -> None:
        for piece in self._chunker.flush():
            self._text.put(piece)
        self._text.put(_DONE)

    def wait(self) -> None:
        for thread in self._threads:
            thread.join()

    def cancel(self) -> None:
        self._cancelled.set()
        self._text.put(_DONE)

    def _synth_loop(self) -> None:
        while True:
            piece = self._text.get()
            if piece is _DONE or self._cancelled.is_set():
                self._audio.put(_DONE)
                return
            try:
                pcm = self._synth.synthesize(piece)
            except Exception as exc:
                # One sentence that cannot be spoken must not silence the rest.
                log.warning("Could not speak %r: %s", piece[:40], exc)
                continue
            if pcm:
                self.spoken.append(piece)
                self._audio.put(pcm)

    def _play_loop(self) -> None:
        from .infrastructure.audio import Speaker

        speaker = None
        try:
            while True:
                pcm = self._audio.get()
                if pcm is _DONE or self._cancelled.is_set():
                    return
                if speaker is None:
                    speaker = Speaker(self._device, on_level=self._presence.set_amplitude)
                    self._presence.set_state("speaking")
                speaker.write(pcm, self._cancelled.is_set)
        except Exception as exc:
            log.warning("Playback failed: %s", exc)
        finally:
            if speaker is not None:
                speaker.close()


class _FallbackSynthesizer:
    """Speak through the second voice provider when the first is out of quota,
    overloaded or slow, instead of going silent."""

    def __init__(self, primary: SpeechSynthesizer, backup: SpeechSynthesizer, on_429):
        self._primary, self._backup, self._on_429 = primary, backup, on_429

    def synthesize(self, text: str) -> bytes:
        try:
            return self._primary.synthesize(text)
        except HttpError as exc:
            if not exc.retryable:
                raise
            if exc.status == 429:
                self._on_429(exc)
            log.warning("Primary TTS failed (%s); falling back to alternate voice provider.", exc.status or exc)
            return self._backup.synthesize(text)


class _FallbackTranscriber:
    """Hear through the second voice provider when the first fails."""

    def __init__(self, primary: Transcriber, backup: Transcriber, on_429):
        self._primary, self._backup, self._on_429 = primary, backup, on_429

    def transcribe(self, wav: bytes) -> str:
        try:
            return self._primary.transcribe(wav)
        except HttpError as exc:
            if not exc.retryable:
                raise
            if exc.status == 429:
                self._on_429(exc)
            log.warning("Primary hearing failed (%s); falling back to alternate voice provider.", exc.status or exc)
            return self._backup.transcribe(wav)


class VoiceService:
    def __init__(self, key_for, has_key):
        self._key_for = key_for
        self._has_key = has_key
        self._cooldown_until: dict[str, float] = {}

    def _voice_providers(self, settings: Settings) -> list[str]:
        from ...shared.errors import NeedsSetup

        keys = {p for p in (GEMINI, OPENROUTER) if self._has_key(p)}
        provider = resolve_voice_provider(settings, keys)
        if provider is None:
            raise NeedsSetup("Lura's voice needs a Gemini or an OpenRouter key.")
        if settings.voice_provider != "auto" or len(keys) < 2:
            return [provider]
        now = time.monotonic()
        order = [GEMINI, OPENROUTER]
        order.sort(key=lambda p: self._cooldown_until.get(p, 0.0) > now)
        return order

    def _mark_429(self, provider: str, exc: HttpError) -> None:
        wait = retry_after(str(exc))
        self._cooldown_until[provider] = time.monotonic() + wait
        log.warning("%s voice is out of quota; using the other provider for %.0f min.", provider, wait / 60)

    def synthesizer(self, settings: Settings, voice: str | None = None) -> SpeechSynthesizer:
        from .infrastructure.tts import GeminiTTS, OpenRouterTTS

        def build(p: str) -> SpeechSynthesizer:
            cls = GeminiTTS if p == GEMINI else OpenRouterTTS
            return cls(self._key_for(p), TTS_MODELS[p], voice or settings.voice_name, settings.tts_style,
                       lite_fallback=len(providers) == 1)

        providers = self._voice_providers(settings)
        primary = build(providers[0])
        if len(providers) == 1:
            return primary
        return _FallbackSynthesizer(primary, build(providers[1]), lambda exc: self._mark_429(providers[0], exc))

    def transcriber(self, settings: Settings) -> Transcriber:
        from .infrastructure.transcribe import ChatTranscriber

        def build(p: str) -> Transcriber:
            return ChatTranscriber(p, self._key_for(p), HEARING_MODELS[p])

        providers = self._voice_providers(settings)
        primary = build(providers[0])
        if len(providers) == 1:
            return primary
        return _FallbackTranscriber(primary, build(providers[1]), lambda exc: self._mark_429(providers[0], exc))

    def player(self, settings: Settings, presence: Presence | None = None) -> SpeechPlayer:
        return SpeechPlayer(self.synthesizer(settings), settings.output_device, presence)

    def preview(self, settings: Settings, voice: str, text: str) -> None:
        """Say a sample line in one voice — the dashboard's ▶ button."""
        player = SpeechPlayer(self.synthesizer(settings, voice), settings.output_device)
        player.say(text)
        player.wait()

    # Thin re-exports so the assistant never imports voice infrastructure.
    @staticmethod
    def record(settings: Settings, should_stop=lambda: False, on_audio=None) -> bytes:
        from .infrastructure.audio import record_utterance
        return record_utterance(settings, should_stop, on_audio)

    @staticmethod
    def level(pcm: bytes) -> float:
        from .infrastructure.audio import rms_of
        return rms_of(pcm)

    @staticmethod
    def to_wav(pcm: bytes) -> bytes:
        from .infrastructure.audio import pcm_to_wav
        return pcm_to_wav(pcm)

    @staticmethod
    def devices() -> dict[str, list[dict]]:
        from .infrastructure.audio import list_devices
        return list_devices()

    @staticmethod
    def mic_test(device) -> dict:
        from .infrastructure.audio import mic_test
        return mic_test(device)

    @staticmethod
    def speaker_test(device) -> None:
        from .infrastructure.audio import chime
        chime(device=device)

    @staticmethod
    def chime(settings: Settings, up: bool = True) -> None:
        from .infrastructure.audio import chime
        chime(device=settings.output_device, up=up)

    @staticmethod
    def wake_listener(settings: Settings):
        from .infrastructure.wake import WakeListener
        return WakeListener(settings.wake_word, settings.input_device, settings.wake_cooldown)

    @staticmethod
    def wake_word_known(word: str) -> bool | None:
        try:
            from .infrastructure.wake import word_known
            return word_known(word)
        except Exception as exc:   # no vosk in this build, a broken model: do not block saving
            log.debug("Cannot check the wake word: %s", exc)
            return None

    @staticmethod
    def ensure_wake_model(progress=None):
        from .infrastructure.wake import ensure_model
        return ensure_model(progress)

    @staticmethod
    def wake_model_present() -> bool:
        from .infrastructure.wake import model_dir
        return model_dir().exists()

    def realtime(self, settings: Settings, system: str, tools, dispatch, presence,
                 initial_prompt: str | None = None):
        from .infrastructure.gemini_live import LiveConversation
        return LiveConversation(self._key_for(GEMINI), settings, system, tools, dispatch,
                                presence, initial_prompt)

    def probe_realtime(self, settings: Settings) -> str:
        from .infrastructure.gemini_live import probe
        return probe(self._key_for(GEMINI), settings.realtime_model, settings.voice_name)
