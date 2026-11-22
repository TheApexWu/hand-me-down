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
    return tmp_path


@pytest.fixture
def card_photo(tmp_path):
    """A white card on a dark table with one line of text. Each photo differs, so hashes differ."""
    def make(name, text):
        img = np.full((720, 1280, 3), 40, np.uint8)
        cv2.rectangle(img, (240, 160), (1040, 560), (245, 245, 245), -1)
        cv2.putText(img, text, (280, 380), cv2.FONT_HERSHEY_SIMPLEX, 1.0, (20, 20, 20), 3)
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
