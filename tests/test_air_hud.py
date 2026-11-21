"""Air face feedback: Air HUD rendering, Air captions, framing hints, engage welcome and hints."""

from __future__ import annotations

import _setup  # noqa: F401
import sys

from lura.modules.air import AirController, Command, HandView
from lura.modules.air.domain import WELCOME, framing_hint
from lura.modules.overlay import STYLES, AirView, OverlayState, State, StateBlender, look_from
from lura.modules.overlay.captions import caption_lines
from lura.shared import events
from test_air_control import FakeAir, FakeDesktop


def _make_controller() -> tuple[AirController, FakeAir, FakeDesktop, list[tuple[str, dict]], list[float]]:
    air = FakeAir()
    desk = FakeDesktop()
    published: list[tuple[str, dict]] = []
    now = [0.0]

    def pub(name: str, payload: dict) -> None:
        published.append((name, payload))

    ctrl = AirController(air, desk, pub, clock=lambda: now[0])  # type: ignore[arg-type]
    return ctrl, air, desk, published, now


def check_render_hud() -> None:
    try:
        import cairo  # noqa: F401
    except ImportError:
        print("  render_hud skipped (cairo unavailable)")
        return

    import numpy as np
    from lura.modules.overlay.render import OVERLAY_SIZE, render_surface

    sample = AirView(
        on=True,
        armed=True,
        engaged=True,
        zone="ok",
        mode="window",
        hands=((0.3, 0.4, False), (0.7, 0.6, True)),
        dwell=0.5,
        hand_age=0.3,
        pulse_ok=False,
        pulse_age=0.2,
    )
    ring_sample = AirView(
        on=True,
        engaged=True,
        zone="far",
        mode="universal",
        ring=("TAB<", "TAB>", "MAX", "CLICK"),
        ring_hover="right",
        grab_progress=0.45,
    )
    off = AirView(on=False, armed=True, engaged=True, mode="window", hands=((0.5, 0.5, True),), dwell=0.8)

    b = StateBlender()
    size = OVERLAY_SIZE
    y0, x1 = int(size * 0.62), int(size * 0.32)

    for style in ("face", "orb", "ring", "bust"):
        assert style in STYLES
        look = look_from(style)
        base = render_surface(1.3, b, size=size, look=look)
        with_none = render_surface(1.3, b, size=size, look=look, air=None)
        with_off = render_surface(1.3, b, size=size, look=look, air=off)
        base.flush()
        with_none.flush()
        with_off.flush()
        base_bytes = bytes(base.get_data())
        assert bytes(with_none.get_data()) == base_bytes, f"{style}: air=None differed"
        assert bytes(with_off.get_data()) == base_bytes, f"{style}: air.on=False drew extra pixels"

        with_hud = render_surface(1.3, b, size=size, look=look, air=sample)
        with_hud.flush()
        hud_bytes = bytes(with_hud.get_data())
        assert hud_bytes != base_bytes, f"{style}: Air HUD drew nothing"
        buf = np.frombuffer(with_hud.get_data(), np.uint8).reshape(size, with_hud.get_stride())[:, : size * 4]
        box_alpha = buf.reshape(size, size, 4)[y0:size, 0:x1, 3]
        assert box_alpha.any(), f"{style}: no non-transparent pixels in HUD box"

        with_ring = render_surface(1.3, b, size=size, look=look, air=ring_sample)
        with_ring.flush()
        assert bytes(with_ring.get_data()) != base_bytes, f"{style}: ring HUD drew nothing"


def check_captions_and_air_view() -> None:
    st = OverlayState()
    st.set_state(State.IDLE.value)
    st.air_source = lambda: AirView(on=True, message="Target: Chrome.", message_age=1.0)
    assert caption_lines(st) == ("", "Target: Chrome.")

    st.air_source = lambda: AirView(on=True, message="Target: Chrome.", message_age=4.0)
    assert caption_lines(st) is None

    # Speaking state suppresses Air messages.
    st.air_source = lambda: AirView(on=True, message="Target: Chrome.", message_age=1.0)
    st.set_state(State.SPEAKING.value)
    assert caption_lines(st) is None

    # Conversation captions win over Air messages even when idle.
    st.set_state(State.IDLE.value)
    st.set_caption("you", "hello")
    st.set_caption("lura", "hi there")
    assert caption_lines(st) == ("hello", "hi there")

    # Source that raises -> air_view() returns None.
    bad = OverlayState()

    def boom():
        raise RuntimeError("broken source")

    bad.air_source = boom
    assert bad.air_view() is None
    assert bad.air_view() is None


def check_framing_hint() -> None:
    def hv(x: float, y: float) -> tuple[HandView, ...]:
        return (HandView("right", x, y, "open_palm", False, 0.95),)

    assert framing_hint(hv(0.5, 0.95)) == "Raise your hand a little."
    assert framing_hint(hv(0.03, 0.5)) == "Move your hand towards the middle."
    assert framing_hint(hv(0.97, 0.5)) == "Move your hand towards the middle."
    assert framing_hint(hv(0.5, 0.5)) is None
    assert framing_hint(()) is None


def check_controller_engage_hints() -> None:
    ctrl, air, _, pub, _ = _make_controller()

    # Hint command produces hint feedback.
    ctrl.process(Command("hint", 1.5, text="Move your hand closer to the camera."))
    hints = [p for n, p in pub if n == events.AIR_ACTION and p["action"] == "hint"]
    assert hints == [{
        "action": "hint",
        "ok": True,
        "message": "Move your hand closer to the camera.",
    }]

    # First engage emits welcome once; disengage + re-engage emits "Air on." without second welcome.
    pub.clear()
    air.eng_state = {
        "engaged": True, "zone": "ok", "phase": "pointing", "ring_hover": "", "grab_progress": 0.0,
    }
    ctrl.process(Command("engage", 2.0))
    assert pub == [(events.AIR_ACTION, {"action": "welcome", "ok": True, "message": WELCOME})]
    st = ctrl.state()
    assert st["engaged"] is True and st["zone"] == "ok" and st["phase"] == "pointing"

    ctrl.process(Command("disengage", 5.5))
    assert pub[-1] == (events.AIR_ACTION, {"action": "disengage", "ok": True, "message": "Air off."})

    ctrl.process(Command("engage", 7.0))
    assert pub[-1] == (events.AIR_ACTION, {"action": "engage", "ok": True, "message": "Air on."})
    welcomes = [p for n, p in pub if n == events.AIR_ACTION and p["action"] == "welcome"]
    assert len(welcomes) == 1


def main() -> int:
    for name, fn in list(globals().items()):
        if name.startswith("check_"):
            fn()
            print(f"  {name[6:]} OK")
    print("air hud OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
