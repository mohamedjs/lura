"""What the subtitles under the face say, shared by the GTK and Qt windows.

Drawn by the toolkit's own text engine (Pango, Qt), never by cairo's toy
text API, which cannot join Arabic letters.
"""

from __future__ import annotations

from .state import OverlayState, State

#: Width of the subtitle strip; the window widens to this while it shows.
CAPTION_WIDTH = 440
#: Room under the face for up to about four lines.
CAPTION_HEIGHT = 150
#: After this long without a new word, an idle face's subtitles fade away.
LINGER_SECONDS = 8.0


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
