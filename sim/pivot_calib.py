"""Pivot calibration: find where the pen tip sits on the claw, and the dot's position.

Arm joints go limp (gripper keeps holding the pen). Hold the pen tip on one dot and rock the arm
around it. Every sample satisfies  R_i @ tip_local + o_i = dot , so least squares gives
tip_local (gripper-body frame) and the dot (base frame). Saves pen.json; logs every sample.

  python pivot_calib.py [seconds=25]
"""
import json
import sys
import time

import mujoco
import numpy as np

import armlog
import arm_tools as at
import penlib as pl
from twin_live import deg_to_model

PORT = "/dev/tty.usbmodem5CBC4305521"


def main():
    secs = float(sys.argv[1]) if len(sys.argv) > 1 else 25.0
    from lerobot.robots.so_follower import SO101Follower, SO101FollowerConfig
    r = SO101Follower(SO101FollowerConfig(port=PORT, id="follower_a"))
    r.bus.connect()
    br = at.load_bridge()
    m = pl.build_model()
    d = mujoco.MjData(m)
    body = pl.Pen(m).body
    log = armlog.ArmLog("pivot", r)
    r.bus.disable_torque(pl.ARM_JOINTS)                 # gripper stays on, still holding the pen
    r.bus._bus.reset_turn_count([r.bus.motors[j].id for j in pl.ARM_JOINTS])
    print(f"Arm is limp. Hold the pen tip on the dot and rock the arm for {secs:.0f} s.")
    Rs, os_, t_end = [], [], time.monotonic() + secs
    while time.monotonic() < t_end:
        pos = {k: float(v) for k, v in r.bus.sync_read("Present_Position").items()}
        d.qpos[:pl.ARM] = deg_to_model(pos, br)
        mujoco.mj_kinematics(m, d)
        Rs.append(d.xmat[body].reshape(3, 3).copy())
        os_.append(d.xpos[body].copy())
        log.tick("pivot", {}, pos, torque_on=False)
        time.sleep(0.05)
    r.bus.disconnect(disable_torque=False)
    Rs, os_ = np.array(Rs), np.array(os_)
    spread = np.degrees(np.ptp([np.arctan2(R[1, 0], R[0, 0]) for R in Rs]))
    A = np.zeros((3 * len(Rs), 6)); b = np.zeros(3 * len(Rs))
    for i, (R, o) in enumerate(zip(Rs, os_)):
        A[3 * i:3 * i + 3, :3] = R
        A[3 * i:3 * i + 3, 3:] = -np.eye(3)
        b[3 * i:3 * i + 3] = -o
    x, *_ = np.linalg.lstsq(A, b, rcond=None)
    tip_local, dot = x[:3], x[3:]
    resid = np.linalg.norm((A @ x - b).reshape(-1, 3), axis=1) * 1000
    sid = mujoco.mj_name2id(m, mujoco.mjtObj.mjOBJ_SITE, "gripperframe")
    site_local = m.site_pos[sid]
    out = {"tip_local_m": tip_local.round(5).tolist(), "dot_base_m": dot.round(5).tolist(),
           "samples": len(Rs), "residual_mm_median": round(float(np.median(resid)), 2),
           "residual_mm_p90": round(float(np.percentile(resid, 90)), 2), "rotation_spread_deg": round(float(spread), 1),
           "tip_past_gripperframe_mm": round(float(np.linalg.norm(tip_local - site_local)) * 1000, 1)}
    log.event("pivot_result", **out)
    (at.HERE / "pen.json").write_text(json.dumps(out, indent=2))
    print(json.dumps(out, indent=2))
    print("saved", at.HERE / "pen.json", "| log", log.close())
    if out["residual_mm_median"] > 2 or len(Rs) < 100:
        print("WARNING: fit is loose. Keep the tip pinned and rock through bigger angles, then rerun.")


if __name__ == "__main__":
    main()
