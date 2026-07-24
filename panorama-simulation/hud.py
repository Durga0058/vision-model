"""On-screen overlay for the dataset video: angles, monkey count, pan bar.

Draws onto BGR numpy frames with OpenCV primitives (no font files needed), so it
is shared by the manual recorder's live window and the replay viewer. Colours are
BGR tuples.
"""

from __future__ import annotations

import cv2
import numpy as np

WHITE = (255, 255, 255)
GREEN = (0, 255, 0)      # current angle
AMBER = (0, 191, 255)    # target angle
BLUE = (255, 120, 0)     # frame centre / midpoint
RED = (0, 0, 255)        # recording
GREY = (150, 150, 150)
CYAN = (255, 255, 0)     # centre crosshair

_FONT = cv2.FONT_HERSHEY_SIMPLEX


def _text(img, txt, org, color=WHITE, scale=0.6, thick=1):
    cv2.putText(img, txt, org, _FONT, scale, (0, 0, 0), thick + 2, cv2.LINE_AA)
    cv2.putText(img, txt, org, _FONT, scale, color, thick, cv2.LINE_AA)


def draw_hud(
    frame_bgr: np.ndarray,
    *,
    current_angle: float,
    target_angle: float,
    monkey_count: int,
    instruction: str,
    limit_deg: float,
    recording: bool | None = None,
    extra_lines: list[str] | None = None,
    center_marker: bool = False,
) -> np.ndarray:
    """Overlay the label state onto ``frame_bgr`` in place and return it.

    ``center_marker`` draws a vertical centre line + a plus at the frame centre so
    the operator can see whether the target is aligned — it is a display-only aid
    and must never be drawn onto frames written to the dataset video.
    """
    h, w = frame_bgr.shape[:2]

    if center_marker:
        _draw_center_marker(frame_bgr)

    lines = [
        f"instruction: {instruction}",
        f"current: {current_angle:+6.2f} deg",
        f"target : {target_angle:+6.2f} deg",
        f"monkeys: {monkey_count}",
    ]
    if extra_lines:
        lines.extend(extra_lines)

    # Translucent panel behind the text.
    pad = 8
    box_h = 10 + 24 * len(lines)
    box_w = 330
    overlay = frame_bgr.copy()
    cv2.rectangle(overlay, (8, 8), (8 + box_w, 8 + box_h), (0, 0, 0), -1)
    cv2.addWeighted(overlay, 0.45, frame_bgr, 0.55, 0, frame_bgr)
    for i, line in enumerate(lines):
        _text(frame_bgr, line, (16, 32 + 24 * i))

    # Recording indicator (top-right).
    if recording is not None:
        col = RED if recording else GREY
        cv2.circle(frame_bgr, (w - 30, 30), 10, col, -1)
        _text(frame_bgr, "REC" if recording else "IDLE",
              (w - 110, 36), col, scale=0.6, thick=2)

    # Pan-position bar along the bottom: current (green) vs target (amber).
    _draw_pan_bar(frame_bgr, current_angle, target_angle, limit_deg)
    return frame_bgr


def _draw_center_marker(frame_bgr):
    """Vertical centre line + a plus at the exact frame centre (alignment aid)."""
    h, w = frame_bgr.shape[:2]
    cx, cy = w // 2, h // 2
    arm = max(14, h // 40)
    # Full-height vertical line (dark outline under a bright core for contrast).
    cv2.line(frame_bgr, (cx, 0), (cx, h), (0, 0, 0), 3)
    cv2.line(frame_bgr, (cx, 0), (cx, h), CYAN, 1)
    # Plus sign at the centre.
    for (p0, p1) in (((cx - arm, cy), (cx + arm, cy)), ((cx, cy - arm), (cx, cy + arm))):
        cv2.line(frame_bgr, p0, p1, (0, 0, 0), 4)
        cv2.line(frame_bgr, p0, p1, CYAN, 2)


def _draw_pan_bar(frame_bgr, current_angle, target_angle, limit_deg):
    h, w = frame_bgr.shape[:2]
    y = h - 26
    x0, x1 = 16, w - 16
    cv2.rectangle(frame_bgr, (x0, y), (x1, y + 12), WHITE, 1)

    def frac_to_px(deg):
        frac = (deg + limit_deg) / (2 * limit_deg) if limit_deg else 0.5
        frac = min(1.0, max(0.0, frac))
        return int(x0 + frac * (x1 - x0))

    mid = int(x0 + 0.5 * (x1 - x0))
    cv2.line(frame_bgr, (mid, y - 3), (mid, y + 15), BLUE, 1)

    tx = frac_to_px(target_angle)
    cv2.line(frame_bgr, (tx, y - 5), (tx, y + 17), AMBER, 2)

    cx = frac_to_px(current_angle)
    cv2.rectangle(frame_bgr, (cx - 3, y - 3), (cx + 3, y + 15), GREEN, -1)
