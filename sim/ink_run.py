"""Find pen-down height on the real card: one dash per level, high to low, stop at the first ink.

After each dash the arm lifts out of the camera's view and a webcam diff looks for new ink.
Whatever happens, it finishes by lifting the pen and returning to L. Needs armd running.

  python ink_run.py OUT_DIR
"""
import json
import socket
import subprocess
import sys
import time
from pathlib import Path

import cv2
import numpy as np

import ink_ladder as il
import pose_go as pg

SOCK = str(pg.ROOT / "state" / "armd.sock")
LEVELS_MM = [48.5, 47.5, 46.5, 45.5, 44.5]                    # around the last first-ink height
DOT_MM = (212.5, 28.1)                                        # located from inked lines in the fixed webcam
DX, DY, GAP, DASH, HOVER, ABOVE = -26.0, 0.0, 9.0, 10.0, 95.0, 8.0
CARD_PX = np.array([[890, 878], [972, 838], [1200, 936], [1120, 976]], np.int32)  # card on the riser, MX Brio frame


def ask(m):
    s = socket.socket(socket.AF_UNIX)
    s.settimeout(3)
    s.connect(SOCK)
    s.sendall((json.dumps(m) + "\n").encode())
    b = b""
    while not b.endswith(b"\n"):
        b += s.recv(65536)
    s.close()
    return json.loads(b)


def send(m):
    r = ask(m)
    if not r.get("ok"):
        raise RuntimeError(r.get("err"))
    while ask({"c": "state"})["moving"]:
        time.sleep(0.03)
    time.sleep(0.25)


def grab(path):
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "fatal", "-y", "-f", "avfoundation", "-pixel_format", "uyvy422",
                    "-video_size", "1920x1080", "-framerate", "30", "-i", "MX Brio", "-vf", "select=gte(n\\,12)",
                    "-frames:v", "1", "-q:v", "2", str(path)], check=True, stderr=subprocess.DEVNULL, timeout=10)
    return cv2.imread(str(path))


def new_ink(base, now):
    mask = np.zeros(base.shape[:2], np.uint8)
    cv2.fillPoly(mask, [CARD_PX], 255)
    mask = cv2.erode(mask, np.ones((9, 9), np.uint8)) > 0     # tape and card edges flicker
    a = cv2.GaussianBlur(cv2.cvtColor(base, cv2.COLOR_BGR2GRAY), (3, 3), 0).astype(float)
    b = cv2.GaussianBlur(cv2.cvtColor(now, cv2.COLOR_BGR2GRAY), (3, 3), 0).astype(float)
    b *= np.median(a[mask]) / max(1.0, np.median(b[mask]))    # auto-exposure shifts between captures
    # scattered flicker on the ruled lines faked contact; a real dash is one connected mark
    n, _, stats, _ = cv2.connectedComponentsWithStats((((a - b) > 30) & mask).astype(np.uint8), connectivity=8)
    return int(stats[1:, cv2.CC_STAT_AREA].max()) if n > 1 else 0


def main():
    out = Path(sys.argv[1])
    cal = json.load(open(Path(__file__).parent / "pen.json"))
    pen, ref = pg.pen_model(cal["pen_length_mm"])
    cx, cy = DOT_MM[0] + DX, DOT_MM[1] + DY
    ys = [cy + GAP * (len(LEVELS_MM) - 1) / 2 - GAP * i for i in range(len(LEVELS_MM))]
    x0, x1 = cx - DASH / 2, cx + DASH / 2
    h = (x0, ys[0], HOVER)
    hover, _ = il.solve(pen, ref["home_seed"], h)
    step_file = pg.ROOT / "state" / "ink_step.json"
    found = None
    try:
        send({"c": "joints", "set": hover, "speed": 25})
        send({"c": "joints", "add": {"lift": -20}, "speed": 35})
        base = grab(out / "ink_b0.jpg")
        noise = new_ink(base, grab(out / "ink_b1.jpg"))
        need = max(8, noise * 2)                              # a light graze is only a few pixels at this range
        print(f"negative control: {noise} px changed with no dash | ink threshold {need} px", flush=True)
        send({"c": "joints", "add": {"lift": 20}, "speed": 35})
        for y, z in zip(ys, LEVELS_MM):
            route = [(h, 0), ((x0, y, z + ABOVE), 40.0), ((x0, y, z), 15.0), ((x1, y, z), 30.0), ((x1, y, z + ABOVE), 20.0), (h, 40.0)]
            seed, path = hover, []
            for (p0, _), (p1, speed) in zip(route, route[1:]):
                seg, seed, _ = il.segment(pen, seed, p0, p1, speed)
                path += seg
            step_file.write_text(json.dumps(path))
            send({"c": "traj", "path": str(step_file), "phase": f"ink:{z}"})
            send({"c": "joints", "add": {"lift": -20}, "speed": 35})
            now = grab(out / "ink_now.jpg")
            n = new_ink(base, now)
            print(f"level {z:+.1f} mm at y {y:.1f}: {n} new dark px", flush=True)
            if n >= need and "--all" not in sys.argv:
                found = {"first_ink_z_mm": z, "x_mm": [x0, x1], "y_mm": y, "new_px": n, "noise_px": noise}
                (pg.ROOT / "state" / "ink_candidate.json").write_text(json.dumps(found, indent=2))  # promoted after a look
                cv2.imwrite(str(out / "ink_found.png"), cv2.resize(now[810:1000, 860:1240], None, fx=3, fy=3, interpolation=cv2.INTER_CUBIC))
                break
            send({"c": "joints", "add": {"lift": 20}, "speed": 35})
    finally:
        ask({"c": "stop"})
        send({"c": "go", "name": "l", "speed": 30})
        step_file.unlink(missing_ok=True)
    print("RESULT:", found or "no ink detected at any level")


if __name__ == "__main__":
    main()
