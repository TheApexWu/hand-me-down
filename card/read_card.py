"""Reducto: what a card says and where on the card it says it.

parse (not extract) with model r-1 and return_citations: every block and every
word comes back with a box as fractions of the image we sent, which is the
squared-up card. Words are assigned to fields by where they sit (card/layout.json).

Every read is cached in cache/reads/ by the card image's hash. With no network
the cache answers; with no cache the read fails and the visit goes on without it.

  python -m card.read_card cards/031_card.jpg     print the fields, draw the boxes
"""
import hashlib
import os
import re
import socket
import sys
import time
from pathlib import Path

import cv2
import numpy as np

from . import layout as lay
from . import store
from .quote import words as norm_words

LABELS = ["for someone who", "from", "hand-me-down", "hand me down", "ok to show this", "ok to show", "received"]
SKIP_TYPES = {"Figure", "Table", "Header", "Footer"}


def online(host="platform.reducto.ai", timeout=2.0):
    try:
        socket.create_connection((host, 443), timeout=timeout).close()
        return True
    except OSError:
        return False


def _box(b):
    if not b or b.get("left") is None:
        return None
    return [round(float(b[k]), 5) for k in ("left", "top", "width", "height")]


def union(boxes):
    boxes = [b for b in boxes if b]
    if not boxes:
        return None
    l, t = min(b[0] for b in boxes), min(b[1] for b in boxes)
    r, btm = max(b[0] + b[2] for b in boxes), max(b[1] + b[3] for b in boxes)
    return [round(l, 5), round(t, 5), round(r - l, 5), round(btm - t, 5)]


def blocks_from_response(resp):
    """Flatten a parse response (as a dict) into [{type, text, box, words}]."""
    out = []
    for chunk in (resp.get("result") or {}).get("chunks") or []:
        for b in chunk.get("blocks") or []:
            text = re.sub(r"\s+", " ", b.get("content") or "").strip()
            if not text:
                continue
            cites = ((b.get("extra") or {}).get("word_citations")) or []
            out.append({
                "type": b.get("type"), "text": text, "box": _box(b.get("bbox")),
                "words": [{"text": w.get("text", ""), "box": _box(w.get("bbox"))} for w in cites],
            })
    return out


def call_reducto(image):
    from reducto import Reducto

    client = Reducto(timeout=40.0, max_retries=1)  # REDUCTO_API_KEY from the environment
    upload = client.upload(file=Path(image))
    res = client.parse.run(input=upload.file_id,
                           settings={"model": "r-1", "return_citations": True})
    resp = res.model_dump(mode="json")
    result = resp.get("result") or {}
    if result.get("type") == "url":  # only for very large outputs
        import httpx
        resp["result"] = httpx.get(result["url"], timeout=20).json()
    return resp


def parse_image(image, call=None):
    """Blocks for one card image, from Reducto or, failing that, the cache.

    Returns (blocks, source) with source "reducto" or "cache". Raises if neither works.
    """
    digest = hashlib.sha256(Path(image).read_bytes()).hexdigest()[:24]
    cached = store.folder("cache/reads") / f"{digest}.json"
    try:
        if call is None:
            store.load_env()   # a key added to .env while the server runs counts from the next card
            if not os.environ.get("REDUCTO_API_KEY"):
                raise RuntimeError("REDUCTO_API_KEY is not set")
            if not online():
                raise ConnectionError("no network")
            call = call_reducto
        resp = call(image)
        store.write_json(cached, resp)
        return blocks_from_response(resp), "reducto"
    except Exception:
        resp = store.read_json(cached)
        if resp is None:
            raise
        return blocks_from_response(resp), "cache"


def _is_label(text):
    t = re.sub(r"^no \d+$|^\d+$", "", " ".join(norm_words(text)))
    return not t or any(t == " ".join(norm_words(lab)) for lab in LABELS)


def fields(blocks, layout):
    """Assign what was read to the card's fields by position.

    Returns {line, bbox, words, name, number}. bbox and word boxes are fractions of the card.
    """
    out = {"line": "", "bbox": None, "words": [], "name": "", "number": None}
    all_words = [w for b in blocks for w in b["words"] if w["box"]]

    if lay.measured(layout) and all_words:
        mine = [w for w in all_words if lay.inside(w["box"], layout["line"])]
        out["line"] = " ".join(w["text"] for w in mine)
        out["words"], out["bbox"] = mine, union([w["box"] for w in mine])
    else:
        # no measured layout (or no word boxes): the longest block that isn't a printed label
        best = None
        for b in blocks:
            if b["type"] in SKIP_TYPES or _is_label(b["text"]) or not lay.inside(b["box"] or [0, 0, 1, 1], layout["line"]):
                continue
            letters = sum(ch.isalpha() for ch in b["text"])
            if best is None or letters > best[0]:
                best = (letters, b)
        if best:
            b = best[1]
            boxed = [w for w in b["words"] if w["box"]]
            out["line"], out["words"] = b["text"], boxed
            out["bbox"] = union([w["box"] for w in boxed]) or b["box"]

    if layout.get("name"):
        out["name"] = " ".join(w["text"] for w in all_words if lay.inside(w["box"], layout["name"]))
        if not out["name"]:
            out["name"] = " ".join(b["text"] for b in blocks if lay.inside(b["box"], layout["name"]))
    if layout.get("number"):
        digits = "".join(ch for w in all_words if lay.inside(w["box"], layout["number"]) for ch in w["text"] if ch.isdigit())
        out["number"] = int(digits) if digits else None
    return out


