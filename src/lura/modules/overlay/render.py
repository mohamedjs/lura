"""The hologram face, drawn with cairo — shared by every window backend.

Look (approved; matches the promo video's head): the MediaPipe face mesh (468
vertices, 898 triangles) as an indigo wireframe over translucent lavender
facets lit from the upper left, glowing teal blinking eyes, teal lips and jaw,
a dotted cyan orbit ring and a cyan light band sweeping down. Nothing else:
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
from .state import StateBlender

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

def render_png(
    t: float, blender: StateBlender, size: int = OVERLAY_SIZE, amp: float = 0.0
) -> bytes:
    """Render one frame to PNG bytes."""
    import io

    bio = io.BytesIO()
    render_surface(t, blender, size, amp).write_to_png(bio)
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


def render_surface(
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
