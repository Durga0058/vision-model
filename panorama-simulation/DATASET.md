# VLA tracking-dataset toolkit

Tools for building the training data described in
[`../vla-training.md`](../vla-training.md): a Vision-Language-Action model that,
given the live camera frame + a language instruction + the current pan angle,
predicts the **absolute pan angle** that centers the instructed monkey and the
**number of monkeys** in view.

Monkeys are composited onto the base panorama at known pan angles and the existing
[`PanoramaCamera`](panorama_camera.py) renders the camera viewport. Because a
monkey's placement angle *is* the absolute angle that centers it, every label
comes straight from the geometry — no manual annotation.

Three tools, one shared episode format:

| Tool | What it does |
|------|--------------|
| [`dataset_auto.py`](dataset_auto.py) | Generates labeled tracking episodes automatically (headless). |
| [`dataset_manual.py`](dataset_manual.py) | Live window; you drive the camera with the arrow keys and record demonstrations. |
| [`replay.py`](replay.py) | Plays a recorded episode back with the angle / count overlay. |

## Setup

Uses the same environment as the simulator (PyYAML, numpy, Pillow, **opencv-python**,
plus `ffmpeg` on PATH — all already in [`requirements.txt`](requirements.txt)):

```bash
PY=/home/miko/Documents/miko/monkey-project/final_project/panorama-simulation/venv3.12/bin/python
$PY -m pip install -r requirements.txt
```

Assets used (already in this folder):
- `panorama-base.jpg` — the virtual world.
- `monkey-images/` — the monkey cut-outs (transparent PNGs) composited into the scene.

## What an episode looks like

Each episode is one tracking trajectory, stored as a directory:

```
datasets/
  episode_0000/
    video.mp4      # camera frames, clean (no overlay) — the vision input
    labels.jsonl   # one JSON record per frame, aligned to video frame order
    meta.json      # episode metadata + the exact monkey layout
```

Every `labels.jsonl` line is a per-frame training sample:

```json
{"frame": 42, "t": 1.68,
 "current_angle": -12.34,      // proprioceptive INPUT: where the camera points now
 "target_angle": 23.40,        // OUTPUT 1: absolute angle the camera is going to
 "monkey_count": 2,            // OUTPUT 2: monkeys visible this frame
 "instruction": "center the closest monkey",   // language INPUT
 "phase": "search",            // "search" (hunting) | "track" (locked on target)
 "ideal_target_angle": 23.40}  // the instructed monkey's true angle (privileged)
```

