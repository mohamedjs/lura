"""Air module: poses, pinch hysteresis, swipes, spread, OneEuro, Stats, sysfs cameras, AirService."""

from __future__ import annotations

import _setup  # noqa: F401
import math
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

from lura.modules.air import (
    GESTURES,
    AirService,
    AirState,
    GestureEvent,
    HandView,
    list_cameras,
    pick_camera,
)
from lura.modules.air.domain import (
    INDEX_MCP,
    INDEX_PIP,
    INDEX_TIP,
    MIDDLE_MCP,
    MIDDLE_PIP,
    MIDDLE_TIP,
    PINKY_MCP,
    PINKY_PIP,
    PINKY_TIP,
    RING_MCP,
    RING_PIP,
    RING_TIP,
    THUMB_IP,
    THUMB_TIP,
    WRIST,
    CameraInfo,
    Hand,
    HandFrame,
    OneEuro,
    Point,
    Recognizer,
    Stats,
    classify_pose,
    extended,
    palm_center,
    pinch_ratio,
)
from lura.shared import events


def make_hand(
    pose: str,
    cx: float = 0.5,
    cy: float = 0.5,
    size: float = 0.2,
    side: str = "right",
    score: float = 0.95,
    pinch_gap: float | None = None,
) -> Hand:
    """Build a synthetic 21-landmark hand matching a named pose at (cx, cy) and palm size."""
    pts: list[Point] = [Point(cx, cy)] * 21
    pts[WRIST] = Point(cx, cy + 0.5 * size)
    pts[INDEX_MCP] = Point(cx - 0.30 * size, cy - 0.45 * size)
    pts[MIDDLE_MCP] = Point(cx, cy - 0.50 * size)
    pts[RING_MCP] = Point(cx + 0.28 * size, cy - 0.45 * size)
    pts[PINKY_MCP] = Point(cx + 0.52 * size, cy - 0.35 * size)

    # Thumb base joints.
    pts[1] = Point(cx - 0.35 * size, cy + 0.25 * size)
    pts[2] = Point(cx - 0.55 * size, cy)
    pts[THUMB_IP] = Point(cx - 0.70 * size, cy - 0.20 * size)

    ext_set = {
        "open_palm": {"index", "middle", "ring", "pinky"},
        "pinch": {"index", "middle", "ring", "pinky"},
        "point": {"index"},
        "victory": {"index", "middle"},
        "fist": set(),
        "thumbs_up": set(),
        "thumbs_down": set(),
    }[pose]

    finger_specs = (
        ("index", -0.30, INDEX_PIP, 7, INDEX_TIP),
        ("middle", 0.00, MIDDLE_PIP, 11, MIDDLE_TIP),
        ("ring", 0.28, RING_PIP, 15, RING_TIP),
        ("pinky", 0.52, PINKY_PIP, 19, PINKY_TIP),
    )
    for name, dx, pip_i, dip_i, tip_i in finger_specs:
        fx = cx + dx * size
        if name in ext_set:
            pts[pip_i] = Point(fx, cy - 0.85 * size)
            pts[dip_i] = Point(fx, cy - 1.25 * size)
            pts[tip_i] = Point(fx, cy - 1.65 * size)
        else:
            pts[pip_i] = Point(fx, cy - 0.65 * size)
            pts[dip_i] = Point(fx, cy - 0.45 * size)
            pts[tip_i] = Point(fx, cy - 0.25 * size)

    if pinch_gap is not None:
        idx_tip = pts[INDEX_TIP]
        pts[THUMB_TIP] = Point(idx_tip.x - pinch_gap * size, idx_tip.y)
    elif pose == "pinch":
        pts[THUMB_TIP] = pts[INDEX_TIP]
    elif pose == "open_palm":
        pts[THUMB_TIP] = Point(cx - 0.95 * size, cy - 0.45 * size)
    elif pose == "thumbs_up":
        pts[THUMB_TIP] = Point(cx - 0.30 * size, cy - 1.15 * size)
    elif pose == "thumbs_down":
        pts[THUMB_TIP] = Point(cx - 0.30 * size, cy + 0.35 * size)
    else:
        # Tucked across the palm: neither thumb-extended nor pinching.
        pts[THUMB_TIP] = Point(cx + 0.15 * size, cy - 0.35 * size)

    return Hand(side=side, score=score, points=tuple(pts))


