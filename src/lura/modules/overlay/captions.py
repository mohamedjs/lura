"""What the subtitles under the face say, shared by the GTK and Qt windows.

Drawn by the toolkit's own text engine (Pango, Qt), never by cairo's toy
text API, which cannot join Arabic letters.
"""

from __future__ import annotations

from .state import OverlayState, State

#: Width of the subtitle box at the default size; it follows Lura's size (``caption_width``).
CAPTION_WIDTH = 340
#: Room under the face for up to about four lines.
CAPTION_HEIGHT = 150
#: After this long without a new word, an idle face's subtitles fade away.
LINGER_SECONDS = 8.0


def caption_width(size: int) -> int:
    """The subtitle box under Lura: a little wider than her, never a banner."""
    return max(260, min(480, int(size * 1.3)))


#: How far the box tucks up into the frame's empty bottom, as a share of its size.
TUCK = 0.12

#: The box's look, shared by both windows: dark glass, a thin glowing border.
BOX_BG, BOX_BORDER = (10, 16, 30, 0.82), (90, 220, 255, 0.45)


def _tail(text: str, limit: int) -> str:
    """The end of a long reply: subtitles show what is being said now."""
    text = " ".join(text.split())
    if len(text) <= limit:
        return text
    cut = text[-limit:]
    space = cut.find(" ")
    return "…" + (cut[space + 1:] if 0 <= space < 30 else cut)


def caption_lines(state: OverlayState) -> tuple[str, str] | None:
    """(you, lura) to show, or None when the subtitles should be hidden."""
    if not state.subtitles_enabled:
        return None
    you, lura, age = state.captions()
    if not (you or lura):
        return None
    if state.state in (State.IDLE,) and age > LINGER_SECONDS:
        return None
    return _tail(you, 110), _tail(lura, 190)
