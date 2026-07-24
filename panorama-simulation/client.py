"""Control client for the panorama camera simulator.

Connects to the simulator's TCP command socket (the stand-in for the UART serial
link), sends ``<COMMAND>:<PARAMS>:EOM`` packets, prints framed responses, and
sends a periodic PING heartbeat to keep the watchdog happy.

Usage:
  python client.py                       # interactive REPL
  python client.py --send "CM_GOAL:30,2" # one-shot command, then wait briefly
  python client.py --no-heartbeat        # disable automatic PING

In the REPL you may type either the raw protocol (CM_GOAL:30,2) or convenience
shortcuts:
  on            -> SYSTEM_ON
  goal 30 2     -> CM_GOAL:30,2
  sweep 20 3    -> CM_SWEEP:20,3
  step -200     -> CM_STEP:-200
  stop          -> CM_STOP
  calib         -> CM_CALIB
  pose          -> CM_POSE
  ping          -> PING
  rst           -> CM_STEP_COUNT_RST
  help          -> show shortcuts
  quit / exit   -> leave
"""

from __future__ import annotations

import argparse
import socket
import sys
import threading
import time

import yaml

EOM = ":EOM"

SHORTCUTS = {
    "on": "SYSTEM_ON",
    "stop": "CM_STOP",
    "calib": "CM_CALIB",
    "pose": "CM_POSE",
    "ping": "PING",
    "rst": "CM_STEP_COUNT_RST",
}


def expand(line: str) -> str:
    """Translate a convenience shortcut into a raw protocol command."""
    parts = line.split()
    if not parts:
        return ""
    head = parts[0].lower()
    if head in SHORTCUTS:
        return SHORTCUTS[head]
    if head == "goal" and len(parts) == 3:
        return f"CM_GOAL:{parts[1]},{parts[2]}"
    if head == "sweep" and len(parts) == 3:
        return f"CM_SWEEP:{parts[1]},{parts[2]}"
    if head == "step" and len(parts) == 2:
        return f"CM_STEP:{parts[1]}"
    # Otherwise assume the user typed raw protocol.
    return line.strip()


def frame(cmd: str) -> bytes:
    cmd = cmd.strip()
    if cmd.endswith(EOM):
        return cmd.encode()
    # Simple commands need the double-colon form: CMD::EOM
    if ":" not in cmd:
        return (cmd + ":" + EOM).encode()
    return (cmd + EOM).encode()


class Client:
    def __init__(self, host: str, port: int):
        self.sock = socket.create_connection((host, port), timeout=5)
        self.sock.settimeout(None)
        self._stop = threading.Event()
        threading.Thread(target=self._reader, daemon=True).start()

    def _reader(self) -> None:
        buf = ""
        while not self._stop.is_set():
            try:
                data = self.sock.recv(4096)
            except OSError:
                break
            if not data:
                print("\n[connection closed by server]")
                self._stop.set()
                break
            buf += data.decode(errors="replace")
            while EOM in buf:
                msg, buf = buf.split(EOM, 1)
                if msg.strip():
                    sys.stdout.write(f"\r<< {msg.strip()}\n>> ")
                    sys.stdout.flush()

    def send(self, cmd: str) -> None:
        self.sock.sendall(frame(cmd))

    def heartbeat(self, interval: float) -> None:
        while not self._stop.wait(interval):
            try:
                self.send("PING")
            except OSError:
                break

    def close(self) -> None:
        self._stop.set()
        try:
            self.sock.close()
        except OSError:
            pass


def main() -> None:
    ap = argparse.ArgumentParser(description="Panorama camera control client")
    ap.add_argument("--config", default="config.yaml")
    ap.add_argument("--host", default=None)
    ap.add_argument("--port", type=int, default=None)
    ap.add_argument("--send", default=None, help="send one command then wait and exit")
    ap.add_argument("--no-heartbeat", action="store_true")
    ap.add_argument("--heartbeat-interval", type=float, default=10.0)
    args = ap.parse_args()

    host, port = args.host, args.port
    if host is None or port is None:
        with open(args.config) as f:
            cfg = yaml.safe_load(f)
        host = host or cfg["control_server"]["host"]
        port = port or cfg["control_server"]["port"]

    try:
        client = Client(host, port)
    except OSError as e:
        print(f"Could not connect to {host}:{port} — is server.py running?  ({e})")
        sys.exit(1)

    if not args.no_heartbeat:
        threading.Thread(
            target=client.heartbeat, args=(args.heartbeat_interval,), daemon=True
        ).start()

    # One-shot mode.
    if args.send:
        client.send(expand(args.send))
        time.sleep(2.0)  # allow ACK + result to arrive
        client.close()
        return

    print(__doc__.split("Usage:")[0].strip())
    print("Type 'help' for shortcuts, 'quit' to exit.\n")
    try:
        while not client._stop.is_set():
            try:
                line = input(">> ").strip()
            except EOFError:
                break
            if not line:
                continue
            low = line.lower()
            if low in ("quit", "exit", "q"):
                break
            if low == "help":
                print(__doc__[__doc__.index("In the REPL"):])
                continue
            cmd = expand(line)
            if cmd:
                client.send(cmd)
    except KeyboardInterrupt:
        pass
    finally:
        client.close()
        print("\nbye")


if __name__ == "__main__":
    main()
