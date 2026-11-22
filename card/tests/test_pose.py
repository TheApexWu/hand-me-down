"""The arm laptop sends its pose for the 3D panel; the screen reads it back."""
import json
import time

POSE = {"pos": {"shoulder_pan": 4.5, "shoulder_lift": -20, "elbow_flex": 35.25, "wrist_flex": 60, "wrist_roll": 0},
        "phase": "write:031", "t": 1795373000.5}


def test_a_pose_is_kept_and_read_back(site):
    assert json.loads(site.get("/pose.json")[1]) == {}
    assert site.call("/pose", body=POSE)[0] == 200
    got = json.loads(site.get("/pose.json")[1])
    assert got["pos"]["elbow_flex"] == 35.25 and got["phase"] == "write:031" and got["t"] == 1795373000.5


def test_a_bad_pose_is_refused_and_the_last_good_one_stays(site):
    site.call("/pose", body=POSE)
    for bad in ({"pos": {"shoulder_pan": 1}}, {"pos": {**POSE["pos"], "wrist_roll": "x"}}, {"pos": {**POSE["pos"], "elbow_flex": 9000}}, {}):
        assert site.call("/pose", body=bad)[0] == 400
    assert json.loads(site.get("/pose.json")[1])["pos"]["shoulder_lift"] == -20


def test_a_pose_with_no_time_gets_the_servers(site):
    site.call("/pose", body={"pos": POSE["pos"]})
    assert abs(json.loads(site.get("/pose.json")[1])["t"] - time.time()) < 5


def test_the_arm_laptop_needs_the_token_to_send_or_read_a_pose(site):
    site.app.token, site.app.trust_loopback = "tok", False
    assert site.call("/pose", body=POSE)[0] == 401 and site.call("/pose.json")[0] == 401
    assert site.call("/pose", body=POSE, token="tok")[0] == 200
    assert site.call("/pose.json", token="tok")[0] == 200


def test_the_arm_meshes_are_served_and_nothing_else_from_sim(site):
    status, body = site.get("/sim/so101/assets/base_so101_v2.stl")
    assert status == 200 and len(body) > 10000
    assert site.get("/sim/so101/so101.xml")[0] == 404
    assert site.get("/sim/so101/assets/../so101.xml")[0] == 404
    assert site.get("/sim/arm_tools.py")[0] == 404
