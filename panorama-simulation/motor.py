"""Simulated stepper-motor controller.

Emulates the `motion_demo` ESP firmware described in motor_commands.md: it parses
``<COMMAND>:<PARAMS>:EOM`` packets, drives a time-based motion profile on a
background thread, tracks limit switches and the heartbeat watchdog, and emits the
same ACK / result / debug framing the real hardware would send over UART.

The class is transport-agnostic: callers feed it raw command strings and supply a
``send`` callback used to deliver framed responses. The control server wires that
callback to a TCP socket; the rest of the program just reads ``current_angle``.
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Callable, Optional

log = logging.getLogger("motor")

EOM = ":EOM"


def _quintic(tau: float) -> float:
    """Smooth 0->1 easing with zero velocity and acceleration at both ends."""
    tau = max(0.0, min(1.0, tau))
    return tau * tau * tau * (tau * (tau * 6.0 - 15.0) + 10.0)


@dataclass
class MotorConfig:
    angle_limit_deg: float = 60.0
    steps_per_degree: float = 35.5
    max_speed_deg_per_sec: float = 60.0
    update_hz: int = 100
    watchdog_warn_sec: float = 20.0
    watchdog_strikes: int = 3
    calib_seek_sec: float = 1.0
    calib_center_sec: float = 1.0


@dataclass
class _Move:
    """An in-progress motion profile."""
    start_angle: float
    target_angle: float
    duration: float
    start_time: float
    kind: str = "goal"           # goal | step | calib
    on_done: Optional[Callable[[], None]] = None
    # For limit detection we record whether the commanded target was clamped.
    requested_angle: float = 0.0
    hit_limit: Optional[str] = None  # 'LEFT' | 'RIGHT' when a switch fires


class StepperMotorSim:
    """Thread-safe simulation of the pan stepper motor + limit switches."""

    def __init__(self, cfg: MotorConfig, send: Callable[[str], None]):
        self.cfg = cfg
        self._send = send
        self._lock = threading.RLock()

        # Output-shaft state.
        self.current_angle = 0.0
        self.step_counter = 0          # cumulative signed steps
        self.system_on = False
        self.calibrated = False

        # Motion state.
        self._move: Optional[_Move] = None
        self._sweep: Optional[dict] = None   # {step_deg, sec, direction}

        # Heartbeat watchdog.
        self._last_cmd_time = time.monotonic()
        self._strikes = 0
        self._watchdog_armed = False   # starts after first command

        self._stop_evt = threading.Event()
        self._thread = threading.Thread(target=self._run, name="motor-sim", daemon=True)
        self._thread.start()

    # ----------------------------------------------------------------- helpers
    @property
    def limit(self) -> float:
        return self.cfg.angle_limit_deg

    def reply(self, msg: str) -> None:
        """Send a response framed with :EOM (msg should not include it)."""
        self._send(msg + EOM)

    def _clamp(self, deg: float) -> tuple[float, Optional[str]]:
        """Clamp a target angle to the limits, returning (clamped, switch_or_None)."""
        if deg > self.limit:
            return self.limit, "LEFT"      # positive angle = left rotation
        if deg < -self.limit:
            return -self.limit, "RIGHT"
        return deg, None

    def is_moving(self) -> bool:
        with self._lock:
            return self._move is not None or self._sweep is not None

    # ----------------------------------------------------------------- watchdog
    def _kick_watchdog(self) -> None:
        self._last_cmd_time = time.monotonic()
        self._strikes = 0
        self._watchdog_armed = True

    # --------------------------------------------------------------- main loop
    def _run(self) -> None:
        dt = 1.0 / self.cfg.update_hz
        while not self._stop_evt.wait(dt):
            now = time.monotonic()
            with self._lock:
                self._advance_motion(now)
                self._advance_sweep(now)
            self._check_watchdog(now)

    def _advance_motion(self, now: float) -> None:
        mv = self._move
        if mv is None:
            return
        elapsed = now - mv.start_time
        tau = 1.0 if mv.duration <= 0 else elapsed / mv.duration
        ang = mv.start_angle + (mv.target_angle - mv.start_angle) * _quintic(tau)
        self.current_angle = ang
        if tau >= 1.0:
            self.current_angle = mv.target_angle
            done = mv.on_done
            hit = mv.hit_limit
            self._move = None
            if hit:
                # Limit switch fired: snap + debug message (firmware behaviour).
                log.debug("limit switch %s pressed", hit)
                self.reply(
                    f"DBG limit switch {hit} pressed: current_angle="
                    f"{mv.requested_angle:.4f} -> {self.current_angle:.4f}"
                )
            if done:
                done()

    def _advance_sweep(self, now: float) -> None:
        sw = self._sweep
        if sw is None or self._move is not None:
            return
        # Launch the next leg of the sweep.
        step = sw["step_deg"] * sw["direction"]
        target = self.current_angle + step
        clamped, switch = self._clamp(target)
        if switch:
            # Bounce off the limit and reverse for the following leg.
            sw["direction"] *= -1
        self._move = _Move(
            start_angle=self.current_angle,
            target_angle=clamped,
            duration=sw["sec"],
            start_time=now,
            kind="goal",
            requested_angle=target,
            hit_limit=switch,
        )

    def _check_watchdog(self, now: float) -> None:
        if not self._watchdog_armed:
            return
        silence = now - self._last_cmd_time
        warn = self.cfg.watchdog_warn_sec
        strike = int(silence // warn)
        if strike > self._strikes and strike <= self.cfg.watchdog_strikes:
            self._strikes = strike
            if strike >= self.cfg.watchdog_strikes:
                self.reply("ERR heartbeat: restarting ESP")
                self._restart()
            else:
                self.reply(f"WARN heartbeat strike {strike}/{self.cfg.watchdog_strikes}")

    def _restart(self) -> None:
        """Simulate the ESP rebooting after sustained heartbeat loss."""
        log.warning("watchdog restart: ESP rebooting")
        self._move = None
        self._sweep = None
        self.system_on = False
        self.calibrated = False
        self.current_angle = 0.0
        self.step_counter = 0
        self._watchdog_armed = False
        self._strikes = 0

    # ------------------------------------------------------------- command API
    def handle(self, raw: str) -> None:
        """Parse and dispatch one framed command (with or without trailing :EOM)."""
        line = raw.strip()
        if line.endswith(EOM):
            line = line[: -len(EOM)]
        if not line:
            return
        parts = line.split(":")
        cmd = parts[0].strip().upper()
        params = parts[1].strip() if len(parts) > 1 else ""

        # Every received command resets the watchdog.
        self._kick_watchdog()

        # PING is always allowed and is the one command with no ACK.
        if cmd == "PING":
            self.reply("PONG")
            return

        with self._lock:
            if not self.system_on and cmd != "SYSTEM_ON":
                self.reply("NACK")
                return
            dispatch = {
                "SYSTEM_ON": self._cmd_system_on,
                "CM_GOAL": self._cmd_goal,
                "CM_SWEEP": self._cmd_sweep,
                "CM_STOP": self._cmd_stop,
                "CM_CALIB": self._cmd_calib,
                "CM_STEP": self._cmd_step,
                "CM_STEP_COUNT_RST": self._cmd_step_rst,
                "CM_POSE": self._cmd_pose,
            }
            fn = dispatch.get(cmd)
            if fn is None:
                self.reply(f"NACK unknown command {cmd}")
                return
            fn(params)

    # --------------------------------------------------------------- commands
    def _cmd_system_on(self, _params: str) -> None:
        self.reply("ACK")
        self.system_on = True
        self._run_calibration()

    def _cmd_calib(self, _params: str) -> None:
        self.reply("ACK")
        self._run_calibration()

    def _run_calibration(self) -> None:
        """Simulate seek-left, seek-right, return-to-mid. Always succeeds here."""
        self._move = None
        self._sweep = None
        seek = self.cfg.calib_seek_sec
        center = self.cfg.calib_center_sec
        now = time.monotonic()

        def to_right():
            self._move = _Move(self.current_angle, self.limit, seek, time.monotonic(),
                               kind="calib", on_done=to_mid)

        def to_mid():
            self._move = _Move(self.current_angle, 0.0, center, time.monotonic(),
                               kind="calib", on_done=finish)

        def finish():
            self.calibrated = True
            self.reply("CM_CALIB:PASS")

        # First seek left limit, then right, then midpoint.
        self._move = _Move(self.current_angle, -self.limit, seek, now,
                           kind="calib", on_done=to_right)

    def _cmd_goal(self, params: str) -> None:
        try:
            deg_s, sec_s = params.split(",")
            deg, sec = float(deg_s), float(sec_s)
        except ValueError:
            self.reply("NACK CM_GOAL: expected deg,sec")
            return
        if sec <= 0:
            self.reply("NACK CM_GOAL: sec must be > 0")
            return
        self.reply("ACK")
        clamped, switch = self._clamp(deg)
        steps = int(round((clamped - self.current_angle) * self.cfg.steps_per_degree))

        def done():
            self.step_counter += steps
            self.reply(f"OK CM_GOAL done: move={steps} total={self.step_counter}")

        self._sweep = None
        self._move = _Move(self.current_angle, clamped, sec, time.monotonic(),
                           kind="goal", on_done=done, requested_angle=deg,
                           hit_limit=switch)

    def _cmd_sweep(self, params: str) -> None:
        try:
            step_s, sec_s = params.split(",")
            step_deg, sec = float(step_s), float(sec_s)
        except ValueError:
            self.reply("NACK CM_SWEEP: expected step_deg,sec")
            return
        if step_deg <= 0 or sec <= 0:
            self.reply("NACK CM_SWEEP: step_deg and sec must be > 0")
            return
        self.reply("ACK")
        # Sweep toward the nearest direction first; positive = left.
        direction = 1 if self.current_angle <= 0 else -1
        self._move = None
        self._sweep = {"step_deg": step_deg, "sec": sec, "direction": direction}
        self.reply(f"OK CM_SWEEP started: step={step_deg} sec={sec}")

    def _cmd_stop(self, _params: str) -> None:
        self.reply("ACK")
        self._move = None
        self._sweep = None
        self.reply("OK CM_STOP: motion halted")

    def _cmd_step(self, params: str) -> None:
        try:
            steps = int(params)
        except ValueError:
            self.reply("NACK CM_STEP: expected integer steps")
            return
        if steps == 0:
            self.reply("NACK CM_STEP: steps must be non-zero")
            return
        self.reply("ACK")
        # Reject a move that pushes further into an already-active limit switch.
        at_left = self.current_angle >= self.limit - 1e-6
        at_right = self.current_angle <= -self.limit + 1e-6
        if steps > 0 and at_left:
            self.reply("DBG CM_STEP rejected: left limit already active")
            return
        if steps < 0 and at_right:
            self.reply("DBG CM_STEP rejected: right limit already active")
            return

        delta_deg = steps / self.cfg.steps_per_degree
        target = self.current_angle + delta_deg
        clamped, switch = self._clamp(target)
        taken = int(round((clamped - self.current_angle) * self.cfg.steps_per_degree))
        # Duration based on max slew rate.
        dur = max(abs(clamped - self.current_angle) / self.cfg.max_speed_deg_per_sec, 1e-3)

        def done():
            self.step_counter += taken
            self.reply(
                f"OK CM_STEP done: steps={taken} total={self.step_counter} "
                f"angle={self.current_angle:.4f} deg"
            )

        self._sweep = None
        self._move = _Move(self.current_angle, clamped, dur, time.monotonic(),
                           kind="step", on_done=done, requested_angle=target,
                           hit_limit=switch)

    def _cmd_step_rst(self, _params: str) -> None:
        self.reply("ACK")
        self.step_counter = 0
        self.reply("OK CM_STEP_COUNT_RST: counter reset to 0")

    def _cmd_pose(self, _params: str) -> None:
        self.reply(f"CM_POSE:{self.current_angle:.4f}")

    # ------------------------------------------------------------------ teardown
    def shutdown(self) -> None:
        self._stop_evt.set()
        self._thread.join(timeout=1.0)
