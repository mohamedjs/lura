"""Lura Hologram Overlay — 3D Cyberpunk AI Wireframe Face.

Floating in the top-right corner with zero chrome, border, or background.

Look: hollow low-poly head of electric cyan (#00F0FF) lines with white-hot
vertex cores and a turquoise bloom. Lines burn bright along the facial ridges
(jawline, cheekbones, brow, nose bridge, lips) and fall away to a dim lattice
across the flat cheeks and forehead. Below the chin the mesh dissolves into
drifting particles and broken line fragments.

Seamless looping animation:
1. Entire mesh breathes slowly (±2% pulse over 4 seconds).
2. Bright cyan light wave travels from chin to skull every 3 seconds.
3. Loose particles below neck drift slowly upward and fade out, continuously respawning.
4. Random individual vertex dots flicker brighter (data activity).
5. Head rotates gently left/right by 4 degrees in a slow figure-eight.
- Speaking: mouth aperture tracks live audio amplitude, glow +40%.
- Idle: glow dims to 60%, motion slows.
- Smooth 30fps loop on transparent background.
"""

from __future__ import annotations

import enum
import logging
import math
import threading
import time

from .face_mesh import EDGES, LANDMARK_INDICES, VERTICES_3D

log = logging.getLogger(__name__)

# Key landmark vertex indices for selective highlights
HIGHLIGHT_NODES: set[int] = set(
    LANDMARK_INDICES.get("eyes_left", [])
    + LANDMARK_INDICES.get("eyes_right", [])
    + LANDMARK_INDICES.get("nose", [])
    + LANDMARK_INDICES.get("mouth_upper", [])
    + LANDMARK_INDICES.get("mouth_lower", [])
    + LANDMARK_INDICES.get("chin", [])
    + LANDMARK_INDICES.get("brow", [])
)
# Prompt spec: lines brightest along facial ridges (jawline, cheekbones, brow,
# nose bridge, lips), dim across the flat cheeks/forehead. Precomputed at import.
FEATURE_NODES: set[int] = set(
    LANDMARK_INDICES.get("jawline", [])
    + LANDMARK_INDICES.get("cheekbones", [])
    + LANDMARK_INDICES.get("brow", [])
    + LANDMARK_INDICES.get("nose", [])
    + LANDMARK_INDICES.get("mouth_upper", [])
    + LANDMARK_INDICES.get("mouth_lower", [])
    + LANDMARK_INDICES.get("chin", [])
    + LANDMARK_INDICES.get("eyes_left", [])
    + LANDMARK_INDICES.get("eyes_right", [])
)
FEATURE_WEIGHT: tuple[float, ...] = tuple(
    1.0 if i in FEATURE_NODES else 0.42 for i in range(len(VERTICES_3D))
)
EYE_NODES: set[int] = set(
    LANDMARK_INDICES.get("eyes_left", []) + LANDMARK_INDICES.get("eyes_right", [])
)

MOUTH_UPPER: set[int] = set(LANDMARK_INDICES.get("mouth_upper", []))
MOUTH_LOWER: set[int] = set(LANDMARK_INDICES.get("mouth_lower", []))
CHIN_NODES: set[int] = set(LANDMARK_INDICES.get("chin", []))

