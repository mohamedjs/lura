"""The pure rules: settings, capabilities, speech chunking, connectors, plans."""

import _setup  # noqa: F401
import json
import sys

from lura.modules.assistant import fit_history, system_prompt
from lura.modules.brain import Message
from lura.modules.connectors import Connector, parse_mcp_json, tool_name
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
    assert tool_name("chrome-devtools", "new_page") == "mcp_chrome_devtools_new_page"
    assert len(tool_name("x" * 40, "y" * 40)) == 64


def check_assistant_rules():
    from lura.modules.assistant.domain import LEGACY_PERSONA, NO_REPLY
    prompt = system_prompt("Call me boss, {name}.", "Nour", "Mohamed", "ar", "\n[ctx]\n", now="Wednesday")
    assert prompt.startswith("You are Nour,") and "The user's name is Mohamed." in prompt
    assert "Call me boss, Nour." in prompt and "[ctx]" in prompt and "It is now Wednesday." in prompt
    assert "rename you" in prompt and NO_REPLY in prompt, "renames and noise are covered"
    assert "Lura" not in prompt, "a renamed assistant is never called Lura"
    old_default = system_prompt(LEGACY_PERSONA, "Lura", "", "en", "")
    assert "user's own instructions" not in old_default and "save it with remember" in old_default
    assert NO_REPLY not in system_prompt("", "Lura", "", "en", "", realtime=True), "Live speaks; it stays silent instead"
    assert system_prompt("Use {braces} freely", "Lura", "", "en", "")   # a stray placeholder never breaks it
    last = prompt.splitlines()[-1]
    assert last.startswith("IMPORTANT: Reply in the language the user speaks"), "language rule comes last"
    assert "Your default is Egyptian Arabic" in last and "if they speak English, answer in English" in last
    assert "Your default is English" in system_prompt("", "Lura", "", "en", "")
    # Arabic is gendered: how she speaks to the user, and of herself.
    assert "address him in masculine" in system_prompt("", "Lura", "", "ar", "", user_gender="male")
    her = system_prompt("", "Lura", "", "ar", "", user_gender="female", assistant_gender="male")
    assert "address her in feminine" in her and "masculine forms for yourself" in her
    assert "avoids gendered forms" in system_prompt("", "Lura", "", "ar", "")
    # Connected MCP servers are named, so she knows to look for their tools.
    with_github = system_prompt("", "Lura", "", "en", "", services="Connected right now:\n- github (2 tools: a, b)")
    assert "find_tools" in with_github and "- github (2 tools" in with_github
    from lura.modules.assistant import briefing_prompt
    assert "تحب تبدأ" in briefing_prompt({}, "ar", "", "female") and "يحب يبدأ" in briefing_prompt({}, "ar", "")
    history = []
    for i in range(14):
        history += [Message("user", f"q{i}"), Message("assistant", "", tool_calls=[]),
                    Message("tool", "r", tool_call_id=f"c{i}"), Message("assistant", f"a{i}")]
    from lura.modules.assistant.domain import message_tokens
    trimmed = fit_history(history, sum(message_tokens(m) for m in history[16:]))   # exactly ten turns
    assert trimmed[0].role == "user" and trimmed[0].text == "q4" and len(trimmed) == 40
    assert fit_history(history, 1)[0].text == "q13", "the last user turn always goes"
    assert "[mem]" in system_prompt("You are {name}.", "Lura", "", "en", "", extra="[mem]")
    # Only the newest recording goes as sound; older ones as their transcript.
    wav = b"RIFF" + b"\0" * 3000
    talk = [Message("user", "first words", audio_wav=wav), Message("assistant", "a"),
            Message("user", "second words", audio_wav=wav)]
    sent = fit_history(talk, 10_000)
    assert sent[0].audio_wav is None and sent[0].text == "first words"
    assert sent[2].audio_wav == wav and sent[2].text == "", "not read twice"
    assert talk[0].audio_wav == wav, "the history itself is untouched"


def check_retry_after():
    from lura.modules.voice.domain import retry_after
    assert retry_after("Quota exceeded… Please retry in 4h7m39.221871599s.") == 4 * 3600 + 7 * 60 + 39.221871599
    assert retry_after("Please retry in 12.5s") == 12.5 and retry_after("retry in 3m") == 180
    assert retry_after("HTTP 503 overloaded") == 300


def check_devices():
    from lura.modules.voice.domain import pick_device
    devices = [{"name": "HDA Intel PCH: ALC257 Analog (hw:0,0)", "hostapi": 0, "max_input_channels": 2, "max_output_channels": 2},
               {"name": "USB Mic: Audio (hw:2,0)", "hostapi": 0, "max_input_channels": 1, "max_output_channels": 0},
               {"name": "pulse", "hostapi": 0, "max_input_channels": 32, "max_output_channels": 32},
               {"name": "USB Mic: Audio", "hostapi": 1, "max_input_channels": 1, "max_output_channels": 0}]
    assert pick_device(devices, None, "input") is None and pick_device(devices, " ", "input") is None
    assert pick_device(devices, "USB Mic: Audio (hw:1,0)", "input") == 1, "card number moved after a replug"
    assert pick_device(devices, "USB Mic: Audio", "input", hostapi=1) == 3, "default host API wins"
    assert pick_device(devices, "USB Mic: Audio", "output") is None, "an input-only device is not a speaker"
    assert pick_device(devices, "2", "input") == 2, "a legacy index still works"
    assert pick_device(devices, "9", "input") is None and pick_device(devices, "Gone", "input") is None


