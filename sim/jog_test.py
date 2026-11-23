"""Sign and tracking test: each arm joint +10 deg and back, slow, half torque, logged.

Leaves the arm holding its starting pose with torque ON (it would drop if switched off).
Release later with:  python jog_test.py --release   (support the arm first).
"""
import json
import os
import sys
import time

import numpy as np

import arm_tools as at
import penlib as pl

PORT = "/dev/tty.usbmodem5CBC4305521"
CAL = os.path.expanduser("~/.cache/huggingface/lerobot/calibration/robots/so_follower/follower_a.json")
STEP_DEG, SPEED, HOLD_S = 10.0, 20.0, 1.5
K = 360 / 4095


def connect():
    from lerobot.robots.so_follower import SO101Follower, SO101FollowerConfig
    cfg = SO101FollowerConfig(port=PORT, id="follower_a", use_degrees=True,
                              disable_torque_on_disconnect=False,
                              max_relative_target={j: 20.0 for j in pl.ARM_JOINTS})
    robot = SO101Follower(cfg)
    robot.connect(calibrate=False)
    return robot


def arm_obs(robot):
    obs = robot.get_observation()
    return {f"{j}.pos": float(obs[f"{j}.pos"]) for j in pl.ARM_JOINTS}


def limits_deg():
    cal = json.load(open(CAL))
    out = {}
    for j in pl.ARM_JOINTS:
        mid = (cal[j]["range_min"] + cal[j]["range_max"]) / 2
        out[j] = ((cal[j]["range_min"] - mid) * K, (cal[j]["range_max"] - mid) * K)
    return out


def move(robot, start, goal, log):
    span = max(abs(goal[k] - start[k]) for k in goal)
    n = max(1, int(np.ceil(span / SPEED * at.RATE_HZ)))
    lag_since = None
    for i in range(1, n + 1):
        cmd = {k: start[k] + (goal[k] - start[k]) * i / n for k in goal}
        robot.send_action(cmd)
        time.sleep(1.0 / at.RATE_HZ)
        now = arm_obs(robot)
        lag = max(abs(now[k] - cmd[k]) for k in cmd)
        log.append(lag)
        if lag > at.LAG_ABORT_DEG:
            lag_since = lag_since or time.monotonic()
            if time.monotonic() - lag_since > at.LAG_ABORT_S:
                raise RuntimeError(f"lag {lag:.1f} deg for > {at.LAG_ABORT_S}s, stopping")
        else:
            lag_since = None


def main():
    robot = connect()
    if "--release" in sys.argv:
        robot.bus.disable_torque()
        robot.bus.disconnect(disable_torque=True)
        print("torque off")
        return
    at.torque_off_and_reset(robot)
    start = arm_obs(robot)
    robot.bus.sync_write("Goal_Position", {k.removesuffix(".pos"): v for k, v in start.items()})
    robot.bus.enable_torque(pl.ARM_JOINTS)
    for j in pl.ARM_JOINTS:
        robot.bus.write("Torque_Limit", j, at.TORQUE_LIMIT_RAW, normalize=False)
    time.sleep(0.5)
    lim = limits_deg()
    print("start pose (LeRobot deg):", {k: round(v, 1) for k, v in start.items()})
    print(f"{'joint':14} {'start':>7} {'target':>7} {'reached':>8} {'err':>6} {'max lag':>8} {'temp C':>6}")
    try:
        for j in pl.ARM_JOINTS:
            key = f"{j}.pos"
            target = start[key] + STEP_DEG
            if target > lim[j][1] - 5:
                target = start[key] - STEP_DEG
            goal = dict(start); goal[key] = target
            log = []
            move(robot, start, goal, log)
            time.sleep(HOLD_S)
            reached = arm_obs(robot)[key]
            move(robot, goal, start, log)
            time.sleep(0.8)
            temp = robot.bus.read("Present_Temperature", j, normalize=False)
            print(f"{j:14} {start[key]:+7.1f} {target:+7.1f} {reached:+8.1f} {reached - target:+6.1f} "
                  f"{max(log):8.1f} {temp:6}")
    except (RuntimeError, KeyboardInterrupt) as e:
        print("STOPPED:", e)
    final = arm_obs(robot)
    print("end pose (LeRobot deg):", {k: round(v, 1) for k, v in final.items()})
    print("Torque stays ON, holding. Support the arm, then run: python jog_test.py --release")
    robot.bus.disconnect(disable_torque=False)


if __name__ == "__main__":
    main()
