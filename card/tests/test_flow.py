import asyncio
import json
import subprocess
import sys
import time

from card import jobs, pick, read_card, store, visit
from card.tests.conftest import reducto_says, response, set_layout

# A stand-in for Hermes: calls the same give() the MCP tool runs, with arguments from argv.
FAKE = f"{sys.executable} -c \"import sys,json; from card.body_server import give; [print(give(c,q)) for c,q in json.loads(sys.argv[1])]\" "
LEAVING, FORGIVEN = "is leaving and can't say it", "has been forgiven and doesn't believe it yet"


def calls(*pairs):
    return FAKE + "'" + json.dumps(pairs) + "'"


def seed(card_photo, *lines):
    for i, text in enumerate(lines):
        visit.run(file=card_photo(f"seed{i}", text), seed=True, call=reducto_says(text))
        time.sleep(0.01)


def test_job_matches_the_contract(card_photo):
    seed(card_photo, LEAVING, FORGIVEN)
    card, job = visit.run(file=card_photo("v", "v"), call=reducto_says("was let off the hook"),
                          cmd=calls(["002", "been forgiven"]), is_online=True)
    on_disk = store.read_json(store.BASE / "jobs" / "002.json")
    assert on_disk == job
    assert {k: job[k] for k in ("card_no", "line", "crop", "quote", "picked_by", "mode")} == {
        "card_no": 2, "line": FORGIVEN, "crop": "cards/002_line.png", "quote": "been forgiven",
        "picked_by": "hermes", "mode": "font"}
    assert len(job["bbox"]) == 4 and all(0 <= v <= 1 for v in job["bbox"])
    assert len(job["written_at"]) == 5 and job["written_at"][2] == ":"
    assert (store.BASE / job["crop"]).exists() and (store.BASE / job["card_img"]).exists()
    # the given card leaves the rail, the visitor's joins it
    assert store.load_card(2)["status"] == "given"
    assert [c["card_no"] for c in store.waiting()] == [1, card["card_no"]]
    # the arm's reply feeds the postmark and the progress estimate
    store.write_json(jobs.done_file(2), {"delivered_at": "11:02", "duration_s": 134})
    assert jobs.done(2)["duration_s"] == 134 and jobs.expected_seconds() == 134


def test_a_second_give_after_the_gift_changes_nothing(card_photo):
    """A model that calls give again after it has given must not undo, or replace, the gift."""
    seed(card_photo, LEAVING, FORGIVEN)
    _, job = visit.run(file=card_photo("v", "v"), call=reducto_says("was let off the hook"), is_online=True,
                       cmd=calls(["002", "been forgiven"], ["001", "is leaving and"], ["002", "made up words"]))
    assert job["picked_by"] == "hermes" and job["card_no"] == 2 and job["quote"] == "been forgiven"
    assert job["misses"] == 0 and store.load_card(1)["status"] == "waiting"


def test_the_hook_does_not_undo_a_gift_either(card_photo):
    store.write_json(store.turn_file(), {"turn": "t9", "visitor": 9, "misses": 0, "max_misses": 2,
                                         "candidates": {"003": FORGIVEN}})
    from card.body_server import give
    assert json.loads(give("003", "forgiven and doesn't")) == {"ok": True, "given": "003"}
    hook = [sys.executable, str(store.ROOT / "card" / "hooks" / "quote_on_card.py")]
    payload = {"hook_event_name": "pre_tool_call", "tool_name": "mcp__body__give", "tool_input": {"card": "003", "quote": "again"}}
    out = subprocess.run(hook, input=json.dumps(payload), capture_output=True, text=True, timeout=20).stdout
    assert json.loads(out)["action"] == "block" and "over" in json.loads(out)["message"]
    assert store.read_json(store.pick_file())["card"] == "003"


def test_two_bad_quotes_fall_back_to_the_longest_waiting_card(card_photo):
    seed(card_photo, LEAVING, FORGIVEN)
    _, job = visit.run(file=card_photo("v", "v"), call=reducto_says("anything"), is_online=True,
                       cmd=calls(["002", "is leaving and"], ["002", "made up words"]))
    assert job["picked_by"] == "rule" and job["card_no"] == 1 and job["misses"] == 2
    assert job["quote"] == "can't say it"          # a pick by rule still carries a phrase, never the whole card


def test_late_model_falls_back_and_cannot_give_afterwards(card_photo):
    seed(card_photo, "one two three", "four five six")
    set_layout(deadline_s=0.6)
    t = time.time()
    _, job = visit.run(file=card_photo("v", "v"), call=reducto_says("anything"), cmd="sleep 30 #", is_online=True)
    assert job["picked_by"] == "rule" and job["card_no"] == 1 and time.time() - t < 5
    assert store.read_json(store.turn_file())["closed"]


def test_missing_hermes_falls_back(card_photo):
    seed(card_photo, "one two three", "four five six")
    _, job = visit.run(file=card_photo("v", "v"), call=reducto_says("anything"), cmd="no-such-binary-xyz -q", is_online=True)
    assert job["picked_by"] == "rule" and "could not start" in job["why"]


