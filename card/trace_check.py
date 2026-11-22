"""Will the arm's tracer make pen strokes out of this line of handwriting?

Runs the tracer in sim/ the way `sim/arm_tools.py plan --trace` runs it and reports the
tracer's own quality gate, so a crop can be checked before a job goes out in "trace" mode.
Nothing is copied from sim/: the tracer is imported from that folder at call time.

  python -m card.trace_check IMAGE [IMAGE ...] [--sim DIR] [--out DIR]
  python -m card.trace_check --cards        every cards/NNN.json that has a crop

One line per image: ok or FAIL, strokes and millimetres of ink against the gate's limits,
the size the line would be written at, and the seconds the trace took. A picture of the
strokes goes to --out. Exit code 1 if any image fails.

ok means the tracer found ink, stayed under its limits, and the strokes don't look like a
shadow or a ruled line (see scribble_reason). It still does not mean the strokes are readable:
look at the picture. Needs Pillow and scikit-image 0.26+ (the tracer's imports).

  python -m card.trace_check --clean IMAGE ...   clean each image first, as the card side does
                                                 before a job goes out (cards/NNN_trace.png)
"""
import argparse
import importlib
import inspect
import re
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SIM = ROOT / "sim"
OUT = Path(tempfile.gettempdir()) / "hmd_trace_check"   # pictures stay out of the repo
HEIGHT_MM = 12.0                                         # the default of arm_tools.py plan --height
_ADDED = []                                              # the sim folder this module put on sys.path

# The tracer's gate only counts strokes and millimetres, so it passes some traces that are scribble.
# These limits catch the ones seen so far. They come from 15 real pencil lines and 8 stand-in cards,
# not yet from marker on the real index cards: treat them as provisional.
MAX_INK_SHARE = 0.40    # more of the crop than this reading as ink = a shadow or dark background, not writing
MAX_LEVEL_MM = 12.0     # one straight level stroke longer than this = a rule or an edge (longest real one: 10.6)
MAX_BAND_SHARE = 0.17   # level strokes along one height adding up to this share of the line's width = a rule


def scribble_reason(tracer, image_path, box, strokes, info):
    """Why a trace that passed the tracer's gate still shouldn't be drawn, or None."""
    import numpy as np
    share = float(tracer.load_ink(str(image_path), tuple(box) if box else None).mean())
    level = [(float(np.ptp(s[:, 0])), float(s[:, 1].mean())) for s in strokes
             if np.ptp(s[:, 0]) > 1.0 and np.ptp(s[:, 1]) <= max(0.6, 0.12 * np.ptp(s[:, 0]))]
    longest = max((w for w, _ in level), default=0.0)
    band = max((sum(w for w, yy in level if abs(yy - y) <= 0.5) for _, y in level), default=0.0)
    band_share = band / max(info.get("width_mm") or 1.0, 1.0)
    info.update(ink_share=round(share, 3), longest_level_mm=round(longest, 1), band_share=round(band_share, 3))
    if share > MAX_INK_SHARE:
        return f"{share:.0%} of the crop reads as ink: a shadow or a dark background, not writing"
    if longest > MAX_LEVEL_MM:
        return f"a straight level line {longest:.0f} mm long is in the trace: a rule on the card, or an edge"
    if band_share > MAX_BAND_SHARE:
        return f"level strokes along one height cover {band_share:.0%} of the line: a rule on the card"
    return None


def load_tracer(sim_dir=None):
    """Import, from sim_dir, the module that arm_tools.py takes trace_image from."""
    sim_dir = Path(sim_dir or SIM).resolve()
    # arm_tools.py names the module itself ("from tracer import trace_image"). Reading the name
    # there keeps this check honest if the arm side switches to tracer_fix; "tracer" otherwise.
    try:
        named = re.search(r"^\s*from\s+(\w+)\s+import\s+trace_image\b", (sim_dir / "arm_tools.py").read_text(), re.M)
    except OSError:
        named = None
    name = named.group(1) if named else "tracer"
    # The arm side's decision (22 Nov): when trace mode is turned on it will use tracer_fix.py, which
    # keeps the short strokes and dots that tracer.py drops. Check with that one if it is there.
    if (sim_dir / "tracer_fix.py").exists():
        name = "tracer_fix"
    for old in _ADDED + [str(sim_dir)]:                  # only one sim folder on the path at a time
        while old in sys.path:
            sys.path.remove(old)
    sys.path.insert(0, str(sim_dir))
    _ADDED[:] = [str(sim_dir)]
    loaded = sys.modules.get(name)
    if loaded is not None and Path(getattr(loaded, "__file__", None) or "").resolve().parent != sim_dir:
        del sys.modules[name]                            # same name, loaded from another folder
    keep, sys.dont_write_bytecode = sys.dont_write_bytecode, True   # leave no __pycache__ in sim/: it isn't ours
    try:
        module = importlib.import_module(name)
    finally:
        sys.dont_write_bytecode = keep
    if Path(module.__file__).resolve().parent != sim_dir:
        raise ImportError(f"{name} was found in {Path(module.__file__).parent}, not in {sim_dir}")
    return module


