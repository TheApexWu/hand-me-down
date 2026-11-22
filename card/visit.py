"""One visit: card photo -> Reducto -> (confirm) -> pick -> jobs/NNN.json for the arm."""
import time

import cv2

from . import jobs, pick, read_card, store
from .capture import save_card_photo


def ingest(frame=None, file=None, call=None):
    """Save the photo, square up the card, read it. Returns the new card record."""
    if frame is None:
        frame = cv2.imread(str(file))
        if frame is None:
            raise ValueError(f"not an image: {file}")
    card = save_card_photo(frame)  # the photo is on disk before anything else happens
    return read_card.read(card, call=call)


def fix_line(card, text):
    """One of us types what the card says. The quote check keeps using Reducto's own read."""
    card["line"] = text.strip()
    card["corrected"] = True
    card["read_error"] = None
    if card["status"] == "unread" and card.get("may_show"):
        card["status"] = "waiting"
    return store.save_card(card)


def shelve(card):
    """Put a card on the rail, or mark it unread so someone types its line in later.

    A card whose "OK to show" box isn't ticked stays private: never on the rail, never given."""
    if not card.get("may_show"):
        card["status"] = "private"
    else:
        card["status"] = "waiting" if card.get("line") else "unread"
    return store.save_card(card)


def discard(card):
    card["status"] = "retaken"
    return store.save_card(card)


def give(visitor, exclude=(), **kw):
    """Choose a card for this visitor and write the job for the arm."""
    choice = pick.choose(visitor, exclude=exclude, **kw)
    if choice["card_no"]:
        stranger = store.load_card(choice["card_no"])
        if not stranger.get("may_show"):
            raise PermissionError(f"card {store.nnn(stranger['card_no'])} is not OK to show; it cannot be given")
        stranger.update(status="given", given_to=visitor["card_no"], given_at=time.time())
        store.save_card(stranger)
    job = jobs.write(choice, visitor)
    visitor.setdefault("got", []).append(job["card_no"])
    store.save_card(visitor)
    return job


def ungive(job):
    """Take a job back (for 'another one'): the card returns to the rail."""
    card = None if job.get("house") else store.load_card(job["card_no"])
    if card and card.get("status") == "given":
        card["status"] = "waiting"
        card.pop("given_to", None)
        store.save_card(card)
    if not jobs.done(job["card_no"]):
        jobs.job_file(job["card_no"]).unlink(missing_ok=True)


def run(frame=None, file=None, seed=False, call=None, **kw):
    """A whole visit with no confirm step (tests, seeding, the command line)."""
    card = ingest(frame=frame, file=file, call=call)
    if seed:
        return shelve(card), None
    job = give(card, **kw)
    shelve(card)  # the visitor's card joins the rail only after their gift is chosen
    return card, job
