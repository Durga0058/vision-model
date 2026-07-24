"""Monkey scene: places monkeys on the base panorama and provides ground truth.

This is the source of truth for every training label. The base panorama is loaded
once and rescaled to a working height; per episode, monkeys are composited onto a
copy of it at pixel positions derived from an assigned *pan angle* — the angle the
camera must move to in order to center that monkey. Because the placement uses the
exact same angle->pixel mapping as ``PanoramaCamera``, "move the camera to a
monkey's angle" always centers it in the viewport.

From the known layout the scene answers the two questions the VLA is trained on:

  * ``select_target(instruction, current_angle)`` -> the monkey an instruction
    refers to (closest / leftmost / rightmost / largest) and hence the correct
    absolute target angle.
  * ``count_in_crop(x0, x1)`` -> how many monkeys are visible in a given viewport.

See ``vla-training.md`` for how these become the (target_angle, monkey_count)
supervision signal.
"""

from __future__ import annotations

import glob
import logging
import math
import os
from dataclasses import dataclass

import numpy as np
from PIL import Image, ImageOps

log = logging.getLogger("scene")

Image.MAX_IMAGE_PIXELS = None  # the base panorama is large (21500x3840)

_IMG_EXTS = ("*.png", "*.jpg", "*.jpeg", "*.webp")


def _reflect(x: float, lo: float, hi: float) -> float:
    """Fold x into [lo, hi] by reflection (a walking monkey bounces off the edges)."""
    if hi <= lo:
        return lo
    span = hi - lo
    y = (x - lo) % (2 * span)
    return lo + (y if y <= span else 2 * span - y)


@dataclass
class SceneConfig:
    base_image: str
    monkey_images_dir: str
    working_height: int = 1080
    # Field of view the *whole* base panorama spans. This sets pixels-per-degree
    # and therefore how far ±angle_range maps across the image. It must be wide
    # enough that the requested angle range fits inside the image (a warning is
    # logged otherwise). For panorama-base.jpg, ~150° fits ±50°.
    horizontal_fov_deg: float = 150.0
    # Output aspect (used to size the viewport for edge/visibility maths).
    output_width: int = 1280
    output_height: int = 720
    # Per-frame camera HFOV (see CameraConfig.camera_fov_deg). Must match the
    # camera so the visible viewport used for placement/counting is correct.
    camera_fov_deg: float | None = None
    invert_pan: bool = True
    # Monkey placement.
    angle_range_deg: float = 50.0            # monkeys placed within ±this
    min_monkeys: int = 1
    max_monkeys: int = 6
    monkey_scale_min: float = 0.12           # monkey height as fraction of pano H
    monkey_scale_max: float = 0.28
    y_frac_min: float = 0.35                 # vertical band for monkey centers
    y_frac_max: float = 0.70
    # Horizontal distribution: how likely an episode clusters monkeys together
    # vs. spreads them out, and how tight a cluster is.
    cluster_prob: float = 0.5
    cluster_spread_deg: float = 10.0
    apply_exif: bool = True
    # Monkey motion: after resting rest_[min,max] seconds a monkey walks at a slow
    # constant angular speed (deg/s) in a random direction, bouncing within the
    # angle range. animate=False keeps every monkey stationary.
    animate: bool = False
    rest_min_seconds: float = 5.0
    rest_max_seconds: float = 10.0
    walk_speed_min_deg_per_sec: float = 1.0
    walk_speed_max_deg_per_sec: float = 3.0

    @classmethod
    def from_dict(cls, cam: dict, ds: dict) -> "SceneConfig":
        """Build from the ``camera`` + ``dataset`` config.yaml sections."""
        return cls(
            base_image=ds["base_image"],
            monkey_images_dir=ds["monkey_images_dir"],
            working_height=ds.get("working_height", 1080),
            horizontal_fov_deg=ds.get("horizontal_fov_deg", 150.0),
            output_width=cam.get("output_width", 1280),
            output_height=cam.get("output_height", 720),
            camera_fov_deg=cam.get("camera_fov_deg"),
            invert_pan=cam.get("invert_pan", True),
            angle_range_deg=ds.get("angle_range_deg", 50.0),
            min_monkeys=ds.get("min_monkeys", 1),
            max_monkeys=ds.get("max_monkeys", 6),
            monkey_scale_min=ds.get("monkey_scale_min", 0.12),
            monkey_scale_max=ds.get("monkey_scale_max", 0.28),
            y_frac_min=ds.get("y_frac_min", 0.35),
            y_frac_max=ds.get("y_frac_max", 0.70),
            cluster_prob=ds.get("cluster_prob", 0.5),
            cluster_spread_deg=ds.get("cluster_spread_deg", 10.0),
            apply_exif=ds.get("apply_exif", True),
            animate=ds.get("animate_monkeys", False),
            rest_min_seconds=ds.get("rest_min_seconds", 5.0),
            rest_max_seconds=ds.get("rest_max_seconds", 10.0),
            walk_speed_min_deg_per_sec=ds.get("walk_speed_min_deg_per_sec", 1.0),
            walk_speed_max_deg_per_sec=ds.get("walk_speed_max_deg_per_sec", 3.0),
        )


