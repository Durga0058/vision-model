import cv2
import numpy as np
import yaml
import json
import time
import os

OUTPUT_DIR = "output"
os.makedirs(OUTPUT_DIR, exist_ok=True)

JSONL_PATH = os.path.join(OUTPUT_DIR, "labels.jsonl")
META_PATH = os.path.join(OUTPUT_DIR, "meta.json")
VIDEO_PATH = os.path.join(OUTPUT_DIR, "video.mp4")

# Load Config
with open("config.yaml", "r") as f:
    config = yaml.safe_load(f) or {}

simulation_cfg = config.get('simulation', {})
panoramas = simulation_cfg.get('panoramas', ["panorama-base1.jpg"])
panorama_path = panoramas[0] if panoramas else "panorama-base1.jpg"
panorama = cv2.imread(panorama_path)

if panorama is None:
    panorama = cv2.imread("panorama-simulation/panorama-base1.jpg")

H_b, W_b, _ = panorama.shape
V_w, V_h = 600, 400

# Target Monkey Coordinates
X_m = 400
Y_m = 200

fps = 30
fourcc = cv2.VideoWriter_fourcc(*'mp4v')
video_writer = cv2.VideoWriter(VIDEO_PATH, fourcc, fps, (V_w, V_h))
jsonl_file = open(JSONL_PATH, "w")

x, y = 0, 0
speed_x = 5
frame_count = 0
start_time = time.time()
instruction_text = "track the monkey"

print("🚀 Running VLA Dataset Simulation with Target Monkey...")

while True:
    x += speed_x
    if x + V_w >= W_b or x <= 0:
        speed_x = -speed_x

    # Crop Viewport Canvas
    viewport = panorama[y:y+V_h, x:x+V_w].copy()

    # Check Visibility
    is_visible = (x <= X_m <= x + V_w) and (y <= Y_m <= y + V_h)
    phase = "track" if is_visible else "search"

    # --- DRAW MONKEY DIRECTLY ON VIEWPORT IF VISIBLE ---
    if is_visible:
        rel_x = X_m - x
        rel_y = Y_m - y
        
        # Draw Monkey Avatar
        cv2.circle(viewport, (rel_x, rel_y), 30, (19, 69, 139), -1)      # Head
        cv2.circle(viewport, (rel_x - 10, rel_y - 8), 6, (255, 255, 255), -1) # Eye L
        cv2.circle(viewport, (rel_x + 10, rel_y - 8), 6, (255, 255, 255), -1) # Eye R
        cv2.circle(viewport, (rel_x - 10, rel_y - 8), 2, (0, 0, 0), -1)
        cv2.circle(viewport, (rel_x + 10, rel_y - 8), 2, (0, 0, 0), -1)
        cv2.ellipse(viewport, (rel_x, rel_y + 8), (10, 5), 0, 0, 180, (0, 0, 0), 2) # Smile
        cv2.putText(viewport, "MONKEY", (rel_x - 30, rel_y - 40),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 255), 2)

    # Status Text HUD
    status_color = (0, 255, 0) if is_visible else (0, 165, 255)
    cv2.putText(viewport, f"Phase: {phase.upper()}", (15, 35), 
                cv2.FONT_HERSHEY_SIMPLEX, 0.7, status_color, 2)

    current_angle = round(((x + V_w / 2) / W_b) * 360.0 - 180.0, 4)
    target_angle = round((X_m / W_b) * 360.0 - 180.0, 4)

    video_writer.write(viewport)

    log_entry = {
        "frame": frame_count,
        "t": round(frame_count / fps, 2),
        "current_angle": current_angle,
        "target_angle": target_angle,
        "monkey_count": 1,
        "instruction": instruction_text,
        "phase": phase,
        "ideal_target_angle": target_angle
    }
    jsonl_file.write(json.dumps(log_entry) + "\n")

    cv2.imshow("VLA Panorama Simulation - Target Tracker", viewport)
    
    frame_count += 1
    key = cv2.waitKey(30) & 0xFF
    if key == ord('q'):
        break

jsonl_file.close()
video_writer.release()
cv2.destroyAllWindows()

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

print("\n✅ Dataset files saved successfully!")
