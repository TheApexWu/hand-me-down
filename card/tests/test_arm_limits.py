"""Nothing the arm is known to refuse is ever written as a job (ARM_STATUS.md, 22 Nov)."""
import json
import time

import pytest

from card import jobs, store, visit
from card.tests.conftest import reducto_says, set_layout
from card.tests.test_loop import wait_for

LONG = "this line just keeps going and going well past what one small card can hold"   # 75 characters


def seed(card_photo, text):
    card, _ = visit.run(file=card_photo(f"s{time.time_ns()}", text), seed=True, call=reducto_says(text))
    return card


def test_the_rules_mirror_the_arm():
    assert jobs.cannot_write("keeps the porch light on") is None
    assert jobs.cannot_write("x" * 60) is None and "61 characters" in jobs.cannot_write("x" * 61)
    assert jobs.cannot_write("it’s fine — really… ok") is None           # curly quotes and dashes are swapped
    assert jobs.cannot_write("café crème") is None                                      # accents are dropped
    assert "❤" in jobs.cannot_write("with love ❤")                                # no glyph: refused
    assert "placeholder" in jobs.cannot_write("PLACEHOLDER HOUSE LINE")
    assert jobs.cannot_write("   ") == "there are no words to write"


def test_a_long_card_is_fine_in_quote_mode_because_only_a_phrase_is_written(card_photo):
    card = seed(card_photo, LONG)
    assert card["status"] == "waiting" and all(len(p) <= 16 for p in card["phrases"])
    _, job = visit.run(file=card_photo("v", "v"), call=reducto_says("anything at all"), is_online=True)
    assert job["card_no"] == card["card_no"] and job["line"] == LONG
    assert len(job["quote"]) <= 16 and len(job["quote"].split()) <= 3 and job["quote"] in card["phrases"]


def test_a_card_with_no_phrase_the_arm_can_write_never_reaches_the_rail(card_photo):
    card = seed(card_photo, "antidisestablishmentarianism notwithstanding")
    assert card["status"] == "unwritable" and "no phrase" in card["cannot_write"] and store.waiting() == []
    short = seed(card_photo, "keeps the porch light on")
    _, job = visit.run(file=card_photo("v", "v"), call=reducto_says("anything at all"), is_online=True)
    assert job["card_no"] == short["card_no"]                       # the one with no phrase is never given
    with pytest.raises(jobs.NoJob):                                 # and nothing can write a job for it
        jobs.write({"card_no": card["card_no"], "quote": None, "picked_by": "rule", "why": None, "misses": 0, "secs": 0},
                   store.load_card(short["card_no"]))


def test_correcting_the_line_can_put_the_card_on_the_rail(card_photo):
    card = seed(card_photo, "love❤ hugs❤ always❤")                   # every phrase has a mark the pen cannot draw
    assert card["status"] == "unwritable"
    fixed = visit.fix_line(store.load_card(card["card_no"]), "with love always")
    assert fixed["status"] == "waiting" and "cannot_write" not in fixed and "with love" in fixed["phrases"]


def test_no_job_is_written_for_the_placeholder_house_line(card_photo):
    set_layout(house_line="PLACEHOLDER HOUSE LINE")
    card, job = visit.run(file=card_photo("v", "v"), call=reducto_says("the first visitor of the day"), is_online=True)
    assert job is None and "placeholder" in card["no_job"] and list(store.folder("jobs").glob("*.json")) == []
    assert store.load_card(card["card_no"])["status"] == "waiting"  # their own card still joins the rail


def test_the_screen_shows_only_their_line_when_there_is_nothing_to_write(card_photo, site):
    set_layout(house_line="PLACEHOLDER HOUSE LINE")
    site.capture(card_photo("v", "v"), "the first visitor of the day")
    site.call("/api/confirm", body={"yes": True})
    state = wait_for("card", site)
    assert state["visitor"].endswith("_ink.png") and "stranger" not in state
    status = json.loads(site.get("/api/status")[1])
    assert status["job"] is None and "house line" in status["error"]


def test_a_test_line_the_arm_would_refuse_is_not_sent(site):
    status, body = site.call("/api/testjob", body={"text": LONG})[:2]
    assert status == 400 and "75 characters" in json.loads(body)["error"]
    assert list(store.folder("jobs").glob("*.json")) == []


def test_every_job_is_font_until_the_arm_can_trace(card_photo):
    set_layout(mode="trace", arm_traces=False)
    seed(card_photo, "keeps the porch light on")
    _, job = visit.run(file=card_photo("v", "v"), call=reducto_says("anything"), is_online=True)
    assert job["mode"] == "font" and job["trace"] is None and job["crop"].endswith("_line.png")


def test_the_progress_bar_expects_ninety_seconds_until_the_arm_has_replied():
    assert jobs.expected_seconds() == 90
    store.write_json(jobs.done_file(1), {"delivered_at": "12:40", "duration_s": 88})
    assert jobs.expected_seconds() == 88


def test_a_job_with_no_reply_leaves_writing_and_can_still_be_delivered(site):
    site.call("/api/testjob", body={"text": "hand me down"})
    writing = wait_for("writing", site)
    store.set_state(**{**writing, "started": time.time() - 600})    # ten minutes with no done file
    held = wait_for("held", site)
    assert held["held"] == "late" and "postmark" not in held         # the visitor screen is out of "writing"
    store.write_json(jobs.done_file(900), {"delivered_at": "13:20", "duration_s": 95})   # the arm was only slow
    assert wait_for("delivered", site)["postmark"]["delivered_at"] == "13:20"


def test_a_tick_area_counts_only_once_the_card_is_located(card_photo):
    set_layout(require_tick=True, tick=[0.86, 0.04, 0.08, 0.13])     # a tick area, but no corners marked yet
    card = seed(card_photo, "keeps the porch light on")
    assert card["ok_to_show"] is None and card["status"] == "private"
