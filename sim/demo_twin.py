"""SIMULATION of an SO-101 writing on an index card. Fallback demo; never touches the real arm.

  python demo_twin.py "line one" ["line two"]     play in a window (q quits)
  python demo_twin.py --record out.mp4 "line"     render offscreen to mp4
Options: --speed N (default 3), --job PATH (text from a job JSON's "line" field).

Joint angles come from the rig's IK (so101_sketch PenModel), played back kinematically.
"""
import argparse
import json
import math
import subprocess
import sys
import time
from pathlib import Path

import cv2
import mujoco
import numpy as np

HERE = Path(__file__).resolve().parent
VENDOR = HERE.parent / "vendor" / "so101-sketch"
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(VENDOR))
import penlib as pl                                            # noqa: E402
from pyhershey import shape_text                               # noqa: E402
from so101_sketch.kinematics import PenModel, fk               # noqa: E402

CARD_CX, CARD_CY = 0.225, -0.03         # metres, arm base frame
CARD_HALF_X, CARD_HALF_Y = 0.038, 0.0635
CARD_Z = 0.001
MARGIN_MM = 8.0
HEADER_V_MM = 24.0                      # red line, towards the far (+x) edge
RULE_MM = 11.0
CAP_MM = 7.5
PEN_MM = 68.0
PEN_BODY_M, PEN_RADIUS_M, NIB_M = 0.110, 0.0055, 0.012
GRIP_RAD = -0.13                        # measured: moving jaw meets the 5.5 mm pen radius here
PARK_DEG = np.array([-105.0, -90.0, 60.0, 60.0, 0.0])   # swung to +y so the card stays in view
PARK_DEG_S = 40.0
W, H, FPS = 1280, 720, 30
HOLD_ARM_S, HOLD_TOP_S = 1.5, 3.0
SETUP = VENDOR / "calibration" / "setups" / "history_artscience_taped_sharpie.json"
INK_RGBA = (0.04, 0.04, 0.09, 1)
PEN_RGBA = (0.05, 0.05, 0.06, 1)
# The scene's two lights saturate pale colours to white, so these are authored darker than they appear.
RULE_RGBA = [0.22, 0.42, 0.80, 1]
HEADER_RGBA = [0.75, 0.05, 0.08, 1]
WINDOW = "SIMULATION - SO-101 twin (not the real arm)"

BASELINES_MM = [HEADER_V_MM - RULE_MM * k for k in range(1, 5)]
USABLE_U_MM = 2 * (CARD_HALF_Y * 1000 - MARGIN_MM)


def card_centre():
    """A measured dot position beats the default constant when calibration has produced one."""
    path = HERE / "pen.json"
    if path.exists():
        dot = json.loads(path.read_text()).get("dot_base_m")
        if dot:
            return float(dot[0]), float(dot[1])
    return CARD_CX, CARD_CY


def glyphs(text, cap_mm):
    scale = cap_mm / 21.0
    out = []
    for item in shape_text(text, "roman_simplex", advance_height=30, text_align="left"):
        g, (px, py) = item["glyph"], item["pos"]
        out += [np.array([(px + x, py + y) for x, y in seg]) * scale for seg in g.segments if len(seg) >= 2]
    return out


def width(text, cap_mm):
    strokes = glyphs(text, cap_mm)
    if not strokes:
        return 0.0
    u = np.vstack(strokes)[:, 0]
    return float(u.max() - u.min())


def wrap(lines, cap_mm):
    out = []
    for line in lines:
        cur = ""
        for word in line.split():
            if width(word, cap_mm) > USABLE_U_MM:
                return None
            trial = f"{cur} {word}".strip()
            if width(trial, cap_mm) <= USABLE_U_MM:
                cur = trial
            else:
                out.append(cur)
                cur = word
        if cur:
            out.append(cur)
    return out


def layout(lines):
    """Page-mm strokes with baselines on the rules, shrinking only when the text won't fit."""
    for cap in np.arange(CAP_MM, 2.9, -0.25):
        wrapped = wrap(lines, cap)
        if wrapped and len(wrapped) <= len(BASELINES_MM):
            break
    else:
        sys.exit("text does not fit on the card")
    first = (len(BASELINES_MM) - len(wrapped)) // 2
    strokes = []
    for i, text in enumerate(wrapped):
        line = glyphs(text, cap)
        u = np.vstack(line)[:, 0]
        mid = (u.min() + u.max()) / 2
        strokes += [s - [mid, -BASELINES_MM[first + i]] for s in line]
    return strokes, wrapped, float(cap)


