"""Lura Hologram Overlay — 3D Cyberpunk AI Wireframe Face.

Floating in the top-right corner with zero chrome, border, or background.
Supports three smoothly transitioned visual states:
- Listening: Stable, gentle idle pulse/glow.
- Thinking: Rotational 3D motion + data-processing glow animation through vertices.
- Speaking: Audio-reactive wave/pulse animation across wireframe nodes.
"""

from __future__ import annotations

import enum
import io
import logging
import math
from pathlib import Path
import threading

log = logging.getLogger(__name__)

# ── 3D Wireframe Face Model ──────────────────────────────────────────────────
# 67 anatomical facial vertices centered at (0, 0, 0)
VERTICES_3D: list[tuple[float, float, float]] = [
    (0.0, -170.0, 3.8), (-35.0, -160.0, -4.8), (35.0, -160.0, -4.8), (-65.0, -136.0, -20.0),
    (65.0, -136.0, -20.0), (0.0, -133.0, 24.4), (-40.0, -118.0, 22.1), (40.0, -118.0, 22.1),
    (-88.0, -100.0, -20.0), (88.0, -100.0, -20.0), (0.0, -93.0, 41.1), (-25.0, -88.0, 40.0),
    (25.0, -88.0, 40.0), (-55.0, -83.0, 30.1), (55.0, -83.0, 30.1), (-25.0, -66.0, 46.5),
    (25.0, -66.0, 46.5), (0.0, -63.0, 49.7), (-55.0, -60.0, 37.6), (55.0, -60.0, 37.6),
    (-100.0, -58.0, -20.0), (100.0, -58.0, -20.0), (-35.0, -52.0, 47.3), (35.0, -52.0, 47.3),
    (-80.0, -48.0, 22.9), (80.0, -48.0, 22.9), (-18.0, -46.0, 45.9), (0.0, -46.0, 75.1),
    (18.0, -46.0, 45.9), (-55.0, -43.0, 41.5), (55.0, -43.0, 41.5), (-35.0, -38.0, 49.8),
    (35.0, -38.0, 49.8), (0.0, -18.0, 78.4), (-80.0, -13.0, 29.3), (80.0, -13.0, 29.3),
    (-48.0, -8.0, 48.3), (48.0, -8.0, 48.3), (-16.0, 0.0, 56.1), (16.0, 0.0, 56.1),
    (0.0, 4.0, 87.0), (0.0, 12.0, 86.7), (-35.0, 17.0, 52.0), (35.0, 17.0, 52.0),
    (-70.0, 27.0, 35.5), (70.0, 27.0, 35.5), (0.0, 34.0, 70.9), (-20.0, 37.0, 69.0),
    (20.0, 37.0, 69.0), (-35.0, 44.0, 48.8), (35.0, 44.0, 48.8), (-20.0, 48.0, 67.3),
    (20.0, 48.0, 67.3), (0.0, 50.0, 68.4), (0.0, 64.0, 65.5), (-62.0, 67.0, 31.5),
    (62.0, 67.0, 31.5), (-45.0, 102.0, 27.5), (45.0, 102.0, 27.5), (-18.0, 127.0, 39.4),
    (18.0, 127.0, 39.4), (0.0, 132.0, 38.9), (-35.0, 137.0, 13.8), (35.0, 137.0, 13.8),
    (-50.0, 167.0, -20.0), (50.0, 167.0, -20.0), (0.0, 170.0, 17.8),
]

# 138 connecting mesh edges
EDGES: list[tuple[int, int]] = [
    (0, 1), (0, 2), (1, 3), (2, 4), (3, 8), (4, 9), (8, 20), (9, 21), (20, 24), (21, 25),
    (0, 5), (1, 6), (2, 7), (3, 6), (4, 7), (8, 13), (9, 14), (5, 1), (5, 2), (5, 6),
    (5, 7), (5, 10), (6, 10), (7, 10), (6, 13), (7, 14), (13, 11), (14, 12), (10, 11),
    (10, 12), (10, 17), (11, 17), (12, 17), (11, 15), (12, 16), (13, 18), (14, 19),
    (17, 15), (17, 16), (15, 18), (16, 19), (18, 24), (19, 25), (15, 22), (18, 22),
    (18, 29), (29, 22), (22, 26), (26, 31), (31, 29), (22, 31), (16, 23), (19, 23),
    (19, 30), (30, 23), (23, 28), (28, 32), (32, 30), (23, 32), (17, 27), (15, 27),
    (16, 27), (27, 26), (27, 28), (27, 33), (33, 40), (33, 38), (33, 39), (26, 38),
    (28, 39), (38, 40), (39, 40), (40, 41), (38, 41), (39, 41), (31, 36), (32, 37),
    (29, 34), (30, 35), (24, 34), (25, 35), (34, 36), (35, 37), (34, 44), (35, 45),
    (36, 42), (37, 43), (38, 36), (39, 37), (38, 42), (39, 43), (42, 44), (43, 45),
    (41, 46), (38, 47), (39, 48), (46, 47), (46, 48), (47, 49), (48, 50), (42, 49),
    (43, 50), (49, 51), (50, 52), (53, 51), (53, 52), (46, 53), (47, 51), (48, 52),
    (53, 54), (51, 54), (52, 54), (44, 55), (45, 56), (55, 49), (56, 50), (55, 57),
    (56, 58), (57, 54), (58, 54), (57, 59), (58, 60), (59, 54), (60, 54), (59, 61),
    (60, 61), (59, 62), (60, 63), (61, 62), (61, 63), (57, 64), (58, 65), (62, 64),
    (63, 65), (62, 66), (63, 66), (64, 66), (65, 66),
]


