"""Entry point: `run` (the daemon), `login`, `selftest`, `say`."""

from __future__ import annotations

import argparse
import asyncio
import getpass
import logging
import signal
import sys
import threading
import time
from dataclasses import fields

from . import __version__
from .config import (
    INPUT_RATE,
    KEYS_FILE,
    OUTPUT_RATE,
    PROVIDERS,
    SETTINGS_FILE,
    VOSK_MODEL_DIR,
    WHERE_TO_GET,
    MissingKeyError,
    Settings,
    forget_key,
    load_key,
    save_key,
    stored_providers,
)

log = logging.getLogger("lura")

AI_STUDIO_KEYS = "https://aistudio.google.com/apikey"

#: EX_CONFIG. A missing key is not a crash to retry — it needs a human. The
#: service unit refuses to restart on this code, so a fresh install waits
#: quietly instead of respawning every five seconds forever.
EXIT_NEEDS_SETUP = 78


def _gemini_client():
    from google import genai

    return genai.Client(api_key=load_key("gemini"))


def _converse(settings: Settings, overlay_state=None) -> None:
    """One exchange, on whichever provider is configured."""
    if settings.provider == "gemini":
        from .live import Conversation

        asyncio.run(Conversation(settings, _gemini_client(),
                                 overlay_state=overlay_state).run())
    else:
        from .openrouter import Conversation as ORConversation

        ORConversation(settings, load_key("openrouter")).run()


# ── commands ────────────────────────────────────────────────────────────────
def cmd_login(args) -> int:
    """Store one provider's API key."""
    provider = args.provider or Settings.load().provider

    if provider == "gemini":
        print("Gemini Live needs an API key — a Google account sign-in on its")
        print("own cannot reach it. Asked once, then never again.\n")
        print(f"  1. Open {WHERE_TO_GET['gemini']}")
        print("  2. Sign in with your Google account")
        print("  3. Create a NEW key. Keys made before September 2026 are the")
        print("     old 'standard' type and are now rejected.\n")
    else:
        print("OpenRouter key.\n")
        print(f"  1. Open {WHERE_TO_GET['openrouter']}")
        print("  2. Create a key (it starts with sk-or-)\n")

    key = getpass.getpass(f"Paste your {provider} key (hidden), then Enter: ").strip()
    if not key:
        print("Nothing pasted — no change.", file=sys.stderr)
        return 1

    save_key(provider, key)
    print(f"\nSaved to {KEYS_FILE} (readable only by you).")
    print(f"Check it with:  lura selftest")
    return 0


def cmd_logout(args) -> int:
    """Forget one provider's key."""
    provider = args.provider or Settings.load().provider
    if forget_key(provider):
        print(f"Removed the stored {provider} key.")
    else:
        print(f"No {provider} key was stored.")
    return 0


def cmd_config(args) -> int:
    """Show settings, or change one."""
    settings = Settings.load()

    if not args.name:
        print(f"# {SETTINGS_FILE}\n")
        for field in fields(settings):
            marker = " *" if field.name in ("provider", "gemini_model", "openrouter_model") else "  "
            print(f"{marker} {field.name:26} {getattr(settings, field.name)!r}")
        print(f"\n* the ones you are most likely to change")
        print(f"\nkeys stored for: {', '.join(stored_providers()) or 'none'}")
        print(f"active provider: {settings.provider}  ->  model {settings.model!r}")
        return 0

    if args.value is None:
        print(getattr(settings, args.name, f"(no such setting: {args.name})"))
        return 0

    try:
        applied = settings.set(args.name, args.value)
    except KeyError:
        print(f"No such setting: {args.name}", file=sys.stderr)
        print("Run `lura config` to see them all.", file=sys.stderr)
        return 1
    except ValueError as exc:
        print(f"{exc}", file=sys.stderr)
        return 1

    # Catch a mistyped model now rather than at the next wake word.
    if args.name == "openrouter_model":
        from .openrouter import model_exists

        ok, near = model_exists(applied)
        if not ok:
            print(f"Warning: no model {applied!r} on OpenRouter.", file=sys.stderr)
            if near:
                print("Did you mean:", file=sys.stderr)
                for candidate in near:
                    print(f"  {candidate}", file=sys.stderr)
            print("Saving anyway — fix it with `lura config openrouter_model <id>`.",
                  file=sys.stderr)

    settings.save()
    print(f"{args.name} = {applied!r}")
    print("Restart to apply:  systemctl --user restart lura")
    return 0


