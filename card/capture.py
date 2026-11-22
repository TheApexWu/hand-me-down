"""The card eye: the webcam over the card slot.

The camera comes from card/layout.json ("camera": an index or part of a name; the
HMD_CAMERA environment variable overrides it), so a replug can't swap it for the
laptop's. A thread keeps it open; capture() returns the sharpest of three fresh frames.

  python -m card.capture        Enter saves cards/NNN.jpg + NNN_card.jpg, q quits
"""
import os
import re
import subprocess
import threading
import time

import cv2

from . import layout as lay
from . import store

BUILT_IN = ("macbook", "facetime", "desk view", "capture screen", "iphone", "ipad")


def list_cameras():
    """[(index, name)] as AVFoundation orders them (ffmpeg's listing)."""
    try:
        out = subprocess.run(
            ["ffmpeg", "-hide_banner", "-f", "avfoundation", "-list_devices", "true", "-i", ""],
            capture_output=True, text=True, timeout=10).stderr
    except (OSError, subprocess.TimeoutExpired):
        return []
    video = out.split("AVFoundation audio devices")[0]
    return [(int(i), name.strip()) for i, name in re.findall(r"\[(\d+)\] (.+)", video)
            if "capture screen" not in name.lower()]


def find_camera(spec=None):
    """Return (index, name). spec: an index, part of a name, or None for the default."""
    spec = str(spec if spec is not None else
               os.environ.get("HMD_CAMERA") or lay.load()["camera"]).strip()
    cams = list_cameras()
    if spec.isdigit():
        return int(spec), dict(cams).get(int(spec), f"camera {spec}")
    for i, name in cams:
        if spec.lower() in name.lower():
            return i, name
    external = [(i, n) for i, n in cams if not any(b in n.lower() for b in BUILT_IN)]
    if external:
        return external[0]
    names = ", ".join(n for _, n in cams) or "none found"
    raise RuntimeError(f'no camera matching "{spec}" and no external camera. Seen: {names}. '
                       'Set "camera" in card/layout.json to an index or part of a name.')


def sharpness(frame):
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    if gray.shape[1] > 960:
        gray = cv2.resize(gray, (960, round(gray.shape[0] * 960 / gray.shape[1])))
    return cv2.Laplacian(gray, cv2.CV_64F).var()


class Camera:
    def __init__(self, spec=None, size=(3840, 2160)):
        self.index, self.name = find_camera(spec)
        self.cap = cv2.VideoCapture(self.index, cv2.CAP_AVFOUNDATION)
        if not self.cap.isOpened():
            raise RuntimeError(f"could not open {self.name}. Give the terminal camera access in "
                               "System Settings > Privacy & Security > Camera.")
        self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, size[0])
        self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, size[1])
        self.frame, self.count, self.error = None, 0, None
        self.lock = threading.Lock()
        self.alive = True
        threading.Thread(target=self._loop, daemon=True).start()

    def _loop(self):
        misses = 0
        while self.alive:
            ok, frame = self.cap.read()
            if not ok:
                misses += 1
                self.error = "camera stopped sending frames" if misses > 30 else None
                time.sleep(0.05)
                continue
            misses, self.error = 0, None
            with self.lock:
                self.frame, self.count = frame, self.count + 1

    def latest(self):
        with self.lock:
            return None if self.frame is None else self.frame.copy()

    def _fresh(self, timeout=3.0):
        start, end = self.count, time.time() + timeout
        while self.count == start and time.time() < end:
            time.sleep(0.01)
        return self.latest()

    def capture(self, n=3):
        """The sharpest of n fresh frames."""
        frames = [f for f in (self._fresh() for _ in range(n)) if f is not None]
        if not frames:
            raise RuntimeError(self.error or f"no frames from {self.name}")
        return max(frames, key=sharpness)

    def preview_jpeg(self, width=960):
        frame = self.latest()
        if frame is None:
            return None
        frame = cv2.resize(frame, (width, round(frame.shape[0] * width / frame.shape[1])))
        return cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 70])[1].tobytes()

    def close(self):
        self.alive = False
        time.sleep(0.1)
        self.cap.release()


def save_card_photo(frame, card_no=None, layout=None):
    """Save the frame as cards/NNN.jpg and the squared-up card as cards/NNN_card.jpg.

    Returns the new card record (not yet read).
    """
    layout = layout or lay.load()
    card_no = card_no or store.next_no()
    cv2.imwrite(str(store.frame_file(card_no)), frame, [cv2.IMWRITE_JPEG_QUALITY, 95])
    cv2.imwrite(str(store.card_img_file(card_no)), lay.rectify(frame, layout), [cv2.IMWRITE_JPEG_QUALITY, 95])
    now = time.time()
    card = {"card_no": card_no, "at": now, "written_at": store.clock(now), "status": "new",
            "photo": store.rel(store.frame_file(card_no)), "card_img": store.rel(store.card_img_file(card_no)),
            "crop": None, "line": "", "bbox": None, "name": "", "ok_to_show": None,
            "words": [], "blocks": [], "read_error": None}
    return store.save_card(card)


if __name__ == "__main__":
    cam = Camera()
    print(f"{cam.name}: press Enter to take a card photo, q then Enter to quit")
    while input().strip().lower() != "q":
        c = save_card_photo(cam.capture())
        print("saved", c["photo"], "and", c["card_img"])
    cam.close()
