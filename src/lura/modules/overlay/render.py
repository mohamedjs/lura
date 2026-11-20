"""The hologram face, drawn with cairo — shared by every window backend.

Look (approved; matches the promo video's head): the MediaPipe face mesh (468
vertices, 898 triangles) as an indigo wireframe over translucent lavender
facets lit from the upper left, glowing teal blinking eyes, teal lips and jaw
and a cyan light band sweeping down. Nothing else:
the background is fully transparent.

Performance: ~9 ms draw + ~2 ms conversion per frame. Frames leave as raw
RGBA (``surface_rgba``), never a PNG round-trip, which cost ~15 ms/frame for
this mesh. Cairo calls are batched by alpha bucket and the loops iterate
``.tolist()``, not numpy scalars.
"""

from __future__ import annotations

import math

import numpy as np

from .face_mp import TRIANGLES, VERTICES
from .looks import THEMES, Look, Palette
from .state import AirView, StateBlender

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

# Palette (taken from the reference frame) — the "lura" theme; others in looks.py.
INDIGO, LAVENDER, TEAL, CYAN = THEMES["lura"]


def _rot(p: np.ndarray, yaw: float, pitch: float) -> np.ndarray:
    cy, sy, cp, sp = math.cos(yaw), math.sin(yaw), math.cos(pitch), math.sin(pitch)
    x, y, z = p[..., 0], p[..., 1], p[..., 2]
    x2 = x * cy + z * sy
    z2 = -x * sy + z * cy
    y3 = y * cp - z2 * sp
    z3 = y * sp + z2 * cp
    return np.stack([x2, y3, z3], -1)




# ── Cairo renderer ──────────────────────────────────────────────────────────

def render_png(t: float, blender: StateBlender, size: int = OVERLAY_SIZE, amp: float = 0.0,
               look: Look | None = None) -> bytes:
    """Render one frame to PNG bytes."""
    import io

    bio = io.BytesIO()
    render_surface(t, blender, size, amp, look).write_to_png(bio)
    return bio.getvalue()


def surface_rgba(surface) -> bytes:
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


def _close_gaps(on: np.ndarray, gap: int) -> np.ndarray:
    """Fill runs of up to ``gap`` empty cells lying between two drawn ones, along rows."""
    out = on.copy()
    for r, row in enumerate(on.tolist()):
        idx = [c for c, v in enumerate(row) if v]
        for c0, c1 in zip(idx, idx[1:]):
            if 1 < c1 - c0 <= gap + 1:
                out[r, c0:c1] = True
    return out


def hit_rects(surface, cell: int = 12, threshold: int = 24, grow: int = 0, gap: int = 4) -> tuple:
    """Rectangles (x, y, w, h) over the cells that hold visible pixels.

    The window takes clicks only there; everywhere else they reach whatever is
    behind it, so the transparent square around Lura never blocks the desktop.
    Small holes (a wireframe's see-through gaps, up to ``gap`` cells) are filled
    so the whole figure is one solid handle to drag. ``grow`` widens the cells
    by that many on every side.
    """
    surface.flush()
    w, h, stride = surface.get_width(), surface.get_height(), surface.get_stride()
    a = np.frombuffer(surface.get_data(), np.uint8).reshape(h, stride)[:, 3: w * 4: 4]
    hc, wc = h // cell, w // cell
    on = a[: hc * cell, : wc * cell].reshape(hc, cell, wc, cell).max((1, 3)) > threshold
    if gap:
        on = _close_gaps(_close_gaps(on, gap).T, gap).T
    for _ in range(grow):
        g = on.copy()
        g[1:] |= on[:-1]
        g[:-1] |= on[1:]
        g[:, 1:] |= on[:, :-1]
        g[:, :-1] |= on[:, 1:]
        on = g
    rects = []
    for r, row in enumerate(on.tolist()):
        c = 0
        while c < wc:
            if row[c]:
                c0 = c
                while c < wc and row[c]:
                    c += 1
                rects.append((c0 * cell, r * cell, (c - c0) * cell, cell))
            c += 1
    return tuple(rects)


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


def _projector(cx: float, cy: float, fs: float, cam: float = 3.4):
    def proj(v):
        k = cam / (cam - v[..., 2])
        return np.stack([cx + v[..., 0] * fs * k, cy - v[..., 1] * fs * k], -1)
    return proj


def render_surface(t: float, blender: StateBlender, size: int = OVERLAY_SIZE, amp: float = 0.0,
                   look: Look | None = None, tools: list[tuple[str, float]] | None = None,
                   air: AirView | None = None):
    """Draw one frame in the chosen style. ``amp`` (0..1 voice loudness) drives the mouth / pulse;
    ``tools`` are (connector, seconds since used) shown as badges."""
    import cairo

    look = look or Look()
    surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, size, size)
    cr = cairo.Context(surface)
    cr.set_operator(cairo.OPERATOR_CLEAR)
    cr.paint()
    cr.set_operator(cairo.OPERATOR_OVER)
    cr.set_line_cap(cairo.LINE_CAP_ROUND)
    cr.set_line_join(cairo.LINE_JOIN_ROUND)
    if look.style == "bust":
        _bust_style(cr, t, blender, size, amp, look.palette, look.figure)
    else:
        _STYLES.get(look.style, _face_style)(cr, t, blender, size, amp, look.palette)
    if tools:
        _tool_badges(cr, size, look.palette, tools, t, _ORIGIN.get(look.style, (0.5, 0.5)))
    if air is not None and air.on:
        _air_hud(cr, size, look.palette, air, t)
    return surface


