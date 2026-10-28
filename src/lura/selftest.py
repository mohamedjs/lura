"""``lura selftest``: answer "why isn't it working" for every layer, in order.

Surfaces API errors verbatim. A retired preview model must read as "model
not found", not as a broken app.
"""

from __future__ import annotations

import shutil

from . import __version__
from .shared.i18n import t


def run(c) -> int:
    from .modules.brain import Done, Message, TextDelta

    s = c.settings.current
    caps = c.settings.capabilities()
    failures = 0

    def ok(msg: str) -> None:
        print(f"OK   {msg}")

    def bad(msg: str) -> None:
        nonlocal failures
        failures += 1
        print(f"FAIL {msg}")

    print(f"lura {__version__} — mode {s.mode}, brain {s.brain_provider}/{s.brain_model}, "
          f"voice {caps.voice_provider or 'none'} ({s.voice_name})\n")

    print("── audio ──")
    try:
        import sounddevice as sd
        print(sd.query_devices())
        print("Say something (3 s)…")
        mic = c.voice.mic_test(s.input_device)
        if "error" in mic:
            bad(f"microphone: {mic['error']}")
        else:
            (ok if mic["ok"] else bad)(f"microphone peak {mic['peak']:.4f}, room {mic['floor']:.4f}, {mic['rate']} Hz"
                                       + ("" if mic["ok"] else " — muted or wrong device (pick it in the dashboard)"))
        c.voice.chime(s)
        ok("speaker (you should have heard two tones)")
    except Exception as exc:
        bad(f"audio: {exc}")

    print("\n── wake word ──")
    try:
        ok(f"model at {c.voice.ensure_wake_model()}")
    except Exception as exc:
        bad(f"wake-word model: {exc}")

    print("\n── keys ──")
    for problem in caps.problems:
        bad(t(problem, "en"))
    for provider, status in c.settings.key_status().items():
        if status["set"]:
            try:
                ok(f"{provider}: {c.brain.check_key(provider, c.settings.key(provider))}")
            except Exception as exc:
                bad(f"{provider}: {exc}")
    if caps.problems:
        print(f"\n{failures} problem(s). Fix them in the dashboard: lura dashboard")
        return 1

    if s.mode == "realtime":
        print(f"\n── gemini live ({s.realtime_model}) ──")
        try:
            ok(f"replied {c.voice.probe_realtime(s)!r}")
        except Exception as exc:
            bad(f"{type(exc).__name__}: {exc} — if the model was not found, its preview id was retired: "
                "lura config realtime_model <id>")
    else:
        print(f"\n── brain ({s.brain_provider}/{s.brain_model}) ──")
        try:
            model = c.brain.model(s)
            text = ""
            for event in model.stream("Reply with exactly the word: hello", [Message("user", "Say it.")], []):
                if isinstance(event, TextDelta):
                    text += event.text
                elif isinstance(event, Done):
                    text = event.message.text or text
            ok(f"streamed {text.strip()[:60]!r}")
        except Exception as exc:
            bad(f"{type(exc).__name__}: {exc}")

        print(f"\n── voice ({caps.voice_provider}) ──")
        try:
            pcm = c.voice.synthesizer(s).synthesize("Test.")
            (ok if pcm else bad)(f"{len(pcm)} bytes of speech")
        except Exception as exc:
            bad(f"speech: {exc}")

    print("\n── connectors ──")
    if shutil.which("npx") or shutil.which("npx.cmd"):
        ok("npx found (built-in connectors can start)")
    else:
        bad("npx not found — install Node.js for the Chrome, web and files connectors")

    print("\n" + ("All good." if not failures else f"{failures} problem(s) above."))
    return 1 if failures else 0
