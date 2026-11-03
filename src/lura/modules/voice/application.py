"""Hearing and speaking, wired for the current settings."""

from __future__ import annotations

import logging
import queue
import threading
import time

from ...shared.http import HttpError
from ...shared.presence import NullPresence, Presence
from ..settings import (ELEVENLABS, GEMINI, HEARING_MODELS, OPENROUTER, TTS_MODELS, VOICE_PROVIDERS, VOICES, Settings,
                        resolve_voice_provider)
from .domain import SentenceChunker, SpeechSynthesizer, Transcriber, retry_after

log = logging.getLogger(__name__)

_DONE = object()


class _Piece(str):
    """Marks where a sentence's audio starts in the playback queue."""


class SpeechPlayer:
    """Speaks streamed text as it arrives.

    Three stages, so nothing waits on anything it does not need: the caller
    feeds text; a synthesis thread turns finished sentences into audio,
    streamed as it is made; a playback thread plays it in order. While
    sentence one plays, sentence two is already being synthesised and
    sentence three still being written.

    ``interrupt()`` stops the sound within one 50 ms slice and drops what the
    device had buffered; ``heard`` is then what the listener actually got to
    hear, which is what the history must say she said.
    """

    #: Audio synthesised ahead of the speaker, in bytes (30 s at 24 kHz). By
    #: time, not by item: streamed chunks are ~70 ms, and a count of them would
    #: stop sentence two from being fetched while sentence one plays.
    MAX_AHEAD = 30 * 2 * 24_000

    def __init__(self, synth: SpeechSynthesizer, device=None, presence: Presence | None = None,
                 listener=None):
        self._synth = synth
        self._device = device
        self._presence = presence or NullPresence()
        #: Hears the room while she speaks (barge-in); gets each played slice.
        self._listener = listener
        self._chunker = SentenceChunker()
        self._text: queue.Queue = queue.Queue()
        self._audio: queue.Queue = queue.Queue()
        self._room = threading.Condition()
        self._ahead = 0
        self._cancelled = threading.Event()
        self.interrupted = False
        self.spoken: list[str] = []
        self.heard: list[str] = []
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
        # The playback thread ends after the synthesis thread's last piece, or
        # at once when cancelled. Joining synthesis too would keep an
        # interrupted Lura deaf for as long as an HTTP call takes to return.
        self._threads[1].join()
        if self._listener is not None:
            self._listener.released.wait(0.3)   # its mic lets go before the recorder opens one

    def cancel(self) -> None:
        self._cancelled.set()
        self._text.put(_DONE)
        with self._room:
            self._room.notify_all()
        self._audio.put(_DONE)

    def interrupt(self) -> None:
        """The user wants the floor: stop talking now."""
        if not self._cancelled.is_set():
            self.interrupted = True
            log.info("Interrupted after: %r", " ".join(self.heard)[-80:])
        self.cancel()

    @property
    def speaking(self) -> bool:
        """Sound has started and not been stopped."""
        return bool(self.heard) and not self._cancelled.is_set() and self._threads[1].is_alive()

    @property
    def carry(self):
        """After a barge-in: the user's first words and the mic still open on
        them, for ``VoiceService.record``. Opaque to everyone else."""
        return self._listener.carry if self._listener is not None else None

    def _queue_audio(self, pcm) -> None:
        with self._room:
            self._room.wait_for(lambda: self._ahead < self.MAX_AHEAD or self._cancelled.is_set())
            self._ahead += len(pcm)
        self._audio.put(pcm)

    def _synth_loop(self) -> None:
        while True:
            piece = self._text.get()
            if piece is _DONE or self._cancelled.is_set():
                self._audio.put(_DONE)
                return
            started = False
            try:
                for pcm in self._synth.stream(piece):
                    if self._cancelled.is_set():
                        break   # closes the HTTP stream
                    if not pcm:
                        continue
                    if not started:
                        started = True
                        self.spoken.append(piece)
                        self._audio.put(_Piece(piece))
                    self._queue_audio(pcm)
            except Exception as exc:
                # One sentence that cannot be spoken must not silence the rest.
                log.warning("Could not speak %r: %s", piece[:40], exc)

    def _play_loop(self) -> None:
        from .infrastructure.audio import Speaker

        speaker = None
        try:
            while True:
                item = self._audio.get()
                if item is _DONE or self._cancelled.is_set():
                    return
                if isinstance(item, _Piece):
                    self.heard.append(str(item))
                    continue
                with self._room:
                    self._ahead -= len(item)
                    self._room.notify_all()
                if speaker is None:
                    speaker = Speaker(self._device, on_level=self._presence.set_amplitude,
                                      on_slice=self._listener.played if self._listener else None)
                    self._presence.set_state("speaking")
                    if self._listener is not None:
                        self._listener.start(self)
                speaker.write(item, self._cancelled.is_set)
        except Exception as exc:
            log.warning("Playback failed: %s", exc)
        finally:
            if speaker is not None:
                speaker.close(abort=self._cancelled.is_set())
            if self._listener is not None:
                self._listener.stop()