def _motion(t: float, b: StateBlender, amp: float):
    """Head turn, mouth opening and glow shared by the styles with a face."""
    glow = 0.65 * b.wl + 1.0 * b.wt + 1.25 * b.ws
    yaw = 0.26 * math.sin(t * 0.45) + b.wt * 0.22 * math.sin(t * 1.3)
    pitch = 0.07 * math.sin(t * 0.31) - 0.06
    mouth = b.ws * max(amp, 0.10 * abs(math.sin(t * 7.0)))
    return glow, yaw, pitch, mouth


# ── style: the face ─────────────────────────────────────────────────────────

def _face_style(cr, t, b, size, amp, pal: Palette):
    glow, yaw, pitch, mouth = _motion(t, b, amp)
    scan_y = 1.25 - (((t * (0.55 + 0.6 * b.wt)) % 2.0) / 2.0) * 2.7
    _face(cr, t, size / OVERLAY_SIZE, pal, _projector(size * 0.5, size * 0.47, size * 0.26),
          size * 0.26, yaw, pitch, scan_y, glow, mouth)


def _face(cr, t, u, pal: Palette, proj, fs, yaw, pitch, scan_y, glow, mouth):
    """The MediaPipe head. ``u`` scales line widths and dots with the frame size."""
    import cairo

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
    d1 = 0.8 * u
    for lv in range(1, 5):
        for x, y in sp[q == lv].tolist():
            cr.rectangle(x - d1, y - d1, 2 * d1, 2 * d1)
        cr.set_source_rgba(*pal.fill, lv / 4 * 0.8)
        cr.fill()

    # Shaded facets: a translucent skin that reads on dark and light wallpapers.
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
        cr.set_source_rgba(*pal.fill, lv / 10)
        cr.fill()

    # Wireframe: wire colour, accent below the mouth, glow where the light band passes.
    el = np.zeros(len(EDGES))
    np.maximum.at(el, _EDGE_OWN, TL[_TRI_OWN])
    band = np.maximum(_gauss(V0[EDGES[:, 0], 1] - scan_y, 0.022),
                      _gauss(V0[EDGES[:, 1], 1] - scan_y, 0.022))
    alpha = np.clip(0.42 + 0.30 * (1.0 - el) + 0.15 * el, 0, 0.9)
    segs = np.concatenate([S[EDGES[:, 0]], S[EDGES[:, 1]]], 1)
    teal = EDGE_TEAL > 0.5
    hot = band > 0.5
    cr.set_line_width(0.75 * u)
    _bucket_stroke(cr, segs[~teal & ~hot], alpha[~teal & ~hot], pal.wire)
    _bucket_stroke(cr, segs[teal & ~hot], np.clip(alpha[teal & ~hot] + 0.15, 0, 1), pal.accent)
    cr.set_line_width(1.1 * u)
    _bucket_stroke(cr, segs[hot], np.clip(0.55 * band[hot] * glow, 0, 1), pal.glow)

    # Vertex dots on the front-facing half.
    d = _smooth(-0.3, 0.8, VV[:, 2])
    d2 = 0.7 * u
    for x, y in S[d > 0.55].tolist():
        cr.rectangle(x - d2, y - d2, 2 * d2, 2 * d2)
    cr.set_source_rgba(*pal.wire, 0.55)
    cr.fill()

    # Lips: accent outline, dark glowing mouth opening while speaking.
    cr.set_line_width(1.2 * u)
    for loop, al in ((OUTER_LIP, 0.75), (INNER_LIP, 0.9)):
        pts = S[loop].tolist()
        cr.move_to(*pts[0])
        for p in pts[1:]:
            cr.line_to(*p)
        cr.close_path()
        cr.set_source_rgba(*pal.accent, al)
        if loop is INNER_LIP and mouth > 0.03:
            cr.stroke_preserve()
            cr.set_source_rgba(*(c * 0.5 for c in pal.accent), min(0.6, 0.2 + 0.5 * mouth))
            cr.fill()
        else:
            cr.stroke()

    # Eyes: accent glow, blinking.
    blink = 1.0 - max(0.0, math.sin(t * 0.9 + 1.3)) ** 80
    for e in _rot(EYES, yaw, pitch):
        ex, ey = proj(e).tolist()
        rx = fs * 0.09
        ry = max(0.8, rx * 0.6 * blink)
        g = cairo.RadialGradient(ex, ey, 0, ex, ey, rx)
        g.add_color_stop_rgba(0.0, *pal.accent, min(1.0, 0.85 + 0.15 * glow))
        g.add_color_stop_rgba(0.45, *pal.accent, 0.55)
        g.add_color_stop_rgba(1.0, *pal.accent, 0.0)
        cr.save()
        cr.translate(ex, ey)
        cr.scale(1.0, ry / rx)
        cr.translate(-ex, -ey)
        cr.set_source(g)
        cr.arc(ex, ey, rx, 0, 2 * math.pi)
        cr.fill()
        cr.restore()


# ── style: the orb (a Siri-like ball of light) ──────────────────────────────

_ORB_TH = np.linspace(0.0, 2.0 * math.pi, 97)


