"""One fast cross-check of both cameras and the arm: numbers in text, one small picture.

  python look.py          report new ink against the saved baseline
  python look.py base     save the current card as the baseline
"""
import json
import shutil
import socket
import sys
import time
from pathlib import Path

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parent.parent
CAM = ROOT / "state" / "cam"
SOCK = ROOT / "state" / "armd.sock"
CARD_AREA = (50_000, 320_000)                                 # px in the 1080p top camera
CARD_ASPECT = (1.9, 3.8)


def frame(name):
    path = CAM / f"{name}.jpg"
    return cv2.imread(str(path)), time.time() - path.stat().st_mtime


def arm():
    try:
        s = socket.socket(socket.AF_UNIX)
        s.settimeout(1)
        s.connect(str(SOCK))
        s.sendall(b'{"c":"state"}\n')
        b = b""
        while not b.endswith(b"\n"):
            b += s.recv(65536)
        s.close()
        r = json.loads(b)
        p = r["pos"]
        return (f"{'moving' if r['moving'] else 'still'} pan {p['shoulder_pan']} lift {p['shoulder_lift']} elbow {p['elbow_flex']} "
                f"wrist {p['wrist_flex']} | max {max(r['temps'].values())} C")
    except OSError as e:
        return f"armd unreachable ({e})"


def find_card(img):
    """The brightest card-shaped rectangle, so moving the riser needs no re-measuring."""
    gray = cv2.GaussianBlur(cv2.cvtColor(img, cv2.COLOR_BGR2GRAY), (7, 7), 0)
    mask = cv2.morphologyEx((gray > 165).astype(np.uint8), cv2.MORPH_CLOSE, np.ones((15, 15), np.uint8))
    best = None
    for c in cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)[0]:
        area = cv2.contourArea(c)
        (_, (w, h), _) = rect = cv2.minAreaRect(c)
        if not CARD_AREA[0] <= area <= CARD_AREA[1] or min(w, h) == 0:
            continue
        fill = area / (w * h)
        if CARD_ASPECT[0] <= max(w, h) / min(w, h) <= CARD_ASPECT[1] and fill > 0.8 and (best is None or fill > best[0]):
            best = (fill, cv2.boxPoints(rect).astype(np.int32))
    return None if best is None else best[1]


def new_ink(base, now, poly):
    mask = np.zeros(base.shape[:2], np.uint8)
    cv2.fillPoly(mask, [poly], 255)
    mask = cv2.erode(mask, np.ones((13, 13), np.uint8)) > 0   # tape and card edges flicker
    a = cv2.GaussianBlur(cv2.cvtColor(base, cv2.COLOR_BGR2GRAY), (3, 3), 0).astype(float)
    b = cv2.GaussianBlur(cv2.cvtColor(now, cv2.COLOR_BGR2GRAY), (3, 3), 0).astype(float)
    b *= np.median(a[mask]) / max(1.0, np.median(b[mask]))
    n, _, stats, _ = cv2.connectedComponentsWithStats((((a - b) > 30) & mask).astype(np.uint8), connectivity=8)
    marks = stats[1:, cv2.CC_STAT_AREA] if n > 1 else np.array([0])
    return int(marks.sum()), int(marks.max())


def picture(top, wrist, poly):
    x, y, w, h = cv2.boundingRect(poly) if poly is not None else (0, 0, top.shape[1], top.shape[0])
    pad = 18
    crop = top[max(0, y - pad):y + h + pad, max(0, x - pad):x + w + pad]
    left = cv2.resize(crop, (720, int(crop.shape[0] * 720 / crop.shape[1])), interpolation=cv2.INTER_AREA)
    right = cv2.resize(wrist, (int(wrist.shape[1] * left.shape[0] / wrist.shape[0]), left.shape[0]), interpolation=cv2.INTER_AREA)
    cv2.imwrite(str(CAM / "look.jpg"), np.hstack([left, right]), [cv2.IMWRITE_JPEG_QUALITY, 82])


def save(label):
    """Encoders can't see a slipped gear, so each checkpoint keeps the numbers and what both cameras saw."""
    out = ROOT / "private" / "calibration" / f"{time.strftime('%H%M%S')}-{label}"
    out.mkdir(parents=True)
    for f in (ROOT / "sim" / "pen.json", *[ROOT / "state" / n for n in ("ink_contact.json", "joint_offsets.json", "poses.json", "last_pose.json")]):
        if f.exists():
            shutil.copy2(f, out / f.name)
    for name in ("top", "wrist"):
        shutil.copy2(CAM / f"{name}.jpg", out / f"{name}.jpg")
    (out / "arm.txt").write_text(arm() + "\n")
    return out


def main():
    if sys.argv[1:2] == ["save"]:
        print("checkpoint", save(sys.argv[2] if len(sys.argv) > 2 else "manual"))
        return
    t0 = time.perf_counter()
    top, top_age = frame("top")
    wrist, wrist_age = frame("wrist")
    poly = find_card(top)
    base_file = CAM / "base.png"
    if sys.argv[1:] == ["base"]:
        cv2.imwrite(str(base_file), top)
        ink = "baseline saved"
    elif poly is None:
        ink = "card not found"
    elif not base_file.exists():
        ink = "no baseline"
    else:
        total, largest = new_ink(cv2.imread(str(base_file)), top, poly)
        ink = f"new ink {total} px, largest mark {largest}"
    picture(top, wrist, poly)
    print(f"{arm()} | top {top_age:.1f}s wrist {wrist_age:.1f}s | {'card found' if poly is not None else 'NO CARD'} | {ink} | "
          f"{(time.perf_counter() - t0) * 1000:.0f} ms | {CAM / 'look.jpg'}")


if __name__ == "__main__":
    main()