# ── State Machine ────────────────────────────────────────────────────────────

class State(enum.Enum):
    LISTENING = "listening"    # Stable, gentle idle pulse/glow
    THINKING = "thinking"      # Rotational or data-processing glow
    SPEAKING = "speaking"      # Audio-reactive wave/pulse animation
    # Backward compatibility aliases
    IDLE = "idle"
    CONNECTING = "connecting"


class OverlayState:
    """Thread-safe state container."""

    def __init__(self):
        self._state = State.LISTENING
        self._transcript = ""
        self._lock = threading.Lock()

    @property
    def state(self) -> State:
        with self._lock:
            return self._state

    @state.setter
    def state(self, val: State | str):
        if isinstance(val, str):
            val = State(val)
        with self._lock:
            self._state = val

    @property
    def transcript(self) -> str:
        with self._lock:
            return self._transcript

    @transcript.setter
    def transcript(self, val: str):
        with self._lock:
            self._transcript = val


class StateBlender:
    """Smooth continuous interpolation between visual states."""

    def __init__(self):
        self.wl = 1.0  # listening weight
        self.wt = 0.0  # thinking weight
        self.ws = 0.0  # speaking weight

    def update(self, target_st: State, dt: float = 0.04):
        # Normalize alias states
        if target_st in (State.IDLE, State.LISTENING):
            tl, tt, ts = 1.0, 0.0, 0.0
        elif target_st in (State.CONNECTING, State.THINKING):
            tl, tt, ts = 0.0, 1.0, 0.0
        else:  # SPEAKING
            tl, tt, ts = 0.0, 0.0, 1.0

        # Smooth exponential lerp (~300ms transition)
        k = min(1.0, dt * 5.0)
        self.wl += (tl - self.wl) * k
        self.wt += (tt - self.wt) * k
        self.ws += (ts - self.ws) * k

        tot = self.wl + self.wt + self.ws
        if tot > 0.001:
            self.wl /= tot
            self.wt /= tot
            self.ws /= tot


# ── Dimensions & Placement ──────────────────────────────────────────────────

OVERLAY_SIZE = 170
MARGIN_RIGHT = 24
MARGIN_TOP = 20


# ── Cairo Procedural 3D Head Renderer ───────────────────────────────────────

