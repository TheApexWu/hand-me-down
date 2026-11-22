"""card/layout.json: the one place that knows where the card is and what's on it.

  corners  the card's four corners in the camera frame, as fractions of the frame: TL TR BR BL
  card_mm  the card's size (only the ratio matters to the code)
  line, name, tick, number   areas on the card as [left, top, width, height], fractions of the card
  page     optional: the arm's page corners in the frame, for the debug view

Nothing here is measured yet. Set it with:  python -m card layout
"""
import json
import os
from pathlib import Path

import cv2
import numpy as np

# HMD_LAYOUT points a dry run at its own layout file; the real one sits next to this module.
FILE = Path(os.environ.get("HMD_LAYOUT") or Path(__file__).parent / "layout.json")
CARD_PX = 1600  # width of the squared-up card image

DEFAULT = {
    "camera": "iphone",    # an iPhone on a fixed stand over the card (the Brio stays on the arm)
    "card_mm": [127, 76],
    "corners": [[0, 0], [1, 0], [1, 1], [0, 1]],
    "line": [0, 0, 1, 1],
    "name": None,
    "tick": None,
    "number": None,
    "tick_ink_min": 0.05,
    # Only a card whose "OK to show" box is ticked goes on the rail, is picked, is written or is put
    # on a screen. Set false only as a deliberate choice, for cards that have no such box.
    "require_tick": True,
    "page": None,
    "mode": "font",
    "arm_traces": False,   # the arm can't trace yet: every job goes out as font until this is true
    "max_chars": 60,       # longest line the arm is asked to write (66-67 fitted in its tests, 74-77 did not)
    "house_line": "PLACEHOLDER HOUSE LINE",
    "deadline_s": 25,
}


def load():
    try:
        saved = json.loads(FILE.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        saved = {}
    return {**DEFAULT, **saved}


def save(layout):
    FILE.write_text(json.dumps({**DEFAULT, **layout}, indent=1) + "\n")


def measured(layout):
    """False while the layout is still the whole-frame default."""
    return layout["corners"] != DEFAULT["corners"] or layout["line"] != DEFAULT["line"]


def rectify(frame, layout):
    """Cut the card out of the frame and square it up."""
    h, w = frame.shape[:2]
    out_w = min(CARD_PX, w) if layout["corners"] == DEFAULT["corners"] else CARD_PX
    if layout["corners"] == DEFAULT["corners"]:
        out_h = round(out_w * h / w)  # no corners yet: keep the frame's own shape
    else:
        out_h = round(out_w * layout["card_mm"][1] / layout["card_mm"][0])
    src = np.float32([[x * w, y * h] for x, y in layout["corners"]])
    dst = np.float32([[0, 0], [out_w, 0], [out_w, out_h], [0, out_h]])
    return cv2.warpPerspective(frame, cv2.getPerspectiveTransform(src, dst), (out_w, out_h),
                               flags=cv2.INTER_AREA if out_w < w else cv2.INTER_LINEAR)


def inside(box, area):
    """Is the centre of box inside area? Both [l, t, w, h]."""
    if not box or not area:
        return False
    cx, cy = box[0] + box[2] / 2, box[1] + box[3] / 2
    return area[0] <= cx <= area[0] + area[2] and area[1] <= cy <= area[1] + area[3]


def may_show(ok_to_show, layout):
    """The one rule for showing a card to anyone: its "OK to show" box is ticked.

    A card whose tick can't be judged (no tick area in the layout) counts as not ticked."""
    return ok_to_show is True or not layout.get("require_tick", True)


def tick_is_marked(card_img, layout):
    """Plain pixel count: is there ink inside the tick box? None if no tick area is set."""
    area = layout.get("tick")
    if not area or layout["corners"] == DEFAULT["corners"]:
        return None   # with the card not located, a tick area would be judging some other part of the frame
    gray = cv2.cvtColor(card_img, cv2.COLOR_BGR2GRAY)
    H, W = gray.shape
    l, t, w, h = area
    # look at the middle of the box so its printed border doesn't count as ink
    x0, x1 = int((l + w * 0.2) * W), int((l + w * 0.8) * W)
    y0, y1 = int((t + h * 0.2) * H), int((t + h * 0.8) * H)
    patch = gray[y0:y1, x0:x1]
    if patch.size == 0:
        return None
    paper = float(np.percentile(gray, 80))
    return float((patch < paper * 0.6).mean()) >= layout["tick_ink_min"]
