import cv2
import numpy as np
import pytest

from card import layout as lay
from card import store


@pytest.fixture(autouse=True)
def data(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "BASE", tmp_path / "data")
    monkeypatch.setenv("HMD_DATA", str(tmp_path / "data"))
    monkeypatch.setattr(lay, "FILE", tmp_path / "layout.json")   # tests never touch the real layout
    # Most tests are about the flow, on cards with no tick box: say so the way a team would, in the layout.
    # test_show.py turns the tick back on.
    lay.save({"require_tick": False})
    return tmp_path


def set_layout(**changes):
    """Change some layout keys, keeping the rest."""
    lay.save({**lay.load(), **changes})


# A card as the camera sees it in card_photo: its corners in the frame, and where things sit on it.
CORNERS = [[0.1875, 0.2222], [0.8125, 0.2222], [0.8125, 0.7778], [0.1875, 0.7778]]
TICK = [0.05, 0.7, 0.08, 0.14]


@pytest.fixture
def card_photo(tmp_path):
    """A white card on a dark table with one line of text. Each photo differs, so hashes differ."""
    def make(name, text, ticked=False):
        img = np.full((720, 1280, 3), 40, np.uint8)
        cv2.rectangle(img, (240, 160), (1040, 560), (245, 245, 245), -1)
        cv2.putText(img, text, (280, 380), cv2.FONT_HERSHEY_SIMPLEX, 1.0, (20, 20, 20), 3)
        cv2.rectangle(img, (280, 440), (344, 496), (60, 60, 60), 2)        # the printed "OK to show" box
        if ticked:                                                            # a tick drawn with a marker
            cv2.line(img, (290, 468), (306, 486), (25, 25, 25), 7)
            cv2.line(img, (306, 486), (336, 448), (25, 25, 25), 7)
        path = tmp_path / f"{name}.jpg"
        cv2.imwrite(str(path), img)
        return path
    return make


def response(text, top=0.46):
    """A Reducto parse response, shaped like the real one, for one line of handwriting."""
    words = text.split()
    step = 0.6 / max(len(words), 1)
    return {"result": {"type": "full", "chunks": [{"blocks": [{
        "type": "Text", "content": text,
        "bbox": {"left": 0.2, "top": top - 0.02, "width": 0.62, "height": 0.12, "page": 1},
        "extra": {"word_citations": [
            {"text": w, "bbox": {"left": 0.21 + i * step, "top": top, "width": step * 0.9, "height": 0.08, "page": 1}}
            for i, w in enumerate(words)]},
    }]}]}}


def reducto_says(*texts):
    """A stand-in for the Reducto call: answers with each text in turn; None means it's down."""
    queue = list(texts)

    def call(image):
        text = queue.pop(0)
        if text is None:
            raise ConnectionError("reducto is down")
        return response(text)
    return call
