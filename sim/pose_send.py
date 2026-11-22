"""Streams the arm's live joints to the card side's 3D panel.

  HMD_TOKEN=... python pose_send.py --url http://192.168.50.198:8040
"""
import argparse
import json
import os
import socket
import time
import urllib.request
from pathlib import Path

SOCK = Path(__file__).resolve().parent.parent / "state" / "armd.sock"
JOINTS = ("shoulder_pan", "shoulder_lift", "elbow_flex", "wrist_flex", "wrist_roll")


def arm_state():
    with socket.socket(socket.AF_UNIX) as s:
        s.settimeout(1)
        s.connect(str(SOCK))
        s.sendall(b'{"c":"state"}\n')
        data = b""
        while not data.endswith(b"\n"):
            chunk = s.recv(65536)
            if not chunk:
                raise ConnectionError("armd closed the socket")
            data += chunk
    return json.loads(data)


def send(url, token, body):
    headers = {"Content-Type": "application/json", "User-Agent": "hmd-pose/1.0"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url.rstrip("/") + "/pose", data=body, headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=1) as r:
        r.read()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", action="append", required=True, help="a card server; repeat for more than one")
    ap.add_argument("--hz", type=float, default=8.0)
    a = ap.parse_args()
    token = os.environ.get("HMD_TOKEN")
    up = {}
    while True:
        started = time.monotonic()
        try:
            s = arm_state()
            # No timestamp: the receiving server stamps it, so the two laptops' clocks can't mark a live arm stale.
            body = json.dumps({"pos": {j: s["pos"][j] for j in JOINTS}, "phase": s.get("phase") or ""}).encode()
        except (OSError, KeyError, ValueError) as e:
            print(f"arm unavailable: {type(e).__name__}", flush=True)
            time.sleep(1)
            continue
        for url in a.url:
            try:
                send(url, token, body)
                now, why = True, ""
            except (OSError, ValueError) as e:
                now, why = False, type(e).__name__
            if up.get(url) != now:
                print(f"{url}: {'sending' if now else 'not sending, ' + why}", flush=True)
                up[url] = now
        time.sleep(max(0, 1 / a.hz - (time.monotonic() - started)))


if __name__ == "__main__":
    main()
