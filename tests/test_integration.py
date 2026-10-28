"""Adapters against local mock servers, and one full pipeline turn with fakes.

No real keys, no network, no audio device.
"""

import _setup  # noqa: F401
import base64
import json
import os
import sys
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

SEEN: list[dict] = []


def serve(handler_fn):
    """Start a server; handler_fn(handler, body) writes the response."""
    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _any(self):
            length = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(length) if length else b""
            try:
                body = json.loads(raw) if raw else {}
            except json.JSONDecodeError:
                body = {"_raw": raw.decode()}
            SEEN.append({"path": self.path, "headers": {k.lower(): v for k, v in self.headers.items()}, "body": body,
                         "method": self.command})
            handler_fn(self, body)

        do_POST = do_GET = do_DELETE = _any

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd, f"http://127.0.0.1:{httpd.server_address[1]}"


def sse(handler, events):
    handler.send_response(200)
    handler.send_header("Content-Type", "text/event-stream")
    handler.end_headers()
    for event in events:
        if isinstance(event, tuple):
            handler.wfile.write(f"event: {event[0]}\ndata: {json.dumps(event[1])}\n\n".encode())
        else:
            handler.wfile.write(f"data: {event if isinstance(event, str) else json.dumps(event)}\n\n".encode())
        handler.wfile.flush()


def check_openai_stream():
    from lura.modules.brain import Done, Message, TextDelta, ToolSpec
    from lura.modules.brain.infrastructure import openai_compat

    def handler(h, body):
        chunk = lambda delta, finish=None: {"choices": [{"index": 0, "delta": delta, "finish_reason": finish}]}
        sse(h, [": OPENROUTER PROCESSING",
                chunk({"content": "Opening "}), chunk({"content": "Chrome."}),
                chunk({"tool_calls": [{"index": 0, "id": "call_1", "type": "function",
                                       "function": {"name": "open_application", "arguments": ""}}]}),
                chunk({"tool_calls": [{"index": 0, "function": {"arguments": '{"app_na'}}]}),
                chunk({"tool_calls": [{"index": 0, "function": {"arguments": 'me": "chrome"}'}}]}),
                chunk({"reasoning_details": [{"type": "reasoning.encrypted", "data": "x"}]}, "tool_calls"),
                {"choices": [], "usage": {"prompt_tokens": 10, "completion_tokens": 3, "cost": 0.0001}},
                "[DONE]"])

    httpd, url = serve(handler)
    openai_compat.BASES["openrouter"] = url
    model = openai_compat.OpenAICompatChat("openrouter", "sk-test", "google/gemini-3.8-flash",
                                           reasoning_effort="minimal", provider_order=["google-vertex"])
    spec = ToolSpec("open_application", "Open", {"type": "object", "properties": {"app_name": {"type": "string"}},
                                                  "required": ["app_name"]})
    events = list(model.stream("sys", [Message("user", audio_wav=b"RIFFfake")], [spec]))
    text = "".join(e.text for e in events if isinstance(e, TextDelta))
    done = events[-1]
    assert isinstance(done, Done) and text == "Opening Chrome." and done.stop_reason == "tool_use"
    call = done.message.tool_calls[0]
    assert call.name == "open_application" and call.arguments == {"app_name": "chrome"} and call.valid
    native = done.message.native[1]
    assert native["reasoning_details"] and native["tool_calls"][0]["function"]["arguments"].endswith("}")
    req = SEEN[-1]
    assert req["path"] == "/chat/completions" and req["headers"]["authorization"] == "Bearer sk-test"
    body = req["body"]
    assert body["stream"] and body["reasoning"] == {"effort": "minimal"}
    assert body["provider"]["order"] == ["google-vertex"] and body["tools"][0]["function"]["name"] == "open_application"
    audio = body["messages"][1]["content"][0]
    assert audio["type"] == "input_audio" and base64.b64decode(audio["input_audio"]["data"]) == b"RIFFfake"
    # The turn replays exactly (same provider) and neutrally (another provider).
    assert model._wire(done.message) is native
    other = openai_compat.OpenAICompatChat("openai", "k", "gpt-5.5")
    assert other._wire(done.message)["tool_calls"][0]["function"]["name"] == "open_application"

    # Broken arguments are flagged, not run.
    def broken(h, body):
        sse(h, [{"choices": [{"delta": {"tool_calls": [{"index": 0, "id": "c", "function": {
            "name": "open_application", "arguments": '{"app_na'}}]}, "finish_reason": "tool_calls"}]}, "[DONE]"])
    httpd2, url2 = serve(broken)
    openai_compat.BASES["openai"] = url2
    done = list(openai_compat.OpenAICompatChat("openai", "k", "gpt-5.5").stream("s", [Message("user", "hi")], [spec]))[-1]
    assert not done.message.tool_calls[0].valid
    httpd.shutdown(), httpd2.shutdown()