def _orb_style(cr, t, b, size, amp, pal: Palette):
    import cairo

    u = size / OVERLAY_SIZE
    cx = cy = size * 0.5
    energy = 0.25 * b.wl + 0.6 * b.wt + b.ws * (0.45 + 1.1 * amp)
    r = size * 0.27 * (1.0 + 0.035 * math.sin(t * 1.6) + 0.08 * b.ws * amp)

    # Soft halo.
    g = cairo.RadialGradient(cx, cy, r * 0.7, cx, cy, r * 1.75)
    g.add_color_stop_rgba(0.0, *pal.glow, 0.22 + 0.25 * energy)
    g.add_color_stop_rgba(1.0, *pal.glow, 0.0)
    cr.set_source(g)
    cr.arc(cx, cy, r * 1.75, 0, 2 * math.pi)
    cr.fill()

    # A tinted glass body, so it reads on light wallpapers too.
    g = cairo.RadialGradient(cx - r * 0.25, cy - r * 0.3, r * 0.1, cx, cy, r)
    g.add_color_stop_rgba(0.0, *pal.fill, 0.18)
    g.add_color_stop_rgba(1.0, *pal.wire, 0.55)
    cr.set_source(g)
    cr.arc(cx, cy, r, 0, 2 * math.pi)
    cr.fill()

    # Flowing colour ribbons inside, added together like light.
    cr.save()
    cr.arc(cx, cy, r * 0.985, 0, 2 * math.pi)
    cr.clip()
    cr.set_operator(cairo.OPERATOR_ADD)
    th = _ORB_TH
    layers = ((pal.accent, 0.9, 3), (pal.wire, -0.7, 2), (pal.glow, 1.3, 4), (pal.fill, -1.1, 5))
    for i, (rgb, speed, k) in enumerate(layers):
        ph = t * speed * (1.0 + 1.2 * b.wt) + i * 1.7
        wob = (0.05 + 0.20 * energy) * (0.6 * np.sin(k * th + ph) + 0.4 * np.sin((k + 1) * th - 1.3 * ph + i))
        rr = r * (0.62 + 0.07 * i + wob)
        ox = r * 0.16 * math.cos(ph * 0.6 + i)
        oy = r * 0.16 * math.sin(ph * 0.5 - i)
        pts = np.stack([cx + ox + rr * np.cos(th), cy + oy + rr * np.sin(th)], -1).tolist()
        cr.move_to(*pts[0])
        for p in pts[1:]:
            cr.line_to(*p)
        cr.close_path()
        g = cairo.RadialGradient(cx + ox, cy + oy, 0, cx + ox, cy + oy, r * 0.9)
        g.add_color_stop_rgba(0.0, *rgb, 0.50)
        g.add_color_stop_rgba(1.0, *rgb, 0.04)
        cr.set_source(g)
        cr.fill()
    cr.restore()

    # Rim and a glassy highlight.
    cr.set_line_width(1.3 * u)
    cr.set_source_rgba(*pal.glow, 0.55 + 0.3 * min(1.0, energy))
    cr.arc(cx, cy, r, 0, 2 * math.pi)
    cr.stroke()
    hx, hy = cx - r * 0.38, cy - r * 0.42
    g = cairo.RadialGradient(hx, hy, 0, hx, hy, r * 0.35)
    g.add_color_stop_rgba(0.0, 1, 1, 1, 0.45)
    g.add_color_stop_rgba(1.0, 1, 1, 1, 0.0)
    cr.set_source(g)
    cr.arc(hx, hy, r * 0.35, 0, 2 * math.pi)
    cr.fill()

    # Thinking: a comet circling the orb.
    if b.wt > 0.02:
        a0 = t * 3.2
        cr.set_line_width(2.4 * u)
        for j in range(6):
            cr.set_source_rgba(*pal.glow, b.wt * (0.85 - j * 0.13))
            cr.arc(cx, cy, r * 1.22, a0 - (j + 1) * 0.16, a0 - j * 0.16)
            cr.stroke()
    # Speaking: ripples going out with the voice.
    if b.ws > 0.02:
        for j in range(2):
            f = ((t * 0.9) + j * 0.5) % 1.0
            cr.set_line_width(1.6 * u)
            cr.set_source_rgba(*pal.accent, b.ws * (0.25 + 0.6 * amp) * (1.0 - f))
            cr.arc(cx, cy, r * (1.05 + 0.5 * f), 0, 2 * math.pi)
            cr.stroke()


# ── style: the ring (a circular equaliser) ──────────────────────────────────

_RING_N = 72
_RING_AN = np.arange(_RING_N) / _RING_N * 2.0 * math.pi


def _ring_style(cr, t, b, size, amp, pal: Palette):
    import cairo

    u = size / OVERLAY_SIZE
    cx = cy = size * 0.5
    R = size * 0.27
    an = _RING_AN
    lev = (0.05 + 0.08 * b.wl * (0.5 + 0.5 * np.sin(an * 3 + t * 1.2))
           + b.wt * 0.18 * (0.5 + 0.5 * np.sin(an * 2 - t * 5.0))
           + b.ws * (0.08 + 0.5 * amp) * (0.35 + 0.65 * np.abs(np.sin(an * 5 + t * 6.0) * np.sin(an * 2 - t * 3.1))))

    # Faint disc and inner line.
    g = cairo.RadialGradient(cx, cy, 0, cx, cy, R)
    g.add_color_stop_rgba(0.0, *pal.fill, 0.0)
    g.add_color_stop_rgba(1.0, *pal.fill, 0.28)
    cr.set_source(g)
    cr.arc(cx, cy, R, 0, 2 * math.pi)
    cr.fill()
    cr.set_line_width(1.0 * u)
    cr.set_source_rgba(*pal.wire, 0.75)
    cr.arc(cx, cy, R * 0.9, 0, 2 * math.pi)
    cr.stroke()

    # The bars.
    c, s = np.cos(an), np.sin(an)
    r0, r1 = R, R * (1.0 + lev)
    segs = np.stack([cx + c * r0, cy + s * r0, cx + c * r1, cy + s * r1], -1)
    cr.set_line_width(max(1.5, 2.0 * math.pi * R / _RING_N * 0.55))
    _bucket_stroke(cr, segs, np.clip(0.45 + 1.4 * lev, 0, 1), pal.accent)

    # Dotted orbit, and the thinking comet.
    cr.set_source_rgba(*pal.glow, 0.6)
    for a in (_ring_an[::2] + t * 0.4).tolist():
        cr.arc(cx + math.cos(a) * R * 1.55, cy + math.sin(a) * R * 1.55, 1.1 * u, 0, 2 * math.pi)
        cr.new_sub_path()
    cr.fill()
    if b.wt > 0.02:
        a0 = t * 3.2
        cr.set_line_width(2.4 * u)
        for j in range(6):
            cr.set_source_rgba(*pal.glow, b.wt * (0.85 - j * 0.13))
            cr.arc(cx, cy, R * 0.78, a0 - (j + 1) * 0.16, a0 - j * 0.16)
            cr.stroke()


