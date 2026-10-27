"""``python -m lura.modules.overlay`` — render every state headlessly and check it."""

from .render import EDGES, OVERLAY_SIZE, TRI, V0, render_png, surface_rgba, render_surface
from .state import OverlayState, State, StateBlender


def selfcheck() -> None:
    n = len(V0)
    assert n == 468 and len(TRI) == 898, "unexpected face mesh size"
    assert TRI.min() >= 0 and TRI.max() < n, "triangle index out of range"
    assert EDGES.max() < n

    st = OverlayState()
    assert st.amplitude == 0.0
    st.set_amplitude(2.0)
    assert 0.9 < st.amplitude <= 1.0, "amplitude must clamp to 1.0"
    st.set_state("speaking")
    assert st.state is State.SPEAKING

    for target in (State.LISTENING, State.THINKING, State.SPEAKING):
        b = StateBlender()
        for _ in range(60):
            b.update(target, dt=0.016)
        for amp in (0.0, 1.0):
            png = render_png(1.7, b, size=OVERLAY_SIZE, amp=amp)
            assert png.startswith(b"\x89PNG"), f"{target} amp={amp}: not a PNG"
            assert len(png) > 1000, f"{target} amp={amp}: frame looks empty"
            rgba = surface_rgba(render_surface(1.7, b, size=OVERLAY_SIZE, amp=amp))
            assert len(rgba) == OVERLAY_SIZE * OVERLAY_SIZE * 4
    from .captions import caption_lines
    st = OverlayState()
    assert caption_lines(st) is None, "nothing said, nothing shown"
    st.set_state("speaking"); st.set_caption("you", "hi"); st.set_caption("lura", "word " * 100)
    you, lura = caption_lines(st)
    assert you == "hi" and lura.startswith("…") and len(lura) <= 192, lura
    st.subtitles_enabled = False
    assert caption_lines(st) is None, "the switch hides them"
    print("overlay self-check OK")


selfcheck()
