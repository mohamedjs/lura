"""Air camera preview, macOS camera ranking, PreviewFlash, PointerGlide and dashboard endpoints."""

from __future__ import annotations

import _setup  # noqa: F401
import http.client
import json
import socket
import sys
import time

import numpy as np

from lura.modules.air import AirController, AirService, Command, pick_camera
from lura.modules.air.domain import (
    GLIDE_SPAN_MAX,
    GLIDE_SPAN_MIN,
    INDEX_TIP,
    MAC_TRANSPORT_BUILTIN,
    MAC_TRANSPORT_VIRTUAL,
    MAC_TYPE_BUILTIN,
    MAC_TYPE_CONTINUITY,
    PointerGlide,
    PreviewFlash,
    camera_candidates,
    mac_camera_info,
)
from test_air_control import FakeAir, FakeDesktop


def check_mac_camera_info() -> None:
    ft = mac_camera_info(0, "FaceTime HD Camera", MAC_TYPE_BUILTIN, MAC_TRANSPORT_BUILTIN)
    assert ft.driver == "builtin" and ft.usable is True and ft.name == "FaceTime HD Camera"

    camo_virt = mac_camera_info(1, "Camo", MAC_TYPE_BUILTIN, MAC_TRANSPORT_VIRTUAL)
    assert camo_virt.driver == "virtual" and camo_virt.usable is False

    camo_hint = mac_camera_info(2, "Camo Studio", "", 0)
    assert camo_hint.driver == "virtual" and camo_hint.usable is False

    iphone = mac_camera_info(3, "Mohamed's iPhone Camera", MAC_TYPE_CONTINUITY, 0)
    assert iphone.driver == "continuity" and iphone.usable is False

    brio = mac_camera_info(4, "Logitech BRIO", "AVCaptureDeviceTypeExternal", 0x75736220)
    assert brio.driver == "usb" and brio.usable is True


def check_pick_camera_mac() -> None:
    camo = mac_camera_info(0, "Camo Studio", "", MAC_TRANSPORT_VIRTUAL)
    iphone = mac_camera_info(1, "Mohamed's iPhone Camera", MAC_TYPE_CONTINUITY, 0)
    facetime = mac_camera_info(2, "FaceTime HD Camera", MAC_TYPE_BUILTIN, MAC_TRANSPORT_BUILTIN)
    cams = [camo, iphone, facetime]

    assert pick_camera(cams) == facetime
    assert pick_camera(cams, "camo") == camo
    assert pick_camera(cams, "iphone") == iphone
    assert camera_candidates(cams, "camo") == [camo, facetime]

    usb = mac_camera_info(0, "Logitech BRIO", "AVCaptureDeviceTypeExternal", 0x75736220)
    builtin = mac_camera_info(1, "FaceTime HD Camera", MAC_TYPE_BUILTIN, MAC_TRANSPORT_BUILTIN)
    assert pick_camera([usb, builtin]) == builtin


def check_preview_flash() -> None:
    flash = PreviewFlash()
    flash.note("click", 1.0)
    assert flash.label(1.1, "point") == "CLICK"
    flash.note("click", 1.3)
    assert flash.label(1.4, "point") == "DOUBLE CLICK"
    assert flash.label(2.5, "point") == "POINT"
    flash.note("cursor", 2.6)
    assert flash.label(2.6, "point") == "POINT"
    assert flash.label(5.0, "") == ""


def check_pointer_glide() -> None:
    g = PointerGlide()
    assert g.at(0.0) is None and g.done(0.0) is True
    g.retarget((0.0, 0.0), (100.0, 0.0), now=0.0, span=0.04)
    assert g.at(0.02) == (50.0, 0.0)
    assert g.at(0.04) == (100.0, 0.0)
    assert g.done(0.04) is True

    # Mid-way retarget starts from the current interpolated position (50, 0).
    g.retarget((0.0, 0.0), (100.0, 0.0), now=0.0, span=0.04)
    g.retarget((999.0, 999.0), (100.0, 100.0), now=0.02, span=0.04)
    assert g.at(0.02) == (50.0, 0.0)
    mid = g.at(0.04)
    assert mid is not None and abs(mid[0] - 75.0) < 1e-9 and abs(mid[1] - 50.0) < 1e-9
    assert g.at(0.06 + 1e-9) == (100.0, 100.0)

    # Span clamps to [GLIDE_SPAN_MIN, GLIDE_SPAN_MAX].
    g_min = PointerGlide()
    g_min.retarget((0.0, 0.0), (100.0, 0.0), now=0.0, span=0.001)
    assert g_min.done(GLIDE_SPAN_MIN * 0.5) is False
    assert g_min.done(GLIDE_SPAN_MIN) is True

    g_max = PointerGlide()
    g_max.retarget((0.0, 0.0), (100.0, 0.0), now=0.0, span=10.0)
    assert g_max.done(GLIDE_SPAN_MAX * 0.5) is False
    assert g_max.done(GLIDE_SPAN_MAX) is True

    assert g.finish() == (100.0, 100.0)
    assert g.at(0.06) is None
    assert g.finish() is None


