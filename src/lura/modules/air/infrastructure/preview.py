"""Camera preview rendering: draw 21 hand landmarks, bones and a status chip, then encode JPEG."""

from __future__ import annotations

import cv2
import numpy as np

HAND_BONES = (
    (0, 1), (1, 2), (2, 3), (3, 4),
    (0, 5), (5, 6), (6, 7), (7, 8),
    (5, 9), (9, 10), (10, 11), (11, 12),
    (9, 13), (13, 14), (14, 15), (15, 16),
    (13, 17), (0, 17), (17, 18), (18, 19), (19, 20),
)
FINGERTIPS = (4, 8, 12, 16, 20)
JOINT_RGB = (255, 210, 0)        # yellow joints
BONE_RGB = (255, 240, 170)       # pale yellow bones
OUTLINE_RGB = (30, 30, 30)       # dark edge so it reads on any background
PREVIEW_JPEG_QUALITY = 75


def render_preview(frame, hands, label: str) -> bytes:
    """Draw tracked hands and an optional status chip onto a copy of ``frame`` and return JPEG bytes."""
    img = np.ascontiguousarray(frame).copy()
    h, w = img.shape[:2]

    for hand in hands:
        pts = [(round(pt.x * w), round(pt.y * h)) for pt in hand.points]
        for a, b in HAND_BONES:
            cv2.line(img, pts[a], pts[b], OUTLINE_RGB, 5, lineType=cv2.LINE_AA)
        for a, b in HAND_BONES:
            cv2.line(img, pts[a], pts[b], BONE_RGB, 2, lineType=cv2.LINE_AA)
        for i, p in enumerate(pts):
            r = 6 if i in FINGERTIPS else 4
            cv2.circle(img, p, r + 1, OUTLINE_RGB, -1, lineType=cv2.LINE_AA)
        for i, p in enumerate(pts):
            r = 6 if i in FINGERTIPS else 4
            cv2.circle(img, p, r, JOINT_RGB, -1, lineType=cv2.LINE_AA)

    if label:
        (tw, th), baseline = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, 0.6, 2)
        pad = 8
        x0, y0 = 10, 10
        x1 = min(w, x0 + tw + pad * 2)
        y1 = min(h, y0 + th + baseline + pad * 2)
        if x1 > x0 and y1 > y0:
            roi = img[y0:y1, x0:x1]
            dark = roi.copy()
            dark[:] = (15, 15, 15)
            cv2.addWeighted(dark, 0.55, roi, 0.45, 0.0, dst=roi)
            cv2.putText(
                img,
                label,
                (x0 + pad, y0 + pad + th),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.6,
                (255, 255, 255),
                2,
                lineType=cv2.LINE_AA,
            )

    ok, buf = cv2.imencode(
        ".jpg",
        cv2.cvtColor(img, cv2.COLOR_RGB2BGR),
        [cv2.IMWRITE_JPEG_QUALITY, PREVIEW_JPEG_QUALITY],
    )
    if not ok:
        raise RuntimeError("preview encode failed")
    return buf.tobytes()
