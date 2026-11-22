"""Real SO-101 pen tools (LeRobot 0.7). Hardware paths are UNTESTED: written against the installed
LeRobot source on 21 Nov, never run on an arm. Run in this order the first time:

  python arm_tools.py offsets   --port P            hold the homing L pose; saves bridge.json
  python arm_tools.py signcheck --port P            each joint +10 deg and back; saves signs
  python arm_tools.py corners   --port P            touch the pen to the 4 page corners; saves paper.json
  python arm_tools.py plan "for someone who" "is leaving" -o traj.json
  python arm_tools.py plan --trace card.png [--box l t w h] [--height 12] -o traj.json
  python arm_tools.py play traj.json                dry run: limits, speed, timing
  python arm_tools.py play traj.json --real --port P

Conversion: LeRobot degrees = sign * degrees(q_model) + offset, per joint (bridge.json).
Power-cycle the motors before each session (servo turn-count bug, LeRobot #5324).
"""
import argparse
import json
import sys
import time
from pathlib import Path

import numpy as np

import penlib as pl

HERE = Path(__file__).parent
BRIDGE = HERE / "bridge.json"
PAPER = HERE / "paper.json"
ROBOT_ID = "follower_a"
RATE_HZ = 50
MAX_JOINT_SPEED_DEG_S = 90.0        # plan is refused above this on any joint
APPROACH_SPEED_DEG_S = 20.0
TORQUE_LIMIT_RAW = 500              # STS3215 Torque_Limit is 0..1000; 500 = half, so a touch stalls
TEMP_ABORT_C = 60
LAG_ABORT_DEG, LAG_ABORT_S = 12.0, 0.5
BACKSTOP_STEP_DEG = 20.0            # LeRobot max_relative_target: coarse backstop only


# ---------- bridge between the arm model (radians) and LeRobot (degrees) ----------

def load_bridge():
    if BRIDGE.exists():
        return json.loads(BRIDGE.read_text())
    return {"sign": {j: 1.0 for j in pl.ARM_JOINTS}, "offset_deg": {j: 0.0 for j in pl.ARM_JOINTS},
            "measured": False}


def save_bridge(br):
    BRIDGE.write_text(json.dumps(br, indent=2))


def model_to_lerobot(q_rad, br):
    return {f"{j}.pos": br["sign"][j] * float(np.degrees(q_rad[i])) + br["offset_deg"][j]
            for i, j in enumerate(pl.ARM_JOINTS)}


def lerobot_to_model(obs, br):
    return np.array([np.radians((obs[f"{j}.pos"] - br["offset_deg"][j]) / br["sign"][j])
                     for j in pl.ARM_JOINTS])


# ---------- robot helpers ----------

def connect(port, robot_id):
    from lerobot.robots.so_follower import SO101Follower, SO101FollowerConfig
    cfg = SO101FollowerConfig(port=port, id=robot_id, use_degrees=True,
                              max_relative_target={j: BACKSTOP_STEP_DEG for j in pl.ARM_JOINTS})
    robot = SO101Follower(cfg)
    robot.connect(calibrate=False)       # use the calibration file from lerobot-calibrate
    return robot


def motor_ids(robot):
    return [m.id for m in robot.bus.motors.values()]


def torque_off_and_reset(robot):
    robot.bus.disable_torque()
    failed = robot.bus._bus.reset_turn_count(motor_ids(robot))
    if failed:
        print("turn-count reset failed on", failed, "- power-cycle the motors before going on")
        sys.exit(1)


def safe_torque_on(robot):
    """Turn count cleared, goal = where the arm is now, then torque on at reduced strength."""
    torque_off_and_reset(robot)
    obs = robot.get_observation()
    hold = {k: v for k, v in obs.items() if k.endswith(".pos")}
    robot.bus.sync_write("Goal_Position", {k.removesuffix(".pos"): v for k, v in hold.items()})
    robot.bus.enable_torque()
    for j in pl.ARM_JOINTS:
        robot.bus.write("Torque_Limit", j, TORQUE_LIMIT_RAW, normalize=False)
    return hold


def release(robot):
    input("Support the arm with your hand, then press Enter to switch the motors off. ")
    robot.bus.disable_torque()
    robot.disconnect()


def move_to(robot, start, goal, speed_deg_s=APPROACH_SPEED_DEG_S):
    """Linear move between two LeRobot action dicts at a capped joint speed."""
    span = max(abs(goal[k] - start[k]) for k in goal)
    n = max(1, int(np.ceil(span / speed_deg_s * RATE_HZ)))
    for i in range(1, n + 1):
        robot.send_action({k: start[k] + (goal[k] - start[k]) * i / n for k in goal})
        time.sleep(1.0 / RATE_HZ)


