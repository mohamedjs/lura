"""Lura's command line. ``lura`` with no command runs the assistant."""

from __future__ import annotations

import argparse
import getpass
import json
import logging
import signal
import sys
import threading
import time

from . import __version__

log = logging.getLogger("lura")

#: EX_CONFIG: a human must fix something. The service units do not restart on it.
EXIT_NEEDS_SETUP = 78


def _logging(verbose: bool) -> None:
    """Console when there is one; always a rotating file, for support.

    A windowed build (LuraApp on Windows/macOS) has no stderr at all, and the
    log file is the only record of why something went wrong.
    """
    from logging.handlers import RotatingFileHandler

    from .shared import paths

    handlers: list[logging.Handler] = []
    if sys.stderr is not None:
        handlers.append(logging.StreamHandler())
    try:
        paths.data_dir().mkdir(parents=True, exist_ok=True)
        handlers.append(RotatingFileHandler(paths.data_dir() / "lura.log", maxBytes=1_000_000,
                                            backupCount=2, encoding="utf-8"))
    except OSError:
        pass
    logging.basicConfig(level=logging.DEBUG if verbose else logging.INFO, handlers=handlers,
                        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s", datefmt="%H:%M:%S")


def _overlay_labels(lang: str, assistant_name: str = "") -> dict:
    from .shared.i18n import t
    labels = {k: t(f"tray.{k}", lang) for k in ("open", "talk", "subtitles", "quit")}
    # The caption says whatever the user named her, not "Lura".
    labels.update(you=t("caption.you", lang), lura=assistant_name.strip() or t("caption.lura", lang))
    return labels


def _container(presence=None):
    from .bootstrap import build
    return build(presence)


# ── run: the daemon ─────────────────────────────────────────────────────────

def cmd_run(args) -> int:
    from .modules.dashboard import DashboardApi, DashboardServer, running_url
    from .modules.overlay import Overlay
    from .shared.i18n import t

    if url := running_url():
        log.info("Lura is already running; opening its dashboard.")
        from .modules.dashboard import open_window
        open_window(url)
        return 0

    c = _container()
    s = c.settings.current
    stop = threading.Event()
    server = DashboardServer(DashboardApi(c.settings, c.brain, c.voice, c.connectors,
                                          c.bus, lambda: c.assistant.status, c.memory, c.skills), s.dashboard_port)
    try:
        server.start()
    except OSError as exc:
        log.error("Dashboard port %d is busy (%s). Change dashboard_port with `lura config`.",
                  s.dashboard_port, exc)
        return 1

    from .shared import events

    def quit_from_tray():
        log.info("Quit from the tray menu.")
        stop.set()

    def toggle_subtitles():
        c.settings.update({"subtitles": not c.settings.current.subtitles})

    overlay = Overlay(enabled=s.overlay_enabled and not args.no_overlay,
                      actions={"open": server.open, "talk": lambda: c.bus.publish(events.TALK_REQUESTED),
                               "subtitles": toggle_subtitles, "quit": quit_from_tray},
                      labels=_overlay_labels(s.ui_language, s.assistant_name))
    c.assistant.presence = overlay.state
    overlay.state.subtitles_enabled = s.subtitles

    def on_settings(payload):
        # Applied live, no restart: the subtitles switch.
        if "subtitles" in payload.get("changed", ()):
            overlay.state.subtitles_enabled = c.settings.current.subtitles

    c.bus.subscribe(events.SETTINGS_CHANGED, on_settings)

    def on_signal(signum, _frame):
        log.info("Signal %s — shutting down.", signum)
        stop.set()

    signal.signal(signal.SIGINT, on_signal)
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, on_signal)

    restart = threading.Event()

    def on_restart(_payload):
        log.info("Restart requested from the dashboard.")
        restart.set()
        stop.set()

    c.bus.subscribe(events.RESTART_REQUESTED, on_restart)
    c.connectors.start_all()

    # First run, or nothing works yet: the dashboard is where that gets fixed.
    if not s.onboarded or not c.settings.capabilities().can_talk:
        if not args.no_browser:
            server.open()
        if not s.onboarded:
            c.settings.update({"onboarded": True})
            from .shared.desktop import apply_autostart
            if s.start_at_login:
                apply_autostart(True)

    log.info("Lura %s — say %r. Dashboard: lura dashboard", __version__, s.wake_word)
    failed = threading.Event()

    def assistant_loop():
        # If the assistant dies, the face must not stay up pretending to
        # listen: stop with a failure code so systemd starts a fresh Lura.
        try:
            c.assistant.run_forever(stop)
        except Exception:
            log.exception("The assistant stopped unexpectedly.")
            failed.set()
        if not stop.is_set():
            log.error("The assistant loop ended on its own; restarting.")
            failed.set()
            stop.set()

    worker = threading.Thread(target=assistant_loop, daemon=True, name="assistant")
    worker.start()
    try:
        overlay.run(stop)        # owns the main thread (Qt needs it)
        if not stop.is_set():
            log.error("The face's window loop ended on its own; restarting.")
            failed.set()
    finally:
        stop.set()
        c.assistant.interrupt()
        worker.join(timeout=5)
        c.connectors.stop_all()
        server.stop()
    if restart.is_set():
        return _restart_self()
    if failed.is_set():
        return 1   # Restart=on-failure brings it back; exit 0 would leave it dead
    log.info("Stopped.")
    return 0