# ── style: the bust (a holographic digital human) ───────────────────────────
#
# A real person's head, neck, shoulders and chest, rendered in Blender (Cycles)
# from the MPFB / MakeHuman (CC0) base mesh with a hologram material — soft
# light, Fresnel rim, fine contour lines — at 9 turns × 3 mouth openings, in
# grey (holo/male.npz, holo/female.npz; regenerate with tools/holo_bust.py).
# Here the frames are tinted with the theme, cross-faded by turn and mouth, and
# set among the effects: HUD rings and sound-wave bars at the base, slanted
# orbit rings with data nodes, a diamond core, shimmering particles, drift.

_BOTTOM = -2.85
_SPARKS = np.random.default_rng(7).random((90, 4))
_WAVE_N = 72
_WAVE_AN = np.arange(_WAVE_N) / _WAVE_N * 2.0 * math.pi
#: Slanted orbit rings: (height, radius, lean, speed).
_ORBITS = ((-1.05, 2.15, 0.22, 0.55), (-2.15, 2.45, -0.18, -0.4))
_ORB_AN = np.linspace(0.0, 2.0 * math.pi, 97)


def _mix(a, b, f):
    return tuple(x + (y - x) * f for x, y in zip(a, b))


def _dots(cr, pts, alpha, rgb, r, levels=4):
    """Many small square dots, grouped by alpha into a few fills."""
    q = np.clip((alpha * levels).astype(int), 0, levels)
    for lv in range(1, levels + 1):
        for x, y in pts[q == lv].tolist():
            cr.rectangle(x - r, y - r, 2 * r, 2 * r)
        cr.set_source_rgba(*rgb, lv / levels)
        cr.fill()


_HOLO: dict = {}
#: The chest core, in head units (Blender: on the sternum, between the pectorals).
CORE = np.array([0.0, -2.0, 0.78])


def _holo_frames(figure: str, pal: Palette):
    """The figure's grey frames tinted with the palette, as cairo surfaces, plus sparkle
    points on each; built once per figure and theme."""
    import cairo
    from pathlib import Path

    key = (figure, pal)
    if key in _HOLO:
        return _HOLO[key]
    if figure not in _HOLO:
        with np.load(Path(__file__).with_name("holo") / f"{figure}.npz") as z:
            _HOLO[figure] = {k: z[k] for k in z.files}
    d = _HOLO[figure]
    tiles = d["tiles"]                                       # (turns, mouths, H, W, [luma, alpha])
    lum = tiles[..., 0].astype(np.float32) / 255.0
    alpha = tiles[..., 1].astype(np.float32) / 255.0
    # Dark → the fill colour, bright → the glow colour, the brightest lines towards white.
    lo, hi = np.array(_mix(pal.fill, pal.wire, 0.4)), np.array(pal.glow)
    f = np.clip(lum * 1.25, 0, 1)[..., None] ** 0.9
    rgb = lo + (hi - lo) * f
    rgb = rgb + (1.0 - rgb) * np.clip(lum - 0.75, 0, 1)[..., None] * 1.6
    rgb = np.clip(rgb * (0.55 + 0.75 * lum[..., None]), 0, 1)
    pre = (rgb * alpha[..., None] * 255).astype(np.uint8)
    a8 = (alpha * 255).astype(np.uint8)
    bgra = np.concatenate([pre[..., ::-1], a8[..., None]], -1)   # cairo ARGB32: B, G, R, A premultiplied
    h, w = tiles.shape[2:4]
    rng = np.random.default_rng(4)
    frames, sparks = [], []
    for ti in range(tiles.shape[0]):
        row, prow = [], []
        for mi in range(tiles.shape[1]):
            buf = np.ascontiguousarray(bgra[ti, mi])
            surf = cairo.ImageSurface.create_for_data(memoryview(buf), cairo.FORMAT_ARGB32, w, h, w * 4)
            row.append((surf, buf))                              # keep the buffer alive with it
            ys, xs = np.nonzero(tiles[ti, mi, :, :, 1] > 140)
            pick = rng.choice(len(xs), min(700, len(xs)), replace=False)
            prow.append(np.stack([xs[pick], ys[pick], rng.random(len(pick))], 1))
        frames.append(row)
        sparks.append(prow)
    out = {"frames": frames, "sparks": sparks, "yaws": d["yaws"], "mouths": d["mouths"],
           "unit": float(d["unit"]), "centre": d["centre"], "size": (w, h)}
    _HOLO[key] = out
    return out