def check_anthropic_stream():
    try:
        import anthropic  # noqa: F401
    except ImportError:
        print("  (anthropic SDK not installed — skipped)")
        return
    from lura.modules.brain import Done, Message, TextDelta, ToolCall, ToolSpec

    def handler(h, body):
        msg = {"id": "msg_1", "type": "message", "role": "assistant", "model": body["model"], "content": [],
               "stop_reason": None, "stop_sequence": None,
               "usage": {"input_tokens": 12, "output_tokens": 1}}
        sse(h, [("message_start", {"type": "message_start", "message": msg}),
                ("content_block_start", {"type": "content_block_start", "index": 0,
                                         "content_block": {"type": "text", "text": ""}}),
                ("content_block_delta", {"type": "content_block_delta", "index": 0,
                                         "delta": {"type": "text_delta", "text": "Let me check. "}}),
                ("content_block_stop", {"type": "content_block_stop", "index": 0}),
                ("content_block_start", {"type": "content_block_start", "index": 1,
                                         "content_block": {"type": "tool_use", "id": "toolu_1",
                                                           "name": "run_command", "input": {}}}),
                ("content_block_delta", {"type": "content_block_delta", "index": 1,
                                         "delta": {"type": "input_json_delta", "partial_json": '{"command": "df -h"}'}}),
                ("content_block_stop", {"type": "content_block_stop", "index": 1}),
                ("message_delta", {"type": "message_delta", "delta": {"stop_reason": "tool_use", "stop_sequence": None},
                                   "usage": {"output_tokens": 20}}),
                ("message_stop", {"type": "message_stop"})])

    httpd, url = serve(handler)
    os.environ["ANTHROPIC_BASE_URL"] = url
    from lura.modules.brain.infrastructure.anthropic_chat import AnthropicChat

    model = AnthropicChat("sk-ant-test", "claude-opus-5-5", reasoning_effort="minimal")
    spec = ToolSpec("run_command", "Run", {"type": "object", "properties": {"command": {"type": "string"}},
                                           "required": ["command"]})
    history = [Message("user", "how much disk?"),
               Message("assistant", "", tool_calls=[ToolCall("c1", "run_command", {"command": "ls"})]),
               Message("tool", "a", tool_call_id="c1"), Message("tool", "b", tool_call_id="c2")]
    events = list(model.stream("sys", history, [spec]))
    text = "".join(e.text for e in events if isinstance(e, TextDelta))
    done = events[-1]
    assert isinstance(done, Done) and text == "Let me check. " and done.stop_reason == "tool_use"
    assert done.message.tool_calls[0].arguments == {"command": "df -h"} and done.message.tool_calls[0].valid
    req = SEEN[-1]["body"]
    assert req["model"] == "claude-opus-5-5" and req["output_config"] == {"effort": "low"}
    assert req["fallbacks"] == "default" and "server-side-fallback-2026-07-01" in SEEN[-1]["headers"].get("anthropic-beta", "")
    assert req["tools"][0]["input_schema"]["required"] == ["command"]
    # Both tool results of one turn travel in one user message.
    assert [m["role"] for m in req["messages"]] == ["user", "assistant", "user"]
    assert [b["tool_use_id"] for b in req["messages"][2]["content"]] == ["c1", "c2"]
    # Cached: the system prompt, and the thread up to the newest turn.
    assert req["system"][0]["cache_control"] == {"type": "ephemeral"}
    assert req["messages"][2]["content"][-1]["cache_control"] == {"type": "ephemeral"}
    assert "cache_control" not in req["messages"][2]["content"][0]
    # The native turn replays unchanged next time.
    assert model._messages([done.message])[0]["content"] == done.message.native[1]
    del os.environ["ANTHROPIC_BASE_URL"]
    httpd.shutdown()