#: EX_TEMPFAIL. Under systemd the unit's Restart=on-failure brings Lura back.
EXIT_RESTART = 75


def _restart_self() -> int:
    """Start again with fresh settings, however Lura was started."""
    import os
    import subprocess

    from .shared.desktop import launch_command
    from .shared.platform import IS_WINDOWS

    if os.environ.get("INVOCATION_ID"):   # running as a systemd service
        log.info("Restarting through systemd.")
        return EXIT_RESTART
    argv = [*launch_command(), "--no-browser"]
    log.info("Restarting: %s", " ".join(argv))
    if IS_WINDOWS:
        # No exec on Windows: start the new Lura detached, then leave.
        subprocess.Popen(argv, creationflags=0x00000008 | 0x00000200, close_fds=True)
        return 0
    logging.shutdown()
    os.execv(argv[0], argv)
    return 0   # not reached


# ── dashboard ───────────────────────────────────────────────────────────────

def cmd_dashboard(args) -> int:
    """The settings window. Opens the running Lura's, or serves one just for setup."""
    from .modules.dashboard import DashboardApi, DashboardServer, run_window, running_url

    # The window runs in a child process: if it crashes (GPU driver, missing
    # library), this one is still here to open the page in a browser.
    if url := running_url():
        run_window(url)
        return 0
    c = _container()
    server = DashboardServer(DashboardApi(c.settings, c.brain, c.voice, c.connectors,
                                          c.bus, lambda: "offline", c.memory, c.skills, can_restart=False),
                             c.settings.current.dashboard_port)
    server.start()
    log.info("Lura is not running; this dashboard is standalone until its window closes.")
    try:
        if not run_window(server.url):
            # The page went to a browser tab, which needs this server alive.
            print(f"The dashboard is open in your browser: {server.url}\nPress Ctrl+C to stop it.")
            threading.Event().wait()
    except KeyboardInterrupt:
        pass
    finally:
        server.stop()
    return 0


def cmd_window(args) -> int:
    """Internal: the dashboard window process the daemon starts."""
    from .modules.dashboard import show_window

    show_window(args.url)
    return 0


# ── keys & settings ─────────────────────────────────────────────────────────

def cmd_login(args) -> int:
    from .modules.settings import PROVIDER_INFO
    from .shared.http import HttpError

    c = _container()
    provider = args.provider
    info = PROVIDER_INFO[provider]
    print(f"{info['label']} key — get one at {info['key_url']}")
    if provider == "gemini":
        print("(Gemini keys created before September 2026 are rejected: make a new one.)")
    key = getpass.getpass("Paste it (hidden), then Enter: ").strip()
    if not key:
        print("Nothing pasted — no change.", file=sys.stderr)
        return 1
    try:
        print(c.brain.check_key(provider, key))
    except HttpError as exc:
        print(f"That key did not work: {exc}", file=sys.stderr)
        return 1
    c.settings.set_key(provider, key)
    print(f"Saved to {c.settings.keys_path} (readable only by you).")
    return 0