def save_line_crop(card_img, bbox, dest, pad=0.3, row_h=None):
    """Cut the line out of the card image. Returns the crop's rectangle as fractions of the card.

    The margin is a share of one written row's height (row_h, a fraction of the image), not of the
    whole box: a line written over two rows has a tall box, and a margin sized from that takes in
    the card's edge, its header rule and whatever lies on the table.
    """
    img = cv2.imread(str(card_img))
    H, W = img.shape[:2]
    rect = [0.0, 0.0, 1.0, 1.0]
    if bbox:
        l, t, w, h = bbox
        py = min(row_h or h, h) * pad
        # Wider at the sides: on real cards Reducto's box for a word can stop short of its last
        # letters (it ended two letters before the end of "nostra"), and a clipped word looks wrong.
        px = min(row_h or h, h) * 2.0 * H / W
        x0, y0 = max(0, int((l - px) * W)), max(0, int((t - py) * H))
        x1, y1 = min(W, int((l + w + px) * W)), min(H, int((t + h + py) * H))
        if x1 - x0 > 8 and y1 - y0 > 8:
            img = img[y0:y1, x0:x1]
            rect = [x0 / W, y0 / H, (x1 - x0) / W, (y1 - y0) / H]
    cv2.imwrite(str(dest), img)
    return [round(v, 5) for v in rect]


def clean_for_trace(bgr):
    """The line as the arm's tracer should see it: grey, paper evened out, printed rules removed.

    The tracer splits ink from paper with one threshold for the whole crop, so a shadow across the
    line, or an index card's blue and red rules, come out as pen strokes. Two things fix that:
    dividing by the paper's own brightness removes the shadow, and a printed rule is told from ink
    by its colour. Measured on real cards: paper and black ballpoint are nearly grey (the spread
    between the colour channels is 3 to 11), the rules are strongly coloured (27 and up, about 50
    in the middle of a line) and never as dark as ink.
    """
    def flat(grey):
        size = min(121, max(41, int(grey.shape[0] * 0.45))) | 1          # wider than any stroke
        kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (size, size))
        paper = cv2.GaussianBlur(cv2.morphologyEx(grey, cv2.MORPH_CLOSE, kernel), (0, 0), 15)
        return np.clip(grey.astype(np.float32) / np.maximum(paper, 1) * 235, 0, 255).astype(np.uint8)

    dark = lambda img: float((img < 150).mean())
    brightest, dullest = bgr.max(axis=2), bgr.min(axis=2)
    cleaned = flat(brightest)                                             # 235 is paper
    # Coloured (spread over 25) and not as dark as ink (over 120 after evening out). On some cards the
    # rules photograph as dark as 135; black ink stays under 120, even where it crosses a rule.
    rule = (brightest.astype(np.int16) - dullest > 25) & (cleaned > 120)
    cleaned[cv2.dilate(rule.astype(np.uint8), np.ones((3, 3), np.uint8)) > 0] = 235
    grey = flat(cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY))
    # Writing in coloured ink would vanish along with the rules. Only then (almost no dark left at all)
    # keep plain grey. Comparing with the grey version's dark share is wrong on its own: on cards whose
    # rules photograph dark, the rules are most of what is dark, and removing them is the point.
    left, before = dark(cleaned), dark(grey)
    return cleaned if left >= 0.01 or left >= 0.15 * before else grey


def save_trace_crop(line_png, dest, keep=None, margin=0.0, side=None):
    """Write the cleaned cut for the tracer.

    keep: the words' boxes as fractions of this cut. Everything farther than margin above or below
    every word, or farther than side to its left or right (both fractions of the cut's height), is
    painted as paper, so the card's edge, its header rule or something dark on the table at the rim
    of the cut can't be traced as writing. side is wider because a word's box can stop short.
    """
    img = cv2.imread(str(line_png))
    if img is None:
        return None
    # The same ink the screen shows (rules, card edge, table and paper all taken out), drawn back
    # as dark ink on even paper: the cleanest thing to hand the tracer's one-threshold split.
    a = ink_alpha(img, keep=keep, margin=margin, side=side)
    cv2.imwrite(str(dest), (235 - 195 * a).astype(np.uint8))
    return dest