def cmd_models(args) -> int:
    """List OpenRouter models, optionally filtered."""
    from .openrouter import list_models

    needle = (args.search or "").lower()
    rows = []
    for model in list_models():
        if needle and needle not in model["id"].lower():
            continue
        arch = model.get("architecture", {}) or {}
        rows.append((
            model["id"],
            ",".join(arch.get("input_modalities") or []),
            ",".join(arch.get("output_modalities") or []),
        ))

    if not rows:
        print(f"Nothing matching {args.search!r}.")
        return 1

    for model_id, takes, gives in sorted(rows):
        print(f"{model_id:58} in:{takes:28} out:{gives}")
    print(f"\n{len(rows)} model(s).")
    print("Use one:  lura config openrouter_model <id>")
    return 0


def cmd_selftest(args) -> int:
    """Answer 'why isn't it working' for every layer, in order."""
    import sounddevice as sd

    from .wake import chime, rms_of

    settings = Settings.load()
    failures = 0

    print(f"lura {__version__}")
    print(f"provider: {settings.provider}   model: {settings.model}\n")

    print("── audio devices ──")
    try:
        print(sd.query_devices())
        default_in, default_out = sd.default.device
        print(f"\ndefault input : {default_in}")
        print(f"default output: {default_out}")
    except Exception as exc:
        print(f"FAIL: cannot list audio devices: {exc}")
        return 1

    print("\n── microphone (3s) ──")
    print("Say something…")
    try:
        recording = sd.rec(
            int(3 * INPUT_RATE),
            samplerate=INPUT_RATE,
            channels=1,
            dtype="int16",
            device=settings.input_device,
        )
        sd.wait()
        level = rms_of(recording.tobytes())
        bar = "#" * min(40, int(level * 200))
        print(f"level: {level:.4f} {bar}")
        if level < 0.001:
            print("FAIL: silence. The mic is muted, or the wrong device is default.")
            print("      Pick one above: lura config input_device <n>")
            failures += 1
        else:
            print("OK")
    except Exception as exc:
        print(f"FAIL: {exc}")
        failures += 1

    print("\n── speaker ──")
    try:
        chime(rate=OUTPUT_RATE, device=settings.output_device)
        print("OK (you should have heard two tones)")
    except Exception as exc:
        print(f"FAIL: {exc}")
        failures += 1

    print("\n── wake model (local, used whichever provider answers) ──")
    if VOSK_MODEL_DIR.exists():
        print(f"OK: {VOSK_MODEL_DIR}")
    else:
        print(f"FAIL: missing {VOSK_MODEL_DIR} — re-run install.sh")
        failures += 1

    print(f"\n── {settings.provider} key ──")
    try:
        key = load_key(settings.provider)
        print("OK: key found")
    except MissingKeyError as exc:
        print(f"FAIL: {exc}")
        return failures + 1

    if settings.provider == "gemini":
        print(f"\n── gemini live ({settings.gemini_model}) ──")
        try:
            from .live import probe

            reply = asyncio.run(probe(settings, _gemini_client()))
            print(f"OK: model replied {reply!r}")
        except Exception as exc:
            print(f"FAIL: {type(exc).__name__}: {exc}")
            print("\nIf this says the model was not found, the preview id has been")
            print("retired. Set a current one:")
            print("  lura config gemini_model <id>")
            failures += 1
    else:
        from .openrouter import OpenRouterError, chat, check_key, model_exists

        print("\n── openrouter key ──")
        try:
            info = check_key(key)
            limit = info.get("limit")
            usage = info.get("usage")
            print(f"OK: label={info.get('label')!r} usage={usage} limit={limit}")
            if limit is not None and usage is not None and usage >= limit:
                print("FAIL: this key is out of credit.")
                failures += 1
        except OpenRouterError as exc:
            print(f"FAIL: {exc}")
            failures += 1

        print(f"\n── model {settings.openrouter_model} ──")
        ok, near = model_exists(settings.openrouter_model)
        if ok:
            print("OK: model exists")
        else:
            print(f"FAIL: no such model. Close matches: {', '.join(near) or 'none'}")
            print("  lura config openrouter_model <id>")
            failures += 1

        print("\n── chat round-trip ──")
        try:
            reply = chat(settings, key, [{"role": "user", "content": "Reply with exactly: hello"}])
            print(f"OK: model replied {reply[:80]!r}")
        except Exception as exc:
            print(f"FAIL: {type(exc).__name__}: {exc}")
            failures += 1

        print(f"\n── speech ({settings.openrouter_tts_model}) ──")
        try:
            from .openrouter import speak

            audio = speak(settings, key, "Test.")
            print(f"OK: {len(audio)} bytes of audio")
        except Exception as exc:
            # TTS model ids are not in /models, so this call is the only check.
            print(f"FAIL: {type(exc).__name__}: {exc}")
            print("  lura config openrouter_tts_model <id>")
            failures += 1

    print("\n" + ("All good." if not failures else f"{failures} problem(s) above."))
    return 1 if failures else 0