def cmd_logout(args) -> int:
    c = _container()
    print(f"Removed the {args.provider} key." if c.settings.remove_key(args.provider)
          else f"No {args.provider} key was stored.")
    return 0


def cmd_config(args) -> int:
    c = _container()
    s = c.settings.current
    if not args.name:
        print(f"# {c.settings.settings_path}\n")
        for name, value in s.to_dict().items():
            print(f"  {name:26} {value!r}")
        caps = c.settings.capabilities()
        print(f"\nkeys: {', '.join(sorted(caps.keys)) or 'none'}   brain: {s.brain_provider}/{s.brain_model}"
              f"   voice: {caps.voice_provider or 'none'}")
        return 0
    if args.value is None:
        print(json.dumps(s.to_dict().get(args.name, f"(no setting {args.name})"), ensure_ascii=False))
        return 0
    value = args.value
    if args.name == "models":
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            print('models takes JSON, e.g. {"anthropic": "claude-sonnet-5-5"}', file=sys.stderr)
            return 1
    try:
        changed = c.settings.update({args.name: value})
    except (KeyError, ValueError) as exc:
        print(f"{exc}\nRun `lura config` to see them all.", file=sys.stderr)
        return 1
    print(f"{args.name} = {c.settings.current.to_dict()[args.name]!r}" if changed else "No change.")
    return 0


def cmd_models(args) -> int:
    c = _container()
    provider = args.provider or c.settings.current.brain_provider
    needle = (args.search or "").lower()
    rows = [m for m in c.brain.list_models(provider) if needle in m.lower()]
    for model in rows:
        print(model)
    print(f"\n{len(rows)} model(s). Use one:  lura config models '{{\"{provider}\": \"<id>\"}}'")
    return 0 if rows else 1


# ── talking ─────────────────────────────────────────────────────────────────

def _talk(initial: bool) -> int:
    from .modules.overlay import Overlay
    from .shared.errors import NeedsSetup

    from .modules.settings import SettingsService
    lang = SettingsService().current
    overlay = Overlay(enabled=True, labels=_overlay_labels(lang.ui_language))
    overlay.state.subtitles_enabled = lang.subtitles
    c = _container(overlay.state)
    c.connectors.start_all()
    stop = threading.Event()
    result = [0]

    def work():
        try:
            if initial:
                c.assistant.briefing()
            else:
                c.assistant.converse()
        except NeedsSetup as exc:
            log.error("%s", exc)
            result[0] = EXIT_NEEDS_SETUP
        finally:
            stop.set()

    signal.signal(signal.SIGINT, lambda *_: (c.assistant.interrupt(), stop.set()))
    threading.Thread(target=work, daemon=True).start()
    overlay.run(stop)
    c.connectors.stop_all()
    return result[0]


def cmd_say(args) -> int:
    return _talk(initial=False)


def cmd_briefing(args) -> int:
    return _talk(initial=True)


# ── connectors, autostart ─────────────────────────────────────────────

def cmd_mcp(args) -> int:
    c = _container()
    try:
        if args.action == "add-url":
            c.connectors.add_url(args.name, args.target, getpass.getpass("Token (Enter for none): "))
        elif args.action == "import":
            with open(args.target, encoding="utf-8") as handle:
                print("Added:", ", ".join(c.connectors.import_json(handle.read())))
        elif args.action == "remove":
            print("Removed." if c.connectors.remove(args.name) else "No such connector.")
        elif args.action in ("on", "off"):
            c.connectors.set_enabled(args.name, args.action == "on")
        elif args.action == "path":
            print(c.connectors.config_path)
            return 0
    except (ValueError, KeyError, OSError) as exc:
        print(exc, file=sys.stderr)
        return 1
    if args.action != "list":
        return 0
    c.connectors.start_all()
    print("Starting connectors (a first npx download can take a minute)…")
    deadline = time.monotonic() + 90
    while time.monotonic() < deadline and any(x["status"] == "starting" for x in c.connectors.list()):
        time.sleep(0.5)
    for x in c.connectors.list():
        print(f"  {x['name']:12} {x['status']:9} {len(x['tools']):3} tools  "
              f"{'(built-in) ' if x['builtin'] else ''}{x['error']}")
    c.connectors.stop_all()
    return 0