def _figure(cr, t, u, pal: Palette, proj, fs, yaw, mouth, figure, glow):
    """Paint the rendered figure: the two nearest turns and mouths, cross-faded."""
    import cairo

    H = _holo_frames(figure, pal)
    yaws, mouths = H["yaws"], H["mouths"]
    fy = float(np.interp(yaw, yaws, np.arange(len(yaws))))
    fm = float(np.interp(mouth, mouths, np.arange(len(mouths))))
    y0, m0 = int(fy), int(fm)
    y1, m1 = min(y0 + 1, len(yaws) - 1), min(m0 + 1, len(mouths) - 1)
    wy, wm = fy - y0, fm - m0
    w, h = H["size"]
    k = fs / H["unit"]                                       # sprite pixels → frame pixels
    x0, top = proj(np.array([H["centre"][0] - w / 2 / H["unit"], H["centre"][1] + h / 2 / H["unit"], 0.0])).tolist()
    cr.save()
    cr.translate(x0, top)
    cr.scale(k, k)
    # The nearest frame fully, then the next mouth and the next turn over it at their share.
    mn = m1 if wm > 0.5 else m0
    for yi, mi, a in ((y0, m0, 1.0), (y0, m1, wm), (y1, mn, wy)):
        if a < 0.02 or (a < 1.0 and (yi, mi) == (y0, m0)):
            continue
        cr.set_source_surface(H["frames"][yi][mi][0], 0, 0)
        cr.get_source().set_filter(cairo.FILTER_GOOD)
        cr.paint_with_alpha(a)
    # A soft bloom: the same frame, larger and faint.
    cr.translate(w / 2, h / 2)
    cr.scale(1.03, 1.03)
    cr.translate(-w / 2, -h / 2)
    cr.set_source_surface(H["frames"][y0][m0][0], 0, 0)
    cr.paint_with_alpha(0.18 * min(1.0, glow))
    cr.restore()

    # Shimmering particles over the surface.
    P = H["sparks"][int(round(fy))][int(round(fm))]
    tw = 0.5 + 0.5 * np.sin(t * (2.0 + 3.0 * P[:, 2]) + P[:, 2] * 40)
    pts = np.stack([x0 + P[:, 0] * k, top + P[:, 1] * k], 1)
    _dots(cr, pts, 0.15 + 0.6 * tw, pal.glow, 0.6 * u)


def _core(cr, x, y, r, u, pal: Palette):
    """The diamond energy core with a lens flare."""
    import cairo

    g = cairo.RadialGradient(x, y, 0, x, y, r * 3.2)
    g.add_color_stop_rgba(0.0, 1, 1, 1, 0.95)
    g.add_color_stop_rgba(0.18, *pal.glow, 0.85)
    g.add_color_stop_rgba(1.0, *pal.glow, 0.0)
    cr.set_source(g)
    cr.arc(x, y, r * 3.2, 0, 2 * math.pi)
    cr.fill()
    for (dx, dy, length) in ((1, 0, 5.5), (0, 1, 2.6)):            # flare streaks
        g = cairo.LinearGradient(x - dx * r * length, y - dy * r * length, x + dx * r * length, y + dy * r * length)
        g.add_color_stop_rgba(0.0, *pal.glow, 0.0)
        g.add_color_stop_rgba(0.5, 1, 1, 1, 0.9)
        g.add_color_stop_rgba(1.0, *pal.glow, 0.0)
        cr.set_source(g)
        cr.set_line_width(1.3 * u)
        cr.move_to(x - dx * r * length, y - dy * r * length)
        cr.line_to(x + dx * r * length, y + dy * r * length)
        cr.stroke()
    cr.move_to(x, y - r * 1.5)
    cr.line_to(x + r, y)
    cr.line_to(x, y + r * 1.5)
    cr.line_to(x - r, y)
    cr.close_path()
    cr.set_source_rgba(1, 1, 1, 0.55)
    cr.fill_preserve()
    cr.set_line_width(1.2 * u)
    cr.set_source_rgba(*pal.glow, 1.0)
    cr.stroke()


def _orbits(cr, proj, pal: Palette, u, t, b, amp, yaw, front: bool):
    """Slanted particle rings with glowing data nodes orbiting the bust."""
    an = _ORB_AN
    for i, (y0, R, lean, speed) in enumerate(_ORBITS):
        r = R * (1.0 + 0.04 * b.ws * amp * math.sin(t * 8 + i))
        z = np.sin(an) * r
        V = _rot(np.stack([np.cos(an) * r, y0 + z * lean, z], -1), yaw, 0.0)
        S = proj(V)
        sel = (V[:, 2] > 0) if front else (V[:, 2] <= 0)
        _dots(cr, S[sel], np.full(int(sel.sum()), 0.75 if front else 0.3), pal.glow, 0.8 * u)
        ph = t * speed * (1.0 + 1.5 * b.wt) + i * 2.0
        nodes = np.array([ph, ph + 2.1, ph + 4.2])
        z = np.sin(nodes) * r
        N = _rot(np.stack([np.cos(nodes) * r, y0 + z * lean, z], -1), yaw, 0.0)
        for (x, y), nz in zip(proj(N).tolist(), N[:, 2].tolist()):
            if (nz > 0) != front:
                continue
            cr.arc(x, y, 2.6 * u, 0, 2 * math.pi)
            cr.set_source_rgba(*pal.glow, 0.25)
            cr.fill()
            cr.arc(x, y, 1.4 * u, 0, 2 * math.pi)
            cr.set_source_rgba(1, 1, 1, 0.9)
            cr.fill()


def _ellipse(cr, cx, cy, rx, ry):
    cr.save()
    cr.translate(cx, cy)
    cr.scale(1.0, ry / rx)
    cr.new_sub_path()
    cr.arc(0, 0, rx, 0, 2 * math.pi)
    cr.restore()


