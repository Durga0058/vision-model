"""Automatic VLA dataset generator.

Generates labeled tracking episodes with no manual annotation, exactly as
described in ``vla-training.md``: the simulator places monkeys at known angles,
picks a language instruction, then simulates the camera panning to center the
instructed monkey using the motor's eased motion profile. Because the simulator
knows every monkey's position, each frame is perfectly labeled with the current
pan angle (proprioception), the correct absolute target angle (output 1) and the
number of monkeys visible (output 2).

Each episode is written as ``episode_XXXX/{video.mp4,labels.jsonl,meta.json}``
(see ``episode.py``). Runs headless — no display, no RTSP, no motor socket.

    python dataset_auto.py                       # 20 episodes -> datasets/
    python dataset_auto.py --episodes 200 --out datasets/train --seed 1
"""

from __future__ import annotations

import argparse
import logging

import numpy as np

from dataset_common import (build_scene, load_config, make_camera, output_root,
                            setup_logging)
from episode import EpisodeWriter, next_episode_dir
from pan_controller import PanController

log = logging.getLogger("auto")


def generate_episode(cfg, scene, rng, ep_dir, created_utc=None) -> dict:
    ds = cfg["dataset"]
    fps = ds["fps"]
    dt = 1.0 / fps
    n_frames = int(round(ds["episode_seconds"] * fps))
    limit = cfg.get("motor", {}).get("angle_limit_deg", 60.0)

    monkeys = scene.random_layout(rng)                    # monkeys rest, then walk
    # Render over the CLEAN base panorama and composite monkeys per frame, so the
    # walking monkeys actually move in the video.
    camera = make_camera(cfg, scene, scene.base_pano)
    instruction = str(rng.choice(ds["instructions"]))

    # Randomly initialize the camera anywhere in its travel — full spread from
    # "target already in the first frame" to "full opposite-direction sweep".
    start = float(rng.uniform(-limit, limit))
    tgt0 = scene.select_target(instruction, start, monkeys, 0.0)
    sweep_dir = 1.0 if (tgt0 is None or tgt0.angle_deg >= start) else -1.0
    ctrl = PanController(
        limit, start_angle=start,
        max_speed_deg_s=ds["max_pan_speed_deg_per_sec"],
        accel_deg_s2=ds.get("pan_accel_deg_per_sec2"),
        gain=ds.get("pan_gain", 2.0),
    )

    writer = EpisodeWriter(ep_dir, camera.cfg.output_width,
                           camera.cfg.output_height, fps)

    acquired = False        # has the instructed monkey been found + locked?
    locked = None           # the specific monkey we committed to tracking
    sweep_goal = sweep_dir * limit

    for i in range(n_frames):
        t = i * dt
        angle = ctrl.angle
        x0, x1 = camera.crop_bounds(angle)
        count = scene.count_in_view(camera, angle, monkeys, t)
        tgt = scene.select_target(instruction, angle, monkeys, t)

        if not acquired and tgt is not None and x0 <= scene.x_at(tgt, t) < x1:
            acquired, locked = True, tgt      # commit to this specific monkey

        if acquired:
            # Track the locked monkey — it may be resting or walking; the capped
            # controller follows it at up to max_pan_speed.
            target_angle = scene.angle_at(locked, t)
            ideal_angle = target_angle
        else:
            # Searching: sweep toward the monkeys; reverse at the limit if empty.
            if abs(angle - sweep_goal) < 0.5:
                sweep_dir = -sweep_dir
                sweep_goal = sweep_dir * limit
            target_angle = sweep_goal          # "keep panning this way"
            ideal_angle = scene.angle_at(tgt, t) if tgt else angle

        ctrl.command(target_angle)
        frame_rgb = scene.render_view(camera, angle, monkeys, t)
        writer.add(frame_rgb, {
            "t": round(t, 4),
            "current_angle": round(angle, 4),
            "target_angle": round(target_angle, 4),
            "monkey_count": count,
            "instruction": instruction,
            "phase": "track" if acquired else "search",
            "ideal_target_angle": round(ideal_angle, 4),
        })
        ctrl.step(dt)

    meta = {
        "mode": "auto",
        "instruction": instruction,
        "fps": fps,
        "duration_s": round(n_frames * dt, 3),
        "output_width": camera.cfg.output_width,
        "output_height": camera.cfg.output_height,
        "angle_limit_deg": limit,
        "angle_range_deg": ds["angle_range_deg"],
        "horizontal_fov_deg": scene.cfg.horizontal_fov_deg,
        "working_height": scene.pano_h,
        "invert_pan": camera.cfg.invert_pan,
        "start_angle": round(start, 4),
        "max_pan_speed_deg_per_sec": ds["max_pan_speed_deg_per_sec"],
        "monkeys": [m.to_dict() for m in monkeys],
        "created_utc": created_utc,
    }
    return writer.finalize(meta)


def main() -> None:
    ap = argparse.ArgumentParser(description="Automatic VLA tracking-dataset generator")
    ap.add_argument("--config", default="config.yaml")
    ap.add_argument("--out", default=None, help="output dataset root (overrides config)")
    ap.add_argument("--episodes", type=int, default=None, help="number of episodes")
    ap.add_argument("--seed", type=int, default=None, help="base RNG seed")
    args = ap.parse_args()

    cfg = load_config(args.config)
    setup_logging(cfg)
    ds = cfg["dataset"]
    n_episodes = args.episodes if args.episodes is not None else ds["num_episodes"]
    base_seed = args.seed if args.seed is not None else ds["seed"]
    root = output_root(cfg, args.out)

    log.info("Loading scene assets...")
    scene = build_scene(cfg)
    log.info("Generating %d episodes -> %s", n_episodes, root)

    for e in range(n_episodes):
        rng = np.random.default_rng(base_seed + e)
        ep_dir = next_episode_dir(root)
        meta = generate_episode(cfg, scene, rng, ep_dir)
        log.info("[%d/%d] %s  instruction=%r monkeys=%d frames=%d",
                 e + 1, n_episodes, ep_dir.split("/")[-1],
                 meta["instruction"], len(meta["monkeys"]), meta["num_frames"])

    log.info("Done. %d episodes in %s", n_episodes, root)


if __name__ == "__main__":
    main()