def _render_frame(t: float, blender: StateBlender, size: int = OVERLAY_SIZE) -> bytes:
    """Render the 3D wireframe head frame to PNG bytes."""
    import cairo

    wl, wt, ws = blender.wl, blender.wt, blender.ws
    surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, size, size)
    cr = cairo.Context(surface)
    cr.set_operator(cairo.OPERATOR_CLEAR)
    cr.paint()
    cr.set_operator(cairo.OPERATOR_OVER)

    cx, cy = size / 2.0, size / 2.0
    dist = 310.0

    # 1. State-specific orientations & parameters
    # Listening: stable, gentle idle pulse
    theta_y_l = 0.04 * math.sin(t * 0.8)
    theta_x_l = 0.02 * math.cos(t * 0.6)
    scale_l = 1.0 + 0.03 * math.sin(t * 2.0)
    pulse_l = 0.7 + 0.3 * math.sin(t * 2.0)

    # Thinking: continuous 3D rotational motion + vertical data-scan sweep
    theta_y_t = 0.72 * math.sin(t * 1.5)
    theta_x_t = 0.12 * math.cos(t * 1.1)
    scan_y = -170.0 + 340.0 * ((t * 0.75) % 1.0)

    # Speaking: organic head tilts + audio-reactive ripple wave
    theta_y_s = 0.07 * math.sin(t * 2.4)
    theta_x_s = 0.04 * math.sin(t * 3.0)

    # Blended rotation & base scale
    rot_y = wl * theta_y_l + wt * theta_y_t + ws * theta_y_s
    rot_x = wl * theta_x_l + wt * theta_x_t + ws * theta_x_s
    base_scale = 0.37 * (wl * scale_l + wt * 1.0 + ws * 1.0)

    cos_y, sin_y = math.cos(rot_y), math.sin(rot_y)
    cos_x, sin_x = math.cos(rot_x), math.sin(rot_x)

    # Ambient center bloom
    if wl > 0.05 or ws > 0.05:
        pat = cairo.RadialGradient(cx, cy, 5, cx, cy, 70)
        alpha_b = (0.07 * pulse_l * wl) + (0.10 * ws)
        pat.add_color_stop_rgba(0.0, 0.0, 0.85, 1.0, alpha_b)
        pat.add_color_stop_rgba(1.0, 0.0, 0.2, 0.8, 0.0)
        cr.set_source(pat)
        cr.arc(cx, cy, 70, 0, 6.283)
        cr.fill()

    # 2. Transform 3D Vertices
    proj: list[tuple[float, float, float]] = []
    node_glow: list[float] = []
    node_radii: list[float] = []

    for x, y, z in VERTICES_3D:
        # Speaking: wave ripples outward from mouth center (0, 48, 68)
        dist_m = math.sqrt(x * x + (y - 48.0) ** 2 + (z - 68.0) ** 2)
        wave = math.sin(dist_m * 0.07 - t * 9.0)

        # Displacements
        dx = ws * (2.8 * wave * (x / 100.0))
        dy = ws * (3.2 * wave * ((y - 48.0) / 100.0))
        dz = ws * (4.0 * wave)

        # Mouth opening cadence during speech
        if abs(x) < 25 and 30 < y < 70:
            dy += ws * (2.5 * abs(math.sin(t * 7.5)))

        vx, vy, vz = x + dx, y + dy, z + dz

        # Rotate around Y then X
        rx = vx * cos_y + vz * sin_y
        rz = -vx * sin_y + vz * cos_y
        ry = vy * cos_x - rz * sin_x
        rz = vy * sin_x + rz * cos_x

        # Perspective projection
        factor = dist / (dist - rz)
        px = cx + rx * factor * base_scale
        py = cy + ry * factor * base_scale
        proj.append((px, py, rz))

        # Thinking data scan glow
        d_scan = abs(y - scan_y)
        scan_glow = wt * max(0.0, 1.0 - d_scan / 38.0)
        node_glow.append(scan_glow)

        # Node radius modulation
        r_base = 1.7
        r_pulse = ws * (1.8 * max(0.0, wave)) + wt * (2.0 * scan_glow) + wl * (0.4 * math.sin(t * 2.0))
        node_radii.append(max(1.2, r_base + r_pulse))

    # 3. Draw Wireframe Edges
    cr.set_line_width(1.2 + 0.3 * ws)
    for i1, i2 in EDGES:
        p1, p2 = proj[i1], proj[i2]
        avg_z = (p1[2] + p2[2]) / 2.0
        base_alpha = max(0.16, min(0.92, 0.48 + avg_z / 150.0))
        e_glow = max(node_glow[i1], node_glow[i2])

        # State color blending
        r = wl * 0.0 + wt * (0.2 + 0.8 * e_glow) + ws * 0.0
        g = wl * 0.85 + wt * (0.75 + 0.25 * e_glow) + ws * 1.0
        b = wl * 1.0 + wt * 1.0 + ws * 0.8
        alpha = min(1.0, base_alpha + e_glow * 0.5)

        cr.set_source_rgba(r, g, b, alpha)
        cr.move_to(p1[0], p1[1])
        cr.line_to(p2[0], p2[1])
        cr.stroke()

    # 4. Draw Wireframe Nodes (Vertices)
    for i, (px, py, rz) in enumerate(proj):
        nr = node_radii[i]
        glow = node_glow[i]
        depth_alpha = max(0.25, min(1.0, 0.60 + rz / 130.0))

        # Glowing Aura
        if glow > 0.08 or ws > 0.3:
            aura_alpha = (glow * 0.6 * wt) + (0.35 * ws)
            cr.set_source_rgba(0.0, 0.95, 1.0, aura_alpha)
            cr.arc(px, py, nr * 2.2, 0, 6.283)
            cr.fill()

        # Core node
        cr_r = wl * 0.85 + wt * (0.85 + 0.15 * glow) + ws * 0.70
        cr_g = wl * 0.95 + wt * 0.95 + ws * 1.00
        cr_b = wl * 1.00 + wt * 1.00 + ws * 0.90
        cr.set_source_rgba(cr_r, cr_g, cr_b, depth_alpha)
        cr.arc(px, py, nr, 0, 6.283)
        cr.fill()

    bio = io.BytesIO()
    surface.write_to_png(bio)
    return bio.getvalue()


