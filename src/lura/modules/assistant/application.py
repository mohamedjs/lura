"""The assistant: wake word → conversation → back to listening.

Two conversation shapes, deliberately not unified, because they differ in who
owns turn detection:

* **pipeline** — we record until the user stops, the brain (any provider)
  answers in a stream, and Gemini TTS speaks it sentence by sentence while
  the rest is still being written.
* **realtime** — Gemini Live owns the turn; audio flows both ways at once.
"""

from __future__ import annotations

import logging
import threading
import time

from ...shared import events
from ...shared.errors import NeedsSetup
from ...shared.events import EventBus
from ...shared.http import HttpError
from ...shared.i18n import t
from ...shared.presence import NullPresence, Presence
from ..brain import BrainService, Done, Message, TextDelta
from ..settings import Settings, SettingsService, brain_hears_audio
from ..tools import FIND_TOOLS, ToolService, ToolSession
from ..voice import VoiceService
from . import domain

log = logging.getLogger(__name__)


class PipelineConversation:
    def __init__(self, settings: Settings, brain: BrainService, voice: VoiceService,
                 tools: ToolService, presence: Presence, stopped: threading.Event):
        self.s = settings
        self._model = brain.model(settings)
        self._voice = voice
        self._tools = tools
        self._presence = presence
        self._stopped = stopped
        self._session = ToolSession()
        self._history: list[Message] = []
        self._ending = False
        self._hears = brain_hears_audio(self._model.provider, self._model.model)
        self._transcriber = None if self._hears else voice.transcriber(settings)
        self._system = domain.system_prompt(settings.persona, settings.assistant_name,
                                            settings.user_name, settings.language,
                                            tools.machine_context())
        log.info("brain %s/%s (%s), voice %s", self._model.provider, self._model.model,
                 "hears audio" if self._hears else "via transcript", settings.voice_name)

    # ── one model round ──
    def _stream(self, with_tools: bool) -> Message | None:
        self._presence.set_state("thinking")
        player = self._voice.player(self.s, self._presence)
        done: Done | None = None
        said = ""
        try:
            for event in self._model.stream(self._system, domain.trim_history(self._history),
                                            self._tools.specs(self._session) if with_tools else []):
                if self._stopped.is_set():
                    player.cancel()
                    return None
                if isinstance(event, TextDelta):
                    said += event.text
                    self._presence.set_transcript(f"{self.s.assistant_name}: {said.strip()}")
                    player.feed(event.text)
                else:
                    done = event
        except HttpError as exc:
            player.cancel()
            log.warning("Brain failed: %s", exc)
            self._say(t("assistant.brain_error", self.s.language))
            return None
        player.finish()
        if done is None:
            player.wait()
            return None
        message = done.message
        self._history.append(message)
        if done.stop_reason == "refusal" and not message.text:
            player.wait()
            self._say(t("assistant.refused", self.s.language))
            return message
        if message.tool_calls:
            # Pre-tool words keep playing while the tools run.
            self._run_tools(message)
        player.wait()
        if message.text:
            log.info("lura: %s", message.text)
        return message

    def _say(self, text: str) -> None:
        try:
            player = self._voice.player(self.s, self._presence)
            player.say(text)
            player.wait()
        except Exception as exc:
            log.warning("Could not say %r: %s", text, exc)

    def _run_tools(self, message: Message) -> None:
        for call in message.tool_calls:
            if not call.valid:
                text, ends = (f"Error: the arguments for {call.name} were incomplete or not valid "
                              f"JSON ({call.raw_arguments[:200]!r}). Call it again correctly.", False)
            else:
                log.info("tool %s %s", call.name, call.arguments)
                text, ends = self._tools.dispatch(call.name, call.arguments, self._session)
            self._ending = self._ending or ends
            self._history.append(Message("tool", text[:6000], tool_call_id=call.id,
                                         tool_name=call.name, is_error=not call.valid))

    # ── one user turn ──
    def _answer(self) -> None:
        rounds = 0
        while not self._stopped.is_set():
            message = self._stream(with_tools=True)
            if message is None or not message.tool_calls:
                return
            if self._ending:
                # A user turn, not tool output: instructions inside a tool
                # result get ignored and the turn ends in silence.
                self._history.append(Message("user", domain.GOODBYE_NUDGE[self.s.language]))
                self._stream(with_tools=False)
                return
            # find_tools changes nothing outside this process; charging it
            # against the budget lets two bad searches end the turn in silence.
            if any(c.name != FIND_TOOLS for c in message.tool_calls):
                rounds += 1
            if rounds >= domain.MAX_TOOL_ROUNDS:
                log.warning("Model used all %d tool rounds; asking it to answer.", rounds)
                self._history.append(Message("user", domain.ANSWER_NOW[self.s.language]))
                self._stream(with_tools=False)
                return

    def run(self, initial_prompt: str | None = None) -> None:
        started = time.monotonic()
        if initial_prompt:
            self._history.append(Message("user", initial_prompt))
            self._answer()
            return
        while not self._ending and not self._stopped.is_set():
            if time.monotonic() - started > self.s.max_session_seconds:
                log.info("Session hit its time limit.")
                return
            self._presence.set_state("listening")
            pcm = self._voice.record(self.s, self._stopped.is_set)
            if not pcm:
                log.info("Nothing said — closing.")
                return
            wav = self._voice.to_wav(pcm)
            if self._hears:
                self._history.append(Message("user", audio_wav=wav))
            else:
                self._presence.set_state("thinking")
                try:
                    heard = self._transcriber.transcribe(wav)
                except HttpError as exc:
                    log.warning("Hearing failed: %s", exc)
                    self._say(t("assistant.hearing_error", self.s.language))
                    return
                if not heard:
                    continue
                self._presence.set_transcript(f"You: {heard}")
                self._history.append(Message("user", heard))
            self._answer()


