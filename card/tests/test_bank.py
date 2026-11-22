from card import bank, gate, quotes, store, visit


def test_bank_is_labeled_and_ai_can_never_reach_the_rail():
    rows = bank.load()
    assert {x["label"] for x in rows} == {"human_public", "ai_generated", "ai_rewritten"}
    assert not [x for x in rows if x["label"] != "human_public" and x.get("rail_eligible")]
    assert {x["label"] for x in bank.rail_items()} == {"human_public"}
    assert all(x["kind"] == "comfort" and x["gate_label"] == "ok" and quotes.phrases_from_text(x["text"]) for x in bank.rail_items())


def test_top_up_is_marked_as_archive_and_does_not_repeat():
    added = bank.top_up(3)
    assert len(added) == 3 and all(c["provenance"] == "archive" for c in added)
    assert len(store.waiting()) == 3 and bank.top_up(3) == []


def test_real_cards_count_before_archive_cards():
    for n in (1, 2):
        store.save_card({"card_no": n, "line": "a real short line", "may_show": True, "checked": True, "status": "waiting", "written_at": "12:00", "at": float(n)})
    assert len(bank.top_up(3)) == 1
