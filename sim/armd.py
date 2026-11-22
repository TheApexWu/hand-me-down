"""Always-on arm server: one process owns the serial bus at 50 Hz, so a command skips the 1.3 s
LeRobot import and state comes from memory. Feeds armlog (runs/, last_pose.json, twin UDP).

Never drops torque: if this dies, the motors keep holding their last goal.

  python armd.py     then drive it with ./arm
"""
import json
import os
import select
import signal
import socket
import sys
import time
from collections import deque

import armlog
import pose_go as pg
from so101_sketch.config import LIMITS_DEG

SOCK = armlog.STATE / "armd.sock"
RATE, FLOOR_MM, PEN_MM, MAX_STEP_DEG = 50, 35.0, 68.0, 1.2
LIFT_OFF_DEG = 25.0                                           # shoulder back: clears the card at any height
J = pg.JOINTS
LIMITED = [j for j in J if j in LIMITS_DEG]                   # the rig sets no limit on wrist_roll
ALIAS = {"pan": "shoulder_pan", "lift": "shoulder_lift", "elbow": "elbow_flex", "wrist": "wrist_flex",
         "roll": "wrist_roll"}


OFFSETS = json.loads((armlog.STATE / "joint_offsets.json").read_text()) if (armlog.STATE / "joint_offsets.json").exists() else {}


def from_motor(d):
    """A reseated gear moves a joint's zero; planners keep working in the original frame."""
    return {k: v - OFFSETS.get(k, 0.0) for k, v in d.items()}


def to_motor(d):
    return {k: v + OFFSETS.get(k, 0.0) for k, v in d.items()}


def read(r):
    return from_motor(pg.read(r))


class Refused(Exception):
    pass


def sustained_hot(history, limit):
    """The gripper sensor spikes +10 C for single samples; a real overheat stays high."""
    return len(history) == history.maxlen and max(min(t[m] for t in history) for m in history[0]) >= limit


