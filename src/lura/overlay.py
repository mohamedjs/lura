"""Dynamic Island HUD overlay — Cybernetic AI Island with audio visualizer.

Floating top-center pill (Dynamic Island) positioned under the camera notch,
featuring the glowing cyber face avatar, real-time audio waveform visualizer,
status badge, and live conversation subtitle.
"""

from __future__ import annotations

import enum
import logging
import math
from pathlib import Path
import threading

log = logging.getLogger(__name__)

ASSETS_DIR = Path(__file__).resolve().parent / "assets"
AVATAR_PATH = ASSETS_DIR / "ai_face_square.png"
FALLBACK_AVATAR = ASSETS_DIR / "ai_face.png"


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
            self._transcript = value[-120:] if len(value) > 120 else value


# ── dimensions ──────────────────────────────────────────────────────────────

ISLAND_W = 430
ISLAND_H = 64
TOP_MARGIN = 8
AVATAR_SIZE = 46
NUM_BARS = 7


# ── GTK Dynamic Island Builder ─────────────────────────────────────────────

def _build_window(shared: OverlayState):
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

    # Center horizontally at top of primary monitor (camera notch position)
    display = Gdk.Display.get_default()
    if display:
        monitor = display.get_primary_monitor()
        if not monitor and display.get_n_monitors() > 0:
            monitor = display.get_monitor(0)
        if monitor:
            geo = monitor.get_geometry()
            scale = monitor.get_scale_factor()
            x = geo.x + ((geo.width // scale) - ISLAND_W) // 2
            y = geo.y + TOP_MARGIN
            win.move(x, y)

    # Main horizontal capsule container
    island = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
    island.get_style_context().add_class("dynamic-island")

    # 1. Cybernetic Face Avatar
    avatar_box = Gtk.Box()
    avatar_box.get_style_context().add_class("avatar-frame")
    avatar_img = None
    target_path = AVATAR_PATH if AVATAR_PATH.exists() else FALLBACK_AVATAR
    if target_path.exists():
        try:
            pix = GdkPixbuf.Pixbuf.new_from_file_at_scale(
                str(target_path), AVATAR_SIZE, AVATAR_SIZE, True
            )
            avatar_img = Gtk.Image.new_from_pixbuf(pix)
            avatar_box.add(avatar_img)
        except Exception as exc:
            log.warning("Could not load avatar pixbuf: %s", exc)

    if not avatar_img:
        fallback_orb = Gtk.Box()
        fallback_orb.set_size_request(AVATAR_SIZE, AVATAR_SIZE)
        fallback_orb.get_style_context().add_class("avatar-orb")
        avatar_box.add(fallback_orb)

    island.pack_start(avatar_box, False, False, 0)

    # 2. Audio Wave Equalizer Bars
    wave_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=3)
    wave_box.set_valign(Gtk.Align.CENTER)
    wave_box.get_style_context().add_class("wave-container")
    bars = []
    for _ in range(NUM_BARS):
        bar = Gtk.Box()
        bar.get_style_context().add_class("wave-bar")
        bar.set_size_request(3, 10)
        wave_box.pack_start(bar, False, False, 0)
        bars.append(bar)

    island.pack_start(wave_box, False, False, 0)

    # 3. Telemetry & Subtitle Information
    info_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
    info_box.set_valign(Gtk.Align.CENTER)

    header_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
    title_lbl = Gtk.Label(label="LURA")
    title_lbl.get_style_context().add_class("island-title")
    dot_lbl = Gtk.Label(label="•")
    dot_lbl.get_style_context().add_class("island-dot")
    status_lbl = Gtk.Label(label="STANDBY")
    status_lbl.get_style_context().add_class("island-status")

    header_box.pack_start(title_lbl, False, False, 0)
    header_box.pack_start(dot_lbl, False, False, 0)
    header_box.pack_start(status_lbl, False, False, 0)

    sub_lbl = Gtk.Label(label='Say "Gemini" to activate')
    sub_lbl.set_halign(Gtk.Align.START)
    sub_lbl.set_max_width_chars(28)
    sub_lbl.set_ellipsize(3)  # PANGO_ELLIPSIZE_END
    sub_lbl.get_style_context().add_class("island-sub")

    info_box.pack_start(header_box, False, False, 0)
    info_box.pack_start(sub_lbl, False, False, 0)
    island.pack_start(info_box, True, True, 0)

    win.add(island)

    css_provider = Gtk.CssProvider()
    Gtk.StyleContext.add_provider_for_screen(
        screen, css_provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
    )

    phase = [0.0]
    prev_transcript = [""]

    def _tick():
        st = shared.state
        phase[0] += 0.08
        p = phase[0]

        # Update transcript
        t = shared.transcript
        if t != prev_transcript[0]:
            prev_transcript[0] = t
            GLib.idle_add(sub_lbl.set_text, t or 'Say "Gemini" to activate')

        # Update status text
        GLib.idle_add(status_lbl.set_text, _STATE_LABEL.get(st, "STANDBY"))

        # Animate equalizer wave bars & compute theme colors
        if st == State.IDLE:
            bar_color = "rgba(0, 190, 240, 0.4)"
            avatar_glow = int(8 + 4 * math.sin(p * 0.7))
            avatar_shadow = f"0 0 {avatar_glow}px rgba(0, 180, 255, 0.5)"
            status_color = "rgba(160, 180, 210, 0.85)"
            border_glow = "rgba(0, 180, 240, 0.35)"
            for i, b in enumerate(bars):
                h = int(6 + 6 * abs(math.sin(p * 0.8 + i * 0.6)))
                b.set_size_request(3, h)

        elif st == State.LISTENING:
            bar_color = "rgba(0, 230, 255, 0.95)"
            avatar_glow = int(14 + 8 * math.sin(p * 2.0))
            avatar_shadow = f"0 0 {avatar_glow}px rgba(0, 230, 255, 0.95), 0 0 {avatar_glow*2}px rgba(0, 130, 255, 0.5)"
            status_color = "rgba(0, 235, 255, 1.0)"
            border_glow = "rgba(0, 220, 255, 0.75)"
            for i, b in enumerate(bars):
                h = int(8 + 22 * abs(math.sin(p * 2.2 + i * 0.9)))
                b.set_size_request(3, h)

        elif st == State.SPEAKING:
            bar_color = "rgba(52, 211, 153, 0.95)"
            avatar_glow = int(16 + 10 * math.sin(p * 2.6))
            avatar_shadow = f"0 0 {avatar_glow}px rgba(52, 211, 153, 0.95), 0 0 {avatar_glow*2}px rgba(16, 185, 129, 0.5)"
            status_color = "rgba(52, 211, 153, 1.0)"
            border_glow = "rgba(52, 211, 153, 0.8)"
            for i, b in enumerate(bars):
                h = int(10 + 26 * abs(math.sin(p * 3.0 + i * 1.2)))
                b.set_size_request(3, h)

        else:  # CONNECTING
            bar_color = "rgba(245, 158, 11, 0.9)"
            avatar_glow = int(12 + 6 * math.sin(p * 2.0))
            avatar_shadow = f"0 0 {avatar_glow}px rgba(245, 158, 11, 0.85)"
            status_color = "rgba(245, 175, 40, 1.0)"
            border_glow = "rgba(245, 158, 11, 0.6)"
            for i, b in enumerate(bars):
                h = int(6 + 12 * abs(math.sin(p * 1.8 + i * 0.7)))
                b.set_size_request(3, h)

        css = f"""
        window {{ background-color: transparent; }}
        .dynamic-island {{
            background: rgba(8, 10, 16, 0.93);
            border-radius: 32px;
            border: 1.5px solid {border_glow};
            box-shadow: 0 8px 30px rgba(0, 0, 0, 0.75), 0 0 16px {border_glow};
            padding: 8px 16px 8px 10px;
        }}
        .avatar-frame {{
            border-radius: 50%;
            border: 2px solid {bar_color};
            box-shadow: {avatar_shadow};
            background-color: #030610;
        }}
        .avatar-orb {{
            border-radius: 50%;
            background: radial-gradient(circle, {bar_color} 0%, rgba(3,6,16,0.3) 100%);
        }}
        .wave-container {{
            padding: 0 4px;
        }}
        .wave-bar {{
            background: {bar_color};
            border-radius: 2px;
            box-shadow: 0 0 6px {bar_color};
        }}
        .island-title {{
            color: rgba(220, 230, 255, 0.95);
            font-family: monospace;
            font-size: 11px;
            font-weight: bold;
            letter-spacing: 3px;
        }}
        .island-dot {{
            color: rgba(120, 140, 180, 0.6);
            font-size: 9px;
        }}
        .island-status {{
            color: {status_color};
            font-family: monospace;
            font-size: 10px;
            font-weight: bold;
            letter-spacing: 1.5px;
        }}
        .island-sub {{
            color: rgba(180, 200, 235, 0.75);
            font-family: monospace;
            font-size: 10px;
        }}
        """
        css_provider.load_from_data(css.encode("utf-8"))
        return True

    GLib.timeout_add(40, _tick)  # 25 fps smooth animations
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
            log.info("Dynamic Island HUD running (top-center).")
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