def to_world(uv_mm, z, centre):
    """Page u right, v up, read from the arm's base looking outward: +v is +x, +u is -y."""
    uv = np.atleast_2d(uv_mm) / 1000.0
    return np.column_stack([centre[0] + uv[:, 1], centre[1] - uv[:, 0], np.full(len(uv), z)])


class Plan:
    """Tip waypoints -> rig IK -> a timed joint trajectory in degrees."""

    def __init__(self, strokes, centre):
        ref = json.loads(SETUP.read_text())
        self.pen = PenModel(ref["pen_reference_q"], PEN_MM / 1000, math.radians(ref["pen_pitch_deg"]))
        seed = {**ref["home_seed"], "wrist_roll": 0.0, "gripper": 0.0}
        self.pts, self.speeds, self.inking = [self.pen.tip(seed)], [1.0], [False]
        hover = CARD_Z + pl.TRAVEL_Z
        for s in strokes:
            world = to_world(pl.resample(s, 1.0), CARD_Z, centre)
            self._go([world[0, 0], world[0, 1], hover], pl.TRAVEL_SPEED, 5.0)
            self._go(world[0], pl.Z_SPEED, 2.0)
            for p in world[1:]:
                self.pts.append(p); self.speeds.append(pl.DRAW_SPEED); self.inking.append(True)
            self._go([world[-1, 0], world[-1, 1], hover], pl.Z_SPEED, 2.0)
        last = self.pts[-1]
        # Higher than this the wrist limit costs position accuracy near the base.
        self._go([last[0], last[1], CARD_Z + 0.06], pl.Z_SPEED * 2, 5.0)
        self._solve(seed)

    def _go(self, target, speed, step_mm):
        a, b = self.pts[-1], np.asarray(target, float)
        n = max(1, int(np.ceil(np.linalg.norm(b - a) * 1000 / step_mm)))
        for k in range(1, n + 1):
            self.pts.append(a + (b - a) * k / n); self.speeds.append(speed); self.inking.append(False)

    def _solve(self, seed):
        q, qs, errs = dict(seed), [], []
        for p in self.pts:
            q, err, _ = self.pen.ik(q, p)
            q["wrist_roll"] = 0.0
            qs.append([q[j] for j in pl.ARM_JOINTS])
            errs.append(err)
        self.errs = np.array(errs) * 1000
        pts = np.array(self.pts)
        dt = np.linalg.norm(np.diff(pts, axis=0), axis=1) / np.array(self.speeds[1:])
        q_deg = np.array(qs)
        park_s = float(np.abs(PARK_DEG - q_deg[-1]).max() / PARK_DEG_S)
        self.t = np.concatenate([[0.0], np.cumsum(dt), [np.sum(dt) + park_s]])
        self.q = np.vstack([q_deg, PARK_DEG])
        self.ink_mask = np.array(self.inking + [False])
        ink_t = self.t[self.ink_mask]
        self.t_first, self.t_last = float(ink_t[0]), float(ink_t[-1])
        # A segment is inked when its end waypoint is; the point before it starts the stroke.
        ends = np.flatnonzero(self.ink_mask)
        tips = np.array([self.pen.tip(dict(zip(pl.ARM_JOINTS, r))) for r in self.q])
        self.seg_t = self.t[ends]
        self.seg_a, self.seg_b = tips[ends - 1], tips[ends]
        self.seg_a[:, 2] = self.seg_b[:, 2] = CARD_Z + 0.0003

    def joints(self, t):
        return np.array([np.interp(t, self.t, self.q[:, j]) for j in range(pl.ARM)])