# ---------- subcommands ----------

def cmd_offsets(a):
    robot = connect(a.port, a.id)
    torque_off_and_reset(robot)
    print("Torque is off. Hold the arm in the homing L pose: upper arm straight up, forearm level\n"
          "pointing forward, wrist level, claw pointing forward (see the left panel of sign_check.png).")
    input("Press Enter while holding it still. ")
    obs = robot.get_observation()
    br = load_bridge()
    for j in pl.ARM_JOINTS:
        br["offset_deg"][j] = float(obs[f"{j}.pos"])
        flag = "  <- large, check calibration" if abs(obs[f"{j}.pos"]) > 20 else ""
        print(f"{j:14s} reads {obs[f'{j}.pos']:7.2f} deg at homing{flag}")
    br["measured"] = True
    save_bridge(br)
    print("saved", BRIDGE)
    release(robot)


def cmd_signcheck(a):
    br = load_bridge()
    if not br.get("measured"):
        sys.exit("run offsets first")
    robot = connect(a.port, a.id)
    hold = safe_torque_on(robot)
    print("Open sign_check.png. Each joint will move +10 degrees (model sense) and back.")
    try:
        for j in pl.ARM_JOINTS:
            goal = dict(hold)
            goal[f"{j}.pos"] += br["sign"][j] * 10.0
            move_to(robot, hold, goal)
            time.sleep(0.8)
            ans = input(f"{j}: did it move like its panel in sign_check.png? [y/n] ").strip().lower()
            move_to(robot, goal, hold)
            if ans.startswith("n"):
                br["sign"][j] *= -1
                print(f"  flipped {j} to sign {br['sign'][j]:+.0f}")
    finally:
        save_bridge(br)
        print("saved", BRIDGE)
        release(robot)


def cmd_corners(a):
    import mujoco
    br = load_bridge()
    if not br.get("measured"):
        sys.exit("run offsets and signcheck first")
    m = pl.build_model()
    d = mujoco.MjData(m)
    pen = pl.Pen(m)
    robot = connect(a.port, a.id)
    torque_off_and_reset(robot)
    print("Torque is off. Pen mounted? Guide the pen tip onto each corner of the taped page,\n"
          "going around the page in order. Keep wrist_roll from spinning past its stop.")
    tips = []
    for i in range(4):
        input(f"corner {i + 1}: tip resting on the corner, press Enter ")
        q = lerobot_to_model(robot.get_observation(), br)
        tips.append(pl.fk_tip(m, d, pen, q))
        print("  tip at", np.round(tips[-1], 4))
    robot.disconnect()
    tips = np.array(tips)
    c = tips.mean(0)
    paper = pl.Paper(center_x=float(c[0]), center_y=float(c[1]), z=float(c[2]),
                     half_x=float(np.ptp(tips[:, 0]) / 2), half_y=float(np.ptp(tips[:, 1]) / 2),
                     press=a.press)
    tilt_mm = (tips[:, 2].max() - tips[:, 2].min()) * 1000
    edge = tips[1] - tips[0]
    yaw = np.degrees(np.arctan2(edge[0], edge[1])) % 90
    yaw = min(yaw, 90 - yaw)
    print(f"page centre {np.round(c, 4)}, size {paper.half_x * 2000:.0f} x {paper.half_y * 2000:.0f} mm, "
          f"height spread {tilt_mm:.1f} mm, edge skew {yaw:.1f} deg")
    if tilt_mm > 3 or yaw > 3:
        print("warning: page is tilted or rotated against the arm; the ink-ladder test will show it")
    paper.save(PAPER)
    print("saved", PAPER)


def cmd_plan(a):
    paper = pl.Paper.load(PAPER)
    if not PAPER.exists():
        print("note: no paper.json yet, planning on the default page")
    if a.trace:
        from tracer import trace_image
        strokes, info = trace_image(a.trace, tuple(a.box) if a.box else None, height_mm=a.height)
        print("trace:", info)
        if not info["ok"]:
            if not a.lines:
                sys.exit("trace failed its quality gate; pass the line as text to fall back to the font")
            print("falling back to the font")
            strokes = pl.text_strokes(a.lines)
    else:
        strokes = pl.text_strokes(a.lines)
    if not pl.check_page(strokes, paper):
        sys.exit("strokes leave the page; shorten the line or lower --height")
    traj = pl.joint_trajectory(strokes, paper, RATE_HZ)
    floor = paper.z - paper.press - 0.001
    if traj["ik_misses"]:
        sys.exit(f"{traj['ik_misses']} points out of reach; refused")
    if traj["min_tip_z"] < floor:
        sys.exit(f"tip would go {1000 * (floor - traj['min_tip_z']):.1f} mm below the allowed depth")
    pl.save_trajectory(traj, a.o, meta={"source": a.trace or " / ".join(a.lines)})
    print(f"saved {a.o}: {traj['t'][-1]:.0f} s, {len(traj['t'])} samples, min tip z {traj['min_tip_z']:.4f} m")


