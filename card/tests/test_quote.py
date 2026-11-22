from card.quote import judge_give, quote_on_card

CARD = "has been forgiven and doesn't believe it yet"


def test_quote_must_be_adjacent_words_on_the_card():
    assert quote_on_card("forgiven and doesn't", CARD)
    assert quote_on_card("FORGIVEN AND DOESN’T believe", CARD)      # case and curly apostrophe
    assert quote_on_card("  believe it yet. ", CARD)
    assert not quote_on_card("forgiven believe", CARD)              # not adjacent
    assert not quote_on_card("has been forgotten", CARD)
    assert not quote_on_card("", CARD)
    assert not quote_on_card("forgiven", CARD)                      # one word is not a quote
    assert not quote_on_card("he", "the end")                       # whole words, not substrings
    assert quote_on_card("Home", "home")                            # a one-word card may be quoted whole


def turn():
    return {"turn": "t1", "visitor": "009", "misses": 0, "max_misses": 2,
            "candidates": {"003": CARD, "004": "is leaving and can't say it"}}


def test_give_accepts_a_real_quote_and_closes_the_turn():
    t = turn()
    pick, _ = judge_give(t, "3", "been forgiven and")
    assert pick == {"turn": "t1", "card": "003", "quote": "been forgiven and", "by": "hermes"}
    assert judge_give(t, "004", "is leaving and")[0] is None        # one gift per turn


def test_give_refuses_quote_from_another_card_then_runs_out():
    t = turn()
    pick, msg = judge_give(t, "003", "is leaving and")              # words are on 004, not 003
    assert pick is None and "One try left" in msg and t["misses"] == 1
    pick, msg = judge_give(t, "012", "anything at all")             # not on the rail
    assert pick is None and "No tries left" in msg and t["closed"]
    assert judge_give(t, "003", "been forgiven and")[0] is None     # too late


def test_give_never_gives_the_visitors_own_card():
    assert judge_give(turn(), "009", "anything here")[0] is None


def test_give_with_no_turn():
    assert judge_give(None, "003", "x y")[0] is None
