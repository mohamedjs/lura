"""Air intelligence (Stage F2): gestures reach Lura's conversation.

No real keys, no network, no audio device.
"""

from __future__ import annotations

import _setup  # noqa: F401
import sys
import threading
import time

from lura.modules.assistant import Assistant, PipelineConversation, fit_history, system_prompt
from lura.modules.assistant.domain import AIR_NOTE_SECONDS, ANSWER_GRACE, air_note, answer_word
from lura.modules.brain import Done, Message, TextDelta
from lura.modules.settings import Settings
from lura.modules.settings.domain import Capabilities
from lura.modules.tools import ToolService
from lura.shared import events
from lura.shared.events import EventBus


class FakeSettings:
    def __init__(self, settings: Settings) -> None:
        self.current = settings

    def capabilities(self) -> Capabilities:
        return Capabilities(frozenset({"anthropic", "gemini"}), self.current.brain_provider, "gemini", True, ())


class FakePlayer:
    interrupted = False
    heard: list[str] = []
    carry = None

    def __init__(self, on_wait=None) -> None:
        self._on_wait = on_wait

    def feed(self, text: str) -> None:
        pass

    def finish(self) -> None:
        pass

    def wait(self) -> None:
        if self._on_wait is not None:
            self._on_wait()

    def cancel(self) -> None:
        pass

    def say(self, text: str) -> None:
        pass


class FakePresence:
    def __init__(self) -> None:
        self.captions: list[tuple[str, str]] = []
        self.states: list[str] = []

    def set_state(self, state: str) -> None:
        self.states.append(state)

    def set_amplitude(self, level: float) -> None:
        pass

    def set_caption(self, who: str, text: str) -> None:
        self.captions.append((who, text))


class FakeMemory:
    enabled = True

    def __init__(self) -> None:
        self.rows: list[list] = []

    def context(self):
        return "", []

    def start(self) -> int:
        return 1

    def add(self, conv: int, role: str, text: str, model: str = "") -> int:
        self.rows.append(["add", role, text])
        return len(self.rows)

    def update(self, row: int, text: str) -> None:
        self.rows.append(["update", row, text])

    def discard(self, row: int) -> None:
        self.rows.append(["discard", row])

    def end(self, conv: int) -> None:
        pass


class FakeBrain:
    def __init__(self, model) -> None:
        self._model = model

    def model(self, settings: Settings):
        return self._model


class RecordingModel:
    def __init__(self, provider: str = "anthropic", model: str = "claude-opus-5-5",
                 reply: str = "Got it.") -> None:
        self.provider = provider
        self.model = model
        self.reply = reply
        self.requests: list[list[Message]] = []

    def stream(self, system: str, history: list[Message], tools):
        self.requests.append(list(history))
        yield TextDelta(self.reply)
        yield Done(Message("assistant", self.reply), "end")


def check_domain_helpers_and_prompt() -> None:
    assert AIR_NOTE_SECONDS == 15.0 and ANSWER_GRACE == 4.0

    # air_note formats
    note = air_note("google-chrome", "PR #42 - GitHub")
    assert note == '[The user is pointing at a window: "PR #42 - GitHub" (google-chrome). "This"/"here" means that window.]', note
    long_title = "x" * 120
    assert f'"{long_title[:80]}" (code)' in air_note("code", long_title)
    assert air_note("vlc", "") == '[The user is pointing at a window: vlc. "This"/"here" means that window.]'
    assert air_note("", "") == ""
    assert air_note("  ", "  ") == ""

    # answer_word en / ar / unknown
    assert answer_word("confirm", "en") == "Yes."
    assert answer_word("deny", "en") == "No."
    assert answer_word("confirm", "ar") == "نعم"
    assert answer_word("deny", "ar") == "لا"
    assert answer_word("point", "en") == ""
    assert answer_word("unknown", "ar") == ""

    # system_prompt includes Air paragraph, is byte-stable, and respects renames
    p1 = system_prompt("", "Lura", "Mohamed", "en", "[ctx]", now="Friday")
    p2 = system_prompt("", "Lura", "Mohamed", "en", "[ctx]", now="Friday")
    assert p1 == p2, "system prompt must be byte-stable"
    assert "Lura Air:" in p1 and "hand gestures" in p1 and "pointing at a window" in p1
    assert "thumbs up or down" in p1
    renamed = system_prompt("", "Nour", "Mohamed", "en", "[ctx]", now="Friday")
    assert "Nour Air:" in renamed and "Lura" not in renamed

    # fit_history keeps a leading Air note on the newest audio message without duplicating transcript
    wav = b"RIFF" + b"\0" * 100
    msg = Message("user", note, audio_wav=wav)
    sent = fit_history([msg], 10_000)
    assert sent[0].audio_wav == wav and sent[0].text == note
    msg_with_transcript = Message("user", f"{note}\nwhat is this", audio_wav=wav)
    sent2 = fit_history([msg_with_transcript], 10_000)
    assert sent2[0].audio_wav == wav and sent2[0].text == note


