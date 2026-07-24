"""Manual (teleoperated) VLA dataset recorder.

Opens a live camera window over a monkey scene. You drive the pan motor with the
arrow keys to center a monkey; while recording, every frame is saved to
``video.mp4`` together with its label record — the absolute angle you are steering
toward (target), the current pan angle, and the number of monkeys in frame — in
the same episode format the automatic generator produces.

    python dataset_manual.py                 # uses config.yaml + datasets/
    python dataset_manual.py --out datasets/manual --instruction "track the one on the left"

Left/Right step the target setpoint; the camera moves to it and stops. Up/Down
change how fast that move runs, so you can slow it down to fine-align a monkey on
the centre crosshair.

    python dataset_manual.py                 # uses config.yaml + datasets/
    python dataset_manual.py --out datasets/manual --instruction "track the one on the left"

Controls
    Left / Right arrow (or A / D) : step the setpoint left / right (camera moves there, then stops)
    Up / Down arrow   (or W / S)  : speed up / slow down the move (0 .. max pan speed)
    ] / [  (or = / -)             : bigger / smaller setpoint jump per Left/Right press
    Space                         : start / stop recording (stopping saves the episode)
    N                             : new random monkey layout (only when not recording)
    I                             : cycle the language instruction
    R                             : recenter camera to 0 deg
    Q / Esc                       : quit

Requires a display (uses an OpenCV window).
"""

from __future__ import annotations

import argparse
import logging
import time

import cv2
import numpy as np

from dataset_common import (build_scene, load_config, make_camera, output_root,
                            setup_logging)
from episode import EpisodeWriter, next_episode_dir
from hud import draw_hud
from pan_controller import PanController

log = logging.getLogger("manual")

# Arrow-key codes vary by OpenCV GUI backend; accept the common ones plus WASD.
LEFT_KEYS = {65361, 2424832, 16777234, ord("a"), ord("A"), 81}
RIGHT_KEYS = {65363, 2555904, 16777236, ord("d"), ord("D"), 83}
UP_KEYS = {65362, 2490368, 16777235, ord("w"), ord("W"), 82}
DOWN_KEYS = {65364, 2621440, 16777237, ord("s"), ord("S"), 84}
# Setpoint-step size adjustment: ] / [  (with = / - and . / , as aliases).
STEP_UP_KEYS = {ord("]"), ord("="), ord("+"), ord(".")}
STEP_DOWN_KEYS = {ord("["), ord("-"), ord("_"), ord(",")}

STEP_MIN, STEP_MAX, STEP_DELTA = 0.5, 20.0, 0.5   # setpoint-jump limits + increment

WINDOW = "VLA manual recorder"


