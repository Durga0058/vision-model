"""Episode dataset format — the on-disk contract shared by all three tools.

One *episode* is one tracking trajectory. It is stored as a directory:

    <dataset_root>/episode_XXXX/
        video.mp4      # the camera frames (clean, no overlay)
        labels.jsonl   # one JSON object per frame, aligned to video frame order
        meta.json      # episode-level metadata + the monkey layout

Each ``labels.jsonl`` line is a per-frame supervision record:

    {"frame": 0, "t": 0.0,
     "current_angle": -12.3,      # proprioceptive input: where the camera points
     "target_angle": 23.4,        # OUTPUT 1: absolute angle to center the target
     "monkey_count": 2,           # OUTPUT 2: monkeys visible this frame
     "instruction": "center the closest monkey",   # language input
     "ideal_target_angle": 23.4}  # policy target (== target_angle in auto mode)

This mirrors the VLA training formulation in ``vla-training.md``: vision (the
frame) + language (instruction) + proprioception (current_angle) -> (target_angle,
monkey_count).
"""

from __future__ import annotations

import json
import os
import re

from video_io import VideoWriter


class EpisodeWriter:
    """Streams frames to video.mp4 and records to labels.jsonl."""

    def __init__(self, ep_dir: str, width: int, height: int, fps: float):
        os.makedirs(ep_dir, exist_ok=True)
        self.ep_dir = ep_dir
        self.width, self.height, self.fps = width, height, fps
        self._video = VideoWriter(
            os.path.join(ep_dir, "video.mp4"), width, height, fps
        )
        self._labels = open(os.path.join(ep_dir, "labels.jsonl"), "w")
        self.n_frames = 0

    def add(self, frame_rgb, record: dict) -> None:
        """Write one RGB frame and its label record (frame index auto-filled)."""
        record = {"frame": self.n_frames, **record}
        self._video.write_rgb(frame_rgb)
        self._labels.write(json.dumps(record) + "\n")
        self.n_frames += 1

    def finalize(self, meta: dict) -> dict:
        self._video.close()
        self._labels.close()
        meta = {**meta, "num_frames": self.n_frames}
        with open(os.path.join(self.ep_dir, "meta.json"), "w") as f:
            json.dump(meta, f, indent=2)
        return meta


def load_episode(ep_dir: str) -> tuple[dict, list[dict], str]:
    """Return (meta, labels, video_path) for an episode directory."""
    with open(os.path.join(ep_dir, "meta.json")) as f:
        meta = json.load(f)
    labels: list[dict] = []
    with open(os.path.join(ep_dir, "labels.jsonl")) as f:
        for line in f:
            line = line.strip()
            if line:
                labels.append(json.loads(line))
    return meta, labels, os.path.join(ep_dir, "video.mp4")


_EP_RE = re.compile(r"episode_(\d+)$")


def next_episode_dir(root: str) -> str:
    """Return the path for the next unused ``episode_XXXX`` under ``root``."""
    os.makedirs(root, exist_ok=True)
    used = [
        int(m.group(1))
        for name in os.listdir(root)
        if (m := _EP_RE.match(name)) and os.path.isdir(os.path.join(root, name))
    ]
    nxt = (max(used) + 1) if used else 0
    return os.path.join(root, f"episode_{nxt:04d}")


def list_episodes(root: str) -> list[str]:
    """All episode directories under ``root``, sorted by index."""
    if not os.path.isdir(root):
        return []
    dirs = [
        name for name in os.listdir(root)
        if _EP_RE.match(name) and os.path.isdir(os.path.join(root, name))
    ]
    return [os.path.join(root, d) for d in sorted(dirs, key=lambda n: int(_EP_RE.match(n).group(1)))]
