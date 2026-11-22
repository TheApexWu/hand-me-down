"""Live 3D twin of the SO-101 for the pen run. Run the viewer under mjpython on macOS.

  mjpython twin.py --demo         viewer + a demo sender child process (no arm, no serial)
  mjpython twin.py                viewer only; reads the player's UDP packets on 127.0.0.1:47800
  python  twin.py --demo-sender   the stand-in player (plain python, separate process)

Packet (player -> twin, 21 bytes): 5 little-endian float32 (model radians, arm_tools joint
order) + 1 byte ink flag. The twin derives the pen tip itself with penlib.fk_tip.
The socket is polled on the main thread: a background thread crashed mjpython (segfault).
"""
import argparse, socket, struct, subprocess, sys, time
from pathlib import Path

import mujoco, mujoco.viewer
import numpy as np

SIM = Path("/Users/amadeuswoo/dev/hand-me-down/sim")
sys.path.insert(0, str(SIM))
import penlib as pl

PORT = 47800
PACK = struct.Struct("<5fB")
PLAN_RGBA = np.array([0.2, 0.4, 0.9, 0.25], np.float32)   # faint blue: planned ink
DONE_RGBA = np.array([0.08, 0.1, 0.35, 1.0], np.float32)  # solid: completed ink
PLAN_R, DONE_R = 0.0004, 0.0006
ON_PAGE_M = 0.0008                                         # tip within 0.8 mm counts as ink


def add_capsule(scn, p0, p1, rgba, radius):
    if scn.ngeom >= scn.maxgeom:
        return
    g = scn.geoms[scn.ngeom]
    mujoco.mjv_initGeom(g, mujoco.mjtGeom.mjGEOM_CAPSULE, np.zeros(3), np.zeros(3),
                        np.eye(3).flatten(), rgba)
    mujoco.mjv_connector(g, mujoco.mjtGeom.mjGEOM_CAPSULE, radius, p0, p1)
    scn.ngeom += 1


def planned_segments(paper, strokes):
    """Faint overlay: consecutive inking waypoints from the plan the arm follows."""
    wps = pl.waypoints(strokes, paper)
    return [(wps[i - 1][0], wps[i][0]) for i in range(1, len(wps)) if wps[i][2] and wps[i - 1][2]]


def demo_sender(paper, strokes, hz=25):
    traj = pl.joint_trajectory(strokes, paper, rate_hz=50)
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    for i in range(0, len(traj["q"]), 50 // hz):
        s.sendto(PACK.pack(*traj["q"][i].astype(np.float32), int(traj["ink"][i])), ("127.0.0.1", PORT))
        time.sleep(1.0 / hz)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--demo", action="store_true")
    ap.add_argument("--demo-sender", action="store_true")
    ap.add_argument("--paper")
    ap.add_argument("--seconds", type=float, default=0, help="stop after N s (0 = until closed)")
    a = ap.parse_args()

    paper = pl.Paper.load(a.paper or SIM / "paper.json")
    strokes = pl.text_strokes(["for someone who", "is leaving"])
    if a.demo_sender:
        return demo_sender(paper, strokes)

    m = pl.build_model(paper)
    d = mujoco.MjData(m)
    pen = pl.Pen(m)
    d.qpos[:pl.ARM] = pl.SEED
    mujoco.mj_forward(m, d)
    plan = planned_segments(paper, strokes)

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind(("127.0.0.1", PORT))
    sock.setblocking(False)
    child = subprocess.Popen([sys.executable, __file__, "--demo-sender"]) if a.demo else None

    done, last_tip, q_new, received = [], None, None, 0
    frames, t0 = 0, time.perf_counter()
    with mujoco.viewer.launch_passive(m, d) as v:
        v.cam.lookat[:] = [paper.center_x - 0.04, paper.center_y, 0.06]
        v.cam.distance, v.cam.azimuth, v.cam.elevation = 0.62, 205, -38
        while v.is_running() and not (a.seconds and time.perf_counter() - t0 > a.seconds):
            while True:                                  # drain; keep only the newest pose
                try:
                    data = sock.recv(64)
                except BlockingIOError:
                    break
                q_new = PACK.unpack(data)
                received += 1
            if q_new is not None:
                d.qpos[:pl.ARM] = q_new[:5]
                mujoco.mj_forward(m, d)
                tip = pen.tip(d)
                on_page = bool(q_new[5] and tip[2] < paper.z + ON_PAGE_M)
                if on_page and last_tip is not None:
                    done.append((last_tip, tip.copy()))
                last_tip = tip.copy() if on_page else None
                q_new = None
            with v.lock():
                scn = v.user_scn
                scn.ngeom = 0
                for p0, p1 in plan:
                    add_capsule(scn, p0, p1, PLAN_RGBA, PLAN_R)
                for p0, p1 in done[-4000:]:
                    add_capsule(scn, p0, p1, DONE_RGBA, DONE_R)
            v.sync()
            frames += 1
    dt = time.perf_counter() - t0
    if child:
        child.terminate()
    sock.close()
    print(f"[measured] loop {frames / dt:.1f} iterations/s over {dt:.1f} s, packets received "
          f"{received} ({received / dt:.1f}/s), completed segments {len(done)}, planned {len(plan)}")


if __name__ == "__main__":
    main()
