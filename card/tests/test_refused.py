"""A job the arm refuses is never shown as delivered, and the visitor screen leaves 'writing'."""
import json
import sys

from card import arm_sync, fake_arm, jobs, pick, store
from card.tests.test_loop import wait_for

FAKE = f"{sys.executable} -c \"import sys,json; from card.body_server import give; [print(give(c,q)) for c,q in json.loads(sys.argv[1])]\" "
REFUSAL = {"refused": "pen-down path within 0.40 mm of a card edge", "delivered_at": None, "duration_s": None}


def visitor_given(site, card_photo, monkeypatch):
    """Two cards on the rail, a visitor, and a job for card 001 in 'writing'."""
    site.capture(card_photo("a", "keeps the porch light on"), "keeps the porch light on", seed=True)
    site.capture(card_photo("b", "never learned to swim"), "never learned to swim", seed=True)
    monkeypatch.setattr(pick, "HERMES_CMD", FAKE + "'" + json.dumps([["001", "the porch light"]]) + "'")
    site.capture(card_photo("v", "v"), "waits up at night for the porch light")
    site.call("/api/confirm", body={"yes": True})
    return wait_for("writing", site)


def test_a_refusal_posted_by_the_arm_side_is_kept_as_a_refusal(site, card_photo, monkeypatch):
    visitor_given(site, card_photo, monkeypatch)
    site.app.token, site.app.trust_loopback = "tok", False          # as the arm laptop sends it
    assert site.call("/done/001.json", body=REFUSAL, token="tok")[0] == 200
    site.app.trust_loopback = True
    assert jobs.done(1) == REFUSAL and not jobs.delivered(jobs.done(1))
    held = wait_for("held", site)
    assert held["held"] == "refused" and "card edge" in held["reason"] and "postmark" not in held
    status = json.loads(site.get("/api/status")[1])
    assert status["done"]["refused"] and status["state"]["stage"] == "held"
    # the refused card comes off the rail, with the arm's reason, so it isn't handed to the next visitor
    card = store.load_card(1)
    assert card["status"] == "unwritable" and "the arm refused it" in card["cannot_write"]
    assert jobs.expected_seconds() == 90                            # a refusal says nothing about how long writing takes


def test_another_one_after_a_refusal_gives_a_different_card_and_clears_the_refused_job(site, card_photo, monkeypatch):
    visitor_given(site, card_photo, monkeypatch)
    store.write_json(jobs.done_file(1), REFUSAL)
    wait_for("held", site)
    assert site.call("/api/another", body={})[0] == 200
    writing = wait_for("writing", site)
    assert writing["job"] == 2 and "held" not in {k for k, v in writing.items() if v}
    assert not jobs.job_file(1).exists() and not jobs.done_file(1).exists()
    assert fake_arm.run_once(store.BASE, seconds=0, log=lambda *_: None) == ["002"]
    assert wait_for("delivered", site)["postmark"]["duration_s"] == 0


def test_a_refused_job_can_be_withdrawn(site, card_photo, monkeypatch):
    visitor_given(site, card_photo, monkeypatch)
    store.write_json(jobs.done_file(1), REFUSAL)
    wait_for("held", site)
    site.call("/api/withdraw", body={})
    wait_for("idle", site)
    assert not jobs.job_file(1).exists() and not jobs.done_file(1).exists()


def test_the_fake_arm_can_refuse_and_the_refusal_travels_through_the_sync(site, tmp_path):
    site.call("/api/testjob", body={"text": "refuse this one please"})
    wait_for("writing", site)
    site.app.token, site.app.trust_loopback = "tok", False
    arm, have, sent = tmp_path / "arm-laptop", {}, set()
    arm_sync.sync_once(site.base, arm, "tok", have, sent, log=lambda *_: None)
    assert fake_arm.run_once(arm, seconds=0, log=lambda *_: None, refuse_if="refuse this") == []
    assert json.loads((arm / "done" / "900.json").read_text())["refused"]
    assert arm_sync.sync_once(site.base, arm, "tok", have, sent, log=lambda *_: None) == 0
    site.app.trust_loopback = True
    held = wait_for("held", site)
    assert held["held"] == "refused" and "fake arm refuses" in held["reason"]
