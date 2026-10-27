"""The pure rules: settings, capabilities, speech chunking, connectors, plans."""

import _setup  # noqa: F401
import json
import sys
import time

from lura.modules.assistant import system_prompt, trim_history
from lura.modules.billing import License, entitled
from lura.modules.brain import Message
from lura.modules.connectors import Connector, may_add, parse_mcp_json, tool_name
from lura.modules.settings import Settings, brain_hears_audio
from lura.modules.settings.domain import apply_patch, capabilities, migrate_legacy
from lura.modules.voice import SentenceChunker, speakable


def check_settings():
    s = Settings()
    assert apply_patch(s, {"language": "ar", "silence_ms": "1200"}) == ["language", "silence_ms"]
    assert s.silence_ms == 1200 and s.language == "ar"
    try:
        apply_patch(s, {"language": "fr"})
        raise AssertionError("fr accepted")
    except ValueError:
        pass
    try:
        apply_patch(s, {"nope": 1})
        raise AssertionError("unknown accepted")
    except KeyError:
        pass
    # All or nothing: a bad value leaves the good one unapplied.
    try:
        apply_patch(s, {"silence_ms": 500, "mode": "bogus"})
    except ValueError:
        pass
    assert s.silence_ms == 1200
    # Gender and voice stay consistent.
    apply_patch(s, {"voice_gender": "male"})
    assert s.voice_name == "Puck"
    apply_patch(s, {"voice_name": "Kore"})
    assert s.voice_gender == "female"
    # Models merge rather than replace.
    apply_patch(s, {"models": {"anthropic": "claude-sonnet-5-5"}})
    assert s.models["anthropic"] == "claude-sonnet-5-5" and s.models["gemini"] == "gemini-3.8-flash"
    apply_patch(s, {"openrouter_provider_order": "a, b"})
    assert s.openrouter_provider_order == ["a", "b"]
    apply_patch(s, {"input_device": "none"})
    assert s.input_device is None


def check_capabilities():
    s = Settings(brain_provider="anthropic")
    c = capabilities(s, set())
    assert not c.can_talk and c.problems == ("problem.no_voice_key", "problem.no_brain_key")
    c = capabilities(s, {"anthropic"})
    assert c.problems == ("problem.no_voice_key",), "Claude alone cannot speak"
    c = capabilities(s, {"anthropic", "openrouter"})
    assert c.can_talk and c.voice_provider == "openrouter"
    c = capabilities(s, {"anthropic", "openrouter", "gemini"})
    assert c.voice_provider == "gemini", "auto prefers direct Gemini"
    s.voice_provider = "openrouter"
    assert capabilities(s, {"anthropic", "openrouter", "gemini"}).voice_provider == "openrouter"
    s.mode = "realtime"
    assert capabilities(s, {"openrouter"}).problems == ("problem.realtime_needs_gemini",)
    assert capabilities(s, {"gemini"}).can_talk
    assert brain_hears_audio("gemini", "gemini-3.8-flash")
    assert brain_hears_audio("openrouter", "google/gemini-3.8-flash")
    assert not brain_hears_audio("openrouter", "anthropic/claude-sonnet-5.5")
    assert not brain_hears_audio("anthropic", "claude-opus-5-5")
    assert not brain_hears_audio("openai", "gpt-5.5")


def check_migration():
    old = {"provider": "openrouter", "openrouter_model": "google/gemini-3.7-flash",
           "reply_language": "English", "voice": "Puck", "wake_word": "lura", "silence_ms": 800}
    new = migrate_legacy(old)
    assert new["brain_provider"] == "openrouter" and new["mode"] == "pipeline"
    assert new["models"]["openrouter"] == "google/gemini-3.7-flash"
    assert new["voice_name"] == "Puck" and new["voice_gender"] == "male"
    assert new["silence_ms"] == 800 and new["language"] == "en"
    assert migrate_legacy({"provider": "gemini"})["mode"] == "realtime"


def check_chunker():
    c = SentenceChunker()
    out = []
    text = "Hi there! I opened Chrome for you. The weather is mild today, around 23 degrees. Anything else?"
    for word in text.split(" "):
        out += c.feed(word + " ")
    out += c.flush()
    assert out[0] == "Hi there!", out           # the first sentence goes out alone, early
    assert " ".join(out) == text, out           # nothing lost, nothing doubled
    c = SentenceChunker()
    out = c.feed("ازيك يا محمد؟ أنا فتحت كروم. ") + c.flush()
    assert out[0] == "ازيك يا محمد؟", out
    c = SentenceChunker()
    long = "word " * 120
    out = c.feed(long) + c.flush()
    assert all(len(p) <= 260 for p in out) and sum(len(p.split()) for p in out) == 120
    assert speakable("**Bold** and `code`") == "Bold and code"


def check_connectors():
    text = json.dumps({"mcpServers": {
        "GitHub": {"command": "npx", "args": ["-y", "@modelcontextprotocol/server-github"],
                   "env": {"GITHUB_PERSONAL_ACCESS_TOKEN": "x"}},
        "linear": {"type": "http", "url": "https://mcp.linear.app/mcp",
                   "headers": {"Authorization": "Bearer abc"}},
    }})
    parsed = {c.name: c for c in parse_mcp_json("// comment\n" + text)}
    assert parsed["github"].command == "npx" and parsed["github"].env
    assert parsed["linear"].transport == "http" and parsed["linear"].token == "abc"
    assert "Authorization" not in parsed["linear"].headers
    assert parsed["linear"].to_mcp_json()["headers"]["Authorization"] == "Bearer abc"
    assert parsed["linear"].public()["token"] == "••••"
    for bad in ("not json", "[]", '{"x": 1}', '{"mcpServers": {"a": {"url": "ftp://x"}}}'):
        try:
            parse_mcp_json(bad)
            raise AssertionError(f"accepted {bad}")
        except ValueError:
            pass
    vscode = parse_mcp_json('{"servers": {"s": {"type": "stdio", "command": "npx", "args": ["x"]}}}')
    assert vscode[0].command == "npx"
    assert may_add(Connector("chrome", builtin=True), is_pro=False)
    assert not may_add(Connector("github"), is_pro=False)
    assert may_add(Connector("github"), is_pro=True)
    assert tool_name("chrome-devtools", "new_page") == "mcp_chrome_devtools_new_page"
    assert len(tool_name("x" * 40, "y" * 40)) == 64


def check_billing():
    now = time.time()
    lic = License(key="k", instance_id="i", status="active", last_validated=now)
    assert entitled(lic, now)
    assert entitled(lic, now + 6 * 86400), "offline grace"
    assert not entitled(lic, now + 8 * 86400), "grace ends"
    lic.status = "expired"
    assert not entitled(lic, now)
    assert not entitled(License(), now)


def check_assistant_rules():
    prompt = system_prompt("You are {name}.", "Lura", "Mohamed", "ar", "\n[ctx]\n")
    assert prompt.startswith("You are Lura.") and "Mohamed" in prompt
    assert prompt.splitlines()[-1].startswith("IMPORTANT: Always answer in Egyptian Arabic"), "language rule comes last"
    history = []
    for i in range(14):
        history += [Message("user", f"q{i}"), Message("assistant", "", tool_calls=[]),
                    Message("tool", "r", tool_call_id=f"c{i}"), Message("assistant", f"a{i}")]
    trimmed = trim_history(history, 10)
    assert trimmed[0].role == "user" and trimmed[0].text == "q4" and len(trimmed) == 40


def main() -> int:
    for name, fn in list(globals().items()):
        if name.startswith("check_"):
            fn()
    print("domain OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
