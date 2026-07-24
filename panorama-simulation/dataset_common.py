"""Shared setup for the dataset tools: config loading + camera construction."""

from __future__ import annotations

import logging
import os

import numpy as np
import yaml

from panorama_camera import CameraConfig, PanoramaCamera
from scene import MonkeyScene, SceneConfig

log = logging.getLogger("dataset")

# Default dataset config, merged under any ``dataset:`` block in config.yaml so
# the tools work even against an older config file.
DEFAULT_DATASET = {
    "output_dir": "datasets",
    "base_image": "panorama-base.jpg",
    "monkey_images_dir": "monkey-images",
    "working_height": 1080,
    "horizontal_fov_deg": 150.0,
    "angle_range_deg": 50.0,
    "min_monkeys": 1,
    "max_monkeys": 6,
    "monkey_scale_min": 0.12,
    "monkey_scale_max": 0.28,
    "y_frac_min": 0.35,
    "y_frac_max": 0.70,
    "cluster_prob": 0.5,
    "cluster_spread_deg": 10.0,
    "fps": 25,
    "episode_seconds": 20.0,
    # Camera motion caps (the pan motor is slow): hard max speed + accel limit and
    # the proportional tracking gain. Used for search, centering, AND tracking a
    # walking monkey, so nothing ever exceeds max_pan_speed_deg_per_sec.
    "max_pan_speed_deg_per_sec": 5.0,
    "pan_accel_deg_per_sec2": 8.0,
    "pan_gain": 2.0,
    # Monkey motion: rest this long (s), then walk at a slow constant speed (deg/s).
    "animate_monkeys": True,
    "rest_min_seconds": 5.0,
    "rest_max_seconds": 10.0,
    "walk_speed_min_deg_per_sec": 1.0,
    "walk_speed_max_deg_per_sec": 3.0,
    "instructions": [
        "center the closest monkey",
        "track the one on the left",
        "track the one on the right",
        "center the largest monkey",
    ],
    "num_episodes": 20,
    "seed": 0,
    "manual_step_deg": 3.0,                  # setpoint nudge per Left/Right press
    "manual_speed_init_deg_per_sec": 5.0,    # starting move speed (clamped to max)
    "manual_speed_step_deg_per_sec": 0.5,    # speed change per Up/Down press
    "manual_instruction": "center the closest monkey",
}


def load_config(path: str) -> dict:
    with open(path) as f:
        cfg = yaml.safe_load(f) or {}
    ds = {**DEFAULT_DATASET, **(cfg.get("dataset") or {})}
    cfg["dataset"] = ds
    return cfg


def setup_logging(cfg: dict) -> None:
    logging.basicConfig(
        level=getattr(logging, cfg.get("logging", {}).get("level", "INFO")),
        format="%(asctime)s %(name)-8s %(levelname)-7s %(message)s",
        datefmt="%H:%M:%S",
    )


def build_scene(cfg: dict) -> MonkeyScene:
    """Load the base panorama + monkey images once (reused across episodes)."""
    return MonkeyScene(SceneConfig.from_dict(cfg.get("camera", {}), cfg["dataset"]))


def make_camera(cfg: dict, scene: MonkeyScene, composited: np.ndarray) -> PanoramaCamera:
    """A PanoramaCamera over an already-composited (monkeys baked in) panorama."""
    cam = cfg.get("camera", {})
    cam_cfg = CameraConfig(
        output_width=cam.get("output_width", 1280),
        output_height=cam.get("output_height", 720),
        fps=cfg["dataset"].get("fps", cam.get("fps", 25)),
        invert_pan=cam.get("invert_pan", True),
        overlay=False,  # dataset frames are clean; replay/manual draw their own HUD
        camera_fov_deg=cam.get("camera_fov_deg"),
    )
    limit = cfg.get("motor", {}).get("angle_limit_deg", 60.0)
    return PanoramaCamera(
        image_path=None,
        working_height=scene.pano_h,
        horizontal_fov_deg=scene.cfg.horizontal_fov_deg,
        angle_limit_deg=limit,
        cam_cfg=cam_cfg,
        pano_array=composited,
    )


def output_root(cfg: dict, override: str | None) -> str:
    root = override or cfg["dataset"]["output_dir"]
    if not os.path.isabs(root):
        root = os.path.join(os.path.dirname(__file__), root)
    return root