def words_in_cut(card, row_h):
    """The line's word boxes as fractions of its cut, and a margin of a quarter of a written row.

    Empty if any word has no box: then nothing is masked, rather than risk painting over writing."""
    rect, words = card.get("crop_rect"), card.get("words") or []
    if not (rect and words and row_h and all(w.get("box") for w in words)):
        return {}
    keep = [[(b[0] - rect[0]) / rect[2], (b[1] - rect[1]) / rect[3], b[2] / rect[2], b[3] / rect[3]]
            for b in (w["box"] for w in words)]
    return {"keep": keep, "margin": 0.25 * row_h / rect[3], "side": 2.0 * row_h / rect[3]}


INK_BGR = (24, 27, 29)   # the screen's ink colour, #1d1b18


def ink_alpha(bgr, keep=None, margin=0.0, side=None):
    """How much ink each pixel holds (0 to 1), with the paper, the printed rules, the card's edge and
    the table taken out: the handwriting alone, ready to sit on the screen's own paper."""
    grey = clean_for_trace(bgr).astype(np.float32)                       # 235 is paper
    H, W = grey.shape
    if keep:
        m, sx = int(margin * H), int((side if side is not None else margin) * H)
        near = np.zeros((H, W), bool)
        for l, t, w, h in keep:
            near[max(0, int(t * H) - m):int((t + h) * H) + m, max(0, int(l * W) - sx):int((l + w) * W) + sx] = True
        grey[~near] = 235
    # Ink after cleaning is 60-130; what is left of a printed rule sits at 180-215. Start the ramp
    # below the rules, so only pen ink shows.
    a = np.clip((175 - grey) / 85, 0, 1)
    a[a < 0.06] = 0
    solid = (a > 0.3).astype(np.uint8)
    n, lab, stats, _ = cv2.connectedComponentsWithStats(solid, 8)
    if n > 1:
        dist = cv2.distanceTransform(solid, cv2.DIST_L2, 3)
        stroke = float(np.percentile(dist[solid > 0], 75))                # half a pen stroke, give or take
        widest = np.zeros(n, np.float32)
        np.maximum.at(widest, lab.ravel(), dist.ravel())
        x, w = stats[:, cv2.CC_STAT_LEFT], stats[:, cv2.CC_STAT_WIDTH]
        at_side = (x <= 1) | (x + w >= W - 1)                             # the card's edge or the table, at the rim
        # a pen line is thin: anything much fatter is the card's edge, the table or a shadow
        keep_label = (widest <= 3 * stroke + 2) & (stats[:, cv2.CC_STAT_AREA] >= 10) & ~at_side
        keep_label[0] = False
        kept = cv2.dilate(keep_label[lab].astype(np.uint8), np.ones((5, 5), np.uint8)) > 0
        a = np.where(kept, a, 0)
    return np.maximum(a, cv2.dilate(a, np.ones((2, 2), np.uint8)) * 0.85)   # a touch bolder, for reading across a room


