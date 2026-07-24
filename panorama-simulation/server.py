"""Panorama RTSP camera simulator — server entry point.

Ties together the simulated stepper motor, the panorama renderer, and the ffmpeg
RTSP publisher, and exposes a TCP command socket that stands in for the UART link.

Run:  python server.py [--config config.yaml]
"""

from __future__ import annotations

import argparse
import logging
import signal
import socket
import subprocess
import threading
import time

import yaml

from motor import EOM, MotorConfig, StepperMotorSim
from panorama_camera import CameraConfig, PanoramaCamera
from rtsp_streamer import RTSPConfig, RTSPStreamer

log = logging.getLogger("server")


class ControlServer:
    """TCP server framing commands with :EOM, broadcasting motor responses."""

    def __init__(self, host: str, port: int):
        self.host = host
        self.port = port
        self.motor: StepperMotorSim | None = None
        self._clients: set[socket.socket] = set()
        self._clients_lock = threading.Lock()
        self._sock: socket.socket | None = None
        self._stop = threading.Event()

    def attach_motor(self, motor: StepperMotorSim) -> None:
        self.motor = motor

    def broadcast(self, msg: str) -> None:
        """Send a framed message to every connected client (motor's send sink)."""
        data = msg.encode()
        with self._clients_lock:
            dead = []
            for c in self._clients:
                try:
                    c.sendall(data)
                except OSError:
                    dead.append(c)
            for c in dead:
                self._clients.discard(c)

    def start(self) -> None:
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._sock.bind((self.host, self.port))
        self._sock.listen(5)
        log.info("Control server listening on %s:%d", self.host, self.port)
        threading.Thread(target=self._accept_loop, name="ctrl-accept", daemon=True).start()

    def _accept_loop(self) -> None:
        while not self._stop.is_set():
            try:
                conn, addr = self._sock.accept()
            except OSError:
                break
            log.info("Client connected: %s", addr)
            with self._clients_lock:
                self._clients.add(conn)
            threading.Thread(
                target=self._client_loop, args=(conn, addr), daemon=True
            ).start()

    def _client_loop(self, conn: socket.socket, addr) -> None:
        buf = ""
        try:
            conn.sendall(b"INFO connected to panorama camera simulator" + EOM.encode())
            while not self._stop.is_set():
                data = conn.recv(4096)
                if not data:
                    break
                buf += data.decode(errors="replace")
                # Commands are framed with :EOM.
                while EOM in buf:
                    cmd, buf = buf.split(EOM, 1)
                    cmd = cmd.strip()
                    if cmd and self.motor:
                        self.motor.handle(cmd + EOM)
        except OSError:
            pass
        finally:
            log.info("Client disconnected: %s", addr)
            with self._clients_lock:
                self._clients.discard(conn)
            conn.close()

    def stop(self) -> None:
        self._stop.set()
        if self._sock:
            self._sock.close()
        with self._clients_lock:
            for c in self._clients:
                c.close()
            self._clients.clear()


def maybe_start_mediamtx(rtsp_cfg: dict) -> subprocess.Popen | None:
    if not rtsp_cfg.get("autostart_mediamtx"):
        return None
    binary = rtsp_cfg.get("mediamtx_binary")
    if not binary:
        log.warning("autostart_mediamtx set but no mediamtx_binary configured")
        return None
    cmd = [binary]
    if rtsp_cfg.get("mediamtx_config"):
        cmd.append(rtsp_cfg["mediamtx_config"])
    log.info("Starting MediaMTX: %s", " ".join(cmd))
    proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    time.sleep(1.5)  # give it a moment to bind the RTSP port
    return proc


