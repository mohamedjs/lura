"""Dynamic Island HUD overlay — Cyberpunk AI Assistant Card (Exact Mockup Match).

Floating at the top-center of each monitor with pure transparent corner clipping,
featuring:
- Header: '✦ LURA' title + activity waveform & settings icon buttons
- Center: Holographic wireframe avatar with planetary orbital ring + 14 equalizer bars
- Status: Illuminated pill chip with bright cyan LED dot
- Prompt: 'Say "Lura" to activate' (or live conversation transcript)
- Sparkle divider & bottom feature navigation bar (Chat | Think | Create | Explore)
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

ISLAND_W = 460
ISLAND_H = 186
AVATAR_SIZE = 56
BARS_PER_SIDE = 7
TOP_PADDING = 12


# ── GTK Cyber Card Window Builder ──────────────────────────────────────────

def _build_monitor_window(monitor, shared: OverlayState):
    """Create a Cyberpunk HUD card window matching the mockup exactly."""
    import gi
    gi.require_version("Gdk", "3.0")
    gi.require_version("Gtk", "3.0")
    gi.require_version("GdkPixbuf", "2.0")
    from gi.repository import Gdk, GdkPixbuf, GLib, Gtk

    win = Gtk.Window(type=Gtk.WindowType.TOPLEVEL)
    win.set_title("Lura Cyber HUD")
    win.set_decorated(False)
    win.set_resizable(False)
    win.set_keep_above(True)
    win.stick()                        # visible across all workspaces
    win.set_skip_taskbar_hint(True)
    win.set_skip_pager_hint(True)
    win.set_accept_focus(False)         # never steal keyboard focus
    win.set_default_size(ISLAND_W, ISLAND_H)
    win.set_type_hint(Gdk.WindowTypeHint.DOCK)

    # Enable RGBA visual and cairo transparent background clearing
    screen = win.get_screen()
    visual = screen.get_rgba_visual()
    if visual:
        win.set_visual(visual)
    win.set_app_paintable(True)
    win.get_style_context().add_class("cyber-window")

    # Center horizontally on this monitor's workarea
    wa = monitor.get_workarea()
    scale = monitor.get_scale_factor()
    x = wa.x + ((wa.width // scale) - ISLAND_W) // 2
    y = wa.y + TOP_PADDING
    win.move(x, y)

    # Main Card Container
    card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
    card.get_style_context().add_class("cyber-card")
    card.set_size_request(ISLAND_W, ISLAND_H)

    # ── 1. Header: '✦ LURA' + Action Buttons (∿, ⚙) ──
    header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
    header.get_style_context().add_class("header-row")

    title_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
    sparkle = Gtk.Label(label="✦")
    sparkle.get_style_context().add_class("header-sparkle")
    title_lbl = Gtk.Label(label="LURA")
    title_lbl.get_style_context().add_class("header-title")
    title_box.pack_start(sparkle, False, False, 0)
    title_box.pack_start(title_lbl, False, False, 0)
    header.pack_start(title_box, True, True, 0)

    btn_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
    wave_btn = Gtk.Label(label="∿")
    wave_btn.get_style_context().add_class("header-btn")
    gear_btn = Gtk.Label(label="⚙")
    gear_btn.get_style_context().add_class("header-btn")
    btn_box.pack_start(wave_btn, False, False, 0)
    btn_box.pack_start(gear_btn, False, False, 0)
    header.pack_start(btn_box, False, False, 0)

    card.pack_start(header, False, False, 0)

    # ── 2. Mid: Left Wave Bars | Orbital Avatar Ring | Right Wave Bars ──
    mid_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=14)
    mid_row.set_halign(Gtk.Align.CENTER)
    mid_row.set_valign(Gtk.Align.CENTER)

    # Left equalizer wave bars (7 bars)
    left_wave_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
    left_wave_box.set_valign(Gtk.Align.CENTER)
    left_bars = []
    for _ in range(BARS_PER_SIDE):
        bar = Gtk.Box()
        bar.get_style_context().add_class("wave-bar")
        bar.set_size_request(3, 16)
        left_wave_box.pack_start(bar, False, False, 0)
        left_bars.append(bar)
    mid_row.pack_start(left_wave_box, False, False, 0)

    # Center Avatar Ring
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
    mid_row.pack_start(avatar_outer, False, False, 0)

    # Right equalizer wave bars (7 bars)
    right_wave_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
    right_wave_box.set_valign(Gtk.Align.CENTER)
    right_bars = []
    for _ in range(BARS_PER_SIDE):
        bar = Gtk.Box()
        bar.get_style_context().add_class("wave-bar")
        bar.set_size_request(3, 16)
        right_wave_box.pack_start(bar, False, False, 0)
        right_bars.append(bar)
    mid_row.pack_start(right_wave_box, False, False, 0)

    card.pack_start(mid_row, False, False, 0)

    # ── 3. Pill Status Badge (● STANDBY) ──
    pill = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
    pill.set_halign(Gtk.Align.CENTER)
    pill.get_style_context().add_class("status-pill")

    dot = Gtk.Label(label="●")
    dot.get_style_context().add_class("status-dot")
    status_lbl = Gtk.Label(label="STANDBY")
    status_lbl.get_style_context().add_class("status-text")

    pill.pack_start(dot, False, False, 0)
    pill.pack_start(status_lbl, False, False, 0)
    card.pack_start(pill, False, False, 0)

    # ── 4. Subtitle: 'Say "Lura" to activate' / Live transcript ──
    sub_lbl = Gtk.Label(label='Say "Lura" to activate')
    sub_lbl.set_halign(Gtk.Align.CENTER)
    sub_lbl.set_max_width_chars(44)
    sub_lbl.set_ellipsize(3)  # PANGO_ELLIPSIZE_END
    sub_lbl.get_style_context().add_class("subtitle-text")
    card.pack_start(sub_lbl, False, False, 0)

    # ── 5. Star Sparkle Divider ──
    div_lbl = Gtk.Label(label="──────────  ✦  ──────────")
    div_lbl.set_halign(Gtk.Align.CENTER)
    div_lbl.get_style_context().add_class("sparkle-divider")
    card.pack_start(div_lbl, False, False, 0)

    # ── 6. Bottom Feature Bar (Chat | Think | Create | Explore) ──
    nav_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
    nav_box.set_halign(Gtk.Align.CENTER)
    nav_box.get_style_context().add_class("nav-bar")

    for idx, (icon, name) in enumerate([
        ("💬", "Chat"),
        ("💡", "Think"),
        ("✦", "Create"),
        ("🌐", "Explore"),
    ]):
        if idx > 0:
            sep = Gtk.Label(label="│")
            sep.get_style_context().add_class("nav-sep")
            nav_box.pack_start(sep, False, False, 0)
        item = Gtk.Label(label=f"{icon} {name}")
        item.get_style_context().add_class("nav-item")
        nav_box.pack_start(item, False, False, 0)

    card.pack_start(nav_box, False, False, 0)

    win.add(card)

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

        # Swap vector hologram avatar on state change
        if st != prev_state[0]:
            prev_state[0] = st
            if avatar_img and st in pix_cache:
                GLib.idle_add(avatar_img.set_from_pixbuf, pix_cache[st])

        # Update transcript
        t = shared.transcript
        if t != prev_transcript[0]:
            prev_transcript[0] = t
            GLib.idle_add(sub_lbl.set_text, t or 'Say "Lura" to activate')

        # Update status text
        GLib.idle_add(status_lbl.set_text, _STATE_LABEL.get(st, "STANDBY"))

        # Dynamic state colors & wave bars
        if st == State.IDLE:
            cyan = "#00d8ff"
            glow_c = "rgba(0, 216, 255, 0.85)"
            outer_ring_glow = f"0 0 {int(18 + 5*math.sin(p*0.7))}px rgba(0, 216, 255, 0.85), inset 0 0 10px rgba(0, 180, 255, 0.45)"
            bar_color = "#00d8ff"
            envelope = [8, 14, 22, 28, 22, 14, 8]
            for i, b in enumerate(left_bars):
                h = int(envelope[i] + 4 * abs(math.sin(p * 0.8 + i * 0.5)))
                b.set_size_request(3, h)
            for i, b in enumerate(right_bars):
                h = int(envelope[6 - i] + 4 * abs(math.sin(p * 0.8 + (6 - i) * 0.5)))
                b.set_size_request(3, h)

        elif st == State.LISTENING:
            cyan = "#00f5ff"
            glow_c = "rgba(0, 245, 255, 0.95)"
            outer_ring_glow = f"0 0 {int(26 + 8*math.sin(p*2.0))}px rgba(0, 245, 255, 0.95), 0 0 42px rgba(0, 140, 255, 0.6), inset 0 0 14px rgba(0, 220, 255, 0.6)"
            bar_color = "#00ffff"
            for i, b in enumerate(all_bars):
                dist = abs(i - 6.5)
                h = int(10 + 26 * abs(math.sin(p * 2.4 + dist * 0.7)))
                b.set_size_request(3, h)

        elif st == State.SPEAKING:
            cyan = "#00ff9d"
            glow_c = "rgba(0, 255, 157, 0.95)"
            outer_ring_glow = f"0 0 {int(28 + 10*math.sin(p*2.8))}px rgba(0, 255, 157, 0.95), 0 0 42px rgba(16, 185, 129, 0.6), inset 0 0 14px rgba(0, 255, 157, 0.6)"
            bar_color = "#00ff9d"
            for i, b in enumerate(all_bars):
                dist = abs(i - 6.5)
                h = int(12 + 30 * abs(math.sin(p * 3.2 + dist * 0.9)))
                b.set_size_request(3, h)

        else:  # CONNECTING
            cyan = "#fbbf24"
            glow_c = "rgba(251, 191, 36, 0.85)"
            outer_ring_glow = f"0 0 {int(18 + 6*math.sin(p*2.0))}px rgba(251, 191, 36, 0.85), inset 0 0 10px rgba(251, 191, 36, 0.4)"
            bar_color = "#fbbf24"
            for i, b in enumerate(all_bars):
                dist = abs(i - 6.5)
                h = int(8 + 14 * abs(math.sin(p * 1.8 + dist * 0.6)))
                b.set_size_request(3, h)

        css = f"""
        window, window.background, .cyber-window {{
            background-color: rgba(0, 0, 0, 0);
            background: none;
            border: none;
            box-shadow: none;
        }}
        .cyber-card {{
            margin: 6px;
            background: linear-gradient(180deg, #071228 0%, #030816 100%);
            border: 2px solid {cyan};
            border-radius: 24px;
            box-shadow: 0 0 26px {glow_c},
                        inset 0 0 22px rgba(0, 120, 255, 0.25),
                        0 20px 45px rgba(0, 0, 0, 0.95);
            padding: 10px 18px 8px 18px;
        }}
        .header-row {{
            padding: 0 4px;
        }}
        .header-sparkle {{
            color: {cyan};
            font-size: 11px;
            text-shadow: 0 0 8px {cyan};
        }}
        .header-title {{
            color: #ffffff;
            font-family: system-ui, -apple-system, sans-serif;
            font-size: 11px;
            font-weight: bold;
            letter-spacing: 2.5px;
        }}
        .header-btn {{
            color: {cyan};
            background: rgba(0, 100, 200, 0.25);
            border: 1px solid rgba(0, 200, 255, 0.45);
            border-radius: 50%;
            font-size: 11px;
            padding: 2px 7px;
        }}
        .avatar-ring-outer {{
            border-radius: 50%;
            border: 2px solid {cyan};
            background: radial-gradient(circle, rgba(0, 100, 200, 0.4) 0%, rgba(2, 6, 16, 0.85) 100%);
            box-shadow: {outer_ring_glow};
            padding: 3px;
        }}
        .avatar-ring-inner {{
            border-radius: 50%;
            border: 1px solid rgba(255, 255, 255, 0.45);
            padding: 2px;
        }}
        .wave-bar {{
            background: {bar_color};
            border-radius: 2px;
            box-shadow: 0 0 8px {bar_color};
        }}
        .status-pill {{
            border-radius: 18px;
            border: 1.5px solid {cyan};
            background: rgba(4, 10, 24, 0.9);
            box-shadow: 0 0 14px {glow_c};
            padding: 3px 22px;
        }}
        .status-dot {{
            color: #00ffcc;
            font-size: 12px;
            text-shadow: 0 0 10px #00ffcc;
        }}
        .status-text {{
            color: #ffffff;
            font-family: monospace, sans-serif;
            font-size: 12px;
            font-weight: bold;
            letter-spacing: 3px;
        }}
        .subtitle-text {{
            color: #b8d2f4;
            font-family: system-ui, -apple-system, sans-serif;
            font-size: 11px;
            font-weight: 500;
        }}
        .sparkle-divider {{
            color: rgba(0, 180, 255, 0.35);
            font-size: 9px;
            letter-spacing: 1px;
        }}
        .nav-bar {{
            padding-bottom: 2px;
        }}
        .nav-item {{
            color: rgba(185, 210, 245, 0.8);
            font-family: system-ui, -apple-system, sans-serif;
            font-size: 10px;
        }}
        .nav-sep {{
            color: rgba(0, 150, 255, 0.3);
            font-size: 9px;
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

            log.info("Lura Cyber UI running on %d monitor(s) (top-center).", len(windows))
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
