import os
import cv2
import random
import numpy as np

# =========================================================
# 1. USE 4 DIFFERENT PANORAMA BASE IMAGES
# =========================================================
PANO_DIR = "panorama-simulation"
PANO_FILES = [
    os.path.join(PANO_DIR, "panorama-base1.jpg"),
    os.path.join(PANO_DIR, "panorama-base2.jpg"),
    os.path.join(PANO_DIR, "panorama-base3.jpg"),
    os.path.join(PANO_DIR, "panorama-base4.jpg")
]

# Filter out only existing images
available_panos = [f for f in PANO_FILES if os.path.exists(f)]

if not available_panos:
    # Fallback to single base image if renamed ones don't exist
    fallback = os.path.join(PANO_DIR, "panorama-base.jpg")
    if os.path.exists(fallback):
        available_panos = [fallback]
    else:
        raise FileNotFoundError("Error: No panorama images found in panorama-simulation folder!")

# Randomly select 1 panorama image on each run
selected_pano = random.choice(available_panos)
print(f"[SUCCESS] Selected Panorama: {selected_pano}")

bg_image = cv2.imread(selected_pano)
img_h, img_w, _ = bg_image.shape

# Viewport dimensions
VIEW_W, VIEW_H = 600, 400

# =========================================================
# 2. RANDOM STARTING POINT & RANDOM MONKEY PLACEMENT
# =========================================================
# Random Camera Starting Point (X, Y)
curr_x = random.randint(0, max(0, img_w - VIEW_W))
curr_y = random.randint(0, max(0, img_h - VIEW_H))

# Random Monkey Placement Coordinates (X, Y)
monkey_x = random.randint(50, max(50, img_w - 50))
monkey_y = random.randint(50, max(50, img_h - 50))

print(f"[INFO] Random Camera Start Viewport: ({curr_x}, {curr_y})")
print(f"[INFO] Random Monkey Coordinates: ({monkey_x}, {monkey_y})")

# =========================================================
# 3. MANUAL CONTROL PART (W / A / S / D)
# =========================================================
step_size = 30

print("\n--- CONTROLS ---")
print("W: Move Up | S: Move Down | A: Move Left | D: Move Right")
print("Q: Quit Simulation\n")

while True:
    # Crop current view window
    viewport = bg_image[curr_y:curr_y + VIEW_H, curr_x:curr_x + VIEW_W].copy()
    
    # Check if monkey is visible in current viewport
    if curr_x <= monkey_x <= curr_x + VIEW_W and curr_y <= monkey_y <= curr_y + VIEW_H:
        rel_m_x = monkey_x - curr_x
        rel_m_y = monkey_y - curr_y
        
        # Render Monkey Marker (Red dot + Green Text)
        cv2.circle(viewport, (rel_m_x, rel_m_y), 18, (0, 0, 255), -1)
        cv2.putText(viewport, "MONKEY", (rel_m_x - 25, rel_m_y - 25), 
                    cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 0), 2)

    # Render On-screen Controls Overlay
    hud_info = f"Pos: ({curr_x}, {curr_y}) | Press W/A/S/D to move"
    cv2.putText(viewport, hud_info, (10, 25), 
                cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 1)

    cv2.imshow("Panorama Simulation (Manual Control)", viewport)
    
    key = cv2.waitKey(30) & 0xFF
    if key == ord('q'):
        print("[INFO] Exiting simulation.")
        break
    elif key == ord('w'): # Up
        curr_y = max(0, curr_y - step_size)
    elif key == ord('s'): # Down
        curr_y = min(img_h - VIEW_H, curr_y + step_size)
    elif key == ord('a'): # Left
        curr_x = max(0, curr_x - step_size)
    elif key == ord('d'): # Right
        curr_x = min(img_w - VIEW_W, curr_x + step_size)

cv2.destroyAllWindows()