def build_model(centre):
    spec = mujoco.MjSpec.from_file(str(pl.XML))
    spec.visual.global_.offwidth, spec.visual.global_.offheight = W, H
    floor = spec.geom("floor")
    floor.material, floor.rgba = "", [0.20, 0.22, 0.25, 1]

    def box(half, pos, rgba):
        spec.worldbody.add_geom(type=mujoco.mjtGeom.mjGEOM_BOX, size=half, pos=pos, rgba=rgba,
                                contype=0, conaffinity=0)
    cx, cy = centre
    box([CARD_HALF_X, CARD_HALF_Y, CARD_Z / 2], [cx, cy, CARD_Z / 2], [1, 1, 1, 1])
    line_z = CARD_Z + 0.00012
    for v in BASELINES_MM + [HEADER_V_MM - RULE_MM * 5]:
        box([0.00022, CARD_HALF_Y, 0.00004], [cx + v / 1000, cy, line_z], RULE_RGBA)
    box([0.00035, CARD_HALF_Y, 0.00004], [cx + HEADER_V_MM / 1000, cy, line_z], HEADER_RGBA)
    return spec.compile()


class Twin:
    def __init__(self, lines):
        self.centre = card_centre()
        strokes, self.wrapped, self.cap = layout(lines)
        self.plan = Plan(strokes, self.centre)
        self.m = build_model(self.centre)
        self.d = mujoco.MjData(self.m)
        self.ren = mujoco.Renderer(self.m, height=H, width=W, max_geom=len(self.plan.seg_t) + 800)
        # The arm's shadow would hide half the card, which is the thing the audience reads.
        self.ren.scene.flags[mujoco.mjtRndFlag.mjRND_SHADOW] = 0
        self.ren.scene.flags[mujoco.mjtRndFlag.mjRND_REFLECTION] = 0
        self.cam = mujoco.MjvCamera()
        self.cam.lookat[:] = [self.centre[0] - 0.035, self.centre[1] + 0.015, 0.06]
        self.cam.distance, self.cam.azimuth, self.cam.elevation = 0.56, 55, -48
        # Azimuth 0 from straight above puts +x at image-up, so the page reads upright.
        self.top = mujoco.MjvCamera()
        self.top.lookat[:] = [self.centre[0], self.centre[1] + 0.024, CARD_Z]
        self.top.distance, self.top.azimuth, self.top.elevation = 0.1375, 0, -90

    @staticmethod
    def _add(scn, kind, p0, p1, radius, rgba):
        g = scn.geoms[scn.ngeom]
        mujoco.mjv_initGeom(g, kind, np.zeros(3), np.zeros(3), np.eye(3).flatten(),
                            np.array(rgba, np.float32))
        mujoco.mjv_connector(g, kind, radius, p0, p1)
        scn.ngeom += 1

    def frame(self, t, cam):
        p = self.plan
        q = p.joints(t)
        self.d.qpos[:pl.ARM] = np.radians(q)
        self.d.qpos[pl.ARM] = GRIP_RAD
        mujoco.mj_forward(self.m, self.d)
        self.ren.update_scene(self.d, camera=cam)
        scn = self.ren.scene
        cap = mujoco.mjtGeom.mjGEOM_CAPSULE
        for a, b in zip(p.seg_a[:np.searchsorted(p.seg_t, t, "right")],
                        p.seg_b[:np.searchsorted(p.seg_t, t, "right")]):
            self._add(scn, cap, a, b, 0.00055, INK_RGBA)
        qd = dict(zip(pl.ARM_JOINTS, q))
        tip = p.pen.tip(qd)
        back = -(fk(qd)[:3, :3] @ p.pen.axis_local)
        self._add(scn, cap, tip + back * 0.001, tip + back * NIB_M, 0.001, PEN_RGBA)
        self._add(scn, cap, tip + back * (NIB_M + PEN_RADIUS_M), tip + back * PEN_BODY_M, PEN_RADIUS_M, PEN_RGBA)
        img = cv2.cvtColor(self.ren.render(), cv2.COLOR_RGB2BGR)
        self._overlay(img, q, tip, t)
        return img

    def _overlay(self, img, q, tip, t):
        p = self.plan
        written = float(np.clip(t - p.t_first, 0, p.t_last - p.t_first))
        rows = [f"{j:14s}{v:+7.1f} deg" for j, v in zip(pl.ARM_JOINTS, q)]
        rows += [f"pen height   {max(0.0, (tip[2] - CARD_Z) * 1000):5.1f} mm",
                 f"writing time {written:5.1f} s"]
        panel = img[10:262, 10:300].copy()
        img[10:262, 10:300] = (panel * 0.35).astype(np.uint8)
        cv2.rectangle(img, (10, 10), (300, 56), (30, 30, 210), -1)
        cv2.putText(img, "SIMULATION", (22, 46), cv2.FONT_HERSHEY_DUPLEX, 1.1, (255, 255, 255), 2, cv2.LINE_AA)
        cv2.putText(img, "MuJoCo, not the real arm", (20, 78), cv2.FONT_HERSHEY_SIMPLEX, 0.5,
                    (200, 220, 255), 1, cv2.LINE_AA)
        for i, text in enumerate(rows):
            cv2.putText(img, text, (20, 104 + 22 * i), cv2.FONT_HERSHEY_PLAIN, 1.05, (240, 240, 240), 1, cv2.LINE_AA)

    def clip(self, speed):
        """Yield every frame: writing and park, a hold on the arm, then the card from above."""
        end = float(self.plan.t[-1])
        for k in range(int(end / speed * FPS) + 1):
            yield self.frame(k * speed / FPS, self.cam)
        hold = self.frame(end, self.cam)
        for _ in range(int(HOLD_ARM_S * FPS)):
            yield hold
        top = self.frame(end, self.top)
        for _ in range(int(HOLD_TOP_S * FPS)):
            yield top

    def report(self):
        p = self.plan
        ink = p.ink_mask[:-1]
        edge_x = CARD_HALF_X - np.abs(p.seg_b[:, 0] - self.centre[0])
        edge_y = CARD_HALF_Y - np.abs(p.seg_b[:, 1] - self.centre[1])
        print(f"SIMULATION text {self.wrapped} cap {self.cap:.2f} mm, card centre {self.centre}, "
              f"ink-to-edge min {min(edge_x.min(), edge_y.min()) * 1000:.1f} mm")
        print(f"waypoints {len(p.errs)}, worst IK error {p.errs.max():.3f} mm (writing {p.errs[ink].max():.3f} mm)")
        print(f"real-time plan {p.t[-1]:.1f} s, writing {p.t_last - p.t_first:.1f} s, ink segments {len(p.seg_t)}")


