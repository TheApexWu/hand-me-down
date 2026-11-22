"""Quote mode: the visitor receives a short, exact phrase from a stranger's card, not the whole card.

From ARM_STATUS.md (22 Nov, 13:43): candidates are 2 or 3 consecutive words in one written row,
at most 24 characters once cleaned the way the arm cleans them. They are built from Reducto's word
boxes, so a phrase never runs across a line break. The chooser must pick one of them exactly (plain
code checks it, in the hook and in the tool), the job carries it as `quote`, and a card with no
candidate can't be given.
"""
from .pen import MAX_QUOTE_CHARS, MAX_QUOTE_WORDS, pen_text
from .quote import words as norm

NEGATIONS = {"not", "no", "never", "nor", "cannot", "without", "dont", "cant", "wont"}
WEAK_START = {"am", "is", "are", "was", "were", "be", "of", "for", "to", "and", "but", "or", "so", "about", "in", "on", "at"}
WEAK_END = WEAK_START | {"a", "an", "the", "i", "we", "my", "your", "our", "this", "that", "not", "really", "very", "like"}


def _bare(word):
    return pen_text(word)[0].lower().strip(".,!?;:\"'()")


def _negates(word):
    bare = _bare(word)
    return bare in NEGATIONS or bare.endswith("n't")


def _flips_meaning(texts, first):
    """Cutting a phrase right after a negation reverses it: "don't give up" must never become "give up"."""
    if not first:
        return False
    return _negates(texts[first - 1])


def _union(boxes):
    l, t = min(b[0] for b in boxes), min(b[1] for b in boxes)
    r, btm = max(b[0] + b[2] for b in boxes), max(b[1] + b[3] for b in boxes)
    return [round(l, 5), round(t, 5), round(r - l, 5), round(btm - t, 5)]


def _rows(boxes):
    """Word indices grouped into written rows, top to bottom, each row left to right."""
    rows = []
    for i in sorted(range(len(boxes)), key=lambda i: boxes[i][1] + boxes[i][3] / 2):
        mid = boxes[i][1] + boxes[i][3] / 2
        if rows and abs(mid - rows[-1]["mid"]) < 0.5 * boxes[i][3]:
            rows[-1]["ids"].append(i)
        else:
            rows.append({"mid": mid, "ids": [i]})
    return [sorted(r["ids"], key=lambda i: boxes[i][0]) for r in rows]


def _from(texts, rows, boxes=None):
    out, seen = [], set()
    for r, ids in enumerate(rows):
        for n in range(2, MAX_QUOTE_WORDS + 1):
            for s in range(len(ids) - n + 1):
                idx = ids[s:s + n]
                if idx != list(range(idx[0], idx[0] + n)):     # consecutive as written, not just side by side
                    continue
                if _flips_meaning(texts, idx[0]):
                    continue
                text, missing = pen_text(" ".join(texts[i] for i in idx))
                if missing or len(text.split()) != n or len(text) > MAX_QUOTE_CHARS:
                    continue
                key = " ".join(norm(text))
                if not key or key in seen:
                    continue
                seen.add(key)
                out.append({"text": text, "words": idx, "row": r, "box": _union([boxes[i] for i in idx]) if boxes else None})
    return out


def phrases(card):
    """Every phrase this card can give, in reading order: [{text, words, row, box}].

    The words are the approved line's; their places on the card are Reducto's. If someone corrected
    the line into a different number of words, there is no telling where each sits: the line is then
    treated as one row, with no boxes."""
    tokens = (card.get("line") or "").split()
    words = [w for w in card.get("words") or [] if w.get("box")]
    if words and len(words) == len(tokens):
        boxes = [w["box"] for w in words]
        return _from(tokens, _rows(boxes), boxes)
    if words and not card.get("corrected"):
        boxes = [w["box"] for w in words]
        return _from([w["text"] for w in words], _rows(boxes), boxes)
    return _from(tokens, [list(range(len(tokens)))])


def phrases_from_text(text):
    """Phrases of a line with no word boxes (tests, warm-up turns): one row."""
    tokens = (text or "").split()
    return _from(tokens, [list(range(len(tokens)))])


def find(card, quote):
    """The card's phrase that is this quote (same words, ignoring case and punctuation), or None."""
    key = " ".join(norm(quote or ""))
    return next((p for p in phrases(card) if " ".join(norm(p["text"])) == key), None) if key else None


def default(card):
    """The phrase plain code gives when no model chose one: a complete-sounding one, then the earliest."""
    def fragment(p):
        parts = p["text"].split()
        return _bare(parts[0]) in WEAK_START or _bare(parts[-1]) in WEAK_END or _negates(parts[-1]) or any(w[-1] in ".,!?;:" for w in parts[:-1])

    found = phrases(card)
    return min(found, key=lambda p: (fragment(p), p["text"][-1] not in ".!?", p["words"][0], -len(p["words"]))) if found else None


def cannot_give(card):
    """Why this card can't be given in quote mode, or None. The rail, the chooser and the job all ask."""
    from .bank import archive_problem
    problem = archive_problem(card)
    if problem:
        return problem
    if "PLACEHOLDER" in (card.get("line") or "").upper():
        return "the arm refuses any line with the word placeholder in it"
    if not phrases(card):
        return f"no phrase of 2 or 3 words in one row within {MAX_QUOTE_CHARS} characters"
    return None
