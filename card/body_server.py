"""BODY: the only tool the chooser has. Hermes runs this as a stdio MCP server.

give(card, quote) is refused unless the quote is found in what Reducto read off
that card. The same check also runs earlier, in the pre_tool_call hook
(card/hooks/quote_on_card.py), so a refused call never gets this far.
"""
import json

try:  # mcp 2.x
    from mcp.server.mcpserver import MCPServer
except ImportError:  # mcp 1.x, as shipped inside some Hermes images
    from mcp.server.fastmcp import FastMCP as MCPServer

from . import store
from .quote import judge_give

server = MCPServer("body", instructions="One tool: give(card, quote).")


@server.tool()
def give(card: str, quote: str) -> str:
    """Give the visitor one waiting card.

    card: the card's number, for example "012".
    quote: two or more consecutive words copied exactly from that card.
    Refused if the words are not on the card.
    """
    turn = store.read_json(store.turn_file())
    already_over = bool(turn and turn.get("closed"))
    pick, message = judge_give(turn, card, quote)
    if turn is not None:
        turn.setdefault("calls", []).append(
            {"card": str(card), "quote": quote, "ok": bool(pick), "by": "tool", "message": message})
        store.write_json(store.turn_file(), turn)
        if pick:
            store.write_json(store.pick_file(), pick)
        elif turn.get("closed") and not already_over:
            # this call used up the last try. A call after the turn is over (a second give after a
            # gift, say) changes nothing: it must never overwrite the gift already recorded.
            store.write_json(store.pick_file(), {"turn": turn["turn"], "failed": message})
    return json.dumps({"ok": True, "given": pick["card"]} if pick else {"error": message})


if __name__ == "__main__":
    server.run("stdio")
