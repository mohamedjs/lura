"""Floating HUD overlay — Iron Man-style status panel for Lura.

A semi-transparent dark panel with a glowing AI core, status text,
and recent transcript. Hovers at the right side of the screen across
all workspaces. State + transcript are shared via ``OverlayState``.

If GTK is unavailable the assistant continues headless.
"""

from __future__ import annotations

import enum
import logging
import math
import threading

log = logging.getLogger(__name__)


# ── shared state ────────────────────────────────────────────────────────────

class State(enum.Enum):
    IDLE = "idle"              # waiting for wake word
    LISTENING = "listening"    # session open, user speaking
    SPEAKING = "speaking"      # assistant is talking
    CONNECTING = "connecting"  # reconnecting between turns


_STATE_LABEL = {
    State.IDLE:       "STANDBY",
    State.LISTENING:  "LISTENING",
    State.SPEAKING:   "SPEAKING",
    State.CONNECTING: "CONNECTING…",
}


class OverlayState:
    """Thread-safe state + transcript, read by the overlay."""

    def __init__(self):
        self._state = State.IDLE
        self._transcript = ""
        self._lock = threading.Lock()

    @property
    def state(self) -> State:
        with self._lock:
            return self._state

    @state.setter
    def state(self, value: State | str):
        if isinstance(value, str):
            value = State(value)
        with self._lock:
            self._state = value

    @property
    def transcript(self) -> str:
        with self._lock:
            return self._transcript

    @transcript.setter
    def transcript(self, value: str):
        with self._lock:
            self._transcript = value[-200:] if len(value) > 200 else value


# ── dimensions ──────────────────────────────────────────────────────────────

HUD_W = 260
HUD_H = 180
MARGIN_X = 24
MARGIN_Y = 48   # clear GNOME top bar
ORB_SIZE = 56


# ── GTK HUD builder ────────────────────────────────────────────────────────

