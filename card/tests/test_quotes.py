"""Quote mode: a stranger's card is given as one exact short phrase from one written row."""
import json
import sys
import time

import pytest

from card import jobs, pick, quotes, store, visit
from card.pen import MAX_QUOTE_CHARS, pen_text
from card.quote import judge_give, quote_on_card
from card.tests.conftest import reducto_says

FAKE = f"{sys.executable} -c \"import sys,json; from card.body_server import give; [print(give(c,q)) for c,q in json.loads(sys.argv[1])]\" "


def two_rows(top_words, bottom_words):
    """A card whose words sit in two written rows, with Reducto-style boxes."""
    words, x = [], 0.05
    for t in top_words:
        words.append({"text": t, "box": [x, 0.34, 0.04 + 0.025 * len(t), 0.09]}); x += 0.07 + 0.025 * len(t)
    x = 0.05
    for t in bottom_words:
        words.append({"text": t, "box": [x, 0.49, 0.04 + 0.025 * len(t), 0.10]}); x += 0.07 + 0.025 * len(t)
    return {"card_no": 9, "line": " ".join(top_words + bottom_words), "words": words}


def test_phrases_are_two_or_three_words_from_one_row_within_the_character_limit():
    card = two_rows(["Lorem", "Ipsum"], ["dolor", "sit", "amet"])
    texts = [p["text"] for p in quotes.phrases(card)]
    assert texts == ["Lorem Ipsum", "dolor sit", "sit amet", "dolor sit amet"]
    assert "Ipsum dolor" not in texts                               # never across a line break
    for p in quotes.phrases(two_rows(["Pretiumpretium", "ex", "sapiensapien"], ["Vitaevitaevitae", "pellentesquepellentesque"])):
        text, missing = pen_text(p["text"])
        assert not missing and 2 <= len(text.split()) <= 3 and len(text) <= MAX_QUOTE_CHARS
    assert [p["text"] for p in quotes.phrases(two_rows(["Pretiumpretium", "ex", "sapiensapien"], ["Vitaevitaevitae", "pellentesquepellentesque"]))] == ["Pretiumpretium ex", "ex sapiensapien"]


def test_a_phrase_takes_the_corrected_words_but_keeps_reductos_places():
    card = two_rows(["Ut", "ehlm", "ad"], ["minlm", "Vehram", "quis"])
    card.update(line="Ut enim ad minim veniam quis", corrected=True)
    texts = [p["text"] for p in quotes.phrases(card)]
    assert "Ut enim" in texts and "minim veniam" in texts and not any("ehlm" in t for t in texts)
    assert quotes.find(card, "MINIM VENIAM")["box"] is not None      # case and punctuation don't matter


def test_the_chooser_must_give_one_listed_phrase_exactly():
    turn = {"turn": "t1", "misses": 0, "max_misses": 2, "candidates": {"004": ["Ad litora", "per conubia", "conubia nostra"]}}
    assert judge_give(dict(turn), "004", "litora torquent")[0] is None          # on the card, but not a listed phrase
    assert judge_give(dict(turn), "004", "per conubia nostra")[0] is None       # longer than allowed: not listed
    assert judge_give(dict(turn), "004", "per")[0] is None
    pick_, _ = judge_give(dict(turn), "004", "Per Conubia.")
    assert pick_["quote"] == "per conubia"                                      # the listed text, not the model's


def test_every_job_quote_is_a_listed_phrase_within_the_limit_and_on_the_card(card_photo, monkeypatch):
    lines = ["keeps the porch light on for whoever is late", "never learned to swim and still loves the sea",
             "is leaving in the morning and can't say it"]
    for i, t in enumerate(lines):
        visit.run(file=card_photo(f"s{i}", t), seed=True, call=reducto_says(t)); time.sleep(0.01)
    monkeypatch.setattr(pick, "HERMES_CMD", FAKE + "'" + json.dumps([["002", "loves the sea"]]) + "'")
    _, job = visit.run(file=card_photo("v", "v"), call=reducto_says("is afraid of deep water"), is_online=True)
    assert job["card_no"] == 2 and job["quote"] == "loves the sea" and job["line"] == lines[1]
    assert len(job["quote"]) <= MAX_QUOTE_CHARS and len(job["quote"].split()) <= 3 and quote_on_card(job["quote"], job["line"])
    assert job["quote_box"] and len(job["quote_box"]) == 4


