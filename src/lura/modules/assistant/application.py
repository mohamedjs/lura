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
from ..memory import MemoryService
from ..settings import Settings, SettingsService, brain_hears_audio
from ..tools import FIND_TOOLS, ToolService, ToolSession
from ..voice import VoiceService
from . import domain

log = logging.getLogger(__name__)


class LiveCaption:
    """What the user is saying, written under the face while they still speak.

    Display only: every ~1.5 s of new speech the audio so far is transcribed in
    the background and shown with a trailing "…". The final transcript never
    comes from here. Each refresh is one hearing call, so they are spaced out
    and capped per turn.
    """

    GAP = 1.5
    MAX_CALLS = 8
    MIN_BLOCKS = 10        # a second of speech before the first try

    def __init__(self, transcriber, to_wav, presence: Presence):
        self._transcriber = transcriber   # callable returning a Transcriber, made on first use
        self._to_wav = to_wav
        self._presence = presence
        self._busy = threading.Event()
        self._last = 0.0
        self._calls = 0
        self._closed = False

    def __call__(self, blocks: list[bytes], start: int) -> None:
        now = time.monotonic()
        if (self._closed or self._busy.is_set() or self._calls >= self.MAX_CALLS
                or now - self._last < self.GAP or len(blocks) - start < self.MIN_BLOCKS):
            return
        self._busy.set()
        self._last, self._calls = now, self._calls + 1
        pcm = b"".join(blocks[start:])
        threading.Thread(target=self._run, args=(pcm,), daemon=True, name="live-caption").start()

    def _run(self, pcm: bytes) -> None:
        try:
            text = self._transcriber().transcribe(self._to_wav(pcm))
            if text and not self._closed:
                self._presence.set_caption("you", f"{text} …")
        except Exception as exc:
            log.debug("Live caption failed: %s", exc)
        finally:
            self._busy.clear()

    def close(self) -> None:
        self._closed = True


