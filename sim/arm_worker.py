"""Arm side of the card queue: jobs/NNN.json -> Hershey pen path -> (with --run) armd.

  python -B arm_worker.py               dry: plan open jobs, print checks, move nothing
  python -B arm_worker.py --run         write on the real arm; needs armd and state/ink_contact.json

Options: --root DIR, --once, --press MM, --poll S, --dry-done (dry only: write done/ for pipeline tests).
"""
import argparse
import fcntl
import json
import os
import signal
import socket
import subprocess
import sys
import time
import unicodedata
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from pyhershey import shape_text

import ink_ladder as il
import pose_go as pg

J = pg.JOINTS
SOCK = pg.ROOT / "state" / "armd.sock"
CONTACT = pg.ROOT / "state" / "ink_contact.json"

CARD_U, CARD_V = 127.0, 76.0
MARGIN = 8.0
FIT_MARGIN = 8.2                     # FK lands a few hundredths off the layout; 8.0 must survive that
RED_LINE_V = 24.0                    # same header position as demo_twin
TEXT_V = (-(CARD_V / 2 - FIT_MARGIN), RED_LINE_V - 2.0)
CAP_MAX, CAP_MIN, CAP_STEP = 18.0, 6.0, 0.25
MAX_LINES, PITCH = 3, 1.45
MAX_QUOTE_WORDS, MAX_QUOTE_CHARS = 3, 24
FONT_CAP = 21.0

HOVER_Z, LIFT = 95.0, 8.0
DRAW, TRAVEL, DESCEND = 24.0, 75.0, 25.0          # mm/s
A_DRAW, A_TRAVEL, A_DESCEND = 270.0, 700.0, 300.0 # mm/s^2
IK_STEP, CORNER_COS = 2.0, np.cos(np.radians(30))
PLAN_STEP, ARMD_STEP = 1.0, 1.2                   # deg/tick: plan with slack under armd's refusal
PLACEHOLDER_FIRST_INK = 2.0
MAX_PRESS = 4.0

# Must match armd.py: its go/joints clearance test uses this pen, floor and lift-off posture.
ARMD_PEN_MM, FLOOR_MM = 68.0, 35.0
LIFT_OFF_DEG = 25.0

TYPOGRAPHY = {"‘": "'", "’": "'", "“": '"', "”": '"', "–": "-", "—": "-",
              "…": "...", " ": " ", "~": "-"}


class Refused(Exception):
    pass


def log(*a):
    print(*a, flush=True)


def clean(text):
    for a, b in TYPOGRAPHY.items():
        text = text.replace(a, b)
    text = "".join(c for c in unicodedata.normalize("NFKD", text) if not unicodedata.combining(c))
    text = " ".join(text.split())
    bad = sorted({c for c in text if c != " " and not 32 < ord(c) < 126})
    if bad:
        raise Refused(f"no pen glyph for {bad}")
    if not text:
        raise Refused("empty line")
    return text


def job_text(job):
    """What the pen writes: the chosen 2-3 word quote, or the whole line for a house or typed test job."""
    quote = job.get("quote")
    if quote and not job.get("house") and not job.get("test"):
        text = clean(str(quote))
        if len(text.split()) > MAX_QUOTE_WORDS or len(text) > MAX_QUOTE_CHARS:
            raise Refused(f"quote must be at most {MAX_QUOTE_WORDS} words and {MAX_QUOTE_CHARS} characters")
        return text
    line = job.get("line")
    if not isinstance(line, str):
        raise Refused("job has no text line")
    return clean(line)


def glyphs(text, cap):
    k = cap / FONT_CAP
    out = []
    for item in shape_text(text, "roman_simplex", advance_height=30, text_align="left"):
        g, (px, py) = item["glyph"], item["pos"]
        strokes = [np.array([(px + x, py + y) for x, y in seg], float) * k for seg in g.segments if len(seg) >= 2]
        out += [s for s in strokes if np.ptp(s, axis=0).max() > 0]     # a zero-length stroke has no time profile
    return out


