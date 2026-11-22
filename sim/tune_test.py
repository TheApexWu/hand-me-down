"""Precision test with writing settings: stiffer gains, higher torque, same-direction approach,
and a software correction that trims the remaining error. Every tick is logged (armlog).

Each joint: start -> target (+10 deg) approached from below -> corrected -> back to start.
Leaves torque ON holding. Release with: python jog_test.py --release (support the arm first).
"""
import time

import armlog
import jog_test as jt
import penlib as pl

P_GAIN = 32            # Feetech's own default; LeRobot uses 16
TORQUE = 800           # 80 % of the STS3215 limit
APPROACH_DEG = 3.0     # always arrive from below the target, so gear slack sits the same way
TRIM_TOL, TRIM_MAX_DEG, TRIM_S = 0.3, 3.0, 1.2
RATE = 50


def present(robot):
    return jt.arm_obs(robot)


def stream(robot, log, start, goal, phase, speed=20.0):
    span = max(abs(goal[k] - start[k]) for k in goal)
    n = max(1, int(span / speed * RATE))
    for i in range(1, n + 1):
        cmd = {k: start[k] + (goal[k] - start[k]) * i / n for k in goal}
        robot.send_action(cmd)
        time.sleep(1 / RATE)
        log.tick(phase, cmd, present(robot))
    return goal


def trim(robot, log, goal, key, phase):
    """Nudge the command until the joint sits within TRIM_TOL of goal (bounded)."""
    cmd = dict(goal)
    t_end = time.monotonic() + TRIM_S
    while time.monotonic() < t_end:
        pos = present(robot)
        err = goal[key] - pos[key]
        log.tick(phase, cmd, pos, extra={"err": round(err, 3)})
        if abs(err) < TRIM_TOL:
            break
        cmd[key] = max(goal[key] - TRIM_MAX_DEG, min(goal[key] + TRIM_MAX_DEG, cmd[key] + 0.5 * err))
        robot.send_action(cmd)
        time.sleep(1 / RATE)
    time.sleep(0.3)
    pos = present(robot)
    log.tick(phase + "-settled", cmd, pos)
    return pos[key], cmd


def main():
    robot = jt.connect()
    log = armlog.ArmLog("tune", robot)
    jt.at.torque_off_and_reset(robot)
    start = present(robot)
    robot.bus.sync_write("Goal_Position", {k.removesuffix(".pos"): v for k, v in start.items()})
    for j in pl.ARM_JOINTS:
        robot.bus.write("P_Coefficient", j, P_GAIN, normalize=False)
    robot.bus.enable_torque(pl.ARM_JOINTS)
    for j in pl.ARM_JOINTS:
        robot.bus.write("Torque_Limit", j, TORQUE, normalize=False)
    log.event("settings", p_gain=P_GAIN, torque_limit=TORQUE, start=armlog._r(start))
    time.sleep(0.5)
    print(f"P={P_GAIN}, torque {TORQUE / 10:.0f}%. log: {log.path}")
    print(f"{'joint':14} {'target':>7} {'raw err':>8} {'trimmed':>8} {'return err':>10}")
    try:
        for j in pl.ARM_JOINTS:
            key = f"{j}.pos"
            target = dict(start); target[key] = start[key] + 10.0
            pre = dict(target); pre[key] -= APPROACH_DEG
            stream(robot, log, start, pre, f"{j}:approach")
            stream(robot, log, pre, target, f"{j}:arrive", speed=8.0)
            time.sleep(0.4)
            raw = present(robot)[key] - target[key]
            got, cmd = trim(robot, log, target, key, f"{j}:trim")
            back_pre = dict(start); back_pre[key] -= APPROACH_DEG
            stream(robot, log, cmd, back_pre, f"{j}:return")
            stream(robot, log, back_pre, start, f"{j}:return-arrive", speed=8.0)
            back, _ = trim(robot, log, start, key, f"{j}:return-trim")
            print(f"{j:14} {target[key]:+7.1f} {raw:+8.2f} {got - target[key]:+8.2f} {back - start[key]:+10.2f}")
            log.event("joint_result", joint=j, raw_err=round(raw, 3), trimmed_err=round(got - target[key], 3),
                      return_err=round(back - start[key], 3))
    except (RuntimeError, KeyboardInterrupt) as e:
        print("STOPPED:", e)
        log.event("stopped", reason=str(e))
    temps = robot.bus.sync_read("Present_Temperature", normalize=False)
    print("temps C:", temps)
    log.save_pose(present(robot), True)
    print("ticks logged:", log.ticks, "->", log.close())
    print("Torque stays ON, holding. Support the arm, then: python jog_test.py --release")
    robot.bus.disconnect(disable_torque=False)


if __name__ == "__main__":
    main()
