import pytest

from card import bank, jobs, quotes, store, visit


def visitor_card(no=1, line="keeps the porch light on"):
    return store.save_card({"card_no": no, "line": line, "may_show": True, "checked": True, "status": "waiting",
                            "written_at": "12:00", "at": float(no)})


def pick_for(card):
    return {"card_no": card["card_no"], "quote": None, "picked_by": "rule", "why": "test", "misses": 0, "secs": 0.0}


def test_top_up_never_puts_more_than_four_archive_lines_on_the_rail(monkeypatch):
    rows = bank.rail_items()
    monkeypatch.setattr(bank, "rail_items", lambda: rows + [{**rows[0], "id": f"x{i}"} for i in range(3)])
    added = bank.top_up(9)
    assert len(added) == bank.MAX_ARCHIVE == 4
    assert all(c["provenance"] == "archive" and c["bank_label"] == "human_public" and c["source"] for c in added)


def test_an_archive_job_says_so_and_a_visitor_job_says_so():
    archive = bank.top_up(1)[0]
    job = jobs.write(pick_for(archive), visitor_card(1))
    assert job["provenance"] == "archive" and job["bank_id"] == archive["bank_id"] and "GoEmotions" in job["source"]
    assert job["quote"] and job["quote_box"] is None
    job = jobs.write(pick_for(visitor_card(2)), visitor_card(3, "another visitor line"))
    assert job["provenance"] == "visitor" and job["source"] is None


def test_an_ai_line_can_never_become_a_job_even_if_put_on_the_rail():
    ai = bank.load("ai_generated")[0]
    card = store.save_card({"card_no": 710, "line": ai["text"], "may_show": True, "checked": True, "status": "waiting",
                            "written_at": "archive", "at": 1.0, "provenance": "archive", "bank_id": ai["id"]})
    assert "AI lines never reach a visitor" in quotes.cannot_give(card)
    assert visit.shelve(card)["status"] != "waiting"
    with pytest.raises(jobs.NoJob):
        jobs.write(pick_for(card), visitor_card(1))


def test_archive_lines_stay_out_of_the_stack_and_the_visitor_count(site):
    import json
    bank.top_up(2)
    stack = json.loads(site.get("/stack.json")[1])
    assert stack["waiting"] == 0 and stack["archive"] == 2 and stack["cards"] == []