def check_render_preview() -> None:
    try:
        import cv2
    except ImportError:
        print("  render_preview skipped (cv2 unavailable)")
        return

    from lura.modules.air.infrastructure.preview import render_preview
    from test_air import make_hand

    hand = make_hand("point", cx=0.5, cy=0.5, size=0.2)
    pts = hand.points
    frame = np.zeros((360, 640, 3), dtype=np.uint8)
    raw = render_preview(frame, (hand,), "CLICK")
    assert raw.startswith(b"\xff\xd8")

    bgr = cv2.imdecode(np.frombuffer(raw, np.uint8), cv2.IMREAD_COLOR)
    assert bgr is not None
    tip = pts[INDEX_TIP]
    b, g, r = [int(v) for v in bgr[round(tip.y * 360), round(tip.x * 640)]]
    assert r > 180 and g > 150 and b < 120, (r, g, b)


def check_air_service_preview() -> None:
    svc = AirService(lambda n, p: None)
    assert svc.preview_jpeg() is None
    assert svc._preview_until > time.monotonic()

    try:
        import cv2  # noqa: F401
    except ImportError:
        print("  air_service_preview encode skipped (cv2 unavailable)")
        return

    frame = np.zeros((180, 320, 3), dtype=np.uint8)
    svc._preview_src = (1, frame, (), "")
    first = svc.preview_jpeg()
    assert first is not None and first.startswith(b"\xff\xd8")
    second = svc.preview_jpeg()
    assert second is first, "second call for same seq must return cached bytes object"
    assert svc._preview_until > time.monotonic()


def check_controller_glide() -> None:
    events_log: list[tuple] = []

    class OrderedDesktop(FakeDesktop):
        def pointer_to(self, x: int, y: int) -> None:
            events_log.append(("pointer_to", (x, y)))
            super().pointer_to(x, y)

        def run(self, name: str, args=None, *, confirmed: bool = False):
            events_log.append(("run", name))
            return super().run(name, args, confirmed=confirmed)

    air = FakeAir()
    desk = OrderedDesktop(pointer_pos=(500, 400))
    now = [0.0]
    ctrl = AirController(air, desk, clock=lambda: now[0], glide=True)  # type: ignore[arg-type]

    ctrl.process(Command("engage", 0.0))
    ctrl.process(Command("cursor", 0.03, dx=100.0, dy=0.0))
    assert desk.pointers == [], f"target should not be sent immediately: {desk.pointers}"

    now[0] = 0.01
    busy = ctrl._glide_step()
    assert busy is True and len(desk.pointers) == 1
    mid_x, mid_y = desk.pointers[-1]
    assert 500 < mid_x < 600 and mid_y == 400, (mid_x, mid_y)

    ctrl.process(Command("click", 0.04))
    assert events_log[-2:] == [("pointer_to", (600, 400)), ("run", "pointer_click")], events_log


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def check_dashboard_air_endpoints() -> None:
    from lura.bootstrap import build
    from lura.modules.dashboard import DashboardApi, DashboardServer

    c = build()
    frames: list[bytes | None] = [b"\xff\xd8fake", None]
    cams = [{"index": 0, "name": "FaceTime HD Camera", "driver": "builtin", "usable": True}]
    server = DashboardServer(
        DashboardApi(
            c.settings,
            c.brain,
            c.voice,
            c.connectors,
            c.bus,
            lambda: "ready",
            c.memory,
            c.skills,
            air_preview=lambda: frames.pop(0) if frames else None,
            air_cameras=lambda: cams,
        ),
        _free_port(),
    )
    server.start()
    try:
        def request(path: str, *, with_token: bool = True):
            conn = http.client.HTTPConnection("127.0.0.1", server.port, timeout=5)
            try:
                headers = {"X-Lura-Token": server.token} if with_token else {}
                conn.request("GET", path, headers=headers)
                res = conn.getresponse()
                return res.status, dict(res.getheaders()), res.read()
            finally:
                conn.close()

        status, headers, body = request("/api/air/preview")
        assert status == 200, status
        assert headers.get("Content-Type") == "image/jpeg", headers
        assert body == b"\xff\xd8fake", body
        assert "blob:" in headers.get("Content-Security-Policy", ""), headers

        status2, _, body2 = request("/api/air/preview")
        assert status2 == 204 and body2 == b"", (status2, body2)

        status_unauth, _, _ = request("/api/air/preview", with_token=False)
        assert status_unauth == 401, status_unauth

        status_cams, headers_cams, body_cams = request("/api/air/cameras")
        assert status_cams == 200, status_cams
        assert "blob:" in headers_cams.get("Content-Security-Policy", ""), headers_cams
        data = json.loads(body_cams)
        assert data == {"cameras": cams, "wanted": c.settings.current.air_camera}, data
    finally:
        server.stop()


def main() -> int:
    for name, fn in list(globals().items()):
        if name.startswith("check_"):
            fn()
            print(f"  {name[6:]} OK")
    print("air preview OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
