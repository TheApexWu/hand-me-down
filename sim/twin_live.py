"""Live 3D twin of the real SO-101: MuJoCo render + readouts in one window.

  python twin_live.py            reads the arm directly, read-only (~20 Hz). Close it before
                                 running a motion script: only one process can own the port.
  python twin_live.py --udp      listens on 127.0.0.1:47801 for poses that motion scripts send
                                 every tick (armlog does this), so it can run alongside them.
Keys: a/d rotate, w/s tilt, +/- zoom, q quit.
"""
import argparse
import json
import socket
import time

import cv2
import mujoco
import numpy as np

import arm_tools as at
import penlib as pl

PORT_SERIAL = "/dev/tty.usbmodem5CBC4305521"
UDP_PORT = 47801
W, H = 960, 640
INK_M = 0.0015


def deg_to_model(pos_deg, br):
    return np.array([np.radians((pos_deg[j] - br["offset_deg"][j]) / br["sign"][j]) for j in pl.ARM_JOINTS])


class DirectSource:
    def __init__(self):
        from lerobot.robots.so_follower import SO101Follower, SO101FollowerConfig
        self.r = SO101Follower(SO101FollowerConfig(port=PORT_SERIAL, id="follower_a"))
        self.r.bus.connect()                      # bus only: torque untouched
        self.last_slow, self.slow = 0, {}

    def read(self):
        pos = {k: float(v) for k, v in self.r.bus.sync_read("Present_Position").items()}
        if time.monotonic() - self.last_slow > 1.0:
            self.slow = {"temp": self.r.bus.sync_read("Present_Temperature", normalize=False),
                         "volt": self.r.bus.sync_read("Present_Voltage", normalize=False),
                         "torque": self.r.bus.sync_read("Torque_Enable", normalize=False)}
            self.last_slow = time.monotonic()
        return pos, self.slow

    def close(self):
        self.r.bus.disconnect(disable_torque=False)


class UdpSource:
    def __init__(self):
        self.s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.s.bind(("127.0.0.1", UDP_PORT))
        self.s.setblocking(False)
        self.pos, self.slow = None, {}

    def read(self):
        while True:
            try:
                msg = json.loads(self.s.recv(4096))
            except BlockingIOError:
                break
            self.pos = msg["pos"]
            if "temp" in msg:
                self.slow["temp"] = msg["temp"]
            self.slow["phase"] = msg.get("phase", "")
        return self.pos, self.slow

    def close(self):
        self.s.close()


def overlay(img, lines):
    y = 28
    for text in lines:
        cv2.putText(img, text, (16, y), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (20, 20, 20), 3, cv2.LINE_AA)
        cv2.putText(img, text, (16, y), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (245, 245, 245), 1, cv2.LINE_AA)
        y += 24


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--udp", action="store_true")
    a = ap.parse_args()
    br = at.load_bridge()
    paper = pl.Paper.load(at.HERE / "paper.json")
    m = pl.build_model(paper, offscreen=(W, H))
    d = mujoco.MjData(m)
    pen = pl.Pen(m)
    ren = mujoco.Renderer(m, height=H, width=W, max_geom=8000)
    cam = mujoco.MjvCamera()
    cam.lookat[:] = [0.14, 0.0, 0.10]
    cam.distance, cam.azimuth, cam.elevation = 0.75, 210, -28
    src = UdpSource() if a.udp else DirectSource()
    trail, last_tip, frames, t0, fps = [], None, 0, time.perf_counter(), 0.0
    try:
        while True:
            pos, slow = src.read()
            if pos:
                d.qpos[:pl.ARM] = deg_to_model(pos, br)
                mujoco.mj_forward(m, d)
                tip = pen.tip(d)
                if tip[2] < paper.z + INK_M:
                    if last_tip is not None:
                        trail.append((last_tip, tip.copy()))
                    last_tip = tip.copy()
                else:
                    last_tip = None
            ren.update_scene(d, camera=cam)
            scn = ren.scene
            for p0, p1 in trail[-3000:]:
                if scn.ngeom >= scn.maxgeom:
                    break
                g = scn.geoms[scn.ngeom]
                mujoco.mjv_initGeom(g, mujoco.mjtGeom.mjGEOM_CAPSULE, np.zeros(3), np.zeros(3),
                                    np.eye(3).flatten(), np.array([0.08, 0.1, 0.35, 1], np.float32))
                mujoco.mjv_connector(g, mujoco.mjtGeom.mjGEOM_CAPSULE, 0.0006, p0, p1)
                scn.ngeom += 1
            img = cv2.cvtColor(ren.render(), cv2.COLOR_RGB2BGR)
            lines = [f"SO-101 live twin  [{'udp' if a.udp else 'direct read'}]  {fps:4.1f} fps"]
            if pos:
                lines += [f"{j:14s} {pos[j]:+7.1f} deg" for j in pl.ARM_JOINTS]
                tip = pen.tip(d)
                lines.append(f"pen tip  x {tip[0]*1000:6.1f}  y {tip[1]*1000:6.1f}  z {tip[2]*1000:6.1f} mm")
            else:
                lines.append("waiting for pose...")
            if slow.get("volt"):
                v = list(slow["volt"].values())
                lines.append(f"supply {min(v)/10:.1f}-{max(v)/10:.1f} V")
            if slow.get("temp"):
                t = list(slow["temp"].values())
                lines.append(f"temp {min(t)}-{max(t)} C")
            if slow.get("torque"):
                on = [k for k, v in slow["torque"].items() if v]
                lines.append(f"torque on: {', '.join(on) if on else 'none (limp)'}")
            if slow.get("phase"):
                lines.append(f"phase: {slow['phase']}")
            overlay(img, lines)
            cv2.imshow("SO-101 live twin", img)
            k = cv2.waitKey(1) & 0xFF
            if k == ord("q"):
                break
            cam.azimuth += {ord("a"): -5, ord("d"): 5}.get(k, 0)
            cam.elevation = float(np.clip(cam.elevation + {ord("w"): 3, ord("s"): -3}.get(k, 0), -89, -2))
            cam.distance *= {ord("+"): 0.9, ord("="): 0.9, ord("-"): 1.1}.get(k, 1.0)
            frames += 1
            if frames % 20 == 0:
                fps = 20 / (time.perf_counter() - t0)
                t0 = time.perf_counter()
            if not a.udp:
                time.sleep(0.03)
    finally:
        src.close()
        cv2.destroyAllWindows()


if __name__ == "__main__":
    main()