def check_poses() -> None:
    poses = ("open_palm", "fist", "point", "victory", "thumbs_up", "thumbs_down", "pinch")
    for size in (0.12, 0.28):
        for cx, cy in ((0.25, 0.35), (0.75, 0.65)):
            for pose in poses:
                hand = make_hand(pose, cx=cx, cy=cy, size=size)
                got, clarity = classify_pose(hand, pinching=(pose == "pinch"))
                assert got == pose, f"expected {pose}, got {got} (size={size}, pos=({cx},{cy}))"
                assert clarity == 1.0, f"expected clarity 1.0 for {pose}, got {clarity}"

    open_h = make_hand("open_palm")
    assert all(extended(open_h).values())
    assert pinch_ratio(open_h) > 0.5

    fist_h = make_hand("fist")
    assert not any(extended(fist_h).values())

    pinch_h = make_hand("pinch")
    assert pinch_ratio(pinch_h) < 0.01
    assert abs(palm_center(open_h).y - 0.5) < 0.1


def check_pinch_hysteresis() -> None:
    rec = Recognizer()
    t = 0.0
    # Ratio 0.30 -> above PINCH_ON (0.28), must not start pinching.
    evs = rec.update(HandFrame(t, (make_hand("open_palm", pinch_gap=0.30),)))
    assert [e.gesture for e in evs] == ["hand_enter"]

    # Ratio 0.25 -> starts pinch.
    t += 0.1
    evs = rec.update(HandFrame(t, (make_hand("open_palm", pinch_gap=0.25),)))
    assert [e.gesture for e in evs] == ["pinch_start"]

    # Ratio 0.35 -> between PINCH_ON and PINCH_OFF, stays pinching.
    t += 0.1
    evs = rec.update(HandFrame(t, (make_hand("open_palm", pinch_gap=0.35),)))
    assert "pinch_end" not in [e.gesture for e in evs]
    assert rec.views(t)[0].pinching is True

    # Ratio 0.45 held for RELEASE_HOLD (0.10 s) -> ends pinch with duration = end - start.
    t += 0.1
    evs = rec.update(HandFrame(t, (make_hand("open_palm", pinch_gap=0.45),)))
    assert "pinch_end" not in [e.gesture for e in evs]
    assert rec.views(t)[0].pinching is True
    t += 0.12
    evs = rec.update(HandFrame(t, (make_hand("open_palm", pinch_gap=0.45),)))
    ends = [e for e in evs if e.gesture == "pinch_end"]
    assert len(ends) == 1
    assert ends[0].data["duration"] == 0.32

    # Fist with thumb right on the index tip is NOT a pinch (index reach < PINCH_REACH).
    rec_fist = Recognizer()
    evs_f = rec_fist.update(HandFrame(0.0, (make_hand("fist", pinch_gap=0.05),)))
    assert "pinch_start" not in [e.gesture for e in evs_f]
    assert rec_fist.views(0.0)[0].pinching is False

    # Active pinch ends when the hand closes into a fist for RELEASE_HOLD (index reach < PINCH_REACH_END).
    rec_close = Recognizer()
    evs_s = rec_close.update(HandFrame(0.0, (make_hand("open_palm", pinch_gap=0.10),)))
    assert "pinch_start" in [e.gesture for e in evs_s]
    assert rec_close.update(HandFrame(0.20, (make_hand("fist", pinch_gap=0.05),))) == []
    evs_e = rec_close.update(HandFrame(0.32, (make_hand("fist", pinch_gap=0.05),)))
    assert [e.gesture for e in evs_e] == ["pinch_end"]
    assert rec_close.views(0.32)[0].pinching is False


def check_pinch_release_debounce() -> None:
    # 1-3 frame release (< 0.10 s) does not end the pinch and produces no double_pinch.
    rec = Recognizer()
    evs: list[GestureEvent] = []
    for t, gap in (
        (0.00, 0.10),  # pinch_start
        (0.10, 0.50),  # flicker frame 1
        (0.13, 0.50),  # flicker frame 2 (0.03 s)
        (0.16, 0.50),  # flicker frame 3 (0.06 s < 0.10 s)
        (0.19, 0.10),  # back to pinching -> cancels pending release
    ):
        evs.extend(rec.update(HandFrame(t, (make_hand("open_palm", pinch_gap=gap),))))
    names = [e.gesture for e in evs]
    assert "pinch_end" not in names, f"flicker should not end pinch: {names}"
    assert "double_pinch" not in names, f"flicker should not emit double_pinch: {names}"
    assert rec.views(0.19)[0].pinching is True

    # Release held 0.12 s ends the pinch cleanly at the frame where it ends.
    evs_rel: list[GestureEvent] = []
    for t in (0.30, 0.34, 0.38, 0.42):
        evs_rel.extend(rec.update(HandFrame(t, (make_hand("open_palm", pinch_gap=0.50),))))
    ends = [e for e in evs_rel if e.gesture == "pinch_end"]
    assert len(ends) == 1
    assert abs(ends[0].t - 0.42) < 1e-6
    assert ends[0].data["duration"] == 0.42
    assert rec.views(0.42)[0].pinching is False

    # Hand loss still ends an active pinch at once with lost=True (no extra RELEASE_HOLD wait).
    rec_loss = Recognizer()
    rec_loss.update(HandFrame(0.0, (make_hand("pinch"),)))
    evs_loss = rec_loss.update(HandFrame(0.50, ()))
    assert [e.gesture for e in evs_loss] == ["pinch_end", "hand_leave"]
    assert evs_loss[0].data["lost"] is True


