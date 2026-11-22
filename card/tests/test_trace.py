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
    set_layout(mode="trace", arm_traces=True)
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


# --- what the tracer's own gate lets through: shadows and ruled lines -------------------------------

def line_crop(tmp_path, name, ink=(25, 25, 25), red_rule=False, black_rule=False, shadow=None, edge=False):
    """A cut-out line of 'handwriting' as the camera might see it."""
    img = np.full((170, 1100, 3), 236, np.uint8)
    if red_rule:
        cv2.line(img, (0, 46), (1100, 52), (95, 95, 225), 3)             # an index card's red header rule
    if black_rule:
        cv2.line(img, (90, 150), (1010, 150), (30, 30, 30), 3)           # a pencil line under the words
    if edge:
        cv2.line(img, (0, 162), (1100, 158), (30, 30, 30), 5)            # the table's edge, right across the cut
    cv2.putText(img, "keeps the porch light on", (60, 115), cv2.FONT_HERSHEY_SCRIPT_SIMPLEX, 1.9, ink, 3, cv2.LINE_AA)
    if shadow:
        img[:, 550:] = (img[:, 550:] * shadow).astype(np.uint8)          # a hard shadow over the right half
    path = tmp_path / f"{name}.png"
    cv2.imwrite(str(path), img)
    return path


def cleaned(tmp_path, path):
    from card.read_card import save_trace_crop
    return save_trace_crop(path, tmp_path / f"{path.stem}_trace.png")


def test_a_deep_shadow_is_not_passed_off_as_writing(tmp_path):
    raw = line_crop(tmp_path, "shadow", shadow=0.5)
    ok, info = traces_cleanly(raw)
    assert ok is False and "shadow" in info["reason"] and info["ink_share"] > 0.4
    ok, info = traces_cleanly(cleaned(tmp_path, raw))                     # the cleaned cut traces like a clean line
    assert ok and info["ink_share"] < 0.2
    plain = traces_cleanly(cleaned(tmp_path, line_crop(tmp_path, "plain")))[1]
    assert abs(info["strokes"] - plain["strokes"]) <= 6


def test_a_coloured_rule_in_dim_light_is_cleaned_away(tmp_path):
    raw = line_crop(tmp_path, "red", red_rule=True, shadow=0.8)
    ok, info = traces_cleanly(cleaned(tmp_path, raw))
    assert ok and info["longest_level_mm"] < 6 and info["band_share"] < 0.1


def test_a_black_line_under_the_words_falls_back_to_the_font(tmp_path):
    raw = line_crop(tmp_path, "pencil", black_rule=True)
    ok, info = traces_cleanly(cleaned(tmp_path, raw))                     # no colour, so cleaning can't tell it from ink
    assert ok is False and info["gate"] is True and "line" in info["reason"]


def test_a_dark_edge_right_across_the_cut_is_cleaned_away(tmp_path):
    raw = line_crop(tmp_path, "table", edge=True)
    ok, info = traces_cleanly(cleaned(tmp_path, raw))
    assert ok and info["longest_level_mm"] < 6


def test_coloured_ink_survives_the_cleaning(tmp_path):
    raw = line_crop(tmp_path, "blue", ink=(190, 70, 40))                  # written in blue pen
    ok, info = traces_cleanly(cleaned(tmp_path, raw))
    assert ok and info["strokes"] > 10


def test_in_trace_mode_the_arm_is_given_the_cleaned_cut(tmp_path):
    job = give_one(tmp_path, a_short_line, "the light")
    assert job["mode"] == "trace" and job["crop"].endswith("_trace.png")
    assert cv2.imread(str(store.BASE / job["crop"]), cv2.IMREAD_UNCHANGED).ndim == 2      # grey, for the tracer
    card = store.load_card(job["card_no"])
    assert card["crop"].endswith("_line.png") and card["trace_img"] == job["crop"]         # the screen keeps the natural cut