def check_thumbs_answer_stops_recording() -> None:
    for lang, intent, expected_word, expected_icon in (
        ("en", "confirm", "Yes.", "👍 "),
        ("ar", "confirm", "نعم", "👍 "),
        ("en", "deny", "No.", "👎 "),
        ("ar", "deny", "لا", "👎 "),
    ):
        bus = EventBus()
        model = RecordingModel()
        presence = FakePresence()
        memory = FakeMemory()
        listening = threading.Event()
        calls = {"record": 0}

        class BlockingVoice:
            def player(self, settings, presence=None, listen=False):
                return FakePlayer()

            def transcriber(self, settings):
                return None

            @staticmethod
            def to_wav(pcm: bytes) -> bytes:
                return pcm

            def record(self, settings, should_stop, live=None, carry=None) -> bytes:
                calls["record"] += 1
                if calls["record"] == 1:
                    listening.set()
                    deadline = time.monotonic() + 5.0
                    while not should_stop() and time.monotonic() < deadline:
                        time.sleep(0.01)
                    return b"partial-audio-to-drop"
                return b""

        settings = Settings(brain_provider="anthropic", language=lang, subtitles=True, live_captions=False)
        assistant = Assistant(FakeSettings(settings), FakeBrain(model), BlockingVoice(),
                              ToolService(None), bus, presence=presence, memory=memory)

        t = threading.Thread(target=assistant.converse, daemon=True)
        t.start()
        assert listening.wait(3.0), "conversation never started listening"
        bus.publish(events.AIR_INTENT, intent=intent, mode="universal", gesture="pose", app="", title="")
        t.join(timeout=5.0)
        assert not t.is_alive(), "conversation did not finish"

        assert len(model.requests) == 1, model.requests
        last_user = model.requests[0][-1]
        assert last_user.role == "user" and last_user.text == expected_word, last_user
        assert ("you", expected_icon + expected_word) in presence.captions, presence.captions
        assert ["add", "user", expected_word] in memory.rows, memory.rows


def check_intent_without_conversation_and_realtime() -> None:
    bus = EventBus()
    assistant = Assistant(FakeSettings(Settings(brain_provider="anthropic")), FakeBrain(RecordingModel()),
                          object(), ToolService(None), bus, presence=FakePresence())
    # No active conversation: must do nothing, raise nothing.
    for intent in ("confirm", "deny", "point", "talk", "other"):
        bus.publish(events.AIR_INTENT, intent=intent, mode="universal", gesture="pose", app="vlc", title="VLC")
    assert not assistant._talk.is_set(), "status is starting, not ready"

    # Realtime mode ignores all intents.
    rt_bus = EventBus()
    rt_assistant = Assistant(FakeSettings(Settings(mode="realtime")), FakeBrain(RecordingModel()),
                             object(), ToolService(None), rt_bus, presence=FakePresence())
    rt_assistant.status = "ready"
    rt_bus.publish(events.AIR_INTENT, intent="point", mode="universal", gesture="pose", app="chrome", title="Tab")
    rt_bus.publish(events.AIR_INTENT, intent="talk", mode="ai", gesture="swipe_left", app="", title="")
    rt_bus.publish(events.AIR_INTENT, intent="confirm", mode="universal", gesture="pose", app="", title="")
    assert rt_assistant._take_air_point() is None
    assert not rt_assistant._talk.is_set()