def check_double_pinch() -> None:
    # Two short pinches 0.3 s apart -> double_pinch emitted once.
    rec = Recognizer()
    all_evs: list[GestureEvent] = []
    for t, gap in (
        (0.00, 0.50),
        (0.05, 0.10),  # pinch 1 start
        (0.15, 0.50),  # pinch 1 release begins
        (0.27, 0.50),  # pinch 1 end (dur = 0.22 <= 0.30)
        (0.55, 0.10),  # pinch 2 start (gap = 0.28 <= 0.45) -> double_pinch
        (0.65, 0.50),  # pinch 2 release begins
        (0.77, 0.50),  # pinch 2 end
    ):
        all_evs.extend(rec.update(HandFrame(t, (make_hand("open_palm", pinch_gap=gap),))))
    assert [e.gesture for e in all_evs].count("double_pinch") == 1

    # Three quick pinches -> still exactly ONE double_pinch (pinch 2 cannot seed another).
    rec_triple = Recognizer()
    evs_triple: list[GestureEvent] = []
    for t, gap in (
        (0.00, 0.50),
        (0.05, 0.10),  # pinch 1 start
        (0.15, 0.50),  # pinch 1 release begins
        (0.27, 0.50),  # pinch 1 end
        (0.50, 0.10),  # pinch 2 start -> double_pinch
        (0.60, 0.50),  # pinch 2 release begins
        (0.72, 0.50),  # pinch 2 end (must NOT count as first of another double_pinch)
        (0.95, 0.10),  # pinch 3 start -> must NOT emit a second double_pinch
        (1.05, 0.50),  # pinch 3 release begins
        (1.17, 0.50),  # pinch 3 end
    ):
        evs_triple.extend(rec_triple.update(HandFrame(t, (make_hand("open_palm", pinch_gap=gap),))))
    assert [e.gesture for e in evs_triple].count("double_pinch") == 1

    # Long first pinch (0.6 s) -> no double_pinch.
    rec2 = Recognizer()
    all_evs2: list[GestureEvent] = []
    for t, gap in (
        (0.00, 0.50),
        (0.05, 0.10),  # start
        (0.65, 0.50),  # release begins
        (0.77, 0.50),  # end (dur = 0.72 > 0.30)
        (1.05, 0.10),  # second pinch 0.28 s later -> must NOT be double_pinch
    ):
        all_evs2.extend(rec2.update(HandFrame(t, (make_hand("open_palm", pinch_gap=gap),))))
    assert "double_pinch" not in [e.gesture for e in all_evs2]


def check_finger_hysteresis() -> None:
    # A middle finger hovering at ratio 1.12 after being extended stays extended.
    v_hand = make_hand("victory")
    ext1 = extended(v_hand)
    assert ext1["middle"] is True

    # Place MIDDLE_TIP so dist(WRIST, MIDDLE_TIP) / dist(WRIST, MIDDLE_PIP) == 1.12.
    pts = list(v_hand.points)
    wrist = pts[WRIST]
    pip = pts[MIDDLE_PIP]
    d_pip = math.hypot(pip.x - wrist.x, pip.y - wrist.y)
    pts[MIDDLE_TIP] = Point(pip.x, wrist.y - 1.12 * d_pip)
    hover_hand = Hand(v_hand.side, v_hand.score, tuple(pts))

    assert extended(hover_hand)["middle"] is False           # without hysteresis: folded
    ext2 = extended(hover_hand, ext1)
    assert ext2["middle"] is True                            # with hysteresis: stays extended
    assert classify_pose(hover_hand, False, ext1)[0] == "victory"

    # Drop below EXTENDED - 0.08 (1.07) -> finally folds.
    pts[MIDDLE_TIP] = Point(pip.x, wrist.y - 1.04 * d_pip)
    folded_hand = Hand(v_hand.side, v_hand.score, tuple(pts))
    assert extended(folded_hand, ext2)["middle"] is False

    # Recognizer keeps prev per track, so victory does not flicker to point at ratio 1.12.
    rec = Recognizer()
    rec.update(HandFrame(0.00, (v_hand,)))
    evs = rec.update(HandFrame(0.20, (v_hand,)))
    assert any(e.gesture == "pose" and e.data == {"pose": "victory"} for e in evs)
    evs_h = rec.update(HandFrame(0.40, (hover_hand,)))
    assert evs_h == []
    assert rec.views(0.40)[0].pose == "victory"


