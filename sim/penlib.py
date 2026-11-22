"""Shared pen-writing core for the SO-101: model, IK, strokes, and joint trajectories.

Used by write_sim.py (MuJoCo render) and arm_tools.py (real arm). Units: metres and radians
inside, millimetres for strokes on the page.
"""
import math
import json
from dataclasses import dataclass, asdict
from pathlib import Path

import mujoco
import numpy as np

HERE = Path(__file__).parent
XML = HERE / "so101" / "scene.xml"
ARM_JOINTS = ["shoulder_pan", "shoulder_lift", "elbow_flex", "wrist_flex", "wrist_roll"]
ARM = len(ARM_JOINTS)

PEN_LENGTH = 0.03                       # pen tip sits this far past the gripper frame
HOVER, TRAVEL_Z = 0.008, 0.030          # metres above the page
DRAW_SPEED, TRAVEL_SPEED, Z_SPEED = 0.025, 0.08, 0.02   # m/s, from the dylanler rig
SEED = np.array([0.0, 0.3, 0.6, 1.2, 0.0])


@dataclass
class Paper:
    """Where the page is, in the arm's base frame. arm_tools.py corners overwrites this."""
    center_x: float = 0.20
    center_y: float = 0.0
    z: float = 0.001                    # page surface height
    half_x: float = 0.065               # 130 x 170 mm page
    half_y: float = 0.085
    press: float = 0.0                  # metres below the surface to aim the tip (real arm: ~0.002)

    @classmethod
    def load(cls, path):
        return cls(**json.loads(Path(path).read_text())) if path and Path(path).exists() else cls()

    def save(self, path):
        Path(path).write_text(json.dumps(asdict(self), indent=2))

    def to_world(self, uv_mm, lift):
        """Page mm (u right, v up, as read by someone facing the robot) -> base-frame xyz."""
        u, v = np.asarray(uv_mm) / 1000.0
        return np.array([self.center_x - v, self.center_y + u, self.z + lift])


def add_rig(spec):
    """The real table setup from defaults.json: the riser box with the card on it."""
    d = json.loads((HERE / "defaults.json").read_text())
    box, card = d["box"], d["card"]
    half = math.radians(box["yaw_deg"]) / 2
    quat = [math.cos(half), 0, 0, math.sin(half)]
    sx, sy, sz = [v / 1000 for v in box["size_mm"]]
    top = box["top_z_mm"] / 1000
    spec.worldbody.add_geom(name=box["name"], type=mujoco.mjtGeom.mjGEOM_BOX, size=[sx / 2, sy / 2, sz / 2],
                            pos=[box["center_mm"][0] / 1000, box["center_mm"][1] / 1000, top - sz / 2], quat=quat,
                            rgba=[0.66, 0.52, 0.36, 1], contype=0, conaffinity=0)
    spec.worldbody.add_geom(name="card", type=mujoco.mjtGeom.mjGEOM_BOX,
                            size=[card["size_mm"][0] / 2000, card["size_mm"][1] / 2000, 0.00025],
                            pos=[card["center_mm"][0] / 1000, card["center_mm"][1] / 1000, top + 0.00025], quat=quat,
                            rgba=[0.97, 0.96, 0.92, 1], contype=0, conaffinity=0)


def build_model(paper=None, offscreen=(1280, 720), rig=False):
    paper = paper or Paper()
    spec = mujoco.MjSpec.from_file(str(XML))
    spec.visual.global_.offwidth, spec.visual.global_.offheight = offscreen
    if rig:
        add_rig(spec)
    else:
        spec.worldbody.add_geom(
            type=mujoco.mjtGeom.mjGEOM_BOX,
            size=[paper.half_x, paper.half_y, max(paper.z, 1e-4) / 2],
            pos=[paper.center_x, paper.center_y, max(paper.z, 1e-4) / 2],
            rgba=[0.97, 0.96, 0.92, 1], contype=0, conaffinity=0,
        )
    return spec.compile()


class Pen:
    """Pen tip = gripperframe site + PEN_LENGTH along the site's approach (x) axis."""

    def __init__(self, m):
        sid = mujoco.mj_name2id(m, mujoco.mjtObj.mjOBJ_SITE, "gripperframe")
        self.body = m.site_bodyid[sid]
        r = np.zeros(9)
        mujoco.mju_quat2Mat(r, m.site_quat[sid])
        self.axis_local = r.reshape(3, 3)[:, 0]
        self.tip_local = m.site_pos[sid] + PEN_LENGTH * self.axis_local

    def tip(self, d):
        return d.xpos[self.body] + d.xmat[self.body].reshape(3, 3) @ self.tip_local

    def axis(self, d):
        return d.xmat[self.body].reshape(3, 3) @ self.axis_local


def fk_tip(m, d, pen, q):
    d.qpos[:ARM] = q
    mujoco.mj_kinematics(m, d)
    mujoco.mj_comPos(m, d)
    return pen.tip(d)