def test_the_chooser_is_shown_each_cards_phrases(card_photo, monkeypatch):
    for i, t in enumerate(["keeps the porch light on", "never learned to swim"]):
        visit.run(file=card_photo(f"s{i}", t), seed=True, call=reducto_says(t)); time.sleep(0.01)
    seen = {}
    monkeypatch.setattr(pick, "ask", lambda line, lines, **kw: seen.update(lines) or {"card": "001", "quote": "porch light on", "misses": 0, "secs": .1, "why": None})
    _, job = visit.run(file=card_photo("v", "v"), call=reducto_says("waits up at night"), is_online=True)
    assert seen["001"]["line"] == "keeps the porch light on" and "the porch light" in seen["001"]["phrases"]
    assert all(len(p) <= MAX_QUOTE_CHARS for v in seen.values() for p in v["phrases"])
    assert job["quote"] == "porch light on"


def test_house_and_typed_test_jobs_still_write_their_line(card_photo, site):
    from card.tests.test_loop import wait_for
    site.call("/api/testjob", body={"text": "a line the two of us wrote by hand"})
    wait_for("writing", site)
    job = store.read_json(jobs.job_file(900))
    assert job["test"] and job["quote"] is None and job["line"] == "a line the two of us wrote by hand"
    _, house = visit.run(file=card_photo("v", "v"), call=reducto_says("the very first visitor"), is_online=True)
    assert house["house"] and house["quote"] is None and house["line"] == "a line the two of us wrote by hand"


def test_a_gift_counts_even_if_the_pick_file_is_slow_to_appear(card_photo, monkeypatch):
    """Docker on a Mac can show a file the container wrote a moment late: the turn's own record counts."""
    for i, t in enumerate(["keeps the porch light on", "never learned to swim"]):
        visit.run(file=card_photo(f"s{i}", t), seed=True, call=reducto_says(t)); time.sleep(0.01)
    # a stand-in for Hermes that gives, then hides the pick file, as if it had not reached this side yet
    hide = (f"{sys.executable} -c \"import json,sys; from card.body_server import give; from card import store; "
            f"print(give('002', 'never learned')); store.pick_file().unlink()\" ")
    monkeypatch.setattr(pick, "HERMES_CMD", hide)
    _, job = visit.run(file=card_photo("v", "v"), call=reducto_says("is afraid of deep water"), is_online=True)
    assert job["picked_by"] == "hermes" and job["card_no"] == 2 and job["quote"] == "never learned"


@pytest.mark.parametrize("line, banned", [("don't give up", "give up"), ("never give up", "give up"), ("You can't quit now", "quit now"),
                                          ("You're not missing anything, don't worry about it.", "worry about it.")])
def test_a_phrase_never_drops_the_negation_before_it(line, banned):
    assert banned not in [p["text"] for p in quotes.phrases_from_text(line)]


def test_a_negation_inside_the_phrase_is_kept():
    assert "not late" in [p["text"] for p in quotes.phrases_from_text("You are not late")]
    assert "don't give up" in [p["text"] for p in quotes.phrases_from_text("Please don't give up")]


@pytest.mark.parametrize("line, want", [("I am so sorry! I hope you are okay!", "you are okay!"),
                                        ("Keep pushing. You'll come out of this stronger I promise", "Keep pushing.")])
def test_the_rule_gives_a_complete_sounding_phrase(line, want):
    assert quotes.default({"line": line})["text"] == want
