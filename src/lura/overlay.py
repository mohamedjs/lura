"""Lura Hologram Overlay — shaded 3D face on a transparent background.

Floating in the top-right corner with zero chrome, border, or background.

Look (matches the promo video's head): the MediaPipe face mesh (468 vertices,
898 triangles) drawn as indigo wireframe over lavender facets lit from the
upper left, glowing teal eyes, teal lips and jaw and a dotted cyan orbit ring;
nothing else: the window background is fully transparent.

Animation:
- The head sways slowly (yaw/pitch), eyes blink, a light band sweeps down.
- Listening: calm, dimmer glow.
- Thinking: the head turns further and the light band speeds up.
- Speaking: the jaw drops with live voice amplitude and the mouth glows.
- 30fps on a transparent window.
"""

from __future__ import annotations

import enum
import logging
import math
import threading
import time

import numpy as np

from .face_mp import TRIANGLES, VERTICES

log = logging.getLogger(__name__)


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

OVERLAY_SIZE = 260
MARGIN_RIGHT = 24
MARGIN_TOP = 20


# ── Face model, precomputed once ────────────────────────────────────────────

V0 = np.asarray(VERTICES, dtype=np.float64)          # (468, 3), ~2 units tall
TRI = np.asarray(TRIANGLES, dtype=np.int64)          # (898, 3)


def _smooth(a: float, b: float, x):
    t = np.clip((x - a) / (b - a), 0.0, 1.0)
    return t * t * (3.0 - 2.0 * t)


def _gauss(x, s: float):
    return np.exp(-(x * x) / (2.0 * s * s))


# Unique edges plus, for each, the triangles that share it.
def _edges():
    seen: dict[tuple[int, int], int] = {}
    pairs: list[tuple[int, int]] = []
    owner_e: list[int] = []
    owner_t: list[int] = []
    for ti, (a, b, c) in enumerate(TRIANGLES):
        for p, q in ((a, b), (b, c), (c, a)):
            k = (p, q) if p < q else (q, p)
            if k not in seen:
                seen[k] = len(pairs)
                pairs.append(k)
            owner_e.append(seen[k])
            owner_t.append(ti)
    return np.asarray(pairs, np.int64), np.asarray(owner_e), np.asarray(owner_t)


EDGES, _EDGE_OWN, _TRI_OWN = _edges()

# Landmarks (MediaPipe indices).
LIP_UP, LIP_LO = V0[13, 1], V0[14, 1]
EYES = np.array([V0[[33, 133, 159, 145]].mean(0), V0[[263, 362, 386, 374]].mean(0)])
EYES[:, 2] += 0.02
INNER_LIP = [78, 95, 88, 178, 87, 14, 317, 402, 318, 324, 308,
             415, 310, 311, 312, 13, 82, 81, 80, 191]
OUTER_LIP = [61, 146, 91, 181, 84, 17, 314, 405, 321, 375, 291,
             409, 270, 269, 267, 0, 37, 39, 40, 185]

# How far each vertex follows the jaw when the mouth opens.
JAW = _smooth(LIP_UP - 0.01, LIP_LO + 0.005, V0[:, 1]) * _gauss(V0[:, 0], 0.42)
# Lines below the mouth turn teal (the jaw/chin in the reference look).
_EDGE_Y = V0[EDGES, 1].mean(1)
EDGE_TEAL = _smooth(LIP_LO - 0.02, LIP_LO - 0.30, _EDGE_Y)

# Sparse dotted skull shell behind the mask.
_shell = []
for i in range(22):
    for j in range(44):
        th, ph = (i + 0.5) / 22 * math.pi, j / 44 * math.pi * 2
        p = (math.sin(th) * math.sin(ph) * 0.86, math.cos(th) * 1.02 + 0.14,
             math.sin(th) * math.cos(ph) * 1.0 - 0.32)
        if (p[2] > 0.05 and p[1] < 0.95) or p[1] < -0.55:
            continue
        _shell.append(p)