class Server:
    def __init__(self, r):
        self.r, self.q, self.running = r, deque(), True
        self.log = armlog.ArmLog("armd", r, temp_every_s=1e9)  # temps are read here so the loop can act on them
        self.pen, self.ref = pg.pen_model(PEN_MM)
        self.pos, self.temps, self.phase, self.last_stop, self.lag_t = read(r), {}, "idle", None, None
        # moves start from the last goal: starting from present banks gravity sag on every command
        self.goal = from_motor({j: float(v) for j, v in r.bus.sync_read("Goal_Position", J).items()})
        self.heat = deque(maxlen=3)

    def z(self, q):
        return float(self.pen.tip({**q, "gripper": 0.0})[2]) * 1000

    def ramp(self, phase, s, e, speed):
        n = max(1, int(1.5 * max(abs(e[j] - s[j]) for j in J) / speed * RATE))  # s-curve peaks at 1.5x its mean
        ease = [(i / n) ** 2 * (3 - 2 * i / n) for i in range(1, n + 1)]        # linear ramps start and stop abruptly
        return [(phase, {j: s[j] + (e[j] - s[j]) * f for j in J}) for f in ease]

    def lift_off(self, start, speed):
        off = {**start, "shoulder_lift": start["shoulder_lift"] - LIFT_OFF_DEG}
        path = [self.z({j: start[j] + (off[j] - start[j]) * i / 40 for j in J}) for i in range(41)]
        if min(path) < path[0] - 1 or path[-1] < FLOOR_MM:
            raise Refused(f"lift-off would not clear the card: z {path[0]:.0f} -> {path[-1]:.0f} mm")
        return off, self.ramp("lift_off", start, off, min(speed, 15))

    def route(self, goal, speed):
        for j in LIMITED:
            lo, hi = LIMITS_DEG[j]
            if not lo <= goal[j] <= hi + 0.01:
                raise Refused(f"{j} {goal[j]:.1f} outside {lo:.1f}..{hi:.1f}")
        start, ticks = dict(self.goal), []
        if self.z(start) < FLOOR_MM:
            start, ticks = self.lift_off(start, speed)
        for legs in (pg.plan(start, goal), [(n, e, s) for n, s, e in reversed(pg.plan(goal, start))]):
            low, ok = pg.check(legs, self.pen, FLOOR_MM / 1000)
            if ok:
                for name, s, e in legs:
                    ticks += self.ramp(name, s, e, speed)
                self.q.extend(ticks)
                return
        raise Refused(f"path dips to {low * 1000:.0f} mm, floor {FLOOR_MM:.0f}")

    def halt(self, why, release=False):
        """Keep the last goal on a normal stop: goal = present would bank the gravity sag every time."""
        self.q.clear()
        if release:
            self.pos = read(self.r)
            self.r.bus.sync_write("Goal_Position", to_motor(self.pos))
            self.goal = dict(self.pos)
        self.phase, self.last_stop, self.lag_t = "idle", why, None
        self.log.event("halt", reason=why)

    def state(self):
        return {"ok": True, "moving": bool(self.q), "left_s": round(len(self.q) / RATE, 2), "phase": self.phase,
                "pos": {k: round(v, 2) for k, v in self.pos.items()}, "tip_z_mm": round(self.z(self.pos), 1),
                "temps": self.temps, "last_stop": self.last_stop}

    def handle(self, m):
        c = m.get("c")
        if c == "state":
            return self.state()
        if c == "stop":
            self.halt("stop command")
            return self.state()
        if c == "quit":
            self.running = False
            return {"ok": True}
        if c == "save":
            poses = json.loads(pg.POSES.read_text()) if pg.POSES.exists() else {}
            poses[m["name"]] = {"reached_deg": dict(self.pos)}
            tmp = pg.POSES.with_suffix(".tmp")
            tmp.write_text(json.dumps(poses, indent=2))
            os.replace(tmp, pg.POSES)
            return self.state()
        if self.q:
            raise Refused("arm is moving: send stop first")
        if c == "gains":
            for key, reg, top in (("p", "P_Coefficient", 64), ("i", "I_Coefficient", 16), ("d", "D_Coefficient", 64)):
                if key in m:
                    for j in J[:4]:
                        self.r.bus.write(reg, j, max(0, min(int(m[key]), top)), normalize=False)
            return {**self.state(), "gains": {reg: self.r.bus.sync_read(reg, J[:4], normalize=False)
                                              for reg in ("P_Coefficient", "I_Coefficient", "D_Coefficient")}}
        speed = max(1.0, min(float(m.get("speed", 20)), 40.0))
        if c == "up":
            if self.z(self.goal) < FLOOR_MM:
                self.q.extend(self.lift_off(dict(self.goal), speed)[1])
        elif c == "go":
            poses = json.loads(pg.POSES.read_text())
            if m["name"] not in poses:
                raise Refused(f"no pose '{m['name']}': have {sorted(poses)}")
            self.route({j: float(poses[m["name"]]["reached_deg"][j]) for j in J}, speed)
        elif c == "joints":
            goal = dict(self.goal)
            for k, v in m.get("set", {}).items():
                goal[ALIAS.get(k, k)] = float(v)
            for k, v in m.get("add", {}).items():
                goal[ALIAS.get(k, k)] += float(v)
            self.route({j: goal[j] for j in J}, speed)
        elif c == "tip":
            if m["z"] < FLOOR_MM:
                raise Refused(f"z {m['z']} below floor {FLOOR_MM:.0f}; pen-down moves go through traj")
            target = [m["x"] / 1000, m["y"] / 1000, m["z"] / 1000]
            q, err, _ = self.pen.ik({**self.ref["home_seed"], "wrist_roll": 0.0, "gripper": 0.0}, target)
            if err > 0.002:
                raise Refused(f"IK misses by {err * 1000:.1f} mm")
            self.route({**{j: float(q[j]) for j in J}, "wrist_roll": 0.0}, speed)
        elif c == "traj":                                     # pre-planned 50 Hz joint path, e.g. pen-down strokes
            pts = json.load(open(m["path"]))
            prev = self.goal
            for p in pts:
                if any(not LIMITS_DEG[j][0] <= p[j] <= LIMITS_DEG[j][1] + 0.01 for j in LIMITED):
                    raise Refused("traj leaves joint limits")
                if max(abs(p[j] - prev[j]) for j in J) > MAX_STEP_DEG:
                    raise Refused(f"traj step over {MAX_STEP_DEG} deg per tick")
                prev = p
            self.q.extend((m.get("phase", "traj"), {j: float(p[j]) for j in J}) for p in pts)
        else:
            raise Refused(f"unknown command {c}")
        return self.state()

    def serve(self, srv):
        while select.select([srv], [], [], 0)[0]:
            conn, _ = srv.accept()
            try:
                conn.settimeout(0.05)
                buf = b""
                while not buf.endswith(b"\n"):
                    chunk = conn.recv(65536)
                    if not chunk:
                        break
                    buf += chunk
                try:
                    reply = self.handle(json.loads(buf))
                except Refused as e:
                    reply = {"ok": False, "err": str(e), **{k: v for k, v in self.state().items() if k != "ok"}}
                except Exception as e:                        # a bad command must never take down the arm loop
                    reply = {"ok": False, "err": f"{type(e).__name__}: {e}"}
                conn.sendall((json.dumps(reply) + "\n").encode())
            except OSError:
                pass
            finally:
                conn.close()

    def loop(self, srv):
        next_t, tick, bad, last_temp, cmd = time.monotonic(), 0, 0, 0.0, {}
        while self.running:
            try:
                self.pos = read(self.r)
                bad = 0
            except Exception:
                bad += 1
                if bad > 25:
                    raise
                next_t = time.monotonic() + 1 / RATE
                time.sleep(1 / RATE)
                continue
            moving = bool(self.q)
            if moving:
                self.phase, cmd = self.q.popleft()
                self.r.bus.sync_write("Goal_Position", to_motor(cmd))
                self.goal = cmd
                lag = max(abs(self.pos[j] - cmd[j]) for j in J)
                self.lag_t = (self.lag_t or time.monotonic()) if lag > pg.LAG_DEG else None
                if self.lag_t and time.monotonic() - self.lag_t > pg.LAG_S:
                    self.halt(f"lag {lag:.1f} deg", release=True)
            elif self.phase != "idle":
                self.phase = "idle"
            extra = None
            if time.monotonic() - last_temp >= 1.0:
                self.temps = self.r.bus.sync_read("Present_Temperature", normalize=False)
                last_temp, extra = time.monotonic(), {"temp": self.temps}
                self.heat.append(self.temps)
                if sustained_hot(self.heat, pg.TEMP_MAX) and self.q:
                    self.halt(f"motor at {max(self.temps.values())} C", release=True)
            if moving or extra or tick % 10 == 0:                 # idle at 5 Hz keeps the run log small
                self.log.tick(self.phase, cmd if moving else {}, self.pos, extra=extra)
            self.serve(srv)
            tick += 1
            next_t += 1 / RATE
            delay = next_t - time.monotonic()
            if delay > 0:
                time.sleep(delay)
            else:
                next_t = time.monotonic()