def check_pose_stability() -> None:
    rec = Recognizer()
    # Brief pose < 0.15 s emits nothing.
    evs = rec.update(HandFrame(0.00, (make_hand("thumbs_up"),)))
    assert [e.gesture for e in evs] == ["hand_enter"]
    assert rec.update(HandFrame(0.10, (make_hand("thumbs_up"),))) == []
    assert rec.update(HandFrame(0.12, (make_hand("victory"),))) == []
    assert rec.update(HandFrame(0.22, (make_hand("victory"),))) == []

    # Held >= 0.15 s emits exactly one pose event.
    evs = rec.update(HandFrame(0.27, (make_hand("victory"),)))
    assert len(evs) == 1 and evs[0].gesture == "pose" and evs[0].data == {"pose": "victory"}
    # Holding longer does not re-emit.
    assert rec.update(HandFrame(0.50, (make_hand("victory"),))) == []


def _run_motion(
    rec: Recognizer,
    t0: float,
    x0: float,
    y0: float,
    dx: float,
    dy: float,
    dur: float,
    fps: int = 30,
    pose: str = "open_palm",
    pinch_gap: float | None = None,
    settle_frames: int = 3,
) -> tuple[float, list[GestureEvent]]:
    evs: list[GestureEvent] = []
    steps = max(1, round(dur * fps))
    dt = 1.0 / fps
    t = t0
    for i in range(1, steps + 1):
        t = t0 + i * dt
        frac = i / steps
        hand = make_hand(pose, cx=x0 + dx * frac, cy=y0 + dy * frac, pinch_gap=pinch_gap)
        evs.extend(rec.update(HandFrame(t, (hand,))))
    for _ in range(settle_frames):
        t += dt
        hand = make_hand(pose, cx=x0 + dx, cy=y0 + dy, pinch_gap=pinch_gap)
        evs.extend(rec.update(HandFrame(t, (hand,))))
    return t, evs


def _stabilize_open(rec: Recognizer, x: float = 0.3, y: float = 0.5, t0: float = 0.0) -> float:
    dt = 1.0 / 30.0
    t = t0
    for i in range(6):
        t = t0 + i * dt
        rec.update(HandFrame(t, (make_hand("open_palm", cx=x, cy=y),)))
    return t