def solve_ik(m, d, pen, target, q0, iters=200, tol=1e-4):
    """Damped least squares: tip on target, pen pointing straight down."""
    q = q0.copy()
    down = np.array([0.0, 0.0, -1.0])
    jacp, jacr = np.zeros((3, m.nv)), np.zeros((3, m.nv))
    lo, hi = m.jnt_range[:ARM, 0], m.jnt_range[:ARM, 1]
    for _ in range(iters):
        tip = fk_tip(m, d, pen, q)
        e_pos = target - tip
        e_rot = np.cross(pen.axis(d), down)
        if np.linalg.norm(e_pos) < tol and np.linalg.norm(e_rot) < 1e-3:
            return q, True
        mujoco.mj_jac(m, d, jacp, jacr, tip, pen.body)
        J = np.vstack([jacp[:, :ARM], 0.3 * jacr[:, :ARM]])
        e = np.concatenate([e_pos, 0.3 * e_rot])
        dq = J.T @ np.linalg.solve(J @ J.T + 1e-4 * np.eye(6), e)
        q = np.clip(q + dq, lo, hi)
    return q, False


def text_strokes(lines, cap_height_mm=10.0, line_gap_mm=17.0):
    """Hershey single-stroke text -> polylines in page mm, centred on the page."""
    from pyhershey import shape_text
    scale = cap_height_mm / 21.0
    shaped = shape_text("\n".join(lines), "roman_simplex", advance_height=line_gap_mm / scale,
                        text_align="center")
    strokes = []
    for item in shaped:
        g, (px, py) = item["glyph"], item["pos"]
        for seg in g.segments:
            pts = np.array([(px + x, py + y) for x, y in seg]) * scale
            if len(pts) >= 2:
                strokes.append(pts)
    return center_strokes(strokes)


def center_strokes(strokes):
    allp = np.vstack(strokes)
    mid = (allp.min(0) + allp.max(0)) / 2
    return [s - mid for s in strokes]


def resample(poly, step_mm=1.0):
    out = [poly[0]]
    for a, b in zip(poly[:-1], poly[1:]):
        n = max(1, int(np.ceil(np.linalg.norm(b - a) / step_mm)))
        out += [a + (b - a) * k / n for k in range(1, n + 1)]
    return np.array(out)


def waypoints(strokes, paper):
    """(world xyz, speed m/s, inking) for every point the tip must visit."""
    wps = []
    for s in strokes:
        s = resample(s)
        wps.append((paper.to_world(s[0], TRAVEL_Z), TRAVEL_SPEED, False))
        wps.append((paper.to_world(s[0], HOVER), Z_SPEED, False))
        wps.append((paper.to_world(s[0], -paper.press), Z_SPEED, True))
        for p in s[1:]:
            wps.append((paper.to_world(p, -paper.press), DRAW_SPEED, True))
        wps.append((paper.to_world(s[-1], TRAVEL_Z), Z_SPEED, False))
    return wps


def check_page(strokes, paper, margin_mm=5.0):
    """Refuse strokes that would leave the page."""
    allp = np.vstack(strokes)
    lim_u = paper.half_y * 1000 - margin_mm
    lim_v = paper.half_x * 1000 - margin_mm
    return bool(np.all(np.abs(allp[:, 0]) <= lim_u) and np.all(np.abs(allp[:, 1]) <= lim_v))


def joint_trajectory(strokes, paper, rate_hz=50):
    """Kinematic plan: joint angles (rad) sampled at rate_hz, plus an inking flag per sample.

    Returns dict with t, q (N x 5), ink (N), ik_misses, min_tip_z.
    """
    m = build_model(paper)
    d = mujoco.MjData(m)
    pen = Pen(m)
    wps = waypoints(strokes, paper)
    q_prev, _ = solve_ik(m, d, pen, wps[0][0], SEED)
    pos_prev = wps[0][0]
    ts, qs, inks, misses, min_z = [0.0], [q_prev], [False], 0, np.inf
    t = 0.0
    for target, speed, ink in wps[1:]:
        q_new, ok = solve_ik(m, d, pen, target, q_prev)
        misses += (not ok)
        dur = np.linalg.norm(target - pos_prev) / speed
        n = max(1, int(np.ceil(dur * rate_hz)))
        for k in range(1, n + 1):
            t += 1.0 / rate_hz
            q = q_prev + (q_new - q_prev) * k / n
            ts.append(t); qs.append(q); inks.append(ink)
            min_z = min(min_z, fk_tip(m, d, pen, q)[2])
        q_prev, pos_prev = q_new, target
    return {"t": np.array(ts), "q": np.array(qs), "ink": np.array(inks),
            "ik_misses": misses, "min_tip_z": float(min_z), "rate_hz": rate_hz}


def save_trajectory(traj, path, meta=None):
    out = {"joints": ARM_JOINTS, "rate_hz": traj["rate_hz"], "units": "radians, arm-model frame",
           "ik_misses": traj["ik_misses"], "min_tip_z": traj["min_tip_z"],
           "t": traj["t"].round(4).tolist(), "q": traj["q"].round(5).tolist(),
           "ink": traj["ink"].astype(int).tolist(), "meta": meta or {}}
    Path(path).write_text(json.dumps(out))