def check_trajectory(traj, br):
    """Limits and speed. Returns (LeRobot action dicts, problems, peak deg/s per joint)."""
    m = pl.build_model()
    q = np.array(traj["q"])
    lo, hi = np.degrees(m.jnt_range[:pl.ARM, 0]), np.degrees(m.jnt_range[:pl.ARM, 1])
    problems = []
    if np.any(np.degrees(q) < lo - 1e-6) or np.any(np.degrees(q) > hi + 1e-6):
        problems.append("joint outside the arm model's range")
    speed = np.abs(np.diff(np.degrees(q), axis=0)) * traj["rate_hz"]
    worst = speed.max(axis=0)
    if worst.max() > MAX_JOINT_SPEED_DEG_S:
        problems.append(f"joint speed {worst.max():.0f} deg/s > {MAX_JOINT_SPEED_DEG_S:.0f}")
    actions = [model_to_lerobot(qi, br) for qi in q]
    return actions, problems, worst


def cmd_play(a):
    traj = json.loads(Path(a.traj).read_text())
    br = load_bridge()
    actions, problems, worst = check_trajectory(traj, br)
    dur = traj["t"][-1]
    print(f"{a.traj}: {dur:.0f} s at {traj['rate_hz']} Hz, ink {100 * np.mean(traj['ink']):.0f}% of samples, "
          f"min tip z {traj['min_tip_z']:.4f} m, IK misses {traj['ik_misses']}")
    print("peak joint speeds (deg/s):", {j: round(float(w), 1) for j, w in zip(pl.ARM_JOINTS, worst)})
    print("bridge:", "measured" if br.get("measured") else "DEFAULTS (not measured)")
    if problems:
        sys.exit("refused: " + "; ".join(problems))
    if not a.real:
        print("dry run OK. Add --real --port P to drive the arm.")
        return
    if not br.get("measured") or not PAPER.exists():
        sys.exit("run offsets, signcheck and corners before --real")

    robot = connect(a.port, a.id)
    hold = safe_torque_on(robot)
    reason = None
    try:
        print("approaching the start pose slowly")
        move_to(robot, hold, actions[0])
        lag_since = None
        t0 = time.perf_counter()
        for i, act in enumerate(actions):
            robot.send_action(act)
            if i % 5 == 0:
                obs = robot.get_observation()
                lag = max(abs(obs[k] - act[k]) for k in act)
                lag_since = (lag_since or time.perf_counter()) if lag > LAG_ABORT_DEG else None
                if lag_since and time.perf_counter() - lag_since > LAG_ABORT_S:
                    reason = f"joint lagging {lag:.0f} deg behind the plan"
                    break
            if i % RATE_HZ == 0:
                temps = {j: robot.bus.read("Present_Temperature", j, normalize=False) for j in pl.ARM_JOINTS}
                if max(temps.values()) >= TEMP_ABORT_C:
                    reason = f"servo at {max(temps.values())} C"
                    break
            sleep = t0 + (i + 1) / traj["rate_hz"] - time.perf_counter()
            if sleep > 0:
                time.sleep(sleep)
    except KeyboardInterrupt:
        reason = "stopped by hand"
    print("ABORTED: " + reason + ". The arm is holding where it stopped." if reason else "finished")
    release(robot)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("offsets", "signcheck", "corners"):
        s = sub.add_parser(name)
        s.add_argument("--port", required=True)
        s.add_argument("--id", default=ROBOT_ID)
        if name == "corners":
            s.add_argument("--press", type=float, default=0.002, help="metres to aim below the surface")
    s = sub.add_parser("plan")
    s.add_argument("lines", nargs="*")
    s.add_argument("--trace")
    s.add_argument("--box", type=float, nargs=4)
    s.add_argument("--height", type=float, default=12.0)
    s.add_argument("-o", default="traj.json")
    s = sub.add_parser("play")
    s.add_argument("traj")
    s.add_argument("--real", action="store_true")
    s.add_argument("--port")
    s.add_argument("--id", default=ROBOT_ID)
    a = ap.parse_args()
    if a.cmd == "plan" and not (a.lines or a.trace):
        ap.error("plan needs text lines or --trace IMAGE")
    if a.cmd == "play" and a.real and not a.port:
        ap.error("--real needs --port")
    {"offsets": cmd_offsets, "signcheck": cmd_signcheck, "corners": cmd_corners,
     "plan": cmd_plan, "play": cmd_play}[a.cmd](a)


if __name__ == "__main__":
    main()