SHELL = np.asarray(_shell)

_ring_an = np.arange(0, 80) / 80.0 * math.pi * 2

_LIGHT = np.array([-0.5, 0.55, 0.68])
_LIGHT /= np.linalg.norm(_LIGHT)

# Palette (taken from the reference frame).
INDIGO = (0.44, 0.41, 0.90)       # wireframe
LAVENDER = (0.62, 0.60, 0.94)     # facet shading
TEAL = (0.05, 0.68, 0.80)         # eyes, lips, jaw lines
CYAN = (0.13, 0.75, 0.88)         # orbit ring, light band


def _rot(p: np.ndarray, yaw: float, pitch: float) -> np.ndarray:
    cy, sy, cp, sp = math.cos(yaw), math.sin(yaw), math.cos(pitch), math.sin(pitch)
    x, y, z = p[..., 0], p[..., 1], p[..., 2]
    x2 = x * cy + z * sy
    z2 = -x * sy + z * cy
    y3 = y * cp - z2 * sp
    z3 = y * sp + z2 * cp
    return np.stack([x2, y3, z3], -1)


# ── Cairo renderer ──────────────────────────────────────────────────────────

def _render_frame(
    t: float, blender: StateBlender, size: int = OVERLAY_SIZE, amp: float = 0.0
) -> bytes:
    """Render one frame to PNG bytes."""
    import io

    bio = io.BytesIO()
    _render_surface(t, blender, size, amp).write_to_png(bio)
    return bio.getvalue()


