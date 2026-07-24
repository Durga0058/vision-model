#  Vision-Language Model: Panorama Simulation & Interactive Control

A high-performance, real-time Python simulation environment designed for visual navigation and dataset generation. This project allows interactive 2D panorama exploration with dynamic viewpoint initialization, random target object (monkey) spawning, and manual keyboard navigation.

---

##  Project Overview

This repository provides a modular simulation system for visual agent models. It allows rendering cropped perspective viewports from ultra-wide 360°/panoramic base images, supporting dynamic spatial positioning and keyframe interaction.

### Key Highlights
- **Multi-Panorama Pipeline:** Dynamically selects and loads from multiple base panoramas (`panorama-base1.jpg` through `panorama-base4.jpg`).
- **Dynamic Randomization:**
  - **Random Viewport Initialization:** Initial camera coordinates $(X, Y)$ are randomized on every run.
  - **Random Target Spawn:** Target object (Monkey marker) spawns at random spatial coordinates $(X_m, Y_m)$ within the scene canvas.
- **Interactive Manual Controls:** Real-time navigation using standard ASCII keyboard controls (`W`, `A`, `S`, `D`).
- **HUD & Visualization:** Real-time overlay showing current viewport coordinates and visual markers for target detection.

---

##  Repository Structure

```text
vision-model/
├── panorama-simulation/          # Core simulator assets and sub-modules
│   ├── panorama-base1.jpg        # Base panorama scene 1
│   ├── panorama-base2.jpg        # Base panorama scene 2
│   ├── panorama-base3.jpg        # Base panorama scene 3
│   └── panorama-base4.jpg        # Base panorama scene 4
├── dataset_manual.py             # Main interactive execution script
├── config.yaml                   # Simulation environment parameters
├── run_simulator.sh              # One-click execution shell script
├── requirements.txt              # Environment dependencies
├── .gitignore                    # Git tracking exclusion rules
├── LICENSE                       # Project license specification
└── README.md                     # Project documentation
## Mathematical & Coordinate LogicThe viewport crop matrix $V$ of dimension $W_v \times H_v$ at current camera center $(x, y)$ on a panorama base canvas $B$ of size $W_b \times H_b$ is defined as:$$V = B[y : y + H_v, \; x : x + W_v]$$Where the camera motion bounds are constrained by:$$0 \le x \le W_b - W_v$$$$0 \le y \le H_b - H_v$$Target visibility check inside current viewport $V(x, y)$:$$\text{Visible} = (x \le X_m \le x + W_v) \land (y \le Y_m \le y + H_v)$$Relative coordinates $(x_r, y_r)$ on screen:$$x_r = X_m - x, \quad y_r = Y_m - y$$  Installation & SetupPrerequisitesPython 3.8 or higherOpenCV with GUI/Qt supportGit1. Clone RepositoryBashgit clone [https://github.com/Durga0058/vision-model.git](https://github.com/Durga0058/vision-model.git)
cd vision-model
2. Set Up Virtual Environment (Optional but Recommended)Bashpython3 -m venv venv
source venv/bin/activate
3. Install DependenciesBashpip install -r requirements.txt
## Configuration (config.yaml)You can tune viewport step sizes, window resolution, and image sources directly via config.yaml:YAMLsimulation:
  panoramas:
    - "panorama-simulation/panorama-base1.jpg"
    - "panorama-simulation/panorama-base2.jpg"
    - "panorama-simulation/panorama-base3.jpg"
    - "panorama-simulation/panorama-base4.jpg"
  viewport:
    width: 600
    height: 400
  controls:
    step_size: 30

randomization:
  random_start_position: true
  random_target_placement: true

target:
  label: "MONKEY"
  marker_color: [0, 0, 255] # Red (BGR)
## Execution GuideOption A: Direct Python ExecutionBashpython3 dataset_manual.py