def test_network_off_still_makes_a_job_marked_offline(card_photo):
    seed(card_photo, LEAVING, FORGIVEN)
    card, job = visit.run(file=card_photo("v", "v"), call=reducto_says(None), is_online=False)
    assert job["picked_by"] == "offline" and job["card_no"] == 1          # longest-waiting, from cached reads
    assert store.load_card(card["card_no"])["status"] == "unread"         # typed in later by one of us
    fixed = visit.fix_line(store.load_card(card["card_no"]), "typed in by one of us")
    assert fixed["status"] == "waiting" and fixed["corrected"]


def test_network_off_and_nothing_cached_gives_the_house_line(card_photo):
    set_layout(house_line="TEAM WRITES THIS", mode="trace", arm_traces=True)
    _, job = visit.run(file=card_photo("v", "v"), call=reducto_says(None), is_online=False)
    assert job["picked_by"] == "offline" and job["line"] == "TEAM WRITES THIS" and job["card_no"] == 900
    assert job["mode"] == "font" and job["crop"] is None and (store.BASE / "jobs" / "900.json").exists()


def test_reads_and_picks_replay_from_cache_with_the_network_off(card_photo):
    seed(card_photo, LEAVING, FORGIVEN)
    photo = card_photo("v", "v")
    _, first = visit.run(file=photo, call=reducto_says("was let off the hook"),
                         cmd=calls(["002", "been forgiven"]), is_online=True)
    assert first["picked_by"] == "hermes"
    visit.ungive(first)                                   # same rail as before
    visit.discard(store.load_card(first["visitor_no"]))
    card, again = visit.run(file=photo, call=reducto_says(None), is_online=False)
    assert card["line"] == "was let off the hook" and card["read_source"] == "cache"
    assert again["picked_by"] == "offline" and again["card_no"] == 2 and again["quote"] == "been forgiven"


def test_words_go_to_fields_by_position(card_photo):
    set_layout(corners=[[0.1875, 0.2222], [0.8125, 0.2222], [0.8125, 0.7778], [0.1875, 0.7778]],
               line=[0.05, 0.2, 0.9, 0.35], name=[0.4, 0.65, 0.55, 0.25], tick=[0.05, 0.7, 0.08, 0.14])

    def call(image):
        r = response("FOR SOMEONE WHO", top=0.06)
        r["result"]["chunks"][0]["blocks"] += response("keeps the porch light on", top=0.3)["result"]["chunks"][0]["blocks"]
        name = response("Maya", top=0.72)["result"]["chunks"][0]["blocks"][0]
        name["extra"]["word_citations"][0]["bbox"]["left"] = 0.6
        r["result"]["chunks"][0]["blocks"].append(name)
        return r

    card = visit.ingest(file=card_photo("v", "v"), call=call)
    assert card["line"] == "keeps the porch light on" and card["name"] == "Maya"
    assert card["ok_to_show"] is False                    # the tick box is empty paper
    assert 0.25 < card["bbox"][1] < 0.35                  # the box is on the handwritten line, not the label


def test_hook_refuses_a_made_up_quote(card_photo):
    """Negative control: the pre_tool_call hook, run the way Hermes runs it."""
    store.write_json(store.turn_file(), {"turn": "t9", "visitor": 9, "misses": 0, "max_misses": 2,
                                         "candidates": {"003": FORGIVEN}})
    hook = [sys.executable, str(store.ROOT / "card" / "hooks" / "quote_on_card.py")]

    def run(card, quote):
        payload = {"hook_event_name": "pre_tool_call", "tool_name": "mcp__body__give",
                   "tool_input": {"card": card, "quote": quote}}
        return subprocess.run(hook, input=json.dumps(payload), capture_output=True, text=True, timeout=20).stdout

    blocked = json.loads(run("003", "never written on this card"))
    assert blocked["decision"] == "block" and "not on card 003" in blocked["reason"]
    assert store.read_json(store.turn_file())["misses"] == 1
    assert run("003", "forgiven and doesn't") == ""        # a real quote passes untouched
    assert json.loads(run("003", "also made up"))["decision"] == "block"
    assert "failed" in store.read_json(store.pick_file())  # two misses: the rule takes over


def test_body_server_over_stdio():
    """The real MCP server, launched the way Hermes launches it."""
    from mcp import Client, StdioServerParameters
    store.write_json(store.turn_file(), {"turn": "t9", "visitor": 9, "misses": 0, "max_misses": 2,
                                         "candidates": {"003": FORGIVEN}})
    params = StdioServerParameters(command=sys.executable, args=["-m", "card.body_server"],
                                   env={"HMD_DATA": str(store.BASE), "PATH": ""}, cwd=str(store.ROOT))

    async def go():
        async with Client(params) as client:
            tools = await client.list_tools()
            assert [t.name for t in tools.tools] == ["give"]
            bad = await client.call_tool("give", {"card": "003", "quote": "never written here"})
            good = await client.call_tool("give", {"card": "003", "quote": "forgiven and doesn't"})
            return bad.content[0].text, good.content[0].text

    bad, good = asyncio.run(go())
    assert "error" in json.loads(bad) and json.loads(good) == {"ok": True, "given": "003"}
    assert store.read_json(store.pick_file())["card"] == "003"
