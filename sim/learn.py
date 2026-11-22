"""Pen feedback loop: draw in every direction, score the ink by camera, keep the lightest height that works.

Each trial draws four strokes (right, left, away from the arm, toward it) in the clear band under the red
line. The webcam diff scores ink along each commanded stroke. Every trial is appended to
private/calibration/learning.jsonl. Needs armd and camd.

  python learn.py [z_mm z_mm z_mm z_mm]
"""
import json
import sys
import time

import cv2
import numpy as np

import arm_worker as aw
import pose_go as pg

LOG = pg.ROOT / "private" / "calibration" / "learning.jsonl"
TOP = pg.ROOT / "state" / "cam" / "top.jpg"
CENTERS = (-42.0, -14.0, 14.0, 42.0)
HOVER, CLEAR, PASS = 95.0, 8.0, 0.8
CARD_MM = np.float32([[-63.5, 38], [63.5, 38], [63.5, -38], [-63.5, -38]])
CARD_PX = np.float32([[546.4, 493], [1190.4, 481], [1174.4, 703], [610.4, 701]])
MASK = np.array([[560, 510], [1175, 500], [1158, 690], [615, 692]], np.int32)
H = cv2.getPerspectiveTransform(CARD_MM, CARD_PX)


def pattern(c):
    return {"right": ((c - 9, 13.3), (c + 3, 13.3)), "left": ((c + 3, 18.3), (c - 9, 18.3)),
            "away": ((c + 6, 12.3), (c + 6, 19.3)), "toward": ((c + 10, 19.3), (c + 10, 12.3))}


def world(p, pt):
    u, v = pt
    return [p.cx + u * p.rs + v * p.rc, p.cy - (u * p.rc - v * p.rs)]


def plan(p, levels):
    first = world(p, pattern(CENTERS[0])["right"][0])
    hover, _ = aw.solve(p.pen, p.ref["home_seed"], [*first, HOVER])
    seed, rows, here = hover, [], [*first, HOVER]

    def go(poly, speed, accel):
        nonlocal seed
        path, seed, _ = aw.move(p.pen, seed, poly, speed, accel)
        rows.append(path)

    for c, z in zip(CENTERS, levels):
        for a, b in pattern(c).values():
            A, B = world(p, a), world(p, b)
            above = [*A, z + CLEAR]
            go([here, [*here[:2], max(here[2], z + CLEAR)], above], aw.TRAVEL, aw.A_TRAVEL)
            go([above, [*A, z]], aw.DESCEND, aw.A_DESCEND)
            go([[*A, z], [*B, z]], aw.DRAW, aw.A_DRAW)
            here = [*B, z]
    go([here, [*here[:2], here[2] + CLEAR], [*here[:2], HOVER]], aw.TRAVEL, aw.A_TRAVEL)
    return hover, [aw.as_q(r) for r in np.vstack(rows)]


def fresh_frame(after):
    end = time.time() + 5
    while time.time() < end and TOP.stat().st_mtime <= after:
        time.sleep(0.05)
    return cv2.imread(str(TOP))


def ink_map(before, after):
    mask = np.zeros(before.shape[:2], np.uint8)
    cv2.fillPoly(mask, [MASK], 255)
    a = cv2.GaussianBlur(cv2.cvtColor(before, cv2.COLOR_BGR2GRAY), (3, 3), 0).astype(float)
    b = cv2.GaussianBlur(cv2.cvtColor(after, cv2.COLOR_BGR2GRAY), (3, 3), 0).astype(float)
    b *= np.median(a[mask > 0]) / max(1.0, np.median(b[mask > 0]))
    new = (((a - b) > 28) & (mask > 0)).astype(np.uint8)
    return cv2.dilate(new, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (9, 7))) > 0


def covered(near, seg, offset=(0.0, 0.0)):
    (u0, v0), (u1, v1) = seg
    n = max(2, int(np.hypot(u1 - u0, v1 - v0) * 2))
    pts = np.column_stack([np.linspace(u0, u1, n) + offset[0], np.linspace(v0, v1, n) + offset[1]])
    px = cv2.perspectiveTransform(pts.reshape(-1, 1, 2).astype(np.float32), H).reshape(-1, 2).round().astype(int)
    return float(near[px[:, 1].clip(0, near.shape[0] - 1), px[:, 0].clip(0, near.shape[1] - 1)].mean())


def main():
    levels = [float(x) for x in sys.argv[1:]] or [47.0, 46.0, 45.0, 44.0]
    p = aw.Planner()
    hover, path = plan(p, levels)
    traj = pg.ROOT / "state" / "learn.traj.json"
    traj.write_text(json.dumps(path))
    before = cv2.imread(str(TOP))
    print(f"drawing {len(levels)} trials at z {levels} | {len(path) / pg.RATE:.0f} s", flush=True)
    try:
        aw.command({"c": "joints", "set": hover, "speed": 25})
        aw.wait_idle()
        t = time.monotonic()
        aw.command({"c": "traj", "path": str(traj), "phase": "learn"})
        aw.wait_idle()
        if time.monotonic() - t < len(path) / pg.RATE - 0.5:
            raise RuntimeError(f"armd halted: {aw.ask({'c': 'state'}).get('last_stop')}")
    finally:
        aw.return_to_l()
        traj.unlink(missing_ok=True)
    time.sleep(0.6)
    after = fresh_frame(time.time())
    near = ink_map(before, after)
    segs = [s for c in CENTERS[:len(levels)] for s in pattern(c).values()]
    grid = np.arange(-5, 5.01, 0.5)
    _, du, dv = max((sum(covered(near, s, (a, b)) for s in segs), a, b) for a in grid for b in grid)
    control = np.mean([covered(near, s, (du, dv - 24)) for s in segs])
    print(f"ink landed {du:+.1f} mm sideways, {dv:+.1f} mm toward the red line | control off the strokes: {control:.2f}")
    best = None
    with open(LOG, "a") as f:
        for c, z in zip(CENTERS, levels):
            score = {k: round(covered(near, s, (du, dv)), 2) for k, s in pattern(c).items()}
            print(f"  z {z:.1f}: " + "  ".join(f"{k} {v:.2f}" for k, v in score.items()))
            f.write(json.dumps({"t": time.strftime("%H:%M:%S"), "z": z, "score": score, "offset_mm": [du, dv], "control": round(float(control), 3)}) + "\n")
            if best is None and min(score.values()) >= PASS:
                best = z
    print("lightest height with every direction inked:", best)
    stamp = time.strftime("%H%M%S")
    cv2.imwrite(str(pg.ROOT / "private" / "webcam" / f"learn_{stamp}.jpg"), after)
    print("frame", f"private/webcam/learn_{stamp}.jpg")


if __name__ == "__main__":
    main()