class Assistant:
    def __init__(self, settings: SettingsService, brain: BrainService, voice: VoiceService,
                 tools: ToolService, bus: EventBus, presence: Presence | None = None):
        self._settings = settings
        self._brain = brain
        self._voice = voice
        self._tools = tools
        self._bus = bus
        self.presence = presence or NullPresence()
        self._talk = threading.Event()
        self._stopped = threading.Event()
        self._busy = threading.Lock()
        self.status = "starting"
        bus.subscribe(events.TALK_REQUESTED, lambda _: self._talk.set())
        bus.subscribe(events.STOP_REQUESTED, lambda _: self.interrupt())

    def _set_status(self, status: str) -> None:
        self.status = status
        self._bus.publish(events.STATUS, status=status)

    def interrupt(self) -> None:
        """End the current conversation (not the daemon)."""
        self._stopped.set()

    def converse(self, initial_prompt: str | None = None) -> None:
        settings = self._settings.current
        caps = self._settings.capabilities()
        if not caps.can_talk:
            for problem in caps.problems:
                log.warning("%s", t(problem, "en"))
            raise NeedsSetup(t(caps.problems[0], "en"))
        with self._busy:
            self._stopped.clear()
            self._talk.clear()   # a "talk now" pressed mid-conversation is already answered
            self._set_status("talking")
            try:
                if settings.mode == "realtime":
                    system = domain.system_prompt(settings.persona, settings.assistant_name,
                                                  settings.user_name, settings.language,
                                                  self._tools.machine_context(), realtime=True)
                    self._voice.realtime(settings, system, self._tools.realtime_specs(),
                                         lambda n, a: self._tools.dispatch(n, a), self.presence,
                                         initial_prompt).run()
                else:
                    PipelineConversation(settings, self._brain, self._voice, self._tools,
                                         self.presence, self._stopped).run(initial_prompt)
            finally:
                self.presence.set_state("idle")
                self.presence.set_amplitude(0.0)
                self._set_status("ready")

    def briefing(self) -> None:
        settings = self._settings.current
        data = self._tools.briefing_data()
        self.converse(domain.briefing_prompt(data, settings.language, settings.user_name))

    def _wait_until_ready(self, stop: threading.Event) -> bool:
        """Block while setup is incomplete — the dashboard is how it gets fixed."""
        reported = None
        while not stop.is_set():
            caps = self._settings.capabilities()
            if caps.can_talk:
                return True
            if caps.problems != reported:
                reported = caps.problems
                self._set_status("needs_setup")
                log.warning("Waiting for setup: %s", "; ".join(t(p, "en") for p in caps.problems))
            stop.wait(2.0)
        return False

    def run_forever(self, stop: threading.Event) -> None:
        """The daemon loop. Returns when ``stop`` is set."""
        stop_watch = threading.Thread(target=lambda: (stop.wait(), self._stopped.set()), daemon=True)
        stop_watch.start()
        listener, wake_word = None, None
        briefed = False
        while not stop.is_set():
            if not self._wait_until_ready(stop):
                break
            settings = self._settings.current
            if listener is None or wake_word != settings.wake_word:
                try:
                    self._set_status("loading")
                    listener, wake_word = self._voice.wake_listener(settings), settings.wake_word
                except Exception as exc:
                    log.warning("Wake word unavailable (%s); retrying in 60 s.", exc)
                    self._set_status("needs_setup")
                    stop.wait(60)
                    continue
            if not briefed:
                briefed = True
                if settings.briefing_on_boot and self._tools.briefing_due():
                    self._tools.mark_briefing_done()
                    try:
                        self.briefing()
                    except Exception as exc:
                        log.warning("Boot briefing failed: %s", exc)
            self._set_status("ready")
            self.presence.set_state("idle")
            try:
                if not listener.listen(stop, self._talk):
                    break
                log.info("Woken.")
                self.presence.set_state("listening")
                self._voice.chime(settings, up=True)
                self.converse()
                self._voice.chime(settings, up=False)
                listener.cooldown()
            except NeedsSetup as exc:
                log.warning("%s", exc)
            except Exception as exc:
                log.exception("Conversation failed: %s", exc)
                stop.wait(2)
