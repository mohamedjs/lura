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
    # Every style, a custom palette and the size range render; broken colours fall back.
    from .looks import SCALE_MAX, STYLES, THEMES, look_from, rgb_hex
    assert rgb_hex(THEMES["lura"].wire) == "#7069e6"
    custom = look_from("bust", "custom", {"wire": "#ff0000", "glow": "nonsense"}, 9)
    assert custom.palette.wire == (1.0, 0.0, 0.0) and custom.palette.glow == THEMES["lura"].glow
    assert custom.scale == SCALE_MAX and look_from("nope").style == "face"
    b = StateBlender()
    for _ in range(60):
        b.update(State.SPEAKING, dt=0.016)
    for style in STYLES:
        for size in (156, 520):
            surf = render_surface(2.3, b, size=size, amp=0.7, look=look_from(style, "gold"))
            rgba = surf.get_data()
            assert surf.get_width() == size and any(rgba[3::4]), f"{style} @ {size}: empty frame"
    # The bust's rendered figure exists for both voices and draws.
    for figure in ("female", "male"):
        surf = render_surface(1.0, StateBlender(), size=260, look=look_from("bust", figure=figure))
        assert any(surf.get_data()[3::4]), f"bust {figure}: empty frame"
    # Tools in use show as badges; only drawn cells take clicks.
    from .render import hit_rects
    st = OverlayState()
    st.set_tool("github")
    assert [n for n, _ in st.tools()] == ["github"] and st.tools(linger=-1) == []
    b = StateBlender()
    plain = render_surface(1.0, b, size=260, look=look_from("orb"))
    badged = render_surface(1.0, b, size=260, look=look_from("orb"), tools=[("github", 1.0)])
    assert bytes(plain.get_data()) != bytes(badged.get_data()), "badge not drawn"
    rects = hit_rects(plain)
    assert rects and not any(x == 0 and y == 0 for x, y, _, _ in rects), "the empty corner must let clicks through"
    assert sum(w * h for *_, w, h in rects) < 260 * 260 * 0.6
    from .captions import caption_lines
    st = OverlayState()
    assert caption_lines(st) is None, "nothing said, nothing shown"
    st.set_state("speaking"); st.set_caption("you", "hi"); st.set_caption("lura", "word " * 100)
    you, lura = caption_lines(st)
    assert you == "hi" and lura.startswith("…") and len(lura) <= 192, lura
    st.subtitles_enabled = False
    assert caption_lines(st) is None, "the switch hides them"
    import numpy as np
    from .state import AirView
    air_sample = AirView(
        on=True, armed=True, mode="window",
        hands=((0.3, 0.4, False), (0.7, 0.6, True)),
        dwell=0.5, hand_age=0.3, pulse_ok=False, pulse_age=0.2,
    )
    for style in STYLES:
        surf = render_surface(1.0, b, size=OVERLAY_SIZE, look=look_from(style), air=air_sample)
        surf.flush()
        buf = np.frombuffer(surf.get_data(), np.uint8).reshape(OVERLAY_SIZE, surf.get_stride())[:, : OVERLAY_SIZE * 4]
        box = buf.reshape(OVERLAY_SIZE, OVERLAY_SIZE, 4)[int(OVERLAY_SIZE * 0.62):OVERLAY_SIZE, : int(OVERLAY_SIZE * 0.32), 3]
        assert box.any(), f"{style}: Air HUD empty"
    print("overlay self-check OK")


selfcheck()
