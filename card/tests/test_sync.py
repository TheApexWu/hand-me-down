"""arm_sync on the arm laptop, against the real server: jobs out, replies back, withdrawn jobs gone."""
import json
import sys
import time

import pytest

from card import arm_sync, fake_arm, jobs, pick, store, visit
from card.tests.conftest import reducto_says

TOKEN = "s3cret-token"
FAKE = f"{sys.executable} -c \"import sys,json; from card.body_server import give; [print(give(c,q)) for c,q in json.loads(sys.argv[1])]\" "


@pytest.fixture
def lan(site, tmp_path):
    """The card laptop on the LAN, and an empty 'arm laptop' folder."""
    site.app.token = TOKEN
    site.app.trust_loopback = False
    arm = tmp_path / "arm-laptop"
    return site, arm, {}, set()


def make_job(card_photo, monkeypatch, n, stranger_line, quote):
    """Seed one card and have a visitor be given it. Returns the job."""
    visit.run(file=card_photo(f"s{n}", stranger_line), seed=True, call=reducto_says(stranger_line))
    stranger = store.next_no() - 1
    monkeypatch.setattr(pick, "HERMES_CMD", FAKE + "'" + json.dumps([[store.nnn(stranger), quote]]) + "'")
    visit.run(file=card_photo(f"other{n}", "x"), seed=True, call=reducto_says(f"some other waiting card {n}"))
    card, job = visit.run(file=card_photo(f"v{n}", "v"), call=reducto_says(f"a visitor's own line {n}"), is_online=True)
    assert job["card_no"] == stranger and job["picked_by"] == "hermes"
    return job


def test_a_job_and_its_pictures_reach_the_arm_and_the_reply_comes_back(card_photo, monkeypatch, lan):
    site, arm, have, sent = lan
    job = make_job(card_photo, monkeypatch, 1, "keeps the porch light on", "the porch light")
    assert arm_sync.sync_once(site.base, arm, TOKEN, have, sent, log=lambda *_: None) == 0
    pulled = json.loads((arm / "jobs" / "001.json").read_text())
    assert pulled["line"] == "keeps the porch light on" and pulled["crop"] == "cards/001_line.png"
    assert (arm / "cards" / "001_line.png").stat().st_size > 0 and (arm / "cards" / "001_card.jpg").exists()
    # the arm (here, the fake one) follows the contract and writes its reply
    assert fake_arm.run_once(arm, seconds=0, log=lambda *_: None) == ["001"]
    assert arm_sync.sync_once(site.base, arm, TOKEN, have, sent, log=lambda *_: None) == 0
    assert jobs.done(job["card_no"])["duration_s"] == 0 and len(jobs.done(job["card_no"])["delivered_at"]) == 5


def test_a_withdrawn_job_disappears_and_its_late_reply_does_not_block_the_next_job(card_photo, monkeypatch, lan):
    site, arm, have, sent = lan
    said = []
    first = make_job(card_photo, monkeypatch, 1, "keeps the porch light on", "the porch light")
    arm_sync.sync_once(site.base, arm, TOKEN, have, sent, log=said.append)
    assert (arm / "jobs" / "001.json").exists()
    visit.ungive(first)                                    # "another one": the card side withdraws the job
    # ...but the arm had already finished it, so a reply for a job that no longer exists is waiting
    (arm / "done").mkdir(exist_ok=True)
    (arm / "done" / "001.json").write_text(json.dumps({"delivered_at": "11:02", "duration_s": 50}))
    visit.discard(store.load_card(1))                      # keep card 1 off the rail so the next job is another card
    second = make_job(card_photo, monkeypatch, 2, "never learned to swim", "learned to swim")
    assert arm_sync.sync_once(site.base, arm, TOKEN, have, sent, log=said.append) == 0     # one pass, no abort
    assert not (arm / "jobs" / "001.json").exists()                                        # withdrawn: the arm can't start it
    assert (arm / "jobs" / f"{store.nnn(second['card_no'])}.json").exists()                # the next job still arrived
    assert any("withdrawn" in line and "settled" in line for line in said)
    assert jobs.done(1) is None                                                            # the stale reply was not accepted
    # and the next reply goes through
    fake_arm.run_once(arm, seconds=0, log=lambda *_: None)
    assert arm_sync.sync_once(site.base, arm, TOKEN, have, sent, log=said.append) == 0
    assert jobs.done(second["card_no"]) is not None


def test_the_arm_skips_a_job_that_is_withdrawn_or_already_done(tmp_path):
    arm = tmp_path / "arm"
    (arm / "jobs").mkdir(parents=True)
    (arm / "done").mkdir()
    (arm / "jobs" / "001.json").write_text(json.dumps({"card_no": 1, "line": "a", "mode": "font"}))
    (arm / "jobs" / "002.json").write_text(json.dumps({"card_no": 2, "line": "b", "mode": "font"}))
    (arm / "done" / "001.json").write_text("{}")
    withdraw = lambda seconds: (arm / "jobs" / "002.json").unlink()     # the job goes while the fake pen "moves"
    assert fake_arm.run_once(arm, seconds=0, log=lambda *_: None, sleep=withdraw) == ["002"]
    (arm / "done" / "002.json").unlink()
    assert fake_arm.run_once(arm, seconds=0, log=lambda *_: None) == []   # nothing left to do: 001 done, 002 gone


def test_sync_refuses_a_job_that_names_a_file_outside_cards(card_photo, monkeypatch, lan):
    site, arm, have, sent = lan
    store.write_json(jobs.job_file(7), {"card_no": 7, "line": "x", "mode": "font", "crop": "../outside.png", "card_img": None})
    said = []
    assert arm_sync.sync_once(site.base, arm, TOKEN, have, sent, log=said.append) == 1
    assert not (arm / "jobs" / "007.json").exists() and not (arm.parent / "outside.png").exists()
    assert "refusing odd image path" in said[-1]


def test_sync_with_the_wrong_token_gets_nothing(lan):
    site, arm, have, sent = lan
    with pytest.raises(Exception) as err:
        arm_sync.sync_once(site.base, arm, "wrong", have, sent)
    assert "401" in str(err.value)
