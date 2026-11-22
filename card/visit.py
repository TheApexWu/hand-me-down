"""One visit: card photo -> Reducto -> (confirm) -> pick -> jobs/NNN.json for the arm."""
import time

from . import jobs, pick, read_card, store
from .capture import read_image, save_card_photo


def ingest(frame=None, file=None, call=None):
    """Save the photo, square up the card, read it. Returns the new card record."""
    if frame is None:
        frame = read_image(file)
        if frame is None:
            raise ValueError(f"not an image: {file}")
    card = save_card_photo(frame)  # the photo is on disk before anything else happens
    return read_card.read(card, call=call)


# Tests set this to skip the approval step where it isn't what they are about. Never set it in real use:
# the arm writes the read text, and a misread must not reach it unchecked.
TRUST_READS = False


def approve(card, text=None):
    """The operator has compared the read with the handwriting and says it is right (or types it right).

    Only an approved card can go on the rail, so the arm is never sent a read no person has checked."""
    if text is not None and text.strip() and text.strip() != card.get("line"):
        card["line"] = text.strip()
        card["corrected"] = True
        card["read_error"] = None
    card["checked"] = True
    if card["status"] in ("unchecked", "unread", "unwritable"):
        return shelve(card)
    return store.save_card(card)   # mid-visit: it is shelved when the visit ends


def fix_line(card, text):
    """One of us types what the card says. The quote check keeps using Reducto's own read."""
    return approve(card, text)


def shelve(card):
    """Put a card on the rail, or mark it unread so someone types its line in later.

    A card whose "OK to show" box isn't ticked stays private: never on the rail, never given."""
    card.pop("cannot_write", None)
    if TRUST_READS:
        card["checked"] = True
    if not card.get("may_show"):
        card["status"] = "private"
    elif not card.get("line"):
        card["status"] = "unread"
    elif not card.get("checked"):
        card["status"] = "unchecked"   # waits for a person to compare the read with the handwriting
    elif jobs.cannot_write(card["line"]):
        # too long for the arm, or a character its pen can't draw: it would only ever be refused
        card["status"], card["cannot_write"] = "unwritable", jobs.cannot_write(card["line"])
    else:
        card["status"] = "waiting"
    return store.save_card(card)


def discard(card):
    card["status"] = "retaken"
    return store.save_card(card)


def give(visitor, exclude=(), **kw):
    """Choose a card for this visitor and write the job for the arm."""
    choice = pick.choose(visitor, exclude=exclude, **kw)
    stranger = store.load_card(choice["card_no"]) if choice["card_no"] else None
    if stranger and not stranger.get("may_show"):
        raise PermissionError(f"card {store.nnn(stranger['card_no'])} is not OK to show; it cannot be given")
    job = jobs.write(choice, visitor)   # raises jobs.NoJob if there is nothing the arm could write
    if stranger:
        stranger.update(status="given", given_to=visitor["card_no"], given_at=time.time())
        store.save_card(stranger)
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
    if not jobs.delivered(jobs.done(job["card_no"])):   # not written by the arm: the job is over, refusal and all
        jobs.job_file(job["card_no"]).unlink(missing_ok=True)
        jobs.done_file(job["card_no"]).unlink(missing_ok=True)


def run(frame=None, file=None, seed=False, call=None, **kw):
    """A whole visit with no confirm step (tests, seeding, the command line)."""
    card = ingest(frame=frame, file=file, call=call)
    if seed:
        return shelve(card), None
    try:
        job = give(card, **kw)
    except jobs.NoJob as none:
        job = None
        card["no_job"] = str(none)
    shelve(card)  # the visitor's card joins the rail only after their gift is chosen
    return card, job