def check_tts_and_hearing():
    from lura.modules.voice.infrastructure import transcribe, tts

    pcm24 = (b"\x01\x00" * 2400)

    def gemini(h, body):
        if "flash-tts" in h.path and "lite" not in h.path:
            h.send_response(429)
            h.end_headers()
            h.wfile.write(b'{"error": "rate limited"}')
            return
        data = {"candidates": [{"content": {"parts": [{"inlineData": {
            "mimeType": "audio/L16;codec=pcm;rate=24000", "data": base64.b64encode(pcm24).decode()}}]}}]}
        out = json.dumps(data).encode()
        h.send_response(200)
        h.send_header("Content-Type", "application/json")
        h.send_header("Content-Length", str(len(out)))
        h.end_headers()
        h.wfile.write(out)

    httpd, url = serve(gemini)
    tts.GEMINI_BASE = url
    out = tts.GeminiTTS("g-key", "gemini-3.8-flash-tts", "Kore").synthesize("Hello")
    assert out == pcm24, "429 falls back to the Lite model"
    paths = [s["path"] for s in SEEN[-2:]]
    assert paths == ["/models/gemini-3.8-flash-tts:generateContent", "/models/gemini-3.8-flash-lite-tts:generateContent"]
    assert SEEN[-1]["body"]["generationConfig"]["speechConfig"]["voiceConfig"]["prebuiltVoiceConfig"]["voiceName"] == "Kore"
    assert SEEN[-1]["headers"]["x-goog-api-key"] == "g-key"
    httpd.shutdown()

    def openrouter(h, body):
        if body.get("response_format") == "pcm":
            h.send_response(400)
            h.end_headers()
            h.wfile.write(b'{"error": "unsupported response_format"}')
            return
        h.send_response(200)
        h.send_header("Content-Type", "audio/wav")
        h.end_headers()
        from lura.modules.voice.infrastructure.audio import pcm_to_wav
        h.wfile.write(pcm_to_wav(pcm24, 24000))

    httpd, url = serve(openrouter)
    tts.OPENROUTER_BASE = url
    voice = tts.OpenRouterTTS("or-key", "google/gemini-3.8-flash-tts", "Puck")
    assert voice.synthesize("Hi") == pcm24 and voice._format == "mp3"
    assert SEEN[-1]["body"]["model"] == "google/gemini-3.8-flash-tts" and SEEN[-1]["body"]["voice"] == "Puck"
    httpd.shutdown()

    def hear(h, body):
        if h.headers.get("Authorization") == "Bearer bad-gemini":
            h.send_response(429)
            h.end_headers()
            h.wfile.write(b'{"error": "quota exceeded"}')
            return
        out = json.dumps({"choices": [{"message": {"content": " ازيك يا لورا "}}]}).encode()
        h.send_response(200)
        h.send_header("Content-Type", "application/json")
        h.end_headers()
        h.wfile.write(out)

    httpd, url = serve(hear)
    transcribe.BASES["gemini"] = url
    transcribe.BASES["openrouter"] = url
    assert transcribe.ChatTranscriber("gemini", "k", "gemini-3.8-flash").transcribe(b"RIFF") == "ازيك يا لورا"
    assert SEEN[-1]["body"]["messages"][0]["content"][1]["type"] == "input_audio"
    from lura.modules.settings import Settings
    from lura.modules.voice import VoiceService
    keys = {"gemini": "bad-gemini", "openrouter": "good-or"}
    vsvc = VoiceService(keys.get, lambda p: p in keys)
    assert vsvc.transcriber(Settings(voice_provider="auto")).transcribe(b"RIFF") == "ازيك يا لورا"
    assert vsvc._voice_providers(Settings(voice_provider="auto")) == ["openrouter", "gemini"]
    httpd.shutdown()


