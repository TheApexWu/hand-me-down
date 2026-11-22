import pytest

from card import gate, jobs, store, visit

pytestmark = pytest.mark.strict_gate


def card(no, line="a plain line"):
    return store.save_card({"card_no": no, "line": line, "may_show": True, "checked": True, "status": "new", "written_at": "12:00", "at": float(no)})


def verdict(hold, reason="threat"):
    return {"hold": hold, "reasons": [reason] if hold else [], "safety": reason if hold else "ok", "ai": {"checked": False}}


def test_clean_card_joins_the_rail(monkeypatch):
    monkeypatch.setattr(gate, "check", lambda line: verdict(False))
    assert visit.shelve(card(1))["status"] == "waiting"


def test_held_card_stays_off_the_rail(monkeypatch):
    monkeypatch.setattr(gate, "check", lambda line: verdict(True))
    assert visit.shelve(card(2))["status"] == "held"
    assert 2 not in {c.get("card_no") if isinstance(c, dict) else c for c in store.waiting()}


def test_held_visitor_gets_no_job(monkeypatch):
    monkeypatch.setattr(gate, "check", lambda line: verdict(True, "self_harm"))
    with pytest.raises(jobs.NoJob, match="self_harm"):
        visit.give(card(3))


def test_person_can_release_and_a_new_wording_is_checked_again(monkeypatch):
    calls = []
    monkeypatch.setattr(gate, "check", lambda line: calls.append(line) or verdict(True))
    held = visit.shelve(card(4))
    assert visit.release(held)["status"] == "waiting" and calls == ["a plain line"]
    changed = visit.approve(store.load_card(4), "different words")
    assert changed["status"] == "waiting" or changed["status"] == "held"
    assert visit.shelve(store.load_card(4))["status"] == "held" and calls[-1] == "different words"


def test_safety_failure_holds_the_card(monkeypatch):
    monkeypatch.setattr(gate, "safety", lambda line, cmd=None: "check_failed")
    assert gate.check("a harmless line")["hold"] is True


def test_contact_details_are_not_sent_to_models(monkeypatch):
    monkeypatch.setattr(gate, "safety", lambda *a, **k: pytest.fail("contact details reached the model"))
    assert gate.check("call me at 917 555 0142")["reasons"] == ["contact details"]
