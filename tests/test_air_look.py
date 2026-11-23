"""Air look (doc 013): screenshot capture, look_at_screen, image wire formats, blind latch, pointing & gaze."""

from __future__ import annotations

import _setup  # noqa: F401
import base64
import struct
import sys
import threading
import time

import numpy as np

from lura.modules.air import AirController, Command
from lura.modules.assistant import Assistant, PipelineConversation, fit_history
from lura.modules.assistant.domain import BLIND_NOTE, LOOK_IMAGE_TOKENS, message_tokens
from lura.modules.brain import Done, Message, TextDelta, ToolCall, ToolReply, ToolSpec
from lura.modules.brain.infrastructure.anthropic_chat import AnthropicChat
from lura.modules.brain.infrastructure.openai_compat import OpenAICompatChat
from lura.modules.computer import ActionError, ComputerService, Rect
from lura.modules.computer.domain import LOOK_MAX_SIDE, Monitor, Window
from lura.modules.computer.infrastructure import capture
from lura.modules.overlay import STYLES, AirView, StateBlender, look_from
from lura.modules.overlay.render import V0, _motion, _projector, _rot, render_surface
from lura.modules.settings import Settings
from lura.modules.tools import ToolService
from lura.modules.tools.application import IMAGE_UNSUPPORTED
from lura.shared.events import EventBus
from lura.shared.http import HttpError
from test_air_assistant import FakeBrain, FakePlayer, FakePresence, FakeSettings, RecordingModel
from test_air_control import FakeAir, FakeDesktop


def check_capture_png_downscale_encode() -> None:
    rgb = np.array(
        [
            [[255, 0, 0], [0, 255, 0], [0, 0, 255]],
            [[10, 20, 30], [100, 150, 200], [250, 240, 230]],
        ],
        dtype=np.uint8,
    )
    raw = capture.png(rgb)
    assert raw.startswith(b"\x89PNG\r\n\x1a\n")
    ihdr_len, ihdr_tag, w, h = struct.unpack("!I4sII", raw[8:24])
    assert ihdr_len == 13 and ihdr_tag == b"IHDR" and (w, h) == (3, 2)

    has_cv2 = False
    try:
        import cv2
        has_cv2 = True
    except ImportError:
        cv2 = None

    if has_cv2:
        decoded_bgr = cv2.imdecode(np.frombuffer(raw, np.uint8), cv2.IMREAD_COLOR)
        assert decoded_bgr is not None
        assert np.array_equal(decoded_bgr[..., ::-1], rgb)

    big = np.zeros((2000, 3000, 3), dtype=np.uint8)
    small = capture.downscale(big, LOOK_MAX_SIDE)
    assert max(small.shape[:2]) <= LOOK_MAX_SIDE, small.shape

    data, mime = capture.encode(rgb, 85)
    assert data and mime == ("image/jpeg" if has_cv2 else "image/png")

    # Force cv2 ImportError -> PNG fallback.
    saved = sys.modules.get("cv2", False)
    sys.modules["cv2"] = None  # type: ignore[assignment]
    try:
        data_png, mime_png = capture.encode(rgb, 85)
        assert mime_png == "image/png" and data_png.startswith(b"\x89PNG\r\n\x1a\n")
    finally:
        if saved is False:
            sys.modules.pop("cv2", None)
        else:
            sys.modules["cv2"] = saved  # type: ignore[assignment]


