"""Trace mode is only promised for a line the arm's own tracer (sim/tracer.py) accepts."""
import cv2
import numpy as np
import pytest

from card import store, visit
from card.tests.conftest import reducto_says, set_layout
from card.trace_check import traces_cleanly

pytest.importorskip("skimage")   # the tracer's own dependency


def photo(tmp_path, name, draw):
    img = np.full((720, 1280, 3), 236, np.uint8)
    draw(img)
    path = tmp_path / f"{name}.jpg"
    cv2.imwrite(str(path), img)
    return path


def a_short_line(img):
    cv2.putText(img, "keeps the light on", (260, 380), cv2.FONT_HERSHEY_SCRIPT_SIMPLEX, 2.2, (25, 25, 25), 4, cv2.LINE_AA)


def a_scribble(img):                     # 210 separate marks: far more strokes than the tracer's limit of 120
    for col in range(30):
        for row in range(7):
            x, y = 250 + col * 26, 318 + row * 13
            cv2.line(img, (x, y), (x + 14, y + 6), (25, 25, 25), 2, cv2.LINE_AA)


def give_one(tmp_path, draw, quote):
    set_layout(mode="trace")
    visit.run(file=photo(tmp_path, "stranger", draw), seed=True, call=reducto_says("keeps the light on"))
    visitor = visit.ingest(file=photo(tmp_path, "visitor", lambda img: None), call=reducto_says("waits up for you"))
    return visit.give(visitor, is_online=True)   # one card waiting: picked by rule, no model needed


def test_a_line_the_tracer_accepts_goes_out_in_trace_mode(tmp_path):
    job = give_one(tmp_path, a_short_line, "the light")
    assert job["mode"] == "trace" and job["trace"]["ok"] and job["trace"]["strokes"] > 0
    assert (store.BASE / job["crop"]).exists()


def test_a_line_the_tracer_refuses_falls_back_to_the_font(tmp_path):
    job = give_one(tmp_path, a_scribble, "the light")
    assert job["mode"] == "font" and job["trace"]["ok"] is False and "strokes" in job["trace"]["why"]
    assert job["line"] == "keeps the light on"            # the arm still has the words to write


def test_font_mode_never_runs_the_tracer(tmp_path):
    set_layout(mode="font")
    visit.run(file=photo(tmp_path, "stranger", a_short_line), seed=True, call=reducto_says("keeps the light on"))
    visitor = visit.ingest(file=photo(tmp_path, "visitor", lambda img: None), call=reducto_says("waits up for you"))
    job = visit.give(visitor, is_online=True)
    assert job["mode"] == "font" and job["trace"] is None


def test_a_missing_tracer_means_font_not_a_crash(tmp_path):
    ok, info = traces_cleanly(photo(tmp_path, "x", a_short_line), sim_dir=tmp_path / "no-sim-here")
    assert ok is False and "not importable" in info["error"]
