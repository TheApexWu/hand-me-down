"""HAND: the only tools the arm's Hermes profile gets.

Hermes sees job numbers and measurements, never card text, so a hostile card cannot steer it.
The limits live here instead of in the prompt. Nothing moves unless state/hermes_arm_live exists.
"""
import json
import os
import sys
import time
from pathlib import Path

import cv2
import numpy as np

try:
    from mcp.server.mcpserver import MCPServer
except ImportError:
    from mcp.server.fastmcp import FastMCP as MCPServer

sys.path.insert(0, str(Path(__file__).resolve().parent))
import arm_worker as aw  # noqa: E402
import learn  # noqa: E402
import pose_go as pg  # noqa: E402

aw.log = lambda *a: print(*a, file=sys.stderr, flush=True)    # stdout belongs to MCP
ROOT = Path(os.environ.get("HMD_ROOT") or pg.ROOT / "private" / "live")
LIVE_FLAG = pg.ROOT / "state" / "hermes_arm_live"
RUNS = pg.ROOT / "private" / "calibration" / "hermes_runs.jsonl"
PRESS, SPEED = (2.0, 4.0), (0.7, 1.2)
WRITING_AREA = np.array([[575, 520], [1165, 510], [1150, 680], [625, 682]], np.int32)
written = {}
server = MCPServer("hand", instructions="Tools for one pen arm. Jobs are numbers; their text is never shown.")


def fail(message):
    return json.dumps({"ok": False, "error": message})


def job_file(job):
    text = str(job).strip()
    if not text.isdigit() or len(text) > 4:
        raise ValueError("job must be a job number")
    return ROOT / "jobs" / f"{int(text):03d}.json"


def recent(n=5):
    if not RUNS.exists():
        return []
    return [json.loads(line) for line in RUNS.read_text().splitlines()[-n:]]


def card_ready():
    age = time.time() - learn.TOP.stat().st_mtime
    if age > 3:
        return False, "camera frame is stale"
    gray = cv2.cvtColor(cv2.imread(str(learn.TOP)), cv2.COLOR_BGR2GRAY)
    mask = np.zeros(gray.shape, np.uint8)
    cv2.fillPoly(mask, [WRITING_AREA], 255)
    if float(np.median(gray[mask > 0])) < 150:
        return False, "no card in the writing area"
    if int(((gray < 75) & (mask > 0)).sum()) > 200:
        return False, "card already has ink: a person must load a blank card"
    return True, "blank card ready"


def ink_score(before, after, line):
    """How much of the planned path got ink, and how much new ink landed away from it."""
    strokes, _, _ = aw.layout(aw.clean(line))
    samples = [np.linspace(a, b, max(2, int(np.linalg.norm(b - a) * 2))) for s in strokes for a, b in zip(s, s[1:])]
    pts = np.vstack(samples).astype(np.float32)
    near = learn.ink_map(before, after)
    project = lambda p: cv2.perspectiveTransform(p.reshape(-1, 1, 2), learn.H).reshape(-1, 2).round().astype(int)
    h, w = near.shape

    def coverage(offset):
        px = project(pts + np.float32(offset))
        return float(near[px[:, 1].clip(0, h - 1), px[:, 0].clip(0, w - 1)].mean())

    grid = np.arange(-5, 5.01, 0.5)
    best, du, dv = max((coverage((a, b)), a, b) for a in grid for b in grid)
    path = np.zeros((h, w), np.uint8)
    for s in strokes:
        cv2.polylines(path, [project(s.astype(np.float32) + np.float32((du, dv))).reshape(-1, 1, 2)], False, 255, 13)
    stray = float((near & (path == 0)).sum() / max(1, near.sum()))
    return {"ink_coverage": round(best, 3), "stray_ink": round(stray, 3), "landed_offset_mm": [float(du), float(dv)]}


@server.tool()
def arm_status() -> str:
    """Arm state, queued job numbers, whether a blank card is ready, and recent run results."""
    try:
        s = aw.ask({"c": "state"})
        ready, why = card_ready()
    except Exception as e:
        return fail(f"arm or camera unavailable: {type(e).__name__}")
    return json.dumps({"ok": True, "live": LIVE_FLAG.exists(), "moving": s["moving"], "max_temp_c": max(s["temps"].values()),
                       "card_ready": ready, "card": why, "queue": [p.stem for p in aw.open_jobs(ROOT)], "recent_runs": recent()})


