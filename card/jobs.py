"""The contract with the arm.

  jobs/031.json  {"card_no": 31, "line": "...", "crop": "cards/031_line.png", "bbox": [l, t, w, h],
                  "quote": "...", "picked_by": "hermes|rule|offline", "written_at": "10:42",
                  "mode": "font|trace"}
  done/031.json  {"delivered_at": "...", "duration_s": 134}      written by the arm side

card_no is the card whose line the arm writes (the stranger's). bbox is the line's box
as fractions of the squared-up card image (card_img); crop is that line already cut out.
In trace mode crop is cards/NNN_trace.png: the same cut in grey with shadows and coloured
rules removed, because that is what the tracer needs. The screen shows the untouched cut.
House-line jobs have no card: they are numbered from 900 and are always mode "font".
"""
import statistics
import time

from . import layout as lay
from . import store

HOUSE_FROM = 900


def job_file(n):
    return store.folder("jobs") / f"{store.nnn(n)}.json"


def done_file(n):
    return store.folder("done") / f"{store.nnn(n)}.json"


def write(pick, visitor, layout=None):
    layout = layout or lay.load()
    stranger = store.load_card(pick["card_no"]) if pick["card_no"] else None
    if stranger and not stranger.get("may_show"):
        raise PermissionError(f"card {store.nnn(stranger['card_no'])} is not OK to show; no job is written for it")
    if stranger:
        n = stranger["card_no"]
        mode, trace, crop = "font", None, stranger.get("crop")
        to_trace = stranger.get("trace_img") or stranger.get("crop")
        if layout["mode"] == "trace" and to_trace:
            # Ask the arm's own tracer, the way the arm will call it, before promising a trace.
            # A line it can't trace cleanly is written in the font instead.
            from .trace_check import traces_cleanly
            ok, info = traces_cleanly(store.BASE / to_trace)
            trace = {"ok": ok, "strokes": info.get("strokes"),
                     "why": None if ok else (info.get("reason") or info.get("error") or "the tracer's gate said no")}
            if ok:
                mode, crop = "trace", to_trace   # in trace mode the arm traces `crop`: give it the cleaned cut
        job = {"card_no": n, "line": stranger["line"], "crop": crop, "bbox": stranger.get("bbox"),
               "quote": pick["quote"], "picked_by": pick["picked_by"], "written_at": stranger["written_at"],
               "mode": mode, "trace": trace,
               "card_img": stranger.get("card_img"), "name": stranger.get("name") or None,
               "ok_to_show": stranger.get("ok_to_show")}
    else:
        used = [int(p.stem) for p in store.folder("jobs").glob("9[0-9][0-9].json")]
        n = max(used, default=HOUSE_FROM - 1) + 1
        job = {"card_no": n, "line": layout["house_line"], "crop": None, "bbox": None, "quote": None,
               "picked_by": pick["picked_by"], "written_at": store.clock(), "mode": "font",
               "card_img": None, "name": None, "ok_to_show": None, "house": True}
    job.update(visitor_no=visitor["card_no"], why=pick["why"], misses=pick["misses"],
               pick_secs=pick["secs"], queued_at=time.time())
    done_file(n).unlink(missing_ok=True)
    store.write_json(job_file(n), job)
    return job


def write_test(text):
    """A job whose words one of us typed, for trying the arm with no card in the loop.

    It follows the contract like any other job. Numbered with the house lines (from 900), always font."""
    used = [int(p.stem) for p in store.folder("jobs").glob("9[0-9][0-9].json")]
    n = max(used, default=HOUSE_FROM - 1) + 1
    job = {"card_no": n, "line": " ".join(text.split()), "crop": None, "bbox": None, "quote": None,
           "picked_by": "rule", "written_at": store.clock(), "mode": "font", "trace": None,
           "card_img": None, "name": None, "ok_to_show": None, "house": True, "test": True,
           "visitor_no": None, "why": "a test line typed by the operator", "misses": 0, "pick_secs": 0.0,
           "queued_at": time.time()}
    done_file(n).unlink(missing_ok=True)
    store.write_json(job_file(n), job)
    return job


def done(n):
    return store.read_json(done_file(n))


def expected_seconds(default=120):
    """How long the arm usually takes, from the replies so far (for the progress bar)."""
    secs = [d["duration_s"] for d in (store.read_json(p) for p in store.folder("done").glob("*.json"))
            if d and isinstance(d.get("duration_s"), (int, float))]
    return round(statistics.median(secs)) if secs else default
