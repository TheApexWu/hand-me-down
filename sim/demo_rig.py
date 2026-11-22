"""SIMULATION of the riser rig: replays the real 50 Hz pen plan offscreen. Never touches the arm.

  python -B demo_rig.py [out.mp4]
"""
import subprocess
import sys

import cv2
import mujoco
import numpy as np

import arm_worker as aw
import penlib as pl
import pose_go as pg

TEXT, WRITE_Z = "Lorem ipsum dolor sit amet", 46.0
OUT = pg.ROOT / "docs" / "hand-me-down-sim-demo.mp4"
W, H, FPS, SPEED = 1280, 720, 25, 3
TICKS_PER_FRAME = SPEED * pg.RATE // FPS
RETURN_DEG_S = 30.0                     # arm_worker.return_to_l asks armd for this speed
FADE_S, HOLD_S = 0.6, 4.0
GRIP_RAD = -0.13                        # moving jaw closed onto the pen
PEN_M, PEN_R, NIB_M, NIB_R, INK_R = 0.085, 0.0055, 0.012, 0.001, 0.0006
INK_RGBA = (0.03, 0.03, 0.07, 1)
PEN_RGBA = (0.05, 0.05, 0.06, 1)
RED_RGBA = (0.75, 0.05, 0.08, 1)
MAIN_CAM = dict(lookat=(0.125, 0.02, 0.125), distance=0.86, azimuth=48.0, elevation=-38.0, fovy=28.0)


def return_path(planner, end):
    """The legs and s-curve armd uses for `go l`, so the arm clears the card the way it really does."""
    start, goal = aw.as_q(end), planner.l_pose
    for legs in (pg.plan(start, goal), [(n, e, s) for n, s, e in reversed(pg.plan(goal, start))]):
        if pg.check(legs, planner.pen68, aw.FLOOR_MM / 1000)[1]:
            break
    else:
        sys.exit("no clear route back to L")
    rows = []
    for _, s, e in legs:
        a, b = np.array([s[j] for j in pg.JOINTS]), np.array([e[j] for j in pg.JOINTS])
        n = max(1, int(1.5 * np.abs(b - a).max() / RETURN_DEG_S * pg.RATE))
        f = np.arange(1, n + 1) / n
        rows.append(a + np.outer(f * f * (3 - 2 * f), b - a))
    return np.vstack(rows)


