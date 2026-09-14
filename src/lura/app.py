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


_active_conv = None


def _converse(settings: Settings, overlay_state=None, mcp_manager=None,
              initial_prompt: str | None = None) -> None:
    """One exchange, on whichever provider is configured."""
    global _active_conv
    if settings.provider == "gemini":
        from .live import Conversation

        conv = Conversation(settings, _gemini_client(),
                            overlay_state=overlay_state,
                            mcp_manager=mcp_manager,
                            initial_prompt=initial_prompt)
        _active_conv = conv
        try:
            asyncio.run(conv.run())
        finally:
            _active_conv = None
    else:
        from .openrouter import Conversation as ORConversation

        ORConversation(settings, load_key("openrouter")).run()


# ── commands ────────────────────────────────────────────────────────────────
def cmd_login(args) -> int:
    """Store one provider's API key."""
    provider = args.provider or Settings.load().provider
    if provider == "gemini":
        print(f"Gemini Live needs an API key — sign-in alone cannot reach it.\n\n"
              f"  1. Open {WHERE_TO_GET['gemini']}\n"
              f"  2. Sign in with your Google account\n"
              f"  3. Create a NEW key (keys before Sep 2026 are rejected).\n")
    else:
        print(f"OpenRouter key.\n\n  1. Open {WHERE_TO_GET['openrouter']}\n  2. Create a key (sk-or-…)\n")

    key = getpass.getpass(f"Paste your {provider} key (hidden), then Enter: ").strip()
    if not key:
        print("Nothing pasted — no change.", file=sys.stderr)
        return 1

    save_key(provider, key)
    print(f"\nSaved to {KEYS_FILE} (readable only by you).\nCheck it with:  lura selftest")
    return 0


def cmd_logout(args) -> int:
    """Forget one provider's key."""
    p = args.provider or Settings.load().provider
    print(f"Removed stored {p} key." if forget_key(p) else f"No {p} key was stored.")
    return 0


def cmd_config(args) -> int:
    """Show settings, or change one."""
    settings = Settings.load()
    if not args.name:
        print(f"# {SETTINGS_FILE}\n")
        for f in fields(settings):
            marker = " *" if f.name in ("provider", "gemini_model", "openrouter_model") else "  "
            print(f"{marker} {f.name:26} {getattr(settings, f.name)!r}")
        print(f"\n* the ones you are most likely to change\nkeys stored for: {', '.join(stored_providers()) or 'none'}\nactive provider: {settings.provider}  ->  model {settings.model!r}")
        return 0

    if args.value is None:
        print(getattr(settings, args.name, f"(no such setting: {args.name})"))
        return 0

    try:
        applied = settings.set(args.name, args.value)
    except (KeyError, ValueError) as exc:
        print(f"{exc}\nRun `lura config` to see them all.", file=sys.stderr)
        return 1

    if args.name == "openrouter_model":
        from .openrouter import model_exists
        ok, near = model_exists(applied)
        if not ok:
            print(f"Warning: no model {applied!r} on OpenRouter.", file=sys.stderr)
            if near:
                print(f"Did you mean: {', '.join(near)}", file=sys.stderr)
            print("Saving anyway — fix it with `lura config openrouter_model <id>`.", file=sys.stderr)

    settings.save()
    print(f"{args.name} = {applied!r}\nRestart to apply:  systemctl --user restart lura")
    return 0


def cmd_models(args) -> int:
    """List OpenRouter models, optionally filtered."""
    from .openrouter import list_models
    needle = (args.search or "").lower()
    rows = [
        (m["id"], ",".join((m.get("architecture") or {}).get("input_modalities") or []),
         ",".join((m.get("architecture") or {}).get("output_modalities") or []))
        for m in list_models() if not needle or needle in m["id"].lower()
    ]
    if not rows:
        print(f"Nothing matching {args.search!r}.")
        return 1
    for mid, takes, gives in sorted(rows):
        print(f"{mid:58} in:{takes:28} out:{gives}")
    print(f"\n{len(rows)} model(s).\nUse one:  lura config openrouter_model <id>")
    return 0


