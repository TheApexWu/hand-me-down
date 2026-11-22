"""Pivot calibration: where the pen tip sits on the claw, and where the dot is.

Arm joints go limp (gripper keeps holding the pen). Hold the tip on one dot and rock the arm around
it. Every sample satisfies  R_i @ tip_local + o_i = dot, so least squares gives tip_local in the
rig's gripper frame and the dot in the base frame. Ends by clearing turn counts and holding.

  python pivot_calib.py [seconds=25]
"""
import json
import subprocess
import sys
import time
from pathlib import Path

import numpy as np

import armlog

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "vendor" / "so101-sketch"))
from so101_sketch.kinematics import fk                        # noqa: E402

PORT = "/dev/tty.usbmodem5CBC4305521"
JOINTS = ["shoulder_pan", "shoulder_lift", "elbow_flex", "wrist_flex", "wrist_roll"]
WARN_S, PLACE_S, HZ = 5.0, 6.0, 20
P_GAIN, TORQUE = 32, 800


def say(text):
    subprocess.Popen(["say", text])


def read(r):
    return {j: float(v) for j, v in r.bus.sync_read("Present_Position", JOINTS).items()}


def solve(Rs, os_, keep):
    n = len(keep)
    A, b = np.zeros((3 * n, 6)), np.zeros(3 * n)
    for k, i in enumerate(keep):
        A[3 * k:3 * k + 3, :3] = Rs[i]
        A[3 * k:3 * k + 3, 3:] = -np.eye(3)
        b[3 * k:3 * k + 3] = -os_[i]
    return np.linalg.lstsq(A, b, rcond=None)[0]


def fit(Rs, os_):
    """Least squares, re-fit without samples where the tip slipped off the dot."""
    keep = np.arange(len(Rs))
    for _ in range(5):
        x = solve(Rs, os_, keep)
        res = np.linalg.norm(Rs @ x[:3] + os_ - x[3:], axis=1) * 1000
        new = np.where(res <= max(1.5, 2.5 * np.median(res[keep])))[0]
        if len(new) == len(keep) or len(new) < 40:
            break
        keep = new
    return x[:3], x[3:], keep, res


def spread_deg(Rs):
    ref = Rs[len(Rs) // 2]
    cos = (np.trace(np.swapaxes(ref, 0, 1) @ Rs, axis1=1, axis2=2) - 1) / 2
    return float(np.degrees(np.arccos(np.clip(cos, -1, 1))).max())


def main():
    secs = float(sys.argv[1]) if len(sys.argv) > 1 else 25.0
    from lerobot.robots.so_follower import SO101Follower, SO101FollowerConfig
    r = SO101Follower(SO101FollowerConfig(port=PORT, id="follower_a"))
    r.bus.connect()
    log = armlog.ArmLog("pivot", r)
    ids = [r.bus.motors[j].id for j in JOINTS]
    Rs, os_ = [], []
    try:
        say("Hold the arm. Going limp in five seconds.")
        time.sleep(WARN_S)
        r.bus.disable_torque(JOINTS)                          # gripper stays on, holding the pen
        say("Limp. Put the pen on the dot.")
        t_rock = time.monotonic() + PLACE_S
        while time.monotonic() < t_rock:
            log.tick("pivot-place", {}, read(r), torque_on=False)
            time.sleep(1 / HZ)
        say("Rock now.")
        t_end = time.monotonic() + secs
        while time.monotonic() < t_end:
            q = read(r)
            t = fk({**q, "gripper": 0.0})
            Rs.append(t[:3, :3].copy())
            os_.append(t[:3, 3].copy())
            log.tick("pivot", {}, q, torque_on=False)
            time.sleep(1 / HZ)
    finally:
        failed = r.bus._bus.reset_turn_count(ids)             # hand motion can leave a turn count
        hold = read(r)
        r.bus.sync_write("Goal_Position", hold)
        for j in JOINTS:
            r.bus.write("P_Coefficient", j, P_GAIN, normalize=False)
            r.bus.write("Torque_Limit", j, TORQUE, normalize=False)
        if failed:
            print("turn-count reset failed on", failed, "- leaving torque OFF. Support the arm.")
            say("Problem. Keep holding the arm.")
        else:
            r.bus.enable_torque(JOINTS)
            say("Done. Holding.")
        log.save_pose(hold, not failed)
        r.bus.disconnect(disable_torque=False)

    Rs, os_ = np.array(Rs), np.array(os_)
    tip, dot, keep, res = fit(Rs, os_)
    out = {"frame": "rig gripper_frame_link (so101_sketch.kinematics.fk)",
           "tip_local_m": tip.round(5).tolist(), "tip_distance_mm": round(float(np.linalg.norm(tip)) * 1000, 1),
           "dot_base_m": dot.round(5).tolist(), "samples": len(Rs), "samples_kept": int(len(keep)),
           "residual_mm_median": round(float(np.median(res[keep])), 2),
           "residual_mm_p90": round(float(np.percentile(res[keep], 90)), 2),
           "rotation_spread_deg": round(spread_deg(Rs[keep]), 1)}
    good = out["residual_mm_median"] <= 1.5 and out["rotation_spread_deg"] >= 15 and len(keep) >= 100
    out["quality"] = "good" if good else "loose"
    log.event("pivot_result", **out)
    print(json.dumps(out, indent=2))
    if good:
        (Path(__file__).parent / "pen.json").write_text(json.dumps(out, indent=2))
        print("saved pen.json")
    else:
        (Path(__file__).parent / "pen.loose.json").write_text(json.dumps(out, indent=2))
        print("NOT saved as pen.json: keep the tip pinned and rock through bigger angles, then rerun.")
    print("log:", log.close())


if __name__ == "__main__":
    main()