def main() -> None:
    ap = argparse.ArgumentParser(description="Manual VLA tracking-dataset recorder")
    ap.add_argument("--config", default="config.yaml")
    ap.add_argument("--out", default=None, help="output dataset root (overrides config)")
    ap.add_argument("--instruction", default=None, help="language instruction to log")
    ap.add_argument("--seed", type=int, default=0, help="RNG seed for the first layout")
    args = ap.parse_args()

    cfg = load_config(args.config)
    setup_logging(cfg)
    ds = cfg["dataset"]
    fps = ds["fps"]
    step_deg = ds.get("manual_step_deg", 3.0)                 # setpoint nudge per Left/Right
    vmax = ds["max_pan_speed_deg_per_sec"]
    speed_step = ds.get("manual_speed_step_deg_per_sec", 0.5)  # per Up/Down press
    pan_speed = min(ds.get("manual_speed_init_deg_per_sec", vmax), vmax)  # move speed
    accel = ds.get("pan_accel_deg_per_sec2")
    gain = ds.get("pan_gain", 2.0)
    limit = cfg.get("motor", {}).get("angle_limit_deg", 60.0)
    # Screen direction of the arrow keys. On-screen x grows with sign*angle, where
    # sign = +1 when invert_pan is true. So the LEFT arrow (pan the view left =
    # toward smaller on-screen x) must change the angle by -step*pan_sign; this
    # keeps the keys correct under either invert_pan setting.
    pan_sign = 1.0 if cfg.get("camera", {}).get("invert_pan", True) else -1.0
    root = output_root(cfg, args.out)
    instructions = list(ds["instructions"])
    instruction = args.instruction or ds.get("manual_instruction", instructions[0])
    if instruction not in instructions:
        instructions.insert(0, instruction)
    instr_idx = instructions.index(instruction)

    log.info("Loading scene assets...")
    scene = build_scene(cfg)
    rng_seed = args.seed

    def new_scene(seed):
        # Manual mode keeps monkeys static (the operator drives); bake them once.
        monkeys = scene.random_layout(np.random.default_rng(seed), animate=False)
        composited = scene.build(monkeys)
        camera = make_camera(cfg, scene, composited)
        return monkeys, camera

    monkeys, camera = new_scene(rng_seed)
    ctrl = PanController(limit, 0.0, max_speed_deg_s=vmax, accel_deg_s2=accel, gain=gain)
    ctrl.max_speed = pan_speed          # moves run at the operator-chosen speed
    target_cmd = 0.0  # absolute setpoint the camera is moving toward

    cv2.namedWindow(WINDOW, cv2.WINDOW_NORMAL)
    cv2.resizeWindow(WINDOW, camera.cfg.output_width, camera.cfg.output_height)

    writer: EpisodeWriter | None = None
    rec_start = 0.0
    ep_dir = None
    delay_ms = max(1, int(1000 / fps))
    last = time.monotonic()

    def start_recording():
        nonlocal writer, rec_start, ep_dir
        ep_dir = next_episode_dir(root)
        writer = EpisodeWriter(ep_dir, camera.cfg.output_width,
                               camera.cfg.output_height, fps)
        rec_start = time.monotonic()
        log.info("Recording -> %s (instruction=%r, monkeys=%d)",
                 ep_dir, instruction, len(monkeys))

    def stop_recording():
        nonlocal writer
        if writer is None:
            return
        meta = {
            "mode": "manual", "instruction": instruction, "fps": fps,
            "duration_s": round(writer.n_frames / fps, 3),
            "output_width": camera.cfg.output_width,
            "output_height": camera.cfg.output_height,
            "angle_limit_deg": limit, "angle_range_deg": ds["angle_range_deg"],
            "horizontal_fov_deg": scene.cfg.horizontal_fov_deg,
            "working_height": scene.pano_h, "invert_pan": camera.cfg.invert_pan,
            "monkeys": [m.to_dict() for m in monkeys], "created_utc": None,
        }
        writer.finalize(meta)
        log.info("Saved %s (%d frames)", ep_dir, writer.n_frames)
        writer = None

    log.info("Ready. ←/→ pan, ↑/↓ speed, Space records, N new layout, Q quits.")
    try:
        while True:
            key = cv2.waitKeyEx(delay_ms)
            if key != -1:
                if key in (ord("q"), ord("Q"), 27):
                    break
                elif key in LEFT_KEYS:      # step the setpoint left; camera moves there & stops
                    target_cmd = max(-limit, min(limit, target_cmd - step_deg * pan_sign))
                    ctrl.command(target_cmd)
                elif key in RIGHT_KEYS:     # step the setpoint right
                    target_cmd = max(-limit, min(limit, target_cmd + step_deg * pan_sign))
                    ctrl.command(target_cmd)
                elif key in UP_KEYS:        # speed up the move (capped at the motor limit)
                    pan_speed = min(vmax, pan_speed + speed_step)
                    ctrl.max_speed = pan_speed
                elif key in DOWN_KEYS:      # slow the move down (toward a crawl / stop)
                    pan_speed = max(0.0, pan_speed - speed_step)
                    ctrl.max_speed = pan_speed
                elif key in STEP_UP_KEYS:   # bigger setpoint jump per Left/Right
                    step_deg = min(STEP_MAX, step_deg + STEP_DELTA)
                elif key in STEP_DOWN_KEYS: # finer setpoint jump per Left/Right
                    step_deg = max(STEP_MIN, step_deg - STEP_DELTA)
                elif key == ord(" "):
                    if writer is None:
                        start_recording()
                    else:
                        stop_recording()
                elif key in (ord("n"), ord("N")) and writer is None:
                    rng_seed += 1
                    monkeys, camera = new_scene(rng_seed)
                    ctrl = PanController(limit, 0.0, max_speed_deg_s=vmax,
                                         accel_deg_s2=accel, gain=gain)
                    ctrl.max_speed = pan_speed
                    target_cmd = 0.0
                    log.info("New layout (seed=%d, monkeys=%d)", rng_seed, len(monkeys))
                elif key in (ord("i"), ord("I")):
                    instr_idx = (instr_idx + 1) % len(instructions)
                    instruction = instructions[instr_idx]
                    log.info("Instruction: %r", instruction)
                elif key in (ord("r"), ord("R")):
                    target_cmd = 0.0
                    ctrl.command(0.0)

            now = time.monotonic()
            dt = now - last
            last = now
            # The camera eases toward the current setpoint at the chosen speed and
            # stops once it arrives (PanController reaches target -> velocity 0).
            ctrl.step(dt)

            angle = ctrl.angle
            x0, x1 = camera.crop_bounds(angle)
            count = scene.count_in_crop(monkeys, x0, x1)
            ideal = scene.select_target(instruction, angle, monkeys, 0.0)
            ideal_angle = ideal.angle_deg if ideal else angle

            frame_rgb = camera.render(angle)  # clean frame for the dataset

            if writer is not None:
                writer.add(frame_rgb, {
                    "t": round(now - rec_start, 4),
                    "current_angle": round(angle, 4),
                    "target_angle": round(target_cmd, 4),
                    "monkey_count": count,
                    "instruction": instruction,
                    "ideal_target_angle": round(ideal_angle, 4),
                })

            # Display copy only — the crosshair aid is NOT in the recorded frame.
            disp = cv2.cvtColor(frame_rgb, cv2.COLOR_RGB2BGR)
            status = "MOVING" if ctrl.moving else "stopped"
            draw_hud(
                disp, current_angle=angle, target_angle=target_cmd,
                monkey_count=count, instruction=instruction, limit_deg=limit,
                recording=(writer is not None), center_marker=True,
                extra_lines=[f"speed: {pan_speed:.1f}/{vmax:.0f} deg/s  step: {step_deg:.1f} deg  ({status})",
                             f"ideal (closest/etc): {ideal_angle:+.2f} deg",
                             "<-/-> move   up/dn speed   ]/[ step   space rec   q quit"],
            )
            cv2.imshow(WINDOW, disp)
    except KeyboardInterrupt:
        pass
    finally:
        stop_recording()
        cv2.destroyAllWindows()
        log.info("bye")


if __name__ == "__main__":
    main()
