"""--lan: every route refuses another machine that doesn't carry the shared token."""
from card import jobs, store

GETS = ["/", "/state.json", "/cards/001_line.png", "/cards/001.jpg", "/jobs/index.json", "/jobs/001.json",
        "/done/index.json", "/done/001.json", "/op", "/layout", "/api/status", "/preview.jpg", "/frame.jpg", "/nothing"]
POSTS = ["/done/001.json", "/api/capture", "/api/confirm", "/api/clear", "/api/layout", "/api/fix"]


def as_another_machine(site, token="s3cret-token"):
    site.app.token = token
    site.app.trust_loopback = False


def test_another_machine_gets_nothing_without_the_token(card_photo, site):
    site.capture(card_photo("a", "keeps the porch light on"), "keeps the porch light on", seed=True)
    as_another_machine(site)
    for path in GETS:
        assert site.call(path)[0] == 401, path
        assert site.call(path, token="wrong")[0] == 401, path
        assert site.call(path + "?token=wrong")[0] == 401, path
    for path in POSTS:
        assert site.call(path, body={})[0] == 401, path
        assert site.call(path, body={}, token="wrong")[0] == 401, path


def test_with_the_token_another_machine_reads_but_cannot_operate(card_photo, site):
    site.capture(card_photo("a", "keeps the porch light on"), "keeps the porch light on", seed=True)
    store.write_json(jobs.job_file(1), {"card_no": 1, "line": "keeps the porch light on"})
    as_another_machine(site)
    for path in ["/", "/state.json", "/cards/001_line.png", "/jobs/index.json", "/jobs/001.json"]:
        assert site.call(path, token="s3cret-token")[0] == 200, path
    assert site.call("/done/001.json", body={"delivered_at": "11:02", "duration_s": 9}, token="s3cret-token")[0] == 200
    assert jobs.done(1) == {"delivered_at": "11:02", "duration_s": 9}
    for path in ["/op", "/layout", "/api/status", "/preview.jpg"]:            # this machine only, token or not
        assert site.call(path, token="s3cret-token")[0] == 403, path
    for path in ["/api/capture", "/api/confirm", "/api/clear", "/api/layout"]:
        assert site.call(path, body={}, token="s3cret-token")[0] == 403, path


def test_a_second_display_opens_the_screen_once_with_the_token_then_a_cookie_carries_it(site):
    as_another_machine(site)
    status, _, headers = site.call("/?token=s3cret-token")
    assert status == 200 and headers["Set-Cookie"].startswith("hmd_token=s3cret-token;")
    assert "HttpOnly" in headers["Set-Cookie"]
    assert site.call("/state.json", cookie="hmd_token=s3cret-token")[0] == 200
    assert site.call("/state.json", cookie="hmd_token=nope")[0] == 401


def test_without_lan_there_is_no_token_and_another_machine_gets_nothing(site):
    site.app.trust_loopback = False            # the server binds to this machine only; this is the backstop
    assert site.app.token is None and site.call("/state.json")[0] == 401


def test_this_machine_needs_no_token(site):
    site.app.token = "s3cret-token"
    assert site.call("/state.json")[0] == 200 and site.call("/op")[0] == 200
