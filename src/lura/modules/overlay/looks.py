"""How the hologram looks: its style, its colours and its size.

Chosen in the dashboard (``overlay_style``, ``overlay_theme``,
``overlay_colors``, ``overlay_scale``) and applied live: the renderer reads
:attr:`OverlayState.look` every frame.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import NamedTuple

RGB = tuple[float, float, float]


class Palette(NamedTuple):
    wire: RGB      #: wireframe lines
    fill: RGB      #: translucent facets, dots
    accent: RGB    #: eyes, lips, jaw, hands
    glow: RGB      #: orbit ring, light band, orb highlights


#: Every theme is brightened enough to read on dark *and* light wallpapers.
THEMES: dict[str, Palette] = {
    "lura": Palette((0.44, 0.41, 0.90), (0.62, 0.60, 0.94), (0.05, 0.68, 0.80), (0.13, 0.75, 0.88)),
    "aurora": Palette((0.12, 0.58, 0.46), (0.42, 0.84, 0.68), (0.05, 0.74, 0.60), (0.50, 0.92, 0.45)),
    "rose": Palette((0.78, 0.30, 0.60), (0.94, 0.60, 0.80), (0.60, 0.38, 0.94), (1.00, 0.52, 0.72)),
    "gold": Palette((0.78, 0.52, 0.12), (0.97, 0.78, 0.42), (0.98, 0.58, 0.16), (1.00, 0.84, 0.36)),
    "ice": Palette((0.36, 0.56, 0.84), (0.76, 0.86, 0.98), (0.28, 0.70, 0.98), (0.72, 0.92, 1.00)),
}
STYLES = ("face", "orb", "bust", "ring")
SCALE_MIN, SCALE_MAX = 0.6, 2.0


def hex_rgb(value: str) -> RGB:
    """'#7068e6' → (0.44, 0.41, 0.9). Raises ValueError for anything else."""
    v = value.strip().lstrip("#")
    if len(v) != 6:
        raise ValueError(f"not a #rrggbb colour: {value!r}")
    return tuple(int(v[i:i + 2], 16) / 255 for i in (0, 2, 4))  # type: ignore[return-value]


def rgb_hex(rgb: RGB) -> str:
    return "#" + "".join(f"{round(c * 255):02x}" for c in rgb)


@dataclass(frozen=True)
class Look:
    style: str = "face"
    palette: Palette = THEMES["lura"]
    scale: float = 1.0
    #: The bust's figure, "female" or "male": it follows Lura's voice.
    figure: str = "female"


def look_from(style: str = "face", theme: str = "lura", colors: dict | None = None,
              scale: float = 1.0, figure: str = "female") -> Look:
    """A Look from the stored settings; unknown or broken values fall back to the default."""
    base = THEMES.get(theme, THEMES["lura"])
    if theme == "custom":
        base = THEMES["lura"]
        picked = {}
        for name, value in (colors or {}).items():
            try:
                picked[name] = hex_rgb(value)
            except (ValueError, TypeError):
                pass
        base = base._replace(**{k: v for k, v in picked.items() if k in Palette._fields})
    return Look(style if style in STYLES else "face", base,
                max(SCALE_MIN, min(SCALE_MAX, float(scale or 1.0))),
                "male" if figure == "male" else "female")
