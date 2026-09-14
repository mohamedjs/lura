"""Dynamic Island HUD overlay — Neumorphic Glassmorphism AI Island.

Floating at the top-center of each monitor, featuring a soft-extruded glass
capsule, transparent glowing cyber face avatar, symmetrical audio wave bars,
neumorphic status chip, and live conversation subtitle.
"""

from __future__ import annotations

import enum
import logging
import math
from pathlib import Path
import threading

log = logging.getLogger(__name__)

ASSETS_DIR = Path(__file__).resolve().parent / "assets"


# ── shared state ────────────────────────────────────────────────────────────

class State(enum.Enum):
    IDLE = "idle"              # waiting for wake word
    LISTENING = "listening"    # session open, user speaking
    SPEAKING = "speaking"      # assistant is talking
    CONNECTING = "connecting"  # reconnecting between turns


_STATE_LABEL = {
    State.IDLE:       "STANDBY",
    State.LISTENING:  "LISTENING…",
    State.SPEAKING:   "SPEAKING…",
    State.CONNECTING: "CONNECTING…",
}


class OverlayState:
    """Thread-safe state + transcript, read by all overlay windows."""

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
            self._transcript = value[-120:] if len(value) > 120 else value


# ── dimensions ──────────────────────────────────────────────────────────────

ISLAND_W = 340
ISLAND_H = 118
AVATAR_SIZE = 44
BARS_PER_SIDE = 4
TOP_PADDING = 12


# ── GTK Dynamic Island Window Builder ──────────────────────────────────────