def check_swipes() -> None:
    # Right, left, up, down.
    cases = [
        (0.3, 0.5, +0.3, 0.0, "swipe_right"),
        (0.7, 0.5, -0.3, 0.0, "swipe_left"),
        (0.5, 0.7, 0.0, -0.3, "swipe_up"),
        (0.5, 0.3, 0.0, +0.3, "swipe_down"),
    ]
    for x0, y0, dx, dy, expected in cases:
        rec = Recognizer()
        t = _stabilize_open(rec, x0, y0)
        _, evs = _run_motion(rec, t, x0, y0, dx, dy, 0.25)
        swipes = [e.gesture for e in evs if e.gesture.startswith("swipe_")]
        assert swipes == [expected], f"expected [{expected}], got {swipes}"

    # Diagonal (dx == dy) -> no swipe.
    rec = Recognizer()
    t = _stabilize_open(rec, 0.2, 0.2)
    _, evs = _run_motion(rec, t, 0.2, 0.2, 0.3, 0.3, 0.25)
    assert not [e for e in evs if e.gesture.startswith("swipe_")]

    # Slow (0.3 over 1.0 s) -> no swipe.
    rec = Recognizer()
    t = _stabilize_open(rec, 0.2, 0.5)
    _, evs = _run_motion(rec, t, 0.2, 0.5, 0.3, 0.0, 1.0)
    assert not [e for e in evs if e.gesture.startswith("swipe_")]

    # Cooldown: second swipe 0.3 s after first -> blocked.
    rec = Recognizer()
    t = _stabilize_open(rec, 0.2, 0.5)
    t, evs1 = _run_motion(rec, t, 0.2, 0.5, +0.35, 0.0, 0.25, settle_frames=0)
    swipe1 = [e for e in evs1 if e.gesture == "swipe_right"]
    assert len(swipe1) == 1
    first_t = swipe1[0].t
    t, evs2 = _run_motion(rec, t, 0.55, 0.5, -0.35, 0.0, 0.25, settle_frames=0)
    assert t - first_t < 0.70
    assert not [e for e in evs2 if e.gesture.startswith("swipe_")]

    # Return stroke: swipe right then hand returns left at the same speed 0.8 s later -> no swipe_left.
    rec_rev = Recognizer()
    t = _stabilize_open(rec_rev, 0.2, 0.5)
    t, evs_a = _run_motion(rec_rev, t, 0.2, 0.5, +0.35, 0.0, 0.25, settle_frames=0)
    first_t = next(e.t for e in evs_a if e.gesture == "swipe_right")
    while t < first_t + 0.80:
        t += 1.0 / 30.0
        rec_rev.update(HandFrame(t, (make_hand("open_palm", cx=0.55, cy=0.5),)))
    _, evs_b = _run_motion(rec_rev, t, 0.55, 0.5, -0.35, 0.0, 0.25, settle_frames=0)
    assert not [e for e in evs_b if e.gesture.startswith("swipe_")], f"return stroke leaked: {evs_b}"

    # Deliberate second swipe_right after cooldown and settling (still 0.25 s) -> emitted.
    rec_same = Recognizer()
    t = _stabilize_open(rec_same, 0.2, 0.5)
    t, evs_s1 = _run_motion(rec_same, t, 0.2, 0.5, +0.35, 0.0, 0.25, settle_frames=0)
    first_t = next(e.t for e in evs_s1 if e.gesture == "swipe_right")
    # Return to 0.20 (blocked by reverse block & unsettled), then hold still for 0.25 s (>= SETTLE_TIME).
    t, evs_ret = _run_motion(rec_same, t, 0.55, 0.5, -0.35, 0.0, 0.30, settle_frames=0)
    assert not [e for e in evs_ret if e.gesture.startswith("swipe_")]
    settle_end = max(t + 0.25, first_t + 0.75)
    while t < settle_end:
        t += 1.0 / 30.0
        rec_same.update(HandFrame(t, (make_hand("open_palm", cx=0.20, cy=0.5),)))
    _, evs_s2 = _run_motion(rec_same, t, 0.20, 0.5, +0.35, 0.0, 0.25, settle_frames=0)
    assert [e.gesture for e in evs_s2 if e.gesture.startswith("swipe_")] == ["swipe_right"]

    # Opposite swipe 2.0 s after a right swipe (after settling) -> emitted.
    rec_opp = Recognizer()
    t = _stabilize_open(rec_opp, 0.2, 0.5)
    t, evs_o1 = _run_motion(rec_opp, t, 0.2, 0.5, +0.35, 0.0, 0.25, settle_frames=0)
    first_t = next(e.t for e in evs_o1 if e.gesture == "swipe_right")
    while t < first_t + 2.0:
        t += 1.0 / 30.0
        rec_opp.update(HandFrame(t, (make_hand("open_palm", cx=0.55, cy=0.5),)))
    _, evs_o2 = _run_motion(rec_opp, t, 0.55, 0.5, -0.35, 0.0, 0.25, settle_frames=0)
    assert [e.gesture for e in evs_o2 if e.gesture.startswith("swipe_")] == ["swipe_left"]

    # Pinching hand moving fast -> no swipe.
    rec = Recognizer()
    t = _stabilize_open(rec, 0.2, 0.5)
    t += 1.0 / 30.0
    rec.update(HandFrame(t, (make_hand("pinch", cx=0.2, cy=0.5),)))
    _, evs_p = _run_motion(rec, t, 0.2, 0.5, +0.35, 0.0, 0.25, pose="pinch")
    assert not [e for e in evs_p if e.gesture.startswith("swipe_")]

    # Low fps (8 fps, 4 samples): 0.20 swipe over 0.45 s is detected.
    rec_low = Recognizer()
    evs_low: list[GestureEvent] = []
    for t_s, x_s in ((0.00, 0.30), (0.15, 0.37), (0.30, 0.43), (0.45, 0.50)):
        evs_low.extend(rec_low.update(HandFrame(t_s, (make_hand("open_palm", cx=x_s, cy=0.5),))))
    assert [e.gesture for e in evs_low if e.gesture.startswith("swipe_")] == ["swipe_right"]

    # Exit swipe: fast swipe leaving frame after 0.15 s and 0.14 travel -> swipe before hand_leave.
    rec_exit = Recognizer()
    evs_in: list[GestureEvent] = []
    for t_s, x_s in ((0.00, 0.70), (0.05, 0.75), (0.10, 0.80), (0.15, 0.84)):
        evs_in.extend(rec_exit.update(HandFrame(t_s, (make_hand("open_palm", cx=x_s, cy=0.5),))))
    assert not [e for e in evs_in if e.gesture.startswith("swipe_")]
    evs_out = rec_exit.update(HandFrame(0.65, ()))  # 0.50 s after last_seen (>= LEAVE_GRACE 0.45)
    assert [e.gesture for e in evs_out] == ["swipe_right", "hand_leave"]