def check_injection_grace_window() -> None:
    # Case 1: injection arrives while Lura is speaking, consumed within 4 s -> used, no recording.
    model = RecordingModel(reply="Should I send it?")
    presence = FakePresence()
    recorded = []
    conv_holder: list[PipelineConversation] = []
    injected_once = {"done": False}

    class Voice1:
        def player(self, settings, presence=None, listen=False):
            def on_wait():
                if not injected_once["done"]:
                    injected_once["done"] = True
                    assert not conv_holder[0]._listening
                    conv_holder[0].inject("Yes.", source="confirm")
            return FakePlayer(on_wait=on_wait)

        def transcriber(self, settings):
            return None

        @staticmethod
        def to_wav(pcm: bytes) -> bytes:
            return pcm

        def record(self, settings, should_stop, live=None, carry=None) -> bytes:
            recorded.append(1)
            return b""

    conv = PipelineConversation(Settings(brain_provider="anthropic", subtitles=False), FakeBrain(model),
                                Voice1(), ToolService(None), presence, threading.Event())
    conv_holder.append(conv)
    conv.run(initial_prompt="Ask the user.", then_listen=True)
    assert len(model.requests) == 2, model.requests
    assert model.requests[1][-1].text == "Yes."
    assert ("you", "👍 Yes.") in presence.captions
    assert recorded == [1], "first turn used injection without recording; second turn closed on empty record"

    # Case 2: injection older than 4 s -> dropped and the user is recorded normally.
    model2 = RecordingModel(reply="Still here?")
    presence2 = FakePresence()
    recorded2 = []
    conv2_holder: list[PipelineConversation] = []
    injected2 = {"done": False}

    class Ear:
        def transcribe(self, wav: bytes) -> str:
            return "actually wait"

    class Voice2:
        def player(self, settings, presence=None, listen=False):
            def on_wait():
                if not injected2["done"]:
                    injected2["done"] = True
                    conv2_holder[0].inject("Yes.", source="confirm")
                    # Backdate beyond ANSWER_GRACE (4.0 s).
                    conv2_holder[0]._inject_t = time.monotonic() - (ANSWER_GRACE + 1.0)
            return FakePlayer(on_wait=on_wait)

        def transcriber(self, settings):
            return Ear()

        @staticmethod
        def to_wav(pcm: bytes) -> bytes:
            return pcm

        def record(self, settings, should_stop, live=None, carry=None) -> bytes:
            recorded2.append(1)
            return b"speech" if len(recorded2) == 1 else b""

    conv2 = PipelineConversation(Settings(brain_provider="anthropic", subtitles=False), FakeBrain(model2),
                                 Voice2(), ToolService(None), presence2, threading.Event())
    conv2_holder.append(conv2)
    conv2.run(initial_prompt="Ask the user.", then_listen=True)
    assert len(model2.requests) == 2, model2.requests
    assert model2.requests[1][-1].text == "actually wait", model2.requests[1][-1]
    assert not any(c[1].startswith("👍") for c in presence2.captions)
    assert len(recorded2) == 2


def check_point_note_text_and_expiry() -> None:
    bus = EventBus()
    model = RecordingModel()
    memory = FakeMemory()
    presence = FakePresence()
    spoken_turns = iter(["explain what's happening here", "and now what"])

    class Ear:
        def transcribe(self, wav: bytes) -> str:
            return next(spoken_turns)

    class TwoTurnVoice:
        n = 0

        def player(self, settings, presence=None, listen=False):
            return FakePlayer()

        def transcriber(self, settings):
            return Ear()

        @staticmethod
        def to_wav(pcm: bytes) -> bytes:
            return pcm

        def record(self, settings, should_stop, live=None, carry=None) -> bytes:
            self.n += 1
            return b"wav" if self.n <= 2 else b""

    settings = Settings(brain_provider="anthropic", subtitles=False)
    assistant = Assistant(FakeSettings(settings), FakeBrain(model), TwoTurnVoice(),
                          ToolService(None), bus, presence=presence, memory=memory)

    # Point within 15 s: attached to turn 1, used once (turn 2 has none), memory keeps plain heard.
    bus.publish(events.AIR_INTENT, intent="point", mode="universal", gesture="pose",
                app="google-chrome", title="Docs - Chrome")
    assistant.converse()
    expected_note = air_note("google-chrome", "Docs - Chrome")
    assert len(model.requests) == 2, model.requests
    assert model.requests[0][-1].text == f"{expected_note}\nexplain what's happening here"
    assert model.requests[1][-1].text == "and now what", "note is used once"
    assert ["add", "user", "explain what's happening here"] in memory.rows, "memory keeps plain heard"

    # Point older than 15 s (16 s): no note attached.
    model_old = RecordingModel()
    spoken_old = iter(["explain this"])

    class OneTurnVoice:
        n = 0

        def player(self, settings, presence=None, listen=False):
            return FakePlayer()

        def transcriber(self, settings):
            class E:
                def transcribe(self, wav: bytes) -> str:
                    return next(spoken_old)
            return E()

        @staticmethod
        def to_wav(pcm: bytes) -> bytes:
            return pcm

        def record(self, settings, should_stop, live=None, carry=None) -> bytes:
            self.n += 1
            return b"wav" if self.n == 1 else b""

    bus2 = EventBus()
    assistant2 = Assistant(FakeSettings(settings), FakeBrain(model_old), OneTurnVoice(),
                           ToolService(None), bus2, presence=FakePresence())
    bus2.publish(events.AIR_INTENT, intent="point", mode="universal", gesture="pose",
                 app="code", title="app.py")
    app_name, title, _ = assistant2._air_point
    assistant2._air_point = (app_name, title, time.monotonic() - 16.0)
    assistant2.converse()
    assert model_old.requests[0][-1].text == "explain this", model_old.requests[0][-1]


