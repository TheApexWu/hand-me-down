"""A labeled message bank.

Labels: human_public (real comments from the pre-2020 GoEmotions dataset), ai_generated and ai_rewritten.
Only screened human comfort lines may fill a short rail, and they stay marked as archive. AI lines exist
to test Pangram and can never reach a visitor.

  python -m card.bank list
  python -m card.bank top-up [N]
"""
import collections
import json
import sys
import time
from pathlib import Path

from . import store

BANK = Path(__file__).with_name("message_bank.jsonl")
ARCHIVE_FROM = 700


def load(label=None, kind=None):
    rows = [json.loads(line) for line in BANK.read_text().splitlines() if line.strip()]
    return [x for x in rows if (label is None or x["label"] == label) and (kind is None or x["kind"] == kind)]


def rail_items():
    return [x for x in load("human_public", "comfort") if x.get("rail_eligible") is True]


def top_up(target=5):
    """Fill a short rail with archive cards. Cards from real visitors always count first."""
    need = target - len(store.waiting())
    if need <= 0:
        return []
    have = {c.get("bank_id") for c in store.all_cards()}
    added = []
    for i, x in enumerate(rail_items()):
        if len(added) >= need:
            break
        if x["id"] in have:
            continue
        added.append(store.save_card({
            "card_no": ARCHIVE_FROM + i, "line": x["text"], "status": "waiting", "may_show": True, "ok_to_show": True,
            "checked": True, "written_at": "archive", "at": time.time(), "provenance": "archive", "bank_id": x["id"],
            "gate": {"hold": False, "reasons": [], "safety": x["gate_label"], "text": x["text"]}}))
    return added


if __name__ == "__main__":
    if sys.argv[1:2] == ["top-up"]:
        print("added", [c["card_no"] for c in top_up(int(sys.argv[2]) if len(sys.argv) > 2 else 5)])
    else:
        print(dict(collections.Counter((x["label"], x["kind"]) for x in load())), "| rail eligible:", len(rail_items()))
