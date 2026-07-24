"""Pipes raw RGB frames into ffmpeg, which encodes H.264 and publishes RTSP.

ffmpeg reads ``rawvideo`` from stdin and pushes to a MediaMTX endpoint, so the
result is playable with ``ffplay rtsp://.../<path>``.
"""

from __future__ import annotations

import logging
import subprocess
from dataclasses import dataclass

log = logging.getLogger("streamer")


@dataclass
class RTSPConfig:
    base_url: str = "rtsp://127.0.0.1:8554"
    path: str = "cam"
    transport: str = "tcp"
    encoder: str = "libx264"
    preset: str = "ultrafast"
    tune: str = "zerolatency"
    bitrate: str = "2M"
    gop: int = 25


class RTSPStreamer:
    def __init__(self, cfg: RTSPConfig, width: int, height: int, fps: int):
        self.cfg = cfg
        self.width = width
        self.height = height
        self.fps = fps
        self.url = f"{cfg.base_url.rstrip('/')}/{cfg.path}"
        self._proc: subprocess.Popen | None = None

    def _build_cmd(self) -> list[str]:
        c = self.cfg
        return [
            "ffmpeg",
            "-hide_banner",
            "-loglevel", "warning",
            # Raw input from stdin.
            "-f", "rawvideo",
            "-pix_fmt", "rgb24",
            "-s", f"{self.width}x{self.height}",
            "-r", str(self.fps),
            "-i", "-",
            # Encode.
            "-c:v", c.encoder,
            "-preset", c.preset,
            "-tune", c.tune,
            "-pix_fmt", "yuv420p",
            "-b:v", c.bitrate,
            "-maxrate", c.bitrate,
            "-bufsize", c.bitrate,
            "-g", str(c.gop),
            # Output to RTSP / MediaMTX.
            "-f", "rtsp",
            "-rtsp_transport", c.transport,
            self.url,
        ]

    def start(self) -> None:
        cmd = self._build_cmd()
        log.info("Starting ffmpeg -> %s", self.url)
        log.debug("ffmpeg cmd: %s", " ".join(cmd))
        self._proc = subprocess.Popen(
            cmd, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
        )

    def write(self, frame_bytes: bytes) -> bool:
        """Write one raw RGB frame. Returns False if the pipe is gone."""
        if self._proc is None or self._proc.stdin is None:
            return False
        try:
            self._proc.stdin.write(frame_bytes)
            return True
        except (BrokenPipeError, ValueError):
            log.error("ffmpeg pipe closed (exit code %s)", self._proc.poll())
            return False

    def is_alive(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    def stop(self) -> None:
        if self._proc is None:
            return
        log.info("Stopping ffmpeg")
        try:
            if self._proc.stdin:
                self._proc.stdin.close()
        except OSError:
            pass
        try:
            self._proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            self._proc.kill()
        self._proc = None
