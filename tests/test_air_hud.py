"""Air face feedback (Stage F1): Air HUD rendering, Air captions, framing hints, welcome and state."""

from __future__ import annotations

import _setup  # noqa: F401
import sys

from lura.modules.air import AirController, AirState, HandView
from lura.modules.air.domain import WELCOME, framing_hint
from lura.modules.overlay import STYLES, AirView, OverlayState, State, StateBlender, look_from
from lura.modules.overlay.captions import caption_lines
from lura.shared import events
from test_air_control import BOUNDS, FakeAir, FakeDesktop, _ev


class PointDesktop(FakeDesktop):
    """FakeDesktop extended with window_at() for pointing dwell/target checks."""

    def __init__(self, target: tuple[int, str, str] | None = (101, "google-chrome", "Chrome")) -> None:
        super().__init__()
        self.target = target

    def window_at(self, x: int, y: int) -> tuple[int, str, str] | None:
        return self.target


def _make_controller(
    target: tuple[int, str, str] | None = (101, "google-chrome", "Chrome"),
) -> tuple[AirController, FakeAir, PointDesktop, list[tuple[str, dict]], list[float]]:
    air = FakeAir()
    desk = PointDesktop(target=target)
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
        mode="window",
        hands=((0.3, 0.4, False), (0.7, 0.6, True)),
        dwell=0.5,
        hand_age=0.3,
        pulse_ok=False,
        pulse_age=0.2,
    )
    off = AirView(on=False, armed=True, mode="window", hands=((0.5, 0.5, True),), dwell=0.8)

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


def check_controller_hints_welcome_and_pointing() -> None:
    # Framing hints: 1.6 s at y=0.95 -> one hint; 5 s later -> none; after 10 s -> one more.
    ctrl, air, desk, pub, now = _make_controller()
    air.state = AirState(
        camera="on",
        hands=(HandView("right", 0.5, 0.95, "open_palm", False, 0.95),),
    )
    for t in (0.0, 0.5, 1.0, 1.4, 1.6):
        now[0] = t
        ctrl.tick(t)
    hints = [p for n, p in pub if n == events.AIR_ACTION and p["action"] == "hint"]
    assert len(hints) == 1 and hints[0]["ok"] is True and hints[0]["message"] == "Raise your hand a little."

    # 5 s later (t = 6.6) -> no second hint yet.
    for t in (3.0, 5.0, 6.6):
        now[0] = t
        ctrl.tick(t)
    hints = [p for n, p in pub if n == events.AIR_ACTION and p["action"] == "hint"]
    assert len(hints) == 1

    # After 10 s since first hint (t = 11.6) -> one more hint.
    now[0] = 11.6
    ctrl.tick(11.6)
    hints = [p for n, p in pub if n == events.AIR_ACTION and p["action"] == "hint"]
    assert len(hints) == 2

    # First arm -> one welcome; disarm + re-arm -> no second welcome.
    pub.clear()
    air.state = AirState(camera="on", hands=())
    now[0] = 20.0
    ctrl.process(_ev("pose", 20.0, data={"pose": "open_palm"}))
    welcomes = [p for n, p in pub if n == events.AIR_ACTION and p["action"] == "welcome"]
    assert welcomes == [{"action": "welcome", "ok": True, "message": WELCOME}]

    # Disarm via fist hold, then re-arm -> no second welcome.
    now[0] = 21.0
    ctrl.process(_ev("pose", 21.0, data={"pose": "fist"}))
    now[0] = 21.6
    ctrl.tick(21.6)
    assert ctrl.state()["armed"] is False
    now[0] = 22.0
    ctrl.process(_ev("pose", 22.0, data={"pose": "open_palm"}))
    assert ctrl.state()["armed"] is True
    welcomes = [p for n, p in pub if n == events.AIR_ACTION and p["action"] == "welcome"]
    assert len(welcomes) == 1

    # hand_since tracking on hand_enter / hand_leave.
    assert ctrl.state()["hand_since"] is None
    ctrl.process(_ev("hand_enter", 22.1, hand="right"))
    assert ctrl.state()["hand_since"] == 22.1
    ctrl.process(_ev("hand_enter", 22.3, hand="left"))
    assert ctrl.state()["hand_since"] == 22.1
    ctrl.process(_ev("hand_leave", 22.4, hand="right"))
    assert ctrl.state()["hand_since"] == 22.1
    ctrl.process(_ev("hand_leave", 22.5, hand="left"))
    assert ctrl.state()["hand_since"] is None

    # Pointing dwell & target in state().
    air.state = AirState(
        camera="on",
        hands=(HandView("right", 0.3, 0.5, "point", False, 0.95, tip_x=0.25, tip_y=0.5),),
    )
    now[0] = 23.0
    ctrl.process(_ev("pose", 23.0, data={"pose": "point"}))
    ctrl.tick(23.0)
    assert ctrl.state()["dwell"] == 0.0
    assert ctrl.state()["target"] == ""

    now[0] = 23.2
    ctrl.tick(23.2)
    assert abs(ctrl.state()["dwell"] - 0.5) < 0.05, ctrl.state()
    assert ctrl.state()["target"] == ""

    now[0] = 23.4
    ctrl.tick(23.4)
    assert ctrl.state()["target"] == "Chrome", ctrl.state()


def main() -> int:
    for name, fn in list(globals().items()):
        if name.startswith("check_"):
            fn()
            print(f"  {name[6:]} OK")
    print("air hud OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