def record(tw, path, speed):
    ff = subprocess.Popen(["ffmpeg", "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "bgr24",
                           "-s", f"{W}x{H}", "-r", str(FPS), "-i", "-", "-c:v", "libx264", "-preset", "veryfast",
                           "-threads", "2", "-pix_fmt", "yuv420p", "-crf", "20", str(path)],
                          stdin=subprocess.PIPE)
    n = 0
    for img in tw.clip(speed):
        ff.stdin.write(img.tobytes())
        n += 1
    ff.stdin.close()
    if ff.wait():
        sys.exit("ffmpeg failed")
    print(f"wrote {path}: {n} frames, {n / FPS:.1f} s at {speed:g}x")


def play(tw, speed):
    cv2.namedWindow(WINDOW, cv2.WINDOW_AUTOSIZE)
    next_t = time.perf_counter()
    img = None
    for img in tw.clip(speed):
        cv2.imshow(WINDOW, img)
        next_t += 1 / FPS
        if cv2.waitKey(max(1, int((next_t - time.perf_counter()) * 1000))) & 0xFF == ord("q"):
            break
    else:
        # The finished card stays up for the audience until the presenter closes it.
        while cv2.getWindowProperty(WINDOW, cv2.WND_PROP_VISIBLE) >= 1:
            if cv2.waitKey(50) & 0xFF == ord("q"):
                break
    cv2.destroyAllWindows()


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("lines", nargs="*")
    ap.add_argument("--record", metavar="PATH.mp4")
    ap.add_argument("--speed", type=float, default=3.0)
    ap.add_argument("--job", metavar="PATH")
    a = ap.parse_args()
    if a.job:
        field = json.loads(Path(a.job).read_text())["line"]
        lines = field if isinstance(field, list) else str(field).splitlines()
    else:
        lines = a.lines or ["for someone who", "is leaving"]
    tw = Twin(lines)
    tw.report()
    if a.record:
        record(tw, a.record, a.speed)
    else:
        play(tw, a.speed)


if __name__ == "__main__":
    main()
