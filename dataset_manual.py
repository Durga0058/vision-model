import cv2
import numpy as np
import yaml
import json
import time
import os

# 1. Output Directory Setup
OUTPUT_DIR = "output"
os.makedirs(OUTPUT_DIR, exist_ok=True)

JSONL_PATH = os.path.join(OUTPUT_DIR, "labels.jsonl")
META_PATH = os.path.join(OUTPUT_DIR, "meta.json")
VIDEO_PATH = os.path.join(OUTPUT_DIR, "video.mp4")

# Load Configuration safely
with open("config.yaml", "r") as f:
    config = yaml.safe_load(f) or {}

# Safely extract values with default fallbacks (Prevents KeyError)
simulation_cfg = config.get('simulation', {})
panoramas = simulation_cfg.get('panoramas', ["panorama-base1.jpg"])

panorama_path = panoramas[0] if panoramas else "panorama-base1.jpg"
panorama = cv2.imread(panorama_path)

if panorama is None:
    # Fallback paths if not found
    panorama = cv2.imread("panorama-simulation/panorama-base1.jpg")

if panorama is None:
    raise FileNotFoundError("Could not find base panorama image! Please check file path.")

H_b, W_b, _ = panorama.shape

viewport_cfg = simulation_cfg.get('viewport', {})
V_w = viewport_cfg.get('width', 600)
V_h = viewport_cfg.get('height', 400)

controls_cfg = simulation_cfg.get('controls', {})
step_size = controls_cfg.get('step_size', 30)

# Random Starting Positions
np.random.seed(int(time.time()))
x = np.random.randint(0, max(1, W_b - V_w))
y = np.random.randint(0, max(1, H_b - V_h))

# Random Monkey (Target) Spawn Position
X_m = np.random.randint(50, max(51, W_b - 50))
Y_m = np.random.randint(50, max(51, H_b - 50))

# Video Writer Setup
fps = 30
fourcc = cv2.VideoWriter_fourcc(*'mp4v')
video_writer = cv2.VideoWriter(VIDEO_PATH, fourcc, fps, (V_w, V_h))

# Open JSONL File for writing
jsonl_file = open(JSONL_PATH, "w")

frame_count = 0
start_time = time.time()
instruction_text = "track the monkey"

print("🚀 Simulation Started! Use W, A, S, D to move, Q to quit & save dataset.")

while True:
    # 1. Crop Viewport Canvas
    viewport = panorama[y:y+V_h, x:x+V_w].copy()

    # 2. Check if Monkey is Visible in Viewport
    is_visible = (x <= X_m <= x + V_w) and (y <= Y_m <= y + V_h)
    phase = "track" if is_visible else "search"

    if is_visible:
        # Draw target marker on viewport
        rel_x = X_m - x
        rel_y = Y_m - y
        cv2.circle(viewport, (rel_x, rel_y), 15, (0, 0, 255), -1)
        cv2.putText(viewport, "MONKEY", (rel_x - 25, rel_y - 20),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 0, 255), 2)

    # 3. Calculate Angles
    current_angle = round(((x + V_w / 2) / W_b) * 360.0 - 180.0, 4)
    target_angle = round((X_m / W_b) * 360.0 - 180.0, 4)
    ideal_target_angle = target_angle

    # 4. Display Info Overlay (HUD)
    cv2.putText(viewport, f"Phase: {phase.upper()}", (10, 30), 
                cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 0), 2)

    # 5. Record Video Frame
    video_writer.write(viewport)

    # 6. Record Line in labels.jsonl
    log_entry = {
        "frame": frame_count,
        "t": round(frame_count / fps, 2),
        "current_angle": current_angle,
        "target_angle": target_angle,
        "monkey_count": 1,
        "instruction": instruction_text,
        "phase": phase,
        "ideal_target_angle": ideal_target_angle
    }
    jsonl_file.write(json.dumps(log_entry) + "\n")

    # Show Window
    cv2.imshow("VLA Dataset Generator - Panorama", viewport)
    
    frame_count += 1

    # Key Controls
    key = cv2.waitKey(30) & 0xFF
    if key == ord('q'):
        break
    elif key == ord('w'):
        y = max(0, y - step_size)
    elif key == ord('s'):
        y = min(H_b - V_h, y + step_size)
    elif key == ord('a'):
        x = max(0, x - step_size)
    elif key == ord('d'):
        x = min(W_b - V_w, x + step_size)

# Cleanup & Metadata Generation
jsonl_file.close()
video_writer.release()
cv2.destroyAllWindows()

# Write meta.json
duration = round(time.time() - start_time, 2)
meta_data = {
    "fps": fps,
    "total_frames": frame_count,
    "duration_seconds": duration,
    "resolution": [V_w, V_h],
    "instruction": instruction_text,
    "created_at": "2026-07-27"
}

with open(META_PATH, "w") as mf:
    json.dump(meta_data, mf, indent=2)

print(f"\n✅ Dataset successfully generated in '{OUTPUT_DIR}/' folder!")
print(f"📹 Saved: {VIDEO_PATH}")
print(f"📄 Saved: {JSONL_PATH}")
print(f"⚙️ Saved: {META_PATH}")
