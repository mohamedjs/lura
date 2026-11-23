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
from ..brain import BrainService, Done, Message, TextDelta, retext
from ..memory import MemoryService
from ..settings import Settings, SettingsService, brain_hears_audio
from ..tools import FIND_TOOLS, ToolService, ToolSession
from ..voice import VoiceService
from . import domain
from .live_caption import LiveCaption

log = logging.getLogger(__name__)


class PipelineConversation:
    def __init__(self, settings: Settings, brain: BrainService, voice: VoiceService,
                 tools: ToolService, presence: Presence, stopped: threading.Event,
                 memory: MemoryService | None = None, extra_prompt: str = "",
                 preload: list | None = None, air_point=lambda: None):
        self.s = settings
        self._model = brain.model(settings)
        self._voice = voice
        self._tools = tools
        self._presence = presence
        self._stopped = stopped
        self._air_point = air_point
        self._inject_lock = threading.Lock()
        self._inject_text: str = ""
        self._inject_source: str = ""
        self._inject_t: float = 0.0
        self._inject_evt = threading.Event()
        self._listening = False
        self._session = ToolSession()
        if preload:
            # Tools this conversation needs from the start (the daily briefing's
            # sources), so the model calls them without a find_tools round first.
            self._session.add(preload)
        self._history: list[Message] = []
        self._ending = False
        self._quiet = False          # the brain judged the last turn not meant for it
        self._player = None          # the reply being spoken, for interrupt()
        self._cut = False            # the user took the floor during this turn
        self._carry = None           # their first words, heard while she spoke
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
                                            user_gender=settings.user_gender,
                                            assistant_gender=settings.voice_gender,
                                            services=tools.services(),
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

    def interrupt(self) -> None:
        """Stop the reply now and listen (a tap on the face)."""
        if self._player is not None:
            self._player.interrupt()

    def inject(self, text: str, source: str = "") -> None:
        """Queue a gesture answer (\"Yes.\"/\"No.\") as the user's next turn."""
        if not text:
            return
        with self._inject_lock:
            self._inject_text = text
            self._inject_source = source
            self._inject_t = time.monotonic()
            self._inject_evt.set()

    def _take_injection(self) -> tuple[str, str] | None:
        with self._inject_lock:
            if not self._inject_evt.is_set():
                return None
            text, source, when = self._inject_text, self._inject_source, self._inject_t
            self._inject_text, self._inject_source, self._inject_t = "", "", 0.0
            self._inject_evt.clear()
        age = time.monotonic() - when
        if age > domain.ANSWER_GRACE:
            log.debug("Dropping stale gesture injection (%.1fs old).", age)
            return None
        return text, source

    def spoken_language(self) -> str | None:
        """"ar" or "en" from the user's last words in this conversation, None before any.
        settings.language is only the default; she answers in the language the user speaks, so
        a thumbs answer must be in that language too, or an English "Yes." switches her."""
        for m in reversed(self._history):
            if m.role != "user" or m.tool_call_id:
                continue
            words = "\n".join(line for line in m.text.splitlines()
                              if not line.startswith("[The user is pointing")).strip()
            if words:
                return "ar" if any("؀" <= ch <= "ۿ" for ch in words) else "en"
        return None

    def _consume_air_note(self) -> str:
        p = self._air_point()
        if not p:
            return ""
        note = domain.air_note(*p)
        if note:
            log.info("Air note attached: %s", p[0])
        return note

    def _cut_short(self, player, message: Message | None, row: int | None = None) -> Message | None:
        """The user took the floor: history, memory and subtitle keep only what
        they actually heard her say, and no further tool round starts."""
        player.wait()       # at once after a cut; also lets the barge-in mic go before the recorder opens it
        self._cut, self._carry = True, player.carry
        heard = " ".join(player.heard).strip()
        text = f"{heard} …" if heard else ""
        self._presence.set_caption("lura", text)
        if message is None:                       # cut off while the brain was still writing
            message = Message("assistant", text) if text else None
            if message is not None:
                self._history.append(message)
                self._keep("add", self._conv, "assistant", text, self._model.model)
            return message
        retext(message, text)                     # the provider's replayed copy too, or the brain never learns
        if text or message.tool_calls:
            self._keep("update", row, text)
        else:                                     # nothing was heard: as if never said
            self._history.remove(message)
            self._keep("discard", row)
        return message

    # ── one model round ──
    def _stream(self, with_tools: bool) -> Message | None:
        self._presence.set_state("thinking")
        player = self._player = self._voice.player(self.s, self._presence, listen=True)
        done: Done | None = None
        said = ""
        fed = 0
        try:
            history = domain.with_tashkeel_note(domain.fit_history(self._history, self._budget),
                                                arabic_default=self.s.language == "ar")
            for event in self._model.stream(self._system, history,
                                            self._tools.specs(self._session) if with_tools else []):
                if player.interrupted:
                    return self._cut_short(player, None)
                if self._stopped.is_set():
                    player.cancel()
                    return None
                if isinstance(event, TextDelta):
                    said += event.text
                    if domain.could_be_quiet(said):
                        continue   # it may be NO_REPLY: say nothing until it is clear
                    ready = domain.speakable_reply(said)
                    self._presence.set_caption("lura", ready)
                    player.feed(ready[fed:])
                    fed = len(ready)
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
        if domain.is_quiet(message.text) and not message.tool_calls:
            player.cancel()
            self._quiet = True
            log.info("Not meant for me — staying quiet.")
            return None
        if domain.NO_REPLY in message.text:   # the marker inside a real answer: never kept
            message.text = message.text.replace(domain.NO_REPLY, "").strip()
        final = domain.speakable_reply(said.replace(domain.NO_REPLY, ""))
        if fed < len(final) and not self._stopped.is_set():
            player.feed(final[fed:])   # held back while it looked like the marker
            self._presence.set_caption("lura", final)
        player.finish()
        self._history.append(message)
        row = self._keep("add", self._conv, "assistant", message.text, self._model.model) if message.text else None
        if done.stop_reason == "refusal" and not message.text:
            player.wait()
            self._say(t("assistant.refused", self.s.language))
            return message
        if message.tool_calls:
            # Pre-tool words keep playing while the tools run.
            self._run_tools(message)
        player.wait()
        if player.interrupted:
            return self._cut_short(player, message, row)
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

    def _caption_heard(self, wav: bytes, message: Message, row: int | None, note: str = "") -> None:
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
                    message.text = f"{note}\n{heard}" if note else heard
                    self._keep("update", row, heard)
            except Exception as exc:
                log.debug("Subtitle transcription failed: %s", exc)

        threading.Thread(target=run, daemon=True, name="caption").start()

    # ── one user turn ──
    def _answer(self) -> None:
        rounds = 0
        while not self._stopped.is_set():
            message = self._stream(with_tools=True)
            if message is None or not message.tool_calls or self._cut:
                return
            if self._ending:
                # A user turn, not tool output: instructions inside a tool
                # result get ignored and the turn ends in silence.
                self._history.append(Message("user", domain.GOODBYE_NUDGE))
                self._stream(with_tools=False)
                return
            # find_tools changes nothing outside this process; charging it
            # against the budget lets two bad searches end the turn in silence.
            if any(c.name != FIND_TOOLS for c in message.tool_calls):
                rounds += 1
            if rounds >= domain.MAX_TOOL_ROUNDS:
                log.warning("Model used all %d tool rounds; asking it to answer.", rounds)
                self._history.append(Message("user", domain.ANSWER_NOW))
                self._stream(with_tools=False)
                return

    def run(self, initial_prompt: str | None = None, then_listen: bool = False) -> None:
        """``then_listen``: after answering the prompt, keep the conversation
        going (the daily briefing ends on a question)."""
        try:
            self._converse(initial_prompt, then_listen)
        finally:
            if self._carry is not None:   # stopped with the user's words still in the mic
                self._carry.close()
            self._keep("end", self._conv)

    def _converse(self, initial_prompt: str | None, then_listen: bool = False) -> None:
        started = time.monotonic()
        if initial_prompt:
            self._presence.set_caption("you", "")
            self._presence.set_caption("lura", "")
            self._history.append(Message("user", initial_prompt))
            self._answer()
            if not then_listen and not self._cut:   # interrupted: they want to say something
                return
            # Then it is a conversation: she ended on a question, so listen
            # for the answer (follow_up_seconds of silence ends it as usual).
        ignored = 0
        while not self._ending and not self._stopped.is_set():
            if time.monotonic() - started > self.s.max_session_seconds:
                log.info("Session hit its time limit.")
                return
            if ignored >= domain.MAX_IGNORED_TURNS:
                log.info("Only noise for %d turns — closing.", ignored)
                return
            self._listening = True
            injected = self._take_injection()
            pcm = b""
            live = None
            try:
                if injected is None:
                    self._presence.set_state("listening")
                    live = LiveCaption(self._captioner, self._voice.to_wav, self._presence) \
                        if self.s.subtitles and self.s.live_captions else None
                    carry, self._carry, self._cut = self._carry, None, False
                    should_stop = lambda: self._stopped.is_set() or self._inject_evt.is_set()
                    try:
                        pcm = self._voice.record(self.s, should_stop, live, carry)
                    finally:
                        if live is not None:
                            live.close()
                    if not self._stopped.is_set() and self._inject_evt.is_set():
                        injected = self._take_injection()
                else:
                    if self._carry is not None:
                        self._carry.close()
                        self._carry = None
                    self._cut = False
            finally:
                self._listening = False
            if self._stopped.is_set():
                return
            if injected is not None:
                heard, source = injected
                prefix = "👍 " if source == "confirm" else "👎 " if source == "deny" else ""
                self._presence.set_caption("lura", "")
                self._presence.set_caption("you", f"{prefix}{heard}")
                self._history.append(Message("user", heard))
                row = self._keep("add", self._conv, "user", heard)
                self._quiet = False
                self._answer()
                if self._quiet:
                    ignored += 1
                    self._forget_turn(row)
                else:
                    ignored = 0
                continue
            if not pcm:
                self._presence.set_caption("you", "")   # a live partial must not linger
                log.info("Nothing said — closing.")
                return
            wav = self._voice.to_wav(pcm)
            self._presence.set_caption("lura", "")
            self._presence.set_caption("you", "…")
            row = None
            if self._hears:
                note = self._consume_air_note()
                message = Message("user", text=note, audio_wav=wav)
                self._history.append(message)
                row = self._keep("add", self._conv, "user", "")
                if self.s.subtitles or self._memory is not None:
                    self._caption_heard(wav, message, row, note)
            else:
                self._presence.set_state("thinking")
                try:
                    heard = self._transcriber.transcribe(wav)
                except HttpError as exc:
                    # One failed call (a timeout, a provider out of quota) is not the
                    # end of the conversation: say so and listen again.
                    log.warning("Hearing failed: %s", exc)
                    self._say(t("assistant.hearing_error", self.s.language))
                    ignored += 1
                    continue
                if not heard and live is not None and domain.worth_keeping(live.last, len(pcm)):
                    # The whole turn came back empty although the captions read real
                    # words from it: their sentence must not vanish in silence.
                    heard = live.last
                    log.info("Final transcript was empty; using the live caption: %s", heard)
                if not heard:
                    ignored += 1
                    self._presence.set_caption("you", "")
                    continue
                note = self._consume_air_note()
                self._presence.set_caption("you", heard)
                self._history.append(Message("user", f"{note}\n{heard}" if note else heard))
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
        self._conversation: PipelineConversation | None = None
        self._air_lock = threading.Lock()
        self._air_point: tuple[str, str, float] | None = None
        self.status = "starting"
        bus.subscribe(events.SETTINGS_CHANGED, self._on_settings)
        bus.subscribe(events.TALK_REQUESTED, lambda _: self._talk.set())
        bus.subscribe(events.STOP_REQUESTED, lambda _: self.interrupt())
        bus.subscribe(events.INTERRUPT_REQUESTED,
                      lambda _: (conv := self._conversation) is not None and conv.interrupt())
        bus.subscribe(events.AIR_INTENT, self._on_air_intent)

    def _on_air_intent(self, payload: dict) -> None:
        intent = payload.get("intent", "")
        if self._settings.current.mode == "realtime":
            log.debug("Ignoring Air intent %r in realtime mode.", intent)
            return
        if intent == "point":
            with self._air_lock:
                self._air_point = (str(payload.get("app", "")), str(payload.get("title", "")),
                                   time.monotonic())
        elif intent in ("confirm", "deny"):
            conv = self._conversation
            if isinstance(conv, PipelineConversation):
                word = domain.answer_word(intent, conv.spoken_language() or self._settings.current.language)
                if word:
                    conv.inject(word, source=intent)
            else:
                log.info("No question to answer with a gesture.")
        elif intent == "talk":
            if self.status == "ready":
                self._talk.set()

    def _take_air_point(self) -> tuple[str, str] | None:
        with self._air_lock:
            point = self._air_point
            self._air_point = None
        if point is None:
            return None
        app, title, when = point
        if time.monotonic() - when >= domain.AIR_NOTE_SECONDS:
            return None
        return app, title

    def _set_status(self, status: str) -> None:
        self.status = status
        self._bus.publish(events.STATUS, status=status)

    def _on_settings(self, payload: dict) -> None:
        if {"input_device", "wake_word"} & set(payload.get("changed", ())):
            self._reload.set()

    def interrupt(self) -> None:
        """End the current conversation (not the daemon)."""
        self._stopped.set()

    def converse(self, initial_prompt: str | None = None, preload: list | None = None,
                 then_listen: bool = False) -> None:
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
                    self._conversation = PipelineConversation(settings, self._brain, self._voice, self._tools,
                                                              self.presence, self._stopped, self._memory,
                                                              self._extra_prompt(), preload,
                                                              air_point=lambda: self._take_air_point())
                    self._conversation.run(initial_prompt, then_listen)
            finally:
                self._conversation = None
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
                                      extra=extra, now=domain.now_text(),
                                      user_gender=settings.user_gender,
                                      assistant_gender=settings.voice_gender,
                                      services=self._tools.services())
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
        """Open the day: what the user did yesterday and has today, from every
        connected work service (code, tasks, calendar, notes, messages), then a
        conversation about it."""
        settings = self._settings.current
        data = self._tools.briefing_data()
        sources = self._tools.briefing_sources()
        log.info("Daily briefing from: %s",
                 ", ".join(f"{s['name']} ({s['kind']})" for s in sources) or "no work services connected")
        today, yesterday = domain.day_names()
        prompt = domain.briefing_prompt(data, settings.language, settings.user_name, settings.user_gender,
                                        sources, today, yesterday)
        self.converse(prompt, preload=[spec for s in sources for spec in s["specs"]], then_listen=True)

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
                if settings.daily_briefing == "boot" and self._tools.briefing_due("boot"):
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
                if settings.daily_briefing == "first_wake" and self._tools.briefing_due("day"):
                    self._tools.mark_briefing_done()
                    try:
                        self.briefing()
                    except NeedsSetup:
                        raise
                    except Exception as exc:
                        log.warning("Daily briefing failed (%s); talking normally.", exc)
                        self.converse()
                else:
                    self.converse()
                self._voice.chime(settings, up=False)
                listener.cooldown()
            except NeedsSetup as exc:
                log.warning("%s", exc)
            except Exception as exc:
                log.exception("Conversation failed: %s", exc)
                stop.wait(2)
