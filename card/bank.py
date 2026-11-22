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
MAX_ARCHIVE = 4      # archive lines waiting at once, at most: they fill a short rail, never replace visitors


def load(label=None, kind=None):
    rows = [json.loads(line) for line in BANK.read_text().splitlines() if line.strip()]
    return [x for x in rows if (label is None or x["label"] == label) and (kind is None or x["kind"] == kind)]


def rail_items():
    return [x for x in load("human_public", "comfort") if x.get("rail_eligible") is True]


def row(bank_id):
    return next((x for x in load() if x["id"] == bank_id), None)


def is_archive(card):
    return card.get("provenance") == "archive" or bool(card.get("bank_id"))


def archive_problem(card):
    """Why an archive card may not be given, or None. Only a screened human comfort line, word for word as
    in the bank, may reach a visitor; the AI-written and AI-rewritten lines exist only to test Pangram."""
    if not is_archive(card):
        return None
    x = row(card.get("bank_id"))
    if not x:
        return "it says archive but has no line in the bank"
    if x["label"] != "human_public":
        return f"bank line {x['id']} is {x['label']}, and AI lines never reach a visitor"
    if x["kind"] != "comfort" or x.get("rail_eligible") is not True:
        return f"bank line {x['id']} is not cleared for the rail"
    if " ".join((card.get("line") or "").split()) != " ".join(x["text"].split()):
        return f"its words differ from bank line {x['id']}"
    return None


def provenance(card):
    """Where a card's words came from, for the job and both screens: a visitor, or the public archive."""
    if not card or not is_archive(card):
        return {"provenance": "visitor", "source": None, "source_name": None, "bank_id": None}
    src = card.get("source") or (row(card.get("bank_id")) or {}).get("source") or ""
    return {"provenance": "archive", "source": src or None, "source_name": src.split(" (")[0] or None,
            "bank_id": card.get("bank_id")}


def top_up(target=5):
    """Fill a short rail with archive cards, at most MAX_ARCHIVE waiting at once. Cards from real visitors
    always count first: only the gap below target is filled."""
    waiting = store.waiting()
    need = min(target - len(waiting), MAX_ARCHIVE - sum(1 for c in waiting if is_archive(c)))
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
            "bank_label": x["label"], "source": x["source"], "source_id": x.get("source_id"),
            "gate": {"hold": False, "reasons": [], "safety": x["gate_label"], "text": x["text"]}}))
    return added


if __name__ == "__main__":
    if sys.argv[1:2] == ["top-up"]:
        print("added", [c["card_no"] for c in top_up(int(sys.argv[2]) if len(sys.argv) > 2 else 5)])
    else:
        print(dict(collections.Counter((x["label"], x["kind"]) for x in load())), "| rail eligible:", len(rail_items()))
