"""Hearing and speaking, wired for the current settings."""

from __future__ import annotations

import logging
import queue
import threading

from ...shared.presence import NullPresence, Presence
from ..settings import GEMINI, HEARING_MODELS, TTS_MODELS, Settings, resolve_voice_provider
from .domain import SentenceChunker, SpeechSynthesizer, Transcriber

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


class VoiceService:
    def __init__(self, key_for, has_key):
        self._key_for = key_for
        self._has_key = has_key

    def _voice_provider(self, settings: Settings) -> str:
        from ...shared.errors import NeedsSetup

        keys = {p for p in (GEMINI, "openrouter") if self._has_key(p)}
        provider = resolve_voice_provider(settings, keys)
        if provider is None:
            raise NeedsSetup("Lura's voice needs a Gemini or an OpenRouter key.")
        return provider

    def synthesizer(self, settings: Settings, voice: str | None = None) -> SpeechSynthesizer:
        from .infrastructure.tts import GeminiTTS, OpenRouterTTS

        provider = self._voice_provider(settings)
        cls = GeminiTTS if provider == GEMINI else OpenRouterTTS
        return cls(self._key_for(provider), TTS_MODELS[provider], voice or settings.voice_name,
                   settings.tts_style)

    def transcriber(self, settings: Settings) -> Transcriber:
        from .infrastructure.transcribe import ChatTranscriber

        provider = self._voice_provider(settings)
        return ChatTranscriber(provider, self._key_for(provider), HEARING_MODELS[provider])

    def player(self, settings: Settings, presence: Presence | None = None) -> SpeechPlayer:
        return SpeechPlayer(self.synthesizer(settings), settings.output_device, presence)

    def preview(self, settings: Settings, voice: str, text: str) -> None:
        """Say a sample line in one voice — the dashboard's ▶ button."""
        player = SpeechPlayer(self.synthesizer(settings, voice), settings.output_device)
        player.say(text)
        player.wait()

    # Thin re-exports so the assistant never imports voice infrastructure.
    @staticmethod
    def record(settings: Settings, should_stop=lambda: False) -> bytes:
        from .infrastructure.audio import record_utterance
        return record_utterance(settings, should_stop)

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