def _base(cr, proj, y0, u, pal: Palette, t, glow):
    """Flat rings of light under the bust, in the same perspective as the body."""
    import cairo

    R = 2.55
    cx, cy = proj(np.array([0.0, y0, 0.0])).tolist()
    rx = proj(np.array([R, y0, 0.0]))[0] - cx
    ry = (proj(np.array([0.0, y0, R]))[1] - proj(np.array([0.0, y0, -R]))[1]) / 2
    cy = (proj(np.array([0.0, y0, R]))[1] + proj(np.array([0.0, y0, -R]))[1]) / 2
    cr.save()
    cr.translate(cx, cy)
    cr.scale(1.0, ry / rx)
    g = cairo.RadialGradient(0, 0, 0, 0, 0, rx * 1.15)
    g.add_color_stop_rgba(0.0, *pal.glow, 0.40 * min(1.0, glow))
    g.add_color_stop_rgba(0.5, *pal.accent, 0.12)
    g.add_color_stop_rgba(1.0, *pal.accent, 0.0)
    cr.set_source(g)
    cr.arc(0, 0, rx * 1.15, 0, 2 * math.pi)
    cr.fill()
    cr.restore()
    for k, f in enumerate((1.0, 0.86, 0.70, 0.52, 0.34)):
        _ellipse(cr, cx, cy, rx * f, ry * f)
        cr.set_line_width((1.6 if k in (0, 2) else 0.8) * u)
        cr.set_source_rgba(*(pal.glow if k % 2 == 0 else pal.accent), 0.85 - k * 0.1)
        cr.stroke()
    for f, speed, dash in ((0.93, 25, (14, 10)), (0.61, -35, (5, 8))):
        _ellipse(cr, cx, cy, rx * f, ry * f)
        cr.set_dash([dash[0] * u, dash[1] * u], -t * speed * u)
        cr.set_line_width(2.2 * u)
        cr.set_source_rgba(*pal.glow, 0.75)
        cr.stroke()
    cr.set_dash([])


def _wave_bars(cr, proj, pal: Palette, u, t, b, amp, yaw, y0, front: bool):
    """Sound-wave bars standing on the base ring: a calm shimmer while she
    listens, a sweep while thinking, tall with her voice."""
    an = _WAVE_AN
    h = (0.10 + 0.10 * b.wl * (0.5 + 0.5 * np.sin(an * 5 + t * 2.0))
         + b.wt * 0.35 * np.clip(np.cos(an - t * 3.0), 0, 1) ** 4
         + b.ws * (0.15 + 1.3 * amp) * np.abs(np.sin(an * 7 + t * 9.0) * np.sin(an * 3 - t * 4.0)))
    R = 2.35
    foot = np.stack([np.cos(an + yaw) * R, np.full_like(an, y0), np.sin(an + yaw) * R], -1)
    head = foot.copy()
    head[:, 1] += h
    sel = (foot[:, 2] > 0) if front else (foot[:, 2] <= 0)
    segs = np.concatenate([proj(foot), proj(head)], 1)[sel]
    cr.set_line_width(1.6 * u)
    _bucket_stroke(cr, segs, np.clip((0.35 if front else 0.15) + 0.5 * h[sel], 0, 1), pal.glow)


def _ear_waves(cr, proj, pal: Palette, u, t, b, amp, yaw, pitch):
    """While she speaks, short waveforms beside the head, like the voice leaving it."""
    if b.ws < 0.05:
        return
    for side in (-1, 1):
        c = proj(_rot(np.array([side * 1.25, 0.05, 0.0]), yaw, pitch)).tolist()
        for k in range(9):
            x = c[0] + side * k * 3.2 * u
            hgt = (4 + 22 * amp * abs(math.sin(t * 11 + k * 1.3 + side))) * u * (1 - k / 11)
            cr.move_to(x, c[1] - hgt)
            cr.line_to(x, c[1] + hgt)
        cr.set_line_width(1.4 * u)
        cr.set_source_rgba(*pal.glow, 0.75 * b.ws)
        cr.stroke()


def _drift(cr, proj, pal: Palette, u, t, yaw, pitch):
    """Specks of light drifting off the back of the head and shoulder."""
    sp = _SPARKS
    ph = (t * (0.06 + 0.10 * sp[:, 1]) + sp[:, 0]) % 1.0
    start = np.stack([-0.35 - 0.5 * sp[:, 2], 1.0 - 2.2 * sp[:, 3], -0.2 - 0.3 * sp[:, 1]], -1)
    pos = start + np.stack([-1.1 * ph, 0.5 * ph, -0.3 * ph], -1) * (0.6 + sp[:, 2:3])
    S = proj(_rot(pos, yaw, pitch))
    al = np.sin(ph * math.pi) * (0.3 + 0.6 * sp[:, 3])
    q = np.clip((al * 4).astype(int), 0, 4)
    for lv in range(1, 5):
        for (x, y), r in zip(S[q == lv].tolist(), (0.5 + sp[q == lv, 1]).tolist()):
            cr.arc(x, y, r * u, 0, 2 * math.pi)
            cr.new_sub_path()
        cr.set_source_rgba(*pal.glow, lv / 4)
        cr.fill()


#: Light streaks rising off the shoulders: (|x|, phase, height, width) per streak.
_STREAKS = np.random.default_rng(11).random((36, 4))
_SHOULDER_LINE = (np.array([0.45, 0.9, 1.3, 1.7]), np.array([-1.25, -1.45, -1.6, -1.76]))


def _streaks(cr, proj, pal: Palette, u, t, b, amp, yaw):
    """Soft vertical streaks of light rising off the shoulders, taller with her voice."""
    import cairo

    sp = _STREAKS
    ax = 0.5 + 1.2 * sp[:, 0]
    side = np.where(np.arange(len(sp)) % 2 == 0, 1.0, -1.0)
    y0 = np.interp(ax, *_SHOULDER_LINE)
    flick = 0.5 + 0.5 * np.sin(t * (1.3 + 1.7 * sp[:, 1]) + sp[:, 1] * 30)
    h = (0.35 + 0.9 * sp[:, 2]) * flick * (0.9 + 0.5 * b.wt + 1.2 * b.ws * amp)
    for x, yb, hh, wd, f in zip((ax * side).tolist(), y0.tolist(), h.tolist(), sp[:, 3].tolist(), flick.tolist()):
        (x1, y1), (x2, y2) = proj(_rot(np.array([[x, yb, 0.2], [x, yb + hh, 0.2]]), yaw, 0.0)).tolist()
        for width, peak in ((4.0 + 4.0 * wd, 0.18), (1.0 + 1.6 * wd, 0.85)):   # bloom, then the core
            g = cairo.LinearGradient(x1, y1, x2, y2)
            g.add_color_stop_rgba(0.0, *pal.glow, 0.0)
            g.add_color_stop_rgba(0.12, *pal.glow, peak * f)
            g.add_color_stop_rgba(1.0, *pal.glow, 0.0)
            cr.set_source(g)
            cr.set_line_width(width * u)
            cr.move_to(x1, y1)
            cr.line_to(x2, y2)
            cr.stroke()