def _surface_rgba(surface) -> bytes:
    """Cairo ARGB32 (premultiplied, BGRA in memory) -> straight RGBA bytes.

    Feeding these to GdkPixbuf skips a PNG encode/decode, which costs more
    than drawing the whole face.
    """
    surface.flush()
    w, h, stride = surface.get_width(), surface.get_height(), surface.get_stride()
    buf = np.frombuffer(surface.get_data(), np.uint8).reshape(h, stride)[:, : w * 4]
    px = buf.reshape(h, w, 4).astype(np.uint16)
    a = px[..., 3:4]
    rgb = np.where(a > 0, (px[..., 2::-1] * 255 + a // 2) // np.maximum(a, 1), 0)
    out = np.concatenate([np.minimum(rgb, 255), a], -1).astype(np.uint8)
    return out.tobytes()


def _bucket_stroke(cr, segs, alphas, rgb, levels=7):
    """Stroke many segments with few cairo calls by grouping alpha levels."""
    if len(segs) == 0:
        return
    q = np.clip((alphas * levels).astype(int), 0, levels)
    for lv in range(1, levels + 1):
        sel = segs[q == lv].tolist()
        if not sel:
            continue
        for x1, y1, x2, y2 in sel:
            cr.move_to(x1, y1)
            cr.line_to(x2, y2)
        cr.set_source_rgba(*rgb, lv / levels)
        cr.stroke()


def _render_surface(
    t: float, blender: StateBlender, size: int = OVERLAY_SIZE, amp: float = 0.0
):
    """Draw one frame of the head. ``amp`` (0..1 voice loudness) opens the jaw."""
    import cairo

    wl, wt, ws = blender.wl, blender.wt, blender.ws
    surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, size, size)
    cr = cairo.Context(surface)
    cr.set_operator(cairo.OPERATOR_CLEAR)
    cr.paint()
    cr.set_operator(cairo.OPERATOR_OVER)
    cr.set_line_cap(cairo.LINE_CAP_ROUND)
    cr.set_line_join(cairo.LINE_JOIN_ROUND)

    cx, cy = size * 0.5, size * 0.47
    fs = size * 0.26                                   # model unit -> pixels
    glow = 0.65 * wl + 1.0 * wt + 1.25 * ws

    yaw = 0.26 * math.sin(t * 0.45) + wt * 0.22 * math.sin(t * 1.3)
    pitch = 0.07 * math.sin(t * 0.31) - 0.06
    mouth = ws * max(amp, 0.10 * abs(math.sin(t * 7.0)))
    scan_y = 1.25 - (((t * (0.55 + 0.6 * wt)) % 2.0) / 2.0) * 2.7

    def proj(v):
        k = 3.4 / (3.4 - v[..., 2])
        return np.stack([cx + v[..., 0] * fs * k, cy - v[..., 1] * fs * k], -1)

    # Deform (jaw) and transform the mesh.
    V = V0.copy()
    V[:, 1] -= JAW * mouth * 0.11
    V[:, 2] -= JAW * mouth * 0.02
    VV = _rot(V, yaw, pitch)
    S = proj(VV)

    # Per-triangle lighting.
    A, B, C = VV[TRI[:, 0]], VV[TRI[:, 1]], VV[TRI[:, 2]]
    n = np.cross(B - A, C - A)
    n /= np.linalg.norm(n, axis=1, keepdims=True) + 1e-9
    n[n[:, 2] < 0] *= -1.0
    lam = np.clip(n @ _LIGHT, 0.0, 1.0)
    TL = np.clip(0.05 + 0.95 * lam ** 1.6 + 0.5 * (1.0 - n[:, 2]) ** 4, 0.0, 1.0)

    # Skull shell dots.
    sv = _rot(SHELL, yaw, pitch)
    front = sv[:, 2] > -0.2
    sp = proj(sv[front])
    sal = (0.10 + 0.25 * _smooth(-0.2, 0.6, sv[front, 2])
           + 0.45 * _gauss(SHELL[front, 1] - scan_y, 0.04))
    q = np.clip((sal * 4).astype(int), 1, 4)
    for lv in range(1, 5):
        for x, y in sp[q == lv].tolist():
            cr.rectangle(x - 0.8, y - 0.8, 1.6, 1.6)
        cr.set_source_rgba(*LAVENDER, lv / 4 * 0.8)
        cr.fill()

    # Shaded facets: a translucent lavender skin that reads on dark and light wallpapers.
    shade = 0.10 + 0.16 * (1.0 - TL) + 0.12 * TL
    qf = np.clip((shade * 10).astype(int), 0, 10)
    tri_xy = S[TRI].reshape(-1, 6)
    for lv in range(1, 11):
        sel = tri_xy[qf == lv].tolist()
        if not sel:
            continue
        for x1, y1, x2, y2, x3, y3 in sel:
            cr.move_to(x1, y1)
            cr.line_to(x2, y2)
            cr.line_to(x3, y3)
            cr.close_path()
        cr.set_source_rgba(*LAVENDER, lv / 10)
        cr.fill()

    # Wireframe: indigo, teal below the mouth, cyan where the light band passes.
    el = np.zeros(len(EDGES))
    np.maximum.at(el, _EDGE_OWN, TL[_TRI_OWN])
    band = np.maximum(_gauss(V0[EDGES[:, 0], 1] - scan_y, 0.022),
                      _gauss(V0[EDGES[:, 1], 1] - scan_y, 0.022))
    alpha = np.clip(0.42 + 0.30 * (1.0 - el) + 0.15 * el, 0, 0.9)
    segs = np.concatenate([S[EDGES[:, 0]], S[EDGES[:, 1]]], 1)
    teal = EDGE_TEAL > 0.5
    hot = band > 0.5
    cr.set_line_width(0.75)
    _bucket_stroke(cr, segs[~teal & ~hot], alpha[~teal & ~hot], INDIGO)
    _bucket_stroke(cr, segs[teal & ~hot], np.clip(alpha[teal & ~hot] + 0.15, 0, 1), TEAL)
    cr.set_line_width(1.1)
    _bucket_stroke(cr, segs[hot], np.clip(0.55 * band[hot] * glow, 0, 1), CYAN)

    # Vertex dots on the front-facing half.
    d = _smooth(-0.3, 0.8, VV[:, 2])
    for x, y in S[d > 0.55].tolist():
        cr.rectangle(x - 0.7, y - 0.7, 1.4, 1.4)
    cr.set_source_rgba(*INDIGO, 0.55)
    cr.fill()

    # Lips: teal outline, dark glowing mouth opening while speaking.
    cr.set_line_width(1.2)
    for loop, al in ((OUTER_LIP, 0.75), (INNER_LIP, 0.9)):
        pts = S[loop].tolist()
        cr.move_to(*pts[0])
        for p in pts[1:]:
            cr.line_to(*p)
        cr.close_path()
        cr.set_source_rgba(*TEAL, al)
        if loop is INNER_LIP and mouth > 0.03:
            cr.stroke_preserve()
            cr.set_source_rgba(0.0, 0.32, 0.42, min(0.6, 0.2 + 0.5 * mouth))
            cr.fill()
        else:
            cr.stroke()

    # Eyes: teal glow, blinking.
    blink = 1.0 - max(0.0, math.sin(t * 0.9 + 1.3)) ** 80
    for e in _rot(EYES, yaw, pitch):
        ex, ey = proj(e).tolist()
        rx = fs * 0.09
        ry = max(0.8, rx * 0.6 * blink)
        g = cairo.RadialGradient(ex, ey, 0, ex, ey, rx)
        g.add_color_stop_rgba(0.0, *TEAL, min(1.0, 0.85 + 0.15 * glow))
        g.add_color_stop_rgba(0.45, *TEAL, 0.55)
        g.add_color_stop_rgba(1.0, *TEAL, 0.0)
        cr.save()
        cr.translate(ex, ey)
        cr.scale(1.0, ry / rx)
        cr.translate(-ex, -ey)
        cr.set_source(g)
        cr.arc(ex, ey, rx, 0, 2 * math.pi)
        cr.fill()
        cr.restore()

    # Dotted orbit ring around the head, back half dimmer.
    an = _ring_an + t * 0.5
    ring = np.stack([np.cos(an) * 1.5, np.full_like(an, -0.05), np.sin(an) * 1.5], -1)
    rv = _rot(ring, yaw * 0.4, 0.22 + pitch)
    rp = proj(rv)
    for back in (True, False):
        sel = (rv[:, 2] <= 0) if back else (rv[:, 2] > 0)
        for x, y in rp[sel].tolist():
            cr.arc(x, y, 1.15, 0, 2 * math.pi)
            cr.new_sub_path()
        cr.set_source_rgba(*CYAN, (0.25 if back else 0.75) * min(1.0, glow))
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

    # Frames go to GdkPixbuf as raw RGBA (see _surface_rgba): drawing is
    # ~9ms, and a PNG round-trip of this detailed face would add ~15ms.
    def _tick():
        blender.update(shared.state, dt=0.033)
        # Idle motion slows to 60%; scale the increment so the phase stays
        # monotonic and the breath/wave never jump across a state blend.
        phase[0] += 0.033 * (0.60 * blender.wl + blender.wt + blender.ws)
        surf = _render_surface(
            phase[0], blender, size=OVERLAY_SIZE, amp=shared.amplitude
        )
        pix = GdkPixbuf.Pixbuf.new_from_bytes(
            GLib.Bytes.new(_surface_rgba(surf)), GdkPixbuf.Colorspace.RGB,
            True, 8, OVERLAY_SIZE, OVERLAY_SIZE, OVERLAY_SIZE * 4,
        )
        # GLib.idle_add, not a direct call: Gtk.main() runs in a daemon
        # thread here, and setting the pixbuf inline leaves the overlay
        # blank on screen.
        GLib.idle_add(img.set_from_pixbuf, pix)
        return True

    # ponytail: 30fps, not 60. A frame costs ~11ms to render and convert,
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
    n = len(V0)
    assert n == 468 and len(TRI) == 898, "unexpected face mesh size"
    assert TRI.min() >= 0 and TRI.max() < n, "triangle index out of range"
    assert EDGES.max() < n

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
