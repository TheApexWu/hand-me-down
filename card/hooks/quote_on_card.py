#!/usr/bin/env python3
"""Hermes pre_tool_call hook for give(card, quote).

Reads the hook payload on stdin ({"tool_name", "tool_input", ...}) and blocks the
call unless the quote is in Reducto's read of that card. A blocked call counts as
a miss; after two the turn is closed and plain code picks by rule.

Wire it in the chooser profile's config.yaml:
  hooks:
    pre_tool_call:
      - matcher: "mcp_+body_+give"
        command: "/abs/path/.venv/bin/python /abs/path/card/hooks/quote_on_card.py"
        fail_closed: true
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from card import store  # noqa: E402
from card.quote import judge_give  # noqa: E402


def decide(payload):
    """Returns None to allow, or a reason string to block."""
    args = payload.get("tool_input") or {}
    if isinstance(args, str):
        try:
            args = json.loads(args)
        except json.JSONDecodeError:
            args = {}
    turn = store.read_json(store.turn_file())
    already_over = bool(turn and turn.get("closed"))
    pick, message = judge_give(turn, args.get("card", ""), args.get("quote", ""))
    if pick:
        return None  # allowed: the tool itself records the gift and closes the turn
    if turn is not None:
        turn.setdefault("calls", []).append(
            {"card": str(args.get("card")), "quote": args.get("quote"), "ok": False, "by": "hook", "message": message})
        store.write_json(store.turn_file(), turn)
        if turn.get("closed") and not already_over and not (store.read_json(store.pick_file()) or {}).get("card"):
            store.write_json(store.pick_file(), {"turn": turn["turn"], "failed": message})  # the last try is used up
    return message


if __name__ == "__main__":
    try:
        reason = decide(json.loads(sys.stdin.read() or "{}"))
    except Exception as exc:  # fail closed
        reason = f"hook error: {exc}"
    if reason:
        print(json.dumps({"action": "block", "message": reason, "decision": "block", "reason": reason}))