def check_leave() -> None:
    rec = Recognizer()
    rec.update(HandFrame(0.0, (make_hand("open_palm"),)))
    # Missing 0.4 s -> still tracked (LEAVE_GRACE = 0.45).
    assert rec.update(HandFrame(0.40, ())) == []
    assert len(rec.views(0.40)) == 1
    # Missing 0.5 s -> hand_leave.
    evs = rec.update(HandFrame(0.50, ()))
    assert [e.gesture for e in evs] == ["hand_leave"]

    # Missing while pinching -> pinch_end(lost=True) before hand_leave.
    rec2 = Recognizer()
    rec2.update(HandFrame(0.0, (make_hand("pinch"),)))
    evs2 = rec2.update(HandFrame(0.50, ()))
    assert [e.gesture for e in evs2] == ["pinch_end", "hand_leave"]
    assert evs2[0].data["lost"] is True


def check_spread() -> None:
    rec = Recognizer()
    t = 0.0
    lh = make_hand("pinch", cx=0.40, cy=0.5, side="left")
    rh = make_hand("pinch", cx=0.60, cy=0.5, side="right")
    evs = rec.update(HandFrame(t, (lh, rh)))
    assert "spread_start" in [e.gesture for e in evs]
    sp = next(e for e in evs if e.gesture == "spread_start")
    assert sp.hand == "both"
    assert abs((rec.spread_scale() or 0.0) - 1.0) < 0.01

    # Move apart from 0.2 to 0.3 and let the filter settle.
    for i in range(1, 35):
        t = i * 0.05
        lh = make_hand("pinch", cx=0.35, cy=0.5, side="left")
        rh = make_hand("pinch", cx=0.65, cy=0.5, side="right")
        rec.update(HandFrame(t, (lh, rh)))
    scale = rec.spread_scale()
    assert scale is not None and abs(scale - 1.5) < 0.05, f"expected ~1.5, got {scale}"

    # Release right pinch (held >= RELEASE_HOLD) -> spread_end with scale ~1.5.
    t += 0.05
    rec.update(HandFrame(t, (
        make_hand("pinch", cx=0.35, cy=0.5, side="left"),
        make_hand("open_palm", cx=0.65, cy=0.5, side="right"),
    )))
    t += 0.12
    evs = rec.update(HandFrame(t, (
        make_hand("pinch", cx=0.35, cy=0.5, side="left"),
        make_hand("open_palm", cx=0.65, cy=0.5, side="right"),
    )))
    ends = [e for e in evs if e.gesture == "spread_end"]
    assert len(ends) == 1
    assert abs(ends[0].data["scale"] - 1.5) < 0.05
    assert rec.spread_scale() is None


def check_score_and_dedup() -> None:
    rec = Recognizer()
    low = make_hand("open_palm", side="right", score=0.40)
    assert rec.update(HandFrame(0.0, (low,))) == []
    assert rec.views(0.0) == ()

    # Duplicate side keeps higher score.
    h1 = make_hand("open_palm", cx=0.2, side="right", score=0.70)
    h2 = make_hand("open_palm", cx=0.8, side="right", score=0.92)
    evs = rec.update(HandFrame(0.1, (h1, h2)))
    assert len(evs) == 1 and evs[0].confidence == 0.92
    assert rec.views(0.1)[0].score == 0.92


def check_one_euro() -> None:
    f = OneEuro()
    assert f(0.5, 0.0) == 0.5  # first value passthrough
    raw = [0.5 + (0.01 if i % 2 == 0 else -0.01) for i in range(1, 60)]
    filtered = [f(v, i / 30.0) for i, v in enumerate(raw, start=1)]
    var_raw = sum((v - 0.5) ** 2 for v in raw) / len(raw)
    var_filt = sum((v - 0.5) ** 2 for v in filtered) / len(filtered)
    assert var_filt < var_raw * 0.5, f"var_filt={var_filt} not much lower than var_raw={var_raw}"