def _build_window(shared: OverlayState):
    import gi
    gi.require_version("Gdk", "3.0")
    gi.require_version("Gtk", "3.0")
    from gi.repository import Gdk, GLib, Gtk

    win = Gtk.Window(type=Gtk.WindowType.TOPLEVEL)
    win.set_title("Lura HUD")
    win.set_decorated(False)
    win.set_resizable(False)
    win.set_keep_above(True)
    win.stick()
    win.set_skip_taskbar_hint(True)
    win.set_skip_pager_hint(True)
    win.set_accept_focus(False)
    win.set_default_size(HUD_W, HUD_H)
    win.set_type_hint(Gdk.WindowTypeHint.DOCK)

    screen = win.get_screen()
    visual = screen.get_rgba_visual()
    if visual:
        win.set_visual(visual)
    win.set_app_paintable(True)

    # Position: right side, vertically centered-upper
    display = Gdk.Display.get_default()
    if display:
        monitor = display.get_primary_monitor()
        if not monitor and display.get_n_monitors() > 0:
            monitor = display.get_monitor(0)
        if monitor:
            geo = monitor.get_geometry()
            scale = monitor.get_scale_factor()
            x = geo.x + (geo.width // scale) - HUD_W - MARGIN_X
            y = geo.y + MARGIN_Y
            win.move(x, y)

    # Layout: vertical box
    vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
    vbox.get_style_context().add_class("hud-panel")

    # ── top section: orb + status ──
    top_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
    top_box.get_style_context().add_class("hud-top")

    orb = Gtk.Box()
    orb.set_size_request(ORB_SIZE, ORB_SIZE)
    orb.get_style_context().add_class("hud-orb")

    status_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)

    title_label = Gtk.Label(label="L U R A")
    title_label.set_halign(Gtk.Align.START)
    title_label.get_style_context().add_class("hud-title")

    status_label = Gtk.Label(label="STANDBY")
    status_label.set_halign(Gtk.Align.START)
    status_label.get_style_context().add_class("hud-status")

    status_box.pack_start(title_label, False, False, 0)
    status_box.pack_start(status_label, False, False, 0)

    top_box.pack_start(orb, False, False, 0)
    top_box.pack_start(status_box, True, True, 0)

    # ── separator line ──
    sep = Gtk.Box()
    sep.set_size_request(-1, 1)
    sep.get_style_context().add_class("hud-sep")

    # ── transcript area ──
    transcript_label = Gtk.Label(label="Say \"Gemini\" to begin")
    transcript_label.set_halign(Gtk.Align.START)
    transcript_label.set_valign(Gtk.Align.START)
    transcript_label.set_line_wrap(True)
    transcript_label.set_max_width_chars(30)
    transcript_label.set_ellipsize(3)  # PANGO_ELLIPSIZE_END
    transcript_label.get_style_context().add_class("hud-transcript")

    vbox.pack_start(top_box, False, False, 0)
    vbox.pack_start(sep, False, False, 0)
    vbox.pack_start(transcript_label, True, True, 0)
    win.add(vbox)

    css_provider = Gtk.CssProvider()
    Gtk.StyleContext.add_provider_for_screen(
        screen, css_provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
    )

    phase = [0.0]
    prev_transcript = [""]

    def _tick():
        st = shared.state
        phase[0] += 0.06
        p = phase[0]

        # Update transcript label if changed
        t = shared.transcript
        if t != prev_transcript[0]:
            prev_transcript[0] = t
            GLib.idle_add(transcript_label.set_text, t or 'Say "Gemini" to begin')

        # Update status label
        GLib.idle_add(status_label.set_text, _STATE_LABEL.get(st, "STANDBY"))

        # Build CSS per state
        if st == State.IDLE:
            glow = int(6 + 3 * math.sin(p * 0.6))
            orb_bg = f"rgba(120, 130, 150, {0.45 + 0.1 * math.sin(p * 0.6):.2f})"
            orb_shadow = f"0 0 {glow}px rgba(160, 170, 200, 0.5)"
            orb_border = "rgba(200, 210, 230, 0.35)"
            status_color = "rgba(160, 170, 200, 0.8)"
        elif st == State.LISTENING:
            glow = int(14 + 8 * math.sin(p * 1.6))
            orb_bg = f"rgba(0, 200, 255, {0.80 + 0.15 * math.sin(p * 1.6):.2f})"
            orb_shadow = f"0 0 {glow}px rgba(0, 210, 255, 0.9), 0 0 {glow * 2}px rgba(0, 100, 255, 0.4)"
            orb_border = "rgba(100, 220, 255, 0.9)"
            status_color = "rgba(0, 220, 255, 1.0)"
        elif st == State.SPEAKING:
            glow = int(16 + 10 * math.sin(p * 2.2))
            orb_bg = f"rgba(52, 211, 153, {0.85 + 0.12 * math.sin(p * 2.2):.2f})"
            orb_shadow = f"0 0 {glow}px rgba(52, 211, 153, 0.95), 0 0 {glow * 2}px rgba(16, 185, 129, 0.5)"
            orb_border = "rgba(100, 230, 180, 0.9)"
            status_color = "rgba(52, 211, 153, 1.0)"
        else:  # CONNECTING
            glow = int(12 + 6 * math.sin(p * 2.0))
            orb_bg = f"rgba(245, 158, 11, {0.75 + 0.15 * math.sin(p * 2.0):.2f})"
            orb_shadow = f"0 0 {glow}px rgba(245, 158, 11, 0.85)"
            orb_border = "rgba(255, 200, 60, 0.8)"
            status_color = "rgba(245, 180, 50, 1.0)"

        css = f"""
        window {{ background-color: transparent; }}
        .hud-panel {{
            background: rgba(10, 12, 18, 0.82);
            border-radius: 18px;
            border: 1px solid rgba(100, 120, 180, 0.25);
            box-shadow: 0 4px 30px rgba(0, 0, 0, 0.6), inset 0 1px 0 rgba(255, 255, 255, 0.06);
            padding: 14px 16px 12px 16px;
        }}
        .hud-top {{
            padding: 0 0 10px 0;
        }}
        .hud-orb {{
            border-radius: 50%;
            background: radial-gradient(circle, {orb_bg} 0%, rgba(10, 12, 18, 0.3) 100%);
            box-shadow: {orb_shadow};
            border: 2px solid {orb_border};
        }}
        .hud-title {{
            color: rgba(200, 210, 240, 0.9);
            font-family: monospace;
            font-size: 13px;
            font-weight: bold;
            letter-spacing: 4px;
        }}
        .hud-status {{
            color: {status_color};
            font-family: monospace;
            font-size: 11px;
            font-weight: bold;
            letter-spacing: 2px;
        }}
        .hud-sep {{
            background: linear-gradient(to right, transparent, rgba(100, 140, 255, 0.35), transparent);
            margin: 0 4px 8px 4px;
        }}
        .hud-transcript {{
            color: rgba(180, 190, 220, 0.7);
            font-family: monospace;
            font-size: 10px;
            padding: 0 2px;
        }}
        """
        css_provider.load_from_data(css.encode("utf-8"))
        return True

    GLib.timeout_add(50, _tick)
    return win


# ── public API ──────────────────────────────────────────────────────────────

def start_overlay(shared: OverlayState) -> threading.Thread | None:
    """Start the overlay in a daemon thread. Returns the thread, or None."""

    def _run():
        try:
            import gi
            gi.require_version("Gdk", "3.0")
            gi.require_version("Gtk", "3.0")
            from gi.repository import Gtk

            win = _build_window(shared)
            win.show_all()
            log.info("HUD overlay running (top-right).")
            Gtk.main()
        except Exception:
            log.warning("Overlay unavailable — running headless.", exc_info=True)

    try:
        import gi
        gi.require_version("Gdk", "3.0")
        gi.require_version("Gtk", "3.0")
        from gi.repository import Gdk, Gtk  # noqa: F401
    except Exception:
        log.info("GTK3 not importable — overlay disabled.")
        return None

    t = threading.Thread(target=_run, daemon=True, name="overlay")
    t.start()
    return t