@server.tool()
def write_job(job: str, press_mm: float = 3.0, speed: float = 1.0) -> str:
    """Write one queued job. press_mm is 2 to 4 below first ink. speed is 0.7 to 1.2 times normal."""
    try:
        path = job_file(job)
    except ValueError as e:
        return fail(str(e))
    stem = path.stem
    if not path.exists() or (ROOT / "done" / path.name).exists():
        return fail("that job is not in the queue")
    if not PRESS[0] <= press_mm <= PRESS[1] or not SPEED[0] <= speed <= SPEED[1]:
        return fail(f"press_mm must be {PRESS[0]}-{PRESS[1]} and speed {SPEED[0]}-{SPEED[1]}")
    data = json.loads(path.read_text())
    line = data.get("line")
    if not isinstance(line, str) or data.get("ok_to_show") is False or "PLACEHOLDER" in line.upper():
        return fail("this job cannot be written")
    first = float(json.loads(aw.CONTACT.read_text())["first_ink_z_mm"])
    keep = aw.DRAW, aw.A_DRAW
    aw.DRAW, aw.A_DRAW = keep[0] * speed, keep[1] * speed ** 2
    try:
        plan = aw.Planner().plan(line, first - press_mm)
    except aw.Refused as e:
        return fail(f"cannot be written: {e}")
    finally:
        aw.DRAW, aw.A_DRAW = keep
    result = {"ok": True, "job": stem, "press_mm": press_mm, "speed": speed, "cap_mm": plan.cap, "est_s": round(plan.est_s)}
    if not LIVE_FLAG.exists():
        written[stem] = {**result, "seconds": plan.est_s}
        return json.dumps({**result, "live": False, "note": "dry run: the arm did not move"})
    ready, why = card_ready()
    if not ready:
        return fail(why)
    before = cv2.imread(str(learn.TOP))
    traj = pg.ROOT / "state" / f"hermes_{stem}.traj.json"
    started = time.monotonic()
    halted = None
    try:
        traj.write_text(json.dumps([aw.as_q(r) for r in plan.q]))
        aw.command({"c": "joints", "set": plan.hover, "speed": 25})
        aw.wait_idle()
        t = time.monotonic()
        aw.command({"c": "traj", "path": str(traj), "phase": f"hermes:{stem}"})
        aw.wait_idle()
        if time.monotonic() - t < len(plan.q) / pg.RATE - 0.5:
            halted = str(aw.ask({"c": "state"}).get("last_stop"))
    finally:
        home = aw.return_to_l()
        traj.unlink(missing_ok=True)
    if halted or not home:
        return fail(f"arm stopped: {halted or 'did not confirm return to L'}")
    time.sleep(0.6)
    score = ink_score(before, learn.fresh_frame(time.time()), line)
    result = {**result, **score, "live": True, "seconds": round(time.monotonic() - started, 1)}
    written[stem] = result
    RUNS.parent.mkdir(parents=True, exist_ok=True)
    with open(RUNS, "a") as f:
        f.write(json.dumps({"t": time.strftime("%H:%M:%S"), **result}) + "\n")
    return json.dumps(result)


@server.tool()
def finish_job(job: str, note: str = "") -> str:
    """Report a written job as delivered. Only works after write_job succeeded for that job."""
    try:
        path = job_file(job)
    except ValueError as e:
        return fail(str(e))
    if path.stem not in written or not path.exists():
        return fail("write this job first")
    aw.write_done(ROOT, path.name, written[path.stem]["seconds"])
    return json.dumps({"ok": True, "job": path.stem, "delivered": True})


@server.tool()
def refuse_job(job: str, reason: str) -> str:
    """Tell the card side this job will not be written, with a short reason."""
    try:
        path = job_file(job)
    except ValueError as e:
        return fail(str(e))
    done = ROOT / "done" / path.name
    if not path.exists() or done.exists():
        return fail("that job is not in the queue")
    done.parent.mkdir(exist_ok=True)
    tmp = done.with_name(f".{done.name}.tmp")
    tmp.write_text(json.dumps({"refused": str(reason)[:200], "delivered_at": None, "duration_s": None}))
    os.replace(tmp, done)
    return json.dumps({"ok": True, "job": path.stem, "refused": True})


@server.tool()
def stop_arm() -> str:
    """Stop any motion and return the arm to its safe home pose."""
    if not LIVE_FLAG.exists():
        return json.dumps({"ok": True, "live": False, "note": "dry run: the arm did not move"})
    return json.dumps({"ok": aw.return_to_l()})


if __name__ == "__main__":
    (ROOT / "jobs").mkdir(parents=True, exist_ok=True)
    (ROOT / "done").mkdir(parents=True, exist_ok=True)
    server.run("stdio")