def check_point_note_audio_branch() -> None:
    # Brain that hears audio (gemini-3.8-flash): gets text=note with WAV, and after
    # the caption transcript arrives, message.text becomes note + "\n" + heard.
    model = RecordingModel(provider="gemini", model="gemini-3.8-flash")
    presence = FakePresence()
    memory = FakeMemory()
    caption_done = threading.Event()
    expected_note = air_note("google-chrome", "Dashboard")

    class Ear:
        def transcribe(self, wav: bytes) -> str:
            caption_done.set()
            return "what is on this page"

    class AudioVoice:
        n = 0

        def player(self, settings, presence=None, listen=False):
            return FakePlayer()

        def transcriber(self, settings):
            return Ear()

        @staticmethod
        def to_wav(pcm: bytes) -> bytes:
            return b"RIFF" + pcm

        def record(self, settings, should_stop, live=None, carry=None) -> bytes:
            self.n += 1
            return b"pcm-bytes" if self.n == 1 else b""

    point_once = iter([("google-chrome", "Dashboard"), None])
    settings = Settings(brain_provider="gemini", subtitles=True, live_captions=False)
    conv = PipelineConversation(settings, FakeBrain(model), AudioVoice(), ToolService(None),
                                presence, threading.Event(), memory=memory,
                                air_point=lambda: next(point_once, None))
    conv.run()

    assert len(model.requests) == 1
    sent_user = model.requests[0][-1]
    assert sent_user.audio_wav == b"RIFFpcm-bytes"
    assert sent_user.text == expected_note, sent_user

    assert caption_done.wait(3.0), "caption thread never ran"
    # Wait briefly for message.text update after transcribe() returns.
    deadline = time.monotonic() + 3.0
    user_msg = conv._history[0]
    while user_msg.text == expected_note and time.monotonic() < deadline:
        time.sleep(0.01)
    assert user_msg.text == f"{expected_note}\nwhat is on this page", user_msg.text
    assert ("you", "what is on this page") in presence.captions, presence.captions
    assert ["update", 1, "what is on this page"] in memory.rows, memory.rows


def check_talk_intent() -> None:
    bus = EventBus()
    assistant = Assistant(FakeSettings(Settings(brain_provider="anthropic")), FakeBrain(RecordingModel()),
                          object(), ToolService(None), bus, presence=FakePresence())

    assistant.status = "talking"
    bus.publish(events.AIR_INTENT, intent="talk", mode="ai", gesture="swipe_right", app="", title="")
    assert not assistant._talk.is_set(), "talk intent ignored while talking"

    assistant.status = "ready"
    bus.publish(events.AIR_INTENT, intent="talk", mode="ai", gesture="swipe_right", app="", title="")
    assert assistant._talk.is_set(), "talk intent sets _talk while ready"


def main() -> int:
    for name, fn in list(globals().items()):
        if name.startswith("check_"):
            fn()
            print(f"  {name[6:]} OK")
    print("air assistant OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
