"""What the visitor screen is given: handwriting as ink, rows to reveal, a clock, and the idle stack."""
import json
import time

import cv2

from card import server, store, visit
from card.tests.conftest import CORNERS, TICK, reducto_says, set_layout


def test_every_card_gets_an_ink_cut_out_that_sits_on_transparent_ground(card_photo):
    card, _ = visit.run(file=card_photo("a", "keeps the porch light on"), seed=True, call=reducto_says("keeps the porch light on"))
    ink = cv2.imread(str(store.BASE / card["ink"]), cv2.IMREAD_UNCHANGED)
    assert ink.shape[2] == 4 and ink.shape[1] == card["ink_size"][0]
    alpha = ink[..., 3]
    assert (alpha == 0).mean() > 0.6 and (alpha > 200).mean() > 0.01        # mostly clear, some solid ink
    assert all(0 <= v <= 1 for v in card["ink_rect"])


def test_the_screen_state_has_rows_and_a_clock(card_photo, site):
    site.capture(card_photo("a", "keeps the porch light on"), "keeps the porch light on", seed=True)
    site.call("/api/testjob", body={"text": "hand me down"})
    from card.tests.test_loop import wait_for
    state = wait_for("writing", site)
    assert abs(state["now"] - time.time()) < 5 and state["est_s"] == 90


def test_the_idle_stack_holds_only_cards_that_are_ok_to_show(card_photo, site):
    set_layout(require_tick=True, corners=CORNERS, tick=TICK, line=[0.02, 0.3, 0.96, 0.35])
    shown = site.capture(card_photo("a", "keeps the porch light on", ticked=True), "keeps the porch light on", seed=True)
    hidden = site.capture(card_photo("b", "the line nobody may see", ticked=False), "the line nobody may see", seed=True)
    stack = json.loads(site.get("/stack.json")[1])
    assert [c["no"] for c in stack["cards"]] == [shown["card_no"]]
    assert stack["cards"][0]["ink"] == f"/cards/{store.nnn(shown['card_no'])}_ink.png" and len(stack["cards"][0]["size"]) == 2
    assert site.get(f"/cards/{store.nnn(hidden['card_no'])}_ink.png")[0] == 404
    assert stack["invitation"] is None or stack["invitation"] == "/invitation.png"


def test_the_teams_handwriting_is_cut_out_and_served(tmp_path, site, monkeypatch, card_photo):
    photo = card_photo("invite", "leave a line, take a line")
    dest = tmp_path / "invitation.png"
    monkeypatch.setitem(server.HANDWRITING, "invitation", dest)
    from card import read_card
    read_card.ink_from_photo(photo, dest)
    stack = json.loads(site.get("/stack.json")[1])
    assert stack["invitation"] == "/invitation.png" and stack["invitation_size"][0] > 100
    status, body = site.get("/invitation.png")
    assert status == 200 and body[:4] == b"\x89PNG"


def test_screen_assets_are_served_but_nothing_outside_screen(site):
    assert site.get("/screen/../card/server.py")[0] == 404
    assert site.get("/screen/nothing-here.js")[0] == 404
