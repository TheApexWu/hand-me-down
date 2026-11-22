"""Keeps both cameras warm: the newest frame of each lands in state/cam/ twice a second.

Looking becomes a file read instead of a 1-2 s camera open and exposure settle.

  python camd.py
"""
import fcntl
import signal
import subprocess
import sys
import time
from pathlib import Path

CAM = Path(__file__).resolve().parent.parent / "state" / "cam"
FEEDS = {"top": ("MX Brio", "1920x1080", ["-pixel_format", "uyvy422"]), "wrist": ("USB HD Camera", "1280x720", [])}


def start(name):
    device, size, pixels = FEEDS[name]
    return subprocess.Popen(
        ["ffmpeg", "-hide_banner", "-loglevel", "fatal", "-y", "-f", "avfoundation", *pixels, "-video_size", size,
         "-framerate", "30", "-i", device, "-vf", "fps=2", "-q:v", "3", "-f", "image2", "-update", "1",
         "-atomic_writing", "1", str(CAM / f"{name}.jpg")],
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=open(CAM / f"{name}.log", "a"))


def main():
    CAM.mkdir(parents=True, exist_ok=True)
    lock = open(CAM / "camd.lock", "w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        sys.exit("camd is already running")
    stop = []
    for s in (signal.SIGINT, signal.SIGTERM):
        signal.signal(s, lambda *_: stop.append(1))
    procs = {name: start(name) for name in FEEDS}
    print("camd ready", flush=True)
    while not stop:
        for name, proc in procs.items():
            if proc.poll() is not None:                       # a replugged camera comes back on its own
                procs[name] = start(name)
        time.sleep(2)
    for proc in procs.values():
        proc.terminate()


if __name__ == "__main__":
    main()