class _FallbackSynthesizer:
    """Speak through the second voice provider when the first is out of quota,
    overloaded or slow, instead of going silent."""

    def __init__(self, primary: SpeechSynthesizer, backup: SpeechSynthesizer, on_429, any_error: bool = False):
        #: ``any_error``: ElevenLabs reports used-up credit as a 401, not a retryable status.
        self._primary, self._backup, self._on_429, self._any = primary, backup, on_429, any_error

    def synthesize(self, text: str) -> bytes:
        try:
            return self._primary.synthesize(text)
        except HttpError as exc:
            if not (exc.retryable or self._any):
                raise
            if exc.status == 429:
                self._on_429(exc)
            log.warning("Primary TTS failed (%s); falling back to alternate voice provider.", exc.status or exc)
            return self._backup.synthesize(text)

    def stream(self, text: str):
        started = False
        try:
            for pcm in self._primary.stream(text):
                started = True
                yield pcm
        except HttpError as exc:
            # Mid-sentence, the backup would start the sentence over.
            if started or not (exc.retryable or self._any):
                raise
            if exc.status == 429:
                self._on_429(exc)
            log.warning("Primary TTS failed (%s); falling back to alternate voice provider.", exc.status or exc)
            yield from self._backup.stream(text)


def elevenlabs_voice(settings: Settings, account_voices) -> str:
    """The chosen ElevenLabs voice for the current gender; else the account's first voice
    of that gender (the one the dashboard shows as picked); else George."""
    from .infrastructure.elevenlabs import FALLBACK_VOICE

    gender = "male" if settings.voice_gender == "male" else "female"
    if chosen := getattr(settings, f"elevenlabs_voice_{gender}"):
        return chosen
    try:
        listed = account_voices()
    except Exception as exc:
        log.warning("Could not list ElevenLabs voices (%s); using the default voice.", exc)
        return FALLBACK_VOICE
    same = [v for v in listed if v["gender"] == gender] or listed
    return same[0]["id"] if same else FALLBACK_VOICE


class _OfflineFallback:
    """Speak with Piper when every online voice failed, whatever the reason
    (out of quota, out of credit, no network): a silent Lura is the worst case."""

    def __init__(self, online: SpeechSynthesizer, offline: SpeechSynthesizer):
        self._online, self._offline = online, offline

    def synthesize(self, text: str) -> bytes:
        try:
            return self._online.synthesize(text)
        except Exception as exc:
            if not _offline_ready():
                raise
            log.warning("Online voice failed (%s); speaking with the offline voice.", str(exc)[:120])
            return self._offline.synthesize(text)

    def stream(self, text: str):
        started = False
        try:
            for pcm in self._online.stream(text):
                started = True
                yield pcm
        except Exception as exc:
            if started or not _offline_ready():
                raise
            log.warning("Online voice failed (%s); speaking with the offline voice.", str(exc)[:120])
            yield from self._offline.stream(text)


def _offline_ready() -> bool:
    from .infrastructure.piper import ready
    return ready()


