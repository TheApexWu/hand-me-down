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

Quote mode: for a stranger's card the arm writes only `quote`, an exact phrase of 2 or 3 words
from one written row of that card, at most 40 characters (card/quotes.py); `line` keeps the whole
card. House-line and typed test jobs still have the arm write `line`.

Every job says where its words came from: "provenance" is "visitor" (a stranger's handwritten
card), "archive" (a screened human line from the public message bank, card/bank.py, with its
"source" and "bank_id"; these have no handwriting and are never shown as a visitor's card),
"house" or "test". The arm can ignore it; the screens and the record use it.

A job the arm refuses gets no done file and leaves the screen waiting, so nothing the arm is
known to refuse is written at all: a quote over its limit, a line over the length it fits (for
house and test jobs), a character its pen font lacks, the placeholder house line.
"""
import statistics
import time

from . import bank, quotes, store
from . import layout as lay
from .pen import MAX_QUOTE_CHARS, MAX_QUOTE_WORDS, pen_text, without_end_marks

HOUSE_FROM = 900


class NoJob(Exception):
    """There is nothing the arm could write for this visitor. The message says why."""


def cannot_write(line, layout=None):
    """Why the arm would refuse to write this whole line (house and test jobs), or None."""
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
        if not stranger.get("checked"):
            raise NoJob(f"card {store.nnn(n)}'s read has not been checked by a person")
        problem = quotes.cannot_give(stranger)
        if problem:
            raise NoJob(f"card {store.nnn(n)} can't be given: {problem}")
        phrase = quotes.find(stranger, pick["quote"]) if pick["quote"] else quotes.default(stranger)
        if not phrase:   # a chosen quote that isn't one of the card's phrases never reaches the arm
            raise NoJob(f"{pick['quote']!r} is not a phrase card {store.nnn(n)} can give")
        quote, missing = pen_text(without_end_marks(phrase["text"]))
        if missing or len(quote.split()) > MAX_QUOTE_WORDS or len(quote) > MAX_QUOTE_CHARS:
            raise NoJob(f"{quote!r} is over the arm's quote limit")
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
               "quote": quote, "quote_box": phrase["box"], "picked_by": pick["picked_by"], "written_at": stranger["written_at"],
               "mode": mode, "trace": trace,
               "card_img": stranger.get("card_img"), "name": stranger.get("name") or None,
               "ok_to_show": stranger.get("ok_to_show")}
        made = bank.provenance(stranger)
        job.update(provenance=made["provenance"], source=made["source"], bank_id=made["bank_id"])
    else:
        problem = cannot_write(layout["house_line"], layout)
        if problem:
            raise NoJob(f"no card is waiting, and the house line can't be written: {problem}")
        used = [int(p.stem) for p in store.folder("jobs").glob("9[0-9][0-9].json")]
        n = max(used, default=HOUSE_FROM - 1) + 1
        job = {"card_no": n, "line": layout["house_line"], "crop": None, "bbox": None, "quote": None,
               "picked_by": pick["picked_by"], "written_at": store.clock(), "mode": "font",
               "card_img": None, "name": None, "ok_to_show": None, "house": True, "provenance": "house"}
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
           "card_img": None, "name": None, "ok_to_show": None, "house": True, "test": True, "provenance": "test",
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