def check_payload_and_stats() -> None:
    ev = GestureEvent("pinch_start", "right", 0.98, 0.52345, 0.38123, 12.5, {"extra": 1})
    p = ev.to_payload("window_control")
    assert set(p.keys()) == {"source", "gesture", "confidence", "hand", "position", "mode", "t", "extra"}
    assert p["source"] == "gesture"
    assert p["position"] == {"x": 0.523, "y": 0.381}
    assert p["mode"] == "window_control"
    assert p["t"] == 12.5

    st = Stats()
    for i in range(20):
        # 20 frames at 20 fps (span = 0.95 s), detect_ms = 1..20.
        st.add(float(i + 1), float(i + 2), i * 0.05, 1 if i < 10 else 0)
    s = st.summary()
    assert s["frames"] == 20
    assert s["hand_frames"] == 10
    assert abs(s["fps"] - 20.0) < 0.2
    assert s["detect_ms_avg"] == 10.5
    assert s["detect_ms_p95"] == 19.0
    assert s["loop_ms_avg"] == 11.5


def check_sysfs_cameras() -> None:
    with tempfile.TemporaryDirectory(prefix="lura-sysfs-") as tmp:
        root = Path(tmp)
        # video0: uvcvideo capture node (index 0)
        v0 = root / "video0"
        (v0 / "device").mkdir(parents=True)
        (v0 / "name").write_text("Integrated Camera: Integrated C\n", encoding="utf-8")
        (v0 / "index").write_text("0\n", encoding="utf-8")
        (v0 / "device" / "driver").symlink_to("../../../../bus/usb/drivers/uvcvideo")

        # video1: uvcvideo metadata node (index 1)
        v1 = root / "video1"
        (v1 / "device").mkdir(parents=True)
        (v1 / "name").write_text("Integrated Camera: Integrated C\n", encoding="utf-8")
        (v1 / "index").write_text("1\n", encoding="utf-8")
        (v1 / "device" / "driver").symlink_to("../../../../bus/usb/drivers/uvcvideo")

        # video9: v4l2loopback virtual camera (device/driver symlink form)
        v9 = root / "video9"
        (v9 / "device").mkdir(parents=True)
        (v9 / "name").write_text("Mobile Webcam\n", encoding="utf-8")
        (v9 / "index").write_text("0\n", encoding="utf-8")
        (v9 / "device" / "driver").symlink_to("../../../../bus/platform/drivers/v4l2loopback")

        # video10: kernel virtual v4l2loopback node (no device/ symlink, lives under virtual/)
        v10_real = root / "virtual" / "video4linux" / "video10"
        v10_real.mkdir(parents=True)
        (v10_real / "name").write_text("Mobile Webcam 2\n", encoding="utf-8")
        (v10_real / "index").write_text("0\n", encoding="utf-8")
        (v10_real / "max_openers").write_text("10\n", encoding="utf-8")
        (root / "video10").symlink_to(v10_real)

        cams = list_cameras(root)
        assert [c.index for c in cams] == [0, 1, 9, 10]
        assert [c.usable for c in cams] == [True, False, False, False]
        assert cams[0].driver == "uvcvideo"
        assert cams[2].driver == "v4l2loopback"
        assert cams[3].driver == "v4l2loopback"

        assert pick_camera(cams) == cams[0]
        assert pick_camera(cams, "9") == cams[2], "a named phone camera (loopback) is selectable"
        assert pick_camera(cams, "mobile") == cams[2]
        assert pick_camera(cams, "10") == cams[3]
        assert pick_camera(cams, "1") is None, "a metadata node never is"
        assert pick_camera(cams, "11") is None
        assert pick_camera(cams, "integrated") == cams[0]
        assert pick_camera(cams, "0") == cams[0]


class FakeCamera:
    """In-memory Camera double with scripted disconnect after N reads on first open."""

    def __init__(self, disconnect_after: int | None = None) -> None:
        self.opens = 0
        self.closes = 0
        self._reads = 0
        self._disconnect_after = disconnect_after
        self._miss_remaining = 0

    @property
    def name(self) -> str:
        return "FakeCam"

    def open(self) -> None:
        self.opens += 1
        self._reads = 0
        self._miss_remaining = 0

    def read(self) -> object | None:
        time.sleep(0.002)
        if self._disconnect_after is not None and self.opens == 1:
            if self._reads >= self._disconnect_after:
                return None
        self._reads += 1
        return object()

    def close(self) -> None:
        self.closes += 1