def _bust_style(cr, t, b, size, amp, pal: Palette, figure: str = "female"):
    import cairo

    glow, yaw, pitch, mouth = _motion(t, b, amp)
    yaw = 0.8 * yaw
    u = size / OVERLAY_SIZE
    fs = size * 0.155
    cx, cy = size * 0.5, size * 0.24
    proj = _projector(cx, cy, fs, cam=12.0)
    body_yaw = yaw
    base_y = _BOTTOM - 0.12

    # Bloom behind the figure.
    g = cairo.RadialGradient(cx, size * 0.45, 0, cx, size * 0.45, size * 0.45)
    g.add_color_stop_rgba(0.0, *_mix(pal.wire, pal.glow, 0.5), 0.18 * min(1.0, glow))
    g.add_color_stop_rgba(1.0, *pal.glow, 0.0)
    cr.set_source(g)
    cr.arc(cx, size * 0.45, size * 0.45, 0, 2 * math.pi)
    cr.fill()

    _base(cr, proj, base_y, u, pal, t, glow)
    _wave_bars(cr, proj, pal, u, t, b, amp, body_yaw, base_y, front=False)
    _orbits(cr, proj, pal, u, t, b, amp, body_yaw, front=False)
    _figure(cr, t, u, pal, proj, fs, yaw, min(1.0, 2.5 * mouth), figure, glow)
    _streaks(cr, proj, pal, u, t, b, amp, yaw)
    ex, ey = proj(_rot(CORE, yaw, 0.0)).tolist()
    _core(cr, ex, ey, fs * (0.13 + 0.06 * b.wt + 0.12 * b.ws * amp) * (1 + 0.08 * math.sin(t * 3)), u, pal)
    _drift(cr, proj, pal, u, t, yaw, 0.0)
    _ear_waves(cr, proj, pal, u, t, b, amp, yaw, 0.0)
    _orbits(cr, proj, pal, u, t, b, amp, body_yaw, front=True)
    _wave_bars(cr, proj, pal, u, t, b, amp, body_yaw, base_y, front=True)


# ── tools in use: a line out to a badge per connector ───────────────────────

#: Where the lines leave Lura (the left edge of the head / orb), as fractions of the frame.
_ORIGIN = {"face": (0.29, 0.42), "orb": (0.24, 0.5), "ring": (0.22, 0.5), "bust": (0.37, 0.24)}


def _tool_badges(cr, size, pal: Palette, tools, t, origin):
    """Each connector in use (e.g. github) as two letters in a holographic
    circle, with a stream of light running out to it from Lura."""
    import cairo

    from .state import TOOL_LINGER

    u = size / OVERLAY_SIZE
    ox, oy = size * origin[0], size * origin[1]
    r = max(11.0, size * 0.062)
    for i, (name, age) in enumerate(tools[-4:]):
        alpha = min(1.0, age / 0.3, (TOOL_LINGER - age) / 0.8)
        if alpha <= 0:
            continue
        bx, by = r + 4 * u, size * 0.14 + i * (2 * r + 8 * u)
        pulse = max(0.0, 1.0 - age / 0.7)

        # The stream: a curve out of Lura, dashes flowing towards the badge.
        mx, my = (ox + bx) / 2, min(oy, by) - size * 0.05
        cr.move_to(ox, oy)
        cr.curve_to(mx, my, mx, by, bx + r, by)
        cr.set_line_width(2.6 * u)
        cr.set_source_rgba(*pal.glow, 0.18 * alpha)
        cr.stroke_preserve()
        cr.set_line_width(1.2 * u)
        cr.set_dash([4 * u, 6 * u], -t * 40 * u)
        cr.set_source_rgba(*pal.glow, 0.85 * alpha)
        cr.stroke()
        cr.set_dash([])

        # The badge.
        g = cairo.RadialGradient(bx, by, 0, bx, by, r * (1.5 + 0.5 * pulse))
        g.add_color_stop_rgba(0.0, *pal.glow, 0.35 * alpha)
        g.add_color_stop_rgba(1.0, *pal.glow, 0.0)
        cr.set_source(g)
        cr.arc(bx, by, r * (1.5 + 0.5 * pulse), 0, 2 * math.pi)
        cr.fill()
        cr.arc(bx, by, r, 0, 2 * math.pi)
        cr.set_source_rgba(*pal.wire, 0.45 * alpha)
        cr.fill_preserve()
        cr.set_line_width(1.4 * u)
        cr.set_source_rgba(*pal.glow, 0.95 * alpha)
        cr.stroke()
        cr.set_line_width(1.0 * u)
        cr.set_dash([2 * u, 4 * u], t * 12 * u)
        cr.arc(bx, by, r * 1.25, 0, 2 * math.pi)
        cr.set_source_rgba(*pal.accent, 0.8 * alpha)
        cr.stroke()
        cr.set_dash([])

        label = (name[:1].upper() + name[1:2].lower()) if name else "?"
        cr.select_font_face("Sans", cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_BOLD)
        cr.set_font_size(r * 0.85)
        ext = cr.text_extents(label)
        cr.move_to(bx - ext.width / 2 - ext.x_bearing, by - ext.height / 2 - ext.y_bearing)
        cr.set_source_rgba(1, 1, 1, alpha)
        cr.show_text(label)


_RING_ANGLES: tuple[tuple[str, int, float], ...] = (
    ("right", 1, 0.0),
    ("down", 3, math.pi / 2.0),
    ("left", 0, math.pi),
    ("up", 2, -math.pi / 2.0),
)


