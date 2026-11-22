"""The quote check. Plain code, no model: is this quote in the ink on that card?"""
import re
import unicodedata

from . import store

MIN_WORDS = 2  # "a few words"; a one-word card may be quoted whole


def words(text):
    text = unicodedata.normalize("NFKC", text or "").casefold()
    return re.sub(r"[^\w\s]", " ", text).split()


def quote_on_card(quote, card_text):
    """True if the quote's words appear, in order and adjacent, on the card."""
    q, t = words(quote), words(card_text)
    if not q or len(q) < min(MIN_WORDS, len(t)):
        return False
    return any(t[i:i + len(q)] == q for i in range(len(t) - len(q) + 1))


def judge_give(turn, card, quote):
    """Decide one give() call against the turn in play.

    Returns (pick or None, message). Counts a miss on every refusal; the caller
    saves the turn. After max_misses the turn is closed and the rule picks.
    """
    if turn is None:
        return None, "no visitor is waiting"
    if turn.get("closed"):
        return None, "this turn is over"
    cid = store.norm_id(card)
    text = turn["candidates"].get(cid)
    if text is None:
        reason = f"card {cid} is not on the rail; choose from {sorted(turn['candidates'])}"
    elif not quote_on_card(quote, text):
        reason = (f"those words are not on card {cid}; quote at least {MIN_WORDS} "
                  "consecutive words exactly as written on it")
    else:
        turn["closed"] = True
        return {"turn": turn["turn"], "card": cid, "quote": quote, "by": "hermes"}, "given"
    turn["misses"] = turn.get("misses", 0) + 1
    if turn["misses"] >= turn.get("max_misses", 2):
        turn["closed"] = True
        return None, reason + ". No tries left."
    return None, reason + ". One try left."
