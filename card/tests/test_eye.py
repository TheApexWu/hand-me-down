import json
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

import cv2
import numpy as np

from card import server, store
from card.tests.conftest import reducto_says


def site(spec):
    app = server.App(camera_spec=spec)
    app.token = "t"
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.make_handler(app))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return app, httpd, f"http://127.0.0.1:{httpd.server_address[1]}"


def post(base, path, data, token="t"):
    req = urllib.request.Request(base + path, data=data, method="POST",
                                 headers={"Content-Type": "image/jpeg", **({"Authorization": f"Bearer {token}"} if token else {})})
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status
    except urllib.error.HTTPError as e:
        return e.code


def test_a_webcam_on_another_machine_feeds_the_preview_and_the_capture():
    app, httpd, base = site("remote")
    app.trust_loopback = False                       # the frames come from the other machine
    img = np.full((720, 1280, 3), 40, np.uint8)
    cv2.rectangle(img, (240, 160), (1040, 560), (245, 245, 245), -1)
    jpg = cv2.imencode(".jpg", img)[1].tobytes()
    assert post(base, "/eye", jpg, token=None) == 401
    assert post(base, "/eye", b"not a picture") == 400
    assert "waiting for the card eye" in app.cam().error
    for _ in range(4):
        assert post(base, "/eye", jpg) == 200
    app.trust_loopback = True
    with urllib.request.urlopen(base + "/preview.jpg", timeout=5) as r:
        assert r.status == 200 and r.headers["Content-Type"] == "image/jpeg"
    status = json.loads(urllib.request.urlopen(base + "/api/status", timeout=5).read())
    assert status["camera"] == "card eye (another machine)" and status["camera_error"] is None
    threading.Timer(0.3, lambda: [post(base, "/eye", jpg) for _ in range(4)]).start()
    app.reducto_call = reducto_says("keeps the porch light on")
    app.capture(seed=True)
    assert store.load_card(store.next_no() - 1)["line"] == "keeps the porch light on"
    httpd.shutdown()


def test_a_server_with_its_own_camera_refuses_frames():
    app, httpd, base = site("0")
    app.camera = object()                            # a camera of its own (no device needed here)
    assert post(base, "/eye", b"\xff\xd8\xff") == 409
    httpd.shutdown()