def cmd_run(args) -> int:
    """The daemon: wait for the wake word, converse, repeat."""
    settings = Settings.load()

    from .wake import WakeListener, chime

    try:
        load_key(settings.provider)
    except MissingKeyError as exc:
        log.error("%s", exc)
        return EXIT_NEEDS_SETUP

    try:
        listener = WakeListener(settings, VOSK_MODEL_DIR)
    except FileNotFoundError as exc:
        log.error("%s", exc)
        return EXIT_NEEDS_SETUP
    stop = threading.Event()

    def on_signal(signum, _frame):
        log.info("Signal %s — shutting down.", signum)
        stop.set()

    signal.signal(signal.SIGTERM, on_signal)
    signal.signal(signal.SIGINT, on_signal)

    log.info("Ready. Say %r.  [%s / %s]", settings.wake_word,
             settings.provider, settings.model)

    overlay = None
    try:
        from .overlay import OverlayState, State, start_overlay

        overlay = OverlayState()
        start_overlay(overlay)
    except Exception as exc:
        log.debug("Overlay not started: %s", exc)

    while not stop.is_set():
        try:
            if overlay:
                overlay.state = State.IDLE
            if not listener.listen(stop):
                break

            log.info("Woken.")
            if overlay:
                overlay.state = State.LISTENING
            chime(rate=OUTPUT_RATE, device=settings.output_device, up=True)

            _converse(settings, overlay_state=overlay)
            if overlay:
                overlay.state = State.IDLE
            chime(rate=OUTPUT_RATE, device=settings.output_device, up=False)
            listener.cooldown()

        except Exception as exc:
            # One bad conversation must not take the daemon down: it is
            # supposed to still be there tomorrow.
            log.exception("Conversation failed: %s", exc)
            time.sleep(2)

    log.info("Stopped.")
    return 0


def cmd_say(args) -> int:
    """Start a conversation now, without the wake word."""
    settings = Settings.load()
    try:
        load_key(settings.provider)
    except MissingKeyError as exc:
        log.error("%s", exc)
        return EXIT_NEEDS_SETUP

    overlay = None
    try:
        from .overlay import OverlayState, State, start_overlay

        overlay = OverlayState()
        start_overlay(overlay)
        overlay.state = State.LISTENING
    except Exception:
        pass

    log.info("Talking to %s (%s). Speak.", settings.provider, settings.model)
    _converse(settings, overlay_state=overlay)
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="lura", description=__doc__)
    parser.add_argument("-v", "--verbose", action="store_true")
    subs = parser.add_subparsers(dest="command")

    subs.add_parser("run", help="wait for the wake word (this is what the service runs)")

    login = subs.add_parser("login", help="store an API key")
    login.add_argument("--provider", choices=PROVIDERS,
                       help="default: whichever provider is active")

    logout = subs.add_parser("logout", help="forget a stored API key")
    logout.add_argument("--provider", choices=PROVIDERS)

    conf = subs.add_parser("config", help="show settings, or change one")
    conf.add_argument("name", nargs="?", help="setting to show or change")
    conf.add_argument("value", nargs="?", help="new value")

    models = subs.add_parser("models", help="list OpenRouter models")
    models.add_argument("search", nargs="?", help="filter, e.g. 'claude' or 'gemini'")

    subs.add_parser("selftest", help="check audio, key and model end to end")
    subs.add_parser("say", help="talk now, skipping the wake word")

    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(message)s",
        datefmt="%H:%M:%S",
    )

    handlers = {
        "run": cmd_run,
        "login": cmd_login,
        "logout": cmd_logout,
        "config": cmd_config,
        "models": cmd_models,
        "selftest": cmd_selftest,
        "say": cmd_say,
    }
    handler = handlers.get(args.command or "run")
    return handler(args)


if __name__ == "__main__":
    sys.exit(main())