def main():
    if SOCK.exists():
        probe = socket.socket(socket.AF_UNIX)
        try:
            probe.connect(str(SOCK))
            sys.exit("armd is already running")
        except OSError:
            SOCK.unlink()
        finally:
            probe.close()
    from lerobot.robots.so_follower import SO101Follower, SO101FollowerConfig
    r = SO101Follower(SO101FollowerConfig(port=pg.PORT, id="follower_a"))
    r.bus.connect()                                           # bus only: robot.connect() drops torque
    srv = socket.socket(socket.AF_UNIX)
    srv.bind(str(SOCK))
    srv.listen(8)
    s = Server(r)
    signal.signal(signal.SIGTERM, lambda *_: setattr(s, "running", False))
    try:
        if not all(r.bus.sync_read("Torque_Enable", J, normalize=False).values()):
            r.bus.sync_write("Goal_Position", to_motor(s.pos))
            r.bus.enable_torque(J)
        for j in J:
            r.bus.write("P_Coefficient", j, pg.P_GAIN, normalize=False)
            r.bus.write("Torque_Limit", j, pg.TORQUE, normalize=False)
        print("armd ready", flush=True)
        s.loop(srv)
    finally:
        srv.close()
        SOCK.unlink(missing_ok=True)
        s.log.close()
        r.bus.disconnect(disable_torque=False)


if __name__ == "__main__":
    main()
