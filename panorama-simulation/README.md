# Panorama RTSP Camera Simulator

Simulates a camera mounted on a pan stepper motor looking at a static panoramic
image. Moving the motor pans a viewport across the panorama; the resulting view
is H.264-encoded and published to a **MediaMTX** server as an **RTSP** stream you
can play with `ffplay`. Motor control uses the exact `:EOM`-framed UART protocol
from [`motor_commands.md`](motor_commands.md) — here the serial link is simulated
over a TCP socket.

```
client.py ──TCP (':EOM' protocol)──▶ server.py
                                      ├─ StepperMotorSim   (motion profile, limit switches, watchdog)
                                      ├─ PanoramaCamera    (angle → cropped viewport)
                                      └─ RTSPStreamer ──ffmpeg──▶ MediaMTX ──RTSP──▶ ffplay
```

## Layout

| File | Purpose |
|------|---------|
| `config.yaml` | All tunable parameters (panorama, camera, motor, RTSP, control port). |
| `server.py` | Main entry: motor sim + renderer + ffmpeg publisher + TCP command server. |
| `client.py` | Interactive control client (REPL + one-shot mode + heartbeat). |
| `motor.py` | `StepperMotorSim` — the simulated ESP firmware / protocol. |
| `panorama_camera.py` | Maps a motor angle to a viewport crop of the panorama. |
| `rtsp_streamer.py` | Pipes raw frames into ffmpeg → RTSP. |
| `run_simulator.sh` | Starts MediaMTX (if needed) then the server. |

## VLA dataset generation

This simulator also backs the VLA tracking-dataset toolkit (automatic + manual
generation and episode replay). It composites monkeys onto the panorama at known
pan angles and records perfectly-labeled tracking episodes. See
**[`DATASET.md`](DATASET.md)** for the tools, config, and usage.

## Setup

Use a plain Python **venv** (not conda), Python 3.12:

```bash
cd panorama-simulation

# 1. Create the environment (once)
python3.12 -m venv venv3.12

# 2. Activate it (do this in every new terminal before running anything)
source venv3.12/bin/activate

# 3. Install dependencies
pip install --upgrade pip
pip install -r requirements.txt
#   PyYAML, numpy, Pillow, opencv-python, matplotlib (+ a few unused placeholders)
```

Once activated, just use `python …` (as shown below). Run `deactivate` to leave.

External tools on `PATH`:
- **ffmpeg / ffplay** — RTSP streaming + playback (the live simulator only).
- A **MediaMTX** binary — RTSP broker for the live simulator (path in `config.yaml`).

The **VLA dataset tools below need only the Python deps** (no ffmpeg/MediaMTX);
the manual recorder and interactive replay additionally need a display (they open
an OpenCV window).

## Run

Activate the venv first (`source venv3.12/bin/activate`) in each terminal.

```bash
# 1. Start MediaMTX + simulator server (one terminal)
./run_simulator.sh
#    or manually:
#    /home/miko/Applications/mediamtx /home/miko/Applications/mediamtx.yml &
#    python server.py

# 2. Play the stream (another terminal)
ffplay -rtsp_transport tcp rtsp://127.0.0.1:8554/cam

# 3. Control the camera (another terminal)
python client.py
```

## Controlling the camera

The client accepts the raw protocol **or** convenience shortcuts:

| Shortcut | Raw command | Effect |
|----------|-------------|--------|
| `on` | `SYSTEM_ON::EOM` | Arm + run calibration (must be sent first). |
| `goal 30 2` | `CM_GOAL:30,2:EOM` | Pan to +30° over 2 s (quintic profile). |
| `sweep 20 3` | `CM_SWEEP:20,3:EOM` | Continuous back-and-forth sweep. |
| `step -200` | `CM_STEP:-200:EOM` | Move 200 motor steps right. |
| `stop` | `CM_STOP::EOM` | Halt motion / sweep. |
| `calib` | `CM_CALIB::EOM` | Re-run calibration. |
| `pose` | `CM_POSE::EOM` | Query current angle. |
| `ping` | `PING::EOM` | Heartbeat (auto-sent every 10 s by default). |
| `rst` | `CM_STEP_COUNT_RST::EOM` | Reset the cumulative step counter. |

One-shot mode: `python client.py --send "goal 45 2"`.

### Angle / limit-switch model