During the **search** phase `target_angle` is the sweep goal ("keep panning this
way"); once the instructed monkey enters view the phase flips to **track** and
`target_angle` becomes that monkey's absolute angle. `monkey_count` is always the
true count (it can be >0 mid-search when non-target monkeys pass through view).

`meta.json` records `fps`, resolution, `angle_limit_deg`, `horizontal_fov_deg`,
`invert_pan`, and the full `monkeys` list (`angle_deg`, `y_frac`, `scale`,
`image_index`) so any episode is fully reproducible / re-renderable.

**Monkey count convention:** a monkey counts as "in frame" when its center falls
inside the viewport crop (see `MonkeyScene.count_in_crop`).

## 1. Automatic generation

```bash
$PY dataset_auto.py                                  # 20 episodes -> datasets/
$PY dataset_auto.py --episodes 500 --out datasets/train --seed 1
```

Per episode it: samples 1–6 monkeys within ±`angle_range_deg`, picks a language
instruction, starts the camera at a **random angle**, then **searches** (sweeps
toward the monkeys) and **locks on + tracks** the instructed monkey once it enters
view — recording video + labels the whole way. The random start gives a full
spread of episodes: sometimes a monkey is already in the first frame (a tiny
move), sometimes a short nudge finds one, sometimes a full opposite-direction
sweep is needed. Fully headless (no display, no RTSP, no motor socket) and
deterministic given `--seed` (episode *e* uses `seed + e`).

**Slow pan motor.** The camera never moves faster than `max_pan_speed_deg_per_sec`
(default 5°/s), with an acceleration limit — search, centering, and tracking all
obey this cap. A full opposite-direction search can therefore take ~20 s, which is
why `episode_seconds` defaults to 20.

**Moving monkeys.** Each monkey rests for a random `rest_[min,max]` seconds, then
walks at a slow constant speed (`walk_speed_[min,max]` deg/s) in a random
direction, bouncing within the angle range. After the camera locks onto the
instructed monkey it **tracks it as it walks** — so `target_angle` follows the
monkey's live angle and `current_angle` trails it within the speed cap. Set
`animate_monkeys: false` for a fully static scene.

Arguments: `--config` (default `config.yaml`), `--out`, `--episodes`, `--seed`.

## 2. Manual generation (teleop)

```bash
$PY dataset_manual.py
$PY dataset_manual.py --out datasets/manual --instruction "track the one on the left"
```

Opens a live camera window over a random monkey scene. **←/→ step the setpoint**
(the camera moves there and stops); **↑/↓ change how fast that move runs**, so you
can slow it down for fine alignment on the centre crosshair:

| Key | Action |
|-----|--------|
| **← / →** (or **A / D**) | step the setpoint left / right — camera moves there, then stops |
| **↑ / ↓** (or **W / S**) | speed up / slow down the move (0 … max pan speed) |
| **] / [** (or **= / −**) | bigger / smaller setpoint jump per ←/→ press |
| **Space** | start / stop recording (stopping saves the episode) |
| **N** | new random monkey layout (only when not recording) |
| **I** | cycle the language instruction |
| **R** | recenter the camera to 0° |
| **Q / Esc** | quit |

The HUD shows a cyan **centre crosshair** (display only — never recorded) plus the
current move speed and step size. To fine-align a monkey that's just off-centre:
press **[** a few times to shrink the setpoint jump (and optionally **↓** to slow
the move), then tap **←/→** — each press now nudges a small amount so you can
settle it on the crosshair. Speed is capped at `max_pan_speed_deg_per_sec`
(starts at `manual_speed_init_deg_per_sec`); the jump starts at `manual_step_deg`.

The recorded `target_angle` is the absolute angle **you** are steering toward;
`ideal_target_angle` also logs what the instruction's ideal target would be, so
manual demonstrations can be compared against the scripted policy. Requires a
display (it opens an OpenCV window).

## 3. Replay

```bash
$PY replay.py datasets/episode_0000                 # interactive viewer
$PY replay.py datasets/episode_0000 --loop          # loop playback
$PY replay.py datasets/episode_0000 --save out.mp4  # write annotated mp4 (headless)
```

Overlays the instruction, current angle, target angle, and monkey count, plus a
pan-position bar (green = current, amber = target, blue tick = center).

Interactive keys: **Space** pause/resume, **← / →** step one frame while paused,
**Q / Esc** quit.

### Inspecting pan speed

`plot_speed.py` overlays the camera's angular velocity (deg/s) over time for every
episode in a dataset — a quick way to confirm motion stays under the speed cap and
to see the search → acquire → track shape:

```bash
$PY plot_speed.py datasets/testrun          # writes datasets/testrun/speed.png
```

## Configuration

All knobs live in the `dataset:` section of [`config.yaml`](config.yaml). Key ones:

| Key | Meaning |
|-----|---------|
| `output_dir` | Where `episode_XXXX/` directories are written. |
| `base_image`, `monkey_images_dir` | Scene assets. |
| `working_height` | Internal panorama height (px). Supersamples the crop so the narrow 2K viewport stays sharp (default `2592`). |
| `horizontal_fov_deg` | FOV the **whole** base panorama spans → sets pixels-per-degree. Must be wide enough that `angle_range_deg` fits given the camera FOV (see below). |

The **output resolution** and **camera FOV** live in the `camera:` section (shared
with the live simulator):

| Key (`camera:`) | Meaning |
|-----------------|---------|
| `output_width` / `output_height` | Encoded frame size. Default `2304×1296` (the deployment camera's 2K). |
| `camera_fov_deg` | Per-frame horizontal FOV the camera actually sees. Default `25.2` to match the real lens / the tracker's `camera_fov_deg`. The viewport crops exactly this many degrees (height from the output aspect, centered vertically). Blank = legacy full-height viewport. |
| `angle_range_deg` | Monkeys placed within ±this. The task uses `50` (keep ≤ `motor.angle_limit_deg`). |
| `min_monkeys` / `max_monkeys` | Monkeys per episode (default `1`–`6`). |
| `monkey_scale_min/max` | Monkey height as a fraction of frame height. |
| `y_frac_min/max` | Vertical band the monkeys sit in. |
| `cluster_prob`, `cluster_spread_deg` | Chance an episode clusters monkeys *together* vs *spreads* them out, and how tight a cluster is → gives "together / far apart / in between" arrangements. |
| `fps`, `episode_seconds` | Video frame rate and auto-episode length (default `20 s`, enough for a slow search + centering + tracking a walker). |
| `max_pan_speed_deg_per_sec` | Hard cap on camera pan speed (default `5`). Applies to search, centering, and tracking. |
| `pan_accel_deg_per_sec2`, `pan_gain` | Accel limit (smooth velocity onset) and the proportional gain that eases the camera to a stop near its target. |
| `animate_monkeys` | Whether monkeys walk (`true`) or stay static (`false`). |
| `rest_min_seconds` / `rest_max_seconds` | Each monkey rests this long before it starts walking. |
| `walk_speed_min_deg_per_sec` / `walk_speed_max_deg_per_sec` | Slow walking speed range (deg/s). |
| `instructions` | Instructions sampled per auto episode (see mapping below). |
| `num_episodes`, `seed` | Defaults for `dataset_auto.py`. |
| `manual_step_deg` | Degrees the setpoint jumps per ←/→ press. |
| `manual_speed_init_deg_per_sec`, `manual_speed_step_deg_per_sec`, `manual_instruction` | Manual-recorder start move-speed, ↑/↓ speed increment, and default instruction. |

CLI flags (`--out`, `--episodes`, `--seed`, `--instruction`) override the config.

### Instruction → target mapping

Instructions are matched by keyword (case-insensitive):

| Keyword in instruction | Target monkey |
|------------------------|---------------|
| `left` | leftmost on screen |
| `right` | rightmost on screen |
| `large` / `big` | largest (by scale) |
| `small` | smallest |
| *(anything else)* | closest to the current frame center |

"Left"/"right" follow **screen position** and stay correct under either
`camera.invert_pan` setting. Add your own phrasings freely — unmatched ones fall
back to "closest".

### Geometry note: three FOVs, and how they must relate

There are **three** angles in play — keep them distinct:

- `dataset.horizontal_fov_deg` — how much real-world angle the **whole panorama
  image** spans. Sets pixels-per-degree.
- `camera.camera_fov_deg` — how much the **camera sees in one frame** (the real
  lens). This is what a downstream tracker's `camera_fov_deg` must equal.
- `dataset.angle_range_deg` — the pan range monkeys are placed across (±50°).

For a monkey at ±`angle_range_deg` to be centerable, the panorama must be wide
enough to hold the viewport at that extreme:

```
horizontal_fov_deg  >=  2 * (angle_range_deg + camera_fov_deg / 2)
```

With `angle_range_deg: 50` and `camera_fov_deg: 25.2` that is ≥ 125.2°; the
default `140` leaves margin. If it is too small the scene logs a warning and
edge monkeys clamp — bump `horizontal_fov_deg` until it clears.

Resolution is independent of all of this: `output_width/height` only changes pixel
density, not FOV. `working_height` should stay comfortably **above** the crop's
pixel size (reported at startup as the viewport size) so the upscale to 2K keeps
detail — the default `2592` supersamples the 25.2° crop.

> Note: you may still see a `Panorama too narrow for ±60° travel` info-warning —
> that refers to the motor's full ±`angle_limit_deg` (60°) travel, not the ±50°
> monkey range, and is harmless as long as `angle_range_deg` fits the formula above.

## Bonus: monkeys in the live RTSP stream

The dataset frames are rendered directly (exact, deterministic labels), but you
can also see the same monkeys in the real RTSP simulator:

```bash
$PY server.py --monkeys --monkeys-seed 3     # composite a random layout into the live stream
ffplay -rtsp_transport tcp rtsp://127.0.0.1:8554/cam
$PY client.py                                # drive the motor as usual
```

This reuses the `dataset:` config for the layout and renders with the matching FOV.
