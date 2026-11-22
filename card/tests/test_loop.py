"""The whole loop without the real arm: card in, confirm, pick, job, fake arm, 'delivered' on the screen."""
import json
import sys
import time

from card import fake_arm, jobs, pick, server, store, visit
from card.tests.conftest import reducto_says

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


def test_the_demo_button_goes_from_card_to_job_and_shows_the_read(card_photo, site, monkeypatch):
    monkeypatch.setattr(server, "DEMO_HOLD_S", 0.5)
    site.capture(card_photo("a", "keeps the porch light on"), "keeps the porch light on", seed=True)
    site.capture(card_photo("b", "never learned to swim"), "never learned to swim", seed=True)
    monkeypatch.setattr(pick, "HERMES_CMD", FAKE + "'" + json.dumps([["001", "the porch light"]]) + "'")
    site.app.reducto_call = reducto_says("I feel lost and left behind")
    assert site.call("/api/demo", body={"file": str(card_photo("v", "v"))})[0] == 200
    assert wait_for("confirm", site)["mine_line"] == "I feel lost and left behind"
    writing = wait_for("writing", site)
    assert writing["by"] == "hermes" and writing["quote"] == "the porch light" and "mine" not in writing
    assert all(c["line"] != "I feel lost and left behind" for c in store.waiting())      # not on the rail unchecked


def test_a_plain_visit_never_shows_the_read_on_the_public_screen(card_photo, site):
    site.capture(card_photo("a", "x"), "I feel lost")
    assert not any(k.startswith("mine") for k in wait_for("confirm", site))


def test_next_card_resets_the_rail_and_runs_again(card_photo, site, monkeypatch):
    monkeypatch.setattr(server, "DEMO_HOLD_S", 0.05)
    site.capture(card_photo("a", "Keep Going!!"), "Keep Going!!", seed=True)
    monkeypatch.setattr(pick, "HERMES_CMD", FAKE + "'" + json.dumps([["001", "Keep Going!!"]] * 2) + "'")
    for n in range(2):
        site.app.reducto_call = reducto_says(f"I feel lost and left behind {n}")
        assert site.call("/api/next", body={"file": str(card_photo(f"v{n}", "v"))})[0] == 200
        writing = wait_for("writing", site)
        assert writing["quote"] == "Keep Going" and writing["job"] == 1
        assert site.call("/api/next", body={})[0] == 409                  # never while the pen may be down
        assert fake_arm.run_once(store.BASE, seconds=0.1, log=lambda *_: None) == ["001"]
        wait_for("delivered", site)
        for _ in range(100):                                             # the server notices the reply on its next tick
            if not json.loads(site.get("/api/status")[1])["busy"]:
                break
            time.sleep(0.02)


def test_a_card_nobody_could_read_gets_no_reply(card_photo, site, monkeypatch):
    monkeypatch.setattr(server, "DEMO_HOLD_S", 0.05)
    site.capture(card_photo("a", "Keep Going!!"), "Keep Going!!", seed=True)
    site.app.reducto_call = reducto_says("")
    assert site.call("/api/next", body={"file": str(card_photo("v", "v"))})[0] == 200
    state = wait_for("idle", site)
    for _ in range(100):
        state = json.loads(site.get("/state.json")[1])
        if state.get("notice") == "unread":
            break
        time.sleep(0.03)
    assert state.get("notice") == "unread" and not list(store.folder("jobs").glob("[0-9]*.json"))
    assert [c["card_no"] for c in store.waiting()] == [1]


def wait_notice(site, want):
    for _ in range(150):
        state = json.loads(site.get("/state.json")[1])
        if state.get("notice") == want:
            return state
        time.sleep(0.03)
    raise AssertionError(f"no {want!r} notice; the screen shows {state}")


def test_no_clear_card_keeps_the_reply_and_frees_the_button(card_photo, site, monkeypatch):
    monkeypatch.setattr(server, "DEMO_HOLD_S", 0.05)
    site.capture(card_photo("a", "Keep Going!!"), "Keep Going!!", seed=True)
    monkeypatch.setattr(pick, "HERMES_CMD", FAKE + "'" + json.dumps([["001", "Keep Going!!"]] * 2) + "'")
    site.app.reducto_call = reducto_says("I feel lost and left behind")
    assert site.call("/api/next", body={"file": str(card_photo("v", "v"))})[0] == 200
    wait_for("writing", site)
    store.write_json(jobs.done_file(1), {"refused": jobs.NOT_CLEAR, "delivered_at": None, "duration_s": None})
    assert wait_notice(site, "blank")["stage"] == "idle"
    assert [c["card_no"] for c in store.waiting()] == [1] and not jobs.job_file(1).exists()
    site.app.reducto_call = reducto_says("I feel lost and left behind again")
    assert site.call("/api/next", body={"file": str(card_photo("w", "w"))})[0] == 200
    assert wait_for("writing", site)["quote"] == "Keep Going"


def test_a_card_the_gate_holds_tells_the_operator_on_screen(card_photo, site, monkeypatch):
    site.capture(card_photo("a", "Keep Going!!"), "Keep Going!!", seed=True)
    monkeypatch.setattr(visit, "screen", lambda card: {"hold": True, "reasons": ["threat"]})
    site.app.reducto_call = reducto_says("a line the gate holds")
    assert site.call("/api/next", body={"file": str(card_photo("v", "v"))})[0] == 200
    assert wait_notice(site, "person")["stage"] == "idle" and not jobs.job_file(1).exists()
