"""SO-101 writes with a pen in MuJoCo and renders it to mp4 (physics on: the arm tracks the plan).

  python write_sim.py ["line one" "line two"]          Hershey font (leg 1)
  python write_sim.py --trace IMAGE [--height 30]      the image's own handwriting (leg 2)
  python write_sim.py --check                          reach test only
Options: --out NAME (files NAME_write.mp4 / NAME_page.png; default "pen"), --speedup N, --paper FILE
"""
import argparse
import subprocess
from pathlib import Path

import mujoco
import numpy as np

import penlib as pl

HERE = Path(__file__).parent
INK_TOL = 0.0008                        # tip within 0.8 mm of the page counts as ink
FPS = 30


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("lines", nargs="*")
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--trace")
    ap.add_argument("--height", type=float, default=30.0, help="traced ink height in mm")
    ap.add_argument("--out", default="pen")
    ap.add_argument("--speedup", type=float, default=4)
    ap.add_argument("--paper")
    a = ap.parse_args()

    paper = pl.Paper.load(a.paper)
    m = pl.build_model(paper)
    d, dk = mujoco.MjData(m), mujoco.MjData(m)
    pen = pl.Pen(m)

    for c in [(-80, -60), (80, -60), (-80, 60), (80, 60), (0, 0)]:
        target = paper.to_world(np.array(c), 0.0)
        q, ok = pl.solve_ik(m, dk, pen, target, pl.SEED)
        err = np.linalg.norm(pl.fk_tip(m, dk, pen, q) - target) * 1000
        print(f"reach {np.round(target, 3)} ok={ok} err={err:.2f} mm q={np.round(q, 2)}")
    if a.check:
        return

    if a.trace:
        from tracer import trace_image
        strokes, info = trace_image(a.trace, height_mm=a.height)
        print("trace:", info)
        if not info["ok"]:
            raise SystemExit("trace failed its quality gate; fall back to the font")
    else:
        strokes = pl.text_strokes(a.lines or ["for someone who", "is leaving"])
    if not pl.check_page(strokes, paper):
        raise SystemExit("strokes leave the page; shrink the text")

    wps = pl.waypoints(strokes, paper)
    q_prev, _ = pl.solve_ik(m, dk, pen, wps[0][0], pl.SEED)
    d.qpos[:pl.ARM] = d.ctrl[:pl.ARM] = q_prev
    mujoco.mj_forward(m, d)

    out_mp4, out_png = HERE / f"{a.out}_write.mp4", HERE / f"{a.out}_page.png"
    renderer = mujoco.Renderer(m, height=720, width=1280, max_geom=20000)
    cam = mujoco.MjvCamera()
    cam.lookat[:] = [paper.center_x - 0.04, paper.center_y, 0.06]
    cam.distance, cam.azimuth, cam.elevation = 0.62, 205, -38
    ff = subprocess.Popen(["ffmpeg", "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "rgb24",
                           "-s", "1280x720", "-r", str(FPS), "-i", "-", "-c:v", "libx264",
                           "-pix_fmt", "yuv420p", "-crf", "20", str(out_mp4)], stdin=subprocess.PIPE)

    strokes_done, ink_err, ik_fail = [[]], [], 0
    frame_every, t_next = a.speedup / FPS, 0.0

    def draw(cam_):
        renderer.update_scene(d, camera=cam_)
        scn = renderer.scene
        for stroke in strokes_done:
            for p0, p1 in zip(stroke[:-1:2], stroke[2::2]):
                if scn.ngeom >= scn.maxgeom:
                    break
                g = scn.geoms[scn.ngeom]
                mujoco.mjv_initGeom(g, mujoco.mjtGeom.mjGEOM_CAPSULE, np.zeros(3), np.zeros(3),
                                    np.eye(3).flatten(), np.array([0.08, 0.1, 0.35, 1], np.float32))
                mujoco.mjv_connector(g, mujoco.mjtGeom.mjGEOM_CAPSULE, 0.0005, p0, p1)
                scn.ngeom += 1
        return renderer.render()

    def step_to(q_from, q_to, n, inking):
        nonlocal t_next
        for k in range(1, n + 1):
            d.ctrl[:pl.ARM] = q_from + (q_to - q_from) * k / n
            mujoco.mj_step(m, d)
            tip = pen.tip(d)
            if inking and tip[2] < paper.z + INK_TOL:
                strokes_done[-1].append(tip.copy())
                ink_err.append(abs(tip[2] - paper.z) * 1000)
            elif strokes_done[-1]:
                strokes_done.append([])
            if d.time >= t_next:
                ff.stdin.write(draw(cam).tobytes())
                t_next += frame_every

    pos_prev = wps[0][0]
    for target, speed, inking in wps:
        q_new, ok = pl.solve_ik(m, dk, pen, target, q_prev)
        ik_fail += (not ok)
        n = max(1, int(round(np.linalg.norm(target - pos_prev) / speed / m.opt.timestep)))
        step_to(q_prev, q_new, n, inking)
        q_prev, pos_prev = q_new, target

    # Park clear of the page so the last shots see the whole sheet.
    step_to(q_prev, np.array([1.3, -1.2, 1.1, 1.0, 0.0]), int(2.0 / m.opt.timestep), False)
    top = mujoco.MjvCamera()
    top.lookat[:] = [paper.center_x, paper.center_y, 0]
    top.distance, top.azimuth, top.elevation = 0.30, 180, -90
    for _ in range(FPS * 2):
        ff.stdin.write(draw(cam).tobytes())
    page = draw(top)
    for _ in range(FPS * 3):
        ff.stdin.write(page.tobytes())
    ff.stdin.close(); ff.wait()
    from PIL import Image
    Image.fromarray(page).save(out_png)

    print(f"sim time {d.time:.1f} s, waypoints {len(wps)}, IK misses {ik_fail}")
    print(f"ink samples {sum(map(len, strokes_done))}, pen height error mean {np.mean(ink_err):.2f} mm "
          f"max {np.max(ink_err):.2f} mm")
    print(f"video {out_mp4} ({a.speedup:g}x speed), page {out_png}")


if __name__ == "__main__":
    main()
