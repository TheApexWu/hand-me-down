"""Font mode must never send the arm a read no person has checked."""
import json
import sys
import time

import pytest

from card import jobs, pick, store, visit
from card.tests.conftest import reducto_says
from card.tests.test_loop import wait_for

pytestmark = pytest.mark.strict_reads
FAKE = f"{sys.executable} -c \"import sys,json; from card.body_server import give; [print(give(c,q)) for c,q in json.loads(sys.argv[1])]\" "


def seed(card_photo, text):
    card, _ = visit.run(file=card_photo(f"s{time.time_ns()}", text), seed=True, call=reducto_says(text))
    return card


def test_a_fresh_read_waits_for_a_person_and_cannot_be_given(card_photo):
    card = seed(card_photo, "Ut ehlm ad minlm Vehram quis")                   # what Reducto made of a real card
    assert card["status"] == "unchecked" and card["checked"] is False and store.waiting() == []
    visitor, job = visit.run(file=card_photo("v", "v"), call=reducto_says("anything at all"), is_online=True)
    assert job.get("house") and job["line"] != card["line"]                   # the unchecked read is not given
    with pytest.raises(jobs.NoJob) as err:                                    # and nothing can write a job for it
        jobs.write({"card_no": card["card_no"], "quote": None, "picked_by": "rule", "why": None, "misses": 0, "secs": 0}, visitor)
    assert "not been checked" in str(err.value)


def test_approving_puts_the_card_on_the_rail_with_the_words_the_person_typed(card_photo):
    card = seed(card_photo, "Ut ehlm ad minlm Vehram quis")
    fixed = visit.approve(store.load_card(card["card_no"]), "Ut enim ad minim veniam quis")
    assert fixed["status"] == "waiting" and fixed["checked"] and fixed["corrected"]
    assert fixed["line"] == "Ut enim ad minim veniam quis"                    # what the arm will write
    assert fixed["reducto_line"] == "Ut ehlm ad minlm Vehram quis"            # what the quote check still uses
    right = seed(card_photo, "keeps the porch light on")
    assert not visit.approve(store.load_card(right["card_no"])).get("corrected")   # approved as read: nothing was changed
    assert [c["card_no"] for c in store.waiting()] == [card["card_no"], right["card_no"]]


def test_the_operators_y_approves_and_the_visitors_tick_does_not(card_photo, site, monkeypatch):
    for text in ("keeps the porch light on", "never learned to swim"):
        seeded = site.capture(card_photo(f"s{time.time_ns()}", text), text, seed=True)
        site.call("/api/approve", body={"card": seeded["card_no"]})
    assert len(store.waiting()) == 2
    monkeypatch.setattr(pick, "HERMES_CMD", FAKE + "'" + json.dumps([["001", "the porch light"], ["002", "learned to swim"]]) + "'")

    # the visitor presses the tick on the screen: the visit goes on, but their own read stays unchecked
    first = site.capture(card_photo("v1", "v"), "waits up at night")
    site.call("/api/confirm", body={"yes": True})
    assert wait_for("writing", site)["job"] == 1
    assert store.load_card(first["card_no"])["status"] == "unchecked"
    status = json.loads(site.get("/api/status")[1])
    assert [c["no"] for c in status["unchecked"]] == [first["card_no"]] and status["unchecked"][0]["line"] == "waits up at night"
    site.call("/api/clear", body={})

    # the operator presses y with a correction typed in: approved, corrected, and on the rail after the visit
    second = site.capture(card_photo("v2", "v"), "is afrald of deep water")
    site.call("/api/confirm", body={"yes": True, "approve": True, "text": "is afraid of deep water"})
    wait_for("writing", site)
    card = store.load_card(second["card_no"])
    assert card["status"] == "waiting" and card["checked"] and card["line"] == "is afraid of deep water"


def test_every_job_carries_only_a_checked_read(card_photo, site, monkeypatch):
    unchecked = site.capture(card_photo("a", "never checked by anyone"), "never checked by anyone", seed=True)
    checked = site.capture(card_photo("b", "keeps the porch light on"), "keeps the porch light on", seed=True)
    site.call("/api/approve", body={"card": checked["card_no"]})
    site.capture(card_photo("v", "v"), "waits up at night")
    site.call("/api/confirm", body={"yes": True, "approve": True})
    writing = wait_for("writing", site)
    assert writing["job"] == checked["card_no"]                               # the only checked card
    job = store.read_json(jobs.job_file(checked["card_no"]))
    assert job["line"] == "keeps the porch light on" and not jobs.job_file(unchecked["card_no"]).exists()


def test_a_reread_needs_checking_again(card_photo):
    card = visit.approve(seed(card_photo, "keeps the porch light on"))
    assert card["status"] == "waiting"
    from card import read_card
    again = read_card.read(store.load_card(card["card_no"]), call=reducto_says("keeps the perch light on"))
    assert again["checked"] is False
    assert visit.shelve(again)["status"] == "unchecked"