def cmd_autostart(args) -> int:
    from .shared.desktop import set_autostart

    c = _container()
    c.settings.update({"start_at_login": args.state == "on"})
    print(f"Start at login: {args.state} ({set_autostart(args.state == 'on')})")
    return 0


def cmd_selftest(args) -> int:
    from .selftest import run
    return run(_container())


def _utf8_console() -> None:
    """Windows consoles default to a legacy code page that cannot print Arabic
    (or "──"), and a print would raise mid-command. Reconfigure, never crash."""
    for stream in (sys.stdout, sys.stderr):
        if stream is not None and hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(encoding="utf-8", errors="replace")
            except (OSError, ValueError):
                pass


def main(argv=None) -> int:
    _utf8_console()
    parser = argparse.ArgumentParser(prog="lura", description="Lura — a voice-first desktop assistant.")
    parser.add_argument("-v", "--verbose", action="store_true")
    parser.add_argument("--version", action="version", version=f"lura {__version__}")
    subs = parser.add_subparsers(dest="command")

    run = subs.add_parser("run", help="run the assistant (the default)")
    run.add_argument("--no-overlay", action="store_true", help="no face on screen")
    run.add_argument("--no-browser", action="store_true", help="never open the dashboard by itself")
    subs.add_parser("dashboard", help="open the settings window")
    win = subs.add_parser("window")   # internal: the daemon's dashboard window
    win.add_argument("url")
    from .modules.settings import PROVIDERS
    for name, helptext in (("login", "store and test an API key"), ("logout", "forget an API key")):
        p = subs.add_parser(name, help=helptext)
        p.add_argument("--provider", choices=PROVIDERS, required=True)
    conf = subs.add_parser("config", help="show settings, or change one")
    conf.add_argument("name", nargs="?")
    conf.add_argument("value", nargs="?")
    models = subs.add_parser("models", help="list a provider's models")
    models.add_argument("provider", nargs="?", choices=PROVIDERS)
    models.add_argument("search", nargs="?")
    subs.add_parser("selftest", help="check audio, keys, models and voice end to end")
    subs.add_parser("say", help="talk now, skipping the wake word")
    subs.add_parser("briefing", help="speak the system briefing now")
    mcp = subs.add_parser("mcp", help="connectors: list, add-url, import, remove, on, off, path")
    mcp.add_argument("action", nargs="?", default="list",
                     choices=["list", "add-url", "import", "remove", "on", "off", "path"])
    mcp.add_argument("name", nargs="?", help="connector name (or the mcp.json path for import)")
    mcp.add_argument("target", nargs="?", help="URL for add-url")
    auto = subs.add_parser("autostart", help="start Lura at login: on or off")
    auto.add_argument("state", choices=["on", "off"])

    args = parser.parse_args(argv)
    if args.command == "mcp" and args.action == "import":
        args.target = args.name
    _logging(args.verbose)
    if args.command is None:
        args = parser.parse_args([*(["-v"] if args.verbose else []), "run"])
    handlers = {
        "run": cmd_run, "dashboard": cmd_dashboard, "window": cmd_window, "login": cmd_login, "logout": cmd_logout,
        "config": cmd_config, "models": cmd_models, "selftest": cmd_selftest, "say": cmd_say,
        "briefing": cmd_briefing, "mcp": cmd_mcp, "autostart": cmd_autostart,
    }
    return handlers[args.command](args)


if __name__ == "__main__":
    sys.exit(main())