class PipelineConversation:
    def __init__(self, settings: Settings, brain: BrainService, voice: VoiceService,
                 tools: ToolService, presence: Presence, stopped: threading.Event,
                 memory: MemoryService | None = None, extra_prompt: str = ""):
        self.s = settings
        self._model = brain.model(settings)
        self._voice = voice
        self._tools = tools
        self._presence = presence
        self._stopped = stopped
        self._session = ToolSession()
        self._history: list[Message] = []
        self._ending = False
        self._quiet = False          # the brain judged the last turn not meant for it
        self._discarded: set = set()
        self._hears = brain_hears_audio(self._model.provider, self._model.model)
        self._transcriber = None if self._hears else voice.transcriber(settings)
        self._caption_transcriber = self._transcriber
        self._memory = memory if memory is not None and memory.enabled else None
        memory_block, prior = self._keep("context") or ("", [])
        self._history = [Message(role, text) for role, text in prior]
        self._conv = self._keep("start")
        self._system = domain.system_prompt(settings.persona, settings.assistant_name,
                                            settings.user_name, settings.language,
                                            tools.machine_context(), now=domain.now_text(),
                                            extra="\n\n".join(p for p in (memory_block, extra_prompt) if p))
        self._budget = settings.context_budget_tokens - domain.tokens(self._system) - domain.REPLY_RESERVE
        log.info("brain %s/%s (%s), voice %s", self._model.provider, self._model.model,
                 "hears audio" if self._hears else "via transcript", settings.voice_name)

    def _keep(self, method: str, *args):
        """Call the memory, if on. A broken history database must never cost a conversation."""
        if self._memory is None:
            return None
        try:
            return getattr(self._memory, method)(*args)
        except Exception as exc:
            log.warning("Memory %s failed: %s", method, exc)
            return None

    # ── one model round ──
    def _stream(self, with_tools: bool) -> Message | None:
        self._presence.set_state("thinking")
        player = self._voice.player(self.s, self._presence)
        done: Done | None = None
        said = ""
        fed = 0
        try:
            for event in self._model.stream(self._system, domain.fit_history(self._history, self._budget),
                                            self._tools.specs(self._session) if with_tools else []):
                if self._stopped.is_set():
                    player.cancel()
                    return None
                if isinstance(event, TextDelta):
                    said += event.text
                    if domain.could_be_quiet(said):
                        continue   # it may be NO_REPLY: say nothing until it is clear
                    self._presence.set_caption("lura", said)
                    player.feed(said[fed:])
                    fed = len(said)
                else:
                    done = event
        except HttpError as exc:
            player.cancel()
            log.warning("Brain failed: %s", exc)
            self._say(t("assistant.brain_error", self.s.language))
            return None
        if done is None:
            player.finish()
            player.wait()
            return None
        message = done.message
        if message.text.strip().startswith(domain.NO_REPLY) and not message.tool_calls:
            player.cancel()
            self._quiet = True
            log.info("Not meant for me — staying quiet.")
            return None
        if fed < len(said) and not self._stopped.is_set():
            player.feed(said[fed:])   # a reply shorter than the marker
            self._presence.set_caption("lura", said)
        player.finish()
        self._history.append(message)
        if message.text:
            self._keep("add", self._conv, "assistant", message.text, self._model.model)
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

    def _captioner(self):
        if self._caption_transcriber is None:
            self._caption_transcriber = self._voice.transcriber(self.s)
        return self._caption_transcriber

    def _forget_turn(self, row: int | None) -> None:
        """The last user turn was noise: out of the history and the memory."""
        while self._history and self._history[-1].role != "user":
            self._history.pop()
        if self._history:
            self._history.pop()
        if row is not None:
            self._discarded.add(row)
            self._keep("discard", row)
        self._presence.set_caption("you", "")

    def _caption_heard(self, wav: bytes, message: Message, row: int | None) -> None:
        """The brain hears the audio itself, so there is no text of what was
        said. For the subtitle and the history, transcribe it alongside — in
        parallel, so the answer is not held up by it. Once it arrives, later
        rounds send this turn as text instead of sound."""
        def run():
            try:
                self._captioner()
                if (heard := self._caption_transcriber.transcribe(wav)) and row not in self._discarded:
                    if self.s.subtitles:
                        self._presence.set_caption("you", heard)
                    message.text = heard
                    self._keep("update", row, heard)
            except Exception as exc:
                log.debug("Subtitle transcription failed: %s", exc)

        threading.Thread(target=run, daemon=True, name="caption").start()

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
        try:
            self._converse(initial_prompt)
        finally:
            self._keep("end", self._conv)

    def _converse(self, initial_prompt: str | None) -> None:
        started = time.monotonic()
        if initial_prompt:
            self._presence.set_caption("you", "")
            self._presence.set_caption("lura", "")
            self._history.append(Message("user", initial_prompt))
            self._answer()
            return
        ignored = 0
        while not self._ending and not self._stopped.is_set():
            if time.monotonic() - started > self.s.max_session_seconds:
                log.info("Session hit its time limit.")
                return
            if ignored >= domain.MAX_IGNORED_TURNS:
                log.info("Only noise for %d turns — closing.", ignored)
                return
            self._presence.set_state("listening")
            live = LiveCaption(self._captioner, self._voice.to_wav, self._presence) \
                if self.s.subtitles and self.s.live_captions else None
            try:
                pcm = self._voice.record(self.s, self._stopped.is_set, live)
            finally:
                if live is not None:
                    live.close()
            if not pcm:
                log.info("Nothing said — closing.")
                return
            wav = self._voice.to_wav(pcm)
            self._presence.set_caption("lura", "")
            self._presence.set_caption("you", "…")
            row = None
            if self._hears:
                message = Message("user", audio_wav=wav)
                self._history.append(message)
                row = self._keep("add", self._conv, "user", "")
                if self.s.subtitles or self._memory is not None:
                    self._caption_heard(wav, message, row)
            else:
                self._presence.set_state("thinking")
                try:
                    heard = self._transcriber.transcribe(wav)
                except HttpError as exc:
                    log.warning("Hearing failed: %s", exc)
                    self._say(t("assistant.hearing_error", self.s.language))
                    return
                if not heard:
                    ignored += 1
                    self._presence.set_caption("you", "")
                    continue
                self._presence.set_caption("you", heard)
                self._history.append(Message("user", heard))
                row = self._keep("add", self._conv, "user", heard)
            self._quiet = False
            self._answer()
            if self._quiet:
                ignored += 1
                self._forget_turn(row)
            else:
                ignored = 0