class LookBackend:
    """Minimal Backend double implementing only what ComputerService.look() calls."""

    def __init__(self) -> None:
        self.captured: list[Rect] = []
        self.capture_error: Exception | None = None
        self._mons = [
            Monitor(0, "eDP-1", Rect(0, 0, 1920, 1080), True),
            Monitor(1, "HDMI-1", Rect(1920, 0, 1920, 1080), False),
        ]
        self._wins: dict[int, Window] = {
            10: Window(10, "Pointed App", "chrome", 1010, 0, Rect(50, 60, 800, 500), False, False, "NORMAL"),
            20: Window(20, "Under Mouse", "code", 1020, 0, Rect(200, 200, 700, 400), False, False, "NORMAL"),
            30: Window(30, "Active Term", "terminal", 1030, 0, Rect(1000, 100, 600, 500), False, False, "NORMAL"),
            40: Window(40, "Hidden", "notes", 1040, 0, Rect(100, 100, 500, 400), False, True, "NORMAL"),
        }
        self._pointer = (300, 300)
        self._active: int | None = 30

    def own_pids(self) -> set[int]:
        return {9999}

    def monitors(self) -> list[Monitor]:
        return list(self._mons)

    def desktops(self) -> tuple[int, int]:
        return (0, 2)

    def pointer(self) -> tuple[int, int]:
        return self._pointer

    def active_window(self) -> int | None:
        return self._active

    def window(self, wid: int) -> Window | None:
        return self._wins.get(wid)

    def windows(self) -> list[Window]:
        return list(self._wins.values())

    def capture(self, rect: Rect) -> tuple[bytes, str]:
        self.captured.append(rect)
        if self.capture_error is not None:
            raise self.capture_error
        return (b"img", "image/png")


def check_computer_look_and_specs() -> None:
    be = LookBackend()
    svc = ComputerService(be)  # type: ignore[arg-type]
    pointed_val: list[tuple[int, str, str, float] | None] = [(10, "chrome", "Pointed App", 2.0)]
    enabled = [True]
    svc.configure_look(pointed=lambda: pointed_val[0], enabled=lambda: enabled[0])

    # tool_specs / owns obey enabled()
    assert any(s.name == "look_at_screen" for s in svc.tool_specs()) and svc.owns("look_at_screen")
    enabled[0] = False
    assert not any(s.name == "look_at_screen" for s in svc.tool_specs()) and not svc.owns("look_at_screen")
    enabled[0] = True

    # Pointed fresh (age 2) -> captures window 10
    r = svc.look("pointed")
    assert r.image == b"img" and r.image_mime == "image/png"
    assert be.captured[-1] == Rect(50, 60, 800, 500)

    # Pointed stale (age 30) -> captures window under pointer (20)
    pointed_val[0] = (10, "chrome", "Pointed App", 30.0)
    r = svc.look("pointed")
    assert r.image == b"img" and be.captured[-1] == Rect(200, 200, 700, 400)

    # No pointer window -> falls through to active window (30)
    be._pointer = (2500, 500)
    r = svc.look("pointed")
    assert r.image == b"img" and be.captured[-1] == Rect(1000, 100, 600, 500)

    # "screen" -> captures monitor under pointer (HDMI-1 at x=2500)
    r = svc.look("screen")
    assert r.image == b"img" and be.captured[-1] == Rect(1920, 0, 1920, 1080)

    # Minimized window -> "Could not look", no image
    pointed_val[0] = (40, "notes", "Hidden", 1.0)
    r = svc.look("pointed")
    assert r.text.startswith("Could not look") and r.image is None

    # Capture raising ActionError("x") -> text starts with "Could not look", no image
    pointed_val[0] = (10, "chrome", "Pointed App", 1.0)
    be.capture_error = ActionError("x")
    r = svc.look("pointed")
    assert r.text.startswith("Could not look") and "x" in r.text and r.image is None


def check_tool_service_dispatch_reply() -> None:
    class ImgProvider:
        def tool_specs(self) -> list[ToolSpec]:
            return [ToolSpec("snap", "snap", {"type": "object", "properties": {}})]

        def owns(self, name: str) -> bool:
            return name == "snap"

        def call(self, name: str, arguments: dict) -> ToolReply:
            return ToolReply("Here it is.", b"PNGDATA", "image/png")

    ts = ToolService()
    ts.add_provider(ImgProvider())
    reply, ends = ts.dispatch_reply("snap", {})
    assert ends is False and reply == ToolReply("Here it is.", b"PNGDATA", "image/png")

    text, ends2 = ts.dispatch("snap", {})
    assert ends2 is False and text == "Here it is." + IMAGE_UNSUPPORTED