class _FallbackTranscriber:
    """Hear through the second voice provider when the first fails."""

    def __init__(self, primary: Transcriber, backup: Transcriber, on_429, any_error: bool = False):
        self._primary, self._backup, self._on_429, self._any = primary, backup, on_429, any_error

    def transcribe(self, wav: bytes) -> str:
        try:
            return self._primary.transcribe(wav)
        except HttpError as exc:
            if not (exc.retryable or self._any):
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
        self._streaming = None   # tts.StreamSupport, made on first use
        self._models: tuple[float, list[dict]] | None = None
        self._el_voices: tuple[float, list[dict]] | None = None

    def _voice_providers(self, settings: Settings) -> list[str]:
        from ...shared.errors import NeedsSetup

        keys = {p for p in VOICE_PROVIDERS if self._has_key(p)}
        provider = resolve_voice_provider(settings, keys)
        if provider is None:
            raise NeedsSetup("Lura's voice needs a Gemini, OpenRouter or ElevenLabs key.")
        # auto falls back between the two Gemini routes; ElevenLabs is chosen by name.
        order = [p for p in (GEMINI, OPENROUTER) if p in keys]
        if provider == ELEVENLABS:   # out of credit or down: hear and speak through Gemini instead
            return [ELEVENLABS, *order[:1]]
        if settings.voice_provider != "auto" or len(order) < 2:
            return [provider]
        now = time.monotonic()
        order.sort(key=lambda p: self._cooldown_until.get(p, 0.0) > now)
        return order

    def _mark_429(self, provider: str, exc: HttpError) -> None:
        wait = retry_after(str(exc))
        self._cooldown_until[provider] = time.monotonic() + wait
        log.warning("%s voice is out of quota; using the other provider for %.0f min.", provider, wait / 60)

    def synthesizer(self, settings: Settings, voice: str | None = None, offline: bool = True) -> SpeechSynthesizer:
        """``offline``: Piper behind it all — off where the online voice itself is being checked."""
        from .infrastructure.tts import GeminiTTS, OpenRouterTTS, StreamSupport

        if self._streaming is None:
            self._streaming = StreamSupport()

        from .infrastructure.tts import is_gemini_tts

        gemini_voice = voice if voice in VOICES["female"] + VOICES["male"] else settings.voice_name

        def build(p: str) -> SpeechSynthesizer:
            if p == ELEVENLABS:
                from .infrastructure.elevenlabs import ElevenLabsTTS
                return ElevenLabsTTS(self._key_for(p), settings.elevenlabs_model,
                                     voice or elevenlabs_voice(settings, self.elevenlabs_voices))
            if p == GEMINI:
                return GeminiTTS(self._key_for(p), TTS_MODELS[p], gemini_voice, settings.tts_style,
                                 lite_fallback=len(providers) == 1, streaming=self._streaming)
            model = settings.openrouter_voice_model or TTS_MODELS[p]
            if is_gemini_tts(model):
                return OpenRouterTTS(self._key_for(p), model, gemini_voice, settings.tts_style,
                                     lite_fallback=len(providers) == 1)
            # Another speech model: its own voices, and no Gemini-style direction in the text.
            # Free ones run out (50 requests a day): speak with Gemini until the reset.
            gemini = OpenRouterTTS(self._key_for(p), TTS_MODELS[p], gemini_voice, settings.tts_style)
            if self._cooldown_until.get(model, 0.0) > time.monotonic():
                return gemini
            own = OpenRouterTTS(self._key_for(p), model, voice or settings.openrouter_voice, "",
                                lite_fallback=False)
            return _FallbackSynthesizer(own, gemini, lambda exc: self._mark_429(model, exc))

        from .infrastructure.piper import PiperTTS

        providers = self._voice_providers(settings)
        primary = build(providers[0])
        if len(providers) > 1:
            primary = _FallbackSynthesizer(primary, build(providers[1]),
                                           lambda exc: self._mark_429(providers[0], exc),
                                           any_error=providers[0] == ELEVENLABS)
        return _OfflineFallback(primary, PiperTTS()) if offline else primary

    def speech_models(self) -> list[dict]:
        """OpenRouter's speech models with their voices, fetched at most once an hour."""
        from .infrastructure.tts import speech_models

        now = time.monotonic()
        if self._models is None or now - self._models[0] > 3600:
            self._models = (now, speech_models())
        return self._models[1]

    def elevenlabs_voices(self) -> list[dict]:
        """The ElevenLabs account's voices with their gender, fetched at most once an hour."""
        from .infrastructure.elevenlabs import voices

        now = time.monotonic()
        if self._el_voices is None or now - self._el_voices[0] > 3600:
            self._el_voices = (now, voices(self._key_for(ELEVENLABS)))
        return self._el_voices[1]

    def check_key(self, provider: str, key: str) -> str:
        """For keys that only hear and speak (ElevenLabs)."""
        from .infrastructure.elevenlabs import check_key
        self._el_voices = None
        return check_key(key)

    def transcriber(self, settings: Settings) -> Transcriber:
        from .infrastructure.transcribe import ChatTranscriber

        def build(p: str) -> Transcriber:
            if p == ELEVENLABS:
                from .infrastructure.elevenlabs import ElevenLabsTranscriber
                return ElevenLabsTranscriber(self._key_for(p))
            return ChatTranscriber(p, self._key_for(p), HEARING_MODELS[p])

        providers = self._voice_providers(settings)
        primary = build(providers[0])
        if len(providers) == 1:
            return primary
        return _FallbackTranscriber(primary, build(providers[1]), lambda exc: self._mark_429(providers[0], exc),
                                    any_error=providers[0] == ELEVENLABS)

    def player(self, settings: Settings, presence: Presence | None = None, listen: bool = False) -> SpeechPlayer:
        """``listen``: a conversation reply — the user may talk over it (barge-in)."""
        listener = None
        if listen and settings.barge_in:
            from .infrastructure.barge_in import BargeInListener
            listener = BargeInListener(settings)
        return SpeechPlayer(self.synthesizer(settings), settings.output_device, presence, listener)

    def preview(self, settings: Settings, voice: str, text: str) -> None:
        """Say a sample line in one voice — the dashboard's ▶ button."""
        player = SpeechPlayer(self.synthesizer(settings, voice, offline=False), settings.output_device)
        player.say(text)
        player.wait()

    # Thin re-exports so the assistant never imports voice infrastructure.
    @staticmethod
    def record(settings: Settings, should_stop=lambda: False, on_audio=None, carry=None) -> bytes:
        from .infrastructure.audio import record_utterance
        return record_utterance(settings, should_stop, on_audio, carry)

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
    def ensure_offline_voice() -> bool:
        """Install Piper and its voices if missing (downloads ~150 MB once)."""
        from .infrastructure.piper import ensure
        return ensure()

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