# Procedural floating data particles below neck: (x_spread, z_depth, speed, phase)
PARTICLES: list[tuple[float, float, float, float]] = [
    (-48.0, 16.0, 26.0, 0.2), (-32.0, 22.0, 30.0, 0.7), (-16.0, 28.0, 34.0, 1.4),
    (0.0, 32.0, 28.0, 2.1), (16.0, 28.0, 32.0, 0.9), (32.0, 22.0, 27.0, 1.8),
    (48.0, 16.0, 29.0, 2.5), (-56.0, 10.0, 24.0, 0.5), (-40.0, 15.0, 35.0, 1.1),
    (-24.0, 22.0, 27.0, 1.9), (-8.0, 28.0, 33.0, 0.3), (8.0, 28.0, 31.0, 2.7),
    (24.0, 22.0, 25.0, 1.3), (40.0, 15.0, 36.0, 0.8), (56.0, 10.0, 23.0, 2.2),
    (-28.0, 20.0, 29.0, 1.6), (-12.0, 26.0, 28.0, 2.4), (12.0, 26.0, 32.0, 0.4),
    (28.0, 20.0, 33.0, 1.7), (0.0, 24.0, 30.0, 1.2), (-20.0, 18.0, 31.0, 0.6),
    (20.0, 18.0, 28.0, 2.3), (-36.0, 12.0, 25.0, 1.5), (36.0, 12.0, 33.0, 0.1),
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
        self._amp = 0.0
        self._amp_ts = 0.0
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
    def amplitude(self) -> float:
        """Voice loudness 0..1, decaying to silence between audio chunks.

        Chunks arrive far slower than the 60fps render loop, so the value is
        decayed against wall-clock time rather than per read.
        """
        with self._lock:
            if self._amp <= 0.0:
                return 0.0
            age = time.monotonic() - self._amp_ts
            return self._amp * max(0.0, 1.0 - age / 0.22)

    @amplitude.setter
    def amplitude(self, val: float):
        with self._lock:
            self._amp = max(0.0, min(1.0, val))
            self._amp_ts = time.monotonic()

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
        self.wl = 1.0  # listening / idle weight
        self.wt = 0.0  # thinking weight
        self.ws = 0.0  # speaking weight

    def update(self, target_st: State, dt: float = 0.04):
        if target_st in (State.IDLE, State.LISTENING):
            tl, tt, ts = 1.0, 0.0, 0.0
        elif target_st in (State.CONNECTING, State.THINKING):
            tl, tt, ts = 0.0, 1.0, 0.0
        else:  # SPEAKING
            tl, tt, ts = 0.0, 0.0, 1.0

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

OVERLAY_SIZE = 220
MARGIN_RIGHT = 24
MARGIN_TOP = 20


# ── Cairo Procedural 3D Head Renderer ───────────────────────────────────────

# Prompt palette: electric cyan #00F0FF lines, pure white hot vertex cores.
CYAN = (0.0, 240 / 255.0, 1.0)


def _render_frame(
    t: float, blender: StateBlender, size: int = OVERLAY_SIZE, amp: float = 0.0
) -> bytes:
    """Render one frame to PNG bytes (self-check / offline use)."""
    import io

    bio = io.BytesIO()
    _render_surface(t, blender, size, amp).write_to_png(bio)
    return bio.getvalue()


def _render_surface(
    t: float, blender: StateBlender, size: int = OVERLAY_SIZE, amp: float = 0.0
):
    """Render the 3D wireframe head frame to a cairo surface.

    ``amp`` is voice loudness 0..1; it drives the mouth aperture while speaking.
    """
    import cairo

    wl, wt, ws = blender.wl, blender.wt, blender.ws
    surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, size, size)
    cr = cairo.Context(surface)
    cr.set_operator(cairo.OPERATOR_CLEAR)
    cr.paint()
    cr.set_operator(cairo.OPERATOR_OVER)

    cx, cy = size / 2.0, size / 2.0
    dist = 380.0

    # Glow modulation: dims to 60% when idle, baseline thinking 100%, expands +40% speaking
    glow_mult = wl * 0.60 + wt * 1.00 + ws * 1.40

    # Motion (1): Mesh breathes slowly — vertices pulse ±2% over 4 seconds
    breath_scale = 0.02 * math.sin(2.0 * math.pi * t / 4.0)

    # Motion (2): Upward light wave from bottom of chin (120) to skull (-165) every 3 seconds
    wave_y = 120.0 - 285.0 * ((t / 3.0) % 1.0)

    # Motion (5): Figure-eight gentle head rotation (4 degrees = ~0.07 rad)
    theta_y_idle = 0.07 * math.sin(t * 0.8)
    theta_x_idle = 0.035 * math.sin(t * 1.6)

    # State orientation blend
    theta_y_t = 0.45 * math.sin(t * 1.3)
    theta_x_t = 0.10 * math.cos(t * 0.9)
    theta_y_s = 0.08 * math.sin(t * 2.2)
    theta_x_s = 0.04 * math.sin(t * 2.8)

    rot_y = wl * theta_y_idle + wt * theta_y_t + ws * theta_y_s
    # Prompt: head held slightly tilted up 5 degrees (~0.087 rad).
    rot_x = 0.087 + wl * theta_x_idle + wt * theta_x_t + ws * theta_x_s
    base_scale = 0.49 * (1.0 + breath_scale)

    cos_y, sin_y = math.cos(rot_y), math.sin(rot_y)
    cos_x, sin_x = math.cos(rot_x), math.sin(rot_x)

    # Ambient contrast backing + cyan bloom (ensures contrast over bright windows)
    dark_pat = cairo.RadialGradient(cx, cy, 10, cx, cy, 90)
    dark_pat.add_color_stop_rgba(0.0, 0.01, 0.03, 0.08, 0.50 * glow_mult)
    dark_pat.add_color_stop_rgba(0.65, 0.01, 0.03, 0.08, 0.30 * glow_mult)
    dark_pat.add_color_stop_rgba(1.0, 0.0, 0.0, 0.0, 0.0)
    cr.set_source(dark_pat)
    cr.arc(cx, cy, 90, 0, 6.283)
    cr.fill()

    bloom_pat = cairo.RadialGradient(cx, cy, 10, cx, cy, 95)
    bloom_alpha = 0.11 * glow_mult
    bloom_pat.add_color_stop_rgba(0.0, 0.0, 0.85, 1.0, bloom_alpha)
    bloom_pat.add_color_stop_rgba(0.5, 0.0, 0.45, 0.95, bloom_alpha * 0.5)
    bloom_pat.add_color_stop_rgba(1.0, 0.0, 0.10, 0.50, 0.0)
    cr.set_source(bloom_pat)
    cr.arc(cx, cy, 95, 0, 6.283)
    cr.fill()

    # Transform 3D Vertices
    proj: list[tuple[float, float, float]] = []
    node_glow: list[float] = []
    node_radii: list[float] = []

    for idx, (x, y, z) in enumerate(VERTICES_3D):
        cy_off = y + 16.0
        cz_off = z - 28.0

        # Motion (1): radial breathing pulse on individual vertices
        dx = breath_scale * (x / 50.0)
        dy = breath_scale * (cy_off / 70.0)
        dz = breath_scale * 3.0

        # Motion (2): Upward light wave proximity
        d_wave = abs(y - wave_y)
        upward_glow = max(0.0, 1.0 - d_wave / 20.0)

        # Motion (4): Random vertex dots flicker for fraction of second (data activity)
        flicker = 0.75 if ((idx * 37 + int(t * 8.0) * 59) % 19 == 0) else 0.0

        # Speaking dynamics: mouth area triangles expand/contract with voice amplitude
        if ws > 0.04:
            dist_m = math.sqrt(x * x + (y - 42.0) ** 2 + (z - 65.0) ** 2)
            ripple = math.sin(dist_m * 0.07 - t * 8.5)
            dx += ws * (2.2 * ripple * (x / 70.0))
            dy += ws * (2.0 * ripple * ((y - 42.0) / 70.0))
            dz += ws * (4.2 * ripple * max(0.0, 1.0 - dist_m / 150.0))

            # Mouth aperture tracks live audio amplitude; a faint idle tremor
            # keeps it alive if no amplitude is being fed in.
            open_amt = max(amp, 0.12 * abs(math.sin(t * 8.0)))
            if idx in MOUTH_UPPER:
                dy -= ws * 5.0 * open_amt
            elif idx in MOUTH_LOWER or idx in CHIN_NODES:
                dy += ws * 9.0 * open_amt

        vx, vy, vz = x + dx, cy_off + dy, cz_off + dz

        rx = vx * cos_y + vz * sin_y
        rz = -vx * sin_y + vz * cos_y
        ry = vy * cos_x - rz * sin_x
        rz = vy * sin_x + rz * cos_x

        factor = dist / (dist - rz)
        px = cx + rx * factor * base_scale
        py = cy + ry * factor * base_scale
        proj.append((px, py, rz))

        glow = max(upward_glow, flicker)
        node_glow.append(glow)

        is_hl = idx in HIGHLIGHT_NODES
        r_base = 1.3 if is_hl else 0.9
        r_pulse = glow * 1.1 + (0.25 if is_hl else 0.0)
        node_radii.append(max(0.7, r_base + r_pulse))

    # Draw Wireframe Edges (delicate thin lines)
    cr.set_line_cap(cairo.LINE_CAP_ROUND)
    cr.set_line_join(cairo.LINE_JOIN_ROUND)
    cr.set_line_width(1.05 * (0.95 if wl > 0.5 else 1.25))
    for i1, i2 in EDGES:
        p1, p2 = proj[i1], proj[i2]
        avg_z = (p1[2] + p2[2]) / 2.0
        depth_alpha = max(0.20, min(0.92, 0.52 + avg_z / 80.0)) * glow_mult
        e_glow = max(node_glow[i1], node_glow[i2])

        # Feature ridges (jaw, cheekbones, brow, nose, lips) burn bright; the
        # flat cheeks and forehead fall away to a dim lattice.
        feat = 0.5 * (FEATURE_WEIGHT[i1] + FEATURE_WEIGHT[i2])

        r = CYAN[0] + (1.0 - CYAN[0]) * e_glow
        g = CYAN[1] + (1.0 - CYAN[1]) * e_glow
        b = CYAN[2]
        alpha = min(0.98, (depth_alpha + e_glow * 0.45) * (0.30 + 0.70 * feat))

        cr.set_source_rgba(r, g, b, alpha)
        cr.move_to(p1[0], p1[1])
        cr.line_to(p2[0], p2[1])
        cr.stroke()

    # Draw Wireframe Nodes (pinpoint glowing dots)
    for i, (px, py, rz) in enumerate(proj):
        nr = node_radii[i]
        glow = node_glow[i]
        feat = FEATURE_WEIGHT[i]
        depth_alpha = max(0.25, min(1.0, 0.60 + rz / 80.0)) * glow_mult
        depth_alpha *= 0.35 + 0.65 * feat
        is_hl = i in HIGHLIGHT_NODES
        is_eye = i in EYE_NODES

        # Cyan halo + white-hot core (same two-pass as the drift particles).
        if glow > 0.12 or is_eye or (is_hl and ws > 0.2):
            halo_alpha = min(0.75, (glow * 0.5) + (0.25 * ws) + (0.09 if is_eye else 0.0))
            halo_alpha *= glow_mult * (0.4 + 0.6 * feat)
            cr.set_source_rgba(CYAN[0], 0.90, CYAN[2], halo_alpha)
            cr.arc(px, py, nr * (1.8 if is_eye else 2.1), 0, 6.283)
            cr.fill()

        cr.set_source_rgba(1.0, 1.0, 1.0, min(1.0, depth_alpha * (1.25 if is_eye else 1.0)))
        cr.arc(px, py, nr, 0, 6.283)
        cr.fill()

    # Motion (3): Loose particles drifting upward below neck
    for p_i, (px_off, pz_off, speed, phase) in enumerate(PARTICLES):
        y_drift = 142.0 - ((t * speed + phase * 40.0) % 70.0)
        progress = (142.0 - y_drift) / 70.0
        p_alpha = math.sin(progress * math.pi) * 0.65 * glow_mult

        if p_alpha > 0.03:
            x_wobble = px_off + 6.0 * math.sin(t * 1.5 + phase * 6.0)
            vx, vy, vz = x_wobble, y_drift + 16.0, pz_off - 28.0

            rx = vx * cos_y + vz * sin_y
            rz = -vx * sin_y + vz * cos_y
            ry = vy * cos_x - rz * sin_x
            rz = vy * sin_x + rz * cos_x

            factor = dist / (dist - rz)
            ppx = cx + rx * factor * base_scale
            ppy = cy + ry * factor * base_scale

            # Every third one is a broken line fragment rather than a dot —
            # the mesh shearing apart as it dissolves.
            if p_i % 3 == 0:
                ang = phase * 2.4 + t * 0.6
                fl = 3.5 + 2.5 * math.sin(phase * 5.0)
                ex, ey = fl * math.cos(ang), fl * math.sin(ang)
                cr.set_line_width(1.0)
                cr.set_source_rgba(CYAN[0], CYAN[1], CYAN[2], p_alpha * 0.85)
                cr.move_to(ppx - ex, ppy - ey)
                cr.line_to(ppx + ex, ppy + ey)
                cr.stroke()
                continue

            cr.set_source_rgba(CYAN[0], 0.85, CYAN[2], p_alpha * 0.40)
            cr.arc(ppx, ppy, 2.2, 0, 6.283)
            cr.fill()

            cr.set_source_rgba(1.0, 1.0, 1.0, p_alpha)
            cr.arc(ppx, ppy, 0.95, 0, 6.283)
            cr.fill()

    return surface


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

    # ponytail: frames go out through a PNG encode/decode (~11ms/frame, still
    # inside the 16ms budget for one monitor). Painting the cairo surface
    # straight into a Gtk.DrawingArea is ~4ms, but needs the python3-gi-cairo
    # foreign-struct converter, which isn't installed here. Switch if the
    # multi-monitor frame rate ever matters.
    def _tick():
        blender.update(shared.state, dt=0.033)
        # Idle motion slows to 60%; scale the increment so the phase stays
        # monotonic and the breath/wave never jump across a state blend.
        phase[0] += 0.033 * (0.60 * blender.wl + blender.wt + blender.ws)
        png_data = _render_frame(
            phase[0], blender, size=OVERLAY_SIZE, amp=shared.amplitude
        )
        loader = GdkPixbuf.PixbufLoader.new_with_type("png")
        loader.write(png_data)
        loader.close()
        pix = loader.get_pixbuf()
        if pix:
            # GLib.idle_add, not a direct call: Gtk.main() runs in a daemon
            # thread here, and setting the pixbuf inline leaves the overlay
            # blank on screen.
            GLib.idle_add(img.set_from_pixbuf, pix)
        return True

    # ponytail: 30fps, not 60. A frame costs ~11ms to render and PNG-encode,
    # and one timeout runs per monitor — at 16ms the main loop saturates and
    # GTK never gets to repaint, so the overlay shows up blank. 33ms leaves
    # slack for the redraw. For a true 60fps, install python3-gi-cairo and
    # paint the cairo surface straight into a Gtk.DrawingArea (~4ms/frame).
    GLib.timeout_add(33, _tick)
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