def ink_width(text, cap):
    u = np.vstack(glyphs(text, cap))[:, 0]
    return float(u.max() - u.min())


def wrap(words, cap, width):
    lines, cur = [], ""
    for w in words:
        if ink_width(w, cap) > width:
            return None
        trial = f"{cur} {w}".strip()
        if ink_width(trial, cap) <= width:
            cur = trial
        else:
            lines.append(cur)
            cur = w
    lines.append(cur)
    return lines if len(lines) <= MAX_LINES else None


def layout(text):
    """Largest cap height that fits, as card-centred (u, v) strokes with +v toward the red line."""
    words, width = text.split(), CARD_U - 2 * FIT_MARGIN
    for cap in np.arange(CAP_MAX, CAP_MIN - 1e-9, -CAP_STEP):
        cap = float(cap)
        lines = wrap(words, cap, width)
        if not lines:
            continue
        pitch = cap * PITCH
        top = (TEXT_V[0] + TEXT_V[1] + cap + pitch * (len(lines) - 1)) / 2
        strokes = []
        for i, line in enumerate(lines):
            row = glyphs(line, cap)
            u = np.vstack(row)[:, 0]
            strokes += [s + [-(u.min() + u.max()) / 2, top - cap - i * pitch] for s in row]
        pts = np.vstack(strokes)
        if pts[:, 1].min() >= TEXT_V[0] and pts[:, 1].max() <= TEXT_V[1]:     # width alone can pick a block too tall
            return strokes, lines, cap
    raise Refused(f"does not fit in {MAX_LINES} lines at {CAP_MIN:.0f} mm cap height ({len(text)} chars)")


def join(strokes, cap):
    """Keep the pen down through a letter: a stroke that starts on the line just drawn retraces to it."""
    tol, out, cur = max(0.6, 0.06 * cap), [], None
    for s in strokes:
        if cur is None:
            cur = s.copy()
            continue
        a, d = cur[:-1], np.diff(cur, axis=0)
        t = np.clip(((s[0] - a) * d).sum(1) / np.maximum((d * d).sum(1), 1e-12), 0, 1)
        near = a + t[:, None] * d
        gaps = np.linalg.norm(near - s[0], axis=1)
        i = int(np.argmin(gaps))
        if gaps[i] <= tol:
            cur = np.vstack([cur, cur[i + 1:-1][::-1], near[i], s])
        else:
            out.append(cur)
            cur = s.copy()
    out.append(cur)
    return out


def split_sharp(poly):
    """Stop at reversals and hard corners with the pen down; carrying speed through them shakes the arm."""
    poly = poly[np.r_[True, np.linalg.norm(np.diff(poly, axis=0), axis=1) > 1e-6]]
    if len(poly) < 3:
        return [poly]
    u = np.diff(poly, axis=0)
    u /= np.linalg.norm(u, axis=1)[:, None]
    cuts = np.flatnonzero((u[:-1] * u[1:]).sum(1) < 0.25) + 1
    edges = [0, *cuts.tolist(), len(poly) - 1]
    return [poly[a:b + 1] for a, b in zip(edges, edges[1:])]


def knots(poly):
    """About 2 mm IK spacing, but every sharp corner stays a key so letters keep their points."""
    poly = poly[np.r_[True, np.linalg.norm(np.diff(poly, axis=0), axis=1) > 1e-6]]
    seg = np.diff(poly, axis=0)
    lengths = np.linalg.norm(seg, axis=1)
    unit = seg / lengths[:, None]
    sharp = np.flatnonzero((unit[:-1] * unit[1:]).sum(1) < CORNER_COS) + 1
    corners = [0, *sharp.tolist(), len(poly) - 1]
    s = np.r_[0, np.cumsum(lengths)]
    at = []
    for a, b in zip(corners, corners[1:]):
        at += np.linspace(s[a], s[b], max(1, int(np.ceil((s[b] - s[a]) / IK_STEP))), endpoint=False).tolist()
    at.append(s[-1])
    return np.column_stack([np.interp(at, s, poly[:, k]) for k in range(poly.shape[1])])