def check_wire_formats() -> None:
    oai = OpenAICompatChat("openrouter", "k", "google/gemini-3.8-flash")
    # Single tool result with image
    h1 = [
        Message("user", "look"),
        Message("assistant", "", tool_calls=[ToolCall("c1", "look_at_screen", {})]),
        Message("tool", "shot 1", tool_call_id="c1", image=b"IMG1", image_mime="image/png"),
    ]
    msgs1 = oai._payload("sys", h1, [])["messages"]
    assert len(msgs1) == 5 and msgs1[-1]["role"] == "user"
    parts1 = msgs1[-1]["content"]
    assert len(parts1) == 2 and parts1[1]["type"] == "image_url"
    assert parts1[1]["image_url"]["url"] == f"data:image/png;base64,{base64.b64encode(b'IMG1').decode()}"

    # Two tool results with images in one run -> one user message after both, with two images
    h2 = [
        Message("user", "look at both"),
        Message("assistant", "", tool_calls=[ToolCall("c1", "look", {}), ToolCall("c2", "look", {})]),
        Message("tool", "shot 1", tool_call_id="c1", image=b"A", image_mime="image/png"),
        Message("tool", "shot 2", tool_call_id="c2", image=b"B", image_mime="image/jpeg"),
    ]
    msgs2 = oai._payload("sys", h2, [])["messages"]
    assert [m["role"] for m in msgs2] == ["system", "user", "assistant", "tool", "tool", "user"]
    img_parts = [p for p in msgs2[-1]["content"] if p["type"] == "image_url"]
    assert len(img_parts) == 2
    assert img_parts[0]["image_url"]["url"].startswith("data:image/png;base64,")
    assert img_parts[1]["image_url"]["url"].startswith("data:image/jpeg;base64,")

    # No image -> no extra user message
    h0 = [
        Message("user", "hi"),
        Message("assistant", "", tool_calls=[ToolCall("c1", "run", {})]),
        Message("tool", "ok", tool_call_id="c1"),
    ]
    msgs0 = oai._payload("sys", h0, [])["messages"]
    assert [m["role"] for m in msgs0] == ["system", "user", "assistant", "tool"]

    # Anthropic _messages without SDK
    anth = AnthropicChat.__new__(AnthropicChat)
    anth.provider = "anthropic"
    am = anth._messages([
        Message("user", "look"),
        Message("assistant", "", tool_calls=[ToolCall("c1", "look_at_screen", {})]),
        Message("tool", "shot 1", tool_call_id="c1", image=b"IMG1", image_mime="image/jpeg"),
    ])
    tr = am[2]["content"][0]
    assert tr["type"] == "tool_result" and isinstance(tr["content"], list)
    assert tr["content"][1] == {
        "type": "image",
        "source": {"type": "base64", "media_type": "image/jpeg", "data": base64.b64encode(b"IMG1").decode()},
    }


def check_fit_history_and_tokens() -> None:
    m1 = Message("tool", "first shot", tool_call_id="c1", image=b"A", image_mime="image/png")
    m2 = Message("tool", "second shot", tool_call_id="c2", image=b"B", image_mime="image/jpeg")
    no_img = Message("tool", "first shot", tool_call_id="c1")
    assert message_tokens(m1) == message_tokens(no_img) + LOOK_IMAGE_TOKENS

    hist = [
        Message("user", "u1"),
        Message("assistant", "", tool_calls=[ToolCall("c1", "look", {})]),
        m1,
        Message("assistant", "a1"),
        Message("user", "u2"),
        Message("assistant", "", tool_calls=[ToolCall("c2", "look", {})]),
        m2,
    ]
    view = fit_history(hist, 100_000)
    assert view[2].image is None and view[2].image_mime == ""
    assert view[2].text.endswith("(The picture is no longer attached.)")
    assert view[6].image == b"B" and view[6].image_mime == "image/jpeg"
    assert m1.image == b"A", "original history is untouched"