- `0°` = midpoint. **Positive = left**, **negative = right** (per the protocol).
- Travel is `±angle_limit_deg` (default `±60°`). Commanding beyond a limit clamps
  to it and emits `DBG limit switch LEFT|RIGHT pressed: ...`, just like the
  hardware. A `CM_STEP` aimed further into an already-active limit is rejected.
- Commands other than `PING`/`SYSTEM_ON` before arming return `NACK`.
- The heartbeat watchdog warns after 20 s of silence and "restarts the ESP" after
  60 s (3 strikes); any received command resets it. The client's auto-PING keeps
  it alive.

The HUD overlay (toggle with `camera.overlay`) shows the live angle, system
state, limit-switch warnings, and a pan-position bar.

## How the angle maps to the image

The panorama is EXIF-rotated and rescaled to `panorama.working_height`. A motor
angle becomes a horizontal pixel offset via
`pixels_per_degree = panorama_width / panorama.horizontal_fov_deg`. The viewport
uses the full panorama height with a width set by the output aspect ratio; its
effective horizontal FOV is printed at startup. If the panorama is too narrow to
cover the full motor travel, the viewport clamps at the image edges (a warning is
logged).

## Key config knobs (`config.yaml`)

- `panorama.image_path`, `working_height`, `horizontal_fov_deg`
- `camera.output_width/height`, `fps`, `invert_pan`, `overlay`
- `motor.angle_limit_deg`, `steps_per_degree`, watchdog + calibration timing
- `rtsp.base_url`, `path`, `transport`, encoder settings, `autostart_mediamtx`
- `control_server.host`, `port`

# VLA dataset generation pipeline

Records perfectly-labeled monkey-tracking episodes for VLA training (vision +
language instruction + current pan angle → target angle + monkey count). It
composites monkeys onto the panorama at known angles, so every frame is labeled
with no manual annotation. Full reference: **[`DATASET.md`](DATASET.md)**.

Complete the [Setup](#setup) above first, then activate the venv:

```bash
cd panorama-simulation
source venv3.12/bin/activate
```

An episode is written as `datasets/episode_XXXX/` containing `video.mp4` (clean
camera frames), `labels.jsonl` (per-frame `current_angle`, `target_angle`,
`monkey_count`, `instruction`), and `meta.json` (the monkey layout).

## 1. Automatic generation (headless — no display, no RTSP)

```bash
python dataset_auto.py                                    # 20 episodes -> datasets/
python dataset_auto.py --episodes 50 --out datasets/train --seed 1
```

Per episode: 1–6 monkeys are placed (they rest, then walk slowly), the camera
starts at a random angle, **searches** for the instructed monkey, then **locks on
and tracks** it — all at ≤ `max_pan_speed_deg_per_sec` (default 5°/s). Flags:
`--config`, `--out`, `--episodes`, `--seed` (episode *e* uses `seed + e`).

> At 2K, generation is ~50 s/episode; for large runs launch it in the background.

## 2. Manual generation (teleoperated — needs a display)

```bash
python dataset_manual.py
python dataset_manual.py --out datasets/manual --instruction "track the one on the left"
```

Opens a live camera window over a random monkey scene. **←/→ step the setpoint**
(the camera moves there and stops); **↑/↓ change the move speed**; **]/[ change the
step size**. A cyan centre crosshair (shown on screen, never written to the video)
helps you judge alignment. Press **Space** to record; stopping saves the episode.

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

To fine-align a monkey: press **[** to shrink the step (and **↓** to slow the move),
then tap **←/→** so it creeps onto the crosshair.

## 3. Replay an episode

```bash
python replay.py datasets/episode_0000                 # interactive viewer (needs display)
python replay.py datasets/episode_0000 --loop          # loop playback
python replay.py datasets/episode_0000 --save out.mp4  # write annotated mp4 (headless)
```

Overlays the instruction, current/target angle, monkey count, and a pan bar.
Interactive keys: **Space** pause/resume, **← / →** step one frame while paused,
**Q / Esc** quit.

## 4. Inspect pan speed

```bash
python plot_speed.py datasets/testrun                  # -> datasets/testrun/speed.png
```

Overlays each episode's angular velocity (deg/s) over time — a quick check that
motion stays under the speed cap and shows the search → acquire → track shape.

## Configuration

All dataset settings live in the `dataset:` section of `config.yaml` (monkey
count/placement, camera FOV & 2K resolution, `max_pan_speed_deg_per_sec`, monkey
walk speed, episode length, instructions, manual-recorder keys). See the
[`DATASET.md`](DATASET.md) config tables for every knob.
