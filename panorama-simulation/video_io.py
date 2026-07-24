"""Thin OpenCV video read/write helpers used by the dataset tools.

Frames inside the pipeline are RGB numpy arrays (that is what ``PanoramaCamera``
produces); OpenCV works in BGR, so the writer accepts either and converts once.
"""

from __future__ import annotations

import cv2
import numpy as np

_FOURCC = cv2.VideoWriter_fourcc(*"mp4v")


class VideoWriter:
    def __init__(self, path: str, width: int, height: int, fps: float):
        self.width, self.height = width, height
        self._vw = cv2.VideoWriter(path, _FOURCC, float(fps), (width, height))
        if not self._vw.isOpened():
            raise RuntimeError(f"Could not open video writer for {path}")

    def write_rgb(self, frame_rgb: np.ndarray) -> None:
        self._vw.write(cv2.cvtColor(frame_rgb, cv2.COLOR_RGB2BGR))

    def write_bgr(self, frame_bgr: np.ndarray) -> None:
        self._vw.write(frame_bgr)

    def close(self) -> None:
        self._vw.release()

    def __enter__(self) -> "VideoWriter":
        return self

    def __exit__(self, *_exc) -> None:
        self.close()


def read_frames_bgr(path: str):
    """Yield BGR frames from a video file in order."""
    cap = cv2.VideoCapture(path)
    if not cap.isOpened():
        raise RuntimeError(f"Could not open video {path}")
    try:
        while True:
            ok, frame = cap.read()
            if not ok:
                break
            yield frame
    finally:
        cap.release()


def video_info(path: str) -> dict:
    cap = cv2.VideoCapture(path)
    try:
        return {
            "frames": int(cap.get(cv2.CAP_PROP_FRAME_COUNT)),
            "fps": cap.get(cv2.CAP_PROP_FPS),
            "width": int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)),
            "height": int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT)),
        }
    finally:
        cap.release()