# ── GTK Window Builder ──────────────────────────────────────────────────────

def _build_monitor_window(monitor, shared: OverlayState):
    """Build a transparent floating window in the top-right corner."""
    import gi
    gi.require_version("Gdk", "3.0")
    gi.require_version("Gtk", "3.0")
    gi.require_version("GdkPixbuf", "2.0")
    from gi.repository import Gdk, GdkPixbuf, GLib, Gtk

    win = Gtk.Window(type=Gtk.WindowType.TOPLEVEL)
    win.set_title("Lura Hologram")
    win.set_decorated(False)
    win.set_resizable(False)
    win.set_keep_above(True)
    win.stick()
    win.set_skip_taskbar_hint(True)
    win.set_skip_pager_hint(True)
    win.set_accept_focus(False)
    win.set_default_size(OVERLAY_SIZE, OVERLAY_SIZE)
    win.set_type_hint(Gdk.WindowTypeHint.DOCK)
    win.set_app_paintable(True)

    screen = win.get_screen()
    visual = screen.get_rgba_visual()
    if visual:
        win.set_visual(visual)

    # Position in top-right corner of this monitor's work area
    wa = monitor.get_workarea()
    scale = monitor.get_scale_factor()
    x = wa.x + (wa.width // scale) - OVERLAY_SIZE - MARGIN_RIGHT
    y = wa.y + MARGIN_TOP
    win.move(x, y)

    # Completely transparent window CSS
    css_provider = Gtk.CssProvider()
    css = b"""
    window, window.background, .cyber-window {
        background-color: rgba(0, 0, 0, 0);
        background: none;
        border: none;
        box-shadow: none;
    }
    """
    css_provider.load_from_data(css)
    Gtk.StyleContext.add_provider_for_screen(
        screen, css_provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
    )

    # Draggable image container
    ev = Gtk.EventBox()
    ev.set_visible_window(False)
    ev.connect(
        "button-press-event",
        lambda w, e: win.begin_move_drag(e.button, int(e.x_root), int(e.y_root), e.time)
        if e.button == 1 else None,
    )

    img = Gtk.Image()
    ev.add(img)
    win.add(ev)

    blender = StateBlender()
    phase = [0.0]

    def _tick():
        blender.update(shared.state, dt=0.04)
        phase[0] += 0.04
        png_data = _render_frame(phase[0], blender, size=OVERLAY_SIZE)
        loader = GdkPixbuf.PixbufLoader.new_with_type("png")
        loader.write(png_data)
        loader.close()
        pix = loader.get_pixbuf()
        if pix:
            GLib.idle_add(img.set_from_pixbuf, pix)
        return True

    GLib.timeout_add(40, _tick)  # 25 FPS animation loop
    return win


# ── Public API ──────────────────────────────────────────────────────────────

def start_overlay(shared: OverlayState) -> threading.Thread | None:
    """Start the hologram on all active monitors in a daemon thread."""

    def _run():
        try:
            import gi
            gi.require_version("Gdk", "3.0")
            gi.require_version("Gtk", "3.0")
            from gi.repository import Gdk, Gtk

            display = Gdk.Display.get_default()
            if not display or display.get_n_monitors() == 0:
                log.warning("No display found for hologram overlay.")
                return

            windows = []
            for i in range(display.get_n_monitors()):
                monitor = display.get_monitor(i)
                win = _build_monitor_window(monitor, shared)
                win.show_all()
                windows.append(win)

            log.info("Lura Hologram running on %d monitor(s) (top-right corner).", len(windows))
            Gtk.main()
        except Exception:
            log.warning("Hologram overlay unavailable — running headless.", exc_info=True)

    try:
        import gi
        gi.require_version("Gdk", "3.0")
        gi.require_version("Gtk", "3.0")
        from gi.repository import Gdk, Gtk  # noqa: F401
    except Exception:
        log.info("GTK3 not available — overlay disabled.")
        return None

    t = threading.Thread(target=_run, daemon=True, name="overlay")
    t.start()
    return t