def check_blind_latch() -> None:
    class BlindProvider:
        def tool_specs(self) -> list[ToolSpec]:
            return [ToolSpec("look_at_screen", "look", {"type": "object", "properties": {}})]

        def owns(self, name: str) -> bool:
            return name == "look_at_screen"

        def call(self, name: str, arguments: dict) -> ToolReply:
            return ToolReply("Screenshot taken.", b"RAWIMG", "image/png")

    class BlindModel:
        provider, model = "openai", "text-only"

        def __init__(self) -> None:
            self.seen: list[list[tuple[str, str, bytes | None]]] = []
            self.step = 0

        def stream(self, system: str, history: list[Message], tools):
            self.seen.append([(m.role, m.text, m.image) for m in history])
            if any(m.image for m in history):
                raise HttpError("no images", 400)
            self.step += 1
            if self.step == 1:
                yield Done(Message("assistant", "", tool_calls=[ToolCall("t1", "look_at_screen", {})]), "tool_use")
            elif self.step == 2:
                yield TextDelta("I cannot see images.")
                yield Done(Message("assistant", "I cannot see images."), "end")
            elif self.step == 3:
                yield Done(Message("assistant", "", tool_calls=[ToolCall("t2", "look_at_screen", {})]), "tool_use")
            else:
                yield TextDelta("Still text only.")
                yield Done(Message("assistant", "Still text only."), "end")

    turns = iter(["look again"])

    class Voice:
        n = 0

        def player(self, settings, presence=None, listen=False):
            return FakePlayer()

        def transcriber(self, settings):
            class Ear:
                def transcribe(self, wav: bytes) -> str:
                    return next(turns)
            return Ear()

        @staticmethod
        def to_wav(pcm: bytes) -> bytes:
            return pcm

        def record(self, settings, should_stop, live=None, carry=None) -> bytes:
            self.n += 1
            return b"speech" if self.n == 1 else b""

    model = BlindModel()
    ts = ToolService()
    ts.add_provider(BlindProvider())
    conv = PipelineConversation(
        Settings(brain_provider="openai", subtitles=False),
        FakeBrain(model),
        Voice(),
        ts,
        FakePresence(),
        threading.Event(),
    )
    conv.run(initial_prompt="Look at this.", then_listen=True)

    # Calls: 1) initial -> tool_call t1; 2) with image -> 400; 3) retry without image -> answer;
    # 4) second user turn -> tool_call t2; 5) sent directly without image (no 400) -> answer.
    assert len(model.seen) == 5, len(model.seen)
    assert any(img for _, _, img in model.seen[1])
    assert not any(img for _, _, img in model.seen[2])
    tool_msgs = [m for m in conv._history if m.role == "tool"]
    assert len(tool_msgs) == 2
    assert tool_msgs[0].image is None and tool_msgs[0].text.endswith(BLIND_NOTE)
    assert tool_msgs[1].image is None and tool_msgs[1].text.endswith(BLIND_NOTE)
    assert not any(img for _, _, img in model.seen[4])


def check_controller_pointed() -> None:
    air = FakeAir()
    win_calls = [0]

    class CountingDesktop(FakeDesktop):
        def window_at(self, x: int, y: int) -> tuple[int, str, str] | None:
            win_calls[0] += 1
            return super().window_at(x, y)

    desk = CountingDesktop(pointer_pos=(400, 300), target=(77, "code", "main.py"))
    now = [10.0]
    ctrl = AirController(air, desk, clock=lambda: now[0])  # type: ignore[arg-type]
    ctrl.process(Command("engage", 0.0))

    for i in range(10):
        t = 0.01 * i
        now[0] = 10.0 + t
        ctrl.process(Command("cursor", t, dx=5.0, dy=2.0))

    assert win_calls[0] == 1, f"expected throttled window_at once in 0.1 s, got {win_calls[0]}"
    p = ctrl.pointed()
    assert p is not None and p[0] == 77 and p[1] == "code" and p[2] == "main.py" and p[3] < 1.0, p
    st = ctrl.state()
    assert st["cursor"] is not None and st["cursor_age"] < 1.0, st