# ── Self-check ──────────────────────────────────────────────────────────────

def _selfcheck() -> None:
    """Catch bad indices / render crashes, which the GTK loop swallows silently."""
    n = len(VERTICES_3D)
    for name, idxs in LANDMARK_INDICES.items():
        for i in idxs:
            assert 0 <= i < n, f"{name}: vertex {i} out of range ({n} verts)"
    for i1, i2 in EDGES:
        assert 0 <= i1 < n and 0 <= i2 < n, f"edge ({i1},{i2}) out of range"
    assert len(FEATURE_WEIGHT) == n
    assert FEATURE_NODES, "no feature ridges — every line would render dim"

    st = OverlayState()
    assert st.amplitude == 0.0
    st.amplitude = 2.0
    assert 0.9 < st.amplitude <= 1.0, "amplitude must clamp to 1.0"

    for target in (State.LISTENING, State.THINKING, State.SPEAKING):
        b = StateBlender()
        for _ in range(60):
            b.update(target, dt=0.016)
        for amp in (0.0, 1.0):
            png = _render_frame(1.7, b, size=OVERLAY_SIZE, amp=amp)
            assert png.startswith(b"\x89PNG"), f"{target} amp={amp}: not a PNG"
            assert len(png) > 1000, f"{target} amp={amp}: frame looks empty"
    print("overlay self-check OK")


if __name__ == "__main__":
    _selfcheck()