class Rig:
    def __init__(self, plan, back_to_l):
        self.q = np.vstack([plan.q, back_to_l])
        self.n_plan, self.ink, self.tips = len(plan.q), np.asarray(plan.ink, bool), plan.tips / 1000
        self.m = pl.build_model(offscreen=(W, H), rig=True)
        self.d = mujoco.MjData(self.m)
        self.body = self.m.body("gripper").id
        floor = self.m.geom("floor").id
        self.m.geom_matid[floor], self.m.geom_rgba[floor] = -1, (0.20, 0.22, 0.25, 1)
        card = self.m.geom("card")
        self.card_top = float(card.pos[2] + card.size[2])
        self.card_c = card.pos.copy()
        r = np.zeros(9)
        mujoco.mju_quat2Mat(r, card.quat)
        self.card_r, self.card_half_u = r.reshape(3, 3), float(card.size[1])
        self.card_yaw = float(np.degrees(np.arctan2(self.card_r[1, 0], self.card_r[0, 0])))
        self.tip_local = self._fit_tip()
        self._ink_segments()
        self.ren = mujoco.Renderer(self.m, height=H, width=W, max_geom=len(self.seg_tick) + 600)
        # The arm's shadow would cover the card, which is what the viewer needs to read.
        self.ren.scene.flags[mujoco.mjtRndFlag.mjRND_SHADOW] = 0
        self.ren.scene.flags[mujoco.mjtRndFlag.mjRND_REFLECTION] = 0
        self.ren.scene.flags[mujoco.mjtRndFlag.mjRND_SKYBOX] = 0
        self.max_xy = self.max_3d = 0.0

    def pose(self, k):
        self.d.qpos[:pl.ARM] = np.radians(self.q[k])
        self.d.qpos[pl.ARM] = GRIP_RAD
        mujoco.mj_kinematics(self.m, self.d)

    def _fit_tip(self):
        """The rig's tool frame is a rigid point on MuJoCo's gripper body, about 19.9 mm from its site."""
        locals_ = []
        for k in np.flatnonzero(self.ink):
            self.pose(k)
            r = self.d.xmat[self.body].reshape(3, 3)
            locals_.append(r.T @ (self.tips[k] - self.d.xpos[self.body]))
        return np.mean(locals_, axis=0)

    def _ink_segments(self):
        down = np.flatnonzero(self.ink)
        a, b, tick = [], [], []
        for run in np.split(down, np.flatnonzero(np.diff(down) > 1) + 1):
            idx = np.r_[run[:-1][::2], run[-1]]
            a.append(self.tips[idx[:-1]])
            b.append(self.tips[idx[1:]])
            tick.append(idx[1:])
        self.seg_a, self.seg_b, self.seg_tick = np.vstack(a), np.vstack(b), np.concatenate(tick)
        self.seg_a[:, 2] = self.seg_b[:, 2] = self.card_top + 0.0002

    def pen_line(self):
        """Tip resting on the card (the plan presses below its surface), held rigidly by the gripper."""
        r, p = self.d.xmat[self.body].reshape(3, 3), self.d.xpos[self.body]
        tip = p + r @ self.tip_local
        tip[2] = max(tip[2], self.card_top)
        grip = p + r @ (self.tip_local + np.array([0, 0, PEN_M]))
        back = (grip - tip) / np.linalg.norm(grip - tip)
        return tip, back, grip

    @staticmethod
    def _capsule(scn, p0, p1, radius, rgba):
        g = scn.geoms[scn.ngeom]
        mujoco.mjv_initGeom(g, mujoco.mjtGeom.mjGEOM_CAPSULE, np.zeros(3), np.zeros(3), np.eye(3).flatten(),
                            np.array(rgba, np.float32))
        mujoco.mjv_connector(g, mujoco.mjtGeom.mjGEOM_CAPSULE, radius, np.asarray(p0, float), np.asarray(p1, float))
        scn.ngeom += 1
        return g

    def render(self, k, cam, speed_label):
        self.pose(k)
        self.ren.update_scene(self.d, camera=cam)
        scn = self.ren.scene
        red = self.card_c + self.card_r @ np.array([aw.RED_LINE_V / 1000, 0, 0])
        half = self.card_r @ np.array([0, self.card_half_u - 0.0005, 0])
        red[2] = self.card_top + 0.00005
        self._capsule(scn, red - half, red + half, 0.0004, RED_RGBA)
        for a, b in zip(self.seg_a[:np.searchsorted(self.seg_tick, k, "right")],
                        self.seg_b[:np.searchsorted(self.seg_tick, k, "right")]):
            self._capsule(scn, a, b, INK_R, INK_RGBA)
        tip, back, grip = self.pen_line()
        nib = self._capsule(scn, tip + back * NIB_R, tip + back * NIB_M, NIB_R, PEN_RGBA)
        self._capsule(scn, tip + back * (NIB_M + PEN_R), grip, PEN_R, PEN_RGBA)
        if k < self.n_plan and self.ink[k]:
            axis = np.array(nib.mat).reshape(3, 3)[:, 2]
            ends = [np.array(nib.pos) + s * axis * (nib.size[2] + NIB_R) for s in (-1, 1)]
            drawn = min(ends, key=lambda e: e[2])
            gap = drawn - self.tips[k]
            self.max_xy = max(self.max_xy, float(np.linalg.norm(gap[:2])))
            self.max_3d = max(self.max_3d, float(np.linalg.norm(gap)))
        img = cv2.cvtColor(self.ren.render(), cv2.COLOR_RGB2BGR)
        cv2.rectangle(img, (18, 18), (252, 66), (30, 30, 200), -1)
        cv2.putText(img, "SIMULATION", (30, 54), cv2.FONT_HERSHEY_DUPLEX, 0.95, (255, 255, 255), 2, cv2.LINE_AA)
        if speed_label:
            cv2.putText(img, f"{SPEED}x", (W - 72, 54), cv2.FONT_HERSHEY_DUPLEX, 0.85, (255, 255, 255), 2, cv2.LINE_AA)
        return img

    def camera(self, above=False):
        """The working view, or straight above with the red line at the top. Above sits under the parked forearm."""
        c = dict(lookat=(self.card_c[0], self.card_c[1], self.card_top), distance=0.13, azimuth=self.card_yaw,
                 elevation=-89.9, fovy=45.0) if above else MAIN_CAM
        self.m.vis.global_.fovy = c["fovy"]
        cam = mujoco.MjvCamera()
        cam.lookat[:], cam.distance, cam.azimuth, cam.elevation = c["lookat"], c["distance"], c["azimuth"], c["elevation"]
        return cam

    def frames(self):
        last = len(self.q) - 1
        img = None
        for k in [*range(0, last, TICKS_PER_FRAME), last]:
            img = self.render(k, self.camera(), True)
            yield img
        final = self.render(last, self.camera(above=True), False)
        n = int(FADE_S * FPS)
        for i in range(1, n + 1):
            yield cv2.addWeighted(img, 1 - i / n, final, i / n, 0)
        for _ in range(int(HOLD_S * FPS)):
            yield final

    def tip_error(self):
        """Pen-down gap to plan.tips for every tick; MuJoCo's own site is the control that should miss."""
        sid = self.m.site("gripperframe").id
        xy, xyz, site = [], [], []
        for k in np.flatnonzero(self.ink):
            self.pose(k)
            gap = self.pen_line()[0] - self.tips[k]
            xy.append(np.linalg.norm(gap[:2]))
            xyz.append(np.linalg.norm(gap))
            site.append(np.linalg.norm(self.d.site_xpos[sid][:2] - self.tips[k][:2]))
        return max(xy) * 1000, max(xyz) * 1000, min(site) * 1000, max(site) * 1000