def check_assistant_air_pointed() -> None:
    bus = EventBus()
    model = RecordingModel()
    turns = iter(["what is this?", "and that?"])

    class TwoTurnVoice:
        n = 0

        def player(self, settings, presence=None, listen=False):
            return FakePlayer()

        def transcriber(self, settings):
            class Ear:
                def transcribe(self, wav: bytes) -> str:
                    return next(turns)
            return Ear()

        @staticmethod
        def to_wav(pcm: bytes) -> bytes:
            return pcm

        def record(self, settings, should_stop, live=None, carry=None) -> bytes:
            self.n += 1
            return b"wav" if self.n <= 2 else b""

    pointed_at = time.monotonic() - 2.0
    assistant = Assistant(
        FakeSettings(Settings(brain_provider="anthropic", subtitles=False)),
        FakeBrain(model),
        TwoTurnVoice(),
        ToolService(None),
        bus,
        presence=FakePresence(),
        air_pointed=lambda: (77, "code", "main.py", time.monotonic() - pointed_at),
    )
    assistant.converse()
    assert len(model.requests) == 2
    assert model.requests[0][-1].text.startswith('[The user is pointing at a window: "main.py" (code).')
    assert "look_at_screen" in model.requests[0][-1].text
    assert model.requests[1][-1].text == "and that?", "the same point must not be used twice"

    # Age 16 -> no note
    model2 = RecordingModel()
    turns2 = iter(["explain this"])

    class OneTurnVoice:
        n = 0

        def player(self, settings, presence=None, listen=False):
            return FakePlayer()

        def transcriber(self, settings):
            class Ear:
                def transcribe(self, wav: bytes) -> str:
                    return next(turns2)
            return Ear()

        @staticmethod
        def to_wav(pcm: bytes) -> bytes:
            return pcm

        def record(self, settings, should_stop, live=None, carry=None) -> bytes:
            self.n += 1
            return b"wav" if self.n == 1 else b""

    assistant2 = Assistant(
        FakeSettings(Settings(brain_provider="anthropic", subtitles=False)),
        FakeBrain(model2),
        OneTurnVoice(),
        ToolService(None),
        EventBus(),
        presence=FakePresence(),
        air_pointed=lambda: (77, "code", "main.py", 16.0),
    )
    assistant2.converse()
    assert model2.requests[0][-1].text == "explain this", model2.requests[0][-1]


def check_gaze_and_settings() -> None:
    assert Settings().look_enabled is True

    b = StateBlender()
    proj = _projector(130.0, 130.0, 67.6)
    nose = V0[int(np.argmax(V0[:, 2]))]

    def nose_xy(gaze: tuple[float, float]) -> tuple[float, float]:
        _, yaw, pitch, _ = _motion(1.0, b, 0.0, gaze=gaze)
        x, y = proj(_rot(nose, yaw, pitch)).tolist()
        return (x, y)

    right_x, _ = nose_xy((1.0, 0.0))
    left_x, _ = nose_xy((-1.0, 0.0))
    assert right_x > left_x, (right_x, left_x)

    _, down_y = nose_xy((0.0, 1.0))
    _, up_y = nose_xy((0.0, -1.0))
    assert down_y > up_y, (down_y, up_y)

    air = AirView(on=True, gaze=(0.5, 0.2))
    for style in STYLES:
        surf = render_surface(1.0, b, look=look_from(style), air=air)
        assert surf.get_width() > 0


def main() -> int:
    for name, fn in list(globals().items()):
        if name.startswith("check_"):
            fn()
            print(f"  {name[6:]} OK")
    print("air look OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
