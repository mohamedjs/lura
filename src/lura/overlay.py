"""Floating overlay orb — a Siri-style status indicator for Lura.

A small translucent circle styled with GTK3 + CSS that hovers
at the top-right of the screen across all workspaces. State is
shared with the voice engine via a thread-safe ``OverlayState`` object.

If GTK is unavailable (headless, SSH, missing display) the overlay
silently degrades to a no-op — the assistant continues normal operation.
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


class OverlayState:
    """Thread-safe state holder read by the overlay, written by the engine."""

    def __init__(self):
        self._state = State.IDLE
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


# ── GTK window & animation ──────────────────────────────────────────────────

WIN_SIZE = 72
MARGIN_X = 28
MARGIN_Y = 42  # clear GNOME top bar (~32px)


def _build_window(shared: OverlayState):
    """Create the floating overlay window."""
    import gi
    gi.require_version("Gdk", "3.0")
    gi.require_version("Gtk", "3.0")
    from gi.repository import Gdk, GLib, Gtk

    win = Gtk.Window(type=Gtk.WindowType.TOPLEVEL)
    win.set_title("Lura Assistant")
    win.set_decorated(False)
    win.set_resizable(False)
    win.set_keep_above(True)
    win.stick()                        # visible across all workspaces
    win.set_skip_taskbar_hint(True)
    win.set_skip_pager_hint(True)
    win.set_accept_focus(False)         # do not steal focus from other apps
    win.set_default_size(WIN_SIZE, WIN_SIZE)
    win.set_type_hint(Gdk.WindowTypeHint.DOCK)

    # RGBA transparency
    screen = win.get_screen()
    visual = screen.get_rgba_visual()
    if visual:
        win.set_visual(visual)
    win.set_app_paintable(True)

    # Position at top-right of primary monitor
    display = Gdk.Display.get_default()
    if display:
        monitor = display.get_primary_monitor()
        if not monitor and display.get_n_monitors() > 0:
            monitor = display.get_monitor(0)
        if monitor:
            geo = monitor.get_geometry()
            scale = monitor.get_scale_factor()
            x = geo.x + (geo.width // scale) - WIN_SIZE - MARGIN_X
            y = geo.y + MARGIN_Y
            win.move(x, y)

    box = Gtk.Box()
    box.set_size_request(WIN_SIZE, WIN_SIZE)
    box.get_style_context().add_class("lura-orb")
    win.add(box)

    css_provider = Gtk.CssProvider()
    Gtk.StyleContext.add_provider_for_screen(
        screen, css_provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
    )

    phase = [0.0]

    def _tick():
        st = shared.state
        phase[0] += 0.08
        p = phase[0]

        if st == State.IDLE:
            glow = int(8 + 3 * math.sin(p * 0.7))
            alpha = 0.45 + 0.10 * math.sin(p * 0.7)
            css = f"""
            window {{ background-color: transparent; }}
            .lura-orb {{
                margin: 10px;
                border-radius: 50%;
                background: radial-gradient(circle, rgba(160, 165, 180, {alpha:.2f}) 0%, rgba(90, 95, 110, {alpha*0.7:.2f}) 70%, rgba(40, 45, 55, 0.2) 100%);
                box-shadow: 0 0 {glow}px rgba(180, 190, 210, 0.45);
                border: 1.5px solid rgba(255, 255, 255, 0.35);
            }}
            """
        elif st == State.LISTENING:
            glow = int(16 + 8 * math.sin(p * 1.8))
            alpha = 0.85 + 0.15 * math.sin(p * 1.8)
            css = f"""
            window {{ background-color: transparent; }}
            .lura-orb {{
                margin: 8px;
                border-radius: 50%;
                background: radial-gradient(circle, rgba(0, 230, 255, {alpha:.2f}) 0%, rgba(0, 130, 255, {alpha*0.85:.2f}) 60%, rgba(10, 50, 180, 0.4) 100%);
                box-shadow: 0 0 {glow}px rgba(0, 210, 255, 0.9), 0 0 {glow*2}px rgba(0, 120, 255, 0.4);
                border: 2px solid rgba(255, 255, 255, 0.85);
            }}
            """
        elif st == State.SPEAKING:
            glow = int(18 + 10 * math.sin(p * 2.5))
            alpha = 0.90 + 0.10 * math.sin(p * 2.5)
            css = f"""
            window {{ background-color: transparent; }}
            .lura-orb {{
                margin: 6px;
                border-radius: 50%;
                background: radial-gradient(circle, rgba(52, 211, 153, {alpha:.2f}) 0%, rgba(16, 185, 129, {alpha*0.85:.2f}) 55%, rgba(5, 100, 70, 0.4) 100%);
                box-shadow: 0 0 {glow}px rgba(52, 211, 153, 0.95), 0 0 {glow*2}px rgba(16, 185, 129, 0.5);
                border: 2px solid rgba(255, 255, 255, 0.9);
            }}
            """
        else:  # CONNECTING
            glow = int(14 + 6 * math.sin(p * 2.2))
            alpha = 0.80 + 0.15 * math.sin(p * 2.2)
            css = f"""
            window {{ background-color: transparent; }}
            .lura-orb {{
                margin: 8px;
                border-radius: 50%;
                background: radial-gradient(circle, rgba(251, 191, 36, {alpha:.2f}) 0%, rgba(245, 158, 11, {alpha*0.85:.2f}) 60%, rgba(180, 83, 9, 0.3) 100%);
                box-shadow: 0 0 {glow}px rgba(245, 158, 11, 0.85), 0 0 {glow*2}px rgba(217, 119, 6, 0.4);
                border: 2px solid rgba(255, 255, 255, 0.8);
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
            log.info("Overlay floating orb running (top-right).")
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
