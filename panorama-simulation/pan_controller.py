"""Rate-limited pan controller shared by the dataset tools.

The camera follows a commanded absolute target angle at up to a hard maximum
speed, so it works equally well for a fixed target (search / centering) and for a
*moving* target (tracking a walking monkey): every frame it steers toward the
current target, capped at ``max_speed_deg_s`` and (optionally) slew-limited by
``accel_deg_s2`` so velocity is continuous rather than jumping.

A proportional law (``velocity = gain * error``, saturated at the speed cap) gives
a clean profile: it cruises at the cap while far away and eases to a stop as it
closes on the target — no overshoot, no fixed move duration to tune. ``step(dt)``
advances either simulated time (auto, dt = 1/fps) or wall-clock time (manual).
"""

from __future__ import annotations


def clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))


class PanController:
    """Pursues an absolute target angle, capped at a maximum speed.

    Positive angle = left (matches the motor protocol / ``invert_pan``).
    """

    def __init__(self, limit_deg: float, start_angle: float = 0.0,
                 max_speed_deg_s: float = 5.0, accel_deg_s2: float | None = 8.0,
                 gain: float = 2.0):
        self.limit = float(limit_deg)
        self.max_speed = float(max_speed_deg_s)
        self.accel = None if accel_deg_s2 in (None, 0) else float(accel_deg_s2)
        self.gain = float(gain)
        self.angle = clamp(float(start_angle), -self.limit, self.limit)
        self.target = self.angle
        self.velocity = 0.0

    def command(self, target_deg: float) -> None:
        """Set the absolute target angle to steer toward (clamped to limits)."""
        self.target = clamp(float(target_deg), -self.limit, self.limit)

    @property
    def moving(self) -> bool:
        return abs(self.velocity) > 1e-3 or abs(self.target - self.angle) > 0.1

    def step(self, dt: float) -> None:
        """Advance one tick: steer toward the target within the speed/accel caps."""
        if dt <= 0:
            return
        err = self.target - self.angle
        desired_v = clamp(self.gain * err, -self.max_speed, self.max_speed)
        if self.accel is not None:
            max_dv = self.accel * dt
            self.velocity += clamp(desired_v - self.velocity, -max_dv, max_dv)
        else:
            self.velocity = desired_v
        self.angle = clamp(self.angle + self.velocity * dt, -self.limit, self.limit)