@dataclass
class Monkey:
    angle_deg: float   # resting pan angle that centers this monkey (its base position)
    y_frac: float      # vertical center as fraction of panorama height
    scale: float       # height as fraction of panorama height
    image_index: int   # which source monkey image
    # Motion: stays at angle_deg until rest_end, then walks at walk_speed (signed
    # deg/s), bouncing within the angle range. walk_speed 0 / rest_end inf = static.
    rest_end: float = float("inf")
    walk_speed: float = 0.0
    # Filled in once composited onto the panorama (static-bake path only):
    x_px: float = 0.0  # horizontal center in panorama pixels
    y_px: float = 0.0
    w_px: int = 0
    h_px: int = 0

    def to_dict(self) -> dict:
        return {
            "angle_deg": round(self.angle_deg, 4),
            "y_frac": round(self.y_frac, 4),
            "scale": round(self.scale, 4),
            "image_index": self.image_index,
            "rest_end": None if math.isinf(self.rest_end) else round(self.rest_end, 3),
            "walk_speed": round(self.walk_speed, 4),
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Monkey":
        rest = d.get("rest_end")
        return cls(
            angle_deg=d["angle_deg"], y_frac=d["y_frac"],
            scale=d["scale"], image_index=d["image_index"],
            rest_end=float("inf") if rest is None else rest,
            walk_speed=d.get("walk_speed", 0.0),
        )


# Map a natural-language instruction to a target-selection strategy by keyword.
def _instruction_strategy(instruction: str) -> str:
    s = instruction.lower()
    if "left" in s:
        return "leftmost"
    if "right" in s:
        return "rightmost"
    if "large" in s or "big" in s or "closest-large" in s:
        return "largest"
    if "small" in s:
        return "smallest"
    return "closest"


class MonkeyScene:
    """Holds the base panorama and composites monkey layouts onto it."""

    def __init__(self, cfg: SceneConfig):
        self.cfg = cfg

        base_path = cfg.base_image
        if not os.path.isabs(base_path):
            base_path = os.path.join(os.path.dirname(__file__), base_path)
        img = Image.open(base_path)
        if cfg.apply_exif:
            img = ImageOps.exif_transpose(img)
        img = img.convert("RGB")
        scale_w = round(img.width * cfg.working_height / img.height)
        img = img.resize((scale_w, cfg.working_height), Image.BILINEAR)
        self.base_pano = np.asarray(img)
        self.pano_h, self.pano_w = self.base_pano.shape[:2]

        self.ppd = self.pano_w / cfg.horizontal_fov_deg
        self.center_x = self.pano_w / 2.0
        self._sign = 1.0 if cfg.invert_pan else -1.0

        # Viewport size (must match PanoramaCamera). With camera_fov_deg set the
        # camera crops that many degrees wide and a shorter, vertically-centered
        # window; otherwise it uses the full panorama height.
        aspect = cfg.output_width / cfg.output_height
        if cfg.camera_fov_deg:
            self.view_w = min(int(round(cfg.camera_fov_deg * self.ppd)), self.pano_w)
            self.view_h = min(int(round(self.view_w / aspect)), self.pano_h)
        else:
            self.view_w = min(int(round(self.pano_h * aspect)), self.pano_w)
            self.view_h = self.pano_h
        # Visible vertical band (fraction of height) the camera actually shows,
        # so monkeys are only placed where they will be seen.
        self.y_band = (
            (self.pano_h - self.view_h) / 2.0 / self.pano_h,
            (self.pano_h + self.view_h) / 2.0 / self.pano_h,
        )

        # Warn if the requested angle range does not fit inside the image.
        room_px = self.center_x - self.view_w / 2.0
        need_px = cfg.angle_range_deg * self.ppd
        if need_px > room_px + 0.5:
            fit_deg = room_px / self.ppd
            log.warning(
                "angle_range ±%.0f° does not fit the panorama (need %.0f px of "
                "pan room, have %.0f). Monkeys beyond ±%.1f° will clamp at the "
                "image edge. Increase dataset.horizontal_fov_deg or reduce "
                "angle_range_deg.",
                cfg.angle_range_deg, need_px, room_px, fit_deg,
            )

        # Load monkey source images (RGBA for alpha compositing).
        mdir = cfg.monkey_images_dir
        if not os.path.isabs(mdir):
            mdir = os.path.join(os.path.dirname(__file__), mdir)
        paths: list[str] = []
        for pat in _IMG_EXTS:
            paths.extend(sorted(glob.glob(os.path.join(mdir, pat))))
        if not paths:
            raise FileNotFoundError(f"No monkey images found in {mdir}")
        self.monkey_srcs = [Image.open(p).convert("RGBA") for p in paths]
        self.monkey_paths = paths

        log.info(
            "Scene: pano %dx%d, %.2f px/deg, viewport width %d, %d monkey images",
            self.pano_w, self.pano_h, self.ppd, self.view_w, len(self.monkey_srcs),
        )

    # -------------------------------------------------------------- geometry
    def angle_to_x(self, angle_deg: float) -> float:
        """Panorama pixel x where a monkey at this pan angle is centered."""
        return self.center_x + self._sign * angle_deg * self.ppd

    def angle_at(self, m: Monkey, t: float) -> float:
        """A monkey's pan angle at time ``t`` (rest, then a bouncing slow walk)."""
        if m.walk_speed == 0.0 or t <= m.rest_end:
            return m.angle_deg
        raw = m.angle_deg + m.walk_speed * (t - m.rest_end)
        R = self.cfg.angle_range_deg
        return _reflect(raw, -R, R)

    def x_at(self, m: Monkey, t: float) -> float:
        """A monkey's panorama pixel x at time ``t``."""
        return self.angle_to_x(self.angle_at(m, t))

    # ------------------------------------------------------------- layout gen
    def random_layout(self, rng: np.random.Generator,
                      animate: bool | None = None) -> list[Monkey]:
        """Sample a random set of monkeys for one episode.

        Placement mixes clustered ("together") and spread ("far apart") episodes
        so the model sees monkeys in every relative arrangement. When ``animate``
        (defaults to ``SceneConfig.animate``) each monkey also gets a rest-then-walk
        motion.
        """
        c = self.cfg
        animate = c.animate if animate is None else animate
        n = int(rng.integers(c.min_monkeys, c.max_monkeys + 1))
        rng_lo, rng_hi = -c.angle_range_deg, c.angle_range_deg

        if rng.random() < c.cluster_prob and n > 1:
            # Clustered: draw around one (or two) centers.
            n_centers = 1 if (n <= 3 or rng.random() < 0.5) else 2
            margin = min(c.cluster_spread_deg, c.angle_range_deg)
            centers = rng.uniform(rng_lo + margin, rng_hi - margin, size=n_centers)
            angles = []
            for i in range(n):
                cen = centers[i % n_centers]
                angles.append(cen + rng.normal(0.0, c.cluster_spread_deg))
        else:
            # Spread: independent uniform angles across the whole range.
            angles = list(rng.uniform(rng_lo, rng_hi, size=n))

        monkeys: list[Monkey] = []
        band_lo, band_hi = self.y_band
        for a in angles:
            scale = float(rng.uniform(c.monkey_scale_min, c.monkey_scale_max))
            # Keep the monkey's requested vertical band, but clamp its center so
            # the whole monkey stays inside what the camera actually sees.
            lo = max(c.y_frac_min, band_lo + scale / 2.0)
            hi = min(c.y_frac_max, band_hi - scale / 2.0)
            if lo > hi:  # very tall monkey / narrow band: fall back to center
                lo = hi = (band_lo + band_hi) / 2.0
            rest_end, walk_speed = float("inf"), 0.0
            if animate:
                rest_end = float(rng.uniform(c.rest_min_seconds, c.rest_max_seconds))
                speed = float(rng.uniform(c.walk_speed_min_deg_per_sec,
                                          c.walk_speed_max_deg_per_sec))
                walk_speed = speed if rng.random() < 0.5 else -speed
            monkeys.append(Monkey(
                angle_deg=float(np.clip(a, rng_lo, rng_hi)),
                y_frac=float(rng.uniform(lo, hi)),
                scale=scale,
                image_index=int(rng.integers(0, len(self.monkey_srcs))),
                rest_end=rest_end,
                walk_speed=walk_speed,
            ))
        return monkeys

    # --------------------------------------------------------------- compose
    def build(self, monkeys: list[Monkey]) -> np.ndarray:
        """Composite ``monkeys`` onto a copy of the base panorama.

        Mutates each monkey with its resolved pixel geometry and returns a new
        RGB uint8 array suitable for ``PanoramaCamera(pano_array=...)``.
        """
        canvas = Image.fromarray(self.base_pano.copy())
        for m in monkeys:
            src = self.monkey_srcs[m.image_index % len(self.monkey_srcs)]
            h_px = max(1, int(round(m.scale * self.pano_h)))
            w_px = max(1, int(round(h_px * src.width / src.height)))
            resized = src.resize((w_px, h_px), Image.LANCZOS)
            cx = self.angle_to_x(m.angle_deg)
            cy = m.y_frac * self.pano_h
            x = int(round(cx - w_px / 2.0))
            y = int(round(cy - h_px / 2.0))
            canvas.paste(resized, (x, y), resized)  # alpha as mask; PIL clips
            m.x_px, m.y_px, m.w_px, m.h_px = cx, cy, w_px, h_px
        return np.asarray(canvas)

    # --------------------------------------------------- dynamic frame render
    def render_view(self, camera, angle_deg: float, monkeys: list[Monkey],
                    t: float) -> np.ndarray:
        """Render the output frame at time ``t`` with monkeys at their live
        positions. ``camera.pano`` must be the clean base panorama (no monkeys
        baked in); every in-view monkey is composited onto the crop each frame so
        moving monkeys animate. Returns an (H, W, 3) uint8 RGB frame."""
        x0, x1 = camera.crop_bounds(angle_deg)
        y0, y1 = camera.y0, camera.y1
        img = Image.fromarray(np.ascontiguousarray(camera.pano[y0:y1, x0:x1]))
        for m in monkeys:
            xa = self.x_at(m, t)
            if not (x0 <= xa < x1):
                continue
            src = self.monkey_srcs[m.image_index % len(self.monkey_srcs)]
            h_px = max(1, int(round(m.scale * self.pano_h)))
            w_px = max(1, int(round(h_px * src.width / src.height)))
            resized = src.resize((w_px, h_px), Image.LANCZOS)
            cx = xa - x0
            cy = m.y_frac * self.pano_h - y0
            img.paste(resized, (int(round(cx - w_px / 2.0)),
                                int(round(cy - h_px / 2.0))), resized)
        frame = img.resize((camera.cfg.output_width, camera.cfg.output_height),
                           Image.BILINEAR)
        return np.asarray(frame)

    # ----------------------------------------------------------- ground truth
    def count_in_crop(self, monkeys: list[Monkey], x0: int, x1: int) -> int:
        """Static-bake path: monkeys whose baked center falls within [x0, x1)."""
        return sum(1 for m in monkeys if x0 <= m.x_px < x1)

    def count_in_view(self, camera, angle_deg: float, monkeys: list[Monkey],
                      t: float) -> int:
        """Monkeys whose live center (at time ``t``) falls within the viewport."""
        x0, x1 = camera.crop_bounds(angle_deg)
        return sum(1 for m in monkeys if x0 <= self.x_at(m, t) < x1)

    def select_target(
        self, instruction: str, current_angle: float, monkeys: list[Monkey],
        t: float = 0.0,
    ) -> Monkey | None:
        """Return the monkey an instruction refers to at time ``t`` (None if empty)."""
        if not monkeys:
            return None
        strat = _instruction_strategy(instruction)
        # "left"/"right" mean where the monkey appears on screen. Screen-x grows
        # with sign*angle (sign follows invert_pan), so key off that rather than
        # the raw angle to stay correct under either pan convention.
        screen_x = lambda m: self._sign * self.angle_at(m, t)
        if strat == "leftmost":
            return min(monkeys, key=screen_x)
        if strat == "rightmost":
            return max(monkeys, key=screen_x)
        if strat == "largest":
            return max(monkeys, key=lambda m: m.scale)
        if strat == "smallest":
            return min(monkeys, key=lambda m: m.scale)
        # closest: nearest in angle to where the camera is now pointing.
        return min(monkeys, key=lambda m: abs(self.angle_at(m, t) - current_angle))