class FakeLandmarker:
    """Returns scripted hands per detect() call, checking monotonic timestamps."""

    def __init__(self, script: list[tuple[Hand, ...]]) -> None:
        self._script = list(script)
        self._idx = 0
        self.last_ts: int | None = None
        self.closed = False

    def detect(self, frame: object, t_ms: int) -> tuple[Hand, ...]:
        if self.last_ts is not None:
            assert t_ms > self.last_ts, f"non-monotonic timestamp {t_ms} <= {self.last_ts}"
        self.last_ts = t_ms
        if self._idx < len(self._script):
            out = self._script[self._idx]
            self._idx += 1
            return out
        return ()

    def close(self) -> None:
        self.closed = True


def _wait_for(pred, timeout: float = 4.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if pred():
            return True
        time.sleep(0.01)
    return False


def check_air_service() -> None:
    # Build a scripted swipe_right sequence (6 stabilize + 8 move + 4 settle).
    script: list[tuple[Hand, ...]] = []
    for _ in range(6):
        script.append((make_hand("open_palm", cx=0.2, cy=0.5),))
    for i in range(1, 9):
        script.append((make_hand("open_palm", cx=0.2 + 0.35 * (i / 8.0), cy=0.5),))
    for _ in range(4):
        script.append((make_hand("open_palm", cx=0.55, cy=0.5),))

    # Fake clock stepping at 30 fps.
    clock_t = [0.0]

    def fake_clock() -> float:
        t = clock_t[0]
        clock_t[0] += 1.0 / 30.0
        return t

    published: list[tuple[str, dict]] = []
    sub_events: list[GestureEvent] = []
    raise_once = [True]

    def pub(name: str, payload: dict) -> None:
        published.append((name, payload))
        if raise_once[0]:
            raise_once[0] = False
            raise RuntimeError("subscriber boom")

    cam = FakeCamera(disconnect_after=len(script) + 2)
    lm = FakeLandmarker(script)
    svc = AirService(
        pub,
        camera_factory=lambda: cam,
        landmarker_factory=lambda: lm,
        clock=fake_clock,
    )
    svc.subscribe(sub_events.append)

    svc.start()
    thread1 = svc._thread
    svc.start()  # idempotent: must not start a second thread
    assert svc._thread is thread1

    assert _wait_for(lambda: any(
        n == events.AIR_GESTURE and p["gesture"] == "swipe_right" for n, p in published
    )), f"swipe_right not published: {published}"

    # Verify swipe payload shape (docs/001 §3 + t).
    swipe_payloads = [
        p for n, p in published if n == events.AIR_GESTURE and p["gesture"] == "swipe_right"
    ]
    assert len(swipe_payloads) == 1
    sp = swipe_payloads[0]
    assert set(sp.keys()) == {"source", "gesture", "confidence", "hand", "position", "mode", "t"}
    assert sp["source"] == "gesture" and sp["mode"] == "universal"

    # Wait for disconnect (15 Nones -> lost) and automatic reopen (~2 s retry).
    assert _wait_for(lambda: any(
        n == events.AIR_STATUS and p["camera"] == "lost" for n, p in published
    ), timeout=4.0), f"lost status not published: {published}"
    assert _wait_for(lambda: cam.opens >= 2 and svc.snapshot().camera == "on", timeout=4.0), (
        f"camera did not reopen: opens={cam.opens}, state={svc.snapshot()}"
    )

    # Thread survived publish raising on first event.
    assert svc.running
    assert any(e.gesture == "swipe_right" for e in sub_events)
    assert "cpu_percent" in svc.stats() and "rss_mb" in svc.stats()

    t0 = time.monotonic()
    svc.stop()
    assert (time.monotonic() - t0) < 2.0
    assert not svc.running
    assert svc.snapshot().camera == "off"
    assert lm.closed
    assert any(n == events.AIR_STATUS and p["camera"] == "off" for n, p in published)


def check_import_hygiene() -> None:
    code = (
        "import sys, lura.modules.air; "
        "assert 'cv2' not in sys.modules and 'mediapipe' not in sys.modules"
    )
    res = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        check=False,
    )
    assert res.returncode == 0, f"import hygiene failed: {res.stderr}"


def main() -> int:
    for name, fn in list(globals().items()):
        if name.startswith("check_"):
            fn()
            print(f"  {name[6:]} OK")
    print("air OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
