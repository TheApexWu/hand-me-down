"""The whole loop without the real arm: card in, confirm, pick, job, fake arm, 'delivered' on the screen."""
import json
import sys
import time

from card import fake_arm, pick, server, store

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
    assert not any(k.startswith("visitor") for k in confirm)         # their own card never reaches the public screen
    assert confirm["words"] > 0

    assert site.call("/api/confirm", body={"yes": True})[0] == 200
    writing = wait_for("writing", site)
    assert writing["stranger"] == "/cards/001_quote.png" and writing["by"] == "hermes" and writing["job"] == 1
    assert writing["quote"] == "the porch light" and not any(k.startswith("visitor") for k in writing)
    assert writing["v"] > confirm["v"]
    import cv2                                                       # only the received words, cut from their ink
    quote = cv2.imread(str(store.BASE / "cards" / "001_quote.png"), cv2.IMREAD_UNCHANGED)
    ink = cv2.imread(str(store.BASE / "cards" / "001_ink.png"), cv2.IMREAD_UNCHANGED)
    assert quote.shape[2] == 4 and quote.shape[1] < ink.shape[1]

    assert fake_arm.run_once(store.BASE, seconds=0.2, log=lambda *_: None) == ["001"]
    delivered = wait_for("delivered", site)
    assert delivered["v"] > writing["v"]                             # the screen is told to redraw
    assert delivered["stranger"] == writing["stranger"] and not any(k.startswith("visitor") for k in delivered)
    post = delivered["postmark"]
    assert len(post["delivered_at"]) == 5 and post["delivered_at"][2] == ":" and post["duration_s"] == 0
    assert len(post["written_at"]) == 5
    status = json.loads(site.get("/api/status")[1])
    assert status["done"]["delivered_at"] == post["delivered_at"] and status["given"] == 1


def test_a_typed_test_line_goes_to_the_arm_and_comes_back_delivered(site):
    """For trying the arm before any real card can flow: the operator types the line."""
    assert site.call("/api/testjob", body={"text": "  hand   me down "})[0] == 200
    writing = wait_for("writing", site)
    assert writing["job"] == 900 and writing["stranger"] is None and writing["house"] is True
    job = store.read_json(store.BASE / "jobs" / "900.json")
    assert {k: job[k] for k in ("card_no", "line", "crop", "bbox", "quote", "picked_by", "mode")} == {
        "card_no": 900, "line": "hand me down", "crop": None, "bbox": None, "quote": None, "picked_by": "rule", "mode": "font"}
    assert len(job["written_at"]) == 5 and job["test"] is True
    assert fake_arm.run_once(store.BASE, seconds=0, log=lambda *_: None) == ["900"]
    assert wait_for("delivered", site)["postmark"]["duration_s"] == 0
    assert site.call("/api/testjob", body={"text": "   "})[0] == 400          # nothing typed, nothing sent


def test_a_job_can_be_withdrawn_before_the_arm_does_it(site):
    site.call("/api/testjob", body={"text": "hand me down"})
    wait_for("writing", site)
    assert site.call("/api/withdraw", body={})[0] == 200
    wait_for("idle", site)
    assert not (store.BASE / "jobs" / "900.json").exists()
    assert fake_arm.run_once(store.BASE, seconds=0, log=lambda *_: None) == []   # nothing for the arm to write


def test_the_screen_shows_the_visitors_card_only_with_the_demo_switch(card_photo, site, monkeypatch):
    site.capture(card_photo("a", "x"), "I feel lost")
    assert not any(k.startswith("mine") for k in wait_for("confirm", site))
    assert site.call("/api/confirm", body={"yes": False})[0] == 200
    wait_for("idle", site)
    monkeypatch.setattr(server, "SHOW_VISITOR", True)
    site.capture(card_photo("b", "y"), "I feel lost")
    assert wait_for("confirm", site)["mine_line"] == "I feel lost"
