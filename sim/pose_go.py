"""Careful move of the pen tip to a point (base frame, mm), using the rig's kinematics.

Path: shoulder + elbow first, then wrist, then base. Every interpolated point is checked before
anything moves. Bus-only connect, so torque never drops. Logged via armlog; feeds twin_live --udp.

  python pose_go.py --x 241 --y -30 --z 60 --name card_hover
"""
import argparse
import json
import math
import sys
import time
from pathlib import Path

import numpy as np

import armlog

ROOT = Path(__file__).resolve().parent.parent
VENDOR = ROOT / "vendor" / "so101-sketch"
sys.path.insert(0, str(VENDOR))
from so101_sketch.config import LIMITS_DEG                    # noqa: E402
from so101_sketch.kinematics import PenModel                  # noqa: E402

PORT = "/dev/tty.usbmodem5CBC4305521"
JOINTS = ["shoulder_pan", "shoulder_lift", "elbow_flex", "wrist_flex", "wrist_roll"]
P_GAIN, TORQUE, RATE = 32, 800, 50
LAG_DEG, LAG_S, TEMP_MAX = 12.0, 0.5, 55
POSES = ROOT / "state" / "poses.json"


def pen_model(length_mm):
    ref = json.load(open(VENDOR / "calibration/setups/history_artscience_taped_sharpie.json"))
    return PenModel(ref["pen_reference_q"], length_mm / 1000, math.radians(ref["pen_pitch_deg"])), ref


def read(r):
    return {j: float(v) for j, v in r.bus.sync_read("Present_Position", JOINTS).items()}


def plan(start, goal):
    """Raise first: turning the pen down from the L pose would put the tip on the table."""
    a = dict(start); a["shoulder_lift"] = goal["shoulder_lift"]; a["elbow_flex"] = goal["elbow_flex"]
    b = dict(a); b["wrist_flex"] = goal["wrist_flex"]; b["wrist_roll"] = goal["wrist_roll"]
    return [("raise", start, a), ("wrist", a, b), ("base", b, goal)]


def check(legs, pen, floor_m):
    low = 9.0
    for _, s, e in legs:
        for f in np.linspace(0, 1, 60):
            q = {j: s[j] + (e[j] - s[j]) * f for j in JOINTS}
            low = min(low, float(pen.tip({**q, "gripper": 0.0})[2]))
    return low, low >= floor_m


def stream(r, log, name, s, e, speed):
    span = max(abs(e[j] - s[j]) for j in JOINTS)
    n = max(1, int(span / speed * RATE))
    lag_t = None
    for i in range(1, n + 1):
        cmd = {j: s[j] + (e[j] - s[j]) * i / n for j in JOINTS}
        r.bus.sync_write("Goal_Position", cmd)
        time.sleep(1 / RATE)
        now = read(r)
        log.tick(name, cmd, now)
        lag = max(abs(now[j] - cmd[j]) for j in JOINTS)
        if lag > LAG_DEG:
            lag_t = lag_t or time.monotonic()
            if time.monotonic() - lag_t > LAG_S:
                raise RuntimeError(f"lag {lag:.1f} deg in leg '{name}'")
        else:
            lag_t = None
        if i % RATE == 0:
            hot = max(r.bus.sync_read("Present_Temperature", normalize=False).values())
            if hot >= TEMP_MAX:
                raise RuntimeError(f"motor at {hot} C")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--x", type=float, default=241.0)
    ap.add_argument("--y", type=float, default=-30.0)
    ap.add_argument("--z", type=float, default=60.0)
    ap.add_argument("--pen", type=float, default=68.0, help="pen length from the rig's gripper frame, mm")
    ap.add_argument("--speed", type=float, default=12.0, help="max joint speed, deg/s")
    ap.add_argument("--floor", type=float, default=35.0, help="refuse if the tip would go below this, mm")
    ap.add_argument("--name", default="card_hover")
    ap.add_argument("--dry", action="store_true")
    a = ap.parse_args()

    pen, ref = pen_model(a.pen)
    target = np.array([a.x, a.y, a.z]) / 1000
    seed = {**ref["home_seed"], "wrist_roll": 0.0, "gripper": 0.0}
    q, err, pitch = pen.ik(seed, target)
    goal = {j: float(q[j]) for j in JOINTS}
    goal["wrist_roll"] = 0.0
    if err > 0.002:
        sys.exit(f"REFUSED: IK misses the target by {err * 1000:.1f} mm")

    from lerobot.robots.so_follower import SO101Follower, SO101FollowerConfig
    r = SO101Follower(SO101FollowerConfig(port=PORT, id="follower_a"))
    r.bus.connect()
    log = armlog.ArmLog(a.name, r)
    try:
        start = read(r)
        legs = plan(start, goal)
        low, ok = check(legs, pen, a.floor / 1000)
        print("start:", {k: round(v, 1) for k, v in start.items()})
        print("goal: ", {k: round(v, 1) for k, v in goal.items()}, f"| pen lean {math.degrees(pitch):.1f} deg")
        print(f"lowest tip on the path: {low * 1000:.1f} mm (floor {a.floor:.0f})")
        temps = r.bus.sync_read("Present_Temperature", normalize=False)
        print("temps C:", temps)
        for j in ("shoulder_pan", "shoulder_lift", "elbow_flex", "wrist_flex"):
            lo, hi = LIMITS_DEG[j]
            if not lo <= goal[j] <= hi + 0.01:
                print(f"REFUSED: {j} goal {goal[j]:.1f} outside {lo:.1f}..{hi:.1f}")
                return
        if not ok:
            print("REFUSED: path goes below the floor. Nothing moved.")
            return
        if max(temps.values()) >= TEMP_MAX:
            print("REFUSED: a motor is too hot. Nothing moved.")
            return
        if a.dry:
            print("dry run only")
            return
        torque = r.bus.sync_read("Torque_Enable", JOINTS, normalize=False)
        if not all(torque.values()):
            r.bus.sync_write("Goal_Position", start)
            r.bus.enable_torque(JOINTS)
        for j in JOINTS:
            r.bus.write("P_Coefficient", j, P_GAIN, normalize=False)
            r.bus.write("Torque_Limit", j, TORQUE, normalize=False)
        try:
            for name, s, e in legs:
                stream(r, log, f"{a.name}:{name}", s, e, a.speed)
                time.sleep(0.3)
        except (RuntimeError, KeyboardInterrupt) as ex:
            hold = read(r)
            r.bus.sync_write("Goal_Position", hold)
            log.event("stopped", reason=str(ex))
            print("STOPPED and holding:", ex)
            return
        time.sleep(1.0)
        end = read(r)
        log.tick(f"{a.name}:settled", goal, end)
        tip = pen.tip({**end, "gripper": 0.0}) * 1000
        print("reached:", {k: round(v, 2) for k, v in end.items()})
        print("joint error:", {k: round(end[k] - goal[k], 2) for k in JOINTS})
        print(f"modelled tip: x {tip[0]:.1f}  y {tip[1]:.1f}  z {tip[2]:.1f} mm (target {a.x:.0f}, {a.y:.0f}, {a.z:.0f})")
        poses = json.loads(POSES.read_text()) if POSES.exists() else {}
        poses[a.name] = {"goal_deg": goal, "reached_deg": end, "tip_target_mm": [a.x, a.y, a.z], "pen_mm": a.pen}
        POSES.write_text(json.dumps(poses, indent=2))
        print("saved pose", a.name, "| log", log.path)
    finally:
        log.close()
        r.bus.disconnect(disable_torque=False)


if __name__ == "__main__":
    main()
