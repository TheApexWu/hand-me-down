"""Arm offline: the operator's switch puts the simulation clip on the screen."""
import json

from card import server


def test_the_switch_needs_a_clip_and_tells_the_screen(site, tmp_path, monkeypatch):
    clip = tmp_path / "sim-demo.mp4"
    monkeypatch.setattr(server, "SIM_CLIP", clip)
    state = lambda: json.loads(site.get("/state.json")[1])

    status, body = site.call("/api/sim", body={"on": True})[:2]            # no clip yet: refused, said why
    assert status == 409 and "no simulation clip" in json.loads(body)["error"] and state()["sim"] is False
    assert json.loads(site.get("/api/status")[1])["sim_clip"] is None

    clip.write_bytes(bytes(range(256)) * 40)
    before = state()
    assert site.call("/api/sim", body={"on": True})[0] == 200
    on = state()
    assert on["sim"] is True and on["v"] > before["v"] and on["stage"] == before["stage"]   # redraw, same stage
    assert site.call("/api/sim", body={"on": False})[0] == 200
    assert state()["sim"] is False and state()["v"] > on["v"]


def test_the_clip_is_served_with_byte_ranges(site, tmp_path, monkeypatch):
    clip = tmp_path / "sim-demo.mp4"
    clip.write_bytes(bytes(range(256)) * 40)
    monkeypatch.setattr(server, "SIM_CLIP", clip)
    import urllib.request
    whole = urllib.request.urlopen(site.base + "/sim.mp4")
    assert whole.status == 200 and whole.headers["Content-Type"] == "video/mp4" and len(whole.read()) == 10240
    part = urllib.request.urlopen(urllib.request.Request(site.base + "/sim.mp4", headers={"Range": "bytes=256-511"}))
    assert part.status == 206 and part.headers["Content-Range"] == "bytes 256-511/10240" and part.read() == bytes(range(256))
    tail = urllib.request.urlopen(urllib.request.Request(site.base + "/sim.mp4", headers={"Range": "bytes=-16"}))
    assert tail.status == 206 and tail.read() == bytes(range(240, 256))


def test_another_machine_needs_the_token_for_the_clip_and_can_never_flip_the_switch(site, tmp_path, monkeypatch):
    clip = tmp_path / "sim-demo.mp4"
    clip.write_bytes(b"x" * 100)
    monkeypatch.setattr(server, "SIM_CLIP", clip)
    site.app.token, site.app.trust_loopback = "s3cret-token", False
    assert site.call("/sim.mp4")[0] == 401 and site.call("/sim.mp4", token="s3cret-token")[0] == 200
    assert site.call("/api/sim", body={"on": True})[0] == 401
    assert site.call("/api/sim", body={"on": True}, token="s3cret-token")[0] == 403