def cmd_selftest(args) -> int:
    """Answer 'why isn't it working' for every layer, in order."""
    import sounddevice as sd
    from .wake import chime, rms_of

    settings = Settings.load()
    failures = 0
    print(f"lura {__version__}\nprovider: {settings.provider}   model: {settings.model}\n\n── audio devices ──")
    try:
        print(sd.query_devices())
        din, dout = sd.default.device
        print(f"\ndefault input : {din}\ndefault output: {dout}")
    except Exception as exc:
        print(f"FAIL: cannot list audio devices: {exc}")
        return 1

    print("\n── microphone (3s) ──\nSay something…")
    try:
        rec = sd.rec(int(3 * INPUT_RATE), samplerate=INPUT_RATE, channels=1, dtype="int16", device=settings.input_device)
        sd.wait()
        level = rms_of(rec.tobytes())
        print(f"level: {level:.4f} {'#' * min(40, int(level * 200))}")
        if level < 0.001:
            print("FAIL: silence. Mic muted or wrong device.\n      Pick one above: lura config input_device <n>")
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
            print(f"FAIL: {type(exc).__name__}: {exc}\n\n"
                  f"If this says the model was not found, the preview id has been retired.\n"
                  f"  lura config gemini_model <id>")
            failures += 1
    else:
        from .openrouter import OpenRouterError, chat, check_key, model_exists
        print("\n── openrouter key ──")
        try:
            info = check_key(key)
            limit, usage = info.get("limit"), info.get("usage")
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
            print(f"FAIL: no such model. Close matches: {', '.join(near) or 'none'}\n"
                  f"  lura config openrouter_model <id>")
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
            print(f"FAIL: {type(exc).__name__}: {exc}\n  lura config openrouter_tts_model <id>")
            failures += 1

    print("\n" + ("All good." if not failures else f"{failures} problem(s) above."))
    return 1 if failures else 0


def _start_overlay():
    try:
        from .overlay import OverlayState, start_overlay
        o = OverlayState()
        start_overlay(o)
        return o
    except Exception as exc:
        log.debug("Overlay not started: %s", exc)
        return None


def _start_mcp():
    try:
        from .mcp_client import MCP_CONFIG, MCPManager
        if MCP_CONFIG.exists():
            m = MCPManager(MCP_CONFIG)
            m.start_all()
            if m.servers:
                log.info("Started %d MCP server(s)", len(m.servers))
            return m
    except Exception as exc:
        log.warning("MCP start failed: %s", exc)
    return None


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
        if _active_conv:
            _active_conv.stop()

    signal.signal(signal.SIGTERM, on_signal)
    signal.signal(signal.SIGINT, on_signal)

    log.info("Ready. Say %r.  [%s / %s]", settings.wake_word,
             settings.provider, settings.model)

    overlay = _start_overlay()
    mcp = _start_mcp()

    from .overlay import State
    try:
        from .briefing import (
            build_briefing_prompt,
            gather_briefing,
            mark_briefing_done,
            should_run_startup_briefing,
        )
        if should_run_startup_briefing():
            log.info("Triggering initial boot briefing...")
            mark_briefing_done()
            b_data = gather_briefing(mcp)
            prompt = build_briefing_prompt(b_data)
            if overlay:
                overlay.state = State.SPEAKING
            _converse(settings, overlay_state=overlay, mcp_manager=mcp, initial_prompt=prompt)
    except Exception as exc:
        log.warning("Boot briefing failed: %s", exc)

    try:
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

                _converse(settings, overlay_state=overlay, mcp_manager=mcp)
                if overlay:
                    overlay.state = State.IDLE
                chime(rate=OUTPUT_RATE, device=settings.output_device, up=False)
                listener.cooldown()

            except Exception as exc:
                log.exception("Conversation failed: %s", exc)
                time.sleep(2)
    finally:
        if mcp:
            mcp.stop_all()

    log.info("Stopped.")
    return 0


