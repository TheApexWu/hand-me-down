"""Leg 2: turn a photo of a handwritten line into single-stroke pen paths in page millimetres.

trace_image(path, box) -> (strokes, info). strokes are polylines (u right, v up) centred on 0,0.
info["ok"] is False when the trace fails the quality gate; callers then fall back to the font.
Run directly to test: python tracer.py IMAGE [left top width height]  (box in 0..1 fractions)
"""
import sys

import numpy as np
from PIL import Image
from skimage.filters import threshold_otsu
from scipy.ndimage import distance_transform_edt
from skimage.measure import approximate_polygon, label, regionprops
from skimage.morphology import remove_small_objects, skeletonize

NBRS = [(-1, -1), (-1, 0), (-1, 1), (0, -1), (0, 1), (1, -1), (1, 0), (1, 1)]


def load_ink(path, box=None, max_width_px=1600):
    """Grayscale crop -> boolean ink mask (True = ink)."""
    img = Image.open(path).convert("L")
    if box:
        left, top, w, h = box
        W, H = img.size
        img = img.crop((int(left * W), int(top * H), int((left + w) * W), int((top + h) * H)))
    if img.width > max_width_px:
        img = img.resize((max_width_px, int(img.height * max_width_px / img.width)))
    a = np.asarray(img, dtype=float)
    ink = a < threshold_otsu(a)
    # Specks smaller than ~0.05% of the crop are dust or paper texture, not writing.
    return remove_small_objects(ink, max_size=max(8, int(ink.size * 0.0001)))


def skeleton_paths(sk):
    """Walk a 1-px skeleton into pixel paths: node-to-node edges first, then closed loops."""
    pix = set(zip(*np.nonzero(sk)))

    def nb(p):
        return [(p[0] + dy, p[1] + dx) for dy, dx in NBRS if (p[0] + dy, p[1] + dx) in pix]

    nodes = {p for p in pix if len(nb(p)) != 2}
    used_edges, paths = set(), []
    for n in nodes:
        for first in nb(n):
            if frozenset((n, first)) in used_edges:
                continue
            used_edges.add(frozenset((n, first)))
            path, prev, cur = [n, first], n, first
            while cur not in nodes:
                nxt = [q for q in nb(cur) if q != prev and frozenset((cur, q)) not in used_edges]
                if not nxt:
                    break
                q = nxt[0]
                used_edges.add(frozenset((cur, q)))
                path.append(q)
                prev, cur = cur, q
            paths.append(path)

    rest = pix - {p for path in paths for p in path}
    while rest:                       # pure loops (an "o" with no ends) never touch a node
        start = rest.pop()
        path, cur = [start], start
        while True:
            nxt = [q for q in nb(cur) if q in rest]
            if not nxt:
                break
            cur = nxt[0]
            rest.discard(cur)
            path.append(cur)
        if len(path) > 2:
            path.append(start)
        paths.append(path)
    return paths


def trace_image(path, box=None, height_mm=12.0, max_width_mm=150.0,
                max_strokes=120, max_total_mm=1500.0, min_stroke_px=5, simplify_px=1.0, tap_mm=0.8):
    ink = load_ink(path, box)
    if ink.sum() == 0:
        return [], {"ok": False, "reason": "no ink found"}
    sk = skeletonize(ink)
    # Dots and periods: blobs too short to trace. Only blobs near the writing count as taps,
    # so specks of dust in empty paper stay out.
    lab = label(ink, connectivity=2)
    blobs = [r for r in regionprops(lab) if sk[r.coords[:, 0], r.coords[:, 1]].sum() < min_stroke_px]
    main = ink.copy()
    for r in blobs:
        main[r.coords[:, 0], r.coords[:, 1]] = False
    taps = []
    if blobs and main.any():
        text_h = np.ptp(np.nonzero(main)[0])
        dist = distance_transform_edt(~main)
        for r in blobs:
            cy, cx = r.centroid
            if dist[int(round(cy)), int(round(cx))] <= 0.25 * text_h:
                taps.append(np.array([cx, -cy]))
    polys = []
    for p in skeleton_paths(sk):
        xy = np.array([(x, -y) for y, x in p], dtype=float)      # image y points down; page v up
        if len(xy) < 2 or np.linalg.norm(np.diff(xy, axis=0), axis=1).sum() < min_stroke_px:
            continue
        polys.append(approximate_polygon(xy, tolerance=simplify_px))

    if not polys:
        return [], {"ok": False, "reason": "no strokes after cleanup"}
    allp = np.vstack(polys)
    w_px, h_px = np.ptp(allp[:, 0]), np.ptp(allp[:, 1])
    scale = height_mm / max(h_px, 1.0)
    if w_px * scale > max_width_mm:
        scale = max_width_mm / w_px
    mid = (allp.min(0) + allp.max(0)) / 2
    strokes = sorted(((s - mid) * scale for s in polys), key=lambda s: s[:, 0].min())
    for c in (np.asarray(taps).reshape(-1, 2) - mid) * scale:
        strokes.append(np.array([c - (tap_mm / 2, 0), c + (tap_mm / 2, 0)]))

    total = float(sum(np.linalg.norm(np.diff(s, axis=0), axis=1).sum() for s in strokes))
    info = {"ok": True, "strokes": len(strokes), "total_mm": round(total, 1),
            "width_mm": round(float(w_px * scale), 1), "height_mm": round(float(h_px * scale), 1),
            "skeleton_px": int(sk.sum())}
    if len(strokes) > max_strokes:
        info.update(ok=False, reason=f"{len(strokes)} strokes > {max_strokes}")
    elif total > max_total_mm:
        info.update(ok=False, reason=f"{total:.0f} mm of ink > {max_total_mm:.0f}")
    return strokes, info


def render_strokes_png(strokes, out, px_per_mm=6, pad_mm=5):
    """Quick 2D preview of traced strokes (black on white)."""
    from PIL import ImageDraw
    allp = np.vstack(strokes)
    lo, hi = allp.min(0) - pad_mm, allp.max(0) + pad_mm
    W, H = ((hi - lo) * px_per_mm).astype(int) + 1
    img = Image.new("RGB", (W, H), "white")
    d = ImageDraw.Draw(img)
    for s in strokes:
        pts = [((x - lo[0]) * px_per_mm, (hi[1] - y) * px_per_mm) for x, y in s]
        d.line(pts, fill=(20, 30, 90), width=2)
    img.save(out)


if __name__ == "__main__":
    box = tuple(map(float, sys.argv[2:6])) if len(sys.argv) >= 6 else None
    strokes, info = trace_image(sys.argv[1], box)
    print(info)
    if strokes:
        render_strokes_png(strokes, "trace_preview.png")
        print("preview: trace_preview.png")