def _air_hud(cr, size: int, pal: Palette, air: AirView, t: float) -> None:
    """Small bottom-left radar HUD showing zone, ring menu, grab progress, action pulse and mode."""
    import cairo

    u = size / OVERLAY_SIZE
    cx, cy = size * 0.14, size * 0.80
    R = size * 0.085
    bright = air.engaged

    # 1. Disc fill and outer ring (dashed when hand is too far).
    cr.new_sub_path()
    cr.arc(cx, cy, R, 0.0, 2.0 * math.pi)
    cr.set_source_rgba(*pal.wire, 0.18 if bright else 0.10)
    cr.fill_preserve()
    cr.set_line_width(1.2 * u)
    cr.set_source_rgba(*pal.glow, 0.90 if bright else 0.35)
    if air.zone == "far":
        cr.set_dash([3.0 * u, 3.0 * u])
    cr.stroke()
    if air.zone == "far":
        cr.set_dash([])

    # 2. Crosshair through the centre.
    cr.set_line_width(0.8 * u)
    cr.set_source_rgba(*pal.glow, 0.15)
    cr.move_to(cx - R, cy)
    cr.line_to(cx + R, cy)
    cr.move_to(cx, cy - R)
    cr.line_to(cx, cy + R)
    cr.stroke()

    # 3. Command ring wedge and four directional labels at 0/90/180/270 deg.
    if air.ring is not None:
        for direction, idx, ang in _RING_ANGLES:
            hovered = direction == air.ring_hover
            if hovered:
                cr.move_to(cx, cy)
                cr.arc(cx, cy, R, ang - math.pi / 4.0, ang + math.pi / 4.0)
                cr.close_path()
                cr.set_source_rgba(*pal.accent, 0.35)
                cr.fill()
            text = air.ring[idx]
            if not text:
                continue
            cr.select_font_face("Sans", cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_BOLD)
            cr.set_font_size(max(7.0, 9.0 * u))
            ext = cr.text_extents(text)
            tx = cx + math.cos(ang) * (R + 10.0 * u) - ext.width / 2.0 - ext.x_bearing
            ty = cy + math.sin(ang) * (R + 10.0 * u) - ext.height / 2.0 - ext.y_bearing
            tx = max(2.0 * u, min(size - ext.width - 2.0 * u, tx))
            ty = max(ext.height + 2.0 * u, min(size - 2.0 * u, ty))
            cr.move_to(tx, ty)
            if hovered:
                cr.set_source_rgba(*pal.accent, 0.98)
            else:
                cr.set_source_rgba(1.0, 1.0, 1.0, 0.85)
            cr.show_text(text)

    # 4. Scan sweep for 0.8 s after a hand appears.
    if 0.0 <= air.hand_age < 0.8:
        frac = air.hand_age / 0.8
        ang = 2.0 * math.pi * frac
        cr.set_line_width(1.5 * u)
        cr.set_source_rgba(*pal.glow, 0.8 * (1.0 - frac))
        cr.move_to(cx, cy)
        cr.line_to(cx + math.cos(ang) * R, cy + math.sin(ang) * R)
        cr.stroke()

    # 5. Tracked hands inside the disc.
    for hx, hy, pinch in air.hands:
        dx = (hx - 0.5) * 1.8 * R
        dy = (hy - 0.5) * 1.8 * R
        d = math.hypot(dx, dy)
        if d > R:
            k = R / d
            dx *= k
            dy *= k
        px, py = cx + dx, cy + dy
        cr.arc(px, py, 2.6 * u, 0.0, 2.0 * math.pi)
        cr.set_source_rgba(*(pal.glow if pinch else pal.accent), 0.95)
        cr.fill()
        if pinch:
            cr.set_line_width(1.2 * u)
            cr.set_source_rgba(*pal.glow, 0.95)
            cr.arc(px, py, 5.0 * u, 0.0, 2.0 * math.pi)
            cr.stroke()

    # 6. Grab hold progress arc (reuses the dwell arc).
    arc_prog = air.grab_progress if air.grab_progress > 0.0 else air.dwell
    if arc_prog > 0.0:
        cr.set_line_width(2.4 * u)
        cr.set_source_rgba(*pal.accent, 0.95)
        cr.arc(cx, cy, R + 4.0 * u, -math.pi / 2.0, -math.pi / 2.0 + 2.0 * math.pi * min(1.0, arc_prog))
        cr.stroke()

    # 7. Action confirmation pulse ring.
    if air.pulse_ok is not None and 0.0 <= air.pulse_age < 0.6:
        frac = air.pulse_age / 0.6
        rgb = pal.glow if air.pulse_ok else (1.0, 0.62, 0.20)
        cr.set_line_width(1.6 * u)
        cr.set_source_rgba(*rgb, 0.9 * (1.0 - frac))
        cr.arc(cx, cy, R * (1.0 + frac * 0.8), 0.0, 2.0 * math.pi)
        cr.stroke()

    # 8. Mode label under the disc (plain ASCII; cairo toy font; omitted while ring labels occupy bottom).
    if air.ring is None:
        label = f"AIR - {air.mode.upper()}"
        cr.select_font_face("Sans", cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_BOLD)
        cr.set_font_size(max(8.0, 9.0 * u))
        ext = cr.text_extents(label)
        lx = max(2.0 * u, cx - ext.width / 2.0 - ext.x_bearing)
        ly = cy + R + 12.0 * u - ext.height / 2.0 - ext.y_bearing
        cr.move_to(lx, ly)
        cr.set_source_rgba(1.0, 1.0, 1.0, 0.85 if bright else 0.45)
        cr.show_text(label)


_STYLES = {"face": _face_style, "orb": _orb_style, "ring": _ring_style, "bust": _bust_style}