def progress(n, dist, speed, accel):
    """Arc length at each of n ticks: smoothstep velocity ramps whose peak acceleration is accel."""
    v = min(speed, float(np.sqrt(dist * accel / 1.5)))
    ramp = 1.5 * v / accel
    total = 2 * ramp + (dist - v * ramp) / v
    n = n or max(1, int(np.ceil(total * pg.RATE)))
    t = np.arange(1, n + 1) / n * total
    edge = lambda tau: v * ramp * (tau ** 3 - tau ** 4 / 2)
    s = v * ramp / 2 + v * (t - ramp)
    s = np.where(t < ramp, edge(t / ramp), s)
    return np.where(t > total - ramp, dist - edge(np.clip((total - t) / ramp, 0, 1)), s), n


def solve(pen, seed, p):
    try:
        return il.solve(pen, seed, p)
    except SystemExit as e:                                   # il.solve exits; one bad job must not kill the worker
        raise Refused(str(e))


def move(pen, seed, poly, speed, accel):
    """One smooth move through a polyline: no stop at interior vertices."""
    pts = knots(np.asarray(poly, float))
    keys, worst = [], 0.0
    for p in pts:
        seed, miss = solve(pen, seed, p)
        keys.append([seed[j] for j in J])
        worst = max(worst, miss)
    keys = np.array(keys)
    arc = np.r_[0, np.cumsum(np.linalg.norm(np.diff(pts, axis=0), axis=1))]
    s, n = progress(0, float(arc[-1]), speed, accel)
    for _ in range(3):
        path = np.column_stack([np.interp(s, arc, keys[:, k]) for k in range(len(J))])
        step = float(np.abs(np.diff(np.vstack([keys[0], path]), axis=0)).max())
        if step <= PLAN_STEP:
            break
        s, n = progress(int(np.ceil(n * step / PLAN_STEP)) + 1, float(arc[-1]), speed, accel)
    return path, seed, worst


def as_q(row):
    return {j: float(v) for j, v in zip(J, row)}


def tip(pen, q):
    return pen.tip({**q, "gripper": 0.0}) * 1000


def route_ok(pen68, start, goal):
    """armd's own clearance test, so a return it would refuse is caught before the pen goes down."""
    z = lambda q: float(tip(pen68, q)[2])
    if z(start) < FLOOR_MM:
        off = {**start, "shoulder_lift": start["shoulder_lift"] - LIFT_OFF_DEG}
        zs = [z({j: start[j] + (off[j] - start[j]) * i / 40 for j in J}) for i in range(41)]
        if min(zs) < zs[0] - 1 or zs[-1] < FLOOR_MM:
            return False
        start = off
    forward = pg.plan(start, goal)
    backward = [(n, e, s) for n, s, e in reversed(pg.plan(goal, start))]
    return any(pg.check(legs, pen68, FLOOR_MM / 1000)[1] for legs in (forward, backward))


def route_seconds(start, goal, speed):
    return sum(1.5 * max(abs(e[j] - s[j]) for j in J) / speed for _, s, e in pg.plan(start, goal))


@dataclass
class Plan:
    lines: list
    cap: float
    z: float
    hover: dict
    q: np.ndarray
    ink: np.ndarray
    tips: np.ndarray
    worst_ik: float
    max_step: float
    limit_hits: int
    edges: dict
    est_s: float


