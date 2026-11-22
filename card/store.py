"""Files on disk. Everything is relative to the repo root (override with HMD_DATA).

  cards/NNN.jpg        the camera frame          cards/NNN_card.jpg  the card, squared up
  cards/NNN_line.png   the handwritten line      cards/NNN.json      what Reducto read
  jobs/NNN.json        a line for the arm        done/NNN.json       the arm's reply
  cache/               Reducto reads, picks, and the run state shared with the screen
"""
import json
import os
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def load_env():
    """Read ROOT/.env (git-ignored) into os.environ. What .env defines wins over the shell:
    it is this project's own file, and a stale key exported from a shell profile shouldn't beat it."""
    env = ROOT / ".env"
    if not env.exists():
        return
    for raw in env.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        value = value.strip().strip('"').strip("'")
        if value:
            os.environ[key.strip()] = value


load_env()
BASE = Path(os.environ.get("HMD_DATA") or ROOT).resolve()


def folder(name):
    path = BASE / name
    path.mkdir(parents=True, exist_ok=True)
    return path


def rel(path):
    """Path as written into JSON: relative to the repo root, forward slashes."""
    try:
        return Path(path).resolve().relative_to(BASE).as_posix()
    except ValueError:
        return str(path)


def write_json(path, obj):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    with os.fdopen(fd, "w") as f:
        json.dump(obj, f, indent=1, ensure_ascii=False)
    os.replace(tmp, path)


def read_json(path, default=None):
    try:
        return json.loads(Path(path).read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return default


# cards ---------------------------------------------------------------------

def nnn(card_no):
    return f"{int(card_no):03d}"


def norm_id(card):
    """'12', 12, '#012', 'card 12' -> '012'."""
    digits = "".join(ch for ch in str(card) if ch.isdigit())
    return nnn(digits) if digits else str(card).strip()


def card_file(card_no):
    return folder("cards") / f"{nnn(card_no)}.json"


def frame_file(card_no):
    return folder("cards") / f"{nnn(card_no)}.jpg"


def card_img_file(card_no):
    return folder("cards") / f"{nnn(card_no)}_card.jpg"


def line_file(card_no):
    return folder("cards") / f"{nnn(card_no)}_line.png"


def load_card(card_no):
    return read_json(card_file(card_no))


def save_card(card):
    write_json(card_file(card["card_no"]), card)
    return card


def all_cards():
    cards = [read_json(p) for p in folder("cards").glob("[0-9][0-9][0-9].json")]
    return sorted((c for c in cards if c), key=lambda c: c["at"])


def next_no():
    return max((int(p.stem) for p in folder("cards").glob("[0-9][0-9][0-9].json")), default=0) + 1


def waiting(exclude=()):
    """Cards that can be given: still waiting, readable, oldest first."""
    exclude = {int(x) for x in exclude}
    return [c for c in all_cards()
            if c.get("status") == "waiting" and c.get("line") and c["card_no"] not in exclude]


# run state (shared with the body server, the hook and the screen) ---------------

def turn_file():
    return folder("cache") / "turn.json"


def pick_file():
    return folder("cache") / "pick.json"


def state():
    return read_json(folder("cache") / "state.json", {"stage": "idle", "v": 0})


def set_state(**fields):
    # v is what the screen watches. It must come last (a caller may pass back an old state,
    # old v included) and must always move forward, even within one millisecond.
    new = {**fields, "v": max(int(time.time() * 1000), int(state().get("v", 0)) + 1)}
    write_json(folder("cache") / "state.json", new)
    return new


def clock(ts=None):
    return time.strftime("%H:%M", time.localtime(ts))