def cmd_briefing(args) -> int:
    """Trigger a voice system & weather briefing immediately."""
    settings = Settings.load()
    try:
        load_key(settings.provider)
    except MissingKeyError as exc:
        log.error("%s", exc)
        return EXIT_NEEDS_SETUP

    overlay = _start_overlay()
    if overlay:
        from .overlay import State
        overlay.state = State.SPEAKING
    mcp = _start_mcp()

    try:
        from .briefing import build_briefing_prompt, gather_briefing
        log.info("Gathering briefing data...")
        b_data = gather_briefing(mcp)
        prompt = build_briefing_prompt(b_data)
        _converse(settings, overlay_state=overlay, mcp_manager=mcp, initial_prompt=prompt)
    finally:
        if mcp:
            mcp.stop_all()
    return 0


def cmd_say(args) -> int:
    """Start a conversation now, without the wake word."""
    settings = Settings.load()
    try:
        load_key(settings.provider)
    except MissingKeyError as exc:
        log.error("%s", exc)
        return EXIT_NEEDS_SETUP

    overlay = _start_overlay()
    if overlay:
        from .overlay import State
        overlay.state = State.LISTENING
    mcp = _start_mcp()

    log.info("Talking to %s (%s). Speak.", settings.provider, settings.model)
    try:
        _converse(settings, overlay_state=overlay, mcp_manager=mcp)
    finally:
        if mcp:
            mcp.stop_all()
    return 0


def cmd_mcp(args) -> int:
    """Show or inspect MCP servers configuration."""
    from .mcp_client import MCP_CONFIG, MCPManager

    if getattr(args, "action", None) == "path":
        print(MCP_CONFIG)
        return 0

    print(f"MCP config: {MCP_CONFIG}\n")
    mgr = MCPManager(MCP_CONFIG)
    servers = mgr.load_config().get("mcpServers", {})
    if not servers:
        print(f"No MCP servers configured yet. Edit {MCP_CONFIG} to add servers.")
        return 0

    for n, c in servers.items():
        print(f"  • {n}: {c.get('command', '')} {' '.join(c.get('args', []))}")
    mgr.start_all()
    tools = mgr.get_gemini_tools()
    print(f"\nAvailable tools ({len(tools)}):")
    for t in tools:
        print(f"  ✓ {t.name}: {t.description}")
    mgr.stop_all()
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="lura", description=__doc__)
    parser.add_argument("-v", "--verbose", action="store_true")
    subs = parser.add_subparsers(dest="command")

    subs.add_parser("run", help="wait for the wake word (this is what the service runs)")
    login = subs.add_parser("login", help="store an API key")
    login.add_argument("--provider", choices=PROVIDERS, help="default: active provider")
    logout = subs.add_parser("logout", help="forget a stored API key")
    logout.add_argument("--provider", choices=PROVIDERS)

    conf = subs.add_parser("config", help="show settings, or change one")
    conf.add_argument("name", nargs="?", help="setting to show or change")
    conf.add_argument("value", nargs="?", help="new value")

    models = subs.add_parser("models", help="list OpenRouter models")
    models.add_argument("search", nargs="?", help="filter, e.g. 'claude' or 'gemini'")

    subs.add_parser("selftest", help="check audio, key and model end to end")
    subs.add_parser("say", help="talk now, skipping the wake word")
    subs.add_parser("briefing", help="speak system & weather briefing now")

    mcp_p = subs.add_parser("mcp", help="list MCP tools or show config path")
    mcp_p.add_argument("action", nargs="?", choices=["list", "path"], default="list")

    args = parser.parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(message)s",
        datefmt="%H:%M:%S",
    )
    handlers = {
        "run": cmd_run, "login": cmd_login, "logout": cmd_logout,
        "config": cmd_config, "models": cmd_models, "selftest": cmd_selftest,
        "say": cmd_say, "briefing": cmd_briefing, "mcp": cmd_mcp,
    }
    return handlers.get(args.command or "run")(args)


if __name__ == "__main__":
    sys.exit(main())
