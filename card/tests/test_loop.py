"""The whole loop without the real arm: card in, confirm, pick, job, fake arm, 'delivered' on the screen."""
import json
import sys
import time

from card import fake_arm, pick, store

FAKE = f"{sys.executable} -c \"import sys,json; from card.body_server import give; [print(give(c,q)) for c,q in json.loads(sys.argv[1])]\" "


def wait_for(stage, site, seconds=5):
    end = time.time() + seconds
    while time.time() < end:
        state = json.loads(site.get("/state.json")[1])
        if state["stage"] == stage:
            return state
        time.sleep(0.05)
    raise AssertionError(f"screen never reached {stage!r}; it shows {state}")


def test_card_to_delivered_with_the_fake_arm(card_photo, site, monkeypatch):
    site.capture(card_photo("a", "keeps the porch light on"), "keeps the porch light on", seed=True)
    site.capture(card_photo("b", "never learned to swim"), "never learned to swim", seed=True)
    monkeypatch.setattr(pick, "HERMES_CMD", FAKE + "'" + json.dumps([["001", "the porch light"]]) + "'")

    site.capture(card_photo("v", "v"), "waits up at night for the porch light")
    confirm = wait_for("confirm", site)
    assert confirm["visitor"].endswith("_line.png")

    assert site.call("/api/confirm", body={"yes": True})[0] == 200
    writing = wait_for("writing", site)
    assert writing["stranger"] == "/cards/001_line.png" and writing["by"] == "hermes" and writing["job"] == 1
    assert len(writing["marks"]["stranger"]) == 1                    # the quoted words are boxed on their line
    assert writing["v"] > confirm["v"]

    assert fake_arm.run_once(store.BASE, seconds=0.2, log=lambda *_: None) == ["001"]
    delivered = wait_for("delivered", site)
    assert delivered["v"] > writing["v"]                             # the screen is told to redraw
    assert delivered["stranger"] == writing["stranger"] and delivered["visitor"] == writing["visitor"]
    post = delivered["postmark"]
    assert len(post["delivered_at"]) == 5 and post["delivered_at"][2] == ":" and post["duration_s"] == 0
    assert len(post["written_at"]) == 5
    status = json.loads(site.get("/api/status")[1])
    assert status["done"]["delivered_at"] == post["delivered_at"] and status["given"] == 1
