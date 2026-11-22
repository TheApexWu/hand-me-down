"""The card eye from another machine: send this machine's webcam to the card laptop's server.

Run it on the laptop the webcam over the card is plugged into. It sends a JPEG a few times a
second to POST /eye on the card server (started with --camera remote), carrying the LAN token the
way arm_sync does. The operator page's preview, capture, Demo run and the layout tool then use it.
Standard library and OpenCV only.

  python card/eye_push.py --list
  python card/eye_push.py --host 192.168.50.198 --port 8040 --token TOKEN --camera 0
"""
import argparse
import os
import sys
import time
import urllib.error
import urllib.request

import cv2

BACKEND = cv2.CAP_AVFOUNDATION if sys.platform == "darwin" else cv2.CAP_ANY


def list_cameras(most=6):
    for i in range(most):
        cap = cv2.VideoCapture(i, BACKEND)
        if cap.isOpened():
            ok, frame = cap.read()
            print(f"[{i}] " + (f"{frame.shape[1]}x{frame.shape[0]}" if ok else "opens, but sent no frame"))
        cap.release()


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--host", help="the card laptop (it prints this line when started with --lan --camera remote)")
    ap.add_argument("--port", type=int, default=8040)
    ap.add_argument("--token", default=os.environ.get("HMD_TOKEN"), help="the token the card laptop printed (or HMD_TOKEN)")
    ap.add_argument("--camera", type=int, default=0, help="this machine's camera index (see --list)")
    ap.add_argument("--fps", type=float, default=4)
    ap.add_argument("--width", type=int, default=1920)
    ap.add_argument("--height", type=int, default=1080)
    ap.add_argument("--quality", type=int, default=88, help="JPEG quality: the handwriting is read from these frames")
    ap.add_argument("--list", action="store_true", help="list the cameras this machine can open, then quit")
    a = ap.parse_args()
    if a.list:
        return list_cameras()
    if not (a.host and a.token):
        sys.exit("give --host and --token (the card laptop prints the full line)")
    cap = cv2.VideoCapture(a.camera, BACKEND)
    if not cap.isOpened():
        sys.exit(f"could not open camera {a.camera} (try --list). On a Mac, give the terminal camera access in "
                 "System Settings > Privacy & Security > Camera.")
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, a.width)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, a.height)
    url, said = f"http://{a.host}:{a.port}/eye", None
    headers = {"Content-Type": "image/jpeg", "Authorization": f"Bearer {a.token}", "X-HMD-Camera": f"card eye (remote camera {a.camera})"}
    while True:
        tick = time.time()
        ok, frame = cap.read()
        if not ok:
            msg = f"camera {a.camera} sent no frame"
        else:
            jpg = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, a.quality])[1].tobytes()
            try:
                with urllib.request.urlopen(urllib.request.Request(url, data=jpg, headers=headers, method="POST"), timeout=5) as r:
                    r.read()
                msg = f"sending {frame.shape[1]}x{frame.shape[0]} at {a.fps:g} fps to {url}"
            except urllib.error.HTTPError as e:
                msg = f"the card server said {e.code}: {e.read()[:200].decode(errors='replace')}"
            except OSError as e:
                msg = f"can't reach {url}: {e}"
        if msg != said:   # say each change once, not every frame
            print(time.strftime("%H:%M:%S"), msg, flush=True)
            said = msg
        time.sleep(max(0.0, 1 / a.fps - (time.time() - tick)))


if __name__ == "__main__":
    main()
