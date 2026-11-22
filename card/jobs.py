"""The contract with the arm.

  jobs/031.json  {"card_no": 31, "line": "...", "crop": "cards/031_line.png", "bbox": [l, t, w, h],
                  "quote": "...", "picked_by": "hermes|rule|offline", "written_at": "10:42",
                  "mode": "font|trace"}
  done/031.json  {"delivered_at": "...", "duration_s": 134}      written by the arm side, or, if it
                 would not write the job: {"refused": "reason", "delivered_at": null, "duration_s": null}

card_no is the card whose line the arm writes (the stranger's). bbox is the line's box
as fractions of the squared-up card image (card_img); crop is that line already cut out.
In trace mode crop is cards/NNN_trace.png: the same cut in grey with shadows and coloured
rules removed, because that is what the tracer needs. The screen shows the untouched cut.
House-line jobs have no card: they are numbered from 900 and are always mode "font".

A job the arm refuses gets no done file and leaves the screen waiting, so nothing the arm is
known to refuse is written at all: a line over the length it fits, a character its pen font
lacks, the placeholder house line (cannot_write, below).
"""
import statistics
import time
import unicodedata

from . import layout as lay
from . import store

HOUSE_FROM = 900

# The arm's own clean-up of a line, mirrored from sim/arm_worker.py (clean) at 45f5dd5: it swaps
# these, drops accents, and then refuses anything that is not plain printable ASCII.
TYPOGRAPHY = {"\u2018": "'", "\u2019": "'", "\u201c": '"', "\u201d": '"', "\u2013": "-", "\u2014": "-",
              "\u2026": "...", "\u00a0": " ", "~": "-"}


class NoJob(Exception):
    """There is nothing the arm could write for this visitor. The message says why."""


def pen_text(text):
    """The line as the arm's pen font will see it, and the characters it has no glyph for."""
    for a, b in TYPOGRAPHY.items():
        text = (text or "").replace(a, b)
    text = "".join(c for c in unicodedata.normalize("NFKD", text) if not unicodedata.combining(c))
    text = " ".join(text.split())
    return text, sorted({c for c in text if c != " " and not 32 < ord(c) < 126})


def cannot_write(line, layout=None):
    """Why the arm would refuse this line, or None if it can write it."""
    layout = layout or lay.load()
    text, missing = pen_text(line)
    if not text:
        return "there are no words to write"
    if "PLACEHOLDER" in text.upper():
        return "it is still the placeholder"
    if missing:
        return "the arm's pen has no way to write " + " ".join(missing)
    if len(text) > layout["max_chars"]:
        return f"it is {len(text)} characters long and the arm fits {layout['max_chars']}"
    return None


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
        problem = cannot_write(stranger["line"], layout)
        if problem:
            raise NoJob(f"card {store.nnn(n)} can't be written: {problem}")
        mode, trace, crop = "font", None, stranger.get("crop")
        to_trace = stranger.get("trace_img") or stranger.get("crop")
        if layout["mode"] == "trace" and layout.get("arm_traces") and to_trace:
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
        problem = cannot_write(layout["house_line"], layout)
        if problem:
            raise NoJob(f"no card is waiting, and the house line can't be written: {problem}")
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
    problem = cannot_write(text)
    if problem:
        raise NoJob(f"that line can't be written: {problem}")
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
    """The arm side's reply to job n, or None. A reply is not always a delivery: see delivered()."""
    return store.read_json(done_file(n))


def delivered(reply):
    """True only for a reply that says the arm wrote the job. A refusal is a reply too, and is never this."""
    return bool(reply) and not reply.get("refused")


def expected_seconds(default=90):
    """How long the arm usually takes, from the replies so far (for the progress bar).
    Until it has replied: 90 s, the arm side's own figure for a two- or three-line card (85 to 95 s)."""
    secs = [d["duration_s"] for d in (store.read_json(p) for p in store.folder("done").glob("*.json"))
            if d and isinstance(d.get("duration_s"), (int, float))]
    return round(statistics.median(secs)) if secs else default