class Planner:
    def __init__(self):
        cal = json.loads((pg.ROOT / "sim" / "pen.json").read_text())
        self.pen, self.ref = pg.pen_model(cal["pen_length_mm"])
        self.pen68, _ = pg.pen_model(ARMD_PEN_MM)
        self.cx, self.cy = cal["dot_base_m"][0] * 1000, cal["dot_base_m"][1] * 1000
        rot = np.radians(cal.get("card_rotation_deg", 0.0))   # the card is never perfectly square to the arm
        self.rc, self.rs = float(np.cos(rot)), float(np.sin(rot))
        self.l_pose = {j: float(v) for j, v in json.loads(pg.POSES.read_text())["l"]["reached_deg"].items()}

    def plan(self, text, z):
        strokes, lines, cap = layout(clean(text))
        strokes = join(strokes, cap)
        world = [np.column_stack([self.cx + s[:, 0] * self.rs + s[:, 1] * self.rc,
                                  self.cy - (s[:, 0] * self.rc - s[:, 1] * self.rs)]) for s in strokes]
        hover_pt = [*world[0][0], HOVER_Z]
        hover, worst = solve(self.pen, self.ref["home_seed"], hover_pt)
        seed, chunks, inks = hover, [], []

        def go(poly, speed, accel, ink=False):
            nonlocal seed, worst
            path, seed, miss = move(self.pen, seed, poly, speed, accel)
            worst = max(worst, miss)
            chunks.append(path)
            inks.append(np.full(len(path), ink))

        here = None
        for xy in world:
            above = [*xy[0], z + LIFT]
            go([hover_pt, above] if here is None else [here, [*here[:2], z + LIFT], above], TRAVEL, A_TRAVEL)
            go([above, [*xy[0], z]], DESCEND, A_DESCEND)
            for piece in split_sharp(xy):
                go(np.column_stack([piece, np.full(len(piece), z)]), DRAW, A_DRAW, ink=True)
            here = [*xy[-1], z]
        go([here, [*here[:2], z + LIFT], [*here[:2], HOVER_Z]], TRAVEL, A_TRAVEL)
        return self.check(np.vstack(chunks), np.concatenate(inks), hover, lines, cap, z, worst)

    def check(self, q, ink, hover, lines, cap, z, worst):
        start = np.array([hover[j] for j in J])
        max_step = float(np.abs(np.diff(np.vstack([start, q]), axis=0)).max())
        limit_hits = 0
        for k, j in enumerate(J):
            if j in pg.LIMITS_DEG:
                lo, hi = pg.LIMITS_DEG[j]
                limit_hits += int(((q[:, k] < lo) | (q[:, k] > hi + 0.01)).sum())
        tips = np.array([tip(self.pen, as_q(r)) for r in q])
        u, v = self.cy - tips[ink, 1], tips[ink, 0] - self.cx
        u, v = u * self.rc + v * self.rs, v * self.rc - u * self.rs
        edges = {"left": float(u.min() + CARD_U / 2), "right": float(CARD_U / 2 - u.max()),
                 "top_red_edge": float(CARD_V / 2 - v.max()), "bottom_arm_edge": float(v.min() + CARD_V / 2)}
        if max_step > ARMD_STEP:
            raise Refused(f"step {max_step:.3f} deg/tick over armd's {ARMD_STEP}")
        if limit_hits:
            raise Refused(f"{limit_hits} ticks outside joint limits")
        if min(edges.values()) < MARGIN:
            raise Refused(f"pen-down path within {min(edges.values()):.2f} mm of a card edge")
        samples = np.linspace(0, len(q) - 1, 60).astype(int)
        if not route_ok(self.pen68, self.l_pose, hover) or not all(route_ok(self.pen68, as_q(q[i]), self.l_pose) for i in samples):
            raise Refused("armd would refuse the route to hover or the return to L")
        est = len(q) / pg.RATE + route_seconds(self.l_pose, hover, 25.0) + route_seconds(as_q(q[-1]), self.l_pose, 30.0)
        return Plan(lines, cap, z, hover, q, ink, tips, worst, max_step, limit_hits, edges, est)