def traces_cleanly(image_path, box=None, sim_dir=None, preview=None):
    """(ok, info): the tracer's quality gate for one image, called as arm_tools.py calls it.

    box is [left, top, width, height] as 0..1 fractions of the image; None takes all of it.
    info is the tracer's own dict (strokes, total_mm, width_mm, height_mm, and a reason when
    it fails) plus seconds, tracer (the file that ran) and the gate's limits. If the tracer
    can't be imported, or throws on this image, ok is False and info["error"] says why.
    preview: a file to save the tracer's picture of the strokes to.
    """
    try:
        tracer = load_tracer(sim_dir)
        args = inspect.signature(tracer.trace_image).parameters
    except Exception as exc:   # no sim/ folder, or Pillow / scikit-image missing from this venv
        return False, {"error": f"tracer not importable from {Path(sim_dir or SIM)}: {type(exc).__name__}: {exc}"}
    limits = {k: args[k].default for k in ("max_strokes", "max_total_mm") if k in args}
    started = time.perf_counter()
    try:
        strokes, info = tracer.trace_image(str(image_path), tuple(box) if box else None, height_mm=HEIGHT_MM)
    except Exception as exc:   # arm_tools.py would stop here too
        return False, {"error": f"{type(exc).__name__}: {exc}", "tracer": tracer.__file__}
    info = {**info, **limits, "seconds": round(time.perf_counter() - started, 3), "tracer": tracer.__file__}
    if preview and len(strokes) and hasattr(tracer, "render_strokes_png"):
        try:
            Path(preview).parent.mkdir(parents=True, exist_ok=True)
            tracer.render_strokes_png(strokes, str(preview))
            info["preview"] = str(preview)
        except Exception as exc:   # the picture is a nicety: the gate's answer still stands
            info["preview_error"] = f"no picture: {type(exc).__name__}: {exc}"
    info["gate"] = bool(info.get("ok"))                 # the tracer's own verdict, kept apart from ours
    if info["gate"]:
        try:
            why = scribble_reason(tracer, image_path, box, strokes, info)
        except Exception as exc:                         # the guard must never be the thing that breaks
            why = None
            info["guard_error"] = f"{type(exc).__name__}: {exc}"
        if why:
            info.update(ok=False, reason=why)
    return bool(info.get("ok")), info


def row(label, ok, info, width=0):
    if "strokes" in info:
        numbers = (f"{info['strokes']:>4}/{info.get('max_strokes', '?')} strokes  "
                   f"{info['total_mm']:>7.1f}/{info.get('max_total_mm', '?')} mm ink  "
                   f"{info['width_mm']:>5.1f} x {info['height_mm']:>4.1f} mm")
    else:
        numbers = "no strokes"
    why = info.get("error") or info.get("reason") or info.get("preview_error") or ""
    return f"{'ok  ' if ok else 'FAIL'}  {label:<{width}}  {numbers}  {info.get('seconds', 0):>5.2f} s  {why}".rstrip()


def main(argv=None):
    ap = argparse.ArgumentParser(prog="python -m card.trace_check", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("images", nargs="*", metavar="IMAGE")
    ap.add_argument("--cards", action="store_true", help="check the cut of every card in cards/ that the arm would trace")
    ap.add_argument("--clean", action="store_true", help="clean each IMAGE first (shadows, coloured rules), as jobs do")
    ap.add_argument("--sim", metavar="DIR", help="the folder with the tracer (default: this repo's sim/)")
    ap.add_argument("--out", metavar="DIR", default=str(OUT), help=f"where the pictures go (default: {OUT})")
    a = ap.parse_args(argv)

    todo = [(Path(f).name, Path(f), Path(f).stem) for f in a.images]   # (label, image, picture name)
    if a.clean:
        from .read_card import save_trace_crop
        Path(a.out).mkdir(parents=True, exist_ok=True)
        todo = [(label, Path(save_trace_crop(image, Path(a.out) / f"{stem}_cleaned.png")), stem) for label, image, stem in todo]
    if a.cards:
        from . import store
        for path in sorted((store.BASE / "cards").glob("[0-9][0-9][0-9].json")):
            card = store.read_json(path) or {}
            if card.get("trace_img") or card.get("crop"):
                todo.append((path.stem, store.BASE / (card.get("trace_img") or card["crop"]), path.stem))
        if not todo:
            print(f"no card with a crop in {store.BASE / 'cards'}")
            return 0
    if not todo:
        ap.error("give at least one IMAGE, or --cards")

    width = max(len(label) for label, _, _ in todo)
    failed, ran = 0, set()
    for label, image, stem in todo:
        ok, info = traces_cleanly(image, sim_dir=a.sim, preview=Path(a.out) / f"{stem}_trace.png")
        print(row(label, ok, info, width))
        failed += not ok
        ran.add(info.get("tracer"))
    print(f"{len(todo) - failed} ok, {failed} failed. Tracer: {', '.join(sorted(filter(None, ran))) or 'none ran'}. "
          f"Pictures of the strokes: {a.out}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
