"""Sends the overhead camera to the card side's screen, cropped to the card and arm.

The wrist camera is never sent: it faces the room and sees people.

  HMD_TOKEN=... python cam_send.py --url http://192.168.50.198:8040
"""
import argparse
import os
import time
import urllib.request
from pathlib import Path

import cv2
import numpy as np

TOP = Path(__file__).resolve().parent.parent / "state" / "cam" / "top.jpg"
Y0, Y1, X0, X1 = 290, 965, 300, 1500      # 16:9 around the riser box and the arm's base
WIDTH, QUALITY, MAX_AGE_S, RETRY_S, EYE_RETRY_S = 720, 72, 3.0, 5.0, 15.0


def post(url, token, jpg, route="/cam"):
    headers = {"Content-Type": "image/jpeg", "User-Agent": "hmd-cam/1.0", "X-HMD-Camera": "overhead (arm laptop)"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url.rstrip("/") + route, data=jpg, headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=1.5) as r:
        r.read()


def has_panel(url, token):
    """An older card server can't parse a raw JPEG, so send only once its screen has the camera panel."""
    headers = {"User-Agent": "hmd-cam/1.0"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    with urllib.request.urlopen(urllib.request.Request(url.rstrip("/") + "/", headers=headers), timeout=1.5) as r:
        return b"cam-wrap" in r.read()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", action="append", required=True, help="a card server; repeat for more than one")
    a = ap.parse_args()
    token = os.environ.get("HMD_TOKEN")
    seen, up, ready, retry_at, eye_at = 0.0, {}, set(), {}, {}
    while True:
        try:
            stamp = TOP.stat().st_mtime
        except OSError:
            time.sleep(0.5)
            continue
        if stamp == seen or time.time() - stamp > MAX_AGE_S:
            time.sleep(0.08)
            continue
        try:
            raw = TOP.read_bytes()
        except OSError:
            continue
        frame = cv2.imdecode(np.frombuffer(raw, np.uint8), cv2.IMREAD_COLOR)
        if frame is None:       # caught mid-write: the next frame replaces it
            time.sleep(0.05)
            continue
        seen = stamp
        crop = frame[Y0:Y1, X0:X1]
        crop = cv2.resize(crop, (WIDTH, round(WIDTH * crop.shape[0] / crop.shape[1])), interpolation=cv2.INTER_AREA)
        jpg = cv2.imencode(".jpg", crop, [cv2.IMWRITE_JPEG_QUALITY, QUALITY])[1].tobytes()
        for url in a.url:
            if time.monotonic() < retry_at.get(url, 0):
                continue
            try:
                if url not in ready and not has_panel(url, token):
                    raise ValueError("no camera panel yet")
                ready.add(url)
                post(url, token, jpg)
                now, why = True, ""
                if time.monotonic() >= eye_at.get(url, 0):     # the full frame is what Reducto reads on a --camera remote server
                    try:
                        post(url, token, raw, "/eye")
                    except OSError:
                        eye_at[url] = time.monotonic() + EYE_RETRY_S
            except (OSError, ValueError) as e:
                now, why = False, str(e) if isinstance(e, ValueError) else type(e).__name__
                ready.discard(url)
                retry_at[url] = time.monotonic() + RETRY_S
            if up.get(url) != now:
                print(f"{url}: {'sending' if now else 'not sending, ' + why}", flush=True)
                up[url] = now


if __name__ == "__main__":
    main()