def main():
    out = sys.argv[1] if len(sys.argv) > 1 else str(OUT)
    planner = aw.Planner()
    plan = planner.plan(TEXT, WRITE_Z)
    rig = Rig(plan, return_path(planner, plan.q[-1]))
    ff = subprocess.Popen(["ffmpeg", "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "bgr24", "-s", f"{W}x{H}",
                           "-r", str(FPS), "-i", "-", "-c:v", "libx264", "-preset", "veryfast", "-crf", "21",
                           "-threads", "2", "-pix_fmt", "yuv420p", "-movflags", "+faststart", out],
                          stdin=subprocess.PIPE)
    count = 0
    for img in rig.frames():
        ff.stdin.write(img.tobytes())
        count += 1
    ff.stdin.close()
    if ff.wait():
        sys.exit("ffmpeg failed")
    print(f"wrote {out}: {count} frames, {count / FPS:.1f} s, plan {rig.n_plan} ticks at {SPEED}x")
    print(f"drawn tip vs plan.tips, rendered pen-down frames: xy max {rig.max_xy * 1000:.4f} mm, "
          f"3D max {rig.max_3d * 1000:.3f} mm")
    xy, xyz, site_lo, site_hi = rig.tip_error()
    print(f"every pen-down tick: xy max {xy:.4f} mm, 3D max {xyz:.3f} mm (card surface vs commanded {WRITE_Z} mm); "
          f"control, MuJoCo site xy gap {site_lo:.2f}..{site_hi:.2f} mm")


if __name__ == "__main__":
    main()