def wait_ready(svc, name, timeout=20):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        row = next(c for c in svc.list() if c["name"] == name)
        if row["status"] in ("ready", "error"):
            return row
        time.sleep(0.1)
    raise AssertionError(f"{name} never started")


def check_connectors():
    from lura.modules.connectors import ConnectorService
    from lura.shared.errors import ProRequired

    pro = [False]
    svc = ConnectorService(lambda: pro[0])
    names = [c["name"] for c in svc.list()]
    assert names[:3] == ["chrome", "web", "files"], names
    try:
        svc.add_command("echo", f"{sys.executable} tests/fixtures/echo_mcp.py")
        raise AssertionError("free plan added a connector")
    except ProRequired:
        pass
    pro[0] = True
    fixture = os.path.join(os.path.dirname(__file__), "fixtures", "echo_mcp.py")
    svc.add_command("echo", f'"{sys.executable}" "{fixture}"')
    row = wait_ready(svc, "echo")
    assert row["status"] == "ready" and row["tools"] == ["echo_text", "open_tab"], row
    found = svc.search("open a new tab in the browser")
    assert found and found[0].name == "mcp_echo_open_tab", found
    assert svc.call("mcp_echo_echo_text", {"text": "hi"}) == "echo: hi"
    # Persisted in mcp.json, in the shape other clients read; builtins are not written.
    saved = json.loads(svc.config_path.read_text())
    assert list(saved["mcpServers"]) == ["echo"] and saved["mcpServers"]["echo"]["command"] == sys.executable
    if os.name != "nt":   # Windows has no Unix modes; the profile folder is private instead
        assert oct(svc.config_path.stat().st_mode)[-3:] == "600"
    # Losing Pro stops custom connectors but keeps them configured.
    pro[0] = False
    svc.refresh_plan()
    assert next(c for c in svc.list() if c["name"] == "echo")["status"] == "locked"
    assert not svc.owns("mcp_echo_echo_text")
    svc.set_enabled("chrome", False)
    assert ConnectorService(lambda: False).get("chrome").enabled is False
    svc.stop_all()

    # Streamable HTTP with a bearer token and a session id.
    def mcp_http(h, body):
        if h.command == "DELETE":
            h.send_response(200)
            h.end_headers()
            return
        assert h.headers["Authorization"] == "Bearer tok"
        if "id" not in body:
            h.send_response(202)
            h.end_headers()
            return
        if body["method"] == "initialize":
            result = {"protocolVersion": "2025-06-18", "capabilities": {}}
        elif body["method"] == "tools/list":
            assert h.headers.get("Mcp-Session-Id") == "s-1"
            result = {"tools": [{"name": "list_issues", "description": "List issues",
                                 "inputSchema": {"type": "object"}}]}
        else:
            return sse(h, [{"jsonrpc": "2.0", "method": "notifications/progress"},
                           {"jsonrpc": "2.0", "id": body["id"], "result": {"content": [{"type": "text", "text": "3 issues"}]}}])
        out = json.dumps({"jsonrpc": "2.0", "id": body["id"], "result": result}).encode()
        h.send_response(200)
        h.send_header("Content-Type", "application/json")
        h.send_header("Mcp-Session-Id", "s-1")
        h.end_headers()
        h.wfile.write(out)

    httpd, url = serve(mcp_http)
    pro[0] = True
    svc = ConnectorService(lambda: pro[0])
    svc.add(__import__("lura.modules.connectors", fromlist=["Connector"]).Connector(
        "tracker", "http", url="http://127.0.0.1:" + url.rsplit(":", 1)[1] + "/mcp", token="tok"))
    row = wait_ready(svc, "tracker")
    assert row["status"] == "ready", row
    assert svc.call("mcp_tracker_list_issues", {}) == "3 issues"
    svc.stop_all()
    httpd.shutdown()


