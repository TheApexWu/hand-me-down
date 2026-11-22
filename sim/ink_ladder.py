"""Ink ladder: short dashes from high to low. The first clean dash is the real pen-down height.

Plans a 50 Hz joint path for armd. Plans only; nothing here talks to the arm.

  python ink_ladder.py [--dx -18]
"""
import argparse
import json
from pathlib import Path

import numpy as np

import pose_go as pg

J = pg.JOINTS
LEVELS_MM = [10, 8, 6, 4, 2, 0, -2, -4]
HOVER_MM, CLEAR_MM, DASH_MM, GAP_MM = 60.0, 12.0, 6.0, 6.0
TRAVEL, DOWN, DRAW = 25.0, 8.0, 10.0                          # mm/s
OUT = pg.ROOT / "state" / "ink_ladder.traj.json"


def solve(pen, seed, p):
    q, err, _ = pen.ik({**seed, "wrist_roll": 0.0, "gripper": 0.0}, np.asarray(p) / 1000)
    out = {j: float(q[j]) for j in J}
    out["wrist_roll"] = 0.0
    miss = float(np.linalg.norm(pen.tip({**out, "gripper": 0.0}) * 1000 - np.asarray(p)))
    if err > 0.001 or miss > 0.5:
        raise SystemExit(f"REFUSED: IK misses {p} by {miss:.2f} mm")
    return out, miss


def segment(pen, seed, a, b, speed):
    a, b = np.asarray(a, float), np.asarray(b, float)
    dist = float(np.linalg.norm(b - a))
    keys, worst = [], 0.0
    for f in np.linspace(0, 1, max(2, int(dist / 2) + 2)):    # IK every ~2 mm keeps the laptop cool
        seed, miss = solve(pen, seed, a + (b - a) * f)
        keys.append(seed)
        worst = max(worst, miss)
    ticks = max(1, int(1.5 * dist / speed * pg.RATE))         # s-curve peaks at 1.5x its mean
    path = []
    for k in range(1, ticks + 1):
        t = k / ticks
        f = t * t * (3 - 2 * t) * (len(keys) - 1)
        i = min(int(f), len(keys) - 2)
        path.append({j: keys[i][j] + (keys[i + 1][j] - keys[i][j]) * (f - i) for j in J})
    return path, seed, worst


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dx", type=float, default=-18.0, help="offset from the blot toward the arm, mm")
    a = ap.parse_args()
    cal = json.load(open(Path(__file__).parent / "pen.json"))
    pen, ref = pg.pen_model(cal["pen_length_mm"])
    cx, cy = cal["dot_base_m"][0] * 1000 + a.dx, cal["dot_base_m"][1] * 1000
    ys = [cy + GAP_MM * (len(LEVELS_MM) - 1) / 2 - GAP_MM * i for i in range(len(LEVELS_MM))]

    route = [((cx - DASH_MM / 2, ys[0], HOVER_MM), TRAVEL)]
    for y, z in zip(ys, LEVELS_MM):
        route += [((cx - DASH_MM / 2, y, z + CLEAR_MM), TRAVEL), ((cx - DASH_MM / 2, y, z), DOWN),
                  ((cx + DASH_MM / 2, y, z), DRAW), ((cx + DASH_MM / 2, y, z + CLEAR_MM), DOWN)]
    route.append(((cx + DASH_MM / 2, ys[-1], HOVER_MM), TRAVEL))

    hover, _ = solve(pen, ref["home_seed"], route[0][0])
    seed, path, worst = hover, [], 0.0
    for (p0, _), (p1, speed) in zip(route, route[1:]):
        seg, seed, miss = segment(pen, seed, p0, p1, speed)
        path += seg
        worst = max(worst, miss)

    prev, step = hover, 0.0
    for q in path:
        step = max(step, max(abs(q[j] - prev[j]) for j in J))
        prev = q
    zs = [float(pen.tip({**q, "gripper": 0.0})[2]) * 1000 for q in path]
    bad = [j for j in J if j in pg.LIMITS_DEG and any(not pg.LIMITS_DEG[j][0] <= q[j] <= pg.LIMITS_DEG[j][1] for q in path)]
    long_pen, _ = pg.pen_model(pg_len := 68.0)
    OUT.write_text(json.dumps({"hover": hover, "path": path, "levels_mm": LEVELS_MM, "ys_mm": ys, "x_mm": cx}))
    print(f"PLAN ONLY, arm not moved | {len(path)} ticks, {len(path) / pg.RATE:.0f} s")
    print(f"dashes x {cx - DASH_MM / 2:.1f}..{cx + DASH_MM / 2:.1f}, y {ys[0]:.1f}..{ys[-1]:.1f} mm | levels {LEVELS_MM}")
    print(f"worst IK miss {worst:.3f} mm | max step {step:.3f} deg/tick (armd limit {1.2}) | z {min(zs):.1f}..{max(zs):.1f} mm")
    print("joint limit violations:", bad or "none")
    print(f"hover height if the pen is really {pg_len:.0f} mm: {float(long_pen.tip({**hover, 'gripper': 0.0})[2]) * 1000:.1f} mm")
    print("saved", OUT)


if __name__ == "__main__":
    main()