def _build_monitor_window(monitor, shared: OverlayState):
    """Create a Neumorphic Glassmorphism island window for a monitor."""
    import gi
    gi.require_version("Gdk", "3.0")
    gi.require_version("Gtk", "3.0")
    gi.require_version("GdkPixbuf", "2.0")
    from gi.repository import Gdk, GdkPixbuf, GLib, Gtk

    win = Gtk.Window(type=Gtk.WindowType.TOPLEVEL)
    win.set_title("Lura Dynamic Island")
    win.set_decorated(False)
    win.set_resizable(False)
    win.set_keep_above(True)
    win.stick()                        # visible across all workspaces
    win.set_skip_taskbar_hint(True)
    win.set_skip_pager_hint(True)
    win.set_accept_focus(False)         # never steal keyboard focus
    win.set_default_size(ISLAND_W, ISLAND_H)
    win.set_type_hint(Gdk.WindowTypeHint.DOCK)

    screen = win.get_screen()
    visual = screen.get_rgba_visual()
    if visual:
        win.set_visual(visual)
    win.set_app_paintable(True)

    # Position horizontally dead-center with clean top margin from workarea
    wa = monitor.get_workarea()
    scale = monitor.get_scale_factor()
    x = wa.x + ((wa.width // scale) - ISLAND_W) // 2
    y = wa.y + TOP_PADDING
    win.move(x, y)

    # Main Neumorphic Glass vertical container
    vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=5)
    vbox.get_style_context().add_class("dynamic-island")

    # ── 1. Top Row: Left Waves | Embossed Cyber Face | Right Waves ──
    top_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
    top_row.set_halign(Gtk.Align.CENTER)
    top_row.set_valign(Gtk.Align.CENTER)

    # Left audio wave bars
    left_wave_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=3)
    left_wave_box.set_valign(Gtk.Align.CENTER)
    left_bars = []
    for _ in range(BARS_PER_SIDE):
        bar = Gtk.Box()
        bar.get_style_context().add_class("wave-bar")
        bar.set_size_request(3, 10)
        left_wave_box.pack_start(bar, False, False, 0)
        left_bars.append(bar)
    top_row.pack_start(left_wave_box, False, False, 0)

    # Center Avatar: Circular Neumorphic Recessed Well
    avatar_box = Gtk.Box()
    avatar_box.get_style_context().add_class("avatar-frame")

    pix_cache = {}
    for st_val, svg_name in [
        (State.IDLE, "head_loading.svg"),
        (State.LISTENING, "head_listening.svg"),
        (State.SPEAKING, "head_speaking.svg"),
        (State.CONNECTING, "head_loading.svg"),
    ]:
        p = ASSETS_DIR / svg_name
        if p.exists():
            try:
                pix_cache[st_val] = GdkPixbuf.Pixbuf.new_from_file_at_scale(
                    str(p), AVATAR_SIZE, AVATAR_SIZE, True
                )
            except Exception:
                pass

    initial_pix = pix_cache.get(State.IDLE)
    avatar_img = None
    if initial_pix:
        avatar_img = Gtk.Image.new_from_pixbuf(initial_pix)
        avatar_box.add(avatar_img)
    else:
        fallback_orb = Gtk.Box()
        fallback_orb.set_size_request(AVATAR_SIZE, AVATAR_SIZE)
        avatar_box.add(fallback_orb)

    top_row.pack_start(avatar_box, False, False, 0)

    # Right audio wave bars
    right_wave_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=3)
    right_wave_box.set_valign(Gtk.Align.CENTER)
    right_bars = []
    for _ in range(BARS_PER_SIDE):
        bar = Gtk.Box()
        bar.get_style_context().add_class("wave-bar")
        bar.set_size_request(3, 10)
        right_wave_box.pack_start(bar, False, False, 0)
        right_bars.append(bar)
    top_row.pack_start(right_wave_box, False, False, 0)

    vbox.pack_start(top_row, False, False, 0)

    # ── 2. Middle Row: Neumorphic Status Chip ──
    chip_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=5)
    chip_box.set_halign(Gtk.Align.CENTER)
    chip_box.get_style_context().add_class("status-chip")

    dot_lbl = Gtk.Label(label="●")
    dot_lbl.get_style_context().add_class("chip-dot")
    status_lbl = Gtk.Label(label="STANDBY")
    status_lbl.get_style_context().add_class("chip-status")

    chip_box.pack_start(dot_lbl, False, False, 0)
    chip_box.pack_start(status_lbl, False, False, 0)
    vbox.pack_start(chip_box, False, False, 0)

    # ── 3. Bottom Row: Soft Transcript Subtitle ──
    sub_lbl = Gtk.Label(label='Say "Gemini" to activate')
    sub_lbl.set_halign(Gtk.Align.CENTER)
    sub_lbl.set_max_width_chars(34)
    sub_lbl.set_ellipsize(3)  # PANGO_ELLIPSIZE_END
    sub_lbl.get_style_context().add_class("island-sub")
    vbox.pack_start(sub_lbl, False, False, 0)

    win.add(vbox)

    css_provider = Gtk.CssProvider()
    Gtk.StyleContext.add_provider_for_screen(
        screen, css_provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
    )

    phase = [0.0]
    prev_transcript = [""]
    prev_state = [None]
    all_bars = left_bars + right_bars

    def _tick():
        st = shared.state
        phase[0] += 0.08
        p = phase[0]

        # Swap SVG dynamically without background
        if st != prev_state[0]:
            prev_state[0] = st
            if avatar_img and st in pix_cache:
                GLib.idle_add(avatar_img.set_from_pixbuf, pix_cache[st])

        # Update transcript
        t = shared.transcript
        if t != prev_transcript[0]:
            prev_transcript[0] = t
            GLib.idle_add(sub_lbl.set_text, t or 'Say "Gemini" to activate')

        # Update status text
        GLib.idle_add(status_lbl.set_text, _STATE_LABEL.get(st, "STANDBY"))

        # Compute Neumorphic + Glassmorphic dynamic colors
        if st == State.IDLE:
            accent = "#00d2ff"
            accent_glow = "rgba(0, 210, 255, 0.45)"
            bar_grad = "linear-gradient(180deg, #00d2ff 0%, #0066cc 100%)"
            avatar_shadow = f"inset 1.5px 1.5px 3px rgba(0,0,0,0.8), inset -1px -1px 2px rgba(255,255,255,0.08), 0 0 {int(8 + 3*math.sin(p*0.7))}px rgba(0, 210, 255, 0.45)"
            border_light = "rgba(0, 210, 255, 0.3)"
            for i, b in enumerate(all_bars):
                dist = abs(i - 3.5)
                h = int(5 + 5 * abs(math.sin(p * 0.8 + dist * 0.5)))
                b.set_size_request(3, h)

        elif st == State.LISTENING:
            accent = "#00f5ff"
            accent_glow = "rgba(0, 245, 255, 0.85)"
            bar_grad = "linear-gradient(180deg, #00ffff 0%, #0284c7 100%)"
            avatar_shadow = f"inset 1.5px 1.5px 3px rgba(0,0,0,0.9), inset -1px -1px 2px rgba(255,255,255,0.12), 0 0 {int(14 + 6*math.sin(p*2.0))}px rgba(0, 245, 255, 0.85)"
            border_light = "rgba(0, 245, 255, 0.65)"
            for i, b in enumerate(all_bars):
                dist = abs(i - 3.5)
                h = int(7 + 20 * abs(math.sin(p * 2.2 + dist * 0.8)))
                b.set_size_request(3, h)

        elif st == State.SPEAKING:
            accent = "#34d399"
            accent_glow = "rgba(52, 211, 153, 0.85)"
            bar_grad = "linear-gradient(180deg, #34d399 0%, #059669 100%)"
            avatar_shadow = f"inset 1.5px 1.5px 3px rgba(0,0,0,0.9), inset -1px -1px 2px rgba(255,255,255,0.12), 0 0 {int(16 + 8*math.sin(p*2.6))}px rgba(52, 211, 153, 0.85)"
            border_light = "rgba(52, 211, 153, 0.65)"
            for i, b in enumerate(all_bars):
                dist = abs(i - 3.5)
                h = int(8 + 24 * abs(math.sin(p * 3.0 + dist * 1.0)))
                b.set_size_request(3, h)

        else:  # CONNECTING
            accent = "#fbbf24"
            accent_glow = "rgba(251, 191, 36, 0.75)"
            bar_grad = "linear-gradient(180deg, #fbbf24 0%, #d97706 100%)"
            avatar_shadow = f"inset 1.5px 1.5px 3px rgba(0,0,0,0.8), inset -1px -1px 2px rgba(255,255,255,0.08), 0 0 {int(10 + 5*math.sin(p*2.0))}px rgba(251, 191, 36, 0.75)"
            border_light = "rgba(251, 191, 36, 0.5)"
            for i, b in enumerate(all_bars):
                dist = abs(i - 3.5)
                h = int(5 + 10 * abs(math.sin(p * 1.8 + dist * 0.7)))
                b.set_size_request(3, h)

        css = f"""
        window {{ background-color: transparent; }}
        .dynamic-island {{
            background: linear-gradient(135deg, rgba(22, 28, 44, 0.84) 0%, rgba(10, 14, 24, 0.92) 100%);
            border-radius: 26px;
            border: 1px solid rgba(255, 255, 255, 0.14);
            box-shadow: 0 16px 36px rgba(0, 0, 0, 0.65),
                        0 0 0 1px rgba(255, 255, 255, 0.08),
                        inset 0 1px 1px rgba(255, 255, 255, 0.22),
                        inset 0 -2px 4px rgba(0, 0, 0, 0.5);
            padding: 8px 16px 6px 16px;
        }}
        .avatar-frame {{
            border-radius: 50%;
            background: radial-gradient(circle, rgba(14, 18, 30, 0.85) 0%, rgba(8, 10, 18, 0.95) 100%);
            box-shadow: {avatar_shadow};
            border: 1.5px solid {border_light};
            padding: 2px;
        }}
        .wave-bar {{
            background: {bar_grad};
            border-radius: 2px;
            box-shadow: 0 0 6px {accent_glow};
        }}
        .status-chip {{
            background: rgba(4, 6, 12, 0.55);
            border-radius: 12px;
            box-shadow: inset 1px 1px 2px rgba(0, 0, 0, 0.6),
                        inset -1px -1px 1px rgba(255, 255, 255, 0.06);
            border: 1px solid rgba(255, 255, 255, 0.08);
            padding: 2px 10px;
        }}
        .chip-dot {{
            color: {accent};
            font-size: 8px;
        }}
        .chip-status {{
            color: rgba(230, 240, 255, 0.92);
            font-family: monospace;
            font-size: 10px;
            font-weight: bold;
            letter-spacing: 1.5px;
        }}
        .island-sub {{
            color: rgba(200, 220, 250, 0.82);
            font-family: system-ui, -apple-system, sans-serif;
            font-size: 10px;
            padding-top: 1px;
        }}
        """
        css_provider.load_from_data(css.encode("utf-8"))
        return True

    GLib.timeout_add(40, _tick)  # 25 fps
    return win


# ── public API ──────────────────────────────────────────────────────────────

def start_overlay(shared: OverlayState) -> threading.Thread | None:
    """Start the overlay on all active monitors in a daemon thread."""

    def _run():
        try:
            import gi
            gi.require_version("Gdk", "3.0")
            gi.require_version("Gtk", "3.0")
            from gi.repository import Gdk, Gtk

            display = Gdk.Display.get_default()
            if not display or display.get_n_monitors() == 0:
                log.warning("No display or monitors found for overlay.")
                return

            windows = []
            for i in range(display.get_n_monitors()):
                monitor = display.get_monitor(i)
                win = _build_monitor_window(monitor, shared)
                win.show_all()
                windows.append(win)

            log.info("Dynamic Island running on %d monitor(s) (top-center).", len(windows))
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