def check_pipeline_turn():
    """Brain → tool → brain → speech, with every outside thing faked."""
    from lura.modules.assistant import PipelineConversation
    from lura.modules.brain import Done, Message, TextDelta, ToolCall
    from lura.modules.settings import Settings
    from lura.modules.tools import ToolService
    from lura.shared.presence import NullPresence

    calls = []

    class FakeModel:
        provider, model = "anthropic", "claude-opus-5-5"

        def stream(self, system, history, tools):
            calls.append([m.role for m in history])
            if history[-1].role == "user":
                yield TextDelta("Checking. ")
                yield Done(Message("assistant", "Checking.", tool_calls=[
                    ToolCall("t1", "run_command", {"command": "echo 42"})]), "tool_use")
            else:
                assert "42" in history[-1].text
                for part in ("The answer ", "is 42. ", "Anything else?"):
                    yield TextDelta(part)
                yield Done(Message("assistant", "The answer is 42. Anything else?"), "end")

    class FakeBrain:
        def model(self, settings):
            return FakeModel()

    spoken = []

    class FakePlayer:
        def __init__(self):
            self.chunker = __import__("lura.modules.voice", fromlist=["SentenceChunker"]).SentenceChunker()

        def feed(self, text):
            spoken.extend(self.chunker.feed(text))

        def finish(self):
            spoken.extend(self.chunker.flush())

        def say(self, text):
            self.feed(text)
            self.finish()

        def wait(self):
            pass

        def cancel(self):
            pass

    class FakeVoice:
        def player(self, settings, presence=None):
            return FakePlayer()

        def transcriber(self, settings):
            return None

    conv = PipelineConversation(Settings(brain_provider="anthropic"), FakeBrain(), FakeVoice(),
                                ToolService(None), NullPresence(), threading.Event())
    conv.run(initial_prompt="What is six times seven?")
    assert calls == [["user"], ["user", "assistant", "tool"]], calls
    assert spoken == ["Checking.", "The answer is 42.", "Anything else?"], spoken


def check_noise_and_live_captions():
    """A turn the brain judges to be noise is neither spoken nor kept; captions
    show what is being said while it is said."""
    import time
    from lura.modules.assistant import PipelineConversation
    from lura.modules.brain import Done, Message, TextDelta
    from lura.modules.settings import Settings
    from lura.modules.tools import ToolService

    histories, spoken, captions = [], [], []

    class FakeModel:
        provider, model = "anthropic", "claude-opus-5-5"

        def stream(self, system, history, tools):
            histories.append([(m.role, m.text) for m in history])
            reply = "[[quiet]]" if history[-1].text == "the TV" else "Hello there."
            for i in range(0, len(reply), 3):
                yield TextDelta(reply[i:i + 3])
            yield Done(Message("assistant", reply), "end")

    class FakeBrain:
        def model(self, settings):
            return FakeModel()

    class Player:
        def feed(self, text):
            spoken.append(text)
        finish = wait = cancel = lambda self: None

        def say(self, text):
            spoken.append(text)

    heard = iter(["the TV", "hi Lura"])

    class Ear:
        def transcribe(self, wav):
            return "partial words" if len(wav) < 40000 else next(heard)

    class FakeVoice:
        recorded = 0

        def player(self, settings, presence=None):
            return Player()

        def transcriber(self, settings):
            return Ear()

        @staticmethod
        def to_wav(pcm):
            return pcm

        def record(self, settings, should_stop, on_audio=None):
            self.recorded += 1
            if self.recorded > 2:
                return b""
            blocks = [b"\0" * 3200] * 15
            if on_audio:
                on_audio(blocks[:12], 0)   # mid-sentence: shorter than the final turn
                time.sleep(0.2)   # the caption thread
            return b"".join(blocks)

    class Presence:
        def set_state(self, state):
            pass

        def set_amplitude(self, level):
            pass

        def set_caption(self, who, text):
            captions.append((who, text))

    PipelineConversation(Settings(brain_provider="anthropic"), FakeBrain(), FakeVoice(), ToolService(None),
                         Presence(), threading.Event()).run()
    assert "".join(spoken) == "Hello there.", spoken
    assert histories[-1] == [("user", "hi Lura")], "the noise turn is gone from the thread"
    assert ("you", "partial words …") in captions, captions


def main() -> int:
    for name, fn in list(globals().items()):
        if name.startswith("check_"):
            fn()
            print(f"  {name[6:]} OK")
    print("integration OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
