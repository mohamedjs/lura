"""Dynamic Island HUD overlay — Cyberpunk HUD Card (Exact Mockup Match).

Floating top-center cyber panel with angled corner accents, centered glowing
wireframe avatar ring, 5-band flanking equalizer wave bars, pill status badge,
and live transcript subtitle.
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

ISLAND_W = 440
ISLAND_H = 148
AVATAR_SIZE = 50
BARS_PER_SIDE = 5
TOP_PADDING = 12


# ── GTK Dynamic Island Window Builder ──────────────────────────────────────

def _build_monitor_window(monitor, shared: OverlayState):
    """Create a Cyber HUD island window matching the mockup exactly."""
    import gi
    gi.require_version("Gdk", "3.0")
    gi.require_version("Gtk", "3.0")
    gi.require_version("GdkPixbuf", "2.0")
    from gi.repository import Gdk, GdkPixbuf, GLib, Gtk

    win = Gtk.Window(type=Gtk.WindowType.TOPLEVEL)
    win.set_title("Lura Cyber Island")
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

    # Gtk.Overlay allows cyber corner accent brackets
    root_overlay = Gtk.Overlay()

    # Main cyber card panel
    panel = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
    panel.get_style_context().add_class("cyber-panel")
    panel.set_size_request(ISLAND_W, ISLAND_H)

    # ── 1. Top Row: Left Waves | Cyber Avatar Ring | Right Waves ──
    top_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=16)
    top_row.set_halign(Gtk.Align.CENTER)
    top_row.set_valign(Gtk.Align.CENTER)

    # Left audio wave bars (5 bars)
    left_wave_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=5)
    left_wave_box.set_valign(Gtk.Align.CENTER)
    left_bars = []
    for _ in range(BARS_PER_SIDE):
        bar = Gtk.Box()
        bar.get_style_context().add_class("wave-bar")
        bar.set_size_request(3, 16)
        left_wave_box.pack_start(bar, False, False, 0)
        left_bars.append(bar)
    top_row.pack_start(left_wave_box, False, False, 0)

    # Center Avatar: Double Neon Glowing Ring
    avatar_outer = Gtk.Box()
    avatar_outer.get_style_context().add_class("avatar-ring-outer")
    avatar_inner = Gtk.Box()
    avatar_inner.get_style_context().add_class("avatar-ring-inner")

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
        avatar_inner.add(avatar_img)
    else:
        fallback_orb = Gtk.Box()
        fallback_orb.set_size_request(AVATAR_SIZE, AVATAR_SIZE)
        avatar_inner.add(fallback_orb)

    avatar_outer.add(avatar_inner)
    top_row.pack_start(avatar_outer, False, False, 0)

    # Right audio wave bars (5 bars)
    right_wave_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=5)
    right_wave_box.set_valign(Gtk.Align.CENTER)
    right_bars = []
    for _ in range(BARS_PER_SIDE):
        bar = Gtk.Box()
        bar.get_style_context().add_class("wave-bar")
        bar.set_size_request(3, 16)
        right_wave_box.pack_start(bar, False, False, 0)
        right_bars.append(bar)
    top_row.pack_start(right_wave_box, False, False, 0)

    panel.pack_start(top_row, False, False, 4)

    # ── 2. Middle Row: Pill Status Chip (STANDBY) ──
    pill = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
    pill.set_halign(Gtk.Align.CENTER)
    pill.get_style_context().add_class("status-pill")

    dot = Gtk.Label(label="●")
    dot.get_style_context().add_class("status-dot")
    status_lbl = Gtk.Label(label="STANDBY")
    status_lbl.get_style_context().add_class("status-text")

    pill.pack_start(dot, False, False, 0)
    pill.pack_start(status_lbl, False, False, 0)
    panel.pack_start(pill, False, False, 0)

    # ── 3. Bottom Row: Subtitle / Live Transcript ──
    sub_lbl = Gtk.Label(label='Say "Gemini" to activate')
    sub_lbl.set_halign(Gtk.Align.CENTER)
    sub_lbl.set_max_width_chars(42)
    sub_lbl.set_ellipsize(3)  # PANGO_ELLIPSIZE_END
    sub_lbl.get_style_context().add_class("subtitle-text")
    panel.pack_start(sub_lbl, False, False, 0)

    root_overlay.add(panel)

    # ── Cyber Corner Accents (Top-Left & Bottom-Right) ──
    tl_accent = Gtk.Box()
    tl_accent.get_style_context().add_class("corner-accent-tl")
    tl_accent.set_size_request(28, 4)
    tl_accent.set_halign(Gtk.Align.START)
    tl_accent.set_valign(Gtk.Align.START)
    tl_accent.set_margin_start(16)
    tl_accent.set_margin_top(3)
    root_overlay.add_overlay(tl_accent)

    br_accent = Gtk.Box()
    br_accent.get_style_context().add_class("corner-accent-br")
    br_accent.set_size_request(28, 4)
    br_accent.set_halign(Gtk.Align.END)
    br_accent.set_valign(Gtk.Align.END)
    br_accent.set_margin_end(16)
    br_accent.set_margin_bottom(3)
    root_overlay.add_overlay(br_accent)

    win.add(root_overlay)

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

        # Swap SVG vector head dynamically on state change
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

        # Animate visualizer and colors matching state
        if st == State.IDLE:
            cyan = "#00d8ff"
            glow_c = "rgba(0, 216, 255, 0.85)"
            outer_ring_glow = f"0 0 {int(16 + 4*math.sin(p*0.7))}px rgba(0, 216, 255, 0.8), inset 0 0 10px rgba(0, 180, 255, 0.4)"
            bar_color = "#00d8ff"
            # Symmetric curve: short on outside, taller near face
            wave_profile = [10, 16, 24, 18, 12]
            for idx, b in enumerate(left_bars):
                base_h = wave_profile[idx]
                h = int(base_h + 4 * abs(math.sin(p * 0.8 + idx * 0.6)))
                b.set_size_request(3, h)
            for idx, b in enumerate(right_bars):
                base_h = wave_profile[4 - idx]
                h = int(base_h + 4 * abs(math.sin(p * 0.8 + (4 - idx) * 0.6)))
                b.set_size_request(3, h)

        elif st == State.LISTENING:
            cyan = "#00f5ff"
            glow_c = "rgba(0, 245, 255, 0.95)"
            outer_ring_glow = f"0 0 {int(24 + 8*math.sin(p*2.0))}px rgba(0, 245, 255, 0.95), 0 0 40px rgba(0, 140, 255, 0.6), inset 0 0 14px rgba(0, 220, 255, 0.6)"
            bar_color = "#00ffff"
            for idx, b in enumerate(all_bars):
                dist = abs(idx - 4.5)
                h = int(10 + 26 * abs(math.sin(p * 2.4 + dist * 0.8)))
                b.set_size_request(3, h)

        elif st == State.SPEAKING:
            cyan = "#00ff9d"
            glow_c = "rgba(0, 255, 157, 0.95)"
            outer_ring_glow = f"0 0 {int(26 + 10*math.sin(p*2.8))}px rgba(0, 255, 157, 0.95), 0 0 40px rgba(16, 185, 129, 0.6), inset 0 0 14px rgba(0, 255, 157, 0.6)"
            bar_color = "#00ff9d"
            for idx, b in enumerate(all_bars):
                dist = abs(idx - 4.5)
                h = int(12 + 30 * abs(math.sin(p * 3.2 + dist * 1.0)))
                b.set_size_request(3, h)

        else:  # CONNECTING
            cyan = "#fbbf24"
            glow_c = "rgba(251, 191, 36, 0.85)"
            outer_ring_glow = f"0 0 {int(18 + 6*math.sin(p*2.0))}px rgba(251, 191, 36, 0.85), inset 0 0 10px rgba(251, 191, 36, 0.4)"
            bar_color = "#fbbf24"
            for idx, b in enumerate(all_bars):
                dist = abs(idx - 4.5)
                h = int(8 + 14 * abs(math.sin(p * 1.8 + dist * 0.7)))
                b.set_size_request(3, h)

        css = f"""
        window {{ background-color: transparent; }}
        .cyber-panel {{
            background: linear-gradient(180deg, #071022 0%, #030814 100%);
            border: 2px solid {cyan};
            border-radius: 20px;
            box-shadow: 0 0 24px {glow_c},
                        inset 0 0 20px rgba(0, 100, 255, 0.2),
                        0 20px 40px rgba(0, 0, 0, 0.9);
            padding: 14px 20px 10px 20px;
        }}
        .corner-accent-tl {{
            background: {cyan};
            border-radius: 3px;
            box-shadow: 0 0 10px {cyan};
        }}
        .corner-accent-br {{
            background: {cyan};
            border-radius: 3px;
            box-shadow: 0 0 10px {cyan};
        }}
        .avatar-ring-outer {{
            border-radius: 50%;
            border: 2px solid {cyan};
            background: radial-gradient(circle, rgba(0, 90, 180, 0.35) 0%, rgba(2, 6, 16, 0.8) 100%);
            box-shadow: {outer_ring_glow};
            padding: 4px;
        }}
        .avatar-ring-inner {{
            border-radius: 50%;
            border: 1px solid rgba(255, 255, 255, 0.4);
            padding: 2px;
        }}
        .wave-bar {{
            background: {bar_color};
            border-radius: 2px;
            box-shadow: 0 0 10px {bar_color};
        }}
        .status-pill {{
            border-radius: 18px;
            border: 1.5px solid {cyan};
            background: rgba(4, 10, 24, 0.9);
            box-shadow: 0 0 14px {glow_c};
            padding: 4px 22px;
        }}
        .status-dot {{
            color: {cyan};
            font-size: 11px;
            text-shadow: 0 0 10px {cyan};
        }}
        .status-text {{
            color: #ffffff;
            font-family: monospace, sans-serif;
            font-size: 13px;
            font-weight: bold;
            letter-spacing: 3px;
        }}
        .subtitle-text {{
            color: #b8d2f4;
            font-family: system-ui, -apple-system, sans-serif;
            font-size: 11px;
            padding-top: 2px;
        }}
        """
        css_provider.load_from_data(css.encode("utf-8"))
        return True

    GLib.timeout_add(40, _tick)  # 25 fps smooth animations
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

            log.info("Cyber HUD running on %d monitor(s) (top-center).", len(windows))
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