def main() -> None:
    ap = argparse.ArgumentParser(description="Panorama RTSP camera simulator")
    ap.add_argument("--config", default="config.yaml")
    ap.add_argument("--monkeys", action="store_true",
                    help="composite a random monkey layout onto the live stream "
                         "(uses the [dataset] config section)")
    ap.add_argument("--monkeys-seed", type=int, default=0,
                    help="RNG seed for the --monkeys layout")
    args = ap.parse_args()

    with open(args.config) as f:
        cfg = yaml.safe_load(f)

    logging.basicConfig(
        level=getattr(logging, cfg.get("logging", {}).get("level", "INFO")),
        format="%(asctime)s %(name)-8s %(levelname)-7s %(message)s",
        datefmt="%H:%M:%S",
    )

    pano_cfg = cfg["panorama"]
    cam_cfg_d = cfg["camera"]
    motor_cfg_d = cfg["motor"]
    ctrl_cfg = cfg["control_server"]
    rtsp_cfg = cfg["rtsp"]

    # MediaMTX (optional autostart).
    mediamtx_proc = maybe_start_mediamtx(rtsp_cfg)

    # Camera / renderer.
    cam_cfg = CameraConfig(
        output_width=cam_cfg_d["output_width"],
        output_height=cam_cfg_d["output_height"],
        fps=cam_cfg_d["fps"],
        invert_pan=cam_cfg_d.get("invert_pan", False),
        overlay=cam_cfg_d.get("overlay", True),
        camera_fov_deg=cam_cfg_d.get("camera_fov_deg"),
    )

    # Optionally bake a monkey scene into the live stream. The scene uses the
    # [dataset] section's own horizontal_fov_deg (wide enough for the ±angle
    # range), so we render over the composited panorama with that same FOV.
    pano_array = None
    hfov = pano_cfg["horizontal_fov_deg"]
    if args.monkeys:
        import numpy as np
        from dataset_common import DEFAULT_DATASET
        from scene import MonkeyScene, SceneConfig
        ds = {**DEFAULT_DATASET, **(cfg.get("dataset") or {})}
        scene = MonkeyScene(SceneConfig.from_dict(cam_cfg_d, ds))
        monkeys = scene.random_layout(np.random.default_rng(args.monkeys_seed))
        pano_array = scene.build(monkeys)
        hfov = scene.cfg.horizontal_fov_deg
        log.info("Live stream showing %d monkeys (seed=%d)", len(monkeys), args.monkeys_seed)

    camera = PanoramaCamera(
        image_path=pano_cfg["image_path"],
        working_height=pano_cfg["working_height"],
        horizontal_fov_deg=hfov,
        angle_limit_deg=motor_cfg_d["angle_limit_deg"],
        cam_cfg=cam_cfg,
        apply_exif=pano_cfg.get("apply_exif_orientation", True),
        pano_array=pano_array,
    )

    # Control server + motor.
    control = ControlServer(ctrl_cfg["host"], ctrl_cfg["port"])
    motor_cfg = MotorConfig(
        angle_limit_deg=motor_cfg_d["angle_limit_deg"],
        steps_per_degree=motor_cfg_d["steps_per_degree"],
        max_speed_deg_per_sec=motor_cfg_d["max_speed_deg_per_sec"],
        update_hz=motor_cfg_d["update_hz"],
        watchdog_warn_sec=motor_cfg_d["watchdog_warn_sec"],
        watchdog_strikes=motor_cfg_d["watchdog_strikes"],
        calib_seek_sec=motor_cfg_d["calib_seek_sec"],
        calib_center_sec=motor_cfg_d["calib_center_sec"],
    )
    motor = StepperMotorSim(motor_cfg, send=control.broadcast)
    control.attach_motor(motor)
    control.start()

    # RTSP streamer.
    streamer = RTSPStreamer(
        RTSPConfig(
            base_url=rtsp_cfg["base_url"],
            path=rtsp_cfg["path"],
            transport=rtsp_cfg.get("transport", "tcp"),
            encoder=rtsp_cfg.get("encoder", "libx264"),
            preset=rtsp_cfg.get("preset", "ultrafast"),
            tune=rtsp_cfg.get("tune", "zerolatency"),
            bitrate=rtsp_cfg.get("bitrate", "2M"),
            gop=rtsp_cfg.get("gop", cam_cfg.fps),
        ),
        width=cam_cfg.output_width,
        height=cam_cfg.output_height,
        fps=cam_cfg.fps,
    )
    streamer.start()

    log.info("Streaming. Play with:  ffplay -rtsp_transport %s %s",
             rtsp_cfg.get("transport", "tcp"), streamer.url)

    stop = threading.Event()

    def shutdown(*_):
        stop.set()

    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)

    # Frame pump loop.
    frame_interval = 1.0 / cam_cfg.fps
    next_t = time.monotonic()
    try:
        while not stop.is_set():
            if not streamer.is_alive():
                log.error("ffmpeg died; stopping")
                break
            angle = motor.current_angle
            status = {
                "system_on": motor.system_on,
                "calibrated": motor.calibrated,
                "moving": motor.is_moving(),
            }
            frame = camera.render(angle, status)
            if not streamer.write(frame.tobytes()):
                break
            next_t += frame_interval
            sleep = next_t - time.monotonic()
            if sleep > 0:
                time.sleep(sleep)
            else:
                next_t = time.monotonic()  # fell behind; resync
    finally:
        log.info("Shutting down")
        streamer.stop()
        control.stop()
        motor.shutdown()
        if mediamtx_proc:
            mediamtx_proc.terminate()


if __name__ == "__main__":
    main()
