"""'OK to show': an unticked card must not go on the rail, be picked, be written or appear on a screen."""
import json
import sys
import time

import pytest

from card import jobs, pick, store, visit
from card.tests.conftest import CORNERS, TICK, reducto_says, set_layout

SECRET, OPEN = "the line nobody may see", "keeps the porch light on"
FAKE = f"{sys.executable} -c \"import sys,json; from card.body_server import give; [print(give(c,q)) for c,q in json.loads(sys.argv[1])]\" "


@pytest.fixture(autouse=True)
def tick_required():
    set_layout(require_tick=True, corners=CORNERS, tick=TICK, line=[0.02, 0.3, 0.96, 0.35])


def seed(card_photo, text, ticked):
    card, _ = visit.run(file=card_photo(f"s{time.time_ns()}", text, ticked=ticked), seed=True, call=reducto_says(text))
    return card


def test_the_tick_is_read_from_the_paper(card_photo):
    assert seed(card_photo, OPEN, ticked=True)["ok_to_show"] is True
    assert seed(card_photo, SECRET, ticked=False)["ok_to_show"] is False


def test_an_unticked_card_never_reaches_the_rail_or_a_job(card_photo):
    private = seed(card_photo, SECRET, ticked=False)
    assert private["status"] == "private" and private["may_show"] is False
    assert store.waiting() == []
    # a visitor arrives: with only the private card around, they get the house line, not that card
    _, job = visit.run(file=card_photo("v", "v", ticked=True), call=reducto_says("anything at all"), is_online=True)
    assert job.get("house") and job["line"] != SECRET
    assert not jobs.job_file(private["card_no"]).exists()
    # typing its line in by hand does not put it on the rail either
    assert visit.fix_line(store.load_card(private["card_no"]), SECRET)["status"] == "private"
    assert store.waiting(exclude={job["visitor_no"]}) == []


def test_the_chooser_is_never_offered_an_unticked_card(card_photo, monkeypatch):
    shown = seed(card_photo, OPEN, ticked=True)
    seed(card_photo, "also fine to show this one", ticked=True)
    private = seed(card_photo, SECRET, ticked=False)
    offered = {}
    monkeypatch.setattr(pick, "ask", lambda line, lines, **kw: offered.update(lines) or
                        {"card": store.nnn(shown["card_no"]), "quote": "the porch light", "misses": 0, "secs": 0.1, "why": None})
    _, job = visit.run(file=card_photo("v", "v", ticked=True), call=reducto_says("waits up for you"), is_online=True)
    assert store.nnn(private["card_no"]) not in offered and SECRET not in offered.values()
    assert job["card_no"] == shown["card_no"] and job["picked_by"] == "hermes"


def test_nothing_writes_a_job_for_an_unticked_card(card_photo):
    private = seed(card_photo, SECRET, ticked=False)
    visitor = seed(card_photo, OPEN, ticked=True)
    choice = {"card_no": private["card_no"], "quote": None, "picked_by": "rule", "why": None, "misses": 0, "secs": 0}
    with pytest.raises(PermissionError):
        jobs.write(choice, visitor)
    assert not jobs.job_file(private["card_no"]).exists()


def test_with_no_tick_box_in_the_layout_every_card_stays_private(card_photo):
    set_layout(tick=None)
    card = seed(card_photo, OPEN, ticked=True)
    assert card["ok_to_show"] is None and card["status"] == "private" and store.waiting() == []


def test_require_tick_off_is_the_only_way_to_show_cards_without_a_box(card_photo):
    set_layout(tick=None, require_tick=False)
    assert seed(card_photo, OPEN, ticked=False)["status"] == "waiting"


def test_an_unticked_cards_pictures_are_never_served(card_photo, site):
    get, capture = site.get, site.capture
    shown = capture(card_photo("a", OPEN, ticked=True), OPEN, seed=True)
    private = capture(card_photo("b", SECRET, ticked=False), SECRET, seed=True)
    assert get(f"/cards/{store.nnn(shown['card_no'])}_line.png")[0] == 200
    for name in ("{}.jpg", "{}_card.jpg", "{}_line.png"):
        assert get("/cards/" + name.format(store.nnn(private["card_no"])))[0] == 404
    status = json.loads(get("/api/status")[1])
    assert SECRET not in json.dumps(status) and status["private"] == 1
    assert [c["no"] for c in status["rail"]] == [shown["card_no"]]


def test_an_unticked_visitor_gets_a_gift_but_never_appears_on_the_screen(card_photo, site, monkeypatch):
    app, get, capture = site.app, site.get, site.capture
    shown = capture(card_photo("a", OPEN, ticked=True), OPEN, seed=True)
    capture(card_photo("b", "left the door unlocked for you", ticked=True), "left the door unlocked for you", seed=True)
    monkeypatch.setattr(pick, "HERMES_CMD", FAKE + "'" + json.dumps([[store.nnn(shown["card_no"]), "the porch light"]]) + "'")
    states = []
    real = store.set_state
    monkeypatch.setattr(store, "set_state", lambda **kw: states.append(real(**kw)) or states[-1])
    visitor = capture(card_photo("v", SECRET, ticked=False), SECRET)
    stages = [s["stage"] for s in states]
    assert "confirm" not in stages and stages[-1] == "writing"      # nothing of theirs to confirm on screen
    assert all(s.get("visitor") is None for s in states)             # their crop is never sent to the screen
    assert states[-1]["stranger"] == f"/cards/{store.nnn(shown['card_no'])}_line.png"
    assert states[-1]["marks"]["visitor"] == []
    assert SECRET not in json.dumps(states) and SECRET not in get("/state.json")[1].decode()
    assert store.load_card(visitor["card_no"])["status"] == "private"
    assert app.job["card_no"] == shown["card_no"] and app.job["picked_by"] == "hermes"
