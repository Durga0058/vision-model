"""Renders the camera viewport into the panorama for a given motor angle.

The panorama is loaded once, EXIF-rotated, and rescaled to a working height so
per-frame cropping is cheap. A motor angle is mapped to a horizontal pixel offset
via the panorama's horizontal field of view; the viewport (full height, output
aspect ratio) is cropped and resized to the output resolution.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import numpy as np
from PIL import Image, ImageDraw, ImageOps, ImageFont

log = logging.getLogger("camera")


@dataclass
class CameraConfig:
    output_width: int = 1280
    output_height: int = 720
    fps: int = 25
    invert_pan: bool = False
    overlay: bool = True
    # Effective per-frame horizontal field of view of the camera, in degrees.
    # When set, the viewport crops exactly this many degrees wide (height derived
    # from the output aspect, centered vertically) instead of using the full
    # panorama height. Set it to the real lens's HFOV so the simulated frames
    # match deployment (this is the value a downstream tracker's camera_fov_deg
    # must agree with). Leave None for the legacy full-height behaviour.
    camera_fov_deg: float | None = None


class PanoramaCamera:
    def __init__(
        self,
        image_path: str | None,
        working_height: int,
        horizontal_fov_deg: float,
        angle_limit_deg: float,
        cam_cfg: CameraConfig,
        apply_exif: bool = True,
        pano_array: np.ndarray | None = None,
    ):
        self.cfg = cam_cfg
        self.horizontal_fov_deg = horizontal_fov_deg
        self.angle_limit_deg = angle_limit_deg

        if pano_array is not None:
            # Use a pre-built panorama (already RGB uint8 at working height).
            # The dataset tools pass a monkey-composited panorama this way so
            # the same crop/overlay path renders both the live stream and the
            # training frames.
            self.pano = np.ascontiguousarray(pano_array)
        else:
            img = Image.open(image_path)
            if apply_exif:
                img = ImageOps.exif_transpose(img)
            img = img.convert("RGB")

            # Rescale to the working height for fast cropping.
            scale_w = round(img.width * working_height / img.height)
            img = img.resize((scale_w, working_height), Image.BILINEAR)
            self.pano = np.asarray(img)
        self.pano_h, self.pano_w = self.pano.shape[:2]

        # Pixels-per-degree across the panorama.
        self.ppd = self.pano_w / horizontal_fov_deg

        # Viewport size. Two modes:
        #  - camera_fov_deg set: crop exactly that many degrees wide, height from
        #    the output aspect, centered vertically (matches a real lens).
        #  - otherwise (legacy): full panorama height, width from output aspect.
        aspect = self.cfg.output_width / self.cfg.output_height
        if self.cfg.camera_fov_deg:
            self.view_w = int(round(self.cfg.camera_fov_deg * self.ppd))
            self.view_w = min(self.view_w, self.pano_w)
            self.view_h = min(int(round(self.view_w / aspect)), self.pano_h)
        else:
            self.view_w = min(int(round(self.pano_h * aspect)), self.pano_w)
            self.view_h = self.pano_h

        self.center_x = self.pano_w / 2.0
        # Vertical crop is fixed and centered (the camera does not tilt).
        self.y0 = (self.pano_h - self.view_h) // 2
        self.y1 = self.y0 + self.view_h
        self.effective_hfov = self.view_w / self.ppd

        # Warn if the configured travel cannot be covered without clamping.
        max_offset = self.angle_limit_deg * self.ppd
        room = self.center_x - self.view_w / 2.0
        if max_offset > room + 0.5:
            log.warning(
                "Panorama too narrow for ±%.0f° travel: viewport will clamp at "
                "the image edges (need %.0f px of pan room, have %.0f).",
                self.angle_limit_deg, max_offset, room,
            )

        try:
            self._font = ImageFont.truetype(
                "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 22
            )
        except OSError:
            self._font = ImageFont.load_default()

        log.info(
            "Panorama %dx%d, %.1f px/deg, viewport %dx%d (effective HFOV %.1f°)",
            self.pano_w, self.pano_h, self.ppd, self.view_w, self.view_h,
            self.effective_hfov,
        )

    def _center_for_angle(self, angle_deg: float) -> float:
        # Positive angle = left rotation => reveal the left (lower-x) side.
        sign = 1.0 if self.cfg.invert_pan else -1.0
        cx = self.center_x + sign * angle_deg * self.ppd
        half = self.view_w / 2.0
        return float(np.clip(cx, half, self.pano_w - half))

    def crop_bounds(self, angle_deg: float) -> tuple[int, int]:
        """Return the horizontal pixel range [x0, x1) of the viewport crop for
        this angle (clamped to the panorama edges). This is the single source of
        truth for "what the camera sees", used both for rendering and for
        deciding which monkeys fall inside the frame."""
        cx = self._center_for_angle(angle_deg)
        x0 = int(round(cx - self.view_w / 2.0))
        x0 = max(0, min(x0, self.pano_w - self.view_w))
        return x0, x0 + self.view_w

    def render(self, angle_deg: float, status: dict | None = None) -> np.ndarray:
        """Return an (H, W, 3) uint8 RGB frame for the given motor angle."""
        x0, x1 = self.crop_bounds(angle_deg)
        crop = self.pano[self.y0:self.y1, x0:x1]

        frame = Image.fromarray(crop).resize(
            (self.cfg.output_width, self.cfg.output_height), Image.BILINEAR
        )
        if self.cfg.overlay:
            self._draw_overlay(frame, angle_deg, status or {})
        return np.asarray(frame)

    def _draw_overlay(self, frame: Image.Image, angle_deg: float, status: dict) -> None:
        d = ImageDraw.Draw(frame)
        w, h = frame.size

        lim = self.angle_limit_deg
        at_left = angle_deg >= lim - 0.05
        at_right = angle_deg <= -lim + 0.05
        sysline = "ARMED" if status.get("system_on") else "OFF"
        if not status.get("calibrated", False) and status.get("system_on"):
            sysline += " (uncalibrated)"

        lines = [
            f"angle: {angle_deg:+6.2f} deg   [{-lim:.0f} .. {lim:.0f}]",
            f"system: {sysline}",
        ]
        if status.get("moving"):
            lines.append("status: MOVING")
        if at_left:
            lines.append("!! LEFT LIMIT SWITCH")
        elif at_right:
            lines.append("!! RIGHT LIMIT SWITCH")

        # Semi-transparent box behind the text.
        d.rectangle([(8, 8), (360, 8 + 26 * len(lines) + 8)], fill=(0, 0, 0))
        for i, line in enumerate(lines):
            color = (255, 80, 80) if line.startswith("!!") else (0, 255, 120)
            d.text((16, 12 + 26 * i), line, fill=color, font=self._font)

        # Pan position bar at the bottom.
        bar_y = h - 24
        d.rectangle([(16, bar_y), (w - 16, bar_y + 10)], outline=(255, 255, 255))
        frac = (angle_deg + lim) / (2 * lim) if lim else 0.5
        px = int(16 + frac * (w - 32))
        d.rectangle([(px - 3, bar_y - 4), (px + 3, bar_y + 14)], fill=(0, 255, 120))
        mid = int(16 + 0.5 * (w - 32))
        d.line([(mid, bar_y - 2), (mid, bar_y + 12)], fill=(120, 120, 255), width=1)
