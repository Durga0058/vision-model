"""Plot camera pan speed (deg/s) over time for the episodes in a dataset.

Reads each episode's labels.jsonl, differentiates current_angle to get the signed
angular velocity, and overlays all episodes on one chart. A marker shows the
search->track handoff (when the instructed monkey is acquired).

    python plot_speed.py datasets/testrun                 # -> datasets/testrun/speed.png
    python plot_speed.py datasets/testrun --out /tmp/s.png
"""

from __future__ import annotations

import argparse
import glob
import json
import os

import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

# dataviz categorical palette (light) + chrome/ink
SERIES = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4",
          "#008300", "#4a3aa7", "#e34948"]
SURFACE, PRIMARY, SECONDARY, MUTED, GRID = \
    "#fcfcfb", "#0b0b0b", "#52514e", "#898781", "#e1e0d9"


def main() -> None:
    ap = argparse.ArgumentParser(description="Plot pan speed per episode")
    ap.add_argument("dataset", help="dataset root containing episode_XXXX/ dirs")
    ap.add_argument("--out", default=None, help="output PNG (default <dataset>/speed.png)")
    args = ap.parse_args()

    eps = sorted(glob.glob(os.path.join(args.dataset, "episode_*")))
    if not eps:
        raise SystemExit(f"no episodes found in {args.dataset}")
    out = args.out or os.path.join(args.dataset, "speed.png")

    plt.rcParams.update({"font.family": "sans-serif", "font.size": 11})
    fig, ax = plt.subplots(figsize=(11, 6.2), dpi=150)
    fig.patch.set_facecolor(SURFACE); ax.set_facecolor(SURFACE)

    vcap = 0.0
    for i, ep in enumerate(eps):
        lab = [json.loads(l) for l in open(os.path.join(ep, "labels.jsonl"))]
        meta = json.load(open(os.path.join(ep, "meta.json")))
        t = np.array([r["t"] for r in lab])
        ang = np.array([r["current_angle"] for r in lab])
        v = np.gradient(ang, t)                     # signed angular velocity, deg/s
        vcap = max(vcap, meta.get("max_pan_speed_deg_per_sec", 0) or np.max(np.abs(v)))
        phase = [r.get("phase", "track") for r in lab]
        c = SERIES[i % len(SERIES)]
        label = (f"{os.path.basename(ep).replace('episode_', 'ep ')}  ·  "
                 f"{meta['instruction']}  ·  {len(meta['monkeys'])} monkeys")
        ax.plot(t, v, color=c, lw=2.0, label=label, zorder=3)
        if "track" in phase:
            k = phase.index("track")
            ax.scatter([t[k]], [v[k]], s=55, facecolor=c, edgecolor="white",
                       linewidth=1.5, zorder=5)

    if vcap:
        for y in (vcap, -vcap):
            ax.axhline(y, color=MUTED, lw=1.0, ls="--", zorder=1)
        ax.text(ax.get_xlim()[1], vcap, f"  speed cap ±{vcap:g}°/s", va="bottom",
                ha="right", fontsize=8.5, color=MUTED)
    ax.axhline(0, color=MUTED, lw=1.0, zorder=1)
    ax.grid(True, axis="y", color=GRID, lw=0.8, zorder=0)
    ax.set_axisbelow(True)
    for s in ("top", "right"):
        ax.spines[s].set_visible(False)
    for s in ("left", "bottom"):
        ax.spines[s].set_color(MUTED)
    ax.tick_params(colors=MUTED)
    ax.set_xlabel("time (s)", color=SECONDARY)
    ax.set_ylabel("angular velocity  (deg / s)   —   + = left,  − = right", color=SECONDARY)
    ax.set_title("Camera pan speed per episode  (search → acquire ● → center → track walker)",
                 color=PRIMARY, fontsize=14, fontweight="bold", pad=12)

    leg = ax.legend(loc="upper right", frameon=True, fontsize=9, labelcolor=PRIMARY)
    leg.get_frame().set_facecolor(SURFACE); leg.get_frame().set_edgecolor(GRID)
    ax.text(0.012, 0.02, "● = search→track handoff (instructed monkey acquired)",
            transform=ax.transAxes, fontsize=8.5, color=MUTED)

    fig.tight_layout()
    fig.savefig(out, facecolor=SURFACE)
    print("saved", out)


if __name__ == "__main__":
    main()