class Assistant:
    def __init__(self, settings: SettingsService, brain: BrainService, voice: VoiceService,
                 tools: ToolService, bus: EventBus, presence: Presence | None = None,
                 memory: MemoryService | None = None, extra_prompt=lambda: ""):
        self._settings = settings
        self._memory = memory
        #: Skills and anything else that adds to the system prompt.
        self._extra_prompt = extra_prompt
        self._brain = brain
        self._voice = voice
        self._tools = tools
        self._bus = bus
        self.presence = presence or NullPresence()
        self._talk = threading.Event()
        self._stopped = threading.Event()
        self._busy = threading.Lock()
        #: Set when the microphone or wake word changes, so the listener is
        #: rebuilt now instead of after the next conversation.
        self._reload = threading.Event()
        self.status = "starting"
        bus.subscribe(events.SETTINGS_CHANGED, self._on_settings)
        bus.subscribe(events.TALK_REQUESTED, lambda _: self._talk.set())
        bus.subscribe(events.STOP_REQUESTED, lambda _: self.interrupt())

    def _set_status(self, status: str) -> None:
        self.status = status
        self._bus.publish(events.STATUS, status=status)

    def _on_settings(self, payload: dict) -> None:
        if {"input_device", "wake_word"} & set(payload.get("changed", ())):
            self._reload.set()

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
                    self._realtime(settings, initial_prompt)
                else:
                    PipelineConversation(settings, self._brain, self._voice, self._tools,
                                         self.presence, self._stopped, self._memory,
                                         self._extra_prompt()).run(initial_prompt)
            finally:
                self.presence.set_state("idle")
                self.presence.set_amplitude(0.0)
                self._set_status("ready")

    def _realtime(self, settings: Settings, initial_prompt: str | None) -> None:
        memory = self._memory if self._memory is not None and self._memory.enabled else None
        block, prior = "", []
        if memory is not None:
            try:
                block, prior = memory.context()
            except Exception as exc:
                log.warning("Memory unavailable: %s", exc)
                memory = None
        extra = "\n\n".join(p for p in (block, domain.recent_lines(prior[-10:]), self._extra_prompt()) if p)
        system = domain.system_prompt(settings.persona, settings.assistant_name, settings.user_name,
                                      settings.language, self._tools.machine_context(), realtime=True,
                                      extra=extra, now=domain.now_text())
        live = self._voice.realtime(settings, system, self._tools.realtime_specs(),
                                    lambda n, a: self._tools.dispatch(n, a), self.presence, initial_prompt)
        try:
            live.run()
        finally:
            if memory is not None and (said := live.transcript):
                try:
                    conv = memory.start()
                    for role, text in said:
                        memory.add(conv, role, text, settings.realtime_model)
                    memory.end(conv)
                except Exception as exc:
                    log.warning("Could not save the conversation: %s", exc)

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
        listener, built_for = None, None
        briefed = False
        while not stop.is_set():
            if not self._wait_until_ready(stop):
                break
            settings = self._settings.current
            wanted = (settings.wake_word, settings.input_device)
            if listener is None or built_for != wanted or self._reload.is_set():
                try:
                    self._set_status("loading")
                    self._reload.clear()
                    listener, built_for = self._voice.wake_listener(settings), wanted
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
                if not listener.listen(stop, self._talk, self._reload):
                    if stop.is_set():
                        break
                    log.info("Microphone or wake word changed; listening again.")
                    continue
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
