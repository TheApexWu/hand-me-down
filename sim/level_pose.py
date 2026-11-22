"""Move slowly to LeRobot 0 deg on the arm joints (the model's zero pose) and hold, for a level check.

Connects the bus only: robot.connect() runs configure(), which drops torque and lets the arm sag.
Refuses if any joint would travel more than MAX_MOVE_DEG. Every tick is logged.
"""
import sys
import time

import armlog
import penlib as pl

PORT = "/dev/tty.usbmodem5CBC4305521"
P_GAIN, TORQUE, SPEED, RATE = 32, 800, 8.0, 50
MAX_MOVE_DEG = 25.0


def read(r):
    return {j: float(v) for j, v in r.bus.sync_read("Present_Position", pl.ARM_JOINTS).items()}


def main():
    from lerobot.robots.so_follower import SO101Follower, SO101FollowerConfig
    r = SO101Follower(SO101FollowerConfig(port=PORT, id="follower_a"))
    r.bus.connect()
    log = armlog.ArmLog("level", r)
    try:
        start = read(r)
        torque = r.bus.sync_read("Torque_Enable", pl.ARM_JOINTS, normalize=False)
        print("start deg:", {k: round(v, 1) for k, v in start.items()}, "| torque:", torque)
        worst = max(abs(v) for v in start.values())
        if worst > MAX_MOVE_DEG:
            print(f"REFUSED: a joint is {worst:.1f} deg from zero (limit {MAX_MOVE_DEG}). Nothing moved.")
            return
        if not all(torque.values()):
            r.bus.sync_write("Goal_Position", start)
            r.bus.enable_torque(pl.ARM_JOINTS)
        for j in pl.ARM_JOINTS:
            r.bus.write("P_Coefficient", j, P_GAIN, normalize=False)
            r.bus.write("Torque_Limit", j, TORQUE, normalize=False)
        n = max(1, int(worst / SPEED * RATE))
        for i in range(1, n + 1):
            cmd = {j: start[j] * (1 - i / n) for j in pl.ARM_JOINTS}
            r.bus.sync_write("Goal_Position", cmd)
            time.sleep(1 / RATE)
            log.tick("to-zero", cmd, read(r))
        time.sleep(1.0)
        end = read(r)
        log.tick("settled", {j: 0.0 for j in pl.ARM_JOINTS}, end)
        print("holding at:", {k: round(v, 2) for k, v in end.items()})
        print("temps C:", r.bus.sync_read("Present_Temperature", normalize=False))
        print("log:", log.path)
    finally:
        log.close()
        r.bus.disconnect(disable_torque=False)


if __name__ == "__main__":
    sys.exit(main())