def pen_height(press, required):
    if not CONTACT.exists():
        if required:
            sys.exit(f"REFUSED: {CONTACT} is missing; run the ink ladder first")
        z = PLACEHOLDER_FIRST_INK - press
        log(f"PLACEHOLDER pen height: write z {z:.2f} mm (first ink {PLACEHOLDER_FIRST_INK} is not measured)")
        return z
    first = float(json.loads(CONTACT.read_text())["first_ink_z_mm"])
    if not 30 <= first <= 70:
        sys.exit(f"REFUSED: first_ink_z_mm {first} is outside the ink ladder's range")
    return first - press


def ask(m):
    s = socket.socket(socket.AF_UNIX)
    s.settimeout(5)
    try:
        s.connect(str(SOCK))
        s.sendall((json.dumps(m) + "\n").encode())
        buf = b""
        while not buf.endswith(b"\n"):
            chunk = s.recv(65536)
            if not chunk:
                raise RuntimeError("armd closed the connection")
            buf += chunk
    finally:
        s.close()
    return json.loads(buf)


def command(m):
    r = ask(m)
    if not r.get("ok"):
        raise RuntimeError(f"armd refused {m['c']}: {r.get('err')}")


def wait_idle():
    while ask({"c": "state"})["moving"]:
        time.sleep(0.05)
    time.sleep(0.25)                                          # the queue empties before the motors settle


def return_to_l():
    try:
        ask({"c": "stop"})
        command({"c": "go", "name": "l", "speed": 30})
        wait_idle()
        return True
    except Exception as e:
        log(f"ARM NOT BACK AT L ({type(e).__name__}: {e}). The pen may be on the card: check ./arm state")
        return False


def write_done(root, name, seconds):
    folder = root / "done"
    folder.mkdir(exist_ok=True)
    tmp = folder / f".{name}.tmp"
    tmp.write_text(json.dumps({"delivered_at": time.strftime("%H:%M"), "duration_s": int(round(seconds))}))
    os.replace(tmp, folder / name)


def run_plan(root, job_path, plan):
    """Pen path through armd. Every exit, including an error or Ctrl-C, stops and returns to L."""
    pts = [as_q(r) for r in plan.q]
    traj = pg.ROOT / "state" / f"worker_{job_path.stem}.traj.json"
    t0 = time.monotonic()
    finished = False
    try:
        traj.write_text(json.dumps(pts))
        command({"c": "joints", "set": plan.hover, "speed": 25})
        wait_idle()
        t1 = time.monotonic()
        command({"c": "traj", "path": str(traj), "phase": f"write:{job_path.stem}"})
        wait_idle()
        # armd plays one tick per 20 ms at best, so an early drain means it halted (stop, lag or heat).
        if time.monotonic() - t1 < len(pts) / pg.RATE - 0.5:
            raise RuntimeError(f"armd halted mid-write: {ask({'c': 'state'}).get('last_stop')}")
        finished = True
    finally:
        old = {s: signal.signal(s, signal.SIG_IGN) for s in (signal.SIGINT, signal.SIGTERM)}
        home = return_to_l()
        traj.unlink(missing_ok=True)
        for s, h in old.items():
            signal.signal(s, h)
        if finished:                                          # after L: done tells people the card can be taken
            write_done(root, job_path.name, time.monotonic() - t0)
        if not home:
            sys.exit("stopping worker: arm did not confirm the return to L")


def report(name, plan):
    down = plan.tips[plan.ink, 2]
    e = plan.edges
    log(f"[{name}] {len(plan.lines)} line(s), cap {plan.cap:.2f} mm: {plan.lines}")
    log(f"  ticks {len(plan.q)} | est {plan.est_s:.1f} s (path {len(plan.q) / pg.RATE:.1f} s) | max {plan.max_step:.3f} deg/tick"
        f" | worst IK miss {plan.worst_ik:.3f} mm | limit violations {plan.limit_hits}")
    log(f"  pen-down mm from card edges: left {e['left']:.1f} right {e['right']:.1f} red-edge {e['top_red_edge']:.1f}"
        f" arm-edge {e['bottom_arm_edge']:.1f} | z {down.min():.2f}..{down.max():.2f}")