def check_end_of_turn():
    import random
    from lura.modules.voice.domain import EndOfTurn
    random.seed(7)

    def run(levels, silence_ms=1000):
        turn = EndOfTurn(0.1, silence_ms, 2.0, 8.0, 30.0)
        for i, level in enumerate(levels):
            if (verdict := turn.feed(level)) != "wait":
                return verdict, i, turn.start
        return "wait", len(levels), turn.start

    words = lambda n: [0.002 if i % 5 == 4 else random.uniform(0.05, 0.2) for i in range(n)]
    # Talking the instant the mic opens: the old 0.5 s calibration took this for noise.
    assert run(words(20) + [0.002] * 15) == ("done", 28, 0)
    assert run([0.01] * 100) == ("nothing", 79, None), "a steady fan is not speech"
    verdict, at, start = run([0.01] * 40 + [0.1] * 10 + [0.01] * 15)
    assert verdict == "done" and start == 37 and at == 59, "leading quiet trimmed to the pre-roll"
    assert run(words(20) + [0.002] * 15, silence_ms=500)[1] == 23
    blip = EndOfTurn(0.1, 1000, 2.0, 8.0, 30.0)
    for level in [0.005] * 10 + [0.2] + [0.005] * 10:
        blip.feed(level)
    assert blip.start is not None and abs(blip.spoken_seconds - 0.1) < 1e-9, "a click is one block"


def check_quiet_and_speech_band():
    import numpy as np
    from lura.modules.assistant.domain import could_be_quiet
    from lura.modules.voice.infrastructure.audio import speech_level
    assert could_be_quiet("") and could_be_quiet(" [[qu") and could_be_quiet("[[quiet]]")
    assert not could_be_quiet("Sure") and not could_be_quiet("[1] first")
    from lura.modules.assistant.domain import is_quiet, speakable_reply
    for quiet in ("[[quiet]]", " `[[quiet]]` ", '"[[quiet]]".', "[[quiet]]\n"):
        assert is_quiet(quiet), quiet
    assert not is_quiet("Okay. [[quiet]]") and speakable_reply("Okay. [[quiet]]") == "Okay. "
    assert speakable_reply("Sure thing [[qu") == "Sure thing ", "a half-written marker is held back"
    assert speakable_reply("Pick [1] or [2]") == "Pick [1] or [2]"
    t = np.arange(1600) / 16000
    tone = lambda f: (np.sin(2 * np.pi * f * t) * 8000).astype(np.int16).tobytes()   # noqa: E731
    assert speech_level(tone(50)) < 0.01 and speech_level(tone(6000)) < 0.01, "hum and hiss are not speech"
    assert abs(speech_level(tone(300)) - 0.1726) < 0.005


def check_resample():
    import numpy as np
    from lura.modules.voice.infrastructure.audio import resample
    tone = (np.sin(np.arange(4800) * 2 * np.pi * 440 / 48000) * 8000).astype(np.int16).tobytes()
    out = np.frombuffer(resample(tone, 48000, 16000), np.int16)
    assert len(out) == 1600 and 7000 < np.abs(out).max() <= 8000
    assert resample(tone, 16000, 16000) is tone


def check_brain_catalog():
    from lura.modules.brain.domain import brain_candidate as bc
    tools = {"supported_parameters": ["tools"], "architecture": {"input_modalities": ["text", "audio"], "output_modalities": ["text"]}}
    assert bc("openrouter", {"id": "google/gemini-3.8-flash", **tools})["hears"] is True
    assert bc("openrouter", {"id": "x/no-tools", "supported_parameters": []}) is None
    assert bc("openrouter", {"id": "google/gemini-3.8-flash-tts", **tools}) is None
    assert bc("gemini", {"id": "models/gemini-3.8-flash"})["id"] == "gemini-3.8-flash"
    assert bc("gemini", {"id": "models/text-embedding-004"}) is None
    assert bc("openai", {"id": "gpt-5.5"}) and not bc("openai", {"id": "gpt-4o-mini-tts"})
    assert not bc("openai", {"id": "gpt-realtime"}) and not bc("openai", {"id": "dall-e-3"})
    assert bc("anthropic", {"id": "claude-sonnet-5-5", "display_name": "Claude Sonnet 5.5"})["name"] == "Claude Sonnet 5.5"


def main() -> int:
    for name, fn in list(globals().items()):
        if name.startswith("check_"):
            fn()
    print("domain OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
