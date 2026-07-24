# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A simulator for a camera mounted on a pan stepper motor looking at a static panoramic image. Moving the (simulated) motor pans a viewport across the panorama; the view is H.264-encoded and published to a **MediaMTX** server as an **RTSP** stream playable with `ffplay`. Motor control uses the exact `:EOM`-framed UART protocol documented in `motor_commands.md` — in the real system this travels over serial; here it is simulated over a TCP socket.

This is a standalone subproject of the larger monkey-detection effort (see `../CLAUDE.md`); the panorama stream is intended as a controllable test source.

## Commands

Use the project venv at `venv3.12/` (Python 3.12). External tools required on PATH: `ffmpeg`, `ffplay`, and a MediaMTX binary (path set in `config.yaml`, default `/home/miko/Applications/mediamtx`).

```bash
./venv3.12/bin/pip install -r requirements.txt    # PyYAML, numpy, Pillow are what the code actually imports

# Run (3 terminals)
./run_simulator.sh                                   # starts MediaMTX (if not running) + server.py
ffplay -rtsp_transport tcp rtsp://127.0.0.1:8554/cam # play the stream
./venv3.12/bin/python client.py                      # interactive control REPL

# One-shot control command (no REPL)
./venv3.12/bin/python client.py --send "goal 45 2"

# Run server / client against an alternate config
./venv3.12/bin/python server.py --config config.yaml
```

There is no test suite or linter. To smoke-test the motor protocol, drive `StepperMotorSim.handle()` directly with a list-appending `send` callback (no socket needed) and inspect the emitted strings.

> Note: `requirements.txt` lists extra packages (`opencv-python`, `posthog`, `aiohttp`, `sounddevice`) that the current code does **not** import — they are placeholders for planned integration with the parent monkey-detection system. The rendering path uses Pillow + numpy only.

## Architecture

Three runtime processes: **MediaMTX** (RTSP broker), **`server.py`** (the simulator), and one or more **`client.py`** instances (controllers). The server owns three cooperating components plus a TCP command server, all wired together in `server.py:main`:

```
client.py ──TCP socket (':EOM' frames)──▶ ControlServer ──▶ StepperMotorSim
                                                │                  │ current_angle (read every frame)
                                                │ broadcast()      ▼
                                          (motor responses)   PanoramaCamera.render(angle)
                                                                   │ RGB frame
                                                                   ▼
                                                              RTSPStreamer ──ffmpeg stdin──▶ MediaMTX ──▶ ffplay
```

Key cross-file relationships and non-obvious design points:

- **The TCP socket stands in for serial.** `ControlServer` (`server.py`) frames messages on `:EOM`, hands each command to `StepperMotorSim.handle()`, and exposes `broadcast()` as the motor's `send` sink — so motor responses (including async ones from background threads) reach *all* connected clients, not just the one that sent the command.

- **The motor is a free-running state machine, not request/response.** `StepperMotorSim` (`motor.py`) runs a background thread at `update_hz` advancing a time-based motion profile (quintic ease via `_quintic`). Commands like `CM_GOAL`/`CM_SWEEP` return an immediate `ACK` then emit a result message later when the motion thread completes the move (via the `_Move.on_done` callback). The frame loop never blocks on the motor — it just samples `motor.current_angle`.

- **Angle → pixels mapping lives in `PanoramaCamera`** (`panorama_camera.py`). The panorama is EXIF-rotated and rescaled to `working_height` once at startup (so per-frame cropping is a cheap numpy slice + Pillow resize). `pixels_per_degree = pano_width / horizontal_fov_deg`; the viewport is full panorama height, width derived from the output aspect ratio. **Positive angle = left = lower x** (matches the protocol; flip with `camera.invert_pan`). If the panorama is too narrow to cover the full motor travel, the viewport clamps at the image edges and logs a warning.

- **Limit switches are angle-based, configured by `motor.angle_limit_deg` (±60 default).** Commanding past a limit clamps to it and emits `DBG limit switch LEFT|RIGHT pressed: ...`; a `CM_STEP` aimed further into an already-active limit is rejected. The firmware's "±40% of calibrated range" clamp is intentionally **not** applied — the full ±limit is reachable, matching the project's "camera moves ±60°" requirement (noted in `motor.py`).

- **Protocol gating & watchdog** (per `motor_commands.md`): every command except `PING`/`SYSTEM_ON` returns `NACK` until armed. The heartbeat watchdog warns after `watchdog_warn_sec` of silence and "restarts the ESP" (resets all state) after `watchdog_strikes` strikes; any received command resets it. `client.py` auto-sends `PING` to keep it alive — pass `--no-heartbeat` to test watchdog behavior.

- **Frame pump** (`server.py:main`) runs at `camera.fps`, samples motor state, renders, and writes raw `rgb24` bytes to ffmpeg's stdin (`RTSPStreamer`). It self-resyncs if it falls behind and exits if ffmpeg dies.

## Configuration

All runtime behavior is in `config.yaml` (sections: `panorama`, `camera`, `motor`, `control_server`, `rtsp`, `logging`). The dataclasses in each module (`MotorConfig`, `CameraConfig`, `RTSPConfig`) mirror these sections; `server.py:main` is the single place that reads YAML and constructs them. Set `rtsp.autostart_mediamtx: true` to have the server launch MediaMTX itself instead of relying on `run_simulator.sh`.

`motor_commands.md` is the authoritative protocol reference — consult it before changing command parsing or response framing in `motor.py`.
