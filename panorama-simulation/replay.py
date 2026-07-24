"""Replay a recorded tracking episode with the label overlay.

Reads ``episode_XXXX/{video.mp4,labels.jsonl,meta.json}`` and plays the video back
with the per-frame angles and monkey count drawn on top (current angle, the target
absolute angle, and the pan-position bar) — a quick way to sanity-check that the
recorded labels line up with what the camera is actually seeing.

    python replay.py datasets/episode_0000                 # interactive viewer
    python replay.py datasets/episode_0000 --save out.mp4   # write annotated mp4 (headless)
    python replay.py datasets/episode_0000 --loop           # loop playback

Interactive keys
    Space : pause / resume
    Left / Right : step one frame (when paused)
    Q / Esc : quit
"""

from __future__ import annotations

import argparse
import os
import sys

import cv2

from episode import load_episode
from hud import draw_hud
from video_io import VideoWriter, read_frames_bgr

LEFT_KEYS = {65361, 2424832, 16777234, 81}
RIGHT_KEYS = {65363, 2555904, 16777236, 83}
WINDOW = "VLA episode replay"


def _annotate(frame_bgr, rec, meta):
    draw_hud(
        frame_bgr,
        current_angle=rec.get("current_angle", 0.0),
        target_angle=rec.get("target_angle", 0.0),
        monkey_count=rec.get("monkey_count", 0),
        instruction=rec.get("instruction", meta.get("instruction", "")),
        limit_deg=meta.get("angle_limit_deg", 60.0),
        extra_lines=[f"frame {rec.get('frame', 0)}  t={rec.get('t', 0.0):.2f}s"
                     f"  [{meta.get('mode', '?')}]"],
    )
    return frame_bgr


def save_annotated(ep_dir, meta, labels, video_path, out_path):
    fps = meta.get("fps", 25)
    w, h = meta.get("output_width"), meta.get("output_height")
    writer = VideoWriter(out_path, w, h, fps)
    n = 0
    for frame, rec in zip(read_frames_bgr(video_path), labels):
        writer.write_bgr(_annotate(frame, rec, meta))
        n += 1
    writer.close()
    print(f"Wrote {n} annotated frames -> {out_path}")


def play_interactive(meta, labels, video_path, loop=False):
    frames = list(read_frames_bgr(video_path))
    if not frames:
        print("No frames in video.")
        return
    n = min(len(frames), len(labels))
    fps = meta.get("fps", 25)
    delay = max(1, int(1000 / fps))
    cv2.namedWindow(WINDOW, cv2.WINDOW_NORMAL)
    cv2.resizeWindow(WINDOW, meta.get("output_width", 1280), meta.get("output_height", 720))

    i, paused = 0, False
    while True:
        frame = _annotate(frames[i].copy(), labels[i], meta)
        cv2.imshow(WINDOW, frame)
        key = cv2.waitKeyEx(0 if paused else delay)
        if key in (ord("q"), ord("Q"), 27):
            break
        if key == ord(" "):
            paused = not paused
            continue
        if paused and key in LEFT_KEYS:
            i = max(0, i - 1)
            continue
        if paused and key in RIGHT_KEYS:
            i = min(n - 1, i + 1)
            continue
        if not paused:
            i += 1
            if i >= n:
                if loop:
                    i = 0
                else:
                    break
    cv2.destroyAllWindows()


def main() -> None:
    ap = argparse.ArgumentParser(description="Replay a recorded tracking episode")
    ap.add_argument("episode", help="path to an episode_XXXX directory")
    ap.add_argument("--save", default=None, help="write an annotated mp4 instead of showing a window")
    ap.add_argument("--loop", action="store_true", help="loop interactive playback")
    args = ap.parse_args()

    if not os.path.isdir(args.episode):
        print(f"Not a directory: {args.episode}", file=sys.stderr)
        sys.exit(1)

    meta, labels, video_path = load_episode(args.episode)
    print(f"Episode: {args.episode}")
    print(f"  mode={meta.get('mode')}  instruction={meta.get('instruction')!r}")
    print(f"  frames={meta.get('num_frames')}  fps={meta.get('fps')}  "
          f"monkeys={len(meta.get('monkeys', []))}")

    if args.save:
        save_annotated(args.episode, meta, labels, video_path, args.save)
    else:
        play_interactive(meta, labels, video_path, loop=args.loop)


if __name__ == "__main__":
    main()
