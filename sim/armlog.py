"""Every tick of every arm run gets recorded, and the last known pose is always on disk.

runs/<YYYYmmdd-HHMMSS>-<label>.jsonl   one line per tick: commanded vs actual degrees, phase,
                                       temperatures once a second
state/last_pose.json                   latest actual pose, rewritten ~5 Hz (atomic), so any
                                       process can see where the arm is, even after a crash
"""
import json
import os
import socket
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RUNS = ROOT / "runs"
STATE = ROOT / "state"


class ArmLog:
    def __init__(self, label, robot=None, temp_every_s=1.0, pose_every_s=0.2):
        RUNS.mkdir(exist_ok=True)
        STATE.mkdir(exist_ok=True)
        self.path = RUNS / f"{datetime.now():%Y%m%d-%H%M%S}-{label}.jsonl"
        self.f = open(self.path, "a", buffering=1)
        self.robot = robot
        self.t0 = time.monotonic()
        self.temp_every, self.pose_every = temp_every_s, pose_every_s
        self.last_temp = self.last_pose = -1e9
        self.ticks = 0
        self.udp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)   # feeds twin_live.py --udp

    def tick(self, phase, cmd, pos, torque_on=True, extra=None):
        now = time.monotonic()
        row = {"t": round(now - self.t0, 4), "wall": datetime.now().isoformat(timespec="milliseconds"),
               "phase": phase, "cmd": _r(cmd), "pos": _r(pos)}
        if self.robot is not None and now - self.last_temp >= self.temp_every:
            try:
                row["temp"] = self.robot.bus.sync_read("Present_Temperature", normalize=False)
            except Exception as e:  # a missed temperature read must not stop the arm
                row["temp_err"] = str(e)[:80]
            self.last_temp = now
        if extra:
            row.update(extra)
        self.f.write(json.dumps(row) + "\n")
        self.ticks += 1
        try:
            msg = {"pos": row["pos"], "phase": phase, **({"temp": row["temp"]} if "temp" in row else {})}
            self.udp.sendto(json.dumps(msg).encode(), ("127.0.0.1", 47801))
        except OSError:
            pass
        if now - self.last_pose >= self.pose_every:
            self.save_pose(pos, torque_on)
            self.last_pose = now

    def save_pose(self, pos, torque_on):
        tmp = STATE / "last_pose.json.tmp"
        tmp.write_text(json.dumps({"wall": datetime.now().isoformat(timespec="milliseconds"),
                                   "pos_deg": _r(pos), "torque_on": torque_on, "run": self.path.name}))
        os.replace(tmp, STATE / "last_pose.json")

    def event(self, name, **data):
        self.f.write(json.dumps({"t": round(time.monotonic() - self.t0, 4), "event": name, **data}) + "\n")

    def close(self):
        self.f.close()
        return self.path


def _r(d):
    return {k.removesuffix(".pos"): round(float(v), 3) for k, v in (d or {}).items()}
