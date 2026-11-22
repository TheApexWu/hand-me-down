from card import store


def test_a_resent_state_still_gets_a_new_version():
    """The screen redraws only when v changes; passing an old state back must not keep its v."""
    first = store.set_state(stage="writing", job=2)
    again = store.set_state(**{**first, "stage": "delivered", "postmark": {"delivered_at": "11:02"}})
    assert again["stage"] == "delivered" and again["v"] > first["v"]
    assert store.state() == again


def test_versions_move_forward_even_in_the_same_millisecond():
    versions = [store.set_state(stage="idle")["v"] for _ in range(50)]
    assert versions == sorted(set(versions))