def open_jobs(root):
    jobs = [p for p in sorted((root / "jobs").glob("[0-9]*.json")) if p.stem.isdigit()]
    return [p for p in jobs if not (root / "done" / p.name).exists()]


def handle(path, root, a, planner, seen):
    try:
        stamp = path.stat().st_mtime_ns
        if seen.get(path.name) == stamp:
            return
        job = json.loads(path.read_text())
    except (FileNotFoundError, json.JSONDecodeError):         # deleted or mid-write: the next poll decides
        return
    name = path.stem
    if a.run and ask({"c": "state"})["moving"]:               # don't burn IK on a plan that can't start yet
        return
    try:
        line = job.get("line")
        if not isinstance(line, str):
            raise Refused("job has no text line")
        if job.get("ok_to_show") is False:
            raise Refused("card is not OK to show")
        if "PLACEHOLDER" in line.upper():
            raise Refused("house line is still the placeholder")
        if job.get("mode", "font") != "font":
            log(f"[{name}] mode {job['mode']} is not ready: FELL BACK to font using line")
        plan = planner.plan(job_text(job), pen_height(a.press, a.run))
    except Refused as e:
        log(f"[{name}] REFUSED: {e}")
        seen[path.name] = stamp
        return
    try:
        unchanged = path.stat().st_mtime_ns == stamp and not (root / "done" / path.name).exists()
    except FileNotFoundError:
        unchanged = False
    if not unchanged:
        log(f"[{name}] skipped: job was deleted, changed or finished while planning")
        return
    report(name, plan)
    if not a.run:
        log(f"[{name}] DRY: arm not moved")
        if a.dry_done:
            write_done(root, path.name, plan.est_s)
        seen[path.name] = stamp
        return
    if ask({"c": "state"})["moving"]:
        log(f"[{name}] arm is busy: waiting")
        return
    log(f"[{name}] writing")
    run_plan(root, path, plan)
    log(f"[{name}] delivered")
    try:                                                      # a failed snapshot must never stop the queue
        subprocess.run([sys.executable, str(Path(__file__).with_name("look.py")), "save", f"job-{name}"], capture_output=True, timeout=5)
    except (OSError, subprocess.SubprocessError) as e:
        log(f"[{name}] checkpoint failed: {e}")


def on_term(*_):
    raise KeyboardInterrupt


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--root", type=Path, default=pg.ROOT)
    ap.add_argument("--run", action="store_true", help="move the real arm")
    ap.add_argument("--press", type=float, default=3.0, help="mm below first ink")
    ap.add_argument("--dry-done", action="store_true", help="dry mode only: write done/NNN.json")
    ap.add_argument("--once", action="store_true", help="one pass over the queue, then exit")
    ap.add_argument("--poll", type=float, default=1.0)
    a = ap.parse_args()
    if not 0 <= a.press <= MAX_PRESS:
        sys.exit(f"--press must be 0..{MAX_PRESS} mm")
    if a.run and a.dry_done:
        sys.exit("--dry-done is dry mode only")
    root = a.root.resolve()
    lock = None
    if a.run:
        pen_height(a.press, True)
        lock = open(__file__)
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)  # a second worker's finally would stop this one's stroke
        except OSError:
            sys.exit("another arm_worker --run is active")
        signal.signal(signal.SIGTERM, on_term)
        try:
            ask({"c": "state"})
        except OSError as e:
            sys.exit(f"armd is not reachable: {e}")
    planner, seen = Planner(), {}
    log(f"worker {'RUN' if a.run else 'DRY'} | root {root}")
    try:
        while True:
            for path in open_jobs(root):
                handle(path, root, a, planner, seen)
            if a.once:
                break
            time.sleep(a.poll)
    except KeyboardInterrupt:
        sys.exit("interrupted")
    except (RuntimeError, OSError) as e:
        sys.exit(f"worker stopped: {type(e).__name__}: {e}")


if __name__ == "__main__":
    main()
