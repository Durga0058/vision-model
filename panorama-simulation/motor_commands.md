# motion_demo UART Command Reference

All commands are sent over Serial at **250000 baud** and must be terminated with `:EOM`.

Every command (including `PING`) resets the heartbeat watchdog. If no command is received for **20 seconds**, the ESP prints a warning strike. After **3 consecutive strikes (60 s total silence)**, the ESP restarts automatically.

---

## Protocol

```
<COMMAND>:<PARAMS>:EOM
```

Simple commands with no parameters use a double colon:

```
<COMMAND>::EOM
```

### Response framing

Most commands receive an immediate `ACK::EOM` as soon as the packet is parsed (before execution completes), followed by a result message when the operation finishes. `PING` is the exception — it replies `PONG::EOM` with no trailing result.

Any command received before `SYSTEM_ON` (other than `PING` and `SYSTEM_ON` itself) is rejected with `NACK::EOM`.

---

## System

### `SYSTEM_ON::EOM`
Arms the system. Runs the calibration routine (finds both limit switches, measures the full range, and moves to the midpoint). Must be sent before any motion command is accepted.

The system is armed **even if calibration fails** — a failed calibration does not block `SYSTEM_ON`. On failure the measured range and midpoint are *not* applied (the motor is left wherever it stopped), so issue a `CM_CALIB` once the hardware fault is cleared.

**Response:** `ACK::EOM`, then `CM_CALIB:PASS:EOM` on success or `CM_CALIB:FAIL:EOM` if calibration times out (see `CM_CALIB`).

---

## Heartbeat

### `PING::EOM`
Heartbeat probe. The ESP replies immediately with `PONG::EOM` and resets the watchdog timer.

Send this at least once every **20 seconds** after the first command to prevent the watchdog from triggering. The watchdog starts after the first UART command is received (not at boot).

**Response:** `PONG::EOM`

**Watchdog behaviour:**

| Silence duration | Action |
|-----------------|--------|
| ≥ 20 s          | `WARN heartbeat strike 1/3` |
| ≥ 40 s          | `WARN heartbeat strike 2/3` |
| ≥ 60 s          | `ERR heartbeat: restarting ESP` → ESP reboots |

Any received command (not just `PING`) resets the strike counter.

---

## Stepper Motor (CM_)

### `CM_GOAL:<deg,sec>:EOM`
Moves the stepper output shaft to `deg` degrees (0 = midpoint) over `sec` seconds using a quintic velocity profile. The target is clamped to ±40% of the calibrated range.

| Parameter | Type  | Description                        |
|-----------|-------|------------------------------------|
| `deg`     | float | Target angle in degrees            |
| `sec`     | float | Duration in seconds (must be > 0)  |

**Example:** `CM_GOAL:30.0,2.5:EOM`  
**Response:** `OK CM_GOAL done: move=<steps> total=<steps>`

---

### `CM_SWEEP:<step_deg,sec>:EOM`
Starts a continuous back-and-forth sweep. The motor moves `step_deg` degrees per leg, bouncing between the safe limits until `CM_STOP` is received.

| Parameter  | Type  | Description                              |
|------------|-------|------------------------------------------|
| `step_deg` | float | Step size per sweep leg in degrees (> 0) |
| `sec`      | float | Duration of each leg in seconds (> 0)   |

**Example:** `CM_SWEEP:20.0,3.0:EOM`

---

### `CM_STOP::EOM`
Stops the stepper sweep and halts servo motion immediately.

---

### `CM_CALIB::EOM`
Re-runs the calibration routine without requiring a full restart. Finds both limit switches, remeasures the range, and returns to the midpoint.

Each seek phase (toward the left limit, then toward the right limit) is guarded by a **2-second watchdog**. The timer resets the moment a limit switch is hit, so the left-seek and right-seek phases each get a fresh 2 s budget. If a switch is not reached within its budget — a broken switch, stalled motor, or mechanical jam — stepping halts immediately (the motor is *not* de-energized) and calibration reports failure. On failure the tracked angle is left unchanged, since the motor stopped at an unknown position.

**Response:** `ACK::EOM`, then `CM_CALIB:PASS:EOM` on success or `CM_CALIB:FAIL:EOM` on timeout.

---

### `CM_STEP:<steps>:EOM`
Moves exactly `steps` motor steps. Positive = right, negative = left. Rejected if the commanded direction would push into an already-active limit switch.

| Parameter | Type | Description                                     |
|-----------|------|-------------------------------------------------|
| `steps`   | long | Signed step count (non-zero)                    |

**Example:** `CM_STEP:-200:EOM`  
**Response:** `OK CM_STEP done: steps=<taken> total=<total> angle=<deg> deg`

---

### `CM_STEP_COUNT_RST::EOM`
Resets the internal cumulative step counter to 0. Does not move the motor.

**Response:** `OK CM_STEP_COUNT_RST: counter reset to 0`

---

### `CM_POSE::EOM`
Returns the current tracked output-shaft angle.

**Response:** `CM_POSE:<ang_deg>:EOM`  
**Example response:** `CM_POSE:-12.3456:EOM`

---

## Limit Switch Behaviour

When a limit switch is triggered during motion, the system:
1. Stops the current move immediately.
2. Snaps `current_angle_deg` to the calibrated position of that switch.
3. Prints a debug message: `DBG limit switch <LEFT|RIGHT> pressed: current_angle=<old> -> <new>`

A `CM_STEP` command directed into an already-active limit switch is rejected:
```
DBG CM_STEP rejected: left limit already active
```

Motion commands directed **away** from an active limit switch execute normally.