def save_ink(card, row_h=None):
    """cards/NNN_ink.png: the line's handwriting as ink on a transparent ground, for the visitor screen.

    Sets card["ink"], card["ink_rect"] (where it sits on the card, as fractions) and card["ink_size"]."""
    crop = cv2.imread(str(store.line_file(card["card_no"])))
    if crop is None or not card.get("crop_rect"):
        return None
    if row_h is None:
        heights = sorted(w["box"][3] for w in card.get("words") or [] if w.get("box"))
        row_h = heights[len(heights) // 2] if heights else None
    a = ink_alpha(crop, **words_in_cut(card, row_h))
    ys, xs = np.nonzero(a > 0.2)
    if not len(xs):
        return None
    H, W = a.shape
    pad = max(6, int(0.02 * H))
    y0, y1, x0, x1 = max(0, ys.min() - pad), min(H, ys.max() + pad + 1), max(0, xs.min() - pad), min(W, xs.max() + pad + 1)
    out = np.zeros((y1 - y0, x1 - x0, 4), np.uint8)
    out[..., :3] = INK_BGR
    out[..., 3] = (a[y0:y1, x0:x1] * 255).astype(np.uint8)
    dest = store.folder("cards") / f"{store.nnn(card['card_no'])}_ink.png"
    cv2.imwrite(str(dest), out)
    l, t, w, h = card["crop_rect"]
    card["ink"] = store.rel(dest)
    card["ink_rect"] = [round(l + w * x0 / W, 5), round(t + h * y0 / H, 5),
                        round(w * (x1 - x0) / W, 5), round(h * (y1 - y0) / H, 5)]
    card["ink_size"] = [int(x1 - x0), int(y1 - y0)]
    return dest


def ink_from_photo(photo, dest):
    """Handwriting from a photo with no Reducto read (the team's invitation, the house line): the whole
    photo, cleaned the same way, as ink on a transparent ground."""
    img = cv2.imread(str(photo))
    if img is None:
        raise ValueError(f"not an image: {photo}")
    if img.shape[1] > 2000:
        img = cv2.resize(img, (2000, round(img.shape[0] * 2000 / img.shape[1])), interpolation=cv2.INTER_AREA)
    a = ink_alpha(img)
    ys, xs = np.nonzero(a > 0.2)
    if not len(xs):
        raise ValueError("no handwriting found in that photo")
    pad = 12
    y0, y1 = max(0, ys.min() - pad), min(a.shape[0], ys.max() + pad + 1)
    x0, x1 = max(0, xs.min() - pad), min(a.shape[1], xs.max() + pad + 1)
    out = np.zeros((y1 - y0, x1 - x0, 4), np.uint8)
    out[..., :3] = INK_BGR
    out[..., 3] = (a[y0:y1, x0:x1] * 255).astype(np.uint8)
    cv2.imwrite(str(dest), out)
    return dest


def read(card, layout=None, call=None):
    """Read a card's image and fill in its record: line, bbox, name, tick, crop."""
    layout = layout or lay.load()
    img_path = store.BASE / card["card_img"]
    try:
        started = time.time()
        blocks, source = parse_image(img_path, call)
        card["read_secs"] = round(time.time() - started, 1)
        got = fields(blocks, layout)
        card.update(blocks=[{"type": b["type"], "text": b["text"], "box": b["box"]} for b in blocks],
                    line=got["line"], reducto_line=got["line"], bbox=got["bbox"], words=got["words"],
                    name=got["name"], printed_no=got["number"], read_source=source,
                    read_error=None if got["line"] else "no handwritten line found")
    except Exception as exc:
        card["read_error"] = f"{type(exc).__name__}: {exc}"[:160]
    card["checked"] = False   # a fresh read: no person has compared it with the handwriting yet
    card["ok_to_show"] = lay.tick_is_marked(cv2.imread(str(img_path)), layout)
    card["may_show"] = lay.may_show(card["ok_to_show"], layout)  # decided once, here, from the paper
    heights = sorted(w["box"][3] for w in card.get("words") or [] if w.get("box"))
    row_h = heights[len(heights) // 2] if heights else None              # one written row's height
    card["crop_rect"] = save_line_crop(img_path, card.get("bbox"), store.line_file(card["card_no"]), row_h=row_h)
    card["crop"] = store.rel(store.line_file(card["card_no"]))
    trace = save_trace_crop(store.line_file(card["card_no"]), store.trace_file(card["card_no"]),
                            **words_in_cut(card, row_h))
    try:
        save_ink(card, row_h)
    except Exception as exc:          # the screen falls back to the photo cut; the visit goes on
        card["ink_error"] = f"{type(exc).__name__}: {exc}"[:160]
    card["trace_img"] = store.rel(trace) if trace else None   # what the arm traces; the screen shows crop
    return store.save_card(card)


def draw_boxes(card, dest):
    """Debug picture: layout areas in green, word boxes in blue, the line in red."""
    layout = lay.load()
    img = cv2.imread(str(store.BASE / card["card_img"]))
    H, W = img.shape[:2]

    def rect(box, colour, thick):
        if box:
            l, t, w, h = box
            cv2.rectangle(img, (int(l * W), int(t * H)), (int((l + w) * W), int((t + h) * H)), colour, thick)

    for key in ("line", "name", "tick", "number"):
        rect(layout.get(key), (60, 170, 60), 2)
    for w in card.get("words", []):
        rect(w["box"], (200, 120, 0), 1)
    rect(card.get("bbox"), (0, 0, 230), 3)
    cv2.imwrite(str(dest), img)
    return dest


if __name__ == "__main__":
    import json
    import shutil
    import time
    from .capture import save_card_photo

    src = sys.argv[1]
    t = time.time()
    c = read(save_card_photo(cv2.imread(src), card_no=999))
    print(f"{time.time() - t:.1f} s via {c.get('read_source')}")
    print(json.dumps({k: c[k] for k in ("line", "bbox", "name", "ok_to_show", "read_error")}, indent=1))
    print("boxes drawn on", draw_boxes(c, f"{src}.boxes.jpg"))
    for f in store.folder("cards").glob("999*"):
        f.unlink()
